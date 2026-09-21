#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply a person's decision on one mesh conflict to both sides `[SPEC-MESH-070]`,
`[SPEC-MESH-098]`.

`mesh_diff.py` finds a conflict; nothing about *resolving* one belongs to
that tool `[SPEC-MESH-038]` -- it is read-only, always. This is the write,
and only this is.

    python tools/resolve_mesh_conflict.py data/library.db pi@bose:/srv/library/library.db \\
        --table recordings --key '["<mbid>"]' --choice local --commit

`--choice` is `local` or `peer` -- the current value on that side, refetched
here rather than trusted from a stale report, since a diff and a resolution
are never the same round trip. `--value '{"title": "...", ...}'` supplies a
third value neither side has yet, naming exactly the value columns
`mesh_diff.TABLES[table]["value"]` lists (minus the provenance column itself,
which this always sets to `manual` regardless of source).

Writes only to whichever side(s) don't already hold the chosen value --
`source`/`boundary_src` becomes `manual` on both sides that receive a write,
so the next `mesh_diff.py` between these two sees `agree`, not `conflict`
`[SPEC-MESH-070]`. Reports by default; `--commit` writes, the same
rehearse-by-default shape every tool here uses `[SPEC-DF-109]`.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import mesh_diff as md  # noqa: E402  -- TABLES, fetch_local/fetch_remote
import remote_peek as rp  # noqa: E402  -- run_remote_sql()/literal()

PATCH_TMP = "/tmp/lempi-mesh-resolve.sql"


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def copy_columns(table: str) -> list:
    """Value columns actually copied from the chosen row -- every one of
    `TABLES[table]["value"]` except the provenance column itself, which is
    always forced to `manual` rather than copied from whichever side won
    `[SPEC-MESH-070]`.
    """
    spec = md.TABLES[table]
    return [c for c in spec["value"] if c != spec["manual_field"]]


def local_where(table: str, key: tuple) -> tuple:
    """(where_clause, params) locating one row by its identity key.
    `passages` needs `files` joined in to resolve `audio_md5` -- the same
    reason `mesh_diff.py`'s own `passages` query does.
    """
    key_cols = md.TABLES[table]["key"]
    if table == "passages":
        audio_md5, kind, start_ms, end_ms = key
        return ("file_id = (SELECT file_id FROM files WHERE audio_md5 = ?) "
                "AND kind = ? AND start_ms = ? AND end_ms = ?",
                (audio_md5, kind, start_ms, end_ms))
    return (" AND ".join(f"{c} = ?" for c in key_cols), tuple(key))


def build_update_literal(table: str, key: tuple, chosen: dict) -> str:
    """One fully-rendered `UPDATE` statement for the remote path -- nothing
    binds across an `ssh` round trip, so every value, including the `WHERE`
    clause's own key, is quoted with `remote_peek.literal` before this
    string ever leaves the machine. `apply_local` below is the local
    counterpart and binds parameters the ordinary way instead; the two are
    not the same function because a local `sqlite3` connection has no
    reason to render text it can bind directly.
    """
    spec = md.TABLES[table]
    cols = copy_columns(table)
    set_clause = ", ".join(f"{c} = {rp.literal(chosen.get(c))}" for c in cols)
    if spec["manual_field"]:
        set_clause += f", {spec['manual_field']} = 'manual'"
    where, params = local_where(table, key)
    for p in params:
        where = where.replace("?", rp.literal(p), 1)
    return f"UPDATE {table} SET {set_clause} WHERE {where};"


def apply_local(db_path: str, table: str, key: tuple, chosen: dict) -> None:
    spec = md.TABLES[table]
    cols = copy_columns(table)
    set_clause = ", ".join(f"{c} = ?" for c in cols)
    if spec["manual_field"]:
        set_clause += f", {spec['manual_field']} = 'manual'"
    where, where_params = local_where(table, key)
    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY, writable=True)
    try:
        conn.execute(f"UPDATE {table} SET {set_clause} WHERE {where}",
                     tuple(chosen.get(c) for c in cols) + where_params)
        conn.commit()
    finally:
        conn.close()


