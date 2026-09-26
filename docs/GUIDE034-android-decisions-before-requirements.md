# GUIDE034: Android — the Decisions Before Requirements

**Development Guidance — the questions an Android app needs answered before its requirements can be written, each with a recommendation and its reason**

Written 2026-09-25, after the tree was tagged `pre-android-development`, in answer to the question whether the Android guidance was ready to support requirements, implementation and test. It was not: GUIDE004 and GUIDE033 disagreed on what plays the audio, one decision taken that day (`[GDE-AND-070]`) contradicted an older principle, and much of what an app needs was never asked. This puts each open matter as a question with a recommendation, the way GUIDE033 §6 did — *proposed, not decided*, until the maintainer answers.

**Answered 2026-09-25.** The maintainer accepted every recommendation except two: `[GDE-APP-050]` is decided as revised by the maintainer's clarification — a scan also finds music new to the fleet, which the phone can send out for induction — and `[GDE-APP-070]` was asked to weigh `Music/Lempi/` against `Music/`, which it now does — its recommendation confirmed the same day. Every question here is now decided; the requirements they produce are [REQ007](spec/REQ007-android.md).

> **Related:** [GUIDE004](GUIDE004-phone-port-strategy.md) (route and licence; `[GDE-AND-060]`, `[GDE-AND-065]`, `[GDE-AND-070]`) · [GUIDE033](GUIDE033-the-player-without-an-appliance.md) (the player made host-indifferent) · [SPEC011](spec/SPEC011-audio-path-supervisor.md) · [SPEC012](spec/SPEC012-library-relink.md)

---

## 1. Three contradictions, and which way each points

**`[GDE-APP-001]` What plays the audio — clear enough to recommend, not yet proven.** GUIDE004 recommends a ground-up app on Media3 with decode, gapless, focus and notification "provided by Media3" `[GDE-AND-020]` `[GDE-AND-040]`; GUIDE033 has the phone link `lempi-player` and play through cpal's AAudio backend `[GDE-HST-010]`. The second is the better path: it keeps the decoder, mixer and fades — the hardest, best-measured code here — as one implementation rather than two `[GDE-FBD-040]`; `[GDE-AND-060]`'s WebView needs the Rust player's web server; and the library now compiles for Android. What is unproven is linking it and running it in the background on a real device. See `[GDE-APP-010]` and `[GDE-APP-020]`.

**`[GDE-APP-002]` "Derives nothing" against "scans storage" — a product choice, not a mistake.** `[GDE-AND-025]` and `[GDE-AND-050]` say the phone consumes derived data and never derives any; `[GDE-AND-070]` says it scans shared storage and adds what it finds. Both hold if what the phone adds is *found*, not *derived*: see `[GDE-APP-050]`.

**`[GDE-APP-003]` Import needs a hash the phone cannot compute — clearer than it looks.** Relink's identity hash shells out to ffmpeg `[SPEC-RLK-080]`, which a phone lacks. But "did this file arrive intact" and "is this a file the catalogue knows" can both be answered by a hash of the file's *bytes*, computed by Vipunen and carried in the payload. See `[GDE-APP-060]`.

## 2. Architecture

**`[GDE-APP-010]` The audio path.** *Options:* the Rust player (cpal → AAudio) inside the app; or Media3/ExoPlayer, with Lempi's passage spans, gain and fades re-implemented in Kotlin. *Recommendation:* **the Rust player**, with Media3 used for the media session only (`[GDE-APP-100]`), and GUIDE004 `[GDE-AND-020]`/`[GDE-AND-040]` amended to say so. *Why:* one engine for every host, the WebView decision already assumes it, and passage playback, fades and crossfade already exist and are measured. *Gated on* `[GDE-APP-020]`: if the spike fails on something structural, this reopens.

**`[GDE-APP-020]` The device spike, before any requirement is frozen.** *Recommendation:* a throwaway app that links the player as a `cdylib`, initialises `ndk_context` `[GDE-HST-330]`, calls `Player::start` with the web server on localhost, and shows the lempi skin in a WebView. **It passes if**, on one real phone, for 30 minutes with the screen off: a passage plays audibly, the underrun count stays at zero, `adb shell dumpsys batterystats` gives a drain per hour, and the skin controls it. It also answers three measurements the rest need: a Director rebuild on a phone core (`[GDE-AND-065]`), whether `File::open` works on a MediaStore path (`[GDE-APP-080]`), and whether Android ducks the stream by itself (`[GDE-HST-340]`). *Why first:* every later question gets cheaper once this has run.

