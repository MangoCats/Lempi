#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `segment_dao.py`'s write path `[SPEC-SA-070]`, `[SPEC-SA-110]`.

`commit_segments()` is the part with no ffmpeg/network dependency worth
mocking around -- `identify_recording()` is monkeypatched with canned
answers, the same reasoning `test_ingest_folder.py` already takes toward
`probe()`/`audio_md5()`. What this checks: an identified span gets the real
recording (and its artists); an unidentified one gets the established
`local:audio:<md5>:<start_ms>` placeholder; every span gets BOTH `radio` and
`album`, sharing one identification; and a file already carrying passages
(the ordinary case -- `ingest_folder.py`'s own whole-file placeholder) has
them replaced, not added alongside.

    python tools/test_segment_dao.py
"""

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import segment_dao  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE, path TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT NOT NULL, start_ms INTEGER, end_ms INTEGER,
    lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    boundary_src TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT, length_ms INTEGER, source TEXT);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT, source TEXT);
CREATE TABLE recording_artists (mbid TEXT NOT NULL, artist_mbid TEXT NOT NULL,
    weight REAL, source TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT NOT NULL,
    weight REAL, source TEXT);
CREATE TABLE ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    stage TEXT NOT NULL, outcome TEXT NOT NULL, confidence REAL, detail TEXT, decided_at INTEGER);
"""

MD5 = "6ceaf106f3b6cd19ac91bc68f4bc0d3d"

# Two spans: the first identifies, the second does not -- both real outcomes
# among the library's own 136 already-segmented tracks (133 of 136 did).
SPANS = [(0.0, 180.0), (180.0, 300.0)]

CANNED = {
    0.0: {"mbid": "90acbf82-eb76-4961-8dac-064d21d6085f", "title": "Tulou Tagaloa",
          "artists": [("0d76d8e2-1ae6-4d42-858b-91137b65cfcd", "Some Artist")]},
    180.0: None,
}

DECISION = {"stage": "grid", "confidence": 1.0, "threshold_db": -40, "min_silence_s": 0.5,
            "match_pct": 1.0, "tested": [(-40, 0.5, 2)], "expected_count": 2}


def fixture() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1, ?, 'dao.mp3')", (MD5,))
    return c


def main() -> int:
    old_identify = segment_dao.identify_recording
    segment_dao.identify_recording = lambda path, start, end, key: CANNED[start]
    try:
        print("commit_segments: both kinds per span, real recording where "
              "identified, placeholder where not")
        c = fixture()
        result = segment_dao.commit_segments(c, 1, MD5, "dao.mp3", SPANS, DECISION, "fake-key")
        check(result == {"tracks": 2, "identified": 1, "unidentified": 1, "replaced": 0},
              f"got {result}")

        rows = c.execute("SELECT * FROM passages ORDER BY start_ms, kind").fetchall()
        check(len(rows) == 4, f"2 spans * 2 kinds = 4 passages, got {len(rows)}")
        kinds_per_start = {}
        for r in rows:
            kinds_per_start.setdefault(r["start_ms"], set()).add(r["kind"])
        check(all(k == {"radio", "album"} for k in kinds_per_start.values()),
              f"every span must carry both kinds, got {kinds_per_start}")
        for r in rows:
            check(r["boundary_src"] == "computed:segment-cascade@v1",
                  f"got {r['boundary_src']}")
            if r["kind"] == "album":
                check((r["lead_in_ms"], r["lead_out_ms"], r["gain_db"]) == (0, 0, 0.0),
                      f"an album cut's segue points must equal its own hard boundaries, got "
                      f"{(r['lead_in_ms'], r['lead_out_ms'], r['gain_db'])}")
            else:
                check(r["lead_in_ms"] is None and r["lead_out_ms"] is None,
                      "a fresh radio cut must await analysis (NULL)")

        rec = c.execute("SELECT * FROM recordings WHERE mbid=?",
                         ("90acbf82-eb76-4961-8dac-064d21d6085f",)).fetchone()
        check(rec is not None and rec["title"] == "Tulou Tagaloa" and rec["source"] == "segment:acoustid",
              f"the identified span's recording must be written, got {dict(rec) if rec else None}")
        artist = c.execute("SELECT * FROM recording_artists WHERE mbid=?",
                            ("90acbf82-eb76-4961-8dac-064d21d6085f",)).fetchone()
        check(artist is not None and artist["artist_mbid"] == "0d76d8e2-1ae6-4d42-858b-91137b65cfcd",
              f"the identified span's artist must be linked, got {dict(artist) if artist else None}")

        placeholder_mbid = f"local:audio:{MD5}:180000"
        placeholder = c.execute("SELECT * FROM recordings WHERE mbid=?",
                                 (placeholder_mbid,)).fetchone()
        check(placeholder is not None and placeholder["source"] == "segment:unidentified",
              f"the unidentified span must get the established placeholder shape, "
              f"got {dict(placeholder) if placeholder else None}")
        check(placeholder is not None and "3 min" in (placeholder["title"] or ""),
              f"the placeholder title must name where it starts, got "
              f"{placeholder['title'] if placeholder else None}")

        for r in rows:
            mbid = (placeholder_mbid if r["start_ms"] == 180000
                    else "90acbf82-eb76-4961-8dac-064d21d6085f")
            pr = c.execute("SELECT mbid FROM passage_recordings WHERE passage_id=?",
                            (r["passage_id"],)).fetchone()
            check(pr is not None and pr["mbid"] == mbid,
                  f"passage {r['passage_id']} ({r['kind']} @ {r['start_ms']}) "
                  f"must link to {mbid}, got {dict(pr) if pr else None}")

        print()
        print("every segmentation decision lands in ingest_decisions [SPEC-SA-123]")
        decisions = c.execute(
            "SELECT * FROM ingest_decisions WHERE audio_md5=? AND stage='segment'", (MD5,)
        ).fetchall()
        check(len(decisions) == 1, f"one commit, one decision row, got {len(decisions)}")
        d = decisions[0]
        check(d["outcome"] == "grid", f"got {d['outcome']}")
        check(d["confidence"] == 1.0, f"got {d['confidence']}")
        detail = json.loads(d["detail"])
        check(detail["threshold_db"] == -40, f"got {detail}")
        check(detail["tracks_found"] == 2, f"got {detail}")

        print()
        print("a second segmentation replaces the first, not adds to it")
        result2 = segment_dao.commit_segments(c, 1, MD5, "dao.mp3", SPANS, DECISION, "fake-key")
        check(result2["replaced"] == 4, f"expected the prior 4 passages replaced, got {result2}")
        n = c.execute("SELECT COUNT(*) FROM passages WHERE file_id=1").fetchone()[0]
        check(n == 4, f"replacing must not accumulate, got {n} passages")
        decisions2 = c.execute(
            "SELECT * FROM ingest_decisions WHERE audio_md5=? AND stage='segment'", (MD5,)
        ).fetchall()
        check(len(decisions2) == 2, f"a second commit adds a second decision row, got {len(decisions2)}")
        c.close()
    finally:
        segment_dao.identify_recording = old_identify

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("segment_dao: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
