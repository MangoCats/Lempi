# IMPL005: The Database Split — Built and Proven

**Implementation Guide — the migration as it actually ran**

Split from [IMPL002](IMPL002-database-split.md) on 2026-09-10, which had reached 948 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [IMPL002](IMPL002-database-split.md) is the front of this series · [PI001](PI001-image-and-partitions.md) for the partitions

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-5 in [IMPL002](IMPL002-database-split.md), §6/§8/§9 in [IMPL010](IMPL010-database-split-review.md), §7 in [IMPL009](IMPL009-database-split-plan.md), §10-19 in [IMPL011](IMPL011-database-split-built.md).

## 10. Status

**Designed in detail, reviewed four times** — against synchronization,
RAM, backup, and MPD (§4); against the plan itself for gaps, oversights,
conflicts, and ambiguities (§7.1–7.5); against whether the resulting
schema stays equally functional on `bose` and the local instance, not only
lempipi (§7.6–7.7); and against indexed query performance and
architectural cleanliness (§9). Seven real problems found across the
second through fourth passes and fixed on paper before any of this was
built: a schema-fidelity gap in the migration tool, a compatibility
conflict that would have broken `bose`, a gap in the `tools/` peer-path
model affecting three existing scripts, a sequencing requirement the
original sketch left implicit, an overcorrection in `[§4.3]`'s own fix
that would have broken `bose`/local's fresh-library bootstrap, a real
cross-boundary foreign key present in every deployed database checked but
absent from the current Rust source — found by querying `sqlite_master`
directly rather than trusting the source, confirmed to not block lempipi
or `bose` while being named as a genuine prerequisite for ever splitting
*local* specifically — and a bootstrap-guard centralization the
performance/cleanliness pass caught before Phase 2 could scatter it.

