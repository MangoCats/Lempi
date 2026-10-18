package io.github.mangocats.lempi

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * The programme the hub assigned to this phone [SPEC-PGM-300], fetched over
 * the members' channel and handed to the player, which holds it until a
 * person answers [SPEC-PGM-050]. Never applied from here.
 *
 * Asked when the player has started and when the Mesh screen opens
 * [SPEC-PGM-310]: the phone asks, the hub never reaches in. After it, the
 * phone tells the hub what it runs and what waits, for the console's view of
 * each node [SPEC-PGM-440].
 */
object Programmes {
    /**
     * One exchange. A sentence saying what happened, or `null` when this
     * phone is not a member of a mesh. Network: never on the main thread.
     */
    fun exchange(ctx: Context): String? {
        val client = MeshClient.forMember(ctx) ?: return null
        return try {
            val (s, out) = client.call("GET", "/member/programme")
            if (s != 200) return "The hub answered $s when asked for its programme: ${out.optString("error")}"
            val p = out.optJSONObject("programme")
            val said = if (p == null) {
                "The hub has assigned this phone no programme."
            } else {
                val name = p.getString("name")
                val version = p.getInt("version")
                val (ps, pr) = player("POST", "/programme/arrival", JSONObject()
                    .put("name", name).put("version", version).put("slots", p.getJSONArray("slots")))
                when (pr.optString("outcome")) {
                    "asking" -> "The household sent $name (version $version): the player asks whether to replace this phone's programme."
                    "already_runs" -> "This phone runs the household's $name, version $version."
                    "kept_before" -> "This phone keeps its own programme over $name version $version, as answered before."
                    else -> "The player did not take the household's $name: $ps ${pr.optString("error")}"
                }
            }
            val (gs, now) = player("GET", "/programme/pending")
            if (gs == 200) {
                val c = now.getJSONObject("current")
                val w = now.optJSONObject("pending")
                client.call("POST", "/member/programme/state", JSONObject()
                    .put("name", c.opt("name"))
                    .put("fingerprint", c.getString("fingerprint"))
                    .put("source", c.getString("source"))
                    .put("hub_version", c.opt("hub_version"))
                    .put("waiting", w?.let {
                        JSONObject().put("name", it.getString("name")).put("hub_version", it.getInt("hub_version"))
                            .put("fingerprint", it.getString("fingerprint")).put("kept", it.getBoolean("kept"))
                    } ?: JSONObject.NULL))
            }
            said
        } catch (e: Exception) {
            "Could not ask the hub for its programme: ${e.message ?: e}"
        }
    }

    /** A request to this phone's own player, with its launch key [REQ-AND-160]. */
    private fun player(method: String, path: String, body: JSONObject? = null): Pair<Int, JSONObject> {
        val k = Lempi.key
        if (Lempi.status != "running" || k == null) return 503 to JSONObject().put("error", "the player is not running")
        val c = URL("http://127.0.0.1:${Lempi.PORT}$path").openConnection() as HttpURLConnection
        c.requestMethod = method
        c.setRequestProperty("x-lempi-key", k)
        c.connectTimeout = 5_000
        c.readTimeout = 20_000
        if (body != null) {
            val bytes = body.toString().toByteArray()
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.setFixedLengthStreamingMode(bytes.size)
            c.outputStream.use { it.write(bytes) }
        }
        val code = c.responseCode
        val text = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.readText() ?: ""
        c.disconnect()
        val json = try { JSONObject(text.ifBlank { "{}" }) } catch (e: Exception) { JSONObject().put("error", text) }
        return code to json
    }
}
