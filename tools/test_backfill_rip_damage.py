#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `backfill_rip_damage.py`: a damaged track a rip's log names is
recorded once, a clean one never, and nothing is written without --commit.

    python tools/test_backfill_rip_damage.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(os.path.dirname(HERE), "sql", "schema.sql")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def run(*args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "backfill_rip_damage.py"), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=60)
    return r.returncode, r.stdout + r.stderr


def failures(db):
    c = sqlite3.connect(db)
    got = [json.loads(d)["track"] for (d,) in c.execute(
        "SELECT detail FROM ingest_decisions WHERE outcome='verification_failed' ORDER BY decision_id")]
    c.close()
    return got


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        album = os.path.join(tmp, "Artist", "Album (2003)")
        os.makedirs(album)
        with open(os.path.join(album, "Artist - Album.log"), "w", encoding="utf-8", newline="") as f:
            f.write("\r\n".join(["AccurateRip summary", "",
                                 "Track  1  accurately ripped (confidence 140)  [6872D822]",
                                 "Track  2  cannot be verified as accurate (confidence 458)  [22BA73ED]", ""]))
        db = os.path.join(tmp, "lib.db")
        c = sqlite3.connect(db)
        c.executescript(open(SCHEMA, encoding="utf-8").read())
        c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, "
                  "first_seen, last_seen) VALUES (1, 'md5', ?1, 1, 0, 'mp3', 1, 'x', 'x')",
                  (os.path.join(album, "Artist - Album.mp3"),))
        c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, decided_at) "
                  "VALUES ('md5', 'rip', 'exact', 'x')")
        c.commit()
        c.close()

        print("a dry run says what it would record, and writes nothing")
        code, out = run(db)
        check(code == 0 and "track  2: cannot be verified as accurate" in out and "track  1" not in out, out)
        check(failures(db) == [], f"nothing written: {failures(db)}")

        print("--commit records the damaged track once, and a second run adds nothing")
        run(db, "--commit")
        check(failures(db) == [2], f"track 2 only: {failures(db)}")
        code, out = run(db, "--commit")
        check(failures(db) == [2] and "0 damaged track(s) recorded" in out, f"{failures(db)} {out}")

        print("a tracks-mode rip: the files share one log, and each takes only its own track")
        album = os.path.join(tmp, "Tracks", "Album (2001)")
        os.makedirs(album)
        with open(os.path.join(album, "Tracks - Album.log"), "w", encoding="utf-8") as f:
            f.write("Track  1  accurately ripped (confidence 9)\nTrack  2  cannot be verified as accurate\n")
        db2 = os.path.join(tmp, "tracks.db")
        c = sqlite3.connect(db2)
        c.executescript(open(SCHEMA, encoding="utf-8").read())
        for n in (1, 2):
            c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, "
                      "first_seen, last_seen) VALUES (?1, ?2, ?3, 1, 0, 'mp3', 1, 'x', 'x')",
                      (n, f"md5-{n}", os.path.join(album, f"0{n}. Song.mp3")))
            c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, detail, decided_at) "
                      "VALUES (?1, 'rip', 'chosen', ?2, 'x')", (f"md5-{n}", json.dumps({"track": n, "tracks_mode": True})))
        c.commit()
        c.close()
        run(db2, "--commit")
        c = sqlite3.connect(db2)
        got = [(md5, json.loads(d)) for md5, d in c.execute(
            "SELECT audio_md5, detail FROM ingest_decisions WHERE outcome='verification_failed'")]
        c.close()
        check(got == [("md5-2", {"track": 2, "detail": "cannot be verified as accurate", "in_file": 1})],
              f"track 2's file only, at its own first passage: {got}")

        # [SPEC-RIP-121]: until 2026-11-05 every file read the folder's first log.
        print("a set's discs in one folder: each file its own disc's log, and a disc with none is said")
        album = os.path.join(tmp, "Set", "Box (2004)")
        os.makedirs(album)
        for disc, bad in ((1, 3), (3, None)):
            with open(os.path.join(album, f"Set - Box (Disc {disc}).log"), "w", encoding="utf-8") as f:
                f.write("".join(f"Track  {n}  {'cannot be verified as accurate' if n == bad else 'accurately ripped'}\n"
                                for n in (1, 2, 3)))
        db3 = os.path.join(tmp, "set.db")
        c = sqlite3.connect(db3)
        c.executescript(open(SCHEMA, encoding="utf-8").read())
        rows = [(1, "one", "Set - Box (Disc 1).mp3", {}), (2, "two", "2-03. Other.mp3", {"track": 3, "tracks_mode": True}),
                (3, "three", "Set - Box (Disc 3).mp3", {})]
        for fid, md5, name, detail in rows:
            c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, "
                      "first_seen, last_seen) VALUES (?1, ?2, ?3, 1, 0, 'mp3', 1, 'x', 'x')",
                      (fid, md5, os.path.join(album, name)))
            c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, detail, decided_at) "
                      "VALUES (?1, 'rip', 'chosen', ?2, 'x')", (md5, json.dumps(detail)))
        c.commit()
        c.close()
        code, out = run(db3, "--commit")
        c = sqlite3.connect(db3)
        got = [(md5, json.loads(d)["track"]) for md5, d in c.execute(
            "SELECT audio_md5, detail FROM ingest_decisions WHERE outcome='verification_failed'")]
        c.close()
        check(got == [("one", 3)], f"disc 1's damage is disc 1's, not disc 2's or 3's: {got}")
        check("none of the 2 log(s) beside it is its own" in out, f"disc 2 has no log, and says so: {out}")

        # [SPEC-RIP-120]: recorded and held under the old rule.
        print("read errors AccurateRip matched: the row and its hold are taken back, and nothing else")
        album = os.path.join(tmp, "Old", "Rule (2002)")
        os.makedirs(album)
        toc = ["     Track |   Start  |  Length  | Start sector | End sector "] + \
              [f"        {n}  |  0:00.00 |  1:00.00 |  {(n - 1) * 4500}    |  {n * 4500 - 1}   " for n in (1, 2, 3, 4)]
        with open(os.path.join(album, "Old - Rule.log"), "w", encoding="utf-8") as f:
            f.write("\n".join([*toc, "", "Range status and errors", "", "     Suspicious position 0:00:10",
                               "     Suspicious position 0:01:10", "     Suspicious position 0:02:10",
                               "     Suspicious position 0:03:10",
                               "", "     Copy OK", "", "AccurateRip summary", "",
                               "Track  1  accurately ripped (confidence 9)",
                               "Track  2  accurately ripped (confidence 9)",
                               "Track  3  accurately ripped (confidence 9)",
                               "Track  4  cannot be verified as accurate (confidence 9)", ""]))
        db4 = os.path.join(tmp, "old.db")
        c = sqlite3.connect(db4)
        c.executescript(open(SCHEMA, encoding="utf-8").read())
        c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, "
                  "first_seen, last_seen) VALUES (1, 'md5', ?1, 1, 0, 'mp3', 1, 'x', 'x')",
                  (os.path.join(album, "Old - Rule.mp3"),))
        c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, decided_at) VALUES ('md5', 'rip', 'exact', 'x')")
        said = {1: "read errors at 0:00:10; accurately ripped", 2: "read errors at 0:01:10; accurately ripped",
                3: "read errors at 0:02:10; accurately ripped",
                4: "read errors at 0:03:10; cannot be verified as accurate"}
        hold = {1: f"damaged rip: {said[1]}", 2: "my own words", 3: "", 4: f"damaged rip: {said[4]}"}
        for n in (1, 2, 3, 4):
            c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, detail, decided_at) "
                      "VALUES ('md5', 'rip', 'verification_failed', ?1, 'x')",
                      (json.dumps({"track": n, "detail": said[n]}),))
            c.execute("INSERT INTO passages (file_id, kind, start_ms, end_ms, boundary_src, director_hold) "
                      "VALUES (1, 'radio', ?1, ?2, 'imported:x', ?3)", (n * 1000, n * 1000 + 500, hold[n]))
        c.commit()
        c.close()
        def now():
            c = sqlite3.connect(db4)
            rows = (sorted(json.loads(d)["track"] for (d,) in c.execute(
                        "SELECT detail FROM ingest_decisions WHERE outcome='verification_failed'")),
                    [h for (h,) in c.execute("SELECT director_hold FROM passages ORDER BY start_ms")])
            c.close()
            return rows
        before = now()
        code, out = run(db4)
        check(code == 0 and "3 to take back" in out and now() == before, f"a dry run says so and writes nothing: {out}")
        code, out = run(db4, "--commit")
        check(code == 0 and "3 taken back, 1 passage(s) released" in out, out)
        check(now() == ([4], ["", "my own words", "", hold[4]]),
              f"its hold released, a person's words and a person's release kept, the unmatched track untouched: {now()}")
        code, out = run(db4, "--commit")
        check("0 taken back" in out and now() == ([4], ["", "my own words", "", hold[4]]), f"a second run changes nothing: {out}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("backfill_rip_damage: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
