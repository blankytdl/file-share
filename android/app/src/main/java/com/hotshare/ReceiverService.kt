package com.hotshare

import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper

/** Keeps the servers alive in the background (foreground service with a persistent notification). */
class ReceiverService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    /** Every 15 s: if nothing is happening and the app is closed for longer than the idle limit, shut everything down and exit. */
    private val watchdog = object : Runnable { override fun run() { if (Core.shouldQuit()) Core.quit() else handler.postDelayed(this, 15000) } }
    override fun onBind(i: Intent?): IBinder? = null
    override fun onDestroy() { handler.removeCallbacks(watchdog); super.onDestroy() }
    override fun onCreate() { super.onCreate(); Core.init(this); Notifier.channels() }
    override fun onStartCommand(i: Intent?, f: Int, id: Int): Int {
        val n = Notifier.foreground()
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC) else startForeground(1, n)
        Core.startServers(); Core.touch(); handler.removeCallbacks(watchdog); handler.postDelayed(watchdog, 15000); return START_STICKY
    }
}

class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) { Core.init(c); Core.answer(i.getIntExtra("id", 0), i.getIntExtra("res", 3)) }
}
