# IMPL009: The Database Split — Implementation Plan

**Implementation Guide — the plan, reviewed for gaps before starting**

Split from [IMPL002](IMPL002-database-split.md) on 2026-09-10, which had reached 948 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [IMPL002](IMPL002-database-split.md) is the front of this series · [PI001](PI001-image-and-partitions.md) for the partitions

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-5 in [IMPL002](IMPL002-database-split.md), §6/§8/§9 in [IMPL010](IMPL010-database-split-review.md), §7 in [IMPL009](IMPL009-database-split-plan.md), §10-19 in [IMPL011](IMPL011-database-split-built.md).

## 7. Implementation plan, reviewed for gaps before starting

Asked directly to plan the implementation carefully and review that plan
for gaps, oversights, conflicts, and ambiguities before proceeding. Doing
that review against the actual schema and the actual tools directory (not
just against §1–6 above) found four more real problems — one of them a
genuine conflict this document's own §3 would otherwise have walked into.
Each is stated as found, then fixed.

### 7.1 Gap: `CREATE TABLE ... AS SELECT` silently drops schema, not just data

§5's migration sketch said "copy every table... via `CREATE TABLE ... AS
SELECT * FROM source.<table>`." Checked against the actual schema rather
than left as a plausible-sounding sentence: **that statement does not
preserve indexes, `UNIQUE`/`CHECK`/`NOT NULL` constraints, `DEFAULT`
values, or foreign keys** — SQLite's own documented behavior, not a bug —
it copies column names and data only.

This is not a theoretical loss. `bundle.rs` creates
`CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms)`
— the constraint that stops a re-import from silently duplicating a
passage. `player_store.rs` creates four more (`idx_file_tags_album`,
`idx_file_tags_artist`, `idx_release_recordings_chosen`, plus the two on
`listener_play_history` and one on `listener_rejections`). A split built
exactly as §5 first described would produce a `library.db` that accepts
duplicate passages the source database never could.

**Fix:** the migration tool reads each table's real DDL from
`sqlite_master` (`CREATE TABLE`/`CREATE INDEX`/`CREATE TRIGGER` text,
verbatim) and executes that on the new file *before* copying data with
`INSERT INTO ... SELECT`, table by table. §5 is corrected below to say this
instead of `CREATE TABLE ... AS SELECT`.

### 7.2 Checked, not assumed: no foreign key crosses the boundary

SQLite does not enforce a `FOREIGN KEY` across two attached databases —
only within one file. Before assuming this doesn't matter, checked every
`REFERENCES` in the schema: the only two are `recording_artists.mbid
REFERENCES recordings(mbid)` and `recording_artists.artist_mbid REFERENCES
artists(mbid)` — both tables land on `library.db`, so both stay
enforceable exactly as they are today. **No table on either side
references a table that ends up on the other side.** Stated here as a
checked fact so a future schema change that *does* add a cross-boundary
reference has something to contradict, rather than the split silently
having assumed this forever.

### 7.3 Conflict: the query rewrite in §3 would have broken `bose`

§3 said every catalog reference "gets a `lib.` prefix." Read literally,
that rewrite ships in the one player binary both appliances run — `bose`
included, which is staying single-file (`[IMPL-BOS-078]`) for the
foreseeable future. A hardcoded `lib.` prefix has no `lib` schema to
resolve against unless something is always attached under that name, and
attaching a database's *own* file to itself under a second alias is not a
pattern this design should lean on untested — SQLite documents `ATTACH`
for a second file, and a same-file self-attach's locking behavior under a
real read/write connection was not going to be verified by assuming it
works.

**Fix:** qualify with a runtime alias, not a hardcoded string. One shared
helper —

```rust
/// Attaches `library_path` as `lib` when it differs from the connection's
/// own file, and returns the schema prefix every catalog query should use.
/// The two paths are equal for every installation that hasn't split yet
/// (bose, today's lempi02w, every existing test fixture) -- in that case
/// nothing is attached and this returns "main", so a query written
/// `{LIB}.recordings` reads the same table through the same connection it
/// always has. Only an installation with a genuinely separate library.db
/// ever attaches anything or ever sees "lib" come back.
pub fn attach_library(conn: &Connection, db_path: &Path, library_path: &Path)
    -> Result<&'static str, DbError>
