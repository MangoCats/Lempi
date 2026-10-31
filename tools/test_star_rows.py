#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for star sync's baseline from a node's own rows `[SPEC-STAR-945]`.

The case is the one found 2026-10-14: a second disc a bundle brought to a node,
credited wrongly, then re-linked on the hub. Against what the hub last sent --
which never held the file -- the patch could only add the right credits and
restamp nothing the node would accept. Against the node's own rows it removes
the wrong credits and moves the release's stamp from what the node holds.

    python tools/test_star_rows.py
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bundle_ledger      # noqa: E402
import star_patch as sp   # noqa: E402
import star_sync as ss    # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE, path TEXT, format TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER, end_ms INTEGER);
CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT, PRIMARY KEY (passage_id, mbid));
CREATE TABLE file_releases (audio_md5 TEXT PRIMARY KEY, release_mbid TEXT, source TEXT, decided_at TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT, PRIMARY KEY (mbid, artist_mbid));
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT);
CREATE TABLE release_recordings (release_mbid TEXT, mbid TEXT, position INTEGER, chosen INTEGER, disc INTEGER,
    PRIMARY KEY (release_mbid, mbid));
CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT);
CREATE TABLE cover_art (release_mbid TEXT PRIMARY KEY, front BLOB);
"""

DISC1 = """
INSERT INTO files VALUES (1, 'disc1', '/m/d1.mp3', 'mp3');
INSERT INTO passages VALUES (11, 1, 'radio', 0, 1000);
INSERT INTO passage_recordings VALUES (11, 'rec-1', 1.0, 'musicbrainz');
INSERT INTO recordings VALUES ('rec-1', 'I Had a Dream'), ('rec-2', 'Going Up the Country');
INSERT INTO releases VALUES ('rel-w', 'Woodstock');
INSERT INTO release_recordings VALUES ('rel-w', 'rec-1', 1, 1, 1), ('rel-w', 'rec-2', 2, 1, 1);
"""


def db(path, *sql):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA + "".join(sql))
    c.commit()
    c.close()
    return path


def answer_of(node_db, want):
    """What `mesh_sync::catalogue_rows` answers, for a node catalogue -- its shape, a
    blob as its hash -- so the hub's half is tested against the same contract."""
    c = sqlite3.connect(node_db)
    files = set(want["files"])
    rec, rel, art = set(want.get("recordings", [])), set(want.get("releases", [])), set(want.get("artists", []))
    fids = ss._in(c, "SELECT file_id FROM files WHERE audio_md5 IN ({})", files)
    pids = ss._in(c, "SELECT passage_id FROM passages WHERE file_id IN ({})", fids)
    rec |= set(ss._in(c, "SELECT mbid FROM passage_recordings WHERE passage_id IN ({})", pids))
    rel |= set(ss._in(c, "SELECT release_mbid FROM file_releases WHERE audio_md5 IN ({})", files))
    rel |= set(ss._in(c, "SELECT release_mbid FROM release_recordings WHERE chosen = 1 AND mbid IN ({})", rec))
    art |= set(ss._in(c, "SELECT artist_mbid FROM recording_artists WHERE mbid IN ({})", rec))
    by = {"file": files, "file_id": fids, "passage": pids, "recording": rec, "release": rel, "artist": art}
    tables = []
    for t, kind, col in ss.ROW_SCOPE:
        if not ss._has(c, t):
            continue
        cols = sp.columns(c, t)
        vals = list(by[kind])
        rows = c.execute(f"SELECT {', '.join(cols)} FROM {t} WHERE {col} IN ({','.join('?' * len(vals)) or 'NULL'})",
                         vals).fetchall()
        tables.append({"name": t, "columns": cols, "rows": [
            [{"sha256": hashlib.sha256(v).hexdigest()} if isinstance(v, bytes) else v for v in r] for r in rows]})
    c.close()
    return {"scope": {"files": sorted(files), "recordings": sorted(rec), "releases": sorted(rel),
                      "artists": sorted(art)}, "tables": json.loads(json.dumps(tables))}


def rows_of(patch, name):
    # Every entry: a natural table's removals and its writes are two since
    # 2026-10-31 [SPEC-NKP-060], and the first alone holds only the removals.
    return [r for t in patch["tables"] if t["name"] == name for r in t["rows"]]


