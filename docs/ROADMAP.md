# ROADMAP: What's Open, Across the Project

**Orientation — Tier 1 · forward-looking only, added 2026-09-03 per `[GOV-DOC-050]`**

A single scan point for "what's not built yet." Most open items live in the
document whose subject they belong to — this page mostly **links** rather than
duplicates, so a change to one of those sections doesn't also require an edit
here. The prose sections below hold only the material that doesn't belong to
a single document: cross-cutting plans, or an investigation that was never
folded into one spec.

> **Related:** [architecture.md](architecture.md) for what's built ·
> [GOV001](GOV001-document-hygiene.md) `[GOV-DOC-050]` for the convention this
> page exists to satisfy · [GUIDE001](GUIDE001-lineage-and-lessons.md) for the
> historical counterpart to this page

---

## 1. Index — every document's own "Open" section

| Area | Document | Section |
| :--- | :--- | :--- |
| System-wide gaps, including where the code falls short of a specification | [architecture.md](architecture.md) | [§10 Known gaps](architecture.md#10-known-gaps) |
| Functional requirements | [REQ002](spec/REQ002-functional-requirements.md) | [§8 Coverage Gaps](spec/REQ002-functional-requirements.md#8-coverage-gaps) |
| Flavor distance | [SPEC005](spec/SPEC005-flavor-distance.md) | [§5a Future Direction — entry/exit flavor](spec/SPEC005-flavor-distance.md#5a-future-direction--entry-and-exit-flavor) |
| Vipunen pipeline | [SPEC007](spec/SPEC007-vipunen-architecture.md) | [§6 Segmentation & Amplitude — PROVISIONAL](spec/SPEC007-vipunen-architecture.md#6-segmentation--amplitude-s2-s6--provisional) |
| DAO segmentation cascade | [SPEC024](spec/SPEC024-dao-segmentation-cascade.md) | [§8 Open](spec/SPEC024-dao-segmentation-cascade.md#8-open) |
| CD ripping (built 2026-09-04, person-assisted — verified against a real disc; sets a disc at a time since 2026-10-14) | [SPEC025](spec/SPEC025-cd-ripping.md) | [§8 Open](spec/SPEC025-cd-ripping.md#8-open) |
| CD ripping: how Vipunen drives a GUI-only Windows tool (decided — person-assisted, built) | [SPEC027](spec/SPEC027-cd-ripping-windows-automation.md) | whole document |
| CD ripping — multi-disc sets, one-piece discs and hidden audio (built 2026-10-14, image rips); the folded-in passage (built 2026-10-27) | [SPEC026](spec/SPEC026-cd-ripping-passages.md) | [§3 Open](spec/SPEC026-cd-ripping-passages.md#3-open) |
| Database schema | [SPEC008](spec/SPEC008-database-schema.md) | [§8 Open](spec/SPEC008-database-schema.md#8-open) |
| Program Director | [SPEC009](spec/SPEC009-program-director.md) | [§9 Open](spec/SPEC009-program-director.md#9-open) |
| Audio path supervisor | [SPEC011](spec/SPEC011-audio-path-supervisor.md) | [§5 Risks and open questions](spec/SPEC011-audio-path-supervisor.md#5-risks-and-open-questions) |
| Library relink — the identity hash (re-keyed to Symphonia 2026-09-27; one file it cannot read remains) | [SPEC045](spec/SPEC045-the-identity-hash.md) | [§3 Done, 2026-09-27](spec/SPEC045-the-identity-hash.md#3-done-2026-09-27) |
| Vipunen console | [SPEC013](spec/SPEC013-vipunen-console.md) | [§6 Open](spec/SPEC013-vipunen-console.md#6-open) |
| Payload schema | [SPEC014](spec/SPEC014-payload-schema.md) | [§6 Open](spec/SPEC014-payload-schema.md#6-open) |
| Mesh library sync — N Vipunen-capable peers (partly built: the diff, its review and conflict resolution, the peer registry and the console's `/mesh` page; whether the rest is superseded by star sync is open question 5 below) | [SPEC035](spec/SPEC035-mesh-library-sync.md) | [§8 Open](spec/SPEC035-mesh-library-sync.md#8-what-remains-open-after-this-document) |
| MPD Director | [SPEC015](spec/SPEC015-mpd-director.md) | [§8 Open](spec/SPEC015-mpd-director.md#8-open) |
| Waveform boundary editor | [SPEC021](spec/SPEC021-waveform-boundary-editor.md) | [§6 Not yet measured](spec/SPEC021-waveform-boundary-editor.md#6-not-yet-measured) |
| Phone ports | [GUIDE004](GUIDE004-phone-port-strategy.md) | [§7 Open](GUIDE004-phone-port-strategy.md#7-open) |
| Player without an appliance (phone groundwork, logging) | [GUIDE033](GUIDE033-the-player-without-an-appliance.md) | [§5 Open](GUIDE033-the-player-without-an-appliance.md#5-open) |
| Hosted flavor service | [GUIDE005](GUIDE005-flavor-service.md) | [§5 Open](GUIDE005-flavor-service.md#5-open) |
| Director as a guest | [GUIDE006](GUIDE006-director-as-a-guest.md) | [§5 Open](GUIDE006-director-as-a-guest.md#5-open) |
| Deploy script naming and target signposting (renamed 2026-09-11; config persistence built; appliances built without `vipunen-support` from 2026-09-12 `[GDE-DEP-098]`); a worktree build's commit stamp `[GDE-DEP-100]` and the final cross-check's wording `[GDE-DEP-110]` resolved 2026-10-14 | [GUIDE011](GUIDE011-deploy-script-naming.md) | [§7 Configuration, and what is still open](GUIDE011-deploy-script-naming.md#7-configuration-and-what-is-still-open) |
| External backends | [GUIDE007](GUIDE007-external-backends-investigation.md) | [§7 Open](GUIDE007-external-backends-investigation.md#7-open) |
| Echo drift measurement (the Phase 1 campaign from 2026-09-12; a longer re-read and three unplanned nodes remain) | [LOG006](LOG006-echo-drift-measurement.md) | [§4 Open](LOG006-echo-drift-measurement.md#4-open) |
| Echo playback — two or more instances, one programme, each from its own files. **Phases 0-6 built**: phase 4 measured in [LOG012](LOG012-phase-4-drift.md) (concluded 2026-09-18), phase 5 in [GUIDE017](GUIDE017-echo-correction.md) (offset and rate correction, both 2026-09-18), phase 6 in [GUIDE018](GUIDE018-echo-invalidation.md) | [GUIDE008](GUIDE008-echo-playback-investigation.md) · [GUIDE009](GUIDE009-echo-playback-plan.md) · [GUIDE010](GUIDE010-echo-node-capabilities.md) · [GUIDE013](GUIDE013-audio-stack-reporting.md) · [LOG006](LOG006-echo-drift-measurement.md) | [GUIDE016 §6 Explicitly not in v1](GUIDE016-echo-playback-plan-build.md#6-explicitly-not-in-v1) |
| Same-song blocking across different recordings (**built 2026-09-12** — three identity tiers, passage → recording MBID → work MBID, covers included; [SPEC037](spec/SPEC037-eligibility-and-frequency.md) `[SPEC-DIR-119]`) | [GUIDE012](GUIDE012-work-based-song-blocking.md) | [§7 Open](GUIDE012-work-based-song-blocking.md#7-open) |
| Appliance setup | [LempiPi/IMPL001](../LempiPi/IMPL001-appliance-setup.md) | [IMPL012 §9 Open](../LempiPi/IMPL012-rebuilding-from-the-repository.md#9-open) |
| Image & partitions | [LempiPi/PI001](../LempiPi/PI001-image-and-partitions.md) | [§7 What is not yet decided](../LempiPi/PI001-image-and-partitions.md#7-what-is-not-yet-decided) |
| Appliance characterisation | [LempiPi/PI006](../LempiPi/PI006-appliance-characterisation.md) | [§9 What was not measured](../LempiPi/PI006-appliance-characterisation.md#9-what-was-not-measured) |
| MPD on the appliance | [LempiPi/PI007](../LempiPi/PI007-mpd-on-the-appliance.md) | [§4 What was not measured](../LempiPi/PI007-mpd-on-the-appliance.md#4-what-was-not-measured) |
| The second appliance, `bose` — in service since 2026-09-06; operating health and its standing findings are [BOSE004](../BosePi/BOSE004-operating-health.md) | [BosePi/](../BosePi/) | [BOSE007 §4 Open](../BosePi/BOSE007-migrating-the-library.md#4-open-and-to-be-measured-before-it-is-claimed) |
| Operating health on `bose`, in service | [BOSE004](../BosePi/BOSE004-operating-health.md) | [§5. Standing findings — open, none urgent](../BosePi/BOSE004-operating-health.md#5-standing-findings--open-none-urgent) |
| The `bose` image update — plan | [BOSE008](../BosePi/BOSE008-image-update-plan.md) | [§8. Open](../BosePi/BOSE008-image-update-plan.md#8-open) |
| The `bose` image update — runbook | [BOSE009](../BosePi/BOSE009-image-update-runbook.md) | [§6. Open](../BosePi/BOSE009-image-update-runbook.md#6-open) |
| Appliance startup preflight | [PI026](../LempiPi/PI026-startup-preflight.md) | [§7. Open](../LempiPi/PI026-startup-preflight.md#7-open) |
| The controller wedge | [PI027](../LempiPi/PI027-the-controller-wedge.md) | [§7. What is still open](../LempiPi/PI027-the-controller-wedge.md#7-what-is-still-open) |
| `smartboardpc`, the build host | [SMART001](../SmartPC/SMART001-survey.md) | [§7. Open](../SmartPC/SMART001-survey.md#7-open) |
| Echo alignment — the standing model | [GUIDE029](GUIDE029-what-is-known-about-alignment.md) | [§4. The largest open question](GUIDE029-what-is-known-about-alignment.md#4-the-largest-open-question) |
| Converting the third appliance | [IMPL016](IMPL016-converting-lempiplay3.md) | [§7. Open](IMPL016-converting-lempiplay3.md#7-open) |
| Drift instrument correction | [LOG007](LOG007-drift-instrument-correction.md) | [§5. Open](LOG007-drift-instrument-correction.md#5-open) |
| Acoustic calibration | [LOG008](LOG008-acoustic-calibration.md) | [§5. Open](LOG008-acoustic-calibration.md#5-open) |
| The lead is a ring | [LOG011](LOG011-the-lead-is-a-ring.md) | [§5. Open](LOG011-the-lead-is-a-ring.md#5-open) |
| Echo phase 4 — measured drift | [LOG012](LOG012-phase-4-drift.md) | [§4. Open](LOG012-phase-4-drift.md#4-open) |
| Per-node presentation delay | [SPEC043](spec/SPEC043-node-delay-control.md) | [§5. Open](spec/SPEC043-node-delay-control.md#5-open) |
| Listener preference editing | [SPEC029](spec/SPEC029-listener-preference-editing.md) | [§8. Open](spec/SPEC029-listener-preference-editing.md#8-open) |
| Play frequency readout | [SPEC031](spec/SPEC031-play-frequency.md) | [§6. Open](spec/SPEC031-play-frequency.md#6-open) |
| The Android device spike | [LOG013](LOG013-the-android-spike.md) | [§4. Open](LOG013-the-android-spike.md#4-open) |
| Wi-Fi watchdog — a future feature, decided by how often a link dies (the travel access point of [SPEC061](spec/SPEC061-travel-wifi-failover.md) starts when asked for once the link is lost, and by itself only on a node with no paired speaker `[SPEC-WFO-090]`; a link that stays up while its gateway stops answering is still this row's case) | [FLEET001](../fleet/FLEET001-the-fleet.md) | [§4. Standing issues](../fleet/FLEET001-the-fleet.md#4-standing-issues) `[FLT-ISS-015]` |
| The shared radio — Wi-Fi and Bluetooth on one chip: the link held, the keeper quiet once settled, one early redial, transfers pausing the music, the access point on request (built and deployed to lp3-wifi and bose 2026-10-25; lempi02w not yet) | [LempiPi/PI031](../LempiPi/PI031-settling-the-shared-radio.md) | [§10. Not yet known](../LempiPi/PI031-settling-the-shared-radio.md#10-not-yet-known) |
| Framebuffer UI — built 2026-09-07, all seven phases; seek on the touch screen not yet confirmed | [SPEC042](spec/SPEC042-framebuffer-implementation-plan.md) | [§8. Implementation plan, phased](spec/SPEC042-framebuffer-implementation-plan.md#8-implementation-plan-phased-against-the-open-items-above) |
| Star sync — the hub merges and distributes both halves | [SPEC046](spec/SPEC046-star-sync.md) | [§11. Open](spec/SPEC046-star-sync.md#11-open) |
| Mesh membership and trust | [SPEC049](spec/SPEC049-mesh-membership-and-trust.md) | [§6. Order of work, and what is open](spec/SPEC049-mesh-membership-and-trust.md#6-order-of-work-and-what-is-open) |
| Mesh without ssh — the signed transport | [SPEC054](spec/SPEC054-mesh-without-ssh.md) | [§7. Open, to settle when built](spec/SPEC054-mesh-without-ssh.md#7-open-to-settle-when-built) |
| Fleet configuration kept private (`[SPEC-FCP-010]` awaits the private repository) | [SPEC055](spec/SPEC055-fleet-configuration-kept-private.md) | [§4. Open](spec/SPEC055-fleet-configuration-kept-private.md#4-open) |
| CD import | [SPEC056](spec/SPEC056-cd-import.md) | [§11. Open](spec/SPEC056-cd-import.md#11-open) |
| Album names held short in folder and file names | [SPEC063](spec/SPEC063-album-names-held-short.md) | [§4. Open](spec/SPEC063-album-names-held-short.md#4-open) |
| Parametric equalizer — built 2026-10-22; follows the stored output rather than the sounding one | [SPEC064](spec/SPEC064-parametric-equalizer.md) | [§5. Open](spec/SPEC064-parametric-equalizer.md#5-open) |
| Tempo, pitch and A-B loop — built 2026-10-22; unheard at their extremes | [SPEC065](spec/SPEC065-tempo-pitch-and-loop.md) | [§6. Open](spec/SPEC065-tempo-pitch-and-loop.md#6-open) |
| Holding a passage back (whole-album play `[REQ-PD-127]` built 2026-10-28, in SPEC066) | [SPEC057](spec/SPEC057-holding-a-passage-back.md) | [§6. Open](spec/SPEC057-holding-a-passage-back.md#6-open) |
| Playing an album as recorded — built 2026-10-28; unheard across a segue | [SPEC066](spec/SPEC066-playing-an-album-as-recorded.md) | [§5. Open](spec/SPEC066-playing-an-album-as-recorded.md#5-open) |
| An album by its identity — built 2026-10-28; two of one name, credit and year look alike | [SPEC067](spec/SPEC067-an-album-by-its-identity.md) | [§3. Open](spec/SPEC067-an-album-by-its-identity.md#3-open) |
| A disc ripped again — written 2026-10-30; the earlier rip named, its files gapless and joined without fades; the merge not built; measured, not heard | [SPEC069](spec/SPEC069-a-disc-ripped-again.md) | [§5. Open](spec/SPEC069-a-disc-ripped-again.md#5-open) |
| Catalogue patch by natural key | [SPEC058](spec/SPEC058-catalogue-patch-by-natural-key.md) | [§9. Open](spec/SPEC058-catalogue-patch-by-natural-key.md#9-open) |
| A node's own occasion policy, a profanity ceiling first (designed, not scheduled) | [GUIDE036](GUIDE036-node-occasion-policies.md) | [§2. The design](GUIDE036-node-occasion-policies.md#2-the-design-a-nodes-own-policy) |
| Integrity -- every stored hash read again where the file lies, damage withheld and repaired (planned 2026-10-14; built but for the phone's scrub, the databases' weekly check -- waiting on a measurement on `lempi02w` -- and the changed-value tripwire) | [GUIDE038](GUIDE038-integrity-gap-closures.md) | [§7 Order and size](GUIDE038-integrity-gap-closures.md#7-order-and-size) |
| Android requirements (draft) | [REQ007](spec/REQ007-android.md) | [§7. Open](spec/REQ007-android.md#7-open--to-be-settled-before-this-leaves-draft) |

## 2. Sendspin — a whole directory of "watch, don't build yet"

[`sendspin/`](../sendspin/) holds six investigation documents (SPIN001–006)
into whether/how Lempi should interoperate with the Sendspin multi-room
ecosystem, Music Assistant, and OpenSubsonic. Every one of them is already
structured as current analysis plus its own small "Open"/"Recommendation"
section — nothing here is built, and the standing recommendation across all
six is to watch rather than commit engineering time. Read
[SPIN001](../sendspin/SPIN001-protocol-and-integration-analysis.md) first;
each of the other five narrows to one sub-question it raised.

## 3. Rearchitecture — what's still ahead

*(From [GUIDE002](GUIDE002-rearchitecture-plan.md)'s phased plan and open
questions — see [GUIDE001 §8](GUIDE001-lineage-and-lessons.md#8-rearchitecture-phases--retrospective)
for the phases that have already shipped.)*

### P4 — Ingest & DAO Segmentation

Requirements (`[REQ-LIB-200..215]`) and specification
([SPEC024](spec/SPEC024-dao-segmentation-cascade.md)) are done; the four
reproducible cascade stages (grid search, DP assembly, RMS quiet-spot
fallback, extra-track merging) are built — see
[GUIDE001 §8](GUIDE001-lineage-and-lessons.md#8-rearchitecture-phases--retrospective)
for what shipped. Two pieces remain genuinely open, both detailed in
[SPEC024 §8](spec/SPEC024-dao-segmentation-cascade.md#8-open):

1. **The 7-strategy automatic MusicBrainz edition search** that would
   supply the cascade's expected track count/durations without a human
   typing them in — real query design, rate-limited network calls, and
   its own accuracy measurement, genuinely separate work from the cascade
   itself.
2. **McRhythm's "Stage 6" boundary refinement** — no recoverable
   algorithm survives to reproduce, only tuning thresholds and aggregate
   results in a historical test-results document. A future pass would be
   new design work informed by those numbers, not a port.

**Independent re-verification against Lempi's own library — partial.**
`[GOV-SRC-020]`: no CI-portable ground-truth corpus exists in this repo —
`segment_dao.py --validate` checks against the user's own live library
(188 files / 2,676 boundaries). A 40-file sample, run 2026-09-03: 40/40
exact track count (100%), 94% of boundary starts within 2s, all resolved
by Stage 2 alone — see [SPEC024](spec/SPEC024-dao-segmentation-cascade.md)'s
own status banner. The full 188-file population, and a real case that
actually exercises DP assembly/the RMS fallback/merging rather than only
their synthetic unit tests, remain unrun.

### Open questions

1. **Which user-defined characteristics to define first?** `[GDE-ARC-030]` supports them generally; MuLibPlay's six years of use suggest christmas / winter / summer / kids are the proven ones `[GDE-PD-020]`.
2. **Wall Art / Kiosk display mode — decided 2026-10-14: a fourth skin.** The pre-rearchitecture plan (`docs/user-interface.md`, now deleted) specified a fullscreen wall-tablet mode: large album art, clock, upcoming-track cards, OLED/LCD burn-in protection. It was never built. The maintainer's decision: it is wanted, as a fourth skin beside `lempi`, `mulibplay` and `winamp`, under the existing skin contract `[REQ-VIS-160]`. Not yet specified or scheduled. Beside it since 2026-10-29, and not the same thing: the **picture frame** [SPEC068](spec/SPEC068-the-picture-frame.md), a slideshow and clock a desktop player puts on its own machine's screen, for `lrhtpc` first.
3. **The Phase 7 features — decided 2026-10-14: to be developed, slowly.** The old roadmap's final phase (`docs/roadmap.md`, now deleted) named station-ID/jingle injection between tracks, news/weather TTS announcements, and MQTT/smart-home hooks. None is in `REQ002`, a `SPEC` or the code yet. The maintainer's decision: each is wanted, one at a time and without hurry; each starts as a requirement in `REQ002` before any code. Scrobbling is a separate question, already declined for the MPD guest path `[SPEC-MPD-100]`.
4. **Library-browse pagination — decided 2026-10-14: the flat cap was not deliberate.** The deleted `REQ001`'s `[REQ-UI-020K]` specified a page-size choice (`10`/`25`/`50`/`100`/`250`) with Prev/Next. The built `/browse` route (`player/src/web/browse.rs`) caps every response at a flat 2,000 rows with no selector `[REQ-VIS-180]`. The maintainer: the cap was an absence, not a decision, so a library past 2,000 rows in any listing is cut short without saying so. Paging is owed.
5. **Mesh sync between N Vipunen peers — decided 2026-10-14: the star is interim.** [SPEC035](spec/SPEC035-mesh-library-sync.md) and [SPEC040](spec/SPEC040-peer-registry-and-review-ui.md) designed peers that each ingest and sync with one another; the diff, its review and conflict resolution, the peer registry and the console's `/mesh` page are built. Since 2026-09-26 the household syncs as a star ([SPEC046](spec/SPEC046-star-sync.md)). The maintainer: star sync is an interim implementation, and the greater peer-to-peer meshing remains the direction, to be developed in the future. Star sync's work should be kept reusable by it -- the signed transport, natural-key patches and the `rows` step are peer-shaped already.

## 4. The appliance's still-open speaker questions

**Does a reopened Bluetooth output stream actually hold indefinitely, or does
the 700 ms settle merely push the failure further out?** Investigated
2026-08-16 in [PI022 §4a](../LempiPi/PI022-the-players-speaker-contract.md#4a-the-reopened-stream-and-feeding-silence-while-paused);
the incident and what was tried is recorded in
[PI008 §2](../LempiPi/PI008-appliance-bringup-history.md#2-a-reopened-stream-dies-a-fresh-one-does-not).

A stream rebuilt against an already-running player (`recover()`) was found to
lose the speaker about twenty-two seconds after reopening, every time, while a
stream opened fresh at startup held indefinitely. Giving PipeWire 700 ms to
finish tearing down the old stream before opening the new one closed the gap
in testing — two minutes, connected on every sample, from the worst case
(player already running, no speaker, then connect and select).

That two-minute result is not enough to trust on its own: an earlier
two-minute test had already been called "verified" once, and the drop
actually arrived twenty seconds after that window closed, at about two and a
half minutes. No test run so far has gone longer than the failure interval it
was trying to rule out. **Still open:** whether the 700 ms settle actually
fixes the fragility, or only delays it past whatever window each test
happened to run, is unconfirmed. Closing this needs a run of at least ten
minutes with the underrun counter watched throughout — the signal that
caught the fault both previous times it mattered — before the reopened-
stream path can be called reliable rather than merely improved.

## 5. What the reviews left

The reviews of 2026-10-08 and 2026-10-14
([reports/review-2026-10-14.md](../reports/review-2026-10-14.md)) and the work
after each closed most of what they found. Where the code falls short of a
specification, the one register is [architecture.md §10](architecture.md#10-known-gaps);
what remains here is the work that crosses documents.

1. **Sync `lempi02w`.** It and `teacherslounge` were rolled forward to
   `37e8df2` on 2026-10-15, and every node now runs a build with the `rows`
   step ([FLEET001](../fleet/FLEET001-the-fleet.md) `[FLT-RUN-010]`). `lempi02w`
   still credits Woodstock's and *An American Treasure*'s second discs as
   their first discs' songs, as its 2026-10-06 bundle left them, until its
   next star sync asks it for its own rows and corrects them
   `[SPEC-STAR-945]`. The two deferred
   device checks of `[FLT-ISS-070]` -- a restore from the Listening backups
   row, and the play-rate check -- remain.
2. **The Program Director's open items**, [SPEC009 §9](spec/SPEC009-program-director.md#9-open):
   a restored queue re-weighed and its provenance kept, `[SPEC-DIR-235]` and
   `[SPEC-DIR-245]` together. (`[SPEC-DIR-240]`, a passage that
   crashed the player coming straight back, was built 2026-10-14.)
3. **How a seek feels.** A seek cuts the ring with the skip's own fade (2 s)
   and lead (0.5 s); a shorter fade of its own may feel crisper. The
   maintainer found it "functional, could be smoother", 2026-10-09: a question
   for the ear, not for a test.
4. **The default skin and the settings layout** (review U-01, U-02), left as
   they are by the maintainer's decision of 2026-10-08. Worth revisiting once
   the rest settles: the settings list has grown since, with the Listening
   backups row and *Album names*.
5. **What CD import still lacks** ([SPEC026](spec/SPEC026-cd-ripping-passages.md)).
   Built 2026-10-14: a set files and browses as one album, its discs named
   apart, a missing disc said loudly; a disc that is one piece is one passage;
   hidden audio is found and made its own passages; since 2026-10-27, one
   that leads into a track is joined to it on a tick `[SPEC-RIP-092]`. Left:
   hidden audio in a tracks-mode rip; hidden audio on a real disc, which no
   disc filed so far has had to prove the finding side.
6. ~~**The hub backup's mirror has not been written since 2026-09-26**~~
   *Resolved 2026-10-15 by `[SPEC-STAR-088]`, as the maintainer chose: the
   mirror stays on `teacherslounge`, daily, written whenever it is reachable
   by a task every quarter hour rather than only at 03:30, when it is usually
   off.* Its first catch-up is the next time `teacherslounge` is on.
7. ~~**CI cannot be read from the hub desktop**~~ *Resolved 2026-10-14: `gh` is authenticated there, and the first read found the docs workflow red since 2026-10-01 -- `check_forbidden_names.py` could not read the committed Essentia binaries, fixed the same night.* Was: `gh` was not authenticated
   there, so a red run was seen only on GitHub; the commit hook does not run this gate.

---

**Traceability:** exists to satisfy `[GOV-DOC-050]` · nothing here is a
requirement or a spec in its own right — every open item traces back to the
`REQ`/`SPEC`/`GDE` tag in the document it's linked from.
