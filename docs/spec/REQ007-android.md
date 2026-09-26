# REQ007: Android

**Requirements Specification — Tier 1, draft**

What Lempi on an Android phone must do, written 2026-09-25 from the decisions in [GUIDE034](../GUIDE034-android-decisions-before-requirements.md), all answered by the maintainer that day. **Draft until the device spike `[GDE-APP-020]` has run**: several requirements below state a behaviour whose feasibility that spike measures, and `[REQ-AND-530]` makes it the gate on this document leaving draft.

The README ranks mobile *Future / Post-V1*. Nothing here schedules the work; it makes the work specifiable.

> **Related:** [REQ002](REQ002-functional-requirements.md) (the index; PORT, HW) · [REQ003](REQ003-audio-playback.md) (AUD) · [REQ005](REQ005-visibility-listening-surface.md) (the listening surface) · [GUIDE004](../GUIDE004-phone-port-strategy.md) · [GUIDE033](../GUIDE033-the-player-without-an-appliance.md) · [GUIDE034](../GUIDE034-android-decisions-before-requirements.md)

---

## 1. What applies unchanged, and what does not

**`[REQ-AND-010]` Every requirement in REQ002–REQ006 applies on Android unless this document names an exception.** Selection `[REQ-PD-*]`, playback, fades and crossfade `[REQ-AUD-*]`, and the listening surface `[REQ-VIS-*]` are the same requirements on every host; they are cited here, never restated, so they cannot drift into a phone variant `[GDE-FBD-040]`.

**`[REQ-AND-020]` The appliance requirements do not apply**: `[REQ-HW-100]`'s 150 MB, `[REQ-HW-110]`, `[REQ-HW-120]` and `[REQ-HW-150]` describe a Pi. The phone's memory and battery budgets are measured by the spike, and become requirements then — see §7.

**`[REQ-AND-030]` Two existing requirements are refined for the phone**, and each now points here:

- Imported metadata is verified by recomputing `audio_md5` `[REQ-PORT-130]`. The phone cannot — that hash needs ffmpeg `[SPEC-RLK-080]` — so it verifies by the byte hash carried in the payload `[REQ-AND-230]`.
- Backups exist because listener state is irreplaceable `[REQ-PORT-150]`; on a phone, app-private storage is deleted with the app, so a backup must also be retrievable off it `[REQ-AND-190]`.

## 2. The player and the platform — `AND`

**`[REQ-AND-100]`** Run on **Android 11 (API 30) and later**, targeting the API level the Play Store currently requires `[GDE-APP-040]`.

**`[REQ-AND-110]` The audio is played by Lempi's own player** — the same decoder, mixer, fades and engine as every other host, through AAudio — not re-implemented on Media3 or any other platform player `[GDE-APP-010]`.

**`[REQ-AND-120]` Playback continues with the screen off and the app in the background**, hosted by a foreground service of type `mediaPlayback`, with a notification carrying the transport controls `[GDE-APP-100]`.

**`[REQ-AND-130]` The phone's own controls reach the player**: lock screen, notification, wired and Bluetooth headset buttons, and Android Auto — through a media session that forwards to the player's commands and never handles audio itself `[GDE-APP-100]`.

**`[REQ-AND-140]` Audio focus is honoured.** Losing focus pauses; losing it briefly pauses, or ducks where a device shows ducking is needed `[GDE-HST-340]`; regaining it resumes **only if focus was what paused it** — a listener's own pause is never undone by the system `[GDE-APP-100]`.

**`[REQ-AND-150]` The listening surface is the existing skins in a WebView**, served by the player's own web server `[GDE-AND-060]`. A native client is evaluated afterwards and is not required here.

**`[REQ-AND-160]` That server is reachable only from the app.** It binds to the loopback address alone, and every HTTP and WebSocket request must carry a secret made anew at each launch; a request without it is refused `[GDE-APP-090]`.

**`[REQ-AND-170]` Controls the phone cannot honour are not shown** — power, Wi-Fi, speakers, LED, radios and following — declared absent in the snapshot's capabilities, as on any host that lacks them `[GDE-HST-360]`.

**`[REQ-AND-180]` The phone neither leads nor follows an echo fleet** in the first release `[GDE-APP-120]`: echo rests on a clock disciplined to one LAN reference `[GDE-ECHO-300]`, which a phone cannot promise.

