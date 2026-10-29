package com.hotshare

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothServerSocket
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
import android.os.PowerManager
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import androidx.documentfile.provider.DocumentFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.*
import java.net.*
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

data class Dev(val name: String, val addr: String, val port: Int, val kind: String)

/** All networking lives here so the foreground service can receive with no Activity. Protocol HSH3 == hotshare_core.py (plain TCP). */
object Core {
    const val DISC = 45454
    const val BUF = 1 shl 20
    private const val MAXH = 16 shl 20
    val BT_UUID: UUID = UUID.fromString("7d3c1a52-9b1e-4c11-8f55-0a1b2c3d4e5f")

    class Item(val rel: String, val size: Long, val mtime: Long, val text: String? = null, val src: String? = null, val open: () -> InputStream)
    class Transfer(val id: Int, val label: String, val incoming: Boolean) {
        @Volatile var names: Array<String> = emptyArray(); @Volatile var sizes = LongArray(0); @Volatile var fdone = LongArray(0)
        @Volatile var done = 0L; @Volatile var total = 0L; @Volatile var cancelled = false; @Volatile var paused = false; @Volatile var discard = false
        @Volatile var speed = 0.0; @Volatile var moved = 0L; @Volatile var connectedOnce = false; @Volatile var state = ""; @Volatile var key = ""; @Volatile var jobId = ""
        @Volatile var parts: List<File> = emptyList()
        var base = 0L; var t0 = 0L; var last = 0L; var wakeAt = 0L
        val eta: Int get() = if (speed > 0) ((total - done) / speed).toInt() else 0
    }
    class Approval(val id: Int, val peer: String, val count: Int, val total: Long) { val latch = CountDownLatch(1); @Volatile var result = 3 }
    class Declined(m: String) : IOException(m)
    class Paused(m: String) : IOException(m)
    class PeerPaused(m: String) : IOException(m)
    class JobInfo(val id: String, val name: String, val names: List<String>, val sizes: List<Long>)
    interface Ui {
        fun log(m: String); fun files(t: Transfer); fun started(t: Transfer); fun progress(t: Transfer); fun state(t: Transfer, s: String, msg: String)
        fun finished(t: Transfer, state: String, msg: String); fun approve(a: Approval); fun dismiss(id: Int); fun text(peer: String, t: String)
    }

    lateinit var app: Context
    lateinit var prefs: SharedPreferences
    @Volatile var ui: Ui? = null
    @Volatile var lastActive = System.currentTimeMillis()
    private val ids = AtomicInteger(1)
    val active = ConcurrentHashMap<Int, Transfer>()
    val approvals = ConcurrentHashMap<Int, Approval>()
    private val cancelledKeys = ConcurrentHashMap<String, Long>()          // transfers YOU cancelled: a reconnecting sender must not restart them
    private val approved = ConcurrentHashMap<String, Long>()                 // a transfer you accepted is never asked about again on reconnect
    private val pausedKeys: MutableSet<String> = ConcurrentHashMap.newKeySet()   // incoming transfers YOU paused: the sender waits
    private val memItems = ConcurrentHashMap<String, List<Item>>(); private val memJobs = ConcurrentHashMap<String, Pair<Dev, String>>()
    private var started = false
    private var tcp: ServerSocket? = null; private var udp: DatagramSocket? = null; private var bt: BluetoothServerSocket? = null
    private var wake: PowerManager.WakeLock? = null; private var mcast: WifiManager.MulticastLock? = null; private var wifi: WifiManager.WifiLock? = null

    @Synchronized fun init(c: Context) {
        if (::app.isInitialized) return
        app = c.applicationContext; prefs = app.getSharedPreferences("hs", Context.MODE_PRIVATE); Notifier.channels()
        val keep = jobs().flatMap { j -> val a = j.getJSONArray("items"); (0 until a.length()).map { a.getJSONObject(it).optString("src") } }.toSet()
        app.cacheDir.listFiles()?.filter { it.name.startsWith("tmp_") && "file:" + it.path !in keep }?.forEach { it.delete() }
        File(partialDir()).listFiles()?.filter { System.currentTimeMillis() - it.lastModified() > 7 * 86400000L }?.forEach { it.delete() }
    }
    fun cfg(k: String, d: String = "") = prefs.getString(k, d) ?: d
    fun trusted(): Set<String> = prefs.getStringSet("trusted", emptySet()) ?: emptySet()
    fun daemon(f: () -> Unit) { Thread { try { f() } catch (e: Exception) { if (e.message?.contains("closed", true) != true) log("Error: ${e.message}") } }.apply { isDaemon = true }.start() }
    fun log(m: String) { ui?.log(m) }
    fun touch() { lastActive = System.currentTimeMillis() }
    private fun partialDir() = File(app.getExternalFilesDir(null) ?: app.filesDir, "partial").apply { mkdirs() }.path
    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
    private fun fid(rel: String, size: Long, mtime: Long) = hex(MessageDigest.getInstance("SHA-1").digest("$rel|$size|$mtime".toByteArray())).substring(0, 20)
    private fun manifestKey(peer: String, files: JSONArray): String {
        val p = (0 until files.length()).map { val f = files.getJSONObject(it); "${f.optString("id")}:${f.optLong("size")}" }.sorted()
        return hex(MessageDigest.getInstance("SHA-1").digest((peer + "|" + p.joinToString("|")).toByteArray())).substring(0, 16)
    }
    private val badChars = Regex("[<>:\"|?*\\u0000-\\u001F]")
    private fun cleanSeg(s: String): String { val c = badChars.replace(s, "_").trimEnd('.', ' '); return if (c.isEmpty()) "_" else c }
    private fun readLine(i: InputStream, max: Int = 65536): String {
        val s = ByteArrayOutputStream(); while (true) { val c = i.read(); if (c < 0) { if (s.size() == 0) throw EOFException("connection closed"); break }; if (c == 10) break; s.write(c); if (s.size() > max) throw IOException("line too long") }
        return s.toString("UTF-8")
    }
    /** Big socket buffers + keep-alive (default buffers throttle Wi-Fi, and a dead link is noticed within ~30 s). */
    private fun tune(s: Socket) { try { s.tcpNoDelay = true; s.keepAlive = true; s.sendBufferSize = 4 shl 20; s.receiveBufferSize = 4 shl 20 } catch (_: Exception) {} }

