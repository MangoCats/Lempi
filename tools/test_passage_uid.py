#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `passage_uid.py` [IMPL-UID-032], and the vectors both languages check.

    python tools/test_passage_uid.py            check
    python tools/test_passage_uid.py --write    rewrite fixtures/passage_uid/vectors.json

`--write` is for a change to the inputs below, never to the encoding: a
vector whose uid changes is a different identity for every passage there is.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import passage_uid as pu  # noqa: E402

VECTORS = os.path.join(os.path.dirname(HERE), "fixtures", "passage_uid", "vectors.json")
FAILED = []

# Each is a case the encoding must keep apart or keep equal.
INPUTS = [
    ("an ordinary single track", "6e1f3c0a9b8d7e6f5a4b3c2d1e0f9a8b", "radio", 0, 200000, 0),
    ("the album cut of the same file", "6e1f3c0a9b8d7e6f5a4b3c2d1e0f9a8b", "album", 0, 200000, 0),
    ("a second passage born on that key", "6e1f3c0a9b8d7e6f5a4b3c2d1e0f9a8b", "radio", 0, 200000, 1),
    ("a cut inside a whole-album file", "f5500d078a0d9a6c63f41a9205a0402f", "radio", 4337680, 4602680, 0),
    ("a span past 2^31 ms", "f5500d078a0d9a6c63f41a9205a0402f", "album", 2147483000, 2147484000, 0),
    ("start and end swapped are another key", "6e1f3c0a9b8d7e6f5a4b3c2d1e0f9a8b", "radio", 200000, 0, 0),
    ("digits that would join if unseparated", "ab", "radio", 12, 3, 0),
    ("the same digits split elsewhere", "ab", "radio", 1, 23, 0),
]


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def vectors():
    return [dict(case=c, audio_md5=m, kind=k, start_ms=s, end_ms=e, salt=t, uid=pu.passage_uid(m, k, s, e, t))
            for c, m, k, s, e, t in INPUTS]


REPO = os.path.dirname(HERE)
# [IMPL-UID-038]: the files that may still write a passage row by hand, and
# why. This list only shrinks: one that no longer does is taken off it, and a
# file not on it that does fails the test.
RAW_INSERTS_ALLOWED = {
    "tools/passage_uid.py": "the helper itself",
    "tools/remote_snapshot.py": "a scratch copy of a remote, not a catalogue; Stage C converts it [IMPL-UID-077]",
    "player/core/src/bundle.rs": "writes the hub's row, then db::stamp_birth stores the uid it was sent",
    "player/src/found.rs": "writes a phone-born row, then db::stamp_birth gives it its uid",
}


def raw_inserts():
    """Every non-test source file writing a passage row by hand."""
    import re
    pat = re.compile(r"INSERT(\s+OR\s+[A-Z]+)?\s+INTO\s+passages\b")
    found = set()
    for top, ext in (("tools", ".py"), ("player", ".rs")):
        for root, dirs, files in os.walk(os.path.join(REPO, top)):
            dirs[:] = [d for d in dirs if d not in ("target", "tests", "__pycache__")]
            for f in files:
                if not f.endswith(ext) or f.startswith("test_"):
                    continue
                p = os.path.join(root, f)
                text = open(p, encoding="utf-8", errors="replace").read()
                if ext == ".rs":
                    text = text.split("#[cfg(test)]")[0]       # a test module's fixtures are not a writer
                if pat.search(text):
                    found.add(os.path.relpath(p, REPO).replace(os.sep, "/"))
    return found


