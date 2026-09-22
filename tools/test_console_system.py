#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `console.py`'s `/system` page `[SPEC-SUI-210..212]`.

`build_info()`/`system_status()` are tested in process, the same way
`test_console_flags.py` already tests `console.flags()`. The shutdown
route itself is tested against a real spawned `console.py` process on a
scratch port and a scratch fixture database -- proving the server actually
stops, and that a running job actually refuses it -- not assumed from the
handler's own logic reading correctly.

    python tools/test_console_system.py
"""

import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONSOLE = os.path.join(HERE, "console.py")
sys.path.insert(0, HERE)
import console  # noqa: E402
import jobs as jobmod  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


def test_build_info_against_this_real_repo() -> None:
    print("build_info() reads this repo's own commit for real")
    b = console.build_info(console.REPO_ROOT)
    check(b["available"] is True, f"this checkout is a real git repo, got {b}")
    check(len(b["commit"]) == 40 and all(c in "0123456789abcdef" for c in b["commit"]),
          f"expected a full 40-char hex commit, got {b['commit']!r}")
    check(b["commit"].startswith(b["commit_short"]), f"got {b}")
    check(bool(b["branch"]), f"expected a branch name, got {b}")
    check(bool(b["commit_date"]), f"expected an ISO commit date, got {b}")
    check(b["dirty"] in (True, False), f"a real repo must resolve dirty to a bool, got {b['dirty']}")


def test_build_info_not_a_repo() -> None:
    print("build_info() against a directory with no git repo degrades plainly, not a crash")
    with tempfile.TemporaryDirectory() as tmp:
        b = console.build_info(tmp)
        check(b == {"available": False}, f"got {b}")


def test_system_status_shape() -> None:
    print("system_status() with no active job reports active_job=None")

    class FakeJobsIdle:
        current = None

    console.STATE.update({"build": {"available": False}, "pid": None, "started_at": "2026-08-29T23:00:00",
                          "port": 5730, "path": "C:/lib.db", "roots": [], "jobs": FakeJobsIdle()})
    s = console.system_status()
    check(s["active_job"] is None, f"got {s}")
    check(s["port"] == 5730 and s["db_path"] == "C:/lib.db", f"got {s}")

    print("system_status() with a running job reports its kind/id/state")

    class FakeJobsBusy:
        current = 7

        def job(self, job_id):
            return {"job_id": 7, "kind": "remote-push", "target": "pi@lempi02w:/x", "state": "running"}

    console.STATE["jobs"] = FakeJobsBusy()
    s = console.system_status()
    check(s["active_job"] == {"job_id": 7, "kind": "remote-push", "target": "pi@lempi02w:/x", "state": "running"},
          f"got {s}")


# -- live: a real spawned console.py, a real HTTP round trip -----------------

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT,
    size_bytes INTEGER, mtime REAL, format TEXT, duration_ms INTEGER,
    first_seen TEXT, last_seen TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT, start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT, weight REAL, source TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
CREATE TABLE id_checks (passage_id INTEGER);
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get_json(port, path, timeout=5):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, json.loads(r.read())
    finally:
        conn.close()


def post_json(port, path, timeout=5):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", path)
        r = conn.getresponse()
        return r.status, json.loads(r.read())
    finally:
        conn.close()


def wait_up(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            get_json(port, "/api/system", timeout=1)
            return True
        except (OSError, ConnectionError):
            time.sleep(0.1)
    return False


def wait_down(proc, timeout=10):
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def test_shutdown_live() -> None:
    print("a live console.py: /api/system reports this repo's build, and shutdown actually stops it")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "lib.db")
        c = sqlite3.connect(db)
        c.executescript(SCHEMA)
        c.commit()
        c.close()

        port = free_port()
        proc = subprocess.Popen([sys.executable, CONSOLE, db, "--port", str(port)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                cwd=console.REPO_ROOT)
        try:
            check(wait_up(port), "the console must come up within 10s")

            status, d = get_json(port, "/api/system")
            check(status == 200, f"got {status}")
            check(d["build"]["available"] is True, f"expected real build info, got {d['build']}")
            check(d["build"]["commit"] == console.build_info(console.REPO_ROOT)["commit"],
                  "the spawned process's own commit must match this checkout's HEAD")
            check(d["pid"] == proc.pid, f"expected pid {proc.pid}, got {d['pid']}")
            check(d["active_job"] is None, f"a fresh instance must report no active job, got {d}")

            status, d = post_json(port, "/api/system/shutdown")
            check(status == 200 and d.get("ok") is True, f"got {status} {d}")
            check(wait_down(proc, timeout=10), "the process must actually exit after shutdown")
            check(proc.returncode == 0, f"expected a clean exit, got {proc.returncode}")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


def test_shutdown_refused_while_a_job_runs() -> None:
    """The actual point of `[SPEC-SUI-212]`'s safety check, proven against a
    real running job rather than a faked `active_job` -- but deterministic:
    real network timing (a black-holed address, an unreachable host) varies
    by environment and made this flaky in practice. Instead, the one stage
    that would otherwise shell out to `ssh` is made to sleep a controlled,
    fixed span -- the same technique `test_jobs_remote_pull.py` already uses
    on `Runner._spawn` -- with a *real* HTTP server (`console.Server`, in a
    background thread of this process) and real socket round trips in front
    of it, so the routing and the shutdown mechanism are still exercised for
    real, only the slow part is not left to chance.
    """
    print("a real running job actually blocks shutdown, and finishing actually unblocks it")
    real_spawn = jobmod.Runner._spawn

    def slow_spawn(self, job_id, stage, argv):
        if stage == "fetch-flags":
            time.sleep(1.5)
            return 1, "simulated: unreachable"
        return real_spawn(self, job_id, stage, argv)

    # `ignore_cleanup_errors`: `console.STATE["db"]` (closed explicitly
    # below) and the `Runner`'s own daemon worker thread can each hold a
    # Windows file lock a beat past this function returning -- harmless,
    # nothing after this reads the directory.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "lib.db")
        c = sqlite3.connect(db)
        c.executescript(SCHEMA)
        c.commit()
        c.close()
        sidecar = os.path.join(tmp, "lib.console.db")

        console.STATE.update({
            "db": console.ro(db), "path": os.path.abspath(db), "roots": [],
            "scan": None, "scanned_at": 0, "port": free_port(),
            "started_at": "2026-08-29T23:00:00", "build": {"available": False},
        })
        jobmod.Runner._spawn = slow_spawn
        console.STATE["jobs"] = jobmod.Runner(console.STATE["path"], sidecar, roots=[])
        port = console.STATE["port"]
        httpd = console.Server(("127.0.0.1", port), console.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            check(wait_up(port), "the in-process server must come up")

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/api/remote", json.dumps({"remote": "nobody@lempi02w:/srv/lempi.db"}),
                         {"Content-Type": "application/json"})
            conn.getresponse().read()
            conn.close()
            status, d = post_json(port, "/api/remote/pull")
            check(status == 200 and "job_id" in d, f"got {status} {d}")
            job_id = d["job_id"]

            # The queued job may not have started yet on the worker thread --
            # poll briefly for 'running' before asserting the refusal, rather
            # than racing it.
            deadline = time.time() + 5
            while time.time() < deadline:
                _, j = get_json(port, f"/api/jobs/{job_id}")
                if j["state"] == "running":
                    break
                time.sleep(0.05)
            check(j["state"] == "running", f"expected the job to be running by now, got {j['state']}")

            status, d = post_json(port, "/api/system/shutdown")
            check(status == 409, f"expected a refusal while the pull is running, got {status} {d}")
            check("remote-pull" in d.get("error", ""), f"the refusal must name the job, got {d}")

            deadline = time.time() + 10
            state = j["state"]
            while time.time() < deadline and state == "running":
                _, j = get_json(port, f"/api/jobs/{job_id}")
                state = j["state"]
                time.sleep(0.1)
            check(state == "failed", f"the simulated fetch failure must fail the job, got {state}")

            status, d = post_json(port, "/api/system/shutdown")
            check(status == 200 and d.get("ok") is True, f"expected shutdown to succeed once idle, got {status} {d}")

            deadline = time.time() + 5
            stopped = False
            while time.time() < deadline:
                try:
                    get_json(port, "/api/system", timeout=1)
                    time.sleep(0.1)
                except (OSError, ConnectionError):
                    stopped = True
                    break
            check(stopped, "the server must actually stop accepting connections after shutdown")
        finally:
            jobmod.Runner._spawn = real_spawn
            try:
                httpd.shutdown()
            except Exception:
                pass
            if console.STATE["db"] is not None:
                console.STATE["db"].close()


def main() -> int:
    test_build_info_against_this_real_repo()
    test_build_info_not_a_repo()
    test_system_status_shape()
    test_shutdown_live()
    test_shutdown_refused_while_a_job_runs()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console system: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
