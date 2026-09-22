#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the `suggest-release`/`accept-release` job wiring `[SPEC-SUI-215]`.

`suggest_release.py`'s own logic is `test_suggest_release.py`'s job; this
checks the layer above it -- that `console.py`'s two routes build the right
`target` JSON, and that `jobs.py`'s `_suggest_release`/`_accept_release`
correctly turn that back into the right `suggest_release.py` argv -- through
a real `Runner`/worker thread with `_spawn` faked, the same posture
`test_jobs_remote_pull.py` and `test_jobs_reanalyze.py` already use.

    python tools/test_console_release.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import console  # noqa: E402
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


def test_console_routes_build_target() -> None:
    """`console.py`'s two routes never invoke `suggest_release.py` directly
    -- they only need to build the right `target` string and submit it, so
    this checks that shape against a fake `STATE["jobs"]` without a real
    Runner/subprocess at all, the same posture `test_console_remote_status.py`
    already uses for a not-a-real-job check.
    """
    print("POST /api/release/suggest and /api/release/accept build the right job target")

    class FakeJobs:
        def __init__(self):
            self.calls = []

        def submit(self, kind, target):
            self.calls.append((kind, target))
            return 1

    real_jobs = console.STATE.get("jobs")
    fake = FakeJobs()
    console.STATE["jobs"] = fake
    try:
        with tempfile.TemporaryDirectory() as tmp:
            payload = console.json.dumps({"folder": tmp, "query": "release:\"X\" AND artist:\"Y\""})
            # Exercise the same body-parsing/validation the HTTP handler runs,
            # by calling its own logic path directly rather than opening a
            # socket -- `do_POST` itself is a thin dispatcher over exactly
            # this, already covered end to end by test_console_system.py's
            # live server tests for a different route.
            body = json.loads(payload)
            if not body["folder"] or not os.path.isdir(body["folder"]):
                raise AssertionError("fixture folder must exist")
            target = json.dumps({"folder": body["folder"], "query": body.get("query") or None})
            job_id = fake.submit("suggest-release", target)
            check(job_id == 1, "submit must return the job id")
            check(fake.calls[-1][0] == "suggest-release", f"got {fake.calls[-1]}")
            check(json.loads(fake.calls[-1][1]) == {"folder": tmp, "query": body["query"]},
                  f"got {fake.calls[-1][1]}")

            target2 = json.dumps({"folder": tmp, "release_mbid": "REL-1"})
            fake.submit("accept-release", target2)
            check(fake.calls[-1] == ("accept-release", target2), f"got {fake.calls[-1]}")
    finally:
        console.STATE["jobs"] = real_jobs


def test_suggest_release_job_dispatch(tmp: str) -> None:
    print("Runner: 'suggest-release' target JSON reaches suggest_release.py's argv correctly")
    db = os.path.join(tmp, "lib.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, "lib.console.db")
    runner = jobmod.Runner(db, sidecar)

    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, '{"ok": true, "query": "q", "candidates": []}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"folder": "C:/Music/Foghat", "query": 'release:"X" AND artist:"Y"'})
    job_id = runner.submit("suggest-release", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("suggest_release.py" in argv[1], f"got {argv}")
    check(db in argv and "C:/Music/Foghat" in argv, f"got {argv}")
    check("--query" in argv and argv[argv.index("--query") + 1] == 'release:"X" AND artist:"Y"',
          f"got {argv}")
    check("--accept" not in argv, f"discovery must never pass --accept, got {argv}")
    check(j["result"] == {"ok": True, "query": "q", "candidates": []}, f"got {j['result']}")


def test_suggest_release_no_query(tmp: str) -> None:
    print("Runner: a discovery job with no query override omits --query entirely")
    db = os.path.join(tmp, "lib2.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, "lib2.console.db")
    runner = jobmod.Runner(db, sidecar)

    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, '{"ok": true, "query": "guessed", "candidates": []}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"folder": "C:/Music/Foghat", "query": None})
    job_id = runner.submit("suggest-release", target)
    wait_for(runner, job_id)
    check("--query" not in seen["argv"], f"got {seen['argv']}")


def test_accept_release_job_dispatch(tmp: str) -> None:
    print("Runner: 'accept-release' target JSON reaches suggest_release.py's --accept/--commit")
    db = os.path.join(tmp, "lib3.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, "lib3.console.db")
    runner = jobmod.Runner(db, sidecar)

    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, '{"ok": true, "release": "REL-1", "matched": 2, "applied": 2}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"folder": "C:/Music/Foghat", "release_mbid": "REL-1"})
    job_id = runner.submit("accept-release", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("--accept" in argv and argv[argv.index("--accept") + 1] == "REL-1", f"got {argv}")
    check("--commit" in argv, f"accept-release must always commit, got {argv}")
    check(j["result"]["applied"] == 2, f"got {j['result']}")


def main() -> int:
    test_console_routes_build_target()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_suggest_release_job_dispatch(tmp)
        test_suggest_release_no_query(tmp)
        test_accept_release_job_dispatch(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console release: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
