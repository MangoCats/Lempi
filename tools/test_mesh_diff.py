#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `mesh_diff.py` `[SPEC035]` `[SPEC-MESH-030..038]`.

No real `ssh` involved, the same posture `test_remote_flags.py` already
uses: `remote_peek.run_remote_sql()` is faked, so these run with no network
and no real remote. The local side is a real, temporary sqlite file --
`fetch_local()` is exercised for real, never faked.

    python tools/test_mesh_diff.py
"""

import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mesh_diff as md  # noqa: E402
import remote_peek as rp  # noqa: E402

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
    format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT NOT NULL);
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def local_db(rows_sql: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.executescript(SCHEMA + rows_sql)
    c.commit()
    c.close()
    return path


def fake_remote(rows_by_table: dict):
    """Swaps in a `run_remote_sql` that answers from `rows_by_table`, matched
    by which table's `FROM` clause the query names -- the one thing that
    reliably distinguishes `mesh_diff.TABLES`' entries once reduced to SQL
    text, including `passages`' own join.
    """
    def run(remote, sql, timeout=10.0):
        for table, rows in rows_by_table.items():
            if f"FROM {table}" in sql:
                return {"ok": True, "rows": rows}
        return {"ok": True, "rows": []}
    return run


def test_files_local_only_peer_only_and_agreement():
    db = local_db(
        "INSERT INTO files VALUES (1,'md5-a','/a.mp3',1,1.0,'mp3',1000,'t','t');"
        "INSERT INTO files VALUES (2,'md5-b','/b.mp3',1,1.0,'mp3',2000,'t','t');"
    )
    real = rp.run_remote_sql
    rp.run_remote_sql = fake_remote({
        "files": [
            {"audio_md5": "md5-b", "format": "mp3", "duration_ms": 2000},  # agrees
            {"audio_md5": "md5-c", "format": "flac", "duration_ms": 3000},  # peer-only
        ],
    })
    try:
        report = md.run(db, "pi@peer:/lempi.db", tables=["files"])
    finally:
        rp.run_remote_sql = real
        os.unlink(db)

    d = report["tables"]["files"]
    check(d["local_only"] == [["md5-a"]], f"expected md5-a local-only, got {d['local_only']}")
    check(d["peer_only"] == [["md5-c"]], f"expected md5-c peer-only, got {d['peer_only']}")
    check(d["agree"] == 1, f"expected 1 agreement (md5-b), got {d['agree']}")
    check(d["conflict"] == [], "files carries no provenance -- never a conflict")


def test_recordings_manual_disagreement_is_a_conflict():
    db = local_db(
        "INSERT INTO recordings VALUES ('m1','Local Title',NULL,'manual');"
    )
    real = rp.run_remote_sql
    rp.run_remote_sql = fake_remote({
        "recordings": [{"mbid": "m1", "title": "Peer Title", "length_ms": None, "source": "computed:x@1"}],
    })
    try:
        report = md.run(db, "pi@peer:/lempi.db", tables=["recordings"])
    finally:
        rp.run_remote_sql = real
        os.unlink(db)

    d = report["tables"]["recordings"]
    check(len(d["conflict"]) == 1, f"local is manual and disagrees -- must be a conflict, got {d}")
    check(d["differ"] == [], "the manual disagreement must not also count as an auto-resolvable differ")


def test_recordings_machine_disagreement_is_not_a_conflict():
    db = local_db(
        "INSERT INTO recordings VALUES ('m1','Local Title',NULL,'computed:x@1');"
    )
    real = rp.run_remote_sql
    rp.run_remote_sql = fake_remote({
        "recordings": [{"mbid": "m1", "title": "Peer Title", "length_ms": None, "source": "computed:x@2"}],
    })
    try:
        report = md.run(db, "pi@peer:/lempi.db", tables=["recordings"])
    finally:
        rp.run_remote_sql = real
        os.unlink(db)

    d = report["tables"]["recordings"]
    check(d["conflict"] == [], f"[SPEC-MESH-036] neither side is manual -- must not need a person, got {d}")
    check(len(d["differ"]) == 1, "the disagreement is still real and should be visible, just not as a conflict")


def test_passages_join_produces_audio_md5_keyed_rows():
    db = local_db(
        "INSERT INTO files VALUES (1,'md5-a','/a.mp3',1,1.0,'mp3',1000,'t','t');"
        "INSERT INTO passages VALUES (1,1,'radio',0,900,0,50,-1.0,'computed:x@1');"
    )
    real = rp.run_remote_sql
    rp.run_remote_sql = fake_remote({"passages": []})
    try:
        report = md.run(db, "pi@peer:/lempi.db", tables=["passages"])
    finally:
        rp.run_remote_sql = real
        os.unlink(db)

    d = report["tables"]["passages"]
    check(d["local_only"] == [["md5-a", "radio", 0, 900]],
          f"passage identity must resolve through the files join, got {d['local_only']}")


def test_unreachable_remote_reports_cleanly():
    db = local_db("")
    real = rp.run_remote_sql
    rp.run_remote_sql = lambda remote, sql, timeout=10.0: {"ok": False, "error": "no route to host"}
    try:
        try:
            md.run(db, "pi@unreachable:/lempi.db", tables=["files"])
            check(False, "an unreachable remote must raise, not silently report an empty diff")
        except RuntimeError as e:
            check("no route to host" in str(e), f"the real error should surface, got: {e}")
    finally:
        rp.run_remote_sql = real
        os.unlink(db)


def main() -> int:
    test_files_local_only_peer_only_and_agreement()
    test_recordings_manual_disagreement_is_a_conflict()
    test_recordings_machine_disagreement_is_not_a_conflict()
    test_passages_join_produces_audio_md5_keyed_rows()
    test_unreachable_remote_reports_cleanly()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("mesh_diff: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
