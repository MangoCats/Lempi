#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `console.py`'s `flag_sync_status()`/`unflag_everywhere()`
`[REQ-VIS-265]`.

`console._peek` (remote reads, still `console.py`'s own) and
`lempi_control._lempi_set_flag`/`lempi_control._remote_set_flag` (the two
writes -- both signals into a running Lempi, never a `listener_flags` write
from this process, split into `lempi_control.py` per the console.py/
lempi_control.py split) are all faked, so these run with no `ssh`, no
network, and no co-resident player. What is under test is the subject
resolution (`[SPEC-DF-112]`'s own passage+recording shape, reused rather
than reinvented) and the three-outcome remote logic these two functions
share with `remote_status()`.

    python tools/test_console_unflag.py
"""

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import console  # noqa: E402
import lempi_control  # noqa: E402

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    path TEXT, size_bytes INTEGER, mtime REAL, format TEXT,
    duration_ms INTEGER, first_seen TEXT, last_seen TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT, start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT,
    weight REAL DEFAULT 1.0, source TEXT);
CREATE TABLE listener_flags (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    flagged_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID;
"""

REC = "local:audio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


def build(flag_rows=()):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-a','/m/a.mp3',1,1.0,'mp3',300000,'t','t')")
    c.execute("INSERT INTO passages VALUES (1,1,'radio',0,200000,0,900,-1.0,'src')")
    c.execute("INSERT INTO passage_recordings VALUES (1,?,1.0,'local:ingest')", (REC,))
    for kind, sid in flag_rows:
        c.execute("INSERT INTO listener_flags VALUES (?,?,'2026-09-01T00:00:00',NULL)", (kind, sid))
    return c


class FakeJobs:
    def __init__(self, remote):
        self._remote = remote

    def get_remote(self):
        return self._remote


def test_passage_flag_subjects() -> None:
    print("passage_flag_subjects(): the passage itself, plus every linked recording")
    c = build()
    subjects = console.passage_flag_subjects(c, 1)
    check(set(subjects) == {("passage", "1"), ("recording", REC)}, f"got {subjects}")


def test_local_flag_detection() -> None:
    print("passage_flagged_locally(): either subject shape counts as flagged")
    check(console.passage_flagged_locally(build(), [("passage", "1"), ("recording", REC)]) is False,
          "nothing inserted -- must not be flagged")
    check(console.passage_flagged_locally(build([("recording", REC)]),
                                           [("passage", "1"), ("recording", REC)]) is True,
          "a recording-keyed flag must count")
    check(console.passage_flagged_locally(build([("passage", "1")]),
                                           [("passage", "1"), ("recording", REC)]) is True,
          "a passage-keyed flag must count too")


def test_flag_sync_status_no_remote_configured() -> None:
    print("flag_sync_status(): no remote configured -- local only, no subprocess touched")
    console.STATE["jobs"] = FakeJobs(None)
    real_peek = console._peek
    console._peek = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called"))
    try:
        r = console.flag_sync_status(build([("recording", REC)]), 1)
    finally:
        console._peek = real_peek
    check(r == {"local": True, "remote": None, "reachable": False, "remote_pid": None, "remote_mbids": []},
          f"got {r}")


def test_flag_sync_status_unreachable() -> None:
    print("flag_sync_status(): lempi02w unreachable is reported, never raised")
    console.STATE["jobs"] = FakeJobs("pi@lempi02w:/srv/library/library.db")
    real_peek = console._peek
    console._peek = lambda remote, kind, args, timeout=12.0: {"ok": False, "error": "no route to host"}
    try:
        r = console.flag_sync_status(build(), 1)
    finally:
        console._peek = real_peek
    check(r == {"local": False, "remote": None, "reachable": False, "remote_pid": None, "remote_mbids": []},
          f"got {r}")


def test_flag_sync_status_agrees_and_diverges() -> None:
    print("flag_sync_status(): local vs. remote agreement and divergence, both reported")
    console.STATE["jobs"] = FakeJobs("pi@lempi02w:/srv/library/library.db")
    real_peek = console._peek

    console._peek = lambda remote, kind, args, timeout=12.0: {
        "ok": True, "current": {"remote_passage_id": 42, "flagged": 0, "remote_mbids": json.dumps([REC])}}
    try:
        r = console.flag_sync_status(build(), 1)
    finally:
        console._peek = real_peek
    check(r == {"local": False, "remote": False, "reachable": True, "remote_pid": 42,
                "remote_mbids": [REC]},
          f"both unflagged must agree, got {r}")

    console._peek = lambda remote, kind, args, timeout=12.0: {
        "ok": True, "current": {"remote_passage_id": 42, "flagged": 1}}
    try:
        r = console.flag_sync_status(build(), 1)  # local still unflagged
    finally:
        console._peek = real_peek
    check(r["local"] is False and r["remote"] is True,
          f"a remote-only flag must be reported as a genuine divergence, got {r}")


def test_unflag_everywhere_no_remote() -> None:
    print("unflag_everywhere(): local clear only, when no remote is configured")
    console.STATE["jobs"] = FakeJobs(None)
    calls = []
    real_set = lempi_control._lempi_set_flag
    lempi_control._lempi_set_flag = lambda port, kind, sid, flagged, timeout=2.0: calls.append(
        (kind, sid, flagged)) or True
    try:
        r = console.unflag_everywhere(build([("recording", REC)]), 1)
    finally:
        lempi_control._lempi_set_flag = real_set
    check(set(calls) == {("passage", "1", False), ("recording", REC, False)}, f"got {calls}")
    check(r["local"] == {"ok": True, "cleared": 2, "of": 2}, f"got {r}")
    check(r["remote"] == {"configured": False}, f"got {r}")


def test_unflag_everywhere_translates_passage_id_for_remote() -> None:
    print("unflag_everywhere(): the passage-keyed subject is translated to the "
          "remote's OWN local passage_id before being sent there")
    console.STATE["jobs"] = FakeJobs("pi@lempi02w:/srv/library/library.db")
    real_peek = console._peek
    real_local, real_remote = lempi_control._lempi_set_flag, lempi_control._remote_set_flag
    console._peek = lambda remote, kind, args, timeout=12.0: {
        "ok": True, "current": {"remote_passage_id": 999, "flagged": 1,
                                 "remote_mbids": json.dumps([REC])}}
    lempi_control._lempi_set_flag = lambda *a, **k: True
    remote_calls = []
    lempi_control._remote_set_flag = lambda remote, port, kind, sid, flagged, timeout=8.0: (
        remote_calls.append((kind, sid, flagged)) or True)
    try:
        r = console.unflag_everywhere(build(), 1)
    finally:
        console._peek = real_peek
        lempi_control._lempi_set_flag, lempi_control._remote_set_flag = real_local, real_remote
    check(("passage", "999", False) in remote_calls,
          f"the LOCAL pid (1) must never be sent to the remote as-is, got {remote_calls}")
    check(("recording", REC, False) in remote_calls, f"got {remote_calls}")
    check(r["remote"] == {"configured": True, "reachable": True, "ok": True, "cleared": 2, "of": 2},
          f"got {r}")


def test_unflag_everywhere_remote_missing_passage() -> None:
    print("unflag_everywhere(): a remote with no such passage skips only the "
          "passage-keyed subject, and still clears the recording-keyed one")
    console.STATE["jobs"] = FakeJobs("pi@lempi02w:/srv/library/library.db")
    real_peek = console._peek
    real_local, real_remote = lempi_control._lempi_set_flag, lempi_control._remote_set_flag
    console._peek = lambda remote, kind, args, timeout=12.0: {
        "ok": True, "current": {"remote_passage_id": None, "flagged": 0, "remote_mbids": "[]"}}
    lempi_control._lempi_set_flag = lambda *a, **k: True
    remote_calls = []
    lempi_control._remote_set_flag = lambda remote, port, kind, sid, flagged, timeout=8.0: (
        remote_calls.append((kind, sid, flagged)) or True)
    try:
        r = console.unflag_everywhere(build(), 1)
    finally:
        console._peek = real_peek
        lempi_control._lempi_set_flag, lempi_control._remote_set_flag = real_local, real_remote
    check(remote_calls == [("recording", REC, False)],
          f"only the recording subject can be resolved remotely, got {remote_calls}")
    check(r["remote"]["of"] == 1, f"got {r}")


def test_unflag_everywhere_clears_a_stale_remote_recording_too() -> None:
    """Found live 2026-09-01: an id correction accepted locally (a real
    MusicBrainz mbid) but never yet pushed left lempi02w still linked to the
    *old* `local:audio:` placeholder -- the flag was set under that old id,
    and resolving subjects only from this library's own current link
    reported success while clearing nothing where the flag actually was.
    """
    print("unflag_everywhere(): a recording the remote still links but this "
          "library has since moved away from is cleared too, not just this "
          "library's own current one")
    console.STATE["jobs"] = FakeJobs("pi@lempi02w:/srv/library/library.db")
    real_peek = console._peek
    real_local, real_remote = lempi_control._lempi_set_flag, lempi_control._remote_set_flag
    old_remote_mbid = "local:audio:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    console._peek = lambda remote, kind, args, timeout=12.0: {
        "ok": True, "current": {"remote_passage_id": 1, "flagged": 1,
                                 "remote_mbids": json.dumps([old_remote_mbid])}}
    lempi_control._lempi_set_flag = lambda *a, **k: True
    remote_calls = []
    lempi_control._remote_set_flag = lambda remote, port, kind, sid, flagged, timeout=8.0: (
        remote_calls.append((kind, sid, flagged)) or True)
    try:
        # `build()` links passage 1 to REC, a DIFFERENT mbid than what the
        # remote itself reports -- the exact divergence found live.
        r = console.unflag_everywhere(build(), 1)
    finally:
        console._peek = real_peek
        lempi_control._lempi_set_flag, lempi_control._remote_set_flag = real_local, real_remote
    check(("recording", old_remote_mbid, False) in remote_calls,
          f"the remote's OWN stale link must be cleared -- this is the bug found live, got {remote_calls}")
    check(("recording", REC, False) in remote_calls,
          f"this library's own current link must still be cleared too, got {remote_calls}")
    check(r["remote"]["of"] == 3, f"passage + both recordings, got {r}")


