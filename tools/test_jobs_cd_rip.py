#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `cd-rip` job kind `[SPEC025..028]`.

`ingest_cd.py` itself is `test_cd_toc.py`/`test_ingest_cd.py`'s job; this
checks the layer above it -- the job kind reaches it with the right argv,
and it is named in `SKIPPED` rather than silently run by
`induct`/`reanalyze`, the same posture `test_jobs_segment_dao.py` and
`test_jobs_analyze_amplitude.py` already use for their own job kinds: a
real `Runner`, `_spawn` faked.

    python tools/test_jobs_cd_rip.py
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


def test_skipped_names_it() -> None:
    print("SKIPPED names 'cd-rip', the job kind that reaches ingest_cd.py")
    names = [s for s, _ in jobmod.SKIPPED]
    check("cd-rip" in names, f"got {names}")
    reason = dict(jobmod.SKIPPED)["cd-rip"]
    check("SPEC-RIP-088" in reason, f"got {reason!r}")


def _runner(tmp: str, name: str) -> "jobmod.Runner":
    db = os.path.join(tmp, f"{name}.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    sidecar = os.path.join(tmp, f"{name}.console.db")
    return jobmod.Runner(db, sidecar), db


def test_folder_reaches_ingest_cd(tmp: str) -> None:
    print("a folder target reaches ingest_cd.py's own --folder unchanged; the album is then analysed")
    runner, db = _runner(tmp, "lib1")
    seen = []
    added = ('{"ok": true, "tracks": 14, "identified": 14, "ambiguous": 0, '
             '"unidentified": 0, "verification_failed": 0, "disc_outcome": "exact", '
             '"candidates": 1, "folder": "C:/Music/A/B (2001)"}')

    def fake_spawn(self, job_id, stage, argv):
        seen.append((stage, argv))
        return {"ingest": (0, added),
                "flavor": (0, "14 files, 0 cached, 14 to extract, 7 jobs\n\n14 extracted, 0 failed in 1.0 min"),
                "amplitude": (0, '{"ok": true, "analyzed": 14, "failed": 0}')}[stage]

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"folder": "C:/rips/some-disc"})
    job_id = runner.submit("cd-rip", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"got {j}")
    argv = seen[0][1]
    check("ingest_cd.py" in argv[1], f"got {argv}")
    check(db in argv, f"got {argv}")
    check("--folder" in argv and argv[argv.index("--folder") + 1] == "C:/rips/some-disc",
          f"got {argv}")
    check("--commit" in argv and "--json" in argv, f"got {argv}")
    check(j["result"]["disc_outcome"] == "exact", f"got {j['result']}")
    # [REQ-LIB-305]: analysed at once, both stages, on the album's own folder.
    check([s for s, _ in seen] == ["ingest", "flavor", "amplitude"], f"three stages: {[s for s, _ in seen]}")
    for stage, a in seen[1:]:
        check(a[a.index("--folder") + 1] == "C:/Music/A/B (2001)", f"{stage} scoped to the album: {a}")
    check(j["result"]["analysis"]["flavor"]["extracted"] == 14
          and j["result"]["analysis"]["amplitude"]["analyzed"] == 14 and j["result"]["analysis_failed"] == [],
          f"the analysis in the job's result: {j['result']}")

    print("an analysis that fails: the album is still added, and the failure is said")
    seen.clear()
    runner._spawn = (lambda self, job_id, stage, argv: seen.append((stage, argv)) or
                     {"ingest": (0, added), "flavor": (2, "ERROR: the Essentia extractor is missing"),
                      "amplitude": (0, '{"ok": true, "analyzed": 14}')}[stage]).__get__(runner, jobmod.Runner)
    j = wait_for(runner, runner.submit("cd-rip", target))
    errors = [e["text"] for e in j["events"] if e["kind"] == "error"]
    check(j["state"] == "done" and j["result"]["ok"] and j["result"]["analysis_failed"] == ["flavor"]
          and any("flavor exited 2" in t for t in errors) and [s for s, _ in seen][-1] == "amplitude",
          f"done, flavor named as failed, amplitude still run: {j['state']} {j['result']} {errors}")


def test_failure_surfaces_as_failed(tmp: str) -> None:
    print("ingest_cd.py's own {\"ok\": false, ...} fails the job, not a crash -- and nothing is analysed")
    runner, db = _runner(tmp, "lib2")
    seen = []

    def fake_spawn(self, job_id, stage, argv):
        seen.append(stage)
        return 1, '{"ok": false, "error": "no .cue or .toc file found"}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)

    target = json.dumps({"folder": "C:/rips/empty"})
    job_id = runner.submit("cd-rip", target)
    j = wait_for(runner, job_id)
    check(j["state"] == "failed", f"got {j}")
    check(j["result"]["error"] == "no .cue or .toc file found", f"got {j['result']}")
    check(seen == ["ingest"], f"no analysis of an album that was not added: {seen}")


def test_choices_and_preview(tmp: str) -> None:
    print("the page's choices reach ingest_cd.py; a preview never commits [SPEC-CDI-040..050]")
    runner, db = _runner(tmp, "lib3")
    seen = []

    def fake_spawn(self, job_id, stage, argv):
        seen.append((stage, argv))
        return 0, '{"ok": true, "dry_run": true, "tracks": 2, "releases": []}'

    runner._spawn = fake_spawn.__get__(runner, jobmod.Runner)
    j = wait_for(runner, runner.submit("cd-rip", json.dumps(
        {"folder": "C:/rips/x", "release": "rel-1", "into": "C:/Music/A/B (2001)"})))
    argv = seen[-1][1]
    check(argv[argv.index("--release") + 1] == "rel-1", f"the chosen edition, got {argv}")
    check(argv[argv.index("--into") + 1] == "C:/Music/A/B (2001)", f"the album folder, got {argv}")
    j = wait_for(runner, runner.submit("cd-preview", json.dumps({"folder": "C:/rips/x"})))
    stage, argv = seen[-1]
    check(stage == "preview" and "--commit" not in argv and "--json" in argv, f"a preview, got {argv}")
    check(j["state"] == "done" and j["result"]["dry_run"] is True, f"its result is the job's, got {j}")


def main() -> int:
    test_skipped_names_it()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_folder_reaches_ingest_cd(tmp)
        test_failure_surfaces_as_failed(tmp)
        test_choices_and_preview(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs cd_rip: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
