#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `phone_uids.py` [IMPL-UID-066], [SPEC-PL-110].

    python tools/test_phone_uids.py
"""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import passage_uid as pu  # noqa: E402
import phone_uids  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


FILES = "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE);"
PASSAGES = """CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, kind TEXT NOT NULL,
    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL{uid});
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);"""
UID_COLS = (", passage_uid TEXT, birth_md5 TEXT, birth_kind TEXT, birth_start_ms INTEGER, "
            "birth_end_ms INTEGER, birth_salt INTEGER")


def hub(root):
    """A: one passage, unmoved. B: one passage, its end moved from 900 to 880
    after its birth. D: two passages, so a phone's other cut of it is no one's.
    E: one passage, which a phone holds with an edit it never received."""
    path = os.path.join(root, "hub.db")
    c = sqlite3.connect(path)
    c.executescript(FILES + PASSAGES.format(uid=UID_COLS))
    c.execute("INSERT INTO files VALUES (1, 'A'), (2, 'B'), (4, 'D'), (5, 'E')")
    for pid, fid, md5, kind, s, e, born in ((50, 1, "A", "radio", 0, 1000, (0, 1000)),
                                           (51, 2, "B", "radio", 10, 880, (10, 900)),
                                           (52, 4, "D", "radio", 0, 500, (0, 500)),
                                           (53, 4, "D", "radio", 500, 1000, (500, 1000)),
                                           (54, 5, "E", "radio", 0, 800, (0, 800))):
        birth = (md5, kind, born[0], born[1], 0)
        c.execute("INSERT INTO passages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (pid, fid, kind, s, e, pu.passage_uid(*birth), *birth))
    c.commit()
    c.close()
    return path


def phone(root, unmatched=True):
    """A as the hub has it, B at its birth span, C the hub lacks, D cut another
    way, E at a span the hub has neither held nor been born at."""
    os.makedirs(root)
    lib = os.path.join(root, "library.db")
    c = sqlite3.connect(lib)
    c.executescript(FILES + PASSAGES.format(uid=""))
    c.execute("INSERT INTO files VALUES (1, 'A'), (2, 'B'), (3, 'C'), (4, 'D'), (5, 'E')")
    c.execute("INSERT INTO passages VALUES (7, 1, 'radio', 0, 1000), (8, 2, 'radio', 10, 900), "
              "(9, 3, 'radio', 0, 700), (12, 5, 'radio', 0, 790)")
    if unmatched:
        c.execute("INSERT INTO passages VALUES (10, 4, 'radio', 0, 499)")
    c.commit()
    c.close()
    lis = os.path.join(root, "listener.db")
    c = sqlite3.connect(lis)
    c.executescript("CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, passage_id INTEGER);"
                    "INSERT INTO listener_play_history VALUES (1, 7), (2, 9), (3, 99), (4, NULL);"
                    "CREATE TABLE player_queue (position INTEGER PRIMARY KEY, passage_id INTEGER NOT NULL);"
                    "INSERT INTO player_queue VALUES (0, 8);")
    c.close()
    return lib, lis


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = phone_uids.main(list(argv))
        except SystemExit as e:
            rc = e
    return rc, buf.getvalue()


def uids(lib):
    c = sqlite3.connect(lib)
    try:
        if "passage_uid" not in [r[1] for r in c.execute("PRAGMA table_info(passages)")]:
            return None
        return dict(c.execute("SELECT passage_id, passage_uid FROM passages"))
    finally:
        c.close()


def main() -> int:
    tmp = tempfile.mkdtemp()
    h = hub(tmp)

    lib, lis = phone(os.path.join(tmp, "a"))
    rc, out = run(lib, "--hub", h, "--listener", lis)
    check(rc == 0 and "5 passage(s) on the phone" in out, f"a rehearsal counts them: {out}")
    check("     1  hub, by its span now" in out and "     1  hub, by its birth span" in out
          and "     1  hub, the one passage" in out and "     1  born here" in out
          and "     1  of a hub file, matching no hub passage" in out,
          f"each passage by how it is found: {out}")
    check("passage 10: D radio 0..499" in out, f"the unmatched one is named: {out}")
    check("listener_play_history: 3 row(s) name a passage; 1 would convert to no uid" in out,
          f"a play of a passage the catalogue lacks is counted: {out}")
    check(uids(lib) is None, "a rehearsal writes nothing")
    rc, out = run(lib, "--hub", h, "--commit", "--expect", "5")
    check(isinstance(rc, SystemExit) and "match no hub passage" in str(rc) and uids(lib) is None
          and not os.path.exists(os.path.join(tmp, "a", "library.pre-uids.db")),
          f"a commit is refused while one is unmatched, before any backup: {rc}")

    lib, lis = phone(os.path.join(tmp, "b"), unmatched=False)
    rc, out = run(lib, "--hub", h, "--commit", "--expect", "5")
    check(isinstance(rc, SystemExit) and uids(lib) is None, "a commit whose count differs is refused")
    rc, out = run(lib, "--hub", h, "--commit", "--expect", "4")
    got = uids(lib)
    check(rc == 0 and "quick_check ok" in out, f"a commit gives them: {rc} {out}")
    check(got and got[7] == pu.passage_uid("A", "radio", 0, 1000, 0), "A takes the hub's uid")
    check(got and got[8] == pu.passage_uid("B", "radio", 10, 900, 0),
          "B, matched by its birth span, takes the hub's uid, not one of its own")
    check(got and got[9] == pu.passage_uid("C", "radio", 0, 700, 0), "C is born here, salt 0")
    check(got and got[12] == pu.passage_uid("E", "radio", 0, 800, 0),
          "E, the one passage of its file and kind on both, takes the hub's uid though its span moved")
    c = sqlite3.connect(lib)
    idx = c.execute("SELECT sql FROM sqlite_master WHERE name = 'passages_uid'").fetchone()
    birth = c.execute("SELECT birth_md5, birth_kind, birth_start_ms, birth_end_ms, birth_salt FROM passages "
                      "WHERE passage_id = 8").fetchone()
    trig = c.execute("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0]
    c.close()
    check(idx and "UNIQUE" in idx[0], "the unique index is made")
    check(birth == ("B", "radio", 10, 900, 0), f"the hub's birth key comes with its uid: {birth}")
    check(trig == 0, "no trigger: a node holds none of the hub's")
    check(os.path.exists(os.path.join(tmp, "b", "library.pre-uids.db")), "the copy is backed up first")
    rc, out = run(lib, "--hub", h)
    check(isinstance(rc, SystemExit) and "already carry uids" in str(rc), "a second run is refused")

    # Two passages of a file the hub lacks, on one span: their kinds keep their births apart.
    lib, _ = phone(os.path.join(tmp, "c"), unmatched=False)
    c = sqlite3.connect(lib)
    c.execute("INSERT INTO passages VALUES (11, 3, 'album', 0, 700)")
    c.commit()
    c.close()
    rc, out = run(lib, "--hub", h, "--commit", "--expect", "5")
    got = uids(lib)
    check(rc == 0 and got[11] == pu.passage_uid("C", "album", 0, 700, 0) and len(set(got.values())) == 5,
          f"another kind on the same span is another birth: {out}")

    # Two passages of E here, against the hub's one: neither is that one.
    lib, _ = phone(os.path.join(tmp, "d"), unmatched=False)
    c = sqlite3.connect(lib)
    c.execute("INSERT INTO passages VALUES (13, 5, 'radio', 790, 1000)")
    c.commit()
    c.close()
    rc, out = run(lib, "--hub", h)
    check(rc == 0 and "     0  hub, the one passage" in out and "     2  of a hub file, matching no hub passage" in out,
          f"two of a file and kind on the phone are never paired with the hub's one: {out}")

    if FAILED:
        print(f"{len(FAILED)} FAILED")
        return 1
    print("test_phone_uids: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
