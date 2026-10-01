#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `passage_hold.py` `[SPEC-HOLD-010]`: holding a passage back from
the Program Director, releasing it, and holding every damaged CD track --
never one a person has released.

    python tools/test_passage_hold.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def run(db, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "passage_hold.py"), db, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=60)
    return r.returncode, r.stdout + r.stderr


def marks(db):
    c = sqlite3.connect(db)
    cols = [r[1] for r in c.execute("PRAGMA table_info(passages)")]
    got = dict(c.execute("SELECT passage_id, director_hold FROM passages")) if "director_hold" in cols else None
    c.close()
    return got


def library(path):
    """A library made before the column existed: one CD rip, three tracks,
    each a radio and an album passage; track 2 failed verification."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
                               start_ms INTEGER, end_ms INTEGER);
        CREATE TABLE ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT, stage TEXT,
                                       outcome TEXT, confidence REAL, detail TEXT, decided_at TEXT);
        INSERT INTO files VALUES (1, 'md5', 'C:\\Music\\A\\Album (2003)\\A - Album.mp3');
        INSERT INTO passages VALUES (10, 1, 'radio', 0, 1000), (11, 1, 'album', 0, 1000),
                                    (20, 1, 'radio', 1000, 2000), (21, 1, 'album', 1000, 2000),
                                    (30, 1, 'radio', 2000, 3000), (31, 1, 'album', 2000, 3000);
        INSERT INTO ingest_decisions (audio_md5, stage, outcome, detail, decided_at)
             VALUES ('md5', 'rip', 'exact', '{}', 'x'),
                    ('md5', 'rip', 'verification_failed', '{"track": 2, "detail": "read errors at 0:00:01"}', 'x');
    """)
    c.commit()
    c.close()


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "lib.db")
        library(db)

        print("--damaged: a dry run names track 2's radio passage, and writes nothing")
        code, out = run(db, "--damaged")
        check(code == 0 and "passage 20" in out and "1 damaged track(s) to hold" in out, out)
        check(marks(db) is None, "not even the column")

        print("--damaged --commit: adds the column, holds that passage only, saying why")
        run(db, "--damaged", "--commit")
        m = marks(db)
        check(m == {10: None, 11: None, 20: "damaged rip: read errors at 0:00:01", 21: None, 30: None, 31: None},
              f"{m}")

        print("--release: a person's decision, which --damaged never undoes")
        code, out = run(db, "--release", "20", "--json")
        check(code == 0 and marks(db)[20] == "" and json.loads(out.splitlines()[-1])["held"] is False, out)
        run(db, "--damaged", "--commit")
        check(marks(db)[20] == "", f"released stays released: {marks(db)[20]!r}")

        print("--hold by hand, with a reason; --list shows only what is held")
        run(db, "--hold", "30", "--why", "the live version is better")
        check(marks(db)[30] == "the live version is better", f"{marks(db)}")
        code, out = run(db, "--list")
        check("30  the live version is better" in out and "1 passage(s) held" in out, out)

        print("a passage that does not exist is refused, out loud")
        code, out = run(db, "--hold", "999", "--json")
        check(code == 1 and "no passage 999" in out, out)

        test_job_and_route(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("passage_hold: all checks passed")
    return 0


def test_job_and_route(tmp):
    print("the console: the profile page's button reaches passage_hold.py through a job")
    import http.client
    import threading
    import time
    sys.path.insert(0, HERE)
    import jobs as jobmod
    import console

    db = os.path.join(tmp, "route.db")
    library(db)
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS id_checks (passage_id INTEGER)")   # every job counts it
    c.execute("CREATE TABLE IF NOT EXISTS passage_recordings (passage_id INTEGER, mbid TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS flavor (subject_kind TEXT, subject_id TEXT)")
    c.commit()
    c.close()
    runner = jobmod.Runner(db, os.path.join(tmp, "route.console.db"))
    seen = []
    runner._spawn = (lambda self, job_id, stage, argv: seen.append((stage, argv)) or (0, '{"ok": true}')
                     ).__get__(runner, jobmod.Runner)

    def wait(job_id):
        for _ in range(200):
            j = runner.job(job_id)
            if j and j["state"] not in ("queued", "running"):
                return j
            time.sleep(0.05)
        raise TimeoutError(job_id)

    j = wait(runner.submit("passage-hold", json.dumps({"passage_id": 10, "hold": True, "why": "too quiet"})))
    stage, argv = seen[-1]
    check(j["state"] == "done" and stage == "hold" and argv[-5:] == ["--hold", "10", "--why", "too quiet", "--json"],
          f"hold: {stage} {argv}")
    wait(runner.submit("passage-hold", json.dumps({"passage_id": 10, "hold": False, "why": ""})))
    stage, argv = seen[-1]
    check(stage == "release" and argv[-3:] == ["--release", "10", "--json"], f"release: {stage} {argv}")

    console.STATE["path"] = console.STATE["library"] = db
    console.STATE["jobs"] = runner
    srv = console.Server(("127.0.0.1", 0), console.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    console.STATE["port"] = port
    host = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
    try:
        def post(body, extra=None):
            h = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h.request("POST", "/api/passage-hold", body=json.dumps(body), headers={**host, **(extra or {})})
            r = h.getresponse()
            return r.status, json.loads(r.read() or b"{}")
        submitted = []
        runner.submit = lambda kind, target: submitted.append((kind, json.loads(target))) or 7
        status, got = post({"passage_id": 30, "hold": True, "why": " the live one is better "})
        check(status == 200 and got == {"job_id": 7}
              and submitted[-1] == ("passage-hold", {"passage_id": 30, "hold": True, "why": "the live one is better"}),
              f"{status} {got} {submitted}")
        status, _ = post({"passage_id": "30", "hold": "yes"})
        check(status == 400 and len(submitted) == 1, f"a malformed request is refused: {status}")
        status, _ = post({"passage_id": 30, "hold": True}, {"Origin": "https://elsewhere.example"})
        check(status == 403 and len(submitted) == 1, f"a foreign page cannot hold anything: {status}")
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
