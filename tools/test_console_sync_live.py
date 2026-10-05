#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Sync page against a real `console.py` process [SPEC-STAR-120..128].

A run is prepared on disk with the fake fleet of test_star_sync_flow.py; the
console is then started on it and spoken to over HTTP: the page, the state, the
items, a verdict, a real `merge` job through the job runner, and a commit the
gate refuses. No node is reached -- nothing here needs the network.

    python tools/test_console_sync_live.py
"""
import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_sync as ss  # noqa: E402
import test_star_sync_flow as fl  # noqa: E402
import test_console_system as tcs  # noqa: E402  -- its scratch library schema

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def call(port, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"} if body is not None else {})
        r = c.getresponse()
        raw = r.read()
        ctype = r.getheader("Content-Type") or ""
        return r.status, (json.loads(raw) if "json" in ctype else raw.decode("utf-8", "replace"))
    finally:
        c.close()


def main() -> int:
    tmp = tempfile.mkdtemp()
    fleet = fl.Fleet(tmp)
    base = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = fl.build(tmp, fleet, base, {"a": (base, False), "b": (base, False)})
    for n, (r, t) in (("a", (3, "2026-10-02 00:00:00")), ("b", (5, "2026-10-03 00:00:00"))):
        c = sqlite3.connect(fleet.nodes[n]["listener"])
        c.execute(f"UPDATE listener_preferences SET rotation={r}, recovery={r}, updated_at='{t}'")
        c.commit()
        c.close()
    with fl.faked(fleet):
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)

    lib = os.path.join(tmp, "lib.db")
    c = sqlite3.connect(lib)
    c.executescript(tcs.SCHEMA)
    c.commit()
    c.close()
    plan_file = os.path.join(tmp, "plan.json")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, LEMPI_STAR_PLAN=plan_file, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "console.py"), lib, "--port", str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                            cwd=os.path.dirname(HERE))
    try:
        up = False
        for _ in range(100):
            try:
                call(port, "GET", "/api/system")
                up = True
                break
            except OSError:
                time.sleep(0.1)
        check(up, "the console comes up")

        st, html = call(port, "GET", "/sync")
        check(st == 200 and "Fleet sync" in html and "approve-all" in html, "the Sync page is served")
        st, d = call(port, "GET", "/")
        check('href="/sync"' in d, "and every page links to it")

        st, d = call(port, "GET", "/api/sync/state")
        check(st == 200 and d.get("plan") is False and "no plan" in d["why"], f"with no plan the page is told so: {d}")

        json.dump(plan, open(plan_file, "w"))
        st, d = call(port, "GET", "/api/sync/state")
        check(d.get("plan") and d["run"]["merged"] and d["run"]["pending"] == 1 and not d["run"]["ready"],
              f"with a plan: the run, one item waiting, not ready: {d.get('run')}")

        st, d = call(port, "GET", "/api/sync/items")
        item = d["items"][0]
        check(d["matching"] == 1 and item["default"] == "b" and item["status"] == "pending"
              and item["name"]["text"].startswith("artist"), f"the item, in words: {item}")

        st, d = call(port, "POST", "/api/sync/stage", {"stage": "commit", "nodes": []})
        check(st == 409 and "no verdict" in d["error"], f"a commit is refused by the gate: {st} {d}")
        st, d = call(port, "POST", "/api/sync/stage", {"stage": "wipe"})
        check(st == 409, "an unknown stage is refused")

        st, d = call(port, "POST", "/api/sync/verdicts", {"verdicts": {item["id"]: "chose:a"}})
        check(st == 200 and d["set"] == 1, f"a verdict is recorded: {d}")
        st, d = call(port, "GET", "/api/sync/items")
        check(d["items"][0]["status"] == "stale", "and awaits a new merge")

        # A real job: the merge, run by the job runner as star_sync.py would be by hand.
        st, d = call(port, "POST", "/api/sync/stage", {"stage": "merge", "nodes": []})
        check(st == 200 and "job_id" in d, f"a merge is submitted as a job: {st} {d}")
        job = d["job_id"]
        end = None
        for _ in range(150):
            time.sleep(0.2)
            st, j = call(port, "GET", f"/api/jobs/{job}")
            if j["state"] in ("done", "failed", "stopped"):
                end = j["state"]
                break
        check(end == "done", f"the job ends done: {end} {[e['text'] for e in j['events'] if e['kind'] in ('log', 'error')][-4:]}")
        st, d = call(port, "GET", "/api/sync/items")
        check(d["items"][0]["status"] == "changed" and d["items"][0]["chosen"] == "a",
              f"the verdict is now reflected by the new merge: {d['items'][0]['status']}")
        check(os.path.isdir(os.path.join(run, "void")), "and the earlier merge is kept, void")

        st, d = call(port, "POST", "/api/sync/approve-all", {})
        check(st == 200 and d["approved"] == {}, f"nothing was waiting for approve-all: {d}")
        st, d = call(port, "GET", "/api/sync/summary")
        check(st == 200 and "report" in d, "the summary route answers")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console sync (live): all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
