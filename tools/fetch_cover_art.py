#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fetch missing cover art from the Cover Art Archive `[REQ-VIS-170]`.

Measured on this library: 1,986 files carry no embedded picture, and 1,656 of
them (83%) already have a `folder.jpg` beside them -- the player now looks
there and needs nothing from the network. Those folders are asked about
anyway, and deliberately: `folder.jpg` is one image, and the archive also
carries the **back** of the sleeve, which no folder convention provides.

So this asks about every release behind an art-less file -- **484** on this
library, not the 309 whose folders have nothing. One manifest request each
tells the archive's answer exactly, then at most one download per side.

Keyed by **release**, not by folder. A directory can hold more than one album,
which is exactly the case on the DAO rips that make up most of the remaining
gap, and `folder.jpg` cannot tell them apart.

**Front and back both**, because MuLibPlay carried both and showed them side by
side; 559 of its 675 albums had a back. Nothing else: its art totalled 80.5 MB
of a 90.7 MB database, and a third image nobody displays would be that bargain
again.

Vipunen's job, not the player's. `[REQ-NEG-100]` forbids *playback* depending on
a live external service; fetching at build time into local storage is the
division that requirement exists to protect. Nothing here runs on the appliance.

    python tools/fetch_cover_art.py data/library.db [--limit N] [--refetch]
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

# The archive asks for one request a second and a User-Agent that identifies
# the client. Both are conditions of use, not politeness.
GAP = 1.0
AGENT = "Lempi/0.1 (+https://github.com/MangoCats/Lempi)"
BASE = "https://coverartarchive.org"

# Below this it is not a picture -- the same floor the player applies, and the
# one MuLibPlay applied before it.
MIN_BYTES = 256

# **Thumbnails, not the originals.** The archive serves full-resolution scans:
# measured here at 2.9 MB average and one at 18.8 MB, against MuLibPlay's 68 KB
# average for the same job. Fetching originals would have added roughly 1.2 GB
# to a 978 MB library to fill a 200-pixel-tall box.
#
# Which size is chosen happens in `covers()` below, from the manifest.
# No thumbnail should approach this. A hit means the archive served something
# unexpected and it is not worth putting in the library.
MAX_BYTES = 2_000_000

DDL = """
CREATE TABLE IF NOT EXISTS cover_art (
    release_mbid TEXT PRIMARY KEY,
    front        BLOB,
    back         BLOB,
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL);
"""


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def get(url: str, want_json: bool = False) -> bytes | None:
    """One image or manifest, or `None` if the archive has not got it.

    A 404 is the ordinary answer for a release nobody has photographed, and is
    not worth a retry or a mention. 503 means slow down.
    """
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    delay = 2.0
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = r.read()
            if want_json:
                return data
            return data if len(data) >= MIN_BYTES else None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            return None
        except (urllib.error.URLError, TimeoutError):
            if attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refetch", action="store_true",
                    help="ask again about releases already tried")
    args = ap.parse_args()

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.executescript(DDL)
    conn.commit()

    # Every release behind a file with no EMBEDDED picture. Folders that
    # already hold a `folder.jpg` are not excluded -- SQL cannot see the
    # filesystem, and they are worth asking about regardless for the back
    # cover the folder convention has no place for.
    todo = conn.execute(
        """SELECT DISTINCT rr.release_mbid
             FROM passages p
             JOIN file_tags ft ON ft.file_id = p.file_id AND ft.has_art = 0
             JOIN passage_recordings pr ON pr.passage_id = p.passage_id
             JOIN release_recordings rr ON rr.mbid = pr.mbid AND rr.chosen = 1
            WHERE p.kind = 'radio'
              AND (?1 OR rr.release_mbid NOT IN (SELECT release_mbid FROM cover_art))
            ORDER BY rr.release_mbid""",
        (1 if args.refetch else 0,)).fetchall()
    todo = [r[0] for r in todo]
    if args.limit:
        todo = todo[:args.limit]

    # A manifest plus up to two images, each followed by the courtesy gap.
    say(f"{len(todo)} release(s) to ask about  "
        f"(~{len(todo) * GAP * 2.5 / 60:.0f} min)\n")
    if not todo:
        return 0

    fronts = backs = neither = 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    def covers(mbid: str, group: bool = False) -> tuple[bytes | None, bytes | None]:
        """The front and back of one release, via its manifest.

        **One request to find out what exists**, then at most one download per
        side. The obvious alternative -- ask for `/front-500`, fall back to
        `-250`, fall back to the original, and the same again for the back --
        is up to nine requests, most of them 404s, and measured at 84 seconds
        per release. The manifest costs about four and answers exactly.
        """
        what = "release-group" if group else "release"
        raw = get(f"{BASE}/{what}/{mbid}", want_json=True)
        time.sleep(GAP)
        if raw is None:
            return None, None
        try:
            images = json.loads(raw).get("images", [])
        except (json.JSONDecodeError, AttributeError):
            return None, None

        def pick(flag: str) -> bytes | None:
            for im in images:
                if not im.get(flag):
                    continue
                thumbs = im.get("thumbnails") or {}
                # 500 is the smallest with headroom over a 200px box on a
                # high-density screen; 250 for releases that lack it. The
                # original is never taken -- that is what cost 2.9 MB a cover.
                url = thumbs.get("500") or thumbs.get("250")
                if not url:
                    continue
                data = get(url)
                time.sleep(GAP)
                if data is not None and len(data) <= MAX_BYTES:
                    return data
            return None

        return pick("front"), pick("back")

    for i, mbid in enumerate(todo, 1):
        front, back = covers(mbid)
        # The release group is the fallback for a front only. A back cover
        # belongs to a particular pressing, so the group's is not this one's.
        if front is None:
            front, _ = covers(mbid, group=True)

        conn.execute(
            "INSERT OR REPLACE INTO cover_art VALUES (?1,?2,?3,'coverartarchive',?4)",
            (mbid, front, back, now))
        conn.commit()
        fronts += front is not None
        backs += back is not None
        neither += front is None and back is None
        if i % 25 == 0 or i == len(todo):
            say(f"  {i}/{len(todo)}   {fronts} front, {backs} back, {neither} neither")

    size = conn.execute(
        "SELECT COALESCE(SUM(LENGTH(front)),0) + COALESCE(SUM(LENGTH(back)),0) "
        "  FROM cover_art").fetchone()[0]
    say(f"\n  {fronts} front, {backs} back, {neither} with nothing")
    say(f"  cover_art now holds {size / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
