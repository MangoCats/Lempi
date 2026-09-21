#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `remote_flags.py` `[SPEC-DF-119]`.

No real `ssh` involved, the same posture `test_remote_peek.py` already
uses: `remote_peek.run_remote_sql()` (the one function that ever shells
out) is faked so these run with no network and no real remote.

  1. `FLAGS_SQL` produces the *identical* flags `export_flags.py`'s own
     `export_flags()` computes from a full local copy, run directly against
     a real fixture database -- proving one query replaces the copy, not
     just claiming it does.
  2. `fetch_flags()`'s degraded-schema fallbacks (`origin` missing, the
     whole table missing) and its plain error passthrough (an unreachable
     host).

    python tools/test_remote_flags.py
"""

import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import remote_flags as rf  # noqa: E402
import remote_peek as rp  # noqa: E402
import export_flags as ef  # noqa: E402

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
    format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id),
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    boundary_src TEXT NOT NULL, CHECK (end_ms > start_ms));
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
CREATE TABLE listener_flags (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    flagged_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID;
"""

SONG_A = "11111111-1111-1111-1111-111111111111"  # flagged by recording

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def build(path: str) -> None:
    """Three files, three passages -- one flagged by recording (an mbid that
    exists nowhere else in this fixture, on purpose: `export_flags()` never
    needs a `recordings` row to pass a recording-kind flag through), one
    flagged by a passage that exists, one flagged by a passage_id that
    doesn't -- the exact three shapes `export_flags.py` itself is tested
    against in `test_flag_sync.py`, reused here for direct comparison.
    """
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-a','/srv/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO files VALUES (2,'md5-b','/srv/b.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES (1,1,'radio',1000,200000,0,900,-1.0,'src')")
    c.execute("INSERT INTO passages VALUES (2,2,'radio',2000,190000,0,900,-1.0,'src')")
    c.execute("INSERT INTO listener_flags (subject_kind, subject_id, flagged_at, origin) "
              "VALUES ('recording', ?, '2026-08-29 09:00:00', NULL)", (SONG_A,))
    c.execute("INSERT INTO listener_flags (subject_kind, subject_id, flagged_at, origin) "
              "VALUES ('passage', '2', '2026-08-29 09:05:00', 'lempipi')")
    # A passage-kind flag whose passage no longer exists -- must be dropped,
    # not fabricated into a broken anchor.
    c.execute("INSERT INTO listener_flags (subject_kind, subject_id, flagged_at, origin) "
              "VALUES ('passage', '9999', '2026-08-29 09:10:00', NULL)")
    c.commit()
    c.close()


def by_subject(flags):
    return {(f["subject_kind"], json.dumps(f["anchor"], sort_keys=True)): f for f in flags}


def test_sql_mirrors_export_flags(tmp: str) -> None:
    print("FLAGS_SQL, run directly, produces exactly what export_flags() computes from a copy")
    db = os.path.join(tmp, "fixture.db")
    build(db)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    expected = ef.export_flags(conn, hostname="desktop")
    check(len(expected) == 2, f"the fixture's own export_flags() must drop the vanished passage, got {len(expected)}")

    got_rows = [dict(r) for r in conn.execute(rf.FLAGS_SQL)]
    check(len(got_rows) == 3, f"the raw SQL sees all three rows before the vanished one is dropped, got {len(got_rows)}")
    conn.close()

    # Run it through fetch_flags()'s own resolution logic (not just the raw
    # SQL) by faking the one round trip it would otherwise make.
    real = rp.run_remote_sql
    rp.run_remote_sql = lambda remote, sql, timeout=10.0: {"ok": True, "rows": got_rows}
    try:
        result = rf.fetch_flags("pi@lempipi:/srv/library/library.db", hostname="desktop")
    finally:
        rp.run_remote_sql = real
    check(result["ok"] is True, f"got {result}")
    check(len(result["flags"]) == 2, f"the vanished passage's flag must be dropped, got {result['flags']}")

    got = by_subject(result["flags"])
    want = by_subject(expected)
    check(got.keys() == want.keys(), f"the same subjects must resolve, got {got.keys()} vs {want.keys()}")
    for key in want:
        check(got[key]["anchor"] == want[key]["anchor"],
              f"{key}: anchor mismatch, got {got[key]['anchor']} vs {want[key]['anchor']}")
        check(got[key]["flagged_at"] == want[key]["flagged_at"], f"{key}: flagged_at mismatch")
    check(got[("recording", json.dumps({"recording_mbid": SONG_A}, sort_keys=True))]["origin"] == "desktop",
          "a NULL origin must fall back to the caller's own hostname, exactly as export_flags.py does")
    check(got[("passage", json.dumps({"audio_md5": "md5-b", "passage_kind": "radio",
                                       "start_ms": 2000, "end_ms": 190000}, sort_keys=True))]["origin"] == "lempipi",
          "an explicit origin must pass through unchanged")


def test_missing_origin_column_falls_back() -> None:
    print("a listener_flags predating the origin column falls back cleanly")
    calls = []

    def fake(remote, sql, timeout=10.0):
        calls.append(sql)
        if "f.origin AS origin" in sql:
            return {"ok": False, "error": "near line 1: no such column: f.origin"}
        return {"ok": True, "rows": [{"subject_kind": "recording", "subject_id": SONG_A,
                                       "flagged_at": "2026-08-29 09:00:00", "origin": None,
                                       "audio_md5": None, "passage_kind": None,
                                       "start_ms": None, "end_ms": None}]}

    real = rp.run_remote_sql
    rp.run_remote_sql = fake
    try:
        result = rf.fetch_flags("pi@lempipi:/srv/library/library.db", hostname="desktop")
    finally:
        rp.run_remote_sql = real
    check(len(calls) == 2, f"expected exactly one retry, got {len(calls)} calls")
    check(result["ok"] is True, f"got {result}")
    check(len(result["flags"]) == 1 and result["flags"][0]["origin"] == "desktop", f"got {result}")


def test_missing_table_is_zero_flags_not_an_error() -> None:
    print("no listener_flags table at all reads as nothing flagged, not a failure")
    real = rp.run_remote_sql
    rp.run_remote_sql = lambda remote, sql, timeout=10.0: (
        {"ok": False, "error": "near line 1: no such table: listener_flags"})
    try:
        result = rf.fetch_flags("pi@lempipi:/srv/library/library.db", hostname="desktop")
    finally:
        rp.run_remote_sql = real
    check(result == {"ok": True, "flags": []}, f"got {result}")


def test_unreachable_passes_through() -> None:
    print("an unreachable remote is reported plainly, not disguised as zero flags")
    real = rp.run_remote_sql
    rp.run_remote_sql = lambda remote, sql, timeout=10.0: {"ok": False, "error": "no route to host"}
    try:
        result = rf.fetch_flags("pi@lempipi:/srv/library/library.db", hostname="desktop")
    finally:
        rp.run_remote_sql = real
    check(result == {"ok": False, "error": "no route to host"}, f"got {result}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        test_sql_mirrors_export_flags(tmp)
    test_missing_origin_column_falls_back()
    test_missing_table_is_zero_flags_not_an_error()
    test_unreachable_passes_through()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("remote_flags: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