**`[REQ-AND-190]` The phone keeps its own listening history and preferences**, independent of the appliances `[GDE-APP-130]`, backed up as `[REQ-PORT-150]` requires — **and a backup can be taken off the phone by the user**, since uninstalling deletes app-private storage.

**`[REQ-AND-195]` A backup leaves the phone by export on request** — decided by the maintainer 2026-09-25 `[REQ-AND-900]`. The user asks, chooses where it goes through Android's own document picker, and the newest integrity-checked backup `[REQ-PORT-150]` is written there. Nothing leaves the phone unasked, and Android's automatic cloud backup is not relied on.

## 3. The library and its storage

**`[REQ-AND-200]` Lempi's own files are private**: its databases, caches, lyrics cache and backups live in app-private storage. Lyrics are never written beside the audio `[GDE-APP-080]`. *Built 2026-09-25:* the host's `writes_beside_audio` switch, false on a phone, refuses the sidecar's route, hides its setting through the snapshot's capabilities, and never runs the generation. Covers and cue sheets also write into the music folder — `[REQ-AND-950]`.

**`[REQ-AND-210]` The music is shared.** Audio lives in shared storage, visible to and playable by other apps; what Lempi imports lands in `Music/Lempi/` `[GDE-APP-070]`.

**`[REQ-AND-220]` Access is the narrowest that works**: MediaStore to find and read audio and images, one folder grant on `Music/` for the `.cue`, `.lrc` and cover files beside them, and never the all-files permission `[GDE-APP-080]`.

**`[REQ-AND-230]` A bundle is imported from the share or open sheet, and every file is verified before it is trusted** — by the SHA-256 of its bytes that Vipunen carried in the payload. A file that does not match is reported, never silently accepted `[GDE-APP-060]`. *Built 2026-09-25* as `[SPEC-PL-087]`: `export_bundle.py` writes the hash; `lempi-core`'s importer checks it, and one with neither that hash nor ffmpeg is reported unverifiable and not written.

**`[REQ-AND-240]` Importing never duplicates.** An imported file byte-identical to one already on the phone is bound to that file, not copied beside it `[GDE-APP-070]`.

**`[REQ-AND-250]` A file Vipunen has rewritten replaces the phone's copy.** When a bundle brings a file whose tags Vipunen wrote back, so its bytes changed, the phone's earlier copy is replaced, not kept beside the new one `[GDE-APP-050]`.

**`[REQ-AND-260]` The phone finds music in shared storage.** A found file whose byte hash matches a catalogue entry gets everything the catalogue holds. Any other becomes a **tags-only passage**: playable, selectable, without flavor or MBID, and marked as tags-only wherever it is shown `[GDE-APP-050]`. How the Director treats it is `[REQ-AND-310]`.

**`[REQ-AND-270]` The phone derives nothing.** It computes no audio-identity hash, no flavor and no segmentation; the byte hash is its only fingerprint `[GDE-APP-060]`, and a file differing from a catalogue entry only in its bytes is found as new `[GDE-AND-050]`.

**`[REQ-AND-280]` New music can be sent out for induction.** The user can list the found files the catalogue does not hold and export them — the files, their tags and byte hashes, and no listener state `[REQ-PORT-120]` — for a Vipunen node to induct. After the next bundle they are catalogue entries on the phone `[GDE-APP-050]`.

**`[REQ-AND-285]` Exported music travels over the local network to an active Vipunen node** — decided by the maintainer 2026-09-25 `[REQ-AND-930]`, "ideally". So the network is the intended route, and not the only one: where no node answers, the same export can be written to a folder the user moves by hand, so the feature never depends on the network being there. How a node is found, how the phone proves it may send, and where the node puts what arrives are specification, and open — §7.

## 4. Selection

**`[REQ-AND-300]` The Director rebuilds when it does on every host** — at start, and after an import — and never on a timer `[GDE-APP-110]`. A further rule, such as only while charging, requires the spike's measurement first.

**`[REQ-AND-310]` A tags-only passage is selected as the Director already selects an unidentified one** `[REQ-AND-940]`, read from `lempi-core` 2026-09-25: it is **eligible** — "no MBID means no history, not exclusion", pinned by the test `an_unidentified_passage_still_plays`; it **rotates on its own passage id** only `[GDE-WRK-055]`, since the recording, work and artist tiers are keyed by MBID; it **cannot appear twice in one queue**, which `choose` guarantees structurally; and flavor shaping **keeps it**, admitting it apart from the distance-gathered pool — "unmeasured is not unsuitable" — or falls back to frequency alone when nothing has flavor `[SPEC-DIR-158]`.

