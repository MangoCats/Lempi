#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An artist's sort name, from MusicBrainz `[REQ-VIS-182]`.

Browse sorts artists by `artists.sort_name` -- "Adams, Bryan", "Cars, The" --
and by the name where there is none. MuLibPlay's import brought one for
nearly every artist it knew; artists identified since came without one, 95 of
them by 2026-10-01, Cyndi Lauper among them, sorting under C.

`record()` fills a sort name an artist lacks and never replaces one, so a
person's or MuLibPlay's stands. `fill()` asks MusicBrainz for each artist
lacking one, one request a second, as `fetch_releases.py` does; a CD add
calls it for the artists AcoustID named, which come with no sort name, and
this command runs it for the whole library.

Dry run unless `--commit`.

    python tools/artist_sort.py data/library.db [--commit]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_releases  # noqa: E402  -- get(), RATE_S, the User-Agent
import lempi_db  # noqa: E402

MB_WS = fetch_releases.BASE.rsplit("/", 1)[0]
# A MusicBrainz artist id. MuLibPlay's own `local:artist:N` ids name no one
# MusicBrainz knows, and are not asked about.
MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def record(conn, mbid: str, sort_name: str | None) -> bool:
    """Fill `mbid`'s sort name if it has none. True when it was filled."""
    if not sort_name:
        return False
    return conn.execute(
        "UPDATE artists SET sort_name = ?2 WHERE mbid = ?1 AND (sort_name IS NULL OR sort_name = '')",
        (mbid, sort_name)).rowcount > 0


def lacking(conn, mbids=None) -> list[str]:
    """MusicBrainz artists with no sort name -- of `mbids`, when given."""
    want = set(mbids) if mbids is not None else None
    return sorted(m for (m,) in conn.execute(
        "SELECT mbid FROM artists WHERE sort_name IS NULL OR sort_name = ''")
        if MBID.match(m or "") and (want is None or m in want))


def lookup(mbid: str) -> str | None:
    return (fetch_releases.get(f"{MB_WS}/artist/{mbid}?fmt=json") or {}).get("sort-name")


def fill(conn, mbids=None, write: bool = True, say=print) -> tuple[int, int]:
    """Ask MusicBrainz for each artist lacking a sort name, and record it
    unless `write` is false. `(found, not_found)`."""
    found = missing = 0
    for i, mbid in enumerate(lacking(conn, mbids)):
        if i:
            time.sleep(fetch_releases.RATE_S)
        sort_name = lookup(mbid)
        name = (conn.execute("SELECT name FROM artists WHERE mbid = ?1", (mbid,)).fetchone() or ["?"])[0]
        if not sort_name:
            say(f"  NOT FOUND  {name}  ({mbid})")
            missing += 1
            continue
        if write:
            record(conn, mbid, sort_name)
        say(f"  {name}  ->  {sort_name}")
        found += 1
    return found, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true", help="write; without it, only say what")
    args = ap.parse_args()
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=args.commit)
    if not lacking(conn):
        print("every MusicBrainz artist here has a sort name")
        return 0
    found, missing = fill(conn, write=args.commit)
    if args.commit:
        conn.commit()
        print(f"\nfilled {found} sort name(s)" + (f"; {missing} not found" if missing else ""))
    else:
        print(f"\nwould fill {found} sort name(s)" + (f"; {missing} not found" if missing else "")
              + ". Nothing written; re-run with --commit.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
