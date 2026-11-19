#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `finish_folder.py`, how far an album from files has come `[SPEC-FIN-020]`.

Each stage is measured from the catalogue: an album as `ingest_folder.py`
leaves it, the same album finished, and one half done. And the index lists a
folder with a track unnamed or unanalyzed, and not one only lacking a file's
album record.

    python tools/test_finish_folder.py
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import finish_folder as ff  # noqa: E402

FAILED = []
ALBUM = os.path.normpath("C:/Music/Traffic/Smiling Phases (1991)")
OTHER = os.path.normpath("C:/Music/Traffic/Mr. Fantasy (1967)")
REL = "7271f4eb-addb-44f0-a098-62fd9104e920"
RECS = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, first_seen TEXT);
CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, artist TEXT, album TEXT,
    track_no INTEGER, disc_no INTEGER, has_art INTEGER);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
    start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER, boundary_src TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT, release_date TEXT, country TEXT);
CREATE TABLE file_releases (audio_md5 TEXT PRIMARY KEY, release_mbid TEXT, source TEXT, decided_at TEXT);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT, characteristic TEXT);
CREATE TABLE cover_art (release_mbid TEXT PRIMARY KEY, front BLOB, back BLOB);
CREATE TABLE recording_works (mbid TEXT, work_mbid TEXT);
CREATE TABLE lowlevel_cache (audio_md5 TEXT, start_ms INTEGER, end_ms INTEGER);
"""


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def library(path: str):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    for fid, folder, name, title in ((1, ALBUM, "1-01. Paper Sun.mp3", "Paper Sun"),
                                     (2, ALBUM, "1-02. Hole In My Shoe.mp3", "Hole In My Shoe"),
                                     (3, os.path.join(ALBUM, "Bonus"), "x.mp3", "Not this album's"),
                                     (4, OTHER, "Dear Mr. Fantasy.mp3", "Dear Mr. Fantasy")):
        c.execute("INSERT INTO files VALUES (?, ?, ?, ?)", (fid, f"md5-{fid}", os.path.join(folder, name),
                                                           f"2026-11-1{fid}T00:00:00"))
        c.execute("INSERT INTO file_tags VALUES (?, ?, 'Traffic', ?, ?, 1, 0)",
                  (fid, title, "Smiling Phases" if folder == ALBUM else "Other", fid))
        for pid, kind in ((fid * 10, "radio"), (fid * 10 + 1, "album")):
            c.execute("INSERT INTO passages VALUES (?, ?, ?, 0, 100000, NULL, 'ingest:whole-file')", (pid, fid, kind))
            c.execute("INSERT INTO passage_recordings VALUES (?, ?)", (pid, f"local:audio:md5-{fid}"))
    # The other album is named and analyzed, but its files have no album
    # record: that alone does not list a folder.
    c.execute("UPDATE passage_recordings SET mbid = ?1 WHERE passage_id IN (40, 41)", (RECS[0],))
    c.execute("UPDATE passages SET lead_in_ms = 0 WHERE passage_id = 40")
    c.execute("INSERT INTO lowlevel_cache VALUES ('md5-4', 0, 100000)")
    c.commit()
    return c


def finish(c, cache_path: str) -> None:
    """What the finishing job leaves: both passages named, the edition
    recorded, flavor, lead-in, a front cover, and the works asked about."""
    for fid, rec in ((1, RECS[0]), (2, RECS[1])):
        c.execute("UPDATE passage_recordings SET mbid = ? WHERE passage_id IN (?, ?)", (rec, fid * 10, fid * 10 + 1))
        c.execute("INSERT INTO file_releases VALUES (?, ?, 'folder:chosen', 't')", (f"md5-{fid}", REL))
        c.execute("UPDATE passages SET lead_in_ms = 120 WHERE passage_id = ?", (fid * 10,))
        c.execute("INSERT INTO flavor VALUES ('recording', ?, 'mood')", (rec,))
        c.execute("INSERT INTO lowlevel_cache VALUES (?, 0, 100000)", (f"md5-{fid}",))
    c.execute("INSERT INTO releases VALUES (?, 'Smiling Phases', '1991', 'US')", (REL,))
    c.execute("INSERT INTO cover_art VALUES (?, ?, NULL)", (REL, b"x" * 5000))
    c.execute("INSERT INTO recording_works VALUES (?, 'work-1')", (RECS[0],))
    c.commit()
    w = sqlite3.connect(cache_path)
    w.execute("CREATE TABLE work_cache (mbid TEXT PRIMARY KEY, response TEXT, fetched_at INTEGER)")
    w.executemany("INSERT INTO work_cache VALUES (?, '{\"relations\": []}', 0)", [(r,) for r in RECS])
    w.commit()
    w.close()


def test_as_ingest_leaves_it(c) -> None:
    print("status(): as ingest_folder.py leaves an album -- passages and tags, nothing else")
    st = ff.status(c, ALBUM)
    check((st["files"], st["radio"], st["album"]) == (2, 2, 2), f"two files directly in it, not the subfolder's: {st}")
    check(st["artist"] == "Traffic" and st["album_tag"] == "Smiling Phases", f"the tags: {st}")
    s = st["stages"]
    check(s["edition"]["state"] == "not done" and s["edition"]["release"] is None, f"edition: {s['edition']}")
    check(s["flavor"] == {"state": "not done", "done": 0, "total": 2, "measured_here": 0, "from_elsewhere": 0},
          f"flavor: {s['flavor']}")
    check(s["amplitude"]["state"] == "not done", f"amplitude: {s['amplitude']}")
    check(s["cover"]["state"] == "waiting" and s["works"]["state"] == "waiting",
          f"cover and works wait for the edition: {s['cover']} {s['works']}")
    check(st["next"] == list(ff.STAGES) and not st["finished"], f"everything to do: {st['next']}")
    check(ff.status(c, ALBUM.upper())["files"] == (2 if os.name == "nt" else 0),
          "the folder named in another case is the same folder where the file system says so")
    check(ff.status(c, os.path.join(ALBUM, "Nowhere"))["files"] == 0, "a folder with no files has no stages")


def test_finished(c, cache_path: str) -> None:
    print("status(): the same album finished -- every stage measured done")
    finish(c, cache_path)
    st = ff.status(c, ALBUM, cache_path)
    s = st["stages"]
    check(s["edition"]["state"] == "done" and s["edition"]["release"]["mbid"] == REL
          and s["edition"]["release"]["source"] == "folder:chosen"
          and (s["edition"]["radio_named"], s["edition"]["album_named"]) == (2, 2), f"edition: {s['edition']}")
    check(s["flavor"]["done"] == 2 and s["amplitude"]["done"] == 2, f"analysis: {s['flavor']} {s['amplitude']}")
    check((s["flavor"]["measured_here"], s["flavor"]["from_elsewhere"]) == (2, 0),
          f"both measured from these files: {s['flavor']}")
    # A track whose recording already had flavor from another copy -- a CD of
    # the same song -- counts as done, and says where it came from.
    c.execute("DELETE FROM lowlevel_cache WHERE audio_md5 = 'md5-2'")
    fl = ff.status(c, ALBUM, cache_path)["stages"]["flavor"]
    check((fl["done"], fl["measured_here"], fl["from_elsewhere"]) == (2, 1, 1),
          f"one measured here, one carried by its recording: {fl}")
    c.execute("INSERT INTO lowlevel_cache VALUES ('md5-2', 0, 100000)")
    c.commit()
    check(s["cover"]["state"] == "done" and s["cover"]["front"] and not s["cover"]["back"], f"cover: {s['cover']}")
    check(s["works"] == {"state": "done", "asked": 2, "recordings": 2, "with_work": 1},
          f"works: asked of both, one performs a known song: {s['works']}")
    check(st["finished"] and st["next"] == [], f"finished: {st['next']}")
    check(ff.status(c, ALBUM)["stages"]["works"]["state"] == "not done",
          "without the works cache, the works are not claimed done")


def test_half_done(c) -> None:
    print("status(): an album passage left on its placeholder keeps the edition partly done")
    c.execute("UPDATE passage_recordings SET mbid = 'local:audio:md5-2' WHERE passage_id = 21")
    c.commit()
    ed = ff.status(c, ALBUM)["stages"]["edition"]
    check(ed["state"] == "partly" and ed["album_named"] == 1 and ed["radio_named"] == 2,
          f"the whole-album play's half counts [SPEC-FIN-045]: {ed}")


def test_index() -> None:
    print("folders(): a folder with a track unnamed or unanalyzed, newest first; not one only lacking album records")
    with tempfile.TemporaryDirectory() as d:
        c = library(os.path.join(d, "lib.db"))
        got = [(f["folder"], f["placeholders"], f["no_analysis"]) for f in ff.folders(c)]
        check(got == [(os.path.join(ALBUM, "Bonus"), 1, 1), (ALBUM, 2, 2)],
              f"the album and its unnamed subfolder, newest first; the named, analyzed one not listed: {got}")
        c.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        c = library(os.path.join(d, "lib.db"))
        test_as_ingest_leaves_it(c)
        test_finished(c, os.path.join(d, "work_relations.db"))
        test_half_done(c)
        c.close()
        check(ff.works_cache_for(os.path.join(d, "lib.db")) == os.path.join(os.path.abspath(d), "work_relations.db"),
              "the works cache lies beside the library, as the induct has always passed it")
    test_index()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("finish_folder: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
