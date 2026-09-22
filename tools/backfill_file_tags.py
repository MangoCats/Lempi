#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Re-read tags for a file whose `file_tags` row came back entirely empty
`[REQ-LIB-146]`.

`ingest_folder.py`'s `probe()` only ever asked `ffprobe` for `format`-level
tags. MP3's ID3 tags live there, so 5,490 of 5,682 `.mp3` files in this
library read fine -- but Ogg Vorbis comments land on the *stream* instead,
which `format_tags` never sees: all 27 `.ogg` files in the library came back
with `title`/`artist`/`album`/`track_no`/`disc_no` all NULL, despite every
one of them carrying a full tag set on disk. Found live 2026-08-31 chasing
why a MusicBrainz release suggestion scored 0/14 for `Xavier Rudd/White
Moth` even after fetching the right release directly.

`probe()` itself is already fixed to fall back to stream-level tags when the
format level has nothing. This backfill re-runs the fixed `probe()` against
every file whose `file_tags` row is still entirely empty, and writes back
whatever it now finds -- mechanical, not a re-detection of anything else
about the file.

Idempotent and safe to re-run: a file that already has any tag data at all
is left alone (`interesting_gaps()`'s own WHERE clause), and one that
genuinely has no tags to find (silence, a bad rip) is reported and skipped
rather than repeatedly retried.

    python tools/backfill_file_tags.py <listener.db>              dry run
    python tools/backfill_file_tags.py <listener.db> --commit      write
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import ingest_folder  # noqa: E402 -- reuses probe(), not a reimplementation


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def interesting_gaps(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every file whose own `file_tags` row has nothing in it at all -- not
    "missing an album" or "missing a track number", which can be genuinely
    true of a file's real tags, but the specific all-NULL shape a probe that
    looked in the wrong place for every field produces.
    """
    return conn.execute("""
        SELECT f.file_id, f.path
          FROM files f JOIN file_tags t ON t.file_id = f.file_id
         WHERE t.title IS NULL AND t.artist IS NULL AND t.album IS NULL
           AND t.track_no IS NULL AND t.disc_no IS NULL
         ORDER BY f.file_id
    """).fetchall()


def backfill(conn: sqlite3.Connection, commit: bool) -> tuple[int, int, int]:
    """Returns `(updated, still_empty, unreadable)`. Writes only when `commit`."""
    updated = still_empty = unreadable = 0
    now = int(time.time())
    for row in interesting_gaps(conn):
        info = ingest_folder.probe(row["path"])
        if info is None:
            say(f"  SKIP  {row['path']}  (would not decode -- moved, or unreadable)")
            unreadable += 1
            continue
        if not any((info["title"], info["artist"], info["album"],
                    info["track_no"], info["disc_no"])):
            say(f"  none  {row['path']}  (genuinely no tags to find)")
            still_empty += 1
            continue
        say(f"  {'would fix' if not commit else 'fixed'}  {row['path']}  "
            + (f"“{info['title']}”" if info["title"] else "(no title)"))
        updated += 1
        if not commit:
            continue
        conn.execute(
            "UPDATE file_tags SET title=?1, artist=?2, album=?3, track_no=?4, "
            "disc_no=?5, has_art=?6, scanned_at=?7 WHERE file_id=?8",
            (info["title"], info["artist"], info["album"], info["track_no"],
             info["disc_no"], info["has_art"], now, row["file_id"]))
    return updated, still_empty, unreadable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")

    gaps = interesting_gaps(conn)
    say(f"{len(gaps)} file(s) with an entirely empty file_tags row\n")

    if not args.commit:
        updated, still_empty, unreadable = backfill(conn, commit=False)
        say(f"\nwould fix {updated}, {still_empty} genuinely untagged, "
            f"{unreadable} unreadable; nothing was written.")
        say("Re-run with --commit to do it.")
        return 0

    conn.execute("BEGIN IMMEDIATE")
    updated, still_empty, unreadable = backfill(conn, commit=True)
    conn.commit()
    say(f"\nfixed {updated}, {still_empty} genuinely untagged, {unreadable} unreadable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
