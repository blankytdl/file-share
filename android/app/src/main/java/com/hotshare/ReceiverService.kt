package com.hotshare

import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder

/** Keeps the servers alive in the background (foreground service with a persistent notification). */
class ReceiverService : Service() {
    override fun onBind(i: Intent?): IBinder? = null
    override fun onCreate() { super.onCreate(); Core.init(this); Notifier.channels() }
    override fun onStartCommand(i: Intent?, f: Int, id: Int): Int {
        startForeground(1, Notifier.foreground(), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        Core.startServers(); return START_STICKY
    }
}

class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) { Core.init(c); Core.answer(i.getIntExtra("id", 0), i.getIntExtra("res", 3)) }
}
