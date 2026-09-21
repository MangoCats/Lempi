#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clear listener references to passages that no longer exist.

**The application-level replacement for a database-level cascade that a
split silently switches off** -- `[IMPL009 §7.7]`'s named prerequisite for
splitting the *local* database specifically.

Three listener-side columns reference `passages` with `ON DELETE SET NULL`:

    listener_play_history.passage_id   -- six years of it
    selection_decisions.passage_id
    player_state.passage_id            -- the resume point

SQLite does not enforce a FOREIGN KEY across two ATTACHed databases. In one
file, deleting a passage NULLs these automatically; in two files the
constraint stops firing **silently**, because a query across an attach
boundary does not error, it just gets no enforcement. So every one of those
columns would start accumulating ids pointing at passages that no longer
exist, and `[SPEC-SC-095]`'s own reason for keeping them denormalised --
"six years of history must survive a rescan that renumbers passages" --
would be the thing broken.

`[IMPL009 §7.7]` names two of the three. `player_state.passage_id` is the
third, found by querying `sqlite_master` on the real local database rather
than reading the source -- the same method that found the other two, and
the same lesson: the deployed schema and the Rust constants are not the
same thing.

**This is correct before a split as well as after**, which is why it can be
wired in ahead of one. In a single file the cascade has already NULLed
everything by the time this runs and it clears nothing; in a split pair,
opened so that both halves are visible on one connection, it does the work
the cascade no longer can. The same call, either shape.

Usage, as a library -- from any tool that deletes or renumbers passages:

    import passage_orphans
    passage_orphans.clear(conn)          # after the delete, before commit

Usage, as a command -- to sweep a database that was already damaged:

    python tools/passage_orphans.py <listener.db> [--library library.db] [--commit]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

# `(table, column)` for every listener-side reference to `passages`.
# Ordered as `sqlite_master` reports them, so a diff against the schema
# reads in the same order.
ORPHAN_REFS = [
    ("listener_play_history", "passage_id"),
    ("selection_decisions", "passage_id"),
    ("player_state", "passage_id"),
]


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def clear(conn, dry_run: bool = False) -> dict[str, int]:
    """NULL every `passage_id` that no longer names a live passage.

    `conn` must be able to see **both** `passages` and the listener tables
    -- one file, or a listener database with the catalogue ATTACHed. An
    unqualified `passages` resolves through the attach chain either way,
    which is what lets this be the same code in both shapes.

    A table that is not present is skipped rather than raising: an
    installation predating `selection_decisions` is ordinary, and a caller
    should not have to know which vintage it is talking to. Returns
    `{table: rows_cleared}`, including zeros, so a caller can report "ran,
    found nothing" distinctly from "did not run".
    """
    out: dict[str, int] = {}
    for table, column in ORPHAN_REFS:
        find = (f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL "
                f"AND {column} NOT IN (SELECT passage_id FROM passages)")
        try:
            n = conn.execute(find).fetchone()[0]
        except sqlite3.OperationalError:
            continue  # table absent on this installation
        out[table] = n
        if n and not dry_run:
            conn.execute(f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL "
                         f"AND {column} NOT IN (SELECT passage_id FROM passages)")
    return out


def open_pair(listener: str, library: str | None, writable: bool):
    """A connection that can see both halves, whichever shape this is."""
    if writable:
        conn = sqlite3.connect(listener)
    else:
        conn = sqlite3.connect(f"file:{listener}?mode=ro", uri=True)
    if library and library != listener:
        mode = "" if writable else "?mode=ro"
        conn.execute("ATTACH DATABASE ? AS lib", (f"file:{library}{mode}",))
    return conn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="the listener database (or a whole lempi.db)")
    ap.add_argument("--library", help="the catalogue half, if this installation is split")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    conn = open_pair(args.db, args.library, writable=args.commit)
    # ATTACH needs a URI for read-only, and sqlite3 only honours that with
    # uri=True on the *connection* -- reopened here rather than silently
    # attaching read-write.
    try:
        found = clear(conn, dry_run=not args.commit)
    except sqlite3.OperationalError as e:
        say(f"error: {e}")
        say("  a split installation keeps `passages` in library.db -- pass --library")
        return 1

    if not found:
        say("no listener table references passages here; nothing to do")
        return 0
    total = sum(found.values())
    for table, n in found.items():
        say(f"  {table:<26} {n} orphaned reference(s)")
    if not args.commit:
        say(f"\n{total} would be cleared (dry run -- pass --commit)")
        return 0
    conn.commit()
    conn.close()
    say(f"\ncleared {total} orphaned reference(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
