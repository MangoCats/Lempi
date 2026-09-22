#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vipunen S3, release half: fill `releases` and `release_recordings` [SPEC-SA-030].

Lempi knows 7,912 recordings and not one release, which is why every album name
it shows comes from a file's own ID3 tag rather than from MusicBrainz. A
Recording is a piece of recorded audio; a Release is the published product, and
*its* title is what an album is called. The link between them is many-to-many,
so it is a join table and not a column, and filling it is ingest work -- Vipunen's
job, never the player's [REQ-VIS-170].

Being a good citizen of a free service is most of the design here:

  * one request per second, which is MusicBrainz's published limit;
  * a real User-Agent with a contact address, which they ask for and enforce;
  * resumable, so an interrupted run costs only what it had not yet fetched;
  * every response cached, so a re-run after a schema change asks nothing twice.

At one request per second the full library is a little over two hours. It is
meant to be left running, and it can be stopped at any point with Ctrl-C.

    python tools/fetch_releases.py data/library.db [--limit N] [--refresh]
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

# They ask for a contact address and will rate-limit or block a generic agent.
UA = "Lempi-Vipunen/0.1 ( https://github.com/MangoCats/Lempi )"
BASE = "https://musicbrainz.org/ws/2/release"

# MusicBrainz asks for at most one request per second averaged over time. This
# is the whole reason the run takes hours; going faster gets an IP blocked, and
# a blocked IP is a worse outcome than a slow scan.
RATE_S = 1.0

# Browse pages at 100; a recording on more than that is rare but real.
PAGE = 100

# Columns the selection needs and the first pass did not collect. Added here
# rather than in Lempi's schema because these are Vipunen's to fill: the player
# only ever reads `releases.title` [SPEC-SA-015].
EXTRA_DDL = [
    # The release GROUP is MusicBrainz's own answer to "these are the same
    # album": every territorial pressing, remaster and reissue of a record
    # shares one. McRhythm had to infer that grouping from track counts and
    # duration signatures `[AM-MB-020]` because it was searching by name and
    # had no identifier to lean on. We do, so we lean on it.
    "ALTER TABLE releases ADD COLUMN release_group TEXT",
    "ALTER TABLE releases ADD COLUMN status TEXT",
    "ALTER TABLE releases ADD COLUMN primary_type TEXT",
    "ALTER TABLE releases ADD COLUMN secondary_types TEXT",
    "ALTER TABLE releases ADD COLUMN country TEXT",
    "ALTER TABLE releases ADD COLUMN track_count INTEGER",
    "ALTER TABLE release_recordings ADD COLUMN track_length_ms INTEGER",
]

# A response that has been fetched once should never be fetched again: the
# answer changes rarely, the run is long, and a re-run after a schema change
# would otherwise repeat two hours of requests for data already on disk.
CACHE_DDL = """
CREATE TABLE IF NOT EXISTS musicbrainz_cache (
    mbid TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    response TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);
"""


def get(url: str, tries: int = 4) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None        # the MBID is gone or was merged away
            # 503 is their documented "slow down", and over a run this long it
            # is routine rather than exceptional -- one in five on the first
            # trial. Backing off is the difference between a complete scan and
            # one with holes that look exactly like missing data.
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


def fetch(mbid: str) -> dict | None:
    """Every release carrying this recording, complete.

    **Browse, not lookup.** A recording lookup with `inc=releases` silently caps
    its list at 25 and ignores `limit`; measured against the browse endpoint,
    one recording that reported 25 actually has 29. Choosing "the best release"
    from an arbitrary first 25 is not a choice, and the truncation is invisible
    -- 25 looks like an answer.

    `release-groups` is what carries primary and secondary type, which is the
    only way to tell an album from a compilation. Without it nothing stops a
    greatest-hits collection winning over the record a song was written for.
    """
    out: list[dict] = []
    offset = 0
    while True:
        url = (f"{BASE}?recording={mbid}&inc=release-groups+media"
               f"&fmt=json&limit={PAGE}&offset={offset}")
        doc = get(url)
        if doc is None:
            return None if offset == 0 else {"releases": out}
        page = doc.get("releases", []) or []
        out.extend(page)
        total = doc.get("release-count", len(out))
        offset += len(page)
        if not page or offset >= total:
            break
        time.sleep(RATE_S)         # each page is its own request
    return {"releases": out}


