# SPEC024: DAO Segmentation Cascade

**Design Specification — Tier 2 · PROVISIONAL, first pass**

How Vipunen splits a disc-at-once (DAO) capture — one continuous file holding
many tracks — into passages, when nothing already marks where one track
ends and the next begins. This is Lempi's own contract for what
`[SPEC-SA-070]` named and deferred: reproduce McRhythm's cascade, but as a
requirement independently derived, not a port of McRhythm's implementation.

> **Status.** Stages 2–5 and the review queue (§7) below are built against
> `[REQ-LIB-200..215]`. The expected track count and per-track durations
> that drive the cascade are supplied by hand this pass (`--expect`), not
> derived automatically — the 7-strategy MusicBrainz edition search that
> would supply them without a human is deferred, §8. **Partial
> re-verification, 2026-09-03:** `segment_dao.py --validate --cascade`
> against a 40-file sample of the real library — 40/40 exact track count
> (100%, versus the prior threshold-sweep-only baseline's 80%), 94% of
> boundary starts within 2s. All 40 resolved at Stage 2 alone, so DP
> assembly, the RMS fallback and extra-track merging are proven only by
> synthetic unit tests so far, not yet by a real file that actually needed
> them — the full 188-file population `[GOV-SRC-020]` calls for has not
> been run (it costs roughly two hours of decode time).

> **Related:** [SPEC007 §6](SPEC007-vipunen-architecture.md#6-segmentation--amplitude-s2-s6--provisional)
> for where this fits in Vipunen's pipeline · [SPEC021](SPEC021-waveform-boundary-editor.md)
> for the review/edit UI this hands off to · inherited
> [MCR-SPEC033 Album Matching](../inherited/mcrhythm/MCR-SPEC033-album_matching.md)
> for the source material · [ROADMAP §3](../ROADMAP.md#3-rearchitecture--whats-still-ahead)

---

## 1. What this replaces

Today, `tools/segment_dao.py` is a threshold-*sweep* silence detector only:
given an expected track count, it tries a small grid of silence thresholds
and keeps whichever produces that count, with no fallback when none does.
Measured against the library's own 188 already-segmented files: 80% exact
track-count match, 94% of boundary starts within 2s. No dynamic-programming
assembly, no RMS-based fallback when silence detection itself fails
(vinyl, cassette, live recordings), no merging of over-detected tracks, and
no MusicBrainz edition search at all — the expected count is always given
by hand.

**`[SPEC-SA-115]` The target is McRhythm's cascade, not McRhythm's
implementation.** [MCR-SPEC033](../inherited/mcrhythm/MCR-SPEC033-album_matching.md)
is ACTIVE DESIGN INPUT, not a Lempi specification `[INH-HAZ-010]` — it
describes a six-microservice architecture and a 71K-line ingest codebase
this project already rejected wholesale `[GDE-MCR-030]`, `[GDE-ARC-010]`.
What's ported below is the four reproducible cascade stages' math, adapted
to Vipunen's own single-process, staged-and-resumable pipeline
`[SPEC-SA-020]`, `[SPEC-SA-105]` — not its source, which was never imported
and is unverifiable from this repository (`stages/stage2.rs` and similar
citations in MCR-SPEC033 point at code that does not exist here).

---

## 2. Stage 2 — Parameter Grid Search

**`[SPEC-SA-116]`** Tests silence-detection settings across a grid —
15 thresholds (-80dB to -30dB, in ~3-4dB steps) × 12 minimum silence
durations (0.1s to 3.0s) = 180 combinations — for the one whose resulting
span count matches the expected track count.

| Outcome | Action |
| :--- | :--- |
| exact match | accept, 100% confidence, cascade ends here |
| within ±1 | proceed to Stage 3 (DP assembly) |
| ≥65% of tracks correct | accept outright rather than falling through further |
| below 65% | proceed to Stage 4 (RMS fallback) |

The 65% floor and the grid bounds are McRhythm's own, calibrated against its
2024-vintage 193-album corpus (`AM-STG2-040`, lowered there from an earlier
80%) — carried forward as a starting point, not a re-derived Lempi figure;
§8 covers re-verification.

**`[SPEC-SA-117]` Windowed dB-profile optimization, not 180 decodes.**
Decoding the file once into a windowed dB/RMS profile and cheaply
re-filtering that profile per threshold candidate — rather than invoking
`silencedetect` fresh for each of the 180 combinations — is the same
discipline this project already applies to feature extraction: never
re-decode audio to try another setting `[GDE-FBD-010]`, `[GDE-CHT-045]`.
`tools/analyze_amplitude.py` already decodes and windows a passage's audio
for its own lead-in/lead-out detection `[SPEC-SA-075]`; its decode/windowing
machinery is the first place to look for reuse before writing a second one.

**`[SPEC-SA-118]` Input: a count, or a count and durations, supplied by
hand.** `--expect` takes either a bare integer (track count only — Stage 2
alone runs, matching today's behavior exactly) or a comma-separated list of
per-track durations in seconds (count is the list's length). Stages 3 and 4
need expected *durations*, not only a count, to score against; without
them they cannot run and are skipped rather than guessed at
`[GOV-SRC-040]` — absent is not the same as zero. Deriving this
automatically, from a MusicBrainz edition search rather than a human typing
it in, is exactly what §8 defers.

---

## 3. Stage 3 — Dynamic-Programming Assembly

**`[SPEC-SA-119]`** Used when Stage 2 over-segments — N detected boundaries
against K expected tracks, N > K — from quiet passages inside a song, a
fade into a quiet intro, or a pause between movements. O(N²K): test
combinations of K−1 boundaries out of the N detected, scoring each by

```
score = Σ |detected_duration[i] − expected_duration[i]|
```

and keeping the combination that minimizes it. No confidence penalty —
this stage is exact given its inputs, the same standing as Stage 2's own
outright match.

---

## 4. Stage 4 — RMS Quiet-Spot Fallback

**`[SPEC-SA-120]`** Used when Stage 2 doesn't resolve at all — silence
detection has nothing to find, the common failure mode on vinyl, cassette
transfers and live recordings with no true silence between tracks. Search
±5 seconds around each expected boundary position for the local RMS
minimum, with the search window's own resolution adaptive to how tight the
neighborhood needs to be:

| Expected minimum track duration | Window |
| ---: | :--- |
| ≤ 0.3s | 25ms |
| ≤ 0.6s | 50ms |
| \> 0.6s | 100ms |

**Carries a 30% confidence penalty** relative to Stage 2/3's outright
matches — used for ranking when a file offers more than one plausible
boundary set, not as a reason to withhold the result from review
`[SPEC-SA-080]`.

---

## 5. Stage 5 — Extra-Track Merging

**`[SPEC-SA-121]`** Used when Stage 2 under-segments the other way — N
detected > K expected: bonus tracks, hidden tracks, or a misdetected
boundary near the album's end. Merge adjacent pairs, keeping the first
K−1 tracks intact and folding the remaining N−K+1 into one final track.
**Capped at 3 merges** — past that the detected boundaries disagree with
the expected count too badly for merging to be the right fix, and the
result should fall through to manual review rather than be consolidated
further.

---

## 6. Provenance

**`[SPEC-SA-122]` `boundary_src` reconciled to the schema's own stated
convention** `[SPEC-SC-045]` (`'computed:<algo>@<ver>'` / `'manual'` /
`'imported:<x>'`). The cascade writes `computed:segment-cascade@v1`,
replacing the current ad hoc `segment:silence-<db>dB+acoustid` string,
which packed parameters into the provenance column instead of using the
column that already exists for that.

**`[SPEC-SA-123]` Every segmentation decision lands in `ingest_decisions`**
(`stage='segment'`), closing the gap `[SPEC-SA-085]` already named and this
tool never actually filled — `segment_dao.py` writes `passages` today and
nothing else. Follows `tools/choose_release.py`'s own established shape:
`outcome` a short summary of which stage resolved it (e.g. `grid:100%`,
`grid:68%`, `rms_fallback`, `merged:2`), `confidence` the match fraction
(discounted 30% where Stage 4 supplied the result), `detail` a JSON blob
carrying what was tried and rejected — the grid points sampled, the chosen
threshold/duration, whether DP assembly or merging ran, and by how much.

---

## 7. Review Queue

**`[SPEC-SA-124]` Built.** `[SPEC021](SPEC021-waveform-boundary-editor.md)`'s
waveform editor, `boundary_reviews` table and `apply_boundary_reviews.py`
already cover correcting a passage's boundaries — nothing about accepting
or correcting a *cascade* result needs a second mechanism. What was missing
was a worklist: a segmented-but-unconfirmed passage used to be reachable
only one profile page at a time, never as "here is what still wants a
look."

`Library::segment_queue()` (`player/src/db/library.rs`), behind
`GET /segment/queue` (`player/src/web/segment.rs`) — parallel to
`[SPEC-SUI-*]`'s identification-review queue (`review_queue()`,
`GET /review/queue`): selects passages whose `boundary_src` starts
`computed:segment-cascade` and has no matching `boundary_reviews` row with
`applied_at` set — i.e., never confirmed (a `decided: bool` field on each
item distinguishes "never looked at" from "accepted or corrected, not yet
applied" — an accepted-but-unapplied passage still needs `tools/
apply_boundary_reviews.py --commit` to actually leave `boundary_src`
computed, per `[GDE-DIS-020]`'s own "deletion is deletion" discipline
applied here to state transitions: nothing here silently completes a step
`apply_boundary_reviews.py` alone performs). `Library::segment_progress()`
distinguishes never-run / found-nothing / all-done the same way the
identification queue already does, rather than only ever showing an empty
list with no explanation — though its counts are live snapshots, not
history: once a passage is actually applied, `boundary_src` becomes
`'manual'` and it leaves both the segmented and confirmed counts together,
there being no `orig_boundary_src` column to reconstruct the history from.

**`[SPEC-SA-125]` Built. "Accept as-is" is a fourth verb, alongside
correct.** The existing `/edit/:passage_id/review` POST is for a
correction — nine draft values, opened through the full waveform editor. A
queue needs a lighter path for the common case where the cascade's
boundaries are already right: `POST /segment/:passage_id/accept`
(`accept_segment()` in `segment.rs`, `PlayerStore::accept_segment()` in
`player_store.rs` — a thin wrapper over the same `record_boundary_review`
the full editor already calls) writes a `boundary_reviews` row whose
values equal the current `passages` row, without requiring the editor to
open at all — parallel to `record_review`'s `kept` decision
(`player/src/web/review.rs`) for identification, and returning the same
204/409 convention. Built in Lempi, reached from Vipunen's console the same
way the waveform editor already is, per `[SPEC-SUI-135]`'s rule that
audio-touching capabilities
live where the audio path is.

---

## 8. Open

**`[SPEC-SA-126]` The 7-strategy MusicBrainz edition search — deferred.**
What supplies Stage 2 its expected count/durations without a human typing
them in. [MCR-SPEC033](../inherited/mcrhythm/MCR-SPEC033-album_matching.md)
names seven query-broadening strategies (basic unquoted, CamelCase
splitting, fuzzy ~1 edit, wildcard fixes, fuzzy ~2 edits, per-token fuzzy,
album-only fallback), deduplication by track-count-and-duration signature,
and Jaro-Winkler ranking (`0.6×artist + 0.4×album`) — concretely specified,
but genuinely new work: real MusicBrainz query design, rate-limited network
calls, and its own accuracy measurement, separate from the cascade this
document covers. `tools/choose_release.py`'s `jaro_winkler()`/`name_match()`
and `tools/suggest_release.py`'s actual free-text MusicBrainz search
machinery (`SEARCH_BASE`/`DETAIL_BASE`, rate-limited `mb_get()`,
`musicbrainz_cache`) are the pieces to build from rather than duplicate.

**`[SPEC-SA-127]` "Stage 6" boundary refinement — no recoverable
algorithm, not attempted.** McRhythm's own post-processing pass that
re-examined boundaries after Stages 2–5, described only by its tuning
thresholds and aggregate effect
(±60s search window, cascade trigger at >30s error on 2+ consecutive
tracks, complementary-pattern tolerance ≤20s magnitude difference; 61% of
matched albums touched, up to +18.8 percentage points in one case,
`docs/inherited/mcrhythm/MCR-STAGE6_FULL_TEST_RESULTS_20260109.md`,
HISTORICAL EVIDENCE) in a document that never specifies *how* a "cascade"
or "complementary" error pattern is detected or how a replacement boundary
is computed from one, and whose source (`stages/stage2.rs:199-218`) was
never imported. Reproducing it is not possible from what survives; a
genuinely new refinement step, informed by but not claiming to reproduce
McRhythm's, is future design work — worth doing once Stages 2–5's own
accuracy is measured and a real shortfall is seen, not before.

**`[SPEC-SA-128]` Independent re-verification against Lempi's own library
— not yet performed.** No CI-portable ground-truth corpus exists in this
repository; the 188-file / 2,676-boundary population `segment_dao.py
--validate` checks against is the user's own live `lempi.db`, not a
packaged fixture set. Consistent with this project's best-effort, iterative
quality discipline `[GDE-PHS-005]`, a `--validate` run's result belongs in
a commit message or a LOG entry as a measured number, not asserted here as
a passing figure this document has not itself produced.

---

**Traceability:** `[SPEC-SA-115..128]` · derives `[REQ-LIB-200..215]` ·
supersedes the "not yet reproduced" framing of `[SPEC-SA-070]`
