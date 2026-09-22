#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recover MuLibPlay's profanity ratings, which `migrate_mulib.py` dropped.

`migrate_mulib.py` lists `profanity` in `DEAD_FIELDS`, justified as "NULL for
all 8,116 rows across six years, or under 10 rows" and citing `[GDE-BMK-040]`.
That finding covers nine columns and profanity is not one of them: 2,529
MuLibPlay tracks carry a non-NULL value and **69 carry a real non-zero one**,
between 0.001 and 0.874. Audited 2026-09-10; this is the only MuLibPlay
listener data that migration actually loses `[SPEC-PREF-082]`.

Every one of the 69 carries an `mbidRecording`, and every one of those
resolves in the Lempi catalogue, so this is a join and not a re-derivation --
no audio decode, no fingerprinting, no `sig` bridge.

**The 2,460 tracks whose profanity is exactly 0.0 are deliberately not
written** `[SPEC-PREF-083]`. An absent value already reads as 0.0 everywhere
it is consulted -- the occasion multiplier is `1 + value * (curve - 1)`, so
0.0 ignores the curve exactly -- and MuLibPlay initialised the column rather
than 2,460 people deciding 2,460 songs were clean. Writing them would cost a
row per track (4,920 with complements) to change nothing, while destroying
the distinction the preference panel is built on: "nobody has an opinion"
against "somebody said no".

Rows land in `flavor` with `source='inherited:mulib'`, positive and
complement, exactly as the same migration wrote the four occasion tags
beside them -- **not** in `listener_characteristics`, which asserts "this
listener set this by hand" and a six-year-old migrated value is not that.

Idempotent: the source is immutable and the write is `INSERT OR REPLACE`, so
re-running lands the same rows.

Usage:
  python tools/backfill_profanity.py <listener.db> [--mulib ../MuLibPlay/mulib.db]
                                     [--library library.db] [--commit]
  python tools/backfill_profanity.py <listener.db> --sql-out patch.sql

`--library` is for a split installation `[IMPL-DBSPLIT-025]`: `flavor` lives
on the catalogue side, so that is the file written. Omitted, the one database
given is both.

`--sql-out` writes the patch instead of applying it, for a catalogue this
machine cannot open -- the appliance's, which the player attaches read-only
and which is reached by shipping SQL and restarting the service
`[PI5-LIB-010]`, exactly as `sync_preferences.py` reaches its own remote.
Every statement carries its own `WHERE EXISTS` guard against `recordings`,
so the patch is checked against whichever catalogue actually applies it
rather than against this one.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

SRC = "inherited:mulib"
CHARACTERISTIC = "user.profanity"
POSITIVE = "profane"
COMPLEMENT = "not_profane"


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def source_ratings(mulib: str) -> list[tuple[str, str, float]]:
    """`(mbid_recording, name, value)` for every genuinely-rated track.

    `profanity <> 0` is the whole filter, and the docstring above is why.
    A track with no `mbidRecording` could not be placed even if it had one,
    and is returned anyway so the caller can report it rather than quietly
    counting a smaller total.
    """
    con = sqlite3.connect(f"file:{mulib}?mode=ro&immutable=1", uri=True)
    rows = con.execute(
        "SELECT mbidRecording, name, profanity FROM tracks "
        "WHERE profanity IS NOT NULL AND profanity <> 0 ORDER BY profanity DESC"
    ).fetchall()
    con.close()
    return [(r[0], r[1], float(r[2])) for r in rows]


def plan(con, ratings: list[tuple[str, str, float]]) -> tuple[list, list, list]:
    """Split the source rows into what can be written, what has no
    recording in this catalogue, and what is already present and identical.

    Existence is checked against `recordings`, not assumed: a library that
    never ingested a given track must not receive a `flavor` row pointing at
    an mbid it does not carry -- the same rule `sync_preferences.py` applies
    before pushing a preference `[SPEC-PREF-110]`.
    """
    writable, missing, unchanged = [], [], []
    for mbid, name, value in ratings:
        if not mbid or not con.execute(
                "SELECT 1 FROM recordings WHERE mbid = ?", (mbid,)).fetchone():
            missing.append((mbid, name, value))
            continue
        have = con.execute(
            "SELECT value FROM flavor WHERE subject_kind='recording' AND subject_id=? "
            "AND characteristic=? AND class=?",
            (mbid, CHARACTERISTIC, POSITIVE)).fetchone()
        if have is not None and abs(have[0] - value) < 1e-12:
            unchanged.append((mbid, name, value))
        else:
            writable.append((mbid, name, value))
    return writable, missing, unchanged


