#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `export_changes.py` and `apply_changes.py` `[SPEC006 §9]`.

Runs the real pipeline end to end: a LOCAL library with three applied
decisions (a recording reassignment, a boundary edit, an artist correction)
is exported for real, and the resulting `changes.json` is applied for real
against several REMOTE fixtures -- one unchanged since baseline (must
fast-forward with no flag), one already carrying the same correction (must
no-op), one that diverged independently (must conflict and be refused until
resolved), and one missing the file or recording entirely.

    python tools/test_sync_changes.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.join(HERE, "export_changes.py")
APPLY = os.path.join(HERE, "apply_changes.py")

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
CREATE TABLE lowlevel_cache (audio_md5 TEXT NOT NULL, start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL, features BLOB NOT NULL, extractor TEXT NOT NULL,
    extracted_at TEXT NOT NULL, PRIMARY KEY (audio_md5, start_ms, end_ms)) WITHOUT ROWID;
CREATE TABLE id_reviews (passage_id INTEGER PRIMARY KEY, decision TEXT NOT NULL,
    chosen_mbid TEXT, decided_at TEXT NOT NULL,
    chosen_release_mbid TEXT, previous_mbid TEXT, applied_at TEXT, origin TEXT);
CREATE TABLE boundary_reviews (passage_id INTEGER PRIMARY KEY,
    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, audio_md5 TEXT, orig_kind TEXT,
    orig_start_ms INTEGER, orig_end_ms INTEGER, orig_lead_in_ms INTEGER,
    orig_lead_out_ms INTEGER, orig_gain_db REAL, decided_at TEXT NOT NULL,
    applied_at TEXT, origin TEXT,
    fade_in_ms INTEGER, fade_out_ms INTEGER, fade_in_curve TEXT, fade_out_curve TEXT,
    orig_fade_in_ms INTEGER, orig_fade_out_ms INTEGER,
    orig_fade_in_curve TEXT, orig_fade_out_curve TEXT);
CREATE TABLE artist_reviews (recording_mbid TEXT PRIMARY KEY, passage_id INTEGER,
    artist_mbid TEXT NOT NULL, artist_name TEXT NOT NULL,
    previous_artist_mbid TEXT, previous_artist_name TEXT, previous_artist_weight REAL,
    decided_at TEXT NOT NULL, applied_at TEXT, origin TEXT);
