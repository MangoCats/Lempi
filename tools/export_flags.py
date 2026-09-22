#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Export flagged recordings and passages for a remote installation `[SPEC006 §10]`.

`[REQ-VIS-265]`'s checkbox is set on lempi02w's own play-history page --
exactly the machine with no Vipunen to act on it. This gets the name of a
flagged recording or passage off that installation and onto a portable form a
*different* installation's own library can resolve, the same way `export_changes.py`
already does for an applied decision rather than a mere identity.

This reads a **copy** of the peer's listener half, pulled down however you
like. (`[SPEC-DF-108]` justified that by lempi02w having no Python. It has
python3 3.11.2 -- `lempi-preflight` logs it at every boot `[PI-PRE-010]` --
so the copy is a convenience now, not a necessity; `remote_flags.py` gets the
same answer over one ssh round trip without copying anything.)

    scp pi@lempi02w:/var/lempi/listener.db /tmp/lempi02w-listener.db
    python tools/export_flags.py /tmp/lempi02w-listener.db -o flags.json

Read-only: nothing here writes to the database it reads from. The write half
is `tools/import_flags.py`, run against the *receiving* installation.
"""

import argparse
import json
import socket
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def export_flags(conn: sqlite3.Connection, hostname: str) -> list:
    """One portable record per flag `[SPEC-DF-107]`: a recording's own mbid
    needs no translation at all; a passage borrows the `(audio_md5, kind,
    start_ms, end_ms)` anchor `[SPEC-DF-103]` already resolves boundary edits
    against, via the same JOIN `apply_changes.py`'s `resolve_passage()` reads
    back out the other way.
    """
    have = lempi_db.tables(conn)  # both halves, not just `main` [IMPL-DBSPLIT-025]
    if "listener_flags" not in have:
        return []

    origin_expr = "f.origin" if has_column(conn, "listener_flags", "origin") else "NULL"
    out = []
    for subject_kind, subject_id, flagged_at, origin in conn.execute(
            f"SELECT f.subject_kind, f.subject_id, f.flagged_at, {origin_expr} "
            f"FROM listener_flags f ORDER BY f.flagged_at"):
        if subject_kind == "recording":
            anchor = {"recording_mbid": subject_id}
        else:
            row = conn.execute(
                """SELECT fi.audio_md5, p.kind, p.start_ms, p.end_ms
                     FROM passages p JOIN files fi ON fi.file_id = p.file_id
                    WHERE p.passage_id = ?1""", (int(subject_id),)).fetchone()
            if not row:
                # The passage this flag named no longer exists in this copy --
                # nothing to anchor it to, the same reasoning `[SPEC-DF-102]`
                # already applies to a review row missing its own baseline.
                continue
            anchor = {"audio_md5": row[0], "passage_kind": row[1],
                      "start_ms": row[2], "end_ms": row[3]}
        out.append({
            "subject_kind": subject_kind,
            "anchor": anchor,
            "flagged_at": flagged_at,
            "origin": origin or hostname,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    # Read-only, and it joins `listener_flags` to `recordings`/`passages`
    # -- both halves. Catalogue as `main`, which is the bulk of what it
    # reads and the half it could never accidentally write here.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY)
    flags = export_flags(conn, socket.gethostname())

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"format_version": 1, "flags": flags}, f, indent=2)

    say(f"{len(flags)} flag(s) exported to {args.out}")
    if not flags:
        say("nothing flagged there yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
