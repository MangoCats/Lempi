#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `payload.py` `[SPEC014]`.

Runs `build()`/`compatible()` in-process against a minimal SPEC008 schema --
`payload.py` has no subcommand shape worth going through a subprocess for,
unlike the tools that write to a database.

    python tools/test_payload.py
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import payload as pl  # noqa: E402

# The post-`[SPEC-SUI-226]` shape, SPEC008-accurate.
SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
    format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id),
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    boundary_src TEXT NOT NULL,
    fade_in_ms INTEGER NOT NULL DEFAULT 20, fade_out_ms INTEGER NOT NULL DEFAULT 20,
    fade_in_curve TEXT NOT NULL DEFAULT 'exponential',
    fade_out_curve TEXT NOT NULL DEFAULT 'exponential',
    CHECK (end_ms > start_ms));
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL,
    sort_name TEXT, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT NOT NULL REFERENCES recordings(mbid),
    artist_mbid TEXT NOT NULL REFERENCES artists(mbid),
    weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
    PRIMARY KEY (mbid, artist_mbid)) WITHOUT ROWID;
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL REFERENCES passages(passage_id),
    mbid TEXT NOT NULL REFERENCES recordings(mbid), weight REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL, PRIMARY KEY (passage_id, mbid)) WITHOUT ROWID;
CREATE TABLE file_tags (file_id INTEGER, title TEXT, artist TEXT, album TEXT,
    track_no INTEGER, disc_no INTEGER, has_art INTEGER, scanned_at TEXT);
CREATE TABLE flavor (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    characteristic TEXT NOT NULL, class TEXT NOT NULL, value REAL NOT NULL,
    source TEXT NOT NULL, accuracy REAL,
    PRIMARY KEY (subject_kind, subject_id, characteristic, class));
"""

# The pre-`[SPEC-SUI-226]` shape -- exactly what `SCHEMA` was before this
# feature, kept as its own constant per `test_apply_boundary_reviews.py`'s own
# reasoning: derived-by-string-surgery is one whitespace change from silently
# testing nothing.
PRE_FADE_SCHEMA = SCHEMA.replace(
    "boundary_src TEXT NOT NULL,\n"
    "    fade_in_ms INTEGER NOT NULL DEFAULT 20, fade_out_ms INTEGER NOT NULL DEFAULT 20,\n"
    "    fade_in_curve TEXT NOT NULL DEFAULT 'exponential',\n"
    "    fade_out_curve TEXT NOT NULL DEFAULT 'exponential',\n"
    "    CHECK (end_ms > start_ms));",
    "boundary_src TEXT NOT NULL, CHECK (end_ms > start_ms));",
)

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def make_db(path: str, schema: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO files VALUES (1,'md5','a.mp3',1,1.0,'mp3',10000,'t','t')")
    return conn


def test_fade_travels_when_the_schema_has_it():
    """`[SPEC-SUI-226]` -- the four fade columns land in the payload exactly
    as `lead_in_ms`/`gain_db` already do, once the source database has them.
    """
    conn = make_db(":memory:", SCHEMA)
    conn.execute(
        "INSERT INTO passages VALUES "
        "(1,1,'radio',0,10000,5,900,-1.0,'manual',15,1500,'linear','cosine')")
    payload = pl.build(conn, ["md5"])
    p = payload["encodings"][0]["passages"][0]
    check(p.get("fade_in_ms") == 15, f"fade_in_ms: {p.get('fade_in_ms')!r}")
    check(p.get("fade_out_ms") == 1500, f"fade_out_ms: {p.get('fade_out_ms')!r}")
    check(p.get("fade_in_curve") == "linear", f"fade_in_curve: {p.get('fade_in_curve')!r}")
    check(p.get("fade_out_curve") == "cosine", f"fade_out_curve: {p.get('fade_out_curve')!r}")
    check(pl.compatible(payload) == [], f"expected compatible, got {pl.compatible(payload)}")


def test_fade_absent_from_a_pre_migration_source():
    """A source database `tools/add_fade_columns.py` has never touched --
    `has_column` reads around the gap the same way `export_changes.py`
    already does for `boundary_reviews`, rather than raising `OperationalError`
    or inventing values the sender never actually held.
    """
    conn = make_db(":memory:", PRE_FADE_SCHEMA)
    conn.execute(
        "INSERT INTO passages VALUES (1,1,'radio',0,10000,5,900,-1.0,'segmentation')")
    payload = pl.build(conn, ["md5"])
    p = payload["encodings"][0]["passages"][0]
    for k in ("fade_in_ms", "fade_out_ms", "fade_in_curve", "fade_out_curve"):
        check(k not in p, f"expected {k} omitted for a pre-fade source, got {p.get(k)!r}")
    check(pl.compatible(payload) == [], f"expected compatible, got {pl.compatible(payload)}")


def test_a_decided_hold_travels_and_an_undecided_one_does_not():
    """`[SPEC-HOLD-080]` A reason held and a person's release ("") travel;
    NULL, undecided, is absent -- so it leaves a receiver's own value alone."""
    conn = make_db(":memory:", SCHEMA)
    conn.execute("ALTER TABLE passages ADD COLUMN director_hold TEXT")
    for pid, start, hold in ((1, 0, "rip damaged"), (2, 3000, ""), (3, 6000, None)):
        conn.execute("INSERT INTO passages (passage_id,file_id,kind,start_ms,end_ms,boundary_src,director_hold) "
                     "VALUES (?,1,'radio',?,?,'x',?)", (pid, start, start + 2000, hold))
    got = [p.get("hold", "ABSENT") for p in pl.build(conn, ["md5"])["encodings"][0]["passages"]]
    check(got == ["rip damaged", "", "ABSENT"], f"holds in the payload: {got}")


