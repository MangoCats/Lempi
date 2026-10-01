#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mark every recording of an album already in the library as Christmas, or
children's, music -- what the import page's boxes do for a disc as it is added
`[SPEC-CDI-096]`, for one added before they existed.

The album is its folder, by exact directory. Written as the import writes it
(`ingest_cd.mark_occasions`): both classes of the pair in `flavor`, source
`cd:import`, an equal mark left as it was. Dry run unless `--commit`.

    python tools/mark_occasion.py data/library.db --folder "C:/Music/Artist/Album" --christmas [--commit]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ingest_cd  # noqa: E402
import lempi_db  # noqa: E402


def recordings_in(conn, folder: str) -> list[str]:
    want = os.path.normpath(folder)
    rows = conn.execute(
        "SELECT DISTINCT f.path, pr.mbid FROM files f JOIN passages p USING (file_id) "
        "JOIN passage_recordings pr USING (passage_id) ORDER BY f.path, p.start_ms").fetchall()
    return list(dict.fromkeys(m for path, m in rows if os.path.normpath(os.path.dirname(path)) == want))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--folder", required=True, help="the album's folder, exactly")
    for occ in ingest_cd.OCCASIONS:
        ap.add_argument(f"--{occ}", dest="occasions", action="append_const", const=occ)
    ap.add_argument("--commit", action="store_true", help="write; without it, only say what")
    args = ap.parse_args()
    if not args.occasions:
        ap.error("name at least one occasion: " + ", ".join(f"--{o}" for o in ingest_cd.OCCASIONS))
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=args.commit, timeout=60)
    mbids = recordings_in(conn, args.folder)
    if not mbids:
        print(f"ERROR: no recordings in {args.folder!r} -- is that the album's folder, exactly?")
        return 1
    print(f"{len(mbids)} recording(s) in {args.folder}")
    if not args.commit:
        print(f"would mark them: {', '.join(args.occasions)} -- dry run, nothing written; --commit to write")
        return 0
    with conn:
        got = ingest_cd.mark_occasions(conn, mbids, args.occasions)
    print("marked: " + ", ".join(f"{o} {n}" for o, n in got.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
