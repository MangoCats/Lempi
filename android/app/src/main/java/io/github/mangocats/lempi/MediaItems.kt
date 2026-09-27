package io.github.mangocats.lempi

import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import java.io.File
import java.security.MessageDigest

/**
 * The media-store item for a file path, on whichever volume holds it: what
 * `MediaStore.createWriteRequest` needs to ask leave to replace a file Lempi
 * did not create [SPEC-PL-098], [SPEC-PID-040].
 */
fun mediaUri(context: Context, path: String): Uri? {
    for (volume in MediaStore.getExternalVolumeNames(context)) {
        val base = MediaStore.Audio.Media.getContentUri(volume)
        @Suppress("DEPRECATION")
        context.contentResolver.query(base, arrayOf(MediaStore.MediaColumns._ID),
            "${MediaStore.MediaColumns.DATA} = ?", arrayOf(path), null)?.use { c ->
            if (c.moveToFirst()) return ContentUris.withAppendedId(base, c.getLong(0))
        }
    }
    return null
}

/** A file's SHA-256, lower-case hex: the byte hash every Lempi node keys by. */
fun sha256(file: File): String {
    val md = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input ->
        val buf = ByteArray(1 shl 16)
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            md.update(buf, 0, n)
        }
    }
    return md.digest().joinToString("") { "%02x".format(it) }
}
