# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add `passages.fade_in_ms`/`fade_out_ms`/`fade_in_curve`/`fade_out_curve`
`[SPEC-SUI-226]`.

Independent of `lead_in_ms`/`lead_out_ms` -- those still time when a
crossfade is *permitted*, and `analyze_amplitude.py` keeps writing them
unchanged. Fade is this passage's own volume envelope: never start or end a
passage at an arbitrary sample, whether that means avoiding a click at a
hard file boundary or a soft way in/out of continuous audio (a DAO capture,
a live recording) that has no silence to lead into. Every passage gets the
same fixed 20 ms default; there is nothing to compute, so unlike
`repair_durations.py` this touches every row identically rather than only
the ones a probe finds wrong.

A plain `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT ...` backfills every
existing row in one statement -- no per-row UPDATE needed. Idempotent: a
second run sees the columns already there and does nothing, the same
`try/except OperationalError` shape `import_flags.py`'s `ensure_flags_table`
already uses for its own late-added column.

Usage:
  python tools/add_fade_columns.py <listener.db> [--write]
"""

from __future__ import annotations

import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
from pathlib import Path

COLUMNS = [
    ("fade_in_ms", "INTEGER NOT NULL DEFAULT 20"),
    ("fade_out_ms", "INTEGER NOT NULL DEFAULT 20"),
    ("fade_in_curve", "TEXT NOT NULL DEFAULT 'exponential'"),
    ("fade_out_curve", "TEXT NOT NULL DEFAULT 'exponential'"),
]


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        say(__doc__)
        return 2
    db = Path(args[0])
    write = "--write" in args

    # Catalogue-only, and it writes -- library half as `main`.
    con = lempi_db.connect(db, lempi_db.ROLE_LIBRARY, writable=True)
    have = {r[1] for r in con.execute("PRAGMA table_info(passages)")}
    missing = [(name, ddl) for name, ddl in COLUMNS if name not in have]

    if not missing:
        say("passages already has every fade column -- nothing to do")
        return 0

    say(f"passages is missing {len(missing)} column(s): "
        f"{', '.join(name for name, _ in missing)}")
    if not write:
        say("\n(dry run -- pass --write to apply)")
        return 0

    for name, ddl in missing:
        con.execute(f"ALTER TABLE passages ADD COLUMN {name} {ddl}")
    con.commit()

    n = con.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    say(f"added {len(missing)} column(s); {n} existing row(s) backfilled "
        f"with fade_in_ms=20, fade_out_ms=20, "
        f"fade_in_curve='exponential', fade_out_curve='exponential'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