```

— used by all three of today's independent connection-opening sites
(`Library::open`, `PlayerStore::open`, the bare `Connection::open` in
`bin/mpd_direct.rs`), so the attach/alias decision is made once and cannot
drift between them. Every one of the ~10 call sites in `[IMPL-DBSPLIT-015]`
is rewritten to interpolate this alias (`format!("... FROM {lib}.recordings ...")`)
rather than a literal `lib.`. `bose` keeps running the same binary,
unmodified, forever — its two paths are just equal.

This also simplifies §5's rollback story: reverting lempi02w's split needs
no binary swap, only pointing `--library` back at the same path as `--db`
(or dropping the flag, with the default being "same as `--db`") — the
binary already handles that case as its normal, most-tested path.

### 7.4 Conflict found in `tools/`: one remote path cannot serve two files

The synchronization review in §4.1 checked `mesh_diff.py` and
`resolve_mesh_conflict.py` and found them clean — they only ever touch
catalog tables. Checking the *rest* of `tools/` before calling
synchronization fully reviewed found three more scripts that assume one
remote path gives access to **both** halves at once:

- `sync_preferences.py` syncs `listener_preferences` (listener-side) but
  its own docstring requires checking "a real artist/recording on *both*
  sides" — a catalog-side existence check — through the same connection.
- `remote_flags.py` and `export_flags.py` fetch flagged recordings/passages
  by joining `listener_flags` (listener-side) against `recordings`/
  `passages` (catalog-side).

All three reach a peer through `tools/jobs.py`'s `sync_peers.remote` /
`remote_config`'s single `sync_remote` value — one string, `user@host:/path`.
Once lempi02w is split, that one path is either `library.db` (and these
three tools lose the listener tables they need) or `listener.db` (and they
lose the catalog tables) — it cannot be both. This is a real conflict
`[IMPL-DBSPLIT-005]`'s "no sync-tool changes needed" did not cover, because
`mesh_diff`/`resolve_mesh_conflict` were the only two tools actually
checked at the time.

**Fix:** `sync_peers` gains a second, nullable column,
`remote_listener TEXT` (`ALTER TABLE sync_peers ADD COLUMN remote_listener
TEXT`, additive and safe against every existing row), read as "same file as
`remote`" when `NULL` — true for every peer that hasn't split, `bose` and
today's lempi02w included, so nothing already configured needs re-entering.
`remote_config` gets the equivalent second key,
`sync_remote_listener`. `sync_preferences.py`/`remote_flags.py`/
`export_flags.py` each take the listener path from the new field and the
catalog path from the existing one; `mesh_diff.py`/`resolve_mesh_conflict.py`
are untouched, since they never wanted the second path at all. The console
UI's peer form (`console_web/mesh.html`) gains one more optional input.

### 7.5 Sequencing gap: the migration tool cannot read a live-written file safely

§5 did not say the source `lempi.db` must be static while the split runs.
Opening it through `sqlite3` (not a raw file copy) already handles WAL
correctly — committed frames included, uncommitted ones invisible, exactly
as a normal reader sees them — but "safely readable" and "a consistent
snapshot to build a replacement from" are different guarantees. If `lempi`
is still running and writing during the split, the two new files can
legitimately each be correct as of slightly different moments, and the
listener state committed in between is simply gone from both.

**Fix, folded into the live-migration runbook (§8) rather than left
implicit:** the split only ever runs with `lempi` and `mpd` stopped on the
target machine. Order: stop services → run the split tool against the now-
static file → verify → point the unit's arguments at the two new files →
start services → verify again. A rehearsal against a *downloaded copy* of
the live file (§8) has no such requirement, since nothing there is being
promoted to production.

### 7.6 Gap, found only by checking that the schema still works for `bose` and local: `PlayerStore`'s bootstrap can't just disappear

Asked directly whether the resulting schema stays equally functional on
`bose` and the local dev instance, not only lempi02w — checking that
question against §4.3's own resolution found it had overreached. "Move
this table/column creation out of runtime `open()` entirely" would have
stopped `PlayerStore` from ever creating `file_tags`/`cover_art`/
`release_recordings`'s tuning columns **even on `bose` and local, which
are not splitting and whose `PlayerStore` connection genuinely still owns
that data.** That is exactly the fresh-library bootstrap failure the
original doc comment ("a library whose scan was already complete never
reached it and browsing died on a missing column") existed to prevent —
the review would have reintroduced the bug it was quoting as the reason
not to.

**Fixed, per `[§4.3]`'s correction above:** the bootstrap stays,
unconditionally, for the case that matters unchanged today — `alias ==
"main"`. It is skipped only when `alias == "lib"`, i.e. only on an
installation that has actually split, because in that case `PlayerStore`
has no writable path to run it against at all.

That still leaves a real gap for the split case alone: `tagscan.rs` opens
`library.db` via `Library::open_writable` and assumes `file_tags` and its
two indexes already exist — true today only because `PlayerStore` created
them first. **Checked, not assumed:** `tools/fetch_cover_art.py` already
creates its own `cover_art` table (`CREATE TABLE IF NOT EXISTS`) before
using it — already self-sufficient, needs no change. `tagscan.rs` does
not have the equivalent for `file_tags` and needs it added, so a
split-native `library.db` — one built by Vipunen/`tagscan` directly rather
than descended from a single-file installation's split — bootstraps
correctly too, not only one that inherited the tables from a
pre-existing `PlayerStore` run.

### 7.7 Found in the real schema, not the source: a foreign key does cross the boundary after all

`[§7.2]` checked `player/core/src/db/mod.rs`'s and `player_store.rs`'s
in-tree `CREATE TABLE` text for a `REFERENCES` crossing the split and found
none. Checking the *actual deployed schema* on all three environments
directly — `sqlite_master`, not the Rust source — for this pass found one:

```sql
-- local, bose, and lempi02w all agree, byte-for-byte:
CREATE TABLE listener_play_history (
    ...
    passage_id  INTEGER REFERENCES passages(passage_id) ON DELETE SET NULL,
    -- denormalised on purpose: six years of history must survive a rescan
    -- that renumbers passages [SPEC-SC-095]
    ...
```

`listener_play_history` (C) references `passages` (B) — a real
cross-boundary foreign key, present identically on every installation
checked. It is not in the current `PLAY_TABLE` constant in
`player_store.rs`, which defines `passage_id` with no `REFERENCES` clause
at all: the deployed databases carry a constraint an earlier version of
this file's schema added and a later simplification of the source stopped
re-stating — `CREATE TABLE IF NOT EXISTS` never retroactively strips a
constraint from a table that already exists, so the two have quietly
disagreed since whenever that simplification landed, on every
installation, unnoticed until this check went to `sqlite_master` instead
of the source. This is direct confirmation that `[§7.1]`'s migration tool
must keep copying DDL from the live database rather than reconstructing it
from the Rust constants, which are demonstrably not the same thing today.

**A second one, found later while enumerating every real table for
`tools/split_database.py` (§8) rather than only the ones already named
here:** `selection_decisions` (C) carries two — `passage_id REFERENCES
passages(passage_id)` (B) and `program_id REFERENCES
listener_programs(program_id)` (C, so unaffected). Same shape, same
conclusion below: nothing in the player enables `foreign_keys`, so this
has never been enforced from playback, split or not.

**And a third, found on 2026-09-10 when this prerequisite was actually
built:** `player_state` (C) carries `passage_id REFERENCES
passages(passage_id) ON DELETE SET NULL` as well — the resume point. Found
the same way as the other two, by reading `sqlite_master` on the real local
database rather than the Rust source, which is the third time that method
has returned something the source does not say. The count in this section
was two; it is three, and `[IMPL-DBSPLIT-060]`'s replacement clears all
three. Losing this one silently would mean a resume point pointing at a
passage a rescan had renumbered — the player would resume the wrong track,
or none.

**Whether this matters depends on who's asking, and the answer differs by
environment:**

- SQLite does not enforce a `FOREIGN KEY` across two `ATTACH`ed databases.
  Once `passages` and `listener_play_history` are in different files, this
  constraint's `ON DELETE SET NULL` cascade simply stops firing — silently,
  since a query naming a schema-qualified table across an attach boundary
  doesn't error, it just doesn't get FK enforcement it would have gotten
  in one file.
- The player's own connections never `PRAGMA foreign_keys = ON`
  (checked — zero occurrences in `player/src/`), so this cascade has never
  fired from anything the player itself does, split or not.
- It **is** live today on the desktop: `segment_dao.py`, `apply_changes.py`,
  `apply_reviews.py`, `ingest_cd.py`, `ingest_folder.py`,
  `backfill_album_cuts.py`, `apply_boundary_reviews.py`, and
  `accept_remote_basis.py` all set `PRAGMA foreign_keys = ON`, and all run
  against the local desktop database when Vipunen renumbers or deletes a
  passage — exactly the six-years-of-history case the inline comment
  names. **Neither `bose` nor `lempi02w` ever runs any of these tools
  against its own database** — segmentation labor is desktop-only per
  `[SPEC035]`'s own decision that "Vipunen work only happens on Vipunen-capable
  nodes."

**Conclusion: does not block lempi02w's migration.** lempi02w never
exercises this cascade today and won't after splitting, for the same
reason it doesn't today — it has no Vipunen. It also does not affect
`bose`, which isn't splitting. **It is a real, tracked prerequisite for
ever splitting the *local* database specifically** — before that could
happen safely, the eight tools above need an explicit application-level
replacement for the cascade (an `UPDATE listener_play_history SET
passage_id = NULL WHERE passage_id NOT IN (SELECT passage_id FROM
passages)`-shaped step run by whichever of them deletes or renumbers a
passage), matching the pattern `backup.rs::restore()` already uses for
cross-database consistency it can't get from a database-level constraint.

**`[IMPL-DBSPLIT-060]` Built 2026-09-10** as `tools/passage_orphans.py`.
One `clear(conn)` call, correct before a split as well as after: in a
single file the cascade has already fired and it clears nothing; on a
split pair opened so both halves are visible on one connection, it is the
only thing that does the work. Wired into the two tools that actually
delete passages — `segment_dao.py` (re-segmenting a file) and
`repair_durations.py` (deleting phantoms) — and available as a CLI to
sweep a database already damaged.

`repair_durations.py` turned out never to have set `PRAGMA foreign_keys =
ON` at all, so it is not in the eight listed above and its passage deletes
have been orphaning listener references in a *single* file too, for as
long as it has existed. The split did not create that bug; it would have
made it universal. The local database was checked when this was built and
carries zero orphans across all three columns, so nothing needed
repairing — but that is luck about which tool has been run lately, not a
property of the design.

---