def test_insert_passage():
    """[IMPL-UID-038]: before uids, exactly the row a tool wrote; after, born
    with its uid, a second birth on one key salted, a cut made again revived."""
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.executescript("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);"
                    "CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER,"
                    " end_ms INTEGER, lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);"
                    "INSERT INTO files VALUES (7, 'abc');")
    pid = pu.insert_passage(c, 7, "album", 0, 1000, "ingest", lead_in_ms=0, lead_out_ms=0, gain_db=0.0)
    check(c.execute("SELECT * FROM passages").fetchall() == [(pid, 7, "album", 0, 1000, 0, 0, 0.0, "ingest")],
          "before uids, the row is the one the tool wrote by hand")
    check(not pu.has_uids(c), "and the catalogue is said to have none")
    for col in ("passage_uid TEXT", "birth_md5 TEXT", "birth_kind TEXT", "birth_start_ms INTEGER",
                "birth_end_ms INTEGER", "birth_salt INTEGER"):
        c.execute(f"ALTER TABLE passages ADD COLUMN {col}")
    a = pu.insert_passage(c, 7, "radio", 0, 1000, "ingest")
    row = c.execute("SELECT passage_uid, birth_md5, birth_kind, birth_start_ms, birth_end_ms, birth_salt "
                    "FROM passages WHERE passage_id = ?", (a,)).fetchone()
    check(row == (pu.passage_uid("abc", "radio", 0, 1000), "abc", "radio", 0, 1000, 0), f"born with its uid: {row}")
    c.execute("UPDATE passages SET end_ms = 900 WHERE passage_id = ?", (a,))      # its boundary edited
    b = pu.insert_passage(c, 7, "radio", 0, 1000, "ingest")                        # a new cut where it was born
    ub = c.execute("SELECT passage_uid, birth_salt FROM passages WHERE passage_id = ?", (b,)).fetchone()
    check(ub == (pu.passage_uid("abc", "radio", 0, 1000, 1), 1), f"a second birth on the key is salted [SPEC-UID-054]: {ub}")
    ua = c.execute("SELECT passage_uid FROM passages WHERE passage_id = ?", (a,)).fetchone()[0]
    c.execute("DELETE FROM passages WHERE passage_id IN (?, ?)", (a, b))       # both cuts of that key gone
    r = pu.insert_passage(c, 7, "radio", 0, 1000, "ingest")
    check(c.execute("SELECT passage_uid FROM passages WHERE passage_id = ?", (r,)).fetchone()[0] == ua,
          "a cut made again after its passages went is born with its old uid [SPEC-UID-056]")
    try:
        pu.insert_passage(c, 99, "radio", 0, 1, "ingest")
        check(False, "a passage of no file is refused, since its uid needs the file's audio_md5")
    except ValueError:
        pass


def main(argv) -> int:
    if "--write" in argv:
        os.makedirs(os.path.dirname(VECTORS), exist_ok=True)
        with open(VECTORS, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"encoding": pu.DOMAIN, "vectors": vectors()}, fh, indent=1)
            fh.write("\n")
        print(f"wrote {VECTORS}")
        return 0
    on_disk = json.load(open(VECTORS, encoding="utf-8"))
    check(on_disk["encoding"] == pu.DOMAIN, f"the fixture is for this encoding: {on_disk['encoding']}")
    for v in on_disk["vectors"]:
        got = pu.passage_uid(v["audio_md5"], v["kind"], v["start_ms"], v["end_ms"], v["salt"])
        check(got == v["uid"], f"{v['case']}: {got} != {v['uid']}")
    uids = [v["uid"] for v in on_disk["vectors"]]
    check(len(set(uids)) == len(uids), "every case is a different identity")
    check(all(len(u) == 32 and u == u.lower() and all(c in "0123456789abcdef" for c in u) for u in uids),
          "each is 32 lowercase hex characters")
    check([v["uid"] for v in vectors()] == uids, "and the inputs here are the fixture's")
    for bad in [("", "radio", 0, 1, 0), ("ab\x1fc", "radio", 0, 1, 0), ("ab", "", 0, 1, 0),
                ("ab", "radio", True, 1, 0), ("ab", "radio", 0, 1.0, 0), ("ab", "radio", 0, 1, -1),
                ("ab", "radio", "0", 1, 0)]:
        try:
            pu.passage_uid(*bad)
            check(False, f"refused: {bad!r}")
        except ValueError:
            pass
    test_insert_passage()
    found = raw_inserts()
    check(found - set(RAW_INSERTS_ALLOWED) == set(),
          f"a passage written by hand outside the helper [IMPL-UID-038]: {sorted(found - set(RAW_INSERTS_ALLOWED))}")
    check(set(RAW_INSERTS_ALLOWED) - found == set(),
          f"no longer written by hand, so off the list: {sorted(set(RAW_INSERTS_ALLOWED) - found)}")
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("passage_uid: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