    // ---------- transfer bookkeeping ----------
    @Suppress("DEPRECATION")
    @Synchronized private fun begin(label: String, incoming: Boolean): Transfer {
        val t = Transfer(ids.getAndIncrement(), label, incoming); active[t.id] = t; touch()
        if (wake == null) wake = (app.getSystemService(Context.POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "hotshare:xfer").apply { setReferenceCounted(false) }
        wake?.acquire(30 * 60 * 1000L)
        if (wifi == null) wifi = (app.getSystemService(Context.WIFI_SERVICE) as WifiManager).createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "hotshare:wifi").apply { setReferenceCounted(false) }
        try { wifi?.acquire() } catch (_: Exception) {}      // Wi-Fi power-save is a classic cause of stalls with the screen off
        try { app.startForegroundService(Intent(app, ReceiverService::class.java)) } catch (_: Exception) {}   // keeps the process alive during a transfer
        ui?.started(t); return t
    }
    @Synchronized private fun end(t: Transfer, state: String, msg: String) {
        active.remove(t.id); touch()
        if (active.isEmpty()) { if (wake?.isHeld == true) wake?.release(); if (wifi?.isHeld == true) wifi?.release() }
        t.state = state; ui?.finished(t, state, msg); Notifier.cancel(2000 + t.id)
        if (ui == null) Notifier.info(when (state) { "ok" -> "Done"; "paused" -> "Transfer paused"; else -> "Transfer stopped" }, "${t.label}: $msg")
    }
    private fun setFiles(t: Transfer, names: Array<String>, sizes: LongArray) { t.names = names; t.sizes = sizes; t.fdone = LongArray(names.size); ui?.files(t) }
    private fun setState(t: Transfer, s: String, msg: String) { t.state = s; ui?.state(t, s, msg) }
    fun cancel(id: Int? = null) { active.values.filter { id == null || it.id == id }.forEach { it.discard = true; it.cancelled = true } }   // cancel = stop and throw the cached data away
    fun pause(id: Int) { active[id]?.let { t -> t.paused = true; if (t.incoming && t.key.isNotEmpty()) pausedKeys.add(t.key) } }
    fun resumeIncoming(key: String) { pausedKeys.remove(key) }
    private fun tick(t: Transfer, done: Long) {
        if (t.cancelled) throw InterruptedIOException("cancelled")
        if (t.paused) throw Paused("paused")
        t.done = done; t.moved++; val now = System.currentTimeMillis()
        if (now - t.wakeAt > 60000) { t.wakeAt = now; wake?.acquire(30 * 60 * 1000L); touch() }    // never let the wake lock lapse during a long transfer
        if (now - t.last < 150 && done != t.total) return
        t.last = now; t.speed = (done - t.base) * 1000.0 / maxOf(1L, now - t.t0)
        val u = ui; if (u != null) u.progress(t) else Notifier.progress(t)
    }

