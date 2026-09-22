#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `ingest_folder.py`'s album/radio duality `[SPEC-SA-110]`,
`[GDE-BMK-030]`.

Exercises the contract on its own -- `--kind both` (the new default) writes
one `radio` and one `album` passage per file, sharing the same span and the
same recording link; an album cut's lead/gain are 0, permanently, not NULL
the way a fresh radio cut's are; and a re-run over the same audio adds
nothing a second time. `probe()`/`audio_md5()` shell out to ffmpeg/ffprobe --
mocked here rather than run for real, the same reasoning every other tool
test in this directory already takes toward audio it does not need to
actually decode.

    python tools/test_ingest_folder.py
"""

import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest_folder  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


class FakeCompleted:
    def __init__(self, stdout: dict, returncode: int = 0):
        self.returncode = returncode
        self.stdout = json.dumps(stdout)
        self.stderr = ""


def test_probe_falls_back_to_stream_tags() -> None:
    """`[REQ-LIB-146]` Ogg Vorbis comments land on the *stream*, not the
    *format* -- found live against `Xavier Rudd/White Moth`, where every one
    of 27 `.ogg` files in the library came back with `file_tags` all NULL
    despite carrying full tags on disk. `subprocess.run` is faked with the
    exact shape `ffprobe` returns for that file, not decoded audio -- the
    same reasoning this file's own module docstring already gives for
    mocking `probe()` wholesale elsewhere; here it is `probe()` itself under
    test, so only its one subprocess call is faked.
    """
    print("probe(): an Ogg file's stream-level tags are read when format-level ones are empty")
    real_run, real_duration = ingest_folder.subprocess.run, ingest_folder.audio_duration.probe_duration_ms
    ingest_folder.subprocess.run = lambda *a, **kw: FakeCompleted({
        "format": {},
        "streams": [{"codec_type": "audio", "tags": {
            "TITLE": "Better People", "ARTIST": "Xavier Rudd", "ALBUM": "White Moth",
            "track": "1", "disc": "1",
        }}],
    })
    ingest_folder.audio_duration.probe_duration_ms = lambda p: 186506.0
    try:
        info = ingest_folder.probe("irrelevant.ogg")
    finally:
        ingest_folder.subprocess.run = real_run
        ingest_folder.audio_duration.probe_duration_ms = real_duration
    check(info is not None, "probe() must not give up just because format.tags was empty")
    if info:
        check(info["title"] == "Better People", f"got {info['title']!r}")
        check(info["artist"] == "Xavier Rudd", f"got {info['artist']!r}")
        check(info["album"] == "White Moth", f"got {info['album']!r}")
        check(info["track_no"] == 1, f"got {info['track_no']!r}")
        check(info["disc_no"] == 1, f"got {info['disc_no']!r}")


def test_probe_prefers_format_tags_when_present() -> None:
    print("probe(): a format-level tag (MP3/ID3's own home) is never overridden by a "
          "same-named stream-level one")
    real_run, real_duration = ingest_folder.subprocess.run, ingest_folder.audio_duration.probe_duration_ms
    ingest_folder.subprocess.run = lambda *a, **kw: FakeCompleted({
        "format": {"tags": {"title": "Format Title", "artist": "Format Artist"}},
        "streams": [{"codec_type": "audio", "tags": {
            "TITLE": "Stream Title", "ALBUM": "Stream Album",
        }}],
    })
    ingest_folder.audio_duration.probe_duration_ms = lambda p: 200000.0
    try:
        info = ingest_folder.probe("irrelevant.mp3")
    finally:
        ingest_folder.subprocess.run = real_run
        ingest_folder.audio_duration.probe_duration_ms = real_duration
    check(info["title"] == "Format Title", f"format-level title must win, got {info['title']!r}")
    check(info["album"] == "Stream Album",
          f"a field format.tags never had at all must still fall back, got {info['album']!r}")


# The minimum this tool's own INSERTs touch -- not the full `sql/schema.sql`,
# the same "just enough" shape every other tool test in this directory uses.
SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE,
    path TEXT, size_bytes INTEGER, mtime REAL, format TEXT, duration_ms INTEGER,
    first_seen TEXT, last_seen TEXT);
CREATE TABLE file_tags (file_id INTEGER, title TEXT, artist TEXT, album TEXT,
    track_no INTEGER, disc_no INTEGER, has_art INTEGER, scanned_at INTEGER);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT NOT NULL, start_ms INTEGER, end_ms INTEGER,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    boundary_src TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT, length_ms INTEGER, source TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT NOT NULL,
    weight REAL, source TEXT);
CREATE TABLE ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT,
    stage TEXT, outcome TEXT, confidence REAL, detail TEXT, decided_at TEXT);
"""

