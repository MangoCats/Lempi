#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `add_byte_hashes.py` `[REQ-AND-960]`.

    python tools/test_add_byte_hashes.py
"""
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import add_byte_hashes as abh  # noqa: E402

# The columns the tool touches, as the live catalogue has them -- without
# `sha256`, which is the library this pass exists for.
SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
    format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    boundary_src TEXT NOT NULL);
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def run(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = abh.main(list(args))
    return rc, out.getvalue()


def main() -> int:
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "library.db")
    here = os.path.join(tmp, "here.mp3")
    with open(here, "wb") as fh:
        fh.write(b"some bytes that stand for audio")
    gone = os.path.join(tmp, "gone.mp3")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'a',?,31,1.0,'mp3',1000,'t','t')", (here,))
    c.execute("INSERT INTO files VALUES (2,'b',?,99,1.0,'mp3',1000,'t','t')", (gone,))
    c.commit()
    c.close()

    rc, out = run(db, "--commit")
    check(rc == 2 and "unknown option" in out,
          f"a mistyped flag must be refused, not run as a dry run: rc={rc}")

    rc, out = run(db)
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(files)")}
    check(rc == 0 and "sha256" not in cols, "a dry run must write nothing, not even the column")
    check("1 to hash" in out and "1 not at their recorded path" in out, f"dry-run report: {out!r}")

    rc, out = run(db, "--write")
    c = sqlite3.connect(db)
    got = dict(c.execute("SELECT file_id, sha256 FROM files"))
    want = hashlib.sha256(open(here, "rb").read()).hexdigest()
    check(got[1] == want, f"the present file must get its byte hash, got {got[1]!r}")
    check(got[2] is None, "a file not at its path must stay NULL, never guessed")
    idx = {r[1] for r in c.execute("PRAGMA index_list(files)")}
    check("files_sha256" in idx, "the column must be indexed for the negotiation's lookups")
    c.close()

    rc, out = run(db, "--write")
    check("1 already hashed, 0 to hash" in out, f"a second run takes up only what is left: {out!r}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("add_byte_hashes: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