def test_committed_fixture_09_round_trips():
    """`fixtures/payload/09-fade-fields.json` `[SPEC-PL-032]` -- the same file
    `player/src/bundle.rs`'s own test checks with `unacceptable()`, checked
    here with `compatible()`. One fixture, both implementations.
    """
    import json
    path = os.path.join(HERE, "..", "fixtures", "payload", "09-fade-fields.json")
    payload = json.load(open(path, encoding="utf-8"))
    check(pl.compatible(payload) == [], f"expected compatible, got {pl.compatible(payload)}")
    p = payload["encodings"][0]["passages"][0]
    check((p["fade_in_ms"], p["fade_out_ms"], p["fade_in_curve"], p["fade_out_curve"])
          == (15, 1200, "linear", "cosine"),
          f"unexpected fade values: {p}")


def test_byte_hash_is_optional_but_must_be_usable():
    """`sha256` `[SPEC-PL-087]`: absent is fine, well-formed is fine, and
    anything else is refused -- the same verdicts as the Rust importer's
    `a_malformed_byte_hash_is_refused`.
    """
    import json
    path = os.path.join(HERE, "..", "fixtures", "payload", "01-valid-four-tracks.json")
    payload = json.load(open(path, encoding="utf-8"))
    check(pl.compatible(payload) == [], "01 carries no sha256 and must stay acceptable")
    enc = payload["encodings"][0]
    enc["sha256"] = "0" * 64
    check(pl.compatible(payload) == [], f"a well-formed sha256 refused: {pl.compatible(payload)}")
    for bad in ("abc", 12, "A" * 64):
        enc["sha256"] = bad
        got = pl.compatible(payload)
        check(got == [f"{enc['audio_md5']}: encoding.sha256 is not 64 lower-case hex digits"],
              f"sha256={bad!r}: got {got}")


def test_lyrics_travel_on_their_recording():
    """`[SPEC-LYR-025]`, `[REQ-AND-202]`: a recording's words ride on it,
    absent when it has none or the library has no `lyrics` table at all, and
    a partial `lyrics` object is refused as any partial row is.
    """
    def library(with_table):
        conn = make_db(":memory:", SCHEMA)
        conn.execute("INSERT INTO passages VALUES (1,1,'radio',0,10000,NULL,NULL,NULL,'x',"
                     "20,20,'exponential','exponential')")
        for m in ("m1", "m2"):
            conn.execute("INSERT INTO recordings VALUES (?,'t',NULL,'s')", (m,))
            conn.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'s')", (m,))
        if with_table:
            conn.execute("CREATE TABLE lyrics (mbid TEXT PRIMARY KEY, text TEXT NOT NULL, "
                         "source TEXT NOT NULL, fetched_at TEXT NOT NULL)")
            conn.execute("INSERT INTO lyrics VALUES ('m1','la la','mulibplay','2026-09-01T00:00:00+00:00')")
        return conn

    recs = {r["mbid"]: r for r in pl.build(library(True), ["md5"])["recordings"]}
    check(recs["m1"].get("lyrics") == {"text": "la la", "source": "mulibplay",
                                       "fetched_at": "2026-09-01T00:00:00+00:00"},
          f"m1 lyrics: {recs['m1'].get('lyrics')!r}")
    check("lyrics" not in recs["m2"], "a recording without words must carry no lyrics key")
    payload = pl.build(library(False), ["md5"])
    check(all("lyrics" not in r for r in payload["recordings"]),
          "a library without the table must send no lyrics")
    payload = pl.build(library(True), ["md5"])
    del payload["recordings"][0]["lyrics"]["source"]
    check(pl.compatible(payload) == ["m1: missing lyrics.source"],
          f"partial lyrics: {pl.compatible(payload)}")


