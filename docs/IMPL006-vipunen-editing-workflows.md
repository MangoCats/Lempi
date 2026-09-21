# IMPL006: Building the Editing Workflows

**Implementation Guide — the build order for [SPEC021](spec/SPEC021-waveform-boundary-editor.md) and [SPEC010 §3](spec/SPEC010-identification-review.md#3-searching-musicbrainz-directly)**

> **Related:** [IMPL003](IMPL003-vipunen-console-build.md), whose Stage 5 this continues from · [SPEC013 §3.4](spec/SPEC013-vipunen-console.md#34-handoff--the-players-own-pages-inside-vipunens-workflow) for the handoff both features are reached through.

Two independent features, requested together `[REQ-LIB-175]`, `[REQ-LIB-180]`, sharing nothing but the `vipunen-support` feature gate `[SPEC-SUI-190]` and the handoff mechanics `[IMPL003 Stage 5]` already built. Five stages; the first two are the waveform editor split at its read/write seam, the third folds an accepted edit into the library, the fourth and fifth are MusicBrainz search and the artist-credit correction it makes possible.

---

## Ordering

| stage | depends on | why here |
| :--- | :--- | :--- |
| 6 · raw audio route + waveform render | IMPL003 Stage 5 | read-only, cannot damage anything; proves decode and render before editing exists |
| 7 · dragging, preview, commit | 6 | the write half; commits to a new table, not to `passages` |
| 8 · `apply_boundary_reviews.py` | 7 | folds an accepted edit into the library, on its own schedule |
| 9 · MusicBrainz search proxy | — | independent of 6–8; can run in parallel |
| 10 · artist-only correction | 9 | needs a candidate to correct *to*, which 9 supplies |

Stages 9–10 do not block 6–8 or the reverse — they touch different tables and different pages, and the only shared resource is the rate-limited MusicBrainz proxy itself, which stage 9 builds once for both.

---

## Stage 6 — Raw audio route and waveform render

**Serve the file, render the picture, commit nothing.** `/edit/:passage_id` and `/edit/:passage_id/audio` from [SPEC021 §3](spec/SPEC021-waveform-boundary-editor.md#3-the-route-surface-all-behind-vipunen-support), `Range`-aware. The page decodes client-side and draws the peak waveform with the passage's *current* (automatic or already-manual) boundaries marked, undraggable.

> **Claims:** opening `/edit/:passage_id` for a passage inside a 40-minute DAO capture shows a waveform within a few seconds, not a fetch of the whole file. The marked start/end/lead-in/lead-out match what `/edit/:passage_id/info` reports for the same passage — two views of one row, not two sources of truth. Nothing in this stage writes anywhere; it is safe to ship and use before Stage 7 exists.
>
> **Done, 2026-08-27.** `/edit/:passage_id/info` and `/edit/:passage_id/audio` (`[SPEC-SUI-201]`) added, both behind `vipunen-support`; the audio route is `Range`-aware (`bytes=START-END`, `START-`, `-SUFFIX`, malformed or multi falling back to the whole file rather than erroring). `edit.js` decodes client-side and draws min/max peaks per pixel, with start/end as lines and lead-in/lead-out as shaded ramps, undraggable. 337 tests with the feature on (320 without), clippy clean both ways, docs governance clean. `decodeAudioData` speed on a real multi-minute capture is not yet measured against a browser — jsdom has no Web Audio, so the jsdom check covers the info round-trip and the not-found case, not the decode itself.

## Stage 7 — Dragging, preview, commit

**The interaction model of [SPEC021 §4](spec/SPEC021-waveform-boundary-editor.md#4-interaction-model), and the `boundary_reviews` write of §2.** Draggable markers, a Web Audio preview transport built from the same ramp formula as [`player/src/fade.rs`](../player/src/fade.rs), and a commit button that posts the five draft values.

> **Claims:** dragging the end marker inward and pressing play previews the shorter span, with a fade-out that visibly matches the shaded ramp under the waveform. The shared `(position, expected gain)` fixture from `[SPEC021 §4]`'s fidelity guard passes against both the Rust formula and the JS one — the number that answers "how loud, here" is one number, computed twice, checked to agree. Committing writes exactly one `boundary_reviews` row per passage; committing twice on the same passage updates it rather than adding a second.
>
> **Done, 2026-08-27.** Four draggable markers (start, end, lead-in, lead-out) plus a gain field, a Web Audio preview built from a freshly-rendered `AudioBuffer` (sliced and faded sample-by-sample with `fade.js`, not `GainNode` automation, so the preview cannot use different interpolation than the number it is checked against), and `POST /edit/:passage_id/review` writing to `boundary_reviews` via the same `ON CONFLICT` upsert `record_review` uses. `fade.js` is its own file and route, checked against `fixtures/fade/exponential.json` from both Rust (`fade.rs`'s own test) and plain Node (`verify-skins.js`, no DOM needed). Verified end to end against a real copy of the library: passage 10068 read `edited: false`; committing `{80000, 300000, 250, 2000, -2.0}` returned `204`; `/info` then read back exactly those five values with `edited: true`; an inverted `start_ms >= end_ms` was rejected `400`. **Not verified by automated test:** the drag interaction and the preview's audible correctness, since jsdom has no Web Audio — that half needs a person with a real browser, the same limit Stage 6 already had for `decodeAudioData`.
>
> **Superseded, 2026-08-31 — `[SPEC-SUI-226]`.** Two more markers (fade-in, fade-out) and a curve `<select>` per side joined the four above, the preview switched from applying lead to applying fade (lead never itself produced a gain ramp; fade is what real playback actually applies), and `fade.js` gained `Linear`/`Cosine` beside its `Exponential`-only checked-against-`exponential.json` state described here. See [SPEC021 §7](spec/SPEC021-waveform-boundary-editor.md#7-interaction-model-additional-detail) for the current shape and [LOG004](LOG004-waveform-editor-build-log.md) for the incident record; this entry is left as the historical record of what Stage 7 shipped that day, not a description of the editor as it stands now.

## Stage 8 — Applying an accepted edit

**`apply_boundary_reviews.py`**, dry-run by default, `--commit` to write, per [SPEC021 §5](spec/SPEC021-waveform-boundary-editor.md#5-applying-an-accepted-edit).

> **Claims:** run without `--commit`, it reports what it would change and touches nothing. With `--commit`, an accepted `boundary_reviews` row becomes the passage's new `start_ms`/`end_ms`/`lead_in_ms`/`lead_out_ms`/`gain_db`, `boundary_src` becomes `'manual'`, and a second run makes no further change. A `lowlevel_cache` row whose span the edit invalidated is re-keyed or removed, never left pointing at a span nothing plays.
>
> **Done, 2026-08-27.** `tools/apply_boundary_reviews.py`, dry-run by default. A moved span is checked against `passages_span`'s own unique index before writing, in both modes, so a rehearsal's count is the count a real run would apply — a collision is refused and reported, not forced. The old span's `lowlevel_cache` row is **deleted, not re-keyed**, when nothing else uses it: re-keying would relabel features extracted for the old span as valid for the new one, and a wrong answer indistinguishable from a right one is worse than an honest gap a later extraction pass fills in. Verified end to end against a real copy of the library: passage 10068's edit from Stage 7's own verification, applied, left `passages` reading exactly `(80000, 300000, 250, 2000, -2.0, 'manual')` and dropped the one real `lowlevel_cache` row keyed to its old span. `tools/test_apply_boundary_reviews.py` covers the rehearsal/commit/no-op/collision/still-used-elsewhere cases against a schema copied from SPEC008, the same discipline `test_apply_reviews.py` uses.

## Stage 9 — MusicBrainz search proxy

**One route, `[SPEC-SUI-196]`'s rate-limited wrapper around `/ws/2/<kind>`**, and the review page grows a search box beside its candidate list.

> **Claims:** searching from the review page for a title AcoustID never suggested returns real MusicBrainz results, rendered as the same radio-candidate shape a fingerprint suggestion already uses. Opening two browser tabs and searching from both does not exceed roughly one request per second to musicbrainz.org — the limit lives in the proxy, not in client behaviour. Every mbid already on a review card — stored, suggested, artist, release — is a working link to its own MusicBrainz page, added without waiting for search: `[SPEC-SUI-195]` needs no new route at all.
>
> **Done, 2026-08-27; built in Lempi, not the console — see SPEC010 §3's correction.** `GET /api/musicbrainz/search` lives in `player/src/web/musicbrainz.rs` (`web.rs` at the time, split 2026-09-02) behind `vipunen-support`, with `reqwest` declared as an `optional` dependency wired to that same feature so an appliance build never resolves, fetches or compiles an HTTP client. The rate limiter is a process-wide `std::sync::Mutex<Option<Instant>>` that reserves the next allowed instant under the lock and only awaits after releasing it — the two browser-tabs claim is a property of one process serialising every caller, not of either tab behaving. Results reuse `Suggestion`, the exact type fingerprint candidates already serialise as, so the review page's existing candidate-radio rendering handles a searched result with no second code path. Every stored and suggested mbid on a card is now `<a target="_blank" href="https://musicbrainz.org/...">`. Verified live against the real musicbrainz.org through a running Lempi, all three kinds: `recording` and `release` searches returned real titles with `artist-credit` resolved; `artist` returned real artist names correctly read from `name`, with `artist: null` since an artist result names no separate credited artist — confirming the one place MusicBrainz's JSON shape actually differs by entity, which `parse_mb_results`'s unit tests also cover from a captured response rather than only the recording case that happens to work. An empty `q` returned `[]` without a network call at all; `kind=bogus` was rejected `400` before the rate limiter or the network were touched.

## Stage 10 — Artist-only correction

**The one genuinely new decision shape in `[SPEC-SUI-197]`'s table**: a recording id can be right while its credited artist is wrong, and today nothing lets a person say so without also touching the recording.

> **Claims:** choosing a searched-or-suggested artist and confirming "the recording is right, the artist is not" records a decision distinct from a recording reassignment — inspectable, undoable, and appliable — that `apply_reviews.py` (extended, not duplicated per [SPEC010 §3](spec/SPEC010-identification-review.md#3-searching-musicbrainz-directly)) folds into `recording_artists` without touching `passage_recordings`. A passage whose recording id was never in question is unaffected by correcting its artist.
>
> **Done, 2026-08-27.** `artist_reviews`, shaped like `id_reviews` — keyed by `passage_id` since that is where the correction is made from, carrying `recording_mbid` captured at decision time so the correction still means something after `passage_recordings` moves on. `previous_artist_mbid`/`name`/`weight` are captured too, the same reason `id_reviews.previous_mbid` is: unlike a boundary edit's automatic values, a recording's prior credit is not re-derivable by re-running anything, so this is the only copy and a genuine `--revert-artist` needed it. The artist-fix panel is offered on every card whose stored id is shape-checked as a real MusicBrainz id (`is_mbid`'s exact check, ported to JS) — independent of whatever the rest of the card decides about the recording, and reachable even on a card whose recording was simply kept. `apply_reviews.py` folds pending corrections in on every run, whether or not a recording reassignment is also pending, replacing the credit rather than adding to it — the same "replace the link" reasoning the recording-reassignment loop already uses for `passage_recordings`.
>
> Verified end to end against a real copy of the library: passage 10068 ("Jump") had no artist correction pending; `POST .../artist/correct` with a searched MusicBrainz artist id returned `204` and correctly captured `previous_artist_name: "Van Halen"` — the real, existing credit — before overwriting it; a passage with no recording linked at all was refused `409`. `apply_reviews.py --commit` applied it (and, in the same run, 40 real pending recording reassignments already sitting in this library from earlier sessions — proving the two decisions apply independently in one pass, not just in theory); `--revert-artist 10068 --commit` restored Van Halen exactly. `tools/test_apply_reviews.py` gained the same rehearsal/commit/no-op/revert coverage against a schema-accurate fixture, deliberately exercising the artist correction *alongside* an unrelated recording reassignment in the same commit to prove neither disturbs the other. 349 tests with the feature on (321 without), clippy clean both ways, docs governance clean.

With Stage 10 done, both features `[REQ-LIB-175]` and `[REQ-LIB-180]` requested together are built.

---

## Stage 11 — Applying without typing a command

*Added 2026-09-12. Stage 8 built the tool; this is the button, and the reason there is now allowed to be one.*

**`[IMPL-SUI-095]` "Never a web click" was the wrong rule; "never thoughtless" was the right one.** `[IMPL-SUI-055]`'s reasoning — *"an edit changes what a passage is, and the library is Vipunen's to write, not a web click's"* — is an argument against an edit landing as a **side effect**: on a timer, folded into a larger workflow, or as the second half of some other action. It is not an argument for making a deliberate, understood action expensive. A command typed into a rarely-used terminal, against a path a person has to look up, is not safer than a button. It is the same act with worse odds of being done right, and with no record of what happened afterwards.

`[IMPL-SUI-055]` itself is **unchanged and still true**: nothing writes the library through the HTTP handler. The `apply-reviews` job spawns the same `apply_boundary_reviews.py` / `apply_reviews.py` Stage 8 built, as subprocesses, through the same job model as every other write. What changed is who types them.

Three things keep it deliberate rather than easy:

1. **Two steps, and the first one is a list.** `/api/pending/detail` shows the exact before/after of every edit that would be written, read-only, before the button that writes them appears. A confirmation reading "apply 4 edits?" asks consent for something unseen.
2. **The write refuses to be triggered accidentally.** `POST /api/apply-reviews` requires an explicit `confirmed` **and** the kind list the page just displayed, so a stray or replayed request does nothing, and reviewing four boundary edits can never also land ninety-nine recording reassignments.
3. **It is never chained.** No other job starts it, and it starts no other job.

**The player is interrupted around the write, not merely warned about.** Pause → write → `/library/reload` → resume, and resume **only** if `player_state.playing` said it was playing when the run began, so applying never starts music at someone who had deliberately stopped it. A pause, not a restart: `/power/restart` and `/power/off` shell out to `sudo systemctl`, which exists on the appliances and not on the desktop this console runs on. The reload is not optional — without it the edit is on disk and inaudible, because the Director holds the spans it was built with.

> **It also stops overstating what is pending.** `apply_reviews.py` acts only on `reassigned` rows carrying a chosen id, so of 99 pending id reviews exactly 40 would ever be written; the other 59 are `kept`/`deferred` judgements that rewrite nothing and whose `applied_at` therefore stays `NULL` for ever. The page says what will be written and names the remainder separately. Promising 99 changes and delivering 40 is worse than not offering the button at all.

> **Done, 2026-09-12.** `tools/jobs.py`'s `apply-reviews` kind, `console.py`'s `pending_detail()` and the two routes, the flags page's review-then-apply panel, and `lempi_control.pause_lempi/play_lempi/reload_lempi_library`. `tools/test_jobs_apply_reviews.py` runs the real applier against a real split pair and pins: the span lands in the catalogue half, `applied_at` is stamped in the listener half, `kinds: ["boundary"]` leaves the id reviews untouched, the call order is pause → reload → play, a stopped player is left stopped, and an empty `kinds` fails without stamping anything.

## Not on this path

**Release search beyond what a chosen recording already links to.** `[SPEC-SUI-197]`'s table names it as wanted; it is not scheduled here, because Stage 9's proxy makes it a small addition once built — a second `kind=release` search against the same route — and adding it before the proxy exists would mean building the rate-limited plumbing twice.

**Track-position correction** (`release_recordings.position`/`disc`), the fourth row of the same table. Narrower than the other three and has not yet had a real case put in front of it; speculative work here would be exactly the "unverified, not evidence" trap `[SPEC-PLAY-*]`'s own review queue was built to avoid applying to itself.

---

**Traceability:** implements `[SPEC-SUI-195..200]`, `[REQ-LIB-175]`, `[REQ-LIB-180]` · `[IMPL-SUI-095]` refines `[IMPL-SUI-055]`'s scope without weakening it · sits under [IMPL003](IMPL003-vipunen-console-build.md) Stage 5