def test_unflag_subject_everywhere_no_remote() -> None:
    print("unflag_subject_everywhere(): local clear only, when no remote is configured")
    console.STATE["jobs"] = FakeJobs(None)
    calls = []
    real_set = lempi_control._lempi_set_flag
    lempi_control._lempi_set_flag = lambda port, kind, sid, flagged, timeout=2.0: calls.append(
        (kind, sid, flagged)) or True
    try:
        r = console.unflag_subject_everywhere("recording", REC)
    finally:
        lempi_control._lempi_set_flag = real_set
    check(calls == [("recording", REC, False)], f"got {calls}")
    check(r == {"local": {"ok": True}, "remote": {"configured": False}}, f"got {r}")


def test_unflag_subject_everywhere_sends_the_identical_subject_to_the_remote() -> None:
    print("unflag_subject_everywhere(): no passage, no union, no translation -- the "
          "identical (kind, subject_id) reaches the remote as given")
    console.STATE["jobs"] = FakeJobs("pi@lempi02w:/srv/library/library.db")
    real_local, real_remote = lempi_control._lempi_set_flag, lempi_control._remote_set_flag
    lempi_control._lempi_set_flag = lambda *a, **k: True
    remote_calls = []
    lempi_control._remote_set_flag = lambda remote, port, kind, sid, flagged, timeout=8.0: (
        remote_calls.append((kind, sid, flagged)) or True)
    try:
        r = console.unflag_subject_everywhere("recording", REC)
    finally:
        lempi_control._lempi_set_flag, lempi_control._remote_set_flag = real_local, real_remote
    check(remote_calls == [("recording", REC, False)], f"got {remote_calls}")
    check(r == {"local": {"ok": True}, "remote": {"configured": True, "ok": True}}, f"got {r}")


def test_unflag_subject_everywhere_reports_a_local_failure_honestly() -> None:
    print("unflag_subject_everywhere(): a failed local clear is reported, not hidden "
          "behind a remote success")
    console.STATE["jobs"] = FakeJobs(None)
    real_set = lempi_control._lempi_set_flag
    lempi_control._lempi_set_flag = lambda *a, **k: False
    try:
        r = console.unflag_subject_everywhere("passage", "1")
    finally:
        lempi_control._lempi_set_flag = real_set
    check(r["local"]["ok"] is False, f"got {r}")


def main() -> int:
    test_passage_flag_subjects()
    test_local_flag_detection()
    test_flag_sync_status_no_remote_configured()
    test_flag_sync_status_unreachable()
    test_flag_sync_status_agrees_and_diverges()
    test_unflag_everywhere_no_remote()
    test_unflag_everywhere_translates_passage_id_for_remote()
    test_unflag_everywhere_remote_missing_passage()
    test_unflag_everywhere_clears_a_stale_remote_recording_too()
    test_unflag_subject_everywhere_no_remote()
    test_unflag_subject_everywhere_sends_the_identical_subject_to_the_remote()
    test_unflag_subject_everywhere_reports_a_local_failure_honestly()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console unflag: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
