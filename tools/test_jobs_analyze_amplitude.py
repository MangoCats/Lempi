#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `analyze-amplitude` job kind `[SPEC-SA-075]`.

The DSP itself is `test_analyze_amplitude.py`'s job; this checks the layer
above it -- that the job kind reaches `analyze_amplitude.py` with the right
argv (`--folder` only when a folder was actually given), and that it is
named in `SKIPPED` rather than silently run by `induct`/`reanalyze` -- the
same posture `test_jobs_reanalyze.py`/`test_console_release.py` already use
for their own job kinds: a real `Runner`, `_spawn` faked.

    python tools/test_jobs_analyze_amplitude.py
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


def test_skipped_names_it() -> None:
    print("SKIPPED names 'amplitude' with a reason, the same as segment/releases/cover art")
    names = [s for s, _ in jobmod.SKIPPED]
    check("amplitude" in names, f"got {names}")


def test_folder_scoped(tmp: str) -> None:
    print("a folder target reaches analyze_amplitude.py's own --folder flag")
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
        return 0, '{"ok": true, "analyzed": 3, "failed": 0, "quiet": 0, "clipped": 0}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    job_id = runner.submit("analyze-amplitude", "C:/Music/Foghat/The Best of Foghat")
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("analyze_amplitude.py" in argv[1], f"got {argv}")
    check(db in argv, f"got {argv}")
    check("--folder" in argv and argv[argv.index("--folder") + 1] == "C:/Music/Foghat/The Best of Foghat",
          f"got {argv}")
    check(j["result"]["analyzed"] == 3, f"got {j['result']}")


def test_library_wide_when_target_empty(tmp: str) -> None:
    print("an empty target (library-wide) omits --folder entirely")
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
        return 0, '{"ok": true, "analyzed": 0, "failed": 0, "quiet": 0, "clipped": 0}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    job_id = runner.submit("analyze-amplitude", "")
    wait_for(runner, job_id)
    check("--folder" not in seen["argv"], f"got {seen['argv']}")


def main() -> int:
    test_skipped_names_it()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_folder_scoped(tmp)
        test_library_wide_when_target_empty(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs analyze_amplitude: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
