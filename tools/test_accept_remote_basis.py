#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `accept_remote_basis.py` `[SPEC-DF-116..117]`.

Runs the real tool as a subprocess against real fixture databases, the same
posture `test_sync_changes.py` already uses. The last test automates
IMPL008's own "done" claim end to end: accept a remote's diverged boundary
as the new local baseline, make a local edit on top of it, then confirm
`apply_changes.py` classifies the resulting push as fast-forward rather than
a conflict -- the actual point of the feature, not assumed from the pieces
working separately.

    python tools/test_accept_remote_basis.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ACCEPT = os.path.join(HERE, "accept_remote_basis.py")
EXPORT_CHANGES = os.path.join(HERE, "export_changes.py")
APPLY_CHANGES = os.path.join(HERE, "apply_changes.py")
sys.path.insert(0, HERE)
import remote_peek as rp  # noqa: E402

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

REC_A = "11111111-1111-1111-1111-111111111111"
REC_B = "22222222-2222-2222-2222-222222222222"

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def run(*args):
    return subprocess.run([sys.executable, ACCEPT, *args], capture_output=True, text=True)


def build(path: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-a','/m/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES "
              "(1,1,'radio',1000,200000,0,900,-1.0,'src',20,20,'exponential','exponential')")
    c.execute("INSERT INTO recordings VALUES (?,'A Song',NULL,'inherited:mulib')", (REC_A,))
    c.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'inherited:mulib')", (REC_A,))
    c.commit()
    c.close()


def boundary(conn, passage_id=1):
    return conn.execute(
        "SELECT start_ms,end_ms,lead_in_ms,lead_out_ms,gain_db FROM passages WHERE passage_id=?",
        (passage_id,)).fetchone()


def boundary_and_fade(conn, passage_id=1):
    """Like `boundary`, plus the four fade columns `[SPEC-SUI-226]`."""
    return conn.execute(
        "SELECT start_ms,end_ms,lead_in_ms,lead_out_ms,gain_db,"
        "fade_in_ms,fade_out_ms,fade_in_curve,fade_out_curve FROM passages WHERE passage_id=?",
        (passage_id,)).fetchone()


def credit(conn, passage_id=1):
    return conn.execute(
        "SELECT mbid, source FROM passage_recordings WHERE passage_id=?", (passage_id,)).fetchone()


ANCHOR_ARGS = ["--audio-md5", "md5-a", "--passage-kind", "radio", "--start-ms", "1000", "--end-ms", "200000"]


def test_boundary_accept(tmp: str) -> None:
    print("accepting a remote boundary value rewrites passages, rehearsal never writes")
    db = os.path.join(tmp, "boundary.db")
    build(db)
    value = '{"start_ms":2000,"end_ms":190000,"lead_in_ms":250,"lead_out_ms":1200,"gain_db":-2.0}'

    r = run(db, "--kind", "boundary_review", *ANCHOR_ARGS, "--value", value)
    check(r.returncode == 0, f"rehearsal exited {r.returncode}: {r.stderr[:300]}")
    c = sqlite3.connect(db)
    check(boundary(c) == (1000, 200000, 0, 900, -1.0), "rehearsal must not write")
    c.close()

    r = run(db, "--kind", "boundary_review", *ANCHOR_ARGS, "--value", value, "--commit", "--json")
    check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:300]}")
    check('"ok": true' in r.stdout, f"expected an ok:true json line, got {r.stdout!r}")
    c = sqlite3.connect(db)
    check(boundary(c) == (2000, 190000, 250, 1200, -2.0), f"expected the remote value to land, got {boundary(c)}")
    c.close()


