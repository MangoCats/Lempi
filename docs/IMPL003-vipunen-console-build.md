# IMPL003: Building the Vipunen Console

**Implementation Guide — the build order for [SPEC013](spec/SPEC013-vipunen-console.md), and what each stage must prove**

> **Related:** [SPEC013](spec/SPEC013-vipunen-console.md) · [SPEC006](spec/SPEC006-data-flow-and-portability.md) · [SPEC012](spec/SPEC012-library-relink.md) · [GUIDE002 §3](GUIDE002-rearchitecture-plan.md#3-phased-plan) — this is `[GDE-PHS-040]`'s ingest half, told as a work order.

Nothing in SPEC013 is built. Six stages, ordered by what unblocks the most and what fails soonest if the design is wrong. Each ends in a **measurable claim**, not a demo `[GDE-LES-030]`.

---

## Ordering, and why it is this one

**`[IMPL-SUI-010]` The riskiest thing in SPEC013 is not the console — it is the payload.** `[SPEC-SUI-130]` puts two implementations, in two languages under two licences, on either end of a format that does not exist yet. Nothing keeps them in agreement, and the failure mode is a bundle that imports cleanly and means something slightly different than it said. So the format and its fixtures come **before either half is written**, not between them.

Everything else follows cost and safety: read-only views are the cheapest useful thing and cannot damage a live library, so they come early; anything that writes waits for a job model that assumes a player is writing too `[SPEC-SUI-082]`.

| stage | depends on | why here |
| :--- | :--- | :--- |
| 0 · induct Frisina by hand | — | clears real backlog, and produces the reference transcript |
| 1 · payload schema + fixtures | — | `[IMPL-SUI-010]`; runs in parallel with 0 and 2 |
| 2 · read-only views | — | safe against the live db; answers the question that started this |
| 3 · jobs and induct | 0, 2 | first writes; 0 gives it something to be checked against |
| 4 · bundle, both halves | 1, 3 | export is a job; the format must already be pinned |
| 5 · handoff and launch | 2 | affordances live on the profile page |

Critical path to *"the new music plays on the appliance"* is **0 → 1 → 3 → 4**.

---

## Stage 0 — Induct the pending music by hand

**`[IMPL-SUI-020]` Do not hold the pending tracks hostage to a UI project.** `Frisina, Gerardo` — two albums, **4 audio files** and 2 `cover.jpg`, 40 MB in total — is inductable today with the CLIs exactly as they stand, and the console is months of work away. Run the pipeline by hand, on the real folder, and **keep every stage's output**.

> **Six files, four tracks.** The transfer unit and the induct unit are not the same number and this document originally used one for both. rsync moves 6 files; `ingest_folder.py` inducts the 4 that are audio. Anywhere a count appears, it now says which.

```
python tools/ingest_folder.py data/library.db "C:/Users/Mango Cat/Music/Frisina, Gerardo"
python tools/ingest_folder.py data/library.db "C:/Users/Mango Cat/Music/Frisina, Gerardo" --commit
python tools/extract_library.py data/library.db
python tools/fingerprint_ids.py data/library.db  ;  ... --merge
```

Cover art needs nothing: both albums already carry `cover.jpg` beside the tracks, which is where [tags.rs](../player/src/tags.rs) looks.

> **This is not just backlog.** The transcript *is* the specification for what `[SPEC-SUI-085]`'s progress display must render — real stage names, real timings, real failure text on a real folder. Designing that view against imagined output is how it ends up showing a spinner and a percentage that means nothing.

> **DONE 2026-08-20.** All four inducted into the live `data/library.db`, every claim met.
>
> | | |
> | :--- | ---: |
> | files / passages / recordings | **5,705 → 5,709**, +4 each |
> | flavor, per track | **71 / 71** characteristics |
> | extraction | 4 of 4, **0 failed**, 42 s at 19 jobs |
> | `listener_play_history` | 37,238 → **37,238**, unchanged |
>
> A listener-state backup was taken first — 2.4 MB, on `backup_now`'s own stated grounds, *"before letting a tool loose on the library"*. The player then took another at startup by itself, which is `[REQ-LIB-160]` working unprompted.
>
> Browse resolves them: **Gerardo Frisina, 4 passages**, with titles, albums and track numbers from the file tags, and `POST /queue/…/last` returns 204 — the player reads and enqueues them. Export to the appliance **was not attempted**; it waits for stage 4.

**`[IMPL-SUI-025]` What stage 0 found, which is the reason it goes first.**

1. **All four came back `unmatched` from AcoustID**, so they keep their `local:audio:` ids. Predicted exactly by `[SPEC-SUI-075]`, and `unmatched` is not a finding `[REQ-LIB-165]` — but it means the four will surface in the review queue under `no-mbid`, and the console must present that as *"no MusicBrainz entry exists"* rather than as a defect awaiting repair.
2. **The fingerprint pass had 140 passages outstanding, not 4.** 8,190 of 8,330 were checked; the backlog was invisible because nothing reports it. That is `[SPEC-SUI-040]`'s job — a library-wide view of which stages have run over what — and it is now a demonstrated need rather than an inferred one. (Results: 133 confirmed, 7 unmatched, **0 contradicted**.)
3. **Stage output is not display-ready.** `ingest_folder.py` renders titles as `�Duala�` on a Windows console — its `say()` fallback mangling the smart quotes. Harmless in a terminal, wrong in a browser, and proof that `[SPEC-SUI-085]` must render from **structured** stage results rather than by piping stdout into a page.

---

## Stage 1 — Pin the payload, and give both halves the same fixtures

**`[IMPL-SUI-030]` `[SPEC-DF-065]` promises one payload schema and does not contain one.** It says "one serializer, one parser, one schema version" and then describes three envelopes. That was sufficient while nothing implemented it. `[SPEC-SUI-130]` is what makes the absence expensive.

Deliverables, in order:

1. **SPEC014 — the payload schema.** Fields, types, units, and — the part `[SPEC-SUI-165]` cannot work without — the **required set**. "Compatible" is defined as *the receiver can construct what it requires*, so that requirement list is a normative artifact, not documentation.
2. **A fixture corpus** both implementations test against: valid payloads, a newer payload carrying unknown fields, one missing a required field, one with an unresolvable conflict, and the **same bundle twice** `[SPEC-SUI-180]`. Each with its expected outcome.
3. **Settle `[SPEC-SUI-180]` here**, not in stage 4. A resend after a dropped connection over an 11-hour link is the ordinary case, not a mistake, and idempotency designed after the writer exists is idempotency retrofitted.

**`[IMPL-SUI-035]` Keep `[SPEC-SUI-175]` deferred but not foreclosed.** The re-read trigger is genuinely open; storing the payload **with its declared version** costs nothing now and is the whole prerequisite. Decide the trigger later; do not make it impossible today.

> **DONE 2026-08-20.** [SPEC014](spec/SPEC014-payload-schema.md) written; [`tools/payload.py`](../tools/payload.py) is the one serializer; eight fixtures registered with expected outcomes in [`fixtures/payload/`](../fixtures/payload/README.md). `compatible()` mechanises **both** halves of `[SPEC-SUI-165]` — a checker with only the required-set half passes fixture 04. Fixture 01 is generated from the **real library**, not hand-written, so the format met data before anything was built on it. `serde`/`serde_json` are already in [player/Cargo.toml](../player/Cargo.toml), so the importer needs no new dependency.

**`[IMPL-SUI-037]` What stage 1 found.**

1. **The size estimate in `[SPEC-DF-093]` is out by ~9×.** Readable JSON is 11.0 KB per recording, not the "~1–2 KB" it argues from; 1.27 KB is the *gzipped* figure `[SPEC-PL-090]`. **The decision it was defending survives** — compression reaches the stated size while keeping inspectability — but the arithmetic under it did not, and `[SPEC-SUI-165]`'s "~16 MB" was my own invention, now measured at 10.4 MB.
2. **73% of the 1,072 MB library is cache no receiver can use** — `musicbrainz_cache` 547 MB, `lowlevel_cache` 202 MB, `identification_cache` 37 MB `[SPEC-PL-040]`. A far stronger form of `[SPEC-SUI-095]`'s argument than the size ratio that motivated it.
3. **The scoped relink of `[SPEC-SUI-105]` is not a separate pass.** Relink never creates a row `[SPEC-RLK-090]`, so it cannot bind an arriving one; the importer must hash and bind what it creates, which is the same walk `[SPEC-PL-085]`. Stage 4 is smaller than planned.
4. **The reference recordings have no `recording_artists` rows at all.** Their names live only in `file_tags`, so tags had to join the payload or the music would land artist-less `[SPEC-PL-050]`. Found by generating from real data; a hand-written fixture would have had artists in it.

---

## Stage 2 — The read-only console

**`[IMPL-SUI-040]` Views first, because they cannot break anything.** The library is WAL `[SPEC-SUI-082]`, so readers never block the player. A console that only reads can be run against the live database on day one, which is the fastest route to finding out whether the design is right.

Build: the server shell `[SPEC-SUI-010]`, `/library` with the profile page `[SPEC-SUI-040]`, `/folder` with the cheap pass `[SPEC-SUI-060]`.

Leave out, deliberately: every POST. No jobs, no induct, no export.

> **DONE 2026-08-20.** [`tools/console.py`](../tools/console.py) and [`tools/console_web/`](../tools/console_web/). Three views, no `do_POST`, database opened `mode=ro` — the safety claim is structural, not promised.
>
> | | measured |
> | :--- | ---: |
> | audio on disk / library rows | **5,745 / 5,709** |
> | assumed here *(size + mtime)* | 5,709 |
> | changed · missing · **verified** | 0 · 0 · **0** |
> | unclaimed by path | **36** |
> | walk | **105 ms** |
>
> **The claim above was wrong in framing and is corrected here.** 7,232 is *every* file under the root; the audio subset — the only part that can be inducted — is 5,745. The two numbers were never comparable.
>
> The 36 unclaimed are the tail relink found by hashing: the `X_2.mp3` literal copies and a `.wkmp_temp` scratch file. **The cheap pass reaches the same candidates without hashing and then refuses to classify them**, which is the design working: only a hash separates `unknown` from `moved`. `verified` is 0 and the page says so in words.

**`[IMPL-SUI-045]` What stage 2 found.**

1. **A quadratic query, of a shape this repo has already recorded.** `flavor` is keyed `(subject_kind, subject_id, …)` and its index repeats that prefix `[SPEC-SC-060]`, so a lookup on `subject_id` alone matches neither and SQLite scans all 578,452 rows **once per passage**. The first console would not start: **>180 s against 0.044 s**, `SCAN` becoming `SEARCH`. This is the same fault `[REQ-LIB-165]` recorded against `release_recordings(mbid)` — fixed there with a new index, fixed here by naming the prefix column, which was already known. **Written fresh, into new code, having read the account of it.** A documented bug is not an inoculation.
2. **`ingest_decisions` holds one stage of seven.** 15,050 rows, every one `release_match` from [`choose_release.py`](../tools/choose_release.py); `ingest_folder`, `extract_library` and `fingerprint_ids` write none, and 333 files have no decision record at all. `[SPEC-SA-085]` requires every stage's decision recorded *"not just logged"*, and `[SPEC-SC-100]` describes the table as holding what each stage decided. It holds what one stage decided. Invisible until something read it — which is the argument for `[SPEC-SUI-045]`, now demonstrated. **Backfilling the other stages is stage 3 work**, since that is when the console drives them.
3. **The scan is 105 ms, not the minutes I budgeted for.** `[SPEC-SUI-060]` justified the cheap pass against a nine-minute hash; the stat walk over 5,745 files is fast enough that the folder view needs no progress reporting at all.

> **Verified by request, not by eye.** Every endpoint and page was fetched and checked, and all scripts pass `node --check`. Nobody has looked at the rendered pages; that is the honest state of it.

---

## Stage 3 — Jobs, and induct

**`[IMPL-SUI-050]` The job model is the real work here; induct is a thin caller.** `[SPEC-SUI-082]` is the constraint that shapes it, and the pattern is already in the tree rather than ours to invent: a long pass opens the library **read-only**, writes to a sidecar, and folds in with `--merge` when things are quiet. Copy it.

Then `[SPEC-SUI-070]`'s propose-then-commit, `[SPEC-SUI-085]`'s database-held job state, and SSE progress rendered from stage-0's real output.

> **DONE 2026-08-20.** [`tools/jobs.py`](../tools/jobs.py), job routes and SSE in [`tools/console.py`](../tools/console.py), [`jobs.html`](../tools/console_web/jobs.html).
>
> **The claim holds exactly.** Stage 0's induction, re-run through the console — propose (4 files, **0 to add**, 4 already present) then confirm — left the library byte-for-byte in row counts: files 5,709, passages 16,409, flavor 578,452, id_checks 8,330, plays 37,238, decisions 15,050, **all unchanged**. `files.audio_md5` is `UNIQUE`, so console and CLI agree to the row.
>
> Job state survives a console restart: both jobs are still listed, with their events, after the process is killed and started again. Stopping a finished job returns `false` rather than an error, and confirming a job that is not a completed proposal is refused with the reason.

**`[IMPL-SUI-055]` The console still does not write the library, and that was worth preserving.** Stage 3 adds `do_POST`, but nothing in it opens the library for writing: jobs run the same CLIs a person runs `[SPEC-SUI-015]` as subprocesses, and *those* write, as they always have. Bookkeeping goes to a sidecar — `<library>.console.db`, named as the id-check sidecar is.

This refines `[SPEC-SUI-085]`, which asked only that job state not live in the browser. A sidecar satisfies that and avoids two costs: tables in `lempi.db` that Lempi never reads `[SPEC-SC-015]`, and taking the library's write lock for bookkeeping — contending with the player `[SPEC-SUI-082]` in order to record that nothing had happened.

**`[IMPL-SUI-057]` Two findings from stage 2 are fixed at their source, not worked around.**

1. **`ingest_folder.py` now writes `ingest_decisions`.** Verified on a copy of the library with a generated test tone: the table gains an `ingest` stage beside `release_match`, carrying passage, file, mbid, boundary kind and *why no identification was attempted*. The other stages remain unrecorded and are still owed.
2. **`--json` gives a caller the record instead of the rendering.** `say()`'s console-encoding fallback is a property of a terminal, and a job is not one. Jobs also run children under `PYTHONIOENCODING=utf-8`, so even the prose log arrives intact.

> **Not verified, and stated rather than implied:** the interrupt path. Every job in this exercise finished in under ten seconds, so `stop` was only ever exercised against a job that had already ended. *"A job killed mid-pass loses at most the in-flight item"* `[SPEC-SA-028]` remains a claim, not a measurement — it wants a long extraction to test against.

---

## Stage 4 — The bundle, both halves

**`[IMPL-SUI-060]` Exporter and importer are one feature and land together.** Vipunen's exporter is Python and AGPL; Lempi's importer is Rust and MIT, and must be written as Lempi code `[GDE-ARC-018]`. Both are tested against stage 1's fixtures **before** either meets the network.

Order: exporter → importer → scoped relink `[SPEC-SUI-105]` → the real transfer.

> **BOTH HALVES BUILT AND VERIFIED 2026-08-20; the appliance import is BLOCKED.** [`tools/export_bundle.py`](../tools/export_bundle.py), [`player/core/src/bundle.rs`](../player/core/src/bundle.rs), [`player/src/bin/import_bundle.rs`](../player/src/bin/import_bundle.rs).
>
> **The result stage 1 existed to produce: the two implementations agree.** Run over the fixture corpus, Python's `compatible()` and Rust's `unacceptable()` return the same verdict on all five — accept, accept, reject, reject, accept. Nothing but the fixtures was keeping them honest, and they are.
>
> | | measured |
> | :--- | ---: |
> | bundle | 4 encodings, **38.9 MB** audio, **66.1 KB** payload (**4.9 KB** gzipped) |
> | against shipping the database | **1,072 MB** |
> | import into a fresh schema | 4 files, 4 tags, 4 passages, 4 recordings, **284 flavor**, 304 rows |
> | re-import of the same bundle | **0 imported, 4 already** `[SPEC-SUI-180]` |
> | audio missing for one encoding | 3 imported, **1 awaiting**, others land |
> | audio present, hashing wrong | 3 imported, **1 corrupt**, exit 1, others land |
> | incompatible payload | **0 files, 40 schema objects unchanged** |
>
> Audio and payload are **on the appliance**: 7,226 → **7,230** files, closing the mirror gap this work started from. The import has not been run there.

**`[IMPL-SUI-065]` Run on the appliance 2026-08-20, and the claim holds to the row.**

| | before → after |
| :--- | ---: |
| files · passages · recordings | 27 → **31** · 56 → **60** · 31 → **35** |
| flavor | 2,201 → **2,485** *(+284 = 4 × 71)* |
| **`listener_play_history`** | **37,481 → 37,481** |
| **`listener_preferences`** | **3,261 → 3,261** |

304 rows written, 0 corrupt, the payload retained at 67,732 bytes, and the appliance's player — after a restart — browses *Gerardo Frisina, 4 passages*. **The music the first question in this work asked about is now on the appliance and playable.**

Two gates were cleared to get there: `ffmpeg` installed on the Pi, which `[SPEC-RLK-080]` decided and nobody had executed, so `relink` had been printing its `apt install` line to nobody; and an `aarch64` build via the container in [build/README.md](../build/README.md).

**`[IMPL-SUI-066]` The same music holds `passage_id` 16407 here and 16168 there.** Bound by `audio_md5`, never by number — `[SPEC-DF-035]` demonstrated rather than argued. A link carrying one machine's id to the other would have opened a real passage that was the wrong song.

**`[IMPL-SUI-068]` Two measurements the appliance made possible, both recorded in [SPEC012](spec/SPEC012-library-relink.md).**

1. **Windows substitutes a private-use codepoint for characters it cannot store**, and **276 of 5,709 paths (4.8%) carry one** — 264 `:` and 17 `?`. A 250-file sample matched 238 by path; all 12 that failed were present under the translated name with byte-identical audio. Path binding loses one file in twenty, invisibly, because both shells render both forms the same `[SPEC-RLK-025]`.
2. **The `[SPEC-RLK-086]` version risk was tested for the first time and did not fire**: ffmpeg 5.1.9/aarch64 against 8.0/x86_64, **238 files, 0 disagreements** `[SPEC-RLK-088]`. It lowers the risk without retiring it — the Symphonia spike agreed on six files and then disagreed on sixty of 5,705.

**`[IMPL-SUI-070]` What a running player does and does not notice, corrected.** This was first recorded as *"the player did not see them until restarted"*, and that was **wrong** — the query was `/browse/tracks?q=Frisina`, and tracks match on **title**. No track is titled "Frisina". The same mistake had already been made and caught once on the desktop, and it was then misread as staleness. Retested against a restarted player holding the tracks: `?q=Frisina` still returns `[]`, and `?q=Duala` returns the track. The empty result was never about caching.

The real division is in the code, and only one half needs a restart:

| | reads | sees an import |
| :--- | :--- | :--- |
| **Browse** `/browse/*` | a fresh connection per request, live SQL | **immediately** — no restart |
| **Program Director** | one in-memory snapshot from `Director::load` | **not until restarted** |

`Director::load` runs once inside `Session::open` and builds the candidate rows, flavor index, artist map, play recency, relations, occasions and Taste centroids. **Nothing reloads it** — there is no refresh path in the tree. So imported music is browsable and queueable by hand at once, and cannot be *selected* until the player restarts.

**`[IMPL-SUI-075]` A live rebuild is affordable, and the queue is the right buffer.** Measured by [`dircheck`](../player/src/bin/dircheck.rs), which exists so this is a number rather than an argument — the same shape as `memcheck` for `[REQ-AUD-110]`:

| 8,330 radio passages | desktop x86_64 | appliance Pi Zero 2W |
| :--- | ---: | ---: |
| `Director::load` | **0.89 s** | **9.86 s** |
| first load, peak RSS | 139 MB | 118 MB |
| **each further Director** | **10.7 MB** | **12.5 MB** |

**The first-load figure is not the Director.** It is the Director plus SQLite's page cache plus query scratch, all one-time; holding two, three and four in turn adds 10.5–12.6 MB each, consistently. An earlier recommendation here argued *against* a live reload on the strength of that 118 MB, and it was **wrong** — a build-then-swap transiently costs about **12 MB** against a 150 MB budget `[GDE-ARC-050]`.

Three things make it work:

1. **The Director is off the audio path.** It chooses what plays *next*; decode, mix and output never consult it. A rebuild therefore cannot glitch a note — the worst case is a late refill of a queue that is still playing.
2. **`Director` is `Send`**, asserted at compile time in `dircheck` so that gaining a `Connection` or an `Rc` breaks the build rather than the reload. The replacement builds on its own thread with its own connection and is handed over when ready, so the running one keeps answering selections throughout and there is never a window with none.
3. **A 180 s queue covers a 9.86 s rebuild 18× on the appliance**, 200× on the desktop. The threshold's real value is not CPU but **I/O**: the load is heavy SQLite reading from an SD card, and starting it only when three minutes of audio is already buffered keeps decode far enough ahead not to contend with it.

Degradation is already defined: `session.rs` falls back to uniform random selection when the Director is absent `[SPEC-DIR-*]`, so even a drop-then-load — unnecessary at 12 MB — would have stated behaviour rather than a stall.

**`[IMPL-SUI-078]` Built and proved 2026-08-20.** `POST /library/reload` sets an intent in `Controls` — the cell `manual_program` already uses, and for the same reason: the Director is not `Sync`, so the browser cannot reach it. `Session::refill` picks the intent up **before** its shortfall check, since a rebuild is precisely what must not start while the queue is short.

Measured against a running player, a copy of the live library, and one track imported underneath it:

| | pool (eligible / total) | `reload_status` |
| :--- | ---: | :--- |
| at startup | 8,069 / **8,330** | — |
| after import, before reload | 8,069 / **8,330** | — |
| after `POST /library/reload` | 7,772 / **8,331** | `rebuilt` |
| a second reload, nothing imported | 7,772 / **8,331** | `rebuilt` |

**Total moves by exactly the one passage imported**, and browse had it the whole time — the split of `[IMPL-SUI-070]` demonstrated from both sides at once. The queue stayed at 6 and playback was never interrupted.

**The `eligible` drop is the queue, not drift.** The pool is first published on the opening refill, before the queue has been filled, so nothing is rotation-suppressed yet; after adoption the six queued passages are re-noted and their recordings and artists suppressed. A second reload reproduces 7,772 / 8,331 exactly, which is what makes it stability rather than a leak.

Two details that are easy to get wrong and are not:

- **Queued passages are re-noted on adoption.** A replacement's `last_played` comes from `listener_play_history`, which knows nothing of passages queued but not yet played, so without re-noting it would treat their artists as un-suppressed and could pick a sibling rotation had ruled out `[REQ-PD-112]`. The notes are rebuilt rather than moved, because each one holds the *previous* value from the Director that issued it.
- **A failed rebuild costs nothing.** The incumbent is never dropped, so a thread that dies without answering leaves selection exactly as it was and only the status line changes.

The console offers it on the jobs page, and **the browser posts it, not the console's server** — the same allowance the handoff embed relies on `[SPEC-SUI-025]`. No Vipunen process speaks to a Lempi process; a page in a tab asks a service on the same machine.

**`[IMPL-SUI-079]` Deployed to the appliance 2026-08-20.** Cross-compiled and shipped by [`install-player.sh`](../build/install-player.sh) (then named `deploy-player.sh`, under `LempiPi/`), which proved the running process answers as the new build before keeping it. `POST /library/reload` returns **202** there and a snapshot caught the rebuild in flight — `reload_status: rebuilding`, then `rebuilt` — with the queue unmoved at 6 and the service active.

Two observations from the appliance that are **not** about this change, and are worth separating from it:

- **Its Director has nothing to choose.** The live database is still the 31-file test library, and against 37,481 plays of those few recordings the census reads **0 eligible of 35**. The full library sits staged beside it as `lempi-new.db`, unswapped. The 9.86 s rebuild figure above is `dircheck` against *that* file; the live one rebuilds in a moment because it is tiny.
- **The audio path is opening onto a dummy sink.** `aplay -l` offers only `vc4hdmi`, and the supervisor reports *"output opened onto a dummy — still silent, retrying"* rather than pretending `[SPEC-APS-060]`. **Checked before attributing it:** the journal shows the identical sequence under the previous binary (pid 1187, 17:46) and the new one (pid 2004, 17:49) — each opens `default @ 44100 Hz` and reports the dummy ~26 s later. It predates the deploy and is a speaker being absent, not a regression.

**`[IMPL-SUI-067]` What the appliance's own database proves.** It holds **27 files and 37,481 plays** — *more* listener history than the desktop's 37,238, because it has been the thing actually playing music. Shipping `lempi.db` would overwrite 37,481 irreplaceable rows with 37,238 different ones. `[SPEC-SUI-100]`'s argument stops being a principle here and becomes an arithmetic fact about two files on two machines.

**`[IMPL-SUI-069]` Three faults found, two of them mine.**

1. **`sql/schema.sql` could not receive a bundle.** The import failed on *"no such table: `file_tags`"* — the executable form of SPEC008 was missing it, along with `cover_art`, `id_checks`, `id_reviews` and `musicbrainz_cache`. `file_tags` is library data, not a cache: for audio with no MusicBrainz entry it is the only place an artist name exists `[SPEC-PL-050]`. Added; the other four are tool-owned and left to their tools, which is a judgement worth revisiting.
2. **The dry run created a table.** `imported_payloads`'s DDL ran before the `apply` check, so a run reporting *"nothing was written"* had written something. Report-by-default meaning "almost nothing" is the kind of quiet exception that makes a dry run untrustworthy.
3. **A report that printed "verified 4 of 3".** Two denominators — encodings in the bundle, files in the library — divided against each other. Fixed to state both.

---

## Stage 5 — Handoff and launch

**`[IMPL-SUI-090]` Small, and last because it is small.** `[SPEC-SUI-155]`'s embed-or-link, `[SPEC-SUI-170]`'s launch sequence.

> **Claims:** with no player running, taking a handoff starts one **on the console's own database path** and the passage plays. With a player already running, no second process appears. With the binary renamed so it cannot start, the operator is told which capability is unavailable and why — not shown a dead link `[SPEC-DF-095]`.

> **Done, 2026-08-27.** `ensure_lempi()` (`console.py` at the time, split into `tools/lempi_control.py`) implements the three-step sequence exactly; the profile page's "Review in Lempi" box embeds `/review?passage=:id`, with an unconditional new-tab link beside it rather than embed-failure detection `[SPEC-SUI-155]`. Verified against a real co-resident pair: `/api/handoff/ensure` on a cold console started `lempi.exe` on Vipunen's own db path and it answered within seconds; called again with one already running, `started: false` and no second process. All of it — the review page, the handoff route, the deep link — sits behind `vipunen-support` `[SPEC-SUI-190]`, off by default; the appliance binary measured ≈200 KB smaller without it *(that figure was corrected to 3.05 MB on 2026-09-11 — see `[SPEC-SUI-190]`)*.

---

## Not on this path

**`[IMPL-SUI-080]` The waveform editor is Lempi's, and it is `[GDE-PHS-040]`'s third deliverable, not this one's.** `[SPEC-SUI-135]` settles *where* it lives; `[SPEC-SA-080]` says what it must do; neither is a reason to build it before the console can reach it. It wants its own requirements and specification first, exactly as segmentation does.

> **Designed 2026-08-27; build under way** — [SPEC021](spec/SPEC021-waveform-boundary-editor.md), alongside `[REQ-LIB-180]`'s MusicBrainz search (design in [SPEC010 §3](spec/SPEC010-identification-review.md#3-searching-musicbrainz-directly)). Build order for both is [IMPL006](IMPL006-vipunen-editing-workflows.md), which starts where this document's Stage 5 ends and is now complete — Stages 6 through 10.

**`[IMPL-SUI-085]` Segmentation stays provisional** `[SPEC-SA-070]`. Stage 0 works because single-recording files need no segmentation; a DAO capture arriving before `[GDE-PHS-040]` lands still needs hand work, and the console must say so rather than produce one whole-file passage and call it done `[SPEC-SUI-075]`.

---

**Traceability:** `[IMPL-SUI-010..090]` · implements `[SPEC-SUI-010..180]` · sits under `[GDE-PHS-040]`
