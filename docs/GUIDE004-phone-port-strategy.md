# GUIDE004: Phone Port Strategy

**Development Guidance — how Lempi should reach a phone, and what each route costs**

Android and iOS reach the same answer by different arguments, and the iOS one is
shorter. §§1–3 are Android; §4 is what changes on iOS.

Lempi selects music; it does not merely play it. A phone port therefore needs the Program Director `[SPEC009]` and `lempi.db` on the device, not a remote control for the appliance. This compares the routes.

> **Related:** [LICENSING.md](../LICENSING.md) · [GUIDE002 §2](GUIDE002-rearchitecture-plan.md#2-architectural-decisions) · [SPEC006 §6](spec/SPEC006-data-flow-and-portability.md#6-deployment-topologies) — a phone is the **Sharing** row, not a new topology

---

## 1. The licence decides more than the engineering does

**`[GDE-AND-010]` A GPL-3 fork forecloses MIT, and MIT is the whole point of Lempi.**
[LICENSING.md](../LICENSING.md) does not treat the permissive licence as a preference. It states the reason: *"Lempi has to stay permissive: it is the part people run on their own hardware, **port to their own platforms**, and embed in appliances."* An Android port is that sentence's own example.

The direction is the one already reasoned through for Vipunen: **MIT may be incorporated into a copyleft work; copyleft may not be incorporated into an MIT one.** Forking a GPL-3 player makes the phone app GPL-3 — permanently, and for everyone downstream. Nothing written into that fork can come back to `player/` under MIT unless it was authored independently of the fork.

That is not an argument against GPL-3. It is an argument that this choice is **one-way**, and it is being made in the direction the project spent effort avoiding.

**`[GDE-AND-015]` The platform itself is permissive, so a ground-up build stays MIT.** Verified 2026-08-20:

| Component | Licence | MIT-compatible |
| :--- | :--- | :---: |
| Jetpack **Media3 / ExoPlayer** | **Apache-2.0** | yes |
| Jetpack **Compose**, AndroidX | Apache-2.0 | yes |
| Kotlin stdlib | Apache-2.0 | yes |
| **Auxio** | **GPL-3.0-or-later** | **no** |
| **Local Player** | MIT | yes |

Everything Google ships for audio and UI is Apache-2.0. **Only the mature players are copyleft**, and they are copyleft precisely because they are derived from other copyleft players.

---

## 2. Most of the work is the same either way

**`[GDE-AND-020]` The differentiator is smaller than it looks**, because the Lempi-specific half is identical whichever shell hosts it.

Needed regardless of route:

- **`lempi-core` extraction + JNI.** Measured 2026-08-20, re-checked 2026-09-02: the Director imports only `rusqlite`, `serde`, std collections, `crate::db` and `crate::queue::QueueEntry` — and `queue.rs` imports `VecDeque`, `PathBuf` and `crate::fade::Curve`, a lightweight, dependency-free local module. **No cpal, symphonia, rubato, axum or tokio.** One exception: `director/program.rs` calls into `libc` (Unix) or shells out to `powershell` (Windows) to sync the OS UTC offset — the only non-trivial, platform-conditional dependency in the module; see `[GDE-AND-020]`'s note in [GUIDE006](GUIDE006-director-as-a-guest.md) `[GDE-EXT-055]`. Director + queue is now ~4,550 lines and has grown since the 2026-08-20 measurement — expect this figure to keep drifting upward; it still lifts cleanly.
- **Passage playback.** Lempi plays spans, not files; `MediaItem.ClippingConfiguration` is a first-class Media3 API.
- **Gain and the fade ramps.** A custom `AudioProcessor`. Media3 has no crossfade and has not for years — and that turns out not to be the gap it first looks like: `[SPEC-SC-046]` is Lempi's own de-click envelope (`fade_in_ms`/`fade_out_ms`, applied on the way in, per-passage), separate from `lead_in_ms`/`lead_out_ms`, which time crossfade admission only and apply no gain ramp of their own at all. `[SPEC-SC-043]` measured this library's lead-in median at **5 ms** and lead-out at **946 ms**, and says near-zero overlap is *intended* — a fork needs to port `fade_in_ms`/`fade_out_ms` as the `AudioProcessor` ramp regardless of whether it ever ports lead/crossfade at all, since fade is what plays on every ordinary passage, crossfade or not.
- **Relink by content.** Phone paths resemble nothing on the Pi, and `[SPEC-RLK-025]` is the standing proof that path rewriting is not sound.
- **Bundle import**, listener-state write-back, backup, Director reload.

Provided by Media3 either way: decode, gapless, audio focus, media session, notification, media buttons, Bluetooth routing.

**So a fork saves only the shell**: library browsing, MediaStore indexing, tag parsing, playlists, search, sort, theming.

**`[GDE-AND-025]` And the shell it saves is the part Lempi replaces.** Auxio's value is *browse your library and choose*. Lempi's premise is that it chooses — the Director exists so nobody picks the next track. Their information architectures are opposed, so a fork inherits an elaborate answer to a question Lempi does not ask, then spends effort suppressing it. The db is also already authoritative for artist, album, span, gain and flavor, so MediaStore indexing is a second source of truth for facts Vipunen has already established `[SPEC-DF-010]`.

---

## 3. The three routes

**`[GDE-AND-030]`**

| | Fork **Auxio** | Fork **Local Player** | **Ground-up** on Media3 |
| :--- | :--- | :--- | :--- |
| Licence outcome | **GPL-3, one-way** | MIT | **MIT** |
| Maturity inherited | high — v4.1.5, 3,427 commits | low — first release May 2026 | none |
| Shell reused | most, and mostly unwanted | some | none |
| Upstream merges | real, ongoing | light | none |
| Fights its library model | **yes** | yes | no |
| Lempi-specific work | same | same | same |

**Auxio** is the best *player* of the three and the worst *host*: its strength is the layer Lempi discards, and its licence is the one thing that cannot be undone later.

**Local Player** is licence-compatible and Compose/Media3-based, but three months old at evaluation. Forking it buys a modest head start on scaffolding against an unproven base — closer to "read it for reference" than "build on it".

**Ground-up** starts with nothing and needs nothing it cannot take under Apache-2.0.

---

## 4. iOS: the same answer, reached faster

**`[GDE-IOS-010]` The licence stops being a preference and becomes a distribution
blocker.** On Android a GPL-3 fork is shippable and merely forecloses MIT. On
iOS it is not shippable at all: the App Store's terms impose per-device and DRM
restrictions that conflict with the GPL's guarantee of free redistribution, and
this is settled by precedent rather than argument — **VLC was pulled from the
App Store in January 2011** over exactly this, and returned only after
relicensing away from pure GPL.

So the fork-or-build question does not need weighing here. It is answered by the
store's terms before engineering is consulted.

**`[GDE-IOS-015]` And there is nothing to fork in any case.** The Android survey
found a mature, well-architected candidate in Auxio — 3,427 commits. The iOS
equivalent does not exist: the open-source landscape is small demonstration
players and abandoned samples. The one mature open-source media application on
the platform is VLC, which is a video player that had to change its licence to
be there.

**`[GDE-IOS-020]` The audio stack suits Lempi better than Android's does.** This
is the one place iOS is ahead, and it is not a small lead:

| Need | Android (Media3) | iOS (AVAudioEngine) |
| :--- | :--- | :--- |
| Play a passage span | `ClippingConfiguration`, millisecond | **`scheduleSegment(_:startingFrame:frameCount:at:)` — frame-exact** |
| Fade-in / fade-out ramp `[SPEC-SC-046]` | custom `AudioProcessor` | mixer-node volume ramp, native |
| Crossfade (lead admission window) | **absent**, requested since 2017 | two player nodes into a mixer |

Lempi's passage is a frame range with a gain and two fade ramps `[SPEC-SC-040]`, `[SPEC-SC-046]` — plus two lead points that time crossfade admission but carry no ramp of their own — and
`scheduleSegment` is that signature almost exactly. Sample-accurate boundaries
also matter more here than they look: `[SPEC-SA-092]` showed boundary error
propagating into *flavor*, not merely into playback.

**`[GDE-IOS-025]` Rust reaches iOS more cleanly than Android, but only from a
Mac.** `aarch64-apple-ios` plus **UniFFI** generates Swift bindings from the
Rust source, packaged as an XCFramework — no JNI boilerplate to hand-write, so
`lempi-core` `[GDE-AND-045]` binds more directly than it would on Android.

**The cost is hardware and a subscription.** Building the Rust library for iOS
*requires a macOS host* — the SDK and toolchain are macOS-only — and signing and
distribution require Xcode plus the Apple Developer Program for anything beyond
a seven-day personal provisioning profile. This project's entire toolchain today
is Windows with a Linux container for the Pi `[GDE-AND-020]`; Android needs no
new machine, and iOS needs one before the first line compiles.

**`[GDE-IOS-030]` Getting the library onto the device is the awkward part.**
There is no rsync over ssh into a sandbox. Music lands in the app's own
Documents directory through the Files app, Finder file sharing, or a transfer
the app implements itself, and no other application can see it. Two
consequences, and neither is fatal:

- **Relink is unaffected and still required** — it binds by content, and the
  device's paths will match nothing `[SPEC-RLK-030]`.
- **A subset is the normal case**, which the design already states: *"a Pi
  holding a subset of a 44 GB library is a normal deployment"* `[SPEC-RLK-060]`.
  A phone holding a subset is the same sentence.

**`[GDE-IOS-035]` Net.** Engineering favours iOS; logistics favour Android. The
audio model fits better, the Rust binding is cleaner, and the licence question
answers itself — against a Mac, a developer subscription, App Store review, and
a sandbox that makes moving 44 GB a design problem rather than a command.

---

## 5. Would anyone use it? An estimate, not a measurement

Everything above is checkable. This section is judgement, and is marked as such
because the rest of this repository is not `[GDE-LES-030]`.

**`[GDE-IOS-040]` The market exists, is real, and is small.** Measured 2026:
**86% of recorded-music spending is streaming**, across 865 million paid
subscriptions. The ownership counter-current is genuine — vinyl reached ~$1.8 bn
globally in its **19th consecutive growth year**, and subscription fatigue is a
named trend — but it is one to two percent of the money, and iOS already has
**200+ local-player apps**. The niche is served, not empty: Doppler at **$9
once**, Evermusic free with 19k ratings, Marvis, Flacbox, VOX.

**The vinyl number is weaker evidence than it looks.** Buying a record is a
physical, ritual, collectible act. Lempi needs someone who maintains a large
*tagged digital* library. Those populations overlap without being the same, and
conflating them is how a niche gets overestimated.

**`[GDE-IOS-045]` The binding constraint is Vipunen, not price, licence or Apple.**
Apple is not the obstacle — local players are plentiful and unmolested, and MIT
raises no App Store conflict at all. The obstacle is the first hour:

1. own thousands of local files, already tagged;
2. run a **Python + Essentia** pipeline on an x86 desktop — and the only macOS
   Essentia build is **x86_64 from 2015** `[GDE-FEX-137]`, so on Apple Silicon
   that is an eleven-year-old binary under Rosetta;
3. register an **AcoustID API key** `[SPEC-SA-055]`;
4. wait — extraction measured at **22.7 h for this library** `[SPEC-SA-025]`;
5. move audio and payload into an application sandbox with no rsync.

Steps 2–4 are not consumer steps, and are barely enthusiast steps. **Whatever
the phone app costs, its addressable market is capped by Vipunen's**, and Vipunen is
the hard part. That is the number that decides adoption, and it is small.

**`[GDE-IOS-050]` The unlock, if one is wanted, is decoupling from Vipunen.** The
2022 AcousticBrainz dump already covers **8,001 of 8,542 recordings — 93.7%** of
this library `[LOG-FEX-055]`. A phone that fetched flavor *by recording MBID*
from a precomputed set would need no Essentia, no 22 hours and no desktop for
mainstream music, and would fall back to tags for the rest. That changes the
addressable market by orders of magnitude and is a **product decision, not a
port decision** — it belongs before any Swift is written, not after.

**`[GDE-IOS-055]` On the two business models, the free one is stronger, and the
subscription is the weakest of the three obvious options.**

| | MIT, free | $0.99/yr, closed | one-time ~$9, either licence |
| :--- | :--- | :--- | :--- |
| Net per user | $0 | **$0.84/yr** at 15% `[GDE-IOS-060]` | ~$7.65 once |
| Break-even on the $99/yr Developer Program | n/a | **118 subscribers, recurring** | 13 sales |
| Fits the audience | **yes** | **no — they are subscription-averse by definition** | yes; it is what Doppler proved |
| Fits `LICENSING.md` | yes | **no** | either |

Three things make **$0.99/yr** the worst option rather than a modest one.
**The economics do not clear support**: one email exchange costs more than a
year of that subscriber. **The positioning is self-defeating**: the people who
reject streaming subscriptions are being sold a subscription, and closing the
source alienates the FOSS-sympathetic half of the same audience. And **the
friction is identical to a larger price** — the decision to enter a payment
method costs the user the same at $0.99 as at $9.

If money is wanted, the market has already answered: **one-time purchase**,
which is what Doppler charges and what this audience buys.

**`[GDE-IOS-060]` Realistic outcome, stated plainly.** For a free MIT app,
launched with a candid README and posted where such things are read: **hundreds
to low thousands of installs, tens of sustained users**. Not because it is
poor — because the funnel narrows at "run this desktop pipeline first". For the
subscription version, materially fewer, since it adds a paywall to the *same*
funnel and subtracts the goodwill.

**`[GDE-IOS-065]` Which is a reason to be clear about the goal, not to stop.**
This project descends from six years of one person's listening `[GDE-BMK-020]`,
and its stated purpose is to fix new-music induction for that library
`[GDE-CHT-020]`. Publishing costs little once the port exists, and someone
finding it is a bonus rather than the point. **Building it *for* adoption is the
version that would disappoint** — that version needs the Vipunen decoupling
`[GDE-IOS-050]` first, and that is a different project.

---

## 6. Recommendation

**`[GDE-AND-040]` Build ground-up on both, and read the others rather than fork them.** On Android that is a judgement — the fork is possible and the shell it saves is mostly unwanted. On iOS it is not a judgement: nothing mature exists to fork, and a copyleft fork could not be distributed if it did.

On Android specifically: The engineering saved by forking is the shell; the shell is largely wrong for a radio; and the price is the licence Lempi exists to keep. Reading Auxio's playback service for how it handles focus, notifications and Android's edges costs nothing and carries no obligation — **ideas are not derivative works, code is.**

**`[GDE-AND-045]` Extract `lempi-core` first, whatever is decided later.** It is worth doing on its own merits: one selection engine shared by desktop, appliance and phone, rather than a second Director in Kotlin — which is the two-implementations fault `[GDE-FBD-040]` names and the payload fixtures were built to prevent `[SPEC-SUI-130]`. It is also the honest way to find the real cost, since it is the piece no route avoids.

**Done, 2026-09-22.** `player/core/` is the crate: `bundle`, `db`, `director`, `fade`, `queue`, `relink` and the data half of `tags`, with the listener-setting defaults that `player_store` reads as fallbacks. `lempi_player` re-exports all of it under the original names, so the 99 existing `crate::db::…` and `crate::director::…` call sites were not touched.

**`[GDE-AND-047]` What the extraction measured, which is the part worth keeping.** The estimate in §2 held: the module lifted without an audio or web dependency following it. Three things it found that reading imports had not:

- **The boundary is 30 crates against the player's 135**, and `cpal`, `symphonia`, `rubato`, `axum`, `tokio`, `hyper`, `alsa` and `reqwest` are unreachable from any of them. `build/verify-targets.sh` now asserts that rather than leaving it to be re-read — it had been re-measured by hand on 2026-08-20, 2026-09-02 and 2026-09-22.
- **The developer-loop saving is small and should not be the reason.** A Director edit rebuilt the single crate in 9.7 s before; `cargo test -p lempi-core` is 4 s after, and the whole workspace is still 11 s.
- **Splitting the crate silently cut the test run by 38%**, 596 to 368, because a bare `cargo test` tests the root package only — the 228 lost were every test in `lempi-core`. `default-members` fixes it; the fault is `echo-client`'s exactly `[GDE-ECHO-384]`, and it is the one thing about this refactor worth remembering. *(Corrected 2026-09-24: first written as "halved, 663 to 359". 663 counted `#[test]` attributes, feature-gated ones included, and 359 was one test binary's line rather than the sum — a miscount of the kind CLAUDE.md §6 describes, made while writing about one.)*

