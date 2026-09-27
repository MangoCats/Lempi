package io.github.mangocats.lempi

import android.content.Context
import android.provider.MediaStore
import io.github.mangocats.lempi.ffi.FoundSummary
import io.github.mangocats.lempi.ffi.scanFound
import java.io.File

/**
 * The phone's own music [REQ-AND-260]: what MediaStore lists as music, handed
 * to the player library, which catalogues what it does not already hold --
 * a moved file rebound, anything else as a tags-only passage.
 *
 * MediaStore is the narrowest way to find audio [REQ-AND-220]; its path
 * column is how the player opens a file, which works on Android 11 with the
 * read permission already granted [REQ-AND-920].
 */
object Found {
    /** Every file MediaStore lists as music, by path. */
    fun audioPaths(context: Context): List<String> {
        val out = ArrayList<String>()
        @Suppress("DEPRECATION") // DATA: still the path, and the player opens paths
        val cols = arrayOf(MediaStore.Audio.Media.DATA)
        context.contentResolver.query(
            MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, cols,
            "${MediaStore.Audio.Media.IS_MUSIC} != 0", null, null,
        )?.use { c ->
            while (c.moveToNext()) c.getString(0)?.let { out.add(it) }
        }
        return out
    }

    /** Scan, and say whether anything changed the player's choices. */
    fun scan(context: Context): FoundSummary =
        scanFound(File(context.filesDir, "library.db").path, audioPaths(context))
}
