#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `resolve_mesh_conflict.py` `[SPEC-MESH-070]`, `[SPEC-MESH-098]`.

The same posture `test_sync_preferences.py` already takes: the pure
decision/SQL-building logic is tested directly; the actual `ssh`/`scp` round
trip in `apply_remote()` is not faked here and is verified live, not by unit
test, the same as every other tool in this family. `resolve()`'s rehearsal
mode (`commit=False`) exercises the full decision path -- which side(s) need
a write -- without ever calling `apply_local`/`apply_remote`, so it needs no
faking at all beyond the remote *read* `mesh_diff.fetch_remote()` already
uses `run_remote_sql` for.

    python tools/test_resolve_mesh_conflict.py
"""

import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import resolve_mesh_conflict as rmc  # noqa: E402
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
    def run(remote, sql, timeout=10.0):
        for table, rows in rows_by_table.items():
            if f"FROM {table}" in sql:
                return {"ok": True, "rows": rows}
        return {"ok": True, "rows": []}
    return run


def test_copy_columns_excludes_the_manual_field():
    check(rmc.copy_columns("recordings") == ["title", "length_ms"],
          f"source is the manual field and must not be copied, got {rmc.copy_columns('recordings')}")
    check("boundary_src" not in rmc.copy_columns("passages"),
          "boundary_src is passages' manual field and must not be copied either")


def test_resolve_choosing_local_writes_only_the_peer_in_rehearsal():
    db = local_db("INSERT INTO recordings VALUES ('m1','Local Title',NULL,'manual');")
    real = rp.run_remote_sql
    rp.run_remote_sql = fake_remote({
        "recordings": [{"mbid": "m1", "title": "Peer Title", "length_ms": None, "source": "computed:x@1"}],
    })
    try:
        result = rmc.resolve(db, "pi@peer:/lempi.db", "recordings", ("m1",),
                              {"title": "Local Title", "length_ms": None}, commit=False)
    finally:
        rp.run_remote_sql = real
        os.unlink(db)

    check(result["local_written"] is False, "local already holds the chosen value -- must not need a write")
    check(result["peer_written"] is True, "peer disagrees with the chosen value -- must need a write")


def test_resolve_a_third_value_needs_both_sides_written():
    db = local_db("INSERT INTO recordings VALUES ('m1','Local Title',NULL,'manual');")
    real = rp.run_remote_sql
    rp.run_remote_sql = fake_remote({
        "recordings": [{"mbid": "m1", "title": "Peer Title", "length_ms": None, "source": "computed:x@1"}],
    })
    try:
        result = rmc.resolve(db, "pi@peer:/lempi.db", "recordings", ("m1",),
                              {"title": "A Third Title", "length_ms": None}, commit=False)
    finally:
        rp.run_remote_sql = real
        os.unlink(db)

    check(result["local_written"] is True, "neither side has the third value -- local must need a write")
    check(result["peer_written"] is True, "neither side has the third value -- peer must need a write")


def test_resolve_requires_the_manual_field_to_actually_be_manual():
    # Same title, but local's source is still computed -- [SPEC-MESH-070]
    # says a resolution sets the provenance to manual, so a side whose value
    # merely *matches* without carrying manual provenance still needs the
    # write, or the next diff would still call it a conflict.
    db = local_db("INSERT INTO recordings VALUES ('m1','Same Title',NULL,'computed:x@1');")
    real = rp.run_remote_sql
    rp.run_remote_sql = fake_remote({
        "recordings": [{"mbid": "m1", "title": "Same Title", "length_ms": None, "source": "manual"}],
    })
    try:
        result = rmc.resolve(db, "pi@peer:/lempi.db", "recordings", ("m1",),
                              {"title": "Same Title", "length_ms": None}, commit=False)
    finally:
        rp.run_remote_sql = real
        os.unlink(db)

    check(result["local_written"] is True,
          "matching value but non-manual provenance must still be written, or the conflict never actually clears")
    check(result["peer_written"] is False, "peer already matches and is already manual")


def test_apply_local_writes_the_value_and_forces_manual():
    db = local_db("INSERT INTO recordings VALUES ('m1','Old',NULL,'computed:x@1');")
    try:
        rmc.apply_local(db, "recordings", ("m1",), {"title": "New", "length_ms": 5000})
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT title, length_ms, source FROM recordings WHERE mbid='m1'").fetchone()
        conn.close()
        check(row == ("New", 5000, "manual"), f"expected ('New', 5000, 'manual'), got {row}")
    finally:
        os.unlink(db)


def test_apply_local_resolves_a_passage_through_its_file():
    db = local_db(
        "INSERT INTO files VALUES (1,'md5-a','/a.wav',1,1.0,'wav',1000,'t','t');"
        "INSERT INTO passages VALUES (1,1,'radio',0,900,10,20,-1.0,'computed:x@1');"
    )
    try:
        rmc.apply_local(db, "passages", ("md5-a", "radio", 0, 900),
                         {"lead_in_ms": 99, "lead_out_ms": 88, "gain_db": -2.0})
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT lead_in_ms, lead_out_ms, gain_db, boundary_src FROM passages WHERE passage_id=1"
        ).fetchone()
        conn.close()
        check(row == (99, 88, -2.0, "manual"), f"expected the new values with boundary_src=manual, got {row}")
    finally:
        os.unlink(db)


def test_build_update_literal_never_uses_bind_placeholders():
    sql = rmc.build_update_literal("passages", ("md5-a", "radio", 0, 900),
                                    {"lead_in_ms": 5, "lead_out_ms": 6, "gain_db": None})
    check("?" not in sql, f"a remote patch must be fully rendered, no bind placeholders: {sql}")
    check("'md5-a'" in sql, f"the key must be quoted as a literal in the WHERE clause: {sql}")


def main() -> int:
    test_copy_columns_excludes_the_manual_field()
    test_resolve_choosing_local_writes_only_the_peer_in_rehearsal()
    test_resolve_a_third_value_needs_both_sides_written()
    test_resolve_requires_the_manual_field_to_actually_be_manual()
    test_apply_local_writes_the_value_and_forces_manual()
    test_apply_local_resolves_a_passage_through_its_file()
    test_build_update_literal_never_uses_bind_placeholders()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("resolve_mesh_conflict: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
