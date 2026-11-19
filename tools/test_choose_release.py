#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""`choose_release.py` leaves a settled edition standing `[SPEC-FIN-040]`.

A recording's release is chosen by scoring. Two sources settle it instead: a CD
add (`cd:import`) and a person's pick of a folder's edition (`folder:chosen`).
Each is given here a compilation the scoring would pass over for the album, and
must keep it; a choice the scoring made itself is made again.

    python tools/test_choose_release.py
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def library(path: str, source: str) -> None:
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT);
        CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, album TEXT);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT);
        CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
        CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT, release_date TEXT, source TEXT,
            release_group TEXT, status TEXT, primary_type TEXT, secondary_types TEXT, track_count INTEGER);
        CREATE TABLE release_recordings (release_mbid TEXT, mbid TEXT, position INTEGER, source TEXT,
            chosen INTEGER DEFAULT 0, PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;
        CREATE TABLE musicbrainz_cache (mbid TEXT PRIMARY KEY, kind TEXT, response TEXT, fetched_at INTEGER);
        INSERT INTO files VALUES (1, 'md5-1', 'C:/Music/Traffic/Mr. Fantasy (1967)/Dear Mr. Fantasy.mp3');
        INSERT INTO file_tags VALUES (1, 'Mr. Fantasy');
        INSERT INTO passages VALUES (10, 1, 'radio');
        INSERT INTO passage_recordings VALUES (10, 'rec');
        INSERT INTO releases VALUES ('album', 'Mr. Fantasy', '1967', 'musicbrainz', 'g1', 'Official', 'Album', NULL, 10);
        INSERT INTO releases VALUES ('comp', 'Smiling Phases', '1991', 'musicbrainz', 'g2', 'Official', 'Album',
                                     'Compilation', 26);
        INSERT INTO release_recordings VALUES ('album', 'rec', 8, 'musicbrainz', 0);
    """)
    c.execute("INSERT INTO release_recordings VALUES ('comp', 'rec', 8, ?1, 1)", (source,))
    c.commit()
    c.close()


def chosen(path: str) -> str:
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT release_mbid FROM release_recordings WHERE mbid = 'rec' AND chosen = 1"
                         ).fetchone()[0]
    finally:
        c.close()


def main() -> int:
    print("a settled edition is not re-scored; a scored one is")
    with tempfile.TemporaryDirectory() as d:
        for source, want in (("folder:chosen", "comp"), ("cd:import", "comp"), ("musicbrainz", "album")):
            db = os.path.join(d, f"{source.replace(':', '-')}.db")
            library(db, source)
            r = subprocess.run([sys.executable, os.path.join(HERE, "choose_release.py"), db],
                               capture_output=True, text=True, timeout=60)
            check(r.returncode == 0, f"{source}: exited {r.returncode}: {r.stderr[-400:]}")
            got = chosen(db)
            check(got == want, f"{source}: the chosen release is {got!r}, wanted {want!r}")
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("choose_release: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
