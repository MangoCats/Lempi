#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tools/induct_covers.py [SPEC047]."""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import induct_covers as ic  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def mp3(path: str, picture: bytes | None = None):
    """Real MPEG frames, so mutagen reads it as an MP3; with an embedded front
    cover when given one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)
    with open(path, "wb") as fh:
        fh.write(frame * 20)
    if picture:
        from mutagen.id3 import APIC, ID3
        tags = ID3()
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=picture))
        tags.save(path)


def pic(tag: bytes) -> bytes:
    return b"\xff\xd8\xff" + tag * 400


def library(tmp: str) -> str:
    db = os.path.join(tmp, "library.db")
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT);
        CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, album TEXT);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER);
        CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
        CREATE TABLE release_recordings (release_mbid TEXT, mbid TEXT, chosen INTEGER);
        CREATE TABLE cover_art (release_mbid TEXT PRIMARY KEY, front BLOB, back BLOB,
            source TEXT NOT NULL, fetched_at TEXT NOT NULL);
        INSERT INTO cover_art VALUES ('r-has', X'FFD8FF00', NULL, 'caa', 't');
    """)
    music = os.path.join(tmp, "Music")
    # alone/: two files of r1, a folder.jpg and a back.jpg.
    # mixed/: one r1, one r2, a folder.jpg that belongs to neither for certain.
    # has/: r-has, which the catalogue already covers.
    # loose/: no release, one album, embedded picture only.
    # onealbum/: one album on disk, two releases chosen for its songs.
    files = [
        (1, "alone/a.mp3", "r1", None), (2, "alone/b.mp3", "r1", None),
        (3, "mixed/c.mp3", "r2", pic(b"E")), (4, "mixed/d.mp3", "r3", None),
        (5, "has/e.mp3", "r-has", pic(b"X")),
        (6, "loose/f.mp3", None, pic(b"L")),
        (8, "onealbum/h.mp3", "r5", None), (9, "onealbum/i.mp3", "r6", None),
    ]
    albums = {3: "Two", 4: "Three", 8: "Leftoverture", 9: "Leftoverture"}
    for fid, rel_path, release, picture in files:
        path = os.path.join(music, *rel_path.split("/"))
        mp3(path, picture)
        c.execute("INSERT INTO files VALUES (?, ?, ?)", (fid, f"md5-{fid}", path))
        c.execute("INSERT INTO file_tags VALUES (?, ?)", (fid, albums.get(fid, "Some Album")))
        c.execute("INSERT INTO passages VALUES (?, ?)", (fid, fid))
        c.execute("INSERT INTO passage_recordings VALUES (?, ?)", (fid, f"rec-{fid}"))
        if release:
            c.execute("INSERT INTO release_recordings VALUES (?, ?, 1)", (release, f"rec-{fid}"))
    # big/: one r4 file, and a real 2400 x 1600 scan beside it.
    mp3(os.path.join(music, "big", "g.mp3"))
    c.execute("INSERT INTO files VALUES (7, 'md5-7', ?)", (os.path.join(music, "big", "g.mp3"),))
    c.execute("INSERT INTO file_tags VALUES (7, 'Big Album')")
    c.execute("INSERT INTO passages VALUES (7, 7)")
    c.execute("INSERT INTO passage_recordings VALUES (7, 'rec-7')")
    c.execute("INSERT INTO release_recordings VALUES ('r4', 'rec-7', 1)")
    from PIL import Image
    import io
    scan = io.BytesIO()
    Image.effect_noise((2400, 1600), 60).convert("RGB").save(scan, "PNG")
    for folder, name, data in (("alone", "Folder.JPG", pic(b"F")), ("alone", "back.jpg", pic(b"B")),
                               ("mixed", "folder.jpg", pic(b"M")), ("big", "folder.png", scan.getvalue()),
                               ("onealbum", "cover.jpg", pic(b"K"))):
        with open(os.path.join(music, folder, name), "wb") as fh:
            fh.write(data)
    c.commit()
    c.close()
    return db


def main() -> int:
    tmp = tempfile.mkdtemp()
    db = library(tmp)
    before = {n: open(os.path.join(dp, n), "rb").read()
              for dp, _, fs in os.walk(os.path.join(tmp, "Music")) for n in fs}

    check(ic.main([db]) == 0, "a dry run succeeds")
    c = sqlite3.connect(db)
    check(c.execute("SELECT COUNT(*) FROM cover_art").fetchone()[0] == 1
          and not c.execute("SELECT 1 FROM sqlite_master WHERE name='file_art'").fetchone(),
          "and writes nothing, not even the table")
    c.close()

    check(ic.main([db, "--write"]) == 0, "a write succeeds")
    c = sqlite3.connect(db)
    art = {r[0]: (bytes(r[1]), r[2] and bytes(r[2]), r[3]) for r in c.execute(
        "SELECT release_mbid, front, back, source FROM cover_art")}
    check(art["r1"][0] == pic(b"F") and art["r1"][1] == pic(b"B") and art["r1"][2] == "found:folder:Folder.JPG",
          f"a folder that is one release's gives it its folder picture, and its back: {art['r1'][2]}")
    check(art["r2"][0] == pic(b"E") and art["r2"][2] == "found:embedded",
          f"a folder of two releases is passed over for the file's own picture: {art['r2'][2]}")
    check("r3" not in art, "a file with neither gets nothing")
    from PIL import Image
    import io
    shrunk = Image.open(io.BytesIO(art["r4"][0]))
    check(shrunk.format == "JPEG" and max(shrunk.size) == 1200 and "shrunk 2400x1600->1200x800" in art["r4"][2],
          f"a large scan is kept as a 1200 px display copy: {shrunk.format} {shrunk.size} {art['r4'][2]}")
    check(art["r1"][0] == pic(b"F"), "a small picture is kept byte for byte")
    check(art["r-has"][0] == b"\xff\xd8\xff\x00" and art["r-has"][2] == "caa",
          "a release the catalogue already covers keeps its own cover")
    files = {r[0]: (bytes(r[1]), r[2]) for r in c.execute("SELECT audio_md5, front, source FROM file_art")}
    check(files.get("md5-6") == (pic(b"L"), "found:embedded"),
          f"a file with no release gets a cover of its own, by its signature: {list(files)}")
    check("r5" not in art and "r6" not in art
          and files.get("md5-8") == (pic(b"K"), "found:folder:cover.jpg") and files.get("md5-9", (0,))[0] == pic(b"K"),
          f"a one-album folder of two releases gives its picture to each file, not to either release: {list(files)}")
    check(set(files) == {"md5-6", "md5-8", "md5-9"}, f"and to no other file: {sorted(files)}")
    c.close()
    after = {n: open(os.path.join(dp, n), "rb").read()
             for dp, _, fs in os.walk(os.path.join(tmp, "Music")) for n in fs}
    check(before == after, "nothing on disk is changed")
    check(ic.main([db, "--write"]) == 0, "a second run succeeds")
    c = sqlite3.connect(db)
    check(c.execute("SELECT COUNT(*) FROM cover_art").fetchone()[0] == 4
          and c.execute("SELECT COUNT(*) FROM file_art").fetchone()[0] == 3, "and adds nothing")
    c.close()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("induct_covers: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
