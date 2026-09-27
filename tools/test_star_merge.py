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
CREATE TABLE listener_programs (program_id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE listener_program_seeds (program_id INTEGER, mbid TEXT, PRIMARY KEY (program_id, mbid));
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
       "INSERT INTO player_state VALUES (1,11,'a')",
       "INSERT INTO listener_programs VALUES (1,'Morning')",
       "INSERT INTO listener_program_seeds VALUES (1,'seed-a')")
    db(b,
       "INSERT INTO listener_preferences VALUES ('artist','X',2,2,0,'2026-09-05T00:00:00')",
       "INSERT INTO listener_preferences VALUES ('artist','T',9,9,0,'2026-09-02 00:00:00')",
       "INSERT INTO listener_preferences VALUES ('recording','new',1,1,0,'2026-09-06 00:00:00')",
       "INSERT INTO listener_flags VALUES ('recording','keep','2026-08-01 00:00:00',NULL)",
       "INSERT INTO listener_flags VALUES ('recording','fresh','2026-09-20 00:00:00',NULL)",
       "INSERT INTO listener_occasions VALUES ('user.xmas','xmasy','linear','Christmas')",
       "INSERT INTO listener_play_history VALUES (3,1000,1,'m',150,200,NULL)",
       "INSERT INTO listener_play_history VALUES (4,2000,2,'n',50,90,'auto')",
       "INSERT INTO player_state VALUES (1,22,'b')",
       "INSERT INTO listener_programs VALUES (1,'Kitchen')",
       "INSERT INTO listener_programs VALUES (2,'Evening')",
       "INSERT INTO listener_program_seeds VALUES (2,'seed-b')")
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


