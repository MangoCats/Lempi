#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `catalogue_integrity.py` [SPEC070].

The shapes are the real catalogue's, cut to what a link touches; the 2026-10-31
case is in it: a release's tracklist naming recordings the catalogue never held.

    python tools/test_catalogue_integrity.py
"""
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalogue_integrity as ci  # noqa: E402

FAILED = []

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, mtime REAL NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, boundary_src TEXT NOT NULL);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT NOT NULL REFERENCES recordings(mbid) ON DELETE CASCADE,
    artist_mbid TEXT NOT NULL REFERENCES artists(mbid), weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
    PRIMARY KEY (mbid, artist_mbid)) WITHOUT ROWID;
CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT, source TEXT NOT NULL,
    track_count INTEGER, artist_credit TEXT);
CREATE TABLE release_recordings (release_mbid TEXT NOT NULL REFERENCES releases(mbid) ON DELETE CASCADE,
    mbid TEXT NOT NULL REFERENCES recordings(mbid) ON DELETE CASCADE, position INTEGER, source TEXT NOT NULL,
    track_length_ms INTEGER, chosen INTEGER DEFAULT 0, disc INTEGER, PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL REFERENCES passages(passage_id) ON DELETE CASCADE,
    mbid TEXT NOT NULL REFERENCES recordings(mbid), weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
    PRIMARY KEY (passage_id, mbid)) WITHOUT ROWID;
CREATE TABLE file_releases (audio_md5 TEXT PRIMARY KEY, release_mbid TEXT NOT NULL, source TEXT NOT NULL);
CREATE TABLE flavor (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, characteristic TEXT NOT NULL,
    class TEXT NOT NULL, value REAL NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id, characteristic, class)) WITHOUT ROWID;
"""

HUB = """
INSERT INTO files VALUES (1, 'md5-a', 'C:/Music/Elton John/Caribou/a.mp3', 100, 1, 't', 't');
INSERT INTO passages VALUES (1, 1, 'album', 0, 1000, 'computed');
INSERT INTO recordings VALUES ('rec-sun', 'Don''t Let the Sun Go Down on Me', 335400, 'musicbrainz');
INSERT INTO passage_recordings VALUES (1, 'rec-sun', 1.0, 'musicbrainz');
INSERT INTO releases VALUES ('rel-caribou', 'Caribou', '1974-06-24', 'musicbrainz', 10, 'Elton John');
INSERT INTO releases VALUES ('rel-org', 'Org', '2009', 'musicbrainz', 2, NULL);
INSERT INTO release_recordings VALUES ('rel-caribou', 'rec-sun', 9, 'musicbrainz', 335400, 1, 1);
INSERT INTO release_recordings VALUES ('rel-caribou', 'rec-ghost', 9, 'musicbrainz', 335400, 0, NULL);
INSERT INTO release_recordings VALUES ('rel-org', 'rec-o1', 1, 'musicbrainz', 196600, 0, NULL);
INSERT INTO release_recordings VALUES ('rel-org', 'rec-o2', 2, 'musicbrainz', 489933, 0, NULL);
INSERT INTO file_releases VALUES ('md5-a', 'rel-caribou', 'cd:import');
"""


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def make(path, *extra):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA + HUB + "".join(extra))
    c.commit()
    return c


def table(name, columns, rows):
    return {"name": name, "columns": columns, "rows": rows}


