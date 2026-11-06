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

And it takes back what the reader no longer calls damage: a track recorded only
for read errors that AccurateRip matched `[SPEC-RIP-120]`, its row removed and
its hold released. Only where the track's own log still says it matched, and
only a hold that is still that row's own words: a person's stays.

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
import passage_hold  # noqa: E402


def _report_for(path: str):
    """A file's own log, read: `(report, None)`, or `(None, what is wrong)`. A
    folder can hold another disc's log and none of this one's `[SPEC-RIP-121]`."""
    folder = folder_of(path)
    names = os.listdir(folder) if os.path.isdir(folder) else []
    logs = [n for n in names if n.lower().endswith(".log")]
    if not logs:
        return None, "no log beside it"
    log = cd_toc.choose_log(names, os.path.basename(path))
    if log is None:
        return None, f"none of the {len(logs)} log(s) beside it is its own"
    return cd_toc.parse_eac_log(os.path.join(folder, log)), None


def plan(conn) -> list[dict]:
    """What is missing: one entry per damaged track not yet recorded."""
    rips = conn.execute(
        "SELECT DISTINCT f.audio_md5, f.path FROM ingest_decisions d JOIN files f USING (audio_md5) "
        "WHERE d.stage = 'rip' ORDER BY f.file_id").fetchall()
    out = []
    for md5, path in rips:
        report, why = _report_for(path)
        if report is None:
            out.append({"audio_md5": md5, "path": path, "track": None, "detail": why})
            continue
        have = {json.loads(d).get("track") for (d,) in conn.execute(
            "SELECT detail FROM ingest_decisions WHERE audio_md5 = ?1 AND stage = 'rip' "
            "AND outcome = 'verification_failed'", (md5,))}
        # A tracks-mode rip's files share one log, each file one track
        # [SPEC-CDI-090]: a file takes only its own track's verdict from it.
        own = None
        for (d,) in conn.execute(
                "SELECT detail FROM ingest_decisions WHERE audio_md5 = ?1 AND stage = 'rip' "
                "AND outcome NOT IN ('verification_failed', 'track_missing')", (md5,)):
            dd = json.loads(d or "{}")
            if dd.get("tracks_mode"):
                own = dd.get("track")
        for t in report.tracks:
            if own is not None and t.number != own:
                continue
            if not t.ok and t.number not in have:
                out.append({"audio_md5": md5, "path": path, "track": t.number, "detail": t.detail,
                            "in_file": 1 if own is not None else None})
    return out


def forgiven(conn) -> list[dict]:
    """Recorded damage the rule no longer calls damage: read errors, and a match
    `[SPEC-RIP-120]`. Each with the words its hold was given."""
    out = []
    for decision, file_id, path, detail in conn.execute(
            "SELECT d.decision_id, f.file_id, f.path, d.detail FROM ingest_decisions d JOIN files f USING (audio_md5) "
            "WHERE d.stage = 'rip' AND d.outcome = 'verification_failed' ORDER BY d.decision_id").fetchall():
        d = json.loads(detail or "{}")
        words = d.get("detail") or ""
        if not (words.startswith("read errors at ") and words.lower().endswith("; accurately ripped")):
            continue
        report, _why = _report_for(path)
        now = next((t for t in (report.tracks if report else []) if t.number == d.get("track")), None)
        if now is not None and now.ok:
            out.append({"decision_id": decision, "file_id": file_id, "path": path, "track": d.get("track"),
                        "detail": words, "why": passage_hold.damage_reason(words)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true", help="write; without it, only say what would be")
    args = ap.parse_args()
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=args.commit, timeout=60)
    todo = plan(conn)
    tracks = [t for t in todo if t["track"] is not None]
    back = forgiven(conn)
    for t in todo:
        album = os.path.basename(folder_of(t["path"]))
        what = f"track {t['track']:>2}: {t['detail']}" if t["track"] is not None else t["detail"]
        print(f"  {album}  {what}")
    for t in back:
        print(f"  {os.path.basename(folder_of(t['path']))}  track {t['track']:>2}: no longer damage: {t['detail']}")
    if not args.commit:
        print(f"\n{len(tracks)} damaged track(s) to record, {len(back)} to take back "
              "-- dry run, nothing written; --commit to write")
        return 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    released = 0
    with conn:
        for t in tracks:
            conn.execute(
                "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                "VALUES (?1,'rip','verification_failed',NULL,?2,?3)",
                (t["audio_md5"], json.dumps({"track": t["track"], "detail": t["detail"],
                                             **({"in_file": t["in_file"]} if t.get("in_file") else {})}), now))
        for t in back:
            conn.execute("DELETE FROM ingest_decisions WHERE decision_id = ?1", (t["decision_id"],))
            # Empty, not NULL: released is a decision, and a bundle carries a
            # decision to a speaker where it would leave undecided alone
            # [SPEC-HOLD-080]. The maintainer's, 2026-11-05.
            if passage_hold.has_column(conn):
                released += conn.execute(
                    f"UPDATE passages SET {passage_hold.COLUMN} = '' WHERE file_id = ?1 AND {passage_hold.COLUMN} = ?2",
                    (t["file_id"], t["why"])).rowcount
    print(f"\n{len(tracks)} damaged track(s) recorded; {len(back)} taken back, {released} passage(s) released")
    return 0


if __name__ == "__main__":
    sys.exit(main())
