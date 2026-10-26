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

object C {
    val bg = 0xFF0F1419.toInt(); val card = 0xFF1A2230.toInt(); val card2 = 0xFF243044.toInt(); val sel = 0xFF22345A.toInt()
    val text = 0xFFE8EDF5.toInt(); val muted = 0xFF8B97A8.toInt(); val accent = 0xFF4C8DFF.toInt(); val accent2 = 0xFF7C5CFF.toInt()
    val ok = 0xFF38D39F.toInt(); val err = 0xFFFF6B6B.toInt(); val track = 0xFF2A3446.toInt()
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
class IconView(c: Context, private val name: String, private val color: Int = C.text) : View(c) {
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
