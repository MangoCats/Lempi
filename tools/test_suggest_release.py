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
    lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);
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
              "VALUES ('passage', '10', '2026-08-29 12:00:00')")
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

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("suggest_release: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