    // ---------- saved jobs: an interrupted send survives a restart / power cut ----------
    fun jobs(): List<JSONObject> { val a = JSONArray(prefs.getString("jobs", "[]")); return (0 until a.length()).map { a.getJSONObject(it) } }
    private fun persistJob(id: String, dev: Dev, items: List<Item>, dest: String) {
        if (items.any { it.text == null && it.src == null }) return          // cannot be re-opened after a restart
        val arr = JSONArray(); items.forEach { x -> arr.put(JSONObject().put("t", if (x.text != null) "text" else "file").put("rel", x.rel).put("size", x.size).put("mtime", x.mtime).put("src", x.src ?: "").put("text", x.text ?: "")) }
        val job = JSONObject().put("id", id).put("name", dev.name).put("addr", dev.addr).put("port", dev.port).put("kind", dev.kind).put("dest", dest).put("items", arr)
        synchronized(this) { val all = jobs().filter { it.getString("id") != id }.toMutableList(); all.add(job); prefs.edit().putString("jobs", JSONArray(all).toString()).apply() }
    }
    private fun dropJob(id: String) {
        val j = jobs().firstOrNull { it.getString("id") == id }
        j?.getJSONArray("items")?.let { a -> for (i in 0 until a.length()) { val src = a.getJSONObject(i).optString("src"); if (src.startsWith("file:") && src.contains("/tmp_")) File(src.substring(5)).delete() } }   // staged copies are cache: erase them
        synchronized(this) { prefs.edit().putString("jobs", JSONArray(jobs().filter { it.getString("id") != id }).toString()).apply() }
        memItems.remove(id); memJobs.remove(id)
    }
    private fun itemFromRec(o: JSONObject): Item? {
        if (o.getString("t") == "text") return textItem(o.getString("text"))
        val src = o.optString("src"); val rel = o.getString("rel"); val size = o.getLong("size"); val mt = o.optLong("mtime")
        return when {
            src.startsWith("uri:") -> { val u = Uri.parse(src.substring(4)); Item(rel, size, mt, src = src) { app.contentResolver.openInputStream(u) ?: throw IOException("cannot open $rel") } }
            src.startsWith("file:") -> { val f = File(src.substring(5)); if (!f.exists()) null else Item(rel, size, mt, src = src) { FileInputStream(f) } }
            else -> null
        }
    }
    /** Jobs that are not running right now (paused, connection lost, or interrupted by a restart). */
    fun pendingJobs(): List<JobInfo> {
        val running = active.values.map { it.jobId }.toSet(); val out = ArrayList<JobInfo>(); val seen = HashSet<String>()
        for (j in jobs()) { val id = j.getString("id"); if (id in running) continue; seen.add(id); val a = j.getJSONArray("items")
            out.add(JobInfo(id, j.getString("name"), (0 until a.length()).map { val o = a.getJSONObject(it); if (o.getString("t") == "text") "Text message" else o.getString("rel") }, (0 until a.length()).map { a.getJSONObject(it).getLong("size") })) }
        for ((id, v) in memItems) if (id !in running && id !in seen) out.add(JobInfo(id, memJobs[id]?.first?.name ?: "device", v.map { if (it.text != null) "Text message" else it.rel }, v.map { it.size }))
        return out
    }
    fun resumeJob(id: String): Boolean {
        val j = jobs().firstOrNull { it.getString("id") == id }
        val items: List<Item>? = memItems[id] ?: j?.let { a -> val arr = a.getJSONArray("items"); (0 until arr.length()).mapNotNull { itemFromRec(arr.getJSONObject(it)) } }
        val target: Pair<Dev, String>? = memJobs[id] ?: j?.let { Pair(Dev(it.getString("name"), it.getString("addr"), it.getInt("port"), it.optString("kind")), it.optString("dest")) }
        if (items.isNullOrEmpty() || target == null) { dropJob(id); return false }
        daemon { sendTo(target.first, items, target.second, id) }; return true
    }
    fun discardJob(id: String) = dropJob(id)

    // ---------- cache ----------
    fun cacheBytes(): Long = (File(partialDir()).listFiles()?.sumOf { it.length() } ?: 0L) + (app.cacheDir.listFiles()?.filter { it.name.startsWith("tmp_") }?.sumOf { it.length() } ?: 0L)
    /** Erases partial files of unfinished transfers, staged copies and the interrupted-send list. Returns bytes freed, or -1 if a transfer is running. */
    fun clearCache(): Long {
        if (active.isNotEmpty()) return -1L
        val n = cacheBytes(); File(partialDir()).listFiles()?.forEach { it.delete() }; app.cacheDir.listFiles()?.filter { it.name.startsWith("tmp_") }?.forEach { it.delete() }
        prefs.edit().remove("jobs").apply(); memItems.clear(); memJobs.clear(); return n
    }

    // ---------- idle auto-quit ----------
    private fun bgEnabled() = prefs.getBoolean("bg", true)
    /** True when nothing has happened for a while with the app closed: stop listening, free everything, exit. */
    fun shouldQuit(): Boolean {
        if (ui != null || active.isNotEmpty() || approvals.isNotEmpty()) { touch(); return false }
        val limit = if (bgEnabled()) (cfg("idle_min", "3").toLongOrNull() ?: 3L) * 60000L else 30000L      // 0 minutes = never (only while background receiving is on)
        return limit > 0 && System.currentTimeMillis() - lastActive > limit
    }
    fun quit() {
        stopServers(); Notifier.cancel(1)
        if (bgEnabled()) Notifier.info("HotShare stopped", "No transfers for a while. Open the app to receive again.")
        app.stopService(Intent(app, ReceiverService::class.java)); android.os.Process.killProcess(android.os.Process.myPid())
    }

    // ---------- history ----------
    @Synchronized fun history(dir: String, name: String, size: Long, peer: String) {
        val a = JSONArray(prefs.getString("hist", "[]")); a.put(JSONObject().put("t", System.currentTimeMillis()).put("d", dir).put("n", name).put("s", size).put("p", peer))
        val o = JSONArray(); for (i in maxOf(0, a.length() - 200) until a.length()) o.put(a.get(i)); prefs.edit().putString("hist", o.toString()).apply()
    }

