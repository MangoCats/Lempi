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
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT, sort_name TEXT, source TEXT);
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
         "artist-credit": [{"artist": {"id": "art-c", "name": "Artist C", "sort-name": "C, Artist"}}]}}
        for n in (1, 2, 3)]}]}
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
        # [SPEC-CDI-098]: and the disc is each recording's album -- what Browse
        # by Album names it by. None of this was written before 2026-10-01.
        links = c.execute("SELECT mbid, position, chosen, source FROM release_recordings "
                          "WHERE release_mbid='rel-C' ORDER BY position").fetchall()
        check([tuple(r) for r in links] == [(f"rec-c{n}", n, 1, "cd:import") for n in (1, 2, 3)]
              and r["album_linked"] == 3, f"each track linked to the disc, as chosen: {[tuple(x) for x in links]} {r}")
        check(c.execute("SELECT title FROM releases WHERE mbid='rel-C'").fetchone()[0] == "Release C",
              "the release, by its title")
        # [SPEC-SC-125]: and the rip's file is taken from it, whatever its
        # recordings' chosen releases.
        check(tuple(c.execute("SELECT release_mbid, source FROM file_releases WHERE audio_md5=?", (MD5,)).fetchone()
                    or ()) == ("rel-C", "cd:import"), "the file is recorded as taken from the disc")
        # [REQ-VIS-182]: the artist's sort name, from the same answer.
        check(c.execute("SELECT sort_name FROM artists WHERE mbid='art-c'").fetchone()[0] == "C, Artist",
              "the artist's sort name is kept")
    finally:
        c.close()


def test_record_release():
    """`record_release` links only the release's own recording at each place,
    and moves `chosen` off any other release that recording was on."""
    c = fixture()
    c.executescript("CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT, "
                    "source TEXT NOT NULL);"
                    "CREATE TABLE release_recordings (release_mbid TEXT NOT NULL, mbid TEXT NOT NULL, "
                    "position INTEGER, source TEXT NOT NULL, chosen INTEGER DEFAULT 0, "
                    "PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;"
                    "INSERT INTO releases VALUES ('rel-hits', 'Greatest Hits', '2010', 'musicbrainz');"
                    "INSERT INTO release_recordings (release_mbid, mbid, position, source, chosen) "
                    "VALUES ('rel-hits', 'rec-1', 7, 'musicbrainz', 1);")
    print("record_release: the disc's own recordings, chosen over a compilation")
    n = ingest_cd.record_release(c, RELEASES[0], [(1, "rec-1"), (2, "rec-elsewhere")])
    rows = {tuple(r) for r in c.execute("SELECT release_mbid, mbid, chosen FROM release_recordings")}
    check(n == 1 and rows == {("rel-hits", "rec-1", 0), ("rel-A", "rec-1", 1)},
          f"one linked, the compilation no longer chosen, a recording not on the disc there not linked: {n} {rows}")
    print("record_release: a second disc with the same recording links it, and leaves the first disc's choice")
    for _ in range(2):
        n2 = ingest_cd.record_release(c, RELEASES[1], [(1, "rec-1")])
        rows = {tuple(r) for r in c.execute("SELECT release_mbid, mbid, chosen FROM release_recordings")}
        check(n2 == 1 and rows == {("rel-hits", "rec-1", 0), ("rel-A", "rec-1", 1), ("rel-B", "rec-1", 0)},
              f"settled, however often it is run: {rows}")
    c.close()


def test_choose_release_leaves_a_disc_alone():
    """`choose_release.py` re-scores every recording -- but not one whose
    release a CD add recorded: the disc is the evidence it approximates."""
    import choose_release
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "lib.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA + "CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, album TEXT);"
                    "CREATE TABLE musicbrainz_cache (response TEXT);"
                    "CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT, "
                    "source TEXT NOT NULL, release_group TEXT, status TEXT, primary_type TEXT, "
                    "secondary_types TEXT, country TEXT, track_count INTEGER);"
                    "CREATE TABLE release_recordings (release_mbid TEXT NOT NULL, mbid TEXT NOT NULL, "
                    "position INTEGER, source TEXT NOT NULL, chosen INTEGER DEFAULT 0, "
                    "PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;"
                    "INSERT INTO files (file_id, audio_md5) VALUES (1, 'md5-x');"
                    "INSERT INTO passages (passage_id, file_id, kind) VALUES (1, 1, 'radio'), (2, 1, 'radio');"
                    "INSERT INTO passage_recordings VALUES (1, 'rec-disc', 1.0, 's'), (2, 'rec-free', 1.0, 's');")
    for rel, kind, src, chosen in (("rel-album", "Album", "musicbrainz", 0), ("rel-disc", "Album", "cd:import", 1)):
        c.execute("INSERT INTO releases VALUES (?, 'T', '1990', 'musicbrainz', ?, 'Official', ?, NULL, NULL, 10)",
                  (rel, rel, kind))
        for rec in ("rec-disc", "rec-free"):
            if rel == "rel-disc" and rec == "rec-free":
                continue
            c.execute("INSERT INTO release_recordings VALUES (?, ?, 1, ?, ?)",
                      (rel, rec, src if rec == "rec-disc" else "musicbrainz", chosen if rec == "rec-disc" else 0))
    # Linked to a release, used by no passage: passed over, not a crash (2026-10-02).
    c.execute("INSERT INTO release_recordings VALUES ('rel-album', 'rec-orphan', 2, 'musicbrainz', 0)")
    c.commit()
    c.close()
    old = sys.argv
    sys.argv = ["choose_release.py", db]
    try:
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            choose_release.main()
    finally:
        sys.argv = old
    c = sqlite3.connect(db)
    chosen = dict(c.execute("SELECT mbid, release_mbid FROM release_recordings WHERE chosen = 1"))
    c.close()
    print("choose_release: a disc's choice stands")
    check(chosen == {"rec-disc": "rel-disc", "rec-free": "rel-album"}, f"chosen afterwards: {chosen}")


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


