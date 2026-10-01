#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hold a passage back from the Program Director, or let it go
`[SPEC-HOLD-010]`.

A held radio passage is never chosen by the Director; a held album passage
is skipped, or replaced by an equal passage from another file, when a whole
album is played `[SPEC-HOLD-070]`. A person can still play either on purpose
-- its page, the queue -- and the album's file is never touched. The mark is
`passages.director_hold`, and it says why:

- **NULL** -- never decided. The Director may pick it.
- **text** -- held, and this is why: "damaged rip: ...", or a person's words.
- **empty** -- released by a person. The Director may pick it, and
  `--damaged` will not hold it again: a person's decision outranks a
  computed one `[SPEC-SA-080]`.

The column is added here when a library lacks it, and reaches a speaker with
its next catalogue sync (`star_patch.py` adds a column a node lacks).

    python tools/passage_hold.py data/library.db --list
    python tools/passage_hold.py data/library.db --hold 16905 --why "the vinyl rip is better"
    python tools/passage_hold.py data/library.db --release 16905
    python tools/passage_hold.py data/library.db --damaged [--commit]

`--damaged` holds both passages, radio and album, of every CD track whose rip
was recorded as failing verification (`ingest_decisions`, `[SPEC-RIP-054]`) and is not
yet decided; a dry run unless `--commit`. `--hold`/`--release` are a person's
own action and write at once. `--json` adds one summary line for a caller.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402

COLUMN = "director_hold"


def has_column(conn: sqlite3.Connection) -> bool:
    return any(r[1] == COLUMN for r in conn.execute("PRAGMA table_info(passages)"))


def ensure_column(conn: sqlite3.Connection) -> bool:
    """Add the column if this library lacks it. True if it was added."""
    if has_column(conn):
        return False
    conn.execute(f"ALTER TABLE passages ADD COLUMN {COLUMN} TEXT")
    return True


def damage_reason(detail: str) -> str:
    return f"damaged rip: {detail}"


def damaged(conn: sqlite3.Connection) -> list[dict]:
    """Both passages of every damaged CD track -- its radio passage and its
    album passage `[SPEC-HOLD-030]` -- not yet decided. A rip's tracks are its
    passages of each kind in order: the add step writes one of each per track."""
    have = has_column(conn)
    out = []
    rows = conn.execute(
        "SELECT f.file_id, f.path, d.detail FROM ingest_decisions d JOIN files f USING (audio_md5) "
        "WHERE d.stage = 'rip' AND d.outcome = 'verification_failed' ORDER BY f.file_id").fetchall()
    for file_id, path, detail in rows:
        d = json.loads(detail)
        n = d.get("track")
        # Its place among the file's passages: the track number for an image
        # rip, 1 for a tracks-mode rip's own file (`in_file`) [SPEC-CDI-090].
        at = d.get("in_file", n)
        for kind in ("radio", "album"):
            passages = conn.execute(
                f"SELECT passage_id, {COLUMN if have else 'NULL'} FROM passages "
                "WHERE file_id = ?1 AND kind = ?2 ORDER BY start_ms", (file_id, kind)).fetchall()
            if not isinstance(at, int) or not 1 <= at <= len(passages):
                out.append({"passage_id": None, "kind": kind, "path": path, "track": n,
                            "why": f"no {kind} passage for track {n} -- {len(passages)} in the file"})
                continue
            pid, mark = passages[at - 1]
            if mark is None:
                out.append({"passage_id": pid, "kind": kind, "path": path, "track": n,
                            "why": damage_reason(d.get("detail", ""))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    act = ap.add_mutually_exclusive_group(required=True)
    act.add_argument("--list", action="store_true", help="every passage held, and why")
    act.add_argument("--hold", type=int, metavar="PASSAGE", help="hold this passage back")
    act.add_argument("--release", type=int, metavar="PASSAGE", help="let this passage be picked again")
    act.add_argument("--damaged", action="store_true", help="hold both passages of every damaged CD track not yet decided")
    ap.add_argument("--why", default="held by hand", help="for --hold: the reason shown with it")
    ap.add_argument("--commit", action="store_true", help="for --damaged: write; without it, only say what")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    writes = args.hold is not None or args.release is not None or (args.damaged and args.commit)
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=writes, timeout=60)
    result: dict = {"ok": True}

    if args.list:
        rows = conn.execute(
            f"SELECT p.passage_id, p.{COLUMN}, f.path FROM passages p JOIN files f USING (file_id) "
            f"WHERE p.{COLUMN} IS NOT NULL AND p.{COLUMN} <> '' ORDER BY f.path, p.start_ms"
        ).fetchall() if has_column(conn) else []
        for pid, why, path in rows:
            print(f"  {pid}  {why}  ({path})")
        print(f"{len(rows)} passage(s) held" + ("" if has_column(conn) else
              " -- this library has no director_hold column yet"))
        result["held"] = len(rows)

    elif args.hold is not None or args.release is not None:
        pid = args.hold if args.hold is not None else args.release
        if conn.execute("SELECT 1 FROM passages WHERE passage_id = ?1", (pid,)).fetchone() is None:
            print(f"ERROR: no passage {pid}")
            if args.json:
                print(json.dumps({"ok": False, "error": f"no passage {pid}"}))
            return 1
        mark = (args.why.strip() or "held by hand") if args.hold is not None else ""
        with conn:
            added = ensure_column(conn)
            conn.execute(f"UPDATE passages SET {COLUMN} = ?1 WHERE passage_id = ?2", (mark, pid))
        if added:
            print("added passages.director_hold to this library")
        print(f"passage {pid}: " + (f"held -- {mark}" if mark else "released; the Director may pick it"))
        result.update(passage_id=pid, held=bool(mark), why=mark or None)

    else:
        todo = damaged(conn)
        for t in todo:
            print(f"  track {t['track']}  {t['kind']} passage {t['passage_id']}  {t['why']}  ({t['path']})")
        able = [t for t in todo if t["passage_id"] is not None]
        if args.commit:
            with conn:
                ensure_column(conn)
                for t in able:
                    conn.execute(f"UPDATE passages SET {COLUMN} = ?1 WHERE passage_id = ?2 "
                                 f"AND {COLUMN} IS NULL", (t["why"], t["passage_id"]))
            print(f"{len(able)} passage(s) of damaged tracks held")
        else:
            print(f"{len(able)} passage(s) of damaged tracks to hold -- dry run, nothing written; --commit to write")
        result["damaged"] = len(able)

    if args.json:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