def test_retired_keys_travel_with_every_payload():
    """`[SPEC-PL-097]`: the library's retired keys ride on every payload --
    all of them, not only those of the encodings sent -- and a library never
    re-keyed sends no `aliases` at all.
    """
    conn = make_db(":memory:", SCHEMA)
    check("aliases" not in pl.build(conn, ["md5"]), "no table, no aliases key")
    conn.execute("CREATE TABLE audio_md5_aliases (old_md5 TEXT PRIMARY KEY, new_md5 TEXT NOT NULL, "
                 "generator TEXT NOT NULL, rekeyed_at TEXT NOT NULL)")
    conn.execute("INSERT INTO audio_md5_aliases VALUES ('b-old','b-new','symphonia@0.5.5','t')")
    conn.execute("INSERT INTO audio_md5_aliases VALUES ('a-old','a-new','symphonia@0.5.5','t')")
    got = pl.build(conn, ["md5"]).get("aliases")
    check(got == [{"old": "a-old", "new": "a-new", "generator": "symphonia@0.5.5"},
                  {"old": "b-old", "new": "b-new", "generator": "symphonia@0.5.5"}],
          f"every alias, in a stable order: {got}")


def test_a_files_release_travels_with_it():
    """[SPEC-SC-125] A file's own release travels on its encoding, and that
    release -- neither chosen nor pictured -- with its track for the file's
    recording, so the receiver can name the album."""
    conn = make_db(":memory:", SCHEMA)
    conn.execute("INSERT INTO passages VALUES (1,1,'radio',0,10000,NULL,NULL,NULL,'x',"
                 "20,20,'exponential','exponential')")
    conn.execute("INSERT INTO recordings VALUES ('m1','t',NULL,'s')")
    conn.execute("INSERT INTO passage_recordings VALUES (1,'m1',1.0,'s')")
    conn.executescript("""
        CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT,
            source TEXT NOT NULL, release_group TEXT, status TEXT, primary_type TEXT,
            secondary_types TEXT, country TEXT, track_count INTEGER);
        CREATE TABLE release_recordings (release_mbid TEXT NOT NULL, mbid TEXT NOT NULL,
            position INTEGER, source TEXT NOT NULL, track_length_ms INTEGER, chosen INTEGER DEFAULT 0,
            disc INTEGER, PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;
        CREATE TABLE file_releases (audio_md5 TEXT PRIMARY KEY, release_mbid TEXT NOT NULL,
            source TEXT NOT NULL, decided_at TEXT);
        INSERT INTO releases (mbid,title,source) VALUES ('album','Album','mb'), ('comp','Compilation','mb');
        INSERT INTO release_recordings VALUES ('album','m1',3,'mb',NULL,1,1), ('comp','m1',9,'mb',NULL,0,1);
        INSERT INTO file_releases VALUES ('md5','comp','cd:import','t');
    """)
    doc = pl.build(conn, ["md5"])
    check(doc["encodings"][0].get("release") == {"mbid": "comp", "source": "cd:import", "decided_at": "t"},
          f"on the encoding: {doc['encodings'][0].get('release')}")
    rels = {r["mbid"]: r for r in doc.get("releases", [])}
    check("comp" in rels and [t["position"] for t in rels["comp"]["tracks"]] == [9],
          f"the file's release, with its track: {sorted(rels)}")