IDLER = ("The Idler Wheel Is Wiser Than the Driver of the Screw and Whipping Cords Will Serve You "
         "More Than Ropes Will Ever Do")


def test_album_names_held_short():
    """[SPEC-NAM-010..020]: the setting, read as the player stores it, and an
    album name held to it at a word, never ending mid-thought."""
    print("album names: the setting, and the shortening")
    p = os.path.join(tempfile.mkdtemp(), "lempi.db")
    c = sqlite3.connect(p)
    c.executescript(SCHEMA + "CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);")
    c.commit()
    check(ingest_cd.album_name_max_setting(p) == 60, "unset: 60")
    for value, want in (("80", 80), ("5", 60), ("999", 60), ("many", 60)):
        c.execute("INSERT OR REPLACE INTO player_settings VALUES ('cd_album_name_max', ?1, 'now')", (value,))
        c.commit()
        got = ingest_cd.album_name_max_setting(p)
        check(got == want, f"stored {value!r}: {want}, got {got}")
    c.close()
    check(ingest_cd.album_name_max_setting(os.path.join(tempfile.mkdtemp(), "none.db")) == 60, "unreadable: 60")

    s = ingest_cd.shorten(IDLER, 60)
    check(s == "The Idler Wheel Is Wiser Than the Driver of the Screw", f"at a word, not on 'and': {s!r}")
    check(ingest_cd.shorten("Fetch the Bolt Cutters", 60) == "Fetch the Bolt Cutters", "a short name is left alone")
    check(ingest_cd.shorten("Hits (The Very Best of Everything Anyone Ever Recorded)", 30) == "Hits",
          "a parenthesis the cut left open is dropped")
    check(ingest_cd.shorten("A" * 80, 60) == "A" * 60, "one word too long is cut at the limit")
    check(ingest_cd.proposed_folder("Fiona Apple", IDLER, "2012", 60)
          == os.path.join("Fiona Apple", "The Idler Wheel Is Wiser Than the Driver of the Screw (2012)"),
          "the folder holds the album to the limit, then adds the year")


