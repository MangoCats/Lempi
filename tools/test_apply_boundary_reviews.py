#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `apply_boundary_reviews.py` `[REQ-LIB-175]`, `[SPEC021 §5]`.

The schema below is copied from SPEC008 including every `NOT NULL` and the
`passages_span` unique index -- `apply_reviews.py`'s own tests exist because a
looser fixture let a real bug through, and the collision refusal this tool
has to make is exactly the kind of thing a looser schema would hide.

    python tools/test_apply_boundary_reviews.py
"""

import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "apply_boundary_reviews.py")

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
    format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id),
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    boundary_src TEXT NOT NULL,
    fade_in_ms INTEGER NOT NULL DEFAULT 20, fade_out_ms INTEGER NOT NULL DEFAULT 20,
    fade_in_curve TEXT NOT NULL DEFAULT 'exponential',
    fade_out_curve TEXT NOT NULL DEFAULT 'exponential',
    CHECK (end_ms > start_ms));
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
CREATE TABLE lowlevel_cache (audio_md5 TEXT NOT NULL, start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL, features BLOB NOT NULL, extractor TEXT NOT NULL,
    extracted_at TEXT NOT NULL, PRIMARY KEY (audio_md5, start_ms, end_ms)) WITHOUT ROWID;
CREATE TABLE boundary_reviews (passage_id INTEGER PRIMARY KEY,
    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, decided_at TEXT NOT NULL, applied_at TEXT,
    fade_in_ms INTEGER, fade_out_ms INTEGER, fade_in_curve TEXT, fade_out_curve TEXT,
    orig_fade_in_ms INTEGER, orig_fade_out_ms INTEGER,
    orig_fade_in_curve TEXT, orig_fade_out_curve TEXT);
"""

# The pre-`[SPEC-SUI-226]` shape of `passages`/`boundary_reviews` -- exactly
# what `SCHEMA` was before this feature, kept as its own constant rather than
# derived from `SCHEMA` by string surgery, which would be one whitespace
# change away from silently testing nothing.
PRE_FADE_SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
    format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id),
    kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    boundary_src TEXT NOT NULL, CHECK (end_ms > start_ms));
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
CREATE TABLE lowlevel_cache (audio_md5 TEXT NOT NULL, start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL, features BLOB NOT NULL, extractor TEXT NOT NULL,
    extracted_at TEXT NOT NULL, PRIMARY KEY (audio_md5, start_ms, end_ms)) WITHOUT ROWID;
CREATE TABLE boundary_reviews (passage_id INTEGER PRIMARY KEY,
    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, decided_at TEXT NOT NULL, applied_at TEXT,
    fade_in_ms INTEGER, fade_out_ms INTEGER, fade_in_curve TEXT, fade_out_curve TEXT,
    orig_fade_in_ms INTEGER, orig_fade_out_ms INTEGER,
    orig_fade_in_curve TEXT, orig_fade_out_curve TEXT);