def answer_holding_ghost(files=False):
    """A node's `rows` answer, as `mesh_sync::catalogue_rows` gives it: it holds
    rec-ghost, credited to an artist, with flavor -- and, by recording, its file."""
    tables = [table("recordings", ["mbid", "title", "length_ms", "source"], [["rec-ghost", "Ticking", 335400, "musicbrainz"]]),
              table("recording_artists", ["mbid", "artist_mbid", "weight", "source"],
                    [["rec-ghost", "art-elton", 1.0, "musicbrainz"], ["rec-ghost", "art-nobody", 1.0, "musicbrainz"]]),
              table("artists", ["mbid", "name", "source"], [["art-elton", "Elton John", "musicbrainz"]]),
              table("flavor", ["subject_kind", "subject_id", "characteristic", "class", "value", "source"],
                    [["recording", "rec-ghost", "mood", "sad", 0.7, "local:essentia"]]),
              # The hub's own row of the same key, in the node's version: never replaced.
              table("recordings", ["mbid", "title", "length_ms", "source"], [["rec-sun", "Changed on the node", 1, "x"]])]
    if files:
        tables += [table("files", ["file_id", "audio_md5", "path", "size_bytes", "mtime", "first_seen", "last_seen", "sha256"],
                         [[7, "md5-g", "/srv/library/audio/Elton John/Caribou/09 Ticking.mp3", 5000, 2, "n", "n", {"sha256": "ab"}]]),
                   table("passages", ["passage_id", "file_id", "kind", "start_ms", "end_ms", "boundary_src"],
                         [[70, 7, "album", 0, 335400, "computed"], [71, 7, "radio", 0, 335400, "computed"]]),
                   table("passage_recordings", ["passage_id", "mbid", "weight", "source"],
                         [[70, "rec-ghost", 1.0, "musicbrainz"], [71, "rec-ghost", 1.0, "musicbrainz"]]),
                   table("file_releases", ["audio_md5", "release_mbid", "source"], [["md5-g", "rel-caribou", "cd:import"]])]
    return {"answer": {"tables": tables, "by_recording": files}, "by_recording": files, "fetchable": True,
            "root": "/srv/library/audio"}


def test_find(tmp):
    print("find(): a finding per owner, its rows, and what it is in words")
    c = make(os.path.join(tmp, "find.db"))
    fs = ci.find(c)
    check(len(fs) == 2, f"two releases, two findings: {[f['owner'] for f in fs]}")
    by = {f["owner"]["release_mbid"]: f for f in fs}
    car, org = by.get("rel-caribou"), by.get("rel-org")
    check(car and car["missing"] == ["rec-ghost"] and len(car["rows"]) == 1 and car["removable"],
          f"Caribou: the stray track 9 alone, removable: {car}")
    check(org and org["missing"] == ["rec-o1", "rec-o2"], f"Org: both its tracks, one finding: {org}")
    a = car["about"]
    check(a["title"] == "Caribou" and a["credit"] == "Elton John" and a["listed"] == 2 and a["left_after"] == 1
          and a["files_from_it"] == 1 and a["tracks"][0]["length"] == "5:35",
          f"what a person judges it by: {a}")
    check("nothing of its tracklist would be left" in ci.words(org), ci.words(org))
    check(ci.want_of(fs) == {"recordings": ["rec-ghost", "rec-o1", "rec-o2"]}, f"{ci.want_of(fs)}")
    again = {f["owner"]["release_mbid"]: f["id"] for f in ci.find(c)}
    check(again == {k: f["id"] for k, f in by.items()}, "the same situation keeps its id")
    c.execute("DELETE FROM release_recordings WHERE mbid = 'rec-o2'")
    check(next(f["id"] for f in ci.find(c) if f["owner"]["release_mbid"] == "rel-org") != org["id"],
          "and a changed one gets a new one")
    c.execute("INSERT INTO passage_recordings VALUES (1, 'rec-lost', 1.0, 'musicbrainz')")
    pr = [f for f in ci.find(c) if f["table"] == "passage_recordings"]
    check(len(pr) == 1 and not pr[0]["removable"], f"a passage's credit to nothing is found, and is not removable: {pr}")
    c.close()


def test_effect():
    print("effect(): what a decision does in this run [SPEC-LNK-042]")
    f = {"id": "f1", "table": "release_recordings", "removable": True, "parent": "recordings"}
    offer = {"node": "bose", "keys": ["k"], "files": [], "files_known": False, "fetchable": True}
    e = lambda ds, offers=(): ci.effect(f, ds, list(offers))["effect"]
    check(e({}) == "undecided", "nothing decided")
    check(e({"f1": {"decision": "leave"}}) == "leave", "left")
    check(e({"f1": {"decision": "remove", "offers_seen": []}}) == "remove", "removed, no node holding it")
    r = ci.effect(f, {"f1": {"decision": "remove", "offers_seen": []}}, [offer])
    check(r["effect"] == "held" and "bose" in r["why"], f"a node found holding it since holds the removal back: {r}")
    check(e({"f1": {"decision": "remove", "offers_seen": ["bose"]}}, [offer]) == "remove",
          "but not one the person saw when they decided")
    check(e({"f1": {"decision": "repair:bose"}}, [offer]) == "repair", "repaired from a node that holds it")
    check(e({"f1": {"decision": "repair:bose"}}) == "held", "held when that node is not in this run")
    check(e({"f1": {"decision": "repair-files:bose"}}, [offer]) == "held", "files from a build that cannot list them: held")
    check(e({"f1": {"decision": "repair-files:bose"}}, [dict(offer, files_known=True)]) == "repair-files", "and from one that can")
    nr = dict(f, removable=False)
    check(ci.effect(nr, {"f1": {"decision": "remove", "offers_seen": []}}, [])["effect"] == "held",
          "a row that is not a link only is never removed")


