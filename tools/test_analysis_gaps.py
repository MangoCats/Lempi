#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Jobs page's *Not analyzed* list and its button
`[REQ-LIB-305]`: `analysis_gaps.missing()` against the real schema, the
`analyze-missing` job kind with `_spawn` faked, and both console routes.

The list must count by the same rules the two tools select by, or it names
work the button can never clear -- so each rule has a case: a passage with
no recording (flavor never extracted), a hand-set boundary (amplitude never
recomputed), an album cut (neither tool looks at it).

    python tools/test_analysis_gaps.py
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
import analysis_gaps as ag  # noqa: E402
import jobs as jobmod  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(HERE), "sql", "schema.sql")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def library(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.executescript(open(SCHEMA, encoding="utf-8").read())
    # The identify step makes this; every job's opening count reads it, so a
    # library that has run any job has one.
    c.execute("CREATE TABLE IF NOT EXISTS id_checks (passage_id INTEGER)")
    c.execute("INSERT INTO recordings (mbid, title, source) VALUES ('r', 'Song', 'test')")
    pid = 0

    def file(fid, path, passages):
        nonlocal pid
        c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, "
                  "first_seen, last_seen) VALUES (?1, ?2, ?3, 1, 0, 'mp3', 600000, 'x', 'x')",
                  (fid, f"md5-{fid}", path))
        for kind, start, lead_in, src, identified, cached in passages:
            pid += 1
            c.execute("INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, lead_in_ms, "
                      "lead_out_ms, boundary_src) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, ?7)",
                      (pid, fid, kind, start, start + 1000, lead_in, src))
            if identified:
                c.execute("INSERT INTO passage_recordings (passage_id, mbid, source) VALUES (?1, 'r', 'test')",
                          (pid,))
            if cached:
                c.execute("INSERT INTO lowlevel_cache VALUES (?1, ?2, ?3, x'00', 'test', 'now')",
                          (f"md5-{fid}", start, start + 1000))
    #                              kind    start lead  src       ident  cached
    file(1, r"C:\Music\Done\Album\01.mp3", [("radio", 0, 100, "cue", True, True)])
    file(2, r"C:\Music\New\Album (2026)\New - Album.mp3",
         [("radio", 0, None, "cue", True, False), ("radio", 1000, None, "cue", True, False),
          ("album", 0, None, "cue", True, False)])             # an album cut: neither tool's
    file(3, r"C:\Music\Odd\Mixed\01.mp3",
         [("radio", 0, 100, "cue", False, False),                  # never identified
          ("radio", 1000, None, "manual", True, True),             # hand-set boundary
          ("radio", 2000, None, "cue", True, True)])               # only amplitude missing
    file(4, "/srv/music/Linux/Album/01.mp3", [("radio", 0, 100, "cue", True, False)])
    c.commit()
    return c


def test_missing():
    print("counted by each tool's own rule, newest folder first")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = library(os.path.join(tmp, "lib.db"))
        got = ag.missing(c)
        by = {f["folder"]: f for f in got["folders"]}
        check(r"C:\Music\Done\Album" not in by, f"a fully analyzed folder is not listed: {list(by)}")
        new = by.get(r"C:\Music\New\Album (2026)", {})
        check(new.get("flavor") == 2 and new.get("amplitude") == 2 and new.get("passages") == 2,
              f"a fresh CD album: both radio passages, both analyses; the album cut not counted: {new}")
        odd = by.get(r"C:\Music\Odd\Mixed", {})
        check(odd.get("flavor") == 0 and odd.get("unidentified") == 1,
              f"no recording: not flavor the button can do, but said: {odd}")
        check(odd.get("amplitude") == 1 and odd.get("manual") == 1,
              f"a hand-set boundary is said, never counted as amplitude to do: {odd}")
        lin = by.get("/srv/music/Linux/Album", {})
        check(lin.get("flavor") == 1, f"a / path is a folder too: {lin}")
        order = [f["folder"] for f in got["folders"]]
        check(order[0] == "/srv/music/Linux/Album" and order[-1] == r"C:\Music\New\Album (2026)",
              f"newest file first: {order}")
        t = got["totals"]
        check((t["flavor"], t["amplitude"], t["unidentified"], t["manual"], t["folders"], t["fixable"])
              == (3, 3, 1, 1, 3, 3), f"totals: {t}")
        check(new.get("first") == 2, f"the folder links its first passage: {new}")

        c.execute("DROP TABLE lowlevel_cache")
        t2 = ag.missing(c)["totals"]
        check(t2["flavor"] == 6, f"no cache table yet: every identified radio passage needs flavor: {t2}")
        c.close()


