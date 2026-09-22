#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `apply-reviews` job `[REQ-LIB-175]`, `[SPEC021 5]`.

The job that made the apply a button instead of a command to type. It runs
the real `apply_boundary_reviews.py` as a subprocess against a real split
pair, so the thing under test is the wiring and the ordering, not a mock of
either.

Two properties matter beyond "it wrote the row", and both are here because
both were decided deliberately rather than fallen into:

  * the player is interrupted *around* the write, not after it -- pause
    before, rebuild and resume after, and resume only if it was playing;
  * the job applies exactly the kinds it was asked for, so reviewing four
    boundary edits can never also land ninety-nine id reassignments.

    python tools/test_jobs_apply_reviews.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs as jobmod        # noqa: E402
import lempi_control         # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


LIBRARY_SQL = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT);
CREATE TABLE passages (
    passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
    start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER, lead_out_ms INTEGER,
    gain_db REAL, boundary_src TEXT,
    fade_in_ms INTEGER, fade_out_ms INTEGER,
    fade_in_curve TEXT, fade_out_curve TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL);
CREATE TABLE lowlevel_cache (audio_md5 TEXT, start_ms INTEGER, end_ms INTEGER);
-- `jobs.counts()` runs before every job and reads both of these.
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
CREATE TABLE id_checks (passage_id INTEGER PRIMARY KEY, stored_mbid TEXT, verdict TEXT);
INSERT INTO files VALUES (1, 'md5aaa', '/music/a.flac');
INSERT INTO passages VALUES (10, 1, 'album', 100, 200000, 0, 0, 0.0, 'ingest:whole-file',
                             20, 20, 'exponential', 'exponential');
INSERT INTO recordings VALUES ('mbid-a', 'A Song');
INSERT INTO passage_recordings VALUES (10, 'mbid-a', 1.0);
"""

LISTENER_SQL = """
CREATE TABLE listener_play_history (played_at INTEGER);
CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT);
CREATE TABLE listener_preferences (subject TEXT, v REAL);
CREATE TABLE listener_settings (k TEXT, v TEXT);
CREATE TABLE player_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), passage_id INTEGER,
    position_ms INTEGER NOT NULL DEFAULT 0, playing INTEGER NOT NULL DEFAULT 0,
    volume REAL NOT NULL DEFAULT 1.0, updated_at TEXT NOT NULL);
CREATE TABLE boundary_reviews (
    passage_id INTEGER PRIMARY KEY, start_ms INTEGER, end_ms INTEGER,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    decided_at TEXT, applied_at TEXT, audio_md5 TEXT, orig_kind TEXT,
    orig_start_ms INTEGER, orig_end_ms INTEGER, orig_lead_in_ms INTEGER,
    orig_lead_out_ms INTEGER, orig_gain_db REAL, origin TEXT,
    fade_in_ms INTEGER, fade_out_ms INTEGER, fade_in_curve TEXT, fade_out_curve TEXT,
    orig_fade_in_ms INTEGER, orig_fade_out_ms INTEGER,
    orig_fade_in_curve TEXT, orig_fade_out_curve TEXT);
CREATE TABLE id_reviews (
    passage_id INTEGER PRIMARY KEY, decision TEXT, chosen_mbid TEXT,
    chosen_release_mbid TEXT, previous_mbid TEXT, decided_at TEXT,
    applied_at TEXT, origin TEXT);
