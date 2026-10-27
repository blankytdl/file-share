package com.hotshare

import android.Manifest
import android.animation.LayoutTransition
import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
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
import android.provider.DocumentsContract
import android.text.InputType
import android.text.TextUtils
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
    class QEntry(val kind: String, val name: String, val size: Long, val items: List<Core.Item>) { var view: View? = null }
    class Row(val ic: IconView, val bar: BarView, val st: TextView) { var last = -1; var done = false }
    class Card(val view: LinearLayout, val title: TextView, val count: TextView, val close: View, val icon: IconView, val bar: BarView, val stat: TextView, val tog: TextView, val box: LinearLayout) {
        var rows: List<Row> = emptyList(); var open = false; var pulse: ObjectAnimator? = null
    }
    companion object { val Q = ArrayList<QEntry>() }   // survives a theme change (activity recreate)

    private val devs = ArrayList<Dev>()
    private val sel = HashSet<Int>()
    private val cards = HashMap<Int, Card>()
    private val dialogs = HashMap<Int, AlertDialog>()
    private lateinit var root: LinearLayout
    private lateinit var devBox: LinearLayout
    private lateinit var qBox: LinearLayout
    private lateinit var xferBox: LinearLayout
    private lateinit var radar: RadarView
    private lateinit var logv: TextView
    private lateinit var subv: TextView
    private lateinit var savePath: TextView
    private lateinit var destEdit: EditText
    private lateinit var qCount: TextView
    private lateinit var qEmpty: TextView
    private lateinit var infoTv: TextView
    private lateinit var subInfo: TextView
    private val medium: Typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    private fun bgOn() = Core.prefs.getBoolean("bg", true)

    // ---------- building blocks ----------
    private fun dlg() = AlertDialog.Builder(this, if (C.dark) android.R.style.Theme_Material_Dialog_Alert else android.R.style.Theme_Material_Light_Dialog_Alert)
    private fun tv(t: String, sp: Float, color: Int, bold: Boolean = false) = TextView(this).apply { text = t; textSize = sp; setTextColor(color); includeFontPadding = false; if (bold) typeface = medium }
    private fun label(t: String) = tv(t.uppercase(), 12f, C.muted, true).apply { letterSpacing = .08f; setPadding(dp(4), dp(26), 0, dp(10)) }
    private fun ico(name: String, size: Int, color: Int = C.text) = IconView(this, name, color).also { it.layoutParams = LinearLayout.LayoutParams(dp(size), dp(size)) }
    private fun lp(w: Int = -1, h: Int = -2, top: Int = 0) = LinearLayout.LayoutParams(w, h).apply { topMargin = dp(top) }
    private fun col() = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
    private fun badge(name: String, size: Int, bg: Int, fg: Int, pad: Int = size / 4) = FrameLayout(this).apply {
        background = roundBg(bg, dp(size / 3).toFloat()); layoutParams = LinearLayout.LayoutParams(dp(size), dp(size))
        addView(IconView(this@MainActivity, name, fg), FrameLayout.LayoutParams(dp(size - 2 * pad), dp(size - 2 * pad), Gravity.CENTER))
    }
    private fun chip(ic: String, text: String, primary: Boolean = false, f: () -> Unit) = LinearLayout(this).apply {
        gravity = Gravity.CENTER; setPadding(dp(10), dp(12), dp(10), dp(12)); background = roundBg(if (primary) C.accent else C.card2, dp(14).toFloat())
        val c = if (primary) Color.WHITE else C.text
        addView(ico(ic, 18, c)); addView(tv(text, 13f, c, true).apply { setPadding(dp(7), 0, 0, 0); maxLines = 1 }); press(f)
    }
    private fun rowOf(vararg v: View) = LinearLayout(this).apply { v.forEach { addView(it, LinearLayout.LayoutParams(0, -2, 1f).apply { setMargins(dp(4), dp(4), dp(4), dp(4)) }) } }
    private fun tile(ic: String, name: String, f: () -> Unit) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER; setPadding(dp(6), dp(16), dp(6), dp(14)); background = roundBg(C.card, dp(18).toFloat())
        addView(badge(ic, 48, C.sel, C.accent, 12)); addView(tv(name, 13f, C.text, true).apply { gravity = Gravity.CENTER; setPadding(0, dp(9), 0, 0) }); press(f)
    }
    private fun size(n: Long) = if (n < 1 shl 20) "${n / 1024} KB" else if (n < 1L shl 30) "%.1f MB".format(n / 1048576.0) else "%.2f GB".format(n / 1073741824.0)
    private fun toast(m: String) = runOnUiThread { Toast.makeText(this, m, Toast.LENGTH_LONG).show() }
    private fun subtitle() = "${Core.cfg("name", Build.MODEL)}   -   ${Core.localIps().firstOrNull() ?: "not connected"}"
    private fun treeIntent() = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)

    private fun savePathText(): String {
        val tree = Core.prefs.getString("tree", null)
        if (tree != null) return try { DocumentsContract.getTreeDocumentId(Uri.parse(tree)).replaceFirst("primary:", "Internal storage/").replace(":", "/") } catch (e: Exception) { "Custom folder" }
        return if (Build.VERSION.SDK_INT >= 29) "Downloads/HotShare" else "Android/data/com.hotshare/files/HotShare"
    }
    private fun openSaveFolder() {
        try {
            val tree = Core.prefs.getString("tree", null)
            val i = if (tree != null) { val u = Uri.parse(tree); Intent(Intent.ACTION_VIEW).setDataAndType(DocumentsContract.buildDocumentUriUsingTree(u, DocumentsContract.getTreeDocumentId(u)), DocumentsContract.Document.MIME_TYPE_DIR).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION) }
            else if (Build.VERSION.SDK_INT >= 29) Intent(DownloadManager.ACTION_VIEW_DOWNLOADS)
            else { toast("Files are in " + savePathText()); return }
            startActivity(i)
        } catch (e: Exception) { toast("No file manager found - look in " + savePathText()) }
    }

    override fun onCreate(b: Bundle?) {
        super.onCreate(b); Core.init(this); C.apply(Core.cfg("theme", "Midnight"))
        window.statusBarColor = C.bg; window.navigationBarColor = C.bg
        if (!C.dark) window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 7)
        if (bgOn()) startForegroundService(Intent(this, ReceiverService::class.java))

        val frame = FrameLayout(this).apply { setBackgroundColor(C.bg) }
        val scroll = ScrollView(this).apply { overScrollMode = View.OVER_SCROLL_NEVER }
        root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(18), dp(44), dp(18), dp(120)) }
        scroll.addView(root); frame.addView(scroll, FrameLayout.LayoutParams(-1, -1))

        // sticky send bar
        val r20 = dp(22).toFloat()
        val bar = LinearLayout(this).apply {
            gravity = Gravity.CENTER_VERTICAL; setPadding(dp(20), dp(14), dp(14), dp(14)); elevation = dp(12).toFloat()
            background = GradientDrawable().apply { setColor(C.card); cornerRadii = floatArrayOf(r20, r20, r20, r20, 0f, 0f, 0f, 0f) }
        }
        val info = col(); infoTv = tv("Nothing to send yet", 15f, C.text, true); subInfo = tv("Select a device above", 12f, C.muted).apply { setPadding(0, dp(3), 0, 0) }
        info.addView(infoTv); info.addView(subInfo); bar.addView(info, LinearLayout.LayoutParams(0, -2, 1f))
        bar.addView(chip("send", "Send", true) { sendQueue() }.apply { setPadding(dp(22), dp(14), dp(22), dp(14)) })
        frame.addView(bar, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM)); setContentView(frame)

        // header: logo, title, radar, history, settings
        val head = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        head.addView(FrameLayout(this).apply {
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(C.accent, C.accent2)).apply { cornerRadius = dp(14).toFloat() }
            addView(IconView(this@MainActivity, "exchange", Color.WHITE), FrameLayout.LayoutParams(dp(28), dp(28), Gravity.CENTER))
        }, LinearLayout.LayoutParams(dp(48), dp(48)))
        val titles = col().apply { setPadding(dp(14), 0, 0, 0) }
        titles.addView(tv("HotShare", 26f, C.text, true)); subv = tv(subtitle(), 13f, C.muted).apply { setPadding(0, dp(4), 0, 0) }; titles.addView(subv)
        head.addView(titles, LinearLayout.LayoutParams(0, -2, 1f))
        head.addView(RadarView(this).also { radar = it }, LinearLayout.LayoutParams(dp(46), dp(46)))
        head.addView(badge("history", 40, C.card2, C.text, 10).apply { press { showHistory() }; (layoutParams as LinearLayout.LayoutParams).leftMargin = dp(8) })
        head.addView(badge("sliders", 40, C.card2, C.text, 10).apply { press { settings() }; (layoutParams as LinearLayout.LayoutParams).leftMargin = dp(8) })
        root.addView(head)

        // receive-into card
        val sc = col().apply { setPadding(dp(16), dp(16), dp(16), dp(12)); background = roundBg(C.card, dp(18).toFloat()) }
        val top = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        top.addView(badge("folder", 44, C.sel, C.accent, 11))
        val c1 = col().apply { setPadding(dp(14), 0, 0, 0) }
        c1.addView(tv("RECEIVE INTO", 11f, C.muted, true).apply { letterSpacing = .08f })
        savePath = tv(savePathText(), 15f, C.text, true).apply { maxLines = 2; ellipsize = TextUtils.TruncateAt.MIDDLE; setPadding(0, dp(4), 0, 0) }; c1.addView(savePath)
        top.addView(c1, LinearLayout.LayoutParams(0, -2, 1f)); sc.addView(top)
        sc.addView(rowOf(chip("folder", "Change folder", true) { startActivityForResult(treeIntent(), 3) }, chip("tray", "Open folder") { openSaveFolder() }), lp(top = 12))
        root.addView(sc, lp(top = 22))

        // devices
        root.addView(label("Nearby devices"))
        root.addView(rowOf(chip("radar", "Scan") { scan() }, chip("plus", "Add IP") { askIp() }, chip("bluetooth", "Bluetooth") { pickBt() }))
        devBox = col().apply { layoutTransition = LayoutTransition() }; root.addView(devBox, lp(top = 6))

        // add to the send list
        root.addView(label("Add to send list"))
        root.addView(rowOf(tile("file", "Files") { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply { type = "*/*"; putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true); addCategory(Intent.CATEGORY_OPENABLE) }, 1) },
            tile("folder", "Folder") { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE), 2) }, tile("apps", "Apps") { pickApps() }))
        root.addView(rowOf(tile("tray", "Paste") { paste() }, tile("text", "Text") { sendText() }, View(this)))

        val qh = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        qh.addView(label("To send"))
        qCount = tv("", 12f, C.accent, true).apply { setPadding(dp(10), dp(26), 0, dp(10)) }; qh.addView(qCount, LinearLayout.LayoutParams(0, -2, 1f))
        qh.addView(chip("close", "Clear all") { clearQueue() }.apply { setPadding(dp(10), dp(8), dp(10), dp(8)) }); root.addView(qh)
        qBox = col().apply { layoutTransition = LayoutTransition() }; root.addView(qBox)
        qEmpty = tv("Nothing queued. Add files above, pick a device, then press Send.", 13f, C.muted).apply { setPadding(dp(4), dp(4), 0, dp(4)) }; root.addView(qEmpty)
        root.addView(tv("Put it into this folder on the other device (optional)", 12f, C.muted).apply { setPadding(dp(4), dp(18), 0, dp(6)) })
        destEdit = EditText(this).apply { hint = "e.g. Photos/Trip"; setHintTextColor(C.muted); setTextColor(C.text); textSize = 14f; setSingleLine(); background = roundBg(C.card, dp(14).toFloat()); setPadding(dp(16), dp(13), dp(16), dp(13)) }
        root.addView(destEdit, lp())

        // transfers
        val th = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        th.addView(label("Transfers"), LinearLayout.LayoutParams(0, -2, 1f)); th.addView(chip("close", "Cancel all") { Core.cancel() }.apply { setPadding(dp(10), dp(8), dp(10), dp(8)) }); root.addView(th)
        xferBox = col().apply { layoutTransition = LayoutTransition() }; root.addView(xferBox)
        logv = tv("", 12f, C.muted).apply { setPadding(dp(4), dp(14), 0, 0) }; root.addView(logv)

        // theme swatches
        root.addView(label("Theme"))
        val sw = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        C.themes.forEach { t ->
            val d = dp(36)
            sw.addView(FrameLayout(this).apply {
                background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(t.accent, t.accent2)).apply { shape = GradientDrawable.OVAL; if (t.name == C.name) setStroke(dp(3), C.text) }
                if (t.name == C.name) addView(IconView(this@MainActivity, "check", Color.WHITE), FrameLayout.LayoutParams(dp(18), dp(18), Gravity.CENTER))
                press { if (t.name != C.name) { Core.prefs.edit().putString("theme", t.name).apply(); recreate() } }
            }, LinearLayout.LayoutParams(d, d).apply { rightMargin = dp(12) })
        }
        root.addView(sw)

        Q.toList().forEach { addEntryView(it) }; syncBar()
        for (i in 0 until root.childCount) root.getChildAt(i).apply { alpha = 0f; translationY = dp(24).toFloat(); animate().alpha(1f).translationY(0f).setStartDelay(i * 45L).setDuration(350).setInterpolator(DecelerateInterpolator()).start() }
        handleShare(intent); scan()
    }

    override fun onNewIntent(i: Intent) { super.onNewIntent(i); handleShare(i) }
    override fun onStart() {
        super.onStart(); Core.ui = this; subv.text = subtitle(); if (!bgOn()) Core.startServers()
        Core.active.values.forEach { t -> val c = card(t); if (t.names.isNotEmpty()) buildRows(c, t); progress(t) }
        Core.approvals.values.forEach { approve(it) }
    }
    override fun onStop() { dialogs.values.forEach { it.dismiss() }; dialogs.clear(); Core.ui = null; Core.onUiGone(); if (!bgOn()) Core.stopServers(); super.onStop() }

    // ---------- devices ----------
    private fun devCard(i: Int, d: Dev, on0: Boolean = false): View {
        val row = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(dp(14), dp(14), dp(16), dp(14)); background = roundBg(C.card, dp(16).toFloat()) }
        row.addView(badge(when (d.kind) { "Android" -> "phone"; "BT" -> "bluetooth"; else -> "laptop" }, 44, C.sel, C.accent, 11))
        val tx = col().apply { setPadding(dp(14), 0, 0, 0) }
        tx.addView(tv(d.name, 16f, C.text, true).apply { maxLines = 1; ellipsize = TextUtils.TruncateAt.END })
        tx.addView(tv("${d.addr.removePrefix("bt:")}   -   ${if (d.kind == "BT") "Bluetooth" else d.kind.ifEmpty { "device" }}", 12f, C.muted).apply { setPadding(0, dp(3), 0, 0) })
        row.addView(tx, LinearLayout.LayoutParams(0, -2, 1f))
        val ck = FrameLayout(this).apply { layoutParams = LinearLayout.LayoutParams(dp(28), dp(28)); background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(Color.TRANSPARENT); setStroke(dp(2), C.muted) } }
        val tick = IconView(this, "check", Color.WHITE).apply { alpha = 0f }; ck.addView(tick, FrameLayout.LayoutParams(dp(18), dp(18), Gravity.CENTER)); row.addView(ck)
        fun paint(on: Boolean) {
            tick.animate().alpha(if (on) 1f else 0f).setDuration(160).start()
            ValueAnimator.ofArgb(if (on) C.card else C.sel, if (on) C.sel else C.card).apply { duration = 200; addUpdateListener { (row.background as GradientDrawable).setColor(it.animatedValue as Int) }; start() }
            (ck.background as GradientDrawable).apply { setColor(if (on) C.accent else Color.TRANSPARENT); setStroke(dp(2), if (on) C.accent else C.muted) }
        }
        if (on0) { sel.add(i); paint(true) }
        row.press { val on = sel.add(i); if (!on) sel.remove(i); paint(on); syncBar() }
        return row
    }
    private fun setDevices(l: List<Dev>, keep: Boolean = false) {
        val keepAddrs = if (keep) sel.mapNotNull { devs.getOrNull(it)?.addr }.toSet() else emptySet<String>()
        devs.clear(); devs.addAll(l); sel.clear(); devBox.removeAllViews()
        if (l.isEmpty()) devBox.addView(col().apply {
            gravity = Gravity.CENTER; setPadding(0, dp(22), 0, dp(22))
            addView(ico("radar", 40, C.muted)); addView(tv("No devices found", 16f, C.text, true).apply { gravity = Gravity.CENTER; setPadding(0, dp(10), 0, dp(6)) })
            addView(tv("Join the same hotspot or Wi-Fi, open HotShare on the other device, then tap Scan.", 13f, C.muted).apply { gravity = Gravity.CENTER })
        })
        l.forEachIndexed { i, d -> devBox.addView(devCard(i, d, d.addr in keepAddrs), lp(top = 8)) }
        syncBar()
    }
    private fun scan() {
        radar.scanning = true; subv.text = subtitle()
        Core.daemon { val r = try { Core.scan() } catch (e: Exception) { Core.log("Scan failed: ${e.message}"); emptyList<Dev>() }; runOnUiThread { radar.scanning = false; setDevices(r); if (r.isEmpty()) toast("No devices found. Check both are on the same network and the PC firewall allows HotShare.") } }
    }
    private fun askIp() {
        val e = EditText(this).apply { hint = "192.168.x.x"; inputType = InputType.TYPE_CLASS_PHONE }
        dlg().setTitle("Device IP").setView(e)
            .setPositiveButton("Add") { _, _ -> val ip = e.text.toString().trim(); if (ip.isNotEmpty()) setDevices(devs + Dev("Manual", ip, Core.cfg("port", "45455").toInt(), "IP"), true) }.show()
    }
    private fun pickBt() {
        try {
            val d = BluetoothAdapter.getDefaultAdapter().bondedDevices.toList()
            if (d.isEmpty()) toast("Pair the other device in system Bluetooth settings first")
            setDevices(devs.filter { it.kind != "BT" } + d.map { Dev(it.name ?: it.address, "bt:${it.address}", 0, "BT") }, true)
        } catch (e: SecurityException) { requestPermissions(arrayOf(Manifest.permission.BLUETOOTH_CONNECT), 5); toast("Allow Bluetooth, then tap again") }
    }

    // ---------- the send list ----------
    private fun entryMeta(e: QEntry) = when (e.kind) { "folder" -> "${e.items.size} files   -   ${size(e.size)}"; "text" -> "Text   -   ${size(e.size)}"; else -> size(e.size) }
    private fun addEntryView(e: QEntry) {
        val row = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(dp(12), dp(12), dp(6), dp(12)); background = roundBg(C.card, dp(16).toFloat()) }
        row.addView(badge(when (e.kind) { "folder" -> "folder"; "text" -> "text"; "app" -> "apps"; else -> "file" }, 42, C.sel, C.accent, 10))
        val tx = col().apply { setPadding(dp(14), 0, dp(6), 0) }
        tx.addView(tv(e.name, 14f, C.text, true).apply { maxLines = 1; ellipsize = TextUtils.TruncateAt.MIDDLE }); tx.addView(tv(entryMeta(e), 12f, C.muted).apply { setPadding(0, dp(3), 0, 0) })
        row.addView(tx, LinearLayout.LayoutParams(0, -2, 1f))
        row.addView(badge("close", 38, Color.TRANSPARENT, C.muted, 10).apply { press { removeEntry(e) } })
        e.view = row; qBox.addView(row, lp(top = 8))
        row.alpha = 0f; row.translationX = dp(40).toFloat(); row.animate().alpha(1f).translationX(0f).setDuration(260).setInterpolator(DecelerateInterpolator()).start()
    }
    private fun addEntry(e: QEntry) { Q.add(e); addEntryView(e); syncBar() }
    private fun removeEntry(e: QEntry) {
        Q.remove(e); val v = e.view; syncBar()
        if (v != null) v.animate().alpha(0f).translationX(dp(60).toFloat()).setDuration(180).withEndAction { qBox.removeView(v) }.start()
    }
    private fun clearQueue() { Q.toList().forEach { removeEntry(it) } }
    private fun textEntry(t: String): QEntry { val it0 = Core.textItem(t); return QEntry("text", t.trim().lines().first().take(60), it0.size, listOf(it0)) }
    private fun syncBar() {
        val n = Q.sumOf { it.items.size }; val sz = Q.sumOf { it.size }
        qCount.text = if (Q.isEmpty()) "" else "${Q.size} item(s)"; qEmpty.visibility = if (Q.isEmpty()) View.VISIBLE else View.GONE
        val tg = sel.sorted().mapNotNull { devs.getOrNull(it)?.name }
        infoTv.text = if (Q.isEmpty()) "Nothing to send yet" else "$n file(s)   -   ${size(sz)}"
        subInfo.text = if (tg.isEmpty()) "Select a device above" else "To " + tg.take(2).joinToString(", ") + if (tg.size > 2) " +${tg.size - 2}" else ""
    }
    private fun sendQueue() {
        if (Q.isEmpty()) { toast("Add files, a folder, text or paste something first"); return }
        val targets = sel.sorted().mapNotNull { devs.getOrNull(it) }
        if (targets.isEmpty()) { toast("Select one or more devices first"); return }
        val items = Q.flatMap { it.items }; val dest = destEdit.text.toString().trim()
        clearQueue(); targets.forEach { d -> Core.daemon { Core.sendTo(d, items, dest) } }
    }

    // ---------- adding things ----------
    override fun onActivityResult(rc: Int, res: Int, d: Intent?) {
        if (res != RESULT_OK || d == null) return
        if (rc == 3) {
            val u = d.data ?: return; contentResolver.takePersistableUriPermission(u, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            Core.prefs.edit().putString("tree", u.toString()).apply(); savePath.text = savePathText(); toast("Receiving into: " + savePathText()); return
        }
        Core.daemon {
            val es = ArrayList<QEntry>()
            if (rc == 1) { val uris = ArrayList<Uri>(); d.clipData?.let { for (i in 0 until it.itemCount) uris.add(it.getItemAt(i).uri) } ?: d.data?.let { uris.add(it) }; uris.forEach { u -> Core.uriItem(u)?.let { x -> es.add(QEntry("file", x.rel, x.size, listOf(x))) } } }
            if (rc == 2) { val t = DocumentFile.fromTreeUri(this, d.data!!)!!; val items = ArrayList<Core.Item>(); Core.walk(t, t.name ?: "folder", items); if (items.isNotEmpty()) es.add(QEntry("folder", t.name ?: "folder", items.sumOf { it.size }, items)) }
            runOnUiThread { if (es.isEmpty()) toast("Nothing to add") else es.forEach { addEntry(it) } }
        }
    }
    private fun paste() {
        val clip = (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).primaryClip
        if (clip == null || clip.itemCount == 0) { toast("Clipboard is empty"); return }
        Core.daemon {
            val es = ArrayList<QEntry>()
            for (i in 0 until clip.itemCount) {
                val item = clip.getItemAt(i); val uri = item.uri
                if (uri != null) Core.uriItem(uri)?.let { x -> es.add(QEntry("file", x.rel, x.size, listOf(x))) }
                else { val t = item.coerceToText(this)?.toString()?.trim(); if (!t.isNullOrEmpty()) es.add(textEntry(t)) }
            }
            runOnUiThread { if (es.isEmpty()) toast("Nothing usable on the clipboard") else { es.forEach { addEntry(it) }; toast("Pasted ${es.size} item(s)") } }
        }
    }
    private fun handleShare(i: Intent) {
        val uris = ArrayList<Uri>()
        if (i.action == Intent.ACTION_SEND) i.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)?.let { uris.add(it) }
        if (i.action == Intent.ACTION_SEND_MULTIPLE) i.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)?.let { uris.addAll(it) }
        if (uris.isNotEmpty()) Core.daemon { val es = uris.mapNotNull { Core.uriItem(it) }.map { x -> QEntry("file", x.rel, x.size, listOf(x)) }; runOnUiThread { es.forEach { addEntry(it) }; toast("Added ${es.size} item(s) - pick a device and press Send") } }
        else if (i.action == Intent.ACTION_SEND) i.getStringExtra(Intent.EXTRA_TEXT)?.let { addEntry(textEntry(it)); toast("Added text - pick a device and press Send") }
    }
    private fun sendText() {
        val e = EditText(this).apply { hint = "Text to send"; (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).primaryClip?.getItemAt(0)?.text?.let { setText(it) } }
        dlg().setTitle("Add text").setView(e).setPositiveButton("Add to list") { _, _ -> if (e.text.isNotBlank()) addEntry(textEntry(e.text.toString())) }.show()
    }
    private fun pickApps() {
        val pm = packageManager
        val apps = pm.getInstalledApplications(0).filter { pm.getLaunchIntentForPackage(it.packageName) != null }.sortedBy { pm.getApplicationLabel(it).toString().lowercase() }
        val on = BooleanArray(apps.size)
        dlg().setTitle("Share apps (APK)").setMultiChoiceItems(apps.map { pm.getApplicationLabel(it).toString() }.toTypedArray(), on) { _, i, v -> on[i] = v }
            .setPositiveButton("Add to list") { _, _ ->
                apps.filterIndexed { i, _ -> on[i] }.forEach { a -> val f = File(a.sourceDir); val nm = pm.getApplicationLabel(a).toString() + ".apk"
                    addEntry(QEntry("app", nm, f.length(), listOf(Core.Item(nm, f.length(), f.lastModified() / 1000) { FileInputStream(f) }))) }
            }.show()
    }

    // ---------- transfer cards (sending AND receiving), each with a per-file list ----------
    private fun card(t: Core.Transfer): Card = cards.getOrPut(t.id) {
        val v = col().apply { setPadding(dp(16), dp(14), dp(16), dp(14)); background = roundBg(C.card, dp(16).toFloat()) }
        val top = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        val icon = ico(if (t.incoming) "download" else "upload", 22, C.accent); top.addView(icon)
        val title = tv(if (t.incoming) "From ${t.label}" else t.label, 15f, C.text, true).apply { setPadding(dp(10), 0, 0, 0); maxLines = 1; ellipsize = TextUtils.TruncateAt.END }
        top.addView(title, LinearLayout.LayoutParams(0, -2, 1f))
        val count = tv("", 12f, C.muted).apply { setPadding(dp(8), 0, dp(8), 0) }; top.addView(count)
        val close = badge("close", 34, Color.TRANSPARENT, C.muted, 8).apply { press { Core.cancel(t.id) } }; top.addView(close)
        val bar = BarView(this); val stat = tv("Starting...", 12f, C.muted)
        val low = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }; low.addView(stat, LinearLayout.LayoutParams(0, -2, 1f))
        val tog = tv("", 12f, C.accent, true).apply { setPadding(dp(10), dp(6), 0, dp(6)) }; low.addView(tog)
        val box = col().apply { visibility = View.GONE; setPadding(0, dp(8), 0, 0) }
        v.addView(top); v.addView(bar, lp(h = dp(8), top = 12)); v.addView(low, lp(top = 8)); v.addView(box)
        xferBox.addView(v, lp(top = 8))
        val c = Card(v, title, count, close, icon, bar, stat, tog, box)
        c.pulse = ObjectAnimator.ofFloat(icon, "alpha", 1f, .35f).apply { duration = 800; repeatCount = ValueAnimator.INFINITE; repeatMode = ValueAnimator.REVERSE; start() }
        tog.press { c.open = !c.open; showRows(c) }
        c
    }
    private fun showRows(c: Card) { c.box.visibility = if (c.open) View.VISIBLE else View.GONE; c.tog.text = if (c.rows.isEmpty()) "" else if (c.open) "Hide files" else "Show files (${c.rows.size})" }
    private fun buildRows(c: Card, t: Core.Transfer) {
        c.box.removeAllViews(); val n = t.names.size; val rows = ArrayList<Row>(); c.count.text = "$n file${if (n != 1) "s" else ""}"
        for (k in 0 until minOf(n, 40)) {
            val r = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(0, dp(6), 0, dp(6)) }
            val ic = IconView(this, if (t.names[k] == "Text message") "text" else "file", C.muted).also { it.layoutParams = LinearLayout.LayoutParams(dp(18), dp(18)) }
            val mid = col(); mid.addView(tv(t.names[k], 12f, C.text).apply { maxLines = 1; ellipsize = TextUtils.TruncateAt.MIDDLE }); val bar = BarView(this); mid.addView(bar, lp(h = dp(4), top = 5))
            val st = tv("", 11f, C.muted)
            r.addView(ic); r.addView(mid, LinearLayout.LayoutParams(0, -2, 1f).apply { setMargins(dp(10), 0, dp(10), 0) }); r.addView(st); c.box.addView(r); rows.add(Row(ic, bar, st))
        }
        if (n > 40) c.box.addView(tv("... and ${n - 40} more", 12f, C.muted))
        c.rows = rows; c.open = n <= 4; showRows(c)
    }
    private fun updateRows(c: Card, t: Core.Transfer, allDone: Boolean) {
        c.rows.forEachIndexed { k, r ->
            val sz = t.sizes.getOrElse(k) { 0L }; val d = t.fdone.getOrElse(k) { 0L }; val done = allDone || (sz > 0 && d >= sz)
            val key = if (done) -2 else if (sz > 0) (100 * d / sz).toInt() else 0
            if (key == r.last) return@forEachIndexed
            r.last = key
            if (done) { r.bar.set(1f, true); r.st.text = "Done"; r.st.setTextColor(C.ok); if (!r.done) { r.done = true; r.ic.setIconColor(C.ok) } }
            else { r.bar.set(if (sz > 0) d.toFloat() / sz else 0f); r.st.text = if (sz > 0) "$key%" else ""; r.st.setTextColor(C.muted) }
        }
    }
    private fun removeCard(id: Int) { cards.remove(id)?.let { it.pulse?.cancel(); xferBox.removeView(it.view) } }

    // ---------- Core.Ui (called from background threads) ----------
    override fun log(m: String) = runOnUiThread { logv.text = (logv.text.toString().lines().filter { it.isNotEmpty() }.takeLast(3) + m).joinToString("\n") }
    override fun files(t: Core.Transfer) = runOnUiThread { val c = card(t); buildRows(c, t) }
    override fun started(t: Core.Transfer) = runOnUiThread { card(t); Unit }
    override fun progress(t: Core.Transfer) = runOnUiThread {
        val c = card(t); c.bar.set(if (t.total > 0) t.done.toFloat() / t.total else 0f)
        c.stat.text = "${size(t.done)} of ${size(t.total)}   -   %.1f MB/s   -   ${t.eta}s left".format(t.speed / 1048576); updateRows(c, t, false)
    }
    override fun finished(t: Core.Transfer, ok: Boolean, msg: String) = runOnUiThread {
        val c = card(t); c.pulse?.cancel(); c.icon.alpha = 1f; c.bar.set(if (ok) 1f else c.bar.cur, ok); updateRows(c, t, ok)
        c.stat.text = (if (ok) "Done - " else "Stopped - ") + msg; c.stat.setTextColor(if (ok) C.ok else C.err)
        c.close.press { removeCard(t.id) }; c.view.postDelayed({ removeCard(t.id) }, 15000)
    }
    override fun approve(a: Core.Approval) = runOnUiThread {
        if (isFinishing || isDestroyed) return@runOnUiThread
        dialogs[a.id] = dlg().setTitle("Incoming from ${a.peer}").setMessage("${a.count} item(s)   -   ${size(a.total)}\nSaving to: ${savePathText()}").setCancelable(false)
            .setPositiveButton("Accept") { _, _ -> Core.answer(a.id, 1) }.setNeutralButton("Always") { _, _ -> Core.answer(a.id, 2) }.setNegativeButton("Decline") { _, _ -> Core.answer(a.id, 3) }.show()
    }
    override fun dismiss(id: Int) = runOnUiThread { dialogs.remove(id)?.dismiss(); Unit }
    override fun text(peer: String, t: String) = runOnUiThread {
        if (isFinishing || isDestroyed) return@runOnUiThread
        dlg().setTitle("Text from $peer").setMessage(t)
            .setPositiveButton("Copy") { _, _ -> (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("HotShare", t)) }.setNegativeButton("Close", null).show()
    }

    // ---------- history & settings ----------
    private fun showHistory() {
        val a = JSONArray(Core.prefs.getString("hist", "[]")); val f = SimpleDateFormat("MM-dd HH:mm", Locale.getDefault())
        val l = (a.length() - 1 downTo 0).map { val o = a.getJSONObject(it); "${f.format(Date(o.getLong("t")))}   ${if (o.getString("d") == "in") "Received from" else "Sent to"} ${o.getString("p")}\n${o.getString("n")} (${size(o.getLong("s"))})" }
        dlg().setTitle("History").apply { if (l.isEmpty()) setMessage("Nothing transferred yet.") else setItems(l.toTypedArray(), null) }
            .setNeutralButton("Clear") { _, _ -> Core.prefs.edit().remove("hist").apply() }.setPositiveButton("Close", null).show()
    }
    private fun settings() {
        val p = Core.prefs; val l = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(12), dp(24), 0) }
        fun field(h: String, k: String, d: String, num: Boolean = false) = EditText(this).apply { hint = h; setText(Core.cfg(k, d)); if (num) inputType = InputType.TYPE_CLASS_NUMBER; l.addView(this) }
        fun sw(t: String, k: String, d: Boolean) = Switch(this).apply { text = t; isChecked = p.getBoolean(k, d); setPadding(0, dp(10), 0, dp(10)); l.addView(this) }
        val name = field("Device name", "name", Build.MODEL); val pin = field("Shared PIN (same on both devices)", "pin", ""); val port = field("Port", "port", "45455", true)
        val auto = sw("Auto-accept everything", "auto", false); val vis = sw("Visible to nearby devices", "visible", true); val bg = sw("Receive in background", "bg", true)
        l.addView(tv("Receiving into: " + savePathText(), 14f, C.muted).apply { setPadding(0, dp(12), 0, dp(4)) })
        l.addView(Button(this).apply { text = "Reset to default folder"; setOnClickListener { p.edit().remove("tree").apply(); savePath.text = savePathText(); text = "Reset" } })
        l.addView(Button(this).apply { text = "Clear trusted devices (${Core.trusted().size})"; setOnClickListener { p.edit().remove("trusted").apply(); text = "Cleared" } })
        dlg().setTitle("Settings").setView(ScrollView(this).apply { addView(l) }).setPositiveButton("Save") { _, _ ->
            val portChanged = port.text.toString() != Core.cfg("port", "45455")
            p.edit().putString("name", name.text.toString()).putString("pin", pin.text.toString()).putString("port", port.text.toString().ifEmpty { "45455" }).putBoolean("auto", auto.isChecked).putBoolean("visible", vis.isChecked).putBoolean("bg", bg.isChecked).apply()
            subv.text = subtitle()
            if (bg.isChecked) startForegroundService(Intent(this, ReceiverService::class.java)) else stopService(Intent(this, ReceiverService::class.java))
            if (portChanged) Core.daemon { Core.restartServers() }
        }.show()
    }
}
