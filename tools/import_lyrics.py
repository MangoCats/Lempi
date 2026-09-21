#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bring MuLibPlay's lyrics into Lempi `[SPEC-LYR-030]`.

    import_lyrics.py <listener.db> <mulib.db> [--apply]

**A join, not a matching problem.** Every lyric MuLibPlay holds carries
`mbidRecording`, which is the identity Lempi already transports
`[SPEC-DF-030]` -- so this pairs on a key rather than guessing from titles.
Measured on the live pair: 2,288 lyrics, 2,265 of them (99.0%) naming a
recording Lempi knows.

Importing is Vipunen's job and not the player's `[SPEC-LYR-030]`, for the same
reason the player does not write `recordings`: derived reference data has one
writer, and a player that edits it becomes a second source of truth for
something it only reads.

Dry by default. `--apply` writes.
"""

import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
from datetime import datetime, timezone

SOURCE = "mulibplay"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lyrics (
    mbid       TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv[1:]
    if len(args) != 2:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    lempi_path, mulib_path = args

    # Catalogue-only, and it writes -- library half as `main`.
    db = lempi_db.connect(lempi_path, lempi_db.ROLE_LIBRARY, writable=True)
    db.execute("ATTACH ? AS m", (mulib_path,))

    # Every candidate, and whether Lempi has anywhere to put it. Counted apart
    # so "we have no such recording" never reads as "the import failed".
    rows = db.execute(
        """
        SELECT t.mbidRecording, t.name, t.lyrics,
               (SELECT 1 FROM recordings r WHERE r.mbid = t.mbidRecording)
        FROM m.tracks t
        WHERE t.lyrics IS NOT NULL AND TRIM(t.lyrics) <> ''
          AND t.mbidRecording IS NOT NULL AND t.mbidRecording <> ''
        """
    ).fetchall()

    known = [(m, n, ly) for m, n, ly, k in rows if k]
    unknown = [(m, n) for m, n, ly, k in rows if not k]

    # A recording named twice with different words is a contradiction in the
    # source, not something to resolve by whichever row sorted last.
    seen: dict[str, str] = {}
    conflicts = []
    for mbid, name, text in known:
        if mbid in seen and seen[mbid] != text:
            conflicts.append((mbid, name))
        seen[mbid] = text

    print(f"lyrics in {mulib_path}: {len(rows)}")
    print(f"  recordings Lempi knows : {len(known)}")
    print(f"  not in this library    : {len(unknown)}")
    if conflicts:
        print(f"  ** {len(conflicts)} recording(s) carry two different texts; last wins **")
        for mbid, name in conflicts[:5]:
            print(f"     {mbid}  {name}")

    if not apply:
        print("\ndry run; pass --apply to write")
        return 0

    db.executescript(SCHEMA)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Replace rather than ignore: re-importing a corrected source should correct
    # the library, which is the only reason to run this twice.
    db.executemany(
        "INSERT INTO lyrics (mbid, text, source, fetched_at) VALUES (?1, ?2, ?3, ?4) "
        "ON CONFLICT(mbid) DO UPDATE SET text = excluded.text, "
        "source = excluded.source, fetched_at = excluded.fetched_at",
        [(m, t, SOURCE, now) for m, t in seen.items()],
    )
    db.commit()

    have = db.execute("SELECT COUNT(*) FROM lyrics").fetchone()[0]
    covered = db.execute(
        """
        SELECT COUNT(DISTINCT p.passage_id) FROM passages p
          JOIN passage_recordings pr ON pr.passage_id = p.passage_id
          JOIN lyrics l ON l.mbid = pr.mbid
        WHERE p.kind = 'radio'
        """
    ).fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM passages WHERE kind = 'radio'").fetchone()[0]
    print(f"\nwrote {len(seen)}; {have} row(s) in lyrics")
    print(f"radio passages with words: {covered} of {total} ({100 * covered / max(total, 1):.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
