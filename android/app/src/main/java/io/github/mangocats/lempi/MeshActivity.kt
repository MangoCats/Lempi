package io.github.mangocats.lempi

import android.app.Activity
import android.app.AlertDialog
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.security.keystore.KeyProperties
import android.text.InputType
import android.util.Base64
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.math.BigInteger
import java.security.KeyFactory
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.X509EncodedKeySpec

/**
 * This phone's membership of a mesh [SPEC049]: joining one, and what it is once
 * joined.
 *
 * Joining is Bluetooth's numeric comparison [SPEC-MTR-130]. The phone asks
 * the hub, the hub commits to a nonce, the phone sends its own signed with its
 * key, the hub reveals its nonce, and both show a six-digit code made from
 * both keys and both nonces. A person compares the two screens and confirms
 * on each. Only then does the hub add the phone to the roster it signs.
 *
 * Once a member, the phone holds that roster and the hub's key. It pins the
 * hub by that key, verifies every roster it is given against the mesh's key,
 * and never accepts an older one.
 */
class MeshActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var col: LinearLayout
    private lateinit var log: TextView
    private val prefs by lazy { getSharedPreferences("mesh", MODE_PRIVATE) }

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        title = "Mesh membership"
        col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 32, 32, 32) }
        log = TextView(this).apply { textSize = 14f; setPadding(0, 24, 0, 0) }
        setContentView(ScrollView(this).apply { addView(col) })
        draw()
    }

    private fun say(line: String) {
        main.post { log.append(line + "\n") }
    }

    private fun text(s: String, size: Float = 15f) = TextView(this).apply { text = s; textSize = size; setPadding(0, 8, 0, 8) }

    private fun draw() {
        col.removeAllViews()
        val fp = try {
            NodeIdentity.fingerprint()
        } catch (e: Exception) {
            col.addView(text("This phone could not make its key: $e"))
            return
        }
        col.addView(text("This phone", 18f))
        val name = EditText(this).apply {
            hint = "a name for this phone (optional)"
            setText(prefs.getString("name", Build.MODEL))
            inputType = InputType.TYPE_CLASS_TEXT
        }
        col.addView(name)
        col.addView(text("fingerprint  ${NodeIdentity.show(fp)}", 13f))
        if (prefs.getString("roster", null) == null) drawJoin(name) else drawMember(name)
        col.addView(log)
    }

    // ---- joining ----------------------------------------------------------

    private fun drawJoin(name: EditText) {
        col.addView(text("\nNot in a mesh. To join, name the hub — the computer running Vipunen's intake — " +
            "and open its console's mesh page to confirm.", 14f))
        val hub = EditText(this).apply {
            hint = "the hub's address, e.g. 192.168.67.95"
            setText(prefs.getString("hub", getSharedPreferences("intake", MODE_PRIVATE).getString("node", "")))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        col.addView(hub)
        col.addView(Button(this).apply {
            text = "Ask to join"
            setOnClickListener {
                isEnabled = false
                val nm = name.text.toString().trim()
                prefs.edit().putString("name", nm).apply()
                Thread({ join(hub.text.toString().trim().removePrefix("https://").trimEnd('/'), nm) }, "lempi-join").start()
            }
        })
    }

    private fun base(host: String) = "https://" + (if (host.contains(':')) host else "$host:$MEMBER_PORT")

    private fun join(host: String, name: String) {
        if (host.isEmpty()) return say("Name the hub first.")
        try {
            val client = MeshClient(base(host), pinHub = null, present = false)
            val me = NodeIdentity.fingerprint()
            val (s1, start) = client.call("POST", "/enrol/start", JSONObject()
                .put("certificate", NodeIdentity.pem()).put("name", name).put("role", "phone"))
            if (s1 != 200) return say("The hub refused: ${start.optString("error", "status $s1")}")
            val hubFp = client.seenHub ?: return say("The hub showed no key.")
            if (hubFp != start.getString("hub_fingerprint")) return say("The hub's key and its answer disagree; not joining.")
            val sid = start.getString("session")
            val commit = start.getString("commit")
            val nonce = NodeIdentity.hex(ByteArray(16).also { SecureRandom().nextBytes(it) })
            val (s2, n) = client.call("POST", "/enrol/nonce", JSONObject().put("session", sid).put("nonce", nonce)
                .put("signature", NodeIdentity.sign("lempi-enrol|$sid|$commit|$nonce")))
            if (s2 != 200) return say("The hub refused: ${n.optString("error", "status $s2")}")
            val nonceH = n.getString("nonce")
            if (NodeIdentity.hex(NodeIdentity.sha256(nonceH.toByteArray())) != commit) {
                return say("The hub's nonce does not match what it committed to; not joining.")
            }
            val code = sasCode(hubFp, me, nonceH, nonce)
            main.post { compare(host, client, sid, code, start, hubFp) }
        } catch (e: Exception) {
            say("Could not reach the hub's members' port: ${e.message ?: e}. Is tools/intake.py running there, with a mesh made?")
        }
    }

    /** The same six digits the hub derives in `mesh.code()`. */
    private fun sasCode(hubFp: String, me: String, nonceH: String, nonceC: String): String {
        val d = NodeIdentity.sha256("lempi-sas|$hubFp|$me|$nonceH|$nonceC".toByteArray())
        val n = BigInteger(1, d.copyOf(8)).mod(BigInteger.valueOf(1_000_000)).toInt()
        return "%03d %03d".format(n / 1000, n % 1000)
    }

    private fun compare(host: String, client: MeshClient, sid: String, code: String, start: JSONObject, hubFp: String) {
        col.removeAllViews()
        col.addView(text("Joining “${start.getString("mesh")}”", 18f))
        col.addView(text("Does the hub's mesh page show this code?", 15f))
        col.addView(text(code, 44f))
        col.addView(text("hub ${NodeIdentity.show(hubFp, short = true)} · mesh ${NodeIdentity.show(start.getString("mesh_fingerprint"), short = true)}", 13f))
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(Button(this).apply {
            text = "The codes match"
            setOnClickListener {
                isEnabled = false
                Thread({ confirm(host, client, sid, code, start, hubFp) }, "lempi-confirm").start()
            }
        })
        row.addView(Button(this).apply {
            text = "They differ"
            setOnClickListener {
                say("Not joined. Codes that differ mean the phone and the hub did not see each other's keys: " +
                    "something between them may be answering for one of them. Nothing was sent.")
                main.postDelayed({ draw() }, 4000)
            }
        })
        col.addView(row)
        col.addView(log)
    }

    private fun confirm(host: String, client: MeshClient, sid: String, code: String, start: JSONObject, hubFp: String) {
        try {
            val (s, c) = client.call("POST", "/enrol/confirm", JSONObject().put("session", sid)
                .put("signature", NodeIdentity.sign("lempi-confirm|$sid|$code")))
            if (s != 200) return say("The hub refused: ${c.optString("error", "status $s")}")
            say("Confirmed here. Now accept it on the hub's mesh page…")
            val until = System.currentTimeMillis() + 15 * 60_000L
            while (System.currentTimeMillis() < until) {
                val (_, st) = client.call("GET", "/enrol/status/$sid")
                when (st.optString("state")) {
                    "accepted" -> {
                        val signed = st.getJSONObject("roster")
                        val r = verifyRoster(signed, start.getString("mesh_fingerprint"), 0)
                            ?: return say("The roster the hub sent does not verify; not joined.")
                        prefs.edit().putString("hub", host).putString("hub_fp", hubFp)
                            .putString("mesh_fp", start.getString("mesh_fingerprint"))
                            .putString("roster", signed.toString()).putInt("version", r.getInt("version")).apply()
                        say("Joined “${r.getJSONObject("mesh").getString("name")}”.")
                        main.post { draw() }
                        return
                    }
                    "rejected" -> return say("The hub rejected it.")
                    "expired" -> return say("It took too long, and the hub let it go. Ask again.")
                }
                Thread.sleep(2000)
            }
            say("No answer from the hub in 15 minutes.")
        } catch (e: Exception) {
            say("Lost the hub: ${e.message ?: e}")
        }
    }

    /**
     * A signed roster, checked: signed by the mesh key whose fingerprint was
     * pinned at enrolment, and not older than `atLeast`. Returns its contents.
     */
    private fun verifyRoster(signed: JSONObject, meshFp: String, atLeast: Int): JSONObject? {
        val body = signed.getString("roster")
        val r = JSONObject(body)
        val pem = r.getJSONObject("mesh").getString("public_key")
        val der = Base64.decode(pem.lines().filter { !it.startsWith("-----") }.joinToString(""), Base64.DEFAULT)
        if (NodeIdentity.hex(NodeIdentity.sha256(der)) != meshFp) return null
        val key = KeyFactory.getInstance(KeyProperties.KEY_ALGORITHM_EC).generatePublic(X509EncodedKeySpec(der))
        val ok = Signature.getInstance("SHA256withECDSA").run {
            initVerify(key)
            update(body.toByteArray())
            verify(Base64.decode(signed.getString("signature"), Base64.DEFAULT))
        }
        return if (ok && r.getInt("version") >= atLeast) r else null
    }

    // ---- a member ---------------------------------------------------------

    private fun drawMember(name: EditText) {
        val r = JSONObject(JSONObject(prefs.getString("roster", "{}")!!).getString("roster"))
        val mesh = r.getJSONObject("mesh")
        col.addView(text("\nA member of “${mesh.getString("name")}”", 18f))
        col.addView(text("mesh ${NodeIdentity.show(prefs.getString("mesh_fp", "")!!)}\n" +
            "hub at ${prefs.getString("hub", "")}, ${NodeIdentity.show(prefs.getString("hub_fp", "")!!, short = true)} · " +
            "roster version ${r.getInt("version")}", 13f))
        val all = CheckBox(this).apply { text = "Show fingerprints" }
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fun fill() {
            list.removeAllViews()
            val ms = r.getJSONArray("members")
            val members = (0 until ms.length()).map { ms.getJSONObject(it) }
            val names = members.map { it.optString("name").trim() }
            members.forEachIndexed { i, m ->
                val fp = m.getString("fingerprint")
                val short = NodeIdentity.show(fp, short = true)
                val shown = when {
                    names[i].isEmpty() -> short
                    all.isChecked || names.count { it == names[i] } > 1 -> "${names[i]} ($short)"
                    else -> names[i]
                }
                val me = if (fp == NodeIdentity.fingerprint()) "  ← this phone" else ""
                list.addView(text("• $shown — ${m.getString("role")}$me", 14f))
            }
        }
        all.setOnCheckedChangeListener { _, _ -> fill() }
        fill()
        col.addView(all)
        col.addView(list)
        col.addView(Button(this).apply {
            text = "Sync my edits with the mesh"
            setOnClickListener { Thread({ syncEdits() }, "lempi-mesh-sync").start() }
        })
        col.addView(text("Takes what the household decided at the last sync, then sends this phone's " +
            "preferences, occasion values and flags — never its plays — for the next one.", 13f))
        col.addView(Button(this).apply {
            text = "Check the channel"
            setOnClickListener { Thread({ check() }, "lempi-mesh-check").start() }
        })
        col.addView(Button(this).apply {
            text = "Leave the mesh"
            setOnClickListener {
                AlertDialog.Builder(this@MeshActivity)
                    .setMessage("Forget this mesh on the phone? The hub still lists the phone until someone removes it there.")
                    .setPositiveButton("Leave") { _, _ ->
                        prefs.edit().remove("roster").remove("hub_fp").remove("mesh_fp").remove("version").apply()
                        draw()
                    }
                    .setNegativeButton("Cancel", null).show()
            }
        })
        name.isEnabled = false   // the name the hub holds is the one given at enrolment
    }

    /**
     * [REQ-AND-330]: the household's edits down, then this phone's up.
     *
     * Down first: what the last star sync decided waits in this phone's
     * outbox at the hub, and is applied by the three-way rule -- a row changed
     * here since the upload it was merged from is kept. Then up: the shared
     * tables as they now stand, for the next sync, with this phone's clock so
     * the hub can refuse edits it could not order.
     */
    private fun syncEdits() {
        try {
            val listener = java.io.File(filesDir, "listener.db").path
            val client = MeshClient(base(prefs.getString("hub", "")!!), pinHub = prefs.getString("hub_fp", null), present = true)
            val (s, o) = client.call("GET", "/member/updates")
            if (s != 200) return say("The hub answered $s: ${o.optString("error")}")
            if (o.isNull("run")) {
                say("Nothing waiting from the household.")
            } else {
                val run = o.getString("run")
                val a = io.github.mangocats.lempi.ffi.meshApply(listener, o.getJSONObject("patch").toString())
                client.call("POST", "/member/updates/applied", JSONObject().put("run", run)
                    .put("applied", a.applied.toLong()).put("already", a.already.toLong()).put("kept", a.kept.toLong()))
                say("From the household's sync of $run: ${a.applied} change(s) taken, ${a.already} already here" +
                    (if (a.kept > 0u) ", ${a.kept} changed here since and kept — they go up now" else "") + ".")
            }
            val out = java.io.File(cacheDir, "mesh-upload.db")
            val snap = io.github.mangocats.lempi.ffi.meshSnapshot(listener, out.path)
            val (s2, up) = client.send("POST", "/member/listener", out.readBytes(), "application/octet-stream",
                mapOf("X-Lempi-Clock" to System.currentTimeMillis().toString()))
            out.delete()
            if (s2 != 200) return say("Not sent: ${up.optString("error", "the hub answered $s2")}")
            say("Sent ${snap.rows} edit(s) for the next sync; this phone's clock is ${up.getLong("clock_offset_ms")} ms " +
                "from the hub's." + (if (snap.heldBack > 0u) " ${snap.heldBack} about single passages stay on the phone." else ""))
        } catch (e: Exception) {
            say("The sync failed: ${e.message ?: e}")
        }
    }

    /** [SPEC-MTR-200] in use: a request only a member can make, to the hub
     *  pinned by its key, and the latest roster, checked. */
    private fun check() {
        try {
            val hub = prefs.getString("hub", "")!!
            val client = MeshClient(base(hub), pinHub = prefs.getString("hub_fp", null), present = true)
            val (s, hello) = client.call("GET", "/member/hello")
            if (s != 200) return say("The hub answered $s: ${hello.optString("error")}")
            say("Verified: the hub is the one this phone enrolled with, and it knows this phone as " +
                "“${hello.optString("name").ifEmpty { NodeIdentity.show(NodeIdentity.fingerprint(), true) }}”, " +
                "a ${hello.getString("role")}. Roster version ${hello.getInt("roster_version")}.")
            val (s2, signed) = client.call("GET", "/member/roster")
            if (s2 != 200) return say("Could not fetch the roster: $s2")
            val r = verifyRoster(signed, prefs.getString("mesh_fp", "")!!, prefs.getInt("version", 0))
                ?: return say("The hub's roster does not verify, or is older than the one held; kept the one held.")
            prefs.edit().putString("roster", signed.toString()).putInt("version", r.getInt("version")).apply()
            main.post { draw() }
        } catch (e: javax.net.ssl.SSLException) {
            // The hub refuses a key its roster no longer holds during the handshake,
            // so a removed member sees a TLS alert, not an answer.
            val m = e.message ?: ""
            say(when {
                "not the hub this phone enrolled with" in m ->
                    "Something answered at the hub's address with a different key, so the phone did not talk to it. " +
                        "Either the hub was set up again, or something else is answering in its place."
                "UNKNOWN_CA" in m || "CERTIFICATE_VERIFY" in m ->
                    "The hub refused this phone's key: it is no longer a member of this mesh. " +
                        "It can be enrolled again from the hub's mesh page."
                else -> "The secure connection failed: $m"
            })
        } catch (e: Exception) {
            say("The channel failed: ${e.message ?: e}")
        }
    }

    companion object {
        const val MEMBER_PORT = 5732
    }
}
