#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `drop_dangling_checks.py` and the `row_drop.py` it keeps rows with.

    python tools/test_drop_dangling_checks.py
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
import drop_dangling_checks as ddc  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


LIB = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
CREATE TABLE id_checks (passage_id INTEGER PRIMARY KEY, stored_mbid TEXT, verdict TEXT, score REAL,
    suggested TEXT, checked_at TEXT);
"""
LIS = "CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT);"


def pair(root):
    os.makedirs(root)
    lib = os.path.join(root, "library.db")
    c = sqlite3.connect(lib)
    c.executescript(LIB)
    c.executemany("INSERT INTO passages VALUES (?, 1)", [(1,), (2,)])
    c.execute("INSERT INTO recordings VALUES ('rec-cigar', 'Have a Cigar ‐ a title past cp1252')")
    c.executemany("INSERT INTO id_checks VALUES (?,?,?,?,?,?)", [
        (1, "rec-a", "confirmed", 0.9, "[]", "2026-10-15T19:44:15"),        # a passage there: kept
        (2, "rec-b", "contradicted", 0.5, "[]", "2026-10-15T19:44:15"),     # kept
        (6454, "rec-cigar", "contradicted", 0.97, "[{}]", "2026-10-15T19:44:15"),  # names nothing
        (6460, "rec-x", "unmatched", None, None, "2026-10-15T19:44:15")])          # names nothing
    c.commit()
    c.close()
    c = sqlite3.connect(os.path.join(root, "listener.db"))
    c.executescript(LIS)
    c.close()
    return lib


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = ddc.main(list(argv))
        except SystemExit as e:
            rc = e
    return rc, buf.getvalue()


def ids(lib):
    c = sqlite3.connect(lib)
    try:
        return [r[0] for r in c.execute("SELECT passage_id FROM id_checks ORDER BY passage_id")]
    finally:
        c.close()


def main() -> int:
    tmp = tempfile.mkdtemp()
    bk = os.path.join(tmp, "backups")
    lib = pair(os.path.join(tmp, "a"))

    rc, out = run(lib)
    check(rc == 0 and "naming no passage: 2" in out and "Have a Cigar" in out,
          f"a rehearsal finds the two, and says what each was a check of: {out}")
    check(ids(lib) == [1, 2, 6454, 6460], "and writes nothing")

    rc, out = run(lib, "--commit")
    check(isinstance(rc, SystemExit) and ids(lib) == [1, 2, 6454, 6460], "a commit without --expect is refused")
    rc, out = run(lib, "--commit", "--expect", "3", "--backups", bk)
    check(isinstance(rc, SystemExit) and ids(lib) == [1, 2, 6454, 6460] and not os.path.exists(bk),
          f"a commit whose count differs is refused before any backup: {rc}")

    rc, out = run(lib, "--commit", "--expect", "2", "--backups", bk)
    check(rc == 0 and ids(lib) == [1, 2], f"the two go, and the checks of passages there stay: {ids(lib)} {out}")
    (folder,) = [os.path.join(bk, d) for d in os.listdir(bk)]
    check(os.path.basename(folder).startswith("pre-dangling-checks-"), f"backed up first: {folder}")
    c = sqlite3.connect(os.path.join(folder, "library.db"))
    check([r[0] for r in c.execute("SELECT passage_id FROM id_checks ORDER BY 1")] == [1, 2, 6454, 6460]
          and c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "and the backup holds the catalogue whole")
    c.close()
    d = json.load(open(os.path.join(folder, "dropped.json"), encoding="utf-8"))
    check(d["table"] == "id_checks" and [r["passage_id"] for r in d["rows"]] == [6454, 6460]
          and d["rows"][0]["score"] == 0.97, "every dropped row is written down, every column of it")

    rc, out = run(lib, "--restore", folder)
    check(rc == 0 and ids(lib) == [1, 2, 6454, 6460], f"and put back exactly: {ids(lib)}")
    c = sqlite3.connect(lib)
    check(c.execute("SELECT stored_mbid, verdict, score, suggested FROM id_checks WHERE passage_id = 6454").fetchone()
          == ("rec-cigar", "contradicted", 0.97, "[{}]"), "with every value it had")
    c.close()
    rc, out = run(lib, "--restore", folder)
    check(isinstance(rc, SystemExit) and ids(lib) == [1, 2, 6454, 6460],
          "a restore over rows already there is refused, writing nothing")

    # The review tool's record of 2026-11-14 names no table: it is read as id_reviews.
    old = os.path.join(tmp, "old")
    os.makedirs(old)
    json.dump({"listener": "x", "at": "y", "rows": [{"passage_id": 1}]}, open(os.path.join(old, "dropped.json"), "w"))
    import row_drop
    check(row_drop.dropped(old, "id_reviews") == [{"passage_id": 1}], "a record from before the table was named reads")
    try:
        row_drop.dropped(folder, "id_reviews")
        check(False, "a record kept for id_checks is refused as id_reviews")
    except SystemExit:
        pass

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("drop_dangling_checks: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