**`[GDE-APP-030]` The glue between Kotlin and Rust.** *Options:* hand-written JNI; UniFFI. *Recommendation:* **UniFFI for the API** — `start`, `command`, `state`, `shutdown` — which is also iOS's route `[GDE-IOS-025]`, **plus one small hand-written JNI entry** that receives the `JavaVM` and `Context` for `[GDE-HST-330]`, since UniFFI has no way to pass them. *Why:* the surface is small and generated bindings do not drift; the one thing they cannot carry is exactly one call.

**`[GDE-APP-040]` The minimum Android version.** The compile check targets API 26, AAudio's first, but nothing records why. *Options:* 26 (AAudio's floor); 30 (Android 11); 33. *Recommendation:* **minimum 30, target the current API Play requires.** *Why:* from API 30 shared storage has one model — scoped storage, with media files openable by ordinary path once the app holds the media permission — so the storage code has no legacy branch; below it there are two. The cost is phones older than Android 11; which share of them that is has not been measured here.

## 3. The library on the phone

**`[GDE-APP-050]` What a file found in shared storage becomes.** *Decided 2026-09-25, as revised by the maintainer:* a scan finds two kinds of file, and they go opposite ways.

- **Music the fleet already has.** Its byte hash `[GDE-APP-060]` matches a catalogue entry, and it gets everything the catalogue has.
- **Music new to the fleet.** It becomes a **tags-only passage** on the phone — playable and selectable by artist and recording, with no flavor, marked as such in the UI, the fallback `[GDE-IOS-050]` sketches — **and the user can send it out to a Vipunen node for induction**. The phone lists what is not in the catalogue; exporting hands those files, with their byte hashes and tags, to wherever the user moves them to a Vipunen host, where `induct` takes a folder as it does today. The next bundle brings them back as catalogue entries, matched by byte hash.

So the phone still never analyses — `[GDE-AND-050]` holds — but it gains an **outbound** route the guides had only ever drawn inbound. Two consequences for the specification. If Vipunen rewrites a file's tags (`tools/push_file_tags.py`), its bytes change, the phone's copy stops matching, and the returning bundle must **replace** that copy, not add a second. And a file already in the fleet under different bytes — another rip, another encoding — is not recognised by byte hash; it is found as new, and it is the desktop's audio-identity relink, after induction, that ties it to the recording. *To verify:* how the Director weights a passage with no flavor today; that is a requirement, not an assumption.

**`[GDE-APP-060]` Verifying and recognising files without ffmpeg.** *Options:* carry a byte hash in the payload; move the identity hash into Rust `[SPEC-RLK-150]`; ship ffmpeg on the phone. *Recommendation:* **a SHA-256 of each file's bytes, computed by Vipunen and carried in the payload** `[GDE-HST-080]`. The phone uses it to confirm an import arrived intact, and to recognise a scanned file as a catalogue entry. The audio-identity hash stays on the desktop. *Why:* the phone needs "same bytes", never "same audio"; a byte hash is cheap, needs no decoder, and a pure-Rust hash crate passes the core boundary gate. *Consequence:* a file retagged on the phone no longer matches, and falls to tags-only until the next bundle — said in the UI, not hidden.

**`[GDE-APP-070]` How a library reaches the phone.** *Accepted:* the user copies audio in by any means and opens the bundle through Android's share or open sheet `[GDE-AND-050]`; a LAN pull is later. *Confirmed 2026-09-25, below:* **where the audio lands.** The folder answers three separate questions, and only one of them depends on it:

| | `Music/Lempi/` | `Music/` |
| :--- | :--- | :--- |
| Discovery | MediaStore sees all shared storage either way | same |
| The folder grant for `.cue`/`.lrc`/covers | reaches only Lempi's own folder — **not** the sidecars of music the user already keeps elsewhere, which is what a scan finds | reaches every file under `Music/` |
| Where imports land | one folder Lempi owns: visibly its arrivals, removable or movable whole, never mixed with the user's own | mixed into the user's tree; which files Lempi manages is no longer visible |

*Recommendation:* **both, for different jobs — grant `Music/`, import into `Music/Lempi/`** — and do not copy an imported file whose byte hash matches one already on the phone: bind the catalogue entry to the existing file, so other players never see a duplicate album. *Why:* the grant has to be as wide as the music a scan can find, while the imports benefit from a boundary.