def test_id_accept(tmp: str) -> None:
    print("accepting a remote id value known locally rewrites passage_recordings")
    db = os.path.join(tmp, "id.db")
    build(db)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO recordings VALUES (?,'B Song',NULL,'inherited:mulib')", (REC_B,))
    c.commit()
    c.close()

    r = run(db, "--kind", "id_review", *ANCHOR_ARGS, "--value", f'{{"mbid":"{REC_B}"}}', "--commit", "--json")
    check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:300]}")
    c = sqlite3.connect(db)
    got = credit(c)
    check(got == (REC_B, "remote-basis"), f"expected ({REC_B!r}, 'remote-basis'), got {got}")
    c.close()

    print("accepting a remote id unknown locally is refused, not fabricated")
    db2 = os.path.join(tmp, "id-unknown.db")
    build(db2)
    unknown = "99999999-9999-9999-9999-999999999999"
    r = run(db2, "--kind", "id_review", *ANCHOR_ARGS, "--value", f'{{"mbid":"{unknown}"}}', "--commit", "--json")
    check(r.returncode == 1, f"expected a refusal exit, got {r.returncode}")
    check("not known here" in r.stdout, f"expected a plain refusal reason, got {r.stdout!r}")
    check('"ok": false' in r.stdout, f"expected an ok:false json line, got {r.stdout!r}")
    c = sqlite3.connect(db2)
    check(credit(c) == (REC_A, "inherited:mulib"), "a refused accept must leave the local value untouched")
    c.close()


def test_unmatched_anchor(tmp: str) -> None:
    print("an anchor nothing here matches is reported, not a crash")
    db = os.path.join(tmp, "unmatched.db")
    build(db)
    r = run(db, "--kind", "boundary_review", "--audio-md5", "md5-nope", "--passage-kind", "radio",
            "--start-ms", "0", "--end-ms", "1", "--value", "{}", "--commit", "--json")
    check(r.returncode == 1, f"expected exit 1, got {r.returncode}")
    check("nothing to accept a basis for" in r.stdout, f"got {r.stdout!r}")


