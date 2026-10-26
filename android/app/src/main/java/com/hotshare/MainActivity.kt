package com.hotshare

import android.Manifest
import android.animation.LayoutTransition
import android.animation.ValueAnimator
import android.app.Activity
import android.app.AlertDialog
import android.bluetooth.BluetoothAdapter
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.widget.*
import androidx.documentfile.provider.DocumentFile
import org.json.JSONArray
import java.io.File
import java.io.FileInputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity(), Core.Ui {
    class Card(val view: LinearLayout, val stat: TextView, val bar: BarView)

    private val devs = ArrayList<Dev>()
    private val sel = HashSet<Int>()
    private val cards = HashMap<Int, Card>()
    private val dialogs = HashMap<Int, AlertDialog>()
    private var pending: List<Core.Item>? = null
    private var pendBtn: View? = null
    private lateinit var root: LinearLayout
    private lateinit var devBox: LinearLayout
    private lateinit var xferBox: LinearLayout
    private lateinit var radar: RadarView
    private lateinit var logv: TextView
    private lateinit var destEdit: EditText
    private fun bgOn() = Core.prefs.getBoolean("bg", true)

    // ---------- UI building blocks ----------
    private fun tv(t: String, sp: Float, color: Int, bold: Boolean = false) = TextView(this).apply { text = t; textSize = sp; setTextColor(color); if (bold) typeface = Typeface.DEFAULT_BOLD }
    private fun label(t: String) = tv(t.uppercase(), 12f, C.muted, true).apply { letterSpacing = .08f; setPadding(0, dp(22), 0, dp(8)) }
    private fun pill(t: String, primary: Boolean = false, f: () -> Unit) = tv(t, 14f, if (primary) Color.WHITE else C.text, true).apply {
        gravity = Gravity.CENTER; setPadding(dp(14), dp(12), dp(14), dp(12)); background = roundBg(if (primary) C.accent else C.card2, dp(14).toFloat()); press(f)
    }
    private fun tile(icon: String, name: String, f: () -> Unit) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER; setPadding(dp(8), dp(18), dp(8), dp(18)); background = roundBg(C.card, dp(18).toFloat())
        addView(tv(icon, 28f, C.text).apply { gravity = Gravity.CENTER }); addView(tv(name, 13f, C.muted, true).apply { gravity = Gravity.CENTER; setPadding(0, dp(6), 0, 0) }); press(f)
    }
    private fun rowOf(vararg v: View) = LinearLayout(this).apply { v.forEach { addView(it, LinearLayout.LayoutParams(0, -2, 1f).apply { setMargins(dp(4), dp(4), dp(4), dp(4)) }) } }
    private fun size(n: Long) = if (n < 1 shl 20) "${n / 1024} KB" else if (n < 1L shl 30) "%.1f MB".format(n / 1048576.0) else "%.2f GB".format(n / 1073741824.0)
    private fun toast(m: String) = runOnUiThread { Toast.makeText(this, m, Toast.LENGTH_LONG).show() }

    override fun onCreate(b: Bundle?) {
        super.onCreate(b); Core.init(this)
        window.statusBarColor = C.bg; window.navigationBarColor = C.bg
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 7)
        if (bgOn()) startForegroundService(Intent(this, ReceiverService::class.java))

        val scroll = ScrollView(this).apply { setBackgroundColor(C.bg); overScrollMode = View.OVER_SCROLL_NEVER }
        root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(18), dp(44), dp(18), dp(28)) }
        scroll.addView(root); setContentView(scroll)

        val head = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        val titles = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        titles.addView(tv("HotShare", 30f, C.text, true)); titles.addView(tv("${Core.cfg("name", Build.MODEL)} · resumable", 13f, C.muted))
        head.addView(titles, LinearLayout.LayoutParams(0, -2, 1f))
        radar = RadarView(this); head.addView(radar, LinearLayout.LayoutParams(dp(56), dp(56)))
        head.addView(tv("⚙", 26f, C.muted).apply { setPadding(dp(14), dp(6), dp(4), dp(6)); press { settings() } })
        root.addView(head)

        root.addView(label("Nearby devices"))
        devBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; layoutTransition = LayoutTransition() }; root.addView(devBox)
        root.addView(rowOf(pill("Scan") { scan() }, pill("Add IP") { askIp() }, pill("Bluetooth") { pickBt() }))

        root.addView(label("Send"))
        root.addView(rowOf(tile("📄", "Files") { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply { type = "*/*"; putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true); addCategory(Intent.CATEGORY_OPENABLE) }, 1) },
            tile("📁", "Folder") { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE), 2) }))
        root.addView(rowOf(tile("📦", "Apps") { pickApps() }, tile("✏️", "Text") { sendText() }))
        destEdit = EditText(this).apply { hint = "Subfolder on receiver (optional)"; setHintTextColor(C.muted); setTextColor(C.text); textSize = 14f; setSingleLine(); background = roundBg(C.card, dp(14).toFloat()); setPadding(dp(16), dp(12), dp(16), dp(12)) }
        root.addView(destEdit, LinearLayout.LayoutParams(-1, -2).apply { setMargins(dp(4), dp(8), dp(4), 0) })

        val th = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        th.addView(label("Transfers"), LinearLayout.LayoutParams(0, -2, 1f)); th.addView(tv("Cancel all", 13f, C.accent, true).apply { setPadding(dp(8), dp(22), 0, dp(8)); press { Core.cancel() } })
        th.addView(tv("History", 13f, C.accent, true).apply { setPadding(dp(16), dp(22), 0, dp(8)); press { showHistory() } })
        root.addView(th)
        xferBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; layoutTransition = LayoutTransition() }; root.addView(xferBox)
        logv = tv("", 12f, C.muted).apply { setPadding(dp(4), dp(14), 0, 0) }; root.addView(logv)

        for (i in 0 until root.childCount) root.getChildAt(i).apply { alpha = 0f; translationY = dp(24).toFloat(); animate().alpha(1f).translationY(0f).setStartDelay(i * 55L).setDuration(350).setInterpolator(DecelerateInterpolator()).start() }
        handleShare(intent); scan()
    }

    override fun onNewIntent(i: Intent) { super.onNewIntent(i); handleShare(i) }
    override fun onStart() { super.onStart(); Core.ui = this; if (!bgOn()) Core.startServers(); Core.approvals.values.forEach { approve(it) } }
    override fun onStop() { dialogs.values.forEach { it.dismiss() }; dialogs.clear(); Core.ui = null; Core.onUiGone(); if (!bgOn()) Core.stopServers(); super.onStop() }

    // ---------- devices ----------
    private fun devCard(i: Int, d: Dev, on0: Boolean = false): View {
        val row = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(dp(16), dp(14), dp(16), dp(14)); background = roundBg(C.card, dp(16).toFloat()) }
        row.addView(tv(when (d.kind) { "Android" -> "📱"; "BT" -> "🔵"; else -> "💻" }, 26f, C.text))
        val tx = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), 0, 0, 0) }
        tx.addView(tv(d.name, 16f, C.text, true)); tx.addView(tv("${d.addr.removePrefix("bt:")} · ${d.kind}", 12f, C.muted))
        row.addView(tx, LinearLayout.LayoutParams(0, -2, 1f)); val ck = tv("", 22f, C.accent, true); row.addView(ck)
        if (on0) { sel.add(i); ck.text = "✓"; (row.background as GradientDrawable).setColor(C.sel) }
        row.press {
            val on = sel.add(i); if (!on) sel.remove(i); ck.text = if (on) "✓" else ""
            ValueAnimator.ofArgb(if (on) C.card else C.sel, if (on) C.sel else C.card).apply { duration = 200; addUpdateListener { (row.background as GradientDrawable).setColor(it.animatedValue as Int) }; start() }
            if (on && pending != null) pendBtn?.visibility = View.VISIBLE
        }
        return row
    }
    private fun setDevices(l: List<Dev>, keep: Boolean = false) {
        val keepAddrs = if (keep) sel.mapNotNull { devs.getOrNull(it)?.addr }.toSet() else emptySet<String>()
        devs.clear(); devs.addAll(l); sel.clear(); devBox.removeAllViews()
        if (l.isEmpty()) devBox.addView(tv("No devices found. Join the same hotspot or Wi-Fi, then tap Scan.", 14f, C.muted).apply { setPadding(dp(4), dp(8), 0, dp(8)) })
        l.forEachIndexed { i, d -> devBox.addView(devCard(i, d, d.addr in keepAddrs), LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(4), 0, dp(4)) }) }
    }
    private fun scan() { radar.scanning = true; Core.daemon { val r = try { Core.scan() } catch (e: Exception) { Core.log("Scan failed: ${e.message}"); emptyList<Dev>() }; runOnUiThread { radar.scanning = false; setDevices(r) } } }
    private fun askIp() {
        val e = EditText(this).apply { hint = "192.168.x.x"; inputType = InputType.TYPE_CLASS_PHONE }
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert).setTitle("Device IP").setView(e)
            .setPositiveButton("Add") { _, _ -> val ip = e.text.toString().trim(); if (ip.isNotEmpty()) setDevices(devs + Dev("Manual", ip, Core.cfg("port", "45455").toInt(), "IP"), true) }.show()
    }
    private fun pickBt() {
        try {
            val d = BluetoothAdapter.getDefaultAdapter().bondedDevices.toList()
            if (d.isEmpty()) toast("Pair the other device in system Bluetooth settings first")
            setDevices(devs.filter { it.kind != "BT" } + d.map { Dev(it.name ?: it.address, "bt:${it.address}", 0, "BT") }, true)
        } catch (e: SecurityException) { requestPermissions(arrayOf(Manifest.permission.BLUETOOTH_CONNECT), 5); toast("Allow Bluetooth, then tap again") }
    }

    // ---------- choosing what to send ----------
    override fun onActivityResult(rc: Int, res: Int, d: Intent?) {
        if (res != RESULT_OK || d == null) return
        if (rc == 3) {
            val u = d.data ?: return; contentResolver.takePersistableUriPermission(u, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            Core.prefs.edit().putString("tree", u.toString()).apply(); toast("Save folder: ${DocumentFile.fromTreeUri(this, u)?.name}"); return
        }
        Core.daemon {
            val items = ArrayList<Core.Item>()
            if (rc == 1) { val uris = ArrayList<Uri>(); d.clipData?.let { for (i in 0 until it.itemCount) uris.add(it.getItemAt(i).uri) } ?: d.data?.let { uris.add(it) }; uris.forEach { u -> Core.uriItem(u)?.let { items.add(it) } } }
            if (rc == 2) { val t = DocumentFile.fromTreeUri(this, d.data!!)!!; Core.walk(t, t.name ?: "folder", items) }
            runOnUiThread { afterPick(items) }
        }
    }
    private fun handleShare(i: Intent) {
        val uris = ArrayList<Uri>()
        if (i.action == Intent.ACTION_SEND) i.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)?.let { uris.add(it) }
        if (i.action == Intent.ACTION_SEND_MULTIPLE) i.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)?.let { uris.addAll(it) }
        if (uris.isNotEmpty()) Core.daemon { val items = uris.mapNotNull { Core.uriItem(it) }; runOnUiThread { afterPick(items, true) } }
        else if (i.action == Intent.ACTION_SEND) i.getStringExtra(Intent.EXTRA_TEXT)?.let { afterPick(listOf(Core.textItem(it)), true) }
    }
    private fun afterPick(items: List<Core.Item>, forceAsk: Boolean = false) {
        if (items.isEmpty()) { toast("Nothing to send"); return }
        pending = items
        if (sel.isNotEmpty() && !forceAsk) { sendPending(); return }
        pendBtn?.let { root.removeView(it) }
        pendBtn = pill("Send ${items.size} item(s) →", true) { sendPending() }.also { root.addView(it, 1, LinearLayout.LayoutParams(-1, -2).apply { setMargins(dp(4), dp(14), dp(4), 0) }) }
        toast("Tap a device, then Send")
    }
    private fun sendPending() {
        val items = pending ?: return; val targets = sel.sorted().mapNotNull { devs.getOrNull(it) }
        if (targets.isEmpty()) { toast("Select one or more devices first"); return }
        val dest = destEdit.text.toString().trim(); pending = null; pendBtn?.let { root.removeView(it) }; pendBtn = null
        targets.forEach { d -> Core.daemon { Core.sendTo(d, items, dest) } }
    }
    private fun sendText() {
        val e = EditText(this).apply { hint = "Text to send"; (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).primaryClip?.getItemAt(0)?.text?.let { setText(it) } }
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert).setTitle("Send text").setView(e).setPositiveButton("Send") { _, _ -> if (e.text.isNotBlank()) afterPick(listOf(Core.textItem(e.text.toString()))) }.show()
    }
    private fun pickApps() {
        val pm = packageManager
        val apps = pm.getInstalledApplications(0).filter { pm.getLaunchIntentForPackage(it.packageName) != null }.sortedBy { pm.getApplicationLabel(it).toString().lowercase() }
        val on = BooleanArray(apps.size)
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert).setTitle("Share apps (APK)").setMultiChoiceItems(apps.map { pm.getApplicationLabel(it).toString() }.toTypedArray(), on) { _, i, v -> on[i] = v }
            .setPositiveButton("Next") { _, _ ->
                afterPick(apps.filterIndexed { i, _ -> on[i] }.map { a -> val f = File(a.sourceDir); Core.Item(pm.getApplicationLabel(a).toString() + ".apk", f.length(), f.lastModified() / 1000) { FileInputStream(f) } })
            }.show()
    }

    // ---------- Core.Ui (called from background threads) ----------
    private fun card(t: Core.Transfer): Card = cards.getOrPut(t.id) {
        val v = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(14), dp(16), dp(14)); background = roundBg(C.card, dp(16).toFloat()) }
        val top = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        top.addView(tv((if (t.incoming) "↓ " else "") + t.label, 15f, C.text, true), LinearLayout.LayoutParams(0, -2, 1f))
        top.addView(tv("✕", 18f, C.muted).apply { setPadding(dp(10), 0, 0, 0); press { Core.cancel(t.id) } })
        val bar = BarView(this); val stat = tv("Starting…", 12f, C.muted)
        v.addView(top); v.addView(bar, LinearLayout.LayoutParams(-1, dp(8)).apply { setMargins(0, dp(10), 0, dp(8)) }); v.addView(stat)
        xferBox.addView(v, LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(4), 0, dp(4)) }); Card(v, stat, bar)
    }
    override fun log(m: String) = runOnUiThread { logv.text = (logv.text.toString().lines().filter { it.isNotEmpty() }.takeLast(3) + m).joinToString("\n") }
    override fun started(t: Core.Transfer) = runOnUiThread { card(t); Unit }
    override fun progress(t: Core.Transfer) = runOnUiThread {
        val c = card(t); c.bar.set(if (t.total > 0) t.done.toFloat() / t.total else 0f)
        c.stat.text = "${size(t.done)} / ${size(t.total)} · %.1f MB/s · ETA ${t.eta}s".format(t.speed / 1048576)
    }
    override fun finished(t: Core.Transfer, ok: Boolean, msg: String) = runOnUiThread {
        val c = card(t); c.bar.set(if (ok) 1f else c.bar.cur, ok); c.stat.text = (if (ok) "✓ " else "✗ ") + msg; c.stat.setTextColor(if (ok) C.ok else C.err)
        c.view.postDelayed({ xferBox.removeView(c.view); cards.remove(t.id) }, 9000)
    }
    override fun approve(a: Core.Approval) = runOnUiThread {
        if (isFinishing || isDestroyed) return@runOnUiThread
        dialogs[a.id] = AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert).setTitle("Incoming from ${a.peer}").setMessage("${a.count} item(s) · ${size(a.total)}").setCancelable(false)
            .setPositiveButton("Accept") { _, _ -> Core.answer(a.id, 1) }.setNeutralButton("Always") { _, _ -> Core.answer(a.id, 2) }.setNegativeButton("Decline") { _, _ -> Core.answer(a.id, 3) }.show()
    }
    override fun dismiss(id: Int) = runOnUiThread { dialogs.remove(id)?.dismiss(); Unit }
    override fun text(peer: String, t: String) = runOnUiThread {
        if (isFinishing || isDestroyed) return@runOnUiThread
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert).setTitle("Text from $peer").setMessage(t)
            .setPositiveButton("Copy") { _, _ -> (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("HotShare", t)) }.setNegativeButton("Close", null).show()
    }

    // ---------- history & settings ----------
    private fun showHistory() {
        val a = JSONArray(Core.prefs.getString("hist", "[]")); val f = SimpleDateFormat("MM-dd HH:mm", Locale.getDefault())
        val l = (a.length() - 1 downTo 0).map { val o = a.getJSONObject(it); "${f.format(Date(o.getLong("t")))}  ${if (o.getString("d") == "in") "↓ from" else "↑ to"} ${o.getString("p")}\n${o.getString("n")} (${size(o.getLong("s"))})" }
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert).setTitle("History").setItems(l.toTypedArray(), null)
            .setNeutralButton("Clear") { _, _ -> Core.prefs.edit().remove("hist").apply() }.setPositiveButton("Close", null).show()
    }
    private fun settings() {
        val p = Core.prefs; val l = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(12), dp(24), 0) }
        fun field(h: String, k: String, d: String, num: Boolean = false) = EditText(this).apply { hint = h; setText(Core.cfg(k, d)); if (num) inputType = InputType.TYPE_CLASS_NUMBER; l.addView(this) }
        fun sw(t: String, k: String, d: Boolean) = Switch(this).apply { text = t; isChecked = p.getBoolean(k, d); setPadding(0, dp(10), 0, dp(10)); l.addView(this) }
        val name = field("Device name", "name", Build.MODEL); val pin = field("Shared PIN (same on both devices)", "pin", ""); val port = field("Port", "port", "45455", true)
        val auto = sw("Auto-accept everything", "auto", false); val vis = sw("Visible to nearby devices", "visible", true); val bg = sw("Receive in background", "bg", true)
        val folder = tv("Save folder: " + (p.getString("tree", null)?.let { DocumentFile.fromTreeUri(this, Uri.parse(it))?.name } ?: "Downloads/HotShare"), 14f, C.muted).apply { setPadding(0, dp(12), 0, dp(6)) }; l.addView(folder)
        val fr = LinearLayout(this); l.addView(fr)
        fr.addView(Button(this).apply { text = "Choose folder"; setOnClickListener { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION), 3) } })
        fr.addView(Button(this).apply { text = "Reset"; setOnClickListener { p.edit().remove("tree").apply(); folder.text = "Save folder: Downloads/HotShare" } })
        l.addView(Button(this).apply { text = "Clear trusted devices (${Core.trusted().size})"; setOnClickListener { p.edit().remove("trusted").apply(); text = "Cleared" } })
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert).setTitle("Settings").setView(ScrollView(this).apply { addView(l) }).setPositiveButton("Save") { _, _ ->
            val portChanged = port.text.toString() != Core.cfg("port", "45455")
            p.edit().putString("name", name.text.toString()).putString("pin", pin.text.toString()).putString("port", port.text.toString().ifEmpty { "45455" }).putBoolean("auto", auto.isChecked).putBoolean("visible", vis.isChecked).putBoolean("bg", bg.isChecked).apply()
            if (bg.isChecked) startForegroundService(Intent(this, ReceiverService::class.java)) else stopService(Intent(this, ReceiverService::class.java))
            if (portChanged) Core.daemon { Core.restartServers() }
        }.show()
    }
}