"""

OLD_REC = "11111111-1111-1111-1111-111111111111"
NEW_REC = "22222222-2222-2222-2222-222222222222"
SONG_X = "33333333-3333-3333-3333-333333333333"
ART_WRONG = "44444444-4444-4444-4444-444444444444"
ART_RIGHT = "55555555-5555-5555-5555-555555555555"
ART_THIRD = "66666666-6666-6666-6666-666666666666"

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def run(script, *args):
    return subprocess.run([sys.executable, script, *args], capture_output=True, text=True)


def build_local(path: str) -> None:
    """The source installation: three decisions, all applied."""
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-a','/m/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO files VALUES (2,'md5-b','/m/b.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES (1,1,'radio',1000,200000,0,900,-1.0,'src',20,20,'exponential','exponential')")
    # Passage 2 carries its POST-edit fade too -- the boundary edit below
    # `[SPEC-SUI-226]` moved it from the fixed default to 15ms/1500ms.
    c.execute("INSERT INTO passages VALUES (2,2,'radio',2000,190000,250,1200,-2.0,'manual',15,1500,'linear','cosine')")
    c.execute("INSERT INTO recordings VALUES (?,'New Title',NULL,'inherited:mulib')", (NEW_REC,))
    c.execute("INSERT INTO recordings VALUES (?,'Song X',NULL,'inherited:mulib')", (SONG_X,))
    c.execute("INSERT INTO artists VALUES (?,'Right Artist',NULL,'inherited:mulib')", (ART_RIGHT,))
    c.execute("INSERT INTO recording_artists VALUES (?,?,1.0,'review:musicbrainz')", (SONG_X, ART_RIGHT))
    c.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'review:acoustid')", (NEW_REC,))
    c.execute("INSERT INTO passage_recordings VALUES (2,?,1.0,'inherited:mulib')", (SONG_X,))

    # 1. A recording reassignment, applied.
    c.execute(
        "INSERT INTO id_reviews (passage_id,decision,chosen_mbid,previous_mbid,decided_at,applied_at) "
        "VALUES (1,'reassigned',?,?,'2026-08-27 10:00:00','2026-08-27 10:05:00')",
        (NEW_REC, OLD_REC))
    # 2. A boundary edit, applied -- the pre-edit span was 1000-200000, and
    # the fade `[SPEC-SUI-226]` moved off the fixed default too.
    c.execute(
        "INSERT INTO boundary_reviews (passage_id,start_ms,end_ms,lead_in_ms,lead_out_ms,gain_db,"
        "audio_md5,orig_kind,orig_start_ms,orig_end_ms,orig_lead_in_ms,orig_lead_out_ms,orig_gain_db,"
        "decided_at,applied_at,fade_in_ms,fade_out_ms,fade_in_curve,fade_out_curve,"
        "orig_fade_in_ms,orig_fade_out_ms,orig_fade_in_curve,orig_fade_out_curve) "
        "VALUES (2,2000,190000,250,1200,-2.0,'md5-b','radio',1000,200000,"
        "0,900,-1.0,'2026-08-27 11:00:00','2026-08-27 11:05:00',"
        "15,1500,'linear','cosine',20,20,'exponential','exponential')")
    # 3. An artist correction, applied.
    c.execute(
        "INSERT INTO artist_reviews (recording_mbid,passage_id,artist_mbid,artist_name,"
        "previous_artist_mbid,previous_artist_name,previous_artist_weight,decided_at,applied_at) "
        "VALUES (?,2,?,'Right Artist',?,'Wrong Artist',1.0,'2026-08-27 12:00:00','2026-08-27 12:05:00')",
        (SONG_X, ART_RIGHT, ART_WRONG))
    c.commit()
    c.close()


def base_remote(path: str) -> sqlite3.Connection:
    """A remote with the same two files and their PRE-edit state -- what the
    baseline says each was before the local installation's own edits."""
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-a','/srv/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO files VALUES (2,'md5-b','/srv/b.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES (1,1,'radio',1000,200000,0,900,-1.0,'src',20,20,'exponential','exponential')")
    c.execute("INSERT INTO passages VALUES (2,2,'radio',1000,200000,0,900,-1.0,'src',20,20,'exponential','exponential')")
    c.execute("INSERT INTO recordings VALUES (?,'Old Title',NULL,'inherited:mulib')", (OLD_REC,))
    c.execute("INSERT INTO recordings VALUES (?,'Song X',NULL,'inherited:mulib')", (SONG_X,))
    c.execute("INSERT INTO artists VALUES (?,'Wrong Artist',NULL,'inherited:mulib')", (ART_WRONG,))
    c.execute("INSERT INTO recording_artists VALUES (?,?,1.0,'inherited:mulib')", (SONG_X, ART_WRONG))
    c.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'inherited:mulib')", (OLD_REC,))
    c.execute("INSERT INTO passage_recordings VALUES (2,?,1.0,'inherited:mulib')", (SONG_X,))
    return c


def link(conn, passage_id):
    return conn.execute(
        "SELECT mbid FROM passage_recordings WHERE passage_id=?", (passage_id,)).fetchone()[0]


def boundary(conn, passage_id):
    return conn.execute(
        "SELECT start_ms,end_ms,lead_in_ms,lead_out_ms,gain_db FROM passages WHERE passage_id=?",
        (passage_id,)).fetchone()


def boundary_and_fade(conn, passage_id):
    """Like `boundary`, plus the four fade columns `[SPEC-SUI-226]` -- kept
    separate so the older no-fade assertions stay exactly as strict as they
    always were, and only the scenario that actually cares about fade reads
    it."""
    return conn.execute(
        "SELECT start_ms,end_ms,lead_in_ms,lead_out_ms,gain_db,"
        "fade_in_ms,fade_out_ms,fade_in_curve,fade_out_curve FROM passages WHERE passage_id=?",
        (passage_id,)).fetchone()


