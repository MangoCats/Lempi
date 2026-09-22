#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Automatic catalog diff between two Vipunen-capable installations `[SPEC035]`.

`[SPEC-MESH-030]`: key-and-fingerprint, not a database copy -- the same order
of cost `[SPEC-DF-119]` measured for `listener_flags`, generalized from one
small table to the catalog tables that actually carry growth. One remote
`SELECT` per table (`remote_peek.py`'s `run_remote_sql()`, reused, never a
second ssh/sqlite3 implementation), one local `SELECT`, compared in memory.
Never touches `lowlevel_cache`, `musicbrainz_cache` or `identification_cache`
-- `[SPEC-SUI-090]` already found no reason to move them, and they are most
of a library's bytes.

    python tools/mesh_diff.py data/library.db pi@bose:/srv/library/library.db -o diff.json

This is read-only against both sides `[SPEC-MESH-038]` -- nothing here writes
anything, on either end, ever. What to do with the result is `export_bundle.py`
(for local-only/peer-only) and a person, always, for anything in `conflict`
`[SPEC-MESH-040]`, `[SPEC-MESH-060]`.
"""

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import remote_peek as rp  # noqa: E402  -- run_remote_sql()/literal(), reused not reinvented

# One entry per identity-scoped table this diffs. `key` names the columns
# that make a row the same thing on both sides `[SPEC-DF-030]`; `manual_field`
# is the provenance column checked for `[SPEC-MESH-060]`'s conflict rule, or
# `None` where the table carries no provenance of its own (`files`: a fact
# about bytes, never a person's opinion).
TABLES = {
    "files": {
        "sql": "SELECT audio_md5, format, duration_ms FROM files",
        "key": ("audio_md5",),
        "value": ("format", "duration_ms"),
        "manual_field": None,
    },
    "recordings": {
        "sql": "SELECT mbid, title, length_ms, source FROM recordings",
        "key": ("mbid",),
        "value": ("title", "length_ms"),
        "manual_field": "source",
    },
    "passages": {
        # audio_md5 lives on `files`, not `passages` itself -- the join is
        # part of the identity, not an extra fetch `[SPEC-DF-030]`.
        "sql": ("SELECT f.audio_md5 AS audio_md5, p.kind AS kind, p.start_ms AS start_ms, "
                "p.end_ms AS end_ms, p.lead_in_ms AS lead_in_ms, p.lead_out_ms AS lead_out_ms, "
                "p.gain_db AS gain_db, p.boundary_src AS boundary_src "
                "FROM passages p JOIN files f ON f.file_id = p.file_id"),
        "key": ("audio_md5", "kind", "start_ms", "end_ms"),
        "value": ("lead_in_ms", "lead_out_ms", "gain_db", "boundary_src"),
        "manual_field": "boundary_src",
    },
}


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def _key_of(row: dict, key_cols) -> tuple:
    return tuple(row.get(c) for c in key_cols)


def fetch_local(db_path: str, table: str) -> dict:
    """Every row of one table, keyed -- a plain local query, no ssh involved."""
    # Catalogue-only, read-only.
    conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(TABLES[table]["sql"])]
    finally:
        conn.close()
    return {_key_of(r, TABLES[table]["key"]): r for r in rows}


def fetch_remote(remote: str, table: str) -> dict:
    """The same shape as `fetch_local`, over one `ssh ... sqlite3 -json ...`
    round trip `[SPEC-DF-116]` instead of a local connection."""
    result = rp.run_remote_sql(remote, TABLES[table]["sql"])
    if not result["ok"]:
        raise RuntimeError(f"{table}: {result['error']}")
    return {_key_of(r, TABLES[table]["key"]): r for r in result["rows"]}


def diff_table(table: str, local: dict, peer: dict) -> dict:
    """`[SPEC-MESH-035]` The four buckets, computed in memory from two
    already-fetched key->row maps -- no further round trip per key."""
    spec = TABLES[table]
    manual_field = spec["manual_field"]
    local_only, peer_only, conflicts, differs = [], [], [], []
    agree = 0

    for key in local.keys() - peer.keys():
        local_only.append(list(key))
    for key in peer.keys() - local.keys():
        peer_only.append(list(key))

    for key in local.keys() & peer.keys():
        lrow, prow = local[key], peer[key]
        same = all(lrow.get(c) == prow.get(c) for c in spec["value"])
        if same:
            agree += 1
            continue
        # `[SPEC-MESH-036]` machine-vs-machine disagreement resolves
        # mechanically (provenance rank, then recency, `[SPEC-DF-070]`) and
        # is not routed to a person. `[SPEC-MESH-060]` a conflict is: values
        # differ AND at least one side is `manual`.
        is_conflict = manual_field is not None and (
            lrow.get(manual_field) == "manual" or prow.get(manual_field) == "manual"
        )
        entry = {"key": list(key), "local": lrow, "peer": prow}
        (conflicts if is_conflict else differs).append(entry)

    return {
        "local_only": local_only,
        "peer_only": peer_only,
        "agree": agree,
        "differ": differs,   # informational only -- [SPEC-MESH-036], no action needed here
        "conflict": conflicts,  # [SPEC-MESH-060] -- needs a person, see [SPEC-MESH-075]
    }


def run(local_db: str, remote: str, tables=None) -> dict:
    tables = tables or list(TABLES)
    report = {"local_db": local_db, "remote": remote, "tables": {}}
    for table in tables:
        local = fetch_local(local_db, table)
        peer = fetch_remote(remote, table)
        report["tables"][table] = diff_table(table, local, peer)
    return report


def summarize(report: dict) -> str:
    lines = [f"mesh diff: {report['local_db']} <-> {report['remote']}"]
    for table, d in report["tables"].items():
        lines.append(
            f"  {table}: {len(d['local_only'])} local-only, {len(d['peer_only'])} peer-only, "
            f"{d['agree']} agree, {len(d['differ'])} differ (auto), "
            f"{len(d['conflict'])} conflict (needs a person)"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("local_db", help="path to this installation's lempi.db")
    ap.add_argument("remote", help="user@host:/path/to/listener.db")
    ap.add_argument("--table", action="append", dest="tables",
                     choices=list(TABLES), help="limit to one table (repeatable)")
    ap.add_argument("-o", "--out", help="write the full report as JSON")
    ap.add_argument("--json", action="store_true",
                     help="print {\"ok\": true, ...report} as the final line "
                          "[jobs.py's parse_json_tail convention]")
    args = ap.parse_args()

    try:
        report = run(args.local_db, args.remote, args.tables)
    except RuntimeError as e:
        say(f"mesh_diff: {e}")
        if args.json:
            say(json.dumps({"ok": False, "error": str(e)}))
        return 1

    say(summarize(report))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        say(f"full report: {args.out}")
    if args.json:
        # A conflict is data to look at, not a failure of the diff itself
        # [SPEC-MESH-038] -- "ok" here means "the comparison completed",
        # independent of the exit code below, which still signals "something
        # needs a person" to a caller running this by hand.
        say(json.dumps({"ok": True, **report}))
    any_conflicts = any(d["conflict"] for d in report["tables"].values())
    return 1 if any_conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
