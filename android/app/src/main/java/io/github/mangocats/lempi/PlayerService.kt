package io.github.mangocats.lempi

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import androidx.annotation.OptIn
import androidx.core.app.NotificationCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.LibraryResult
import androidx.media3.session.MediaLibraryService
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaStyleNotificationHelper
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
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
 *
 * **Audio focus** [REQ-AND-140], [GDE-APP-100]. Playback may start from here,
 * from the skin in the WebView, or from a headset, and only this service can
 * ask Android for focus -- so it watches the player's own state each second,
 * and asks whenever it finds it playing without focus; refused, it pauses.
 * Losing focus for good pauses. Losing it for a moment pauses too, and
 * regaining it resumes **only if focus was what paused it**: a listener's own
 * pause is never undone by the system. A loss that allows ducking is left to
 * Android, which lowers the stream itself when an app does not ask to handle
 * it; Lempi's own duck [GDE-HST-340] is built only if a device shows that
 * does not reach its stream. Unplugged headphones pause, as the listener
 * would -- and stay paused.
 *
 * **The media session** [REQ-AND-130]. A [MediaLibraryService], so Android
 * Auto can browse to it, with a session over [LempiPlayer], which forwards to
 * the player's commands and plays nothing itself. The session is what the
 * lock screen, the notification, headset and Bluetooth buttons and Auto all
 * talk to. Its notification is this service's own, media-styled and tied to
 * the session, and kept for as long as the player runs -- Media3's default
 * would drop the service out of the foreground when paused, and the player,
 * its web server and its scan live in this process.
 */
@OptIn(UnstableApi::class)
class PlayerService : MediaLibraryService() {
    private var wake: PowerManager.WakeLock? = null
    private val main = Handler(Looper.getMainLooper())
    private var ticks = 0
    private val refresh = object : Runnable {
        override fun run() {
            if (Lempi.status == "running") {
                keepFocus()
                // The notification every five ticks, as before.
                if (ticks++ % 5 == 0) update()
                main.postDelayed(this, 1_000)
            }
        }
    }

