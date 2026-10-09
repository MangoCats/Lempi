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
| CD ripping (built 2026-09-04, single-disc, person-assisted — verified against a real disc) | [SPEC025](spec/SPEC025-cd-ripping.md) | [§8 Open](spec/SPEC025-cd-ripping.md#8-open) |
| CD ripping: how Vipunen drives a GUI-only Windows tool (decided — person-assisted, built) | [SPEC027](spec/SPEC027-cd-ripping-windows-automation.md) | whole document |
| CD ripping — hidden-audio & multi-disc passage representation (designed, not yet built) | [SPEC026](spec/SPEC026-cd-ripping-passages.md) | [§3 Open](spec/SPEC026-cd-ripping-passages.md#3-open) |
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
| Deploy script naming and target signposting (renamed 2026-09-11; config persistence built; appliances built without `vipunen-support` from 2026-09-12 `[GDE-DEP-098]`) — **open: a worktree cross-compile cannot stamp its commit `[GDE-DEP-100]`, and the final cross-check misreports why `[GDE-DEP-110]`** | [GUIDE011](GUIDE011-deploy-script-naming.md) | [§7 Configuration, and what is still open](GUIDE011-deploy-script-naming.md#7-configuration-and-what-is-still-open) |
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
| Wi-Fi watchdog — a future feature, decided by how often a link dies (the travel failover of [SPEC061](spec/SPEC061-travel-wifi-failover.md) acts when the link is lost; a link that stays up while its gateway stops answering is still this row's case) | [FLEET001](../fleet/FLEET001-the-fleet.md) | [§4. Standing issues](../fleet/FLEET001-the-fleet.md#4-standing-issues) `[FLT-ISS-015]` |
| Framebuffer UI — built 2026-09-07, all seven phases; seek on the touch screen not yet confirmed | [SPEC042](spec/SPEC042-framebuffer-implementation-plan.md) | [§8. Implementation plan, phased](spec/SPEC042-framebuffer-implementation-plan.md#8-implementation-plan-phased-against-the-open-items-above) |
| Star sync — the hub merges and distributes both halves | [SPEC046](spec/SPEC046-star-sync.md) | [§11. Open](spec/SPEC046-star-sync.md#11-open) |
| Mesh membership and trust | [SPEC049](spec/SPEC049-mesh-membership-and-trust.md) | [§6. Order of work, and what is open](spec/SPEC049-mesh-membership-and-trust.md#6-order-of-work-and-what-is-open) |
| Mesh without ssh — the signed transport | [SPEC054](spec/SPEC054-mesh-without-ssh.md) | [§7. Open, to settle when built](spec/SPEC054-mesh-without-ssh.md#7-open-to-settle-when-built) |
| Fleet configuration kept private (`[SPEC-FCP-010]` awaits the private repository) | [SPEC055](spec/SPEC055-fleet-configuration-kept-private.md) | [§4. Open](spec/SPEC055-fleet-configuration-kept-private.md#4-open) |
| CD import | [SPEC056](spec/SPEC056-cd-import.md) | [§11. Open](spec/SPEC056-cd-import.md#11-open) |
| Holding a passage back (whole-album play, `[REQ-PD-127]`, not built) | [SPEC057](spec/SPEC057-holding-a-passage-back.md) | [§6. Open](spec/SPEC057-holding-a-passage-back.md#6-open) |
| Catalogue patch by natural key | [SPEC058](spec/SPEC058-catalogue-patch-by-natural-key.md) | [§9. Open](spec/SPEC058-catalogue-patch-by-natural-key.md#9-open) |
| A node's own occasion policy, a profanity ceiling first (designed, not scheduled) | [GUIDE036](GUIDE036-node-occasion-policies.md) | [§2. The design](GUIDE036-node-occasion-policies.md#2-the-design-a-nodes-own-policy) |
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
2. **Wall Art / Kiosk display mode — dropped, or merely unrevisited?** The pre-rearchitecture plan (`docs/user-interface.md`, now deleted) specified a fullscreen wall-tablet mode: large album art, clock, upcoming-track cards, OLED/LCD burn-in protection. Grep for `wall.art|kiosk|burn.in` across `player/` and `tools/` returns nothing — it was never built, and the current skin model (`lempi`/`mulibplay`/`winamp`, all document-shaped, `[REQ-VIS-160]`) has no kiosk-style skin among them. Nothing in `REQ002` accepts or rejects it. If wanted, it is a fourth skin under the existing contract; if not, this line is where that should be said.
3. **The Phase 7 feature list — dropped, or merely unrevisited?** The old roadmap's final phase (`docs/roadmap.md`, now deleted) named station-ID/jingle injection between tracks, news/weather TTS announcements, and MQTT/smart-home hooks. None appear in `REQ002` or any current `SPEC`, and none exist in code. This is *not* the same question as scrobbling — `[SPEC-MPD-100]` already, deliberately, declines that one for the MPD guest path specifically, reasoning that guest clients already scrobble. The other three were simply never revisited after the rearchitecture and carry no decision either way.
4. **Library-browse pagination — was the REQ001-era page-size selector dropped on purpose?** The deleted `REQ001`'s `[REQ-UI-020K]` specified a page-size dropdown (`10`/`25`/`50`/`100`/`250`) with dynamic Prev/Next controls. The built `/browse` route (`player/src/web/browse.rs`, split out of `web.rs` 2026-09-02) instead caps every response at a flat 2,000 rows with no selector `[REQ-VIS-180]`. That may be the right call for a LAN player with a "Built for a phone" design brief — a flat cap is simpler and 2,000 rows is generous — but it was never stated as a deliberate simplification, only as an absence.
5. **Mesh sync between N Vipunen peers — superseded, or still wanted?** [SPEC035](spec/SPEC035-mesh-library-sync.md) and [SPEC040](spec/SPEC040-peer-registry-and-review-ui.md) designed peers that each ingest and sync with one another; the diff, its review and conflict resolution, the peer registry and the console's `/mesh` page are built. Since 2026-09-26 the household syncs as a star instead — one hub, every other node a spoke ([SPEC046](spec/SPEC046-star-sync.md)). Whether the rest of the peer design is still wanted, or superseded by the star, is undecided.

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

## 5. After the 2026-10-08 remediation

The review of 2026-10-08 and the remediation that followed it closed most of
what they found. What they left, reviewed 2026-10-09, in the order suggested
then. Items that belong to one document are linked, not copied.

1. **Roll the remaining appliances forward.** `bose` and `lp3-wifi` run builds
   from 2026-10-05, before the restore fixes, the live Director, the fallback
   and the audible timeline; `lempi02w` runs the latest
   ([FLEET001](../fleet/FLEET001-the-fleet.md) `[FLT-RUN-010]`). Both have
   overlay roots. Do the two deferred device checks of `[FLT-ISS-070]` — a
   restore from the Listening backups row, and the play-rate check — on the
   way. `lp3-wifi` is one star-sync run behind on preference edits, and
   `bose` and `smartboardpc` await the catalogue patch by natural key
   `[FLT-ISS-060]`.
2. **A restore does not re-point `player_state` or `player_queue`.** Only the
   play history follows recordings through a renumbering `[REQ-LIB-160]`, so
   after a rebuild the resume point and the remembered queue can name other
   songs.
3. **`switch::carry_queue` discards drops waiting before a handoff.** A
   passage that failed to open just before a switch keeps its queueing mark
   instead of giving it back `[REQ-PD-112]`.
4. **The Program Director's open items**, [SPEC009 §9](spec/SPEC009-program-director.md#9-open):
   first `[SPEC-DIR-240]`, a passage that crashed the player coming straight
   back (a cheap guard, worth most on appliances powered by their speaker);
   then `[SPEC-DIR-235]` and `[SPEC-DIR-245]` together, a restored queue
   re-weighed and its provenance kept.
5. **The now-playing panel while MPD is live**, [architecture.md §10](architecture.md#10-known-gaps):
   the MPD backend knows its head and position, and could publish them
   through the snapshot fields the panel already reads.
6. **How a seek feels.** A seek cuts the ring with the skip's own fade (2 s)
   and lead (0.5 s); a shorter fade of its own may feel crisper. The
   maintainer found it "functional, could be smoother", 2026-10-09: a question
   for the ear, not for a test.
7. **Errors that read as zero, beyond the backups.** Review finding O-04 was
   fixed in `backup.rs` only; the census, the pool count and other figures
   shown to a person still read a failed query as 0.
8. **The default skin and the settings layout** (review U-01, U-02), left as
   they are by the maintainer's decision of 2026-10-08. Worth revisiting once
   the rest settles: the settings list has grown since, with the Listening
   backups row.

---

**Traceability:** exists to satisfy `[GOV-DOC-050]` · nothing here is a
requirement or a spec in its own right — every open item traces back to the
`REQ`/`SPEC`/`GDE` tag in the document it's linked from.