def wait_for(runner, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = runner.job(job_id)
        if j and j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish")


def runner_with(tmp, name, outputs):
    db = os.path.join(tmp, f"{name}.db")
    library(db).close()
    r = jobmod.Runner(db, os.path.join(tmp, f"{name}.console.db"))
    seen = []

    def fake_spawn(self, job_id, stage, argv):
        seen.append((stage, argv))
        return outputs[stage]
    r._spawn = fake_spawn.__get__(r, jobmod.Runner)
    return r, db, seen


def test_job():
    print("analyze-missing: flavor then amplitude, library-wide, no identification")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        r, db, seen = runner_with(tmp, "ok", {
            "flavor": (0, "130 files, 0 cached, 130 to extract, 7 jobs\n\n128 extracted, 2 failed in 9.0 min"),
            "amplitude": (0, '{"ok": true, "analyzed": 130, "failed": 0}')})
        j = wait_for(r, r.submit("analyze-missing", ""))
        check(j["state"] == "done", f"done: {j['state']}")
        check([s for s, _ in seen] == ["flavor", "amplitude"], f"two stages, in order: {seen}")
        check("extract_library.py" in seen[0][1][1] and seen[0][1][2:] == [db],
              f"flavor: the whole library, nothing narrower: {seen[0][1]}")
        check("analyze_amplitude.py" in seen[1][1][1] and "--folder" not in seen[1][1]
              and "--recheck" not in seen[1][1], f"amplitude: library-wide, NULL only: {seen[1][1]}")
        check(not any("fingerprint_ids.py" in " ".join(a) for _, a in seen), "never re-identifies")
        check(j["result"]["flavor"] == {"extracted": 128, "failed": 2, "todo": 130}, f"{j['result']}")
        check(j["result"]["amplitude"].get("analyzed") == 130, f"{j['result']}")

        r2, _, seen2 = runner_with(tmp, "bad", {"flavor": (1, "boom"),
                                               "amplitude": (0, '{"ok": true, "analyzed": 3}')})
        j2 = wait_for(r2, r2.submit("analyze-missing", ""))
        check([s for s, _ in seen2] == ["flavor", "amplitude"] and j2["state"] == "failed",
              f"a failed first stage does not stop the second, and the job says failed: {j2['state']}")


def test_routes():
    print("the console: GET the list, POST starts the job")
    import console
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "lib.db")
        library(db).close()
        console.STATE["path"] = db
        console.STATE["library"] = db
        runner = jobmod.Runner(db, os.path.join(tmp, "lib.console.db"))
        submitted = []
        runner.submit = lambda kind, target: submitted.append((kind, target)) or 99
        console.STATE["jobs"] = runner
        srv = console.Server(("127.0.0.1", 0), console.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        console.STATE["port"] = port          # what the origin check compares against
        try:
            h = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h.request("GET", "/api/analysis/missing", headers={"Host": f"127.0.0.1:{port}"})
            got = json.loads(h.getresponse().read())
            check(got["totals"]["flavor"] == 3, f"the list over HTTP: {got['totals']}")
            h.request("POST", "/api/analysis/run", headers={"Host": f"127.0.0.1:{port}"})
            r = json.loads(h.getresponse().read())
            check(r == {"job_id": 99} and submitted == [("analyze-missing", "")], f"{r} {submitted}")
            h.request("POST", "/api/analysis/run", headers={"Host": f"127.0.0.1:{port}",
                                                            "Origin": "https://elsewhere.example"})
            resp = h.getresponse()
            resp.read()
            check(resp.status == 403 and len(submitted) == 1, f"a foreign page cannot start it: {resp.status}")
        finally:
            srv.shutdown()
            srv.server_close()


def main() -> int:
    test_missing()
    test_job()
    test_routes()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("analysis gaps: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