    // ---------- items ----------
    fun uriItem(u: Uri): Item? {
        app.contentResolver.query(u, null, null, null, null)?.use { c ->
            if (!c.moveToFirst()) return null
            val name = c.getString(c.getColumnIndexOrThrow(OpenableColumns.DISPLAY_NAME)); var size = c.getColumnIndex(OpenableColumns.SIZE).let { if (it >= 0 && !c.isNull(it)) c.getLong(it) else -1L }
            val mi = c.getColumnIndex("last_modified"); val mtime = if (mi >= 0 && !c.isNull(mi)) c.getLong(mi) / 1000 else 0L
            if (size <= 0) size = app.contentResolver.openAssetFileDescriptor(u, "r")?.use { it.length } ?: -1L
            if (size <= 0) {   // provider can't report size: stage a copy so the declared size is exact
                val f = File(app.cacheDir, "tmp_${System.nanoTime()}")
                (app.contentResolver.openInputStream(u) ?: return null).use { i -> f.outputStream().use { o -> i.copyTo(o, BUF) } }
                return Item(name, f.length(), mtime, src = "file:" + f.path) { f.inputStream() }
            }
            return Item(name, size, mtime, src = "uri:" + u) { app.contentResolver.openInputStream(u) ?: throw IOException("cannot open $name") }
        }
        return null
    }
    fun walk(f: DocumentFile, rel: String, out: MutableList<Item>) {
        for (c in f.listFiles()) if (c.isDirectory) walk(c, rel + "/" + c.name, out)
        else out.add(Item(rel + "/" + c.name, c.length(), c.lastModified() / 1000, src = "uri:" + c.uri) { app.contentResolver.openInputStream(c.uri) ?: throw IOException("cannot open") })
    }
    fun textItem(t: String): Item { val b = t.toByteArray(); return Item("text", b.size.toLong(), 0, t) { ByteArrayInputStream(b) } }

