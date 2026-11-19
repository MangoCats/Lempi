#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `suggest_release.py` `[SPEC-SUI-215]`.

No real MusicBrainz call in any test here: `mb_get` (the one function that
ever reaches the network) is faked, the same technique `test_remote_peek.py`
already uses on `subprocess.run`. Fixture shapes for the search/detail
responses mirror the real MusicBrainz release-detail JSON confirmed live
against `e4d469ff-3633-4e16-8f49-03c48e37c5fb` ("The Best of Foghat") while
designing this feature -- trimmed to three tracks for test speed.

    python tools/test_suggest_release.py
"""

import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import suggest_release as sr  # noqa: E402

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT NOT NULL, size_bytes INTEGER, mtime REAL, format TEXT,
    duration_ms INTEGER, first_seen TEXT, last_seen TEXT);
CREATE TABLE file_tags (file_id INTEGER, title TEXT, artist TEXT, album TEXT,
    track_no INTEGER, disc_no INTEGER, has_art INTEGER, scanned_at TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT, start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT, passage_uid TEXT GENERATED ALWAYS AS (printf('%032x', passage_id)) VIRTUAL);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL,
    sort_name TEXT, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT NOT NULL, artist_mbid TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
    PRIMARY KEY (mbid, artist_mbid)) WITHOUT ROWID;
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
    PRIMARY KEY (passage_id, mbid)) WITHOUT ROWID;
CREATE TABLE listener_flags (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    flagged_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID;
CREATE TABLE id_checks (passage_id INTEGER PRIMARY KEY, stored_mbid TEXT NOT NULL,
    verdict TEXT NOT NULL, score REAL, suggested TEXT, checked_at TEXT NOT NULL);
"""

FOLDER = os.path.normpath("C:/Music/TestBand/Greatest Hits")

REC_1, REC_2, REC_3 = ("11111111-1111-1111-1111-111111111111",
                        "22222222-2222-2222-2222-222222222222",
                        "33333333-3333-3333-3333-333333333333")
