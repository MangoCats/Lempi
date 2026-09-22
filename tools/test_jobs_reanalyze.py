#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `reanalyze` kind `[SPEC-SUI-214]`.

`fingerprint_ids.py` already had `--recheck`; nothing before this wired it to
a caller, so `steps_for(..., recheck=True)`'s identify stage carrying that
flag is the whole feature. Checked two ways: a direct unit check on
`steps_for()` itself (no I/O), and an integration check that `kind='reanalyze'`
actually reaches it with `recheck=True` while `kind='induct'` (and anything
else unrecognized) reaches it with `recheck=False` -- through the real
`Runner`/worker thread, `_spawn` faked, the same posture
`test_jobs_remote_pull.py` already uses.

    python tools/test_jobs_reanalyze.py
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


def test_steps_for_unit() -> None:
    print("steps_for(): --recheck reaches only the identify stage, only when asked")
    plain = jobmod.steps_for("db.sqlite", "C:/Music/Foghat")
    recheck = jobmod.steps_for("db.sqlite", "C:/Music/Foghat", recheck=True)
    check([s for s, _ in plain] == ["ingest", "extract", "identify", "merge"],
          f"stage names/order must be unchanged, got {[s for s, _ in plain]}")
    check([s for s, _ in recheck] == [s for s, _ in plain],
          "recheck must not add or remove stages, only alter one's argv")

    plain_identify = dict(plain)["identify"]
    recheck_identify = dict(recheck)["identify"]
    check("--recheck" not in plain_identify, f"plain induct must not recheck, got {plain_identify}")
    check("--recheck" in recheck_identify, f"reanalyze must recheck, got {recheck_identify}")

    for stage in ("ingest", "extract", "merge"):
        check(dict(plain)[stage] == dict(recheck)[stage],
              f"{stage}'s argv must be identical either way, got "
              f"{dict(plain)[stage]!r} vs {dict(recheck)[stage]!r}")


def wait_for(runner, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = runner.job(job_id)
        if j and j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def test_dispatch_integration(tmp: str) -> None:
    print("through the real Runner: kind='reanalyze' recheck=True, kind='induct' recheck=False")
    db = os.path.join(tmp, "lib.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, "lib.console.db")
    runner = jobmod.Runner(db, sidecar)

    seen = {}

    def fake_spawn(self, job_id, stage, argv):
        if stage == "identify":
            seen[job_id] = argv
        return 0, ""

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    induct_id = runner.submit("induct", "C:/Music/Foghat")
    wait_for(runner, induct_id)
    reanalyze_id = runner.submit("reanalyze", "C:/Music/Foghat")
    wait_for(runner, reanalyze_id)

    check(induct_id in seen, "induct must have reached the identify stage")
    check("--recheck" not in seen.get(induct_id, []),
          f"induct must not recheck, got {seen.get(induct_id)}")
    check(reanalyze_id in seen, "reanalyze must have reached the identify stage")
    check("--recheck" in seen.get(reanalyze_id, []),
          f"reanalyze must recheck, got {seen.get(reanalyze_id)}")


def main() -> int:
    test_steps_for_unit()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_dispatch_integration(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs reanalyze: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
