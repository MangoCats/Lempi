#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `assign_passage_uids.py` [IMPL-UID-052], [IMPL-UID-036].

    python tools/test_assign_passage_uids.py
"""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import assign_passage_uids as apu  # noqa: E402
import passage_uid as pu  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


LIB = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, kind TEXT NOT NULL,
    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, boundary_src TEXT);
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
INSERT INTO files VALUES (1, 'A'), (2, 'B');
INSERT INTO passages VALUES (5, 1, 'radio', 0, 1000, 'x'), (9, 1, 'album', 0, 1000, 'x'), (3, 2, 'radio', 10, 900, 'x');
"""


def pair(root):
    os.makedirs(root)
    lib = os.path.join(root, "library.db")
    c = sqlite3.connect(lib)
    c.executescript(LIB)
    c.close()
    c = sqlite3.connect(os.path.join(root, "listener.db"))
    c.executescript("CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT);")
    c.close()
    return lib


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = apu.main(list(argv))
        except SystemExit as e:
            rc = e
    return rc, buf.getvalue()


def cols(lib):
    c = sqlite3.connect(lib)
    try:
        return [r[1] for r in c.execute("PRAGMA table_info(passages)")]
    finally:
        c.close()


def main() -> int:
    tmp = tempfile.mkdtemp()
    bk = os.path.join(tmp, "bk")
    lib = pair(os.path.join(tmp, "a"))
    rc, out = run(lib)
    check(rc == 0 and "3 passage(s), 3 distinct" in out, f"a rehearsal counts them: {out}")
    check("passage_uid" not in cols(lib), "and writes nothing")
    rc, out = run(lib, "--commit", "--expect", "4", "--backups", bk)
    check(isinstance(rc, SystemExit) and "passage_uid" not in cols(lib) and not os.path.exists(bk),
          "a commit whose count differs is refused before any backup")

    rc, out = run(lib, "--commit", "--expect", "3", "--backups", bk)
    check(rc == 0, f"the commit: {rc} {out}")
    c = sqlite3.connect(lib)
    got = dict(c.execute("SELECT passage_id, passage_uid FROM passages").fetchall())
    check(got == {5: pu.passage_uid("A", "radio", 0, 1000), 9: pu.passage_uid("A", "album", 0, 1000),
                  3: pu.passage_uid("B", "radio", 10, 900)}, f"each passage, its own number kept, has its uid: {got}")
    check(c.execute("SELECT birth_md5, birth_kind, birth_start_ms, birth_end_ms, birth_salt FROM passages "
                    "WHERE passage_id = 3").fetchone() == ("B", "radio", 10, 900, 0), "with its birth key beside it")
    check(c.execute("SELECT 1 FROM sqlite_master WHERE name = 'passage_lineage'").fetchone() is not None,
          "and the lineage table exists")
    try:
        c.execute("INSERT INTO passages (file_id, kind, start_ms, end_ms, boundary_src) VALUES (2, 'album', 0, 5, 'x')")
        check(False, "a passage written by hand without a uid is refused by the trigger")
    except sqlite3.DatabaseError as e:
        check("IMPL-UID-036" in str(e), f"and says why: {e}")
    try:
        c.execute("UPDATE passages SET passage_uid = 'other' WHERE passage_id = 3")
        check(False, "a uid that is set cannot be changed")
    except sqlite3.DatabaseError:
        pass
    c.execute("UPDATE passages SET end_ms = 800 WHERE passage_id = 3")          # a boundary edit
    check(c.execute("SELECT passage_uid FROM passages WHERE passage_id = 3").fetchone()[0]
          == pu.passage_uid("B", "radio", 10, 900), "and a boundary edit leaves the uid as it was")
    new = pu.insert_passage(c, 2, "album", 0, 5, "x")
    check(c.execute("SELECT passage_uid FROM passages WHERE passage_id = ?", (new,)).fetchone()[0]
          == pu.passage_uid("B", "album", 0, 5), "while the helper makes a passage with its uid")
    c.commit()
    c.close()
    (folder,) = os.listdir(bk)
    b = sqlite3.connect(os.path.join(bk, folder, "library.db"))
    check([r[1] for r in b.execute("PRAGMA table_info(passages)")].count("passage_uid") == 0,
          "the backup is the catalogue as it was before")
    b.close()
    rc, out = run(lib, "--commit", "--expect", "4", "--backups", bk)
    check(isinstance(rc, SystemExit) and "already carry" in str(rc), f"a second run is refused: {rc}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("assign_passage_uids: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
