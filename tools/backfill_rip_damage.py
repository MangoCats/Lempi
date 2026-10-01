#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record the damaged tracks of CD rips already in the library, from the rip
logs kept beside them `[SPEC-RIP-054]`.

Adding a rip records each track its log says failed, as an `ingest_decisions`
row (stage `rip`, outcome `verification_failed`). Until 2026-10-01 the log
reader did not know CUERipper's "cannot be verified as accurate", nor where an
image rip's read errors fell, so two damaged discs were added with nothing
recorded: seven tracks of one, one of the other. This reads each CD rip's log
again with the reader as it is now, and adds what is missing. A track already
recorded is left alone, so running it twice adds nothing.

Dry run unless `--commit`.

    python tools/backfill_rip_damage.py data/library.db [--commit]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cd_toc  # noqa: E402
from analysis_gaps import folder_of  # noqa: E402  -- Windows-real paths, on any machine
import lempi_db  # noqa: E402


def plan(conn) -> list[dict]:
    """What is missing: one entry per damaged track not yet recorded."""
    rips = conn.execute(
        "SELECT DISTINCT f.audio_md5, f.path FROM ingest_decisions d JOIN files f USING (audio_md5) "
        "WHERE d.stage = 'rip' ORDER BY f.file_id").fetchall()
    out = []
    for md5, path in rips:
        folder = folder_of(path)
        logs = sorted(n for n in os.listdir(folder) if n.lower().endswith(".log")) if os.path.isdir(folder) else []
        if not logs:
            out.append({"audio_md5": md5, "path": path, "track": None, "detail": "no log beside it"})
            continue
        have = {json.loads(d).get("track") for (d,) in conn.execute(
            "SELECT detail FROM ingest_decisions WHERE audio_md5 = ?1 AND stage = 'rip' "
            "AND outcome = 'verification_failed'", (md5,))}
        report = cd_toc.parse_eac_log(os.path.join(folder, logs[0]))
        for t in report.tracks:
            if not t.ok and t.number not in have:
                out.append({"audio_md5": md5, "path": path, "track": t.number, "detail": t.detail})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true", help="write; without it, only say what would be")
    args = ap.parse_args()
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=args.commit, timeout=60)
    todo = plan(conn)
    tracks = [t for t in todo if t["track"] is not None]
    for t in todo:
        album = os.path.basename(folder_of(t["path"]))
        what = f"track {t['track']:>2}: {t['detail']}" if t["track"] is not None else t["detail"]
        print(f"  {album}  {what}")
    if not args.commit:
        print(f"\n{len(tracks)} damaged track(s) to record -- dry run, nothing written; --commit to write")
        return 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with conn:
        for t in tracks:
            conn.execute(
                "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                "VALUES (?1,'rip','verification_failed',NULL,?2,?3)",
                (t["audio_md5"], json.dumps({"track": t["track"], "detail": t["detail"]}), now))
    print(f"\n{len(tracks)} damaged track(s) recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
