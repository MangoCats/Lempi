#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Land flags exported from a remote installation `[SPEC006 §10]`.

Reads a `flags.json` from `tools/export_flags.py` and, for each record,
resolves its portable anchor against *this* installation's own library --
a recording directly by its mbid, a passage via `apply_changes.py`'s own
`resolve_passage()`, the identical anchor `[SPEC-DF-103]` already uses for a
boundary edit. What resolves is flagged here exactly as `[REQ-VIS-265]`'s
checkbox would have flagged it locally; what does not is reported, not
silently dropped -- "overlapping" libraries are not identical by
definition, and a flag Vipunen's own `/flags` page could not resolve would be
the blank rendering `[REQ-LIB-190]` was built to avoid.

Idempotent: a flag already present here, by the same resolved subject, is
skipped -- re-running this after a second pull is harmless.

Rehearse by default, like every other tool here:

    python tools/import_flags.py <this installation's db> flags.json
    python tools/import_flags.py <this installation's db> flags.json --commit
"""

import argparse
import json
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

from apply_changes import resolve_passage


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def ensure_flags_table(conn: sqlite3.Connection) -> None:
    """The identical schema `player/src/db.rs`'s `FLAGS_TABLE` creates on
    every Lempi start `[SPEC-DF-107]` -- needed here too, since this writes
    directly to the SQLite path and cannot assume any particular Lempi has
    ever opened this file at all. A desktop that has never had a listener
    flag anything locally, and is only ever pulled *into*, is exactly the
    case where the table is missing outright, not merely missing a column
    -- `CREATE TABLE IF NOT EXISTS` covers that; `ALTER TABLE ADD COLUMN`
    still runs after, to bring an older table (built before `origin` was
    added) up to date, the same two-step `ensure_flags_columns` documents
    on the Rust side.

    Previously this ran only the `ALTER TABLE`, wrapped in a blanket
    `except OperationalError: pass` that silently treated "no such table:
    listener_flags" the same as "already has the column" -- so a pull
    against a library missing the table outright looked like it was
    landing flags right up until the very next `SELECT` against it failed
    for real.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS listener_flags ("
        "subject_kind TEXT NOT NULL CHECK (subject_kind IN ('recording','passage')), "
        "subject_id TEXT NOT NULL, flagged_at TEXT NOT NULL, origin TEXT, "
        "PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID")
    try:
        conn.execute("ALTER TABLE listener_flags ADD COLUMN origin TEXT")
    except sqlite3.OperationalError:
        pass  # already has it


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("flags", help="flags.json from export_flags.py")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--json", action="store_true",
                     help="also print one final JSON summary line, for a caller "
                          "(the Vipunen console's remote-pull job) rather than a person")
    args = ap.parse_args()

    with open(args.flags, encoding="utf-8") as f:
        doc = json.load(f)
    flags = doc.get("flags", [])

    # Writes `listener_flags`, and reads the catalogue to check a flag
    # names something real -- so the listener half is `main` and the
    # catalogue rides along read-only.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LISTENER, writable=True)
    conn.execute("PRAGMA busy_timeout = 60000")
    ensure_flags_table(conn)
    conn.commit()

    say(f"{len(flags)} flag(s) in {args.flags}")
    matched = already = unmatched = 0
    if args.commit:
        conn.execute("BEGIN IMMEDIATE")

    for entry in flags:
        kind = entry["subject_kind"]
        anchor = entry["anchor"]

        if kind == "recording":
            subject_id = anchor["recording_mbid"]
            known = conn.execute(
                "SELECT 1 FROM recordings WHERE mbid=?1", (subject_id,)).fetchone()
            if not known:
                say(f"not present here: recording {subject_id}")
                unmatched += 1
                continue
        else:
            passage_id = resolve_passage(conn, anchor)
            if passage_id is None:
                say(f"not present here: passage at {anchor['audio_md5'][:12]}… "
                    f"({anchor['start_ms']}-{anchor['end_ms']})")
                unmatched += 1
                continue
            subject_id = str(passage_id)

        exists = conn.execute(
            "SELECT 1 FROM listener_flags WHERE subject_kind=?1 AND subject_id=?2",
            (kind, subject_id)).fetchone()
        if exists:
            already += 1
            continue

        matched += 1
        say(f"flagging {kind} {subject_id} "
            f"(flagged {entry['flagged_at']} on {entry['origin']})")
        if args.commit:
            conn.execute(
                "INSERT INTO listener_flags (subject_kind, subject_id, flagged_at, origin) "
                "VALUES (?1, ?2, ?3, ?4)",
                (kind, subject_id, entry["flagged_at"], entry["origin"]))

    say(f"\n{matched} new flag(s), {already} already flagged here, "
        f"{unmatched} not present here")
    if args.commit:
        conn.commit()
        say("committed" if matched else "nothing to write")
    else:
        say("nothing was written. Re-run with --commit to do it.")
    if args.json:
        say(json.dumps({"matched": matched, "already": already, "unmatched": unmatched}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
