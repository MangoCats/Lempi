#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `mesh-diff` job kind `[SPEC-MESH-096]`.

`mesh_diff.py` itself is `test_mesh_diff.py`'s job; this checks the layer
above it, the same way `test_jobs_sync_preferences.py` does for its own
job kind -- that the job reaches the tool with the right argv, as one
subprocess call, and that a report containing conflicts still finishes
`done`, not `failed` `[SPEC-MESH-038]`.

    python tools/test_jobs_mesh_diff.py
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
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, kind TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
CREATE TABLE id_checks (passage_id INTEGER);
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def wait_for(runner, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = runner.job(job_id)
        if j and j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def _runner(tmp: str) -> tuple:
    db = os.path.join(tmp, "lib.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, "lib.console.db")
    return jobmod.Runner(db, sidecar), db


def test_target_reaches_mesh_diff_and_a_conflict_still_finishes_done(tmp: str) -> None:
    runner, db = _runner(tmp)
    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, json.dumps({"ok": True, "tables": {
            "recordings": {"local_only": [], "peer_only": [], "agree": 0,
                           "differ": [], "conflict": [{"key": ["m1"]}]},
        }})

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    job_id = runner.submit("mesh-diff", "pi@bose:/var/lempi/listener.db")
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"a conflict in the result must not fail the job, got {j}")
    argv = seen["argv"]
    check("mesh_diff.py" in argv[1], f"got {argv}")
    check(db in argv, f"got {argv}")
    check("pi@bose:/var/lempi/listener.db" in argv, f"got {argv}")
    check("--json" in argv, f"got {argv}")
    check(len(j["result"]["tables"]["recordings"]["conflict"]) == 1, f"got {j['result']}")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_target_reaches_mesh_diff_and_a_conflict_still_finishes_done(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs mesh_diff: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
