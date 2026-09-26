package io.github.mangocats.lempi

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.DocumentsContract
import android.widget.ScrollView
import android.widget.TextView
import io.github.mangocats.lempi.ffi.ImportSummary
import io.github.mangocats.lempi.ffi.LempiException
import io.github.mangocats.lempi.ffi.importBundle
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.zip.ZipInputStream

/**
 * Import a bundle from Vipunen [REQ-AND-230], by either route it can take:
 *
 * - a `.zip` from the share sheet (SEND), "Open with" (VIEW), or the file
 *   picker ([PICK_ZIP]);
 * - an unpacked bundle folder, from the folder picker ([PICK_FOLDER]).
 *
 * Either way the bundle is copied into private staging first -- only
 * `payload.json` and `audio/...`, and nothing whose name climbs out of the
 * folder -- and handed to the player library, which verifies every file
 * against Vipunen's byte hash before placing it in `Music/Lempi/`
 * [REQ-AND-210]. Staging is removed afterwards, and the running player is
 * asked to rebuild its choices so the new music can be picked.
 */
class ImportActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var log: TextView

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        title = "Import a bundle"
        log = TextView(this).apply { setPadding(32, 32, 32, 32); textSize = 15f }
        setContentView(ScrollView(this).apply { addView(log) })
        if (saved != null) return
        val i = intent
        when (i.action) {
            // The untyped form: the typed one is Android 13, and the floor is 11.
            @Suppress("DEPRECATION")
            Intent.ACTION_SEND -> (i.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))?.let { importWith(fromZip(it)) }
                ?: say("Nothing was shared.")
            Intent.ACTION_VIEW -> i.data?.let { importWith(fromZip(it)) } ?: say("Nothing to open.")
            PICK_ZIP -> startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE).setType("application/zip"), REQ_ZIP)
            PICK_FOLDER -> startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE), REQ_FOLDER)
            else -> say("Share a bundle's .zip with Lempi, or choose one from Lempi's menu.")
        }
    }

    @Deprecated("Activity's own result handling; this app has no AndroidX")
    override fun onActivityResult(code: Int, result: Int, data: Intent?) {
        val uri = data?.data
        if (result != RESULT_OK || uri == null) {
            finish()
            return
        }
        importWith(if (code == REQ_ZIP) fromZip(uri) else fromFolder(uri))
    }

    private fun say(line: String) = main.post { log.append(line + "\n") }

    /** Stage on a worker thread, import, report; staging is always removed. */
    private fun importWith(stageInto: (File) -> Unit) {
        Thread({
            val staging = File(cacheDir, "import-${System.currentTimeMillis()}")
            try {
                say("Unpacking…")
                staging.mkdirs()
                stageInto(staging)
                say("Checking every file against Vipunen's record, then placing it in Music/Lempi…")
                val music = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MUSIC), "Lempi")
                val r = importBundle(File(filesDir, "library.db").path, staging.path, music.path)
                report(r)
                if (r.imported > 0u || r.reused > 0u || r.replaced > 0u) reload()
            } catch (e: LempiException) {
                say("\nImport failed: ${e.message}")
            } catch (e: Exception) {
                say("\nImport failed: $e")
            } finally {
                staging.deleteRecursively()
            }
        }, "lempi-import").start()
    }

    private fun report(r: ImportSummary) {
        if (r.refused.isNotEmpty()) {
            say("\nRefused, and nothing changed:")
            r.refused.forEach { say("  • $it") }
            return
        }
        say("\nImported ${r.imported}.")
        if (r.already > 0u) say("Already here: ${r.already}.")
        if (r.replaced > 0u) say("Rewritten by Vipunen since, and replaced where they lie: ${r.replaced}.")
        if (r.reused > 0u) say("Already on the phone, and bound rather than copied again: ${r.reused}.")
        listOf(
            "Damaged in transit, not placed" to r.corrupt,
            "Rewritten by Vipunen, but the phone would not let it be replaced" to r.notReplaced,
            "Not in the bundle" to r.missing,
            "No byte hash to check against, not placed" to r.unverifiable,
            "A different file already has this name, left alone" to r.conflicts,
            "A path outside the music folder, refused" to r.unsafePaths,
        ).forEach { (what, list) ->
            if (list.isNotEmpty()) {
                say("\n$what (${list.size}):")
                list.forEach { say("  • $it") }
            }
        }
    }

    /** Ask the running player to rebuild its choices [the web API's own reload]. */
    private fun reload() {
        val key = Lempi.key
        if (Lempi.status != "running" || key == null) {
            say("\nThe player is not running; the new music is picked up when it starts.")
            return
        }
        try {
            val c = URL("http://127.0.0.1:${Lempi.PORT}/library/reload").openConnection() as HttpURLConnection
            c.requestMethod = "POST"
            c.setRequestProperty("x-lempi-key", key)
            c.connectTimeout = 5_000
            c.readTimeout = 10_000
            val code = c.responseCode
            c.disconnect()
            say(if (code in 200..299) "\nThe player is rebuilding its choices to include it."
                else "\nThe player did not accept the reload ($code); it will include it at its next start.")
        } catch (e: Exception) {
            say("\nCould not reach the player to reload: $e")
        }
    }

    /** A bundle's `.zip`: `payload.json` and `audio/...` only. */
    private fun fromZip(uri: Uri): (File) -> Unit = { staging ->
        val input = contentResolver.openInputStream(uri) ?: throw IllegalStateException("cannot open $uri")
        var files = 0
        ZipInputStream(input.buffered()).use { z ->
            while (true) {
                val e = z.nextEntry ?: break
                val name = e.name
                if (e.isDirectory || !wanted(name)) continue
                val out = File(staging, name)
                out.parentFile?.mkdirs()
                out.outputStream().use { z.copyTo(it) }
                files++
            }
        }
        say("Unpacked $files file(s).")
    }

    /** An unpacked bundle folder, chosen in the folder picker. */
    private fun fromFolder(tree: Uri): (File) -> Unit = { staging ->
        val files = copyTree(tree, DocumentsContract.getTreeDocumentId(tree), staging, "")
        say("Copied $files file(s).")
    }

    private fun copyTree(tree: Uri, docId: String, staging: File, prefix: String): Int {
        var n = 0
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, docId)
        val cols = arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME, DocumentsContract.Document.COLUMN_MIME_TYPE)
        contentResolver.query(children, cols, null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val id = c.getString(0)
                val name = c.getString(1)
                val rel = prefix + name
                if (c.getString(2) == DocumentsContract.Document.MIME_TYPE_DIR) {
                    if (rel == "audio" || rel.startsWith("audio/")) n += copyTree(tree, id, staging, "$rel/")
                } else if (wanted(rel)) {
                    val out = File(staging, rel)
                    out.parentFile?.mkdirs()
                    contentResolver.openInputStream(DocumentsContract.buildDocumentUriUsingTree(tree, id))
                        ?.use { i -> out.outputStream().use { i.copyTo(it) } }
                    n++
                }
            }
        }
        return n
    }

    /** Only what a bundle holds, and nothing that could leave the staging folder. */
    private fun wanted(name: String): Boolean {
        if (name.startsWith("/") || name.contains('\\')) return false
        if (name.split('/').any { it == ".." || it.isEmpty() }) return false
        return name == "payload.json" || name.startsWith("audio/")
    }

    companion object {
        const val PICK_ZIP = "io.github.mangocats.lempi.PICK_ZIP"
        const val PICK_FOLDER = "io.github.mangocats.lempi.PICK_FOLDER"
        private const val REQ_ZIP = 1
        private const val REQ_FOLDER = 2
    }
}
