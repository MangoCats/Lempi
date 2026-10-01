#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `ingest_cd.py`'s write path -- `commit_rip()` `[SPEC-RIP-060..074]`.

No network, no ffmpeg: `disc_outcome`/`releases` are passed in already
resolved (`lookup_disc_id()` itself is the one function here that touches
the network, and is not exercised by these tests), and
`segment_dao.identify_recording` is monkeypatched, the same approach
`test_segment_dao.py` already takes toward the same function.

Covers the four branches `[SPEC028] §3` describes:
  * a single Disc ID candidate -> written directly, real recording+artists
  * more than one candidate -> placeholder + `id_checks.suggested`, the
    down-select case `[SPEC-RIP-069]`
  * CD-TEXT present -> placeholder + `id_checks.suggested` regardless of
    candidate count, CD-TEXT as the default shown identity `[SPEC-RIP-066]`
  * nothing at all -> AcoustID fallback, then the bare placeholder
    `[SPEC-RIP-065]`/`[SPEC-RIP-072]`

    python tools/test_ingest_cd.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cd_toc        # noqa: E402
import ingest_cd     # noqa: E402
import segment_dao   # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE,
    path TEXT, size_bytes INTEGER, mtime REAL, format TEXT, duration_ms INTEGER,
    first_seen TEXT, last_seen TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT NOT NULL, start_ms INTEGER, end_ms INTEGER,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT, length_ms INTEGER, source TEXT);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT, source TEXT);
CREATE TABLE recording_artists (mbid TEXT NOT NULL, artist_mbid TEXT NOT NULL,
    weight REAL, source TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT NOT NULL,
    weight REAL, source TEXT);
CREATE TABLE ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    stage TEXT NOT NULL, outcome TEXT NOT NULL, confidence REAL, detail TEXT, decided_at TEXT);
CREATE TABLE id_checks (passage_id INTEGER PRIMARY KEY, stored_mbid TEXT NOT NULL,
    verdict TEXT NOT NULL, score REAL, suggested TEXT, checked_at TEXT NOT NULL);
