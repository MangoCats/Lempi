package io.github.mangocats.lempi

import android.content.Context

/**
 * The player library, and what the service knows about it.
 *
 * Everything the app asks of the player -- start, command, state, stop -- is
 * the generated interface in [io.github.mangocats.lempi.ffi] [REQ-AND-410].
 * Only [init] is hand-written JNI (`player/android/src/lib.rs`), because it
 * hands over the Android context. It returns `null` on success and a message
 * on failure; nothing is thrown across the boundary.
 */
object Lempi {
    init {
        System.loadLibrary("lempi_android")
    }

    /** Hand over the context, and send the player's log lines to [logPath]. */
    @JvmStatic
    external fun init(context: Context, logPath: String): String?

    /** The port the service serves the skin on. */
    const val PORT = 5720

    /** Made by the service at each start [REQ-AND-160]; read by the activity. */
    @Volatile
    var key: String? = null

    /** "starting", "running", "stopped", or the error that stopped it. */
    @Volatile
    var status: String = "stopped"
}
