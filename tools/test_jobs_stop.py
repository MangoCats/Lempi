#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for stopping a running job `[REQ-LIB-132]`, with real processes: a
stage killed with everything it started; a cooperative stage that stops
itself when asked, and one killed after its grace when it does not; a step
that must not be left half-done let finish, and the job stopped after it.

    python tools/test_jobs_stop.py
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


def wait_for(runner, job_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = runner.job(job_id)
        if j and j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def wait_running(runner, job_id, stage_marker, timeout=15.0):
    """Until the stage has started and written its marker."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(stage_marker):
            return
        time.sleep(0.05)
    raise TimeoutError("the stage never started")


def alive(pid: int) -> bool:
    if os.name == "nt":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# A stage that starts a child which outlives it unless the whole tree is
# stopped, and writes both pids where the test can read them.
TREE = """
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
open(sys.argv[1], "w").write(f"{os.getpid()} {child.pid}")
time.sleep(120)
"""

# A stage that stops itself when the stop file appears, as send_bundle does.
COOPERATIVE = """
import os, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
stop = os.environ["LEMPI_STOP_FILE"]
for _ in range(1200):
    if os.path.exists(stop):
        print("STOPPED: asked, and cleaned up")
        sys.exit(3)
    time.sleep(0.1)
"""

# One that never looks, and must be killed after its grace.
DEAF = """
import os, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
time.sleep(120)
"""

# An all-or-nothing step: takes a moment, and records that it finished.
ATOMIC = """
import os, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
time.sleep(2)
open(sys.argv[1] + ".done", "w").write("whole")
"""


def runner_for(tmp, name, kind_stage, script):
    db = os.path.join(tmp, f"{name}.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    r = jobmod.Runner(db, os.path.join(tmp, f"{name}.console.db"))
    src = os.path.join(tmp, f"{name}.py")
    with open(src, "w") as fh:
        fh.write(script)
    marker = os.path.join(tmp, f"{name}.pid")
    stage = kind_stage[1]

    def run(job_id, kind, target):
        db2 = r._db()
        db2.execute("UPDATE jobs SET state='running' WHERE job_id=?1", (job_id,))
        db2.commit()
        db2.close()
        code, _ = r._spawn(job_id, stage, [sys.executable, src, marker])
        if code < 0:
            return r._finish(job_id, "stopped")
        # The step after the one that ran: where an atomic stop takes effect.
        if r._stopped(job_id):
            return r._finish(job_id, "stopped")
        r._finish(job_id, "done" if code == 0 else "failed")
    r._run = run
    return r, marker


def test_kill_tree(tmp):
    print("an ordinary stage is stopped at once, with the child it started")
    r, marker = runner_for(tmp, "tree", ("analyze-amplitude", "analyze"), TREE)
    job = r.submit("analyze-amplitude", "")
    wait_running(r, job, marker)
    time.sleep(0.3)
    me, child = (int(x) for x in open(marker).read().split())
    check(r.stop(job), "stop says it stopped it")
    j = wait_for(r, job)
    time.sleep(0.5)
    check(j["state"] == "stopped", f"recorded as stopped, not failed: {j['state']}")
    check(not alive(me) and not alive(child), f"the stage and its child are gone: {alive(me)}, {alive(child)}")


def test_cooperative(tmp):
    print("a cooperative stage stops itself when asked, and is not killed")
    r, marker = runner_for(tmp, "coop", ("send-bundle", "send"), COOPERATIVE)
    job = r.submit("send-bundle", "")
    wait_running(r, job, marker)
    r.stop(job)
    j = wait_for(r, job)
    texts = [e["text"] or "" for e in j["events"]]
    check(j["state"] == "stopped", f"stopped: {j['state']}")
    check(any("asked, and cleaned up" in t for t in texts), f"it stopped itself: {texts}")
    check(not any(e["kind"] == "error" for e in j["events"]), "and nothing had to be killed")


def test_cooperative_deaf(tmp):
    print("a cooperative stage that does not stop is killed after its grace, loudly")
    old = dict(jobmod.STOP_COOPERATIVE)
    jobmod.STOP_COOPERATIVE[("send-bundle", "send")] = 2
    try:
        r, marker = runner_for(tmp, "deaf", ("send-bundle", "send"), DEAF)
        job = r.submit("send-bundle", "")
        wait_running(r, job, marker)
        r.stop(job)
        j = wait_for(r, job)
    finally:
        jobmod.STOP_COOPERATIVE.clear()
        jobmod.STOP_COOPERATIVE.update(old)
    errors = [e["text"] for e in j["events"] if e["kind"] == "error"]
    check(j["state"] == "stopped" and any("did not stop itself within 2 s" in t for t in errors),
          f"killed, and said so: {j['state']} {errors}")


def test_atomic(tmp):
    print("an all-or-nothing step is let finish, and the job stops after it")
    r, marker = runner_for(tmp, "atomic", ("cd-rip", "ingest"), ATOMIC)
    job = r.submit("cd-rip", "")
    wait_running(r, job, marker)
    r.stop(job)
    j = wait_for(r, job)
    texts = [e["text"] or "" for e in j["events"]]
    check(os.path.exists(marker + ".done"), "the step finished whole")
    check(j["state"] == "stopped", f"and the job stopped after it: {j['state']}")
    check(any("is let finish" in t for t in texts), f"the log says why it waited: {texts}")


def test_policy():
    print("the policy names the send, a CD add's move and a sync")
    check(jobmod.stop_policy("send-bundle", "send")[0] == "cooperative", "a send stops itself")
    check(jobmod.stop_policy("send-bundle", "check")[0] == "kill", "its dry run is simply stopped")
    check(jobmod.stop_policy("cd-rip", "ingest")[0] == "atomic", "a CD add's move is let finish")
    check(jobmod.stop_policy("cd-rip", "flavor")[0] == "kill", "its analysis is not")
    check(jobmod.stop_policy("star-sync", "commit")[0] == "atomic", "a sync is let finish")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_policy()
        test_kill_tree(tmp)
        test_cooperative(tmp)
        test_cooperative_deaf(tmp)
        test_atomic(tmp)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs stop: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
