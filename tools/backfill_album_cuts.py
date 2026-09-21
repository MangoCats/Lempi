#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Give an old radio-only passage the album twin it never got `[SPEC-SA-110]`,
`[GDE-BMK-030]`.

Every recording-in-file span is meant to carry two passages, one per `kind`
-- the single best schema idea in the lineage, and the migrated MuLibPlay
data has it: 8,079 `album` against 8,078 `radio`, essentially 1:1. Everything
Lempi's own newer ingest paths wrote does not: 116 `ingest:whole-file` and
136 `segment:silence-*+acoustid` passages, all `radio`, none `album` -- found
live 2026-08-31 starting from one flagged track with no album cut at all.

The backfill is mechanical, not a re-detection. `analyze_amplitude.py` only
ever writes `lead_in_ms`/`lead_out_ms` on a `kind='radio'` row `[SPEC-SUI-*]`;
it never touches `start_ms`/`end_ms`. So for every one of these newer
passages the hard boundary the album cut needs is already sitting right
there on its radio sibling -- copied, not recomputed. An album cut's own
segue points equal its own hard boundaries `[GDE-BMK-030]`: `lead_in_ms=0`,
`lead_out_ms=0`, `gain_db=0.0`, permanently, never NULL the way a radio cut
awaiting analysis is.

Idempotent and safe to re-run: a `(file_id, kind, start_ms, end_ms)` pair
already present is left alone, matching `passages_span`'s own unique index.

    python tools/backfill_album_cuts.py <listener.db>              dry run
    python tools/backfill_album_cuts.py <listener.db> --commit      write
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

# Fade gets the schema's own default (20ms/exponential) by omission, the
# same de-click ramp every passage gets regardless of kind `[SPEC-SUI-226]`
# -- an album cut still plays start-to-end through real speakers and still
# deserves the click guard, even though it is never itself trimmed.
_INSERT_ALBUM = """
    INSERT INTO passages (file_id, kind, start_ms, end_ms,
                           lead_in_ms, lead_out_ms, gain_db, boundary_src)
    VALUES (?1, 'album', ?2, ?3, 0, 0, 0.0, ?4)
"""


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def missing_album_twins(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every `radio` passage with no `album` row at its own exact span, from
    a source this tool actually knows how to pair by span.

    Scoped to `ingest:whole-file` and `segment:silence-*` on purpose, not
    every `radio` row: those are the two paths confirmed to leave `start_ms`/
    `end_ms` identical between a passage's own kinds (`analyze_amplitude.py`
    only ever writes `lead_in_ms`/`lead_out_ms`, never the span). The
    migrated `inherited:mulib` data does not share that property -- its own
    radio cut is independently trimmed at the row level, so its `start_ms`
    can legitimately differ from its album twin's by design, not by a gap.
    Matching on exact span there would misread an already-paired row as
    missing one and duplicate it. `inherited:mulib` is already ~1:1 paired
    (8,079 album / 8,078 radio) and is not this tool's problem to chase.
    """
    return conn.execute("""
        SELECT r.passage_id, r.file_id, r.start_ms, r.end_ms, r.boundary_src
          FROM passages r
         WHERE r.kind = 'radio'
           AND (r.boundary_src = 'ingest:whole-file' OR r.boundary_src LIKE 'segment:silence-%')
           AND NOT EXISTS (
               SELECT 1 FROM passages a
                WHERE a.kind = 'album' AND a.file_id = r.file_id
                  AND a.start_ms = r.start_ms AND a.end_ms = r.end_ms)
         ORDER BY r.file_id, r.start_ms
    """).fetchall()


def backfill(conn: sqlite3.Connection, commit: bool) -> tuple[int, int]:
    """Returns `(would_add, recordings_copied)`. Writes only when `commit`."""
    radios = missing_album_twins(conn)
    added = copied = 0
    for r in radios:
        # Not a blind suffix -- `+backfill:album-twin` says plainly that this
        # span was never independently detected for `album`, only copied
        # from a `radio` sibling that was, so a later reader is never misled
        # into thinking two detections agreed `[SPEC-DF-102]`'s own reasoning
        # for keeping provenance honest.
        boundary_src = f"{r['boundary_src']}+backfill:album-twin"
        if not commit:
            added += 1
            continue
        album_id = conn.execute(_INSERT_ALBUM, (
            r["file_id"], r["start_ms"], r["end_ms"], boundary_src)).lastrowid
        added += 1
        for pr in conn.execute(
                "SELECT mbid, weight, source FROM passage_recordings WHERE passage_id=?1",
                (r["passage_id"],)):
            conn.execute(
                "INSERT INTO passage_recordings (passage_id, mbid, weight, source) "
                "VALUES (?1,?2,?3,?4)", (album_id, pr["mbid"], pr["weight"], pr["source"]))
            copied += 1
    return added, copied


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")

    if not args.commit:
        added, _ = backfill(conn, commit=False)
        say(f"would add {added} album cut(s); nothing was written.")
        say("Re-run with --commit to do it.")
        return 0

    conn.execute("BEGIN IMMEDIATE")
    added, copied = backfill(conn, commit=True)
    conn.commit()
    say(f"added {added} album cut(s), copied {copied} recording link(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
