#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drop the `id_checks` rows that name a passage the catalogue no longer holds.

An `id_checks` row is a fingerprint check's verdict on one passage. One that
outlives its passage says nothing about any music there is. It also stops
the star sync cold: the catalogue patch names a passage by what it is
[SPEC-NKP-030], a row naming none cannot be named, and so **every node's
whole catalogue patch is withheld** [SPEC-NKP-080].

Found 2026-11-14. Five checks of the *Wish You Were Here* cuts that the swap
of 2026-11-01 removed (passages 6454-6462) had withheld every catalogue
patch since the round of 2026-11-08: `none sent` on every node in three
SUMMARYs (LOG014 section 2, item 10). The link check cannot remove such a row,
since an `id_checks` row is not a link only [SPEC-LNK-030]. Dropping them
was the maintainer's decision that day.

Rehearses by default and writes nothing. `--commit --expect N` needs the
count the rehearsal printed, and refuses if what it finds then differs. It
backs the catalogue up first, whole, through SQLite's backup API, into
`data/backups/pre-dangling-checks-<UTC>/`, with every dropped row in
`dropped.json`. It then deletes in one transaction. `--restore DIR` puts
exactly those rows back (`row_drop.py`).

    python tools/drop_dangling_checks.py data/library.db
    python tools/drop_dangling_checks.py data/library.db --commit --expect 5
    python tools/drop_dangling_checks.py data/library.db --restore data/backups/pre-dangling-checks-<UTC>
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402
import row_drop  # noqa: E402  -- the backup, the record of what went, and putting it back
import staged  # noqa: E402  -- utf8_output: a title is not always cp1252

DANGLING = ("SELECT * FROM main.id_checks WHERE passage_id NOT IN (SELECT passage_id FROM main.passages) "
            "ORDER BY passage_id")


def say(*a):
    print(*a, flush=True)


def open_lib(library, writable):
    conn = lempi_db.connect(library, lempi_db.ROLE_LIBRARY, writable=writable, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    for t in ("id_checks", "passages"):
        if not conn.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = ?", (t,)).fetchone():
            raise SystemExit(f"{library} holds no {t}: not a catalogue")
    return conn


def find(conn) -> list[dict]:
    cur = conn.execute(DANGLING)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def show(conn, rows):
    say(f"id_checks naming no passage: {len(rows)}")
    for r in rows:
        t = conn.execute("SELECT title FROM main.recordings WHERE mbid = ?", (r.get("stored_mbid"),)).fetchone()
        say(f"  passage {r['passage_id']:>6}  {r.get('verdict')!s:<12} checked {r.get('checked_at')}  "
            f"of \"{t[0] if t and t[0] else r.get('stored_mbid')}\"")


def commit(library, expect, backups) -> int:
    conn = open_lib(library, writable=False)
    rows = find(conn)
    show(conn, rows)
    conn.close()
    if len(rows) != expect:
        raise SystemExit(f"refused: found {len(rows)}, expected {expect}; nothing was written")
    if not rows:
        say("nothing to drop")
        return 0
    say("backing up the catalogue whole, through the backup API ...")
    folder = row_drop.keep(library, backups, "pre-dangling-checks", "id_checks", rows)
    say(f"backed up to {folder}, with every row to be dropped in dropped.json")
    conn = open_lib(library, writable=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        again = find(conn)
        if [r["passage_id"] for r in again] != [r["passage_id"] for r in rows]:
            raise SystemExit("refused: the dangling checks changed while backing up; nothing was written")
        n = 0
        for r in rows:
            n += conn.execute("DELETE FROM main.id_checks WHERE passage_id = ? "
                              "AND passage_id NOT IN (SELECT passage_id FROM main.passages)",
                              (r["passage_id"],)).rowcount
        if n != len(rows):
            raise SystemExit(f"refused: {n} row(s) would go, not {len(rows)}; nothing was written")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    left = find(conn)
    ok = conn.execute("PRAGMA main.quick_check").fetchone()[0]
    conn.close()
    say(f"dropped {n} row(s); {len(left)} left naming no passage; quick_check {ok}")
    return 0 if ok == "ok" and not left else 1


def main(argv=None) -> int:
    staged.utf8_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("library", help="the catalogue half, e.g. data/library.db")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--expect", type=int)
    ap.add_argument("--backups", default=os.path.join(os.path.dirname(HERE), "data", "backups"))
    ap.add_argument("--restore", metavar="DIR")
    a = ap.parse_args(argv)
    if a.restore:
        conn = open_lib(a.library, writable=True)
        try:
            n = row_drop.put_back(conn, a.restore, "id_checks", ("passage_id",))
        finally:
            conn.close()
        say(f"restored {n} row(s) from {a.restore}")
        return 0
    if a.commit:
        if a.expect is None:
            raise SystemExit("--commit needs --expect N: the count a rehearsal printed")
        return commit(a.library, a.expect, a.backups)
    conn = open_lib(a.library, writable=False)
    rows = find(conn)
    show(conn, rows)
    conn.close()
    say(f"\nnothing was written. To drop them: --commit --expect {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
