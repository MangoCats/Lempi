#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `repair_library.py` `[GDE-INT-170]`: a damaged and a missing hub
file each fetched from the speaker that holds it whole -- passing over one
whose own check found it damaged, one that has not checked it, and one whose
copy no longer matches -- proven on arrival, and put in place, the damaged
copy kept aside. A dry run changes nothing; a file no speaker holds whole is
said and left; what arrives wrong is refused.

A fake fleet answers here, so no node is needed.

    python tools/test_repair_library.py
"""

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import repair_library as rl  # noqa: E402
import verify_library as vl  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Fleet:
    """Each speaker's checks and files, as ssh would find them."""

    def __init__(self, nodes):
        self.nodes = nodes            # host -> {md5: (path, outcome, bytes)}
        self.fetched = []

    def run(self, host, cmd, stdout=None, timeout=60):
        files = self.nodes.get(host)
        if files is None:
            return 255, "ssh: connect to host: timed out"
        if cmd.startswith("sqlite3"):
            md5 = re.search(r"[0-9a-f]{32}", cmd).group(0)
            if md5 not in files:
                return 0, ""
            path, outcome, _b = files[md5]
            return 0, f"{path}|{outcome}|2026-10-25T00:00:00Z"
        path = cmd.split("-- ", 1)[1].strip("'")
        held = next((v for v in files.values() if v[0] == path), None)
        if held is None:
            return 1, "No such file"
        if cmd.startswith("sha256sum"):
            return 0, f"{sha(held[2])}  {path}"
        if cmd.startswith("cat"):
            self.fetched.append(host)
            stdout.write(held[2])
            return 0, ""
        return 127, "unknown command"


def no_hold(host, say=None, seconds=None):
    return contextlib.nullcontext()


def library(tmp, rows):
    p = os.path.join(tmp, "library.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, sha256 TEXT)")
    c.executemany("INSERT INTO files VALUES (?, ?, ?, ?)", rows)
    c.commit()
    c.close()
    return p


def run(plan, fleet, go):
    out = []
    old = rl.say
    rl.say = out.append
    try:
        rc = rl.repair(plan, go=go, run=fleet.run, today="2026-10-25", hold=no_hold)
    finally:
        rl.say = old
    return rc, out


def main():
    print("repair_library")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        music = os.path.join(tmp, "music")
        os.makedirs(music)
        good_rot, good_gone, good_lost = b"rot-good-bytes", b"gone-good-bytes", b"lost-good-bytes"
        rot, gone, lost = (os.path.join(music, n + ".mp3") for n in ("rot", "gone", "lost"))
        with open(rot, "wb") as fh:
            fh.write(b"rot-BAD-bytes")
        md5 = {"rot": "a" * 32, "gone": "b" * 32, "lost": "c" * 32}
        lib = library(tmp, [(1, md5["rot"], rot, sha(good_rot)), (2, md5["gone"], gone, sha(good_gone)),
                            (3, md5["lost"], lost, sha(good_lost))])
        verify = {"checked_at": "2026-10-25T03:30:00-04:00", "files": 3, "ok": 0, "bytes": 0, "unrecorded": 0,
                  "retagged": [], "damaged": [{"file_id": 1, "path": rot}],
                  "missing": [{"file_id": 2, "path": gone}, {"file_id": 3, "path": lost}], "unreadable": [],
                  "ok_whole": False}
        vl.keep(lib, verify)
        plan = {"hub": {"library": lib},
                "nodes": {"bad": {"host": "pi@bad", "member": "x"}, "unchecked": {"host": "pi@unchecked", "member": "x"},
                          "stale": {"host": "pi@stale", "member": "x"}, "away": {"host": "pi@away", "member": "x"},
                          "good": {"host": "pi@good", "member": "x"}, "mirror": {"host": "sw@mirror"}}}
        fleet = Fleet({
            "pi@bad": {md5["rot"]: ("/srv/a/rot.mp3", "damaged", b"x")},
            "pi@unchecked": {},
            "pi@stale": {md5["rot"]: ("/srv/a/rot.mp3", "ok", b"changed-since")},
            "pi@good": {md5["rot"]: ("/srv/a/rot.mp3", "ok", good_rot),
                        md5["gone"]: ("/srv/a/gone.mp3", "ok", good_gone)},
            "sw@mirror": {md5["lost"]: ("/m/lost.mp3", "ok", good_lost)},
        })

        rc, said = run(plan, fleet, go=False)
        text = "\n".join(said)
        check(rc == 1, f"a dry run with one file no speaker holds whole exits 1: {rc}")
        check(not fleet.fetched and open(rot, "rb").read() == b"rot-BAD-bytes" and not os.path.exists(gone),
              "a dry run fetches and changes nothing")
        check("would fetch it from good (/srv/a/rot.mp3)" in text, text)
        check("bad: its own check found it damaged" in text, "a speaker whose own check found it damaged is passed over")
        check("unchecked: has not checked it" in text, "and one that has not checked it")
        check("stale: its copy no longer matches the record" in text, "and one whose copy no longer matches, hashed now")
        check("away: could not be asked" in text, "a speaker that cannot be reached is said")
        check("NOT REPAIRED: no speaker holds it whole" in text, "a file no speaker holds whole is said")
        check(not any(h == "sw@mirror" for h in fleet.fetched) and "mirror" not in text,
              "a node with no player is not asked")

        rc, said = run(plan, fleet, go=True)
        text = "\n".join(said)
        check(rc == 1, f"one left unrepaired: exit 1 ({rc})")
        check(open(rot, "rb").read() == good_rot and open(gone, "rb").read() == good_gone,
              "the damaged file and the missing one are put back, their bytes the record's")
        check(open(rot + ".damaged-2026-10-25", "rb").read() == b"rot-BAD-bytes",
              "the damaged copy is kept aside, not deleted")
        check(fleet.fetched == ["pi@good", "pi@good"], f"each fetched once, from the good speaker: {fleet.fetched}")
        check(not os.path.exists(lost) and not any(n.endswith(".repair-part") for n in os.listdir(music)),
              "nothing half-fetched is left")

        # What arrives wrong is refused, and the file left as it was.
        with open(rot, "wb") as fh:
            fh.write(b"rot-BAD-again")
        lying = Fleet({"pi@good": {md5["rot"]: ("/srv/a/rot.mp3", "ok", good_rot)}})
        real_run = lying.run

        def corrupting(host, cmd, stdout=None, timeout=60):
            if cmd.startswith("cat"):
                stdout.write(b"garbled in transit")
                return 0, ""
            return real_run(host, cmd, stdout, timeout)
        lying.run = corrupting
        plan1 = dict(plan, nodes={"good": {"host": "pi@good", "member": "x"}})
        rc, said = run(plan1, lying, go=True)
        text = "\n".join(said)
        check("what arrived does not match the record" in text and open(rot, "rb").read() == b"rot-BAD-again",
              f"a fetch that arrives wrong is refused, the file left alone: {text}")

        # Nothing to repair.
        vl.keep(lib, dict(verify, checked_at="2026-10-26T03:30:00-04:00", damaged=[], missing=[], ok_whole=True))
        rc, said = run(plan, fleet, go=True)
        check(rc == 0 and "nothing to repair" in "\n".join(said), f"a whole library: exit 0, said: {said}")

    if FAILED:
        print(f"\n{len(FAILED)} check(s) failed")
        return 1
    print("repair_library: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