    // ---------- sending (auto-reconnect, pause / resume, saved jobs) ----------
    private fun reconnectMs() = (cfg("reconnect_s", "300").toLongOrNull() ?: 300L).coerceAtLeast(10L) * 1000L
    private fun sleepOrStop(t: Transfer, ms: Long): String? {
        val until = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < until) { if (t.cancelled) return "cancelled"; if (t.paused) return "paused"; try { Thread.sleep(150) } catch (_: InterruptedException) {} }
        return null
    }

    fun sendTo(dev: Dev, items: List<Item>, dest: String, existing: String? = null) {
        val jid = existing ?: UUID.randomUUID().toString().take(12)
        memItems[jid] = items; memJobs[jid] = Pair(dev, dest); persistJob(jid, dev, items, dest)       // saved first: survives a crash / power cut
        val t = begin("To ${dev.name}", false); t.jobId = jid
        var attempt = 0; var deadline = 0L; var seen = -1L
        while (true) {
            try { sendOnce(dev, items, dest, t); dropJob(jid); end(t, "ok", "Sent ${items.size} item(s)"); return }
            catch (e: IOException) {
                if (t.cancelled) { dropJob(jid); end(t, "cancelled", "cancelled; cached data discarded"); return }
                if (t.paused || e is Paused) { end(t, "paused", "press Resume to continue"); return }
                if (e is Declined) { dropJob(jid); end(t, "fail", e.message ?: "declined"); return }
                val delay: Long
                if (e is PeerPaused) { setState(t, "waiting", "Paused by the receiver - waiting for it to resume"); delay = 2000L }
                else {
                    if (e is ConnectException && t.moved == 0L && !t.connectedOnce) { end(t, "paused", "cannot reach ${dev.name} - is HotShare open there? Press Resume to retry"); return }
                    val now = System.currentTimeMillis()
                    if (t.moved != seen) { seen = t.moved; attempt = 0; deadline = 0L }          // progress since the last failure -> fresh window
                    if (deadline == 0L) deadline = now + reconnectMs()
                    if (now > deadline) { end(t, "paused", "connection lost - press Resume when the other device is back"); return }
                    attempt++; delay = minOf(10000L, (1500 * Math.pow(1.5, (attempt - 1).toDouble())).toLong())
                    setState(t, "reconnecting", "Connection lost - reconnecting in ${delay / 1000 + 1}s")
                }
                when (sleepOrStop(t, delay)) {
                    "cancelled" -> { dropJob(jid); end(t, "cancelled", "cancelled; cached data discarded"); return }
                    "paused" -> { end(t, "paused", "press Resume to continue"); return }
                }
            }
            catch (e: SecurityException) { dropJob(jid); end(t, "fail", "files are no longer accessible - add them again"); return }
            catch (e: Exception) { end(t, "paused", (e.message ?: "failed") + " - press Resume to retry"); return }
        }
    }
    private fun sendOnce(dev: Dev, items: List<Item>, dest: String, t: Transfer) {
        setState(t, "connecting", "Connecting...")
        val sock: Closeable; val rin: InputStream; val rout: OutputStream; var plain: Socket? = null
        if (dev.addr.startsWith("bt:")) {
            val ad = BluetoothAdapter.getDefaultAdapter(); val s = ad.getRemoteDevice(dev.addr.substring(3)).createRfcommSocketToServiceRecord(BT_UUID); ad.cancelDiscovery(); s.connect()
            sock = s; rin = s.inputStream; rout = s.outputStream
        } else {
            bindLanNow(); val s = Socket(); tune(s); s.connect(InetSocketAddress(dev.addr, dev.port), 8000); s.soTimeout = 180000   // long: the receiver may be waiting for a tap on Accept
            plain = s; sock = s; rin = s.inputStream; rout = s.outputStream
        }
        sock.use {
            val inp = BufferedInputStream(rin, BUF); val out = BufferedOutputStream(rout, BUF)
            val man = JSONArray(); val ids = HashMap<Item, String>(); val sent = ArrayList<Item>(); var total = 0L
            for (item in items) {
                if (item.text != null) { man.put(JSONObject().put("id", "t${sent.size}").put("t", "text").put("path", "text").put("size", item.size).put("text", item.text)); sent.add(item) }
                else { val id = fid(item.rel, item.size, item.mtime); if (ids.containsValue(id)) continue; ids[item] = id; sent.add(item); total += item.size; man.put(JSONObject().put("id", id).put("t", "file").put("path", item.rel).put("size", item.size)) }
            }
            out.write("HSH3\n${JSONObject().put("name", cfg("name", Build.MODEL)).put("pin", cfg("pin")).put("dest", dest).put("files", man)}\n".toByteArray()); out.flush()
            val resp = readLine(inp)
            if (resp.startsWith("ERR")) { val m = resp.drop(4); throw if (m == "paused") PeerPaused(m) else Declined(m) }
            if (resp != "OK") throw Declined("${dev.name} declined (or PIN mismatch)")
            t.connectedOnce = true; plain?.soTimeout = 60000; setState(t, "running", "")
            val offs = JSONObject(readLine(inp, MAXH)).getJSONObject("off")
            var done = 0L; for (id in ids.values) done += offs.optLong(id, 0)
            t.total = total; t.base = done; t.t0 = System.currentTimeMillis(); val buf = ByteArray(BUF); val md = MessageDigest.getInstance("SHA-256")
            setFiles(t, sent.map { if (it.text != null) "Text message" else it.rel }.toTypedArray(), sent.map { it.size }.toLongArray())
            sent.forEachIndexed { ix, x -> val xid = ids[x]; t.fdone[ix] = if (xid == null) x.size else offs.optLong(xid, 0).coerceIn(0, x.size) }
            for ((ix, item) in sent.withIndex()) {
                if (item.text != null) continue
                val id = ids[item]!!; val off = offs.optLong(id, 0).coerceIn(0, item.size); md.reset()
                item.open().use { s ->
                    var left = off; while (left > 0) { val n = s.read(buf, 0, minOf(buf.size.toLong(), left).toInt()); if (n < 0) throw IOException("file changed"); md.update(buf, 0, n); left -= n }
                    left = item.size - off
                    while (left > 0) { val n = s.read(buf, 0, minOf(buf.size.toLong(), left).toInt()); if (n < 0) throw IOException("file shrank"); out.write(buf, 0, n); md.update(buf, 0, n); left -= n; done += n; t.fdone[ix] = item.size - left; tick(t, done) }
                }
                out.write((hex(md.digest()) + "\n").toByteArray()); t.fdone[ix] = item.size
            }
            out.flush(); val r = readLine(inp)
            if (r == "ERR paused") throw PeerPaused("paused")
            if (r != "DONE") throw IOException(r.ifEmpty { "no reply" })
            tick(t, total); sent.forEach { history("out", it.rel, it.size, dev.name) }
        }
    }

    // ---------- receiving ----------
    private fun approve(peer: String, n: Int, total: Long): Boolean {
        val a = Approval(ids.getAndIncrement(), peer, n, total); approvals[a.id] = a
        val u = ui; if (u != null) u.approve(a) else Notifier.approval(a)
        a.latch.await(90, TimeUnit.SECONDS); approvals.remove(a.id); ui?.dismiss(a.id); Notifier.cancel(a.id)
        if (a.result == 2) prefs.edit().putStringSet("trusted", trusted() + peer).apply()
        return a.result == 1 || a.result == 2
    }
    fun answer(id: Int, res: Int) { approvals[id]?.let { it.result = res; it.latch.countDown() } }
    fun onUiGone() { approvals.values.forEach { Notifier.approval(it) } }

    private fun handle(rin: InputStream, rout: OutputStream) {
        val inp = BufferedInputStream(rin, BUF); val out = BufferedOutputStream(rout, BUF)
        val first = readLine(inp)
        if (first == "HSH?") {   // discovery over the transfer port (works where UDP broadcast is blocked)
            if (prefs.getBoolean("visible", true)) { out.write("${JSONObject().put("name", cfg("name", Build.MODEL)).put("port", cfg("port", "45455").toInt()).put("os", "Android")}\n".toByteArray()); out.flush() }
            return
        }
        if (first != "HSH3") return
        touch()
        val h = JSONObject(readLine(inp, MAXH)); val peer = h.optString("name", "?"); val pin = cfg("pin"); val files = h.getJSONArray("files")
        if (files.length() > 200000) throw IOException("too many files")
        var total = 0L
        for (i in 0 until files.length()) { val fo = files.getJSONObject(i); if (fo.getString("t") == "file") { val sz = fo.getLong("size"); if (sz < 0) throw IOException("bad manifest"); total += sz } }
        val key = manifestKey(peer, files); var ok = pin.isEmpty() || h.optString("pin") == pin
        if (ok && key in pausedKeys) { out.write("ERR paused\n".toByteArray()); out.flush(); return }
        val cancelledAt = cancelledKeys[key]
        if (ok && cancelledAt != null && System.currentTimeMillis() - cancelledAt < 30000L) { out.write("ERR cancelled by the receiver\n".toByteArray()); out.flush(); return }
        val prior = approved[key]; val alreadyApproved = prior != null && System.currentTimeMillis() - prior < 3600000L     // reconnects never re-prompt
        if (ok && !alreadyApproved && !prefs.getBoolean("auto", false) && peer !in trusted()) ok = approve(peer, files.length(), total)
        if (!ok) { out.write("NO\n".toByteArray()); out.flush(); log("Declined transfer from $peer"); return }
        approved[key] = System.currentTimeMillis()
        val t = begin(peer, true); t.total = total; t.key = key; var got = 0; val parts = HashMap<String, File>()
        try {
            val pdir = File(partialDir()); val offs = JSONObject(); var done = 0L
            for (i in 0 until files.length()) {
                val f = files.getJSONObject(i); if (f.getString("t") != "file") continue
                val p = File(pdir, f.getString("id").filter { it.isLetterOrDigit() }.take(40) + ".part"); parts[f.getString("id")] = p
                var n = if (p.exists()) p.length() else 0L; if (n > f.getLong("size")) { p.delete(); n = 0 }
                offs.put(f.getString("id"), n); done += n
            }
            t.parts = parts.values.toList()
            if (pdir.usableSpace < total - done) { out.write("ERR not enough free space\n".toByteArray()); out.flush(); throw Declined("not enough free space") }
            out.write("OK\n${JSONObject().put("off", offs)}\n".toByteArray()); out.flush()
            setFiles(t, Array(files.length()) { val fo = files.getJSONObject(it); if (fo.getString("t") == "file") fo.getString("path") else "Text message" }, LongArray(files.length()) { files.getJSONObject(it).optLong("size", 0) })
            for (i in 0 until files.length()) { val fo = files.getJSONObject(i); if (fo.getString("t") == "file") t.fdone[i] = offs.getLong(fo.getString("id")) }
            t.base = done; t.t0 = System.currentTimeMillis(); val buf = ByteArray(BUF); val md = MessageDigest.getInstance("SHA-256")
            val dest = h.optString("dest").replace("\\", "/").split("/").filter { it.isNotEmpty() && it != "." && it != ".." }.map { cleanSeg(it) }
            for (i in 0 until files.length()) {
                val f = files.getJSONObject(i)
                if (f.getString("t") == "text") { val s = f.optString("text").take(1_000_000); val u = ui; if (u != null) u.text(peer, s) else Notifier.info("Text from $peer", s.take(300)); history("in", "text", s.toByteArray().size.toLong(), peer); t.fdone[i] = t.sizes[i]; continue }
                val rel = f.getString("path"); val size = f.getLong("size"); val part = parts[f.getString("id")]!!; val off = offs.getLong(f.getString("id")); md.reset()
                if (off > 0) { log("Resuming $rel from ${off / 1048576} MB"); FileInputStream(part).use { s -> while (true) { val n = s.read(buf); if (n < 0) break; md.update(buf, 0, n) } } }
                var left = size - off
                FileOutputStream(part, true).use { o -> while (left > 0) { val n = inp.read(buf, 0, minOf(buf.size.toLong(), left).toInt()); if (n < 0) throw IOException("connection lost"); o.write(buf, 0, n); md.update(buf, 0, n); left -= n; done += n; t.fdone[i] = size - left; tick(t, done) } }
                if (readLine(inp) != hex(md.digest())) { part.delete(); throw IOException("checksum mismatch: $rel") }
                val segs = rel.replace("\\", "/").split("/").filter { it.isNotEmpty() && it != "." && it != ".." }.map { cleanSeg(it) }
                saveFinal(part, dest + segs.dropLast(1), segs.lastOrNull() ?: "file"); part.delete(); got++; t.fdone[i] = size     // cache erased as soon as the file is safely saved
                history("in", rel, size, peer); log("Received $rel (verified)")
            }
            out.write("DONE\n".toByteArray()); out.flush(); tick(t, total); end(t, "ok", "Received $got file(s)")
        } catch (e: Exception) {
            try { out.write((if (e is Paused) "ERR paused\n" else "ERR ${e.message}\n").toByteArray()); out.flush() } catch (_: Exception) {}
            if (t.discard) parts.values.forEach { it.delete() }
            if (t.cancelled) { approved.remove(key); cancelledKeys[key] = System.currentTimeMillis() }
            when {
                e is Paused -> end(t, "paused", "partial data kept. Press Resume and the sender continues")
                t.cancelled -> end(t, "cancelled", "cancelled; cached data discarded")
                else -> end(t, "fail", (e.message ?: "failed") + " - partial data kept, the sender reconnects on its own")
            }
        }
    }

    /** Custom folder (SAF tree chosen in Settings) or Downloads/HotShare via MediaStore. */
    private fun saveFinal(part: File, dirs: List<String>, name: String) {
        val tree = prefs.getString("tree", null)
        if (tree != null) {
            var d = DocumentFile.fromTreeUri(app, Uri.parse(tree)) ?: throw IOException("save folder unavailable - choose it again in Settings")
            for (p in dirs) d = d.findFile(p)?.takeIf { it.isDirectory } ?: d.createDirectory(p) ?: throw IOException("cannot create $p")
            var n = name; var i = 1; val b = name.substringBeforeLast('.', name); val e = name.substringAfterLast('.', "")
            while (d.findFile(n) != null) n = if (e.isEmpty()) "$b (${i++})" else "$b (${i++}).$e"
            val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(e.lowercase()) ?: "application/octet-stream"
            val f = d.createFile(mime, n) ?: throw IOException("cannot create $n")
            (app.contentResolver.openOutputStream(f.uri) ?: throw IOException("cannot write")).use { o -> part.inputStream().use { it.copyTo(o, BUF) } }
        } else if (Build.VERSION.SDK_INT >= 29) {
            val cv = ContentValues().apply { put(MediaStore.Downloads.DISPLAY_NAME, name); put(MediaStore.Downloads.RELATIVE_PATH, "Download/HotShare" + dirs.joinToString("") { "/$it" }); put(MediaStore.Downloads.IS_PENDING, 1) }
            val uri = app.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv) ?: throw IOException("MediaStore refused file")
            try { (app.contentResolver.openOutputStream(uri) ?: throw IOException("cannot write")).use { o -> part.inputStream().use { it.copyTo(o, BUF) } } }
            catch (e: Exception) { app.contentResolver.delete(uri, null, null); throw e }
            cv.clear(); cv.put(MediaStore.Downloads.IS_PENDING, 0); app.contentResolver.update(uri, cv, null, null)
        } else {   // Android 8-9: no scoped MediaStore -> app folder (no permission needed); pick a custom folder in the app for a nicer location
            val dir = File(app.getExternalFilesDir(null) ?: app.filesDir, "HotShare" + dirs.joinToString("") { "/$it" }).apply { mkdirs() }
            var f = File(dir, name); var i = 1; val b = name.substringBeforeLast('.', name); val e = name.substringAfterLast('.', "")
            while (f.exists()) f = File(dir, if (e.isEmpty()) "$b (${i++})" else "$b (${i++}).$e")
            part.copyTo(f)
        }
    }

    // ---------- servers ----------
    @Synchronized fun startServers() {
        if (started) return; started = true; bindLan()
        mcast = (app.getSystemService(Context.WIFI_SERVICE) as WifiManager).createMulticastLock("hs").apply { setReferenceCounted(false); acquire() }
        daemon { tcpLoop() }; daemon { udpLoop() }; daemon { btLoop() }
    }
    @Synchronized fun stopServers() {
        started = false; try { tcp?.close() } catch (_: Exception) {}; try { udp?.close() } catch (_: Exception) {}; try { bt?.close() } catch (_: Exception) {}
        if (mcast?.isHeld == true) mcast?.release()
    }
    fun restartServers() { stopServers(); Thread.sleep(300); startServers() }
    private fun tcpLoop() {
        val ss = ServerSocket().apply { reuseAddress = true; receiveBufferSize = 4 shl 20; bind(InetSocketAddress(cfg("port", "45455").toInt())) }; tcp = ss
        while (!ss.isClosed) {
            val c = try { ss.accept() } catch (_: IOException) { break }
            daemon { tune(c); c.soTimeout = 45000; c.use { try { handle(it.getInputStream(), it.getOutputStream()) } catch (e: Exception) { log("Receive error: ${e.message}") } } }
        }
    }
    private fun udpLoop() {
        val s = DatagramSocket(null).apply { reuseAddress = true; bind(InetSocketAddress(DISC)) }; udp = s; val b = ByteArray(256)
        while (!s.isClosed) { val p = DatagramPacket(b, b.size); try { s.receive(p) } catch (_: IOException) { break }
            if (prefs.getBoolean("visible", true) && String(p.data, 0, p.length).startsWith("HSH?")) {
                val r = JSONObject().put("name", cfg("name", Build.MODEL)).put("port", cfg("port", "45455").toInt()).put("os", "Android").toString().toByteArray(); try { s.send(DatagramPacket(r, r.size, p.address, p.port)) } catch (_: IOException) {} } }
    }
    private fun btLoop() {
        val a = BluetoothAdapter.getDefaultAdapter() ?: return
        val ss = try { a.listenUsingRfcommWithServiceRecord("HotShare", BT_UUID) } catch (e: SecurityException) { return }; bt = ss
        while (true) { val c = try { ss.accept() } catch (_: IOException) { break }; daemon { c.use { handle(it.inputStream, it.outputStream) } } }
    }
    fun localIps(): List<String> = try {
        NetworkInterface.getNetworkInterfaces()?.toList()?.filter { it.isUp && !it.isLoopback }
            ?.flatMap { ni -> ni.interfaceAddresses.mapNotNull { (it.address as? Inet4Address)?.hostAddress } } ?: emptyList()
    } catch (e: Exception) { emptyList() }

    private var netCb: ConnectivityManager.NetworkCallback? = null

    /** Android routes app traffic over mobile data when the Wi-Fi has no internet, so LAN scans/sends silently fail.
     *  Pinning the process to the Wi-Fi/Ethernet network fixes that. */
    @Suppress("DEPRECATION")
    fun bindLanNow() {
        try {
            val cm = app.getSystemService(ConnectivityManager::class.java)
            val n = cm.allNetworks.firstOrNull { val c = cm.getNetworkCapabilities(it); c != null && (c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || c.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) }
            if (n != null) cm.bindProcessToNetwork(n)
        } catch (_: Exception) {}
    }
    fun bindLan() {
        try {
            val cm = app.getSystemService(ConnectivityManager::class.java); bindLanNow(); netCb?.let { cm.unregisterNetworkCallback(it) }
            val req = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).addTransportType(NetworkCapabilities.TRANSPORT_ETHERNET).build()
            val cb = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) { try { cm.bindProcessToNetwork(network) } catch (_: Exception) {} }
                override fun onLost(network: Network) { try { cm.bindProcessToNetwork(null) } catch (_: Exception) {}; bindLanNow() }
            }
            cm.registerNetworkCallback(req, cb); netCb = cb
        } catch (_: Exception) {}
    }
    private fun lanIps(): List<String> = try {
        NetworkInterface.getNetworkInterfaces()?.toList()?.filter { it.isUp && !it.isLoopback && !Regex("rmnet|ccmni|pdp|dummy|tun|ppp|v4-").containsMatchIn(it.name) }
            ?.flatMap { ni -> ni.interfaceAddresses.mapNotNull { (it.address as? Inet4Address)?.hostAddress } } ?: emptyList()
    } catch (e: Exception) { emptyList() }
    private fun probe(ip: String, port: Int): Dev? = try {
        Socket().use { s ->
            s.connect(InetSocketAddress(ip, port), 450); s.soTimeout = 700; s.getOutputStream().apply { write("HSH?\n".toByteArray()); flush() }
            val j = JSONObject(readLine(BufferedInputStream(s.getInputStream()))); Dev(j.getString("name"), ip, j.getInt("port"), j.optString("os"))
        }
    } catch (e: Exception) { null }
    /** Fallback when broadcasts are blocked: ask every host of the local /24 on the transfer port. */
    private fun sweep(port: Int): List<Dev> {
        val mine = lanIps().toSet(); val hosts = LinkedHashSet<String>()
        for (ip in mine) { val pre = ip.substringBeforeLast('.'); for (n in 1..254) if ("$pre.$n" !in mine) hosts.add("$pre.$n") }
        val found = ConcurrentHashMap<String, Dev>(); val pool = java.util.concurrent.Executors.newFixedThreadPool(64)
        hosts.forEach { h -> pool.execute { probe(h, port)?.let { found[h] = it } } }
        pool.shutdown(); pool.awaitTermination(12, TimeUnit.SECONDS); return found.values.toList()
    }

    fun scan(): List<Dev> {
        bindLanNow()
        val s = DatagramSocket().apply { broadcast = true; soTimeout = 1500 }; val q = "HSH?".toByteArray()
        val nis = NetworkInterface.getNetworkInterfaces()?.toList() ?: emptyList(); val targets = HashSet<InetAddress>(); targets.add(InetAddress.getByName("255.255.255.255"))
        for (ni in nis) for (ia in ni.interfaceAddresses) ia.broadcast?.let { targets.add(it) }
        for (t in targets) try { s.send(DatagramPacket(q, q.size, t, DISC)) } catch (_: Exception) {}
        val mine = nis.flatMap { it.inetAddresses.toList() }.map { it.hostAddress }; val res = LinkedHashMap<String, Dev>(); val b = ByteArray(512)
        try { while (true) { val p = DatagramPacket(b, b.size); s.receive(p); val ip = p.address.hostAddress ?: continue; if (ip in mine) continue
            try { val j = JSONObject(String(p.data, 0, p.length)); res[ip] = Dev(j.getString("name"), ip, j.getInt("port"), j.optString("os")) } catch (_: Exception) {} } } catch (_: SocketTimeoutException) {}
        s.close(); val found = res.values.toList()
        return if (found.isNotEmpty()) found else sweep(cfg("port", "45455").toInt())
    }
}

