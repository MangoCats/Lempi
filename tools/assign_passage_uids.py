#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Give every passage in the hub's catalogue its permanent identity [IMPL-UID-052].

Step 3 of Stage A's window, run on the hub alone. No player computes a uid
for a passage that already exists. The next round's strict natural patch
carries these to every node, which keeps its own numbers [IMPL-UID-052].

In one transaction it:
- adds `passage_uid` and the birth key (`birth_md5`, `birth_kind`,
  `birth_start_ms`, `birth_end_ms`, `birth_salt`) to `passages`;
- gives each passage the uid of its span as it stands, with salt 0. The key
  is unique in one catalogue, as `passages_span` is [IMPL-UID-039];
- creates the unique index `passages_uid` and the table `passage_lineage`
  [SPEC-UID-065];
- creates two triggers [IMPL-UID-036]. One refuses a passage written without
  a uid; the other refuses changing a uid that is set.

From then on `passage_uid.insert_passage` is the only way a tool makes a
passage, and a tool that writes one by hand fails loudly, not silently.

Rehearses by default and writes nothing. `--commit` needs `--expect N`, the
passage count the rehearsal printed, and refuses a catalogue that already has
uids. It backs the catalogue up whole, through SQLite's backup API, into
`data/backups/pre-passage-uids-<UTC>/` first. The undo is that backup.

    python tools/assign_passage_uids.py data/library.db
    python tools/assign_passage_uids.py data/library.db --commit --expect 24443
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402
import passage_uid as pu  # noqa: E402
import staged  # noqa: E402  -- utf8_output

COLUMNS = (("passage_uid", "TEXT"), ("birth_md5", "TEXT"), ("birth_kind", "TEXT"),
           ("birth_start_ms", "INTEGER"), ("birth_end_ms", "INTEGER"), ("birth_salt", "INTEGER"))
SCHEMA = [
    "CREATE UNIQUE INDEX passages_uid ON passages(passage_uid)",
    "CREATE TABLE passage_lineage (old_uid TEXT NOT NULL, new_uid TEXT NOT NULL, why TEXT NOT NULL, "
    "stamp TEXT NOT NULL, PRIMARY KEY (old_uid, new_uid))",
    # [IMPL-UID-036]: no custom function, so these hold in the sqlite3 shell and
    # in every tool not yet written.
    "CREATE TRIGGER passages_uid_required BEFORE INSERT ON passages WHEN NEW.passage_uid IS NULL "
    "BEGIN SELECT RAISE(ABORT, 'a passage needs its passage_uid: make it with passage_uid.insert_passage "
    "[IMPL-UID-036]'); END",
    "CREATE TRIGGER passages_uid_fixed BEFORE UPDATE OF passage_uid ON passages "
    "WHEN OLD.passage_uid IS NOT NULL AND NEW.passage_uid IS NOT OLD.passage_uid "
    "BEGIN SELECT RAISE(ABORT, 'a passage_uid is never changed [IMPL-UID-036]'); END",
]


def say(*a):
    print(*a, flush=True)


def plan(conn) -> list[tuple]:
    """(passage_id, uid, md5, kind, start, end) for every passage; refuses what it cannot name."""
    rows = conn.execute("SELECT p.passage_id, f.audio_md5, p.kind, p.start_ms, p.end_ms FROM main.passages p "
                        "LEFT JOIN main.files f ON f.file_id = p.file_id ORDER BY p.passage_id").fetchall()
    orphans = [r[0] for r in rows if r[1] is None]
    if orphans:
        raise SystemExit(f"refused: {len(orphans)} passage(s) name no file, so have no birth key: {orphans[:10]}")
    out, seen = [], {}
    for pid, md5, kind, start, end in rows:
        uid = pu.passage_uid(md5, kind, start, end, 0)
        if uid in seen:
            raise SystemExit(f"refused: passages {seen[uid]} and {pid} give one uid {uid} [SPEC-UID-052]")
        seen[uid] = pid
        out.append((pid, uid, md5, kind, start, end))
    return out


def main(argv=None) -> int:
    staged.utf8_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("library", help="the hub's catalogue, e.g. data/library.db")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--expect", type=int)
    ap.add_argument("--backups", default=os.path.join(os.path.dirname(HERE), "data", "backups"))
    a = ap.parse_args(argv)
    conn = lempi_db.connect(a.library, lempi_db.ROLE_LIBRARY, writable=a.commit, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    if pu.has_uids(conn):
        raise SystemExit("refused: this catalogue's passages already carry uids; a uid is never recalculated")
    p = plan(conn)
    say(f"{len(p)} passage(s), {len(p)} distinct uid(s), each from its span as it stands, salt 0")
    for pid, uid, md5, kind, s, e in p[:3]:
        say(f"  passage {pid}: {uid} = ({md5}, {kind}, {s}, {e}, 0)")
    if not a.commit:
        conn.close()
        say(f"\nnothing was written. To assign them: --commit --expect {len(p)}")
        return 0
    if a.expect != len(p):
        raise SystemExit(f"refused: {len(p)} passage(s), expected {a.expect}; nothing was written")
    conn.close()
    import star_sync  # noqa: E402  -- its backup: the API, integrity-checked [SPEC-STAR-106]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = os.path.join(a.backups, f"pre-passage-uids-{stamp}")
    say("backing up the catalogue whole, through the backup API ...")
    star_sync.local_backup(a.library, os.path.join(folder, os.path.basename(a.library)))
    say(f"backed up to {folder}")
    conn = lempi_db.connect(a.library, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    try:
        conn.execute("BEGIN IMMEDIATE")
        if pu.has_uids(conn) or len(plan(conn)) != len(p):
            raise SystemExit("refused: the catalogue changed while backing up; nothing was written")
        for name, decl in COLUMNS:
            conn.execute(f"ALTER TABLE main.passages ADD COLUMN {name} {decl}")
        conn.executemany("UPDATE main.passages SET passage_uid = ?, birth_md5 = ?, birth_kind = ?, birth_start_ms = ?, "
                         "birth_end_ms = ?, birth_salt = 0 WHERE passage_id = ?",
                         [(uid, md5, kind, s, e, pid) for pid, uid, md5, kind, s, e in p])
        for sql in SCHEMA:
            conn.execute(sql)                  # CREATE lands in main, the catalogue: ROLE_LIBRARY
        missing = conn.execute("SELECT count(*) FROM main.passages WHERE passage_uid IS NULL").fetchone()[0]
        if missing:
            raise SystemExit(f"refused: {missing} passage(s) left without a uid; nothing was written")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    n = conn.execute("SELECT count(DISTINCT passage_uid) FROM main.passages").fetchone()[0]
    ok = conn.execute("PRAGMA main.quick_check").fetchone()[0]
    conn.close()
    say(f"assigned {n} uid(s); quick_check {ok}")
    return 0 if ok == "ok" and n == len(p) else 1


if __name__ == "__main__":
    sys.exit(main())
