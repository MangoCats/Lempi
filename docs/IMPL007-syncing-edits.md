# IMPL007: Syncing Edits, and a GUI for the Bundle

**Implementation Guide — the build order for [SPEC006 §9](spec/SPEC006-data-flow-and-portability.md#9-syncing-an-applied-edit-to-a-remote-installation) and a console front end for `[SPEC-SUI-095]`'s existing bundle transport.**

> **Related:** [IMPL006](IMPL006-vipunen-editing-workflows.md), whose review tables this reuses as the sync unit · [IMPL003 Stage 4](IMPL003-vipunen-console-build.md#stage-4--the-bundle-both-halves) built the bundle this gives a GUI to.

Two independent pieces of work, requested together 2026-08-27 but touching neither the same code nor the same trust boundary.

---

## Ordering

| stage | depends on | why here |
| :--- | :--- | :--- |
| 1 · `boundary_reviews` gains a baseline | — | schema first; nothing else can be tested without it |
| 2 · `export_changes.py` | 1 | read-only, cannot damage anything |
| 3 · `apply_changes.py`, fast-forward and conflict | 2 | the write half, against fixtures before a real remote |
| 4 · bundle-builder GUI in the console | — | independent of 1-3; reuses browse's existing filters |
| 5 · the deploy step: prepare, don't push | 4 | opens a local terminal with the commands staged, never runs them itself |

---

## Stage 1 — `boundary_reviews` gains a portable baseline

**`record_boundary_review` captures `audio_md5`, `orig_start_ms`, `orig_end_ms`, `orig_lead_in_ms`, `orig_lead_out_ms`, `orig_gain_db`** from the passage's *current* row, the same query shape `record_review` already uses for `previous_mbid` `[SPEC-DF-102]`. All three review tables gain `origin TEXT`, `NULL` meaning "decided on this machine."

> **Claims:** a fresh `boundary_reviews` row carries the passage's pre-edit span even though the row's own `start_ms`/`end_ms` are the *new* target — the two are readable side by side, and an existing library's `boundary_reviews` table migrates via `ALTER TABLE ADD COLUMN`, the same pattern `REVIEW_COLUMNS` already uses.
>
> **Done, 2026-08-27, and it found a real bug while it was in there.** `artist_reviews` was keyed by `passage_id` -- reached from a passage's card, so that seemed natural, but the credit belongs to the *recording*, and the same recording can sit under several passages. Two different cards for one recording could each record their own, silently conflicting correction, and a synced correction had no originating passage on the receiver at all to key it by. Re-keyed to `recording_mbid` before this went further, not worked around at the sync layer -- `passage_id` stays as a plain, non-unique column, informational only. `record_boundary_review` also gained the guard `record_review`/`record_artist_review` already had and it was missing: re-committing an *applied* boundary edit is refused, since re-reading `passages` afterward would have captured the already-applied values as the "original," corrupting the one thing this stage exists to keep honest. 352 tests with `vipunen-support` on (321 without), clippy clean both ways.
>
> **Extended, 2026-08-31 — `[SPEC-SUI-226]`.** `record_boundary_review` now also captures `orig_fade_in_ms`/`orig_fade_out_ms`/`orig_fade_in_curve`/`orig_fade_out_curve` alongside the baseline above, the identical treatment fade's four new columns needed once they existed at all — see [SPEC021 §2](spec/SPEC021-waveform-boundary-editor.md#2-data-model) for the current column list and [SPEC008 §3 `[SPEC-SC-046]`](spec/SPEC008-database-schema.md) for what fade *is*. Stages 2–3 below carry it through unmodified in structure: `export_changes.py`/`apply_changes.py` read around a source too old to have the columns the same way they already read around a table too old to have `origin`.

## Stage 2 — `export_changes.py`

**Reads every `applied_at IS NOT NULL` row across the three review tables and writes one portable JSON record per row**, per `[SPEC-DF-103]`'s identity table. `origin` on the outgoing record is the row's own `origin` if it has one (a decision arriving from a *third* machine, being forwarded), else this machine's hostname.

> **Claims:** run against a library with a mix of applied recording, boundary and artist corrections, produces one JSON array where every record resolves to a real anchor and nothing requires the exporting machine's own `passage_id` to be understood.
>
> **Done, 2026-08-27.** Reads around a review table missing `origin` entirely -- `PRAGMA table_info` rather than assuming the Rust-side migration has already run, since this tool has no dependency on a `vipunen-support` Lempi ever having opened the file. `id_review`'s target carries the recording's title and current artist credits, read live from the source's own `recordings`/`recording_artists`, so a receiver that has never seen that recording can still construct it -- the exact NOT NULL trap that made the first `apply_reviews.py` unable to apply anything `[REQ-LIB-165]`.

## Stage 3 — `apply_changes.py`

**Classifies every record against the target's *current* live-schema value at the same identity** — fast-forward, no-op, or conflict, per `[SPEC-DF-101]`. `--commit` lands fast-forwards immediately; a conflict is refused and reported until named in `--resolve N=ours|theirs`. Landing a decision writes the review-table row (stamped with the *original* `decided_at` and `origin`, never the arrival time) and the live-schema change in one transaction — the same shape `apply_reviews.py`/`apply_boundary_reviews.py` already write, not a third implementation of it.

> **Claims:** three fixtures prove the three outcomes on a schema-accurate target: a target unchanged since baseline takes the incoming value with no flag; a target already holding the same value reports a no-op; a target that independently changed the same fact is refused until `--resolve` names a side, and the report names both values, both decision dates, and which machine made each.
>
> **Done, 2026-08-27.** Creates all three review tables outright, `ALTER TABLE`-migrating an existing one to add `origin` -- a target no `vipunen-support` Lempi has ever opened has none of them, which describes every real appliance today, and this tool has no dependency on one having run first. A second, real bug this stage's own idempotency claim found: re-applying an already-landed `changes.json` reported the boundary edit as "not present" rather than "already in sync", because applying it moves the passage's span away from the very anchor the record was keyed on. Fixed by resolving against the anchor span *or* the target span -- if the target span is what is found, the edit already landed. **Verified against a real pair, not just fixtures:** exported all 42 applied decisions actually sitting in a copy of the real library (40 pre-existing recording reassignments plus a fresh boundary edit and artist correction made for this test) and applied them to a second, untouched copy -- all 42 fast-forwarded cleanly, and the receiving copy matched the source exactly, down to the artist credit and the boundary values. 352 tests with `vipunen-support` on (321 without), clippy clean both ways.

## Stage 4 — Bundle-builder GUI

**A new console page reusing the browse filters that already exist**, building a bundle through a new job type on `tools/jobs.py`'s existing `Runner` rather than a CLI invocation — the same progress machinery Stage 3 of `[IMPL003]` already built for induction. Shows what would be bundled (encodings, size) before committing to building it.

> **Claims:** selecting an artist and pressing "Build bundle" produces the same `out/<name>/` directory `export_bundle.py` would from the equivalent `--like` filter, with a running job log rather than a silent CLI wait.
>
> **Done, 2026-08-27.** A third `Runner` job kind, one `_spawn` call, no multi-stage loop -- `export_bundle.py` is already one atomic operation. **Verified live, against the real library:** searching "Frisina" and building produced `4 encodings, 4 recordings, 4 files 38.9 MB, payload 66.1 KB (4.9 KB gzipped)` -- the exact figures `[IMPL003 Stage 4]` measured by hand from the CLI, now reached from a search box and a button.

## Stage 5 — Prepare, don't push

**No SSH, no rsync, no remote host configured into Vipunen.** The built bundle's page opens a local terminal window (an ordinary console on the operator's own machine — `cmd`/PowerShell on Windows, the platform default elsewhere) and separately displays the exact `rsync` / `import_bundle --apply` / `POST /library/reload` commands as selectable text, for the operator to copy into the window that just opened and run themselves. Vipunen never executes a command against another host; opening a local terminal is the same act as the operator opening one themselves.

> **Claims:** pressing "Deploy" opens a real terminal window and the page shows commands that, copied verbatim, reproduce exactly what `[IMPL003]`'s own manual bundle deployment already did by hand.
>
> **Done, 2026-08-27.** `POST /api/export/open-terminal` (an action, not a query, so POST rather than the GET the route sketch might suggest) spawns an ordinary `cmd`/Terminal/`x-terminal-emulator` on the operator's own machine, `cwd`'d to the bundle directory -- Vipunen names no remote host anywhere in this path. **Verified live:** a real `cmd.exe` window opened at the bundle's own directory; a nonexistent directory was refused with a named reason before anything was spawned.

---

**Traceability:** implements `[SPEC-DF-100..106]`, `[REQ-LIB-185]` · Stage 4-5 give a GUI to `[SPEC-SUI-095]`, already built