object Notifier {
    private fun nm() = Core.app.getSystemService(NotificationManager::class.java)
    private var lastP = 0L
    fun channels() { nm().createNotificationChannel(NotificationChannel("svc", "Background receiving", NotificationManager.IMPORTANCE_LOW)); nm().createNotificationChannel(NotificationChannel("hs", "Transfers", NotificationManager.IMPORTANCE_HIGH)) }
    fun foreground(): Notification = Notification.Builder(Core.app, "svc").setSmallIcon(R.drawable.ic_stat).setContentTitle("HotShare is ready to receive").setContentText("Visible to nearby devices")
        .setContentIntent(PendingIntent.getActivity(Core.app, 0, Intent(Core.app, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)).setOngoing(true).build()
    fun approval(a: Core.Approval) {
        fun pi(res: Int) = PendingIntent.getBroadcast(Core.app, a.id * 10 + res, Intent(Core.app, ActionReceiver::class.java).putExtra("id", a.id).putExtra("res", res), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm().notify(a.id, Notification.Builder(Core.app, "hs").setSmallIcon(R.drawable.ic_stat).setContentTitle("Incoming from ${a.peer}").setContentText("${a.count} item(s), ${a.total / 1048576} MB")
            .addAction(Notification.Action.Builder(0, "Accept", pi(1)).build()).addAction(Notification.Action.Builder(0, "Always", pi(2)).build()).addAction(Notification.Action.Builder(0, "Decline", pi(3)).build()).setAutoCancel(true).build())
    }
    fun info(title: String, text: String) { nm().notify((System.nanoTime() % 100000).toInt() + 5000, Notification.Builder(Core.app, "hs").setSmallIcon(R.drawable.ic_stat).setContentTitle(title).setContentText(text).setAutoCancel(true).build()) }
    fun progress(t: Core.Transfer) {
        val now = System.currentTimeMillis(); if (now - lastP < 1000) return; lastP = now
        nm().notify(2000 + t.id, Notification.Builder(Core.app, "svc").setSmallIcon(R.drawable.ic_stat).setContentTitle(t.label).setContentText("%.1f MB/s".format(t.speed / 1048576))
            .setProgress(1000, if (t.total > 0) (t.done * 1000 / t.total).toInt() else 0, false).setOngoing(true).build())
    }
    fun cancel(id: Int) = nm().cancel(id)
}
