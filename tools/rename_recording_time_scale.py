# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rename `listener_settings.track_time_scale` to `recording_time_scale`
`[SPEC-VOC-010]`.

The column was always the recording-side half of `[REQ-PD-118]`'s two master
time scales -- "one for artists, one for recordings" -- never a per-track
concept. `player/src/director/frequency.rs`'s own `Weighing`/`Policy` fields
were renamed the same way; this closes the one place that rename could not
reach on its own, because a Rust identifier and a SQL column are not the
same edit.

`ALTER TABLE ... RENAME COLUMN` (SQLite 3.25.0+, 2018) does this in place:
the stored value, the `CHECK` constraint, and every other column are
untouched, and nothing reads or writes through the old name afterward --
`sql/schema.sql` and `player/src/director/library.rs` were updated in the
same change that added this tool. Idempotent: a database already migrated,
or one with no `listener_settings` row at all (never tuned), is a no-op
either way, not an error.

Usage:
  python tools/rename_recording_time_scale.py <listener.db> [--write]
"""

from __future__ import annotations

import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
from pathlib import Path


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

    # Listener-only: `listener_settings` is the single table it touches.
    con = lempi_db.connect(db, lempi_db.ROLE_LISTENER, writable=True)
    have = lempi_db.tables(con)  # both halves [IMPL-DBSPLIT-025]
    if "listener_settings" not in have:
        say("no listener_settings table -- nothing tuned yet, nothing to rename")
        return 0

    cols = {r[1] for r in con.execute("PRAGMA table_info(listener_settings)")}
    if "recording_time_scale" in cols:
        say("listener_settings.recording_time_scale already exists -- nothing to do")
        return 0
    if "track_time_scale" not in cols:
        say("listener_settings has neither column -- unexpected shape, "
            "refusing to guess")
        return 1

    say("listener_settings.track_time_scale -> recording_time_scale")
    if not write:
        say("\n(dry run -- pass --write to apply)")
        return 0

    con.execute(
        "ALTER TABLE listener_settings RENAME COLUMN track_time_scale TO recording_time_scale")
    con.commit()
    say("renamed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
