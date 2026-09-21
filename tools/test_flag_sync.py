#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Case 3 `[SPEC006 §10]`: `export_flags.py`, `import_flags.py`,
and `apply_changes.py`'s `--emit-sql` / `--clear-flags`.

Runs the real pipeline end to end, the same posture `test_sync_changes.py`
already uses for Case 2: a source library with two real flags (one
recording-kind, one passage-kind) is exported for real, and the resulting
`flags.json` is imported for real against a second library that overlaps it
only partially -- one flag lands, one has nothing there to resolve it
against. Separately, `apply_changes.py --emit-sql` is checked against the
one property that matters most: the database it compares against is never
itself modified, and the script it writes, replayed elsewhere, produces the
identical result `--commit` would have written directly.

    python tools/test_flag_sync.py
"""

import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORT_FLAGS = os.path.join(HERE, "export_flags.py")
IMPORT_FLAGS = os.path.join(HERE, "import_flags.py")
EXPORT_CHANGES = os.path.join(HERE, "export_changes.py")
APPLY_CHANGES = os.path.join(HERE, "apply_changes.py")

SCHEMA = """
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
CREATE TABLE listener_flags (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    flagged_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID;
CREATE TABLE id_reviews (passage_id INTEGER PRIMARY KEY, decision TEXT NOT NULL,
    chosen_mbid TEXT, decided_at TEXT NOT NULL,
    chosen_release_mbid TEXT, previous_mbid TEXT, applied_at TEXT, origin TEXT);
CREATE TABLE boundary_reviews (passage_id INTEGER PRIMARY KEY,
    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, audio_md5 TEXT, orig_kind TEXT,
    orig_start_ms INTEGER, orig_end_ms INTEGER, orig_lead_in_ms INTEGER,
    orig_lead_out_ms INTEGER, orig_gain_db REAL, decided_at TEXT NOT NULL,
    applied_at TEXT, origin TEXT);
CREATE TABLE artist_reviews (recording_mbid TEXT PRIMARY KEY, passage_id INTEGER,
    artist_mbid TEXT NOT NULL, artist_name TEXT NOT NULL,
    previous_artist_mbid TEXT, previous_artist_name TEXT, previous_artist_weight REAL,
    decided_at TEXT NOT NULL, applied_at TEXT, origin TEXT);
"""

SONG_A = "11111111-1111-1111-1111-111111111111"  # flagged by recording
SONG_B = "22222222-2222-2222-2222-222222222222"  # only ever named via its passage

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def run(script, *args):
    return subprocess.run([sys.executable, script, *args], capture_output=True, text=True)


def lempipi_db(path: str) -> None:
    """The appliance: two files, one flagged by recording, one by passage --
    the second is exactly `[REQ-VIS-265]`'s own reasoning for keying by
    passage at all, an unidentified recording with nothing else to name it by.
    """
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-a','/srv/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO files VALUES (2,'md5-b','/srv/b.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO files VALUES (3,'md5-c','/srv/c.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES (1,1,'radio',1000,200000,0,900,-1.0,'src')")
    c.execute("INSERT INTO passages VALUES (2,2,'radio',2000,190000,0,900,-1.0,'src')")
    c.execute("INSERT INTO passages VALUES (3,3,'radio',3000,180000,0,900,-1.0,'src')")
    c.execute("INSERT INTO recordings VALUES (?,'Song A',NULL,'inherited:mulib')", (SONG_A,))
    c.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'inherited:mulib')", (SONG_A,))
    # Passage 2 has no recording at all -- exactly the case a passage-kind flag exists for.
    # Passage 3 is not flagged; it exists only so the library isn't trivially empty.
    c.execute("INSERT INTO listener_flags (subject_kind, subject_id, flagged_at) "
              "VALUES ('recording', ?, '2026-08-27 09:00:00')", (SONG_A,))
    c.execute("INSERT INTO listener_flags (subject_kind, subject_id, flagged_at) "
              "VALUES ('passage', '2', '2026-08-27 09:05:00')")
    c.commit()
    c.close()


def desktop_db(path: str, *, has_song_b_passage: bool) -> None:
    """The desktop: always has Song A (by mbid, at a different local
    passage_id, proving the id -- not the appliance's row number -- is what
    resolves it), and *optionally* the same passage Song B was flagged at,
    so both the matched and unmatched paths are exercised for real.
    """
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (10,'md5-a','/home/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES (10,10,'radio',1000,200000,0,900,-1.0,'src')")
    c.execute("INSERT INTO recordings VALUES (?,'Song A',NULL,'inherited:mulib')", (SONG_A,))
    c.execute("INSERT INTO passage_recordings VALUES (10,?,1.0,'inherited:mulib')", (SONG_A,))
    if has_song_b_passage:
        c.execute("INSERT INTO files VALUES (20,'md5-b','/home/b.mp3',1,1.0,'mp3',300000,'t','t')")
        c.execute("INSERT INTO passages VALUES (20,20,'radio',2000,190000,0,900,-1.0,'src')")
    c.commit()
    c.close()


def flags_here(conn):
    return {(k, i): o for k, i, o in
            conn.execute("SELECT subject_kind, subject_id, origin FROM listener_flags")}


def test_pull(tmp: str) -> None:
    print("Hop 1: pull -- a recording-kind flag matches by mbid, a passage-kind "
          "flag matches only where the library actually overlaps")
    lempipi = os.path.join(tmp, "lempipi.db")
    lempipi_db(lempipi)

    flags_json = os.path.join(tmp, "flags.json")
    r = run(EXPORT_FLAGS, lempipi, "-o", flags_json)
    check(r.returncode == 0, f"export exited {r.returncode}: {r.stderr[:300]}")
    check("2 flag(s)" in r.stdout, f"expected 2 flags, got {r.stdout!r}")

    # --- overlapping desktop: both flags resolve ---
    desk1 = os.path.join(tmp, "desk1.db")
    desktop_db(desk1, has_song_b_passage=True)
    r = run(IMPORT_FLAGS, desk1, flags_json, "--commit")
    check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
    check("2 new flag(s), 0 already flagged here, 0 not present here" in r.stdout,
          f"expected both to match, got {r.stdout!r}")
    c = sqlite3.connect(desk1)
    got = flags_here(c)
    check(("recording", SONG_A) in got, f"the recording flag must land on its mbid, got {got}")
    check(("passage", "20") in got, f"the passage flag must resolve to THIS library's own passage_id, got {got}")
    check(got[("recording", SONG_A)] == "lempipi" or got[("recording", SONG_A)],
          f"origin must be stamped, got {got[('recording', SONG_A)]!r}")
    c.close()

    print("re-pulling the same flags.json a second time is a pure no-op")
    r = run(IMPORT_FLAGS, desk1, flags_json, "--commit")
    check("0 new flag(s), 2 already flagged here, 0 not present here" in r.stdout,
          f"expected both already present, got {r.stdout!r}")

    # --- non-overlapping desktop: the passage flag has nothing to resolve against ---
    print("a library that does not overlap that passage reports it, rather than dropping it silently")
    desk2 = os.path.join(tmp, "desk2.db")
    desktop_db(desk2, has_song_b_passage=False)
    r = run(IMPORT_FLAGS, desk2, flags_json, "--commit")
    check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
    check("1 new flag(s), 0 already flagged here, 1 not present here" in r.stdout,
          f"expected one match and one miss, got {r.stdout!r}")
    check("not present here" in r.stdout, "the unmatched flag must be named, not silently skipped")
    c = sqlite3.connect(desk2)
    got = flags_here(c)
    check(list(got.keys()) == [("recording", SONG_A)], f"only the resolvable flag should land, got {got}")
    c.close()

    print("rehearsal (no --commit) never writes")
    desk3 = os.path.join(tmp, "desk3.db")
    desktop_db(desk3, has_song_b_passage=True)
    r = run(IMPORT_FLAGS, desk3, flags_json)
    check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
    c = sqlite3.connect(desk3)
    check(c.execute("SELECT COUNT(*) FROM listener_flags").fetchone()[0] == 0,
          "a rehearsal must not write any flag")
    c.close()

    # A real regression, not a hypothetical: `ensure_origin_column()` ran
    # only `ALTER TABLE listener_flags ADD COLUMN origin`, wrapped in a
    # blanket `except OperationalError: pass` that swallowed "no such
    # table: listener_flags" the same as "already has the column" -- so a
    # desktop that had never once had a listener flag anything locally,
    # and was only ever pulled *into*, looked fine right up until the very
    # next SELECT against the table it never created.
    print("a desktop with no listener_flags table at all -- not merely missing origin -- still lands the pull")
    desk4 = os.path.join(tmp, "desk4.db")
    c4 = sqlite3.connect(desk4)
    schema_without_flags = ";".join(
        stmt for stmt in SCHEMA.split(";") if "listener_flags" not in stmt)
    c4.executescript(schema_without_flags)
    c4.execute("INSERT INTO files VALUES (10,'md5-a','/home/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c4.execute("INSERT INTO passages VALUES (10,10,'radio',1000,200000,0,900,-1.0,'src')")
    c4.execute("INSERT INTO recordings VALUES (?,'Song A',NULL,'inherited:mulib')", (SONG_A,))
    c4.execute("INSERT INTO passage_recordings VALUES (10,?,1.0,'inherited:mulib')", (SONG_A,))
    have = {r[0] for r in c4.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check("listener_flags" not in have, "the fixture must genuinely lack the table, or this proves nothing")
    c4.commit()
    c4.close()

    r = run(IMPORT_FLAGS, desk4, flags_json, "--commit")
    check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
    check("Traceback" not in r.stderr, f"must not crash, got {r.stderr[:400]}")
    check("1 new flag(s)" in r.stdout, f"expected the recording flag to land, got {r.stdout!r}")
    c4 = sqlite3.connect(desk4)
    got = flags_here(c4)
    check(("recording", SONG_A) in got, f"the table must exist and hold the pulled flag, got {got}")
    c4.close()


def test_push_back_emit_sql(tmp: str) -> None:
    print("Hop 3: --emit-sql never writes to the compare copy, and the script "
          "it writes reproduces --commit's own result elsewhere; --clear-flags "
          "removes the flag the landed change was presumably answering")
    desktop = os.path.join(tmp, "desktop.db")
    c = sqlite3.connect(desktop)
    c.executescript(SCHEMA)
    new_rec = "33333333-3333-3333-3333-333333333333"
    old_rec = "44444444-4444-4444-4444-444444444444"
    c.execute("INSERT INTO files VALUES (1,'md5-x','/home/x.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES (1,1,'radio',1000,200000,0,900,-1.0,'src')")
    c.execute("INSERT INTO recordings VALUES (?,'The Real Title',NULL,'inherited:mulib')", (new_rec,))
    c.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'review:acoustid')", (new_rec,))
    c.execute("INSERT INTO id_reviews (passage_id,decision,chosen_mbid,previous_mbid,decided_at,applied_at) "
              "VALUES (1,'reassigned',?,?,'2026-08-27 10:00:00','2026-08-27 10:05:00')", (new_rec, old_rec))
    c.commit()
    c.close()

    changes_json = os.path.join(tmp, "changes.json")
    r = run(EXPORT_CHANGES, desktop, "-o", changes_json)
    check(r.returncode == 0, f"export exited {r.returncode}: {r.stderr[:300]}")
    check("1 applied change(s)" in r.stdout, f"expected 1 change, got {r.stdout!r}")

    def compare_copy(path: str) -> None:
        c = sqlite3.connect(path)
        c.executescript(SCHEMA)
        c.execute("INSERT INTO files VALUES (1,'md5-x','/srv/x.mp3',1,1.0,'mp3',300000,'t','t')")
        c.execute("INSERT INTO passages VALUES (1,1,'radio',1000,200000,0,900,-1.0,'src')")
        c.execute("INSERT INTO recordings VALUES (?,'Old Title',NULL,'inherited:mulib')", (old_rec,))
        c.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'inherited:mulib')", (old_rec,))
        # Flagged on the appliance, by BOTH plausible identities, before the review happened.
        c.execute("INSERT INTO listener_flags (subject_kind, subject_id, flagged_at) "
                  "VALUES ('passage', '1', '2026-08-26 08:00:00')")
        c.execute("INSERT INTO listener_flags (subject_kind, subject_id, flagged_at) "
                  "VALUES ('recording', ?, '2026-08-26 08:00:00')", (old_rec,))
        c.commit()
        c.close()

    copy_path = os.path.join(tmp, "lempipi-copy.db")
    compare_copy(copy_path)
    before_bytes = open(copy_path, "rb").read()

    patch_path = os.path.join(tmp, "patch.sql")
    r = run(APPLY_CHANGES, copy_path, changes_json, "--commit", "--emit-sql", patch_path, "--clear-flags")
    check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
    check("1 fast-forward" in r.stdout, f"expected the change to fast-forward, got {r.stdout!r}")
    check("was not modified" in r.stdout, f"must say the compare copy was not touched, got {r.stdout!r}")

    after_bytes = open(copy_path, "rb").read()
    check(before_bytes == after_bytes, "--emit-sql must never modify the file it compared against")

    check(os.path.exists(patch_path), "the patch file must be written")
    patch_sql = open(patch_path, encoding="utf-8").read()
    check(new_rec in patch_sql, "the patch must name the new recording literally, not as a placeholder")
    check("DELETE FROM listener_flags" in patch_sql, "--clear-flags must emit the clearing deletes")
    check("SELECT" not in patch_sql.upper(),
          "the comparison's own reads must not leak into the emitted script")

    # Replay the emitted script against a FRESH copy of the same starting
    # point, the same as lempipi's own `sqlite3 < patch.sql` would.
    replay_path = os.path.join(tmp, "replay.db")
    compare_copy(replay_path)
    rc = sqlite3.connect(replay_path)
    rc.executescript(patch_sql)
    rc.commit()
    got_mbid = rc.execute("SELECT mbid FROM passage_recordings WHERE passage_id=1").fetchone()[0]
    check(got_mbid == new_rec, f"replaying the patch must land the same reassignment, got {got_mbid}")
    remaining = rc.execute("SELECT subject_kind, subject_id FROM listener_flags").fetchall()
    check(remaining == [], f"--clear-flags must have removed both flags on replay, got {remaining}")
    rc.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        test_pull(tmp)
        test_push_back_emit_sql(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("flag_sync: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
