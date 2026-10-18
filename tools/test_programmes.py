#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tools/programmes.py [SPEC062]: the hub's fingerprint is the
player's, a version is content the hub has held, and an assigned member is
handed its programme over the members' channel and says what it runs."""
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intake  # noqa: E402
import mesh  # noqa: E402
import programmes as pg  # noqa: E402
from test_mesh import Candidate  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def slot(name, start, seeds):
    return {"name": name, "start": start, "seeds": list(seeds)}


def enrol(port, mdir, phone):
    """The enrolment test_mesh walks through, start to accept."""
    st, out, seen = phone.call(port, "POST", "/enrol/start",
                               {"certificate": phone.pem, "name": "Pixel", "role": "phone"})
    sid, commit = out["session"], out["commit"]
    nonce_c = secrets.token_hex(16)
    st, out, _ = phone.call(port, "POST", "/enrol/nonce", {
        "session": sid, "nonce": nonce_c, "signature": phone.sign(f"lempi-enrol|{sid}|{commit}|{nonce_c}")})
    code = mesh.code(seen, phone.fp, out["nonce"], nonce_c)
    phone.call(port, "POST", "/enrol/confirm",
               {"session": sid, "signature": phone.sign(f"lempi-confirm|{sid}|{code}")})
    mesh.accept(mdir, sid)


def main() -> int:
    # The player's own example, from `programme.rs`'s test of its canonical
    # form [SPEC-PGM-110]: the same text here, so the same fingerprint.
    two = [slot("Mellow", "22:00", "ba"), slot("Soft", "04:00", "c")]
    check(pg.canonical(two) == '[{"name":"Soft","seeds":["c"],"start":"04:00"},'
                               '{"name":"Mellow","seeds":["a","b"],"start":"22:00"}]',
          "the canonical form is the player's, slot order and seed order aside")
    check(pg.canonical([slot("Ä", None, [])]) == '[{"name":"Ä","seeds":[],"start":null}]',
          "unescaped UTF-8, and a slot chosen by hand has a null start")
    check(pg.fingerprint([slot("Soft", "4:00", "c"), slot("Mellow", "22:00", "ab")]) == pg.fingerprint(two),
          "a start written 4:00 and seeds in another order are the same content")

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "library.db")
    sqlite3.connect(db).close()
    c = pg.connect(db)
    check(os.path.basename(pg.sidecar_for(db)) == "library.console.db", "kept in the console's sidecar")

    a = pg.store(c, "WKMP", two, "test")
    check(a == {"programme": "WKMP", "version": 1, "fingerprint": pg.fingerprint(two), "new": True},
          f"the first content is version 1: {a}")
    again = pg.store(c, " wkmp ", list(reversed(two)), "elsewhere")
    check(again["version"] == 1 and not again["new"] and again["programme"] == "WKMP",
          "the same content under the same name, however written, is the version already held")
    three = two + [slot("Night", "02:00", "n")]
    check(pg.store(c, "WKMP", three, "test")["version"] == 2, "different content is the next version")

    # A listener file's programme, as the hub's own player holds it.
    lis = os.path.join(tmp, "listener.db")
    l = sqlite3.connect(lis)
    l.executescript("CREATE TABLE listener_programs (program_id INTEGER PRIMARY KEY, name TEXT, start_time TEXT);"
                    "CREATE TABLE listener_program_seeds (program_id INTEGER, mbid TEXT, position INTEGER);"
                    "INSERT INTO listener_programs VALUES (1,'Soft','04:00'),(2,'Mellow','22:00');"
                    "INSERT INTO listener_program_seeds VALUES (1,'c',1),(2,'b',1),(2,'a',2);")
    l.commit()
    l.close()
    name, slots = pg.read_listener(lis)
    check(name is None and pg.fingerprint(slots) == pg.fingerprint(two),
          "a listener file's slots read back as the same content, unnamed when it records no name")

    mdir = mesh.mesh_dir(db)
    mesh.init(mdir, "Test mesh", "desktop")
    hub_pem = mesh.pem_of(mesh.hub_certificate(mdir))
    srv = intake.serve_members(mdir, "127.0.0.1", 0, db=db)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    phone = Candidate(tmp, "phone")
    enrol(port, mdir, phone)

    st, out, _ = phone.call(port, "GET", "/member/programme", present=True, pin=hub_pem)
    check(st == 200 and out == {"programme": None}, f"nothing assigned, nothing handed over: {out}")
    check(pg.find_member(mdir, "pixel")["fingerprint"] == phone.fp, "a member is found by its name")
    pg.assign(c, phone.fp, "wkmp")
    st, out, _ = phone.call(port, "GET", "/member/programme", present=True, pin=hub_pem)
    p = out["programme"]
    check(st == 200 and p["name"] == "WKMP" and p["version"] == 2 and p["fingerprint"] == pg.fingerprint(three)
          and pg.fingerprint(p["slots"]) == p["fingerprint"],
          "the member is handed the current version, whole, its fingerprint its content's")
    s = pg.standing(c, mdir)
    check(s["nodes"][0]["state"] == "not heard from yet" and s["nodes"][0]["sent_version"] == 2
          and s["nodes"][0]["node"] == "Pixel", f"the hub records what it handed over: {s['nodes']}")

    waiting = {"name": "WKMP", "hub_version": 2, "fingerprint": p["fingerprint"], "kept": False}
    st, out, _ = phone.call(port, "POST", "/member/programme/state",
                            {"name": None, "fingerprint": "f" * 64, "source": "node", "hub_version": None,
                             "waiting": waiting}, present=True, pin=hub_pem)
    check(st == 200 and out["assigned"], "the member says what it runs")
    check(pg.standing(c, mdir)["nodes"][0]["state"] == "asked about version 2; no answer yet",
          "and the hub reads it as a question waiting")
    phone.call(port, "POST", "/member/programme/state",
               {"name": "WKMP", "fingerprint": p["fingerprint"], "source": "hub", "hub_version": 2,
                "waiting": None}, present=True, pin=hub_pem)
    check(pg.standing(c, mdir)["nodes"][0]["state"] == "runs version 2, the current one",
          "and, replaced, as running the current version")
    pg.store(c, "WKMP", three + [slot("Dawn", "06:00", "d")], "test")
    check(pg.standing(c, mdir)["nodes"][0]["state"] == "runs version 2, an older one",
          "a newer version leaves it on an older one until it asks again")

    stranger = Candidate(tmp, "stranger")
    try:
        stranger.call(port, "GET", "/member/programme", present=True, pin=hub_pem)
        check(False, "a stranger is refused")
    except Exception:
        check(True, "a stranger's key is refused at the handshake")
    srv.shutdown()
    c.close()
    print(f"\n{len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