def run_correct(tmp, name, decisions, answers=None, extra=""):
    db = os.path.join(tmp, f"{name}.db")
    make(db, extra).close()
    dpath = os.path.join(tmp, f"{name}.decisions.json")
    ci.save_decisions(dpath, decisions)
    apaths = {}
    for n, a in (answers or {}).items():
        apaths[n] = os.path.join(tmp, f"{name}.{n}.json")
        with open(apaths[n], "w", encoding="utf-8") as fh:
            json.dump(a, fh)
    c = sqlite3.connect(db)
    ids = {f["owner"]["release_mbid"]: f["id"] for f in ci.find(c)}
    c.close()
    return db, ids, dpath, apaths


def test_remove_and_leave(tmp):
    print("correct(): a removal takes the dangling rows and nothing else; leave writes nothing")
    db, ids, dpath, _ = run_correct(tmp, "remove", {})
    ci.save_decisions(dpath, {ids["rel-org"]: {"decision": "remove", "offers_seen": []},
                              ids["rel-caribou"]: {"decision": "leave"}})
    res = ci.correct(db, {"decisions": dpath})
    by = {f["owner"]["release_mbid"]: f for f in res["findings"]}
    check(by["rel-org"]["effect"] == "remove" and by["rel-org"]["applied"] == {"release_recordings": -2},
          f"Org's two rows removed: {by['rel-org'].get('applied')}")
    check(by["rel-caribou"]["effect"] == "leave", "Caribou left")
    c = sqlite3.connect(db)
    check(c.execute("SELECT COUNT(*) FROM release_recordings WHERE release_mbid = 'rel-org'").fetchone()[0] == 0, "gone")
    check(c.execute("SELECT COUNT(*) FROM release_recordings WHERE release_mbid = 'rel-caribou'").fetchone()[0] == 2,
          "the left one untouched")
    check(c.execute("SELECT COUNT(*) FROM releases WHERE mbid = 'rel-org'").fetchone()[0] == 1,
          "the release itself stays [SPEC-LNK-900]")
    c.close()
    check(res["remaining"] == 1, f"one finding remains: {res['remaining']}")
    text = "\n".join(ci.report_lines(res))
    check("1 leave, 1 remove" in text and "Org" in text, text)


def test_repair(tmp):
    print("correct(): a repair adds what the node holds, never replaces, and keeps every child's parent")
    ans = {"bose": answer_holding_ghost()}
    db, ids, dpath, apaths = run_correct(tmp, "repair", {}, ans)
    offers = ci.offers_of(ci.find(sqlite3.connect(db)), ans)
    check([o["node"] for o in offers.get(ids["rel-caribou"], [])] == ["bose"] and ids["rel-org"] not in offers,
          f"bose offers for Caribou alone: {offers}")
    ci.save_decisions(dpath, {ids["rel-caribou"]: {"decision": "repair:bose"}})
    res = ci.correct(db, {"decisions": dpath, "answers": apaths})
    car = next(f for f in res["findings"] if f["owner"]["release_mbid"] == "rel-caribou")
    check(car["effect"] == "repair" and car["applied"] == {"recordings": 1, "artists": 1, "recording_artists": 1, "flavor": 1},
          f"the recording, its one artist the node sent, that credit, its flavor: {car.get('applied')}")
    c = sqlite3.connect(db)
    check(c.execute("SELECT title FROM recordings WHERE mbid = 'rec-sun'").fetchone()[0] == "Don't Let the Sun Go Down on Me",
          "the hub's own row is never replaced by the node's")
    check(c.execute("SELECT COUNT(*) FROM recording_artists WHERE artist_mbid = 'art-nobody'").fetchone()[0] == 0,
          "a credit whose artist neither holds is not added")
    check(c.execute("PRAGMA foreign_key_check").fetchall() == [] or
          all(r[0] == "release_recordings" for r in c.execute("PRAGMA foreign_key_check")),
          "nothing new points at nothing: only Org's rows still do")
    c.close()
    check(res["remaining"] == 1, f"Org still dangles, and is still a finding: {res['remaining']}")


