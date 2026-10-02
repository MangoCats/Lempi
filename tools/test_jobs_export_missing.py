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


def runner_with(tmp, name, local_only, diff_code=0, holds=None):
    db = os.path.join(tmp, f"{name}.db")
    library(db)
    r = jobmod.Runner(db, os.path.join(tmp, f"{name}.console.db"), roots=["C:\\Music"])
    seen = []

    def fake_spawn(self, job_id, stage, argv):
        seen.append((stage, argv))
        if stage == "diff":
            if diff_code == 0:
                with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as f:
                    tables = {"files": {"local_only": [[m] for m in local_only], "peer_only": [],
                                        "differ": [], "conflict": []}}
                    if holds is not None:
                        tables["holds"] = holds
                    json.dump({"tables": tables}, f)
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


def test_holds(tmp):
    print("holds the speaker lacks go too: with the music, or alone for files it has [SPEC-HOLD-080]")
    holds = {"local_only": [["md5-1", "radio", 0, 900], ["md5-3", "radio", 0, 900]],
             "differ": [{"key": ["md5-3", "album", 0, 1000]}], "peer_only": [["md5-2", "radio", 0, 1]]}
    r, _, seen = runner_with(tmp, "holds", ["md5-1"], holds=holds)
    j = wait_for(r, r.submit("export-missing", json.dumps({"peer": "speaker-a",
                                                           "remote": "pi@speaker-a:/srv/library/library.db"})))
    diff, bundle = seen[0][1], seen[1][1]
    check(diff[diff.index("--table", diff.index("--table") + 1) + 1] == "holds", f"holds asked for: {diff}")
    present = open(bundle[bundle.index("--present-md5-file") + 1], encoding="utf-8").read().split()
    check(present == ["md5-3"], f"a missing file carries its own hold; the speaker's own decision is its: {present}")
    check(j["result"]["holds"] == 2 and j["result"]["hold_files"] == 1 and j["result"]["missing"] == 1,
          f"said: {j['result']}")

    print("nothing missing but a hold still builds a bundle")
    r2, _, seen2 = runner_with(tmp, "holdsonly", [], holds={"local_only": [["md5-3", "radio", 0, 900]], "differ": []})
    j2 = wait_for(r2, r2.submit("export-missing", json.dumps({"peer": "speaker-b",
                                                              "remote": "pi@speaker-b:/srv/library/library.db"})))
    check(j2["state"] == "done" and [s for s, _ in seen2] == ["diff", "bundle"] and j2["result"]["missing"] == 0
          and "out_dir" in j2["result"], f"{j2['state']} {seen2} {j2['result']}")


def test_credits(tmp):
    print("artist credits the speaker lacks, on files it has, go as payload alone [SPEC-PL-054]")
    r, db, seen = runner_with(tmp, "credits", ["md5-1"])
    c = sqlite3.connect(db)
    for pid, fid, mbid in ((1, 1, "r1"), (3, 3, "r3")):
        c.execute("INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, boundary_src) "
                  "VALUES (?, ?, 'radio', 0, 1000, 'x')", (pid, fid))
        c.execute("INSERT INTO recordings (mbid, title, source) VALUES (?, 't', 's')", (mbid,))
        c.execute("INSERT INTO passage_recordings (passage_id, mbid, weight, source) VALUES (?, ?, 1.0, 's')",
                  (pid, mbid))
    c.execute("INSERT INTO artists (mbid, name, source) VALUES ('art-3', 'Three', 's')")
    c.execute("INSERT INTO recording_artists (mbid, artist_mbid, weight, source) VALUES ('r3', 'art-3', 1.0, 's')")
    # rel-new: chosen for r3's file. rel-back: on r1, whose file is missing (it brings its own).
    # rel-theirs: the speaker's cover only.
    c.execute("CREATE TABLE IF NOT EXISTS releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT, source TEXT NOT NULL)")
    c.execute("INSERT INTO releases (mbid, title, source) VALUES ('rel-new', 'N', 's'), ('rel-back', 'B', 's'), ('rel-theirs', 'T', 's')")
    c.execute("INSERT INTO release_recordings (release_mbid, mbid, position, source, chosen) VALUES "
              "('rel-new', 'r3', 1, 's', 1), ('rel-back', 'r1', 1, 's', 1), ('rel-theirs', 'r3', 2, 's', 0)")
    c.commit()
    c.close()
    real = r._spawn

    def with_credits(job_id, stage, argv):
        code, out = real(job_id, stage, argv)
        if stage == "diff":
            path = argv[argv.index("-o") + 1]
            d = json.load(open(path, encoding="utf-8"))
            d["tables"]["credits"] = {"local_only": [["r1", "a1"], ["r3", "a1"], ["r-elsewhere", "a1"]],
                                      "peer_only": [], "differ": [], "conflict": []}
            d["tables"]["albums"] = {"local_only": [["r3", "rel-x"]], "peer_only": [], "differ": [], "conflict": []}
            d["tables"]["sort_names"] = {"local_only": [["art-3"]], "peer_only": [], "differ": [], "conflict": []}
            d["tables"]["file_releases"] = {"local_only": [["md5-3"]], "peer_only": [], "conflict": [],
                                            "differ": [{"key": ["md5-1"], "local": {}, "peer": {}}]}
            d["tables"]["covers"] = {"local_only": [["rel-new"]], "peer_only": [],
                                     "differ": [{"key": ["rel-back"], "local": {"front": 1, "back": 1},
                                                 "peer": {"front": 1, "back": 0}},
                                                {"key": ["rel-theirs"], "local": {"front": 0, "back": 0},
                                                 "peer": {"front": 1, "back": 0}}], "conflict": []}
            json.dump(d, open(path, "w", encoding="utf-8"))
        return code, out
    r._spawn = with_credits
    j = wait_for(r, r.submit("export-missing", json.dumps({"peer": "speaker-a",
                                                           "remote": "pi@speaker-a:/srv/library/library.db"})))
    diff, bundle = seen[0][1], seen[1][1]
    check("credits" in diff, f"credits asked for: {diff}")
    present = open(bundle[bundle.index("--present-md5-file") + 1], encoding="utf-8").read().split()
    check("albums" in diff and j["result"]["album_files"] == 1, f"albums asked for, and said: {j['result']}")
    check("sort_names" in diff and j["result"]["sort_names"] == 1, f"sort names asked for, and said: {j['result']}")
    check("file_releases" in diff and j["result"]["file_releases"] == 1,
          f"a file's own album, for a file the speaker has (not one it is sent whole): {j['result']}")
    covers_list = open(bundle[bundle.index("--cover-releases") + 1], encoding="utf-8").read().splitlines()
    check(covers_list == ["rel-back back", "rel-new back front"],
          f"the bundle told which cover sides the speaker lacks: {covers_list}")
    check("covers" in diff and j["result"]["covers"] == 1,
          f"a cover side the speaker lacks goes; one only it has does not: {j['result']}")
    check(present == ["md5-3"] and j["result"]["credit_files"] == 1,
          f"only the file the speaker has; a missing one brings its own, one with no file here nothing: "
          f"{present} {j['result']}")


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


