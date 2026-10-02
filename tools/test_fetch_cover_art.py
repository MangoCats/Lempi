#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `fetch_cover_art.py` `[SPEC-COV-060]`: which albums are asked
about, that only a missing side is filled, that an archive which fails is not
taken for one with nothing, and the release-group fallback by the group's own
id. The archive is faked; the real one was probed 2026-10-02.

    python tools/test_fetch_cover_art.py
"""
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_cover_art as fca  # noqa: E402

FAILED = []
JPEG = b"\xff\xd8\xff" + b"j" * 400


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def library():
    c = sqlite3.connect(":memory:")
    c.executescript("""
    CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT, release_group TEXT);
    CREATE TABLE release_recordings (release_mbid TEXT, mbid TEXT, chosen INTEGER);
    CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
    CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, kind TEXT);
    """)
    # rel-cd: a CD add, no row. rel-mulib: a front of its own, no back.
    # rel-full: both. rel-other: not chosen by anything. rel-nogroup: no front, archive has none.
    for n, (rel, group) in enumerate((("rel-cd", "grp-cd"), ("rel-mulib", None), ("rel-full", None),
                                      ("rel-nogroup", "grp-empty")), 1):
        c.execute("INSERT INTO releases VALUES (?, 't', ?)", (rel, group))
        c.execute("INSERT INTO passages VALUES (?, 'radio')", (n,))
        c.execute("INSERT INTO passage_recordings VALUES (?, ?)", (n, f"rec{n}"))
        c.execute("INSERT INTO release_recordings VALUES (?, ?, 1)", (rel, f"rec{n}"))
    c.execute("INSERT INTO release_recordings VALUES ('rel-other', 'rec1', 0)")
    fca.prepare(c)
    c.execute("INSERT INTO cover_art (release_mbid, front, back, source, fetched_at) VALUES "
              "('rel-mulib', ?, NULL, 'inherited:mulib', 'x'), ('rel-full', ?, ?, 'found:folder', 'x')",
              (b"MINE" * 100, JPEG, JPEG))
    return c


def main() -> int:
    asked = []
    archive = {("rel-cd", False): (None, b"BACK" * 100), ("grp-cd", True): (b"GRPF" * 100, None),
               ("rel-mulib", False): (b"THEIRS" * 100, b"MBACK" * 100), ("rel-nogroup", False): (None, None),
               ("grp-empty", True): (None, None)}
    real, sleep = fca.covers, fca.time.sleep
    fca.covers = lambda mbid, group=False: asked.append((mbid, group)) or archive[(mbid, group)]
    try:
        print("asked about: each album shown that lacks a side, not one with both, not one not shown")
        c = library()
        todo = fca.wanted(c, 60)
        check(todo == [("rel-cd", True, True), ("rel-mulib", False, True), ("rel-nogroup", True, True)], f"{todo}")

        print("only the missing side is filled; a front of its own stays; the group's front by the group's id")
        fca.fetch_for(c, todo, say=lambda s: None)
        rows = {r: (f, b, s) for r, f, b, s in c.execute("SELECT release_mbid, front, back, source FROM cover_art")}
        check(rows["rel-cd"][:2] == (b"GRPF" * 100, b"BACK" * 100), "a CD add gets the group's front and its own back")
        check(rows["rel-mulib"][:2] == (b"MINE" * 100, b"MBACK" * 100) and rows["rel-mulib"][2] ==
              "inherited:mulib+coverartarchive", f"MuLibPlay's front kept, the back added, both named: {rows['rel-mulib'][2]}")
        check(("grp-cd", True) in asked and ("rel-cd", True) not in asked, f"the group asked by its own id: {asked}")

        print("asked recently is not asked again; given by name, it is")
        check(fca.wanted(c, 60) == [], f"{fca.wanted(c, 60)}")
        check(fca.wanted(c, 60, ["rel-nogroup", "rel-full"]) == [("rel-nogroup", True, True)], "by name")
        c.execute("UPDATE cover_art SET caa_asked_at = '2026-01-01T00:00:00' WHERE release_mbid = 'rel-nogroup'")
        check([r for r, _, _ in fca.wanted(c, 60)] == ["rel-nogroup"], "asked long ago, asked again")

        print("an archive that fails leaves the release unasked")
        c = library()

        def down(mbid, group=False):
            raise fca.Unavailable("HTTP 503")
        fca.covers = down
        n = fca.fetch_for(c, [("rel-cd", True, True)], say=lambda s: None)
        check(n["unavailable"] == 1 and c.execute("SELECT count(*) FROM cover_art WHERE release_mbid='rel-cd'")
              .fetchone()[0] == 0, f"nothing recorded: {n}")
        check([r for r, _, _ in fca.wanted(c, 60)][0] == "rel-cd", "and it is asked next time")
    finally:
        fca.covers, fca.time.sleep = real, sleep
    print("a thumbnail by either naming: 500 or large, else 250 or small; never the original")
    check(fca.thumbnail({"thumbnails": {"large": "L", "small": "S"}}) == "L", "the older naming")
    check(fca.thumbnail({"thumbnails": {"250": "a", "500": "b", "1200": "c"}}) == "b", "the newer")
    check(fca.thumbnail({"thumbnails": {"small": "S"}}) == "S" and fca.thumbnail({"image": "orig"}) is None,
          "the smallest when that is all; none rather than the original")
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("fetch_cover_art: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
