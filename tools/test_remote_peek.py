#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `remote_peek.py` `[SPEC-DF-116]`.

No real `ssh` involved -- that leg is confirmed live against the actual
appliance, the same posture `[SPEC-DF-114]` already used for the full-copy
jobs. What is tested here, in process:

  1. `sql_for()` produces the *identical current value* `apply_changes.py`'s
     own `current_recording()`/`current_boundary()`/`current_artist()` would,
     run directly against a real fixture database -- proving the shapes are
     mirrored, not reinvented, exactly as the build order asked.
  2. `literal()` escapes what needs escaping, so a query built from anchor
     values can never break out of its own `SELECT`.
  3. `peek()`'s failure handling -- non-zero exit, a timeout, a malformed
     remote spec, malformed JSON -- collapses to `{"ok": False}` and never
     raises, with `subprocess.run` faked so no network or real `ssh` binary
     is needed `[SPEC-DF-118]`.

    python tools/test_remote_peek.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import remote_peek as rp  # noqa: E402
import apply_changes as ac  # noqa: E402

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
    format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id),
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    boundary_src TEXT NOT NULL,
    fade_in_ms INTEGER NOT NULL DEFAULT 20, fade_out_ms INTEGER NOT NULL DEFAULT 20,
    fade_in_curve TEXT NOT NULL DEFAULT 'exponential',
    fade_out_curve TEXT NOT NULL DEFAULT 'exponential',
    CHECK (end_ms > start_ms));
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL,
    sort_name TEXT, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT NOT NULL REFERENCES recordings(mbid),
    artist_mbid TEXT NOT NULL REFERENCES artists(mbid),
    weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
    PRIMARY KEY (mbid, artist_mbid)) WITHOUT ROWID;
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL REFERENCES passages(passage_id),
    mbid TEXT NOT NULL REFERENCES recordings(mbid), weight REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL, PRIMARY KEY (passage_id, mbid)) WITHOUT ROWID;
