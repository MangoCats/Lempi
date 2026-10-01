#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Export page's *Send what's missing* `[SPEC-STAR-090]`: the
`export-missing` job -- a read-only diff of `files` against the speaker, then
a bundle of exactly the audio it lacks -- and its route. `_spawn` faked; the
real `mesh_diff.py` was run against lempi02w on 2026-10-01.

    python tools/test_jobs_export_missing.py
"""
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs as jobmod  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(HERE), "sql", "schema.sql")
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
    raise TimeoutError(job_id)


def library(path):
    c = sqlite3.connect(path)
    c.executescript(open(SCHEMA, encoding="utf-8").read())
    c.execute("CREATE TABLE IF NOT EXISTS id_checks (passage_id INTEGER)")
    for fid, p in ((1, "C:\\Music\\A\\Album (2001)\\01.mp3"), (2, "C:\\Music\\A\\Album (2001)\\02.mp3"),
                   (3, "C:\\Music\\B\\Other (1999)\\01.mp3")):
        c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, "
                  "first_seen, last_seen) VALUES (?1, ?2, ?3, 1, 0, 'mp3', 1000, 'x', 'x')", (fid, f"md5-{fid}", p))
    c.commit()
    c.close()


def runner_with(tmp, name, local_only, diff_code=0):
    db = os.path.join(tmp, f"{name}.db")
    library(db)
    r = jobmod.Runner(db, os.path.join(tmp, f"{name}.console.db"), roots=["C:\\Music"])
    seen = []

    def fake_spawn(self, job_id, stage, argv):
        seen.append((stage, argv))
        if stage == "diff":
            if diff_code == 0:
                with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as f:
                    json.dump({"tables": {"files": {"local_only": [[m] for m in local_only], "peer_only": [],
                                                    "differ": [], "conflict": []}}}, f)
            return diff_code, ""
        return 0, ""
    r._spawn = fake_spawn.__get__(r, jobmod.Runner)
    return r, db, seen


def test_job(tmp):
    print("the diff, read-only and on files alone; then exactly the missing audio, bundled")
    r, db, seen = runner_with(tmp, "one", ["md5-1", "md5-2"])
    j = wait_for(r, r.submit("export-missing", json.dumps({"peer": "speaker-a",
                                                           "remote": "pi@speaker-a:/srv/library/library.db"})))
    check(j["state"] == "done" and [s for s, _ in seen] == ["diff", "bundle"], f"{j['state']} {seen}")
    diff = seen[0][1]
    check("mesh_diff.py" in diff[1] and diff[2:4] == [db, "pi@speaker-a:/srv/library/library.db"]
          and diff[diff.index("--table") + 1] == "files", f"the speaker's own library, files only: {diff}")
    bundle = seen[1][1]
    md5_file = bundle[bundle.index("--md5-file") + 1]
    check("export_bundle.py" in bundle[1] and open(md5_file, encoding="utf-8").read().split() == ["md5-1", "md5-2"]
          and "--root" in bundle, f"the bundle carries exactly what the speaker lacks: {bundle}")
    res = j["result"]
    check(res["missing"] == 2 and res["albums"] == [{"folder": "C:\\Music\\A\\Album (2001)", "files": 2}]
          and res["out_dir"] == bundle[bundle.index("-o") + 1], f"said by album, and where: {res}")

    print("a speaker that has everything gets no bundle")
    r2, _, seen2 = runner_with(tmp, "none", [])
    j2 = wait_for(r2, r2.submit("export-missing", json.dumps({"peer": "speaker-b", "remote": "pi@speaker-b:/srv/library/library.db"})))
    check(j2["state"] == "done" and [s for s, _ in seen2] == ["diff"] and j2["result"]["missing"] == 0
          and "out_dir" not in j2["result"], f"{j2['state']} {seen2} {j2['result']}")

    print("a diff that fails builds nothing, and says so")
    r3, _, seen3 = runner_with(tmp, "fail", [], diff_code=1)
    j3 = wait_for(r3, r3.submit("export-missing", json.dumps({"peer": "speaker-c", "remote": "pi@speaker-c:/srv/library/library.db"})))
    errors = [e["text"] for e in j3["events"] if e["kind"] == "error"]
    check(j3["state"] == "failed" and [s for s, _ in seen3] == ["diff"] and any("nothing was built" in t for t in errors),
          f"{j3['state']} {seen3} {errors}")


def test_route(tmp):
    print("the route: names resolved to their configured remote, one job each, the commands each needs")
    import console
    db = os.path.join(tmp, "route.db")
    library(db)
    runner = jobmod.Runner(db, os.path.join(tmp, "route.console.db"))
    runner.upsert_peer("speaker-a", "pi@speaker-a:/srv/library/library.db")
    runner.upsert_peer("speaker-b", "pi@speaker-b:/srv/library/library.db")
    submitted = []
    runner.submit = lambda kind, target: submitted.append((kind, json.loads(target))) or len(submitted)
    console.STATE["path"] = console.STATE["library"] = db
    console.STATE["jobs"] = runner
    srv = console.Server(("127.0.0.1", 0), console.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    console.STATE["port"] = port
    try:
        def post(body):
            h = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h.request("POST", "/api/export/missing", body=json.dumps(body),
                      headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
            resp = h.getresponse()
            return resp.status, json.loads(resp.read() or b"{}")
        st, got = post({"peers": ["speaker-a", "speaker-b"]})
        check(st == 200 and [k for k, _ in submitted] == ["export-missing"] * 2
              and submitted[0][1] == {"peer": "speaker-a", "remote": "pi@speaker-a:/srv/library/library.db"},
              f"{st} {submitted}")
        one = got["jobs"][0]
        check(one["ssh"] == "pi@speaker-a" and one["library"] == "/srv/library/library.db"
              and one["incoming"] == "/srv/library/incoming" and one["url"].startswith("http://speaker-a:"),
              f"each speaker's own commands: {one}")
        st, got = post({"peers": ["speaker-a", "pi@speaker-c:/x"]})
        check(st == 400 and len(submitted) == 2, f"a name not configured is refused, nothing started: {st} {got}")
        st, got = post({"peers": []})
        check(st == 400, f"none ticked: {st}")
    finally:
        srv.shutdown()
        srv.server_close()


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_job(tmp)
        test_route(tmp)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs export_missing: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