CAT = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, duration_ms INTEGER);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, end_ms INTEGER, boundary_src TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, source TEXT,
    PRIMARY KEY (passage_id, mbid));
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT, source TEXT);
"""


def cat(path, *sql):
    c = sqlite3.connect(path)
    c.executescript(CAT)
    for s in sql:
        c.execute(s)
    c.commit()
    c.close()


def test_catalogue(tmp):
    """[SPEC-STAR-047]: three ways against the ancestor."""
    base_rows = ("INSERT INTO files VALUES (1,'m1','C:/old/a.mp3',100)",
                 "INSERT INTO passages (passage_id, end_ms, boundary_src) VALUES (1,1000,'ingest')",
                 "INSERT INTO passages (passage_id, end_ms, boundary_src) VALUES (2,2000,'ingest')",
                 "INSERT INTO passage_recordings VALUES (1,'old','inherited:mulib')")
    base, desk, tl, pi = (os.path.join(tmp, f"{n}.cat") for n in ("base", "desk", "tl", "pi"))
    cat(base, *base_rows)
    # The copies have migrated: a column the ancestor predates, with a default.
    migrate = "ALTER TABLE passages ADD COLUMN fade_out_ms INTEGER NOT NULL DEFAULT 20"
    # the desktop: its own path, and a repaired duration
    cat(desk, migrate, "INSERT INTO files VALUES (1,'m1','C:/music/a.mp3',123)", *base_rows[1:])
    # teacherslounge: a Linux path, a re-credit, a manual boundary, and passage 2 dropped
    cat(tl, migrate, "INSERT INTO files VALUES (1,'m1','/home/sw/a.mp3',100)",
        "INSERT INTO passages VALUES (1,1500,'manual',102)",
        "INSERT INTO passage_recordings VALUES (1,'new','synced:GMKtec')",
        "INSERT INTO artists VALUES ('a','Someone','synced:GMKtec')")
    # a Pi: a computed boundary for passage 1, and passage 2 edited
    cat(pi, migrate, "INSERT INTO files VALUES (1,'m1','/srv/a.mp3',100)",
        "INSERT INTO passages VALUES (1,1400,'computed:x',20)",
        "INSERT INTO passages VALUES (2,2100,'computed:x',20)",
        "INSERT INTO passage_recordings VALUES (1,'old','inherited:mulib')",
        "INSERT INTO artists VALUES ('a','Someone','review:acoustid')")
    nodes = [dict(name="desktop", library=desk), dict(name="tl", library=tl), dict(name="pi", library=pi)]
    out = os.path.join(tmp, "cat-out")
    rep = sm.build({"catalogue": dict(base=base, machine_from="desktop", nodes=nodes)}, out)
    L = os.path.join(out, "library.db")
    check(rep.data["catalogue"]["integrity"] == "ok", "the merged catalogue must pass integrity_check")
    check(rows(L, "SELECT path, duration_ms FROM files") == [("C:/music/a.mp3", 123)],
          "machine scope stays the hub's own; a change only one copy made is taken")
    check(rows(L, "SELECT mbid FROM passage_recordings") == [("new",)],
          "a re-credit replaces the old credit rather than joining it")
    p = dict(rows(L, "SELECT passage_id, end_ms FROM passages"))
    check(p.get(1) == 1500, "a manual boundary outranks a conflicting computed one")
    check(p.get(2) == 2100, "a deletion loses to a conflicting modification")
    check(rows(L, "SELECT source FROM artists") == [("review:acoustid",)],
          "one edit under two labels: the hub keeps the original, not a spoke's synced: copy")
    check(rep.data["catalogue"]["tables"]["artists"]["conflicts"] == 0
          and rep.data["catalogue"]["tables"]["artists"]["relabelled"] == 1,
          "and it is counted as relabelled, not as a conflict")
    check(rows(L, "SELECT fade_out_ms FROM passages WHERE passage_id = 1") == [(102,)],
          "a value in a column the ancestor predates still counts as an edit")
    p1 = [c for c in rep.data["catalogue"]["conflicts"] if c["table"] == "passages" and c["key"] == [1]]
    check(p1 and set(p1[0]["values"]) == {"tl", "pi"},
          "a copy that only gained the migration's default is not a changer: "
          + str(p1 and sorted(p1[0]["values"])))
    kinds = {(c["table"], tuple(c["key"])) for c in rep.data["catalogue"]["conflicts"]}
    check(("passages", (1,)) in kinds and ("passages", (2,)) in kinds,
          f"both conflicts are reported: {kinds}")
    out2 = os.path.join(tmp, "cat-out2")
    nodes.reverse()
    sm.build({"catalogue": dict(base=base, machine_from="desktop", nodes=nodes)}, out2)
    check(open(L, "rb").read() == open(os.path.join(out2, "library.db"), "rb").read(),
          "the catalogue too: the same bytes whatever the order")


def test_receivers(tmp):
    """[SPEC-STAR-048]: an appliance's catalogue is received, not authored."""
    base, desk, app = (os.path.join(tmp, f"r-{n}.cat") for n in ("base", "desk", "app"))
    rows0 = ("INSERT INTO files VALUES (1,'m1','C:/a.mp3',100)",
             "INSERT INTO passages VALUES (1,1000,'ingest')",
             "INSERT INTO passages VALUES (2,2000,'ingest')")
    cat(base, *rows0)
    cat(desk, *rows0)
    # The appliance never got passage 2, holds a stale value for passage 1,
    # and alone has a works table.
    cat(app, "INSERT INTO files VALUES (1,'m1','/srv/a.mp3',100)",
        "INSERT INTO passages VALUES (1,900,'inherited:mulib')",
        "CREATE TABLE works (mbid TEXT PRIMARY KEY, title TEXT, source TEXT)",
        "INSERT INTO works VALUES ('w1','A Song','mb')")
    out = os.path.join(tmp, "r-out")
    rep = sm.build({"catalogue": dict(base=base, machine_from="desktop", nodes=[
        dict(name="desktop", library=desk), dict(name="app", library=app, role="receiver")])}, out)
    L = os.path.join(out, "library.db")
    p = dict(rows(L, "SELECT passage_id, end_ms FROM passages"))
    check(p == {1: 1000, 2: 2000},
          f"a receiver's absence is not a deletion, and its stale value is not taken: {p}")
    check(rows(L, "SELECT title FROM works") == [("A Song",)],
          "a table no author holds is taken from the receivers")
    rd = rep.data["catalogue"]["receiver_differences"]
    check([(x["table"], x["key"]) for x in rd] == [("passages", [1])],
          f"the stale value is listed for the person, and only it: {rd}")


