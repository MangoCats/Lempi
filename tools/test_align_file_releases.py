#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `align_file_releases.py` `[SPEC-SC-125]`: a CD rip takes its
recorded edition; a folder takes the release titled like its tag or name that
holds most of it; a folder of several albums is matched one album at a time;
a folder named for the artist is no album title; a CD or hand decision stands.

    python tools/test_align_file_releases.py
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import align_file_releases as afr  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def library():
    c = sqlite3.connect(":memory:")
    c.executescript(open(os.path.join(os.path.dirname(HERE), "sql", "schema.sql"), encoding="utf-8").read())
    c.executescript("""
    CREATE TABLE IF NOT EXISTS ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT, stage TEXT,
        outcome TEXT, confidence REAL, detail TEXT, decided_at TEXT);
    INSERT INTO artists (mbid, name, source) VALUES ('a-cyndi', 'Cyndi Lauper', 's'), ('a-eagles', 'Eagles', 's');
    INSERT INTO releases (mbid, title, release_date, source) VALUES
        ('unusual', 'She''s So Unusual', '1983', 's'), ('twelve', 'Twelve Deadly Cyns', '1994', 's'),
        ('desperado', 'Desperado', '1973', 's'), ('border', 'On the Border', '1974', 's'),
        ('box', 'Eagles', '2005', 's');
    """)
    n = 0
    def add(md5, path, recs, tag=None, artist="a-cyndi"):
        nonlocal n
        n += 1
        c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, first_seen, last_seen) "
                  "VALUES (?, ?, ?, 1, 0, 'mp3', 1, 't', 't')", (n, md5, path))
        if tag:
            c.execute("INSERT INTO file_tags (file_id, album, scanned_at) VALUES (?, ?, 0)", (n, tag))
        for i, m in enumerate(recs):
            pid = n * 100 + i
            c.execute("INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, boundary_src) "
                      "VALUES (?, ?, 'radio', ?, ?, 'x')", (pid, n, i * 10, i * 10 + 5))
            c.execute("INSERT OR IGNORE INTO recordings (mbid, title, source) VALUES (?, 't', 's')", (m,))
            c.execute("INSERT INTO passage_recordings (passage_id, mbid, weight, source) VALUES (?, ?, 1, 's')", (pid, m))
            c.execute("INSERT OR IGNORE INTO recording_artists (mbid, artist_mbid, weight, source) VALUES (?, ?, 1, 's')",
                      (m, artist))
    # A CD rip of Twelve Deadly Cyns: one image file, its edition recorded.
    add("cd-twelve", "/m/Cyndi Lauper/Twelve (1994)/x.mp3", ["girls", "time"])
    c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, detail, decided_at) "
              "VALUES ('cd-twelve','rip','chosen',?,'t')",
              (json.dumps({"chosen": "twelve"}),))
    # A folder holding She's So Unusual, file per track.
    add("f1", "/m/Cyndi Lauper/She's So Unusual (1983)/01.mp3", ["girls"])
    add("f2", "/m/Cyndi Lauper/She's So Unusual (1983)/02.mp3", ["time"])
    # An artist folder of two albums, by tag; and the box set titled "Eagles" holds everything.
    add("e1", "/m/Eagles/a.mp3", ["tequila"], "Desperado", "a-eagles")
    add("e2", "/m/Eagles/b.mp3", ["already"], "On the Border", "a-eagles")
    for rel, m, chosen in (("unusual", "girls", 1), ("twelve", "girls", 0), ("unusual", "time", 1), ("twelve", "time", 0),
                           ("desperado", "tequila", 1), ("box", "tequila", 0), ("border", "already", 1), ("box", "already", 0)):
        c.execute("INSERT INTO release_recordings (release_mbid, mbid, source, chosen) VALUES (?, ?, 's', ?)", (rel, m, chosen))
    return c


def main() -> int:
    print("a CD rip, its folder, and an artist folder of two albums")
    c = library()
    p = afr.plan(c)
    got = {m: r for m, (r, _) in p["write"].items()}
    check(got == {"cd-twelve": "twelve", "f1": "unusual", "f2": "unusual", "e1": "desperado", "e2": "border"},
          f"each file its own album, not the box set named like the folder: {got}")
    check(sorted((a, b) for a, b, _ in p["changes"]) == [("She's So Unusual", "Twelve Deadly Cyns")] * 2,
          f"only the CD rip's tracks change name: {p['changes']}")

    print("a decision by hand stands; --commit records the rest, once")
    c.execute("INSERT INTO file_releases VALUES ('f1', 'twelve', 'manual', 't')")
    p = afr.plan(c)
    check("f1" not in p["write"] and p["kept"] == 1, f"kept: {p['kept']} {sorted(p['write'])}")
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("align_file_releases: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
