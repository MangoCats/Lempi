#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `payload.py` `[SPEC014]`.

Runs `build()`/`compatible()` in-process against a minimal SPEC008 schema --
`payload.py` has no subcommand shape worth going through a subprocess for,
unlike the tools that write to a database.

    python tools/test_payload.py
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import payload as pl  # noqa: E402

# The post-`[SPEC-SUI-226]` shape, SPEC008-accurate.
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
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL,
    sort_name TEXT, source TEXT NOT NULL);
CREATE TABLE recording_artists (mbid TEXT NOT NULL REFERENCES recordings(mbid),
    artist_mbid TEXT NOT NULL REFERENCES artists(mbid),
    weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
    PRIMARY KEY (mbid, artist_mbid)) WITHOUT ROWID;
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL REFERENCES passages(passage_id),
    mbid TEXT NOT NULL REFERENCES recordings(mbid), weight REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL, PRIMARY KEY (passage_id, mbid)) WITHOUT ROWID;
CREATE TABLE file_tags (file_id INTEGER, title TEXT, artist TEXT, album TEXT,
    track_no INTEGER, disc_no INTEGER, has_art INTEGER, scanned_at TEXT);
CREATE TABLE flavor (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    characteristic TEXT NOT NULL, class TEXT NOT NULL, value REAL NOT NULL,
    source TEXT NOT NULL, accuracy REAL,
    PRIMARY KEY (subject_kind, subject_id, characteristic, class));
"""

# The pre-`[SPEC-SUI-226]` shape -- exactly what `SCHEMA` was before this
# feature, kept as its own constant per `test_apply_boundary_reviews.py`'s own
# reasoning: derived-by-string-surgery is one whitespace change from silently
# testing nothing.
PRE_FADE_SCHEMA = SCHEMA.replace(
    "boundary_src TEXT NOT NULL,\n"
    "    fade_in_ms INTEGER NOT NULL DEFAULT 20, fade_out_ms INTEGER NOT NULL DEFAULT 20,\n"
    "    fade_in_curve TEXT NOT NULL DEFAULT 'exponential',\n"
    "    fade_out_curve TEXT NOT NULL DEFAULT 'exponential',\n"
    "    CHECK (end_ms > start_ms));",
    "boundary_src TEXT NOT NULL, CHECK (end_ms > start_ms));",
)

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def make_db(path: str, schema: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO files VALUES (1,'md5','a.mp3',1,1.0,'mp3',10000,'t','t')")
    return conn


def test_fade_travels_when_the_schema_has_it():
    """`[SPEC-SUI-226]` -- the four fade columns land in the payload exactly
    as `lead_in_ms`/`gain_db` already do, once the source database has them.
    """
    conn = make_db(":memory:", SCHEMA)
    conn.execute(
        "INSERT INTO passages VALUES "
        "(1,1,'radio',0,10000,5,900,-1.0,'manual',15,1500,'linear','cosine')")
    payload = pl.build(conn, ["md5"])
    p = payload["encodings"][0]["passages"][0]
    check(p.get("fade_in_ms") == 15, f"fade_in_ms: {p.get('fade_in_ms')!r}")
    check(p.get("fade_out_ms") == 1500, f"fade_out_ms: {p.get('fade_out_ms')!r}")
    check(p.get("fade_in_curve") == "linear", f"fade_in_curve: {p.get('fade_in_curve')!r}")
    check(p.get("fade_out_curve") == "cosine", f"fade_out_curve: {p.get('fade_out_curve')!r}")
    check(pl.compatible(payload) == [], f"expected compatible, got {pl.compatible(payload)}")


def test_fade_absent_from_a_pre_migration_source():
    """A source database `tools/add_fade_columns.py` has never touched --
    `has_column` reads around the gap the same way `export_changes.py`
    already does for `boundary_reviews`, rather than raising `OperationalError`
    or inventing values the sender never actually held.
    """
    conn = make_db(":memory:", PRE_FADE_SCHEMA)
    conn.execute(
        "INSERT INTO passages VALUES (1,1,'radio',0,10000,5,900,-1.0,'segmentation')")
    payload = pl.build(conn, ["md5"])
    p = payload["encodings"][0]["passages"][0]
    for k in ("fade_in_ms", "fade_out_ms", "fade_in_curve", "fade_out_curve"):
        check(k not in p, f"expected {k} omitted for a pre-fade source, got {p.get(k)!r}")
    check(pl.compatible(payload) == [], f"expected compatible, got {pl.compatible(payload)}")


def test_committed_fixture_09_round_trips():
    """`fixtures/payload/09-fade-fields.json` `[SPEC-PL-032]` -- the same file
    `player/src/bundle.rs`'s own test checks with `unacceptable()`, checked
    here with `compatible()`. One fixture, both implementations.
    """
    import json
    path = os.path.join(HERE, "..", "fixtures", "payload", "09-fade-fields.json")
    payload = json.load(open(path, encoding="utf-8"))
    check(pl.compatible(payload) == [], f"expected compatible, got {pl.compatible(payload)}")
    p = payload["encodings"][0]["passages"][0]
    check((p["fade_in_ms"], p["fade_out_ms"], p["fade_in_curve"], p["fade_out_curve"])
          == (15, 1200, "linear", "cosine"),
          f"unexpected fade values: {p}")


def main() -> int:
    test_fade_travels_when_the_schema_has_it()
    test_fade_absent_from_a_pre_migration_source()
    test_committed_fixture_09_round_trips()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("payload: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