def test_routine(tmp):
    """[SPEC-STAR-085]: the hub's catalogue taken as it stands; each node
    still gets it with its own paths, and its own plays."""
    hub, node = os.path.join(tmp, "h-hub.cat"), os.path.join(tmp, "h-node.cat")
    cat(hub, "INSERT INTO files VALUES (1,'A','C:/a.mp3',100)", "INSERT INTO artists VALUES ('x','New name','manual')")
    cat(node, "INSERT INTO files VALUES (1,'A','/srv/a.mp3',100)", "INSERT INTO artists VALUES ('x','Old name','synced:GMKtec')")
    hl, nl = os.path.join(tmp, "h-hub.lis"), os.path.join(tmp, "h-node.lis")
    db(hl)
    db(nl, "INSERT INTO listener_play_history VALUES (1,5000,1,'m',100,200,'auto')")
    out = os.path.join(tmp, "h-out")
    rep = sm.build({"hub_state_from": "desktop", "hub_library": hub,
                    "nodes": [dict(name="desktop", listener=hl),
                              dict(name="node", listener=nl, files=node)]}, out)
    check(rep.data["hub_library"]["integrity"] == "ok", "the hub's catalogue is taken whole")
    check(rows(os.path.join(out, "library.db"), "SELECT name FROM artists") == [("New name",)],
          "as the hub has it: only the hub authors")
    mine = os.path.join(out, "nodes", "node")
    check(rows(os.path.join(mine, "library.db"), "SELECT name FROM artists") == [("New name",)]
          and rows(os.path.join(mine, "library.db"), "SELECT path FROM files") == [("/srv/a.mp3",)],
          "the node receives it, with its own path")
    check(rows(os.path.join(mine, "listener.db"), "SELECT count(*) FROM listener_play_history") == [(1,)],
          "and keeps its own plays")


def test_translation(tmp):
    """[SPEC-STAR-049]: two files inducted in opposite order on the desktop
    and on an appliance -- the same passage id is different music."""
    base, desk, app = (os.path.join(tmp, f"t-{n}.cat") for n in ("base", "desk", "app"))
    cat(base)
    cat(desk, "INSERT INTO files VALUES (1,'A','C:/a.mp3',100)", "INSERT INTO files VALUES (2,'B','C:/b.mp3',100)",
        "INSERT INTO passages VALUES (1,1000,'ingest')", "INSERT INTO passages VALUES (2,1000,'ingest')")
    c = sqlite3.connect(desk)
    c.execute("ALTER TABLE passages ADD COLUMN file_id INTEGER")
    c.execute("ALTER TABLE passages ADD COLUMN kind TEXT")
    c.execute("ALTER TABLE passages ADD COLUMN start_ms INTEGER")
    c.execute("UPDATE passages SET file_id = passage_id, kind = 'radio', start_ms = 0")
    c.commit()
    c.close()
    cat(app, "INSERT INTO files VALUES (1,'B','/b.mp3',100)", "INSERT INTO files VALUES (2,'A','/a.mp3',100)",
        "INSERT INTO passages VALUES (1,1000,'ingest')", "INSERT INTO passages VALUES (2,1000,'ingest')")
    c = sqlite3.connect(app)
    for s in ("ALTER TABLE passages ADD COLUMN file_id INTEGER", "ALTER TABLE passages ADD COLUMN kind TEXT",
              "ALTER TABLE passages ADD COLUMN start_ms INTEGER",
              "UPDATE passages SET file_id = passage_id, kind = 'radio', start_ms = 0"):
        c.execute(s)
    c.commit()
    c.close()
    dl, al = os.path.join(tmp, "t-desk.lis"), os.path.join(tmp, "t-app.lis")
    db(dl)
    db(al, "INSERT INTO listener_play_history VALUES (1,5000,1,'rec-B',100,200,'auto')")
    out = os.path.join(tmp, "t-out")
    rep = sm.build({"hub_state_from": "desktop",
                    "nodes": [dict(name="desktop", listener=dl),
                              dict(name="app", listener=al, catalogue=app, files=app)],
                    "catalogue": dict(base=base, machine_from="desktop", nodes=[
                        dict(name="desktop", library=desk), dict(name="app", library=app, role="receiver")])},
                   out)
    hub = rows(os.path.join(out, "listener.db"), "SELECT passage_id, mbid FROM listener_play_history")
    check(hub == [], f"the appliance's plays stay its own, never the hub's [REQ-PD-113]: {hub}")
    mine = os.path.join(out, "nodes", "app")
    got = rows(os.path.join(mine, "listener.db"), "SELECT passage_id, mbid FROM listener_play_history")
    check(got == [(2, "rec-B")], f"in its own copy, its play of its passage 1 (file B) is the hub's passage 2: {got}")
    check(rep.data["translations"]["app"]["ids"] == [[1, 2], [2, 1]], "and the translation is reported")
    f = rows(os.path.join(mine, "library.db"), "SELECT file_id, audio_md5, path FROM files ORDER BY file_id")
    check(f == [(1, "A", "/a.mp3"), (2, "B", "/b.mp3")],
          f"its catalogue is the hub's, with its own paths matched by audio_md5, not file id: {f}")
    check(not os.path.exists(os.path.join(out, "nodes", "desktop")), "the hub's own pair is not repeated")


