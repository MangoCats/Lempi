#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `artist_sort.py` `[REQ-VIS-182]`: a sort name filled where an
artist has none, never replaced, asked of MusicBrainz only for a MusicBrainz
artist. The lookup is faked; the real one was run against the live library
2026-10-01.

    python tools/test_artist_sort.py
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artist_sort  # noqa: E402

FAILED = []
LAUPER = "7bd9e20e-74b9-446a-a2ed-a223f82a36e7"
ADAMS = "0b9d4a8e-0000-4000-8000-000000000001"
GONE = "0b9d4a8e-0000-4000-8000-000000000002"


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def library():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL, sort_name TEXT, source TEXT NOT NULL)")
    c.executemany("INSERT INTO artists VALUES (?,?,?,?)", [
        (LAUPER, "Cyndi Lauper", None, "musicbrainz"),
        (ADAMS, "Bryan Adams", "Adams, Bryan", "inherited:mulib"),
        (GONE, "Merged Away", "", "musicbrainz"),
        ("local:artist:39", "", None, "inherited:mulib")])
    return c


def main() -> int:
    asked = []
    real = artist_sort.lookup
    artist_sort.lookup = lambda m: asked.append(m) or {LAUPER: "Lauper, Cyndi", ADAMS: "WRONG"}.get(m)
    sleep = artist_sort.time.sleep
    artist_sort.time.sleep = lambda s: None
    try:
        print("only a MusicBrainz artist lacking one is asked about")
        c = library()
        check(artist_sort.lacking(c) == sorted([LAUPER, GONE]), f"lacking: {artist_sort.lacking(c)}")

        print("a dry run asks and writes nothing")
        found, missing = artist_sort.fill(c, write=False, say=lambda s: None)
        check((found, missing) == (1, 1) and c.execute(
            "SELECT sort_name FROM artists WHERE mbid=?", (LAUPER,)).fetchone()[0] is None, f"{found} {missing}")

        print("filled where there was none; one already there is never replaced")
        asked.clear()
        artist_sort.fill(c, say=lambda s: None)
        got = dict(c.execute("SELECT name, sort_name FROM artists"))
        check(got["Cyndi Lauper"] == "Lauper, Cyndi" and got["Bryan Adams"] == "Adams, Bryan"
              and ADAMS not in asked, f"{got} asked {asked}")
        check(not artist_sort.record(c, ADAMS, "Other, Name"), "record() keeps an existing sort name")

        print("narrowed to the artists named, as a CD add asks")
        c = library()
        asked.clear()
        artist_sort.fill(c, [LAUPER, "local:artist:39"], say=lambda s: None)
        check(asked == [LAUPER], f"asked {asked}")
    finally:
        artist_sort.lookup = real
        artist_sort.time.sleep = sleep
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("artist_sort: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
