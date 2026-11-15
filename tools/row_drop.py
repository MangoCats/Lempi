# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dropping rows from a live database so that they can be put back exactly.

One way for the tools that remove rows found by evidence --
`drop_review_copies.py` [IMPL-UID-044], `drop_dangling_checks.py` -- to keep
what they remove. Before a row goes, the whole database is backed up through
SQLite's backup API (`star_sync.local_backup`, integrity-checked, never a copy
of the files) into `<backups>/<prefix>-<UTC>/`, and every row to be dropped is
written beside it to `dropped.json`. `put_back` restores those rows and only
those, refusing if anything has taken their place since, so an undo never
rolls back the rest of the database's life.

`dropped.json` is `{"db": ..., "table": ..., "at": ..., "rows": [{column: value}]}`.
The first file of this kind, written 2026-11-14, names its database `listener`
and no table; `put_back` reads both forms.
"""
from __future__ import annotations

import datetime as dt
import json
import os


def keep(src: str, backups: str, prefix: str, table: str, rows: list[dict]) -> str:
    """Back `src` up and write `rows` beside it; the folder holding both."""
    import star_sync  # noqa: E402  -- its backup: the API, integrity-checked [SPEC-STAR-106]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = os.path.join(backups, f"{prefix}-{stamp}")
    star_sync.local_backup(src, os.path.join(folder, os.path.basename(src)))
    with open(os.path.join(folder, "dropped.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"db": os.path.abspath(src), "table": table, "at": stamp, "rows": rows}, fh, indent=1)
    return folder


def dropped(folder: str, table: str) -> list[dict]:
    """The rows a commit wrote down, refusing a file kept for another table."""
    with open(os.path.join(folder, "dropped.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    if d.get("table", table) != table:
        raise SystemExit(f"{folder} holds rows of {d['table']}, not {table}")
    return d["rows"]


def put_back(conn, folder: str, table: str, key: tuple, schema: str = "main") -> int:
    """Insert the rows `folder` holds, in one transaction, refusing -- writing
    nothing -- if a row with the same `key` is there again."""
    rows = dropped(folder, table)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for r in rows:
            where = " AND ".join(f"{k} IS ?" for k in key)
            if conn.execute(f"SELECT 1 FROM {schema}.{table} WHERE {where}", [r[k] for k in key]).fetchone():
                raise SystemExit(f"refused: {table} holds {dict((k, r[k]) for k in key)} again; nothing was written")
            cols = list(r)
            conn.execute(f"INSERT INTO {schema}.{table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         [r[c] for c in cols])
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return len(rows)
