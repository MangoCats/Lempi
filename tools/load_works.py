#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 4 of [GDE-WRK-125]: fill `works` and `recording_works` from the crawl.

`fetch_works.py` caches MusicBrainz responses and writes nothing to the
catalogue, deliberately `[GDE-WRK-110]`. This is the other half: read that
cache, and record which Works each recording performs.

**Identity, not relation.** No `recording_relations` rows are written and no
pairwise closure is computed. The Director blocks on a shared work MBID the
same way it already blocks on a shared recording MBID `[GDE-WRK-037]`, so the
only thing the catalogue has to carry is the membership.

Every `performance` relation is kept, whatever its attributes -- `live`,
`cover`, `partial`, `instrumental`, `medley` are all recorded and none is
read `[GDE-WRK-052]`. A recording performing several works gets a row for each
`[GDE-WRK-070]`.

    python tools/load_works.py data/library.db [--cache data/work_relations.db]
                              [--dry-run]
"""

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

SOURCE = "musicbrainz:work-rels"

DDL = """
CREATE TABLE IF NOT EXISTS works (
    mbid   TEXT PRIMARY KEY,
    title  TEXT,
    source TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS recording_works (
    mbid       TEXT NOT NULL,
    work_mbid  TEXT NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (mbid, work_mbid)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS recording_works_by_work ON recording_works(work_mbid);"""


def read_cache(path: str):
    """`{recording mbid: [(work mbid, title), ...]}` from the crawl's cache."""
    cc = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out, errors = {}, 0
    for mbid, body in cc.execute("SELECT mbid, response FROM work_cache"):
        d = json.loads(body)
        if "error" in d:
            errors += 1
            continue
        seen = {}
        for rel in d.get("relations", []):
            w = rel.get("work")
            if w and w.get("id"):
                seen[w["id"]] = w.get("title")
        if seen:
            out[mbid] = sorted(seen.items())
    cc.close()
    return out, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db", help="library half, e.g. data/library.db")
    ap.add_argument("--cache", default="data/work_relations.db")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    works, errors = read_cache(args.cache)
    print(f"cache: {len(works)} recording(s) with at least one Work, {errors} errored")

    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=not args.dry_run)

    # Only recordings the catalogue actually knows: the cache may be ahead of
    # it, and a row pointing at an absent recording is a lie the Director would
    # load and never resolve.
    known = {r[0] for r in conn.execute("SELECT mbid FROM recordings")}
    rows, wrows, skipped = [], {}, 0
    for mbid, ws in works.items():
        if mbid not in known:
            skipped += 1
            continue
        for wmbid, title in ws:
            rows.append((mbid, wmbid, SOURCE))
            wrows[wmbid] = title
    print(f"to write: {len(wrows)} work(s), {len(rows)} recording_works row(s)"
          f"{f', {skipped} recording(s) not in the catalogue' if skipped else ''}")

    multi = sum(1 for mbid, ws in works.items() if mbid in known and len(ws) > 1)
    print(f"  recordings performing more than one work: {multi}")

    if args.dry_run:
        print("dry run -- nothing written")
        return 0

    conn.executescript(DDL)
    conn.executemany("INSERT OR REPLACE INTO works (mbid, title, source) VALUES (?,?,?)",
                     [(w, t, SOURCE) for w, t in wrows.items()])
    conn.executemany(
        "INSERT OR REPLACE INTO recording_works (mbid, work_mbid, source) VALUES (?,?,?)",
        rows)
    conn.commit()

    n_w = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    n_rw = conn.execute("SELECT COUNT(*) FROM recording_works").fetchone()[0]
    print(f"written -- works {n_w}, recording_works {n_rw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
