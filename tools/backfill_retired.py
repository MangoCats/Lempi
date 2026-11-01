#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hub's word on the files merges retired before it kept one `[SPEC-RTF-015]`.

From 2026-11-01 a merge records each file it retires, in its own transaction
(`ingest_cd.record_retired`). *Highway 61* (2026-10-30) and *Wish You Were
Here* (2026-11-01) were merged before that: their `merged` decisions name
where each file went, and the files' rows are gone. They are read back from
a copy of the library taken before each merge -- `--before`, as many as
there are -- matched by album folder and name, and recorded as a merge would
have recorded them, dated by the merge.

    python tools/backfill_retired.py data/library.db \\
        --before data/backups/before-highway61-merge-2026-10-30/library.db \\
        --before data/backups/before-wywh-swap-2026-11-01/library.db [--commit]

A file already recorded is left alone, so a second run writes nothing. A
retired file that no `--before` copy names is said, and not invented.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest_cd   # noqa: E402
import lempi_db    # noqa: E402
from ingest_folder import AUDIO  # noqa: E402


def say(text: str) -> None:
    ingest_cd.say(text)


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def earlier_rows(before: list[str]) -> dict[str, list[tuple]]:
    """`{album folder: [(name, audio_md5, path, sha256, size_bytes)]}` across every copy."""
    out: dict[str, list[tuple]] = {}
    for p in before:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            have = {r[1] for r in c.execute("PRAGMA table_info(files)")}
            cols = ", ".join(x if x in have else "NULL" for x in ("sha256", "size_bytes"))
            for md5, path, sha, size in c.execute(f"SELECT audio_md5, path, {cols} FROM files"):
                if path:
                    out.setdefault(_key(os.path.dirname(path)), []).append(
                        (os.path.basename(path.replace("\\", "/")), md5, path, sha, size))
        finally:
            c.close()
    return out


def plan(conn, before: list[str]) -> tuple[list[dict], list[str]]:
    """What a merge would have recorded, for each retired audio file not yet
    recorded; and what could not be found."""
    earlier = earlier_rows(before)
    done = set(ingest_cd.retired_files(conn))
    todo, lost = [], []
    for did, dao, detail, at in conn.execute(
            "SELECT decision_id, audio_md5, detail, decided_at FROM ingest_decisions "
            "WHERE stage = 'rip' AND outcome = 'merged' ORDER BY decision_id").fetchall():
        d = json.loads(detail or "{}")
        row = conn.execute("SELECT path FROM files WHERE audio_md5 = ?1", (dao,)).fetchone()
        if not row:
            lost.append(f"merge {did}: its disc {dao} is no longer catalogued")
            continue
        into = os.path.dirname(row[0])
        root = os.path.dirname(os.path.dirname(os.path.abspath(into)))
        there = {name.lower(): r for r in earlier.get(_key(into), []) for name in (r[0],)}
        for dst in d.get("retired") or []:
            name = os.path.basename(dst)
            if not name.lower().endswith(AUDIO):
                continue        # a sheet or log that went with them
            hit = there.get(name.lower())
            if hit is None:
                lost.append(f"merge {did}: {name} is in no --before copy of {into}")
                continue
            _n, md5, path, sha, size = hit
            if md5 in done:
                continue
            todo.append({"audio_md5": md5, "at": at, "detail": {
                "sha256": sha, "size_bytes": size, "rel_path": os.path.relpath(path, root).replace(os.sep, "/"),
                "retired_to": dst, "replaced_by": dao, "why": "merge", "backfilled": True}})
    return todo, lost


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("--before", action="append", required=True, help="a copy of the library from before a merge")
    ap.add_argument("--commit", action="store_true", help="write; without it, say what it would")
    args = ap.parse_args()
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=args.commit, timeout=60)
    todo, lost = plan(conn, args.before)
    for t in todo:
        say(f"  {t['audio_md5']}  {t['detail']['rel_path']}  (retired {t['at'][:10]}, replaced by "
            f"{t['detail']['replaced_by'][:8]})")
    for line in lost:
        say(f"  NOT FOUND -- {line}")
    if args.commit and todo:
        conn.execute("BEGIN IMMEDIATE")
        for t in todo:
            conn.execute("INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                         "VALUES (?1,?2,'retired',1.0,?3,?4)",
                         (t["audio_md5"], ingest_cd.RETIRE_STAGE, json.dumps(t["detail"], sort_keys=True), t["at"]))
        conn.commit()
    say(f"{len(todo)} retired file(s) {'recorded' if args.commit else 'to record -- --commit to write'}"
        + (f"; {len(lost)} not found" if lost else ""))
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main())
