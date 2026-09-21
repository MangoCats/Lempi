# `tools/` — Vipunen

Everything here is Vipunen: the library-building side of Lempi, Python, AGPL-3.0-or-later — see [GUIDE002 §2](../docs/GUIDE002-rearchitecture-plan.md#2-architectural-decisions) (`[GDE-ARC-010]`) for why it's a separate entity from `player/`. 92 files in one flat directory with no subpackages; this index exists so "where is the code that does X" has an answer without moving anything, per `[GDE-CHT-040]`'s scope discipline — restructuring into subpackages was considered and set aside precisely because the flat layout is what every doc's CLI example, every path Vipunen's own job runner builds, and every `test_*.py`'s import already assume.

Each script is run directly — `python tools/<script>.py --help` for its own arguments — and most carry a runnable example in their own module docstring. `test_<name>.py` beside `<name>.py` is that module's own tests, run the same way (`python` on it, from `tools/`); a handful of `test_console_*.py` files test slices of `console.py` instead of a same-named module. **`pytest` is not the runner here and reports 59 errors if you try it** — every one of them `fixture 'tmp' not found`, because these files pass their own temporary directory in from a `__main__` block rather than taking a pytest fixture. The tests are fine; the runner is wrong. All 53 pass when run directly.

---

## Console & orchestration

The Vipunen console — a local web UI over the library, and the job runner it drives. Stage 2/3 of [IMPL003](../docs/IMPL003-vipunen-console-build.md).

| script | what |
| :--- | :--- |
| `console.py` | The console itself: read-only views (library, folder, profile), plus the routes that submit jobs and the one deliberate write exception `[SPEC-DF-116..117]`. |
| `jobs.py` | Runs one job at a time as a subprocess — the same CLI a person would run by hand — and reports its progress back to the console `[SPEC-SUI-080]`. |
| `lempi_control.py` | Everything that talks to the co-resident player's own process or HTTP API: liveness, capability and staleness checks, starting it, signaling a flag write `[SPEC-SUI-170]`, `[SPEC-SUI-227]`. |
| `console_web/` | The console's own HTML/CSS/JS, served by `console.py` — not Python, listed here because it's the other half of the same UI. |

## Ingest & library building

Bringing audio into the library and keeping its schema current.

| script | what |
| :--- | :--- |
| `ingest_folder.py` | Bring a folder of audio into the library `[REQ-LIB-100]`. |
| `extract_library.py` | Extract lowlevel features and classify them into flavor `[LOG-FEX-102]`. |
| `fingerprint_ids.py` | Check every recording MBID against the audio itself `[REQ-LIB-165]`. |
| `verify_ids.py` | Are the recording MBIDs on these passages actually right? |
| `segment_dao.py` | Segment a disc-at-once capture into its tracks — grid search, DP assembly, RMS fallback, extra-track merging `[SPEC-SA-070]`, `[SPEC024]`. |
| `analyze_amplitude.py` | Automatic lead-in/lead-out detection `[SPEC-SA-075]`. |
| `backfill_album_cuts.py` | Give an old radio-only passage the album twin it never got `[SPEC-SA-110]`. |
| `backfill_file_tags.py` | Re-read tags for a file whose `file_tags` row came back entirely empty. |
| `repair_durations.py` | Repair `files.duration_ms` from the decoded length `[REQ-LIB-145]`. |
| `audio_duration.py` | A file's real, decoded duration — never a header/bitrate estimate. |
| `add_fade_columns.py` | Schema migration: add the fade columns to `passages` `[SPEC-SUI-226]`. |
| `rename_recording_time_scale.py` | Schema migration: rename a `listener_settings` column. |
| `migrate_mulib.py` | Migrate MuLibPlay's database into the Lempi schema `[GDE-PHS-010]`. |
| `migrate_mulib_art.py` | Bring MuLibPlay's hand-curated album art into `cover_art`. |

## Identification & release matching

Resolving what a file actually is, against MusicBrainz.

| script | what |
| :--- | :--- |
| `choose_release.py` | Which release is a file actually from? Vipunen S3, selection half. |
| `fetch_releases.py` | Fill `releases` and `release_recordings` `[SPEC-SA-030]`. Vipunen S3, release half. |
| `fetch_chosen_tracks.py` | The track listing of each chosen release. Vipunen S3, third pass. |
| `suggest_release.py` | Suggest a MusicBrainz release for a folder of already-split files. |
| `fetch_cover_art.py` | Fetch missing cover art from the Cover Art Archive `[REQ-VIS-170]`. |
| `import_lyrics.py` | Bring MuLibPlay's lyrics into Lempi `[SPEC-LYR-030]`. |

## Review & sync

Folding reviewed decisions into the library, and syncing them — and flags — between installations `[SPEC006 §9-10]`.

| script | what |
| :--- | :--- |
| `apply_reviews.py` | Fold reviewed id decisions into the library `[REQ-LIB-165]`. |
| `apply_boundary_reviews.py` | Fold reviewed boundary edits into the library `[SPEC021 §5]`. |
| `accept_remote_basis.py` | Accept a remote's current value as this library's new local value for one review. |
| `export_changes.py` / `apply_changes.py` | Export applied edits for a remote installation, and apply them there — the three-way merge core. |
| `export_flags.py` / `import_flags.py` | Export/land flagged recordings and passages between installations. |
| `remote_flags.py` | Fetch a remote's flagged recordings and passages directly, no database copy `[SPEC-DF-119]`. |
| `remote_peek.py` | Read exactly one row from a remote installation over `ssh`, no database copy. |
| `remote_snapshot.py` | Build a tiny local snapshot of exactly what a `changes.json` needs, over targeted reads `[SPEC-DF-120]`. |
| `push_file_tags.py` | Push locally-known `file_tags` to a remote whose own copy is behind. |

## Flavor extraction & classifier training

The Gaia/Stage B pipeline — reproducing AcousticBrainz's own extraction chain locally, per [GUIDE003](../docs/GUIDE003-feature-extraction-strategy.md). The biggest cluster here, matching `[GDE-PHS-000]`'s billing as the project's highest-risk unknown.

| script | what |
| :--- | :--- |
| `gaia_classify.py` | Classify lowlevel features into the 71-dimension flavor vector. |
| `gaia_history.py` | Read Gaia `.history` transformation chains. |
| `gaia_predict.py` | Apply a Gaia chain to lowlevel features, and check it against the reference. |
| `model_store.py` | Persistence for the Stage B distilled classifiers. |
| `train_stageb.py` | Distil AcousticBrainz's highlevel classifiers. Route 3, step 2. |
| `train_final.py` | Retrain the final Stage B model selection and persist it. |
| `build_stageb_dataset.py` | Build the paired lowlevel → highlevel dataset. Route 3, step 1. |
| `stageb_iter2.py`, `stageb_iter3.py`, `stageb_iter4.py`, `stageb_iter5.py` | Successive Stage B iterations, each building on the last's confirmed findings. |
| `derive_constants.py` | Re-derive the flavor constants on locally extracted values `[SPEC-FD-090]`. |
| `pool_params.py` | Re-derive the pool parameters against the local library `[SPEC-DIR-200]`. |
| `ab_harvest.py` | Tier 0 harvest: extract library flavor vectors from the archived AcousticBrainz dumps. |
| `passage_duration_experiment.py` | Does slice duration degrade flavor? `[SPEC-SA-090]` |
| `provenance_consistency_test.py` | Does provenance consistency beat per-track accuracy? `[SPEC-FD-130]` |
| `recompute_floor.py` | Recompute the reproducibility floor on our own library. |

## Export & portability

| script | what |
| :--- | :--- |
| `export_bundle.py` | Build a bundle for a remote Lempi `[SPEC-SUI-095]`, `[SPEC014]`. |
| `payload.py` | Build the derived-data payload that travels between installations `[SPEC014]`. |

## Everything else

| script | what |
| :--- | :--- |
| `check_docs.py` | Documentation governance checks `[GOV-DOC-010..040]`. |
| `check_forbidden_names.py` | Fails if a retired project name appears outside the one file allowed to record it `[GOV-FBN-010..020]`. |
| `echo_skew.py` | How far apart two echo nodes really are, sampled continuously from each node's own anchor `[GDE-ARC-037]`. Replaces `echo_skew.sh` for anything longer than a spot check. `--clocks` asks each node's own chrony what its clock error is, so a reading is never ambiguous about clock-versus-audio `[GDE-ARC-038]`. |
| `load_occasions.py` | Load MuLibPlay's four seasonal curves as data `[SPEC-DIR-134]`. |
| `secret.py` | Where credentials come from, in one place. |
