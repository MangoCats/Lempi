package io.github.mangocats.lempi

import android.view.View
import android.view.WindowInsets

/**
 * Keep [view]'s content clear of what Android draws over it [REQ-AND-155]:
 * the status bar and the title bar above, the navigation bar below, a camera
 * cutout, and the keyboard while it is up.
 *
 * An app targeting API 35 or later is drawn edge to edge, and nothing opts
 * out. Measured 2026-10-17 on a Pixel 7a, Android 17: the title bar stayed in
 * place, but every screen's content began at the top of the display, behind
 * a 118 px status bar and the title bar under it -- the start-up error showed
 * only its last word, "listener.db" -- and ran under the 126 px navigation
 * bar at the bottom. The framework's title bar adds its own height to the
 * top inset it hands the content, so the system bars' insets are all there
 * is to apply. On Android 11 to 14 the window still fits the bars itself and
 * those insets arrive as zero, so this changes nothing there.
 */
fun fitBars(view: View): View {
    view.setOnApplyWindowInsetsListener { v, insets ->
        val clear = insets.getInsets(
            WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout() or WindowInsets.Type.ime())
        v.setPadding(clear.left, clear.top, clear.right, clear.bottom)
        WindowInsets.CONSUMED
    }
    return view
}
