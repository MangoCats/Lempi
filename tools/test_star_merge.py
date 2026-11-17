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
    passage_uid TEXT, mbid TEXT, heard_ms INTEGER, span_ms INTEGER, selected_by TEXT);
CREATE TABLE player_state (id INTEGER PRIMARY KEY, passage_uid TEXT, updated_at TEXT);
CREATE TABLE listener_programs (program_id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE listener_program_seeds (program_id INTEGER, mbid TEXT, PRIMARY KEY (program_id, mbid));
CREATE TABLE listener_settings (id INTEGER PRIMARY KEY, artist_time_scale REAL,
    utc_offset_minutes INTEGER, updated_at TEXT);
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


def test_held_out(tmp):
    """[SPEC-STAR-082]: a node's copy leaves out the hub's music it does not
    hold, and every row that names it, and says so -- where until 2026-10-14
    one album added on the hub refused the whole sync."""
    out = os.path.join(tmp, "ho-out")
    os.makedirs(out)
    hub = sqlite3.connect(os.path.join(out, "library.db"))
    hub.executescript("""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT UNIQUE, path TEXT, size_bytes INTEGER,
                            mtime REAL, last_seen TEXT, first_seen TEXT);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, end_ms INTEGER);
        CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
        CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE file_releases (audio_md5 TEXT PRIMARY KEY, release_mbid TEXT);
        CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE flavor (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, characteristic TEXT NOT NULL,
                             class TEXT NOT NULL, value REAL NOT NULL,
                             PRIMARY KEY (subject_kind, subject_id, characteristic, class)) WITHOUT ROWID;
        INSERT INTO files VALUES (1, 'held', 'C:/Music/A/Old Album/1.mp3', 1, 1, 't', 't'),
                                 (2, 'new', 'C:/Music/J/Time the Conqueror (2008)/1.mp3', 1, 1, 't', 't');
        INSERT INTO passages VALUES (10, 1, 100), (20, 2, 200), (21, 2, 300);
        INSERT INTO passage_recordings VALUES (10, 'r1'), (20, 'r2'), (21, 'r2');
        INSERT INTO file_tags VALUES (1, 'a'), (2, 'b');
        INSERT INTO file_releases VALUES ('held', 'rel1'), ('new', 'rel2');
        INSERT INTO recordings VALUES ('r1', 'One'), ('r2', 'Two');
        INSERT INTO flavor VALUES ('passage', '10', 'mood', 'happy', 0.5), ('passage', '20', 'mood', 'happy', 0.5),
                                  ('passage', '21', 'mood', 'sad', 0.5), ('recording', 'r2', 'mood', 'happy', 0.5),
                                  ('recording', '20', 'mood', 'happy', 0.5);
    """)
    hub.commit()
    hub.close()
    summary = os.path.join(tmp, "ho-node.db")
    node = sqlite3.connect(summary)
    node.executescript("CREATE TABLE files (audio_md5 TEXT, path TEXT, size_bytes INTEGER, mtime REAL, "
                       "last_seen TEXT, first_seen TEXT);"
                       "INSERT INTO files VALUES ('held', '/srv/a.mp3', 1, 1, 't', 't');")
    node.commit()
    node.close()
    rep = sm.Report()
    sm.catalogue_copies({"hub_state_from": "hub", "nodes": [dict(name="hub"), dict(name="pi", files=summary)]},
                        out, rep)
    P = os.path.join(out, "nodes", "pi", "library.db")
    check(rows(P, "SELECT audio_md5, path FROM files") == [("held", "/srv/a.mp3")],
          "the node's copy holds the music it has, by its own path")
    check(rows(P, "SELECT passage_id FROM passages") == [(10,)]
          and rows(P, "SELECT passage_id FROM passage_recordings") == [(10,)]
          and rows(P, "SELECT file_id FROM file_tags") == [(1,)]
          and rows(P, "SELECT audio_md5 FROM file_releases") == [("held",)],
          "and nothing that names the file it lacks: passages, credits, tags, its release")
    check(len(rows(P, "SELECT mbid FROM recordings")) == 2, "music's own names stay")
    check(rows(P, "SELECT subject_kind, subject_id FROM flavor ORDER BY 1, 2")
          == [("passage", "10"), ("recording", "20"), ("recording", "r2")],
          "a passage's flavor goes with the passage it names, and a recording's stays, whatever its id spells")
    h = rep.data["copies"]["pi"].get("held_out") or {}
    check(h.get("files") == 1 and h.get("albums") == ["Time the Conqueror (2008)"]
          and h.get("rows", {}).get("passages") == 2 and h.get("rows", {}).get("flavor") == 2,
          f"what was left out, by album: {h}")
    said = "\n".join(sm.copies_section(rep.data["copies"]))
    check("not on pi, so left out of its copy" in said and "Time the Conqueror (2008)" in said
          and "Export page" in said, f"and the report says so, and what to do: {said}")


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


REVIEW = ("CREATE TABLE id_reviews (passage_uid TEXT PRIMARY KEY, decision TEXT, chosen_mbid TEXT, "
          "decided_at TEXT, applied_at TEXT, origin TEXT)")
REVIEW_BY_NUMBER = REVIEW.replace("passage_uid TEXT", "passage_id INTEGER")


def opposite_numbered(tmp, tag):
    """Two catalogues holding the same two files numbered the other way round: on
    the desktop passage 1 is file A and 2 is B; on the appliance 1 is B and 2 is A.
    Each passage has its uid, the same on both: `a` thirty-two times for file A, `b` for B [SPEC-UID-030]."""
    base, desk, app = (os.path.join(tmp, f"{tag}-{n}.cat") for n in ("base", "desk", "app"))
    cat(base)
    for path, files in ((desk, [(1, "A", "C:/a.mp3"), (2, "B", "C:/b.mp3")]),
                        (app, [(1, "B", "/b.mp3"), (2, "A", "/a.mp3")])):
        cat(path, *[f"INSERT INTO files VALUES ({i},'{m}','{p}',100)" for i, m, p in files],
            "INSERT INTO passages VALUES (1,1000,'ingest')", "INSERT INTO passages VALUES (2,1000,'ingest')")
        c = sqlite3.connect(path)
        for q in ("ALTER TABLE passages ADD COLUMN file_id INTEGER", "ALTER TABLE passages ADD COLUMN kind TEXT",
                  "ALTER TABLE passages ADD COLUMN start_ms INTEGER", "ALTER TABLE passages ADD COLUMN passage_uid TEXT",
                  "UPDATE passages SET file_id = passage_id, kind = 'radio', start_ms = 0",
                  "UPDATE passages SET passage_uid = replace(printf('%32s', ''), ' ', "
                  "lower((SELECT audio_md5 FROM files f WHERE f.file_id = passages.file_id)))"):
            c.execute(q)
        c.commit()
        c.close()
    return base, desk, app


def test_translation(tmp):
    """[IMPL-UID-064]: two files inducted in opposite order on the desktop and on an
    appliance, so the same passage number is different music. By uid nothing is
    translated: a review of B is one row whichever node made it, the appliance's
    play stays its own, and its copy last sent, still by number from before Stage B,
    is read through no map at all: its review by number is left out, and counted
    [IMPL-UID-080]. Until Stage D it was read through its own catalogue's map."""
    base, desk, app = opposite_numbered(tmp, "t")
    dl, al, sent = (os.path.join(tmp, f"t-{n}.lis") for n in ("desk", "app", "sent"))
    review = lambda who: f"INSERT INTO id_reviews VALUES ({who},'reassigned','rec-B2','2026-10-15 00:20:04',NULL,NULL)"
    db(dl, REVIEW)
    db(al, REVIEW, review("'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'"), "INSERT INTO listener_play_history VALUES (1,5000,'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','rec-B',100,200,'auto')")
    db(sent, REVIEW_BY_NUMBER, review("1"))             # its 1 is B: as it was last sent, by number
    out = os.path.join(tmp, "t-out")
    rep = sm.build({"hub_state_from": "desktop",
                    "nodes": [dict(name="desktop", listener=dl),
                              dict(name="app", listener=al, catalogue=app, files=app, sent=sent)],
                    "catalogue": dict(base=base, machine_from="desktop", nodes=[
                        dict(name="desktop", library=desk), dict(name="app", library=app, role="receiver")])},
                   out)
    hub = rows(os.path.join(out, "listener.db"), "SELECT passage_uid, mbid FROM listener_play_history")
    check(hub == [], f"the appliance's plays stay its own, never the hub's [REQ-PD-113]: {hub}")
    mine = os.path.join(out, "nodes", "app")
    got = rows(os.path.join(mine, "listener.db"), "SELECT passage_uid, mbid FROM listener_play_history")
    check(got == [("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "rec-B")], f"in its own copy, its play of B is still B's: {got}")
    merged = rows(os.path.join(out, "listener.db"), "SELECT passage_uid, chosen_mbid FROM id_reviews")
    check(merged == [("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "rec-B2")], f"its review of B is the hub's review of B, by uid: {merged}")
    check(rows(os.path.join(mine, "listener.db"), "SELECT passage_uid FROM id_reviews") == [("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",)],
          "and goes back to it as it is")
    check(rep.data["items"] == [], f"its copy by number adds no item: {rep.data['items']}")
    check(rep.data["translations"].get("app", {}).get("by_number") == {"id_reviews": 1},
          f"and the report says one row by number was left out: {rep.data['translations']}")
    report = open(os.path.join(out, "report.md"), encoding="utf-8").read()
    check("## Left out, by number" in report and "through its own" not in report,
          f"in words, and nothing is said to be read through a map: {report}")
    f = rows(os.path.join(mine, "library.db"), "SELECT file_id, audio_md5, path FROM files ORDER BY file_id")
    check(f == [(1, "A", "/a.mp3"), (2, "B", "/b.mp3")],
          f"its catalogue is the hub's, with its own paths matched by audio_md5, not file id: {f}")
    check(not os.path.exists(os.path.join(out, "nodes", "desktop")), "the hub's own pair is not repeated")


def test_a_snapshot_by_number_is_refused(tmp):
    """[IMPL-UID-064]: a player from before Stage B sends its listener by number,
    and would be sent a copy it cannot read. The run is refused by its name, and
    nothing is merged; a phone, which sends no table naming a passage, is not."""
    base, desk, app = opposite_numbered(tmp, "sn")
    dl, al = (os.path.join(tmp, f"sn-{n}.lis") for n in ("desk", "app"))
    db(dl, REVIEW)
    db(al, REVIEW_BY_NUMBER)
    out = os.path.join(tmp, "sn-out")
    try:
        sm.build({"hub_state_from": "desktop", "hub_library": desk,
                  "nodes": [dict(name="desktop", listener=dl), dict(name="app", listener=al, catalogue=app, files=app)]},
                 out)
        check(False, "a listener by number must be refused")
    except SystemExit as e:
        check("app's listener names passages by number (id_reviews)" in str(e) and "[IMPL-UID-064]" in str(e), f"{e}")
    check(not os.path.exists(os.path.join(out, "listener.db")), "and nothing was merged")
    phone = os.path.join(tmp, "sn-phone.lis")
    pc = sqlite3.connect(phone)
    pc.execute("CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT, flagged_at TEXT, origin TEXT, "
               "PRIMARY KEY (subject_kind, subject_id))")
    pc.commit()
    pc.close()
    sm.build({"hub_state_from": "desktop", "hub_library": desk,
              "nodes": [dict(name="desktop", listener=dl), dict(name="phone", listener=phone)]},
             os.path.join(tmp, "sn-out2"))
    check(os.path.exists(os.path.join(tmp, "sn-out2", "listener.db")), "a phone with no table naming a passage merges")


LYRICS = """
CREATE TABLE passage_lyrics (audio_md5 TEXT NOT NULL, kind TEXT NOT NULL, start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL, mbid TEXT, text TEXT NOT NULL, no_words INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (audio_md5, kind, start_ms));
CREATE INDEX passage_lyrics_mbid ON passage_lyrics(mbid);
"""


def test_passage_lyrics(tmp):
    """[SPEC-LYR-200], [SPEC-LYR-205]: words saved for a passage are keyed by
    what it is, never by its number. So between two nodes that number the same
    music the other way round they cross untranslated; the latest save wins;
    every node's copy holds every row, one for a file it lacks included; a
    cleared row travels as a row; and an older player's listener, which has no
    such table, neither stops the merge nor loses a row for anyone else."""
    base, desk, app = (os.path.join(tmp, f"ly-{n}.cat") for n in ("base", "desk", "app"))
    cat(base)
    # File A is passage 1 on the desktop and passage 2 on the appliance.
    for path, files in ((desk, [(1, "A", "C:/a.mp3"), (2, "B", "C:/b.mp3")]),
                        (app, [(1, "B", "/b.mp3"), (2, "A", "/a.mp3")])):
        cat(path, *[f"INSERT INTO files VALUES ({i},'{m}','{p}',100)" for i, m, p in files],
            "INSERT INTO passages VALUES (1,1000,'ingest')", "INSERT INTO passages VALUES (2,1000,'ingest')")
        c = sqlite3.connect(path)
        for s in ("ALTER TABLE passages ADD COLUMN file_id INTEGER", "ALTER TABLE passages ADD COLUMN kind TEXT",
                  "ALTER TABLE passages ADD COLUMN start_ms INTEGER",
                  "UPDATE passages SET file_id = passage_id, kind = 'radio', start_ms = 0"):
            c.execute(s)
        c.commit()
        c.close()
    dl, al, ol = (os.path.join(tmp, f"ly-{n}.lis") for n in ("desk", "app", "old"))
    db(dl)
    db(al)
    db(ol)                                              # a player from before the table
    for path, sql in ((dl, ["INSERT INTO passage_lyrics VALUES ('A','radio',0,1000,'rec-A','typed on the hub',0,"
                            "'2026-11-12 10:00:00.000','desktop')",
                            "INSERT INTO passage_lyrics VALUES ('Z','radio',0,1000,'rec-Z','for a file only the hub has',0,"
                            "'2026-11-12 10:05:00.000','desktop')",
                            # Within one second of the appliance's save of the same passage, and after it.
                            "INSERT INTO passage_lyrics VALUES ('S','radio',0,1000,'rec-S','the later of two in one second',0,"
                            "'2026-11-12 12:00:00.900','desktop')"]),
                      (al, ["INSERT INTO passage_lyrics VALUES ('A','radio',0,1000,'rec-A','corrected on the appliance',0,"
                            "'2026-11-12 11:00:00.250','app')",
                            "INSERT INTO passage_lyrics VALUES ('S','radio',0,1000,'rec-S','the earlier of two in one second',0,"
                            "'2026-11-12 12:00:00.100','app')",
                            "INSERT INTO passage_lyrics VALUES ('B','radio',0,1000,'rec-B','',0,"
                            "'2026-11-12 11:30:00.000','app')"])):
        c = sqlite3.connect(path)
        c.executescript(LYRICS)
        for s in sql:
            c.execute(s)
        c.commit()
        c.close()
    out = os.path.join(tmp, "ly-out")
    rep = sm.build({"hub_state_from": "desktop",
                    "nodes": [dict(name="desktop", listener=dl),
                              dict(name="app", listener=al, catalogue=app, files=app),
                              dict(name="old", listener=ol)],
                    "catalogue": dict(base=base, machine_from="desktop", nodes=[
                        dict(name="desktop", library=desk), dict(name="app", library=app, role="receiver")])},
                   out)
    q = "SELECT audio_md5, kind, start_ms, text, origin FROM passage_lyrics ORDER BY audio_md5"
    want = [("A", "radio", 0, "corrected on the appliance", "app"),
            ("B", "radio", 0, "", "app"),
            ("S", "radio", 0, "the later of two in one second", "desktop"),
            ("Z", "radio", 0, "for a file only the hub has", "desktop")]
    hub = rows(os.path.join(out, "listener.db"), q)
    check(hub == want, f"the latest save wins, and a cleared row and a hub-only row are kept: {hub}")
    # [SPEC-LYR-140]: the stamp is to the millisecond, and is compared to it.
    # Cut to the second this was a tie, kept for `app` as the first by name.
    within = [i for i in rep.data["items"] if i["table"] == "passage_lyrics" and i["key"][0] == "S"]
    check([i["kind"] for i in within] == ["conflict"] and within[0]["default"] == "desktop",
          f"two saves 800 ms apart are a newer and an older, never a tie: {within}")
    check({c["node"]: c["stamp"] for c in within[0]["candidates"]}
          == {"app": "2026-11-12 12:00:00.1", "desktop": "2026-11-12 12:00:00.9"} if within else False,
          f"and the item shows each stamp to its fraction: {within}")
    for a, b in (("2026-11-12 10:00:00", "2026-11-12 10:00:00.100"), ("2026-11-12 10:00:00.100", "2026-11-12 10:00:00.9"),
                 ("2026-11-12T10:00:00.900Z", "2026-11-12 10:00:01"), ("2026-11-12 10:00:00.09", "2026-11-12 10:00:00.1")):
        check(sm.norm_time(a) < sm.norm_time(b), f"{a} reads before {b}: {sm.norm_time(a)!r} {sm.norm_time(b)!r}")
    check(sm.norm_time("2026-11-12 10:00:00") == sm.norm_time("2026-11-12T10:00:00.000Z") == "2026-11-12 10:00:00",
          "a whole second is one time however it is written")
    check(sm.norm_time("2026-11-12T10:00:00+02:00") == "2026-11-12 10:00:00" and sm.norm_time(0) == "1970-01-01 00:00:00",
          "a zone is dropped and an epoch read, as before")
    check("app" not in rep.data["translations"],
          "the two nodes number this music the other way round, and nothing is translated [IMPL-UID-064]")
    mine = rows(os.path.join(out, "nodes", "app", "listener.db"), q)
    check(mine == want, f"the appliance's copy holds every row under the same keys, file Z's included: {mine}")
    check(not rep.data["copies"]["app"].get("not_on_node"), "and none is left out for naming no passage it holds")
    older = rows(os.path.join(out, "nodes", "old", "listener.db"), q)
    check(older == want, f"an older player's copy is what it will hold once it is current: {older}")
    t = rep.data["tables"]["passage_lyrics"]
    check(t["rule"] == sm.LWW and t["merged"] == 4 and t["per_node"] == {"app": 3, "desktop": 3, "old": 0},
          f"reported as a last-write-wins table: {t}")


def test_lyrics_uids_come_from_the_hub(tmp):
    """[IMPL-UID-063]: the hub alone sets a saved row's `passage_uid`, and changes no
    save in doing it -- the same stamp, the same words. Its value reaches every node's
    copy, with no item and no tie, though the node's row lacks it; and a node's new
    save, which the hub does not hold, keeps the uid it was saved with."""
    lyr = [x for x in LYRICS.replace("origin TEXT, PRIMARY KEY", "origin TEXT, passage_uid TEXT, PRIMARY KEY")
           .split(";") if x.strip()]
    row = lambda md5, uid, at: (f"INSERT INTO passage_lyrics VALUES ('{md5}','radio',0,200000,'rec','words',0,"
                                f"'{at}','here',{uid})")
    U, N = "'" + "a" * 32 + "'", "'" + "b" * 32 + "'"
    dl, al, sent = (os.path.join(tmp, f"lu-{n}.lis") for n in ("desk", "app", "sent"))
    db(dl, *lyr, row("A", U, "2026-11-12 10:00:00.000"))                        # set by the hub
    db(al, *lyr, row("A", "NULL", "2026-11-12 10:00:00.000"),                   # as the node was sent it
       row("B", N, "2026-11-16 09:00:00.000"))                                 # and its own new save
    db(sent, *lyr, row("A", "NULL", "2026-11-12 10:00:00.000"))
    out = os.path.join(tmp, "lu-out")
    rep = sm.build({"hub_state_from": "desktop",
                    "nodes": [dict(name="desktop", listener=dl), dict(name="app", listener=al, sent=sent)]}, out)
    q = "SELECT audio_md5, passage_uid FROM passage_lyrics ORDER BY audio_md5"
    want = [("A", "a" * 32), ("B", "b" * 32)]
    check(rows(os.path.join(out, "listener.db"), q) == want, f"the hub's: {rows(os.path.join(out, 'listener.db'), q)}")
    check(rows(os.path.join(out, "nodes", "app", "listener.db"), q) == want, "and the node's copy, the same")
    check(rep.data["items"] == [], f"no item: {rep.data['items']}")
    check(not any(d["table"] == "passage_lyrics" and "tie" in d["what"] for d in rep.data["decisions"]),
          f"and no tie: {rep.data['decisions']}")


def test_a_hub_that_set_no_uid_keeps_a_nodes_save_whole(tmp):
    """[IMPL-UID-065]: the hub holds a row it could not place, its uid NULL, and a
    node saves newer words under the same key with its passage's uid. The node's
    save wins, and so does its uid: the hub's NULL is a value never set, not one
    set to nothing. Until 2026-11-17 the hub's NULL replaced it, so every node was
    sent the save without the uid that said which passage's it was. Where the hub
    did set a uid, its value is still the merge's, whatever a node holds."""
    lyr = [x for x in LYRICS.replace("origin TEXT, PRIMARY KEY", "origin TEXT, passage_uid TEXT, PRIMARY KEY")
           .split(";") if x.strip()]
    row = lambda md5, uid, text, at: (f"INSERT INTO passage_lyrics VALUES ('{md5}','radio',0,200000,'rec','{text}',0,"
                                      f"'{at}','here',{uid})")
    U, H, N = ("'" + ch * 32 + "'" for ch in "abc")
    dl, al, sent = (os.path.join(tmp, f"lun-{n}.lis") for n in ("desk", "app", "sent"))
    db(dl, *lyr, row("A", "NULL", "old", "2026-11-12 10:00:00.000"),          # one the hub could not place
       row("C", H, "old", "2026-11-12 10:00:00.000"))                        # one it did
    db(al, *lyr, row("A", U, "new", "2026-11-17 09:00:00.000"),               # the node's newer save
       row("C", N, "new", "2026-11-17 09:00:00.000"))
    db(sent, *lyr, row("A", "NULL", "old", "2026-11-12 10:00:00.000"), row("C", H, "old", "2026-11-12 10:00:00.000"))
    out = os.path.join(tmp, "lun-out")
    rep = sm.build({"hub_state_from": "desktop",
                    "nodes": [dict(name="desktop", listener=dl), dict(name="app", listener=al, sent=sent)]}, out)
    q = "SELECT audio_md5, text, passage_uid FROM passage_lyrics ORDER BY audio_md5"
    want = [("A", "new", "a" * 32), ("C", "new", "b" * 32)]
    for where in ("listener.db", os.path.join("nodes", "app", "listener.db")):
        got = rows(os.path.join(out, where), q)
        check(got == want, f"{where}: the node's save with its own uid, and the hub's uid where it set one: {got}")
    check(rep.data["items"] == [], f"no item: {rep.data['items']}")


def test_a_review_survives_two_rounds(tmp):
    """[SPEC-STAR-049] both ways. Found 2026-10-18: an id review made once on
    the hub was on eight passages after three syncs, because a node's copy
    was written in the hub's numbers and read back as the node's. By uid there
    is no numbering to write it in [IMPL-UID-064]. Two rounds here, the node's
    copy of the first fed back as its listener in the second: the review stays
    one row, on the same music, on both sides."""
    base, desk, app = opposite_numbered(tmp, "rr")     # the hub's 1 is A; the node's 1 is B
    dl, al = os.path.join(tmp, "rr-desk.lis"), os.path.join(tmp, "rr-app.lis")
    db(dl, REVIEW, "INSERT INTO id_reviews VALUES ('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','reassigned','rec-A','2026-10-15 00:20:04',NULL,NULL)")
    db(al, REVIEW, "INSERT INTO listener_play_history VALUES (1,5000,'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','rec-B',100,200,'auto')")

    def merge(tag, node_listener):
        out = os.path.join(tmp, f"rr-out-{tag}")
        sm.build({"hub_state_from": "desktop", "hub_library": desk,
                  "nodes": [dict(name="desktop", listener=dl),
                            dict(name="app", listener=node_listener, catalogue=app, files=app)]}, out)
        return os.path.join(out, "listener.db"), os.path.join(out, "nodes", "app", "listener.db")

    hub1, node1 = merge("1", al)
    got = rows(node1, "SELECT passage_uid, chosen_mbid FROM id_reviews")
    check(got == [("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "rec-A")], f"the node's copy names file A's passage by its uid: {got}")
    check(rows(node1, "SELECT passage_uid FROM listener_play_history") == [("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",)],
          "and its own play stays on its own passage of file B")
    hub2, node2 = merge("2", node1)          # the node now holds what it was sent
    got = rows(hub2, "SELECT passage_uid, chosen_mbid FROM id_reviews")
    check(got == [("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "rec-A")], f"a second round leaves one review, on file A: {got}")
    check(rows(node2, "SELECT passage_uid, chosen_mbid FROM id_reviews") == [("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "rec-A")],
          "and the node's copy is unchanged")


def opposite(tmp, tag):
    """Two catalogues numbering files A and B the other way round: the hub's
    passage 1 is file A, the node's passage 1 is file B. Each passage has the
    same uid on both, `a` or `b` thirty-two times."""
    _, desk, app = opposite_numbered(tmp, tag)
    return desk, app


def backup_of(tmp, tag, epoch, *sql):
    """A history folder holding one copy, named for `epoch`."""
    d = os.path.join(tmp, f"{tag}-history")
    os.makedirs(d)
    db(os.path.join(d, f"listener-{epoch}.db"), *sql)
    return d


def test_a_removal_is_read_in_the_hubs_numbering(tmp):
    """[IMPL-UID-042]: a node clears its flag on file B. Until 2026-11-14 the
    evidence was compared in the node's numbers against the merge's in the hub's:
    the hub's flag on ITS passage 1 -- file A, other music -- was deleted
    everywhere, and file B's flag stayed. By uid there is one numbering
    [IMPL-UID-064], and the node's backup holds its flags by uid too. Until Stage D
    a backup from before Stage B, by its own numbers, was read through its own map;
    one now is left out (`test_a_history_by_number_is_no_evidence`)."""
    desk, app = opposite(tmp, "rn")
    # Flagged before the backup of 1789000000 (2026-09-10), so not re-added since.
    flag = lambda sid, o="NULL": f"INSERT INTO listener_flags VALUES ('passage','{sid}','2026-08-01 00:00:00',{o})"
    dl, al = os.path.join(tmp, "rn-desk.lis"), os.path.join(tmp, "rn-app.lis")
    db(dl, flag("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "'GMKtec'"), flag("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"))    # the hub: both
    db(al, flag("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))                                # the node now: file A only
    bk = backup_of(tmp, "rn-app", 1789000000, flag("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"), flag("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))   # B and A, by uid
    out = os.path.join(tmp, "rn-out")
    rep = sm.build({"hub_state_from": "desktop", "hub_library": desk,
                    "nodes": [dict(name="desktop", listener=dl),
                              dict(name="app", listener=al, catalogue=app, files=app, backups=bk)]}, out)
    hub = rows(os.path.join(out, "listener.db"), "SELECT subject_id FROM listener_flags ORDER BY subject_id")
    check(hub == [("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",)], f"file B's flag is removed and file A's is kept: {hub}")
    mine = rows(os.path.join(out, "nodes", "app", "listener.db"), "SELECT subject_id FROM listener_flags")
    check(mine == [("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",)], f"and the node keeps file A's flag, by the same uid: {mine}")
    check(any(d["rule"] == "SPEC-STAR-050" and d["key"] == ["passage", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"] for d in rep.data["decisions"]),
          f"the removal is reported by file B's uid: {rep.data['decisions']}")


def test_a_withdrawn_review_stays_withdrawn(tmp):
    """[IMPL-UID-042]: an id review withdrawn on the hub. Until 2026-11-14 the
    review tables took no removals, so the node's copy brought it back at the
    next merge -- how 27 copied reviews outlived their repair. The node's copy
    last sent and its backups hold it by uid [IMPL-UID-064]."""
    desk, app = opposite(tmp, "wr")
    row = lambda who: f"INSERT INTO id_reviews VALUES ({who},'reassigned','rec-A','2026-10-15 00:20:04',NULL,NULL)"
    dl, al, sent = (os.path.join(tmp, f"wr-{n}.lis") for n in ("desk", "app", "sent"))
    db(dl, REVIEW)                                  # withdrawn on the hub
    db(al, REVIEW, row("'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"))                  # the node still holds it
    db(sent, REVIEW, row("'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"))                  # and was last sent it
    hub_bk = backup_of(tmp, "wr-desk", 1789000000, REVIEW, row("'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"))
    app_bk = backup_of(tmp, "wr-app", 1789000000, REVIEW, row("'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"))
    out = os.path.join(tmp, "wr-out")
    rep = sm.build({"hub_state_from": "desktop", "hub_library": desk,
                    "nodes": [dict(name="desktop", listener=dl, backups=hub_bk),
                              dict(name="app", listener=al, catalogue=app, files=app, backups=app_bk, sent=sent)]}, out)
    check(rows(os.path.join(out, "listener.db"), "SELECT * FROM id_reviews") == [],
          "the hub's withdrawal holds: the node's copy does not bring the review back")
    check(rows(os.path.join(out, "nodes", "app", "listener.db"), "SELECT * FROM id_reviews") == [],
          "and the node's copy loses it")
    check(rep.data["items"] == [], f"a removal no node edited is no question: {rep.data['items']}")


def test_a_history_by_number_is_no_evidence(tmp):
    """[IMPL-UID-080]: a backup from before Stage B names its flags by the node's own
    numbers. Until Stage D it was read through the node's own map from number to uid;
    now nothing of it is read, so it shows no removal. The node's flag on B, cleared
    since, then stands: a removal no source by uid evidences is no removal. (A copy
    last sent by number is counted in the report, `test_translation`; a backup is
    read once per table and is not.)"""
    desk, app = opposite(tmp, "hn")
    flag = lambda sid: f"INSERT INTO listener_flags VALUES ('passage','{sid}','2026-08-01 00:00:00',NULL)"
    dl, al = os.path.join(tmp, "hn-desk.lis"), os.path.join(tmp, "hn-app.lis")
    db(dl, flag("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), flag("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"))
    db(al, flag("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
    bk = backup_of(tmp, "hn-app", 1789000000, flag(1), flag(2))   # by its own numbers
    out = os.path.join(tmp, "hn-out")
    rep = sm.build({"hub_state_from": "desktop", "hub_library": desk,
                    "nodes": [dict(name="desktop", listener=dl),
                              dict(name="app", listener=al, catalogue=app, files=app, backups=bk)]}, out)
    hub = rows(os.path.join(out, "listener.db"), "SELECT subject_id FROM listener_flags ORDER BY subject_id")
    check(hub == [("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",), ("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",)], f"no number reads as a removal of anyone's flag: {hub}")
    check(not any(d["rule"] == "SPEC-STAR-050" for d in rep.data["decisions"]), f"none is reported: {rep.data['decisions']}")


def test_a_node_that_missed_the_run(tmp):
    """[IMPL-UID-042]: run R1 removed flag F; node b missed R1, and still holds
    F unchanged from its R0 copy, which its history is. Until 2026-11-14 F came
    back from b at R2. An edit b made to F since is not undone, and the newest
    copy is the newest run's merge, not the hub's: a hub left out of R1's
    commit does not make R1's additions read as removed."""
    F = lambda o="NULL": f"INSERT INTO listener_flags VALUES ('recording','F','2026-10-01 00:00:00',{o})"
    G = "INSERT INTO listener_flags VALUES ('recording','G','2026-10-02 00:00:00',NULL)"
    p = lambda n: os.path.join(tmp, f"mr-{n}.lis")
    db(p("r0"), F())                                # R0's merge
    db(p("r1"), G)                                  # R1's: F removed, G added by a
    db(p("r1-a"), G)                                # what a was sent in R1

    def run(tag, b_now, newest=True, hub_sent=("r1", "R1")):
        db(p(f"{tag}-desk"))                        # the hub holds neither now
        db(p(f"{tag}-a"), G)
        db(p(f"{tag}-b"), *b_now)
        nodes = [dict(name="desktop", listener=p(f"{tag}-desk"), sent=p(hub_sent[0]), sent_run=hub_sent[1],
                      backups=backup_of(tmp, f"mr-{tag}-desk", 1789000000)),
                 dict(name="a", listener=p(f"{tag}-a"), sent=p("r1-a"), sent_run="R1",
                      backups=backup_of(tmp, f"mr-{tag}-a", 1789000000, G)),
                 dict(name="b", listener=p(f"{tag}-b"), sent=p("r0"), sent_run="R0",
                      backups=backup_of(tmp, f"mr-{tag}-b", 1788000000, F()))]
        m = {"hub_state_from": "desktop", "nodes": nodes}
        if newest:
            m["newest_sent"] = {"run": "R1", "listener": p("r1")}
        out = os.path.join(tmp, f"mr-{tag}-out")
        rep = sm.build(m, out)
        return rep, {r[0] for r in rows(os.path.join(out, "listener.db"), "SELECT subject_id FROM listener_flags")}

    rep, left = run("1", [F()])
    check(left == {"G"}, f"F, removed in the run b missed, stays removed; G stays: {left}")
    check(any(d["rule"] == "IMPL-UID-042" and d["key"] == ["recording", "F"] for d in rep.data["decisions"]),
          "and the removal says which node missed it")
    rep, left = run("2", [F("'edited'")])
    check(left == {"F", "G"}, f"an edit b made to F since its copy is kept: {left}")
    rep, left = run("3", [F()], newest=False)
    check(left == {"F", "G"} and "missing" in rep.data["newest_sent"],
          f"without the newest copy F comes back, and the report says it could not check: {left}")
    rep, left = run("4", [F(), G], hub_sent=("r0", "R0"))
    check("G" in left and "F" not in left,
          f"a hub whose copy is older than the newest run does not make G read as removed: {left}")


def test_a_phone_that_never_applied_its_offer(tmp):
    """[IMPL-UID-042], found 2026-11-15: R1 removed flag F, and offered phone p a
    copy without it. p's copy is recorded at the offer, but p never applied it:
    its upload, unchanged since before R1, still holds F, and F is not in its new
    copy. That read as an addition, and F came back. The newest run had merged
    this very upload, so it is passed over. A *new* upload holding F, against a
    copy without it, is a re-addition, and stays."""
    F = "INSERT INTO listener_flags VALUES ('recording','F','2026-10-01 00:00:00',NULL)"
    p = lambda n: os.path.join(tmp, f"ph-{n}.lis")
    db(p("r1"))                                      # R1's merge: F removed
    db(p("offer"))                                   # what p was offered in R1: no F

    def run(tag, upload_at):
        db(p(f"{tag}-desk"))
        db(p(f"{tag}-phone"), F)                     # p still holds F
        nodes = [dict(name="desktop", listener=p(f"{tag}-desk"), sent=p("r1"), sent_run="R1",
                      taken_at="2026-11-15T00:00:00Z", backups=backup_of(tmp, f"ph-{tag}-desk", 1789000000)),
                 dict(name="phone", listener=p(f"{tag}-phone"), sent=p("offer"), sent_run="R1",
                      taken_at=upload_at, backups=backup_of(tmp, f"ph-{tag}-phone", 1788000000, F))]
        m = {"hub_state_from": "desktop", "nodes": nodes,
             "newest_sent": {"run": "R1", "listener": p("r1"), "weighed": {"phone": "2026-10-29T18:26:24Z"}}}
        out = os.path.join(tmp, f"ph-{tag}-out")
        sm.build(m, out)
        return {r[0] for r in rows(os.path.join(out, "listener.db"), "SELECT subject_id FROM listener_flags")}

    left = run("same", "2026-10-29T18:26:24Z")
    check(left == set(), f"the same upload the newest run weighed does not bring F back: {left}")
    left = run("new", "2026-11-14T09:00:00Z")
    check(left == {"F"}, f"a new upload holding F against a copy without it is a re-addition, and stays: {left}")


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
    db(nl, "INSERT INTO listener_play_history VALUES (1,5000,'uid-a','m',100,200,'auto')")
    out = os.path.join(tmp, "k-out")
    rep = sm.build({"hub_state_from": "desktop", "hub_library": hub,
                    "nodes": [dict(name="desktop", listener=hl),
                              dict(name="node", listener=nl, catalogue=node, files=node)]}, out)
    mine = os.path.join(out, "nodes", "node")
    f = rows(os.path.join(mine, "library.db"), "SELECT audio_md5, path FROM files")
    check(f == [("NEW", "/srv/a.mp3")], f"the node's copy takes the new key, with its own path: {f}")
    check("node" not in rep.data["translations"], f"nothing of it read by number: {rep.data['translations']}")
    check(rows(os.path.join(mine, "listener.db"), "SELECT passage_uid FROM listener_play_history") == [("uid-a",)],
          "and its play keeps its passage, by uid [IMPL-UID-064]")


def test_rust_agrees():
    """[SPEC-NSH-050]: the tables an appliance sends over the signed transport
    are named in Rust (`MERGED`, `MACHINE_SCOPE` in lempi-core's mesh_sync.rs),
    and must be exactly the ones this merge shares and keeps per machine."""
    import re
    src = open(os.path.join(os.path.dirname(HERE), "player", "core", "src", "mesh_sync.rs"),
               encoding="utf-8").read()
    def const(name):
        body = re.search(rf"pub const {name}: \[&str; \d+\] = \[(.*?)\];", src, re.S).group(1)
        return sorted(re.findall(r'"([a-z_0-9]+)"', body))
    shared = sorted(t for t, s in sm.TABLES.items() if s["rule"] in (sm.LWW, sm.UNION))
    check(const("MERGED") == shared, f"Rust MERGED {const('MERGED')} must be the merge's shared {shared}")
    check(const("MACHINE_SCOPE") == sorted(sm.MACHINE_SCOPE["files"]),
          "Rust MACHINE_SCOPE must be the merge's machine-scope files columns")


def prefs(path, rows, extra=()):
    db(path, *[f"INSERT INTO listener_preferences VALUES ('{k}','{i}',{r},{r},0,'{t}')"
               for k, i, r, t in rows], *extra)


def run_items(tmp, tag, base, hub, a, b, verdicts=None, sent=("desktop", "a", "b"), extra=None):
    """Three nodes that were last sent `base`, now holding what they hold."""
    paths = {}
    for n, rws in (("base", base), ("desktop", hub), ("a", a), ("b", b)):
        paths[n] = os.path.join(tmp, f"{tag}-{n}.lis")
        prefs(paths[n], rws, (extra or {}).get(n, ()))
    vp = None
    if verdicts:
        vp = os.path.join(tmp, f"{tag}-verdicts.json")
        json.dump({i: {"verdict": v, "by": "item"} for i, v in verdicts.items()}, open(vp, "w"))
    out = os.path.join(tmp, f"{tag}-out")
    nodes = [dict(name=n, listener=paths[n], taken_at="2026-09-26T00:00:00Z", **(
        {"sent": paths["base"]} if n in sent else {}), **({"backups": paths[n] + ".bk"} if os.path.isdir(paths[n] + ".bk") else {}))
        for n in ("a", "b", "desktop")]
    return sm.build({"hub_state_from": "desktop", "verdicts": vp, "nodes": nodes}, out), out


def test_items(tmp):
    """[SPEC-STAR-110..114]: an item is a change the merge would discard."""
    X0, Y0 = ("artist", "X", 1, "2026-09-01 00:00:00"), ("artist", "Y", 1, "2026-09-01 00:00:00")
    base = [X0, Y0]
    rot = lambda out, who="X": rows(os.path.join(out, "listener.db"),
                                    f"SELECT rotation FROM listener_preferences WHERE subject_id='{who}'")[0][0]

    rep, out = run_items(tmp, "i1", base, base, base, base)
    check(rep.data["items"] == [] and rep.data["propagated"] == {},
          "every node holding what the hub holds: nothing to approve, nothing even carried")

    rep, out = run_items(tmp, "i2", base, base, [("artist", "X", 3, "2026-09-05 00:00:00"), Y0], base)
    check(rep.data["items"] == [] and rep.data["propagated"] == {"listener_preferences": 1} and rot(out) == 3,
          f"one node changed a row and the merge takes it: carried, not an item: {rep.data['items']}")

    a3, b5 = ("artist", "X", 3, "2026-09-05 00:00:00"), ("artist", "X", 5, "2026-09-07 00:00:00")
    rep, out = run_items(tmp, "i3", base, base, [a3, Y0], [b5, Y0])
    (it,) = rep.data["items"]
    check(it["kind"] == "conflict" and it["default"] == "b" and it["chosen"] == "b" and rot(out) == 5,
          f"two nodes changed one row differently: the most recent is the default: {it}")
    check([c["node"] for c in it["candidates"]] == ["a", "b"] and it["table"] == "listener_preferences",
          "and both changes are listed as candidates")
    ident = it["id"]
    rep2, out2 = run_items(tmp, "i3b", base, base, [a3, Y0], [b5, Y0])
    check(rep2.data["items"][0]["id"] == ident, "the same situation has the same id")
    rep3, out3 = run_items(tmp, "i3c", base, base, [a3, Y0], [b5, Y0], {ident: "chose:a"})
    check(rep3.data["items"][0]["chosen"] == "a" and rot(out3) == 3, "a verdict can take the other change")
    for n in ("a", "b"):
        got = rows(os.path.join(out3, "nodes", n, "listener.db"),
                   "SELECT rotation FROM listener_preferences WHERE subject_id='X'")
        check(got == [(3,)], f"and every node's own copy gets it, {n}'s too: {got}")
    rep4, out4 = run_items(tmp, "i3d", base, base, [a3, Y0], [b5, Y0], {ident: "approved"})
    check(rot(out4) == 5 and rep4.data["items"][0]["chosen"] == "b", "approved takes the default")
    rep5, out5 = run_items(tmp, "i3e", base, base, [a3, Y0], [b5, Y0], {ident: "chose:nobody"})
    check(rot(out5) == 5, "a verdict naming a node that is no candidate changes nothing")

    rep, out = run_items(tmp, "i4", base, base, [("artist", "X", 3, "2026-09-05 00:00:00"), Y0],
                         [("artist", "X", 3, "2026-09-07 00:00:00"), Y0])
    check(rep.data["items"] == [], "the same settings made at two times are one change, not a conflict")

    rep, out = run_items(tmp, "i5", base, [("artist", "X", 2, "2026-09-03 00:00:00"), Y0],
                         [("artist", "X", 3, "2026-09-04 00:00:00"), Y0], base)
    (it,) = rep.data["items"]
    check(it["default"] == "a" and {c["node"] for c in it["candidates"]} == {"a", "desktop"},
          f"the hub's own change counts too, and the later one is the default: {it}")

    rep, out = run_items(tmp, "i6", base, base, [("artist", "X", 3, "2026-08-30 00:00:00"), Y0], base)
    (it,) = rep.data["items"]
    check(it["default"] == "b" and rot(out) == 1 and {c["node"] for c in it["candidates"]} == {"a", "b"},
          f"a change with an older stamp loses to the row the hub holds (b's, by name), and says so: {it}")

    rep, out = run_items(tmp, "i7", base, base, base, [("artist", "X", 9, "2026-09-06 00:00:00"), Y0], sent=("a", "desktop"))
    check(rep.data["items"] == [] and rot(out) == 9,
          "a node with no last-sent copy is taken as in step with the hub: only its difference counts")

    rep, out = run_items(tmp, "i8", base, base, [("artist", "X", 3, "2026-09-05 00:00:00"), Y0],
                         [("artist", "X", 4, "2026-09-05 00:00:00"), Y0])
    (it,) = rep.data["items"]
    check(it["kind"] == "tie" and it["default"] == "a", f"equal stamps: a tie, the first node by name: {it}")

    own = lambda off, t: (f"INSERT INTO listener_settings VALUES (1,1.0,{off},'{t}')",)
    rep, out = run_items(tmp, "i9", base, base, base, base, extra={
        "base": own(-240, "2026-09-01 00:00:00"), "desktop": own(-240, "2026-09-01 00:00:00"),
        "a": own(-60, "2026-09-09 00:00:00"), "b": own(-240, "2026-09-01 00:00:00")})
    check(rep.data["items"] == [] and rep.data["propagated"] == {},
          "a node's own zone offset is never a change to review")
    settings = lambda out, who=None: rows(os.path.join(out, "listener.db") if who is None else
                                          os.path.join(out, "nodes", who, "listener.db"),
                                          "SELECT artist_time_scale, utc_offset_minutes FROM listener_settings")
    check(settings(out) == [(1.0, -240)] and settings(out, "a") == [(1.0, -60)] and settings(out, "b") == [(1.0, -240)],
          f"[SPEC-STAR-111] and each copy keeps its own node's: {settings(out)} {settings(out, 'a')} {settings(out, 'b')}")

    # [SPEC-STAR-111]: the hub sets a time scale; a, later, only rewrites its own offset.
    scale = lambda s, off, t: (f"INSERT INTO listener_settings VALUES (1,{s},{off},'{t}')",)
    rep, out = run_items(tmp, "i9b", base, base, base, base, extra={
        "base": scale(1.0, -300, "2026-09-01 00:00:00"), "desktop": scale(2.0, -300, "2026-09-05 00:00:00"),
        "a": scale(1.0, -240, "2026-09-09 00:00:00"), "b": scale(1.0, -300, "2026-09-01 00:00:00")})
    check(rep.data["items"] == [] and rep.data["propagated"] == {"listener_settings": 1},
          f"an offset rewritten later is no write of the household's: the hub's scale carried, no item: {rep.data['items']}")
    check(settings(out) == [(2.0, -300)] and settings(out, "a") == [(2.0, -240)] and settings(out, "b") == [(2.0, -300)],
          f"every node takes the hub's scale, each keeping its own offset: "
          f"{settings(out)} {settings(out, 'a')} {settings(out, 'b')}")
    said = [d for d in rep.data["decisions"] if d.get("rule") == "SPEC-STAR-111"]
    check(len(said) >= 1 and "only its stamp or its own utc_offset_minutes" in said[0]["what"], f"and the report says why: {said}")

    # The same, but a changed the scale too: a real write, newer, so an item, a the default.
    rep, out = run_items(tmp, "i9c", base, base, base, base, extra={
        "base": scale(1.0, -300, "2026-09-01 00:00:00"), "desktop": scale(2.0, -300, "2026-09-05 00:00:00"),
        "a": scale(3.0, -240, "2026-09-09 00:00:00"), "b": scale(1.0, -300, "2026-09-01 00:00:00")})
    (it,) = rep.data["items"]
    check(it["kind"] == "conflict" and it["default"] == "a" and settings(out) == [(3.0, -300)]
          and settings(out, "a") == [(3.0, -240)],
          f"two real writes are an item as before, the newer the default, each copy its own offset: {it}")

    # A removal against an edit is an item; a removal alone is carried.
    flag = lambda n, o: f"INSERT INTO listener_flags VALUES ('recording','{n}','2026-08-01 00:00:00',{o})"
    bk = os.path.join(tmp, "i10-a.lis.bk")
    os.makedirs(bk)
    db(os.path.join(bk, "listener-1789000000.db"), flag("F", "NULL"), flag("G", "NULL"))
    rep, out = run_items(tmp, "i10", [], [], [], [], extra={
        "base": (flag("F", "NULL"), flag("G", "NULL")), "desktop": (flag("F", "NULL"), flag("G", "NULL")),
        "a": (), "b": (flag("F", "'edited'"), flag("G", "NULL"))})
    (it,) = rep.data["items"]
    left = {r[0] for r in rows(os.path.join(out, "listener.db"), "SELECT subject_id FROM listener_flags")}
    check(it["kind"] == "delete-vs-edit" and it["default"] == "a" and it["key"] == ["recording", "F"],
          f"a flag a removed and b edited is an item, removal the default (not re-added): {it}")
    check(left == set(), f"both removals took effect by default, G with no question asked: {left}")
    bk = os.path.join(tmp, "i10b-a.lis.bk")
    os.makedirs(bk)
    db(os.path.join(bk, "listener-1789000000.db"), flag("F", "NULL"), flag("G", "NULL"))
    rep, out = run_items(tmp, "i10b", [], [], [], [], {it["id"]: "chose:b"}, extra={
        "base": (flag("F", "NULL"), flag("G", "NULL")), "desktop": (flag("F", "NULL"), flag("G", "NULL")),
        "a": (), "b": (flag("F", "'edited'"), flag("G", "NULL"))})
    left = {r[0] for r in rows(os.path.join(out, "listener.db"), "SELECT subject_id FROM listener_flags")}
    check(left == {"F"}, f"a verdict for the edit keeps the flag, and G is still removed: {left}")


def test_removal_within_a_backups_second(tmp):
    """[SPEC-STAR-050]: a backup's time is whole seconds, from its name. A row
    stamped inside that second is one the backup held, so its removal since is
    applied -- not read as re-added because its stamp has a fraction. Until
    2026-11-13 '...:00.400' read as newer than a backup of '...:00', and the
    deleted row came back."""
    epoch = 1789000000
    second = sm.norm_time(epoch)
    later = sm.norm_time(epoch + 1)
    check(not sm.edited_since_backup(second + ".400", second), "inside the backup's second: held by it")
    check(not sm.edited_since_backup(second, second), "the backup's own second")
    check(sm.edited_since_backup(later + ".000", second), "the next second: written since")
    check(sm.edited_since_backup(later.replace(" ", "T") + "Z", second), "in any form a stamp takes")

    d = os.path.join(tmp, "subsecond")
    bk = os.path.join(d, "bk")
    os.makedirs(bk)
    a, b = os.path.join(d, "a.db"), os.path.join(d, "b.db")
    row = f"INSERT INTO listener_flags VALUES ('recording','gone','{second}.400',NULL)"
    db(a, row)
    db(b)
    db(os.path.join(bk, f"listener-{epoch}.db"), row)
    manifest = dict(nodes=[dict(name="b", listener=b, backups=bk, taken_at="2026-09-26T00:00:00Z"),
                           dict(name="a", listener=a, taken_at="2026-09-26T00:00:00Z")],
                    hub_state_from="a")
    out = os.path.join(d, "out")
    sm.build(manifest, out)
    left = rows(os.path.join(out, "listener.db"), "SELECT subject_id FROM listener_flags")
    check(left == [], f"the removal b's backup evidences is applied: {left}")


def test_people_see_a_uids_first_eight(tmp):
    """[IMPL-UID-076]: a report shows a person a passage's uid by its first
    eight characters, never a number; the JSON beside it keeps the whole uid."""
    uid = "abcdef01" + "0" * 24
    check(sm.shown([uid, "2026-11-16T00:00:00Z"]) == ["abcdef01", "2026-11-16T00:00:00Z"], "a key")
    check(sm.shown({"passage_uid": uid, "n": 3}) == {"passage_uid": "abcdef01", "n": 3}, "a row")
    check(sm.shown("21159") == "21159" and sm.shown(uid.upper()) == uid.upper(), "nothing else is shortened")
    item = dict(id="x1", table="boundary_reviews", key=[uid, "2026-11-16T00:00:00Z"], column=None,
                kind="conflict", rule="lww", default="a", chosen="a",
                candidates=[dict(node="a", value={"passage_uid": uid, "start_ms": 1}, stamp=None),
                            dict(node="b", value={"passage_uid": uid, "start_ms": 2}, stamp=None)])
    text = "\n".join(sm.items_section({"items": [item]}))
    check("abcdef01" in text and uid not in text, f"the report's item names the passage by eight: {text}")
    rep = sm.Report()
    rep.data["items"].append(item)
    out = os.path.join(tmp, "short-uids")
    os.makedirs(out)
    sm.write_report(rep, out)
    check(uid in open(os.path.join(out, "items.json"), encoding="utf-8").read(), "items.json keeps the uid whole")
    check(uid not in open(os.path.join(out, "report.md"), encoding="utf-8").read(), "report.md does not")


def main() -> int:
    tmp = tempfile.mkdtemp()
    test_rust_agrees()
    test_removal_within_a_backups_second(tmp)
    test_items(tmp)
    test_people_see_a_uids_first_eight(tmp)
    test_rekeyed(tmp)
    test_catalogue(tmp)
    test_held_out(tmp)
    test_receivers(tmp)
    test_translation(tmp)
    test_a_snapshot_by_number_is_refused(tmp)
    test_passage_lyrics(tmp)
    test_lyrics_uids_come_from_the_hub(tmp)
    test_a_hub_that_set_no_uid_keeps_a_nodes_save_whole(tmp)
    test_a_review_survives_two_rounds(tmp)
    test_a_removal_is_read_in_the_hubs_numbering(tmp)
    test_a_withdrawn_review_stays_withdrawn(tmp)
    test_a_history_by_number_is_no_evidence(tmp)
    test_a_node_that_missed_the_run(tmp)
    test_a_phone_that_never_applied_its_offer(tmp)
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
    check(rows(L, "SELECT passage_uid FROM player_state") == [('11',)],
          "node state comes from the hub's own node, not merged")
    B = os.path.join(out, "nodes", "b", "listener.db")
    hb = rows(B, "SELECT play_id, played_at, heard_ms FROM listener_play_history ORDER BY play_id")
    check(hb == [(3, 1000, 150), (4, 2000, 50)], f"b's copy holds b's own plays, unchanged: {hb}")
    check(rows(B, "SELECT passage_uid FROM player_state") == [('22',)], "and b's own player state")
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