    private val audio by lazy { getSystemService(AudioManager::class.java) }
    private val focusRequest by lazy {
        AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                .build())
            // Android ducks the stream itself for a prompt that allows it.
            .setWillPauseWhenDucked(false)
            .setOnAudioFocusChangeListener({ onFocus(it) }, main)
            .build()
    }
    private var hasFocus = false
    /** Paused by a brief loss of focus, and so to resume when it returns. */
    private var pausedForFocus = false

    private fun playing() = try { playerState()?.playing == true } catch (e: LempiException) { false }

    private fun send(t: Transport) {
        try {
            playerCommand(t)
        } catch (e: LempiException) {
            Log.w(TAG, "command refused: ${e.message}")
        }
        main.postDelayed({ update() }, 400)
    }

    /** Playing without focus -- started from the skin, a headset, or at
     *  start -- asks for it; refused (a call in progress), it pauses. */
    private fun keepFocus() {
        if (hasFocus || !playing()) return
        if (audio.requestAudioFocus(focusRequest) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
            hasFocus = true
            pausedForFocus = false
        } else {
            Log.i(TAG, "focus refused; pausing")
            send(Transport.PAUSE)
        }
    }

    private fun onFocus(change: Int) {
        when (change) {
            AudioManager.AUDIOFOCUS_LOSS -> {
                // For good: another player took over. Paused, and not resumed
                // by anything but the listener.
                hasFocus = false
                pausedForFocus = false
                if (playing()) send(Transport.PAUSE)
                audio.abandonAudioFocusRequest(focusRequest)
                Log.i(TAG, "focus lost; paused")
            }
            AudioManager.AUDIOFOCUS_LOSS_TRANSIENT -> {
                if (playing()) {
                    pausedForFocus = true
                    send(Transport.PAUSE)
                }
                Log.i(TAG, "focus lost for a moment; paused=$pausedForFocus")
            }
            AudioManager.AUDIOFOCUS_GAIN -> {
                hasFocus = true
                if (pausedForFocus) {
                    pausedForFocus = false
                    send(Transport.PLAY)
                    Log.i(TAG, "focus back; resumed")
                }
            }
        }
    }

    /** Headphones unplugged, or a Bluetooth output gone: pause, as the
     *  listener would, and stay paused. */
    private val noisy = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            if (i.action == AudioManager.ACTION_AUDIO_BECOMING_NOISY && playing()) {
                pausedForFocus = false
                send(Transport.PAUSE)
                Log.i(TAG, "output became noisy; paused")
            }
        }
    }

    private val player by lazy {
        LempiPlayer(
            onPlay = { send(Transport.PLAY) },
            // The listener's own pause, from any control: never undone by focus.
            onPause = { pausedForFocus = false; send(Transport.PAUSE) },
            onSkip = { send(Transport.SKIP) },
        )
    }
    private lateinit var session: MediaLibrarySession

    /** What Auto browses: one folder holding one thing, the radio. */
    private val root = MediaItem.Builder().setMediaId("root").setMediaMetadata(
        MediaMetadata.Builder().setTitle("Lempi").setIsBrowsable(true).setIsPlayable(false)
            .setMediaType(MediaMetadata.MEDIA_TYPE_FOLDER_MIXED).build()).build()
    private val radio = MediaItem.Builder().setMediaId("radio").setMediaMetadata(
        MediaMetadata.Builder().setTitle("Lempi radio").setIsBrowsable(false).setIsPlayable(true)
            .setMediaType(MediaMetadata.MEDIA_TYPE_RADIO_STATION).build()).build()

    private val library = object : MediaLibrarySession.Callback {
        override fun onGetLibraryRoot(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, params: LibraryParams?,
        ): ListenableFuture<LibraryResult<MediaItem>> = Futures.immediateFuture(LibraryResult.ofItem(root, params))

        override fun onGetChildren(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, parentId: String,
            page: Int, pageSize: Int, params: LibraryParams?,
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> =
            Futures.immediateFuture(LibraryResult.ofItemList(
                if (parentId == root.mediaId) ImmutableList.of(radio) else ImmutableList.of(), params))

        override fun onGetItem(
            session: MediaLibrarySession, browser: MediaSession.ControllerInfo, mediaId: String,
        ): ListenableFuture<LibraryResult<MediaItem>> = Futures.immediateFuture(
            if (mediaId == radio.mediaId) LibraryResult.ofItem(radio, null)
            else LibraryResult.ofError(LibraryResult.RESULT_ERROR_BAD_VALUE))

        // Items chosen in Auto carry no URI -- there is nothing to resolve;
        // the player plays what it chooses.
        override fun onAddMediaItems(
            mediaSession: MediaSession, controller: MediaSession.ControllerInfo, mediaItems: MutableList<MediaItem>,
        ): ListenableFuture<MutableList<MediaItem>> = Futures.immediateFuture(mediaItems)
    }

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Playback", NotificationManager.IMPORTANCE_LOW))
        session = MediaLibrarySession.Builder(this, player, library)
            .setSessionActivity(PendingIntent.getActivity(this, 0,
                Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE))
            .build()
        startForeground(1, notification("Starting", null),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaLibrarySession = session

    /** This service keeps its own notification; see the class comment. */
    override fun onUpdateNotification(session: MediaSession, startInForegroundRequired: Boolean) {
        update()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Headset and Bluetooth buttons arrive here too; Media3 routes them to
        // the session.
        if (intent?.action == Intent.ACTION_MEDIA_BUTTON) return super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            STOP -> {
                stopPlayer()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            TOGGLE, SKIP -> {
                if (intent.action == SKIP) {
                    send(Transport.SKIP)
                } else if (playing()) {
                    // The listener's own pause: never undone by focus.
                    pausedForFocus = false
                    send(Transport.PAUSE)
                } else {
                    send(Transport.PLAY)
                }
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
        registerReceiver(noisy, IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY))
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
            try { unregisterReceiver(noisy) } catch (e: IllegalArgumentException) { }
            audio.abandonAudioFocusRequest(focusRequest)
            hasFocus = false
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
        session.release()
        player.release()
        super.onDestroy()
    }

    private fun update() {
        val s = if (Lempi.status == "running") playerState() else null
        player.publish(s)
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

    private fun action(icon: Int, label: String, what: String, code: Int): NotificationCompat.Action {
        val pi = PendingIntent.getService(this, code,
            Intent(this, PlayerService::class.java).setAction(what), PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Action.Builder(icon, label, pi).build()
    }

    /** Media-styled and tied to the session, so the lock screen and the
     *  quick settings show it as a player. */
    private fun notification(text: String, playing: Boolean?): Notification {
        val open = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val b = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Lempi")
            .setContentText(text)
            .setSubText(BuildConfig.VERSION_NAME)
            .setContentIntent(open)
            .setOngoing(true)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
        val compact = mutableListOf<Int>()
        if (playing != null) {
            b.addAction(action(if (playing) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play,
                if (playing) "Pause" else "Play", TOGGLE, 2))
            b.addAction(action(android.R.drawable.ic_media_next, "Skip", SKIP, 3))
            compact += listOf(0, 1)
        }
        b.addAction(action(android.R.drawable.ic_menu_close_clear_cancel, "Stop", STOP, 1))
        if (::session.isInitialized) {
            b.setStyle(MediaStyleNotificationHelper.MediaStyle(session)
                .setShowActionsInCompactView(*compact.toIntArray()))
        }
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