def test_closes_the_loop(tmp: str) -> None:
    """IMPL008's own "done" claim, automated: accept a remote's diverged
    boundary as the new baseline, make a local edit on top of it (the same
    shape Lempi's own editor would leave in `boundary_reviews`), export it,
    and confirm `apply_changes.py` against the still-diverged remote copy
    classifies it fast-forward -- not a conflict.
    """
    print("accept, then edit, then push: apply_changes.py classifies it fast-forward, not conflict")
    lempi02w = os.path.join(tmp, "lempi02w.db")
    build(lempi02w)
    vc = sqlite3.connect(lempi02w)
    # lempi02w diverged independently -- amplitude was re-analysed there, with
    # no involvement from this desktop at all. The trim points
    # (start_ms/end_ms) are untouched: that identity is the anchor
    # `remote_peek.py` finds this row by, same as `apply_changes.py`'s own
    # `resolve_passage` would locally -- a *moved* trim point is a
    # different, harder case this targeted read does not claim to solve.
    # Fade `[SPEC-SUI-226]` diverged there too, off its fixed default.
    vc.execute("UPDATE passages SET lead_in_ms=250,lead_out_ms=1200,gain_db=-2.0,"
               "fade_in_ms=15,fade_out_ms=1500,fade_in_curve='linear',fade_out_curve='cosine' "
               "WHERE passage_id=1")
    vc.commit()
    vc.close()

    desktop = os.path.join(tmp, "desktop.db")
    build(desktop)  # still at the pre-divergence lead-in/out/gain/fade

    # What remote_peek.py would have returned, computed with its own SQL
    # against a plain local copy standing in for the ssh round trip.
    anchor = {"audio_md5": "md5-a", "passage_kind": "radio", "start_ms": 1000, "end_ms": 200000}
    rc = sqlite3.connect(lempi02w)
    row = rc.execute(rp.sql_for("boundary_review", anchor)).fetchone()
    rc.close()
    check(row is not None, "the anchor must still resolve on lempi02w -- its identity did not move")
    remote_value = dict(zip(
        ["start_ms", "end_ms", "lead_in_ms", "lead_out_ms", "gain_db",
         "fade_in_ms", "fade_out_ms", "fade_in_curve", "fade_out_curve"], row))
    check(remote_value == {"start_ms": 1000, "end_ms": 200000, "lead_in_ms": 250,
                            "lead_out_ms": 1200, "gain_db": -2.0,
                            "fade_in_ms": 15, "fade_out_ms": 1500,
                            "fade_in_curve": "linear", "fade_out_curve": "cosine"}, f"got {remote_value}")

    r = run(desktop, "--kind", "boundary_review", *ANCHOR_ARGS,
            "--value", json.dumps(remote_value), "--commit")
    check(r.returncode == 0, f"accept exited {r.returncode}: {r.stderr[:300]}")
    dc = sqlite3.connect(desktop)
    check(boundary(dc) == (1000, 200000, 250, 1200, -2.0), "the local baseline must now match lempi02w's")
    check(boundary_and_fade(dc) == (1000, 200000, 250, 1200, -2.0, 15, 1500, "linear", "cosine"),
          f"the fade half of the accepted basis must land too, got {boundary_and_fade(dc)}")

    # Now a local edit ON TOP of the accepted basis -- Lempi's own editor
    # would capture exactly this as `orig_*`, since that is what `passages`
    # held the moment editing began. Fade moves again too, further off what
    # was just accepted.
    dc.execute("UPDATE passages SET start_ms=2500,end_ms=190500,lead_in_ms=300,"
               "lead_out_ms=1100,gain_db=-1.5,fade_in_ms=25,fade_out_ms=1600,"
               "fade_in_curve='cosine',fade_out_curve='exponential' WHERE passage_id=1")
    dc.execute(
        "INSERT INTO boundary_reviews (passage_id,start_ms,end_ms,lead_in_ms,lead_out_ms,gain_db,"
        "audio_md5,orig_kind,orig_start_ms,orig_end_ms,orig_lead_in_ms,orig_lead_out_ms,orig_gain_db,"
        "decided_at,applied_at,fade_in_ms,fade_out_ms,fade_in_curve,fade_out_curve,"
        "orig_fade_in_ms,orig_fade_out_ms,orig_fade_in_curve,orig_fade_out_curve) "
        "VALUES (1,2500,190500,300,1100,-1.5,'md5-a','radio',1000,200000,"
        "250,1200,-2.0,'2026-08-29 10:00:00','2026-08-29 10:00:05',"
        "25,1600,'cosine','exponential',15,1500,'linear','cosine')")
    dc.commit()
    dc.close()

    changes_json = os.path.join(tmp, "changes.json")
    r = subprocess.run([sys.executable, EXPORT_CHANGES, desktop, "-o", changes_json],
                       capture_output=True, text=True)
    check(r.returncode == 0, f"export exited {r.returncode}: {r.stderr[:300]}")

    r = subprocess.run([sys.executable, APPLY_CHANGES, lempi02w, changes_json, "--commit"],
                       capture_output=True, text=True)
    check(r.returncode == 0, f"apply exited {r.returncode}: {r.stderr[:400]}")
    check("1 fast-forward" in r.stdout, f"expected a fast-forward, got {r.stdout!r}")
    check("0 conflict" in r.stdout,
          f"the accepted basis (fade included) must prevent a conflict, got {r.stdout!r}")
    vc = sqlite3.connect(lempi02w)
    check(boundary(vc) == (2500, 190500, 300, 1100, -1.5),
          f"the edit must land on lempi02w, got {boundary(vc)}")
    check(boundary_and_fade(vc) == (2500, 190500, 300, 1100, -1.5, 25, 1600, "cosine", "exponential"),
          f"the fade half of the edit must land on lempi02w too, got {boundary_and_fade(vc)}")
    vc.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        test_boundary_accept(tmp)
        test_id_accept(tmp)
        test_unmatched_anchor(tmp)
        test_closes_the_loop(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("accept_remote_basis: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