"""

REC = "11111111-1111-1111-1111-111111111111"
ART = "22222222-2222-2222-2222-222222222222"

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def build(path: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-a','/srv/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES "
              "(1,1,'radio',1000,200000,250,1200,-2.0,'src',15,1500,'linear','cosine')")
    c.execute("INSERT INTO recordings VALUES (?,'A Song',NULL,'inherited:mulib')", (REC,))
    c.execute("INSERT INTO artists VALUES (?,'A Band',NULL,'inherited:mulib')", (ART,))
    c.execute("INSERT INTO recording_artists VALUES (?,?,1.0,'inherited:mulib')", (REC, ART))
    c.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'inherited:mulib')", (REC,))
    c.commit()
    c.close()


def test_sql_mirrors_apply_changes(tmp: str) -> None:
    print("sql_for() returns the identical current value apply_changes.py computes locally")
    db = os.path.join(tmp, "fixture.db")
    build(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    anchor = {"audio_md5": "md5-a", "passage_kind": "radio", "start_ms": 1000, "end_ms": 200000}

    passage_id = ac.resolve_passage(conn, anchor)
    check(passage_id == 1, f"fixture's own resolve_passage must find it, got {passage_id}")

    expected_mbid = ac.current_recording(conn, passage_id)
    row = conn.execute(rp.sql_for("id_review", anchor)).fetchone()
    check(row is not None and row[0] == expected_mbid,
          f"id_review: expected {expected_mbid!r}, remote_peek's own SQL got {row!r}")

    expected_boundary = ac.current_boundary(conn, passage_id)
    row = conn.execute(rp.sql_for("boundary_review", anchor)).fetchone()
    got_boundary = dict(zip(
        ["start_ms", "end_ms", "lead_in_ms", "lead_out_ms", "gain_db",
         "fade_in_ms", "fade_out_ms", "fade_in_curve", "fade_out_curve"], row))
    check(got_boundary == expected_boundary,
          f"boundary_review: expected {expected_boundary}, got {got_boundary}")

    print("boundary_review_no_fade mirrors the same row minus the four fade columns "
          "-- the fallback peek() retries with [SPEC-SUI-226]")
    row = conn.execute(rp.sql_for("boundary_review_no_fade", anchor)).fetchone()
    got_no_fade = dict(zip(["start_ms", "end_ms", "lead_in_ms", "lead_out_ms", "gain_db"], row))
    check(got_no_fade == {k: expected_boundary[k] for k in got_no_fade},
          f"boundary_review_no_fade: expected the lead/gain subset, got {got_no_fade}")

    expected_artist = ac.current_artist(conn, REC)
    row = conn.execute(rp.sql_for("artist_review", {"recording_mbid": REC})).fetchone()
    got_artist = {"artist_mbid": row[0], "artist_name": row[1]}
    check(got_artist == expected_artist,
          f"artist_review: expected {expected_artist}, got {got_artist}")

    print("sql_for() against an anchor nothing matches returns no row, not an error")
    miss = {"audio_md5": "md5-nope", "passage_kind": "radio", "start_ms": 0, "end_ms": 1}
    check(conn.execute(rp.sql_for("boundary_review", miss)).fetchone() is None,
          "an unmatched anchor must yield no row")
    conn.close()


def test_passage_flag_sql(tmp: str) -> None:
    print("sql_for('passage_flag'): resolves the remote's own passage_id, and "
          "reports flagged under either subject shape [SPEC-DF-107]")
    db = os.path.join(tmp, "flag-fixture.db")
    build(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE listener_flags (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, "
        "flagged_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID")
    anchor = {"audio_md5": "md5-a", "passage_kind": "radio", "start_ms": 1000, "end_ms": 200000}

    row = conn.execute(rp.sql_for("passage_flag", anchor)).fetchone()
    check(row[:2] == (1, 0), f"not yet flagged -- expected (passage_id=1, flagged=0), got {row}")
    check(json.loads(row[2]) == [REC], f"the currently-linked recording must ride along, got {row}")

    conn.execute("INSERT INTO listener_flags VALUES ('recording', ?, 't', NULL)", (REC,))
    row = conn.execute(rp.sql_for("passage_flag", anchor)).fetchone()
    check(row[:2] == (1, 1), f"a recording-keyed flag must count, got {row}")

    conn.execute("DELETE FROM listener_flags")
    conn.execute("INSERT INTO listener_flags VALUES ('passage', '1', 't', NULL)")
    row = conn.execute(rp.sql_for("passage_flag", anchor)).fetchone()
    check(row[:2] == (1, 1), f"a passage-keyed flag must count too, got {row}")

    print("sql_for('passage_flag') against a passage the remote does not have at all: "
          "passage_id is NULL, not an error, and never falsely 'flagged'")
    miss = {"audio_md5": "md5-nope", "passage_kind": "radio", "start_ms": 0, "end_ms": 1}
    row = conn.execute(rp.sql_for("passage_flag", miss)).fetchone()
    check(row[:2] == (None, 0), f"got {row}")
    check(json.loads(row[2]) == [], f"no passage at all means no linked recordings either, got {row}")
    conn.close()


def test_literal_escaping(tmp: str) -> None:
    print("literal() escapes a value that would otherwise break out of the SELECT")
    check(rp.literal("it's a trap") == "'it''s a trap'", "a single quote must be doubled, not dropped")
    check(rp.literal(None) == "NULL", "None must render as SQL NULL")
    check(rp.literal(1000) == "1000", "an int must render bare, not quoted")
    check(rp.literal(-2.0) == "-2.0", "a float must render bare, not quoted")

    print("a hostile anchor value cannot smuggle a second statement into sql_for()'s output")
    db = os.path.join(tmp, "hostile.db")
    build(db)
    conn = sqlite3.connect(db)
    hostile = {"audio_md5": "x'; DROP TABLE passages; --", "passage_kind": "radio",
               "start_ms": 0, "end_ms": 1}
    sql = rp.sql_for("boundary_review", hostile)
    # Proof, not a heuristic: run it for real. Python's own sqlite3 module
    # refuses to `execute()` more than one statement at a time, so broken
    # escaping that let the DROP TABLE become a second statement would raise
    # here rather than silently succeed -- a match-nothing SELECT that
    # leaves `passages` intact is the actual guarantee `literal()` exists
    # to provide.
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error as e:
        check(False, f"the hostile value broke out of its own literal: {e}")
    else:
        check(row is None, f"the hostile audio_md5 must match nothing, got {row}")
        check(conn.execute("SELECT count(*) FROM passages").fetchone()[0] == 1,
              "the hostile value must not have dropped or altered the table")
    conn.close()


def test_peek_error_handling() -> None:
    print("peek() rejects a malformed remote spec without touching a subprocess at all")
    called = []
    real_run = rp.subprocess.run
    rp.subprocess.run = lambda *a, **k: called.append(1) or real_run(*a, **k)
    try:
        r = rp.peek("not-a-remote-spec", "boundary_review",
                     {"audio_md5": "x", "passage_kind": "radio", "start_ms": 0, "end_ms": 1})
        check(r == {"ok": False, "error": "remote must be user@host:/path, got 'not-a-remote-spec'"},
              f"got {r}")
        check(not called, "a malformed remote must never reach subprocess.run")
    finally:
        rp.subprocess.run = real_run

    anchor = {"recording_mbid": REC}

    def fake(returncode=0, stdout="", stderr=""):
        class R:
            pass
        r = R()
        r.returncode, r.stdout, r.stderr = returncode, stdout, stderr
        return r

    print("peek() reports ok on a clean round trip with a row")
    rp.subprocess.run = lambda *a, **k: fake(0, json.dumps([{"artist_mbid": ART, "artist_name": "A Band"}]))
    try:
        r = rp.peek("pi@lempipi:/srv/library/library.db", "artist_review", anchor)
        check(r == {"ok": True, "current": {"artist_mbid": ART, "artist_name": "A Band"}}, f"got {r}")
    finally:
        rp.subprocess.run = real_run

    print("peek() reports ok with current=None on a clean round trip with no row")
    rp.subprocess.run = lambda *a, **k: fake(0, "")
    try:
        r = rp.peek("pi@lempipi:/srv/library/library.db", "artist_review", anchor)
        check(r == {"ok": True, "current": None}, f"got {r}")
    finally:
        rp.subprocess.run = real_run

    print("peek() reports not-ok on a non-zero exit, without raising")
    rp.subprocess.run = lambda *a, **k: fake(255, "", "ssh: connect to host lempipi port 22: No route to host")
    try:
        r = rp.peek("pi@lempipi:/srv/library/library.db", "artist_review", anchor)
        check(r["ok"] is False and "No route to host" in r["error"], f"got {r}")
    finally:
        rp.subprocess.run = real_run

    print("peek() reports not-ok, fast, on a timeout -- never hangs [SPEC-DF-118]")
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=10)
    rp.subprocess.run = raise_timeout
    try:
        r = rp.peek("pi@lempipi:/srv/library/library.db", "artist_review", anchor)
        check(r["ok"] is False and "lempipi" in r["error"], f"got {r}")
    finally:
        rp.subprocess.run = real_run

    print("peek() reports not-ok on a malformed reply instead of raising")
    rp.subprocess.run = lambda *a, **k: fake(0, "{not json")
    try:
        r = rp.peek("pi@lempipi:/srv/library/library.db", "artist_review", anchor)
        check(r["ok"] is False and "unparseable" in r["error"], f"got {r}")
    finally:
        rp.subprocess.run = real_run

    print("peek() retries a boundary_review once without fade columns "
          "against a remote that has never migrated for them [SPEC-SUI-226]")
    boundary_anchor = {"audio_md5": "md5-a", "passage_kind": "radio", "start_ms": 1000, "end_ms": 200000}
    calls = []
    no_fade_row = {"start_ms": 1000, "end_ms": 200000, "lead_in_ms": 250,
                    "lead_out_ms": 1200, "gain_db": -2.0}

    def fake_missing_fade_then_ok(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            return fake(1, "", "Runtime error: no such column: p.fade_in_ms")
        return fake(0, json.dumps([no_fade_row]))

    rp.subprocess.run = fake_missing_fade_then_ok
    try:
        r = rp.peek("pi@lempipi:/srv/library/library.db", "boundary_review", boundary_anchor)
        check(len(calls) == 2, f"expected exactly one retry (2 round trips), got {len(calls)}")
        check(r == {"ok": True, "current": no_fade_row}, f"got {r}")
    finally:
        rp.subprocess.run = real_run

    print("peek() does not retry a boundary_review failure unrelated to fade columns")
    calls.clear()

    def fake_unrelated_failure(*a, **k):
        calls.append(1)
        return fake(255, "", "ssh: connect to host lempipi port 22: No route to host")

    rp.subprocess.run = fake_unrelated_failure
    try:
        r = rp.peek("pi@lempipi:/srv/library/library.db", "boundary_review", boundary_anchor)
        check(len(calls) == 1, f"an unrelated failure must not trigger a retry, got {len(calls)} call(s)")
        check(r["ok"] is False and "No route to host" in r["error"], f"got {r}")
    finally:
        rp.subprocess.run = real_run


def test_python_fallback_command_shape():
    """The python3 fallback must ask for the same thing `sqlite3 -json` does.

    Not run against a host here -- that is `bose`'s job, and it is exercised
    live -- but the command is built from `shlex.quote`d parts and a
    read-only URI, and both of those are worth pinning: a fallback that
    quietly opened a remote read-write would be a much worse bug than the
    missing binary it works around.
    """
    cmd = rp._python_cmd("/var/lempi/listener.db", "SELECT 1")
    check("python3 -c" in cmd, f"must invoke python3, got {cmd}")
    check("mode=ro" in rp._PY_FALLBACK, "the fallback must open the remote READ-ONLY")
    check("/var/lempi/listener.db" in cmd and "SELECT 1" in cmd,
          f"path and sql must both reach it, got {cmd}")
    tricky = rp._python_cmd("/a b/c.db", "SELECT 'x';DROP TABLE t")
    check("'/a b/c.db'" in tricky or '"/a b/c.db"' in tricky,
          f"a path with a space must be quoted, got {tricky}")


def main() -> int:
    test_python_fallback_command_shape()
    with tempfile.TemporaryDirectory() as tmp:
        test_sql_mirrors_apply_changes(tmp)
        test_passage_flag_sql(tmp)
        test_literal_escaping(tmp)
    test_peek_error_handling()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("remote_peek: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
