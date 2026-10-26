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
    const val BUF = 1 shl 18
    private const val MAXH = 16 shl 20
    val BT_UUID: UUID = UUID.fromString("7d3c1a52-9b1e-4c11-8f55-0a1b2c3d4e5f")

    class Item(val rel: String, val size: Long, val mtime: Long, val text: String? = null, val open: () -> InputStream)
    class Transfer(val id: Int, val label: String, val incoming: Boolean) {
        @Volatile var done = 0L; @Volatile var total = 0L; @Volatile var cancelled = false; @Volatile var speed = 0.0
        var base = 0L; var t0 = 0L; var last = 0L
        val eta: Int get() = if (speed > 0) ((total - done) / speed).toInt() else 0
    }
    class Approval(val id: Int, val peer: String, val count: Int, val total: Long) { val latch = CountDownLatch(1); @Volatile var result = 3 }
    class Declined(m: String) : IOException(m)
    interface Ui {
        fun log(m: String); fun started(t: Transfer); fun progress(t: Transfer); fun finished(t: Transfer, ok: Boolean, msg: String)
        fun approve(a: Approval); fun dismiss(id: Int); fun text(peer: String, t: String)
    }

    lateinit var app: Context
    lateinit var prefs: SharedPreferences
    @Volatile var ui: Ui? = null
    private val ids = AtomicInteger(1)
    val active = ConcurrentHashMap<Int, Transfer>()
    val approvals = ConcurrentHashMap<Int, Approval>()
    private var started = false
    private var tcp: ServerSocket? = null; private var udp: DatagramSocket? = null; private var bt: BluetoothServerSocket? = null
    private var wake: PowerManager.WakeLock? = null; private var mcast: WifiManager.MulticastLock? = null

    @Synchronized fun init(c: Context) {
        if (::app.isInitialized) return
        app = c.applicationContext; prefs = app.getSharedPreferences("hs", Context.MODE_PRIVATE); Notifier.channels()
        app.cacheDir.listFiles()?.filter { it.name.startsWith("tmp_") }?.forEach { it.delete() }
        File(partialDir()).listFiles()?.filter { System.currentTimeMillis() - it.lastModified() > 7 * 86400000L }?.forEach { it.delete() }
    }
    fun cfg(k: String, d: String = "") = prefs.getString(k, d) ?: d
    fun trusted(): Set<String> = prefs.getStringSet("trusted", emptySet()) ?: emptySet()
    fun daemon(f: () -> Unit) { Thread { try { f() } catch (e: Exception) { if (e.message?.contains("closed", true) != true) log("Error: ${e.message}") } }.apply { isDaemon = true }.start() }
    fun log(m: String) { ui?.log(m) }
    private fun partialDir() = File(app.getExternalFilesDir(null) ?: app.filesDir, "partial").apply { mkdirs() }.path
    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
    private fun fid(rel: String, size: Long, mtime: Long) = hex(MessageDigest.getInstance("SHA-1").digest("$rel|$size|$mtime".toByteArray())).substring(0, 20)
    private fun readLine(i: InputStream, max: Int = 65536): String {
        val s = ByteArrayOutputStream(); while (true) { val c = i.read(); if (c < 0) { if (s.size() == 0) throw EOFException("connection closed"); break }; if (c == 10) break; s.write(c); if (s.size() > max) throw IOException("line too long") }
        return s.toString("UTF-8")
    }

    // ---------- transfer bookkeeping ----------
    @Synchronized private fun begin(label: String, incoming: Boolean): Transfer {
        val t = Transfer(ids.getAndIncrement(), label, incoming); active[t.id] = t
        if (wake == null) wake = (app.getSystemService(Context.POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "hotshare:xfer").apply { setReferenceCounted(false) }
        wake?.acquire(30 * 60 * 1000L); ui?.started(t); return t
    }
    @Synchronized private fun end(t: Transfer, ok: Boolean, msg: String) {
        active.remove(t.id); if (active.isEmpty() && wake?.isHeld == true) wake?.release()
        ui?.finished(t, ok, msg); Notifier.cancel(2000 + t.id); if (ui == null) Notifier.info(if (ok) "Done" else "Transfer stopped", "${t.label}: $msg")
    }
    fun cancel(id: Int? = null) { active.values.filter { id == null || it.id == id }.forEach { it.cancelled = true } }
    private fun tick(t: Transfer, done: Long) {
        if (t.cancelled) throw InterruptedIOException("cancelled")
        t.done = done; val now = System.currentTimeMillis()
        if (now - t.last < 150 && done != t.total) return
        t.last = now; t.speed = (done - t.base) * 1000.0 / maxOf(1L, now - t.t0)
        val u = ui; if (u != null) u.progress(t) else Notifier.progress(t)
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
                return Item(name, f.length(), mtime) { f.inputStream() }
            }
            return Item(name, size, mtime) { app.contentResolver.openInputStream(u) ?: throw IOException("cannot open $name") }
        }
        return null
    }
    fun walk(f: DocumentFile, rel: String, out: MutableList<Item>) {
        for (c in f.listFiles()) if (c.isDirectory) walk(c, rel + "/" + c.name, out)
        else out.add(Item(rel + "/" + c.name, c.length(), c.lastModified() / 1000) { app.contentResolver.openInputStream(c.uri) ?: throw IOException("cannot open") })
    }
    fun textItem(t: String): Item { val b = t.toByteArray(); return Item("text", b.size.toLong(), 0, t) { ByteArrayInputStream(b) } }

    // ---------- sending (auto-resume) ----------
    fun sendTo(dev: Dev, items: List<Item>, dest: String) {
        val t = begin("→ ${dev.name}", false); var attempt = 0
        while (true) {
            try { sendOnce(dev, items, dest, t); end(t, true, "Sent ${items.size} item(s)"); return }
            catch (e: IOException) {
                if (t.cancelled) { end(t, false, "Cancelled"); return }
                if (e is Declined || ++attempt > 3) { end(t, false, e.message ?: "failed"); return }
                log("Connection problem – resuming (attempt $attempt)…"); try { Thread.sleep(1000L * attempt) } catch (_: InterruptedException) {}
            } catch (e: Exception) { end(t, false, e.message ?: "failed"); return }
        }
    }
    private fun sendOnce(dev: Dev, items: List<Item>, dest: String, t: Transfer) {
        val sock: Closeable; val rin: InputStream; val rout: OutputStream
        if (dev.addr.startsWith("bt:")) {
            val ad = BluetoothAdapter.getDefaultAdapter(); val s = ad.getRemoteDevice(dev.addr.substring(3)).createRfcommSocketToServiceRecord(BT_UUID); ad.cancelDiscovery(); s.connect()
            sock = s; rin = s.inputStream; rout = s.outputStream
        } else {
            val s = Socket(); s.connect(InetSocketAddress(dev.addr, dev.port), 8000); s.soTimeout = 180000   // longer than the receiver's 90 s approval wait
            sock = s; rin = s.inputStream; rout = s.outputStream
        }
        sock.use {
            val inp = BufferedInputStream(rin, BUF); val out = BufferedOutputStream(rout, BUF)
            val man = JSONArray(); val ids = HashMap<Item, String>(); var total = 0L
            items.forEachIndexed { n, it ->
                if (it.text != null) man.put(JSONObject().put("id", "t$n").put("t", "text").put("path", "text").put("size", it.size).put("text", it.text))
                else { val id = fid(it.rel, it.size, it.mtime); if (ids.containsValue(id)) return@forEachIndexed; ids[it] = id; total += it.size; man.put(JSONObject().put("id", id).put("t", "file").put("path", it.rel).put("size", it.size)) }
            }
            out.write("HSH3\n${JSONObject().put("name", cfg("name", Build.MODEL)).put("pin", cfg("pin")).put("dest", dest).put("files", man)}\n".toByteArray()); out.flush()
            val resp = readLine(inp); if (resp.startsWith("ERR")) throw Declined(resp.drop(4)); if (resp != "OK") throw Declined("${dev.name} declined (or PIN mismatch)")
            val offs = JSONObject(readLine(inp, MAXH)).getJSONObject("off")
            var done = 0L; for (id in ids.values) done += offs.optLong(id, 0)
            t.total = total; t.base = done; t.t0 = System.currentTimeMillis(); val buf = ByteArray(BUF); val md = MessageDigest.getInstance("SHA-256")
            for (item in items) {
                val id = ids[item] ?: continue; val off = offs.optLong(id, 0).coerceIn(0, item.size); md.reset()
                item.open().use { s ->
                    var left = off; while (left > 0) { val n = s.read(buf, 0, minOf(buf.size.toLong(), left).toInt()); if (n < 0) throw IOException("file changed"); md.update(buf, 0, n); left -= n }
                    left = item.size - off
                    while (left > 0) { val n = s.read(buf, 0, minOf(buf.size.toLong(), left).toInt()); if (n < 0) throw IOException("file shrank"); out.write(buf, 0, n); md.update(buf, 0, n); left -= n; done += n; tick(t, done) }
                }
                out.write((hex(md.digest()) + "\n").toByteArray())
            }
            out.flush(); val r = readLine(inp); if (r != "DONE") throw IOException(r.ifEmpty { "no reply" })
            tick(t, total); items.forEach { history("out", it.rel, it.size, dev.name) }
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
        if (readLine(inp) != "HSH3") return
        val h = JSONObject(readLine(inp, MAXH)); val peer = h.optString("name", "?"); val pin = cfg("pin"); val files = h.getJSONArray("files")
        if (files.length() > 200000) throw IOException("too many files")
        var total = 0L
        for (i in 0 until files.length()) { val fo = files.getJSONObject(i); if (fo.getString("t") == "file") { val sz = fo.getLong("size"); if (sz < 0) throw IOException("bad manifest"); total += sz } }
        var ok = pin.isEmpty() || h.optString("pin") == pin
        if (ok && !prefs.getBoolean("auto", false) && peer !in trusted()) ok = approve(peer, files.length(), total)
        if (!ok) { out.write("NO\n".toByteArray()); out.flush(); log("Declined transfer from $peer"); return }
        val t = begin(peer, true); t.total = total; var got = 0
        try {
            val pdir = File(partialDir()); val offs = JSONObject(); val parts = HashMap<String, File>(); var done = 0L
            for (i in 0 until files.length()) {
                val f = files.getJSONObject(i); if (f.getString("t") != "file") continue
                val p = File(pdir, f.getString("id").filter { it.isLetterOrDigit() }.take(40) + ".part"); parts[f.getString("id")] = p
                var n = if (p.exists()) p.length() else 0L; if (n > f.getLong("size")) { p.delete(); n = 0 }
                offs.put(f.getString("id"), n); done += n
            }
            if (pdir.usableSpace < total - done) { out.write("ERR not enough free space\n".toByteArray()); out.flush(); throw Declined("not enough free space") }
            out.write("OK\n${JSONObject().put("off", offs)}\n".toByteArray()); out.flush()
            t.base = done; t.t0 = System.currentTimeMillis(); val buf = ByteArray(BUF); val md = MessageDigest.getInstance("SHA-256")
            val dest = h.optString("dest").replace("\\", "/").split("/").filter { it.isNotEmpty() && it != "." && it != ".." && !it.contains(":") }
            for (i in 0 until files.length()) {
                val f = files.getJSONObject(i)
                if (f.getString("t") == "text") { val s = f.optString("text").take(1_000_000); val u = ui; if (u != null) u.text(peer, s) else Notifier.info("Text from $peer", s.take(300)); history("in", "text", s.toByteArray().size.toLong(), peer); continue }
                val rel = f.getString("path"); val size = f.getLong("size"); val part = parts[f.getString("id")]!!; val off = offs.getLong(f.getString("id")); md.reset()
                if (off > 0) { log("Resuming $rel from ${off / 1048576} MB"); FileInputStream(part).use { s -> while (true) { val n = s.read(buf); if (n < 0) break; md.update(buf, 0, n) } } }
                var left = size - off
                FileOutputStream(part, true).use { o -> while (left > 0) { val n = inp.read(buf, 0, minOf(buf.size.toLong(), left).toInt()); if (n < 0) throw IOException("connection lost"); o.write(buf, 0, n); md.update(buf, 0, n); left -= n; done += n; tick(t, done) } }
                if (readLine(inp) != hex(md.digest())) { part.delete(); throw IOException("checksum mismatch: $rel") }
                val segs = rel.replace("\\", "/").split("/").filter { it.isNotEmpty() && it != "." && it != ".." && !it.contains(":") }
                saveFinal(part, dest + segs.dropLast(1), segs.lastOrNull() ?: "file"); part.delete(); got++
                history("in", rel, size, peer); log("Received $rel ✓ verified")
            }
            out.write("DONE\n".toByteArray()); out.flush(); tick(t, total); end(t, true, "Received $got file(s)")
        } catch (e: Exception) {
            try { out.write("ERR ${e.message}\n".toByteArray()); out.flush() } catch (_: Exception) {}
            end(t, false, (e.message ?: "failed") + if (e is IOException && !t.cancelled) " (partial kept)" else "")
        }
    }

    /** Custom folder (SAF tree chosen in Settings) or Downloads/HotShare via MediaStore. */
    private fun saveFinal(part: File, dirs: List<String>, name: String) {
        val tree = prefs.getString("tree", null)
        if (tree != null) {
            var d = DocumentFile.fromTreeUri(app, Uri.parse(tree)) ?: throw IOException("save folder unavailable — choose it again in Settings")
            for (p in dirs) d = d.findFile(p)?.takeIf { it.isDirectory } ?: d.createDirectory(p) ?: throw IOException("cannot create $p")
            var n = name; var i = 1; val b = name.substringBeforeLast('.', name); val e = name.substringAfterLast('.', "")
            while (d.findFile(n) != null) n = if (e.isEmpty()) "$b (${i++})" else "$b (${i++}).$e"
            val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(e.lowercase()) ?: "application/octet-stream"
            val f = d.createFile(mime, n) ?: throw IOException("cannot create $n")
            (app.contentResolver.openOutputStream(f.uri) ?: throw IOException("cannot write")).use { o -> part.inputStream().use { it.copyTo(o, BUF) } }
        } else {
            val cv = ContentValues().apply { put(MediaStore.Downloads.DISPLAY_NAME, name); put(MediaStore.Downloads.RELATIVE_PATH, "Download/HotShare" + dirs.joinToString("") { "/$it" }); put(MediaStore.Downloads.IS_PENDING, 1) }
            val uri = app.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv) ?: throw IOException("MediaStore refused file")
            try { (app.contentResolver.openOutputStream(uri) ?: throw IOException("cannot write")).use { o -> part.inputStream().use { it.copyTo(o, BUF) } } }
            catch (e: Exception) { app.contentResolver.delete(uri, null, null); throw e }
            cv.clear(); cv.put(MediaStore.Downloads.IS_PENDING, 0); app.contentResolver.update(uri, cv, null, null)
        }
    }

    // ---------- servers ----------
    @Synchronized fun startServers() {
        if (started) return; started = true
        mcast = (app.getSystemService(Context.WIFI_SERVICE) as WifiManager).createMulticastLock("hs").apply { setReferenceCounted(false); acquire() }
        daemon { tcpLoop() }; daemon { udpLoop() }; daemon { btLoop() }
    }
    @Synchronized fun stopServers() {
        started = false; try { tcp?.close() } catch (_: Exception) {}; try { udp?.close() } catch (_: Exception) {}; try { bt?.close() } catch (_: Exception) {}
        if (mcast?.isHeld == true) mcast?.release()
    }
    fun restartServers() { stopServers(); Thread.sleep(300); startServers() }
    private fun tcpLoop() {
        val ss = ServerSocket().apply { reuseAddress = true; bind(InetSocketAddress(cfg("port", "45455").toInt())) }; tcp = ss
        while (!ss.isClosed) {
            val c = try { ss.accept() } catch (_: IOException) { break }
            daemon { c.soTimeout = 60000; c.use { try { handle(it.getInputStream(), it.getOutputStream()) } catch (e: Exception) { log("Receive error: ${e.message}") } } }
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
    fun scan(): List<Dev> {
        val s = DatagramSocket().apply { broadcast = true; soTimeout = 1500 }; val q = "HSH?".toByteArray()
        val nis = NetworkInterface.getNetworkInterfaces()?.toList() ?: emptyList(); val targets = HashSet<InetAddress>(); targets.add(InetAddress.getByName("255.255.255.255"))
        for (ni in nis) for (ia in ni.interfaceAddresses) ia.broadcast?.let { targets.add(it) }
        for (t in targets) try { s.send(DatagramPacket(q, q.size, t, DISC)) } catch (_: Exception) {}
        val mine = nis.flatMap { it.inetAddresses.toList() }.map { it.hostAddress }; val res = LinkedHashMap<String, Dev>(); val b = ByteArray(512)
        try { while (true) { val p = DatagramPacket(b, b.size); s.receive(p); val ip = p.address.hostAddress ?: continue; if (ip in mine) continue
            try { val j = JSONObject(String(p.data, 0, p.length)); res[ip] = Dev(j.getString("name"), ip, j.getInt("port"), j.optString("os")) } catch (_: Exception) {} } } catch (_: SocketTimeoutException) {}
        s.close(); return res.values.toList()
    }
}

object Notifier {
    private fun nm() = Core.app.getSystemService(NotificationManager::class.java)
    private var lastP = 0L
    fun channels() { nm().createNotificationChannel(NotificationChannel("svc", "Background receiving", NotificationManager.IMPORTANCE_LOW)); nm().createNotificationChannel(NotificationChannel("hs", "Transfers", NotificationManager.IMPORTANCE_HIGH)) }
    fun foreground(): Notification = Notification.Builder(Core.app, "svc").setSmallIcon(android.R.drawable.ic_menu_share).setContentTitle("HotShare is ready to receive").setContentText("Visible to nearby devices")
        .setContentIntent(PendingIntent.getActivity(Core.app, 0, Intent(Core.app, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)).setOngoing(true).build()
    fun approval(a: Core.Approval) {
        fun pi(res: Int) = PendingIntent.getBroadcast(Core.app, a.id * 10 + res, Intent(Core.app, ActionReceiver::class.java).putExtra("id", a.id).putExtra("res", res), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm().notify(a.id, Notification.Builder(Core.app, "hs").setSmallIcon(android.R.drawable.stat_sys_download).setContentTitle("Incoming from ${a.peer}").setContentText("${a.count} item(s), ${a.total / 1048576} MB")
            .addAction(Notification.Action.Builder(0, "Accept", pi(1)).build()).addAction(Notification.Action.Builder(0, "Always", pi(2)).build()).addAction(Notification.Action.Builder(0, "Decline", pi(3)).build()).setAutoCancel(true).build())
    }
    fun info(title: String, text: String) { nm().notify((System.nanoTime() % 100000).toInt() + 5000, Notification.Builder(Core.app, "hs").setSmallIcon(android.R.drawable.stat_sys_download_done).setContentTitle(title).setContentText(text).setAutoCancel(true).build()) }
    fun progress(t: Core.Transfer) {
        val now = System.currentTimeMillis(); if (now - lastP < 1000) return; lastP = now
        nm().notify(2000 + t.id, Notification.Builder(Core.app, "svc").setSmallIcon(android.R.drawable.stat_sys_download).setContentTitle(t.label).setContentText("%.1f MB/s".format(t.speed / 1048576))
            .setProgress(1000, if (t.total > 0) (t.done * 1000 / t.total).toInt() else 0, false).setOngoing(true).build())
    }
    fun cancel(id: Int) = nm().cancel(id)
}