FAKE_MD5 = "deadbeef" * 4  # 32 hex chars, the real shape audio_md5() returns


def run(db_path: str, folder: str, extra_args=()) -> int:
    old_argv = sys.argv
    sys.argv = ["ingest_folder.py", db_path, folder, "--commit", *extra_args]
    try:
        return ingest_folder.main()
    finally:
        sys.argv = old_argv


def main() -> int:
    test_probe_falls_back_to_stream_tags()
    test_probe_prefers_format_tags_when_present()
    print()

    fd, tmp_db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(tmp_db)
    conn = sqlite3.connect(tmp_db)
    conn.executescript(SCHEMA)
    conn.close()

    # Real shelling-out replaced with a fixed answer -- what this test checks
    # is what `main()` does with a probe result, not whether ffmpeg runs.
    ingest_folder.audio_md5 = lambda p: FAKE_MD5
    ingest_folder.probe = lambda p: {
        "duration_ms": 200000, "title": "A Song", "artist": "Someone",
        "album": None, "track_no": None, "disc_no": None, "has_art": 0,
    }

    with tempfile.TemporaryDirectory() as folder:
        with open(os.path.join(folder, "song.mp3"), "wb") as f:
            f.write(b"not real audio -- probe()/audio_md5() are mocked above")

        print("--kind both (the new default): one radio cut, one album cut, "
              "same span, same recording")
        run(tmp_db, folder)
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM passages ORDER BY kind").fetchall()
        check(len(rows) == 2, f"expected 2 passages (radio+album), got {len(rows)}")
        by_kind = {r["kind"]: r for r in rows}
        check(set(by_kind) == {"radio", "album"}, f"expected radio+album, got {set(by_kind)}")
        if set(by_kind) == {"radio", "album"}:
            radio, album = by_kind["radio"], by_kind["album"]
            check(radio["lead_in_ms"] is None and radio["lead_out_ms"] is None,
                  "a fresh radio cut must await analysis (NULL), not claim 0 up front")
            check((album["lead_in_ms"], album["lead_out_ms"], album["gain_db"]) == (0, 0, 0.0),
                  "an album cut's segue points must equal its own hard boundaries "
                  f"[GDE-BMK-030], got {(album['lead_in_ms'], album['lead_out_ms'], album['gain_db'])}")
            check((radio["start_ms"], radio["end_ms"]) == (album["start_ms"], album["end_ms"]) == (0, 200000),
                  "both must share the file's own full span")

            mbid = ingest_folder.LOCAL_PREFIX + FAKE_MD5
            prs = {r["passage_id"] for r in conn.execute(
                "SELECT passage_id FROM passage_recordings WHERE mbid=?", (mbid,))}
            check(prs == {radio["passage_id"], album["passage_id"]},
                  f"both passages must link to the same recording, got {prs}")
        conn.close()

        print()
        print("re-running against the same audio: already present, nothing duplicated")
        run(tmp_db, folder)
        conn = sqlite3.connect(tmp_db)
        n = conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        check(n == 2, f"a re-run over the same audio must not add more passages, got {n}")
        conn.close()

    print()
    print("--kind radio (explicit override): the single-kind path still works")
    fd, tmp_db2 = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(tmp_db2)
    conn = sqlite3.connect(tmp_db2)
    conn.executescript(SCHEMA)
    conn.close()
    with tempfile.TemporaryDirectory() as folder:
        with open(os.path.join(folder, "song.mp3"), "wb") as f:
            f.write(b"not real audio -- probe()/audio_md5() are mocked above")
        run(tmp_db2, folder, extra_args=["--kind", "radio"])
        conn = sqlite3.connect(tmp_db2)
        rows = conn.execute("SELECT kind FROM passages").fetchall()
        check(rows == [("radio",)], f"expected exactly one radio passage, got {rows}")
        conn.close()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("ingest_folder: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
