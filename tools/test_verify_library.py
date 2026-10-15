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

        print("[GDE-INT-175] a rip's sidecars: recorded, then changed or gone; adopted; strays")
        import sidecars
        r = vl.verify(db, progress=lambda s: None)
        check(r["sidecars"]["ok"] == 0 and r["sidecars_whole"], f"no ingest_decisions table: nothing to check: {r['sidecars']}")
        album = os.path.join(music, "Artist", "Album")
        os.makedirs(album)
        os.replace(files["ok"][0], os.path.join(album, "ok.mp3"))
        side = {"Album.cue": b'FILE "ok.mp3" MP3\n', "Album.log": b"CUERipper v2.2.6\nAccurately ripped\n",
                "Album.accurip": b"[AccurateRip]\n", "WS_FTP.LOG": b"2002.03.02 05:49 B C:\\Documents\n"}
        for n, data in side.items():
            with open(os.path.join(album, n), "wb") as fh:
                fh.write(data)
        c = sqlite3.connect(db)
        c.execute("UPDATE files SET path = ? WHERE audio_md5 = 'md5-ok'", (os.path.join(album, "ok.mp3"),))
        c.execute("CREATE TABLE ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT, stage TEXT, "
                  "outcome TEXT, confidence REAL, detail TEXT, decided_at TEXT)")
        check(sidecars.of_rip(album) == ["Album.accurip", "Album.cue", "Album.log"],
              f"a log is a rip log only if it says so: {sidecars.of_rip(album)}")
        n = sidecars.record(c, "md5-ok", album, ["Album.cue", "Album.log"], now="2026-10-15T20:00:00")
        c.commit()
        check(n == 2 and [x[0] for x in sidecars.unrecorded(c)] == [album]
              and sidecars.unrecorded(c)[0][2] == ["Album.accurip"],
              f"two recorded; the AccurateRip report not yet: {sidecars.unrecorded(c)}")
        c.close()
        r = vl.verify(db, progress=lambda s: None)
        check(r["sidecars"]["ok"] == 2 and r["sidecars"]["unrecorded"] == 1 and r["sidecars_whole"],
              f"both match; one never recorded: {r['sidecars']}")

        # The album moves (a relink): the record follows its audio, not a path.
        moved = os.path.join(music, "Artist", "Album (2001)")
        os.replace(album, moved)
        c = sqlite3.connect(db)
        c.execute("UPDATE files SET path = ? WHERE audio_md5 = 'md5-ok'", (os.path.join(moved, "ok.mp3"),))
        c.commit()
        c.close()
        with open(os.path.join(moved, "Album.log"), "ab") as fh:
            fh.write(b"edited\n")
        os.remove(os.path.join(moved, "Album.cue"))
        r = vl.verify(db, progress=lambda s: None)
        s = r["sidecars"]
        check([os.path.basename(x["path"]) for x in s["changed"]] == ["Album.log"]
              and [os.path.basename(x["path"]) for x in s["missing"]] == ["Album.cue"] and not r["sidecars_whole"],
              f"found in the moved folder; the log changed, the sheet gone: {s}")
        check("1 changed, 1 missing" in vl.summary(r), vl.summary(r))

        c = sqlite3.connect(db)
        todo = sidecars.unrecorded(c)
        adopted = sum(sidecars.record(c, md5, f, names, outcome="adopted") for f, md5, names in todo)
        c.commit()
        check(adopted == 1 and sidecars.unrecorded(c) == []
              and {x["outcome"] for x in sidecars.recorded(c)} == {"recorded", "adopted"},
              f"the AccurateRip report adopted, said as adopted: {sidecars.recorded(c)}")
        c.close()

        # An artist's folder of two albums: each sheet keyed to its own album's audio.
        two = os.path.join(music, "Two")
        os.makedirs(two)
        for n in ("EatAPeach.mp3", "Fillmore.mp3", "EatAPeach.cue", "Fillmore.cue", "Other.cue"):
            with open(os.path.join(two, n), "wb") as fh:
                fh.write(n.encode())
        c = sqlite3.connect(db)
        c.execute("INSERT INTO files VALUES (10, 'md5-fill', ?, NULL)", (os.path.join(two, "Fillmore.mp3"),))
        c.execute("INSERT INTO files VALUES (11, 'md5-peach', ?, NULL)", (os.path.join(two, "EatAPeach.mp3"),))
        got = {md5: names for f, md5, names in sidecars.unrecorded(c) if f == two}
        c.close()                               # uncommitted: the two rows go with it
        check(got == {"md5-peach": ["EatAPeach.cue"], "md5-fill": ["Fillmore.cue", "Other.cue"]},
              f"each sheet to the audio of its name, else the folder's first: {got}")
        for n in os.listdir(two):
            os.remove(os.path.join(two, n))
        os.rmdir(two)

        # Strays: audio no row claims; a FLAC beside its MP3 is its copy; the inbox waits.
        open(os.path.join(music, "Basso.aiff"), "wb").close()
        open(os.path.join(moved, "ok.flac"), "wb").close()
        os.makedirs(os.path.join(music, "_Rips", "Next Album"))
        open(os.path.join(music, "_Rips", "Next Album", "01.wav"), "wb").close()
        st = vl.verify(db, progress=lambda s: None)["strays"]
        # rot.mp3 too: its row was deleted above and the file left behind.
        check(st["root"] == music and st["audio"] == [os.path.join(music, n) for n in ("Basso.aiff", "rot.mp3")]
              and st["lossless_copies"] == 1 and st["inbox_audio"] == 1,
              f"two strays, one lossless copy, one waiting in the inbox: {st}")
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("verify_library: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
