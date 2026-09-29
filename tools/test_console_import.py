#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The CD import page, live `[SPEC056]`: a real `console.py` on a scratch port,
a real rip in its inbox (a WAV ffmpeg makes, a CUE naming it, EAC's log), and a
local server standing in for MusicBrainz -- so the page's whole path runs
through HTTP, as a person's clicks would drive it, with no network.

    python tools/test_console_import.py
"""
import http.client
import http.server
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cd_toc  # noqa: E402
import test_console_system as tcs  # noqa: E402  -- free_port, wait_up
import test_ingest_cd as tic  # noqa: E402  -- the catalogue's schema

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def call(port, method, path, body=None, timeout=30):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        data = None if body is None else json.dumps(body).encode()
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        r = conn.getresponse()
        raw = r.read()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw.decode("utf-8", "replace")
    finally:
        conn.close()


def wait_job(port, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, j = call(port, "GET", f"/api/jobs/{job_id}")
        if isinstance(j, dict) and j.get("state") in ("done", "failed"):
            return j
        time.sleep(0.5)
    return {"state": "timeout"}


RELEASE = {"id": "rel-live", "title": "Album", "date": "2001", "country": "XE",
           "artist-credit": [{"name": "Artist"}],
           "media": [{"tracks": [{"position": n, "number": str(n), "title": f"Song {n}", "length": 2000,
                                  "recording": {"id": f"rec-live-{n}", "title": f"Song {n}",
                                                "artist-credit": [{"name": "Artist",
                                                                   "artist": {"id": "art-live", "name": "Artist"}}]}}
                                 for n in (1, 2)]}]}


class FakeMusicBrainz(http.server.BaseHTTPRequestHandler):
    """Any Disc ID lookup answers with the one release above."""
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"releases": [RELEASE]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_rip(inbox):
    wav = os.path.join(inbox, "Artist - Album.wav")
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=330:duration=4", "-ar", "44100", "-ac", "2", wav], check=True)
    with open(os.path.join(inbox, "Artist - Album.cue"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write('PERFORMER "Artist"\nTITLE "Album"\nFILE "Artist - Album.wav" WAVE\n'
                '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n')
    with open(os.path.join(inbox, "Artist - Album.log"), "w", encoding="utf-8") as f:
        f.write("Copy OK\n\nNo errors occurred\n\nTrack  1  accurately ripped (confidence 9)\n"
                "Track  2  accurately ripped (confidence 9)\n")


def test_the_page_live():
    print("a live console: set the inbox, see the rip, preview it, add it, and the refusals")
    if not shutil.which("ffmpeg"):
        check(False, "this test needs ffmpeg")
        return
    mb = http.server.HTTPServer(("127.0.0.1", 0), FakeMusicBrainz)
    threading.Thread(target=mb.serve_forever, daemon=True).start()
    tmp = tempfile.mkdtemp()
    music, inbox = os.path.join(tmp, "Music"), os.path.join(tmp, "Music", "_Rips")
    os.makedirs(music)
    db = os.path.join(tmp, "lib.db")
    c = sqlite3.connect(db)
    c.executescript(tic.SCHEMA + "CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);"
                    "CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);")
    c.commit()
    c.close()
    port = tcs.free_port()
    env = dict(os.environ, LEMPI_MUSICBRAINZ_URL=f"http://127.0.0.1:{mb.server_address[1]}")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "console.py"), db, "--port", str(port),
                             "--root", music], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env=env, cwd=os.path.dirname(HERE))
    try:
        check(tcs.wait_up(port), "the console comes up")
        st, page = call(port, "GET", "/import")
        check(st == 200 and "Import a CD" in page and 'href="/import"' in page, "the page is served, in the nav")
        st, g = call(port, "GET", "/api/import/guide")
        check(st == 200 and 'id="ripping-a-disc"' in g["html"] and g["anchors"]["rip"] == "ripping-a-disc",
              "the guide comes rendered, with its anchors")
        st, s = call(port, "GET", "/api/import/state")
        check(s["inbox"] == inbox and not s["inbox_exists"], f"the default inbox, not yet made: {s['inbox']}")
        st, s = call(port, "POST", "/api/import/inbox", {"path": inbox})
        check(st == 200 and s["inbox_exists"], "use this folder makes it")
        make_rip(inbox)
        st, s = call(port, "GET", "/api/import/state")
        cards = s["rips"]
        check(len(cards) == 1 and cards[0]["state"] == "finished" and cards[0]["verdict"]["level"] == "ok",
              f"the rip appears, finished and good: {cards}")

        st, bad = call(port, "POST", "/api/import/preview", {"id": os.path.join("..", "x.cue")})
        check(st == 400, "a rip outside the inbox is refused")
        st, p = call(port, "POST", "/api/import/preview", {"id": cards[0]["id"]})
        check(st == 200 and p["id"] == os.path.join("Artist - Album", "Artist - Album.cue"),
              f"opening it stages it in its own folder: {p}")
        j = wait_job(port, p["job_id"])
        r = j.get("result") or {}
        check(j["state"] == "done" and r.get("dry_run") and r["releases"][0]["id"] == "rel-live",
              f"the preview finds the edition: {j}")
        check(sorted(os.listdir(p["folder"])) == ["Artist - Album.cue", "Artist - Album.log", "Artist - Album.wav"],
              f"and writes nothing: {os.listdir(p['folder'])}")

        st, bad = call(port, "POST", "/api/import/add", {"folder": p["folder"], "into": os.path.join("..", "x")})
        check(st == 400, "an album folder outside the music folder is refused")
        st, bad = call(port, "POST", "/api/import/add", {"folder": music, "into": "A"})
        check(st == 400, "a folder that is not a rip in the inbox is refused")
        name = r["releases"][0]["folder"]
        st, a = call(port, "POST", "/api/import/add", {"folder": p["folder"], "release": "rel-live", "into": name})
        j = wait_job(port, a["job_id"])
        res = j.get("result") or {}
        home = os.path.join(music, name)
        check(j["state"] == "done" and res.get("ok") and res["identified"] == 2, f"added, both identified: {j}")
        check(sorted(os.listdir(home)) == ["Artist - Album.cue", "Artist - Album.log", "Artist - Album.mp3"],
              f"filed in the music folder, WAV gone: {os.listdir(home) if os.path.isdir(home) else home}")
        st, s = call(port, "GET", "/api/import/state")
        check(s["rips"] == [], f"and the inbox is empty again: {s['rips']}")
        c = sqlite3.connect(db)
        n = c.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        c.close()
        check(n == 4, f"two tracks, both kinds, in the catalogue: {n}")
    finally:
        call(port, "POST", "/api/system/shutdown")
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        mb.shutdown()


def main() -> int:
    test_the_page_live()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console import: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
