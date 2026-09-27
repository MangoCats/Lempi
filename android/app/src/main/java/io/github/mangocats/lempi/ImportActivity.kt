package io.github.mangocats.lempi

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.StatFs
import android.provider.OpenableColumns
import android.provider.DocumentsContract
import android.widget.ScrollView
import android.widget.TextView
import io.github.mangocats.lempi.ffi.ImportSummary
import io.github.mangocats.lempi.ffi.LempiException
import io.github.mangocats.lempi.ffi.importBundle
import java.io.File
import java.util.zip.ZipInputStream

/**
 * Import a bundle from Vipunen [REQ-AND-230], by either route it can take:
 *
 * - a `.zip` from the share sheet (SEND), "Open with" (VIEW), or the file
 *   picker ([PICK_ZIP]);
 * - an unpacked bundle folder, from the folder picker ([PICK_FOLDER]).
 *
 * Either way the bundle is copied into private staging first -- only
 * `payload.json`, `audio/...` and `covers/...`, and nothing whose name climbs
 * out of the folder -- and handed to the player library, which verifies every
 * file against Vipunen's byte hash before placing it in `Music/Lempi/`
 * [REQ-AND-210]. Each step first checks there is room for it, and refuses
 * rather than failing part-way. Staging is removed afterwards, and the running
 * player is asked to rebuild its choices so the new music can be picked.
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
            SCAN -> { title = "Music on the phone"; scan() }
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
                val music = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MUSIC), "Lempi")
                // Before anything is placed: the audio staged is at most what
                // placing it can need. Files the phone holds already take no
                // room, so this can refuse a bundle that would have fitted --
                // never the other way round.
                need("placing its audio in Music/Lempi", bytesUnder(File(staging, "audio")), music)
                say("Checking every file against Vipunen's record, then placing it in Music/Lempi…")
                val r = importBundle(File(filesDir, "library.db").path, staging.path, music.path)
                report(r)
                if (r.imported > 0u || r.reused > 0u || r.replaced > 0u || r.upgraded > 0u || r.rekeyed > 0u || r.covers > 0u) reload()
            } catch (e: NoRoom) {
                say("\nNot imported, and nothing changed: ${e.message}")
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
        if (r.upgraded > 0u) say("Found on the phone already, and now given Vipunen's data where they lie: ${r.upgraded}.")
        if (r.rekeyed > 0u) say("Renamed to Vipunen's new signature for the same audio: ${r.rekeyed}.")
        if (r.reused > 0u) say("Already on the phone, and bound rather than copied again: ${r.reused}.")
        if (r.releases > 0u) say("Albums described: ${r.releases}, with ${r.covers} cover picture(s).")
        listOf(
            "Damaged in transit, not placed" to r.corrupt,
            "Rewritten by Vipunen, but the phone would not let it be replaced" to r.notReplaced,
            "Changed on the phone since it was found, left as it is" to r.changedSinceScan,
            "Not in the bundle" to r.missing,
            "No byte hash to check against, not placed" to r.unverifiable,
            "A different file already has this name, left alone" to r.conflicts,
            "A path outside the music folder, refused" to r.unsafePaths,
            "Cover pictures damaged in transit, not stored" to r.badCovers,
        ).forEach { (what, list) ->
            if (list.isNotEmpty()) {
                say("\n$what (${list.size}):")
                list.forEach { say("  • $it") }
            }
        }
    }

    private fun reload() {
        say(when (val code = Lempi.reload()) {
            null -> "\nThe player is not running; the new music is picked up when it starts."
            in 200..299 -> "\nThe player is rebuilding its choices to include it."
            else -> "\nThe player did not accept the reload ($code); it will include it at its next start."
        })
    }

    /** Look for music already on the phone [REQ-AND-260]. */
    private fun scan() {
        Thread({
            try {
                say("Looking through the phone's music…")
                val r = Found.scan(this)
                say("\nLooked at ${r.lookedAt} file(s) not yet in Lempi.")
                if (r.rebound > 0u) say("Found where they had moved to: ${r.rebound}.")
                say("Added as tags-only (playable; named by their tags, no MusicBrainz data yet): ${r.tagsOnly}.")
                if (r.duplicates > 0u) say("Second copies of music Lempi already has, left alone: ${r.duplicates}.")
                if (r.retired > 0u) say("Removed, as not in a Music folder or a second copy: ${r.retired}.")
                if (r.hashed > 0u) say("Files Lempi had, fingerprinted now: ${r.hashed}.")
                if (r.identified > 0u) say("Found music identified by its audio: ${r.identified}.")
                if (r.unreadable.isNotEmpty()) {
                    say("\nCould not be read as audio (${r.unreadable.size}):")
                    r.unreadable.forEach { say("  • $it") }
                }
                if (r.rebound > 0u || r.tagsOnly > 0u || r.retired > 0u || r.identified > 0u) reload()
            } catch (e: LempiException) {
                say("\nScan failed: ${e.message}")
            }
        }, "lempi-scan").start()
    }

    /** Too little free space for a step of the import; nothing was changed. */
    private class NoRoom(message: String) : Exception(message)

    /**
     * Refuse, before starting, a step that would run out of space part-way:
     * `bytes` plus a margin must be free on the volume holding `where` (or its
     * nearest existing ancestor, since `Music/Lempi` may not exist yet).
     */
    private fun need(what: String, bytes: Long, where: File) {
        var dir: File? = where
        while (dir != null && !dir.exists()) dir = dir.parentFile
        val free = StatFs((dir ?: where).path).availableBytes
        if (bytes + MARGIN > free) {
            throw NoRoom("$what needs ${mb(bytes)} MB and ${mb(MARGIN)} MB to spare, and ${mb(free)} MB is free.")
        }
    }

    private fun mb(b: Long) = (b + 999_999) / 1_000_000

    private fun bytesUnder(dir: File): Long = dir.walkTopDown().filter { it.isFile }.sumOf { it.length() }

    /** A bundle's `.zip`: `payload.json`, `audio/...` and `covers/...` only. */
    private fun fromZip(uri: Uri): (File) -> Unit = { staging ->
        val size = contentResolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst() && !c.isNull(0)) c.getLong(0) else null
        }
        // At least its own size unpacked: an audio bundle is stored, and a
        // data-only one is deflated and unpacks to about ten times it
        // [SPEC-PL-095]. So this is a first check, and each entry below keeps
        // the margin free as well.
        if (size != null) need("unpacking the bundle", size, staging)
        val input = contentResolver.openInputStream(uri) ?: throw IllegalStateException("cannot open $uri")
        var files = 0
        ZipInputStream(input.buffered()).use { z ->
            while (true) {
                val e = z.nextEntry ?: break
                val name = e.name
                if (e.isDirectory || !wanted(name)) continue
                need("unpacking $name", maxOf(e.size, 0L), staging)
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
        need("copying the bundle", sizeOfTree(tree, DocumentsContract.getTreeDocumentId(tree), ""), staging)
        val files = copyTree(tree, DocumentsContract.getTreeDocumentId(tree), staging, "")
        say("Copied $files file(s).")
    }

    /** What [copyTree] would copy, in bytes. */
    private fun sizeOfTree(tree: Uri, docId: String, prefix: String): Long {
        var n = 0L
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, docId)
        val cols = arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE, DocumentsContract.Document.COLUMN_SIZE)
        contentResolver.query(children, cols, null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val rel = prefix + c.getString(1)
                if (c.getString(2) == DocumentsContract.Document.MIME_TYPE_DIR) {
                    if (bundleDir(rel)) n += sizeOfTree(tree, c.getString(0), "$rel/")
                } else if (wanted(rel) && !c.isNull(3)) {
                    n += c.getLong(3)
                }
            }
        }
        return n
    }

    /** A folder of a bundle that is copied: its audio, and its covers. */
    private fun bundleDir(rel: String) =
        rel == "audio" || rel.startsWith("audio/") || rel == "covers" || rel.startsWith("covers/")

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
                    if (bundleDir(rel)) n += copyTree(tree, id, staging, "$rel/")
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
        // A payload in parts [SPEC-PL-095]: payload-001.json, payload-002.json, ...
        val part = Regex("payload-[0-9]+\\.json")
        return name == "payload.json" || part.matches(name) || name.startsWith("audio/") || name.startsWith("covers/")
    }

    companion object {
        const val PICK_ZIP = "io.github.mangocats.lempi.PICK_ZIP"
        const val PICK_FOLDER = "io.github.mangocats.lempi.PICK_FOLDER"
        const val SCAN = "io.github.mangocats.lempi.SCAN"
        private const val REQ_ZIP = 1
        private const val REQ_FOLDER = 2
        /** Room kept free beyond what a step needs, for the database and the system. */
        private const val MARGIN = 64L * 1_000_000
    }
}