"""

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def build(tmp: str, extra_passage=None) -> str:
    """A fresh library in its own directory, so each case starts clean."""
    os.makedirs(tmp, exist_ok=True)
    db = os.path.join(tmp, "t.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5','/m/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES "
              "(1,1,'radio',1000,200000,0,900,-1.0,'segmentation',20,20,'exponential','exponential')")
    c.execute("INSERT INTO lowlevel_cache VALUES ('md5',1000,200000,x'00',"
              "'essentia','t')")
    # The boundary edit moves fade off its fixed default too `[SPEC-SUI-226]`.
    c.execute("INSERT INTO boundary_reviews VALUES "
              "(1,2000,190000,250,1200,-2.0,'t',NULL,15,1500,'linear','cosine',"
              "20,20,'exponential','exponential')")
    if extra_passage:
        c.execute("INSERT INTO passages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", extra_passage)
    c.commit()
    c.close()
    return db


def run(db, *args):
    return subprocess.run([sys.executable, TOOL, db, *args],
                          capture_output=True, text=True)


def passage(db, passage_id=1):
    c = sqlite3.connect(db)
    row = c.execute(
        "SELECT start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db, boundary_src "
        "FROM passages WHERE passage_id=?", (passage_id,)).fetchone()
    c.close()
    return row


def passage_fade(db, passage_id=1):
    """Like `passage`, plus the four fade columns `[SPEC-SUI-226]` -- kept
    separate so the pre-fade assertions stay exactly as strict as before."""
    c = sqlite3.connect(db)
    row = c.execute(
        "SELECT fade_in_ms, fade_out_ms, fade_in_curve, fade_out_curve "
        "FROM passages WHERE passage_id=?", (passage_id,)).fetchone()
    c.close()
    return row


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:

        print("a rehearsal writes nothing")
        db = build(tmp + "/a")
        r = run(db)
        check(r.returncode == 0, f"rehearsal exited {r.returncode}: {r.stderr[:200]}")
        check("would apply 1" in r.stdout, f"expected a rehearsal summary, got {r.stdout!r}")
        check(passage(db) == (1000, 200000, 0, 900, -1.0, "segmentation"),
              f"a rehearsal must not change the passage, got {passage(db)}")

        print("a commit rewrites the passage and marks it manual")
        r = run(db, "--commit")
        check(r.returncode == 0, f"commit exited {r.returncode}: {r.stderr[:300]}")
        got = passage(db)
        check(got == (2000, 190000, 250, 1200, -2.0, "manual"), f"passage is now {got}")
        got_fade = passage_fade(db)
        check(got_fade == (15, 1500, "linear", "cosine"),
              f"fade must land alongside the rest of the boundary edit [SPEC-SUI-226], got {got_fade}")
        c = sqlite3.connect(db)
        check(c.execute("SELECT applied_at FROM boundary_reviews WHERE passage_id=1")
               .fetchone()[0] is not None, "applied_at must be stamped")

        print("the old span's cache row is dropped, nothing else was touched")
        check(c.execute("SELECT COUNT(*) FROM lowlevel_cache WHERE start_ms=1000")
               .fetchone()[0] == 0, "the old-span cache row must be gone")
        c.close()

        print("re-running is a no-op, not a second application")
        r = run(db, "--commit")
        check("0 boundary edit(s)" in r.stdout, f"expected nothing pending, got {r.stdout!r}")
        check(passage(db) == got, "a no-op run must not touch the passage again")

        print("a new span colliding with another passage on the same file is refused")
        db2 = build(tmp + "/b", extra_passage=(2, 1, "radio", 2000, 190000, None, None, None, "s",
                                                20, 20, "exponential", "exponential"))
        r = run(db2, "--commit")
        check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:300]}")
        check("refused" in r.stdout.lower(), f"expected a refusal, got {r.stdout!r}")
        check(passage(db2) == (1000, 200000, 0, 900, -1.0, "segmentation"),
              "a colliding edit must not be applied")
        c = sqlite3.connect(db2)
        check(c.execute("SELECT applied_at FROM boundary_reviews WHERE passage_id=1")
               .fetchone()[0] is None, "a refused edit must not be stamped applied")
        c.close()

        print("a cache row still used by another passage's identical span survives")
        db3 = build(tmp + "/c")
        c = sqlite3.connect(db3)
        # A second file whose audio happens to hash the same, still at the
        # OLD span -- the cache is keyed by audio_md5, not by file, so this is
        # the real case a byte-identical duplicate rip produces.
        c.execute("INSERT INTO files VALUES (2,'md5','/m/b.mp3',1,1.0,'mp3',300000,'t','t')")
        c.execute("INSERT INTO passages VALUES "
                  "(3,2,'radio',1000,200000,0,900,-1.0,'segmentation',20,20,'exponential','exponential')")
        c.commit()
        c.close()
        r = run(db3, "--commit")
        check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:300]}")
        c = sqlite3.connect(db3)
        check(c.execute("SELECT COUNT(*) FROM lowlevel_cache WHERE start_ms=1000")
               .fetchone()[0] == 1,
              "a span still used by another passage's identical audio must survive")
        c.close()

        print("a passages table never migrated for fade is refused clearly, not a raw SQL error")
        os.makedirs(tmp + "/d", exist_ok=True)
        db4 = os.path.join(tmp + "/d", "t.db")
        c = sqlite3.connect(db4)
        # The pre-`[SPEC-SUI-226]` shape: no fade columns on `passages` at
        # all -- a library `tools/add_fade_columns.py` has never touched.
        c.executescript(PRE_FADE_SCHEMA)
        c.execute("INSERT INTO files VALUES (1,'md5','/m/a.mp3',1,1.0,'mp3',300000,'t','t')")
        c.execute("INSERT INTO passages VALUES (1,1,'radio',1000,200000,0,900,-1.0,'segmentation')")
        c.execute("INSERT INTO boundary_reviews VALUES "
                  "(1,2000,190000,250,1200,-2.0,'t',NULL,15,1500,'linear','cosine',"
                  "20,20,'exponential','exponential')")
        c.commit()
        c.close()
        r = run(db4, "--commit")
        # A whole-run precondition failure, the same posture "no boundary
        # edits recorded yet" already takes -- not a per-row refusal, which
        # is what returns 0 elsewhere in this tool.
        check(r.returncode == 1, f"expected a clean exit 1, got {r.returncode}: {r.stderr[:300]}")
        check("add_fade_columns.py" in r.stdout,
              f"expected a pointer to the migration script, got {r.stdout!r}")
        check("Traceback" not in r.stdout and "Traceback" not in r.stderr,
              f"a missing column must be a clear refusal, not a raw traceback: {r.stderr[:400]}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("apply_boundary_reviews: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