"""


def build(tmp: str, playing: int) -> tuple:
    """A real split pair, named by the convention `lempi_db` resolves."""
    lib = os.path.join(tmp, "library.db")
    lis = os.path.join(tmp, "listener.db")
    c = sqlite3.connect(lib)
    c.executescript(LIBRARY_SQL)
    c.commit()
    c.close()
    c = sqlite3.connect(lis)
    c.executescript(LISTENER_SQL)
    c.execute("INSERT INTO player_state VALUES (1, 10, 0, ?1, 1.0, '2026-09-11 00:00:00')",
              (playing,))
    # 100-200000 becomes 100-208000, with a real lead-out and fade-out change.
    c.execute("""INSERT INTO boundary_reviews
                 (passage_id, start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db,
                  decided_at, applied_at, audio_md5, orig_kind,
                  orig_start_ms, orig_end_ms, orig_lead_in_ms, orig_lead_out_ms, orig_gain_db,
                  fade_in_ms, fade_out_ms, fade_in_curve, fade_out_curve,
                  orig_fade_in_ms, orig_fade_out_ms, orig_fade_in_curve, orig_fade_out_curve)
                 VALUES (10, 100, 208000, 0, 900, 0.0,
                         '2026-09-11 00:00:00', NULL, 'md5aaa', 'album',
                         100, 200000, 0, 0, 0.0,
                         20, 150, 'exponential', 'exponential',
                         20, 20, 'exponential', 'exponential')""")
    c.execute("INSERT INTO id_reviews VALUES (10, 'reassigned', 'mbid-a', NULL, NULL, "
              "'2026-09-11 00:00:00', NULL, NULL)")
    c.commit()
    c.close()
    return lib, lis


def run_job(runner, kinds, timeout=120):
    job_id = runner.submit("apply-reviews", json.dumps({"kinds": kinds}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = runner._db()
        row = db.execute("SELECT state, result FROM jobs WHERE job_id=?1", (job_id,)).fetchone()
        db.close()
        if row and row["state"] in ("done", "failed", "stopped"):
            return job_id, row["state"], json.loads(row["result"] or "{}")
        time.sleep(0.25)
    return job_id, "timeout", {}


def events(runner, job_id) -> list:
    db = runner._db()
    rows = db.execute("SELECT kind, stage, text FROM job_events WHERE job_id=?1 "
                      "ORDER BY event_id", (job_id,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


class FakePlayer:
    """Stands in for a co-resident Lempi, recording the order it was called."""

    def __init__(self):
        self.calls = []

    def install(self):
        self._saved = (lempi_control.pause_lempi, lempi_control.play_lempi,
                       lempi_control.reload_lempi_library)
        lempi_control.pause_lempi = lambda *a, **k: self.calls.append("pause") or True
        lempi_control.play_lempi = lambda *a, **k: self.calls.append("play") or True
        lempi_control.reload_lempi_library = lambda *a, **k: self.calls.append("reload") or True

    def restore(self):
        (lempi_control.pause_lempi, lempi_control.play_lempi,
         lempi_control.reload_lempi_library) = self._saved


def test_boundary_applies_and_brackets_the_player(tmp):
    print("a boundary edit lands, and the player is paused around the write")
    lib, lis = build(tmp, playing=1)
    runner = jobmod.Runner(lib, os.path.join(tmp, "library.console.db"))
    fake = FakePlayer()
    fake.install()
    try:
        job_id, state, result = run_job(runner, ["boundary"])
    finally:
        fake.restore()

    check(state == "done", f"job state was {state!r}")
    check((result.get("boundary") or {}).get("applied") == 1,
          f"expected 1 boundary applied, got {result.get('boundary')}")

    c = sqlite3.connect(lib)
    row = c.execute("SELECT start_ms, end_ms, lead_out_ms, fade_out_ms, boundary_src "
                    "FROM passages WHERE passage_id=10").fetchone()
    c.close()
    check(row == (100, 208000, 900, 150, 'manual'),
          f"the catalogue half must carry the new span, got {row}")

    c = sqlite3.connect(lis)
    stamped = c.execute("SELECT applied_at FROM boundary_reviews WHERE passage_id=10").fetchone()[0]
    pending_id = c.execute("SELECT applied_at FROM id_reviews WHERE passage_id=10").fetchone()[0]
    c.close()
    check(stamped is not None, "the listener half must be stamped applied_at")
    check(pending_id is None,
          "an id review must NOT be applied when only 'boundary' was asked for")

    check(fake.calls == ["pause", "reload", "play"],
          f"pause -> write -> reload -> resume, in that order; got {fake.calls}")

    stages = [e["text"] for e in events(runner, job_id) if e["kind"] == "stage"]
    check(stages == ["pause", "boundary", "reload", "resume"], f"stages were {stages}")
    log = " ".join(e["text"] or "" for e in events(runner, job_id) if e["kind"] == "log")
    check("boundary edit(s) written" in log, f"the summary must say what was written: {log[-200:]!r}")


def test_a_stopped_player_is_left_stopped(tmp):
    print()
    print("a player that was not playing is not started by applying")
    lib, _ = build(tmp, playing=0)
    runner = jobmod.Runner(lib, os.path.join(tmp, "library.console.db"))
    fake = FakePlayer()
    fake.install()
    try:
        job_id, state, _ = run_job(runner, ["boundary"])
    finally:
        fake.restore()
    check(state == "done", f"job state was {state!r}")
    check(fake.calls == ["pause", "reload"],
          f"no resume for a player that was already stopped; got {fake.calls}")
    log = " ".join(e["text"] or "" for e in events(runner, job_id) if e["kind"] == "log")
    check("left stopped, as it was found" in log, f"and it must say so: {log!r}")


def test_no_kinds_is_refused(tmp):
    print()
    print("a job with no kinds named writes nothing")
    lib, lis = build(tmp, playing=0)
    runner = jobmod.Runner(lib, os.path.join(tmp, "library.console.db"))
    _, state, _ = run_job(runner, [])
    check(state == "failed", f"expected failed, got {state!r}")
    c = sqlite3.connect(lis)
    still = c.execute("SELECT applied_at FROM boundary_reviews WHERE passage_id=10").fetchone()[0]
    c.close()
    check(still is None, "and nothing may have been stamped")


def main() -> int:
    for fn in (test_boundary_applies_and_brackets_the_player,
               test_a_stopped_player_is_left_stopped,
               test_no_kinds_is_refused):
        with tempfile.TemporaryDirectory() as tmp:
            fn(tmp)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs apply_reviews: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