**`[REQ-AND-315]` *Proposed, for the maintainer:* a tags-only passage also rotates by its artist, keyed on the normalised artist tag.** Without it, a newly found album rotates track by track and nothing stops several of its tracks playing close together — the artist tier exists for exactly that and is keyed by MBID, which a found file lacks. The key would be kept distinct from MBIDs, so a tag name can never collide with or block an identified artist.

## 5. Building and shipping

**`[REQ-AND-400]` The app is an `android/` Gradle project in this repository**, with the Rust library built by `cargo-ndk` in the existing Android image, and every build reports the commit it was built from `[GDE-APP-140]`.

**`[REQ-AND-410]` Kotlin reaches Rust through a generated interface** (UniFFI) for starting, commanding, reading and stopping the player, and **one** hand-written JNI entry that hands over the Android context before start. Starting without that context is refused with a named error, not left to fail inside the audio stack `[GDE-APP-030]` `[GDE-HST-330]`.

**`[REQ-AND-420]` The first releases are a signed APK installed directly**; the Play Store follows when its policies are met on purpose `[GDE-APP-150]`.

## 6. Verification

**`[REQ-AND-500]` The player's Android compile check is a CI gate**, not a one-off `[GDE-APP-160]`. *Met 2026-09-25: the `android-core` CI job and `verify-targets.sh` stage E check the player library with warnings denied; proven to fail on a warning planted under `cfg(target_os = "android")`, which the host build does not compile.*

**`[REQ-AND-510]` The player library's tests run on an emulator or a device**, as an optional stage of `build/verify-targets.sh` that says when it did not run `[GDE-APP-160]`.

**`[REQ-AND-520]` Each release passes a device checklist, recorded as numbers**: 30 minutes screen-off with the underrun count, battery drain per hour, focus lost and regained, headset buttons, the notification, a bundle import, a scan, an export and a Director rebuild `[GDE-APP-160]`.

**`[REQ-AND-530]` The device spike passes before this document leaves draft** `[GDE-APP-020]`: a passage audible for 30 minutes screen-off with zero underruns, a measured drain, the skin controlling it in a WebView.

## 7. Open — to be settled before this leaves draft

1. **`[REQ-AND-900]` How a backup leaves the phone** `[REQ-AND-190]`. *Decided 2026-09-25: export on request* — `[REQ-AND-195]`.
2. **`[REQ-AND-910]` The memory and battery budgets**, as numbers, from the spike `[REQ-AND-020]`; `[GDE-AND-065]` measured a Director rebuild at about 16.5 CPU-seconds and +122.5 MB on a Pi 3 core.
3. **`[REQ-AND-920]` Whether a MediaStore path opens with `File::open`** on the device; if not, the audio-source resolver `[GDE-HST-080]` covers every path, not only the sidecars `[GDE-APP-080]`.
4. **`[REQ-AND-930]` How exported music reaches Vipunen** `[REQ-AND-280]`. *Decided 2026-09-25: over the local network to an active node, ideally* — `[REQ-AND-285]`. Still open beneath it: discovery, authorisation and where the node files what arrives.
5. **`[REQ-AND-940]` The Director's handling of a passage without flavor** `[REQ-AND-310]`. *Answered from the code 2026-09-25* — see `[REQ-AND-310]`; it raised `[REQ-AND-315]`, which awaits the maintainer.
6. **`[REQ-AND-950]` Whether a phone also refuses to write covers and cue sheets into `Music/`** `[REQ-AND-200]`. Both are Lempi's files in a shared folder, as the lyrics sidecar is; both help another player that reads them. The switch built for the sidecar extends to them by marking their settings `beside` in `web/settings.rs`.

---

**Traceability:** `[REQ-AND-010..950]` · every requirement cites the GUIDE034 decision it comes from · refines `[REQ-PORT-130]`, `[REQ-PORT-150]` · excludes `[REQ-HW-100]`, `[REQ-HW-110]`, `[REQ-HW-120]`, `[REQ-HW-150]`
