package io.github.mangocats.lempi

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.ScrollView
import android.widget.TextView
import io.github.mangocats.lempi.ffi.BackupReady
import io.github.mangocats.lempi.ffi.LempiException
import io.github.mangocats.lempi.ffi.prepareBackup
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * A backup of the listening, taken off the phone on request [REQ-AND-195].
 *
 * The phone's plays, preferences, likes and programmes exist nowhere else
 * [REQ-AND-190], and its own backups live in app-private storage, which
 * uninstalling deletes. So the user asks, chooses where it goes in Android's
 * own save dialog -- Downloads, a card, a cloud folder -- and the newest
 * backup that passes its integrity check is written there. The copy is then
 * read back and compared, byte hash for byte hash: a backup that was not
 * checked where it landed is a hope, not a backup. Nothing leaves the phone
 * unasked.
 */
class BackupActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var log: TextView

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        title = "Export a backup"
        log = TextView(this).apply { setPadding(32, 32, 32, 32); textSize = 15f }
        setContentView(ScrollView(this).apply { addView(log) })
        if (saved != null) return
        val stamp = SimpleDateFormat("yyyyMMdd-HHmm", Locale.ROOT).format(Date())
        startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/vnd.sqlite3")
            .putExtra(Intent.EXTRA_TITLE, "lempi-listening-$stamp.db"), REQ_SAVE)
    }

    @Deprecated("Activity's own result handling; this app has no AndroidX")
    override fun onActivityResult(code: Int, result: Int, data: Intent?) {
        val uri = data?.data
        if (code != REQ_SAVE || result != RESULT_OK || uri == null) {
            finish()
            return
        }
        Thread({ export(uri) }, "lempi-backup").start()
    }

    private fun say(line: String) = main.post { log.append(line + "\n") }

    private fun export(uri: Uri) {
        try {
            say("Taking a fresh backup and checking it…")
            val b: BackupReady = prepareBackup(File(filesDir, "listener.db").path)
            if (!b.fresh) say("A fresh backup could not be taken; this is the newest earlier one that passed its check.")
            contentResolver.openOutputStream(uri, "wt")?.use { out ->
                File(b.path).inputStream().use { it.copyTo(out) }
            } ?: throw IllegalStateException("cannot open the chosen place for writing")
            val back = contentResolver.openInputStream(uri)?.use { sha256(it) }
            if (back != b.sha256) {
                say("\nWritten, but what arrived is not what was sent (${back ?: "unreadable"}). Do not rely on it; try again, or somewhere else.")
                return
            }
            val taken = SimpleDateFormat("d MMM yyyy, HH:mm", Locale.getDefault()).format(Date(b.takenAt * 1000))
            say("\nBackup written, and checked where it landed.")
            say("Taken $taken: ${b.plays} plays, ${b.preferences} preferences, ${b.likes} likes, ${b.programs} programmes.")
            say("${b.bytes / 1024u} KB.")
        } catch (e: LempiException) {
            say("\nNo backup written: ${e.message}")
        } catch (e: Exception) {
            say("\nNo backup written: $e")
        }
    }

    private fun sha256(input: java.io.InputStream): String {
        val md = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(1 shl 16)
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            md.update(buf, 0, n)
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    companion object {
        private const val REQ_SAVE = 1
    }
}
