#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `repair_durations.py` `[REQ-LIB-145]`.

A real short MP3, decoded for real -- not a mocked probe -- because the
entire point of this tool is that a header/bitrate estimate cannot be
trusted, and a fixture that used one to fake success would prove nothing.

    python tools/test_repair_durations.py
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "repair_durations.py")
FFMPEG = shutil.which("ffmpeg")

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
    format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id),
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    boundary_src TEXT NOT NULL, CHECK (end_ms > start_ms));
CREATE TABLE lowlevel_cache (audio_md5 TEXT NOT NULL, start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL, features BLOB NOT NULL, extractor TEXT NOT NULL,
    extracted_at TEXT NOT NULL, PRIMARY KEY (audio_md5, start_ms, end_ms)) WITHOUT ROWID;
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def make_mp3(path: str, duration_s: float) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={duration_s}",
         "-c:a", "libmp3lame", "-q:a", "4", path],
        check=True,
    )


def run(db, *args):
    return subprocess.run([sys.executable, TOOL, db, *args],
                          capture_output=True, text=True)


def files_row(db):
    c = sqlite3.connect(db)
    row = c.execute("SELECT duration_ms FROM files WHERE file_id=1").fetchone()
    c.close()
    return row[0]


def passage_end(db):
    c = sqlite3.connect(db)
    row = c.execute("SELECT end_ms FROM passages WHERE passage_id=1").fetchone()
    c.close()
    return row[0]


def cache_rows(db):
    c = sqlite3.connect(db)
    rows = c.execute("SELECT audio_md5, start_ms, end_ms FROM lowlevel_cache").fetchall()
    c.close()
    return rows


def build(tmp: str, real_ms: int, stored_ms: int, mp3_duration_s: float) -> str:
    """A file whose stored duration disagrees with what its own audio
    actually decodes to -- the exact shape of the real bug, not a proxy
    for it -- with a whole-passage span overrunning the real audio, and a
    `lowlevel_cache` row keyed to the overrun end that a repair should
    orphan and clean up."""
    os.makedirs(tmp, exist_ok=True)
    mp3 = os.path.join(tmp, "song.mp3")
    make_mp3(mp3, mp3_duration_s)
    db = os.path.join(tmp, "t.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5abc',?,1,1.0,'mp3',?,'t','t')",
              (mp3, stored_ms))
    c.execute("INSERT INTO passages VALUES "
              "(1,1,'radio',0,?,0,900,0.0,'ingest:whole-file')", (stored_ms,))
    c.execute("INSERT INTO lowlevel_cache VALUES ('md5abc',0,?,x'00','essentia','t')",
              (stored_ms,))
    c.commit()
    c.close()
    return db


def main() -> int:
    if not FFMPEG:
        print("SKIPPED: ffmpeg not found")
        return 0

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:

        print("dry run writes nothing")
        db = build(tmp + "/dry", real_ms=3000, stored_ms=8000, mp3_duration_s=3.0)
        r = run(db)
        check(r.returncode == 0, f"dry run should exit 0, got {r.returncode}: {r.stderr}")
        check(files_row(db) == 8000, "a dry run must not touch files.duration_ms")
        check(passage_end(db) == 8000, "a dry run must not touch passages.end_ms")
        check("(dry run" in r.stdout, "dry run should say so")

        print("--write repairs a wrong duration, clamps the overrun end, "
              "and cleans up the orphaned cache row")
        db = build(tmp + "/write", real_ms=3000, stored_ms=8000, mp3_duration_s=3.0)
        r = run(db, "--write")
        check(r.returncode == 0, f"--write should exit 0, got {r.returncode}: {r.stderr}")
        got_dur = files_row(db)
        check(abs(got_dur - 3000) < 300, f"files.duration_ms should be ~3000, got {got_dur}")
        got_end = passage_end(db)
        check(abs(got_end - 3000) < 300, f"passages.end_ms should be clamped to ~3000, got {got_end}")
        rows = cache_rows(db)
        check(not any(r2[2] == 8000 for r2 in rows),
              f"the row keyed to the old, overrun end_ms should be gone, got {rows}")
        check("deleted 1 orphaned lowlevel_cache row" in r.stdout,
              f"should report the cleanup, got: {r.stdout}")

        print("a value that already agrees with the real decode is left alone")
        db = build(tmp + "/ok", real_ms=3000, stored_ms=3000, mp3_duration_s=3.0)
        r = run(db, "--write")
        check(r.returncode == 0, f"got {r.returncode}: {r.stderr}")
        check("updated 0 durations" in r.stdout,
              f"a correct value must not be rewritten, got: {r.stdout}")
        rows = cache_rows(db)
        check(any(r2[2] == 3000 for r2 in rows),
              "an untouched passage's own cache row must survive")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("repair_durations: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
