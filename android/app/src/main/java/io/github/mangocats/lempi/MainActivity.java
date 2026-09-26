package io.github.mangocats.lempi;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.TextView;

/**
 * The listening surface: the player's own lempi skin in a WebView
 * [REQ-AND-150], loaded once the service reports it is running.
 */
public final class MainActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private WebView web;
    private TextView status;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        status = new TextView(this);
        status.setPadding(32, 32, 32, 32);
        setContentView(status);

        String read = android.os.Build.VERSION.SDK_INT >= 33
                ? Manifest.permission.READ_MEDIA_AUDIO : Manifest.permission.READ_EXTERNAL_STORAGE;
        if (checkSelfPermission(read) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {read}, 1);
        } else {
            begin();
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        begin();
    }

    private void begin() {
        startForegroundService(new Intent(this, PlayerService.class));
        poll();
    }

    /** Wait for the service; show its error if it gives one. */
    private void poll() {
        String s = Lempi.status;
        if ("running".equals(s)) {
            show();
        } else if ("starting".equals(s) || "stopped".equals(s)) {
            status.setText("Starting Lempi…");
            main.postDelayed(this::poll, 250);
        } else {
            status.setText("Lempi could not start:\n\n" + s);
        }
    }

    private void show() {
        if (web != null) return;
        web = new WebView(this);
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        // Links stay in the app; the skin is the whole interface.
        web.setWebViewClient(new WebViewClient());
        setContentView(web);
        // The key once, in the query; the player answers with a cookie that
        // every later request -- and the WebSocket -- carries [REQ-AND-160].
        web.loadUrl("http://127.0.0.1:" + Lempi.PORT + "/?key=" + Lempi.key);
    }

    /**
     * Not visible: no skin. Measured 2026-09-26 on the moto g power (2021),
     * the WebView's renderer ran the lempi skin at 87% of a core with the
     * screen off -- more than with it on, since nothing was painting and the
     * script still handled a snapshot every 500 ms. The player lives in the
     * service and does not notice; the skin is rebuilt on return.
     */
    @Override
    protected void onStop() {
        super.onStop();
        main.removeCallbacksAndMessages(null);
        if (web != null) {
            setContentView(status);
            web.destroy();
            web = null;
        }
    }

    @Override
    protected void onStart() {
        super.onStart();
        if (web == null) poll();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
