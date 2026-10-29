package com.hotshare

import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Shader
import android.graphics.drawable.GradientDrawable
import android.view.MotionEvent
import android.view.View
import android.view.animation.DecelerateInterpolator

class Theme(val name: String, val dark: Boolean, val bg: Int, val card: Int, val card2: Int, val sel: Int, val text: Int, val muted: Int, val accent: Int, val accent2: Int, val ok: Int, val err: Int, val track: Int)

fun mix(a: Int, b: Int, f: Float): Int {
    fun m(sh: Int): Int { val x = (a shr sh) and 0xFF; val y = (b shr sh) and 0xFF; return (x + (y - x) * f).toInt().coerceIn(0, 255) }
    return (0xFF shl 24) or (m(16) shl 16) or (m(8) shl 8) or m(0)
}

/** Colours of the active theme; call C.apply(name) before building views. */
object C {
    private fun rgb(h: Int) = (0xFF shl 24) or h
    private fun mk(name: String, dark: Boolean, bgH: Int, a1H: Int, a2H: Int): Theme {
        val bg = rgb(bgH); val a1 = rgb(a1H); val a2 = rgb(a2H); val w = rgb(0xFFFFFF); val k = rgb(0x000000)
        return if (dark) Theme(name, true, bg, mix(bg, w, .075f), mix(bg, w, .15f), mix(mix(bg, w, .075f), a1, .2f), rgb(0xE8EDF5), rgb(0x8D99AB), a1, a2, rgb(0x38D39F), rgb(0xFF6B6B), mix(bg, w, .17f))
        else Theme(name, false, bg, w, mix(bg, k, .07f), mix(w, a1, .14f), rgb(0x1B2430), rgb(0x66728A), a1, a2, rgb(0x12A37A), rgb(0xD94646), mix(bg, k, .11f))
    }
    val themes: List<Theme> = listOf(mk("Midnight", true, 0x0F1419, 0x4C8DFF, 0x7C5CFF), mk("Crimson", true, 0x150B0D, 0xFF4D5E, 0xFF7A45), mk("Ocean", true, 0x0A1620, 0x22D3EE, 0x3B82F6), mk("Emerald", true, 0x0C1713, 0x34D399, 0x22C55E),
        mk("Sunset", true, 0x181110, 0xFB923C, 0xF43F5E), mk("Orchid", true, 0x130F1D, 0xC084FC, 0xEC4899), mk("Daylight", false, 0xEEF2F8, 0x2F6DF0, 0x6A4CE0))
    var name = "Midnight"; var dark = true
    var bg = 0; var card = 0; var card2 = 0; var sel = 0; var text = 0; var muted = 0; var accent = 0; var accent2 = 0; var ok = 0; var err = 0; var track = 0; var warn = 0
    init { apply("Midnight") }
    fun apply(n: String) {
        val t = themes.firstOrNull { it.name == n } ?: themes[0]
        name = t.name; dark = t.dark; bg = t.bg; card = t.card; card2 = t.card2; sel = t.sel; text = t.text; muted = t.muted; accent = t.accent; accent2 = t.accent2; ok = t.ok; err = t.err; track = t.track; warn = rgb(if (t.dark) 0xF5B84B else 0xB97800)
    }
}

fun Context.dp(v: Int) = (v * resources.displayMetrics.density).toInt()
fun roundBg(color: Int, r: Float) = GradientDrawable().apply { setColor(color); cornerRadius = r }

/** Click with a springy press animation. */
@SuppressLint("ClickableViewAccessibility")
fun View.press(onClick: () -> Unit) {
    setOnTouchListener { v, e ->
        when (e.action) {
            MotionEvent.ACTION_DOWN -> v.animate().scaleX(.95f).scaleY(.95f).setDuration(90).start()
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> v.animate().scaleX(1f).scaleY(1f).setDuration(140).start()
        }
        false
    }
    setOnClickListener { onClick() }
}

/** Pulsing radar rings while scanning. */
class RadarView(c: Context) : View(c) {
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    private var t = 0f
    private val anim = ValueAnimator.ofFloat(0f, 1f).apply { duration = 1600; repeatCount = ValueAnimator.INFINITE; addUpdateListener { t = it.animatedValue as Float; invalidate() } }
    var scanning = false
        set(v) { field = v; if (v) { if (!anim.isStarted) anim.start() } else { anim.cancel(); t = 0f; invalidate() } }

    override fun onDraw(c: Canvas) {
        val cx = width / 2f; val cy = height / 2f; val m = minOf(cx, cy)
        p.style = Paint.Style.FILL; p.color = C.accent; c.drawCircle(cx, cy, m * .18f, p)
        p.style = Paint.Style.STROKE; p.strokeWidth = 3f
        for (k in 0..2) {
            val f = if (scanning) (t + k / 3f) % 1f else .35f + k * .25f
            p.color = C.accent; p.alpha = if (scanning) ((1 - f) * 220).toInt() else 70
            c.drawCircle(cx, cy, m * (.25f + .75f * f), p)
        }
    }
    override fun onDetachedFromWindow() { anim.cancel(); super.onDetachedFromWindow() }
}

/** Rounded progress bar with eased fill. */
class BarView(c: Context) : View(c) {
    private val track = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = C.track }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    var cur = 0f
        private set
    private var done = false
    private var anim: ValueAnimator? = null

    fun set(target: Float, ok: Boolean = false) {
        done = ok; anim?.cancel()
        anim = ValueAnimator.ofFloat(cur, target.coerceIn(0f, 1f)).apply { duration = 250; interpolator = DecelerateInterpolator(); addUpdateListener { cur = it.animatedValue as Float; invalidate() }; start() }
    }
    override fun onDraw(c: Canvas) {
        val h = height.toFloat(); val w = width.toFloat(); val r = h / 2
        c.drawRoundRect(0f, 0f, w, h, r, r, track)
        if (cur > 0f) {
            fill.shader = LinearGradient(0f, 0f, w, 0f, if (done) C.ok else C.accent, if (done) C.ok else C.accent2, Shader.TileMode.CLAMP)
            c.drawRoundRect(0f, 0f, maxOf(h, w * cur), h, r, r, fill)
        }
    }
}

/** Crisp vector icon from the shared icon set (no emoji/fonts, so it never renders as a broken box). */
class IconView(c: Context, private var name: String, private var color: Int = C.text) : View(c) {
    fun setIconColor(c: Int) { color = c; invalidate() }
    fun setIcon(n: String, col: Int) { name = n; color = col; invalidate() }
    private val p = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND }

    override fun onDraw(c: Canvas) {
        val k = minOf(width, height) / 24f; val ox = (width - 24f * k) / 2; val oy = (height - 24f * k) / 2
        p.color = color; p.strokeWidth = 2f * k
        val prims = Icons.all[name] ?: return
        for (pr in prims) {
            val v = pr.v
            when (pr.k) {
                0, 1 -> {
                    val path = Path(); path.moveTo(ox + v[0] * k, oy + v[1] * k)
                    var i = 2; while (i < v.size) { path.lineTo(ox + v[i] * k, oy + v[i + 1] * k); i += 2 }
                    if (pr.k == 1) path.close()
                    p.style = Paint.Style.STROKE; c.drawPath(path, p)
                }
                2 -> { p.style = Paint.Style.STROKE; c.drawCircle(ox + v[0] * k, oy + v[1] * k, v[2] * k, p) }
                else -> { p.style = Paint.Style.FILL; c.drawCircle(ox + v[0] * k, oy + v[1] * k, v[2] * k, p) }
            }
        }
    }
}
