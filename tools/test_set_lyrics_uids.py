#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `set_lyrics_uids.py` [IMPL-UID-063].

    python tools/test_set_lyrics_uids.py
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import set_lyrics_uids as slu  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


UA, UB, UC = "a" * 32, "b" * 32, "c" * 32
LYRICS = ("CREATE TABLE passage_lyrics (audio_md5 TEXT NOT NULL, kind TEXT NOT NULL, start_ms INTEGER NOT NULL, "
          "end_ms INTEGER NOT NULL, mbid TEXT, text TEXT NOT NULL, no_words INTEGER NOT NULL DEFAULT 0, "
          "updated_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (audio_md5, kind, start_ms))")


def build(tmp):
    """One file whole: A's radio cut 4-190 s (uid a), its album cut 0-200 s (b), and
    the next song's radio cut 203-395 s (c). Words: A's radio cut saved twice, the
    older under a former start; the album cut once; one under a retired digest of
    the same file; and one for a segment no passage holds."""
    db = os.path.join(tmp, "whole.db")
    c = sqlite3.connect(db)
    c.executescript(f"""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
            start_ms INTEGER, end_ms INTEGER, passage_uid TEXT);
        CREATE TABLE audio_md5_aliases (old_md5 TEXT PRIMARY KEY, new_md5 TEXT NOT NULL);
        INSERT INTO files VALUES (1, 'm1');
        INSERT INTO passages VALUES (12, 1, 'radio', 4000, 190000, '{UA}'),
                                    (11, 1, 'album', 0, 200000, '{UB}'),
                                    (14, 1, 'radio', 203000, 395000, '{UC}');
        INSERT INTO audio_md5_aliases VALUES ('m1-old', 'm1');
        {LYRICS};
        INSERT INTO passage_lyrics VALUES ('m1', 'radio', 4000, 190000, 'rec-a', 'words now', 0, '2026-11-10 08:00:00', 'here');
        INSERT INTO passage_lyrics VALUES ('m1', 'radio', 3000, 190000, 'rec-a', 'words then', 0, '2026-11-01 08:00:00', 'here');
        INSERT INTO passage_lyrics VALUES ('m1', 'album', 0, 200000, 'rec-a', 'the album cut', 0, '2026-11-02 08:00:00', 'here');
        INSERT INTO passage_lyrics VALUES ('m1-old', 'radio', 203000, 395000, 'rec-c', 'under the old digest', 0,
                                           '2026-11-03 08:00:00', 'there');
        INSERT INTO passage_lyrics VALUES ('m9', 'radio', 0, 100000, 'rec-z', 'a file not here', 0,
                                           '2026-11-04 08:00:00', 'there');
    """)
    c.commit()
    c.close()
    return db


def rows(db, sql):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def main() -> int:
    tmp = tempfile.mkdtemp()
    db = build(tmp)
    before = rows(db, "SELECT * FROM passage_lyrics ORDER BY audio_md5, kind, start_ms")
    print("a rehearsal writes nothing")
    check(slu.main([db]) == 0, "rehearses")
    check(rows(db, "SELECT * FROM passage_lyrics ORDER BY audio_md5, kind, start_ms") == before,
          "and the table is as it was -- no column added either")
    print("the wrong count is refused")
    try:
        slu.main([db, "--commit", "--expect", "9", "--backups", os.path.join(tmp, "bk")])
        check(False, "a wrong --expect must be refused")
    except SystemExit as e:
        check("expected 9" in str(e), f"{e}")
    print("committed: each row its passage's uid, every other column untouched")
    check(slu.main([db, "--commit", "--expect", "4", "--backups", os.path.join(tmp, "bk")]) == 0, "commits")
    got = dict(((m, k, s), u) for m, k, s, u in rows(db, "SELECT audio_md5, kind, start_ms, passage_uid FROM passage_lyrics"))
    check(got[("m1", "radio", 4000)] == UA and got[("m1", "radio", 3000)] == UA,
          f"A's radio cut, both saves -- the older under a former start: {got}")
    check(got[("m1", "album", 0)] == UB, f"the album cut its own, not the radio cut's: {got}")
    check(got[("m1-old", "radio", 203000)] == UC, f"a retired digest followed to its file: {got}")
    check(got[("m9", "radio", 0)] is None, f"a segment no passage holds keeps none: {got}")
    after = rows(db, "SELECT audio_md5, kind, start_ms, end_ms, mbid, text, no_words, updated_at, origin "
                     "FROM passage_lyrics ORDER BY audio_md5, kind, start_ms")
    check(after == before, "nothing else about any row changed, and none was added or removed")
    check(os.path.exists(os.path.join(tmp, "bk")) and os.listdir(os.path.join(tmp, "bk")), "a backup was taken first")
    print("a second run has nothing to do")
    check(slu.main([db, "--commit", "--expect", "0", "--backups", os.path.join(tmp, "bk2")]) == 0, "nothing left to set")

    print("a row whose segment two passages share is left without, and said, never guessed")
    rows_, by = [dict(audio_md5="m1", file="m1", kind="radio", start_ms=4000, end_ms=190000, updated_at="t2",
                      passage_uid=None)], {("m1", "radio"): [(4000, 190000, UA), (5000, 191000, UC)]}
    todo, left = slu.plan(rows_, by)
    check(todo == {} and left and "2 passages" in left[0][1], f"a row two passages share is left, said: {left}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("set_lyrics_uids: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
