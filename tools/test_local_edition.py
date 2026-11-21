#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `local_edition.py`, an album MusicBrainz does not have `[SPEC-FIN-080..095]`.

A library built from `sql/schema.sql`, holding a folder as `ingest_folder.py`
leaves one -- each file's two passages on a `local:audio:` placeholder -- with
a `cover.jpg` beside it. Checked: what a local edition writes, and what it
leaves alone (a MusicBrainz recording, the passages' own links, which keep a
self-published track out of the review queue); naming it again corrects the
same release; an artist the library has is the library's; the refusals; and
that the Finish page then reads the folder as named, the archive is never
asked about it, and the index no longer lists it.

    python tools/test_local_edition.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_cover_art  # noqa: E402
import finish_folder as ff  # noqa: E402
import local_edition as le  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(HERE), "sql", "schema.sql")
MB_REC = "11111111-1111-1111-1111-111111111111"
MB_ARTIST = "22222222-2222-2222-2222-222222222222"
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def md5(n: int) -> str:
    return f"{n:032x}"


def library(tmp: str, name: str, mb_file: bool = False, cover: bool = True) -> tuple[str, str]:
    """A library and an album folder of three tracks, the second one tagged
    with its featured singer; with `mb_file`, a fourth file a MusicBrainz
    recording already names."""
    folder = os.path.join(tmp, name, "Future Sound")
    os.makedirs(folder)
    if cover:
        with open(os.path.join(folder, "cover.jpg"), "wb") as fh:
            fh.write(b"\xff\xd8\xff" + b"c" * 600)
    db = os.path.join(tmp, f"{name}.db")
    c = sqlite3.connect(db)
    with open(SCHEMA, encoding="utf-8") as fh:
        c.executescript(fh.read())
    rows = [(1, "01 Big Dreams.mp3", "Big Dreams", "stellarmusic", 1),
            (2, "02 In Your Eyes.mp3", "In Your Eyes (feat. Laura Aiisha)", "stellarmusic", 2),
            (3, "03 Power Intro.mp3", "Power Intro", "stellarmusic", 3)]
    if mb_file:
        rows.append((4, "04 Known.mp3", "Known Song", "Someone", 4))
    for fid, fname, title, artist, no in rows:
        c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, first_seen, "
                  "last_seen) VALUES (?1, ?2, ?3, 1, 1, 'mp3', ?4, '2026-11-20T10:00:00', '2026-11-20T10:00:00')",
                  (fid, md5(fid), os.path.join(folder, fname), 200_000 + fid))
        c.execute("INSERT INTO file_tags (file_id, title, artist, album, track_no, has_art, scanned_at) "
                  "VALUES (?1, ?2, ?3, 'Future Sound', ?4, 0, 0)", (fid, title, artist, no))
        rec = MB_REC if fid == 4 else f"local:audio:{md5(fid)}"
        src = "musicbrainz" if fid == 4 else "local:ingest"
        c.execute("INSERT OR IGNORE INTO recordings (mbid, title, length_ms, source) VALUES (?1, ?2, 1, ?3)",
                  (rec, title, src))
        for pid, kind in ((fid * 10, "radio"), (fid * 10 + 1, "album")):
            c.execute("INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, boundary_src) "
                      "VALUES (?1, ?2, ?3, 0, 200000, 'ingest:whole-file')", (pid, fid, kind))
            c.execute("INSERT INTO passage_recordings (passage_id, mbid, weight, source) VALUES (?1, ?2, 1.0, ?3)",
                      (pid, rec, src))
    c.commit()
    c.close()
    return db, folder


