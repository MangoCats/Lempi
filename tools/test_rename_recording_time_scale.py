#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `rename_recording_time_scale.py` `[SPEC-VOC-010]`.

    python tools/test_rename_recording_time_scale.py
"""

import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "rename_recording_time_scale.py")

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def run(db, *args):
    return subprocess.run([sys.executable, TOOL, db, *args], capture_output=True, text=True)


def pre_migration_db(tmp: str) -> str:
    db = os.path.join(tmp, "t.db")
    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE listener_settings (id INTEGER PRIMARY KEY, "
        "artist_time_scale REAL NOT NULL DEFAULT 1.0, "
        "track_time_scale REAL NOT NULL DEFAULT 1.0, "
        "utc_offset_minutes INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)")
    c.execute("INSERT INTO listener_settings VALUES (1, 0.5, 0.25, -300, 't')")
    c.commit()
    c.close()
    return db


def columns(db: str) -> set:
    c = sqlite3.connect(db)
    cols = {row[1] for row in c.execute("PRAGMA table_info(listener_settings)")}
    c.close()
    return cols


def test_dry_run_reports_and_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        db = pre_migration_db(tmp)
        r = run(db)
        check(r.returncode == 0, f"expected exit 0, got {r.returncode}: {r.stderr}")
        check("track_time_scale -> recording_time_scale" in r.stdout,
              f"expected the rename announced, got {r.stdout!r}")
        check("dry run" in r.stdout, f"expected a dry-run notice, got {r.stdout!r}")
        cols = columns(db)
        check("track_time_scale" in cols and "recording_time_scale" not in cols,
              f"a dry run must not touch the schema, got {cols}")


def test_write_renames_and_preserves_the_value():
    with tempfile.TemporaryDirectory() as tmp:
        db = pre_migration_db(tmp)
        r = run(db, "--write")
        check(r.returncode == 0, f"expected exit 0, got {r.returncode}: {r.stderr}")
        check("renamed" in r.stdout, f"expected confirmation, got {r.stdout!r}")
        cols = columns(db)
        check("recording_time_scale" in cols and "track_time_scale" not in cols,
              f"expected the column renamed, got {cols}")
        conn = sqlite3.connect(db)
        value = conn.execute(
            "SELECT artist_time_scale, recording_time_scale, utc_offset_minutes "
            "FROM listener_settings WHERE id = 1").fetchone()
        conn.close()
        check(value == (0.5, 0.25, -300), f"expected every value preserved, got {value}")


def test_second_run_is_a_no_op():
    with tempfile.TemporaryDirectory() as tmp:
        db = pre_migration_db(tmp)
        run(db, "--write")
        r = run(db, "--write")
        check(r.returncode == 0, f"expected exit 0, got {r.returncode}: {r.stderr}")
        check("already exists -- nothing to do" in r.stdout,
              f"a second run must recognise it is already done, got {r.stdout!r}")


def test_no_listener_settings_table_is_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "fresh.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE files (file_id INTEGER PRIMARY KEY)")
        c.commit()
        c.close()
        r = run(db, "--write")
        check(r.returncode == 0, f"expected exit 0, got {r.returncode}: {r.stderr}")
        check("nothing tuned yet" in r.stdout, f"expected the untuned-library case, got {r.stdout!r}")


def main() -> int:
    test_dry_run_reports_and_does_not_write()
    test_write_renames_and_preserves_the_value()
    test_second_run_is_a_no_op()
    test_no_listener_settings_table_is_not_an_error()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("rename_recording_time_scale: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