def apply_remote(remote: str, table: str, key: tuple, chosen: dict) -> bool:
    """The identical stop/patch/restart recipe `sync_preferences.py`'s own
    `apply_remote()` and `push_file_tags.py` already use -- reused in shape,
    not copied in code, since each patches a different table.
    """
    host, sep, path = remote.partition(":")
    if not sep or not path:
        raise ValueError(f"remote must be user@host:/path, got {remote!r}")
    sql_text = "BEGIN IMMEDIATE;\n" + build_update_literal(table, key, chosen) + "\nCOMMIT;\n"
    local_tmp = PATCH_TMP.rsplit("/", 1)[-1]
    with open(local_tmp, "w", encoding="utf-8") as f:
        f.write(sql_text)
    try:
        r = subprocess.run(["scp", "-q", local_tmp, f"{host}:{PATCH_TMP}"], timeout=30)
        if r.returncode != 0:
            return False
        r = subprocess.run(
            ["ssh", host, f"sudo systemctl stop lempi && sqlite3 {path} < {PATCH_TMP} "
                          f"&& sudo systemctl start lempi"],
            timeout=60)
        return r.returncode == 0
    finally:
        try:
            os.remove(local_tmp)
        except OSError:
            pass


def resolve(local_db: str, remote: str, table: str, key: tuple, chosen: dict, commit: bool) -> dict:
    """Refetches both sides' current rows -- a diff and a resolution are
    never the same round trip, and the report a person is looking at may be
    minutes old by the time they click a button `[SPEC-MESH-075]`.
    """
    local_rows = md.fetch_local(local_db, table)
    peer_rows = md.fetch_remote(remote, table)
    local_row = local_rows.get(key)
    peer_row = peer_rows.get(key)
    if local_row is None or peer_row is None:
        raise RuntimeError(f"{table} {list(key)}: no longer present on both sides -- refetch and retry")

    cols = copy_columns(table)
    result = {"table": table, "key": list(key), "local_written": False, "peer_written": False}

    local_matches = all(local_row.get(c) == chosen.get(c) for c in cols) and (
        md.TABLES[table]["manual_field"] is None or local_row.get(md.TABLES[table]["manual_field"]) == "manual")
    peer_matches = all(peer_row.get(c) == chosen.get(c) for c in cols) and (
        md.TABLES[table]["manual_field"] is None or peer_row.get(md.TABLES[table]["manual_field"]) == "manual")

    if not local_matches:
        result["local_written"] = True
        if commit:
            apply_local(local_db, table, key, chosen)
    if not peer_matches:
        result["peer_written"] = True
        if commit:
            if not apply_remote(remote, table, key, chosen):
                raise RuntimeError(f"{table} {list(key)}: remote patch failed")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("local_db")
    ap.add_argument("remote", help="user@host:/path/to/listener.db")
    ap.add_argument("--table", required=True, choices=[t for t in md.TABLES if md.TABLES[t]["manual_field"]])
    ap.add_argument("--key", required=True, help="JSON array, matching TABLES[table]['key'] order")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--choice", choices=["local", "peer"])
    group.add_argument("--value", help="JSON object of value columns, for a third value")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    key = tuple(json.loads(args.key))

    try:
        if args.choice:
            rows = md.fetch_local(args.local_db, args.table) if args.choice == "local" \
                else md.fetch_remote(args.remote, args.table)
            row = rows.get(key)
            if row is None:
                raise RuntimeError(f"{args.table} {list(key)}: not found on the {args.choice} side")
            chosen = {c: row.get(c) for c in copy_columns(args.table)}
        else:
            chosen = json.loads(args.value)

        result = resolve(args.local_db, args.remote, args.table, key, chosen, args.commit)
    except RuntimeError as e:
        say(f"resolve_mesh_conflict: {e}")
        if args.json:
            say(json.dumps({"ok": False, "error": str(e)}))
        return 1

    say(f"{args.table} {list(key)}: "
        f"{'wrote local, ' if result['local_written'] else 'local already agreed, '}"
        f"{'wrote peer' if result['peer_written'] else 'peer already agreed'}"
        + ("" if args.commit else " (rehearsal -- rerun with --commit)"))
    if args.json:
        say(json.dumps({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