def store(conn, mbid: str, doc: dict) -> int:
    """Write the releases a recording appears on. Returns how many."""
    n = 0
    for rel in doc.get("releases", []):
        rid = rel.get("id")
        if not rid:
            continue
        group = rel.get("release-group") or {}
        media = rel.get("media") or []
        conn.execute(
            "INSERT OR REPLACE INTO releases "
            "  (mbid, title, release_date, source, release_group, status, "
            "   primary_type, secondary_types, country, track_count) "
            "VALUES (?1, ?2, ?3, 'musicbrainz', ?4, ?5, ?6, ?7, ?8, ?9)",
            (rid, rel.get("title"), rel.get("date"), group.get("id"), rel.get("status"),
             group.get("primary-type"),
             # A comma-separated list because it is read for scoring, never
             # joined against: "Compilation" and "Live" are what disqualify a
             # release from being the record a song belongs to.
             ",".join(group.get("secondary-types") or []) or None,
             rel.get("country"),
             sum(m.get("track-count") or 0 for m in media) or None),
        )
        # Position lives under media/tracks; a recording can legitimately appear
        # twice on one release (a reprise), so the first is taken rather than
        # the last, and its absence is not an error.
        position = length = None
        for medium in media:
            for track in medium.get("tracks", []) or []:
                if track.get("position") is not None:
                    position = track["position"]
                    # The track's own length, which is the evidence that this
                    # release is the one the file came from -- a remaster runs
                    # to a different second than the original.
                    length = track.get("length")
                    break
            if position is not None:
                break
        conn.execute(
            "INSERT OR REPLACE INTO release_recordings "
            "(release_mbid, mbid, position, source, track_length_ms) "
            "VALUES (?1, ?2, ?3, 'musicbrainz', ?4)",
            (rid, mbid, position, length),
        )
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=0, help="stop after N lookups")
    ap.add_argument("--refresh", action="store_true",
                    help="ignore the cache and ask MusicBrainz again")
    args = ap.parse_args()

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(CACHE_DDL)
    for ddl in EXTRA_DDL:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass               # already added by an earlier run

    # Only recordings the library actually uses, and only those without links
    # already -- that is what makes the run resumable.
    todo = [r[0] for r in conn.execute(
        "SELECT DISTINCT r.mbid FROM recordings r "
        "  JOIN passage_recordings pr ON pr.mbid = r.mbid "
        " WHERE NOT EXISTS (SELECT 1 FROM release_recordings rr WHERE rr.mbid = r.mbid) "
        " ORDER BY r.mbid")]
    if args.limit:
        todo = todo[: args.limit]

    if not todo:
        print("nothing to fetch: every used recording already has releases")
        return 0

    print(f"{len(todo)} recording(s) to look up, ~{len(todo) * RATE_S / 60:.0f} minutes "
          f"at MusicBrainz's one-per-second limit (longer where a recording "
          f"needs more than one page)")
    started = time.time()
    linked = found = missing = failed = cached = 0

    for i, mbid in enumerate(todo, 1):
        doc = None
        if not args.refresh:
            row = conn.execute(
                "SELECT response FROM musicbrainz_cache WHERE mbid = ?", (mbid,)).fetchone()
            if row:
                doc = json.loads(row[0])
                cached += 1
        if doc is None:
            try:
                doc = fetch(mbid)
            except Exception as e:                      # noqa: BLE001 - report and go on
                failed += 1
                print(f"  {mbid}: {e}", file=sys.stderr)
                time.sleep(RATE_S)
                continue
            # Cache even an empty answer: "this recording has no releases" is
            # worth remembering, and it is what stops a re-run asking again.
            conn.execute(
                "INSERT OR REPLACE INTO musicbrainz_cache (mbid, kind, response, fetched_at) "
                "VALUES (?1, 'recording+releases', ?2, ?3)",
                (mbid, json.dumps(doc or {}), int(time.time())))
            time.sleep(RATE_S)

        if not doc:
            missing += 1
        else:
            added = store(conn, mbid, doc)
            linked += added
            found += 1 if added else 0

        # Commit as it goes: a run this long must not lose an hour to a
        # power cut, and SQLite handles the frequency without complaint.
        if i % 25 == 0 or i == len(todo):
            conn.commit()
            done = time.time() - started
            rate = i / max(done, 1e-9)
            print(f"  {i}/{len(todo)}  {linked} links, {missing} without releases, "
                  f"{failed} failed, {cached} from cache  "
                  f"({done / 60:.1f} min, ~{(len(todo) - i) / max(rate, 1e-9) / 60:.0f} left)",
                  flush=True)

    conn.commit()
    albums = conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    print(f"\ndone: {linked} recording-release links over {albums} releases, "
          f"{missing} recordings with none, {failed} failed")
    if failed:
        print("re-run to retry the failures; everything fetched is cached and will not "
              "be asked for again")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Resumable by design, so an interrupt is a pause and not a loss.
        print("\ninterrupted; re-run to carry on from here")
        sys.exit(130)
