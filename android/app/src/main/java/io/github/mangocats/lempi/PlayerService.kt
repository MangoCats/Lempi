package io.github.mangocats.lempi

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import io.github.mangocats.lempi.ffi.LempiException
import io.github.mangocats.lempi.ffi.Transport
import java.io.File
import java.security.SecureRandom
import io.github.mangocats.lempi.ffi.command as playerCommand
import io.github.mangocats.lempi.ffi.start as playerStart
import io.github.mangocats.lempi.ffi.state as playerState
import io.github.mangocats.lempi.ffi.stop as playerStop

/**
 * The player's home [REQ-AND-120]: a foreground service of type
 * mediaPlayback, so playback outlives the screen and the activity.
 *
 * Start runs off the main thread: it opens the databases, builds the Director
 * and binds the port before it returns, which is seconds, not the
 * milliseconds the main thread allows.
 *
 * The notification says what is playing and carries the transport, read and
 * sent through the generated interface [REQ-AND-410]. It is refreshed every
 * few seconds, which is cheap: `state()` reads what the engine last
 * published, and no WebView is involved.
 */
class PlayerService : Service() {
    private var wake: PowerManager.WakeLock? = null
    private val main = Handler(Looper.getMainLooper())
    private val refresh = object : Runnable {
        override fun run() {
            if (Lempi.status == "running") {
                update()
                main.postDelayed(this, 5_000)
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Playback", NotificationManager.IMPORTANCE_LOW))
        startForeground(1, notification("Starting", null),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            STOP -> {
                stopPlayer()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            TOGGLE, SKIP -> {
                try {
                    if (intent.action == SKIP) {
                        playerCommand(Transport.SKIP)
                    } else {
                        playerCommand(if (playerState()?.playing == true) Transport.PAUSE else Transport.PLAY)
                    }
                } catch (e: LempiException) {
                    Log.w(TAG, "command refused: ${e.message}")
                }
                main.postDelayed({ update() }, 400)
                return START_NOT_STICKY
            }
        }
        if (Lempi.status != "stopped") return START_NOT_STICKY
        Lempi.status = "starting"
        Thread({ startPlayer() }, "lempi-start").start()
        return START_NOT_STICKY
    }

    private fun startPlayer() {
        val files = filesDir
        var err = Lempi.init(applicationContext, File(files, "lempi.log").path)
        if (err == null) {
            val raw = ByteArray(24).also { SecureRandom().nextBytes(it) }
            val key = raw.joinToString("") { "%02x".format(it) }
            Lempi.key = key
            err = try {
                playerStart(File(files, "listener.db").path, File(files, "library.db").path,
                    Lempi.PORT.toUShort(), key)
                null
            } catch (e: LempiException) {
                e.message ?: e.toString()
            }
        }
        if (err != null) {
            Log.e(TAG, "start failed: $err")
            Lempi.status = err
            show(notification("Could not start: $err", null))
            return
        }
        wake = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "lempi:playback")
            .also { it.acquire() }
        Lempi.status = "running"
        main.post(refresh)
        // The phone's own music [REQ-AND-260], once playing has begun: a first
        // scan hashes every file not yet catalogued, and playback should not
        // wait for it. The Director rebuilds only if it changed anything.
        try {
            val found = Found.scan(this)
            if (found.rebound > 0u || found.tagsOnly > 0u || found.retired > 0u || found.identified > 0u) Lempi.reload()
        } catch (e: LempiException) {
            Log.w(TAG, "scan: ${e.message}")
        }
    }

    private fun stopPlayer() {
        main.removeCallbacks(refresh)
        if (Lempi.status == "running") {
            try {
                playerStop()
            } catch (e: LempiException) {
                Log.e(TAG, "stop: ${e.message}")
            }
        }
        wake?.let { if (it.isHeld) it.release() }
        Lempi.status = "stopped"
    }

    override fun onDestroy() {
        stopPlayer()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun update() {
        val s = playerState()
        val text = when {
            s == null -> "Stopped"
            s.title == null -> if (s.playing) "Playing" else "Paused"
            else -> listOfNotNull(s.title, s.artist).joinToString("  -  ")
        }
        show(notification(text, s?.playing))
    }

    private fun show(n: Notification) {
        getSystemService(NotificationManager::class.java).notify(1, n)
    }

    private fun action(label: String, what: String, code: Int): Notification.Action {
        val pi = PendingIntent.getService(this, code,
            Intent(this, PlayerService::class.java).setAction(what), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Action.Builder(null, label, pi).build()
    }

    private fun notification(text: String, playing: Boolean?): Notification {
        val open = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val b = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Lempi")
            .setContentText(text)
            .setSubText(BuildConfig.VERSION_NAME)
            .setContentIntent(open)
            .setOngoing(true)
        if (playing != null) {
            b.addAction(action(if (playing) "Pause" else "Play", TOGGLE, 2))
            b.addAction(action("Skip", SKIP, 3))
        }
        b.addAction(action("Stop", STOP, 1))
        return b.build()
    }

    companion object {
        const val STOP = "io.github.mangocats.lempi.STOP"
        const val TOGGLE = "io.github.mangocats.lempi.TOGGLE"
        const val SKIP = "io.github.mangocats.lempi.SKIP"
        private const val TAG = "lempi"
        private const val CHANNEL = "playback"
    }
}
