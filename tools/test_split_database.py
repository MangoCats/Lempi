#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `split_database.py` `[IMPL002 §8]`.

Builds a small real source database -- both catalog and listener tables,
one deliberate index on each side -- and exercises the actual `main()`
entry point in both rehearsal and `--commit` modes, the same way this
project's other CLI tools are tested: through their real interface, not a
reimplementation of their internals.

    python tools/test_split_database.py
"""

import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import split_database as sd  # noqa: E402

SCHEMA = """
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL);
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, mbid TEXT, played_at INTEGER);
CREATE INDEX listener_play_mbid ON listener_play_history(mbid);
CREATE TABLE player_state (id INTEGER PRIMARY KEY, position_ms INTEGER);
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

INSERT INTO recordings VALUES ('mb-1', 'A Song');
INSERT INTO files VALUES (1, '/music/a.flac');
INSERT INTO passages VALUES (1, 1, 'album', 0, 200000);
INSERT INTO listener_play_history VALUES (1, 'mb-1', 1700000000);
INSERT INTO player_state VALUES (1, 5000);
INSERT INTO schema_meta VALUES ('schema_version', '1');
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def source_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.close()  # Windows holds the file open otherwise, blocking os.unlink later
    return path


def out_paths():
    d = tempfile.mkdtemp()
    return os.path.join(d, "library.db"), os.path.join(d, "listener.db")


def test_rehearsal_writes_neither_output_file():
    src = source_db()
    library_path, listener_path = out_paths()
    # main() reads argv-style args via argparse, called directly with sys.argv
    # patched -- the same shape the tool's own CLI takes, not a private API.
    old_argv = sys.argv
    try:
        sys.argv = ["split_database.py", src, "--library-out", library_path, "--listener-out", listener_path]
        rc = sd.main()
        check(rc == 0, "rehearsal should exit 0")
        check(not os.path.exists(library_path), "rehearsal must not write library.db")
        check(not os.path.exists(listener_path), "rehearsal must not write listener.db")
    finally:
        sys.argv = old_argv
        os.unlink(src)


def test_commit_produces_two_correct_files_and_leaves_the_source_alone():
    src = source_db()
    library_path, listener_path = out_paths()
    old_argv = sys.argv
    try:
        sys.argv = ["split_database.py", src, "--library-out", library_path,
                    "--listener-out", listener_path, "--commit"]
        rc = sd.main()
        check(rc == 0, "commit should exit 0")
        check(os.path.exists(library_path), "commit must write library.db")
        check(os.path.exists(listener_path), "commit must write listener.db")

        lib = sqlite3.connect(library_path)
        check(lib.execute("SELECT title FROM recordings WHERE mbid='mb-1'").fetchone() == ("A Song",),
              "library.db must have the catalog data")
        check(
            lib.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='passages_span'"
            ).fetchone()[0] == 1,
            "library.db must carry the passages_span UNIQUE index, not just the table",
        )
        check(
            lib.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone() == ("1",),
            "schema_meta must be copied to library.db too (a judgment call, not an omission)",
        )
        check(
            lib.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='listener_play_history'"
            ).fetchone()[0] == 0,
            "library.db must NOT contain a listener-side table",
        )

        listener = sqlite3.connect(listener_path)
        check(
            listener.execute("SELECT mbid, played_at FROM listener_play_history").fetchone() == ("mb-1", 1700000000),
            "listener.db must have the listener data",
        )
        check(
            listener.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='listener_play_mbid'"
            ).fetchone()[0] == 1,
            "listener.db must carry its own index too",
        )
        check(
            listener.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone() == ("1",),
            "schema_meta must be copied to listener.db as well",
        )
        check(
            listener.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='recordings'"
            ).fetchone()[0] == 0,
            "listener.db must NOT contain a catalog-side table",
        )

        source_untouched = sqlite3.connect(src)
        check(
            source_untouched.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 1,
            "the source file must be completely unmodified after --commit",
        )
        lib.close()
        listener.close()
        source_untouched.close()
    finally:
        sys.argv = old_argv
        os.unlink(src)


