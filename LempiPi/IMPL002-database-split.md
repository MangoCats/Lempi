# IMPL002: The `[PI-DB-010]` Database Split, in Detail

**Implementation Specification — Tier 2 · BUILT — the design it was built from**

> **Status, corrected 2026-09-21.** This header read "DESIGNED, NOT YET BUILT"
> and the paragraph below read "neither appliance has built" it. Both were true
> when written and neither is now. `lempipi` split first; the desktop followed
> on 2026-09-11; and `bose` and `lp3-wifi` each run a separate `--listener` and
> `--library` today, which their own unit files settle in one line rather than
> by inference. The migration as it actually ran is
> [IMPL011](IMPL011-database-split-built.md), and what the local split then
> owed the appliance — with how each item was proved — is
> [PI025](PI025-what-the-local-split-owes-lempipi.md). This document stays the
> design those were built from; read it as design, not as work outstanding.

Everything needed to build the split [PI023 §5](PI023-the-database-split-on-disk.md#5-the-database-split)
had described since early in the project: one `lempi.db` becoming two files,
`library.db` (B) and `listener.db` (C). This document is what PI001 left as
"design, not code" made concrete enough to implement — the exact tables, the
exact call sites in the player, the migration procedure, and what a review
against synchronization, RAM, backup, and MPD turned up.

> **Related:** [PI023 §5](PI023-the-database-split-on-disk.md#5-the-database-split) ·
> [SPEC008 schema](../docs/spec/SPEC008-database-schema.md) ·
> [SPEC035 mesh sync](../docs/spec/SPEC035-mesh-library-sync.md) ·
> [BOSE006 `[IMPL-BOS-078]`](../BosePi/BOSE006-what-lands-where.md#3-what-lands-where), the
> reason `bose` did *not* build this and put the whole file on C instead

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-5 in [IMPL002](IMPL002-database-split.md), §6/§8/§9 in [IMPL010](IMPL010-database-split-review.md), §7 in [IMPL009](IMPL009-database-split-plan.md), §10-19 in [IMPL011](IMPL011-database-split-built.md).

> **Split on 2026-09-10.** The plan is now
> [IMPL009](IMPL009-database-split-plan.md), the scope and the corrected
> procedure [IMPL010](IMPL010-database-split-review.md), and what was built
> and proven [IMPL011](IMPL011-database-split-built.md). This document keeps
> the design and the original migration.
## 1. Why now, and why lempipi first

Asked directly whether splitting lempipi's database would complicate the
mesh-sync tooling built for `[SPEC035]`: it doesn't. `tools/mesh_diff.py`
and `tools/resolve_mesh_conflict.py` already address each side of a diff by
an independent `(path, table)` pair drawn from a hardcoded `TABLES` allowlist
(`files`, `recordings`, `passages` — never a `listener_*` name), so a split
target just means pointing the tool at `library.db` instead of `lempi.db`.

**`[IMPL-DBSPLIT-005]` No change to either sync tool is needed for this.**
That finding is *why* this is safe to build now rather than a reason to
build it — see §5 for the review in full.

lempipi, not bose, is the right first target: bose deliberately substituted
the whole file on C (`[IMPL-BOS-078]`) specifically because building it for
real showed B genuinely goes read-only there, and moving the split's own
work onto a second appliance mid-way through would confuse which machine is
testing what. lempipi's B never becomes read-only in the current design
(`[PI-A-020]`'s successor discussion), so it does not need the same
workaround — it can build the split PI-DB-010 actually describes.

---

## 2. The actual code today: three connections, one file

Reading `[PI-DB-030]`'s claim of "four queries cross the boundary" against
the player source directly (not against the doc) found the real shape is
bigger, but still bounded and enumerable.

**`[IMPL-DBSPLIT-010]` As of this writing, `lempi` opens the *same*
`lempi.db` path three separate times**, for three different reasons:

| Connection | Opened in | Mode | Owns |
| :--- | :--- | :--- | :--- |
| `Library` | `db/library.rs::open()` | `SQLITE_OPEN_READ_ONLY` | catalog browsing, passage lookup, **and** several listener-joined reads |
| `PlayerStore` | `db/player_store.rs::open()` | read-write | `player_state`, listener history/rejection/flags/preferences tables — **and creates two catalog-side tables and one catalog-side column set as a side effect of being "the only writable handle"** |
| a bare `Connection` | `bin/mpd_direct.rs` (the real binary) | default (rw) | feeds `Director::load()`, which reads catalog (`flavor`, `programs`, relations) and listener (`listener_likes`, `listener_preferences`, `listener_settings`) **together, unqualified, in the same queries** |

**`[IMPL-DBSPLIT-015]` Every real (non-test) cross-boundary reference,
enumerated rather than estimated:**

- `db/library.rs`: the `DESCRIBE` constant (play count + last-played, 2
  references), `PLAYS_EXPR` (1), `count_radio`/`play_history`/
  `play_history_count` (5, via `listener_flags`/`listener_play_history`/
  `listener_rejections`) — 8 sites, all reads.
- `director/library.rs::load_taste()`: 2 sites (`listener_likes`,
  `listener_preferences`), each joined in-process against `flavor`
  (catalog) via `FlavorIndex`, not in SQL — no JOIN to rewrite, just the two
  raw `SELECT`s to qualify.
- `backup.rs::restore()`: the class-D bundle-restore path. Already
  cross-database (`ATTACH ... AS snap`) for a *different* reason — comparing
  an imported snapshot against the live file — and needs re-examination
  once the live side is itself two files (§4).
- `PlayerStore::open()`: **not a query to qualify, an ownership question.**
  It creates `file_tags` (`TAG_TABLE`) and `cover_art` (`ART_TABLE`) — both
  listed as B-side tables in PI001 §5's own table — and `ALTER TABLE
  release_recordings ADD COLUMN chosen/position/disc`, altering a catalog
  table. The doc comment names the reason honestly: *"Created here because
  this is the player's only writable handle."* That assumption is exactly
  what a split removes. See §4.3.

Nothing found here contradicts `[PI-DB-010]`'s design — the boundary the
schema already draws (the `listener_` prefix) is still the right line. What
this section corrects is the doc's undersell of how many places currently
assume there is no boundary at all.

---

## 3. File layout and the ATTACH mechanics

**`[IMPL-DBSPLIT-020]` Two files, exactly the tables PI001 §5 already
named:**

| File | Partition | Tables |
| :--- | :--- | :--- |
| `library.db` | B, `/srv/library/library.db` | `files`, `passages`, `passage_recordings`, `recordings`, `artists`, `releases`, `release_recordings`, `flavor`, `cover_art`, `file_tags`, `id_checks`, `programs`, the caches (`lowlevel_cache`, `musicbrainz_cache`, `identification_cache`) |
| `listener.db` | C, `/var/lempi/listener.db` | `player_state`, `player_settings`, `listener_play_history`, `listener_rejections`, `listener_flags`, `listener_preferences`, `listener_likes`, `listener_settings`, `id_reviews`, `selection_decisions` |

**`[IMPL-DBSPLIT-025]` One connection, one ATTACH, matching `[PI-DB-020]`
exactly: the player opens `listener.db` and attaches `library.db` read-only,
never the reverse.**

```sql
ATTACH DATABASE 'file:/srv/library/library.db?mode=ro' AS lib;
PRAGMA main.synchronous = FULL;      -- listener state, per [PI-C-050]
PRAGMA main.journal_mode = WAL;
```

Concretely, this means collapsing today's three separate connections
(`Library`, `PlayerStore`, the bare one in `mpd_direct.rs`) into **one
attach-capable connection type**, or at minimum having each of the three
open `listener.db` and issue the same `ATTACH` rather than three
independent opens of the same combined file. The natural home for this is a
new small wrapper — `db::AttachedDb::open(listener_path, library_path)` —
that every one of the three current call sites constructs instead of
calling `Connection::open` directly. `Library` and `PlayerStore` keep their
existing method surfaces; only what they hold internally changes.

**`[IMPL-DBSPLIT-030]` CLI surface: a new explicit flag, not a directory
convention.** `lempi`'s existing flags are all explicit (`--device`,
`--mpd-root`) rather than inferred from a shared parent directory, and
`[PI-SET-020]`'s whole argument against implicit conventions applies here
too. Add `--library <path>` alongside the existing positional/`--db`
listener-database argument; `lempi-bose.service`-style units on lempipi
gain one more `ExecStart` argument, nothing structural.

**`[IMPL-DBSPLIT-035]` Query qualification is mechanical, not invented per
site.** Every reference to a table in the `library.db` column of §3's table
gets a `lib.` prefix; every `listener_*`/`player_*`/`id_reviews`/
`selection_decisions` reference is already correct unqualified, since it
resolves to `main` by default. The 8+2 sites enumerated in `[IMPL-DBSPLIT-015]`
are exactly the ones that mix both in one statement and need the prefix on
one side only.

---

## 4. What the review against applicable concerns found

### 4.1 Synchronization — no complication, confirmed against the actual tool

Already answered directly and re-stated here for the record
(`[IMPL-DBSPLIT-005]`): `mesh_diff.py`/`resolve_mesh_conflict.py` never
touch a `listener_*` table, and address each side by an independent path.
Post-split, the peer registry's stored path for lempipi changes from
`/srv/library/library.db` to `/srv/library/library.db` — a configuration
edit, not a code change. A **mixed mesh** (bose still one file, lempipi
split) works with zero tool changes, since the two sides of a diff have
never been required to share a layout.

### 4.2 RAM — negligible, and not the risk in this design

Two small `sqlite3` connections instead of one cost a few hundred KB of
page-cache overhead each, immaterial against `[REQ-HW-100]`'s 150 MB
budget and dwarfed by the PipeWire/WirePlumber/BlueZ stack already running
on lempipi. `library.db` is opened `mode=ro` via the `ATTACH` URI, so no
separate writable-open memory cost exists on that side at all. **This split
does not interact with the overlay-RAM risk found on `bose`
(`[IMPL-BOS-165]`)** — lempipi has no overlay yet, and if one is added
later, `recurse=0` scoping the overlay to root alone (§6 of PI001,
already planned regardless of this split) is what controls that risk, not
the database layout.

### 4.3 The real finding: `PlayerStore` cannot stay "the only writable handle"

`file_tags`, `cover_art`, and `release_recordings`'s Vipunen-selection
columns are populated by tools that are not the player at all —
`tagscan`, `tools/fetch_cover_art.py`, and Vipunen's own release-selection
step. `PlayerStore::open()` creates/migrates them today only because, with
one file, it happens to be the sole writable connection available at
runtime. Once B and C are separate files, `PlayerStore`'s writable open is
scoped to `listener.db` and has no writable path to `library.db` at all —
by design, per `[PI-B-010]`, B is read-only except during an attended
import.

**Resolution, corrected in `[§7.6]` after a cross-environment check found
"entirely" was too strong: conditional on whether this installation has
actually split.** When `db_path == library_path` (bose, local, every
unsplit installation) `PlayerStore`'s connection *is* the library's own
writable connection, exactly as today, and removing its bootstrap
unconditionally would have broken a fresh single-file library exactly the
way the original doc comment warned against. Only when the two paths
genuinely differ does `PlayerStore` skip these statements (it has no
writable path to `lib` to run them against) — moved instead to the
migration tool (§8) for whatever the source already had at split time, and
to `tagscan`/`fetch_cover_art.py` making their own bootstrap
self-sufficient (`fetch_cover_art.py` already is; `tagscan` needs the same
fix — see `[§7.6]`).

### 4.4 Backup — smaller and more frequent becomes possible, not required

`[PI-C-030]`'s off-device backup requirement gets cheaper: `listener.db`
alone is the ~2.4 MB figure PI001 §5 already cites, not the ~1 GB combined
file. `library.db` still needs backing up, but it changes only on a
deliberate import — the same rare, attended event `[PI-B-030]` already
describes — so it can be backed up on that event rather than on the
interval `listener.db` deserves. No requirement changes; the split just
lets the two files follow schedules that already match how often each one
actually changes.

### 4.5 MPD — unaffected, confirmed by design, not merely assumed

MPD's own database (its playback index, distinct from `lempi.db`) already
lives on B per BOSE002's own note ("MPD's own database — its index —
belongs here too and not on A") and is addressed by `mpd.conf`'s `db_file`
directly, never through `lempi`'s connection. Splitting `lempi.db` touches
nothing MPD reads.

---

## 5. Migration procedure

**`[IMPL-DBSPLIT-040]` The tool: `tools/split_database.py`, following this
project's existing rehearse-by-default shape** (`[SPEC-DF-109]`, the same
convention `resolve_mesh_conflict.py` and `export_bundle.py` already use).

```
python tools/split_database.py lempi.db --library-out library.db --listener-out listener.db
python tools/split_database.py lempi.db --library-out library.db --listener-out listener.db --commit
```

1. Open the source `lempi.db` **read-only** — this tool never writes to
   the file it's splitting.
2. Create `library.db` fresh; copy every table in §3's B column via
   `CREATE TABLE ... AS SELECT * FROM source.<table>` over an `ATTACH`,
   preserving indexes and the schema Vipunen/tagscan/fetch_cover_art expect —
   including `file_tags`/`cover_art`/`release_recordings`'s tuning columns,
   per `[IMPL-DBSPLIT]` 4.3's resolution, created here instead of at
   player runtime from now on.
3. Create `listener.db` fresh the same way for every table in the C column.
4. **Verify before anything is swapped in**: row counts on both new files
   match the source table-for-table; spot-check a handful of `listener_play_history`
   rows and `passages` rows by primary key against the source; confirm
   `library.db`'s `PRAGMA integrity_check` and `listener.db`'s both pass.
5. Report only, unless `--commit`. `--commit` is the only mode that writes
   the two output files to their final names — dry-run writes to a
   temp directory and deletes it, so a rehearsal never leaves half-built
   files where the real ones would go.

**`[IMPL-DBSPLIT-045]` This tool never deletes the source file.** The
original `lempi.db` is left exactly where it was, renamed with a
timestamp suffix, not removed — the same "keep the old thing until the new
thing is proven" discipline `request-unlock.sh`'s recovery and
`seed-library.sh`'s `--force-swap` guard both already use. Reverting a bad
split is: stop `lempi`, point its unit file's arguments back at the single
old file (dropping `--library`), restart. No data is at risk from the
split tool itself failing partway, because it only ever writes to files
that do not yet exist where the running system reads from.

---
