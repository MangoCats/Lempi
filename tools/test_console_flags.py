#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `console.py`'s `flags()` `[REQ-VIS-265]`, `[REQ-LIB-190]`.

Read-only, matching the console's own posture: this resolves what Lempi's
`listener_flags` table says into something nameable and something to link
to, and never writes a byte of it.

    python tools/test_console_flags.py
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import console  # noqa: E402

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT NOT NULL, size_bytes INTEGER, mtime REAL, format TEXT,
    duration_ms INTEGER, first_seen TEXT, last_seen TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT, start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);
CREATE TABLE file_tags (file_id INTEGER, title TEXT, artist TEXT, album TEXT,
    track_no INTEGER, disc_no INTEGER, has_art INTEGER, scanned_at TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL,
    sort_name TEXT, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT,
    weight REAL DEFAULT 1.0, source TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT,
    weight REAL DEFAULT 1.0, source TEXT);
CREATE TABLE listener_flags (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    flagged_at TEXT NOT NULL, PRIMARY KEY (subject_kind, subject_id));
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


def build():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-a','/m/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO files VALUES (2,'md5-b','/m/b.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES (10,1,'radio',0,300000,0,0,0,'src')")
    c.execute("INSERT INTO passages VALUES (11,1,'radio',0,300000,0,0,0,'src')")  # same recording, 2nd file
    c.execute("INSERT INTO passages VALUES (20,2,'radio',0,300000,0,0,0,'src')")  # unidentified
    c.execute("INSERT INTO recordings VALUES ('rec-1','A Song',NULL,'s')")
    c.execute("INSERT INTO artists VALUES ('art-1','A Band',NULL,'s')")
    c.execute("INSERT INTO recording_artists VALUES ('rec-1','art-1',1.0,'s')")
    c.execute("INSERT INTO passage_recordings VALUES (10,'rec-1',1.0,'s')")
    c.execute("INSERT INTO passage_recordings VALUES (11,'rec-1',1.0,'s')")
    c.execute("INSERT INTO file_tags VALUES (2,'Tag Title','Tag Artist',NULL,NULL,NULL,0,'t')")
    return c


def main() -> int:
    print("no listener_flags table at all reads as nothing flagged, not a crash")
    bare = sqlite3.connect(":memory:")
    bare.executescript(SCHEMA.split("CREATE TABLE listener_flags")[0])
    check(console.flags(bare) == [], "a library with no flags table must report an empty list")

    print("a recording-keyed flag resolves its name and every passage that carries it")
    c = build()
    c.execute("INSERT INTO listener_flags VALUES ('recording','rec-1','2026-08-28 00:00:00')")
    rows = console.flags(c)
    check(len(rows) == 1, f"expected 1 flagged row, got {len(rows)}")
    r = rows[0]
    check(r["title"] == "A Song" and r["artist"] == "A Band", f"got {r}")
    check(sorted(r["passages"]) == [10, 11], f"expected both passages, got {r['passages']}")
    check(r["resolved"] is True, "a recording with real passages must resolve")

    print("a passage-keyed flag on unidentified audio falls back to the file's own tag")
    c = build()
    c.execute("INSERT INTO listener_flags VALUES ('passage','20','2026-08-28 00:00:00')")
    r = console.flags(c)[0]
    check(r["title"] == "Tag Title" and r["artist"] == "Tag Artist", f"got {r}")
    check(r["passages"] == [20], f"got {r['passages']}")
    check(r["resolved"] is True, "a passage that still exists must resolve")

    print("a passage-keyed flag whose passage no longer exists says so plainly")
    c = build()
    c.execute("INSERT INTO listener_flags VALUES ('passage','9999','2026-08-28 00:00:00')")
    r = console.flags(c)[0]
    check(r["resolved"] is False, "a vanished passage must not silently resolve")
    check(r["passages"] == [], f"got {r['passages']}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console flags: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
