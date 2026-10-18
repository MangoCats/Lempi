package io.github.mangocats.lempi

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.TextView
import android.window.OnBackInvokedCallback
import android.window.OnBackInvokedDispatcher

/**
 * The listening surface: the player's own lempi skin in a WebView
 * [REQ-AND-150], loaded once the service reports it is running.
 */
class MainActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private var web: WebView? = null
    private lateinit var status: TextView
    /** Holds the status or the skin, clear of the system bars [REQ-AND-155]. */
    private lateinit var frame: FrameLayout

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        status = TextView(this).apply { setPadding(32, 32, 32, 32) }
        frame = FrameLayout(this)
        setContentView(fitBars(frame))
        showing(status)
        val read = if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_AUDIO
        else Manifest.permission.READ_EXTERNAL_STORAGE
        if (checkSelfPermission(read) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(read), 1)
        } else {
            begin()
        }
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, results: IntArray) {
        begin()
    }

    private fun begin() {
        startForegroundService(Intent(this, PlayerService::class.java))
        poll()
    }

    /** Wait for the service; show its error if it gives one. */
    private fun poll() {
        when (val s = Lempi.status) {
            "running" -> show()
            "starting", "stopped" -> {
                status.text = "Starting Lempi…"
                main.postDelayed({ poll() }, 250)
            }
            else -> status.text = "Lempi could not start:\n\n$s"
        }
    }

    private fun show() {
        if (web != null) return
        val w = WebView(this)
        w.settings.javaScriptEnabled = true
        w.settings.domStorageEnabled = true
        // Links stay in the app; the skin is the whole interface. Back goes
        // to the skin's previous page while it has one.
        w.webViewClient = object : WebViewClient() {
            override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
                holdBack(view.canGoBack())
            }
        }
        showing(w)
        // The key once, in the query; the player answers with a cookie that
        // every later request -- and the WebSocket -- carries [REQ-AND-160].
        w.loadUrl("http://127.0.0.1:${Lempi.PORT}/?key=${Lempi.key}")
        web = w
        // The household's programme, if the hub assigned one [SPEC-PGM-310];
        // then the page is asked to look at once rather than at its next
        // minute, so the question shows while the person is here.
        val app = applicationContext
        Thread({
            Programmes.exchange(app)?.let { said ->
                android.util.Log.i("lempi", "programme: $said")
                main.post { web?.evaluateJavascript("window.Lempi && Lempi.askProgramme && Lempi.askProgramme()", null) }
            }
        }, "lempi-programme").start()
    }

    /**
     * Not visible: no skin. Measured 2026-09-26 on the moto g power (2021),
     * the WebView's renderer ran the lempi skin at 87% of a core with the
     * screen off -- more than with it on, since nothing was painting and the
     * script still handled a snapshot every 500 ms. The player lives in the
     * service and does not notice; the skin is rebuilt on return.
     */
    override fun onStop() {
        super.onStop()
        main.removeCallbacksAndMessages(null)
        web?.let {
            showing(status)
            holdBack(false)
            it.destroy()
            web = null
        }
    }

    override fun onStart() {
        super.onStart()
        if (web == null) poll()
    }

    /** Importing a bundle [REQ-AND-230]; sharing a .zip with Lempi does the same. */
    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "Import a bundle (.zip)…")
        menu.add(0, 2, 1, "Import a bundle folder…")
        menu.add(0, 3, 2, "Look for music on the phone")
        menu.add(0, 4, 3, "Export a backup…")
        menu.add(0, 5, 4, "Send new music to Vipunen…")
        menu.add(0, 6, 5, "Mesh membership…")
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId == 4) {
            startActivity(Intent(this, BackupActivity::class.java))
            return true
        }
        if (item.itemId == 5) {
            startActivity(Intent(this, SendActivity::class.java))
            return true
        }
        if (item.itemId == 6) {
            startActivity(Intent(this, MeshActivity::class.java))
            return true
        }
        val action = when (item.itemId) {
            1 -> ImportActivity.PICK_ZIP
            2 -> ImportActivity.PICK_FOLDER
            3 -> ImportActivity.SCAN
            else -> return super.onOptionsItemSelected(item)
        }
        startActivity(Intent(this, ImportActivity::class.java).setAction(action))
        return true
    }

    private fun showing(v: View) {
        frame.removeAllViews()
        frame.addView(v)
    }

    /**
     * Back, in two shapes [REQ-AND-155]. From Android 13 it is a callback held
     * only while the skin can go back, so that otherwise the system's own
     * gesture leaves the app, with its predictive animation; an app targeting
     * API 36 on Android 16 or later is never sent [onBackPressed] at all. On
     * Android 11 and 12 it is [onBackPressed], as before.
     */
    private var backHeld = false
    private var backCallback: Any? = null

    private fun holdBack(want: Boolean) {
        if (Build.VERSION.SDK_INT < 33 || want == backHeld) return
        val cb = (backCallback ?: OnBackInvokedCallback { web?.goBack() }.also { backCallback = it })
            as OnBackInvokedCallback
        if (want) onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, cb)
        else onBackInvokedDispatcher.unregisterOnBackInvokedCallback(cb)
        backHeld = want
    }

    @Deprecated("Android 11 and 12 only; see holdBack")
    override fun onBackPressed() {
        val w = web
        if (w != null && w.canGoBack()) w.goBack() else super.onBackPressed()
    }
}
