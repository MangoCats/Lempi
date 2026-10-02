#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record the release of CD rips already in the library `[SPEC-CDI-098]`.

Adding a rip identifies the exact edition -- by its Disc ID, its barcode, or a
person's pick -- and records which in `ingest_decisions` (stage `rip`,
`detail.chosen`). Until 2026-10-01 that was all it did: no `releases` row, no
`release_recordings` link, so Browse by Album, which names an album by its
release, had nothing for 162 of the 169 radio passages added that week.

This reads each rip's chosen edition, fetches it once from MusicBrainz (one
request a second, as `fetch_releases.py` does), and links it as each
recording's chosen release, through the same `record_release()` an add now
calls. A rip whose recordings are all linked already is not fetched, so
running it twice asks nothing.

Dry run unless `--commit`.

    python tools/backfill_cd_releases.py data/library.db [--commit]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_releases  # noqa: E402  -- get(), RATE_S
import ingest_cd  # noqa: E402  -- record_release(), MB_WS
import lempi_db  # noqa: E402


def chosen_editions(conn) -> dict[str, set[str]]:
    """Each chosen edition, with the recordings of its rips' files that are
    not yet linked to it as chosen -- the work left."""
    out: dict[str, set[str]] = {}
    for md5, detail in conn.execute("SELECT audio_md5, detail FROM ingest_decisions WHERE stage = 'rip'"):
        try:
            rid = (json.loads(detail or "{}") or {}).get("chosen")
        except ValueError:
            continue
        if not rid:
            continue
        for (mbid,) in conn.execute(
                "SELECT DISTINCT pr.mbid FROM files f JOIN passages p ON p.file_id = f.file_id "
                "JOIN passage_recordings pr ON pr.passage_id = p.passage_id "
                "WHERE f.audio_md5 = ?1 AND pr.mbid NOT LIKE 'local:%'", (md5,)):
            out.setdefault(rid, set()).add(mbid)
    linked = set()
    try:
        # Linked at all, chosen or not: a recording on two discs is chosen
        # for one of them only, and is done for both.
        linked = {(r, m) for r, m in conn.execute(
            "SELECT release_mbid, mbid FROM release_recordings WHERE source = 'cd:import'")}
    except Exception:
        pass    # no table, or no chosen column yet: nothing linked
    return {rid: {m for m in mbids if (rid, m) not in linked} for rid, mbids in out.items()
            if any((rid, m) not in linked for m in mbids)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true", help="write; without it, only say what")
    args = ap.parse_args()
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=args.commit)
    todo = chosen_editions(conn)
    if not todo:
        print("every CD rip's recordings are linked to their release already")
        return 0
    total = failed = 0
    for i, (rid, mbids) in enumerate(sorted(todo.items())):
        if i:
            time.sleep(fetch_releases.RATE_S)
        release = fetch_releases.get(f"{ingest_cd.MB_WS}/release/{rid}?fmt=json&inc=recordings+release-groups")
        if not release:
            print(f"  NOT FOUND  {rid} -- MusicBrainz has no such release now; {len(mbids)} recording(s) left unlinked")
            failed += 1
            continue
        tracks = [(t.get("position"), (t.get("recording") or {}).get("id"))
                  for m in release.get("media") or [] for t in m.get("tracks") or []]
        mine = [(p, m) for p, m in tracks if m in mbids]
        # Counted the way record_release() links, so the dry run's number is
        # the commit's; and only a commit calls it, since its column checks
        # are DDL, which sqlite3 does not roll back.
        n = (ingest_cd.record_release(conn, release, mine) if args.commit else
             sum(1 for p, m in mine
                 if ((ingest_cd._track_at_position(release, p) or {}).get("recording") or {}).get("id") == m))
        total += n
        print(f"  {release.get('title')} ({(release.get('date') or '?')[:4]})  {n} of {len(mbids)} recording(s)"
              + ("" if n == len(mbids) else "  -- the rest are not on this release at their place"))
    if args.commit:
        conn.commit()
        print(f"\nlinked {total} recording(s) to their release" + (f"; {failed} release(s) not found" if failed else ""))
    else:
        print(f"\nwould link {total} recording(s). Nothing written; re-run with --commit.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
