#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `backfill_rip_damage.py`: a damaged track a rip's log names is
recorded once, a clean one never, and nothing is written without --commit.

    python tools/test_backfill_rip_damage.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(os.path.dirname(HERE), "sql", "schema.sql")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def run(*args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "backfill_rip_damage.py"), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=60)
    return r.returncode, r.stdout + r.stderr


def failures(db):
    c = sqlite3.connect(db)
    got = [json.loads(d)["track"] for (d,) in c.execute(
        "SELECT detail FROM ingest_decisions WHERE outcome='verification_failed' ORDER BY decision_id")]
    c.close()
    return got


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        album = os.path.join(tmp, "Artist", "Album (2003)")
        os.makedirs(album)
        with open(os.path.join(album, "Artist - Album.log"), "w", encoding="utf-8", newline="") as f:
            f.write("\r\n".join(["AccurateRip summary", "",
                                 "Track  1  accurately ripped (confidence 140)  [6872D822]",
                                 "Track  2  cannot be verified as accurate (confidence 458)  [22BA73ED]", ""]))
        db = os.path.join(tmp, "lib.db")
        c = sqlite3.connect(db)
        c.executescript(open(SCHEMA, encoding="utf-8").read())
        c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, "
                  "first_seen, last_seen) VALUES (1, 'md5', ?1, 1, 0, 'mp3', 1, 'x', 'x')",
                  (os.path.join(album, "Artist - Album.mp3"),))
        c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, decided_at) "
                  "VALUES ('md5', 'rip', 'exact', 'x')")
        c.commit()
        c.close()

        print("a dry run says what it would record, and writes nothing")
        code, out = run(db)
        check(code == 0 and "track  2: cannot be verified as accurate" in out and "track  1" not in out, out)
        check(failures(db) == [], f"nothing written: {failures(db)}")

        print("--commit records the damaged track once, and a second run adds nothing")
        run(db, "--commit")
        check(failures(db) == [2], f"track 2 only: {failures(db)}")
        code, out = run(db, "--commit")
        check(failures(db) == [2] and "0 damaged track(s) recorded" in out, f"{failures(db)} {out}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("backfill_rip_damage: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