**`[GDE-APP-080]` The storage mechanics behind `[GDE-AND-070]`.** *Recommendation:* MediaStore to find audio and images, opened by path (API 30+); **one folder grant** (`ACTION_OPEN_DOCUMENT_TREE`) on `Music/Lempi/` for the `.cue`, `.lrc` and cover files beside the audio, which are not media; Lempi's own files in app-private storage; `lyrics_sidecar.rs`'s writes moved there. *Then* the audio-source resolver `[GDE-HST-080]` shrinks to the sidecar reads that go through the grant — **if** the spike shows `File::open` works on MediaStore paths. If it does not, the resolver covers every path GUIDE033 §2 lists.

## 4. Running on Android

**`[GDE-APP-090]` The localhost web server's exposure.** Any app on a phone can open a connection to a localhost port — a boundary the appliance never had. *Recommendation:* **bind to `127.0.0.1` only, and require a per-launch secret** on every HTTP and WebSocket request, handed to the WebView at load and refused when absent. *Why:* without it, any installed app can drive playback and read the listening history.

**`[GDE-APP-100]` Background playback, the session and focus.** *Recommendation:* a **foreground service of type `mediaPlayback`** hosts the player; a **Media3 `MediaSession` over a thin `SimpleBasePlayer` adapter** that forwards to the player's commands gives the notification, lock screen, Bluetooth buttons and Android Auto without Media3 touching audio; audio focus maps to commands — loss to `Pause`, transient loss to `Pause` (or a duck, `[GDE-HST-340]`), gain back to `Play` **only if focus was what paused it**. *Why:* this is the platform's supported shape; anything else is killed in the background.

**`[GDE-APP-110]` When the Director rebuilds.** Today: once at start, off the audio path, and when asked after an import `[IMPL-SUI-075]` — never on a timer. *Recommendation:* **the same triggers on the phone, no more**, with the cost measured by the spike before any "only while charging" rule is added. *Why:* 16.5 CPU-seconds on a Pi 3 core `[GDE-AND-065]` is small against hours of decoding, and a rule written before the phone measurement would be a guess.

**`[GDE-APP-120]` Whether a phone joins the echo fleet.** *Recommendation:* **no, in the first release** — neither leader nor follower. Echo rests on every node's clock disciplined to one LAN reference `[GDE-ECHO-300]`, which a phone on changing networks cannot promise. Build the app without `echo-client`, and add a capability to the snapshot so the skins hide the follow controls, as they hide Wi-Fi `[GDE-HST-360]`.

**`[GDE-APP-130]` The phone's listening history and preferences.** *Recommendation:* **its own**, independent of the appliances in the first release; the existing preference sync is a later feature. *Why:* sync is a feature with conflicts of its own, and a phone that works alone is the prerequisite for any of it.

## 5. Building, shipping and testing

**`[GDE-APP-140]` Where the app lives and how it builds.** *Recommendation:* a top-level `android/` Gradle project; the Rust library built by `cargo-ndk` inside the existing `lempi-android` image, which already holds the NDK; one script that builds both, and says which commit it built, as the deploy scripts do.

**`[GDE-APP-150]` How it is distributed.** *Recommendation:* **a sideloaded, signed APK first**; the Play Store later, when its policies (foreground-service type, storage permissions) have been met on purpose rather than discovered. The MIT licence raises no store conflict `[GDE-IOS-045]`.

**`[GDE-APP-160]` The test strategy.** *Recommendation,* in four layers:

1. the Rust tests, unchanged, on the host and in CI;
2. **the player's Android compile check made a CI gate now** — run once on 2026-09-25 and clean, but a one-off decays `[GDE-HST-025]`;
3. the player library's own tests **run on an emulator or device** (`cargo test --no-run` via `cargo-ndk`, pushed with `adb`), as an optional stage of `verify-targets.sh`;
4. a **device acceptance checklist** per release, run by a person: 30 minutes screen-off, focus loss and return, headset buttons, notification, import, scan, a rebuild — with underruns and battery drain recorded as numbers, not impressions.

**`[GDE-APP-170]` What the answers become.** *Recommendation:* once these are answered, a requirements document for Android — requirements that differ from the appliance, citing the existing ones where behaviour is the same — and a specification of the app written after `[GDE-APP-020]` has run. The README's *Post-V1* ranking of mobile is the maintainer's to move, and nothing here assumes it has.

---

**Traceability:** `[GDE-APP-001..170]` · resolves the conflicts between `[GDE-AND-020]`/`[GDE-AND-040]` and `[GDE-HST-010]`, and between `[GDE-AND-025]`/`[GDE-AND-050]` and `[GDE-AND-070]` · derived from `[GDE-AND-060]`, `[GDE-AND-065]`, `[GDE-HST-080]`, `[GDE-HST-330]`, `[GDE-HST-340]`, `[GDE-ECHO-300]`, `[SPEC-RLK-080]`
