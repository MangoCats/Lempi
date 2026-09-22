#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `segment-dao` job kind `[SPEC024]`.

The cascade itself is `test_segment_dao.py`/`test_segment_cascade.py`'s job;
this checks the layer above it -- that the job kind reaches `segment_dao.py`
with the right argv (both `--expect` shapes, bare count and duration list),
and that it is named in `SKIPPED` rather than silently run by
`induct`/`reanalyze` -- the same posture `test_jobs_analyze_amplitude.py`
already uses for its own job kind: a real `Runner`, `_spawn` faked.

    python tools/test_jobs_segment_dao.py
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


def test_skipped_names_it() -> None:
    print("SKIPPED still names 'segment', now noting the job kind that reaches it")
    names = [s for s, _ in jobmod.SKIPPED]
    check("segment" in names, f"got {names}")
    reason = dict(jobmod.SKIPPED)["segment"]
    check("SPEC024" in reason, f"got {reason!r}")


def _runner(tmp: str, name: str) -> "jobmod.Runner":
    db = os.path.join(tmp, f"{name}.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, f"{name}.console.db")
    return jobmod.Runner(db, sidecar), db


def test_bare_count_reaches_expect(tmp: str) -> None:
    print("a bare-count target reaches segment_dao.py's own --expect unchanged")
    runner, db = _runner(tmp, "lib1")
    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, '{"ok": true, "tracks": 12, "stage": "grid", "confidence": 1.0}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"file": "C:/Music/Foghat/dao.mp3", "expect": 12})
    job_id = runner.submit("segment-dao", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("segment_dao.py" in argv[1], f"got {argv}")
    check(db in argv, f"got {argv}")
    check("--file" in argv and argv[argv.index("--file") + 1] == "C:/Music/Foghat/dao.mp3",
          f"got {argv}")
    check("--expect" in argv and argv[argv.index("--expect") + 1] == "12", f"got {argv}")
    check("--commit" in argv and "--json" in argv, f"got {argv}")
    check(j["result"]["stage"] == "grid", f"got {j['result']}")


def test_duration_list_reaches_expect(tmp: str) -> None:
    print("a duration-list target reaches --expect as the same comma-separated string")
    runner, db = _runner(tmp, "lib2")
    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, '{"ok": true, "tracks": 3, "stage": "dp", "confidence": 1.0}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"file": "C:/Music/x/dao.mp3", "expect": "245,198,312"})
    job_id = runner.submit("segment-dao", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("--expect" in argv and argv[argv.index("--expect") + 1] == "245,198,312",
          f"got {argv}")
    check(j["result"]["stage"] == "dp", f"got {j['result']}")


def main() -> int:
    test_skipped_names_it()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_bare_count_reaches_expect(tmp)
        test_duration_list_reaches_expect(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs segment_dao: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
