#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fill `releases.artist_credit` for the releases Browse names albums after
`[REQ-VIS-183]`.

Browse showed an album under its first passage's artist, because `releases`
held no credit of its own: Woodstock, a two-disc soundtrack of thirty-odd
acts, read "John Sebastian", 2026-10-14. A CD add and a release fetch now
keep the credit MusicBrainz sends; this asks for it of the releases already
in the library that lack one.

Not all 62,000 rows -- most are the other editions of a recording, kept to
choose among and never shown. Only those an album can be named after: each
file's own release `[SPEC-SC-125]`, and for each recording the library
plays, the release Browse would take, in Browse's own order (chosen first,
then the earliest). 984 on the hub, 2026-10-14, one request a second: a quarter
of an hour, resumable, a credit already there never replaced.

    python tools/fetch_release_credits.py data/library.db [--limit N] [--dry-run]
"""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_releases  # noqa: E402
import lempi_db        # noqa: E402

# The releases an album can be named after, lacking a credit.
WANTED = """
WITH shown AS (
    SELECT fr.release_mbid AS mbid FROM file_releases fr
    UNION
    SELECT (SELECT rr.release_mbid FROM release_recordings rr JOIN releases rel ON rel.mbid = rr.release_mbid
             WHERE rr.mbid = held.mbid ORDER BY rr.chosen DESC, rel.release_date, rel.title LIMIT 1)
      FROM (SELECT DISTINCT mbid FROM passage_recordings) held
)
SELECT rel.mbid FROM releases rel JOIN shown ON shown.mbid = rel.mbid
 WHERE rel.artist_credit IS NULL ORDER BY rel.mbid
"""


def wanted(conn) -> list[str]:
    if not lempi_db.tables(conn) >= {"file_releases", "releases", "release_recordings"}:
        return []
    return [r[0] for r in conn.execute(WANTED) if r[0] and fetch_releases.MBID.match(r[0])]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=None, help="ask about at most N releases")
    ap.add_argument("--dry-run", action="store_true", help="count what would be asked, ask nothing")
    args = ap.parse_args()

    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=not args.dry_run, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    if not args.dry_run:
        try:
            conn.execute("ALTER TABLE releases ADD COLUMN artist_credit TEXT")
            print("added releases.artist_credit")
        except sqlite3.OperationalError:
            pass    # there already
    elif not any(c[1] == "artist_credit" for c in conn.execute("PRAGMA table_info(releases)")):
        print("releases has no artist_credit column yet; a real run adds it -- nothing to count against")
        return 0
    todo = wanted(conn)
    print(f"{len(todo)} release(s) an album can be named after have no credit"
          + (f"; asking about {min(len(todo), args.limit)}" if args.limit is not None else ""))
    if args.dry_run:
        return 0
    todo = todo[:args.limit] if args.limit is not None else todo
    filled = gone = empty = 0
    try:
        for i, mbid in enumerate(todo, 1):
            doc = fetch_releases.get(f"{fetch_releases.BASE}/{mbid}?inc=artist-credits&fmt=json")
            credit = fetch_releases.credit_of(doc) if doc else None
            if doc is None:
                gone += 1
            elif credit is None:
                empty += 1
            else:
                conn.execute("UPDATE releases SET artist_credit = ?1 WHERE mbid = ?2 AND artist_credit IS NULL",
                             (credit, mbid))
                filled += 1
            if i % 25 == 0 or i == len(todo):
                conn.commit()
                print(f"  {i}/{len(todo)}: {filled} filled, {empty} with no credit, {gone} gone", flush=True)
            time.sleep(fetch_releases.RATE_S)
    except KeyboardInterrupt:
        print("stopped; what was asked is kept, and a re-run takes up the rest")
    finally:
        conn.commit()
        conn.close()
    print(f"done: {filled} filled, {empty} with no credit, {gone} no longer on MusicBrainz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
