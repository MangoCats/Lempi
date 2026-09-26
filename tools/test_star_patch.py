#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `star_patch.py` [SPEC-STAR-080], one per duty.

    python tools/test_star_patch.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_patch as sp  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def db(path, *sql):
    c = sqlite3.connect(path)
    for s in sql:
        c.execute(s)
    c.commit()
    c.close()


def rows(path, sql):
    c = sqlite3.connect(path)
    r = c.execute(sql).fetchall()
    c.close()
    return r


SCHEMA = ("CREATE TABLE prefs (id TEXT PRIMARY KEY, v REAL, updated_at TEXT) WITHOUT ROWID",
          "CREATE TABLE plays (play_id INTEGER PRIMARY KEY, passage_id INTEGER)",
          "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT UNIQUE, art BLOB)")


def main() -> int:
    tmp = tempfile.mkdtemp()
    base, target = os.path.join(tmp, "base.db"), os.path.join(tmp, "target.db")
    db(base, *SCHEMA,
       "INSERT INTO prefs VALUES ('a', 1.0, 't1')", "INSERT INTO prefs VALUES ('gone', 1.0, 't1')",
       "INSERT INTO plays VALUES (1, 16407)",
       "INSERT INTO files VALUES (1, 'A', NULL)", "INSERT INTO files VALUES (2, 'B', NULL)")
    db(target, *SCHEMA,
       "INSERT INTO prefs VALUES ('a', 2.5, 't2')", "INSERT INTO prefs VALUES ('new', 0.1, 't2')",
       "INSERT INTO plays VALUES (1, 16409)",
       "INSERT INTO files VALUES (1, 'B', x'00ff')", "INSERT INTO files VALUES (2, 'A', NULL)",
       "CREATE TABLE reviews (id INTEGER PRIMARY KEY, what TEXT)",
       "CREATE INDEX reviews_what ON reviews (what)",
       "INSERT INTO reviews VALUES (1, 'x')")
    patch = os.path.join(tmp, "p.json")
    check(sp.make(base, target, patch) == 0, "make succeeds")

    # The node kept playing after its snapshot: a play the patch never names.
    live = os.path.join(tmp, "live.db")
    shutil.copyfile(base, live)
    db(live, "INSERT INTO plays VALUES (2, 500)")
    before = open(live, "rb").read()
    check(sp.apply(live, patch, commit=False) == 0, "a rehearsal succeeds")
    check(open(live, "rb").read() == before, "and writes nothing")

    check(sp.apply(live, patch, commit=True) == 0, "a commit succeeds")
    check(rows(live, "SELECT id, v FROM prefs ORDER BY id") == [("a", 2.5), ("new", 0.1)],
          "changed, added and removed rows all land")
    check(rows(live, "SELECT play_id, passage_id FROM plays ORDER BY play_id") == [(1, 16409), (2, 500)],
          "a translated play lands, and a play recorded after the snapshot survives [REQ-PD-113]")
    check(rows(live, "SELECT file_id, audio_md5, art FROM files ORDER BY file_id") == [(1, "B", b"\x00\xff"), (2, "A", None)],
          "two rows swapping a UNIQUE value land, and a blob survives the journey")
    check(rows(live, "SELECT what FROM reviews") == [("x",)], "a table the node lacks is created and filled")
    check(rows(live, "SELECT name FROM sqlite_master WHERE name='reviews_what'") == [("reviews_what",)],
          "with its index")

    check(sp.apply(live, patch, commit=True) == 0, "applying twice is harmless")
    check(rows(live, "SELECT count(*) FROM plays") == [(2,)], "and changes nothing")

    # An edit made on the node after its snapshot is not overwritten.
    edited = os.path.join(tmp, "edited.db")
    shutil.copyfile(base, edited)
    db(edited, "UPDATE prefs SET v = 9, updated_at = 't3' WHERE id = 'a'")
    before = open(edited, "rb").read()
    check(sp.apply(edited, patch, commit=True) == 1, "a row changed since the snapshot is a conflict")
    check(open(edited, "rb").read() == before, "and one conflict means nothing is written")

    # The node-side backup and fingerprint.
    bk = os.path.join(tmp, "bk.db")
    check(sp.backup(live, bk) == 0, "a backup succeeds")
    try:
        sp.backup(live, bk)
        check(False, "a backup must never be written over")
    except SystemExit:
        pass
    import contextlib
    import io
    prints = []
    for p in (live, bk, base):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sp.fingerprint(p)
        prints.append(out.getvalue())
    check(prints[0] == prints[1], "the backup fingerprints exactly as its source")
    check(prints[0] != prints[2], "and a different database does not")
    db(live, "INSERT INTO plays VALUES (99, 1)")
    check(sp.restore(bk, live) == 0, "a restore succeeds")
    check(rows(live, "SELECT count(*) FROM plays WHERE play_id = 99") == [(0,)],
          "and puts the backup back whole")

    # A column the player adds on open, which a read-only node never got.
    old, new = os.path.join(tmp, "old.db"), os.path.join(tmp, "new.db")
    db(old, "CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT)",
       "INSERT INTO files VALUES (1, '/a')")
    db(new, "CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT, md5_generator TEXT)",
       "INSERT INTO files VALUES (1, '/a', NULL)")
    p2 = os.path.join(tmp, "p2.json")
    sp.make(old, new, p2)
    node = os.path.join(tmp, "node.db")
    shutil.copyfile(old, node)
    check(sp.apply(node, p2, commit=True) == 0, "a patch adding a nullable column applies")
    check(rows(node, "SELECT * FROM files") == [(1, "/a", None)], "and the column is there, rows untouched")

    # Dropping a column is a schema change, not a row patch.
    try:
        sp.make(new, old, os.path.join(tmp, "x.json"))
        check(False, "a target lacking a column must be refused")
    except SystemExit as e:
        check("never drops a column" in str(e), "and said so")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("star_patch: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
