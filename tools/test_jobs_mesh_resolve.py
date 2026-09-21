#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `mesh-resolve` job kind `[SPEC-MESH-098]`.

`resolve_mesh_conflict.py` itself is `test_resolve_mesh_conflict.py`'s job;
this checks that the JSON-packed target (`{peer, table, key, choice}` or
`{peer, table, key, value}`) reaches it as the right argv, the same layer
`test_jobs_sync_preferences.py` checks for its own job kind.

    python tools/test_jobs_mesh_resolve.py
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


def test_choice_target_builds_the_right_argv(tmp: str) -> None:
    runner, db = _runner(tmp)
    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, '{"ok": true, "table": "recordings", "key": ["m1"], ' \
                   '"local_written": false, "peer_written": true}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"peer": "pi@bose:/var/lempi/listener.db", "table": "recordings",
                          "key": ["m1"], "choice": "local"})
    job_id = runner.submit("mesh-resolve", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("resolve_mesh_conflict.py" in argv[1], f"got {argv}")
    check(db in argv, f"got {argv}")
    check("pi@bose:/var/lempi/listener.db" in argv, f"got {argv}")
    check("--table" in argv and "recordings" in argv, f"got {argv}")
    check("--key" in argv and json.dumps(["m1"]) in argv, f"got {argv}")
    check("--choice" in argv and "local" in argv, f"got {argv}")
    check("--value" not in argv, f"a --choice target must not also pass --value: {argv}")
    check("--commit" in argv and "--json" in argv, f"got {argv}")


def test_value_target_builds_the_right_argv(tmp: str) -> None:
    runner, db = _runner(tmp)
    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, '{"ok": true}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"peer": "pi@bose:/var/lempi/listener.db", "table": "recordings",
                          "key": ["m1"], "value": {"title": "Chosen", "length_ms": None}})
    job_id = runner.submit("mesh-resolve", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("--value" in argv, f"got {argv}")
    check(json.dumps({"title": "Chosen", "length_ms": None}) in argv, f"got {argv}")
    check("--choice" not in argv, f"a --value target must not also pass --choice: {argv}")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        d1 = os.path.join(tmp, "1")
        os.makedirs(d1)
        test_choice_target_builds_the_right_argv(d1)
        d2 = os.path.join(tmp, "2")
        os.makedirs(d2)
        test_value_target_builds_the_right_argv(d2)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs mesh_resolve: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