"""

MD5 = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"

# Five tracks, one per branch. Times are arbitrary but strictly increasing.
TRACKS = [
    cd_toc.TocTrack(number=1, start_ms=0,      end_ms=60000,  start_sector=0, end_sector=0),
    cd_toc.TocTrack(number=2, start_ms=60000,  end_ms=120000, start_sector=0, end_sector=0),
    cd_toc.TocTrack(number=3, start_ms=120000, end_ms=180000, start_sector=0, end_sector=0,
                     title="CD-TEXT Song Three"),
    cd_toc.TocTrack(number=4, start_ms=180000, end_ms=240000, start_sector=0, end_sector=0),
    cd_toc.TocTrack(number=5, start_ms=240000, end_ms=300000, start_sector=0, end_sector=0),
]
TOC = cd_toc.DiscToc(tracks=TRACKS, leadout_sector=0, source="eac-cue")

RELEASES = [
    {"id": "rel-A", "title": "Release A", "media": [{"tracks": [
        {"position": 1, "recording": {"id": "rec-1", "title": "Song One",
         "artist-credit": [{"artist": {"id": "art-1", "name": "Artist One"}}]}},
        {"position": 2, "recording": {"id": "rec-2a", "title": "Song Two (A)",
         "artist-credit": [{"artist": {"id": "art-2a", "name": "Artist Two A"}}]}},
    ]}]},
    {"id": "rel-B", "title": "Release B", "media": [{"tracks": [
        # Same recording as release A at position 1 -- must dedup to ONE candidate.
        {"position": 1, "recording": {"id": "rec-1", "title": "Song One",
         "artist-credit": [{"artist": {"id": "art-1", "name": "Artist One"}}]}},
        # A DIFFERENT recording at position 2 -- the genuine down-select case.
        {"position": 2, "recording": {"id": "rec-2b", "title": "Song Two (B)",
         "artist-credit": [{"artist": {"id": "art-2b", "name": "Artist Two B"}}]}},
    ]}]},
]

CANNED_ACOUSTID = {
    180000: {"mbid": "rec-4-acoustid", "title": "Song Four",
             "artists": [("art-4", "Artist Four")]},
    240000: None,   # track 5: AcoustID also misses -- the true "nothing" case
}


def fixture() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def test_chosen_edition():
    """[SPEC-CDI-040]: an edition a person chose is the answer -- CD-TEXT no
    longer sends its tracks to review, and the decision says a person chose."""
    tmpdir = tempfile.mkdtemp()
    mp3_path = os.path.join(tmpdir, "dao.mp3")
    with open(mp3_path, "wb") as f:
        f.write(b"\0" * 128)
    rel = {"id": "rel-C", "title": "Release C", "media": [{"tracks": [
        {"position": n, "recording": {"id": f"rec-c{n}", "title": f"Song {n}",
         "artist-credit": [{"artist": {"id": "art-c", "name": "Artist C"}}]}} for n in (1, 2, 3)]}]}
    toc = cd_toc.DiscToc(tracks=TRACKS[:3], leadout_sector=0, source="eac-cue")
    c = fixture()
    try:
        r = ingest_cd.commit_rip(c, "/rip", toc, mp3_path, MD5, "exact", [rel], None, None, chosen=True)
        print("commit_rip: a chosen edition")
        check(r["identified"] == 3 and r["ambiguous"] == 0, f"all three from the chosen edition, got {r}")
        check(c.execute("SELECT COUNT(*) FROM id_checks").fetchone()[0] == 0,
              "no down-select waits in review")
        row = c.execute("SELECT outcome, detail FROM ingest_decisions WHERE stage='rip'").fetchone()
        check(row["outcome"] == "chosen" and json.loads(row["detail"])["chosen_by"] == "person",
              f"the decision records a person chose, got {tuple(row)}")
    finally:
        c.close()


def test_preview_pieces():
    """[SPEC-CDI-040..050]: what the preview shows of an edition, the folder it
    proposes, and a name Windows accepts."""
    c = fixture()
    try:
        c.execute("INSERT INTO passage_recordings VALUES (1, 'rec-1', 1.0, 'x')")
        rel = dict(RELEASES[0], date="1994-05-10", country="US",
                   **{"artist-credit": [{"name": "A", "joinphrase": " & "}, {"name": "B"}]})
        s = ingest_cd.release_summary(rel, 2, c)
        print("release_summary: one edition, as the preview shows it")
        check(s["artist"] == "A & B" and s["year"] == "1994", f"credit and year, got {s}")
        check(s["in_library"] == 1, f"one of its two recordings is already held, got {s['in_library']}")
        check(s["folder"] == os.path.join("A & B", "Release A (1994)"), f"got {s['folder']!r}")
    finally:
        c.close()
    check(ingest_cd.safe_name('AC/DC: "Live"?.') == "AC_DC_ _Live__", ingest_cd.safe_name('AC/DC: "Live"?.'))


def test_files_on_disk():
    """[SPEC-CDI-020], [SPEC-RIP-040]: the rip moves home whole, never over
    another; the CUE names the MP3 after, with only its extension and type
    changed -- in bytes, so a Windows code-page title survives."""
    src, dst = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "Artist", "Album")
    cue = 'REM DISCID B90E090E\r\nPERFORMER "Bj\xf6rk"\r\nFILE "Bj\xf6rk - Post.wav" WAVE\r\n  TRACK 01 AUDIO\r\n'
    with open(os.path.join(src, "Bj\xf6rk - Post.cue"), "wb") as f:
        f.write(cue.encode("cp1252"))
    for n in ("Bj\xf6rk - Post.wav", "Bj\xf6rk - Post.log"):
        with open(os.path.join(src, n), "wb") as f:
            f.write(b"x")
    print("move_rip and point_cue_at")
    ingest_cd.move_rip(src, dst)
    check(sorted(os.listdir(dst)) == sorted(["Bj\xf6rk - Post.cue", "Bj\xf6rk - Post.wav", "Bj\xf6rk - Post.log"]),
          f"everything moved, got {os.listdir(dst)}")
    check(not os.path.exists(src), "and the emptied inbox folder is gone")
    again = tempfile.mkdtemp()
    with open(os.path.join(again, "Bj\xf6rk - Post.log"), "wb") as f:
        f.write(b"y")
    try:
        ingest_cd.move_rip(again, dst)
        check(False, "a clash is refused")
    except FileExistsError:
        check(open(os.path.join(dst, "Bj\xf6rk - Post.log"), "rb").read() == b"x", "a clash is refused, nothing overwritten")
    sheet = os.path.join(dst, "Bj\xf6rk - Post.cue")
    ingest_cd.point_cue_at(sheet)
    got = open(sheet, "rb").read()
    check(got == cue.replace('.wav" WAVE', '.mp3" MP3').encode("utf-8"),
          f"the code-page sheet is UTF-8 now, repointed, its line endings kept: {got!r}")
    check(not got.startswith(b"\xef\xbb\xbf"), "without a byte-order mark, as Lempi writes its own")
    check(cd_toc.parse_eac_cue(sheet).data_file == "Bj\xf6rk - Post.mp3", "and it still names the MP3, accent and all")
    before = os.path.getmtime(sheet)
    time.sleep(0.05)
    ingest_cd.point_cue_at(sheet)
    check(open(sheet, "rb").read() == got and os.path.getmtime(sheet) == before,
          "a second pass leaves it byte-for-byte, and does not even rewrite it")


def test_keep_lossless_setting():
    """[SPEC-CDI-058]: the Lempi Settings switch, off unless it says on."""
    p = os.path.join(tempfile.mkdtemp(), "lempi.db")
    c = sqlite3.connect(p)
    c.executescript(SCHEMA + "CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);")
    c.commit()
    print("keep_lossless_setting")
    check(ingest_cd.keep_lossless_setting(p) is False, "unset: off")
    c.execute("INSERT INTO player_settings VALUES ('keep_lossless_rips', '1', 'now')")
    c.commit()
    check(ingest_cd.keep_lossless_setting(p) is True, "set: on")
    c.close()
    check(ingest_cd.keep_lossless_setting(os.path.join(tempfile.mkdtemp(), "none.db")) is False,
          "unreadable: off")


def test_end_to_end():
    """[SPEC-CDI-040], [SPEC-CDI-050], with a real WAV and ffmpeg: the preview
    leaves the rip byte-for-byte as it was; the commit files it in its album
    folder, records that path, removes the WAV and points the CUE at the MP3.
    Only the Disc ID lookup is stubbed -- the one network call."""
    import hashlib
    import subprocess
    if not ingest_cd.FFMPEG:
        check(False, "end to end needs ffmpeg")
        return
    inbox = tempfile.mkdtemp()
    wav = os.path.join(inbox, "Artist - Album.wav")
    subprocess.run([ingest_cd.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-ar", "44100", "-ac", "2", wav], check=True)
    with open(os.path.join(inbox, "Artist - Album.cue"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write('PERFORMER "Artist"\nTITLE "Album"\nFILE "Artist - Album.wav" WAVE\n'
                '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n')
    rel = {"id": "rel-E", "title": "Album", "date": "2001", "artist-credit": [{"name": "Artist"}],
           "media": [{"tracks": [{"position": n, "title": f"Song {n}", "length": 2000,
                                  "recording": {"id": f"rec-e{n}", "title": f"Song {n}"}} for n in (1, 2)]}]}
    db = os.path.join(tempfile.mkdtemp(), "lempi.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA + "CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);")
    c.commit()
    c.close()

    def snapshot(d):
        return {n: hashlib.sha256(open(os.path.join(d, n), "rb").read()).hexdigest() for n in sorted(os.listdir(d))}

    old = ingest_cd.lookup_disc_id
    ingest_cd.lookup_disc_id = lambda toc: ("exact", [rel])
    try:
        print("end to end: preview, then commit")
        before = snapshot(inbox)
        p = ingest_cd.do_ingest(db, inbox, commit=False)
        check(snapshot(inbox) == before, "the preview changes nothing on disk -- no MP3 left behind")
        check(p["tracks"] == 2 and p["releases"][0]["title"] == "Album", f"it shows the disc, got {p}")
        check(p["folder"] == os.path.join("Artist", "Album (2001)"), f"and proposes the folder, got {p['folder']!r}")
        home = os.path.join(tempfile.mkdtemp(), "Artist", "Album (2001)")
        r = ingest_cd.do_ingest(db, inbox, commit=True, release="rel-E", into=home, keep_flac=False)
        names = sorted(os.listdir(home))
        check(names == ["Artist - Album.cue", "Artist - Album.mp3"], f"filed, WAV removed, got {names}")
        check(r["identified"] == 2 and r.get("wav_removed"), f"both tracks identified, got {r}")
        cue = open(os.path.join(home, "Artist - Album.cue"), encoding="utf-8").read()
        check('FILE "Artist - Album.mp3" MP3' in cue, f"the CUE names the MP3: {cue!r}")
        c = sqlite3.connect(db)
        path = c.execute("SELECT path FROM files").fetchone()[0]
        c.close()
        check(path == os.path.join(home, "Artist - Album.mp3"), f"the catalogue names the album's home, got {path!r}")
    finally:
        ingest_cd.lookup_disc_id = old


def main() -> int:
    test_end_to_end()
    test_chosen_edition()
    test_preview_pieces()
    test_files_on_disk()
    test_keep_lossless_setting()
    # `commit_rip` stats the encoded file for the `files` row -- a real,
    # empty file is enough; identification never actually reads it because
    # `segment_dao.identify_recording` is monkeypatched below.
    tmpdir = tempfile.mkdtemp()
    mp3_path = os.path.join(tmpdir, "dao.mp3")
    with open(mp3_path, "wb") as f:
        f.write(b"\0" * 128)

    old_identify = segment_dao.identify_recording
    segment_dao.identify_recording = (
        lambda path, start_s, end_s, key: CANNED_ACOUSTID[round(start_s * 1000)])
    try:
        c = fixture()
        result = ingest_cd.commit_rip(
            c, "/rip/folder", TOC, mp3_path, MD5,
            "exact", RELEASES, rip_report=None, acoustid_key="fake-key")

        print("commit_rip: five tracks, one per identification branch")
        check(result["tracks"] == 5, f"got {result}")
        check(result["identified"] == 2,
              f"track 1 (single candidate) + track 4 (acoustid) = 2, got {result}")
        check(result["ambiguous"] == 2,
              f"track 2 (2 candidates) + track 3 (cd-text) = 2, got {result}")
        check(result["unidentified"] == 1, f"track 5 (nothing at all), got {result}")

        rows = c.execute("SELECT * FROM passages ORDER BY start_ms, kind").fetchall()
        check(len(rows) == 10, f"5 tracks * 2 kinds = 10 passages, got {len(rows)}")
        for r in rows:
            check(r["boundary_src"] == "imported:eac-cue", f"got {r['boundary_src']}")

        print()
        print("track 1: single Disc ID candidate -> written directly, real artist linked")
        pr1 = c.execute(
            "SELECT pr.mbid, pr.source FROM passage_recordings pr "
            "JOIN passages p ON p.passage_id=pr.passage_id "
            "WHERE p.start_ms=0 AND p.kind='radio'").fetchone()
        check(pr1["mbid"] == "rec-1", f"got {pr1['mbid']}")
        check(pr1["source"] == "musicbrainz", f"got {pr1['source']}")
        artist = c.execute(
            "SELECT * FROM recording_artists WHERE mbid='rec-1'").fetchone()
        check(artist is not None and artist["artist_mbid"] == "art-1",
              f"expected the real artist linked, got {artist}")
        check(c.execute("SELECT COUNT(*) FROM id_checks WHERE passage_id="
                         "(SELECT passage_id FROM passages WHERE start_ms=0 AND kind='radio')"
                         ).fetchone()[0] == 0,
              "a cleanly-resolved track must not land in the review queue")

        print()
        print("track 2: two Disc ID candidates -> placeholder + down-select in id_checks")
        pid2 = c.execute(
            "SELECT passage_id, mbid FROM passages p JOIN passage_recordings pr "
            "USING(passage_id) WHERE p.start_ms=60000 AND p.kind='radio'").fetchone()
        check(pid2["mbid"].startswith(f"local:audio:{MD5}:60000"), f"got {pid2['mbid']}")
        chk2 = c.execute(
            "SELECT * FROM id_checks WHERE passage_id=?", (pid2["passage_id"],)).fetchone()
        check(chk2 is not None, "an ambiguous track must get an id_checks row")
        check(chk2["verdict"] == "unmatched", f"got {chk2['verdict']}")
        suggested2 = json.loads(chk2["suggested"])
        check(len(suggested2) == 2, f"expected 2 down-select candidates, got {suggested2}")
        check({s["mbid"] for s in suggested2} == {"rec-2a", "rec-2b"}, f"got {suggested2}")

        print()
        print("track 3: CD-TEXT present -> placeholder + suggested, even with 0 MB candidates")
        pid3 = c.execute(
            "SELECT passage_id FROM passages WHERE start_ms=120000 AND kind='radio'"
        ).fetchone()["passage_id"]
        rec3 = c.execute(
            "SELECT r.title, r.source FROM recordings r JOIN passage_recordings pr "
            "USING(mbid) WHERE pr.passage_id=?", (pid3,)).fetchone()
        check(rec3["title"] == "CD-TEXT Song Three", f"got {rec3['title']}")
        check(rec3["source"] == "cd:text", f"got {rec3['source']}")
        chk3 = c.execute("SELECT * FROM id_checks WHERE passage_id=?", (pid3,)).fetchone()
        check(chk3 is not None and chk3["suggested"] is None,
              f"no MB candidates at this position, got {chk3}")

        print()
        print("track 4: nothing from Disc ID, AcoustID hits -> identified via cd:acoustid")
        rec4 = c.execute(
            "SELECT r.title, r.source FROM recordings r JOIN passage_recordings pr "
            "USING(mbid) JOIN passages p USING(passage_id) "
            "WHERE p.start_ms=180000 AND p.kind='radio'").fetchone()
        check(rec4["title"] == "Song Four", f"got {rec4}")
        check(rec4["source"] == "cd:acoustid", f"got {rec4}")

        print()
        print("track 5: nothing resolves at all -> bare placeholder, empty suggested")
        pid5 = c.execute(
            "SELECT passage_id FROM passages WHERE start_ms=240000 AND kind='radio'"
        ).fetchone()["passage_id"]
        chk5 = c.execute("SELECT * FROM id_checks WHERE passage_id=?", (pid5,)).fetchone()
        check(chk5 is not None and chk5["suggested"] is None, f"got {chk5}")
        rec5 = c.execute(
            "SELECT r.source FROM recordings r JOIN passage_recordings pr USING(mbid) "
            "WHERE pr.passage_id=?", (pid5,)).fetchone()
        check(rec5["source"] == "cd:unidentified", f"got {rec5}")

        print()
        print("both kinds share one identification per track")
        for start in (0, 60000, 120000, 180000, 240000):
            mbids = {r["mbid"] for r in c.execute(
                "SELECT pr.mbid FROM passage_recordings pr JOIN passages p USING(passage_id) "
                "WHERE p.start_ms=?", (start,))}
            check(len(mbids) == 1, f"start={start}: radio and album must agree, got {mbids}")

        print()
        print("one disc-level ingest_decisions row, stage='rip'")
        decisions = c.execute(
            "SELECT * FROM ingest_decisions WHERE audio_md5=? AND stage='rip'", (MD5,)
        ).fetchall()
        # One disc-level row plus zero verification-failure rows (rip_report=None).
        check(len(decisions) == 1, f"got {len(decisions)}")
        check(decisions[0]["outcome"] == "exact", f"got {decisions[0]['outcome']}")
        detail = json.loads(decisions[0]["detail"])
        check(detail["candidates"] == 2, f"got {detail}")

        c.close()
    finally:
        segment_dao.identify_recording = old_identify

    print()
    print("commit_rip: a track-level verification failure is recorded, not dropped")
    c = fixture()
    report = cd_toc.RipReport(
        tracks=[cd_toc.TrackRipReport(number=1, ok=False, detail="could not be verified"),
                cd_toc.TrackRipReport(number=2, ok=True, detail="accurately ripped")],
        all_ok=False)
    old_identify = segment_dao.identify_recording
    segment_dao.identify_recording = lambda *a, **k: None
    try:
        ingest_cd.commit_rip(c, "/rip", TOC, mp3_path, MD5, "none", [],
                              rip_report=report, acoustid_key=None)
        fails = c.execute(
            "SELECT * FROM ingest_decisions WHERE stage='rip' AND outcome='verification_failed'"
        ).fetchall()
        check(len(fails) == 1, f"exactly track 1 failed, got {len(fails)}")
        detail = json.loads(fails[0]["detail"])
        check(detail["track"] == 1, f"got {detail}")
        n_passages = c.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        check(n_passages == 10, f"a failed track is still written, not dropped, got {n_passages}")
        # [SPEC-HOLD-010]: the damaged track's radio passage is held back from
        # the Director at once -- its album passage, and the good track, not.
        held = c.execute("SELECT kind, start_ms, director_hold FROM passages "
                         "WHERE director_hold IS NOT NULL").fetchall()
        check(len(held) == 1 and held[0]["kind"] == "radio" and held[0]["start_ms"] == 0
              and held[0]["director_hold"] == "damaged rip: could not be verified",
              f"track 1's radio passage, and only it, is held, saying why: {[tuple(h) for h in held]}")
    finally:
        segment_dao.identify_recording = old_identify
        c.close()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("ingest_cd: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