ART_1 = "44444444-4444-4444-4444-444444444444"
REL_1 = "e4d469ff-3633-4e16-8f49-03c48e37c5fb"

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def build(path: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    # Two files that actually match the (fake) release: track-numbered,
    # titled, positioned like the real Foghat fixture that motivated this.
    c.execute("INSERT INTO files VALUES (1,'md5-a',?,1,1.0,'mp3',199500,'t','t')",
              (os.path.join(FOLDER, "01 - Alpha.mp3"),))
    c.execute("INSERT INTO file_tags VALUES (1,'Alpha','TestBand','Greatest Hits',1,NULL,0,'t')")
    c.execute("INSERT INTO passages VALUES (10,1,'radio',0,199500,NULL,NULL,NULL,'ingest:whole-file')")

    c.execute("INSERT INTO files VALUES (2,'md5-b',?,1,1.0,'mp3',210800,'t','t')",
              (os.path.join(FOLDER, "02 - Beta.mp3"),))
    c.execute("INSERT INTO file_tags VALUES (2,'Beta','TestBand','Greatest Hits',2,NULL,0,'t')")
    c.execute("INSERT INTO passages VALUES (20,2,'radio',0,210800,NULL,NULL,NULL,'ingest:whole-file')")

    # A file that exists but shares nothing with the release's tracklist --
    # must stay unmatched, not guessed into a wrong slot.
    c.execute("INSERT INTO files VALUES (3,'md5-c',?,1,1.0,'mp3',50000,'t','t')",
              (os.path.join(FOLDER, "03 - Studio Chatter.mp3"),))
    c.execute("INSERT INTO file_tags VALUES (3,'Studio Chatter','TestBand','Greatest Hits',3,NULL,0,'t')")
    c.execute("INSERT INTO passages VALUES (30,3,'radio',0,50000,NULL,NULL,NULL,'ingest:whole-file')")

    # A file in a SUBFOLDER -- must never be considered part of this folder.
    c.execute("INSERT INTO files VALUES (4,'md5-d',?,1,1.0,'mp3',180000,'t','t')",
              (os.path.join(FOLDER, "Bonus Disc", "01 - Unrelated.mp3"),))
    c.commit()
    c.close()


def fake_detail() -> dict:
    """Trimmed from the real, live-fetched `e4d469ff-...` response."""
    return {
        "id": REL_1, "title": "Greatest Hits", "date": "1999", "status": "Official",
        "country": "US",
        "artist-credit": [{"name": "TestBand", "joinphrase": "",
                            "artist": {"id": ART_1, "name": "TestBand"}}],
        "release-group": {"id": "rg-1", "primary-type": "Album", "secondary-types": []},
        "media": [{"track-count": 3, "tracks": [
            {"position": 1, "title": "Alpha", "length": 199000,
             "recording": {"id": REC_1, "title": "Alpha", "length": 199000,
                           "artist-credit": [{"name": "TestBand", "joinphrase": "",
                                              "artist": {"id": ART_1, "name": "TestBand"}}]}},
            {"position": 2, "title": "Beta", "length": 211000,
             "recording": {"id": REC_2, "title": "Beta", "length": 211000,
                           "artist-credit": [{"name": "TestBand", "joinphrase": "",
                                              "artist": {"id": ART_1, "name": "TestBand"}}]}},
            # A third track this test fixture's folder never had a file for --
            # must be reported as an unmatched TRACK, the mirror case of an
            # unmatched file.
            {"position": 3, "title": "Gamma", "length": 180000,
             "recording": {"id": REC_3, "title": "Gamma", "length": 180000,
                           "artist-credit": [{"name": "TestBand", "joinphrase": "",
                                              "artist": {"id": ART_1, "name": "TestBand"}}]}},
        ]}],
    }


def fake_mb_get(url: str):
    if "query=" in url:
        return {"releases": [{"id": REL_1, "score": "100"}]}
    if f"/{REL_1}" in url:
        return fake_detail()
    return None


# -- pure logic, no I/O -------------------------------------------------------

def test_gather_folder_files_exact_scope(tmp: str) -> None:
    print("gather_folder_files(): exact directory match, not recursive")
    db = os.path.join(tmp, "lib.db")
    build(db)
    conn = sqlite3.connect(db)
    files = sr.gather_folder_files(conn, FOLDER)
    conn.close()
    paths = {os.path.basename(f["path"]) for f in files}
    check(paths == {"01 - Alpha.mp3", "02 - Beta.mp3", "03 - Studio Chatter.mp3"},
          f"expected exactly the three flat files, got {paths}")


def test_match_files_to_tracks_and_score() -> None:
    print("match_files_to_tracks(): a clean match, a genuine miss, both reported")
    files = [
        {"file_id": 1, "path": "01 - Alpha.mp3", "duration_ms": 199500,
         "title": "Alpha", "track_no": 1, "passage_ids": [10]},
        {"file_id": 2, "path": "02 - Beta.mp3", "duration_ms": 210800,
         "title": "Beta", "track_no": 2, "passage_ids": [20]},
        {"file_id": 3, "path": "03 - Studio Chatter.mp3", "duration_ms": 50000,
         "title": "Studio Chatter", "track_no": 3, "passage_ids": [30]},
    ]
    tracks = sr.tracks_from_detail(fake_detail())
    matches, unmatched_files, unmatched_tracks = sr.match_files_to_tracks(files, tracks)

    check(len(matches) == 2, f"expected exactly Alpha and Beta to match, got {matches}")
    by_pos = {m["track_position"]: m for m in matches}
    check(by_pos[1]["recording_mbid"] == REC_1 and by_pos[1]["file"] == "01 - Alpha.mp3",
          f"got {by_pos.get(1)}")
    check(by_pos[2]["recording_mbid"] == REC_2 and by_pos[2]["file"] == "02 - Beta.mp3",
          f"got {by_pos.get(2)}")
    check(unmatched_files == ["03 - Studio Chatter.mp3"],
          f"the unrelated file must be named, not silently matched, got {unmatched_files}")
    check(unmatched_tracks == ["Gamma"],
          f"the track with no file must be named too, got {unmatched_tracks}")

    score = sr.folder_score(files, matches)
    check(0.0 < score <= 1.0, f"expected a score in (0,1], got {score}")
    check(sr.folder_score(files, []) == 0.0, "no matches must score exactly 0")


def test_release_id_in() -> None:
    print("release_id_in(): a pasted id or URL is recognised, a search phrase is not")
    check(sr.release_id_in(REL_1) == REL_1,
          "a bare id, exactly as `--accept` already expects, must round-trip")
    check(sr.release_id_in(REL_1.upper()) == REL_1,
          "musicbrainz.org itself renders ids lowercase, but a pasted one may not be")
    check(sr.release_id_in(f"https://musicbrainz.org/release/{REL_1}") == REL_1,
          "the whole page URL, pasted as-is, must still find the id inside it")
    check(sr.release_id_in(f"  https://musicbrainz.org/release/{REL_1}?tab=details ") == REL_1,
          "surrounding whitespace or a trailing query string must not block the match")
    check(sr.release_id_in('release:"Greatest Hits" AND artist:"TestBand"') is None,
          "an ordinary search phrase must not be mistaken for an id")
    check(sr.release_id_in("") is None, "an empty query has no id to find")
    check(sr.release_id_in(None) is None, "release_id_in must tolerate a None query, not raise")


# -- discovery, faked network --------------------------------------------------

class Args:
    def __init__(self, **kw):
        self.folder = FOLDER
        self.query = None
        self.accept = None
        self.commit = False
        self.json = True
        self.__dict__.update(kw)


def test_discover(tmp: str) -> None:
    print("do_discover(): finds, scores, and caches the release -- no real network")
    db = os.path.join(tmp, "discover.db")
    build(db)
    conn = sqlite3.connect(db)
    sr.ensure_schema(conn)
    real_get = sr.mb_get
    sr.mb_get = fake_mb_get
    try:
        files = sr.gather_folder_files(conn, FOLDER)
        rc = sr.do_discover(conn, Args(), files)
    finally:
        sr.mb_get = real_get
    check(rc == 0, f"expected exit 0, got {rc}")

    cand = conn.execute("SELECT COUNT(*) FROM releases WHERE mbid=?1", (REL_1,)).fetchone()[0]
    check(cand == 1, "the candidate release must be cached even though nothing was accepted yet")
    tracks_cached = conn.execute(
        "SELECT COUNT(*) FROM release_recordings WHERE release_mbid=?1", (REL_1,)).fetchone()[0]
    check(tracks_cached == 3, f"all three tracks must be cached, got {tracks_cached}")
    dangling = conn.execute("SELECT COUNT(*) FROM release_recordings WHERE release_mbid=?1 "
                            "AND mbid NOT IN (SELECT mbid FROM recordings)", (REL_1,)).fetchone()[0]
    check(dangling == 0, f"every cached track must name a recording the catalogue holds [SPEC-LNK-015], {dangling} did not")
    check(conn.execute("SELECT COUNT(*) FROM passage_recordings").fetchone()[0] == 0,
          "discovery must never touch passage_recordings")
    conn.close()


def test_discover_direct_release_id(tmp: str) -> None:
    print("do_discover(): a pasted release id/URL is fetched directly, never searched")
    db = os.path.join(tmp, "discover-direct.db")
    build(db)
    conn = sqlite3.connect(db)
    sr.ensure_schema(conn)

    searched = []
    real_search = sr.search_releases

    def spying_search(query):
        searched.append(query)
        return real_search(query)

    real_get = sr.mb_get
    sr.mb_get = fake_mb_get
    sr.search_releases = spying_search
    try:
        files = sr.gather_folder_files(conn, FOLDER)
        for query in (REL_1, f"https://musicbrainz.org/release/{REL_1}"):
            out = []
            real_say = sr.say
            sr.say = out.append
            try:
                rc = sr.do_discover(conn, Args(query=query), files)
            finally:
                sr.say = real_say
            check(rc == 0, f"expected exit 0 for query {query!r}, got {rc}")
            result = json.loads(out[-1])
            check(result["ok"] and len(result["candidates"]) == 1
                  and result["candidates"][0]["mbid"] == REL_1,
                  f"the named release must be the one and only candidate, got {out[-1]}")
    finally:
        sr.mb_get = real_get
        sr.search_releases = real_search
    check(searched == [], f"a directly-named release must never reach search_releases(), got {searched}")
    conn.close()


# -- accept, the write half ----------------------------------------------------

def test_accept_rehearsal_then_commit(tmp: str) -> None:
    print("do_accept(): rehearsal writes nothing; --commit lands recordings + decisions")
    db = os.path.join(tmp, "accept.db")
    build(db)
    conn = sqlite3.connect(db)
    sr.ensure_schema(conn)
    real_get = sr.mb_get
    sr.mb_get = fake_mb_get
    try:
        files = sr.gather_folder_files(conn, FOLDER)

        rc = sr.do_accept(conn, Args(accept=REL_1, commit=False), files)
        check(rc == 0, f"rehearsal exited {rc}")
        check(conn.execute("SELECT COUNT(*) FROM passage_recordings").fetchone()[0] == 0,
              "rehearsal must not write")

        rc = sr.do_accept(conn, Args(accept=REL_1, commit=True), files)
        check(rc == 0, f"commit exited {rc}")
    finally:
        sr.mb_get = real_get

    got_alpha = conn.execute("SELECT mbid, source FROM passage_recordings WHERE passage_id=10").fetchone()
    got_beta = conn.execute("SELECT mbid, source FROM passage_recordings WHERE passage_id=20").fetchone()
    check(got_alpha == (REC_1, sr.SOURCE), f"got {got_alpha}")
    check(got_beta == (REC_2, sr.SOURCE), f"got {got_beta}")
    check(conn.execute("SELECT COUNT(*) FROM passage_recordings WHERE passage_id=30").fetchone()[0] == 0,
          "the unmatched file's passage must be left untouched")

    check(conn.execute("SELECT title FROM recordings WHERE mbid=?1", (REC_1,)).fetchone() == ("Alpha",),
          "a genuinely new recording must be created from the release track's own title")
    check(conn.execute("SELECT name FROM artists WHERE mbid=?1", (ART_1,)).fetchone() == ("TestBand",),
          "the artist must be created too")
    check(conn.execute(
        "SELECT 1 FROM recording_artists WHERE mbid=?1 AND artist_mbid=?2", (REC_1, ART_1)).fetchone(),
        "the recording-artist link must be written")

    decisions = conn.execute(
        "SELECT audio_md5, stage, outcome FROM ingest_decisions ORDER BY audio_md5").fetchall()
    check(len(decisions) == 2, f"expected one decision per matched passage, got {decisions}")
    check(all(stage == "folder_release_match" and outcome == REL_1 for _, stage, outcome in decisions),
          f"got {decisions}")
    conn.close()


def test_accept_clears_stale_flags(tmp: str) -> None:
    """A real regression, not a hypothetical: accepting a release moves a
    passage's `passage_recordings` row from its pre-accept `local:audio:...`
    id (the one `ingest_folder.py` always synthesizes) to the newly-matched
    real recording -- and a `listener_flags` row set against that OLD id,
    exactly the id a listener would have flagged while it was still wrong,
    is left pointing at nothing `console.flags()` can resolve any more
    unless `do_accept` clears it the same way `apply_changes.py`'s own
    `--clear-flags` `[SPEC-DF-112]` already does for the sync path.
    """
    print("do_accept(): a flag set against the pre-accept id is cleared, not orphaned")
    db = os.path.join(tmp, "flags.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    old_mbid = "local:audio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    c.execute("INSERT INTO files VALUES (1,'md5-a',?,1,1.0,'mp3',199500,'t','t')",
              (os.path.join(FOLDER, "01 - Alpha.mp3"),))
    c.execute("INSERT INTO file_tags VALUES (1,'Alpha','TestBand','Greatest Hits',1,NULL,0,'t')")
    c.execute("INSERT INTO passages VALUES (10,1,'radio',0,199500,NULL,NULL,NULL,'ingest:whole-file')")
    # The pre-accept state ingest_folder.py always leaves: a passage_recordings
    # row pointing at the file's own synthesized local:audio: id.
    c.execute("INSERT INTO recordings VALUES (?,'Alpha',NULL,'inherited:mulib')", (old_mbid,))
    c.execute("INSERT INTO passage_recordings VALUES (10,?,1.0,'inherited:mulib')", (old_mbid,))
    # Both plausible flags a listener could have set while it was still
    # unidentified: by the (wrong) recording id, and by the passage itself.
    c.execute("INSERT INTO listener_flags (subject_kind, subject_id, flagged_at) "
              "VALUES ('recording', ?, '2026-08-29 12:00:00')", (old_mbid,))
    c.execute("INSERT INTO listener_flags (subject_kind, subject_id, flagged_at) "
              "VALUES ('passage', printf('%032x', 10), '2026-08-29 12:00:00')")
    c.commit()
    c.close()

    conn = sqlite3.connect(db)
    sr.ensure_schema(conn)
    real_get = sr.mb_get
    sr.mb_get = fake_mb_get
    try:
        files = sr.gather_folder_files(conn, FOLDER)
        rc = sr.do_accept(conn, Args(accept=REL_1, commit=True), files)
    finally:
        sr.mb_get = real_get
    check(rc == 0, f"exited {rc}")

    got = conn.execute("SELECT mbid FROM passage_recordings WHERE passage_id=10").fetchone()
    check(got == (REC_1,), f"the passage must actually be reassigned, got {got}")
    remaining = conn.execute("SELECT subject_kind, subject_id FROM listener_flags").fetchall()
    check(remaining == [], f"both the old-id flag and the passage flag must be cleared, got {remaining}")
    conn.close()


def test_accept_clears_stale_id_check(tmp: str) -> None:
    """A second real regression, found live: Lempi's own review queue reads
    `id_checks.stored_mbid` directly (`player/src/db.rs`'s `review_queue()`),
    not `passage_recordings` -- a *different*, AcoustID-fingerprint-based
    identification method this tool never runs. Left in place, a passage
    this run just resolved kept surfacing on that queue captioned with its
    pre-accept id and "no MusicBrainz id", both now false. The fix deletes
    the stale row outright rather than trying to update it to agree --
    nothing here re-ran AcoustID, so there is no fresher verdict to write,
    only an obsolete one to stop asserting.
    """
    print("do_accept(): a stale id_checks row (a different identification method) is cleared too")
    db = os.path.join(tmp, "idchecks.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    old_mbid = "local:audio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    c.execute("INSERT INTO files VALUES (1,'md5-a',?,1,1.0,'mp3',199500,'t','t')",
              (os.path.join(FOLDER, "01 - Alpha.mp3"),))
    c.execute("INSERT INTO file_tags VALUES (1,'Alpha','TestBand','Greatest Hits',1,NULL,0,'t')")
    c.execute("INSERT INTO passages VALUES (10,1,'radio',0,199500,NULL,NULL,NULL,'ingest:whole-file')")
    c.execute("INSERT INTO recordings VALUES (?,'Alpha',NULL,'inherited:mulib')", (old_mbid,))
    c.execute("INSERT INTO passage_recordings VALUES (10,?,1.0,'inherited:mulib')", (old_mbid,))
    # The AcoustID fingerprint pass's own verdict from before -- unrelated to,
    # and un-informed by, this run.
    c.execute("INSERT INTO id_checks VALUES (10,?,'unmatched',NULL,NULL,'2026-08-15T00:00:00')",
              (old_mbid,))
    c.commit()
    c.close()

    conn = sqlite3.connect(db)
    sr.ensure_schema(conn)
    real_get = sr.mb_get
    sr.mb_get = fake_mb_get
    try:
        files = sr.gather_folder_files(conn, FOLDER)
        rc = sr.do_accept(conn, Args(accept=REL_1, commit=True), files)
    finally:
        sr.mb_get = real_get
    check(rc == 0, f"exited {rc}")

    check(conn.execute("SELECT mbid FROM passage_recordings WHERE passage_id=10").fetchone() == (REC_1,),
          "the passage must actually be reassigned")
    check(conn.execute("SELECT 1 FROM id_checks WHERE passage_id=10").fetchone() is None,
          "the stale fingerprint-check row must be gone, not left naming the old id")
    conn.close()


# -- finishing an album from files [SPEC-FIN-035..045] -------------------------

REL_2 = "7271f4eb-addb-44f0-a098-62fd9104e920"
ART_2 = "55555555-5555-5555-5555-555555555555"
SET = os.path.normpath("C:/Music/Traffic/Smiling Phases (1991)")


def two_disc_detail() -> dict:
    """Two discs, each with a track 1 and a track 2, as *Smiling Phases* has
    sixteen and ten: by place alone, disc 2's tracks were disc 1's."""
    credit = [{"name": "Traffic", "joinphrase": " & ", "artist": {"id": ART_1, "name": "Traffic",
                                                               "sort-name": "Traffic"}},
              {"name": "Steve Winwood", "joinphrase": "", "artist": {"id": ART_2, "name": "Steve Winwood",
                                                                    "sort-name": "Winwood, Steve"}}]

    def track(pos, rec, title, length):
        return {"position": pos, "number": str(pos), "title": title, "length": length,
                "recording": {"id": rec, "title": title, "length": length, "artist-credit": credit}}
    return {
        "id": REL_2, "title": "Smiling Phases", "date": "1991", "status": "Official", "country": "US",
        "barcode": "731451111111", "disambiguation": "",
        "label-info": [{"label": {"name": "Island"}}, {"label": {"name": "Island"}}],
        "artist-credit": credit[:1],
        "release-group": {"id": "rg-sp", "primary-type": "Album", "secondary-types": ["Compilation"]},
        "media": [{"position": 1, "format": "CD", "track-count": 2,
                   "tracks": [track(1, REC_1, "Paper Sun", 257000), track(2, REC_2, "Hole In My Shoe", 184000)]},
                  {"position": 2, "format": "CD", "track-count": 2,
                   "tracks": [track(1, REC_3, "Glad", 419000),
                              track(2, "66666666-6666-6666-6666-666666666666", "Freedom Rider", 329000)]}]}


def build_set(path: str) -> None:
    """Three of the set's four tracks, each file with its radio and album
    passage, as `ingest_folder.py` leaves them: placeholders, no edition."""
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("CREATE TABLE ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT, stage TEXT, "
              "outcome TEXT, confidence REAL, detail TEXT, decided_at INTEGER)")
    for fid, name, title, disc, no, ms in ((1, "1-01. Paper Sun.mp3", "Paper Sun", 1, 1, 257750),
                                           (2, "1-02. Hole In My Shoe.mp3", "Hole In My Shoe", 1, 2, 184033),
                                           (3, "2-01. Glad.mp3", "Glad", 2, 1, 419265)):
        c.execute("INSERT INTO files VALUES (?1, ?2, ?3, 1, 1.0, 'mp3', ?4, 't', 't')",
                  (fid, f"md5-{fid}", os.path.join(SET, name), ms))
        c.execute("INSERT INTO file_tags VALUES (?1, ?2, 'Traffic', 'Smiling Phases', ?3, ?4, 0, 't')",
                  (fid, title, no, disc))
        for kind, pid in (("radio", fid * 10), ("album", fid * 10 + 1)):
            c.execute("INSERT INTO passages VALUES (?1, ?2, ?3, 0, ?4, NULL, NULL, NULL, 'ingest:whole-file')",
                      (pid, fid, kind, ms))
            c.execute("INSERT OR IGNORE INTO recordings VALUES (?1, ?2, NULL, 'local:ingest')",
                      (f"local:audio:md5-{fid}", title))
            c.execute("INSERT OR IGNORE INTO passage_recordings VALUES (?1, ?2, 1.0, 'local:ingest')",
                      (pid, f"local:audio:md5-{fid}"))
    c.commit()
    c.close()


def test_two_discs_are_told_apart() -> None:
    print("match_files_to_tracks(): a two-disc set, its tracks by disc and place")
    files = [{"file_id": 1, "path": "1-01. Paper Sun.mp3", "duration_ms": 257750, "title": "Paper Sun",
              "track_no": 1, "disc_no": 1, "passage_ids": [10]},
             {"file_id": 3, "path": "2-01. Glad.mp3", "duration_ms": 419265, "title": "Glad",
              "track_no": 1, "disc_no": 2, "passage_ids": [30]}]
    tracks = sr.tracks_from_detail(two_disc_detail())
    check([(t["disc"], t["position"]) for t in tracks] == [(1, 1), (1, 2), (2, 1), (2, 2)],
          f"each track carries its disc: {[(t['disc'], t['position']) for t in tracks]}")
    matches, unmatched_files, unmatched_tracks = sr.match_files_to_tracks(files, tracks)
    got = {m["file"]: (m["track_disc"], m["track_position"], m["recording_mbid"]) for m in matches}
    check(got == {"1-01. Paper Sun.mp3": (1, 1, REC_1), "2-01. Glad.mp3": (2, 1, REC_3)},
          f"each file to its own disc's track: {got}")
    check(unmatched_tracks == ["Hole In My Shoe", "Freedom Rider"],
          f"both discs' track 2s are unmatched; by place alone disc 2's read as matched: {unmatched_tracks}")
    check(sr.tracks_from_detail(two_disc_detail())[0]["artists"]
          == [(ART_1, "Traffic", "Traffic"), (ART_2, "Steve Winwood", "Winwood, Steve")],
          "every credited artist, with the sort name")


def test_edition_facts() -> None:
    print("edition_facts(): what tells two editions of one title apart")
    got = sr.edition_facts(two_disc_detail())
    check(got == {"country": "US", "status": "Official", "barcode": "731451111111", "disambiguation": None,
                  "discs": 2, "formats": "CD", "labels": "Island"}, f"got {got}")
    check(sr.edition_facts({})["discs"] is None and sr.edition_facts({})["labels"] is None,
          "a response that says nothing says None, not an empty string")


def test_accept_finishes_like_a_cd_add(tmp: str) -> None:
    print("do_accept(): both passages, every artist, one decision per file, and the edition recorded as settled")
    db = os.path.join(tmp, "set.db")
    build_set(db)
    conn = sqlite3.connect(db)
    sr.ensure_schema(conn)
    real_get = sr.mb_get
    sr.mb_get = lambda url: two_disc_detail() if f"/{REL_2}" in url else None
    try:
        files = sr.gather_folder_files(conn, SET)
        check(all(len(f["passage_ids"]) == 2 and f["passage_ids"][0] == f["file_id"] * 10 for f in files),
              f"each file's radio passage, then its album one: {[f['passage_ids'] for f in files]}")
        out = []
        real_say, sr.say = sr.say, out.append
        try:
            rc = sr.do_accept(conn, Args(folder=SET, accept=REL_2, commit=True), files)
        finally:
            sr.say = real_say
    finally:
        sr.mb_get = real_get
    check(rc == 0, f"exited {rc}: {out[-3:]}")
    creds = dict(conn.execute("SELECT passage_id, mbid FROM passage_recordings"))
    check(creds == {10: REC_1, 11: REC_1, 20: REC_2, 21: REC_2, 30: REC_3, 31: REC_3},
          f"the album passage takes the same recording as its radio twin [SPEC-FIN-045]: {creds}")
    rr = conn.execute("SELECT mbid, chosen, source, disc, position FROM release_recordings "
                      "WHERE release_mbid = ?1 AND chosen = 1 ORDER BY disc, position", (REL_2,)).fetchall()
    check(rr == [(REC_1, 1, "folder:chosen", 1, 1), (REC_2, 1, "folder:chosen", 1, 2),
                 (REC_3, 1, "folder:chosen", 2, 1)],
          f"the chosen release of each matched recording, at its disc and place [SPEC-FIN-040]: {rr}")
    fr = conn.execute("SELECT audio_md5, release_mbid, source FROM file_releases ORDER BY audio_md5").fetchall()
    check(fr == [(f"md5-{n}", REL_2, "folder:chosen") for n in (1, 2, 3)],
          f"each file shown under the edition picked: {fr}")
    sorts = dict(conn.execute("SELECT mbid, sort_name FROM artists"))
    check(sorts == {ART_1: "Traffic", ART_2: "Winwood, Steve"}, f"every artist, with the sort name: {sorts}")
    check(conn.execute("SELECT COUNT(*) FROM recording_artists WHERE mbid = ?1", (REC_1,)).fetchone()[0] == 2,
          "both credited artists linked to the recording")
    decisions = conn.execute("SELECT COUNT(*), COUNT(DISTINCT audio_md5) FROM ingest_decisions "
                             "WHERE stage = 'folder_release_match'").fetchone()
    check(decisions == (3, 3), f"one decision per file, not per passage: {decisions}")
    result = json.loads(out[-1])
    check(result["applied"] == 6 and result["linked"] == 3, f"said in the result: {result}")

    # A search afterwards that only looks at the release keeps the choice:
    # until 2026-11-19 its replace reset `chosen` and dropped the disc.
    sr.store_release_detail(conn, two_disc_detail())
    rr2 = conn.execute("SELECT mbid, chosen, source, disc FROM release_recordings "
                       "WHERE release_mbid = ?1 ORDER BY disc, position", (REL_2,)).fetchall()
    check([r for r in rr2 if r[1] == 1] == [(m, 1, "folder:chosen", d) for m, d in ((REC_1, 1), (REC_2, 1), (REC_3, 2))],
          f"a later look at the release takes nothing back: {rr2}")
    conn.close()


def test_accept_on_a_split_pair(tmp: str) -> None:
    """The hub's library is two files, the catalogue and the listener beside
    it [IMPL-DBSPLIT-025], and accepting clears listener flags. Opened with the
    listener read-only, every accept on the hub was refused whole -- "attempt
    to write a readonly database" -- and nothing was named: found 2026-11-19,
    finishing *Smiling Phases*. Every test above used one whole file, where
    the flags table sits in the catalogue's own, and could not see it."""
    print("main --accept on a split pair: the listener's flags are written too, and the accept lands")
    root = os.path.join(tmp, "split")
    os.makedirs(root)
    lib = os.path.join(root, "library.db")
    build_set(lib)
    c = sqlite3.connect(lib)
    c.execute("DROP TABLE listener_flags")                  # it lives in the listener half
    c.commit()
    c.close()
    lis = sqlite3.connect(os.path.join(root, "listener.db"))
    lis.execute("CREATE TABLE listener_flags (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, "
                "flagged_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID")
    lis.execute("INSERT INTO listener_flags VALUES ('recording', 'local:audio:md5-1', 't', NULL)")
    lis.commit()
    lis.close()
    out = []
    real = (sr.mb_get, sr.say, sys.argv)
    sr.mb_get = lambda url: two_disc_detail() if f"/{REL_2}" in url else None
    sr.say = out.append
    sys.argv = ["suggest_release.py", lib, SET, "--accept", REL_2, "--commit", "--json"]
    try:
        rc = sr.main()
    finally:
        sr.mb_get, sr.say, sys.argv = real
    check(rc == 0 and json.loads(out[-1]).get("ok"), f"the accept lands on a split pair: {rc} {out[-2:]}")
    c = sqlite3.connect(lib)
    named = c.execute("SELECT COUNT(*) FROM passage_recordings WHERE mbid NOT LIKE 'local:%'").fetchone()[0]
    c.close()
    check(named == 6, f"every passage named: {named}")
    lis = sqlite3.connect(os.path.join(root, "listener.db"))
    left = lis.execute("SELECT COUNT(*) FROM listener_flags").fetchone()[0]
    lis.close()
    check(left == 0, f"and the flag on the old id cleared, in the listener half: {left} left")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_gather_folder_files_exact_scope(tmp)
        test_match_files_to_tracks_and_score()
        test_release_id_in()
        test_discover(tmp)
        test_discover_direct_release_id(tmp)
        test_accept_rehearsal_then_commit(tmp)
        test_accept_clears_stale_flags(tmp)
        test_accept_clears_stale_id_check(tmp)
        test_two_discs_are_told_apart()
        test_edition_facts()
        test_accept_finishes_like_a_cd_add(tmp)
        test_accept_on_a_split_pair(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("suggest_release: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
