#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `finish-folder` job kind `[SPEC-FIN-050..055]`.

The tools it runs are each tested on their own; this checks the layer above:
the steps in order, each reaching its tool with the right argv, the edition
first and the job stopping when it fails, a later step's failure said while
the rest still run, the edition not applied again once it is in place, and the
player asked to rebuild. A real `Runner`, with `_spawn` and the player faked --
so nothing here asks MusicBrainz, the archive, or the desktop's own player.

    python tools/test_jobs_finish_folder.py
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

FOLDER = os.path.normpath("C:/Music/Traffic/Smiling Phases (1991)")
REL = "7271f4eb-addb-44f0-a098-62fd9104e920"
REC = "11111111-1111-1111-1111-111111111111"

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT);
CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, artist TEXT, album TEXT,
    track_no INTEGER, disc_no INTEGER, has_art INTEGER);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
    start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER, boundary_src TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT, release_date TEXT, country TEXT);
CREATE TABLE file_releases (audio_md5 TEXT PRIMARY KEY, release_mbid TEXT, source TEXT, decided_at TEXT);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
CREATE TABLE id_checks (passage_id INTEGER);
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def wait_for(runner, job_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = runner.job(job_id)
        if j and j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def runner_for(tmp: str, name: str, named: bool = False):
    """A library with one file of the album, its two passages on a
    placeholder -- or, `named`, already finished as the edition `REL`."""
    db = os.path.join(tmp, f"{name}.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1, 'md5-1', ?)", (os.path.join(FOLDER, "1-01. Paper Sun.mp3"),))
    c.execute("INSERT INTO file_tags VALUES (1, 'Paper Sun', 'Traffic', 'Smiling Phases', 1, 1, 0)")
    mbid = REC if named else "local:audio:md5-1"
    for pid, kind in ((10, "radio"), (11, "album")):
        c.execute("INSERT INTO passages VALUES (?, 1, ?, 0, 257000, NULL, 'ingest:whole-file')", (pid, kind))
        c.execute("INSERT INTO passage_recordings VALUES (?, ?)", (pid, mbid))
    if named:
        c.execute("INSERT INTO releases VALUES (?, 'Smiling Phases', '1991', 'US')", (REL,))
        c.execute("INSERT INTO file_releases VALUES ('md5-1', ?, 'folder:chosen', 't')", (REL,))
    c.commit()
    c.close()
    return jobmod.Runner(db, os.path.join(tmp, f"{name}.console.db")), db


ACCEPTED = json.dumps({"ok": True, "release": REL, "matched": 1, "total_files": 1, "applied": 2, "linked": 1})
TOOLS = {"flavor": (0, "1 files, 0 cached, 1 to extract, 1 jobs\n\n1 extracted, 0 failed in 0.1 min"),
         "amplitude": (0, '{"ok": true, "analyzed": 1, "failed": 0}'),
         "cover": (0, "1 release(s) to ask about"), "works": (0, "done"), "load-works": (0, "done")}


def fake(runner, answers):
    seen = []

    def spawn(self, job_id, stage, argv):
        seen.append((stage, argv))
        return answers[stage]
    runner._spawn = spawn.__get__(runner, jobmod.Runner)
    return seen


def submit(runner):
    return wait_for(runner, runner.submit("finish-folder", json.dumps({"folder": FOLDER, "release_mbid": REL})))


def test_every_step_in_order(tmp: str) -> None:
    print("the edition first, then flavor, lead-in and out, cover and works, then the player")
    runner, db = runner_for(tmp, "all")
    seen = fake(runner, {"edition": (0, ACCEPTED), **TOOLS})
    j = submit(runner)
    check(j["state"] == "done", f"done: {j['state']} {[e['text'] for e in j['events'] if e['kind'] == 'error']}")
    check([s for s, _ in seen] == ["edition", "flavor", "amplitude", "cover", "works", "load-works"],
          f"the steps in order: {[s for s, _ in seen]}")
    argv = dict(seen)
    ed = argv["edition"]
    check("suggest_release.py" in ed[1] and ed[2] == db and ed[3] == FOLDER
          and ed[ed.index("--accept") + 1] == REL and "--commit" in ed, f"the edition picked, accepted: {ed}")
    for stage in ("flavor", "amplitude"):
        check(argv[stage][argv[stage].index("--folder") + 1] == FOLDER, f"{stage} scoped to the album: {argv[stage]}")
    cv = argv["cover"]
    check("fetch_cover_art.py" in cv[1] and cv[cv.index("--release") + 1] == REL, f"the edition's cover: {cv}")
    wk = argv["works"]
    cache = os.path.join(tmp, "work_relations.db")
    check("fetch_works.py" in wk[1] and wk[wk.index("--folder") + 1] == FOLDER
          and wk[wk.index("--cache") + 1] == cache, f"this album's works alone, the cache beside the library: {wk}")
    check(j["result"]["edition"]["applied"] == 2 and j["result"]["reloaded"] is True and j["result"]["failed"] == [],
          f"said in the result: {j['result']}")
    check(j["result"]["status"]["folder"] == FOLDER and "stages" in j["result"]["status"],
          "and the folder's stages, measured after")
    check(any("asked to rebuild" in e["text"] for e in j["events"]), "the player asked to rebuild")


def test_the_edition_failing_stops_it(tmp: str) -> None:
    print("an edition that fails, or matches nothing, stops the job there")
    for name, answer in (("refused", (1, '{"ok": false, "error": "release not found"}')),
                         ("nothing", (0, json.dumps({"ok": True, "matched": 0, "applied": 0})))):
        runner, _ = runner_for(tmp, name)
        seen = fake(runner, {"edition": answer, **TOOLS})
        j = submit(runner)
        check(j["state"] == "failed" and [s for s, _ in seen] == ["edition"],
              f"{name}: failed after the edition alone: {j['state']} {[s for s, _ in seen]}")
    check(any("matched a track" in e["text"] for e in j["events"] if e["kind"] == "error"),
          "a match of nothing says so")


def test_a_later_failure_is_said_and_the_rest_run(tmp: str) -> None:
    print("the archive not answering fails the job, says why, and the works still run")
    runner, _ = runner_for(tmp, "cover")
    seen = fake(runner, {"edition": (0, ACCEPTED), **TOOLS, "cover": (1, "the archive did not answer")})
    j = submit(runner)
    errors = [e["text"] for e in j["events"] if e["kind"] == "error"]
    check(j["state"] == "failed" and j["result"]["failed"] == ["cover"], f"failed, on the cover: {j['result']}")
    check(any("Cover Art Archive did not answer" in t for t in errors), f"and says so: {errors}")
    check([s for s, _ in seen][-2:] == ["works", "load-works"], f"the works still ran: {[s for s, _ in seen]}")


def test_an_edition_in_place_is_not_applied_again(tmp: str) -> None:
    print("the same edition, already in place with every track named: not applied again")
    runner, _ = runner_for(tmp, "again", named=True)
    seen = fake(runner, {"edition": (0, ACCEPTED), **TOOLS})
    j = submit(runner)
    check("edition" not in [s for s, _ in seen] and j["result"]["edition"] == {"ok": True, "skipped": True},
          f"the edition left as it is: {[s for s, _ in seen]} {j['result'].get('edition')}")
    check(any("not applied again" in e["text"] for e in j["events"]), "and says why")
    check([s for s, _ in seen] == ["flavor", "amplitude", "cover", "works", "load-works"],
          f"the rest still run: {[s for s, _ in seen]}")
    # A different edition is the person's new choice, and is applied.
    other = "bea74ee9-3719-4c55-80d2-02a7a69b7f79"
    seen.clear()
    j = wait_for(runner, runner.submit("finish-folder", json.dumps({"folder": FOLDER, "release_mbid": other})))
    check(seen and seen[0][0] == "edition" and seen[0][1][seen[0][1].index("--accept") + 1] == other,
          f"a different edition is applied: {[s for s, _ in seen]}")


def test_a_local_edition(tmp: str) -> None:
    print("a local edition: its own tool, from a spec file; the cover taken with it, the archive not asked")
    local = {"title": "Future Sound", "artist": "stellarmusic", "date": "2014",
             "tracks": [{"audio_md5": "a" * 32, "position": 1, "disc": None, "title": "Big Dreams", "artist": ""}]}
    applied = json.dumps({"ok": True, "local": True, "release": "local:release:" + "b" * 32, "title": "Future Sound",
                          "named": 1, "applied": 1, "matched": 1, "cover": "found:folder:cover.jpg"})
    runner, db = runner_for(tmp, "local")
    specs = []
    seen = []

    def spawn(self, job_id, stage, argv):
        seen.append((stage, argv))
        if stage == "edition":
            with open(argv[argv.index("--spec") + 1], encoding="utf-8") as fh:
                specs.append(json.load(fh))
            return 0, applied
        return TOOLS[stage]
    runner._spawn = spawn.__get__(runner, jobmod.Runner)
    j = wait_for(runner, runner.submit("finish-folder", json.dumps({"folder": FOLDER, "local": local})))
    check(j["state"] == "done", f"done: {j['state']} {[e['text'] for e in j['events'] if e['kind'] == 'error']}")
    check([s for s, _ in seen] == ["edition", "flavor", "amplitude", "works", "load-works"],
          f"no archive asked, the rest in order: {[s for s, _ in seen]}")
    ed = dict(seen)["edition"]
    check("local_edition.py" in ed[1] and ed[2] == db and ed[3] == FOLDER and "--commit" in ed,
          f"its own tool, committing: {ed}")
    check(specs == [local], f"the spec as the person sent it: {specs}")
    check(not os.path.exists(ed[ed.index("--spec") + 1]), "and its file removed after")
    check(any("taken from the files" in e["text"] for e in j["events"]), "the cover said taken with the edition")
    check(j["result"]["release"].startswith("local:release:"), f"the release recorded: {j['result'].get('release')}")
    # Refused by the tool: the job stops there, as for an edition from MusicBrainz.
    runner, _ = runner_for(tmp, "local-refused")
    seen = fake(runner, {"edition": (1, '{"ok": false, "error": "two tracks share a place: 2"}'), **TOOLS})
    j = wait_for(runner, runner.submit("finish-folder", json.dumps({"folder": FOLDER, "local": local})))
    check(j["state"] == "failed" and [s for s, _ in seen] == ["edition"]
          and j["result"]["edition"]["error"].startswith("two tracks"), f"refused, stopped: {j['state']} {seen}")


def test_the_edition_is_never_cut_short() -> None:
    print("stopping the job lets the edition finish: it writes in one transaction")
    check(jobmod.stop_policy("finish-folder", "edition") == ("atomic", 0), jobmod.stop_policy("finish-folder", "edition"))
    check(jobmod.stop_policy("finish-folder", "flavor")[0] == "kill", "analysis stops at once, as anywhere")


def main() -> int:
    asked = []
    old = jobmod.lempi_control.reload_lempi_library
    jobmod.lempi_control.reload_lempi_library = lambda *a, **k: asked.append(1) or True
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            test_every_step_in_order(tmp)
            test_the_edition_failing_stops_it(tmp)
            test_a_later_failure_is_said_and_the_rest_run(tmp)
            test_an_edition_in_place_is_not_applied_again(tmp)
            test_a_local_edition(tmp)
        test_the_edition_is_never_cut_short()
    finally:
        jobmod.lempi_control.reload_lempi_library = old
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs finish_folder: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
