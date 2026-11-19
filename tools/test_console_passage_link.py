#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The console's `/passage/<uid>` route `[SPEC-HOLD-045]`: Lempi's passage
page links here by uid, and the console sends it on to `/profile/<n>` under
its own library's number for that passage.

Tested two ways: `passage_by_uid` in process, including a library from
before uids; and a real spawned `console.py`, spawned the way
`test_console_csrf.py` spawns it, proving the redirect and the refusal.

    python tools/test_console_passage_link.py
"""

import http.client
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

UID = "da5e4a8b03ddf1f0beebf7c5a829592c"
OTHER = "0123456789abcdef0123456789abcdef"

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT,
    size_bytes INTEGER, mtime REAL, format TEXT, duration_ms INTEGER,
    first_seen TEXT, last_seen TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT, start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT, passage_uid TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
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


def library(path, with_uids=True):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA if with_uids else SCHEMA.replace(", passage_uid TEXT", ""))
    c.execute("INSERT INTO files (file_id, path) VALUES (1, 'x.mp3')")
    if with_uids:
        # Numbered unlike their order, so the answer is the row's own number.
        c.execute("INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, passage_uid) "
                  "VALUES (25556, 1, 'album', 0, 1000, ?1), (25555, 1, 'radio', 0, 1000, ?2)", (UID, OTHER))
    c.commit()
    return c


def test_lookup() -> None:
    print("passage_by_uid: this library's number for the uid, or None")
    with tempfile.TemporaryDirectory() as tmp:
        c = library(os.path.join(tmp, "lib.db"))
        check(console.passage_by_uid(c, UID) == 25556, "the album passage, by its own uid")
        check(console.passage_by_uid(c, OTHER) == 25555, "the radio passage, by its own")
        check(console.passage_by_uid(c, "f" * 32) is None, "a uid this library does not hold")
        c.close()
        c = library(os.path.join(tmp, "old.db"), with_uids=False)
        check(console.passage_by_uid(c, UID) is None, "a library from before uids has no answer, and does not raise")
        c.close()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.getheader("Location"), r.read().decode(errors="replace")
    finally:
        c.close()


def test_live() -> None:
    print("a live console.py: /passage/<uid> redirects to /profile/<n>, and refuses a uid it does not hold")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "lib.db")
        library(db).close()
        port = free_port()
        proc = subprocess.Popen([sys.executable, CONSOLE, db, "--port", str(port)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                cwd=console.REPO_ROOT)
        try:
            deadline, up = time.time() + 10, False
            while time.time() < deadline and not up:
                try:
                    up = get(port, "/console.css")[0] == 200
                except OSError:
                    time.sleep(0.1)
            if not check(up, "the console must come up within 10s"):
                return
            st, where, _ = get(port, f"/passage/{UID}")
            check(st == 302 and where == "/profile/25556", f"on to the profile by number: {st} {where}")
            st, _, body = get(port, "/profile/25556")
            check(st == 200 and "<" in body, f"and the profile page is there to land on: {st}")
            st, where, body = get(port, "/passage/" + "f" * 32)
            check(st == 404 and where is None and "has that uid" in body,
                  f"a uid this library does not hold is a 404 that says so: {st} {where}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    test_lookup()
    test_live()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console passage link: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