def patch_sql(ratings: list[tuple[str, str, float]]) -> str:
    """The same rows as `write`, as a transaction that checks itself.

    The existence rule `plan` applies locally cannot be applied here: this
    patch is for a catalogue this process has never opened. So each row
    carries `WHERE EXISTS (SELECT 1 FROM recordings ...)` and is checked by
    the database that actually applies it -- which is the stronger form of
    the same rule, not a weaker one, since it cannot go stale between
    generating the patch and landing it.
    """
    lines = ["BEGIN IMMEDIATE;"]
    for mbid, _name, value in ratings:
        if not mbid:
            continue
        q = mbid.replace("'", "''")
        for cls, v in ((POSITIVE, value), (COMPLEMENT, 1.0 - value)):
            lines.append(
                f"INSERT OR REPLACE INTO flavor "
                f"SELECT 'recording', '{q}', '{CHARACTERISTIC}', '{cls}', {v!r}, "
                f"'{SRC}', NULL WHERE EXISTS (SELECT 1 FROM recordings WHERE mbid = '{q}');")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def write(con, writable: list[tuple[str, str, float]]) -> int:
    rows = []
    for mbid, _name, value in writable:
        rows.append(("recording", mbid, CHARACTERISTIC, POSITIVE, value, SRC, None))
        rows.append(("recording", mbid, CHARACTERISTIC, COMPLEMENT, 1.0 - value, SRC, None))
    con.executemany("INSERT OR REPLACE INTO flavor VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="lempi.db (or the listener half of a split installation)")
    ap.add_argument("--mulib", default="../MuLibPlay/mulib.db")
    ap.add_argument("--library", help="catalogue half of a split installation; flavor lives here")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--sql-out", help="write a self-guarding patch instead of applying it")
    args = ap.parse_args()

    target = args.library or args.db
    ratings = source_ratings(args.mulib)
    say(f"MuLibPlay: {len(ratings)} tracks carry a non-zero profanity rating")

    if args.sql_out:
        sql = patch_sql(ratings)
        with open(args.sql_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(sql)
        placed = sum(1 for m, _n, _v in ratings if m)
        say(f"  wrote {args.sql_out}: {placed} ratings, each with its complement, "
            f"every row guarded by its own EXISTS check against `recordings`")
        return 0

    # `flavor` is catalogue-side, so that half is `main`. `--library` still
    # names it explicitly for halves that are not siblings; where they are,
    # or where this is one whole database, it is found without being told
    # `[IMPL-DBSPLIT-025]`.
    con = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=args.commit,
                           peer=(args.library if args.library else None))
    try:
        writable, missing, unchanged = plan(con, ratings)
    except sqlite3.OperationalError as e:
        say(f"error: {target} does not look like a catalogue ({e})")
        say("       a split installation keeps `flavor` in library.db -- pass --library")
        return 1

    say(f"  {len(writable)} to write, {len(unchanged)} already present and identical, "
        f"{len(missing)} with no such recording here")
    for mbid, name, value in missing:
        say(f"    skipped {value:.3f}  {name}  ({mbid or 'no mbidRecording'})")
    for mbid, name, value in writable[:5]:
        say(f"    {value:.3f}  {name}")
    if len(writable) > 5:
        say(f"    ... and {len(writable) - 5} more")

    if not args.commit:
        say("\n(dry run -- pass --commit to write)")
        return 0
    if not writable:
        say("\nnothing to write")
        return 0
    n = write(con, writable)
    con.close()
    say(f"\nwrote {n} flavor rows ({len(writable)} ratings, each with its complement) into {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
