#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `console.py`'s `pending_counts()`/`_pending_for_passage()`
`[REQ-VIS-275]`.

Found live: a boundary edit saved in Lempi's own editor read as identical to
one already pushed to lempipi, from this very console's profile page --
`boundary_reviews.applied_at IS NULL` was never surfaced anywhere. This is
the fix: how many drafts across the three review tables are sitting
unapplied, globally and for one passage, counted the same way
`tools/apply_reviews.py`/`tools/apply_boundary_reviews.py` already do.

    python tools/test_console_pending.py
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import console  # noqa: E402

SCHEMA = """
CREATE TABLE id_reviews (passage_id INTEGER PRIMARY KEY, decision TEXT,
    chosen_mbid TEXT, decided_at TEXT, applied_at TEXT);
CREATE TABLE boundary_reviews (passage_id INTEGER PRIMARY KEY,
    start_ms INTEGER, end_ms INTEGER, decided_at TEXT, applied_at TEXT);
CREATE TABLE artist_reviews (recording_mbid TEXT PRIMARY KEY, passage_id INTEGER,
    artist_mbid TEXT, artist_name TEXT, decided_at TEXT, applied_at TEXT);
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


def main() -> int:
    print("no review tables at all: zero, not a crash")
    bare = sqlite3.connect(":memory:")
    check(console.pending_counts(bare) == {"id": 0, "boundary": 0, "artist": 0, "total": 0},
          f"got {console.pending_counts(bare)}")

    print("a mix of applied and unapplied rows across all three tables")
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.executescript("""
        INSERT INTO id_reviews VALUES (1, 'reassigned', 'rec-a', 't1', NULL);
        INSERT INTO id_reviews VALUES (2, 'reassigned', 'rec-b', 't2', 't2');  -- already applied
        INSERT INTO boundary_reviews VALUES (1, 0, 1000, 't3', NULL);
        INSERT INTO boundary_reviews VALUES (3, 0, 1000, 't4', NULL);
        INSERT INTO artist_reviews VALUES ('rec-a', 1, 'art-a', 'A Band', 't5', NULL);
    """)
    counts = console.pending_counts(c)
    check(counts == {"id": 1, "boundary": 2, "artist": 1, "total": 4}, f"got {counts}")

    print()
    print("_pending_for_passage: only what actually belongs to this passage's own "
          "id/boundary rows and its own recording's artist row")
    pending1 = console._pending_for_passage(c, 1, ["rec-a"])
    check(set(pending1) == {"id", "boundary", "artist"}, f"passage 1 should have all three, got {pending1}")
    pending3 = console._pending_for_passage(c, 3, [])
    check(set(pending3) == {"boundary"}, f"passage 3 should have only boundary, got {pending3}")
    pending2 = console._pending_for_passage(c, 2, ["rec-b"])
    check(pending2 == {}, f"passage 2's id_review is already applied, expected nothing, got {pending2}")
    pending_missing = console._pending_for_passage(c, 999, [])
    check(pending_missing == {}, f"a passage with nothing recorded must report nothing, got {pending_missing}")

    print()
    print("a library predating some of these tables: the missing one reads as zero, not an error")
    c2 = sqlite3.connect(":memory:")
    c2.executescript("CREATE TABLE boundary_reviews (passage_id INTEGER PRIMARY KEY, "
                     "start_ms INTEGER, end_ms INTEGER, decided_at TEXT, applied_at TEXT);"
                     "INSERT INTO boundary_reviews VALUES (5, 0, 1000, 't', NULL);")
    check(console.pending_counts(c2) == {"id": 0, "boundary": 1, "artist": 0, "total": 1},
          f"got {console.pending_counts(c2)}")
    check(console._pending_for_passage(c2, 5, []) == {"boundary": {"decided_at": "t"}},
          f"got {console._pending_for_passage(c2, 5, [])}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console pending: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
