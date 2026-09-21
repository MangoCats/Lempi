#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `sync-preferences` job kind `[SPEC030]`.

`sync_preferences.py` itself is `test_sync_preferences.py`'s job; this
checks the layer above it -- that the job kind reaches it with the right
argv, as one subprocess call, and is not listed in `SKIPPED` (it is a
cross-installation maintenance action reached only from its own console
button, never something `induct`/`reanalyze` would otherwise attempt --
the same reason `remote-pull`/`remote-push` are absent from that list too).

    python tools/test_jobs_sync_preferences.py
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


def test_not_in_skipped() -> None:
    print("sync-preferences is not named in SKIPPED -- induct never touches it either way")
    names = [s for s, _ in jobmod.SKIPPED]
    check("sync-preferences" not in names, f"got {names}")


def _runner(tmp: str, name: str) -> "jobmod.Runner":
    db = os.path.join(tmp, f"{name}.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, f"{name}.console.db")
    return jobmod.Runner(db, sidecar), db


def test_target_reaches_sync_preferences(tmp: str) -> None:
    print("a remote target reaches sync_preferences.py's own argv, one subprocess call")
    runner, db = _runner(tmp, "lib1")
    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        seen["argv"] = argv
        return 0, '{"ok": true, "committed": true, "pull": 2, "push": 1, ' \
                   '"skipped_missing": 0, "ties": 0}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    job_id = runner.submit("sync-preferences", "pi@lempipi:/srv/library/library.db")
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen["argv"]
    check("sync_preferences.py" in argv[1], f"got {argv}")
    check(db in argv, f"got {argv}")
    check("pi@lempipi:/srv/library/library.db" in argv, f"got {argv}")
    check("--commit" in argv and "--json" in argv, f"got {argv}")
    check(j["result"]["pull"] == 2 and j["result"]["push"] == 1, f"got {j['result']}")


def main() -> int:
    test_not_in_skipped()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_target_reaches_sync_preferences(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs sync_preferences: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
