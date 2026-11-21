#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The console's *Finish an album* routes `[SPEC-FIN-010..050]`, against a real
spawned `console.py`.

The page and its index are served; a folder's status is read; a run is refused
without a real folder, a release id, or a catalogued file in it, and refused
cross-site; and the Jobs page links to it. One run is submitted for real, with
MusicBrainz pointed at a port nothing answers, so the job reaches the real
tools, fails at the edition, and runs nothing after it -- no network, no
analysis, the catalogue untouched.

    python tools/test_console_finish.py
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
CONSOLE = os.path.join(HERE, "console.py")
sys.path.insert(0, HERE)
import console  # noqa: E402

FAILED = []
REL = "7271f4eb-addb-44f0-a098-62fd9104e920"

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, size_bytes INTEGER, mtime REAL,
    format TEXT, duration_ms INTEGER, first_seen TEXT, last_seen TEXT);
CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, artist TEXT, album TEXT, track_no INTEGER,
    disc_no INTEGER, has_art INTEGER, scanned_at TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER,
    end_ms INTEGER, lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT, passage_uid TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT, weight REAL, source TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
CREATE TABLE id_checks (passage_id INTEGER);
"""


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        text = r.read().decode("utf-8", "replace")
        try:
            return r.status, json.loads(text)
        except ValueError:
            return r.status, text
    finally:
        c.close()


def main() -> int:
    print("a live console.py: the Finish an album page, its routes, and a run that reaches the job")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        album = os.path.join(tmp, "Traffic", "Smiling Phases (1991)")
        empty = os.path.join(tmp, "Nobody", "Nothing (2000)")
        os.makedirs(album)
        os.makedirs(empty)
        db = os.path.join(tmp, "library.db")
        c = sqlite3.connect(db)
        c.executescript(SCHEMA)
        c.execute("INSERT INTO files VALUES (1, 'md5-1', ?, 1, 0, 'mp3', 257000, '2026-11-13T14:48:25', 't')",
                  (os.path.join(album, "1-01. Paper Sun.mp3"),))
        c.execute("INSERT INTO file_tags VALUES (1, 'Paper Sun', 'Traffic', 'Smiling Phases', 1, 1, 0, 't')")
        for pid, kind in ((10, "radio"), (11, "album")):
            c.execute("INSERT INTO passages VALUES (?, 1, ?, 0, 257000, NULL, NULL, NULL, 'ingest:whole-file', ?)",
                      (pid, kind, f"{pid:032x}"))
            c.execute("INSERT INTO passage_recordings VALUES (?, 'local:audio:md5-1', 1.0, 'local:ingest')", (pid,))
        c.commit()
        c.close()
        port, dead = free_port(), free_port()
        env = dict(os.environ, LEMPI_MUSICBRAINZ_URL=f"http://127.0.0.1:{dead}",
                   LEMPI_MB_PACE_FILE=os.path.join(tmp, "pace"))
        proc = subprocess.Popen([sys.executable, CONSOLE, db, "--port", str(port)], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, cwd=console.REPO_ROOT, env=env)
        try:
            deadline, up = time.time() + 15, False
            while time.time() < deadline and not up:
                try:
                    up = req(port, "GET", "/console.css")[0] == 200
                except OSError:
                    time.sleep(0.1)
            if not check(up, "the console must come up within 15s"):
                return 1

            st, page = req(port, "GET", "/finish")
            check(st == 200 and "Finish an album" in page and "/api/finish/run" in page, f"the page is served: {st}")
            # While a job runs, the stages are read again and redrawn in place,
            # not only when it ends; and the job's log is kept when it does.
            watch = page.split("function watchRun", 1)[-1].split("\nfunction ", 1)[0]
            check("setInterval" in watch and "/api/finish/status" in watch and "standing" in watch
                  and "clearInterval" in watch, "the stages are refreshed while a job runs, and the refresh stopped after")
            check("album(runBox)" in watch and "outcome(job)" in watch, "the log is carried over, its outcome said")
            st, d = req(port, "GET", "/api/finish/folders")
            check(st == 200 and [f["folder"] for f in d] == [album], f"the index lists the album: {d}")
            st, d = req(port, "GET", "/api/finish/status?folder=" + album.replace(" ", "%20"))
            check(st == 200 and d["files"] == 1 and d["next"][0] == "edition" and not d["finished"],
                  f"the album's stages: {d}")
            check({"measured_here", "from_elsewhere"} <= set(d["stages"]["flavor"]),
                  f"flavor says where its count comes from: {d['stages']['flavor']}")
            check(req(port, "GET", "/api/finish/status")[0] == 400, "a status with no folder is refused")

            for body, why in (({"folder": album, "release_mbid": "Smiling Phases"}, "a release that is no id"),
                              ({"folder": os.path.join(tmp, "nowhere"), "release_mbid": REL}, "a folder not on disk"),
                              ({"folder": empty, "release_mbid": REL}, "a folder with nothing catalogued in it"),
                              ({}, "nothing at all")):
                st, d = req(port, "POST", "/api/finish/run", body)
                check(st == 400 and "error" in d, f"{why} is refused: {st} {d}")
            st, _ = req(port, "POST", "/api/finish/run", {"folder": album, "release_mbid": REL},
                        {"Origin": "http://evil.example"})
            check(st == 403, f"a cross-site run is refused: {st}")

            # A local edition [SPEC-FIN-080..085]: the form's files read, its
            # shape refused here, and its fit refused by the tool, in the job.
            check("localForm" in page and "/api/finish/tracks" in page, "the page has the local edition's form")
            st, d = req(port, "GET", "/api/finish/tracks?folder=" + album.replace(" ", "%20"))
            check(st == 200 and len(d["tracks"]) == 1 and d["tracks"][0]["placeholder"]
                  and d["tracks"][0]["tag_title"] == "Paper Sun" and d["album_tag"] == "Smiling Phases",
                  f"the form's files: {d}")
            check(req(port, "GET", "/api/finish/tracks")[0] == 400, "the form's files with no folder are refused")
            for local, why in (({"title": "", "artist": "Traffic"}, "no title"),
                               ({"title": "T", "artist": "Traffic", "date": "1991-ish"}, "a date that is not one"),
                               ({"title": "T", "artist": "Traffic", "tracks": [{"audio_md5": "x"}]}, "a track naming no file"),
                               ("Smiling Phases", "an edition that is not an object")):
                st, d = req(port, "POST", "/api/finish/run", {"folder": album, "local": local})
                check(st == 400 and "error" in d, f"a local edition with {why} is refused: {st} {d}")
            st, d = req(port, "POST", "/api/finish/run", {"folder": album, "local": {
                "title": "Smiling Phases", "artist": "Traffic", "tracks": [{"audio_md5": "f" * 32}]}})
            check(st == 200 and "job_id" in d, f"a well-formed local edition is a job: {st} {d}")
            deadline = time.time() + 60
            while time.time() < deadline:
                job = req(port, "GET", f"/api/jobs/{d['job_id']}")[1]
                if job["state"] not in ("queued", "running"):
                    break
                time.sleep(0.3)
            stages = [e["text"] for e in job["events"] if e["kind"] == "stage"]
            check(job["state"] == "failed" and stages == ["edition"]
                  and "name no file" in ((job.get("result") or {}).get("edition") or {}).get("error", ""),
                  f"the tool refuses a track not in the folder, and nothing after runs: {job['state']} {stages} "
                  f"{job.get('result')}")
            st, jobs_page = req(port, "GET", "/jobs")
            check(st == 200 and "/finish?folder=" in jobs_page and "finish this album" in jobs_page,
                  "the Jobs page links its Not analyzed list and its induct to the page")

            st, d = req(port, "POST", "/api/finish/run", {"folder": album, "release_mbid": REL.upper()})
            check(st == 200 and "job_id" in d, f"a real run is a job: {st} {d}")
            job = None
            deadline = time.time() + 90
            while time.time() < deadline:
                job = req(port, "GET", f"/api/jobs/{d['job_id']}")[1]
                if job["state"] not in ("queued", "running"):
                    break
                time.sleep(0.5)
            check(job and job["kind"] == "finish-folder" and job["state"] == "failed",
                  f"it reaches the edition and fails there, MusicBrainz unreachable: {job and job['state']}")
            target = json.loads(job["target"]) if job else {}
            check(target == {"folder": os.path.normpath(album), "release_mbid": REL},
                  f"the release id as MusicBrainz writes it, the folder as the catalogue does: {target}")
            stages = [e["text"] for e in job["events"] if e["kind"] == "stage"] if job else []
            check(stages == ["edition"], f"and nothing after the edition ran: {stages}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        c = sqlite3.connect(db)
        check(c.execute("SELECT COUNT(*) FROM passage_recordings WHERE mbid LIKE 'local:%'").fetchone()[0] == 2,
              "the catalogue is as it was")
        c.close()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console finish: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