def run(db: str, folder: str, spec: dict, tmp: str, commit: bool = True) -> tuple[int, dict]:
    """The tool as the job runs it: a spec file, --json, its last line read."""
    path = os.path.join(tmp, "spec.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    argv = [sys.executable, os.path.join(HERE, "local_edition.py"), db, folder, "--spec", path, "--json"]
    r = subprocess.run(argv + (["--commit"] if commit else []), capture_output=True, text=True, encoding="utf-8")
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    return r.returncode, json.loads(lines[-1]) if lines else {"stdout": r.stdout, "stderr": r.stderr}


def test_from_the_tags(tmp: str) -> None:
    print("named from the album's fields and the tags: release, artist, credits, places, cover")
    db, folder = library(tmp, "tags")
    code, out = run(db, folder, {"title": "Future Sound: Stellar Piano", "artist": "stellarmusic", "date": "2014"}, tmp)
    check(code == 0 and out.get("ok") and out["named"] == 3 and out["committed"], f"committed: {out}")
    rel = out["release"]
    check(rel.startswith("local:release:") and not ff.MBID.match(rel), f"a local release id: {rel}")
    c = sqlite3.connect(db)
    check(c.execute("SELECT title, release_date, artist_credit, track_count, source FROM releases WHERE mbid = ?",
                    (rel,)).fetchone() == ("Future Sound: Stellar Piano", "2014", "stellarmusic", 3, "local:edition"),
          "the release as named")
    artists = c.execute("SELECT mbid, name FROM artists").fetchall()
    check(len(artists) == 1 and artists[0][0].startswith("local:artist:") and artists[0][1] == "stellarmusic",
          f"one local artist: {artists}")
    credits = c.execute("SELECT COUNT(*) FROM recording_artists WHERE artist_mbid = ?", (artists[0][0],)).fetchone()[0]
    check(credits == 3, f"credited on every track: {credits}")
    places = c.execute("SELECT position, chosen, source FROM release_recordings WHERE release_mbid = ? "
                       "ORDER BY position", (rel,)).fetchall()
    check(places == [(1, 1, "folder:local"), (2, 1, "folder:local"), (3, 1, "folder:local")],
          f"each track at its place, chosen, settled: {places}")
    shown = c.execute("SELECT COUNT(*) FROM file_releases WHERE release_mbid = ? AND source = 'folder:local'",
                      (rel,)).fetchone()[0]
    check(shown == 3, f"every file shown under it: {shown}")
    cover = c.execute("SELECT LENGTH(front), source FROM cover_art WHERE release_mbid = ?", (rel,)).fetchone()
    check(cover and cover[0] > 500 and cover[1].startswith("found:folder:cover.jpg"), f"the cover beside it: {cover}")
    links = c.execute("SELECT DISTINCT source FROM passage_recordings").fetchall()
    check(links == [("local:ingest",)], f"the passages' links untouched, so the review queue still leaves them: {links}")
    c.close()

    st = ff.status(sqlite3.connect(db), folder)
    ed = st["stages"]["edition"]
    check(ed["state"] == "done" and ed["radio_named"] == 3 and ed["album_named"] == 3 and ed["release"]["local"],
          f"the Finish page reads it named: {ed}")
    check(st["stages"]["cover"]["state"] == "done" and st["stages"]["works"]["state"] == "not needed",
          f"cover done, works not needed: {st['stages']['cover']['state']}, {st['stages']['works']['state']}")
    check(st["next"] == ["flavor", "amplitude"], f"only the analysis left: {st['next']}")
    listed = [f for f in ff.folders(sqlite3.connect(db)) if f["folder"] == os.path.normpath(folder)]
    check(listed and listed[0]["placeholders"] == 0, f"the index counts no placeholder left: {listed}")
    check(rel not in [r for r, _, _ in fetch_cover_art.wanted(sqlite3.connect(db), 60, [rel])]
          and all(r != rel for r, _, _ in fetch_cover_art.wanted(sqlite3.connect(db), 0)),
          "the archive is never asked about it")


def test_named_again(tmp: str) -> None:
    print("named again: the same release corrected, a featured artist credited, a track left out")
    db, folder = library(tmp, "again")
    _, first = run(db, folder, {"title": "Future Sound", "artist": "stellarmusic"}, tmp)
    tracks = [{"audio_md5": md5(1), "position": 1, "title": "Big Dreams", "artist": ""},
              {"audio_md5": md5(2), "position": 2, "title": "In Your Eyes", "artist": "stellarmusic; Laura Aiisha"}]
    code, out = run(db, folder, {"title": "Future Sound: Stellar Piano", "artist": "stellarmusic", "date": "2014-08-08",
                                 "tracks": tracks}, tmp)
    check(code == 0 and out["release"] == first["release"], f"the same release: {first.get('release')} {out}")
    c = sqlite3.connect(db)
    rel = out["release"]
    check(c.execute("SELECT title, release_date FROM releases WHERE mbid = ?", (rel,)).fetchone()
          == ("Future Sound: Stellar Piano", "2014-08-08"), "its title and date corrected")
    check(c.execute("SELECT title FROM recordings WHERE mbid = ?", (f"local:audio:{md5(2)}",)).fetchone()
          == ("In Your Eyes",), "the track's title as the person gave it")
    credits = c.execute("SELECT a.name, ra.weight FROM recording_artists ra JOIN artists a ON a.mbid = ra.artist_mbid "
                        "WHERE ra.mbid = ? ORDER BY ra.weight DESC", (f"local:audio:{md5(2)}",)).fetchall()
    check(credits == [("stellarmusic", 1.0), ("Laura Aiisha", 0.5)], f"both credited, the first shown: {credits}")
    left = c.execute("SELECT COUNT(*) FROM release_recordings WHERE release_mbid = ? AND mbid = ?",
                     (rel, f"local:audio:{md5(3)}")).fetchone()[0] + c.execute(
        "SELECT COUNT(*) FROM file_releases WHERE audio_md5 = ?", (md5(3),)).fetchone()[0]
    check(left == 0, "the track left out leaves the edition")
    check(any(s["why"] == "left out of the edition" for s in out["skipped"]), f"and is said: {out['skipped']}")
    c.close()
    st = ff.status(sqlite3.connect(db), folder)["stages"]["edition"]
    check(st["state"] == "partly" and st["radio_named"] == 2, f"the page counts it unnamed: {st}")


def test_the_librarys_own_artist(tmp: str) -> None:
    print("an artist the library already has is the library's; a MusicBrainz track is left as it is")
    db, folder = library(tmp, "own", mb_file=True)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO artists (mbid, name, source) VALUES (?, 'StellarMusic', 'musicbrainz')", (MB_ARTIST,))
    c.commit()
    c.close()
    code, out = run(db, folder, {"title": "Future Sound", "artist": "stellarmusic"}, tmp)
    check(code == 0 and out["artists"] == [{"name": "stellarmusic", "id": MB_ARTIST, "existing": True}],
          f"the one by that name, whatever its case: {out.get('artists')}")
    check(out["named"] == 3 and any("MusicBrainz recording" in s["why"] for s in out["skipped"]),
          f"the MusicBrainz file skipped and said: {out['skipped']}")
    c = sqlite3.connect(db)
    check(c.execute("SELECT COUNT(*) FROM recording_artists WHERE mbid = ?", (MB_REC,)).fetchone()[0] == 0
          and c.execute("SELECT COUNT(*) FROM file_releases WHERE audio_md5 = ?", (md5(4),)).fetchone()[0] == 0,
          "and nothing written for it")
    # Two artists by one name: which is meant is not this tool's to guess.
    c.execute("INSERT INTO artists (mbid, name, source) VALUES ('33333333-3333-3333-3333-333333333333', "
              "'Laura Aiisha', 'musicbrainz'), ('44444444-4444-4444-4444-444444444444', 'Laura Aiisha', 'musicbrainz')")
    aid, existing = le.resolve_artist(c, "Laura Aiisha")
    check(aid.startswith("local:artist:") and not existing, f"two by one name: a local one: {aid}")
    check(le.resolve_artist(c, "nobody")[0] == le.resolve_artist(c, "NOBODY")[0], "one id for one name, any case")
    c.close()


def test_refusals(tmp: str) -> None:
    print("refused, with why, and nothing written")
    db, folder = library(tmp, "refused", cover=False)
    for spec, why in (({"title": "", "artist": "x"}, "title and an artist"),
                      ({"title": "T", "artist": "x", "date": "Aug 2014"}, "a date is"),
                      ({"title": "T", "artist": "x", "tracks": [{"audio_md5": md5(9)}]}, "name no file"),
                      ({"title": "T", "artist": "x", "tracks": [{"audio_md5": md5(1), "position": 2},
                                                                {"audio_md5": md5(2), "position": 2}]}, "share a place"),
                      ({"title": "T", "artist": "x", "tracks": [{"audio_md5": "nope"}]}, "names no file"),
                      ({"title": "T", "artist": "x", "tracks": [{"audio_md5": md5(1), "position": 0}]}, "whole number")):
        code, out = run(db, folder, spec, tmp)
        check(code == 1 and not out.get("ok") and why in (out.get("error") or ""), f"{spec}: {out}")
    c = sqlite3.connect(db)
    check(c.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0, "nothing written")
    c.close()
    code, out = run(db, folder, {"title": "T", "artist": "x"}, tmp, commit=False)
    c = sqlite3.connect(db)
    check(code == 0 and out["named"] == 3 and not out["committed"]
          and c.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0, "a rehearsal writes nothing")
    c.close()


def test_no_picture(tmp: str) -> None:
    print("no picture beside or in the files: nothing to take, and still finished")
    db, folder = library(tmp, "bare", cover=False)
    code, out = run(db, folder, {"title": "T", "artist": "x"}, tmp)
    check(code == 0 and out["cover"] is None, f"no cover: {out.get('cover')}")
    st = ff.status(sqlite3.connect(db), folder)["stages"]["cover"]
    check(st["state"] == "nothing to take", f"nothing to take: {st}")


def test_the_same_files_the_same_release(tmp: str) -> None:
    print("the same files make the same release on another library, as a node ingesting alone would")
    a, fa = library(tmp, "node-a", cover=False)
    b, fb = library(tmp, "node-b", cover=False)
    pa = le.plan(sqlite3.connect(a), fa, {"title": "T", "artist": "x"})
    pb = le.plan(sqlite3.connect(b), fb, {"title": "Other", "artist": "y"})
    check(pa["release"] == pb["release"], f"{pa['release']} {pb['release']}")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_from_the_tags(tmp)
        test_named_again(tmp)
        test_the_librarys_own_artist(tmp)
        test_refusals(tmp)
        test_no_picture(tmp)
        test_the_same_files_the_same_release(tmp)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("local_edition: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
