#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests that flavor extraction fails out loud.

Found 2026-10-01: with the Essentia extractor missing, `extract_library.py`
ran for two silent minutes, printed "0 extracted, 218 failed", and exited 0
-- so the job that ran it said *done*. And with the models missing, the
classifier would have loaded none and "classified" every passage into
nothing, caching it so it was never tried again. Each case here is one of
those, and none needs the real extractor or models present.

    python tools/test_extract_library.py
"""
import contextlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCHEMA = os.path.join(os.path.dirname(HERE), "sql", "schema.sql")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def library(path: str) -> None:
    """One identified radio passage, never extracted."""
    c = sqlite3.connect(path)
    c.executescript(open(SCHEMA, encoding="utf-8").read())
    c.execute("INSERT INTO recordings (mbid, title, source) VALUES ('r1', 'Song', 'test')")
    c.execute("INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, "
              "first_seen, last_seen) VALUES (1, 'md5', 'C:/nowhere/song.mp3', 1, 0, 'mp3', 200000, 'x', 'x')")
    c.execute("INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, boundary_src) "
              "VALUES (1, 1, 'radio', 0, 200000, 'cue')")
    c.execute("INSERT INTO passage_recordings (passage_id, mbid, source) VALUES (1, 'r1', 'test')")
    c.commit()
    c.close()


def run(db: str, essentia: str) -> tuple[int, str]:
    env = dict(os.environ, LEMPI_ESSENTIA_DIR=essentia, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(HERE, "extract_library.py"), db, "--jobs", "1"],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    return r.returncode, r.stdout + r.stderr


def test_missing_tools(tmp: str):
    print("a missing extractor, or missing models: stop at once and say which")
    db = os.path.join(tmp, "lib.db")
    library(db)
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty)
    code, out = run(db, empty)
    check(code == 2 and "ERROR: the Essentia extractor is missing" in out and "vendor/essentia" in out,
          f"no extractor: exit 2, named -- got {code}: {out[-300:]}")
    check("failed:" not in out, "and no passage was even tried")

    fake = os.path.join(tmp, "noclassifiers")
    os.makedirs(os.path.join(fake, "svm_beta1"))
    Path(fake, "streaming_extractor_music.exe").write_bytes(b"")
    code, out = run(db, fake)
    check(code == 2 and "0 of 18 flavor classifier models" in out,
          f"an extractor but no models: exit 2, named -- got {code}: {out[-300:]}")


def test_classifier_refuses_partial():
    print("the classifier refuses an incomplete set rather than classify into nothing")
    import gaia_classify as gc
    with tempfile.TemporaryDirectory() as d:
        try:
            gc.Classifier(Path(d))
            check(False, "an empty model folder must raise")
        except gc.ModelsMissing as e:
            check("0 of 18" in str(e), f"{e}")


def test_every_passage_failing(tmp: str):
    print("every passage failing: each named with why, and a non-zero exit")
    import extract_library as el
    db = os.path.join(tmp, "all-fail.db")
    library(db)
    exe = os.path.join(tmp, "exists.exe")
    Path(exe).write_bytes(b"")

    class Quiet:                       # stands in for the 18 real models
        def classify(self, doc):
            return {}
    saved = (el.EXTRACTOR, el.gc.Classifier, sys.argv)
    el.EXTRACTOR = Path(exe)
    el.gc.Classifier = Quiet
    sys.argv = ["extract_library.py", db, "--jobs", "1"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = el.main()
    finally:
        el.EXTRACTOR, el.gc.Classifier, sys.argv = saved
    out = buf.getvalue()
    check(code == 1, f"nothing extracted is a failure, not a quiet zero: exit {code}")
    check("failed: C:/nowhere/song.mp3 [0-200000 ms]: the audio file is not there" in out,
          f"the passage is named, with why: {out}")
    check("1 failed: the audio file is not there" in out, f"the reasons are summed at the end: {out}")
    check("extracting 1 passage(s)" in out, f"a line at the start, before the first result: {out}")
    check("1/1" in out, f"progress counts a failure too: {out}")


def test_folder_scope(tmp: str):
    print("--folder: only the album's own folder, as analyze_amplitude --folder [REQ-LIB-305]")
    db = os.path.join(tmp, "scope.db")
    library(db)                        # its one file is C:/nowhere/song.mp3
    empty = os.path.join(tmp, "scope-empty")
    os.makedirs(empty, exist_ok=True)
    for folder, want in (("C:/elsewhere", "0 to extract"), ("C:/nowhere", "1 to extract")):
        env = dict(os.environ, LEMPI_ESSENTIA_DIR=empty, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, os.path.join(HERE, "extract_library.py"), db, "--folder", folder],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        check(want in r.stdout, f"--folder {folder}: {want}, got {r.stdout[-200:]}")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_missing_tools(tmp)
        test_classifier_refuses_partial()
        test_every_passage_failing(tmp)
        test_folder_scope(tmp)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("extract_library: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
