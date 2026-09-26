# REQ002: Functional Requirements

**Requirements Specification — Tier 1**

What Lempi and Vipunen must do. **Supersedes REQ001**, a v1 artifact fully mined and deleted 2026-08-30 per `[GDE-DIS-010]` — its ideas either live here or are tracked as open questions in [GUIDE002 §6](../GUIDE002-rearchitecture-plan.md#6-open-questions).

Derived from six years of MuLibPlay production behaviour `[GDE-BMK-*]` and McRhythm's refined functional work `[GDE-MCR-050]`, inherited as [MCR-REQ001](../inherited/mcrhythm/MCR-REQ001-requirements.md). McRhythm's *requirements* are inherited; its *architecture* is rejected `[GDE-CHT-050]`.

> **Domain acronyms** are chosen to avoid McRhythm's REQ namespace entirely (`PI, CF, NET, UI, VER, PB, OFF, NF, CTL, SEL, QUE, AUTH, TECH, IPD, FLV, ART, PERS, HIST, ERR, XFD, DEF, AF, UQ, OV`), so a `grep` for a Lempi requirement cannot match inherited material `[INH-HAZ-020]`.

> **Related:** [SPEC023 Domain Vocabulary](SPEC023-domain-vocabulary.md) for what file/passage/recording/release/album/artist/track mean below — "recording" and "passage" are used precisely throughout; "track"/"song"/"album" appear only where they name a UI label or an informal sense, per SPEC023's own carve-out.

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1 in [REQ003](REQ003-audio-playback.md), §3 in [REQ004](REQ004-visibility-provenance.md), [REQ005](REQ005-visibility-listening-surface.md) and [REQ006](REQ006-visibility-words-and-the-rest.md), §2 and §4-8 in [REQ002](REQ002-functional-requirements.md).

> **Split on 2026-09-10.** Audio playback is now
> [REQ003](REQ003-audio-playback.md), and visibility -- which alone ran to
> 562 lines -- is [REQ004](REQ004-visibility-provenance.md),
> [REQ005](REQ005-visibility-listening-surface.md) and
> [REQ006](REQ006-visibility-words-and-the-rest.md). This document keeps the
> remaining domains, the non-requirements and the coverage gaps.
> **Android** is [REQ007](REQ007-android.md) `[REQ-AND-*]`, added 2026-09-25
> as a draft: what differs on a phone, citing these for what does not.
## 2. Program Director — `PD`

**`[REQ-PD-100]`** Select the next passage automatically, continuously, without user intervention. The queue never empties while eligible passages exist.

**`[REQ-PD-110]`** Implement MuLibPlay's weighting **as designed** `[GDE-PD-010..030]`: log-scale rotation, multiplicative artist-then-recording eligibility, hard rotation block, linear recovery ramp, seasonal occasion multipliers, length bonus, and a `minWeightLimit` floor.

> **As designed, not as shipped.** This previously read "reproduce exactly". It changed when a variable shadowing was found in the shipped code: MuLibPlay's artist recovery ramp never reached the recording weight, so a partially recovered artist has never damped its recordings `[SPEC-DIR-117]`. Lempi implements the ramp. MuLibPlay is a proven baseline, not a ceiling — six years of satisfactory listening is evidence the design is sound, not evidence that every behaviour of the binary is worth preserving.
>
> **Bit-identical reproduction is therefore no longer the acceptance test**, and could not be: the two now deliberately differ. The `GateOnly` coupling is retained so the divergence can be *measured* rather than assumed, which is the more useful check — it says how much the corrected ramp actually changes selection. Consistent with `[GDE-QUA-*]`, that measurement is diagnostic, never a pass/fail gate.

**`[REQ-PD-115]` Related recordings share a rotation.** Hearing a live take, a remaster or a compilation appearance suppresses the others `[SPEC-DIR-116]`. Every relation applies, each judged on its own play history, damped over a recovery window scaled by relation strength.

**`[REQ-PD-118]` Two master time scales — one for artists, one for recordings** — multiply every block and ramp duration `[SPEC-DIR-118]`. Range 0.0001–100.0000 to four decimal places, default 1.0000, at which they are exactly inert. They scale durations only, never weights, so *when* a passage becomes eligible is adjustable without touching *how much* it is wanted.

**`[REQ-PD-112]` Record every play, keyed by recording MBID.** Rotation is meaningless without it: an unrecorded play leaves a recording as eligible as it was before, so a long session repeats what the algorithm exists to space out.

> Recorded at the **start** of playback, not on completion. Rotation spaces out what the listener has *encountered*, and a passage skipped after ten seconds has been encountered — suppressing it for a while is the wanted behaviour. This also matches MuLibPlay, whose own note says the history structures update "as each new track finishes playing (or is put in the play queue)".
>
> Stored with `passage_id` **and** `mbid` `[SPEC-SC-095]`: the passage id is the convenience, the MBID is what survives a rescan that renumbers passages. An unidentified passage still records a play with a null MBID — it simply cannot contribute to rotation.
>
> A passage may legally hold a medley of several recordings, so the query selects the heaviest by a scalar subquery rather than a join, which would return that passage twice in every pool.

**`[REQ-PD-120]`** Select from **`radio` passages only** `[GDE-BMK-030]`.

**`[REQ-PD-130]`** Shape candidates by flavor distance `[SPEC-FD-040]` in two stages — prune against programme seeds, then order by similarity to the passage already queued — and apply randomness **last**, over the shaped pool `[GDE-PD-050]`.

**`[REQ-PD-140]`** Express a programme as **a list of exemplar passages**, not tuned parameters `[GDE-PD-040]`. "What should 10 AM sound like?" is answered by naming songs.

**`[REQ-PD-150]`** Honour user Likes and Dislikes as inputs to selection `[GDE-MCR-070]`. *How* Taste combines with seed shaping is open design `[GDE-OPN-030]`.

**`[REQ-PD-160]`** Degrade gracefully on partial flavor data: a passage with 11 known characteristics remains selectable alongside one with 71 `[SPEC-FD-040]`.

## 4. Library Building — `LIB` *(Vipunen)*

**`[REQ-LIB-100]`** Induct new music without hand labour — the reason the project exists `[GDE-CHT-020]`.

**`[REQ-LIB-110]`** Segment a multi-track file into passages and identify each against MusicBrainz `[SPEC-SA-070]`.

**`[REQ-LIB-120]`** Compute flavor locally, with no dependence on any live external service `[GDE-FEX-027]`. AcousticBrainz's API died within seven months of a successful bulk query `[GDE-MCR-045]`.

**`[REQ-LIB-130]`** Import is **incremental, resumable and interruptible** `[GDE-CHT-045]`. Adding a handful of tracks is the common case; a full library scan is the exception.

**`[REQ-LIB-140]`** Never re-decode audio to improve a classifier. Lowlevel features are cached permanently `[SPEC-SC-080]`.

**`[REQ-LIB-145]` Repair `duration_ms` from the decoded length at ingest, wherever it disagrees.** `[SPEC-SC-030]` already specifies "decoded, not header-claimed", and the migrated library violates it: **29.2% of files differ from their decoded length by more than 5 s**, 3.2% *over*-state it, and one overstates by **38.4 minutes** `[LOG-FEX-106]`.

> This is not cosmetic. Segmentation used the inflated value to create a **phantom passage** in a tail that does not exist, and the player uses `duration_ms` for lead-out timing. A field this load-bearing being wrong on a quarter of the library will keep producing symptoms that look like unrelated bugs — the extraction failure that surfaced it looked at first like an ffmpeg fault.
>
> Repair it where it disagrees, rather than always: `ffprobe` costs ~50 ms, but rewriting a correct value is churn. Passages already derived from a wrong duration need re-checking, not just the file row.
>
> **Done 2026-08-13** by `tools/repair_durations.py`, over all 5,590 files:
>
> | | |
> | :--- | ---: |
> | durations wrong by >1 s | **1,621 (29.0%)** |
> | …over-stating the file | 270 |
> | error: median / p95 / max | **35.0 s** / 234.6 s / 2301.3 s |
> | passage ends past the real audio, clamped | **453** |
> | phantom passages deleted | 1 |
>
> A **median error of 35 seconds** is not encoder rounding. The 29.0% measured over the whole library matches the 29.2% sample estimate `[LOG-FEX-106]` exactly.
>
> One consequence worth recording: `lowlevel_cache` is keyed `(audio_md5, start_ms, end_ms)` `[SPEC-SC-080]`, so clamping an end **orphans that passage's cached features**. The features were still correct — extraction had already clamped the range before analysing — so 212 rows were **re-keyed rather than re-extracted**, each matching exactly one cache row on `(audio_md5, start_ms)`. Radio-passage coverage is now **8,078 of 8,078**. Any repair that moves a passage boundary must consider the cache key, or it silently discards work.

**`[REQ-LIB-146]` Read a file's tags from wherever the container actually put them, not wherever the majority format happens to.** `ingest_folder.py`'s `probe()` asked `ffprobe` only for `format`-level tags. MP3's ID3 tags live there, so 5,490 of 5,682 `.mp3` files in the library read fine — but Ogg Vorbis comments land on the *stream* instead, and `format_tags` never sees them: **all 27 `.ogg` files in the library came back with `title`/`artist`/`album`/`track_no`/`disc_no` entirely NULL**, despite every one of them carrying a full tag set on disk.

> Found live 2026-08-31 chasing why `tools/suggest_release.py` scored 0/14 tracks matched for `Xavier Rudd/White Moth` even after being pointed at the exact right release directly by id — the release was right; the folder's own files had nothing in `file_tags` to match titles against at all.
>
> Fixed by asking `ffprobe` for `stream_tags` as well as `format_tags` in the same call, and falling back to the stream-level value per field only when the format level has nothing — a format-level tag a file genuinely has is never overwritten by a same-named stream-level one, verified by a test constructed the other way round for exactly that reason.
>
> Backfilled by `tools/backfill_file_tags.py`, scoped to a `file_tags` row that is entirely empty (the specific shape a probe that looked in the wrong place for every field produces, not "missing one optional field", which can be genuinely true of a file's real tags) — idempotent, and reports rather than retries a file that turns out to have no tags to find at all.

**`[REQ-LIB-150]`** Relocate a moved or renamed library by content, not path `[SPEC-SC-035]`.

**`[REQ-LIB-170]` Vipunen has an interface, and it shows what is known, what is not, and what it decided.** The pipeline is seven stages `[SPEC-SA-020]` whose order is recorded nowhere, `ingest_decisions` is written and read by nothing, and a folder of new music is discovered only when a person thinks to point a tool at it — a four-track EP went four months unnoticed. That is `[GDE-BMK-050]`'s undocumented ritual with better parts, and `[REQ-LIB-100]` is not met by a pipeline that works only when someone remembers it exists. Designed in [SPEC013](SPEC013-vipunen-console.md).

**`[REQ-LIB-175]` A passage's boundaries, fades and gain are editable while hearing the edit.** *(Requested 2026-08-27; built 2026-08-27.)* Automatic segmentation and amplitude analysis are estimates, not guarantees, and `[REQ-VIS-130]` already requires them reviewable and overridable through a waveform view. What was missing was the concrete shape of that view — start, end, lead-in, lead-out and gain, each draggable, each auditioned by playing the change back before it is kept. Designed in [SPEC021](SPEC021-waveform-boundary-editor.md); built in Lempi and reached from Vipunen's profile page, per `[SPEC-SUI-135]`.

**`[REQ-LIB-180]` A recording, artist, release or track id can be corrected by searching MusicBrainz directly, not only by choosing among fingerprint-suggested candidates.** *(Requested 2026-08-27; built 2026-08-27, artist and recording only — release and track search remain designed, not built, per [SPEC010 §3](SPEC010-identification-review.md#3-searching-musicbrainz-directly).)* `[REQ-LIB-165]`'s review queue settles the common case — AcoustID suggests the right answer and a person confirms it — but has no path for the others: self-released audio, a remaster AcoustID has never indexed, or a *credit* that is simply wrong (the right recording, filed under the wrong performer) while the recording id itself is fine. A person must be able to open the candidate's own MusicBrainz page to see what an id actually names before trusting it, and to search MusicBrainz by name when no suggested candidate is right. Designed in [SPEC010 §3](SPEC010-identification-review.md#3-searching-musicbrainz-directly).

**`[REQ-LIB-185]` An edit applied on one installation can reach another that already holds the same music, without a full library replacement.** *(Requested 2026-08-27; built 2026-08-27.)* The bundle transport (`[SPEC-SUI-095]`) carries new audio and its derived facts, but a receiver that already holds an `audio_md5` treats it as fully present and applies nothing further to it — correct for new music, useless for a correction to a track both installations already have. Designed in [SPEC006 §9](SPEC006-data-flow-and-portability.md#9-syncing-an-applied-edit-to-a-remote-installation).

**`[REQ-VIS-265]` A track can be flagged "for review" from the play-history page, on or off at any time.** *(Requested 2026-08-27; built 2026-08-27.)* Hearing something wrong — a misidentified track, a boundary that clips a note, a credit that names the wrong performer — happens while listening, not while inducting, and there was no way to mark it for later without leaving the player. One checkbox per row, keyed by recording where the play had one and by passage where it did not, since an unidentified track is exactly the case most worth flagging. A plain toggle, not a decision: nothing is applied, nothing is refused, checking and unchecking are the same call with the state reversed.

**`[REQ-VIS-270]` A passage's own facts are reachable from Lempi's own browse listing, on an appliance with no Vipunen at all.** *(Requested 2026-08-31; built 2026-08-31.)* Vipunen's profile page already shows this for the desktop; lempi02w has none of it. An info link on each browse row opens `/passage/:id`, unconditional like `/browse`/`/why` beside it -- span, lead/gain/fade, boundary source, every recording this passage names with its credited artist(s), and its own `album`/`radio` sibling `[GDE-BMK-030]` if the file has one. Deliberately narrower than Vipunen's own page: no decision history, no MusicBrainz release candidates -- nothing that would need this build to reach out over a network `[SPEC-SA-100]`'s own boundary already refuses to cross the other way. A read against the same database `browse`/`why_for` already query, no decoder, no allocation that scales with the library -- the same cost class already running on a Pi Zero 2W today, not a new one.

**`[REQ-VIS-275]` Whether a reviewed edit has reached the library at all is blatantly obvious, not something a person has to already know to check.** *(Requested 2026-08-31; built 2026-08-31.)* Found live: a boundary edit saved in Lempi's own editor read as identical to one already pushed to lempi02w, from this very console's profile page -- `boundary_reviews.applied_at IS NULL` was never surfaced anywhere, so the page showed the same thing whether an edit was a live fact or a draft nobody had folded in yet. A warning banner now leads a passage's own profile page, above everything else, whenever `id_reviews`/`boundary_reviews`/`artist_reviews` carries an unapplied row for it, naming the exact command (`tools/apply_reviews.py` / `tools/apply_boundary_reviews.py --commit`) that closes the gap -- not a button: both tools' own long-standing posture is "an edit changes what a passage *is*, and the library is Vipunen's to write, not a web click's," and this does not relitigate that. A second, global count (`GET /api/pending`) rides in every page's own header via `console.js`, since every page already loads it -- the one place a badge actually reaches all of them, not just the page someone happened to already be on.

**`[REQ-VIS-280]` A passage's own profile page shows whether it is flagged "for review" — locally, on lempi02w, and against what the page itself is showing — and offers to clear it everywhere at once.** *(Requested 2026-09-01; built 2026-09-01.)* Two vivid dots, not the muted `.pill` styling used elsewhere on this page: shown/local and lempi02w, green when they agree, yellow when lempi02w's own answer differs — `[SPEC-DF-115]`'s own point that lempi02w's flags can change with nothing to do with Vipunen at all. An "unflag" button clears every plausible subject `[SPEC-DF-112]`'s own `clear_flags_for()` shape already argues for (the passage itself, and every recording currently linked to it) on both installations in one action.

`[REQ-LIB-190]`'s rule is not relitigated: Vipunen still never writes `listener_flags`. The button *signals* Lempi instead — the same `POST /history/flag/:kind/:id` route the play-history page's own checkbox already calls, co-resident over plain HTTP, lempi02w's over one `ssh ... curl` round trip, never a service interruption since nothing here writes to either database directly `[SPEC-DF-123]`.

**`[REQ-VIS-285]` A displayed title and artist reach an editing panel for the recording or artist they name, in the now-playing row, the queue, and (Lempi skin) history.** *(Requested 2026-09-04; built 2026-09-04.)* Designed in [SPEC029](SPEC029-listener-preference-editing.md). Only where a real mbid exists — unidentified audio or an uncredited artist stays plain text, the same case that already leaves the name itself unbadged.

**`[REQ-VIS-290]` The panel opened by `[REQ-VIS-285]` edits `listener_preferences.rotation`/`recovery`/`restraint` for that subject, and can reset any one field back to the Program Director's own default.** *(Requested 2026-09-04; built 2026-09-04.)* Designed in [SPEC029](SPEC029-listener-preference-editing.md) — MuLibPlay's own hand-tunable cooldown/preference, migrated into Lempi's schema unchanged but, until this, reachable by nothing.

**`[REQ-VIS-295]` A person can reconcile `listener_preferences` between two installations on request, moving only the subjects that actually differ, in whichever direction is newer, and never onto a library missing the artist or recording altogether.** *(Requested 2026-09-04; built 2026-09-04.)* Designed in [SPEC030](SPEC030-preference-sync.md) — reached from the console's existing `/flags` "Sync with a remote" section, the same `remote_config` target `[REQ-LIB-190]`'s pull/push already use.

**`[REQ-VIS-300]` The panel opened by `[REQ-VIS-285]` shows how often that subject has actually been played, so the tuning above it is a judgement about something known rather than a guess.** *(Requested 2026-09-04; built 2026-09-04.)* The question a person is really answering at this panel is "do I want to hear this more or less than I have been", and until this the panel offered the sliders without the one fact that makes either direction obvious. Five rolling windows, narrowest first — past 24h, 7d, 30d, 365d, and all time — so a recording heard twice this week and a recording heard twice since 2020 stop looking alike. Broken out by who or what chose each play: an **All** row counting every play for any reason, which is the true ceiling and never less than any other row; a **User** row always present even at all-zero, since "never once put on deliberately" is itself an answer worth reading beside a high All; and one row per Program Director programme that has actually selected this subject (including the `auto` sentinel for a Director pick with no programme in force), sorted by its own all-time count. Provenance is recorded when a passage is queued (`listener_play_history.selected_by`) and never inferred when read back — history predating the column stays `NULL` forever rather than being back-annotated into a story nobody verified, and a backend that does not track it (either MPD path) contributes to All alone. Artist mode joins through `recording_artists`, so a play recorded against a recording counts toward the artist credited with it. Its own route and its own fetch: opening the preference panel never waits on this, and the table appears when it is ready and closes when that panel closes. Designed in [SPEC031](SPEC031-play-frequency.md) — written after the fact, 2026-09-10: the commit that built this named a SPEC031 that was never written, and both that document and this entry are reconstructed from the implementation and that commit message rather than from a contemporaneous specification.

**`[REQ-VIS-305]` The panel opened by `[REQ-VIS-285]` also sets a recording's "special" tags — Christmas, Winter, Summer, Children's, Profanity, Spiritual — and can drop any one back to what the library inherited.** *(Requested 2026-09-10; built 2026-09-10.)* Designed in [SPEC029 §4](SPEC029-listener-preference-editing.md) — the other half of MuLibPlay's own track editor, carried forward by the migration (41 christmasy recordings, 140 for children) and made to act again by `tools/load_occasions.py`, with nothing anywhere to tag a recording added since or correct one tagged wrong. Which specials exist is `listener_occasions`, the same rows the Program Director weighs by, so the list offered and the list that acts cannot drift apart. The listener's own value lives in `listener_characteristics` and overrides the inherited one rather than averaging with it; Reset deletes it rather than writing a zero, which would be the different claim "definitely not."

**`[REQ-VIS-310]` The panel says which recording it is editing — title, artist and release — and says it the same way wherever it was opened from.** *(Requested 2026-09-10; built 2026-09-10.)* Designed in [SPEC029 §5](SPEC029-listener-preference-editing.md). The heading was the caller's own label, and the callers do not know the same things: the queue carries no album, the runner-up list carries only a title. The server answers all three, from the same expressions a history row already uses. The caller's label still fills the heading until that lands and stays if it fails, and an uncredited artist or a recording on no release drops that half rather than showing it empty `[REQ-VIS-120]`.

**`[REQ-VIS-315]` The specials reconcile between two installations in the same action `[REQ-VIS-295]` already reconciles tunings, and a special defined on one installation becomes usable on the other.** *(Requested 2026-09-10; built 2026-09-10.)* Designed in [SPEC029 §7](SPEC029-listener-preference-editing.md) and [SPEC030 §7](SPEC030-preference-sync.md). `listener_characteristics` carries the same shape of row `listener_preferences` does — one decision, one `updated_at`, no merge history — so it takes the identical last-write-wins rule, through the identical code rather than a near-copy of it. The `listener_occasions`/`listener_occasion_points` registry travels with the values, because a value for `user.spiritual` means nothing on a database that has never heard of it; that half is **additive** — a definition the other side lacks is copied, one both sides have and disagree about is reported and left alone, since a curve is a setting somebody tuned and no clock should overwrite it. One patch, one service restart, however many tables moved.

**`[REQ-VIS-320]` Lempi's browse page reaches Vipunen's console, and starts one if none is serving.** *(Built before 2026-09-11; the exact request and build dates are not recoverable here, since this repository was seeded as a single commit `[GDE-NAM-030]`. Reconstructed 2026-09-21 from the implementation, in the manner `[REQ-VIS-300]` records for itself — there is no contemporaneous specification, and this entry should not be read as one.)* The mirror image of `[SPEC-SUI-150]`'s handoff: that one finds and starts a co-resident Lempi from Vipunen's own browser, and this finds and starts a co-resident Vipunen from Lempi's, for the one link a desktop listener curating their own library would otherwise need a terminal to follow. The page probes `/vipunen/available` on load and asks `/vipunen/ensure` to launch, and the link says what it found rather than offering a dead target `[REQ-VIS-120]`. Gated behind `vipunen-support` exactly as `/review` and `/edit` are, because Vipunen is x86-desktop-only and an appliance build has nothing to offer here — on a build without the feature the route does not exist at all, which is the same posture the other two take `[SPEC-SUI-190]`. **Availability is a question about serving, not about the port being held.** A socket check cannot tell a working console from a wedged one: a dead-but-bound Vipunen read as *absent*, a second was started beside it, and the next click started a third — three consoles `LISTENING` on `127.0.0.1:5730` with every connection refused, measured 2026-09-11, because `console.py` asks for `allow_reuse_address` and Windows permits exactly that. Both sides now ask over HTTP and the console refuses to start on a port another console already owns.

**`[REQ-LIB-190]` Vipunen lists what has been flagged, and lets a person choose one to review.** *(Requested 2026-08-27; built 2026-08-27.)* `[REQ-VIS-265]`'s checkbox is set in Lempi; this is where it is worked from. Read-only, like every other view in the console — flagging and unflagging stay Lempi's, since it is listener state `[SPEC-SC-020]` and listener state is Lempi's to write. Choosing a flagged track opens its profile page, which now offers both handoffs `[SPEC-SUI-135]`, `[SPEC-SUI-140]` name — id review and the waveform editor, the second of which had been designed but never actually linked from the page until this closed the gap. That page later gained its own unflag button `[REQ-VIS-280]` — still true to this entry's own rule: the console signals, it never writes `listener_flags` itself.

**`[REQ-LIB-195]` A track flagged on one installation can be reviewed on another that shares an overlapping library, and the resulting correction can return to where the flag was made.** *(Requested 2026-08-27; built 2026-08-27.)* `[REQ-VIS-265]`'s flag is set from lempi02w's own play-history page — the appliance a listener actually hears something wrong on — but the appliance carries no Vipunen to act on it, and `[REQ-LIB-190]`'s list only ever reads a console's own local `listener_flags`, blind to what a *different* installation flagged. `[REQ-LIB-185]`'s sync answers "a correction reaches a remote installation" once the desktop already knows which track; this closes the leg before it — the name of the track — and, once reviewed, carries the correction back by the same mechanism, rather than requiring a whole-library replacement `[PI005 §1]` already found the wrong tool for exactly this appliance. Designed in [SPEC022](SPEC022-flag-and-edit-sync.md) `[SPEC-DF-107..118]` (split out of SPEC006 §10 once that document passed its own line limit).

**`[REQ-LIB-200]` `[REQ-LIB-110]`'s segmentation falls back through stages rather than accepting the first attempt.** A single silence-threshold guess is not a cascade; grid search, dynamic-programming assembly, an RMS quiet-spot fallback and extra-track merging are each tried in order, whichever resolves a file with the fewest assumptions. Designed in [SPEC024](SPEC024-dao-segmentation-cascade.md).

**`[REQ-LIB-205]` Every segmentation decision is recorded, not just applied.** Which stage matched, its confidence, and the candidates it did not choose — the same `ingest_decisions` discipline `[SPEC-SA-085]` every other Vipunen stage already keeps, closing the one place segmentation was silent about how it decided.

**`[REQ-LIB-210]` A cascade result is always reviewable and overridable, and a human's correction is permanent.** Restates `[REQ-LIB-175]`/`[SPEC-SA-080]` explicitly for the cascade: `boundary_src='manual'` permanently outranks recomputation `[SPEC-SC-045]`, so accepting the machine's split and correcting it are both first-class, and neither is silently undone by a later re-run.

**`[REQ-LIB-215]` An unconfirmed automatic segmentation is discoverable as a worklist, not only reachable one profile page at a time.** Segmentation without a way to find what still needs a look is a machine nobody checks. Designed in [SPEC024](SPEC024-dao-segmentation-cascade.md) §7.

**`[REQ-LIB-220]` Vipunen can rip a physical CD directly, obtaining exact track boundaries from the disc's own table of contents rather than inferring them from audio content.** Ground truth beats a guess: a disc-at-once rip's own TOC states boundaries to the sector, which is what `[REQ-LIB-200]`'s cascade otherwise exists to approximate from silence and duration alone. Designed in [SPEC025](SPEC025-cd-ripping.md).

**`[REQ-LIB-225]` TOC-derived boundaries outrank the cascade, and a human's correction still outranks either.** The same ladder `[SPEC-SC-045]` already establishes for `manual` over `computed`, extended one rung: `imported` (a disc's own TOC) sits above `computed` (the cascade's inference) and below `manual` (a person's own review) — ground truth beats a guess, and a person who has actually listened outranks both.

**`[REQ-LIB-230]` Ripping is user-initiated and interactive; a read failure is reported, never silently guessed at.** A scratched disc, a drive that needs a disc swapped, or a track that would not verify are told to the person doing the ripping, the same `[PI3-API-030]` discipline against a status implying success it did not earn.

**`[REQ-LIB-235]` When the disc's TOC resolves a MusicBrainz release directly, that release's own track metadata is used rather than falling back to per-track audio fingerprinting.** A Disc ID match identifies the exact pressing from the disc's own geometry; AcoustID's per-track guess-and-confirm remains the fallback for a disc that resolves no release this way (a self-burned compilation, an unreleased recording), not the first resort when a stronger answer is available. Designed in [SPEC028](SPEC028-cd-ripping-identification.md) §1.

**`[REQ-LIB-240]` A ripped file is encoded before it enters the library, and any lossless intermediate is temporary unless the user asks to keep it.** Consistent with `[REQ-VIS-205]`'s standing rule for anything written beyond what the library strictly needs: a working WAV/FLAC exists only long enough to produce the library's own encoded copy, and an archival lossless copy is opt-in, never a silent default.

**`[REQ-LIB-245]` The rip's read-verification aggressiveness is a user-adjustable setting, defaulting to a level that trades a little resilience for materially faster rips.** Designed in [SPEC025](SPEC025-cd-ripping.md) §5 — matching the underlying tool's own 0–3 scale, defaulting to **2**.

**`[REQ-LIB-250]` The ripping tool is optional and user-installed; its absence degrades this one capability, never the rest of Vipunen.** EAC on Windows, `cdrdao` on Linux `[SPEC-RIP-020]` — neither is a hard dependency of anything else Vipunen does, so "Rip a CD" is offered as unavailable with a plain reason when its platform's tool is not found, the same posture `analyze_amplitude.py` already takes toward a missing `ffmpeg`, never a crash or an opaque failure mid-rip.

**`[REQ-LIB-255]` Real audio hidden in a pregap or at `INDEX 00` gets its own passage, and may additionally get a second passage pairing it with the adjacent track.** Designed in [SPEC026](SPEC026-cd-ripping-passages.md) §1 — the hidden span is never silently dropped by a naive `INDEX 01`-only split, and a passage covering the hidden audio together with the track it leads into is a legal second row over the same span, not a competing representation.

**`[REQ-LIB-260]` A multi-disc set ripped disc-by-disc is one file per disc, all resolving to one shared MusicBrainz Release, segmented one passage per track by default.** Designed in [SPEC026](SPEC026-cd-ripping-passages.md) §2 — `release_recordings.disc` `[SPEC-SC-048]` already carries medium order, so no schema change is needed; a wider passage spanning a whole disc or a side may additionally be created, the same pattern `[REQ-LIB-255]` establishes for hidden audio, applied at a larger granularity.

**`[REQ-LIB-265]` Creating a folded-in or wider passage is always the user's choice, made easy rather than made automatically.** Designed in [SPEC026](SPEC026-cd-ripping-passages.md) §§1-2 — Vipunen detects and offers the candidate span (hidden audio plus its neighbour, a whole disc, an album side) as a one-action confirmation, the same discover-then-let-a-person-confirm shape `[SPEC-SUI-215]`'s release suggestion already uses; nothing about the audio itself is trusted to decide whether a listener wants the wider passage to exist.

**`[REQ-LIB-270]` CD-TEXT is the default source for a ripped disc's title/artist/track metadata when the disc carries it; a resolved MusicBrainz match remains available as a one-action alternative.** Designed in [SPEC028](SPEC028-cd-ripping-identification.md) §2 — an unmeasured default per `[GOV-SRC-020]` (no corpus of discs carrying both exists to rank them by disagreement rate), following the same disc's-own-data reasoning `[REQ-LIB-220]` already uses for boundaries rather than a reliability finding. A disc with no CD-TEXT shows the MusicBrainz result alone, unchanged from today.

**`[REQ-LIB-275]` A multi-disc rip session prompts for each next disc but never blocks on one being unavailable or ripped out of order.** Designed in [SPEC026](SPEC026-cd-ripping-passages.md) §2 — the operator may skip a disc that isn't at hand, insert discs out of sequence, or stop early, and whatever was actually ripped is recorded as part of the set. Which disc a physical disc actually is comes from its own Disc ID lookup (`[REQ-LIB-235]`), not from session sequence, so an accidental duplicate or an unrelated disc is identified rather than assumed.

**`[REQ-LIB-280]` No optical drive detected degrades this one capability exactly like no ripping tool found.** Designed in [SPEC025](SPEC025-cd-ripping.md) §5a — checked at the same point as `[REQ-LIB-250]`'s tool check, so "Rip a CD" is offered as unavailable with a plain reason rather than failing on click, regardless of which of the two is actually missing.

**`[REQ-LIB-285]` A track that fails read verification does not abort the rip; the disc is ripped best-effort and the failure is recorded, never silent.** Designed in [SPEC025](SPEC025-cd-ripping.md) §5a — ripping continues with the remaining tracks after the tool's own retries are exhausted, and the failed track is written anyway rather than dropped, with an `ingest_decisions` row marking it for review the same way an unconfirmed segmentation already is (`[REQ-LIB-215]`).

**`[REQ-LIB-290]` A drive that stalls mid-rip is reported and retried per track, without discarding tracks already ripped.** Designed in [SPEC025](SPEC025-cd-ripping.md) §5a — the same failure shape as a verification failure (`[REQ-LIB-285]`), since `ingest_decisions` rows are per-track; a stall on one track does not implicate the ones ripped before it.

**`[REQ-LIB-295]` When a Disc ID match is ambiguous — more than one plausible release — the person ripping picks from the real candidates, not a silently top-ranked guess.** Designed in [SPEC028](SPEC028-cd-ripping-identification.md) §3 — the disc itself, in the ripping person's hand, is a tiebreaker no ranking heuristic has access to.

**`[REQ-LIB-300]` When no automated match resolves at all, the person ripping can search MusicBrainz directly or enter track/artist/album metadata by hand, rather than accept an anonymous placeholder.** Designed in [SPEC028](SPEC028-cd-ripping-identification.md) §3 — reuses the recording/artist search Vipunen already built (`[REQ-LIB-180]`) rather than a second mechanism, and is a second, independent reason to finish that endpoint's still-unbuilt release/track-search half.

## 5. Portability — `PORT`

**`[REQ-PORT-100]`** A Lempi installation with **no Vipunen** can receive derived data and use every advanced feature `[SPEC-DF-080]`.

**`[REQ-PORT-110]`** Derived data travels by embedded tag, per-file sidecar, or whole-database migration — one payload schema across all three `[SPEC-DF-065]`.

**`[REQ-PORT-120]`** Listener state **never** travels with music `[SPEC-DF-055]`. The transport carries facts about the music, never facts about the listener.

**`[REQ-PORT-130]`** Imported metadata is verified before trust: recompute `audio_md5` and discard encoding-scope claims that disagree `[SPEC-DF-070]`. *On a phone, which cannot compute `audio_md5`, by the byte hash carried in the payload instead — [REQ007](REQ007-android.md) `[REQ-AND-030]`.*

**`[REQ-PORT-140]`** Writing tags to a user's files requires informed consent, and uses temp-file → verify → atomic replace so a failed write cannot damage the library `[SPEC-DF-092]`.

**`[REQ-PORT-150]`** Listener state is exported automatically on a schedule, integrity-checked before rotation, retained generationally `[SPEC-DF-094]`. It is the only irreplaceable data in the system. *On a phone a backup must also be retrievable off it, since uninstalling deletes app-private storage — [REQ007](REQ007-android.md) `[REQ-AND-190]`.*

**`[REQ-PORT-160]`** Two or more Vipunen-capable installations can reconcile their catalogs — class A/B/C content and flags — against each other, not only against one hub. Designed in [SPEC035](SPEC035-mesh-library-sync.md) §2.

**`[REQ-PORT-170]`** What differs between two installations is discovered automatically, without a full database copy. Designed in [SPEC035](SPEC035-mesh-library-sync.md) §3.

**`[REQ-PORT-180]`** Nothing found by that discovery moves without a person approving it first. Designed in [SPEC035](SPEC035-mesh-library-sync.md) §4.

**`[REQ-PORT-190]`** Segmentation is computed only where Vipunen runs; two installations that independently ingest the same audio under different encodings each pay that cost, and neither's result is inferred onto the other. Designed in [SPEC035](SPEC035-mesh-library-sync.md) §5.

**`[REQ-PORT-200]`** A manual correction on one installation that disagrees with a manual correction on another is resolved by a person, once, and the resolution leaves both installations in agreement — indistinguishable afterward from data that never conflicted. Designed in [SPEC035](SPEC035-mesh-library-sync.md) §6.

## 6. Appliance — `HW`

**`[REQ-HW-100]`** Run continuously on a Raspberry Pi Zero 2W (512 MB) — **≤150 MB RSS** `[GDE-MCR-020]`. MuLibPlay uses 171 MB on a 1.8 GB Pi 4 `[GDE-BMK-010]`; Lempi must fit a third of the memory.

**`[REQ-HW-110]` Reach first audio quickly on power-up — best effort, and bounded by the output profile.** Management services may start afterwards.

This is deliberately **not** an absolute target, because the audio output channel determines what is achievable `[IMPL-PROF-010]`. A Bluetooth sink must associate before any audio can flow, and that cost is inherent to the channel rather than to Lempi. Two consequences:

- **`[REQ-HW-112]`** Where an output channel imposes an unavoidable startup delay, that delay is **accepted for that profile only**. It must not be allowed to set the standard for profiles that do not share it.
- **`[REQ-HW-114]`** Profiles without such a delay — I2S DAC, USB DAC, HDMI — must be configurable for the faster boot, **sacrificing Bluetooth capability** to do so. Fast boot and Bluetooth are alternatives, not a compromise to be split.

**`[REQ-HW-120]`** Survive repeated hard power loss without database corruption.

**`[REQ-HW-130]`** The player is portable and reaches ARM. Vipunen need not `[SPEC-SA-018]`.

**`[REQ-HW-140]` Desktop and server hosts are first-class targets, not a by-product of the appliance.** Lempi runs on Windows, Linux and macOS as an ordinary application `[GDE-CHT-045]`. The Pi Zero 2W is the *constraining* target, not the only one.

**`[REQ-HW-145]` Every supported target is tested, not merely compiled.** `build/verify-targets.sh` runs the suite on Linux x86_64, Linux aarch64 (under emulation) and the host. Compiling is not testing: an audit found aarch64 had only ever been *built*, Linux x86_64 never built at all, and the suite only ever *run* on Windows.

**`[REQ-HW-147]` At least one verification must use a real audio device.** A null sink reports no device rate and therefore cannot detect a sample-rate fault. This is not hypothetical: playback ran **8.8% fast — about 1.5 semitones sharp** — because a 48 kHz device met a 44.1 kHz library with the resampler unwired, and every prior test had used a null sink.

**`[REQ-HW-150]` Where the library lives on its own partition, it stays read-only outside a deliberate, bounded import — never open the rest of the time.** Adding content is an attended operation with a clear start and end, not a standing state; the window a mistake or a power loss could land in should be exactly as long as the import itself, not the appliance's whole service life. Built for `bose` as `BosePi/attended-import.sh` `[IMPL-BOS-150]`.

**`[REQ-HW-155]` Where the system partition is made read-only, a way back that doesn't require re-imaging exists for the case that still boots.** A card swap is an acceptable answer to "the system will not boot at all"; it is not an acceptable *only* answer to "the system boots fine and a person wants to write to it again." Built for `bose` as the flag-file escape hatch `[IMPL-BOS-160]`, checked at boot on either of two markers: one on the partition that stays writable *from the running system* through lock-in, one on the partition that stays writable *from an external reader* even though the running system itself can no longer write it.

## 7. Non-Requirements

**`[REQ-NEG-100]`** Lempi does **not** stream audio to remote devices `[REQ-AUD-150]`, require any live external service at playback time, or modify audio data `[REQ-AUD-100]`.

**`[REQ-NEG-110]`** Vipunen does **not** play audio, run on the appliance, or hold listener state `[SPEC-SA-100]`.

**`[REQ-NEG-120]` Neither Lempi nor Vipunen integrates with personal-cloud accounts.** No Google Drive, Gmail, Calendar, or equivalent from any vendor — not for storage, not for scheduling, not for identity. This is a scope boundary, not an unimplemented feature.

> Three reasons it stays closed. **Playback must not depend on a reachable service** `[REQ-NEG-100]`; an appliance that cannot play music because a token expired has failed at its only job. **Network cost is justified per data class** `[SPEC006 §B]` — identification earns its lookups because MBIDs cannot be derived locally; nothing in playback, selection, or flavor can make that case. **Listener history is the user's** `[SPEC-DF-090]`, which is why class-D export exists at all; routing it through a third-party account inverts that.
>
> The near-miss worth naming: occasion weighting `[SPEC-DIR-130]` is seasonal, computed from month and day against the system clock (`[SPEC009]` §3, `[SPEC-DIR-130..137]`). It is *not* a calendar integration and must not become one — reading real appointments would make selection fail when a remote service is unreachable.
>
> Off-machine backup of a class-D export to cloud storage is a legitimate thing a **user** may choose to do with a file Lempi has already written. Lempi does not do it for them, and nothing in the system may assume it happened.

---

## 8. Coverage Gaps

Areas where [MCR-REQ001](../inherited/mcrhythm/MCR-REQ001-requirements.md) has substantial requirements that Lempi has **not yet adopted or rejected** — recorded so the gap is visible rather than accidental:

| McRhythm area | Status |
| :--- | :--- |
| User identity / authentication (`AUTH`, 13 reqs) | Undecided. MuLibPlay is single-user with no auth. |
| Multi-user coordination (`PERS`, `UQ`) | Undecided; likely out of scope for v1. |
| Network status & offline operation (`NET`, `OFF`, 66 reqs) | Partially implied by `[REQ-LIB-120]`; not enumerated. |
| Three build tiers Full/Lite/Minimal (`VER`, 19 reqs) | Superseded by the Lempi/Vipunen split `[GDE-ARC-010]`. |
| Error handling (`ERR`, 5 reqs) | Not yet enumerated. |

---

**Traceability:** `[REQ-AUD-100..NEG-110]` · supersedes `REQ001` · derived from `[GDE-BMK-*]`, `[GDE-PD-*]`, `[GDE-CHT-*]`, inherited `MCR-REQ001`
