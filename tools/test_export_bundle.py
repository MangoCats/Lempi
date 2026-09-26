#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `export_bundle.py`'s `--md5-file` `[SPEC-MESH-040]`.

Only the new selection path -- reading audio_md5s to include from a file,
the mesh-diff-to-bundle handoff -- not a full test of `export_bundle.py`
end to end, which had no test suite before this and is not what changed.

    python tools/test_export_bundle.py
"""

import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_bundle as eb  # noqa: E402

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
    format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, artist TEXT,
    album TEXT, track_no INTEGER, disc_no INTEGER, has_art INTEGER NOT NULL DEFAULT 0,
    scanned_at INTEGER NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT NOT NULL);
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT NOT NULL, artist_mbid TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL, sort_name TEXT,
    source TEXT NOT NULL);
CREATE TABLE flavor (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    characteristic TEXT NOT NULL, class TEXT NOT NULL, value REAL NOT NULL,
    source TEXT NOT NULL, accuracy REAL);
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def build_library(db_path: str, audio_path: str, md5: str) -> None:
    c = sqlite3.connect(db_path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,?,?,4,1.0,'wav',1000,'t','t')", (md5, audio_path))
    c.execute("INSERT INTO passages VALUES (1,1,'radio',0,1000,NULL,NULL,NULL,'x')")
    c.execute("INSERT INTO passage_recordings VALUES (1,'m1',1.0,'s')")
    c.execute("INSERT INTO recordings VALUES ('m1','Song',NULL,'s')")
    c.commit()
    c.close()


def test_md5_file_selects_the_listed_encodings():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "lempi.db")
    audio_path = os.path.join(tmp, "a.wav")
    with open(audio_path, "wb") as fh:
        fh.write(b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt ")  # never hashed here, just present
    md5 = "deadbeef00000000000000000000000"
    build_library(db_path, audio_path, md5)

    md5_file = os.path.join(tmp, "approved.txt")
    with open(md5_file, "w", encoding="utf-8") as fh:
        fh.write(f"{md5}\n\n")  # a blank line in the middle -- must be skipped, not treated as a hash

    out_dir = os.path.join(tmp, "bundle")
    old_argv = sys.argv
    sys.argv = ["export_bundle.py", db_path, "--md5-file", md5_file, "-o", out_dir]
    try:
        rc = eb.main()
    finally:
        sys.argv = old_argv

    check(rc == 0, f"expected a clean export, exit code was {rc}")
    payload_path = os.path.join(out_dir, "payload.json")
    check(os.path.isfile(payload_path), "payload.json must exist after a successful export")
    if os.path.isfile(payload_path):
        with open(payload_path, encoding="utf-8") as fh:
            text = fh.read()
        check(md5 in text, "the approved md5 must actually reach the payload")
        # The byte hash of the file that shipped `[SPEC-PL-087]`, checked
        # against hashlib directly rather than against the exporter's helper.
        import hashlib
        import json
        shipped = os.path.join(out_dir, "audio", "a.wav")
        with open(shipped, "rb") as fh:
            want = hashlib.sha256(fh.read()).hexdigest()
        enc = json.loads(text)["encodings"][0]
        check(enc.get("sha256") == want,
              f"the encoding must carry the shipped file's sha256, got {enc.get('sha256')!r}")


def test_md5_file_combines_with_a_hand_typed_md5():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "lempi.db")
    audio_path = os.path.join(tmp, "a.wav")
    open(audio_path, "wb").close()
    md5 = "cafebabe00000000000000000000000"
    build_library(db_path, audio_path, md5)

    empty_md5_file = os.path.join(tmp, "approved.txt")
    open(empty_md5_file, "w").close()

    out_dir = os.path.join(tmp, "bundle")
    old_argv = sys.argv
    sys.argv = ["export_bundle.py", db_path, "--md5-file", empty_md5_file,
                "--md5", md5, "-o", out_dir]
    try:
        rc = eb.main()
    finally:
        sys.argv = old_argv
    check(rc == 0, f"an empty --md5-file plus a hand-typed --md5 must still export, got {rc}")


def main() -> int:
    test_md5_file_selects_the_listed_encodings()
    test_md5_file_combines_with_a_hand_typed_md5()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("export_bundle: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
