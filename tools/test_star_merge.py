#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `star_merge.py` [SPEC046], one per rule and duty.

    python tools/test_star_merge.py
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_merge as sm  # noqa: E402

SCHEMA = """
CREATE TABLE listener_preferences (subject_kind TEXT, subject_id TEXT, rotation REAL,
    recovery REAL, restraint REAL, updated_at TEXT, PRIMARY KEY (subject_kind, subject_id));
CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT, flagged_at TEXT, origin TEXT,
    PRIMARY KEY (subject_kind, subject_id));
CREATE TABLE listener_occasions (characteristic TEXT, class TEXT, interp TEXT, label TEXT);
CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, played_at INTEGER,
    passage_id INTEGER, mbid TEXT, heard_ms INTEGER, span_ms INTEGER, selected_by TEXT);
CREATE TABLE player_state (id INTEGER PRIMARY KEY, passage_id INTEGER, updated_at TEXT);
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def db(path, *sql):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    for s in sql:
        c.execute(s)
    c.commit()
    c.close()


def fleet(tmp):
    """Two nodes descended from one library, and a backup on one of them."""
    a, b = os.path.join(tmp, "a.db"), os.path.join(tmp, "b.db")
    db(a,
       "INSERT INTO listener_preferences VALUES ('artist','X',1,1,0,'2026-09-01 00:00:00')",
       "INSERT INTO listener_preferences VALUES ('artist','T',1,1,0,'2026-09-02 00:00:00')",
       "INSERT INTO listener_flags VALUES ('recording','keep','2026-08-01 00:00:00','GMKtec')",
       "INSERT INTO listener_occasions VALUES ('user.xmas','xmasy','linear',NULL)",
       "INSERT INTO listener_play_history VALUES (7,1000,1,'m',100,200,'auto')",
       "INSERT INTO player_state VALUES (1,11,'a')")
    db(b,
       "INSERT INTO listener_preferences VALUES ('artist','X',2,2,0,'2026-09-05T00:00:00')",
       "INSERT INTO listener_preferences VALUES ('artist','T',9,9,0,'2026-09-02 00:00:00')",
       "INSERT INTO listener_preferences VALUES ('recording','new',1,1,0,'2026-09-06 00:00:00')",
       "INSERT INTO listener_flags VALUES ('recording','keep','2026-08-01 00:00:00',NULL)",
       "INSERT INTO listener_flags VALUES ('recording','fresh','2026-09-20 00:00:00',NULL)",
       "INSERT INTO listener_occasions VALUES ('user.xmas','xmasy','linear','Christmas')",
       "INSERT INTO listener_play_history VALUES (3,1000,1,'m',150,200,NULL)",
       "INSERT INTO listener_play_history VALUES (4,2000,2,'n',50,90,'auto')",
       "INSERT INTO player_state VALUES (1,22,'b')")
    # b's own history: a flag it held on 09-10 and has since cleared, and one
    # it cleared on 09-10 that a later flag elsewhere must survive.
    bk = os.path.join(tmp, "bk")
    os.makedirs(bk)
    db(os.path.join(bk, "listener-1789000000.db"),   # 2026-09-10
       "INSERT INTO listener_flags VALUES ('recording','cleared','2026-08-15 00:00:00',NULL)",
       "INSERT INTO listener_flags VALUES ('recording','readded','2026-08-15 00:00:00',NULL)")
    c = sqlite3.connect(a)
    c.execute("INSERT INTO listener_flags VALUES ('recording','cleared','2026-08-15 00:00:00','x')")
    c.execute("INSERT INTO listener_flags VALUES ('recording','readded','2026-09-21 00:00:00','x')")
    c.commit()
    c.close()
    return dict(nodes=[dict(name="b", listener=b, backups=bk, taken_at="2026-09-26T00:00:00Z"),
                       dict(name="a", listener=a, taken_at="2026-09-26T00:00:00Z")],
                hub_state_from="a"), (a, b)


def rows(path, sql):
    c = sqlite3.connect(path)
    r = c.execute(sql).fetchall()
    c.close()
    return r


def main() -> int:
    tmp = tempfile.mkdtemp()
    manifest, inputs = fleet(tmp)
    before = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in inputs}
    out = os.path.join(tmp, "out")
    rep = sm.build(manifest, out)
    L = os.path.join(out, "listener.db")

    check(rep.data["integrity"] == "ok", "the output must pass integrity_check")
    p = dict(((k, i), (r, u)) for k, i, r, u in
             rows(L, "SELECT subject_kind, subject_id, rotation, updated_at FROM listener_preferences"))
    check(p[("artist", "X")][0] == 2, "last write wins, across timestamp spellings")
    check(("recording", "new") in p, "a preference only one node holds is kept")
    check(p[("artist", "T")][0] == 1, "an exact tie keeps the first node by name")
    check(any("tie at" in d["what"] for d in rep.data["decisions"]), "and the tie is reported")

    f = dict(rows(L, "SELECT subject_id, origin FROM listener_flags"))
    check(f.get("keep") == "GMKtec", "union: a known origin beats an empty one")
    check("fresh" in f, "a flag only one node holds is kept")
    check("cleared" not in f, "a flag b's backup shows it cleared is removed everywhere")
    check("readded" in f, "a flag re-added after that removal is kept")

    o = rows(L, "SELECT label FROM listener_occasions")
    check(o == [("Christmas",)], "field by field, a value beats NULL")

    h = rows(L, "SELECT play_id, played_at, heard_ms, selected_by FROM listener_play_history")
    check(h == [(1, 1000, 150, "auto"), (2, 2000, 50, "auto")],
          f"events: one play heard twice is one row, the larger heard_ms, renumbered: {h}")
    check(rows(L, "SELECT passage_id FROM player_state") == [(11,)],
          "node state comes from the hub's own node, not merged")

    after = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in inputs}
    check(before == after, "no input may change")
    sidecars = [x for x in os.listdir(tmp) if x.endswith(("-shm", "-wal"))]
    check(not sidecars, f"nothing may be written beside an input: {sidecars}")

    out2 = os.path.join(tmp, "out2")
    manifest["nodes"].reverse()
    sm.build(manifest, out2)
    same = open(L, "rb").read() == open(os.path.join(out2, "listener.db"), "rb").read()
    check(same, "the same inputs in another order must give the same bytes")
    check(open(os.path.join(out, "report.md")).read() == open(os.path.join(out2, "report.md")).read(),
          "and the same report")

    try:
        sm.build(manifest, out)
        check(False, "a non-empty output directory must be refused")
    except SystemExit:
        pass
    c = sqlite3.connect(inputs[0])
    c.execute("CREATE TABLE mystery (x)")
    c.commit()
    c.close()
    try:
        sm.build(manifest, os.path.join(tmp, "out3"))
        check(False, "a table without a rule must be refused")
    except SystemExit as e:
        check("mystery" in str(e), "and named")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("star_merge: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