def test_refuses_to_overwrite_an_existing_output_file():
    src = source_db()
    library_path, listener_path = out_paths()
    with open(library_path, "w") as f:
        f.write("not a database, just occupying the path")
    old_argv = sys.argv
    try:
        sys.argv = ["split_database.py", src, "--library-out", library_path,
                    "--listener-out", listener_path, "--commit"]
        rc = sd.main()
        check(rc != 0, "must refuse rather than overwrite an existing output path")
    finally:
        sys.argv = old_argv
        os.unlink(src)


def test_a_missing_optional_table_is_skipped_not_an_error():
    # No `flavor` table at all in this source -- an older or leaner library.
    fd, src = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(src)
    conn.executescript(SCHEMA)  # SCHEMA already lacks `flavor`
    conn.close()
    library_path, listener_path = out_paths()
    old_argv = sys.argv
    try:
        sys.argv = ["split_database.py", src, "--library-out", library_path,
                    "--listener-out", listener_path, "--commit"]
        rc = sd.main()
        check(rc == 0, "a source missing an optional table must not fail the split")
    finally:
        sys.argv = old_argv
        os.unlink(src)


def test_a_source_that_changes_mid_run_is_named_rather_than_blamed_on_the_copy():
    """The live-write race, which cost a real rehearsal a false diagnosis.

    A player still appending plays makes the copy one row short of the
    source, and the old message -- "listener_play_history has 37974 rows,
    source has 37975" -- reads like the copy lost one and sends whoever sees
    it hunting for corruption. Same numbers, opposite remedy: stop the
    writer `[IMPL-VP3-140]`.

    Simulated by counting the source, then appending to it before asking
    whether it drifted -- which is exactly what a concurrent writer does.
    """
    src = source_db()
    before = sd.table_counts(src)
    conn = sqlite3.connect(src)
    conn.execute("INSERT INTO listener_play_history VALUES (2, 'mb-1', 1700000001)")
    conn.commit()
    conn.close()

    moved = sd.source_drift(src, before)
    check(moved, "a source that gained a row must be reported as having drifted")
    tables = {t for t, _, _ in moved}
    check(tables == {"listener_play_history"},
          f"only the table that changed should be named, got {sorted(tables)}")
    for t, was, now in moved:
        check(now == was + 1, f"{t}: expected {was}+1, reported {was} -> {now}")

    # And a source that holds still must not be accused of moving.
    steady = source_db()
    check(sd.source_drift(steady, sd.table_counts(steady)) == [],
          "an unchanged source must report no drift")
    os.unlink(src)
    os.unlink(steady)


def test_the_rehearsal_workdir_lands_beside_each_output():
    """Rehearsing in /tmp is what made a 1.17 GB split fail on a 452 MB tmpfs
    while proving nothing `[IMPL-VP3-120]`. Beside the destination, the
    rehearsal fits exactly when the real run would.
    """
    src = source_db()
    lib, lis = out_paths()
    lis_dir = tempfile.mkdtemp()          # a SEPARATE partition, in effect
    lis = os.path.join(lis_dir, "listener.db")

    seen = []
    real_mkdtemp = tempfile.mkdtemp

    def spy(*a, **kw):
        seen.append(kw.get("dir"))
        return real_mkdtemp(*a, **kw)

    tempfile.mkdtemp = spy
    old_argv = sys.argv
    try:
        sys.argv = ["split_database.py", src, "--library-out", lib, "--listener-out", lis]
        rc = sd.main()
    finally:
        tempfile.mkdtemp = real_mkdtemp
        sys.argv = old_argv
    check(rc == 0, f"rehearsal should pass, returned {rc}")
    check(os.path.dirname(os.path.abspath(lib)) in seen,
          f"a workdir should sit beside the library output; dirs were {seen}")
    check(os.path.dirname(os.path.abspath(lis)) in seen,
          f"and another beside the listener output; dirs were {seen}")
    os.unlink(src)


def main() -> int:
    test_rehearsal_writes_neither_output_file()
    test_a_source_that_changes_mid_run_is_named_rather_than_blamed_on_the_copy()
    test_the_rehearsal_workdir_lands_beside_each_output()
    test_commit_produces_two_correct_files_and_leaves_the_source_alone()
    test_refuses_to_overwrite_an_existing_output_file()
    test_a_missing_optional_table_is_skipped_not_an_error()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("split_database: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
