#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `drop_review_copies.py` [IMPL-UID-044].

    python tools/test_drop_review_copies.py
"""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drop_review_copies as drc  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


LIB = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, PRIMARY KEY (passage_id, mbid));
"""
LIS = """
CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT, flagged_at TEXT, origin TEXT);
CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, passage_id INTEGER);
CREATE TABLE id_reviews (passage_id INTEGER PRIMARY KEY, decision TEXT NOT NULL, chosen_mbid TEXT,
    decided_at TEXT NOT NULL, chosen_release_mbid TEXT, previous_mbid TEXT, applied_at TEXT, origin TEXT);
"""
T = "2026-10-15 00:20:04"


def pair(root, extra=()):
    os.makedirs(root)
    lib, lis = os.path.join(root, "library.db"), os.path.join(root, "listener.db")
    c = sqlite3.connect(lib)
    c.executescript(LIB)
    c.executemany("INSERT INTO passages VALUES (?, 1)", [(p,) for p in (10, 11, 12, 13, 14)])
    c.executemany("INSERT INTO recordings VALUES (?, ?)", [("rec-A", "Somewhere Here on Earth"), ("rec-B", "Purple Rain")])
    c.execute("INSERT INTO passage_recordings VALUES (11, 'rec-B', 1.0)")
    c.commit()
    c.close()
    c = sqlite3.connect(lis)
    c.executescript(LIS)
    rows = [(10, "reassigned", "rec-A", T, None, "local:x", "2026-10-16 00:35", "GMKtec"),   # the original
            (11, "reassigned", "rec-A", T, None, "local:x", "2026-10-16 00:35", None),       # a copy, applied
            (12, "reassigned", "rec-A", T, None, "local:x", None, None),                     # a copy, pending
            (13, "kept", None, "2026-11-01 10:00:00", None, None, None, None),               # alone: no copy
            (6455, "reassigned", "rec-B", "2026-11-01 15:35:19", None, "rec-A", "x", "GMKtec"),  # an orphan
            *extra]
    c.executemany("INSERT INTO id_reviews VALUES (?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return lis


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = drc.main(list(argv))
        except SystemExit as e:
            rc = e
    return rc, buf.getvalue()


def ids(lis):
    c = sqlite3.connect(lis)
    try:
        return sorted(r[0] for r in c.execute("SELECT passage_id FROM id_reviews"))
    finally:
        c.close()


def main() -> int:
    tmp = tempfile.mkdtemp()
    bk = os.path.join(tmp, "backups")

    lis = pair(os.path.join(tmp, "a"))
    rc, out = run(lis)
    check(rc == 0 and "copies: 2" in out and "orphans: 1" in out, f"a rehearsal finds two copies and one orphan: {out}")
    check("PENDING" in out and "Purple Rain" in out and "Somewhere Here on Earth" in out,
          "and says which is pending, what the passage is and what the copy would make it")
    check(ids(lis) == [10, 11, 12, 13, 6455], "and writes nothing")

    rc, out = run(lis, "--commit")
    check(isinstance(rc, SystemExit) and ids(lis) == [10, 11, 12, 13, 6455],
          "a commit without the expected counts is refused, writing nothing")
    rc, out = run(lis, "--commit", "--expect-copies", "3", "--expect-orphans", "1", "--backups", bk)
    check(isinstance(rc, SystemExit) and ids(lis) == [10, 11, 12, 13, 6455] and not os.path.exists(bk),
          f"a commit whose counts differ from what it finds is refused before any backup: {rc}")

    rc, out = run(lis, "--commit", "--expect-copies", "2", "--expect-orphans", "1", "--backups", bk)
    check(rc == 0 and ids(lis) == [10, 13], f"the copies and the orphan go; the original and a lone row stay: {ids(lis)}")
    (folder,) = [os.path.join(bk, d) for d in os.listdir(bk)]
    check(os.path.basename(folder).startswith("pre-review-copies-"), f"backed up first: {folder}")
    c = sqlite3.connect(os.path.join(folder, "listener.db"))
    check(sorted(r[0] for r in c.execute("SELECT passage_id FROM id_reviews")) == [10, 11, 12, 13, 6455]
          and c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "and the backup holds what was there")
    c.close()
    dropped = json.load(open(os.path.join(folder, "dropped.json"), encoding="utf-8"))["rows"]
    check(sorted(r["passage_id"] for r in dropped) == [11, 12, 6455], "every dropped row is written down")

    rc, out = run(lis, "--restore", folder)
    check(rc == 0 and ids(lis) == [10, 11, 12, 13, 6455], f"and can be put back exactly: {ids(lis)}")
    rc, out = run(lis, "--restore", folder)
    check(isinstance(rc, SystemExit) and ids(lis) == [10, 11, 12, 13, 6455],
          "a restore over rows already there is refused, writing nothing")

    # A row sharing the time but not the decision is not plainly a copy.
    lis = pair(os.path.join(tmp, "b"), [(14, "deferred", None, T, None, None, None, None)])
    rc, out = run(lis)
    check(rc == 1 and "REFUSED" in out and "passage 14" in out, f"a row unlike its original is named: {out}")
    rc, out = run(lis, "--commit", "--expect-copies", "2", "--expect-orphans", "1", "--backups", os.path.join(tmp, "bk-b"))
    check(isinstance(rc, SystemExit) and ids(lis) == [10, 11, 12, 13, 14, 6455],
          "and a commit is refused while it stands, writing nothing")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("drop_review_copies: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
