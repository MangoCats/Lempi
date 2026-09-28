#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The console refuses cross-site POSTs `[SecurityReview C1 link 1]`.

`console.py` binds `127.0.0.1` only, but before this a page you merely
visited could POST to it: `do_POST` read the body as JSON whatever the
`Content-Type`, and checked neither `Origin` nor `Host`, so a bodyless or
`text/plain` cross-site POST reached every route -- mesh removal, shutdown,
`open-terminal`. The guard is `Handler._same_origin`.

Tested two ways: the guard's own logic against forged headers, in process;
and a real spawned `console.py` proving a forged-`Origin` POST is refused
while a same-origin one still lands. Spawned the way `test_console_system.py`
spawns it -- a scratch port, a scratch fixture, no real host or ssh.

    python tools/test_console_csrf.py
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
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CONSOLE = os.path.join(HERE, "console.py")
sys.path.insert(0, HERE)
import console  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


class FakeHeaders(dict):
    """`get` with a default, like `http.client`'s message object."""

    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


def guard(headers: dict, port: int = 5730) -> bool:
    """Run `_same_origin` with these request headers and nothing else."""
    h = Handler_stub()
    h.headers = FakeHeaders(headers)
    with mock.patch.dict(console.STATE, {"port": port}, clear=False):
        return console.Handler._same_origin(h)


class Handler_stub:
    pass


def test_guard_logic() -> None:
    print("_same_origin: forged Origin/Host refused, loopback and no-Origin allowed")
    P = 5730
    # A visited page: a foreign Origin. Refused whatever else it sends.
    check(not guard({"Origin": "http://evil.example", "Host": f"127.0.0.1:{P}"}, P),
          "a foreign Origin is refused even with a loopback Host")
    check(not guard({"Origin": "https://bank.example", "Host": f"localhost:{P}"}, P),
          "a foreign https Origin is refused")
    # DNS rebinding: our own Origin is impossible to forge, but the Host is
    # the attacker's name resolving to 127.0.0.1. Refused on Host.
    check(not guard({"Host": "attacker.example"}, P),
          "a non-loopback Host (DNS rebinding) is refused")
    check(not guard({"Host": f"evil.example:{P}"}, P),
          "a non-loopback Host with our port is still refused")
    # The console's own page: same-origin. Both header forms it uses.
    check(guard({"Origin": f"http://127.0.0.1:{P}", "Host": f"127.0.0.1:{P}"}, P),
          "our own Origin + Host is allowed")
    check(guard({"Origin": f"http://localhost:{P}", "Host": f"localhost:{P}"}, P),
          "localhost Origin + Host is allowed")
    # A non-browser client (curl, the test harness): no Origin, loopback Host.
    check(guard({"Host": f"127.0.0.1:{P}"}, P),
          "no Origin with a loopback Host is allowed (a non-browser client)")
    # No-body POSTs the UI makes carry no Content-Type: the guard must not
    # depend on it. Same-origin with no content type is allowed.
    check(guard({"Origin": f"http://127.0.0.1:{P}", "Host": f"127.0.0.1:{P}"}, P),
          "a bodyless same-origin POST (no Content-Type) is allowed")


# -- live: a real spawned console.py -----------------------------------------

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT,
    size_bytes INTEGER, mtime REAL, format TEXT, duration_ms INTEGER,
    first_seen TEXT, last_seen TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT, start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT, weight REAL, source TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
CREATE TABLE id_checks (passage_id INTEGER);
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(port, timeout=10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            c.request("GET", "/api/system")
            c.getresponse().read()
            c.close()
            return True
        except (OSError, ConnectionError):
            time.sleep(0.1)
    return False


def post(port, path, headers, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request("POST", path, body=body, headers=headers)
        r = c.getresponse()
        return r.status, r.read().decode()
    finally:
        c.close()


def get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        # A `Host` in `headers` overrides http.client's own, so the test can
        # forge the DNS-rebinding case.
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read().decode()
    finally:
        c.close()


def test_live() -> None:
    print("a live console.py: a forged-Origin POST is refused, a same-origin one lands")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "lib.db")
        con = sqlite3.connect(db)
        con.executescript(SCHEMA)
        con.commit()
        con.close()
        port = free_port()
        proc = subprocess.Popen([sys.executable, CONSOLE, db, "--port", str(port)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                cwd=console.REPO_ROOT)
        try:
            check(wait_up(port), "the console must come up within 10s")
            # The attack shape: foreign Origin, text/plain (no CORS preflight).
            body = json.dumps({"remote": "attacker@evil:/x"})
            st, _ = post(port, "/api/remote", {"Content-Type": "text/plain",
                                               "Origin": "http://evil.example"}, body)
            check(st == 403, f"a cross-site POST must be refused, got {st}")
            _, back = get(port, "/api/remote")
            check("evil" not in back, f"nothing must have been stored, got {back}")
            # A bodyless cross-site POST to a no-Content-Type route: also refused.
            st, _ = post(port, "/api/system/shutdown", {"Origin": "http://evil.example"})
            check(st == 403, f"a bodyless cross-site POST must be refused too, got {st}")
            # The real page: same-origin. It lands.
            st, _ = post(port, "/api/remote", {"Content-Type": "application/json",
                                               "Origin": f"http://127.0.0.1:{port}",
                                               "Host": f"127.0.0.1:{port}"},
                         json.dumps({"remote": "me@host:/lib.db"}))
            check(st == 200, f"a same-origin POST must still work, got {st}")
            _, back = get(port, "/api/remote")
            check("me@host" in back, f"the same-origin write must land, got {back}")

            # [SecurityReview2 R2] GET is guarded too. A DNS-rebinding read
            # (foreign Host) is refused; a normal loopback GET is served.
            st, _ = get(port, "/api/remote", {"Host": "attacker.example"})
            check(st == 403, f"a foreign-Host GET must be refused, got {st}")
            st, _ = get(port, "/api/system", {"Origin": "http://evil.example"})
            check(st == 403, f"a foreign-Origin GET must be refused, got {st}")
            st, body = get(port, "/api/system")
            check(st == 200, f"a normal loopback GET must be served, got {st}")
            # handoff/ensure moved to POST: the GET is gone, the POST answers.
            st, _ = get(port, "/api/handoff/ensure")
            check(st == 404, f"GET /api/handoff/ensure must be gone, got {st}")
            st, _ = post(port, "/api/handoff/ensure", {"Host": f"127.0.0.1:{port}"})
            check(st == 200, f"POST /api/handoff/ensure must answer, got {st}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    test_guard_logic()
    test_live()
    if FAILED:
        print(f"\n{len(FAILED)} check(s) failed")
        return 1
    print("\nconsole CSRF: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
