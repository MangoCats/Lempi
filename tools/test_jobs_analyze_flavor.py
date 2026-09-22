#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `analyze-flavor` job kind.

Extraction itself is `extract_library.py`'s own job; this checks the layer
above it -- that the job kind reaches `extract_library.py` with a
`--passage` flag carrying the target passage id, the same shape
`test_jobs_analyze_amplitude.py` already uses for its own job kind: a real
`Runner`, `_spawn` faked.

    python tools/test_jobs_analyze_flavor.py
"""

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


def test_reaches_extract_library_with_the_passage_id(tmp: str) -> None:
    print("the passage id reaches extract_library.py's own --passage flag")
    db = os.path.join(tmp, "lib1.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, "lib1.console.db")
    runner = jobmod.Runner(db, sidecar)

    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, "1 files, 0 cached, 1 to extract, 4 jobs\n1 extracted, 0 failed"

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    job_id = runner.submit("analyze-flavor", "16212")
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("extract_library.py" in argv[1], f"got {argv}")
    check(db in argv, f"got {argv}")
    check("--passage" in argv and argv[argv.index("--passage") + 1] == "16212",
          f"got {argv}")


def test_a_failed_extract_is_reported_failed(tmp: str) -> None:
    print("a nonzero exit is reported as a failed job, not silently done")
    db = os.path.join(tmp, "lib2.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, "lib2.console.db")
    runner = jobmod.Runner(db, sidecar)

    def fake_spawn(self, job_id, stage, argv):
        return 1, "no such passage"

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    job_id = runner.submit("analyze-flavor", "999999")
    j = wait_for(runner, job_id)
    check(j["state"] == "failed", f"got {j}")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_reaches_extract_library_with_the_passage_id(tmp)
        test_a_failed_extract_is_reported_failed(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs analyze_flavor: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
