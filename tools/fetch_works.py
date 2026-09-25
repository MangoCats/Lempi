#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 2 of [GDE-WRK-125]: fetch MusicBrainz **Work** relations per recording.

A MusicBrainz *Recording* is one rendering; a *Work* is the song. Lempi blocks
rotation on the recording MBID, so a remaster, a 5.1 mix, an acoustic take and
a live version are four unrelated recordings and none of them suppresses the
others `[GDE-WRK-010]`. The Work is the entity that says they are the same
song, and `recording_relations` is where that belongs `[SPEC-DIR-116]`.

This tool only **fetches and caches**. It writes no relation rows: deriving
them is a separate step with policy in it -- covers, live takes, and the
same-artist restriction `[GDE-WRK-050]` -- and re-deriving from a local cache
is instant where re-fetching is hours `[GDE-WRK-110]`.

Being a good citizen of a free service is most of the design, exactly as in
`fetch_releases.py`:

  * one request per second, which is MusicBrainz's published limit;
  * a real User-Agent with a contact address, which they ask for and enforce;
  * resumable, so an interrupted run costs only what it had not yet fetched;
  * every response cached, so a re-run after a policy change asks nothing twice.

**The cache is its own file, not `musicbrainz_cache`.** This runs for hours
unattended against a catalogue other tools are using; a long-lived writer on
the 1.17 GB library is an avoidable risk, and folding these responses into
`musicbrainz_cache` is a fast, reviewable step once the crawl is done
`[GDE-WRK-115]`.

    python tools/fetch_works.py data/library.db [--cache data/work_relations.db]
                               [--limit N] [--seed other.db] [--refresh]
                               [--give-up-after N]
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

# They ask for a contact address and will rate-limit or block a generic agent.
UA = "Lempi-Vipunen/0.1 ( https://github.com/MangoCats/Lempi )"
BASE = "https://musicbrainz.org/ws/2/recording/"
INC = "work-rels+artist-credits"
RATE_S = 1.0

CACHE_DDL = """
CREATE TABLE IF NOT EXISTS work_cache (
    mbid       TEXT PRIMARY KEY,
    response   TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
) WITHOUT ROWID;"""


def targets(conn) -> list[str]:
    """Every recording reachable from a radio passage, real MBIDs only.

    `local:audio:...` ids are synthetic stand-ins for unidentified segments
    `[GDE-WRK-015]`; MusicBrainz has nothing to say about them.
    """
    rows = conn.execute(
        "SELECT DISTINCT pr.mbid FROM passage_recordings pr "
        "JOIN passages p ON p.passage_id = pr.passage_id AND p.kind = 'radio' "
        "WHERE pr.mbid NOT LIKE 'local:%' ORDER BY pr.mbid"
    ).fetchall()
    return [r[0] for r in rows]


# What this tool once cached when a request never got an answer. Such a row is
# not an answer about the recording, so it is fetched again rather than
# counted as done.
UNREACHABLE = json.dumps({"error": "unreachable"})


def fetch(mbid: str) -> str | None:
    """One recording, with backoff. A 404 is an answer, not a failure; no
    answer at all is `None`, and is not cached.

    It used to be cached, as `UNREACHABLE` -- so a run with the network down
    marked every recording it tried as done, and nothing asked again without
    `--refresh`. Unattended in `induct`, that would have been permanent.
    """
    url = f"{BASE}{mbid}?inc={INC}&fmt=json"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=40).read().decode()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return json.dumps({"error": "404"})
            time.sleep(2 ** attempt * 2)
        except Exception:
            time.sleep(2 ** attempt * 2)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db", help="library half, e.g. data/library.db")
    ap.add_argument("--cache", default="data/work_relations.db")
    ap.add_argument("--limit", type=int, help="stop after N fetches")
    ap.add_argument("--seed", help="copy already-fetched rows from another cache first")
    ap.add_argument("--refresh", action="store_true", help="re-ask for everything")
    ap.add_argument("--give-up-after", type=int, metavar="N",
                    help="stop after N recordings in a row get no answer (network down)")
    args = ap.parse_args()

    lib = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY)
    want = targets(lib)
    lib.close()     # read once; not held open against the catalogue for hours

    cache = sqlite3.connect(args.cache)
    cache.executescript(CACHE_DDL)
    cache.commit()

    if args.seed:
        cache.execute("ATTACH DATABASE ? AS seed", (args.seed,))
        names = {r[0] for r in cache.execute(
            "SELECT name FROM seed.sqlite_master WHERE type='table'")}
        table = "work_cache" if "work_cache" in names else "wcache"
        n = cache.execute(
            f"INSERT OR IGNORE INTO work_cache (mbid, response, fetched_at) "
            f"SELECT mbid, response, fetched_at FROM seed.{table}").rowcount
        cache.commit()
        cache.execute("DETACH DATABASE seed")
        print(f"seeded {n} cached response(s) from {args.seed}", flush=True)

    have = set() if args.refresh else {
        r[0] for r in cache.execute(
            "SELECT mbid FROM work_cache WHERE response != ?", (UNREACHABLE,))}
    todo = [m for m in want if m not in have]
    # Counted before --limit truncates it, or a capped run reports the whole
    # library as cached and the next reader believes the crawl is finished.
    outstanding = len(todo)
    if args.limit:
        todo = todo[: args.limit]

    print(f"{len(want)} recording(s) reachable from radio passages, "
          f"{len(want) - outstanding} already cached, {outstanding} outstanding, "
          f"{len(todo)} to fetch this run "
          f"-- at least {len(todo) * RATE_S / 3600:.1f} h at {RATE_S:.0f} req/s",
          flush=True)

    unanswered, in_a_row, gave_up = 0, 0, False
    for i, mbid in enumerate(todo, 1):
        response = fetch(mbid)
        if response is None:
            unanswered += 1
            in_a_row += 1
            if args.give_up_after and in_a_row >= args.give_up_after:
                gave_up = True
                break
        else:
            in_a_row = 0
            cache.execute("INSERT OR REPLACE INTO work_cache VALUES (?,?,?)",
                          (mbid, response, int(time.time())))
            cache.commit()      # committed per row: an interrupt costs one request
        if i % 100 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(RATE_S)

    total = cache.execute("SELECT COUNT(*) FROM work_cache").fetchone()[0]
    if unanswered:
        # Said, not buried: these recordings have no Work yet, and the run
        # still exits 0 because what it did fetch is good `[GDE-WRK-130]`.
        print(f"NOT ANSWERED: {unanswered} recording(s) got no response"
              f"{f'; gave up after {in_a_row} in a row -- is the network up?' if gave_up else ''}"
              f" -- left uncached, so the next run asks again", flush=True)
    print(f"done -- {total} response(s) cached in {args.cache}", flush=True)
    cache.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
