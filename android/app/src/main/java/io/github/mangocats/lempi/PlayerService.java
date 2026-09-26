package io.github.mangocats.lempi;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import java.io.File;
import java.security.SecureRandom;

/**
 * The player's home [REQ-AND-120]: a foreground service of type
 * mediaPlayback, so playback outlives the screen and the activity.
 *
 * <p>Start runs off the main thread -- it opens the databases, builds the
 * Director and binds the port before it returns, which is seconds, not the
 * milliseconds the main thread allows.
 */
public final class PlayerService extends Service {
    static final String STOP = "io.github.mangocats.lempi.STOP";
    private static final String TAG = "lempi";
    private static final String CHANNEL = "playback";

    private PowerManager.WakeLock wake;

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(
                new NotificationChannel(CHANNEL, "Playback", NotificationManager.IMPORTANCE_LOW));
        startForeground(1, notification("Starting"),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && STOP.equals(intent.getAction())) {
            stopPlayer();
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!"stopped".equals(Lempi.status)) {
            return START_NOT_STICKY;
        }
        Lempi.status = "starting";
        new Thread(this::startPlayer, "lempi-start").start();
        return START_NOT_STICKY;
    }

    private void startPlayer() {
        File files = getFilesDir();
        String err = Lempi.init(getApplicationContext(), new File(files, "lempi.log").getPath());
        if (err == null) {
            byte[] raw = new byte[24];
            new SecureRandom().nextBytes(raw);
            StringBuilder hex = new StringBuilder();
            for (byte b : raw) hex.append(String.format("%02x", b));
            Lempi.key = hex.toString();
            err = Lempi.start(new File(files, "listener.db").getPath(),
                    new File(files, "library.db").getPath(), Lempi.PORT, Lempi.key);
        }
        if (err != null) {
            Log.e(TAG, "start failed: " + err);
            Lempi.status = err;
            update("Could not start: " + err);
            return;
        }
        PowerManager pm = getSystemService(PowerManager.class);
        wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "lempi:playback");
        wake.acquire();
        Lempi.status = "running";
        update("Playing  -  " + BuildConfig.VERSION_NAME);
    }

    private void stopPlayer() {
        if ("running".equals(Lempi.status)) {
            String err = Lempi.stop();
            if (err != null) Log.e(TAG, "stop: " + err);
        }
        if (wake != null && wake.isHeld()) wake.release();
        Lempi.status = "stopped";
    }

    @Override
    public void onDestroy() {
        stopPlayer();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void update(String text) {
        getSystemService(NotificationManager.class).notify(1, notification(text));
    }

    private Notification notification(String text) {
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 1,
                new Intent(this, PlayerService.class).setAction(STOP), PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle("Lempi")
                .setContentText(text)
                .setContentIntent(open)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "Stop", stop).build())
                .build();
    }
}
