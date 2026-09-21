#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `tools/backfill_file_tags.py` `[REQ-LIB-146]`.

An entirely-empty `file_tags` row is re-probed and filled in; a row that
already has any tag data at all is left alone even if some other field is
still NULL; a file that genuinely has nothing to find is reported and
skipped, not repeatedly retried; dry run reports without writing. `probe()`
is faked here, the same reasoning `test_ingest_folder.py` already gives for
not shelling out to real ffprobe in a test that is not testing ffprobe.

    python tools/test_backfill_file_tags.py
"""

import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backfill_file_tags as backfill  # noqa: E402
import ingest_folder  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT);
CREATE TABLE file_tags (file_id INTEGER, title TEXT, artist TEXT, album TEXT,
    track_no INTEGER, disc_no INTEGER, has_art INTEGER, scanned_at INTEGER);
"""


def fixture() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.executescript("""
        -- file 1: the ordinary gap this tool exists for -- an Ogg file whose
        -- probe() once looked in the wrong place for every field.
        INSERT INTO files VALUES (1, 'white-moth/01 - Better People.ogg');
        INSERT INTO file_tags VALUES (1, NULL, NULL, NULL, NULL, NULL, 0, 1000);
        -- file 2: already has a title -- must never be touched, even though
        -- its track_no is also NULL, which alone would look like a gap.
        INSERT INTO files VALUES (2, 'tagged/02 - Something.mp3');
        INSERT INTO file_tags VALUES (2, 'Something', 'Someone', NULL, NULL, NULL, 0, 1000);
        -- file 3: genuinely has no tags at all -- must be reported, not
        -- treated as a failure or retried forever.
        INSERT INTO files VALUES (3, 'silence/untagged.wav');
        INSERT INTO file_tags VALUES (3, NULL, NULL, NULL, NULL, NULL, 0, 1000);
        -- file 4: moved or deleted since it was ingested -- probe() can't
        -- decode it any more.
        INSERT INTO files VALUES (4, 'gone/missing.ogg');
        INSERT INTO file_tags VALUES (4, NULL, NULL, NULL, NULL, NULL, 0, 1000);
    """)
    return c


def fake_probe(path: str):
    if "Better People" in path:
        return {"duration_ms": 186506, "title": "Better People", "artist": "Xavier Rudd",
                "album": "White Moth", "track_no": 1, "disc_no": 1, "has_art": 0}
    if "untagged" in path:
        return {"duration_ms": 5000, "title": None, "artist": None,
                "album": None, "track_no": None, "disc_no": None, "has_art": 0}
    if "missing" in path:
        return None
    raise AssertionError(f"unexpected probe() call for {path!r} -- file 2 must never be re-probed")


def main() -> int:
    real_probe = ingest_folder.probe
    ingest_folder.probe = fake_probe
    try:
        print("interesting_gaps(): only the entirely-empty row is a candidate")
        c = fixture()
        gaps = {r["file_id"] for r in backfill.interesting_gaps(c)}
        check(gaps == {1, 3, 4},
              f"file 2 has a title already and must be excluded, got {gaps}")
        c.close()

        print()
        print("dry run: reports what it would fix, writes nothing")
        c = fixture()
        updated, still_empty, unreadable = backfill.backfill(c, commit=False)
        check((updated, still_empty, unreadable) == (1, 1, 1),
              f"expected (1 fixable, 1 genuinely untagged, 1 unreadable), got "
              f"{(updated, still_empty, unreadable)}")
        row = c.execute("SELECT title FROM file_tags WHERE file_id=1").fetchone()
        check(row["title"] is None, "a dry run must not write")
        c.close()

        print()
        print("--commit: the Ogg file's tags are filled in from probe()'s stream-tag fallback")
        c = fixture()
        updated, still_empty, unreadable = backfill.backfill(c, commit=True)
        check((updated, still_empty, unreadable) == (1, 1, 1),
              f"got {(updated, still_empty, unreadable)}")
        row = c.execute("SELECT * FROM file_tags WHERE file_id=1").fetchone()
        check((row["title"], row["artist"], row["album"], row["track_no"], row["disc_no"])
              == ("Better People", "Xavier Rudd", "White Moth", 1, 1),
              f"got {dict(row)}")

        print()
        print("the already-tagged row and the genuinely-untagged row are both left as they were")
        untouched = c.execute("SELECT * FROM file_tags WHERE file_id=2").fetchone()
        check((untouched["title"], untouched["artist"]) == ("Something", "Someone"),
              "file 2 must never even reach probe() -- see fake_probe()'s own assertion")
        still = c.execute("SELECT title FROM file_tags WHERE file_id=3").fetchone()
        check(still["title"] is None, "a file with genuinely nothing to find stays NULL")

        print()
        print("re-running after --commit: idempotent, nothing left to fix")
        updated2, still_empty2, unreadable2 = backfill.backfill(c, commit=True)
        check(updated2 == 0, f"the fixed row is no longer entirely empty, got {updated2}")
        check(still_empty2 == 1 and unreadable2 == 1,
              f"the untagged and unreadable rows are still there to report, got "
              f"{(still_empty2, unreadable2)}")
        c.close()
    finally:
        ingest_folder.probe = real_probe

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("backfill_file_tags: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
