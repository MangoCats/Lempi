package io.github.mangocats.lempi

import java.io.File
import java.io.InputStream
import java.util.zip.ZipInputStream

/**
 * A bundle's `.zip` into a staging folder [SPEC-PL-095]: `payload.json` (or its
 * parts), `audio/...` and `covers/...` only, and nothing that could leave the
 * folder. Shared by the Import screen and the mesh screen's catalogue update.
 * `before` is told each entry's name and size before it is written, and may
 * throw to stop -- the Import screen's check that the room is there.
 */
fun unzipBundle(input: InputStream, staging: File, before: (String, Long) -> Unit = { _, _ -> }): Int {
    var files = 0
    ZipInputStream(input.buffered()).use { z ->
        while (true) {
            val e = z.nextEntry ?: break
            val name = e.name
            if (e.isDirectory || !bundleEntryWanted(name)) continue
            before(name, maxOf(e.size, 0L))
            val out = File(staging, name)
            out.parentFile?.mkdirs()
            out.outputStream().use { z.copyTo(it) }
            files++
        }
    }
    return files
}

/** Only what a bundle holds, and nothing that could leave the staging folder. */
fun bundleEntryWanted(name: String): Boolean {
    if (name.startsWith("/") || name.contains('\\')) return false
    if (name.split('/').any { it == ".." || it.isEmpty() }) return false
    // A payload in parts [SPEC-PL-095]: payload-001.json, payload-002.json, ...
    val part = Regex("payload-[0-9]+\\.json")
    return name == "payload.json" || part.matches(name) || name.startsWith("audio/") || name.startsWith("covers/")
}
