package io.github.mangocats.lempi

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.DocumentsContract
import android.provider.MediaStore
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import io.github.mangocats.lempi.ffi.FoundFile
import io.github.mangocats.lempi.ffi.foundToSend
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Music the phone found for itself, sent to Vipunen for induction
 * [REQ-AND-280].
 *
 * The node is named, not discovered [REQ-AND-286]: an address, and the port
 * if not the intake's own. The key is Lempi's own unless the user gives one
 * [REQ-AND-287]. Nothing moves before Vipunen has answered the description
 * of each file [REQ-AND-288]: held already (these bytes, or this audio
 * re-tagged), a near match, or new. Only the chosen files it wants are sent,
 * and each is checked there against its byte hash. What arrives waits for a
 * person at Vipunen [REQ-AND-289]; on the phone it stays tags-only until a
 * bundle names it.
 *
 * Where no node answers, the same offer and files can be written to a folder
 * the user moves by hand [REQ-AND-285]; `tools/intake.py --from-folder` takes
 * it from there.
 *
 * A file starts chosen only if it has an artist and a title: the untagged
 * ones on a phone are most often not music at all.
 */
class SendActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var node: EditText
    private lateinit var key: EditText
    private lateinit var list: LinearLayout
    private lateinit var log: TextView
    private lateinit var send: Button
    private var files: List<FoundFile> = emptyList()
    private val boxes = mutableListOf<CheckBox>()
    /** By file index: what Vipunen answered, and whether it wants it. */
    private val answers = mutableMapOf<Int, Pair<String, Boolean>>()
    /** By file index: a repair a person at Vipunen decided [SPEC-PID-040] --
     *  this file is a damaged copy, and Vipunen has the good one. */
    private val repairs = mutableMapOf<Int, JSONObject>()
    private lateinit var repairButton: Button
    /** Fetched and checked, waiting for leave to write: file index, its
     *  media-store item, the good copy in the cache. */
    private var ready: List<Triple<Int, Uri, File>> = emptyList()

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        title = "Send music to Vipunen"
        val prefs = getSharedPreferences("intake", MODE_PRIVATE)
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 32, 32, 32) }
        node = EditText(this).apply {
            hint = "Vipunen's address, e.g. 192.168.67.95"
            setText(prefs.getString("node", ""))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        key = EditText(this).apply {
            hint = "Key (blank: Lempi's own)"
            setText(prefs.getString("key", ""))
        }
        val ask = Button(this).apply { text = "Ask Vipunen"; setOnClickListener { offer() } }
        send = Button(this).apply { text = "Send"; isEnabled = false; setOnClickListener { sendWanted() } }
        val folder = Button(this).apply { text = "Save to a folder instead"; setOnClickListener { pickFolder() } }
        repairButton = Button(this).apply { visibility = View.GONE; setOnClickListener { fetchGood() } }
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        log = TextView(this).apply { textSize = 14f; setPadding(0, 24, 0, 0) }
        listOf(node, key, ask, send, repairButton, folder, list, log).forEach { col.addView(it) }
        setContentView(ScrollView(this).apply { addView(col) })
        Thread({
            try {
                files = foundToSend(File(filesDir, "library.db").path)
                main.post { showFiles() }
            } catch (e: Exception) {
                say("Could not list the phone's music: $e")
            }
        }, "lempi-found").start()
    }

    private fun say(line: String) {
        main.post { log.append(line + "\n") }
    }

    private fun label(f: FoundFile) = listOfNotNull(f.artist, f.title).joinToString(" — ")
        .ifEmpty { File(f.path).name } + (f.album?.let { "  ($it)" } ?: "")

    private fun showFiles() {
        list.removeAllViews()
        boxes.clear()
        if (files.isEmpty()) {
            say("Every file on the phone is one Vipunen has named. Nothing to send.")
            return
        }
        say("${files.size} file(s) Lempi found that Vipunen has not named.")
        files.forEach { f ->
            val box = CheckBox(this).apply {
                text = label(f)
                isChecked = f.artist != null && f.title != null
                setOnCheckedChangeListener { _, _ -> refreshSend() }
            }
            boxes += box
            list.addView(box)
        }
    }

    private fun refreshSend() {
        val n = files.indices.count { boxes[it].isChecked && answers[it]?.second == true }
        send.text = if (n > 0) "Send $n" else "Send"
        send.isEnabled = n > 0
    }

    /** `host`, or `host:port`; the intake's own port if none. */
    private fun base(): String? {
        val raw = node.text.toString().trim().removePrefix("http://").trimEnd('/')
        if (raw.isEmpty()) {
            say("Name Vipunen's address first.")
            return null
        }
        getSharedPreferences("intake", MODE_PRIVATE).edit()
            .putString("node", raw).putString("key", key.text.toString().trim()).apply()
        return "http://" + (if (raw.contains(':')) raw else "$raw:$PORT")
    }

    private fun theKey() = key.text.toString().trim().ifEmpty { APP_KEY }

    private fun description(indices: List<Int>): JSONObject {
        val arr = JSONArray()
        for (i in indices) {
            val f = files[i]
            arr.put(JSONObject().apply {
                put("id", i)
                put("sha256", f.sha256)
                f.audioMd5?.let { put("audio_md5", it) }
                put("size", f.size.toLong())
                put("duration_ms", f.durationMs.toLong())
                put("name", File(f.path).name)
                put("tags", JSONObject().apply {
                    f.title?.let { put("title", it) }
                    f.artist?.let { put("artist", it) }
                    f.album?.let { put("album", it) }
                    f.trackNo?.let { put("track", it) }
                })
            })
        }
        return JSONObject().put("files", arr)
    }

    private fun chosen() = files.indices.filter { boxes[it].isChecked }

    /** [REQ-AND-288]: describe, and hear which are wanted. */
    private fun offer() {
        val base = base() ?: return
        val which = chosen()
        if (which.isEmpty()) {
            // Nothing to offer, but Vipunen may still have repairs for files here.
            say("\nAsking Vipunen at $base whether any file here needs repair…")
            Thread({
                try {
                    askRepairs(base)
                    if (repairs.isEmpty()) say("None does. Choose files to offer them.")
                } catch (e: Exception) {
                    say("Could not reach Vipunen: ${e.message ?: e}.")
                }
            }, "lempi-repairs").start()
            return
        }
        say("\nAsking Vipunen at $base about ${which.size} file(s)…")
        Thread({
            try {
                val c = URL("$base/offer").openConnection() as HttpURLConnection
                c.requestMethod = "POST"
                c.connectTimeout = 8000
                c.readTimeout = 30000
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.setRequestProperty("X-Lempi-Key", theKey())
                c.setRequestProperty("X-Lempi-Sender", "Lempi on ${Build.MODEL}")
                c.outputStream.use { it.write(description(which).toString().toByteArray()) }
                val code = c.responseCode
                if (code != 200) {
                    say(if (code == 403) "Vipunen refused the key." else "Vipunen answered $code.")
                    return@Thread
                }
                val ans = JSONObject(c.inputStream.bufferedReader().readText()).getJSONArray("files")
                answers.clear()
                for (k in 0 until ans.length()) {
                    val a = ans.getJSONObject(k)
                    answers[a.getInt("id")] = a.getString("verdict") to a.getBoolean("want")
                }
                main.post {
                    for ((i, a) in answers) {
                        val what = when (a.first) {
                            "held" -> "Vipunen has it"
                            "near" -> "Vipunen has something like it"
                            "new" -> "new to Vipunen"
                            "pending" -> "already waiting at Vipunen"
                            else -> a.first
                        }
                        boxes[i].text = label(files[i]) + "\n   " + what
                    }
                    refreshSend()
                }
                val wanted = answers.values.count { it.second }
                say("Vipunen wants $wanted of ${which.size}.")
                askRepairs(base)
            } catch (e: Exception) {
                say("Could not reach Vipunen: ${e.message ?: e}.\nIs its intake running (tools/intake.py), and the address right? " +
                    "Or save to a folder instead.")
            }
        }, "lempi-offer").start()
    }

    private fun sendWanted() {
        val base = base() ?: return
        val which = files.indices.filter { boxes[it].isChecked && answers[it]?.second == true }
        send.isEnabled = false
        Thread({
            var sent = 0
            for (i in which) {
                val f = files[i]
                try {
                    val c = URL("$base/file/${f.sha256}").openConnection() as HttpURLConnection
                    c.requestMethod = "PUT"
                    c.connectTimeout = 8000
                    c.readTimeout = 120000
                    c.doOutput = true
                    c.setFixedLengthStreamingMode(File(f.path).length())
                    c.setRequestProperty("X-Lempi-Key", theKey())
                    File(f.path).inputStream().use { input -> c.outputStream.use { input.copyTo(it) } }
                    val code = c.responseCode
                    if (code == 201) {
                        sent++
                        say("  sent: ${label(f)}")
                    } else {
                        say("  NOT sent (Vipunen answered $code): ${label(f)}")
                    }
                } catch (e: Exception) {
                    say("  NOT sent (${e.message ?: e}): ${label(f)}")
                }
            }
            say("\nSent $sent of ${which.size}. They wait at Vipunen for a person to decide; " +
                "once inducted, the next bundle names them here.")
            main.post { answers.clear(); refreshSend() }
        }, "lempi-send").start()
    }

    // ---- repairs [SPEC-PID-040] ------------------------------------------

    /**
     * Which of the phone's files a person at Vipunen found to be damaged
     * copies of files it holds, with the good copy on offer. Asked of every
     * listed file, chosen or not. On the calling thread.
     */
    private fun askRepairs(base: String) {
        val c = URL("$base/repairs").openConnection() as HttpURLConnection
        c.connectTimeout = 8000
        c.readTimeout = 30000
        c.setRequestProperty("X-Lempi-Key", theKey())
        if (c.responseCode != 200) return   // an older intake: no repairs to offer
        val offered = JSONObject(c.inputStream.bufferedReader().readText()).getJSONArray("repairs")
        val bySha = (0 until offered.length()).map { offered.getJSONObject(it) }
            .associateBy { it.getString("damaged_sha256") }
        repairs.clear()
        files.forEachIndexed { i, f -> bySha[f.sha256]?.let { repairs[i] = it } }
        main.post {
            for (i in repairs.keys) {
                boxes[i].text = label(files[i]) + "\n   damaged here; Vipunen has the good copy"
            }
            repairButton.text = "Repair ${repairs.size} damaged file(s)"
            repairButton.isEnabled = true
            repairButton.visibility = if (repairs.isEmpty()) View.GONE else View.VISIBLE
        }
        if (repairs.isNotEmpty()) {
            say("${repairs.size} file(s) here are damaged copies of Vipunen's; it offers the good copy of each.")
        }
    }

    /** Fetch each good copy, check it by its byte hash, then ask Android for
     *  leave to write over the damaged files -- which Lempi did not create. */
    private fun fetchGood() {
        val base = base() ?: return
        repairButton.isEnabled = false
        Thread({
            val got = mutableListOf<Triple<Int, Uri, File>>()
            for ((i, r) in repairs) {
                val f = files[i]
                val good = r.getString("good_sha256")
                val staged = File(cacheDir, "repair-$good")
                try {
                    val c = URL("$base/good/$good").openConnection() as HttpURLConnection
                    c.connectTimeout = 8000
                    c.readTimeout = 120000
                    c.setRequestProperty("X-Lempi-Key", theKey())
                    if (c.responseCode != 200) {
                        say("  not fetched (Vipunen answered ${c.responseCode}): ${label(f)}")
                        continue
                    }
                    c.inputStream.use { input -> staged.outputStream().use { input.copyTo(it) } }
                    if (sha256(staged) != good) {
                        staged.delete()
                        say("  the good copy did not arrive intact: ${label(f)}")
                        continue
                    }
                    val uri = mediaUri(this, f.path)
                    if (uri == null) {
                        staged.delete()
                        say("  not in the phone's media store, so leave to replace it cannot be asked: ${label(f)}")
                        continue
                    }
                    got += Triple(i, uri, staged)
                } catch (e: Exception) {
                    staged.delete()
                    say("  not fetched (${e.message ?: e}): ${label(f)}")
                }
            }
            if (got.isEmpty()) {
                main.post { repairButton.isEnabled = true }
                return@Thread
            }
            ready = got
            val request = MediaStore.createWriteRequest(contentResolver, got.map { it.second })
            main.post {
                say("Fetched and checked ${got.size} good copy(ies). Asking leave to write them over the damaged files…")
                @Suppress("DEPRECATION")
                startIntentSenderForResult(request.intentSender, REQ_WRITE, null, 0, 0, 0)
            }
        }, "lempi-repair-fetch").start()
    }

    /** With leave: write each good copy over its damaged file, read the file
     *  back, and tell Vipunen what its bytes now hash to -- only the good
     *  copy's hash closes the repair there. */
    private fun writeGood(allowed: Boolean) {
        val items = ready
        ready = emptyList()
        if (!allowed) {
            items.forEach { it.third.delete() }
            say("Not allowed, so the damaged files are left as they were.")
            main.post { repairButton.isEnabled = true }
            return
        }
        val base = base() ?: return
        Thread({
            var done = 0
            for ((i, uri, staged) in items) {
                val f = files[i]
                val r = repairs[i] ?: continue
                try {
                    contentResolver.openOutputStream(uri, "wt")?.use { out -> staged.inputStream().use { it.copyTo(out) } }
                        ?: throw IllegalStateException("could not open it for writing")
                    val now = sha256(File(f.path))
                    val c = URL("$base/repaired/${f.sha256}").openConnection() as HttpURLConnection
                    c.requestMethod = "POST"
                    c.connectTimeout = 8000
                    c.readTimeout = 30000
                    c.doOutput = true
                    c.setRequestProperty("Content-Type", "application/json")
                    c.setRequestProperty("X-Lempi-Key", theKey())
                    c.setRequestProperty("X-Lempi-Sender", "Lempi on ${Build.MODEL}")
                    c.outputStream.use { it.write(JSONObject().put("sha256", now).toString().toByteArray()) }
                    if (now == r.getString("good_sha256") && c.responseCode == 200) {
                        done++
                        say("  repaired: ${label(f)}")
                    } else {
                        say("  written, but its bytes are not the good copy's (Vipunen answered ${c.responseCode}): ${label(f)}")
                    }
                } catch (e: Exception) {
                    say("  NOT repaired (${e.message ?: e}): ${label(f)}")
                } finally {
                    staged.delete()
                }
            }
            say("\nRepaired $done of ${items.size}. Lempi reads them again when the player next starts, " +
                "and Vipunen's next bundle names them.")
            main.post { repairButton.visibility = View.GONE }
        }, "lempi-repair").start()
    }

    // ---- the folder route [REQ-AND-285] ----------------------------------

    private fun pickFolder() {
        if (chosen().isEmpty()) return say("Choose at least one file.")
        @Suppress("DEPRECATION")
        startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE), REQ_FOLDER)
    }

    @Deprecated("Activity's own result handling; this app has no AndroidX")
    override fun onActivityResult(code: Int, result: Int, data: Intent?) {
        if (code == REQ_WRITE) return writeGood(result == RESULT_OK)
        val tree = data?.data
        if (code != REQ_FOLDER || result != RESULT_OK || tree == null) return
        Thread({ toFolder(tree) }, "lempi-folder").start()
    }

    /** The offer, and the chosen files named by their byte hash, in a new
     *  folder inside the one chosen. */
    private fun toFolder(tree: Uri) {
        try {
            val which = chosen()
            val parent = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
            val stamp = SimpleDateFormat("yyyyMMdd-HHmm", Locale.ROOT).format(Date())
            val dir = DocumentsContract.createDocument(contentResolver, parent,
                DocumentsContract.Document.MIME_TYPE_DIR, "lempi-offer-$stamp")
                ?: throw IllegalStateException("could not make a folder there")
            fun write(name: String, mime: String, body: (java.io.OutputStream) -> Unit) {
                val doc = DocumentsContract.createDocument(contentResolver, dir, mime, name)
                    ?: throw IllegalStateException("could not create $name")
                contentResolver.openOutputStream(doc)?.use(body) ?: throw IllegalStateException("could not write $name")
            }
            write("offer.json", "application/json") { it.write(description(which).toString(2).toByteArray()) }
            for (i in which) {
                val f = files[i]
                val ext = File(f.path).extension.ifEmpty { "bin" }
                write("${f.sha256}.$ext", "application/octet-stream") { out -> File(f.path).inputStream().use { it.copyTo(out) } }
                say("  written: ${label(f)}")
            }
            say("\nWritten to lempi-offer-$stamp: the offer and ${which.size} file(s). Take the folder to Vipunen and run\n" +
                "  python tools/intake.py data/library.db --from-folder <that folder>")
        } catch (e: Exception) {
            say("\nNot written: ${e.message ?: e}")
        }
    }

    companion object {
        /** Lempi's own key [REQ-AND-287]: `intake.APP_KEY` in tools/intake.py. */
        const val APP_KEY = "lempi-intake-7f3c2a9e5d1b4c86"
        const val PORT = 5731
        private const val REQ_FOLDER = 1
        private const val REQ_WRITE = 2
    }
}
