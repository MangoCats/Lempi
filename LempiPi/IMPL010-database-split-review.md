# IMPL004: The Database Split — Scope, Correction and Review

**Implementation Guide — what the plan got wrong before it was run**

Split from [IMPL002](IMPL002-database-split.md) on 2026-09-10, which had reached 948 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [IMPL002](IMPL002-database-split.md) is the front of this series · [PI001](PI001-image-and-partitions.md) for the partitions

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-5 in [IMPL002](IMPL002-database-split.md), §6/§8/§9 in [IMPL010](IMPL010-database-split-review.md), §7 in [IMPL009](IMPL009-database-split-plan.md), §10-19 in [IMPL011](IMPL011-database-split-built.md).

## 6. Player refactor: scope, not yet built

**`[IMPL-DBSPLIT-050]` Enumerated, bounded, not yet implemented:**

- New `db::AttachedDb` (or equivalent) wrapping the `ATTACH` open, replacing
  the three independent `Connection::open`/`Connection::open_with_flags`
  call sites in `library.rs`, `player_store.rs`, and `bin/mpd_direct.rs`.
- 8 call sites in `db/library.rs` (§2) and 2 in `director/library.rs`
  get a `lib.` prefix on their catalog-table references.
- `PlayerStore::open()` loses the `TAG_TABLE`/`ART_TABLE` creation and the
  `release_recordings` `ALTER`s (§4.3); those move into
  `tools/split_database.py` (for a fresh split) and into whatever
  `tagscan`/`fetch_cover_art.py` already do when opening B writable (for a
  library.db that already exists and gains a tool it hasn't seen before).
- `backup.rs::restore()` needs its own pass: it currently reasons about
  "the live file" as one thing to compare a snapshot against, and after the
  split that reasoning must say which of the two live files a given
  restored table belongs to. Not yet worked through in detail — flagged
  here rather than glossed over.
- Every test fixture across these files that builds one in-memory or
  temp-file connection with both table families present needs either a
  second attached temp file or an equivalent in-memory `ATTACH` — this is
  the largest *count* of individual edits (dozens of test setup blocks),
  though each one is mechanical once the pattern is established for the
  first.

**This is real, multi-file, cross-cutting work — sized honestly as at least
one focused implementation pass with its own test cycle, not a
same-turn addition to a design review.** Building it carefully, with the
project's own TDD discipline and full regression between changes, is the
right next step; committing it hastily onto a live appliance holding
irreplaceable listener history (`[PI-C-020]`) is exactly the failure mode
this whole design exists to avoid.

---

## 8. The migration procedure, corrected

Supersedes §5's sketch with the fixes from §7.

**`tools/split_database.py`**, same rehearse-by-default shape:

```
python tools/split_database.py lempi.db --library-out library.db --listener-out listener.db
python tools/split_database.py lempi.db --library-out library.db --listener-out listener.db --commit
```

1. Open the source read-only.
2. For each table in §3's B column: read its `CREATE TABLE` statement and
   every associated `CREATE INDEX`/`CREATE TRIGGER` statement from
   `sqlite_master`, execute them verbatim against `library.db`, then
   `INSERT INTO library.<table> SELECT * FROM source.<table>` — DDL first,
   data second, per `[§7.1]`. Same procedure against `listener.db` for the
   C column.
3. Verify before anything is promoted: row counts match table-for-table;
   `PRAGMA integrity_check` passes on both new files; spot-check primary
   keys; confirm every index named in the source's `sqlite_master` exists
   in the corresponding new file (not just that *a* index exists).
4. Report only, unless `--commit`; `--commit` is the only mode that writes
   to the real output paths rather than a temp directory.
5. Never deletes or modifies the source file.

**Live runbook against lempipi**, once the player refactor (§7.3's
`attach_library` helper and the ~10 rewritten call sites) has its own full
test cycle behind it and the tool above has been rehearsed against a
downloaded copy of lempipi's actual database (not synthetic data, not
bose's):

1. Off-device backup of lempipi's current `lempi.db`, per `[PI-C-030]` —
   mandatory, not conditional on anything else in this list.
2. Stop `lempi` and `mpd` on lempipi.
3. Copy `lempi.db` aside on lempipi itself (timestamped, not deleted —
   `[IMPL-DBSPLIT-045]`).
4. Run `split_database.py --commit` against that copy, producing
   `library.db` on B and `listener.db` on C.
5. Verify (step 3 of the tool's own procedure) before touching the running
   configuration at all.
6. Update the service unit's arguments to the two new paths.
7. Start `lempi` and `mpd`; verify both active, and verify audibly that
   playback still works — the same "heard it play" discipline
   `[IMPL-BOS-120]` already requires before any lock-in-style point of
   no return.
8. Update `sync_peers`/`remote_config` for lempipi's entry (§7.4) so
   `sync_preferences.py`/`remote_flags.py`/`export_flags.py` keep working
   from whichever machine drives them against lempipi.

Rollback at any point through step 6: stop services, point the unit's
arguments back at the single original `lempi.db` copy from step 3, start
services — no binary change needed, per `[§7.3]`.

---

## 9. Architectural review: indexed performance, and one more footgun found

Asked directly whether the split matches or beats today's query performance,
and whether the design is clean rather than merely correct. Checked both
against the real, identified call sites rather than against SQLite's
documentation in the abstract.

**Performance: measured, not assumed.** Every real cross-boundary reference
found in `[§2]`/`[§7]` is either a correlated scalar subquery/`EXISTS` check
(`PLAYS_EXPR`, `DESCRIBE`, the `listener_flags` check in `play_history`) or
independent single-schema queries combined by `UNION ALL`/`CASE` in SQL or by
a `HashMap` in Rust (`director/library.rs::load_taste`) — **never a bare
`JOIN` across the boundary at library scale.** That shape matters: a
correlated subquery's per-row cost is one indexed B-tree seek, and a B-tree's
seek cost does not change based on which file backs it. Built a real
two-file split of `browse_artists`' actual query shape against this
project's own 8,185 recordings plus 50,000 synthetic play-history rows and
ran `EXPLAIN QUERY PLAN` on both:

```
split:  SEARCH h USING COVERING INDEX listener_play_mbid (mbid=?)   -- 10.7ms / 8,185 rows
single: SEARCH h USING COVERING INDEX listener_play_mbid (mbid=?)   -- 10.1ms / 8,185 rows
```

**Identical query plan, indistinguishable timing** (the ~0.6ms difference is
noise at this scale). The split costs nothing here because the query shapes
already avoid the one pattern that would cost something — a real
constraint on future query design, not just a property of today's code:
**a bulk `JOIN` between a catalog table and a listener table, at full-library
scale, is the thing to avoid post-split** — not because SQLite can't use
indexes across an attach (it can, and does, as measured), but because a true
join needs the optimizer to choose a join order across two schemas, which is
less exercised territory than the correlated-subquery shape every current
call site already uses. Recommend this measurement become a real Rust
integration test in Phase 2 (rusqlite can run `EXPLAIN QUERY PLAN` directly),
so a future query that reintroduces a bulk cross-boundary join fails a test
rather than surfacing as a slow page someone eventually notices.

**Cleanliness: one more real footgun found, plus three checked and cleared.**

- **Found:** `[§4.3]`/`[§7.6]`'s conditional bootstrap (`PlayerStore` creates
  B-side tables only when `alias == "main"`) is correct but, written as a
  scattered `if` around each `CREATE`/`ALTER`, is exactly the kind of thing a
  later change forgets to guard. **Fix, before Phase 2 writes any of it:**
  one function, `ensure_library_tables_if_owned(conn, alias)`, called once,
  holding every one of those statements and the single guard — matching
  `attach_library`'s own "one place, not scattered" discipline rather than
  introducing a second kind of scatter to replace the first.
- **Checked, clear: no `CREATE TRIGGER` anywhere in this schema** (confirmed
  by the same grep that found the indexes in `[§7.1]`). Worth stating as a
  standing constraint, not just an absence: SQLite triggers cannot reference
  objects in a different attached database, so this split forecloses ever
  writing one that reacts to a catalog-table change by updating a
  listener-side column, or vice versa. Nothing today wants that; a future
  design should know it isn't available before reaching for it.
- **Checked, clear: no new denormalization.** The one column that already
  duplicates identity across the boundary — `listener_play_history.mbid`,
  alongside the FK-linked `passage_id` `[§7.7]` — predates this design
  entirely and exists for its own documented reason (surviving a rescan).
  The split adds no new duplicated column anywhere.
- **Checked, clear: the two-path peer model's failure mode is loud, not
  silent.** `[§7.4]`'s `remote`/`remote_listener` split means a
  misconfigured peer (wrong path in the wrong field) fails with "no such
  table" the first time a job runs against it, not a quiet wrong-answer —
  `mesh_diff`'s and `sync_preferences`' table names are disjoint by
  construction, so there's no name that resolves to the wrong file's table
  and returns a plausible-looking wrong result instead of an error.

**Verdict: solid.** Three review passes plus this one found six real
problems and fixed all of them before any of the migration itself ran;
this pass found a seventh (the bootstrap centralization) and confirmed
performance empirically rather than by assumption. Nothing found across
four passes changes the conclusion that the design delivers its intended
functionality — write-safety and backup-size improvements per `[§4.4]` —
at no performance cost, provided Phase 2 keeps every future catalog↔listener
query in the correlated-subquery shape already established rather than
introducing a bulk join.

---

