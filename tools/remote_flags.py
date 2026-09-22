#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fetch a remote's flagged recordings and passages directly, no database copy `[SPEC-DF-119]`.

`export_flags.py` needed a full local copy only because it is Python, and
`[SPEC-DF-108]` already established lempi02w has none to run it *there* --
the same reason `[SPEC-DF-116]` gave `remote_peek.py` an `ssh ... sqlite3
-json ...` round trip instead of a copy for a single review anchor. There is
nothing about `listener_flags` that needs a copy either: it is one small
table, a handful of rows even on a well-used appliance, not the ~1.16 GB
`[SPEC-DF-114]` measured a full `scp` at. This runs the identical
resolution `export_flags.py`'s own `export_flags()` does -- a passage-kind
flag's `passage_id` joined out to the portable `(audio_md5, kind, start_ms,
end_ms)` anchor `[SPEC-DF-103]` already uses, a recording-kind flag passed
through by its own mbid -- as one query, over one `ssh` round trip, via
`remote_peek.py`'s `run_remote_sql()`.

    python tools/remote_flags.py pi@lempi02w:/srv/library/library.db         --remote-listener /var/lempi/listener.db -o flags.json

Writes the identical `flags.json` shape `export_flags.py` always has
(`{"format_version": 1, "flags": [...]}`) -- `import_flags.py` runs
unchanged either way, and a person can still fall back to the original
`scp` + `export_flags.py` recipe by hand if `ssh` access to run arbitrary
`sqlite3` is ever unavailable.
"""

import argparse
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote_peek as rp  # noqa: E402  -- run_remote_sql(), reused not reinvented

# One query for everything `export_flags.py` needed a full copy and a JOIN
# per passage-kind row to compute. `CAST(f.subject_id AS INTEGER)` is safe
# against a recording-kind row (an mbid, not a number) precisely because the
# join condition also requires subject_kind='passage' -- SQLite's cast never
# raises, and the ON clause short-circuits before it would matter anyway.
FLAGS_SQL = (
    "SELECT f.subject_kind AS subject_kind, f.subject_id AS subject_id, "
    "f.flagged_at AS flagged_at, f.origin AS origin, "
    "fi.audio_md5 AS audio_md5, p.kind AS passage_kind, "
    "p.start_ms AS start_ms, p.end_ms AS end_ms "
    "FROM listener_flags f "
    "LEFT JOIN passages p ON f.subject_kind='passage' AND p.passage_id = CAST(f.subject_id AS INTEGER) "
    "LEFT JOIN files fi ON fi.file_id = p.file_id "
    "ORDER BY f.flagged_at")

# A `listener_flags` predating `[SPEC-DF-104]`'s `origin` column -- every
# real appliance running this feature already has it, but a query that
# assumed so and simply failed on an old one would be exactly the silent
# gap `[REQ-LIB-165]` was written against.
FLAGS_SQL_NO_ORIGIN = FLAGS_SQL.replace("f.origin AS origin", "NULL AS origin")


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def attached(sql: str, listener: str | None) -> str:
    """`sql` with the peer's listener half ATTACHed in front of it.

    `run_remote_sql` runs `sqlite3 -json <path> "<sql>"`, and one path
    cannot serve both halves of a split installation `[IMPL002 §7.4]`. It
    can, however, run more than one statement: attaching first and then
    selecting leaves the unqualified `listener_flags` to resolve through
    the attach chain, exactly as it does locally.

    The positional `remote` stays the peer's **catalogue**, which is what
    `sync_remote` means for every tool that reaches a peer -- `passages` and
    `files` are the two tables this query joins, and `mesh_diff.py` wants
    that path and only that.
    """
    if not listener:
        return sql
    return f"ATTACH DATABASE {rp.literal(listener)} AS lis; {sql}"


def fetch_flags(remote: str, hostname: str, timeout: float = rp.TOTAL_TIMEOUT,
                listener: str | None = None) -> dict:
    """`{"ok": True, "flags": [...]}` in the exact shape `export_flags.py`'s
    own `export_flags()` produces, or `{"ok": False, "error": "..."}`.

    `listener` is the peer's listener-half path where it has one. Without
    it, against a split peer, this tool answers wrongly in both directions:
    pointed at the listener half it fails on `passages`, and pointed at the
    catalogue half it fails on `listener_flags` -- where the guard below
    then reports "nothing flagged" for an installation that has plenty.
    Measured against `pi@lempi02w` 2026-09-11.
    """
    result = rp.run_remote_sql(remote, attached(FLAGS_SQL, listener), timeout=timeout)
    if not result["ok"] and "no such column" in result["error"].lower() and "origin" in result["error"].lower():
        result = rp.run_remote_sql(remote, attached(FLAGS_SQL_NO_ORIGIN, listener), timeout=timeout)
    if not result["ok"] and "no such table" in result["error"].lower() and "listener_flags" in result["error"].lower():
        # No version of Lempi carrying `[REQ-VIS-265]` has opened this
        # library yet -- nothing flagged there is not a failure to report.
        #
        # **Only when this peer is not split.** On a split one the table is
        # absent from the *catalogue file* while being perfectly present in
        # the installation, and answering "nothing flagged" to that is the
        # absent-vs-unknown confusion `[SPEC-PREF-150]` names, in a
        # different disguise. With a listener path given, the attach above
        # has already made the table reachable, so reaching here means it
        # genuinely is not there.
        if listener:
            return {"ok": False, "error":
                    f"listener_flags is missing from {listener} on this peer"}
        return {"ok": True, "flags": []}
    if not result["ok"]:
        return result

    flags = []
    for row in result["rows"]:
        if row["subject_kind"] == "recording":
            anchor = {"recording_mbid": row["subject_id"]}
        else:
            if row.get("audio_md5") is None:
                # The passage this flag named no longer exists on the
                # remote -- nothing to anchor it to, the same reasoning
                # `[SPEC-DF-102]` already applies to a review row missing
                # its own baseline.
                continue
            anchor = {"audio_md5": row["audio_md5"], "passage_kind": row["passage_kind"],
                      "start_ms": row["start_ms"], "end_ms": row["end_ms"]}
        flags.append({
            "subject_kind": row["subject_kind"], "anchor": anchor,
            "flagged_at": row["flagged_at"], "origin": row.get("origin") or hostname,
        })
    return {"ok": True, "flags": flags}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("remote",
                help="user@host:/path/to/library.db -- the peer's CATALOGUE half")
    ap.add_argument("-o", "--out", required=True)
    # The peer's listener half, where it has one `[IMPL002 §7.4]`. Path
    # only: the host comes from `remote`.
    ap.add_argument("--remote-listener",
                    help="path to the peer's listener.db, if it is split")
    ap.add_argument("--timeout", type=float, default=rp.TOTAL_TIMEOUT)
    ap.add_argument("--json", action="store_true",
                     help="also print one final JSON summary line, for a caller "
                          "(the Vipunen console's remote-pull job) rather than a person")
    args = ap.parse_args()

    result = fetch_flags(args.remote, socket.gethostname(), timeout=args.timeout,
                         listener=args.remote_listener)
    if not result["ok"]:
        say(f"could not reach {args.remote}: {result['error']}")
        if args.json:
            say(json.dumps({"ok": False, "error": result["error"]}))
        return 1

    flags = result["flags"]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"format_version": 1, "flags": flags}, f, indent=2)

    say(f"{len(flags)} flag(s) exported to {args.out}")
    if not flags:
        say("nothing flagged there yet")
    if args.json:
        say(json.dumps({"ok": True, "count": len(flags)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