def test_the_second_disc():
    print("a node's own rows: the wrong credits removed, the release moved from what the node holds")
    d = tempfile.mkdtemp()
    sent = db(os.path.join(d, "sent.db"), DISC1)
    # The hub now: disc 2 re-linked to its own songs, its release restamped, a cover.
    hub = db(os.path.join(d, "hub.db"), DISC1, """
        INSERT INTO files VALUES (5776, 'disc2', '/m/d2.mp3', 'mp3');
        INSERT INTO passages VALUES (17509, 5776, 'radio', 0, 826067), (17510, 5776, 'album', 0, 826067);
        INSERT INTO passage_recordings VALUES (17509, 'rec-soul', 1.0, 'musicbrainz'), (17510, 'rec-soul', 1.0, 'musicbrainz');
        INSERT INTO recordings VALUES ('rec-soul', 'Soul Sacrifice');
        INSERT INTO release_recordings VALUES ('rel-w', 'rec-soul', 1, 1, 2);
        INSERT INTO file_releases VALUES ('disc2', 'rel-w', 'cd:import', '2026-10-14T09:41:48');
        INSERT INTO cover_art VALUES ('rel-w', x'0102');""")
    # The node: disc 2 as the bundle left it -- its own ids, the wrong credit, the old stamp.
    node = db(os.path.join(d, "node.db"), DISC1, """
        INSERT INTO files VALUES (40, 'disc2', '/srv/d2.mp3', 'mp3');
        INSERT INTO passages VALUES (400, 40, 'radio', 0, 826067), (401, 40, 'album', 0, 826067);
        INSERT INTO passage_recordings VALUES (400, 'rec-1', 1.0, 'musicbrainz'), (401, 'rec-1', 1.0, 'musicbrainz');
        INSERT INTO file_releases VALUES ('disc2', 'rel-w', 'cd:import', '2026-10-06T22:07:39');
        INSERT INTO cover_art VALUES ('rel-w', x'0102');""")
    summary = db(os.path.join(d, "summary.db"))
    s = sqlite3.connect(summary)
    s.execute("ATTACH ? AS n", (node,))
    s.execute("INSERT INTO files SELECT * FROM n.files")
    s.execute("INSERT INTO passages SELECT * FROM n.passages")
    s.commit()
    s.close()

    files = ss.files_to_ask(summary, sent, touched=set())
    check(files == ["disc2"], f"the file the node holds and the sent copy lacks is asked about: {files}")
    want = ss.rows_wanted(files, sent, hub)
    check(want["recordings"] == ["rec-soul"], f"the hub's credit is in scope: {want}")
    answer = answer_of(node, want)
    check(answer["scope"]["recordings"] == ["rec-1", "rec-soul"], f"the node adds its own: {answer['scope']}")

    base = os.path.join(d, "baseline.db")
    rep = ss.baseline_from_rows(sent, hub, answer, base)
    check(rep["blobs_unmatched"] == 0, f"the cover matched the hub's by its hash: {rep}")
    b = sqlite3.connect(base)
    check(b.execute("SELECT file_id FROM files WHERE audio_md5 = 'disc2'").fetchone() == (5776,),
          "the node's file is named by the hub's id")
    check(sorted(b.execute("SELECT passage_id, mbid FROM passage_recordings WHERE passage_id IN (17509, 17510)")) ==
          [(17509, "rec-1"), (17510, "rec-1")], "and its credits, the wrong ones, by the hub's passage ids")
    check(b.execute("SELECT front FROM cover_art").fetchone() == (b"\x01\x02",), "the cover is the bytes, not the hash")
    b.close()

    patch = sp.make_values(base, hub, exclude=ss.SIGNED_EXCLUDE, natural=True, omit=ss.SIGNED_OMIT)
    pr = rows_of(patch, "passage_recordings")
    gone = [r for r in pr if r["now"] is None]
    came = [r for r in pr if r["was"] is None]
    check(len(gone) == 2 and all(r["key"][1] == "rec-1" for r in gone), f"the two wrong credits are removed: {gone}")
    check(len(came) == 2 and all(r["key"][1] == "rec-soul" for r in came), f"and the right ones added: {came}")
    fr = rows_of(patch, "file_releases")
    check(len(fr) == 1 and fr[0]["was"][3] == "2026-10-06T22:07:39" and fr[0]["now"][3] == "2026-10-14T09:41:48",
          f"the release moves from what the node holds: {fr}")
    check(not rows_of(patch, "files") and not rows_of(patch, "cover_art"), "and what is equal is not sent")

    # The control: against the copy last sent, as before 2026-10-14.
    old = sp.make_values(sent, hub, exclude=ss.SIGNED_EXCLUDE, natural=True, omit=ss.SIGNED_OMIT)
    check(not [r for r in rows_of(old, "passage_recordings") if r["now"] is None],
          "against the last-sent copy no credit is removed -- the bug this replaces")
    check(rows_of(old, "file_releases")[0]["was"] is None, "and the release arrives as an insert the node refuses")


