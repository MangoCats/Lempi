#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tools/pending.py and the intake's repair routes [SPEC048].

A damaged copy is told apart from a different file; each of the three
decisions does what it says and nothing else; and a repair travels through a
real intake on loopback: offered, served only as decided, closed only by the
good copy's hash.
"""
import hashlib
import json
import os
import random
import sqlite3
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intake  # noqa: E402
import pending  # noqa: E402
from test_intake import call  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write(path: str, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def arrive(pdir: str, data: bytes, name: str, tags: dict, signature: str) -> str:
    """A file as the intake keeps it: `<sha>.<ext>` beside its note."""
    s = sha(data)
    write(os.path.join(pdir, s + os.path.splitext(name)[1]), data)
    with open(os.path.join(pdir, s + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"offered": {"name": name, "tags": tags}, "verdict": "near", "signature": signature,
                   "file": s + os.path.splitext(name)[1], "bytes": len(data)}, fh)
    return s


def main() -> int:
    tmp = tempfile.mkdtemp()
    music = os.path.join(tmp, "Music")
    rng = random.Random(7)
    good = bytes(rng.getrandbits(8) for _ in range(256 * 1024))
    good_path = os.path.join(music, "Aretha Franklin", "Singles", "113 - Think.mp3")
    write(good_path, good)
    db = os.path.join(tmp, "library.db")
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, size_bytes INTEGER,
                            duration_ms INTEGER, sha256 TEXT);
        CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, artist TEXT);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER);
        CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
    """)
    c.execute("INSERT INTO files VALUES (1, 'sig-good', ?, ?, 1000, ?)", (good_path, len(good), sha(good)))
    c.commit()
    c.close()
    pdir = pending.pending_dir(db)
    conn = pending.open_ro(db)

    # One 4 KiB block of other data, on a block boundary: the Moto G's damage.
    damaged = good[:8192] + bytes(rng.getrandbits(8) for _ in range(4096)) + good[12288:]
    s_dmg = arrive(pdir, damaged, "113 - Think.mp3", {"artist": "Aretha Franklin", "title": "Think"}, "sig-dmg")
    # The same name and size, and every block different: another file.
    other = bytes(rng.getrandbits(8) for _ in range(len(good)))
    s_other = arrive(pdir, other, "113 - Think.mp3", {"artist": "Aretha Franklin", "title": "Think",
                                                      "album": "Live: At Last?"}, "sig-other")
    s_new = arrive(pdir, b"a song of the phone's own" * 50, "Out.mp3", {"artist": "Fluke", "title": "Out"}, "sig-new")

    es = {e["sha"]: e for e in pending.entries(pdir)}
    d = pending.damaged_copy(conn, es[s_dmg])
    check(d is not None and d["path"] == good_path and d["blocks_differ"] == 1
          and (d["first_byte"], d["last_byte"]) == (8192, 12287),
          f"a copy with one block of other data is a damaged copy of the library's file: {d}")
    check(pending.damaged_copy(conn, es[s_other]) is None, "the same name and size, all different, is not")
    check(pending.damaged_copy(conn, es[s_new]) is None, "a file of another name is not")

    # Repair, through a real intake.
    g = pending.repair(conn, pdir, es[s_dmg])
    check(g["sha256"] == sha(good), "repair names the library's good copy")
    it = intake.Intake(db, pdir, intake.APP_KEY, signature=lambda p: "sig")
    httpd = intake.serve(it, "127.0.0.1", 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    status, body = call(port, "GET", "/repairs")
    check(status == 200 and body["repairs"] == [{"damaged_sha256": s_dmg, "good_sha256": sha(good),
                                                 "size": len(good), "name": "113 - Think.mp3"}],
          f"the intake offers the repair: {body}")
    status, body = call(port, "GET", "/repairs", key="wrong")
    check(status == 403, "not without the key")
    status, got = call(port, "GET", "/good/" + sha(good))
    check(status == 200 and got == good, "the good copy is served, byte for byte")
    status, _ = call(port, "GET", "/good/" + sha(other))
    check(status == 404, "a file no repair names is not: this is not a way to fetch the library")
    status, _ = call(port, "POST", "/repaired/" + s_dmg, {"sha256": sha(damaged)})
    check(status == 409 and os.path.exists(os.path.join(pdir, s_dmg + ".json")),
          "a report of the wrong bytes does not close the repair")
    status, _ = call(port, "POST", "/repaired/" + s_dmg, {"sha256": sha(good)})
    check(status == 200 and pending.decided(pdir, s_dmg) == "repaired"
          and not os.path.exists(os.path.join(pdir, s_dmg + ".json")),
          "the good copy's hash closes it, and it moves on")
    check(call(port, "GET", "/repairs")[1]["repairs"] == [], "and is no longer offered")

    # Reject: set aside, and declined if offered again.
    pending.reject(pdir, pending.find(pdir, s_other[:10]))
    check(pending.decided(pdir, s_other) == "rejected", "a rejected file moves on")
    status, ans = call(port, "POST", "/offer", {"files": [
        {"id": 1, "sha256": s_other, "name": "113 - Think.mp3", "tags": {"artist": "X", "title": "Y"}}]})
    check(ans["files"][0]["verdict"] == "rejected" and not ans["files"][0]["want"],
          f"and offered again, it is declined: {ans}")

    # Induct: to Music/<artist>/<album>/ by its tags, checked by its bytes.
    placed = pending.induct(pdir, pending.find(pdir, s_new), music)
    check(placed["folder"] == os.path.join(music, "Fluke") and os.path.isfile(placed["path"])
          and pending.sha256_file(placed["path"]) == s_new,
          f"with no album tag, it goes to the artist's folder, byte for byte: {placed}")
    check(pending.decided(pdir, s_new) == "inducted" and pending.entries(pdir) == [],
          "and it moves on, leaving nothing waiting")
    check(pending.safe('AC/DC: "Live"?') == "AC_DC_ _Live__", "a tag becomes a folder name any host takes")

    # A file placed where another already is: nothing moves.
    s2 = arrive(pdir, b"another" * 99, "Out.mp3", {"artist": "Fluke", "title": "Out"}, "sig-2")
    try:
        pending.induct(pdir, pending.find(pdir, s2), music)
        check(False, "a different file at the destination stops the induction")
    except SystemExit:
        check(pending.find(pdir, s2) and pending.sha256_file(placed["path"]) == s_new,
              "a different file at the destination stops the induction, and nothing moves")
    httpd.shutdown()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("pending: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
