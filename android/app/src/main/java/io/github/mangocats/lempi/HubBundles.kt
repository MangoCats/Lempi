package io.github.mangocats.lempi

import android.content.Context
import android.os.Environment
import android.os.StatFs
import org.json.JSONObject
import java.io.File

/**
 * Music the hub's Export page left for this phone [REQ-AND-235], and what the
 * phone has free to take it.
 *
 * The hub judges a bundle by what its import needs at its peak -- the staged
 * bundle and the copy being placed, about twice the unpacked size -- against
 * what this phone last said it has free. The phone judges again before it
 * fetches, against what is free then, with the same two numbers as
 * `tools/member_bundles.py`: above [CONFIRM_AT] a person must say yes, above
 * [REFUSE_AT] it is not fetched.
 */
object HubBundles {
    const val CONFIRM_AT = 0.80
    const val REFUSE_AT = 0.99

    /** Free space for an import: the smaller of where it is staged and where
     *  its audio is placed. On a phone without a card they are one partition. */
    fun free(ctx: Context): Long {
        var music: File? = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MUSIC), "Lempi")
        while (music != null && !music.exists()) music = music.parentFile
        val placed = music?.let { StatFs(it.path).availableBytes } ?: Long.MAX_VALUE
        return minOf(StatFs(ctx.cacheDir.path).availableBytes, placed)
    }

    /** `ok`, `confirm` or `refuse`, as the hub reckons it. */
    fun verdict(peak: Long, free: Long): String {
        if (free <= 0) return "refuse"
        val r = peak.toDouble() / free
        return if (r > REFUSE_AT) "refuse" else if (r > CONFIRM_AT) "confirm" else "ok"
    }

    /** Tell the hub what is free, for the Export page's check. Quietly
     *  nothing when not a member or the hub is out of reach: the Export page
     *  shows when it last heard, and treats never as needing a yes. */
    fun reportStorage(ctx: Context) {
        val client = MeshClient.forMember(ctx) ?: return
        try {
            client.call("POST", "/member/storage", JSONObject().put("available_bytes", free(ctx)))
        } catch (e: Exception) {
            android.util.Log.i("lempi", "could not tell the hub what is free: ${e.message ?: e}")
        }
    }

    /** The bundle waiting for this phone, or `null` if none. Network: never on
     *  the main thread. */
    fun offer(ctx: Context): JSONObject? {
        val client = MeshClient.forMember(ctx) ?: return null
        val (s, o) = client.call("GET", "/member/bundle")
        return if (s == 200 && o.optString("state") == "ready") o else null
    }

    fun mb(b: Long) = (b + 999_999) / 1_000_000
}