**Performance confirmed, not assumed**: a real two-file split of this
project's own 8,185 recordings plus 50,000 synthetic plays, `EXPLAIN QUERY
PLAN`'d against both the split and single-file shapes, produced the
identical plan and indistinguishable timing (§9). The design costs nothing
because every real call site already uses correlated subqueries rather
than a bulk cross-boundary join — now a documented constraint on future
query design, not just an incidental property of today's code.

**Confirmed equally functional across all three environments for the work
this document actually scopes**: the `attach_library` alias approach
(`[§7.3]`) makes the one player binary behave identically on `bose` and
local (nothing attached, `alias = "main"`, unchanged) and on a split
lempipi (`lib` attached read-only) — no environment-specific binary, no
behavior change for the two that aren't splitting. The one open item
(`[§7.7]`) is scoped to a future local split, not to anything being built
or deployed now.

## 11. Player refactor: built and proven

**`attach_library()` and `QualifyingConn`** (`db/mod.rs`): built exactly as
`[§7.3]` specified — `"main"` when the two paths are equal, an attached
read-only `"lib"` only when they genuinely differ, one shared
`prepare`/`query_row`/`execute`/`execute_batch` rewrite point so every other
`Connection` method reaches through `Deref` unchanged. TDD'd against the
real linked SQLite before anything used it: same-path, different-path
(read-only enforced), two independent connections attaching the same file.

**`Library` and `PlayerStore`** both now hold a `QualifyingConn`. Every real
catalog-table reference found in `[§2]`/`[§7.6]` — 58+ sites in
`library.rs`, 8 in `player_store.rs`, plus `Director`/`FlavorIndex`'s own —
qualified with the `__LIB__.` placeholder and proven to resolve correctly
under both modes. `ensure_library_tables_if_owned()` built exactly as
`[§9]`'s cleanliness pass specified: one function, one guard
(`lib_alias() == "main"`), not scattered conditionals. `Library::open_writable`
made self-sufficient (`[§7.6]`) — it now calls `ensure_tag_table()` itself.

**A real end-to-end safety net, not just unit tests**: `library.rs` gained
an integration test that physically splits the existing `historyable()`
fixture across two real files — copying each table's actual DDL from
`sqlite_master`, not a second hand-written copy that could drift — opens
`Library::open_split` against them, and reruns `play_history`'s exact
assertions. It passes: title, artist, and album all resolve correctly
through the attached schema. This is the test that would catch a future
missed `__LIB__` prefix as a loud failure, not a slow page discovered by
a person.

Full regression throughout: 385 lib tests, all integration tests, the
whole workspace (every bin target) — green at each step.

## 12. The core playback path: wired

`bin/lempi.rs` (confirmed against `bose`'s actual systemd invocation to be
the real production binary — `mpd_direct.rs` is a dev tool) gained a
`--library` flag, defaulting to the `--db` path like every other split-aware
entry point. Threaded to all three places that used to clone `db` alone:
the tag-scan thread now scans `library` (`file_tags` is B-side), the engine
thread carries both into `Session::open`, and the backup thread was checked
and left on `db` alone — `backup.rs`'s own `LISTENER_TABLES` constant and
its doc comment ("Deliberately NOT here: `files`, `passages`, `recordings`,
...") confirm backup is already listener-only by design, needing no second
path. `Session` itself now holds both paths, including in its
rebuild-in-flight closure (`[IMPL-SUI-075]`) — the one place still calling
the single-path `Library::open` after everything else was converted.

**A real, larger-than-expected gap found while wiring this, not assumed
away**: `web::Ui` gained a `library` field, but its ~23 existing handler
call sites — across `browse.rs`, `edit.rs`, `review.rs`, `media.rs`,
`segment.rs`, `preference.rs`, `control.rs`, `bluetooth.rs`, `vipunen.rs` —
all still open only `ui.db.clone()` and call `Library::open`/`PlayerStore::open`
with that one path. On unsplit `bose`/local this is invisible, since `open`
already defaults to same-path. **On a split lempipi, every one of those
pages fails with "no such table" the first time it's opened**, because
`Library::open(db)` becomes `open_split(db, db)` and `attach_library` sees
the two paths equal, resolving catalog references against `main` —
`listener.db` — which does not have them.

This is real remaining work, scoped out of this pass deliberately rather
than rushed: converting 23 call sites across 9 files, each needing a second
captured path threaded into an existing `tokio::task::spawn_blocking`
closure, deserves its own focused pass with the same care the qualified
queries got — not a mechanical sweep squeezed into the tail of this one
with no test coverage of the web handlers' actual database behavior to
catch a mistake. **The core playback path — audio, resume state, tag
scanning, listener backup, radio selection, its periodic rebuild — is fully
split-aware and proven. Lempipi's web UI (browse/edit/review/segment
pages) is not, yet, and must not be assumed to work until this is done.**

Full regression at every step: 385 lib tests, all integration tests, the
whole workspace (every bin target) — green.

## 13. The web layer: closed

All 23 call sites (§12) now clone `ui.library` alongside `ui.db` and open
through `open_split`. Verified by a clean compile (an unused `library`
binding would have warned) and full regression; not further tested at the
handler/HTTP level, since this is purely mechanical threading with no new
SQL — the qualification logic itself is proven at the `Library`/`PlayerStore`
level already. `vipunen.rs`'s one remaining `ui.db` use launches Vipunen's own
console as a subprocess and is out of scope: Vipunen runs on the desktop,
which stays single-file regardless of this work.

## 14. `tools/split_database.py`: built and run against real data

Built exactly to `[§7.1]`'s corrected procedure — every table's real DDL
(`CREATE TABLE` and every associated `CREATE INDEX`) read from the source's
own `sqlite_master` and replayed before `INSERT INTO ... SELECT`, never
`CREATE TABLE ... AS SELECT`. Rehearses by default; `--commit` is the only
mode that writes the real output paths, and refuses outright if either
already exists rather than overwriting. Never opens the source for
anything but reading.

**Enumerating every real table for this** (rather than only the ones
`[§2]`/`[§7.6]` had already named) found two more things worth recording
plainly:

- A second cross-boundary foreign key, `selection_decisions.passage_id →
  passages` — folded into `[§7.7]`'s finding rather than treated as new,
  since the conclusion is identical.
- `schema_meta` (two rows: `schema_version`/`spec`) describes *a file's*
  schema, which is ambiguous the moment there are two files. Judgment
  call, stated as one rather than silently resolved: copied to **both**
  outputs, so a future tool checking a database's schema version finds it
  regardless of which half it opened.

**Tested two ways**: `tools/test_split_database.py` (rehearsal writes
nothing, `--commit` produces two correct files with their indexes intact
and leaves the source untouched, refuses to overwrite an existing output,
tolerates a source missing an optional table) — all pass. Then rehearsed
for real against this project's own `data/lempi_new.db` — the same file
that seeded `bose` — with no synthetic substitute: **1,066,520 rows across
19 catalog tables, 42,321 rows across 15 listener tables, verification
passed**, and confirmed nothing was written to disk, per the rehearsal
contract.

## 16. `sync_peers`'s `remote_listener`: the registry half is done, the query half is not

Built: the additive `ALTER TABLE sync_peers ADD COLUMN remote_listener` migration
(guarded against re-running on a sidecar that already has it), `remote_config`'s
equivalent `sync_remote_listener` key, and `get_remote_listener()`/
`set_remote_listener()`/`list_peers()`/`upsert_peer()`/`activate_peer()` all
carrying the second path through — `NULL` means "same file as `remote`",
true for every peer that hasn't split, so nothing already configured needs
re-entering. `console.py`'s `/api/peers` route and `mesh.html`'s peer form
both accept an optional second address, blank by default. Six tests in
`test_jobs_peers.py` cover the default-to-`None` case, carrying a real
second path through `upsert`→`activate`→`get_remote_listener()`, and
switching from a split peer back to an unsplit one correctly clearing the
stale listener path rather than leaving it active against the wrong peer.

**Not done: the three tools this exists for don't consume it yet.**
`remote_flags.py`'s `FLAGS_SQL` is one query joining `listener_flags` (C)
against `passages`/`files` (B) in a single `sqlite3` invocation over `ssh`
— exactly the shape that cannot run against a split peer's single file, the
reason `remote_listener` exists at all. Making it work needs `remote_peek.py`'s
`run_remote_sql()` to optionally `ATTACH` a second path before running the
query (the same "open listener as main, attach library as `lib`" direction
`[PI-DB-020]` and `attach_library()` already use in Rust), and `FLAGS_SQL`'s
catalog references qualified with `lib.` to match. `sync_preferences.py`
needs the identical treatment for its own existence-check join. `export_flags.py`
runs locally against `self.library` already and needs only to also open
`self.library`'s listener-side file if that ever differs — the smallest of
the three, and the only one not making a remote SQL call at all. None of
this is built; the registry can store and hand back a listener path today,
but nothing yet asks for one.

## 17. Rehearsed against lempipi's actual data, not a substitute

Before ever touching the live device: cross-compiled `lempi` for `aarch64`
(confirmed genuine — `file` reports `ELF 64-bit ... ARM aarch64`), pulled a
read-only copy of lempipi's real `/srv/library/library.db` to the dev host
(1,161,781,248 bytes, matching exactly — this copy also satisfies `[PI-C-030]`'s
off-device backup requirement for the live runbook below, so it's being kept,
not discarded), and rehearsed `split_database.py` against it: 1,065,361
catalog rows across 19 tables, 44,337 listener rows across 15, verification
passed. Then committed the split for real to scratch files and proved it
two ways against actual Rust code, not just the Python tool's own checks:

- `flavorcheck` against the resulting `library.db` alone: 8,151 flavor
  subjects loaded correctly.
- `dircheck` (given an optional second path for exactly this — `[§17]`'s
  own addition) against the real `listener.db`+`library.db` pair:
  **`Director::load` succeeded** — the one function reading
  `listener_play_history`/`listener_likes`/`listener_preferences`/
  `listener_programs` from one file and `recordings`/`artists`/`flavor`/
  `recording_relations`/`passages`/`files` from the other, and the most
  consequential cross-boundary path there is, since it drives actual song
  selection. 8,330 radio passages, 733 ms cold load, 148 MB peak RSS —
  comfortably inside `[REQ-HW-100]`'s budget even before accounting for
  this being lempipi's tighter 464 MB, not the dev host's.

This is real-data proof, not synthetic-fixture proof, for the one thing
that most needed it.

## 18. The live migration: run, and one real bug found and fixed inside the same window

Executed against lempipi for real, 2026-09-07, following `[§8]`'s runbook
exactly: stopped `lempi`/`mpd`, confirmed the WAL was already clean (the
service's own shutdown had checkpointed it — `PRAGMA wal_checkpoint(TRUNCATE)`
afterward reported zero pages, confirming rather than assuming), made a
timestamped on-device copy (`lempi.db.pre-split-20260907` — kept then,
**deleted 2026-09-11** once the split had four days in service and the live
halves were verified to hold everything it did `[PI-PRE-098]`), ran `split_database.py --commit` against that copy on lempipi's
own hardware (1,065,361 catalog rows, 44,337 listener rows — identical to
the dev-host rehearsal, verification passed), deployed the cross-compiled
binary, updated the systemd override's `ExecStart` to the two new paths,
reloaded, and restarted.

**`[IMPL-DBSPLIT-055]` Found within the first minute, from the journal, not
from `systemctl is-active` alone:** `record decision: query: no such table:
main.passages` and `save player state: query: no such table: main.passages`,
on every single call. Root cause: `[§7.7]`'s own review had already found
both cross-boundary foreign keys (`listener_play_history`/
`selection_decisions` → `passages`) but concluded they were inert because
nothing in this crate explicitly enables `PRAGMA foreign_keys` — true, and
the wrong question. Never checked whether **rusqlite's bundled SQLite
defaults it to ON**, which it does, confirmed by asking it directly.
SQLite only ever checks a `FOREIGN KEY` against a table in its own schema,
never an attached one — so every write to a table referencing the
now-attached `passages` failed, silently (both call sites are deliberately
non-fatal — "must never stop the music" — so audio kept playing throughout,
which is exactly why this needed the log, not the service status, to find).

Same discipline as `bose`'s `[IMPL-BOS-165]`/`[IMPL-BOS-166]`: found live,
root-caused, fixed, tested, and redeployed inside the same incident window
rather than left running degraded. Fix: one `PRAGMA foreign_keys = OFF` in
`QualifyingConn::open()`, turned off unconditionally rather than only when
split, since nothing in this crate ever relied on the cascade firing from
the player's own writes. TDD — a new test reproduces the exact failure
shape and confirms the fix — plus full regression (386 tests), before
redeploying. Confirmed fixed against the live system two ways: the journal
stopped showing the error, and `listener.db` queried directly showed fresh
`player_state`/`selection_decisions` rows landing in real time.

**Status: `lempi` and `mpd` both active on lempipi, running the split
database, writes confirmed landing correctly.** Not yet confirmed: audible
playback, the one verification this project has never allowed a machine or
a log line to stand in for at a point of no easy return.

## 19. What remains

- **The three tools' query-level ATTACH support** (§16) — real, scoped,
  understood, not yet built. Does not block the migration — mesh-sync
  catalog diffing already works against a split peer per `[§4.1]`; only
  `remote_flags.py`/`sync_preferences.py`'s own cross-table fetch is
  affected, and neither runs unattended.
- **Hearing it play** — the last step, and the only one that has never
  been substitutable by a green test or an active service.

Scope for the first real implementation and migration pass stays lempipi
only; `bose` and local stay single-file and untouched until lempipi has
proven the split in practice.

