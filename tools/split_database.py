#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Splits one `lempi.db` into `library.db` (B) and `listener.db` (C)
`[PI-DB-010]`, per the migration procedure `[IMPL002 §8]` finally specifies.

    python tools/split_database.py lempi.db --library-out library.db --listener-out listener.db
    python tools/split_database.py lempi.db --library-out library.db --listener-out listener.db --commit

Rehearses by default, the same shape every tool here uses `[SPEC-DF-109]`:
without `--commit` this writes to a temporary directory and deletes it,
reporting what it would have done. `--commit` is the only mode that writes
the two real output files, and only after every check below has passed.

**Never touches the source.** It is opened read-only and never renamed,
altered, or deleted -- reverting a bad split is: stop `lempi`, point its
unit file's arguments back at the one original file, restart. No data is
ever at risk from this tool failing partway, because it only ever writes to
paths that do not yet exist where the running system reads from
`[IMPL-DBSPLIT-045]`.

**Copies DDL before data, per `[IMPL002 §7.1]`.** `CREATE TABLE ... AS
SELECT` silently drops every index and constraint -- confirmed against this
project's own schema, not theoretical: it would drop `passages_span`, the
`UNIQUE` index that stops a re-import from duplicating a passage. So every
table here is created from its own `CREATE TABLE`/`CREATE INDEX` text, read
straight out of the source's `sqlite_master`, and only then filled with
`INSERT INTO ... SELECT`.
"""

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile

# Every table this project's schema actually has, as of the audit that built
# this list `[IMPL002 §8]` -- read from a real database's own `sqlite_master`,
# not reconstructed from memory of what `schema.sql` says it should be.
# Two are judgment calls, named as such rather than silently decided:
#
# - `ingest_decisions`: keyed by `audio_md5`, Vipunen's own ingest audit log,
#   tied to specific audio content and never touched by the player
#   (`db/mod.rs`'s `segmentable()` fixture says so directly). Placed on B
#   with everything else Vipunen produces about the catalog.
# - `schema_meta`: two rows, `schema_version`/`spec` -- metadata about *this
#   file's own schema*, which is ambiguous the moment there are two files.
#   Copied to **both** rather than assigned to one side, so a future tool
#   that checks a database's schema version finds it regardless of which
#   half it opened.
LIBRARY_TABLES = [
    "files", "passages", "passage_recordings", "recordings", "artists",
    "recording_artists", "recording_relations", "releases",
    "release_recordings", "flavor", "flavor_constants", "cover_art",
    "file_tags", "id_checks", "lyrics", "ingest_decisions",
    "lowlevel_cache", "musicbrainz_cache", "identification_cache",
]
LISTENER_TABLES = [
    "listener_play_history", "listener_rejections", "listener_flags",
    "listener_preferences", "listener_likes", "listener_programs",
    "listener_program_seeds", "listener_occasions",
    "listener_occasion_points", "listener_characteristics",
    "listener_settings", "player_state",
    "player_settings", "id_reviews", "boundary_reviews", "artist_reviews",
    "selection_decisions",
]
BOTH = ["schema_meta"]


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def copy_table(source: sqlite3.Connection, dest: sqlite3.Connection, table: str) -> int:
    """Copies one table's real DDL (its `CREATE TABLE` and every associated
    `CREATE INDEX`/`CREATE TRIGGER`) from `source`, then its rows. Returns
    the row count copied. A table the source doesn't have -- an older
    library that predates a later column, say -- is skipped, not an error:
    the destination simply doesn't get a table nothing would have used.
    """
    if not table_exists(source, table):
        say(f"  {table}: not present in source, skipping")
        return 0
    ddl_rows = source.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL "
        "ORDER BY (type != 'table')",  # the CREATE TABLE itself before its indexes
        (table,),
    ).fetchall()
    for (ddl,) in ddl_rows:
        dest.execute(ddl)
    cols = [r[1] for r in source.execute(f"PRAGMA table_info({table})")]
    placeholders = ",".join("?" for _ in cols)
    rows = source.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall()
    if rows:
        dest.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
    return len(rows)


def journal_mode_of(path: str) -> str:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        con.close()


def build_half(source_path: str, out_path: str, tables: list) -> dict:
    """Builds one output file against `out_path`, returning `{table: rows}`."""
    if os.path.exists(out_path):
        os.remove(out_path)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    dest = sqlite3.connect(out_path)
    # Carry the source's journal mode across. A fresh SQLite file is
    # `delete`, and nothing here used to change that -- so splitting a WAL
    # database silently produced two non-WAL halves. That matters: the
    # console reads the live library while the player writes it, and
    # `[IMPL-SUI-040]` rests on WAL for exactly that ("readers never block
    # the player"). lempipi's own halves are `delete` today because this
    # line did not exist when they were made.
    #
    # It is also the one pragma that changes what a cross-half transaction
    # guarantees: SQLite documents multi-database transactions as atomic
    # only when the journal mode is NOT WAL. Preserving the source's choice
    # keeps that a property of the installation rather than an accident of
    # the migration.
    mode = source.execute("PRAGMA journal_mode").fetchone()[0]
    dest.execute(f"PRAGMA journal_mode = {mode}")
    counts = {}
    try:
        for table in tables:
            counts[table] = copy_table(source, dest, table)
        dest.commit()
    finally:
        source.close()
        dest.close()
    return counts


def table_counts(source_path: str) -> dict:
    """Row counts for every table this split touches, read-only.

    Taken once before the copy and once after, so that a source which is
    still being written can be *named* as such rather than showing up as a
    short copy `[IMPL-VP3-140]`.
    """
    counts = {}
    con = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        for table in LIBRARY_TABLES + LISTENER_TABLES + BOTH:
            if table_exists(con, table):
                counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()
    return counts


def source_drift(source_path: str, before: dict) -> list:
    """Tables whose row count in the SOURCE moved while the copy ran.

    Returns `(table, before, after)` triples; empty means the source held
    still, which is the only condition under which a row-count comparison
    against it means anything.
    """
    after = table_counts(source_path)
    return [(t, n, after.get(t)) for t, n in before.items() if after.get(t) != n]


def verify(source_path: str, library_path: str, listener_path: str) -> list:
    """Every check before anything is promoted `[IMPL002 §8]`: row counts
    match table-for-table, `PRAGMA integrity_check` passes on both new
    files, and every index the source had exists in whichever new file
    inherited its table. Returns a list of problem strings -- empty means
    clean.
    """
    problems = []
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    for path, tables in ((library_path, LIBRARY_TABLES + BOTH), (listener_path, LISTENER_TABLES + BOTH)):
        dest = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        integrity = dest.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            problems.append(f"{path}: integrity_check reported {integrity!r}")
        for table in tables:
            if not table_exists(source, table):
                continue
            src_n = source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if not table_exists(dest, table):
                problems.append(f"{path}: {table} missing entirely ({src_n} rows expected)")
                continue
            dst_n = dest.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if src_n != dst_n:
                problems.append(f"{path}: {table} has {dst_n} rows, source has {src_n}")
            src_idx = {
                r[0] for r in source.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? "
                    "AND name NOT LIKE 'sqlite_autoindex_%'", (table,))
            }
            dst_idx = {
                r[0] for r in dest.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? "
                    "AND name NOT LIKE 'sqlite_autoindex_%'", (table,))
            }
            missing_idx = src_idx - dst_idx
            if missing_idx:
                problems.append(f"{path}: {table} is missing index(es) {sorted(missing_idx)}")
        dest.close()
    source.close()
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="the existing single lempi.db to split (never modified)")
    ap.add_argument("--library-out", required=True, help="output path for library.db (B)")
    ap.add_argument("--listener-out", required=True, help="output path for listener.db (C)")
    ap.add_argument("--commit", action="store_true", help="write the real files (default: rehearse only)")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        say(f"source not found: {args.source}")
        return 1
    for out in (args.library_out, args.listener_out):
        if os.path.exists(out):
            say(f"refusing to overwrite an existing file: {out}")
            say("remove it first, or choose a different --library-out/--listener-out.")
            return 1

    # Rehearse ON THE DESTINATION, not in /tmp `[IMPL-VP3-120]`.
    #
    # The default `tempfile` location is often a small tmpfs -- 452 MB on a
    # Pi 3 with 905 MB of RAM -- and a full catalogue here is 1.17 GB, so the
    # rehearsal died of a full disk having proved nothing. Putting each half's
    # workdir beside its OWN destination fixes that and buys a check this tool
    # did not previously make: the rehearsal now fits exactly when the real
    # run would fit, so it pre-flights the destination's free space instead of
    # some unrelated partition's.
    #
    # It matters that the two halves are taken separately. They routinely land
    # on different partitions -- the catalogue on a large one, the listener
    # store on a small state partition -- and a single shared workdir would
    # test whichever it happened to sit on.
    workdirs = []
    if args.commit:
        library_path, listener_path = args.library_out, args.listener_out
    else:
        def beside(out: str, name: str) -> str:
            d = tempfile.mkdtemp(prefix=".split-rehearsal-",
                                 dir=os.path.dirname(os.path.abspath(out)) or ".")
            workdirs.append(d)
            return os.path.join(d, name)
        library_path = beside(args.library_out, "library.db")
        listener_path = beside(args.listener_out, "listener.db")

    try:
        source_before = table_counts(args.source)
        say(f"{'writing' if args.commit else 'rehearsing (no files written outside a temp dir)'}:")
        say(f"  library.db ({len(LIBRARY_TABLES) + len(BOTH)} tables) <- {args.source}")
        lib_counts = build_half(args.source, library_path, LIBRARY_TABLES + BOTH)
        say(f"  listener.db ({len(LISTENER_TABLES) + len(BOTH)} tables) <- {args.source}")
        listener_counts = build_half(args.source, listener_path, LISTENER_TABLES + BOTH)

        say("verifying...")
        # Did the SOURCE move while we were reading it? `[IMPL-VP3-140]`
        #
        # Without this the answer comes back as "listener_play_history has
        # 37974 rows, source has 37975", which reads like the copy lost one --
        # and sends whoever sees it looking for corruption. The true cause is
        # that a player was still running and appended a play mid-copy. Same
        # numbers, opposite remedy: stop the writer, do not distrust the tool.
        moved = {t: (n, m) for t, n, m in source_drift(args.source, source_before)}
        if moved:
            say("THE SOURCE CHANGED WHILE IT WAS BEING READ -- this run proves nothing:")
            for t, (before, after) in moved.items():
                say(f"  - {t}: {before} rows when this started, {after} now")
            say("something is still writing to the source. Stop it and run again;")
            say("for an appliance that is `systemctl stop lempi`.")
            return 1

        problems = verify(args.source, library_path, listener_path)
        if problems:
            say("PROBLEMS FOUND -- nothing should be trusted from this run:")
            for p in problems:
                say(f"  - {p}")
            return 1

        say(f"journal_mode carried across from the source: {journal_mode_of(args.source)}")
        for label, counts in (("library.db", lib_counts), ("listener.db", listener_counts)):
            total = sum(counts.values())
            nonzero = {t: n for t, n in counts.items() if n}
            say(f"{label}: {total} rows across {len(nonzero)} non-empty tables")
        say("verification passed: row counts match, integrity_check ok, every index present.")

        if args.commit:
            say(f"committed: {args.library_out}, {args.listener_out}")
            say("the source file was not modified. Point lempi's unit at both new paths")
            say("(--db <listener> --library <library>) only after hearing it play.")
        else:
            say("rehearsal only -- re-run with --commit to write the real files.")
        return 0
    finally:
        for d in workdirs:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