def test_a_boundary_both_made():
    print("a boundary the node made as the hub did: asked about, and the node is not 'changed'")
    d = tempfile.mkdtemp()
    sent = db(os.path.join(d, "sent.db"), DISC1)
    # The hub and the node both moved the passage's end, each applying the same review,
    # and each numbered the passage it made itself -- as Givin the Dog a Bone was.
    remade = ("DELETE FROM passages; INSERT INTO passages VALUES ({0}, 1, 'radio', 0, 900); "
              "UPDATE passage_recordings SET passage_id = {0};")
    hub = db(os.path.join(d, "hub.db"), DISC1, remade.format(30))
    node = db(os.path.join(d, "node.db"), DISC1, remade.format(12))
    summary = db(os.path.join(d, "summary.db"))
    s = sqlite3.connect(summary)
    s.execute("ATTACH ? AS n", (node,))
    s.execute("INSERT INTO files SELECT * FROM n.files")
    s.execute("INSERT INTO passages SELECT * FROM n.passages")
    s.commit()
    s.close()

    alone = ss.catalogue_state(sent, summary, hub)
    check(alone["state"] == "changed", f"against the copy alone it reads changed -- the bug this replaces: {alone}")
    files = ss.files_to_ask(summary, sent, touched=set())
    check(files == ["disc1"], f"the file whose passage moved is asked about, though no bundle touched it: {files}")
    base = os.path.join(d, "baseline.db")
    ss.baseline_from_rows(sent, hub, answer_of(node, ss.rows_wanted(files, sent, hub)), base)
    st = ss.catalogue_state(base, summary, hub)
    check(st["state"] in ("as-sent", "holds-hub-rows", "other-ids"), f"against the baseline it is in step: {st}")
    patch = sp.make_values(base, hub, exclude=ss.SIGNED_EXCLUDE, natural=True, omit=ss.SIGNED_OMIT)
    check(not rows_of(patch, "passages"), f"and the passage the node already holds is not sent: {rows_of(patch, 'passages')}")


def test_run_names():
    print("run_names: only a run's stamp is a run")
    d = tempfile.mkdtemp()
    for n in ("20261029T1653Z", "20261029T1745Z", "adopted", "bundled.json"):
        if n.endswith(".json"):
            open(os.path.join(d, n), "w").close()
        else:
            os.makedirs(os.path.join(d, n))
    check(ss.run_names(d) == ["20261029T1653Z", "20261029T1745Z"], f"adopted/ is not the newest run: {ss.run_names(d)}")


def test_the_ledger():
    print("bundle_ledger: an applied send, read back by node and time")
    d = tempfile.mkdtemp()
    lib = os.path.join(d, "library.db")
    bundle = os.path.join(d, "bundle")
    os.makedirs(bundle)
    with open(os.path.join(bundle, "payload.json"), "w", encoding="utf-8") as fh:
        json.dump({"encodings": [{"audio_md5": "a"}, {"audio_md5": "b"}]}, fh)
    check(bundle_ledger.record(lib, "bose", bundle, at="2026-10-14T10:00:00+00:00") == 2, "two files recorded")
    bundle_ledger.record(lib, "lp3-wifi", bundle, at="2026-10-14T10:00:00+00:00")
    check(bundle_ledger.touched_since(lib, "bose", "2026-10-14T09:00:00+00:00") == {"a", "b"}, "after the last send")
    check(bundle_ledger.touched_since(lib, "bose", "2026-10-14T11:00:00+00:00") == set(), "not before it")
    check(bundle_ledger.touched_since(lib, "smartboardpc", None) == set(), "and only that node's")
    check(os.path.exists(os.path.join(d, "sync", "bundled.json")), "kept beside the library, in sync/")


def main() -> int:
    test_the_second_disc()
    test_a_boundary_both_made()
    test_run_names()
    test_the_ledger()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("star rows: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