def _cueripper_rip(parent: str, album: str, wav_secs: int | None = None) -> str:
    """A rip shaped as CUERipper leaves one: a folder `Artist - Album` cut
    short, files named after it with `(Disc 1)`, and an `.accurip` cut
    shorter still -- the names *The Idler Wheel* arrived with, 2026-10-13."""
    import subprocess
    stem = f"Fiona Apple - {album}"
    folder = os.path.join(parent, stem)
    os.makedirs(folder)
    with open(os.path.join(folder, stem + "(Disc 1).cue"), "w", encoding="cp1252", newline="\r\n") as f:
        f.write(f'PERFORMER "Fiona Apple"\nTITLE "{IDLER}"\nFILE "{stem}(Disc 1).wav" WAVE\n'
                '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n')
    for name in (stem + "(Disc 1).log", stem + "(Dis.accurip", "folder.jpg"):
        with open(os.path.join(folder, name), "w") as f:
            f.write(name)
    wav = os.path.join(folder, stem + "(Disc 1).wav")
    if wav_secs:
        subprocess.run([ingest_cd.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=330:duration={wav_secs}", "-ar", "44100", "-ac", "2", wav], check=True)
    else:
        open(wav, "wb").close()
    return folder


def test_files_renamed_with_the_folder():
    """[SPEC-NAM-040..050]: the ripper's names follow the album folder, the CUE
    names what lies beside it, and a move that fails puts everything back."""
    print("move_rip: names follow the folder; a failed move is undone")
    rip = _cueripper_rip(tempfile.mkdtemp(), IDLER[:60])
    home = os.path.join(tempfile.mkdtemp(), "Fiona Apple", "The Idler Wheel (2012)")
    renames = ingest_cd.renamed_files(rip, home, "Fiona Apple", 60, ("x.mp3",))
    want = {f"Fiona Apple - {IDLER[:60]}(Disc 1).{e}": f"Fiona Apple - The Idler Wheel(Disc 1).{e}"
            for e in ("cue", "wav", "log")}
    want[f"Fiona Apple - {IDLER[:60]}(Dis.accurip"] = "Fiona Apple - The Idler Wheel(Disc 1).accurip"
    check(renames == want, f"renamed after the folder, the cut-short one made whole: {renames}")
    check(ingest_cd.renamed_files(rip, home, None, 60) .get(f"Fiona Apple - {IDLER[:60]}(Disc 1).cue")
          == "The Idler Wheel(Disc 1).cue", "with no artist known, the album alone")

    # Fail on the third file: what moved comes back, the folders made go.
    real, calls = ingest_cd.shutil.move, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 3:
            raise OSError("[WinError 3] The system cannot find the path specified")
        return real(src, dst)
    before = sorted(os.listdir(rip))
    ingest_cd.shutil.move = flaky
    try:
        ingest_cd.move_rip(rip, home, renames)
        check(False, "the failing move raises")
    except OSError:
        pass
    finally:
        ingest_cd.shutil.move = real
    check(sorted(os.listdir(rip)) == before, f"every file back in the inbox: {sorted(os.listdir(rip))}")
    check(not os.path.exists(os.path.dirname(home)), "and no empty album or artist folder left")

    ingest_cd.move_rip(rip, home, renames)
    names = sorted(os.listdir(home))
    check(names == sorted(list(want.values()) + ["folder.jpg"]), f"moved under the new names: {names}")
    check(not os.path.exists(rip), "the inbox folder is gone")
    cue = open(os.path.join(home, "Fiona Apple - The Idler Wheel(Disc 1).cue"), encoding="utf-8").read()
    check('FILE "Fiona Apple - The Idler Wheel(Disc 1).wav" WAVE' in cue, f"the CUE names the renamed WAV: {cue!r}")

    print("check_paths: a destination Windows cannot open is refused before anything moves")
    rip2 = _cueripper_rip(tempfile.mkdtemp(), IDLER[:60])
    deep = os.path.join(tempfile.mkdtemp(), "x" * 120, "y" * 60)
    if os.name == "nt":
        try:
            ingest_cd.check_paths(rip2, deep, {})
            check(False, "a 260-character path is refused")
        except ingest_cd.PathTooLong as e:
            check("Settings" in str(e) and "nothing was moved" in str(e), f"and says what to do: {e}")
    try:
        ingest_cd.check_paths(rip2, home, {"folder.jpg": "a", "Fiona Apple - " + IDLER[:60] + "(Disc 1).log": "a"})
        check(False, "two files given one name are refused")
    except ValueError as e:
        check("two files one name" in str(e), str(e))


def test_long_title_end_to_end():
    """[SPEC-NAM-010..050], with a real WAV and ffmpeg: *The Idler Wheel* as
    CUERipper left it, added into the folder the preview proposed. Its files
    follow the folder's name, the MP3 and its CUE share a stem -- how the
    player finds a sheet (`player/src/cue.rs`) -- and the catalogue names it."""
    if not ingest_cd.FFMPEG:
        check(False, "long title end to end needs ffmpeg")
        return
    print("long title end to end: preview proposes a short folder; the add files under it")
    rip = _cueripper_rip(tempfile.mkdtemp(), IDLER[:60], wav_secs=4)
    rel = {"id": "rel-I", "title": IDLER, "date": "2012-06-19", "artist-credit": [{"name": "Fiona Apple"}],
           "media": [{"tracks": [{"position": n, "title": f"Song {n}", "length": 2000,
                                  "recording": {"id": f"rec-i{n}", "title": f"Song {n}"}} for n in (1, 2)]}]}
    db = os.path.join(tempfile.mkdtemp(), "lempi.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA + "CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);")
    c.commit()
    c.close()
    old = ingest_cd.lookup_disc_id
    ingest_cd.lookup_disc_id = lambda toc: ("exact", [rel])
    try:
        p = ingest_cd.do_ingest(db, rip, commit=False)
        want = os.path.join("Fiona Apple", "The Idler Wheel Is Wiser Than the Driver of the Screw (2012)")
        check(p["folder"] == want and p["album_name_max"] == 60, f"the proposed folder: {p['folder']!r}")
        home = os.path.join(tempfile.mkdtemp(), p["folder"])
        r = ingest_cd.do_ingest(db, rip, commit=True, release="rel-I", into=home, keep_flac=False)
        stem = "Fiona Apple - The Idler Wheel Is Wiser Than the Driver of the Screw(Disc 1)"
        names = sorted(os.listdir(home))
        check(names == sorted([stem + e for e in (".accurip", ".cue", ".log", ".mp3")] + ["folder.jpg"]),
              f"every file named after the folder, the WAV gone: {names}")
        cue = open(os.path.join(home, stem + ".cue"), encoding="utf-8").read()
        check(f'FILE "{stem}.mp3" MP3' in cue, f"the CUE names the MP3 beside it: {cue!r}")
        c = sqlite3.connect(db)
        path = c.execute("SELECT path FROM files").fetchone()[0]
        c.close()
        check(path == os.path.join(home, stem + ".mp3") and r["identified"] == 2, f"catalogued as filed: {path!r}")
    finally:
        ingest_cd.lookup_disc_id = old


def _two_disc_release(rid="rel-W", lengths2=(4000, 4000)):
    """A two-disc set shaped as MusicBrainz answers a Disc ID: every disc,
    each naming the Disc IDs it is known by -- Woodstock's, 2026-10-14."""
    def medium(pos, disc_id, lengths):
        return {"position": pos, "format": "CD", "track-count": len(lengths), "discs": [{"id": disc_id}],
                "tracks": [{"position": n, "number": str(n), "title": f"D{pos} Song {n}", "length": ms,
                            "recording": {"id": f"rec-d{pos}-{n}", "title": f"D{pos} Song {n}",
                                          "artist-credit": [{"artist": {"id": "art-w", "name": "Artist W"}}]}}
                           for n, ms in enumerate(lengths, 1)]}
    return {"id": rid, "title": "Two Discs", "date": "1999", "artist-credit": [{"name": "Artist W"}],
            "media": [medium(1, "disc-one-id", (2000, 2000)), medium(2, "disc-two-id", lengths2)]}


def test_which_disc():
    """[SPEC-CDI-099]: a track's number means its place on the disc ripped,
    not on the set's first disc."""
    print("which disc of a set: by Disc ID, by track count, by track lengths")
    toc2 = cd_toc.DiscToc(tracks=[cd_toc.TocTrack(number=1, start_ms=0, end_ms=4000, start_sector=0, end_sector=0),
                                  cd_toc.TocTrack(number=2, start_ms=4000, end_ms=8000, start_sector=0, end_sector=0)],
                          leadout_sector=0, source="eac-cue")
    rel = _two_disc_release()
    check(ingest_cd.disc_medium(rel, "disc-two-id", toc2)["position"] == 2, "the Disc ID names the second disc")
    check(ingest_cd.disc_medium(rel, None, toc2)["position"] == 2,
          "two discs of two tracks each: the one whose lengths fit")
    three = cd_toc.DiscToc(tracks=TRACKS[:3], leadout_sector=0, source="eac-cue")
    lopsided = dict(rel, media=[rel["media"][0], dict(rel["media"][1], **{"track-count": 3})])
    check(ingest_cd.disc_medium(lopsided, None, three)["position"] == 2, "the only disc with three tracks")
    narrowed = ingest_cd.for_this_disc([rel], "disc-two-id", toc2)
    check(len(narrowed[0]["media"]) == 1 and narrowed[0]["_track_total"] == 4,
          f"cut to that disc, the set's size kept: {narrowed[0].get('_track_total')}")
    check(ingest_cd._track_at_position(narrowed[0], 1)["recording"]["id"] == "rec-d2-1",
          "and track 1 is the second disc's first song")
    check(ingest_cd._track_at_position(rel, 1, "rec-d2-1")["recording"]["id"] == "rec-d2-1",
          "a whole release, asked with the recording, finds it on its own disc")
    check(ingest_cd.for_this_disc([rel], None, cd_toc.DiscToc(tracks=[], leadout_sector=0, source="eac-cue")) == [],
          "a set whose disc cannot be told is left out, not guessed")
    check(ingest_cd.disc_of_set(narrowed) == (2, 2), f"disc 2 of 2: {ingest_cd.disc_of_set(narrowed)}")
    check(ingest_cd.disc_of_set(narrowed * 2) is None, "not while the edition is still a choice")
    single = ingest_cd.for_this_disc([dict(rel, media=rel["media"][:1])], None, toc2)
    check(ingest_cd.disc_of_set(single) is None, "and not for a single disc")

    print("set_summary: which disc, and what of the rest the library holds [SPEC-RIP-110]")
    c = fixture()
    c.execute("INSERT INTO passage_recordings (passage_id, mbid) VALUES (1, 'rec-d1-1')")
    s = ingest_cd.set_summary(narrowed[0], c)
    check(s["disc"] == 2 and s["discs"] == 2, f"disc 2 of 2: {s}")
    check([(d["number"], d["recordings"], d["held"]) for d in s["set"]] == [(1, 2, 1), (2, 2, 0)],
          f"disc 1 half held, disc 2 none: {s['set']}")
    check(ingest_cd.set_summary(single[0], c) == {}, "a single disc says nothing of a set")

    c = fixture()
    c.execute("CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT, "
              "source TEXT NOT NULL, track_count INTEGER)")
    n = ingest_cd.record_release(c, narrowed[0], [(1, "rec-d2-1"), (2, "rec-d2-2")])
    rows = c.execute("SELECT mbid, position, disc FROM release_recordings ORDER BY position").fetchall()
    check(n == 2 and [tuple(r) for r in rows] == [("rec-d2-1", 1, 2), ("rec-d2-2", 2, 2)],
          f"linked as disc 2's tracks: {[tuple(r) for r in rows]}")
    check(c.execute("SELECT track_count FROM releases").fetchone()[0] == 4, "the release keeps the set's track count")
    # A row a release fetch wrote first, with no place, takes the disc's.
    c.execute("DELETE FROM release_recordings")
    c.execute("INSERT INTO release_recordings (release_mbid, mbid, source) VALUES ('rel-W', 'rec-d2-1', 'musicbrainz')")
    ingest_cd.record_release(c, narrowed[0], [(1, "rec-d2-1")])
    row = tuple(c.execute("SELECT position, disc, chosen, source FROM release_recordings").fetchone())
    check(row == (1, 2, 1, "cd:import"), f"a placeless row already there takes this disc's place: {row}")
    # The backfill's path: a whole release and the recordings, no disc.
    c.execute("DELETE FROM release_recordings")
    n = ingest_cd.record_release(c, rel, [(1, "rec-d2-1")])
    check(n == 1 and c.execute("SELECT disc FROM release_recordings").fetchone()[0] == 2,
          "a whole release links a second-disc recording to its own disc")


def test_disc_in_the_names():
    """[SPEC-RIP-108]: one disc of a set names every file with its disc, in
    place of whatever disc the ripper wrote, and keeps a cover already there."""
    print("renamed_files: a disc of a set carries its disc in every name")
    rip = _cueripper_rip(tempfile.mkdtemp(), "Two Discs")
    home = os.path.join(tempfile.mkdtemp(), "Fiona Apple", "Two Discs (1999)")
    got = ingest_cd.renamed_files(rip, home, "Fiona Apple", 60, ("x.mp3",), disc=2)
    stem = "Fiona Apple - Two Discs"
    want = {f"{stem}(Disc 1).{e}": f"{stem} (Disc 2).{e}" for e in ("cue", "wav", "log")}
    want[f"{stem}(Dis.accurip"] = f"{stem} (Disc 2).accurip"
    want["x.mp3"] = "x (Disc 2).mp3"
    check(got == want, f"the ripper's (Disc 1) replaced by the set's disc, the cover kept: {got}")
    os.makedirs(home)
    open(os.path.join(home, "folder.jpg"), "w").close()
    got = ingest_cd.renamed_files(rip, home, "Fiona Apple", 60, disc=2)
    check(got.get("folder.jpg") == "folder (Disc 2).jpg", f"a cover the folder has already is not replaced: {got}")
    check(f"{stem}(Disc 1).cue" not in ingest_cd.renamed_files(rip, home, "Fiona Apple", 60),
          "a single disc is named as before")

    tracks = tempfile.mkdtemp()
    for n in ("01. One.wav", "02. Two.wav", "Artist - Album.cue", "Artist - Album.log", "2-03. Three.wav"):
        open(os.path.join(tracks, n), "w").close()
    got = ingest_cd.renamed_files(tracks, home, "Artist", 60, ("01. One.mp3",), disc=2)
    check(got == {"01. One.wav": "2-01. One.wav", "02. Two.wav": "2-02. Two.wav", "01. One.mp3": "2-01. One.mp3",
                  "Artist - Album.cue": "Artist - Album (Disc 2).cue",
                  "Artist - Album.log": "Artist - Album (Disc 2).log"},
          f"tracks mode: 2-01, the sheet and log (Disc 2), a name with its disc left alone: {got}")


def test_second_disc_end_to_end():
    """[SPEC-CDI-099], with a real WAV and ffmpeg: the second disc of a set,
    ripped and added, is catalogued as the second disc's songs -- Woodstock's
    was not, 2026-10-06."""
    import subprocess
    if not ingest_cd.FFMPEG:
        check(False, "second disc end to end needs ffmpeg")
        return
    print("second disc end to end: the songs of the disc in hand")
    inbox = tempfile.mkdtemp()
    subprocess.run([ingest_cd.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                    "-ar", "44100", "-ac", "2", os.path.join(inbox, "W - Two Discs(Disc 2).wav")], check=True)
    with open(os.path.join(inbox, "W - Two Discs(Disc 2).cue"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write('PERFORMER "W"\nTITLE "Two Discs"\nFILE "W - Two Discs(Disc 2).wav" WAVE\n'
                '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 00:04:00\n')
    rel = _two_disc_release()
    db = os.path.join(tempfile.mkdtemp(), "lempi.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA + "CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);")
    c.commit()
    c.close()
    old = (ingest_cd.lookup_disc_id, cd_toc.musicbrainz_disc_id)
    ingest_cd.lookup_disc_id = lambda toc: ("exact", [rel])
    cd_toc.musicbrainz_disc_id = lambda toc: "disc-two-id"
    try:
        p = ingest_cd.do_ingest(db, inbox, commit=False)
        titles = [t["title"] for t in p["releases"][0]["tracks"]]
        check(titles == ["D2 Song 1", "D2 Song 2"], f"the preview lists the second disc: {titles}")
        s = p["releases"][0]
        check((s.get("disc"), s.get("discs"), [d["held"] for d in s.get("set", [])]) == (2, 2, [0, 0]),
              f"and says disc 2 of 2, neither held [SPEC-RIP-110]: {s.get('set')}")
        # The first disc is filed already, as CUERipper named it, with its cover.
        home = os.path.join(tempfile.mkdtemp(), "W", "Two Discs (1999)")
        os.makedirs(home)
        first = ["W - Two Discs(Disc 1).cue", "W - Two Discs(Disc 1).mp3", "folder.jpg"]
        for n in first:
            open(os.path.join(home, n), "w").close()
        open(os.path.join(inbox, "folder.jpg"), "w").close()
        r = ingest_cd.do_ingest(db, inbox, commit=True, release="rel-W", into=home, keep_flac=False)
        names = sorted(os.listdir(home))
        check(names == sorted(first + ["W - Two Discs (Disc 2).cue", "W - Two Discs (Disc 2).mp3",
                                       "folder (Disc 2).jpg"]),
              f"filed beside the first disc, every name its own [SPEC-RIP-108]: {names}")
        cue = open(os.path.join(home, "W - Two Discs (Disc 2).cue"), encoding="utf-8").read()
        check('FILE "W - Two Discs (Disc 2).mp3" MP3' in cue, f"the sheet names its own MP3: {cue!r}")
        c = sqlite3.connect(db)
        got = c.execute("SELECT r.title FROM passages p JOIN passage_recordings pr ON pr.passage_id = p.passage_id "
                        "JOIN recordings r ON r.mbid = pr.mbid WHERE p.kind = 'radio' ORDER BY p.start_ms").fetchall()
        discs = c.execute("SELECT DISTINCT disc FROM release_recordings").fetchall()
        c.close()
        check([g[0] for g in got] == ["D2 Song 1", "D2 Song 2"] and r["identified"] == 2,
              f"catalogued as the second disc's songs: {got}")
        check(discs == [(2,)], f"and linked as disc 2: {discs}")
    finally:
        ingest_cd.lookup_disc_id, cd_toc.musicbrainz_disc_id = old


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
        check("rel-E" in COVERS_ASKED and (r.get("cover") or {}).get("fronts") == 1,
              f"the disc's cover asked for, and its front kept: {r.get('cover')} {COVERS_ASKED}")
        cue = open(os.path.join(home, "Artist - Album.cue"), encoding="utf-8").read()
        check('FILE "Artist - Album.mp3" MP3' in cue, f"the CUE names the MP3: {cue!r}")
        c = sqlite3.connect(db)
        path, sha = c.execute("SELECT path, sha256 FROM files").fetchone()
        c.close()
        check(path == os.path.join(home, "Artist - Album.mp3"), f"the catalogue names the album's home, got {path!r}")
        from byte_hash import sha256_file
        check(sha == sha256_file(path), f"and records its byte hash, as folder induction does [REQ-AND-960]: {sha}")

        # [SPEC-CDI-047]: the same edition again -- a different rip, so the
        # audio hash cannot catch it -- is refused before anything is encoded,
        # and added only when asked.
        print("end to end: a second copy of a whole edition, refused unless asked")
        again = tempfile.mkdtemp()
        subprocess.run([ingest_cd.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=550:duration=4",
                        "-ar", "44100", "-ac", "2", os.path.join(again, "Artist - Album.wav")], check=True)
        with open(os.path.join(again, "Artist - Album.cue"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write('PERFORMER "Artist"\nTITLE "Album"\nFILE "Artist - Album.wav" WAVE\n'
                    '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n')
        p2 = ingest_cd.do_ingest(db, again, commit=False)
        check(p2["releases"][0]["duplicate"] is True and p2["releases"][0]["in_library"] == 2,
              f"the preview says every track is held: {p2['releases'][0]}")
        before = snapshot(again)
        home2 = os.path.join(tempfile.mkdtemp(), "Artist", "Album (2001) 2")
        try:
            ingest_cd.do_ingest(db, again, commit=True, release="rel-E", into=home2, keep_flac=False)
            check(False, "a whole edition already held is refused")
        except ingest_cd.AlreadyHeld as e:
            check("Add it again" in str(e) and "2 of 2" in str(e), f"and says how to add it anyway: {e}")
        check(snapshot(again) == before and not os.path.exists(home2),
              "refused before anything was encoded or moved -- no MP3 left behind")
        r2 = ingest_cd.do_ingest(db, again, commit=True, release="rel-E", into=home2, keep_flac=False,
                                 allow_duplicate=True)
        check(r2["identified"] == 2 and sorted(os.listdir(home2)) == ["Artist - Album.cue", "Artist - Album.mp3"],
              f"asked for, the second copy is added: {r2}")
        c = sqlite3.connect(db)
        check(c.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2, "two copies, as asked")
        c.close()

        print("a partial overlap -- a compilation sharing a song -- is a note, not a refusal")
        c = sqlite3.connect(db)
        part = dict(rel, media=[{"tracks": [rel["media"][0]["tracks"][0],
                                            {"position": 2, "recording": {"id": "rec-new", "title": "New"}}]}])
        check(ingest_cd.held_of_edition(c, part, [1, 2]) == (1, 2), "one of two held")
        ingest_cd.refuse_duplicate(c, [part], [1, 2], allow=False)          # does not raise
        ingest_cd.refuse_duplicate(c, [rel, part], [1, 2], allow=False)     # several editions: the person picks
        c.close()
    finally:
        ingest_cd.lookup_disc_id = old


def test_tracks_end_to_end():
    """[SPEC-CDI-090..094], with real WAVs and ffmpeg: a tracks-mode rip --
    a file per track, track 3 never written, track 2 damaged -- previewed
    without a change on disk, then added: each track its own MP3 with one
    radio and one album passage, the album passage the whole file and the
    radio one from the track's own INDEX 01; the missing track recorded; the
    damaged one held; the disc looked up by the log's table of contents."""
    import hashlib
    import subprocess
    import passage_hold
    if not ingest_cd.FFMPEG:
        check(False, "tracks end to end needs ffmpeg")
        return
    inbox = tempfile.mkdtemp()
    for name, secs in (("01. One.wav", 3), ("02. Two.wav", 2)):
        subprocess.run([ingest_cd.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={secs}", "-ar", "44100", "-ac", "2",
                        os.path.join(inbox, name)], check=True)
    # Track 2's file opens with its own half-second gap (INDEX 00 at 0,
    # INDEX 01 at 0:00:37): the album passage keeps it, the radio one starts
    # after it. Track 3's file was never written.
    with open(os.path.join(inbox, "Artist - Album.cue"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write('PERFORMER "Artist"\nTITLE "Album"\nCATALOG 0075678341427\n'
                'FILE "01. One.wav" WAVE\n  TRACK 01 AUDIO\n    TITLE "One"\n    INDEX 01 00:00:00\n'
                'FILE "02. Two.wav" WAVE\n  TRACK 02 AUDIO\n    TITLE "Two"\n    INDEX 00 00:00:00\n'
                '    INDEX 01 00:00:37\n'
                'FILE "03. Three.wav" WAVE\n  TRACK 03 AUDIO\n    TITLE "Three"\n    INDEX 01 00:00:00\n')
    with open(os.path.join(inbox, "Artist - Album.log"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("        1  |  0:00.00 |  0:03.00 |         0    |      224   \n"
                "        2  |  0:03.00 |  0:02.37 |       225    |      411   \n"
                "        3  |  0:05.37 |  0:04.00 |       412    |      711   \n\n"
                "AccurateRip summary\n\n"
                "Track  1  accurately ripped (confidence 12)  [0A0B0C0D]\n"
                "Track  2  cannot be verified as accurate (confidence 30)  [11111111], AccurateRip returned [2]\n")
    rel = {"id": "rel-T", "title": "Album", "date": "2001", "artist-credit": [{"name": "Artist"}],
           "media": [{"tracks": [{"position": n, "title": f"Song {n}", "length": 3000,
                                  "recording": {"id": f"rec-t{n}", "title": f"Song {n}"}} for n in (1, 2, 3)]}]}
    db = os.path.join(tempfile.mkdtemp(), "lempi.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA + "CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);"
                    "CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT, characteristic TEXT, class TEXT, "
                    "value REAL, source TEXT, accuracy REAL, "
                    "PRIMARY KEY (subject_kind, subject_id, characteristic, class));")
    c.commit()
    c.close()

    def snapshot(d):
        return {n: hashlib.sha256(open(os.path.join(d, n), "rb").read()).hexdigest() for n in sorted(os.listdir(d))}

    looked = []
    old = (ingest_cd.lookup_disc_id, ingest_cd.lookup_barcode)
    ingest_cd.lookup_disc_id = lambda toc: (looked.append(("disc", toc.leadout_sector, toc.source)) or ("exact", [rel]))
    ingest_cd.lookup_barcode = lambda code: (looked.append(("barcode", code)) or ("barcode", [rel]))
    try:
        print("tracks mode end to end: preview, then commit")
        before = snapshot(inbox)
        p = ingest_cd.do_ingest(db, inbox, commit=False)
        check(snapshot(inbox) == before, "the preview changes nothing on disk")
        check(p["mode"] == "tracks" and p["tracks"] == 3 and p["missing"] == [{"number": 3, "title": "Three"}]
              and p["audio"] == "2 of 3 track files", f"it shows the rip as it is, got {p}")
        check(looked and looked[0] == ("disc", 712, "log-toc") and p["disc_id"],
              f"the disc is looked up by the log's table of contents, leadout and all: {looked}")
        check(p["rip"]["tracks"][1]["ok"] is False, f"and track 2's verdict is shown: {p['rip']}")

        home = os.path.join(tempfile.mkdtemp(), "Artist", "Album (2001)")
        r = ingest_cd.do_ingest(db, inbox, commit=True, release="rel-T", into=home, keep_flac=False,
                                occasions=("christmas",))
        check(r.get("occasions") == {"christmas": 2}, f"both tracks marked Christmas [SPEC-CDI-096]: {r.get('occasions')}")
        names = sorted(os.listdir(home))
        check(names == ["01. One.mp3", "02. Two.mp3", "Artist - Album.cue", "Artist - Album.log"],
              f"a MP3 per track, filed, WAVs removed, got {names}")
        check(r["tracks"] == 2 and r["identified"] == 2 and r["missing"] == [3] and r["verification_failed"] == 1
              and r.get("wav_removed"), f"got {r}")
        cue = open(os.path.join(home, "Artist - Album.cue"), encoding="utf-8").read()
        check('FILE "01. One.mp3" MP3' in cue and 'FILE "02. Two.mp3" MP3' in cue and ".wav" not in cue,
              f"every FILE line names its MP3: {cue!r}")

        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        files = c.execute("SELECT file_id, path, duration_ms, sha256 FROM files ORDER BY file_id").fetchall()
        from byte_hash import sha256_file
        check(all(f["sha256"] == sha256_file(f["path"]) for f in files),
              "each track's byte hash recorded [REQ-AND-960]")
        check([os.path.basename(f["path"]) for f in files] == ["01. One.mp3", "02. Two.mp3"]
              and all(os.path.dirname(f["path"]) == home for f in files), f"{[tuple(f) for f in files]}")
        ps = c.execute("SELECT f.path, p.kind, p.start_ms, p.end_ms, p.director_hold FROM passages p "
                       "JOIN files f USING (file_id) ORDER BY f.file_id, p.kind").fetchall()
        two = {(row["kind"]): row for row in ps if row["path"].endswith("02. Two.mp3")}
        check(len(ps) == 4 and two["album"]["start_ms"] == 0 and two["radio"]["start_ms"] == 493
              and two["album"]["end_ms"] == two["radio"]["end_ms"] == files[1]["duration_ms"],
              f"one radio and one album passage a track; the album whole, the radio from INDEX 01: "
              f"{[tuple(x) for x in ps]}")
        check(two["album"]["director_hold"] and two["radio"]["director_hold"]
              and not any(row["director_hold"] for row in ps if row["path"].endswith("01. One.mp3")),
              "the damaged track's two passages held, and only they")
        mbids = [row[0] for row in c.execute("SELECT pr.mbid FROM passage_recordings pr JOIN passages p "
                                             "USING (passage_id) WHERE p.kind='radio' ORDER BY p.file_id")]
        check(mbids == ["rec-t1", "rec-t2"], f"each track its release's recording: {mbids}")
        dec = [(row["outcome"], json.loads(row["detail"])) for row in
               c.execute("SELECT outcome, detail FROM ingest_decisions ORDER BY decision_id")]
        check(any(o == "track_missing" and d == {"track": 3, "title": "Three"} for o, d in dec),
              f"the missing track is recorded: {dec}")
        check(any(o == "verification_failed" and d.get("track") == 2 and d.get("in_file") == 1 for o, d in dec),
              f"the damage, at the file's own first passage: {dec}")
        check(sum(1 for o, d in dec if o == "chosen" and d.get("tracks_mode")) == 2, f"a decision per file: {dec}")
        check(passage_hold.damaged(c) == [], "nothing left for --damaged: both passages already held")
        # [SPEC-CDI-047]: a tracks rip is compared on the tracks it has -- the
        # same two again are a whole duplicate, though the edition has three.
        check(ingest_cd.held_of_edition(c, rel, [1, 2]) == (2, 2)
              and ingest_cd.held_of_edition(c, rel, [1, 2, 3]) == (2, 3),
              "held counted over the tracks this rip would add")
        c.close()

        print("which lookup: the sheet for an image, the log for tracks, the barcode without it")
        looked.clear()
        img = cd_toc.DiscToc(tracks=[cd_toc.TocTrack(1, 0, 1000, 0, 74, file="a.wav")], leadout_sector=75,
                             barcode="0075678341427")
        ingest_cd.identify_disc(img, None)
        check(looked == [("disc", 75, "")], f"an image: its own sheet: {looked}")
        looked.clear()
        trk = cd_toc.parse_eac_cue(os.path.join(home, "Artist - Album.cue"))
        ingest_cd.lookup_disc_id = lambda toc: (looked.append(("disc", toc.leadout_sector, toc.source)) or ("none", []))
        out = ingest_cd.identify_disc(trk, None)
        check(looked == [("barcode", "0075678341427")] and out[0] == "barcode" and out[2] is None,
              f"tracks with no log: no disc id at all, the barcode instead: {looked} {out[:1]}")
    finally:
        ingest_cd.lookup_disc_id, ingest_cd.lookup_barcode = old


def test_occasions():
    """[SPEC-CDI-096]: the disc's title suggests Christmas or children's;
    ticked, every recording on the disc is marked fully that occasion, as
    MuLibPlay's tagging was -- and a mark already there is left as it is."""
    print("occasions: guessed from the title, then every recording marked")
    g = ingest_cd.guess_occasions
    check(g("Now That’s What I Call Christmas! 3") == ["christmas"], g("Now That’s What I Call Christmas! 3"))
    check(g("Merry Christmas… Have a Nice Life!") == ["christmas"], "Cyndi Lauper's Christmas album")
    check(g("A Very Special X-Mas") == ["christmas"] and g("Joyeux Noël") == ["christmas"], "other spellings")
    check(g("Kidz Bop 5") == ["childrens"] and g("Songs for Children") == ["childrens"]
          and g("Lullabies for Little Ones") == ["childrens"], "children's titles")
    check(g("Sugar Ray") == [] and g("...Baby One More Time") == [] and g("Kidnapped") == [],
          "an ordinary title suggests nothing -- not even 'Baby', or 'Kid' inside a word")
    check(g(None, "", "Christmas Hits") == ["christmas"], "any of the titles given")

    c = fixture()
    c.execute("CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT, characteristic TEXT, class TEXT, "
              "value REAL, source TEXT, accuracy REAL, PRIMARY KEY (subject_kind, subject_id, characteristic, class))")
    c.execute("INSERT INTO flavor VALUES ('recording','rec-old','user.christmas','christmasy',1.0,'inherited:mulib',NULL)")
    c.execute("INSERT INTO flavor VALUES ('recording','rec-old','user.christmas','not_christmasy',0.0,'inherited:mulib',NULL)")
    got = ingest_cd.mark_occasions(c, ["rec-new", "rec-old", "rec-new", None], ["christmas"])
    rows = {(r["subject_id"], r["class"]): (r["value"], r["source"]) for r in
            c.execute("SELECT subject_id, class, value, source FROM flavor")}
    check(got == {"christmas": 2}, f"two recordings, a duplicate and a blank skipped: {got}")
    check(rows[("rec-new", "christmasy")] == (1.0, "cd:import") and rows[("rec-new", "not_christmasy")] == (0.0, "cd:import"),
          f"both classes of the pair, as the inherited tagging has them: {rows}")
    check(rows[("rec-old", "christmasy")] == (1.0, "inherited:mulib"), f"an equal mark is left as it was: {rows}")
    try:
        ingest_cd.mark_occasions(c, ["x"], ["easter"])
        check(False, "an unknown occasion is refused")
    except ValueError:
        pass

    mp3 = os.path.join(tempfile.mkdtemp(), "dao.mp3")
    with open(mp3, "wb") as f:
        f.write(b"\0" * 128)
    rel = {"id": "rel-X", "media": [{"tracks": [{"position": n, "recording": {"id": f"rec-x{n}", "title": f"S{n}"}}
                                                for n in (1, 2)]}]}
    r = ingest_cd.commit_rip(c, "/rip", cd_toc.DiscToc(tracks=TRACKS[:2], leadout_sector=0, source="eac-cue"),
                             mp3, "md5-xmas", "exact", [rel], None, None, chosen=True,
                             occasions=("christmas", "childrens"))
    marked = sorted((r2["subject_id"], r2["characteristic"]) for r2 in c.execute(
        "SELECT subject_id, characteristic FROM flavor WHERE class IN ('christmasy','for_children') "
        "AND subject_id LIKE 'rec-x%'"))
    check(r["occasions"] == {"christmas": 2, "childrens": 2} and marked == [
        ("rec-x1", "user.childrens"), ("rec-x1", "user.christmas"),
        ("rec-x2", "user.childrens"), ("rec-x2", "user.christmas")], f"commit_rip marks the disc: {r} {marked}")
    c.close()


# [SPEC-COV-060]: an add asks the archive for its disc's cover. Faked for the
# whole suite -- no test reaches the network -- and what was asked, kept.
COVERS_ASKED = []
ingest_cd.fetch_cover_art.covers = (
    lambda mbid, group=False: COVERS_ASKED.append(mbid) or (bytes([0xFF, 0xD8, 0xFF]) + b"f" * 400, None))
ingest_cd.fetch_cover_art.time.sleep = lambda s: None


def main() -> int:
    test_end_to_end()
    test_tracks_end_to_end()
    test_occasions()
    test_chosen_edition()
    test_record_release()
    test_choose_release_leaves_a_disc_alone()
    test_preview_pieces()
    test_files_on_disk()
    test_keep_lossless_setting()
    test_album_names_held_short()
    test_files_renamed_with_the_folder()
    test_long_title_end_to_end()
    test_which_disc()
    test_disc_in_the_names()
    test_second_disc_end_to_end()
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
        # [SPEC-HOLD-030]: both passages of the damaged track are held at once
        # -- radio, so the Director never chooses it; album, so a whole-album
        # play skips or replaces it -- and the good track's are not.
        held = c.execute("SELECT kind, start_ms, director_hold FROM passages "
                         "WHERE director_hold IS NOT NULL ORDER BY kind").fetchall()
        check([(h["kind"], h["start_ms"], h["director_hold"]) for h in held]
              == [("album", 0, "damaged rip: could not be verified"),
                  ("radio", 0, "damaged rip: could not be verified")],
              f"track 1's two passages, and only they, are held, saying why: {[tuple(h) for h in held]}")
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
