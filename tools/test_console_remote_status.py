#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `console.py`'s `remote_status()` `[SPEC-DF-116..118]`.

`console._peek` (the one function that ever shells out) is faked here so
these run with no `ssh`, no network, and no real remote -- what is under
test is the three-outcome logic `[SPEC-DF-116..118]` asks for: unreachable,
in agreement, diverged. The subprocess plumbing itself is exercised for real
by `test_remote_peek.py`.

    python tools/test_console_remote_status.py
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import console  # noqa: E402

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT, size_bytes INTEGER, mtime REAL, format TEXT,
    duration_ms INTEGER, first_seen TEXT, last_seen TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT, start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT,
    weight REAL DEFAULT 1.0, source TEXT);
"""

REC = "11111111-1111-1111-1111-111111111111"

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


def build():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-a','/m/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES (1,1,'radio',1000,200000,0,900,-1.0,'src')")
    c.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'inherited:mulib')", (REC,))
    return c


class FakeJobs:
    def __init__(self, remote):
        self._remote = remote

    def get_remote(self):
        return self._remote


def main() -> int:
    real_peek = console._peek

    print("no remote configured: nothing to check, and no subprocess touched")
    console.STATE["jobs"] = FakeJobs(None)
    console._peek = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called"))
    check(console.remote_status(build(), 1) == {"remote": None}, "expected the bare not-configured shape")

    print("lempipi unreachable: reported, and never raises")
    console.STATE["jobs"] = FakeJobs("pi@lempipi:/srv/library/library.db")
    console._peek = lambda remote, kind, args, timeout=12.0: {"ok": False, "error": "no route to host"}
    r = console.remote_status(build(), 1)
    check(r["remote"] == "pi@lempipi:/srv/library/library.db", f"got {r}")
    check(r["reachable"] is False, f"expected unreachable, got {r}")
    check(r["checks"] == {}, f"an unreachable remote must offer nothing to accept, got {r}")

    print("lempipi reachable and in agreement: reachable, nothing diverged")
    def agree(remote, kind, args, timeout=12.0):
        if kind == "id_review":
            return {"ok": True, "current": {"mbid": REC}}
        return {"ok": True, "current": {"start_ms": 1000, "end_ms": 200000,
                                         "lead_in_ms": 0, "lead_out_ms": 900, "gain_db": -1.0}}
    console._peek = agree
    r = console.remote_status(build(), 1)
    check(r["reachable"] is True, f"got {r}")
    check(all(not c["diverged"] for c in r["checks"].values()), f"nothing should diverge, got {r['checks']}")

    print("lempipi diverged on the boundary only: exactly that one check is flagged")
    def diverged_boundary(remote, kind, args, timeout=12.0):
        if kind == "id_review":
            return {"ok": True, "current": {"mbid": REC}}
        return {"ok": True, "current": {"start_ms": 2000, "end_ms": 190000,
                                         "lead_in_ms": 250, "lead_out_ms": 1200, "gain_db": -2.0}}
    console._peek = diverged_boundary
    r = console.remote_status(build(), 1)
    check(r["checks"]["id_review"]["diverged"] is False, f"id must still agree, got {r['checks']}")
    check(r["checks"]["boundary_review"]["diverged"] is True, f"boundary must diverge, got {r['checks']}")
    check(r["checks"]["boundary_review"]["current"]["start_ms"] == 2000, f"got {r['checks']}")

    print("a remote row of None (nothing there) is not treated as a divergence to offer")
    console._peek = lambda remote, kind, args, timeout=12.0: {"ok": True, "current": None}
    r = console.remote_status(build(), 1)
    check(all(not c["diverged"] for c in r["checks"].values()),
          f"nothing concrete to offer must not be flagged as diverged, got {r['checks']}")

    console._peek = real_peek

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console remote_status: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
