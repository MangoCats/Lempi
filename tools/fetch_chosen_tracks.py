#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vipunen S3, third pass: the track listing of each chosen release.

The browse endpoint that finds candidate releases returns `media` without the
tracks inside it, so `position` and `track_length_ms` come back empty. Both
matter: position is what lets an album be listed in the order it was recorded
onto the record rather than alphabetically `[REQ-VIS-190]`, and it should come
from MusicBrainz rather than from whatever the person who ripped the disc typed
into a tag.

Fetching it per *candidate* would be ruinous -- one recording here has 86 of
them. Fetching it per **chosen** release is cheap, because a chosen release
covers every one of its tracks at once: an album of twelve songs is one request,
not twelve. Run after `choose_release.py`.

    python tools/fetch_chosen_tracks.py data/library.db [--limit N]
"""

import argparse
import json
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import time
import urllib.error
import urllib.request

UA = "Lempi-Vipunen/0.1 ( https://github.com/MangoCats/Lempi )"
BASE = "https://musicbrainz.org/ws/2/release/"
RATE_S = 1.0


def get(url: str, tries: int = 4) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 503) and attempt < tries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS musicbrainz_cache ("
        "  mbid TEXT PRIMARY KEY, kind TEXT NOT NULL, response TEXT NOT NULL,"
        "  fetched_at INTEGER NOT NULL);")

    # Only releases something actually chose, and only those still missing a
    # position -- which is what makes a second run cost nothing.
    try:
        conn.execute("ALTER TABLE release_recordings ADD COLUMN disc INTEGER")
    except sqlite3.OperationalError:
        pass                       # already added by an earlier run

    todo = [r[0] for r in conn.execute(
        "SELECT DISTINCT release_mbid FROM release_recordings "
        " WHERE chosen = 1 AND position IS NULL ORDER BY release_mbid")]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("nothing to fetch: every chosen release already has its track listing")
        return 0

    print(f"{len(todo)} chosen release(s), ~{len(todo) * RATE_S / 60:.0f} minutes")
    started = time.time()
    placed = failed = 0

    for i, rid in enumerate(todo, 1):
        row = conn.execute("SELECT response FROM musicbrainz_cache WHERE mbid = ?",
                           (rid,)).fetchone()
        if row:
            doc = json.loads(row[0])
        else:
            try:
                doc = get(f"{BASE}{rid}?inc=recordings&fmt=json")
            except Exception as e:                      # noqa: BLE001
                failed += 1
                print(f"  {rid}: {e}", file=sys.stderr)
                time.sleep(RATE_S)
                continue
            conn.execute(
                "INSERT OR REPLACE INTO musicbrainz_cache (mbid, kind, response, fetched_at)"
                " VALUES (?1, 'release+recordings', ?2, ?3)",
                (rid, json.dumps(doc or {}), int(time.time())))
            time.sleep(RATE_S)

        if not doc:
            continue
        # A release carries its discs in order, and each disc its tracks. Both
        # numbers are kept: disc two's opener is not the album's second track.
        for medium in doc.get("media", []) or []:
            disc = medium.get("position")
            for track in medium.get("tracks", []) or []:
                rec = (track.get("recording") or {}).get("id")
                if not rec:
                    continue
                n = conn.execute(
                    "UPDATE release_recordings SET position = ?1, track_length_ms = ?2 "
                    " WHERE release_mbid = ?3 AND mbid = ?4",
                    (track.get("position"), track.get("length"), rid, rec)).rowcount
                if n:
                    # Which disc, kept beside the track it belongs to -- disc
                    # two's opener is not the album's second track.
                    conn.execute("UPDATE release_recordings SET disc = ?1 "
                                 " WHERE release_mbid = ?2 AND mbid = ?3", (disc, rid, rec))
                placed += n

        if i % 20 == 0 or i == len(todo):
            conn.commit()
            done = time.time() - started
            print(f"  {i}/{len(todo)}  {placed} positions, {failed} failed "
                  f"({done / 60:.1f} min)", flush=True)

    conn.commit()
    print(f"\ndone: {placed} track positions from {len(todo) - failed} release(s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted; re-run to carry on")
        sys.exit(130)
