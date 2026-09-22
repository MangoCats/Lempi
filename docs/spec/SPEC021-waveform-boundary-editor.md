# SPEC021: Waveform Boundary Editor

**Design Specification — Tier 2 · Built 2026-08-27, see [IMPL006](../IMPL006-vipunen-editing-workflows.md) Stages 6–8**

How a passage's start, end, lead-in, lead-out, fade-in, fade-out and gain are reviewed and corrected by hand, hearing the edit as it is made `[REQ-LIB-175]`, `[REQ-VIS-130]`, `[SPEC-SA-080]`.

> **Related:** [SPEC013 §3.4](SPEC013-vipunen-console.md#34-handoff--the-players-own-pages-inside-vipunens-workflow) for where this is reached from · [SPEC010](SPEC010-identification-review.md) for the sibling feature it was designed alongside · [SPEC008 §3](SPEC008-database-schema.md) for `passages` · [`player/core/src/fade.rs`](../../player/core/src/fade.rs) for the fade curves this must not disagree with (design context in the inherited [`MCR-SPEC002-crossfade.md`](../inherited/mcrhythm/MCR-SPEC002-crossfade.md), `[INH-*]` — its curve *formulas* are not what `fade.rs` implements, only its lead/fade orthogonality (`[XFD-ORTH-010]`) — Lempi stores each as an independent duration from `start_ms`/`end_ms` `[SPEC-SC-043]`, not McRhythm's six ordered absolute points, and enforces no cross-pair ordering: fade-in/fade-out may legitimately overlap, resolved by `Envelope`'s gain multiplication rather than by clamping).

---

## 1. Where it lives, and why that is settled already

Where this lives was decided already, in `[SPEC-SUI-135]`: built in Lempi, reached from Vipunen's profile page, behind `vipunen-support` `[SPEC-SUI-190]`. This document is the "how", not the "where."

**It does not need Lempi's real-time mixer.** The first instinct — reuse the engine's own decoder and crossfade path so the preview is provably what production plays — turns out not to be the cheapest way to get that guarantee, and it puts editing traffic through the same loop `[PI3-API-030]` exists to keep uninterruptible. What the editor actually needs is: the raw audio bytes, and the same fade-curve arithmetic the engine uses, applied client-side. Lempi already has to serve the first; the second is a few lines of `fade.rs` ported to JS once and covered by a shared test vector, not a new mode threaded through the tick loop.

**It does not write `passages`.** `[SPEC-SC-045]`'s `boundary_src = 'manual'` outranking automation is a promise about the *library*, and the library is Vipunen's to write `[SPEC-SA-015]`. The identification-review feature already solved this exact problem for recording ids — a decision table Lempi owns, applied to the library by a separate, deliberate Vipunen-side step — and boundary edits use the same shape rather than inventing a second one.

---

## 2. Data model

**`[SPEC-SUI-200]` A new table, `boundary_reviews`, shaped like `id_reviews`:**

```sql
CREATE TABLE boundary_reviews (
    passage_id      INTEGER PRIMARY KEY,
    start_ms        INTEGER NOT NULL,
    end_ms          INTEGER NOT NULL,
    lead_in_ms      INTEGER,
    lead_out_ms     INTEGER,
    gain_db         REAL,
    fade_in_ms      INTEGER,
    fade_out_ms     INTEGER,
    fade_in_curve   TEXT,
    fade_out_curve  TEXT,
    decided_at      TEXT NOT NULL,
    applied_at      TEXT
);
```

**`fade_*` added `[SPEC-SUI-226]`**, alongside `orig_fade_in_ms`/`orig_fade_out_ms`/`orig_fade_in_curve`/`orig_fade_out_curve` sync-baseline columns — the same `orig_*` shape `[SPEC-DF-102]` already gives `start_ms`/`lead_in_ms`/`gain_db`, for the same reason: a fade edit needs a sync-safe pre-edit baseline exactly like a boundary edit already has one. See [SPEC008 §3 `[SPEC-SC-046]`](SPEC008-database-schema.md) for what fade *is* and why it is orthogonal to lead.

Created by Lempi on first write, the same way `id_reviews` is `[SPEC-SC-020]`-adjacent player-owned state, not Vipunen schema. `applied_at` carries the same refusal-to-quietly-withdraw rule as `id_reviews.applied_at`: once `apply_boundary_reviews.py` has rewritten `passages`, the row can be reverted but not silently deleted, for the same reason — the evidence a manual edit overrode should survive the edit.

**No `previous_*` columns.** Unlike a recording reassignment, the *original* automatic values are always recoverable by re-running the amplitude/segmentation pass that produced them — they are derived data, not the only copy of a fact. A revert re-derives rather than restores.

---

## 3. The route surface, all behind `vipunen-support`

| route | method | does |
| :--- | :--- | :--- |
| `/edit/:passage_id` | GET | the editor page |
| `/edit/:passage_id/info` | GET | the passage's current boundaries as JSON |
| `/edit/:passage_id/audio` | GET | a decoded WAV window, `?from_ms=&to_ms=`, around the passage — never the raw file, see `[SPEC-SUI-224]` |
| `/edit/:passage_id/review` | POST | write a `boundary_reviews` row — body carries the nine values `[SPEC-SUI-226]` |

**`[SPEC-SUI-201]` `/info` exists because Lempi has no server-side templating.** Every page here is a static shell compiled in with `include_str!` and fetches its own state over a small JSON route once loaded — `/why/:id` and `/review/queue` are the same shape already. There was never a way to bake a passage's boundaries into the `/edit/:passage_id` response itself, so a sibling route carries them instead.

**`[SPEC-SUI-224]` `/edit/:passage_id/audio` decodes through `PassageDecoder` and returns a WAV, scoped to a bounded window, not the raw file.** The original design served the raw file's bytes, `Range`-aware in principle — a first draft, corrected once a real 4-hour DAO capture proved decoding a whole file client-side unusable rather than merely slow. See [LOG004](../LOG004-waveform-editor-build-log.md) `[LOG-WFE-*]` for the full incident account; §4 and §6 below describe the interaction model this route serves, not this history.

**Reached the same way review is.** `?passage=` deep-links `/edit` exactly as it deep-links `/review` `[SPEC-SUI-150]`; the profile page's handoff box gets a second button once this exists, not a second mechanism.

---

## 4. Interaction model

**One canvas, six draggable markers, one gain control.**

- **Waveform**: decoded once client-side via `decodeAudioData` on the fetched bytes, reduced to a min/max pair per horizontal pixel — the standard peak-rendering approach, cheap even for a multi-minute capture because it runs once per load, not per frame.
- **Start / End**: two vertical handles bounding the passage. Dragging either previews from the new boundary on release, not on every pixel of motion — scrubbing a waveform at 60 fps while also decoding audio on every frame is where a "real-time" editor stops feeling real-time.
- **Lead-in / Lead-out**: two secondary handles, constrained inside [start, end], shown as a shaded band rather than a bare line so the marker's *extent* is visible, not just its edge. **Not a fade curve** — since `[SPEC-SUI-226]` this band marks a timing window only (when a crossfade with a neighbour is permitted), and the preview does not sound it; see Fade-in/Fade-out below for the band that is actually heard.
- **Fade-in / Fade-out** `[SPEC-SUI-226]`: two more handles, independently constrained inside [start, end] — not nested inside lead's own span, since lead and fade are orthogonal (`[XFD-ORTH-010]`) and legitimately overlap either way. Shown in a second, visually distinct shading from lead's own — the two fills are independent semi-transparent layers, so where they overlap the canvas's own alpha blending shows it rather than a third hand-computed state. Each side also gets a `<select>` for its curve (`linear`/`cosine`/`exponential`), beside its precise ms field.
- **Gain**: a numeric field beside the waveform, not a marker on it — gain is not a position, and drawing it as one (a common mistake) invites confusing loudness with placement.
- **Preview**: a transport bar under the waveform — play/pause, and a playhead that moves during playback and can be dragged to seek — built from a Web Audio `AudioBufferSourceNode` slicing the decoded buffer at the current draft boundaries and applying the current draft fades and gain. This is what makes it "real-time": every drag changes what the next press of play actually sounds like, with no round trip to the server. The preview applies `fade_in_ms`/`fade_out_ms` — not `lead_in_ms`/`lead_out_ms` — since lead only times crossfade admission and was never itself a gain ramp during ordinary playback; fade is the envelope real playback actually applies.
- **Commit**: one button, disabled until something has changed, posting the nine draft values to `/edit/:passage_id/review`. There is no autosave — an edit is a deliberate decision, matching `id_reviews`' own "recorded, not applied" posture `[SPEC-SUI-140]`.

**The fade curve must be the same formula in both places, or the preview lies.** [`player/core/src/fade.rs`](../../player/core/src/fade.rs) is the ramp profiles the mixer actually uses — `[SPEC-AUD-040]` is a dead tag, per [GOV001](../GOV001-document-hygiene.md)'s own registry, from a deleted `SPEC001-audio-engine.md`; `fade.rs` is the one that runs. This editor's JS implements the identical formula, and a shared table of `(position, expected gain)` pairs — computed once in Rust, checked once in JS against the same numbers — is the guard against the two drifting apart silently, the same failure class `[SPEC-PLAY-030]` was written to close for two backends agreeing about what a play is.

---

## 5. Applying an accepted edit

**`apply_boundary_reviews.py`, alongside `apply_reviews.py`, not folded into it.** The two tools share a shape — dry-run by default, `--commit` to write, refuse an already-applied row rather than silently re-apply — but touch different tables under different constraints (`passages.boundary_src` here, `passage_recordings`/`recording_artists` there), and `apply_reviews.py` is already sized to its own job. Rewrites `passages` for a `boundary_reviews` row not yet applied: `start_ms`, `end_ms`, `lead_in_ms`, `lead_out_ms`, `gain_db`, `fade_in_ms`, `fade_out_ms`, `fade_in_curve`, `fade_out_curve` `[SPEC-SUI-226]`, and sets `boundary_src = 'manual'` — which, per `[SPEC-SC-045]`, is what makes the override outrank any future recomputation permanently.

`lowlevel_cache` is keyed `(audio_md5, start_ms, end_ms)` `[SPEC-SC-080]`. A boundary edit that moves `start_ms`/`end_ms` orphans the cached features for the old span exactly as `[REQ-LIB-145]`'s duration repair did — `apply_boundary_reviews.py` always invalidates rather than re-keying (deletes the old-span row when nothing else uses it), never silently leaving a stale cache row pointing at a span nothing plays anymore. Re-keying was rejected deliberately: it would relabel features extracted for the old span as valid for the new one, and a wrong answer that looks like a right one is worse than an honest gap.

---

## 6. Not yet measured

- ~~Whether `decodeAudioData` on a multi-minute DAO capture (a third of this library) is fast enough to feel immediate, or needs the `Range` fetch narrowed further before decode.~~ Measured, 2026-08-30, against a real 4h05m capture: not fast enough, not even close — see `[SPEC-SUI-224]`. `/edit/:passage_id/audio` now decodes a bounded window server-side rather than the whole file client-side, closing this rather than narrowing it.
- Whether dragging start/end across a passage boundary that abuts another passage in the same file needs a visible marker for the *neighbour's* span, so an edit here cannot silently create or close a gap the neighbour did not ask for.
- Whether the fixed ±60s window `[SPEC-SUI-224]` needs an on-demand "load more" when an edit genuinely needs to reach further — not yet requested, and the honest facts-line note stands in for it until it is.

Measurement questions for whoever builds this, not open design questions — the shape above does not change based on any answer.

---

## 7. Interaction model, additional detail

Refinements to §4's interaction model, described here as they now stand. The incidents that produced them — what was reported, root-caused, and verified live — are recorded in [LOG004: Waveform Editor Build Log](../LOG004-waveform-editor-build-log.md) `[LOG-WFE-*]`, not narrated here.

**`[SPEC-SUI-217]` Two audio engines, one paused on entry.** Lempi's engine plays through the server's own audio device continuously, independent of any browser tab. The editor's preview (§4's `AudioBufferSourceNode`) is a *second*, independent stream to the same output, deliberately not unified with the first — the exact mixer-integration cost §1 already ruled out. `edit.js` posts `POST /command/pause` once on load (the same route the main skin's own pause button uses) and says so on the page. There is no auto-resume on unload: unreliable to detect, and the main page's own Play button is one click away. Preview playback stop is scoped to the specific node being stopped, not to a shared variable, so a stale callback from an already-stopped node can never null out a different node that is still sounding.

**`[SPEC-SUI-218]` A viewport, not just a buffer.** `msOf`/`draw` map the canvas against `view = {fromMs, toMs}` rather than always `[0, totalMs()]` — zoom (buttons, and mouse wheel centred on the cursor) narrows or widens the span; **Whole passage** and **Zoom out** jump to the two spans actually worth naming, the second labelled "Zoom out" rather than "Whole file" since `/edit/:passage_id/audio` serves a bounded window rather than the literal whole file (§3). A drag that starts on empty canvas (not a marker) pans the viewport, told apart from a click-to-jump by whether the pointer actually moved past a small threshold before release. Below the top ~28px "knob rail" a canvas click always means pan-or-jump, never a marker grab.

**`[SPEC-SUI-219]` A knob looks grabbable; a numeric field is exact.** Markers are drawn as an actual circular knob shape near the top of the canvas, not a bare vertical line indistinguishable from the waveform beside it. A second row of plain ms fields (start/end/lead-in/lead-out) sits beside the gain field, wired through the identical clamps `applyDrag` enforces — the same five values, typed instead of dragged.

**`[SPEC-SUI-220]` One undo stack, pushed on completed edits only.** A pre-change snapshot of the draft is pushed before a drag begins, and before a field's `change` fires — never on a drag's own motion, matching the "preview on release, not per-pixel" rule §4 states for playback. Undo pops the stack, stops any playing preview so heard and shown state never disagree, and restores the prior values.

**`[SPEC-SUI-221]` A marker off the current edge is distinguished from one that does not exist.** Each precise field reveals its own marker on `focus`, before a character is typed, and again after a `change`: `ensureVisible` re-centres the view on it, and re-zooms when the marker, though technically on screen, is too small a fraction of a wide view to actually read. `revealSpan` scales that zoom to how far the edit actually moved the value — the lead fields to the lead's own length, and `start_ms`/`end_ms` to the size of the last edit, since a boundary position's own absolute magnitude (a file-relative ms count, often in the millions) is meaningless as a zoom hint the way a lead's own length is.

**`[SPEC-SUI-222]` A marker exactly on the view's own boundary is never clipped in half.** `EDGE_PAD_CSS` (2px) reserves a small margin on each side of the canvas, shared by drawing, hit-testing, panning and wheel-zoom math alike — one padded coordinate mapping (`msToX`/`xToMs`), not a fix applied to rendering alone while hit-testing disagrees with it. This matters because `start_ms` is `0` and `end_ms` is the file's own duration for most passages in this library (one recording, one file), so **Whole passage** and **Zoom out** routinely place both markers exactly on the canvas edge.

**`[SPEC-SUI-223]` A per-passage flavor refresh, suggested before a large move.** `extract_library.py` takes `--passage <id>`; `tools/jobs.py`'s `analyze-flavor` kind and `POST /api/analyze-flavor` reach it from a button on the passage's own profile page, for re-extracting flavor on one passage without a library- or folder-wide pipeline run. A small edit does not need this — a trim of a few hundred ms does not make flavor meaningfully wrong — so `edit.js` only suggests it: `loadedBoundary`, captured once at page load (distinct from `base`, which resets on every save), is compared against the live draft, and a move past 5000ms on either boundary shows a plain-language note beside Save naming where the actual action lives, since Lempi cannot invoke Vipunen's tools itself.

**`[SPEC-SUI-225]` Bringing a point on screen is not the same claim as making it legible.** A re-zoom is driven by the *magnitude* of what changed, not only by whether the target is technically inside `view`. A 4.5 s lead-out on a five-minute file is ~1.5% of a whole-passage view -- a handful of pixels, present but indistinguishable from the end marker beside it -- and a 300 ms nudge to `end_ms` on a wide "Zoom out" view is sub-pixel. Both reported live as "changes in end time are not reflected" while `draw()` had genuinely run every time. Given a magnitude -- a lead's own length, or how far a start/end edit just moved -- the viewport leaves six times that much breathing room around the point, so the change reads as one.

**`[SPEC-SUI-226]` Fade's remaining implementation detail**, beyond the schema and interaction-model facts already in §2, §4 and §5. `fade.rs`'s `Envelope` combines an independent fade-in `Fade` and fade-out `Fade` by multiplying their gains, so a short passage where the two regions overlap falls to the smaller of the two rather than one silently overriding the other — `Fade` itself is unchanged and stays exactly what it is for Skip/Handoff (`engine/mod.rs`'s `cut_ring_to_incoming`). `Stream::envelope: Envelope` replaces the mixer's old `Stream::fade: Fade`; `engine/mod.rs::open()` builds it from the four `QueueEntry` fade fields and a computed `total_frames`, and `Stream::frames_written` is seeded from `origin_frames` on a resume, so a fade-in cannot spuriously re-trigger and a fade-out cannot land early. `record_boundary_review` validates both curve names against `Curve::parse` and refuses an unknown one, the same posture `record_review` takes for its own verb. `BOUNDARY_REVIEW_FADE_COLUMNS` is its own `ALTER TABLE` batch, so an installation already past the first migration only ever gains columns, and `tools/add_fade_columns.py` backfills every existing row with the fixed default in one idempotent, dry-run-by-default statement. §5's `apply_boundary_reviews.py` falls back to the passage's own current fade values (`COALESCE`) only for a `boundary_reviews` row recorded before these columns existed — every row recorded from here on always carries all four, since fade is a required part of the post, not optional the way lead/gain are.

**`[SPEC-SUI-228]` The decoded window's end is rounded to whole milliseconds wherever it acts as a boundary.** `buffer.duration` is a sample count over a sample rate and is essentially never an integer, so a drag or a typed value landing exactly on that ceiling would otherwise save as a float, which the server's `u64` `start_ms`/`end_ms` fields reject. `totalMs()` stays unrounded where it is only a view extent; the rounding applies at the points where it becomes a limit on a value that will be stored.
