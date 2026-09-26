package io.github.mangocats.lempi;

import android.content.Context;

/**
 * The player library, and what the service knows about it.
 *
 * <p>The three natives are {@code player/android/src/lib.rs}. Each returns
 * {@code null} on success and a message on failure -- nothing is thrown across
 * the boundary.
 */
final class Lempi {
    static {
        System.loadLibrary("lempi_android");
    }

    /** Hand over the context, and send the player's log lines to {@code logPath}. */
    static native String init(Context context, String logPath);

    /** Start the player, its web server on loopback {@code port} behind {@code key}. */
    static native String start(String listener, String library, int port, String key);

    /** Persist and stop. */
    static native String stop();

    /** The port the service serves the skin on. */
    static final int PORT = 5720;

    /** Made by the service at each start [REQ-AND-160]; read by the activity. */
    static volatile String key;

    /** "starting", "running", "stopped", or the error that stopped it. */
    static volatile String status = "stopped";

    private Lempi() {}
}
