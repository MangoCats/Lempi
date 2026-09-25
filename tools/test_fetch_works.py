#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `fetch_works.py`'s handling of no answer `[GDE-WRK-130]`.

It runs unattended inside `induct` now, where a network outage used to mark
every recording it tried as done, permanently. `fetch` is faked, so nothing
here touches MusicBrainz, and `RATE_S` is zeroed.

    python tools/test_fetch_works.py
"""

import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_works as fw  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def library(mbids) -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE recordings (mbid TEXT PRIMARY KEY);"
        "CREATE TABLE files (file_id INTEGER PRIMARY KEY);"
        "CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);"
        "CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, kind TEXT);"
        "CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);")
    for i, m in enumerate(mbids, 1):
        c.execute("INSERT INTO passages VALUES (?, 'radio')", (i,))
        c.execute("INSERT INTO passage_recordings VALUES (?, ?)", (i, m))
    c.commit()
    c.close()
    return path


def run(db, cache, answers, *extra):
    """`answers` maps mbid -> response, or None for no answer."""
    asked = []

    def fake(mbid):
        asked.append(mbid)
        return answers.get(mbid)

    old = (fw.fetch, fw.RATE_S, sys.argv)
    fw.fetch, fw.RATE_S = fake, 0
    sys.argv = ["fetch_works.py", db, "--cache", cache, *extra]
    try:
        rc = fw.main()
    finally:
        fw.fetch, fw.RATE_S, sys.argv = old
    return rc, asked


def cached(cache):
    c = sqlite3.connect(cache)
    try:
        return dict(c.execute("SELECT mbid, response FROM work_cache"))
    finally:
        c.close()


def test_no_answer_is_not_cached_and_is_asked_again():
    print("no answer: not cached, asked again next run; a 404 is an answer")
    db = library(["a", "b", "c"])
    cache = db + ".cache"
    rc, _ = run(db, cache, {"a": '{"relations": []}', "b": None, "c": json.dumps({"error": "404"})})
    check(rc == 0, f"a partial run still exits 0, got {rc}")
    got = cached(cache)
    check(set(got) == {"a", "c"}, f"only answers are cached, got {sorted(got)}")
    _, asked = run(db, cache, {"b": '{"relations": []}'})
    check(asked == ["b"], f"the next run asks only for what got no answer, asked {asked}")
    for p in (db, cache):
        os.unlink(p)


def test_a_legacy_unreachable_row_is_asked_again():
    print("a row cached as unreachable by the old code is fetched again")
    db = library(["a"])
    cache = db + ".cache"
    c = sqlite3.connect(cache)
    c.executescript(fw.CACHE_DDL)
    c.execute("INSERT INTO work_cache VALUES ('a', ?, 0)", (fw.UNREACHABLE,))
    c.commit()
    c.close()
    _, asked = run(db, cache, {"a": '{"relations": []}'})
    check(asked == ["a"], f"expected a retry of the unreachable row, asked {asked}")
    check(cached(cache)["a"] == '{"relations": []}', "and its real answer replaces it")
    for p in (db, cache):
        os.unlink(p)


def test_gives_up_when_nothing_answers():
    print("--give-up-after N stops after N unanswered in a row")
    db = library([f"m{i}" for i in range(10)])
    cache = db + ".cache"
    rc, asked = run(db, cache, {}, "--give-up-after", "3")
    check(rc == 0, f"giving up is not a failure of what was fetched, got {rc}")
    check(len(asked) == 3, f"expected 3 attempts, made {len(asked)}")
    check(cached(cache) == {}, "nothing is cached from a dead network")
    for p in (db, cache):
        os.unlink(p)


def main() -> int:
    test_no_answer_is_not_cached_and_is_asked_again()
    test_a_legacy_unreachable_row_is_asked_again()
    test_gives_up_when_nothing_answers()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("fetch_works: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