Two things in the new crate still leave the process, and a phone finds both: `relink::hash_encoded` shells out to **ffmpeg** and must keep doing so `[SPEC-RLK-080]`, and `director::program`'s UTC-offset sync uses `libc` on Unix — which Android and iOS both are, so only the first is real work.

**`[GDE-AND-050]` The phone is a bundle target, not a new design.** `[SPEC-DF-080]`'s *Sharing* row already describes it: audio arrives with its payload, class A/B/C is imported after verification, and every advanced feature works **with no Vipunen present**. The exporter and importer exist. Vipunen's AGPL never approaches the phone, because the phone consumes derived data and never derives any.

---

## 7. Open

1. **`[GDE-AND-060]` Whether the phone runs the existing web UI in a WebView.** `axum` and `tokio` are Apache-2.0/MIT and the three skins already exist `[REQ-VIS-160]`. Maximum reuse, at the cost of a non-native feel and a server on a battery. Cheap to spike, and it would answer how much native UI is really wanted.
2. **`[GDE-AND-065]` Battery cost of the Director.** Measured on the appliance at 9.86 s and ~12 MB per rebuild `[IMPL-SUI-075]` — but that is `Director::load` **in isolation**. In service, rebuilding while it plays, `lempi02w` logged a median of **22.8 s** over 17 rebuilds of the same 8,330 passages, and a phone's budget belongs against that figure `[GDE-HST-200]`. Per-selection cost is unmeasured, and a phone budget is not a Pi budget.
3. **`[GDE-AND-070]` Where the library lives** — app-private storage, or a user-chosen tree via the Storage Access Framework. Affects relink, and whether other apps can see the music.

---

**Traceability:** `[GDE-AND-010..070]`, `[GDE-IOS-010..065]` · derived from [LICENSING.md](../LICENSING.md), `[GDE-ARC-018]`, `[SPEC-DF-080]`, `[GDE-FBD-040]`
