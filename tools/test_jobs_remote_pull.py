#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `_remote_pull` wiring `[SPEC-DF-119]`.

Runs the real `Runner` and its real background worker thread, with only the
one stage that would otherwise shell out to `ssh` (`fetch-flags`) faked --
everything downstream (`import_flags.py`, the job's own `result`, its final
state) is the real subprocess pipeline, exercised end to end the same way
`test_flag_sync.py` already does for the tools directly. What is under test
here is specifically that `remote_flags.py` replaced the old `scp` + copy +
`export_flags.py` three-stage fetch with one `fetch-flags` stage, wired
correctly into the rest of the pull.

    python tools/test_jobs_remote_pull.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs as jobmod  # noqa: E402

SCHEMA = """
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE listener_flags (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    flagged_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID;
-- Empty, but present -- jobs.py's own counts() queries these unconditionally
-- before dispatching to any job kind, the same "never touches a missing
-- table" discipline as everywhere else in this console.
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, kind TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
CREATE TABLE id_checks (passage_id INTEGER);
"""

SONG_A = "11111111-1111-1111-1111-111111111111"

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def build_library(path: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO recordings VALUES (?,'A Song',NULL,'inherited:mulib')", (SONG_A,))
    c.commit()
    c.close()


def wait_for(runner, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = runner.job(job_id)
        if j and j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def fake_spawn_success(remote_flags_json):
    """Stands in for the one stage that would otherwise run `ssh` -- writes
    the `flags.json` `remote_flags.py --json` would have, everything else
    (`import`) runs for real.
    """
    def _spawn(self, job_id, stage, argv):
        if stage == "fetch-flags":
            check("remote_flags.py" in argv[1], f"fetch-flags must run remote_flags.py, got {argv}")
            out_path = argv[argv.index("-o") + 1]
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(remote_flags_json, f)
            return 0, f"{len(remote_flags_json['flags'])} flag(s) exported to {out_path}"
        return jobmod.Runner._spawn(self, job_id, stage, argv)
    return _spawn


def fake_spawn_unreachable(self, job_id, stage, argv):
    if stage == "fetch-flags":
        return 1, "could not reach pi@lempipi:/srv/library/library.db: no route to host"
    raise AssertionError(f"stage {stage!r} must never run after fetch-flags fails")


def test_pull_lands_a_flag(tmp: str) -> None:
    print("a successful fetch-flags feeds import_flags.py, and the flag lands for real")
    library = os.path.join(tmp, "library.db")
    build_library(library)
    sidecar = os.path.join(tmp, "library.console.db")
    runner = jobmod.Runner(library, sidecar)
    inner = fake_spawn_success({"format_version": 1, "flags": [
        {"subject_kind": "recording", "anchor": {"recording_mbid": SONG_A},
         "flagged_at": "2026-08-29 09:00:00", "origin": "lempipi"},
    ]})
    runner._spawn = inner.__get__(runner, jobmod.Runner)
    job_id = runner.submit("remote-pull", "pi@lempipi:/srv/library/library.db")
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"expected done, got {j}")
    check(j["result"]["matched"] == 1, f"expected 1 matched flag, got {j['result']}")
    stages = [e["stage"] for e in j["events"] if e["kind"] == "stage"]
    check(stages == ["fetch-flags", "import"], f"expected exactly these two stages, got {stages}")
    check("scp" not in json.dumps(j["events"]), "no scp/copy step should appear anywhere in this job's log")

    c = sqlite3.connect(library)
    row = c.execute("SELECT flagged_at, origin FROM listener_flags WHERE subject_kind='recording' "
                    "AND subject_id=?", (SONG_A,)).fetchone()
    c.close()
    check(row == ("2026-08-29 09:00:00", "lempipi"), f"the flag must actually be committed, got {row}")


def test_unreachable_fails_before_import(tmp: str) -> None:
    print("fetch-flags failing (an unreachable remote) fails the job before import ever runs")
    library = os.path.join(tmp, "library2.db")
    build_library(library)
    sidecar = os.path.join(tmp, "library2.console.db")
    runner = jobmod.Runner(library, sidecar)
    runner._spawn = fake_spawn_unreachable.__get__(runner, jobmod.Runner)
    job_id = runner.submit("remote-pull", "pi@lempipi:/srv/library/library.db")
    j = wait_for(runner, job_id)
    check(j["state"] == "failed", f"expected failed, got {j}")
    c = sqlite3.connect(library)
    n = c.execute("SELECT count(*) FROM listener_flags").fetchone()[0]
    c.close()
    check(n == 0, "nothing must land when the fetch itself never succeeded")


def main() -> int:
    # `ignore_cleanup_errors`: each `Runner` leaves a daemon worker thread
    # parked on its own queue forever (by design -- the same as the real
    # console process), which can hold a Windows file lock on its own
    # sqlite sidecar a beat longer than this function runs. Harmless here;
    # nothing after this reads the directory.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_pull_lands_a_flag(tmp)
        test_unreachable_fails_before_import(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs remote_pull: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
