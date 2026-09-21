#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `cd-rip` job kind `[SPEC025..028]`.

`ingest_cd.py` itself is `test_cd_toc.py`/`test_ingest_cd.py`'s job; this
checks the layer above it -- the job kind reaches it with the right argv,
and it is named in `SKIPPED` rather than silently run by
`induct`/`reanalyze`, the same posture `test_jobs_segment_dao.py` and
`test_jobs_analyze_amplitude.py` already use for their own job kinds: a
real `Runner`, `_spawn` faked.

    python tools/test_jobs_cd_rip.py
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
    print("SKIPPED names 'cd-rip', the job kind that reaches ingest_cd.py")
    names = [s for s, _ in jobmod.SKIPPED]
    check("cd-rip" in names, f"got {names}")
    reason = dict(jobmod.SKIPPED)["cd-rip"]
    check("SPEC-RIP-088" in reason, f"got {reason!r}")


def _runner(tmp: str, name: str) -> "jobmod.Runner":
    db = os.path.join(tmp, f"{name}.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, f"{name}.console.db")
    return jobmod.Runner(db, sidecar), db


def test_folder_reaches_ingest_cd(tmp: str) -> None:
    print("a folder target reaches ingest_cd.py's own --folder unchanged")
    runner, db = _runner(tmp, "lib1")
    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, ('{"ok": true, "tracks": 14, "identified": 14, "ambiguous": 0, '
                    '"unidentified": 0, "verification_failed": 0, "disc_outcome": "exact", '
                    '"candidates": 1}')

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"folder": "C:/rips/some-disc"})
    job_id = runner.submit("cd-rip", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("ingest_cd.py" in argv[1], f"got {argv}")
    check(db in argv, f"got {argv}")
    check("--folder" in argv and argv[argv.index("--folder") + 1] == "C:/rips/some-disc",
          f"got {argv}")
    check("--commit" in argv and "--json" in argv, f"got {argv}")
    check(j["result"]["disc_outcome"] == "exact", f"got {j['result']}")


def test_failure_surfaces_as_failed(tmp: str) -> None:
    print("ingest_cd.py's own {\"ok\": false, ...} fails the job, not a crash")
    runner, db = _runner(tmp, "lib2")

    def fake_spawn(self, job_id, stage, argv):
        return 1, '{"ok": false, "error": "no .cue or .toc file found"}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"folder": "C:/rips/empty"})
    job_id = runner.submit("cd-rip", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "failed", f"got {j}")
    check(j["result"]["error"] == "no .cue or .toc file found", f"got {j['result']}")


def main() -> int:
    test_skipped_names_it()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_folder_reaches_ingest_cd(tmp)
        test_failure_surfaces_as_failed(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs cd_rip: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