def test_repair_with_files(tmp):
    print("correct(): with its files, the node's file catalogued under new ids at the hub's place for it")
    ans = {"bose": answer_holding_ghost(files=True)}
    # A second folder, so the hub's music root is C:/Music, as a library's is.
    db, ids, dpath, apaths = run_correct(tmp, "files", {}, ans,
                                         "INSERT INTO files VALUES (5, 'md5-b', 'C:/Music/Foghat/b.mp3', 1, 1, 't', 't');")
    ci.save_decisions(dpath, {ids["rel-caribou"]: {"decision": "repair-files:bose"}})
    res = ci.correct(db, {"decisions": dpath, "answers": apaths})
    car = next(f for f in res["findings"] if f["owner"]["release_mbid"] == "rel-caribou")
    check(car["effect"] == "repair-files", f"{car['effect']}: {car.get('why')}")
    o = car["offers"][0]
    check(o["files_known"] and [x["audio_md5"] for x in o["files"]] == ["md5-g"], f"the offer names the file: {o}")
    want = os.path.join("C:/Music", "Elton John", "Caribou", "09 Ticking.mp3")
    check(len(res["fetch"]) == 1 and os.path.normpath(res["fetch"][0]["dest"]) == os.path.normpath(want)
          and res["fetch"][0]["src"].startswith("/srv/library/audio/") and res["fetch"][0]["sha256"] is None,
          f"copied to the same place under the hub's root; a hash sent as a hash is not one to check: {res['fetch']}")
    c = sqlite3.connect(db)
    f = c.execute("SELECT file_id, path FROM files WHERE audio_md5 = 'md5-g'").fetchone()
    check(f and f[0] == 6, f"a new id, the hub's next: {f}")
    ps = c.execute("SELECT passage_id, kind FROM passages WHERE file_id = ? ORDER BY passage_id", (f[0],)).fetchall()
    check(ps == [(2, "album"), (3, "radio")], f"its passages, under new ids: {ps}")
    pr = c.execute("SELECT passage_id, mbid FROM passage_recordings WHERE mbid = 'rec-ghost' ORDER BY 1").fetchall()
    check(pr == [(2, "rec-ghost"), (3, "rec-ghost")], f"credited as on the node: {pr}")
    check(c.execute("SELECT release_mbid FROM file_releases WHERE audio_md5 = 'md5-g'").fetchone() == ("rel-caribou",),
          "and taken from the release it is on")
    c.close()
    check(ci.place("/srv/x/a.mp3", "/srv/library", "C:/M") is None, "a path outside the node's root has no place")


def test_decide(tmp):
    print("decide(): checked against the finding, kept beside the runs")
    db, ids, dpath, _ = run_correct(tmp, "decide", {})
    c = sqlite3.connect(db)
    f = next(f for f in ci.find(c) if f["owner"]["release_mbid"] == "rel-org")
    c.close()
    for bad, why in (("delete", "not a decision"), ("repair:bose", "bose offers nothing")):
        try:
            ci.decide(dpath, f, bad, [])
            check(False, f"{bad!r} must be refused: {why}")
        except SystemExit:
            pass
    d = ci.decide(dpath, f, "remove", [{"node": "x", "keys": [], "files": [], "files_known": False, "fetchable": False}])
    check(d["offers_seen"] == ["x"] and "Org" in d["what"], f"what the person had in front of them is kept: {d}")
    check(ci.load_decisions(dpath)[f["id"]]["decision"] == "remove", "and it is on disk")
    check(ci.mark_committed(dpath, [dict(f, effect="remove")], "20261031T1600Z") == 1
          and ci.load_decisions(dpath)[f["id"]]["committed"] == "20261031T1600Z", "the commit that carried it is kept")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_find(tmp)
        test_effect()
        test_remove_and_leave(tmp)
        test_repair(tmp)
        test_repair_with_files(tmp)
        test_decide(tmp)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("catalogue_integrity: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