def artist_of(conn, recording_mbid):
    row = conn.execute(
        "SELECT a.mbid FROM recording_artists ra JOIN artists a ON a.mbid=ra.artist_mbid "
        "WHERE ra.mbid=?", (recording_mbid,)).fetchone()
    return row[0] if row else None


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        local_db = os.path.join(tmp, "local.db")
        build_local(local_db)

        changes_json = os.path.join(tmp, "changes.json")
        r = run(EXPORT, local_db, "-o", changes_json)
        check(r.returncode == 0, f"export exited {r.returncode}: {r.stderr[:300]}")
        check("3 applied change(s)" in r.stdout, f"expected 3 changes, got {r.stdout!r}")
        with open(changes_json) as f:
            doc = json.load(f)
        check(len(doc["changes"]) == 3, f"expected 3 change records, got {len(doc['changes'])}")
        # `[SPEC-SUI-226]`: the exported record must actually carry fade,
        # not just span/lead/gain -- the bug a first version of this export
        # had (a column-order mismatch silently zeroed out every exported
        # boundary_review) would have failed the "3 changes" check above,
        # but would not have caught a *wrong* value landing in the right key.
        boundary_change = next(c for c in doc["changes"] if c["kind"] == "boundary_review")
        check(boundary_change["target"].get("fade_in_ms") == 15,
              f"exported target fade_in_ms wrong, got {boundary_change['target']}")
        check(boundary_change["target"].get("fade_out_curve") == "cosine",
              f"exported target fade_out_curve wrong, got {boundary_change['target']}")
        check(boundary_change["baseline"].get("fade_in_ms") == 20,
              f"exported baseline fade must be the fixed pre-edit default, got {boundary_change['baseline']}")

        # --- Scenario 1: remote unchanged since baseline -> fast-forward ---
        print("unchanged remote: all three fast-forward with no flag needed")
        remote1 = os.path.join(tmp, "remote1.db")
        c1 = base_remote(remote1)
        c1.commit()
        c1.close()  # released before the subprocess opens the same file
        r = run(APPLY, remote1, changes_json, "--commit")
        check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
        check("3 fast-forward" in r.stdout, f"expected 3 fast-forwards, got {r.stdout!r}")
        check("0 conflict" in r.stdout, f"expected no conflicts, got {r.stdout!r}")
        c = sqlite3.connect(remote1)
        check(link(c, 1) == NEW_REC, f"reassignment did not land, got {link(c, 1)}")
        check(boundary(c, 2) == (2000, 190000, 250, 1200, -2.0), f"boundary edit did not land, got {boundary(c, 2)}")
        check(boundary_and_fade(c, 2) == (2000, 190000, 250, 1200, -2.0, 15, 1500, 'linear', 'cosine'),
              f"fade did not land alongside the rest of the boundary edit, got {boundary_and_fade(c, 2)}")
        check(artist_of(c, SONG_X) == ART_RIGHT, f"artist correction did not land, got {artist_of(c, SONG_X)}")
        c.close()

        print("re-applying the same file again is entirely a no-op")
        r = run(APPLY, remote1, changes_json, "--commit")
        check("3 already in sync" in r.stdout, f"expected 3 no-ops, got {r.stdout!r}")

        # --- Scenario 2: remote already carries the same correction ---
        print("remote already matching the target: no-op, nothing rewritten")
        remote2 = os.path.join(tmp, "remote2.db")
        c2 = base_remote(remote2)
        c2.execute("UPDATE passage_recordings SET mbid=? WHERE passage_id=1", (NEW_REC,))
        c2.execute("INSERT INTO recordings VALUES (?,'New Title',NULL,'inherited:mulib')", (NEW_REC,))
        c2.execute("DELETE FROM passage_recordings WHERE passage_id=1 AND mbid=?", (OLD_REC,))
        c2.execute("UPDATE passages SET start_ms=2000,end_ms=190000,lead_in_ms=250,"
                   "lead_out_ms=1200,gain_db=-2.0,fade_in_ms=15,fade_out_ms=1500,"
                   "fade_in_curve='linear',fade_out_curve='cosine' WHERE passage_id=2")
        c2.execute("DELETE FROM recording_artists WHERE mbid=?", (SONG_X,))
        c2.execute("INSERT INTO artists VALUES (?,'Right Artist',NULL,'inherited:mulib')", (ART_RIGHT,))
        c2.execute("INSERT INTO recording_artists VALUES (?,?,1.0,'inherited:mulib')", (SONG_X, ART_RIGHT))
        c2.commit()
        r = run(APPLY, remote2, changes_json, "--commit")
        check("3 already in sync" in r.stdout, f"expected 3 no-ops, got {r.stdout!r}")
        c2.close()

        # --- Scenario 3: remote diverged independently -> conflict ---
        print("remote diverged independently: refused and reported, not overwritten")
        remote3 = os.path.join(tmp, "remote3.db")
        c3 = base_remote(remote3)
        third_rec = "77777777-7777-7777-7777-777777777777"
        c3.execute("INSERT INTO recordings VALUES (?,'A Third Recording',NULL,'inherited:mulib')", (third_rec,))
        c3.execute("UPDATE passage_recordings SET mbid=? WHERE passage_id=1", (third_rec,))
        c3.execute("INSERT INTO id_reviews (passage_id,decision,chosen_mbid,previous_mbid,decided_at,applied_at) "
                   "VALUES (1,'reassigned',?,?,'2026-08-26 09:15:00','2026-08-26 09:20:00')",
                   (third_rec, OLD_REC))
        c3.execute("INSERT INTO artists VALUES (?,'A Third Artist',NULL,'inherited:mulib')", (ART_THIRD,))
        c3.execute("DELETE FROM recording_artists WHERE mbid=?", (SONG_X,))
        c3.execute("INSERT INTO recording_artists VALUES (?,?,1.0,'inherited:mulib')", (SONG_X, ART_THIRD))
        c3.execute("INSERT INTO artist_reviews (recording_mbid,artist_mbid,artist_name,decided_at,applied_at) "
                   "VALUES (?,?,'A Third Artist','2026-08-26 09:00:00','2026-08-26 09:05:00')",
                   (SONG_X, ART_THIRD))
        c3.commit()
        r = run(APPLY, remote3, changes_json, "--commit")
        check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
        check("2 conflict" in r.stdout, f"expected 2 conflicts (id + artist), got {r.stdout!r}")
        check("1 fast-forward" in r.stdout, f"the boundary edit did not diverge and should still land, got {r.stdout!r}")
        check("CONFLICT" in r.stdout, "a conflict must be reported by name")
        check("2026-08-26 09:15:00" in r.stdout, "the target's own decision date must be shown")
        check(link(c3, 1) == third_rec, "a conflict must not overwrite the divergent value")
        check(artist_of(c3, SONG_X) == ART_THIRD, "a conflict must not overwrite the divergent credit")
        check(boundary(c3, 2) == (2000, 190000, 250, 1200, -2.0), "the non-conflicting boundary edit must still land")
        check(boundary_and_fade(c3, 2) == (2000, 190000, 250, 1200, -2.0, 15, 1500, 'linear', 'cosine'),
              "the fade half of the non-conflicting boundary edit must land too, "
              f"got {boundary_and_fade(c3, 2)}")

        print("--resolve theirs applies the incoming change over the divergence")
        r = run(APPLY, remote3, changes_json, "--resolve", "1=theirs", "--resolve", "3=theirs", "--commit")
        check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
        check("2 resolved" in r.stdout, f"expected 2 resolutions applied, got {r.stdout!r}")
        check(link(c3, 1) == NEW_REC, f"theirs must overwrite to the incoming value, got {link(c3, 1)}")
        check(artist_of(c3, SONG_X) == ART_RIGHT, f"theirs must overwrite the credit, got {artist_of(c3, SONG_X)}")
        c3.close()

        print("--resolve ours keeps the local divergence")
        remote4 = os.path.join(tmp, "remote4.db")
        c4 = base_remote(remote4)
        c4.execute("INSERT INTO recordings VALUES ('88888888-8888-8888-8888-888888888888','Yet Another',NULL,'s')")
        c4.execute("UPDATE passage_recordings SET mbid='88888888-8888-8888-8888-888888888888' WHERE passage_id=1")
        c4.commit()
        r = run(APPLY, remote4, changes_json, "--resolve", "1=ours", "--commit")
        check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
        check(link(c4, 1) == "88888888-8888-8888-8888-888888888888",
              "ours must leave the local divergence untouched")
        c4.close()

        # --- Scenario 4: remote is missing the file/recording entirely ---
        print("remote missing the file/recording entirely: reported, not an error")
        remote5 = os.path.join(tmp, "remote5.db")
        c5 = sqlite3.connect(remote5)
        c5.executescript(SCHEMA)
        c5.commit()
        c5.close()
        r = run(APPLY, remote5, changes_json, "--commit")
        check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
        check("3 not present here" in r.stdout, f"expected all 3 missing, got {r.stdout!r}")

        # --- Scenario 5: a real, older library `[SPEC-DF-104]` was written
        # for -- `id_reviews` predating `origin`, and `boundary_reviews`/
        # `artist_reviews` never created at all. Caught only by testing
        # against an actual pre-existing copy of the real library, not by
        # any fixture built with the current schema from the start.
        print("an older library missing `origin` and two whole tables migrates cleanly")
        remote7 = os.path.join(tmp, "remote7.db")
        c7 = sqlite3.connect(remote7)
        c7.executescript("""
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
            -- The pre-[SPEC-DF-104] shape: no `origin` column at all.
            CREATE TABLE id_reviews (passage_id INTEGER PRIMARY KEY, decision TEXT NOT NULL,
                chosen_mbid TEXT, decided_at TEXT NOT NULL,
                chosen_release_mbid TEXT, previous_mbid TEXT, applied_at TEXT);
            -- No boundary_reviews or artist_reviews table at all -- a
            -- library no vipunen-support Lempi has ever opened since those
            -- features shipped, which describes every real appliance today.
        """)
        c7.execute("INSERT INTO files VALUES (1,'md5-a','/srv/a.mp3',1,1.0,'mp3',300000,'t','t')")
        c7.execute("INSERT INTO files VALUES (2,'md5-b','/srv/b.mp3',1,1.0,'mp3',300000,'t','t')")
        c7.execute("INSERT INTO passages VALUES (1,1,'radio',1000,200000,0,900,-1.0,'src')")
        c7.execute("INSERT INTO passages VALUES (2,2,'radio',1000,200000,0,900,-1.0,'src')")
        c7.execute("INSERT INTO recordings VALUES (?,'Old Title',NULL,'inherited:mulib')", (OLD_REC,))
        c7.execute("INSERT INTO recordings VALUES (?,'Song X',NULL,'inherited:mulib')", (SONG_X,))
        c7.execute("INSERT INTO artists VALUES (?,'Wrong Artist',NULL,'inherited:mulib')", (ART_WRONG,))
        c7.execute("INSERT INTO recording_artists VALUES (?,?,1.0,'inherited:mulib')", (SONG_X, ART_WRONG))
        c7.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'inherited:mulib')", (OLD_REC,))
        c7.execute("INSERT INTO passage_recordings VALUES (2,?,1.0,'inherited:mulib')", (SONG_X,))
        c7.commit()
        c7.close()
        r = run(APPLY, remote7, changes_json, "--commit")
        check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
        check("3 fast-forward" in r.stdout, f"expected all 3 to still land, got {r.stdout!r}")
        c7 = sqlite3.connect(remote7)
        check(link(c7, 1) == NEW_REC, "the reassignment must land despite the missing origin column")
        check(boundary(c7, 2) == (2000, 190000, 250, 1200, -2.0),
              "the boundary edit must land despite boundary_reviews never existing here")
        # `passages` here started with none of the four fade columns at all
        # `[SPEC-SUI-226]` -- `ensure_passages_fade_columns` must have added
        # them on the fly for the fade half of the same edit to land, the
        # same auto-migration `ensure_review_tables` already does for the
        # three review tables themselves.
        check(boundary_and_fade(c7, 2) == (2000, 190000, 250, 1200, -2.0, 15, 1500, 'linear', 'cosine'),
              f"the fade half must land even though passages lacked the columns entirely, "
              f"got {boundary_and_fade(c7, 2)}")
        check(artist_of(c7, SONG_X) == ART_RIGHT,
              "the artist correction must land despite artist_reviews never existing here")
        c7.close()

        # --- Rehearsal never writes ---
        print("rehearsal (no --commit) never writes, even for fast-forwards")
        remote6 = os.path.join(tmp, "remote6.db")
        c_r6 = base_remote(remote6)
        c_r6.commit()
        c_r6.close()
        r = run(APPLY, remote6, changes_json)
        check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
        c6 = sqlite3.connect(remote6)
        check(link(c6, 1) == OLD_REC, "a rehearsal must not change anything")
        c6.close()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("sync_changes: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
