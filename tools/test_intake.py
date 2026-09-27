#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tools/intake.py [REQ-AND-280], [REQ-AND-287..289].

A real server on loopback, a real library file, real HTTP: the offer's
verdicts, the key, and what a file must be to be kept.
"""
import hashlib
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intake  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def library(path: str):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, duration_ms INTEGER, sha256 TEXT);
        CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, artist TEXT);
    """)
    c.execute("INSERT INTO files VALUES (1, 'sig-held', 'C:/m/held.mp3', 200000, ?)", (sha(b"held bytes"),))
    c.execute("INSERT INTO files VALUES (2, 'sig-retagged', 'C:/m/retagged.mp3', 180000, ?)", (sha(b"desktop copy"),))
    c.execute("INSERT INTO files VALUES (3, 'sig-near', 'C:/m/near.mp3', 240000, ?)", (sha(b"near"),))
    c.execute("INSERT INTO file_tags VALUES (3, 'Teardrop', 'Massive Attack')")
    c.commit()
    c.close()


def call(port, method, path, body=None, key=intake.APP_KEY, headers=None):
    h = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = {"X-Lempi-Key": key, **(headers or {})} if key else dict(headers or {})
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    h.request(method, path, body=data, headers=hdrs)
    r = h.getresponse()
    text = r.read()
    h.close()
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


def main() -> int:
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "library.db")
    library(db)
    pending = os.path.join(tmp, "pending")
    it = intake.Intake(db, pending, intake.APP_KEY, signature=lambda p: "sig-of-" + os.path.basename(p))
    httpd = intake.serve(it, "127.0.0.1", 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    status, _ = call(port, "POST", "/offer", {"files": []}, key="wrong")
    check(status == 403, f"a wrong key is refused: {status}")
    status, _ = call(port, "POST", "/offer", {"files": []}, key=None)
    check(status == 403, f"no key is refused: {status}")

    new_bytes = b"the phone's own song"
    offer = {"files": [
        {"id": 1, "sha256": sha(b"held bytes"), "tags": {}},
        {"id": 2, "sha256": sha(b"phone copy, re-tagged"), "audio_md5": "sig-retagged", "tags": {}},
        {"id": 3, "sha256": sha(b"phone near"), "duration_ms": 241500,
         "tags": {"artist": "  massive  ATTACK ", "title": "teardrop"}},
        {"id": 4, "sha256": sha(new_bytes), "name": "Out.mp3", "duration_ms": 1000,
         "tags": {"artist": "Fluke", "title": "Out"}},
        {"id": 5, "sha256": "not-a-hash"},
    ]}
    status, ans = call(port, "POST", "/offer", offer)
    v = {a["id"]: a for a in ans["files"]}
    check(status == 200, f"an offer is answered: {status}")
    check(v[1]["verdict"] == "held" and v[1]["by"] == "bytes" and not v[1]["want"], f"held by its bytes: {v[1]}")
    check(v[2]["verdict"] == "held" and v[2]["by"] == "audio" and not v[2]["want"],
          f"a re-tagged copy is held, by its signature: {v[2]}")
    check(v[3]["verdict"] == "near" and v[3]["want"] and v[3]["near"][0]["path"] == "C:/m/near.mp3",
          f"same artist and title, 1.5 s apart, is a near match: {v[3]}")
    check(v[4]["verdict"] == "new" and v[4]["want"], f"nothing like it is new: {v[4]}")
    check(v[5]["verdict"] == "refused", f"an entry with no valid hash is refused: {v[5]}")

    status, _ = call(port, "PUT", "/file/" + sha(b"held bytes"), b"held bytes")
    check(status == 409, f"a file not wanted is not taken: {status}")
    status, _ = call(port, "PUT", "/file/" + sha(new_bytes), b"damaged in transit!")
    check(status == 422, f"bytes that are not what was offered are refused: {status}")
    check(not os.listdir(pending), f"and nothing is kept: {os.listdir(pending)}")

    status, got = call(port, "PUT", "/file/" + sha(new_bytes), new_bytes)
    check(status == 201 and got["verdict"] == "new", f"a wanted file is kept: {status} {got}")
    kept = os.path.join(pending, sha(new_bytes) + ".mp3")
    check(open(kept, "rb").read() == new_bytes, "the file itself, byte for byte")
    note = json.load(open(os.path.join(pending, sha(new_bytes) + ".json"), encoding="utf-8"))
    check(note["offered"]["tags"]["title"] == "Out" and note["verdict"] == "new"
          and note["signature"] == "sig-of-" + sha(new_bytes) + ".mp3",
          f"beside a note of what was said and made of it: {note}")

    status, ans = call(port, "POST", "/offer", {"files": [offer["files"][3]]})
    check(ans["files"][0]["verdict"] == "pending" and not ans["files"][0]["want"],
          f"offered again, it is known to be waiting: {ans}")
    status, _ = call(port, "PUT", "/file/" + sha(new_bytes), new_bytes)
    check(status == 409, f"and not taken twice: {status}")

    # [REQ-AND-285]: the same offer, carried by hand.
    folder = os.path.join(tmp, "lempi-offer-x")
    os.makedirs(folder)
    good, bad = b"a second new song", b"a third, damaged later"
    files = [
        {"id": 0, "sha256": sha(b"held bytes"), "name": "held.mp3", "tags": {}},
        {"id": 1, "sha256": sha(good), "name": "Puppy.mp3", "tags": {"artist": "Fluke", "title": "Puppy"}},
        {"id": 2, "sha256": sha(bad), "name": "Bad.mp3", "tags": {"artist": "X", "title": "Y"}},
        {"id": 3, "sha256": sha(b"never written"), "name": "Gone.mp3", "tags": {"artist": "Z", "title": "W"}},
    ]
    with open(os.path.join(folder, "offer.json"), "w") as fh:
        json.dump({"files": files}, fh)
    with open(os.path.join(folder, sha(good) + ".mp3"), "wb") as fh:
        fh.write(good)
    with open(os.path.join(folder, sha(bad) + ".mp3"), "wb") as fh:
        fh.write(b"not what was offered")
    out = it.from_folder(folder)
    check(out["kept"] == [("Puppy.mp3", "new")], f"the wanted, sound file is kept: {out['kept']}")
    check(out["not_wanted"] == [("held.mp3", "held")], f"a held one is not: {out['not_wanted']}")
    check(out["damaged"] == ["Bad.mp3"] and out["missing"] == ["Gone.mp3"],
          f"damaged and missing are named: {out}")
    check(os.path.isfile(os.path.join(pending, sha(good) + ".mp3"))
          and os.path.isfile(os.path.join(pending, sha(good) + ".json"))
          and not os.path.exists(os.path.join(pending, sha(bad) + ".mp3")),
          "kept with its note, and the damaged one not at all")

    httpd.shutdown()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("intake: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