def test_rekeyed(tmp):
    """[SPEC-RLK-155]: the hub has re-keyed a file and the node has not. The
    node's old `audio_md5` is the same file, so its path still lands, and its
    passage is not read as music the hub no longer has."""
    hub, node = os.path.join(tmp, "k-hub.cat"), os.path.join(tmp, "k-node.cat")
    for path, md5, where in ((hub, "NEW", "C:/a.mp3"), (node, "OLD", "/srv/a.mp3")):
        cat(path, f"INSERT INTO files VALUES (1,'{md5}','{where}',100)",
            "INSERT INTO passages VALUES (1,1000,'ingest')")
        c = sqlite3.connect(path)
        for s in ("ALTER TABLE passages ADD COLUMN file_id INTEGER", "ALTER TABLE passages ADD COLUMN kind TEXT",
                  "ALTER TABLE passages ADD COLUMN start_ms INTEGER",
                  "UPDATE passages SET file_id = 1, kind = 'radio', start_ms = 0"):
            c.execute(s)
        c.commit()
        c.close()
    c = sqlite3.connect(hub)
    c.execute("CREATE TABLE audio_md5_aliases (old_md5 TEXT PRIMARY KEY, new_md5 TEXT NOT NULL, "
              "generator TEXT NOT NULL, rekeyed_at TEXT NOT NULL)")
    c.execute("INSERT INTO audio_md5_aliases VALUES ('OLD','NEW','symphonia@0.5.5','2026-09-27T00:00:00Z')")
    c.commit()
    c.close()
    hl, nl = os.path.join(tmp, "k-hub.lis"), os.path.join(tmp, "k-node.lis")
    db(hl)
    db(nl, "INSERT INTO listener_play_history VALUES (1,5000,1,'m',100,200,'auto')")
    out = os.path.join(tmp, "k-out")
    rep = sm.build({"hub_state_from": "desktop", "hub_library": hub,
                    "nodes": [dict(name="desktop", listener=hl),
                              dict(name="node", listener=nl, catalogue=node, files=node)]}, out)
    mine = os.path.join(out, "nodes", "node")
    f = rows(os.path.join(mine, "library.db"), "SELECT audio_md5, path FROM files")
    check(f == [("NEW", "/srv/a.mp3")], f"the node's copy takes the new key, with its own path: {f}")
    t = rep.data["translations"]["node"]
    check(t["ids"] == [] and t["unmatched"] == [], f"its passage is the hub's, not gone: {t}")
    check(rows(os.path.join(mine, "listener.db"), "SELECT passage_id FROM listener_play_history") == [(1,)],
          "and its play keeps its passage")


def main() -> int:
    tmp = tempfile.mkdtemp()
    test_rekeyed(tmp)
    test_catalogue(tmp)
    test_receivers(tmp)
    test_translation(tmp)
    test_routine(tmp)
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
    check(h == [(7, 1000, 100, "auto")],
          f"plays are the hub's own, b's neither merged nor shared [REQ-PD-113]: {h}")
    check(rows(L, "SELECT passage_id FROM player_state") == [(11,)],
          "node state comes from the hub's own node, not merged")
    B = os.path.join(out, "nodes", "b", "listener.db")
    hb = rows(B, "SELECT play_id, played_at, heard_ms FROM listener_play_history ORDER BY play_id")
    check(hb == [(3, 1000, 150), (4, 2000, 50)], f"b's copy holds b's own plays, unchanged: {hb}")
    check(rows(B, "SELECT passage_id FROM player_state") == [(22,)], "and b's own player state")
    check(rows(B, "SELECT rotation FROM listener_preferences WHERE subject_id = 'X'") == [(2,)],
          "and the household's edits, merged exactly as the hub's are")

    # [SPEC-MTR-040]: programmes are each node's own, never merged.
    check(rows(L, "SELECT program_id, name FROM listener_programs") == [(1, "Morning")]
          and rows(L, "SELECT mbid FROM listener_program_seeds") == [("seed-a",)],
          "the hub keeps its own programmes, none of b's")
    check(rows(B, "SELECT program_id, name FROM listener_programs ORDER BY program_id") == [(1, "Kitchen"), (2, "Evening")]
          and rows(B, "SELECT mbid FROM listener_program_seeds") == [("seed-b",)],
          "and b keeps its own, even one numbered as the hub's")

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
