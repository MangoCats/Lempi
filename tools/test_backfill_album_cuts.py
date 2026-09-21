#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `tools/backfill_album_cuts.py` `[SPEC-SA-110]`.

A radio-only passage gets a same-span album twin with lead/gain zeroed and
its recording link copied; a passage that already has both is left alone;
dry run reports without writing; and a re-run after `--commit` finds
nothing left to do.

    python tools/test_backfill_album_cuts.py
"""

import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backfill_album_cuts as backfill  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT NOT NULL, start_ms INTEGER, end_ms INTEGER,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    boundary_src TEXT);
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT NOT NULL,
    weight REAL, source TEXT);
"""


def fixture() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.executescript("""
        INSERT INTO files VALUES (1, 'a.mp3'), (2, 'b.mp3'), (3, 'dao.mp3');
        -- file 1: a radio-only passage, single-track ingest -- the ordinary gap
        INSERT INTO passages VALUES
            (10, 1, 'radio', 0, 200000, NULL, NULL, NULL, 'ingest:whole-file');
        INSERT INTO passage_recordings VALUES (10, 'local:audio:aaaa', 1.0, 'local:ingest');
        -- file 2: an inherited:mulib pair whose spans genuinely differ by
        -- design -- out of this tool's scope, and must not be touched even
        -- though a naive exact-span match would call it "missing" one.
        INSERT INTO passages VALUES
            (20, 2, 'radio', 100, 180000, 300, 1200, -2.0, 'inherited:mulib'),
            (21, 2, 'album', 0, 181000, 0, 0, 0.0, 'inherited:mulib');
        INSERT INTO passage_recordings VALUES (20, 'aaaaaaaa-0000-0000-0000-000000000001', 1.0, 's');
        INSERT INTO passage_recordings VALUES (21, 'aaaaaaaa-0000-0000-0000-000000000001', 1.0, 's');
        -- file 3: a DAO-segmented radio-only track -- the other in-scope source
        INSERT INTO passages VALUES
            (30, 3, 'radio', 60000, 240000, NULL, NULL, NULL, 'segment:silence-40dB+acoustid');
        INSERT INTO passage_recordings VALUES (30, 'aaaaaaaa-0000-0000-0000-000000000002', 1.0, 'segment:acoustid');
    """)
    return c


def main() -> int:
    print("dry run: reports what it would add, writes nothing")
    c = fixture()
    added, copied = backfill.backfill(c, commit=False)
    check(added == 2, f"two in-scope passages are missing their album twin, got {added}")
    check(copied == 0, "a dry run must copy nothing")
    n = c.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    check(n == 4, f"a dry run must not write, got {n} passages")
    c.close()

    print()
    print("--commit: the missing twin is added, same span, lead/gain zeroed, "
          "recording link copied [GDE-BMK-030]")
    c = fixture()
    added, copied = backfill.backfill(c, commit=True)
    check(added == 2, f"expected 2 added (whole-file and DAO), got {added}")
    check(copied == 2, f"expected 2 recording links copied, got {copied}")
    dao_album = c.execute(
        "SELECT * FROM passages WHERE file_id=3 AND kind='album'").fetchone()
    check(dao_album is not None, "the DAO-segmented file must also get its album twin")
    if dao_album is not None:
        check((dao_album["start_ms"], dao_album["end_ms"]) == (60000, 240000),
              "the DAO twin must share its radio sibling's own span")
        check(dao_album["boundary_src"] == "segment:silence-40dB+acoustid+backfill:album-twin",
              f"got {dao_album['boundary_src']}")
    album = c.execute(
        "SELECT * FROM passages WHERE file_id=1 AND kind='album'").fetchone()
    check(album is not None, "the album twin must exist")
    if album is not None:
        check((album["start_ms"], album["end_ms"]) == (0, 200000),
              "the twin must share the radio sibling's own span")
        check((album["lead_in_ms"], album["lead_out_ms"], album["gain_db"]) == (0, 0, 0.0),
              "an album cut's segue points must equal its own hard boundaries")
        check(album["boundary_src"] == "ingest:whole-file+backfill:album-twin",
              f"provenance must say this was copied, not detected, got {album['boundary_src']}")
        prs = {r["mbid"] for r in c.execute(
            "SELECT mbid FROM passage_recordings WHERE passage_id=?", (album["passage_id"],))}
        check(prs == {"local:audio:aaaa"}, f"the recording link must be copied, got {prs}")

    print()
    print("a passage that already has both kinds is left alone")
    n2 = c.execute("SELECT COUNT(*) FROM passages WHERE file_id=2").fetchone()[0]
    check(n2 == 2, f"file 2 already had its pair and must stay at 2, got {n2}")

    print()
    print("re-running after --commit: idempotent, nothing left to add")
    added2, copied2 = backfill.backfill(c, commit=True)
    check(added2 == 0, f"nothing should be missing on a second pass, got {added2}")
    check(copied2 == 0, f"nothing should be copied on a second pass, got {copied2}")
    c.close()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("backfill_album_cuts: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