def test_covers_travel_as_files_named_by_the_payload():
    """`[SPEC-PL-105]`: a recording's chosen release, and any release of it
    with a front cover, travel with its tracks for the payload's recordings
    only; the cover is named as a file with its byte hash, and `cover_files`
    returns exactly those bytes. Other releases stay home.
    """
    import hashlib
    conn = make_db(":memory:", SCHEMA)
    conn.execute("INSERT INTO passages VALUES (1,1,'radio',0,10000,NULL,NULL,NULL,'x',"
                 "20,20,'exponential','exponential')")
    conn.execute("INSERT INTO recordings VALUES ('m1','t',NULL,'s')")
    conn.execute("INSERT INTO passage_recordings VALUES (1,'m1',1.0,'s')")
    conn.executescript("""
        CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT,
            source TEXT NOT NULL, release_group TEXT, status TEXT, primary_type TEXT,
            secondary_types TEXT, country TEXT, track_count INTEGER);
        CREATE TABLE release_recordings (release_mbid TEXT NOT NULL, mbid TEXT NOT NULL,
            position INTEGER, source TEXT NOT NULL, track_length_ms INTEGER, chosen INTEGER DEFAULT 0,
            disc INTEGER, PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;
        CREATE TABLE cover_art (release_mbid TEXT PRIMARY KEY, front BLOB, back BLOB,
            source TEXT NOT NULL, fetched_at TEXT NOT NULL);
        INSERT INTO releases (mbid,title,source) VALUES ('r-chosen','Chosen','mb'),
            ('r-pictured','Pictured','mb'), ('r-other','Other','mb');
        INSERT INTO release_recordings VALUES ('r-chosen','m1',3,'mb',NULL,1,1),
            ('r-pictured','m1',5,'mb',NULL,0,1), ('r-other','m1',7,'mb',NULL,0,1);
    """)
    jpg = b"\xff\xd8\xff" + b"j" * 600
    png = b"\x89PNG" + b"p" * 600
    conn.execute("INSERT INTO cover_art VALUES ('r-pictured', ?, ?, 'caa', 't')", (jpg, png))
    doc = pl.build(conn, ["md5"])
    rels = {r["mbid"]: r for r in doc.get("releases", [])}
    check(sorted(rels) == ["r-chosen", "r-pictured"], f"chosen and pictured only: {sorted(rels)}")
    check(rels["r-chosen"]["tracks"] == [{"recording": "m1", "position": 3, "disc": 1, "chosen": 1,
                                          "track_length_ms": None, "source": "mb"}],
          f"tracks: {rels['r-chosen']['tracks']}")
    cov = rels["r-pictured"].get("cover", {})
    check(cov.get("front") == {"file": "covers/r-pictured-front.jpg",
                               "sha256": hashlib.sha256(jpg).hexdigest()}, f"front: {cov.get('front')}")
    check(cov.get("back", {}).get("file") == "covers/r-pictured-back.png", f"back: {cov.get('back')}")
    files = pl.cover_files(conn, doc)
    check(files == {"covers/r-pictured-front.jpg": jpg, "covers/r-pictured-back.png": png},
          f"cover files: {sorted(files)}")


def test_a_files_own_cover_travels_on_its_encoding():
    """`[SPEC-COV-040]`: a file's own cover, in `file_art`, travels on its
    encoding as a release's does on the release, and nowhere without one."""
    conn = make_db(":memory:", SCHEMA)
    conn.execute("CREATE TABLE file_art (audio_md5 TEXT PRIMARY KEY, front BLOB, back BLOB, "
                 "source TEXT NOT NULL, fetched_at TEXT NOT NULL)")
    doc = pl.build(conn, ["md5"])
    check("cover" not in doc["encodings"][0], "no cover where file_art has none")
    jpg = b"\xff\xd8\xff" + b"o" * 600
    conn.execute("INSERT INTO file_art VALUES ('md5', ?, NULL, 'found:embedded', 't')", (jpg,))
    doc = pl.build(conn, ["md5"])
    cov = doc["encodings"][0].get("cover", {})
    check(cov.get("source") == "found:embedded" and cov.get("front", {}).get("file") == "covers/file-md5-front.jpg"
          and "back" not in cov, f"the encoding's own cover: {cov}")
    check(pl.cover_files(conn, doc) == {"covers/file-md5-front.jpg": jpg}, "and its bytes")


def main() -> int:
    test_a_files_release_travels_with_it()
    test_covers_travel_as_files_named_by_the_payload()
    test_a_files_own_cover_travels_on_its_encoding()
    test_retired_keys_travel_with_every_payload()
    test_lyrics_travel_on_their_recording()
    test_fade_travels_when_the_schema_has_it()
    test_fade_absent_from_a_pre_migration_source()
    test_a_decided_hold_travels_and_an_undecided_one_does_not()
    test_committed_fixture_09_round_trips()
    test_byte_hash_is_optional_but_must_be_usable()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("payload: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
