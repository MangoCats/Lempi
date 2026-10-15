#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `verify_library.py` `[GDE-INT-115]`: a file that matches, one
re-tagged, one damaged, one missing and one with no hash recorded, each
named as what it is; the result kept and pruned; the library untouched.

    python tools/test_verify_library.py
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify_library as vl  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def main() -> int:
    print("each file is named as what it is: ok, retagged, damaged, missing, unrecorded")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        music = os.path.join(tmp, "music")
        os.makedirs(music)
        files = {}
        for name, data in (("ok", b"ok-bytes"), ("retag", b"retag-bytes"), ("rot", b"rot-bytes"),
                           ("gone", b"gone-bytes"), ("nohash", b"nohash-bytes")):
            p = os.path.join(music, name + ".mp3")
            with open(p, "wb") as fh:
                fh.write(data)
            files[name] = (p, hashlib.sha256(data).hexdigest())
        db = os.path.join(tmp, "library.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, sha256 TEXT)")
        for i, (name, (p, sha)) in enumerate(files.items(), 1):
            c.execute("INSERT INTO files VALUES (?,?,?,?)", (i, f"md5-{name}", p, None if name == "nohash" else sha))
        c.commit()
        c.close()
        before = hashlib.sha256(open(db, "rb").read()).hexdigest()

        # After the record: a tag rewritten (bytes change, audio the same),
        # bytes rotted (both change), a file gone.
        with open(files["retag"][0], "ab") as fh:
            fh.write(b"+tag")
        with open(files["rot"][0], "r+b") as fh:
            fh.write(b"X")
        os.remove(files["gone"][0])
        vl.audio_md5 = lambda p: "md5-retag" if p.endswith("retag.mp3") else "something-else"

        r = vl.verify(db, progress=lambda s: None)
        check(r["ok"] == 1 and r["files"] == 5, f"one matches of five: {r['ok']}/{r['files']}")
        check([x["path"] for x in r["retagged"]] == [files["retag"][0]], f"the re-tag: {r['retagged']}")
        check([x["path"] for x in r["damaged"]] == [files["rot"][0]], f"the damage: {r['damaged']}")
        check([x["path"] for x in r["missing"]] == [files["gone"][0]], f"the missing: {r['missing']}")
        check(r["unrecorded"] == 1 and not r["unreadable"], "one with no hash, none unreadable")
        check(r["ok_whole"] is False, "not whole: something is damaged or missing")
        check("1 damaged" in vl.summary(r) and "1 retagged" in vl.summary(r), vl.summary(r))

        print("the result is kept by date, pruned, and read back; the library is untouched")
        path = vl.keep(db, r)
        check(os.path.isfile(path) and vl.newest(db)["damaged"] == r["damaged"], f"kept and read back: {path}")
        d = vl.verify_dir(db)
        for n in range(20):
            with open(os.path.join(d, f"2026-01-{n + 1:02d}.json"), "w") as fh:
                json.dump({}, fh)
        vl.keep(db, r)
        check(len([f for f in os.listdir(d) if f.endswith(".json")]) == vl.KEEP, "pruned to the newest 14")
        check(hashlib.sha256(open(db, "rb").read()).hexdigest() == before, "the library was not written")

        print("a whole library is whole")
        c = sqlite3.connect(db)
        c.execute("DELETE FROM files WHERE path LIKE '%gone%' OR path LIKE '%rot%'")
        c.commit()
        c.close()
        check(vl.verify(db, progress=lambda s: None)["ok_whole"] is True, "nothing damaged or missing: whole")
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("verify_library: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