def test_send(tmp):
    print("send-bundle: send_bundle.py against the speaker, a dry run unless asked")
    r, db, seen = runner_with(tmp, "send", [])
    seen.clear()
    target = {"peer": "speaker-a", "remote": "pi@speaker-a:/srv/library/library.db",
              "bundle": "C:\\Lempi\\out\\missing-speaker-a-9\\bundle"}
    j = wait_for(r, r.submit("send-bundle", json.dumps(target)))
    stage, argv = seen[-1]
    check(j["state"] == "done" and stage == "check" and "send_bundle.py" in argv[1]
          and argv[2:7] == [target["bundle"], "pi@speaker-a", "--library", "/srv/library/library.db"][:5]
          and "--apply" not in argv, f"a dry run: {stage} {argv}")
    wait_for(r, r.submit("send-bundle", json.dumps(dict(target, apply=True))))
    stage, argv = seen[-1]
    check(stage == "send" and argv[-1] == "--apply", f"the real send: {stage} {argv}")


def test_send_route(tmp):
    print("the send route: a configured speaker, and only a bundle built for it")
    import console
    db = os.path.join(tmp, "sroute.db")
    library(db)
    runner = jobmod.Runner(db, os.path.join(tmp, "sroute.console.db"))
    runner.upsert_peer("speaker-a", "pi@speaker-a:/srv/library/library.db")
    submitted = []
    runner.submit = lambda kind, target: submitted.append((kind, json.loads(target))) or 1
    console.STATE["path"] = console.STATE["library"] = db
    console.STATE["jobs"] = runner
    good = os.path.join(console.REPO_ROOT, "out", "missing-speaker-a-test", "bundle")
    other = os.path.join(console.REPO_ROOT, "out", "missing-speaker-b-test", "bundle")
    for b in (good, other):
        os.makedirs(b, exist_ok=True)
        with open(os.path.join(b, "payload.json"), "w") as f:
            f.write("{}")
    srv = console.Server(("127.0.0.1", 0), console.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    console.STATE["port"] = port
    try:
        def post(body):
            h = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h.request("POST", "/api/export/send", body=json.dumps(body),
                      headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
            resp = h.getresponse()
            return resp.status, json.loads(resp.read() or b"{}")
        st, _ = post({"peer": "speaker-a", "bundle": good})
        check(st == 200 and submitted[-1] == ("send-bundle", {
            "peer": "speaker-a", "remote": "pi@speaker-a:/srv/library/library.db",
            "bundle": os.path.abspath(good), "apply": False}), f"a dry run unless asked: {submitted}")
        st, _ = post({"peer": "speaker-a", "bundle": good, "apply": "yes"})
        check(st == 200 and submitted[-1][1]["apply"] is False, "apply only when exactly true")
        st, _ = post({"peer": "speaker-a", "bundle": good, "apply": True})
        check(submitted[-1][1]["apply"] is True, "apply when asked")
        n = len(submitted)
        for body, why in (({"peer": "speaker-a", "bundle": other}, "another speaker's bundle"),
                          ({"peer": "speaker-a", "bundle": tmp}, "a folder outside out/"),
                          ({"peer": "nobody", "bundle": good}, "a speaker not configured")):
            st, _ = post(body)
            check(st == 400 and len(submitted) == n, f"{why} is refused: {st}")
    finally:
        srv.shutdown()
        srv.server_close()
        import shutil
        for b in (good, other):
            shutil.rmtree(os.path.dirname(b), ignore_errors=True)


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_job(tmp)
        test_holds(tmp)
        test_credits(tmp)
        test_route(tmp)
        test_send(tmp)
        test_send_route(tmp)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs export_missing: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
