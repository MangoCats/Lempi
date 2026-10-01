#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `mark_occasion.py` `[SPEC-CDI-096]`: an album already in the
library, marked whole -- its own folder only, nothing without --commit.

    python tools/test_mark_occasion.py
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(os.path.dirname(HERE), "sql", "schema.sql")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def run(*args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "mark_occasion.py"), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=60)
    return r.returncode, r.stdout + r.stderr


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "lib.db")
        c = sqlite3.connect(db)
        c.executescript(open(SCHEMA, encoding="utf-8").read())
        xmas, other = os.path.join(tmp, "A", "Merry (1998)"), os.path.join(tmp, "A", "Other (2001)")
        for fid, folder, mbid in ((1, xmas, "rec-1"), (2, xmas, "rec-2"), (3, other, "rec-3")):
            c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, "
                      "first_seen, last_seen) VALUES (?1, ?2, ?3, 1, 0, 'mp3', 1000, 'x', 'x')",
                      (fid, f"md5-{fid}", os.path.join(folder, f"{fid}.mp3")))
            c.execute("INSERT INTO recordings (mbid, title, source) VALUES (?1, 't', 'test')", (mbid,))
            for kind in ("radio", "album"):
                pid = c.execute("INSERT INTO passages (file_id, kind, start_ms, end_ms, boundary_src) "
                                "VALUES (?1, ?2, 0, 1000, 'cue')", (fid, kind)).lastrowid
                c.execute("INSERT INTO passage_recordings (passage_id, mbid, source) VALUES (?1, ?2, 'test')",
                          (pid, mbid))
        c.commit()
        c.close()

        def marked():
            c = sqlite3.connect(db)
            got = sorted(c.execute("SELECT subject_id, class, value FROM flavor WHERE characteristic = 'user.christmas'"))
            c.close()
            return got

        print("a dry run counts the album's recordings and writes nothing")
        code, out = run(db, "--folder", xmas, "--christmas")
        check(code == 0 and "2 recording(s)" in out and marked() == [], out)
        print("--commit marks its recordings, and no other album's")
        code, out = run(db, "--folder", xmas, "--christmas", "--commit")
        check(marked() == [("rec-1", "christmasy", 1.0), ("rec-1", "not_christmasy", 0.0),
                           ("rec-2", "christmasy", 1.0), ("rec-2", "not_christmasy", 0.0)], f"{marked()} {out}")
        print("a folder with nothing in it, or no occasion named, is refused out loud")
        code, out = run(db, "--folder", os.path.join(tmp, "nowhere"), "--christmas")
        check(code == 1 and "no recordings" in out, out)
        code, out = run(db, "--folder", xmas)
        check(code != 0 and "occasion" in out, out)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("mark_occasion: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
