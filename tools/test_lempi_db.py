#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `lempi_db.py` -- and for the SQLite behaviour it rests on.

Half of what follows tests SQLite rather than this project, deliberately.
The whole design turns on two facts: unqualified names resolve through the
attach chain, and `CREATE TABLE` does not. If either ever stops being true,
`tools/` would start reading zeroes out of shadow tables with no error
anywhere, so both are pinned here rather than trusted to a docstring.

    python tools/test_lempi_db.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db as vd  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


def make_library(path):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE flavor (subject_id TEXT, characteristic TEXT, value REAL);
        INSERT INTO recordings VALUES ('m1', 'A Real Title');
        INSERT INTO flavor VALUES ('m1', 'user.christmas', 1.0);
    """)
    c.commit()
    c.close()


def make_listener(path):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE listener_play_history (played_at INTEGER, mbid TEXT, passage_id INTEGER);
        CREATE TABLE listener_preferences (subject_id TEXT, rotation REAL);
        INSERT INTO listener_play_history VALUES (1, 'm1', 7);
    """)
    c.commit()
    c.close()


def make_whole(path):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE listener_play_history (played_at INTEGER, mbid TEXT, passage_id INTEGER);
        INSERT INTO recordings VALUES ('m1', 'A Real Title');
    """)
    c.commit()
    c.close()


def make_partial(path):
    """Whole, but missing the table an earlier version used as THE listener
    marker. `test_jobs_remote_pull.py` builds exactly this, and a
    single-marker `shape()` called it the library half of a split pair and
    refused to open it."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
        CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT);
    """)
    c.commit()
    c.close()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="lempi-db-test-")
    lib = os.path.join(tmp, "library.db")
    lis = os.path.join(tmp, "listener.db")
    whole = os.path.join(tmp, "lempi.db")
    make_library(lib)
    make_listener(lis)
    make_whole(whole)

    print("SQLite itself: the two behaviours this design rests on")
    c = sqlite3.connect(lis)
    c.execute("ATTACH DATABASE ? AS lib", (lib,))
    check(c.execute("SELECT title FROM recordings WHERE mbid='m1'").fetchone()[0] == "A Real Title",
          "an unqualified read must resolve through the attach chain")
    c.execute("INSERT INTO recordings VALUES ('m2','Written Unqualified')")
    check(c.execute("SELECT COUNT(*) FROM lib.recordings").fetchone()[0] == 2,
          "an unqualified write must land in the attached half, not main")
    c.execute("CREATE TABLE IF NOT EXISTS recordings (mbid TEXT PRIMARY KEY, title TEXT)")
    check("recordings" in {r[0] for r in c.execute(
              "SELECT name FROM main.sqlite_master WHERE type='table'")},
          "CREATE TABLE IF NOT EXISTS must be shown to create a shadow in main -- "
          "if this ever fails, the hazard is gone and the authorizer is dead weight")
    # Statement text never prepared before the shadow existed: main wins.
    check(c.execute("SELECT title FROM recordings WHERE mbid = 'm1'").fetchone() is None,
          "the shadow must be shown to MASK the real table, silently")
    # ...but text already prepared keeps resolving to the real table, from
    # the statement cache. This is why the masking is worse than a clean
    # break: one process, one connection, both answers at once.
    check(c.execute("SELECT title FROM recordings WHERE mbid='m1'").fetchone() is not None,
          "a statement prepared before the shadow must be shown to still see the real "
          "table -- the partial failure this design exists to prevent")
    c.close()
    os.remove(lis)
    make_listener(lis)

    print()
    print("shape: decided by contents, never by filename")
    check(vd.shape(lib) == vd.ROLE_LIBRARY, f"got {vd.shape(lib)}")
    check(vd.shape(lis) == vd.ROLE_LISTENER, f"got {vd.shape(lis)}")
    check(vd.shape(whole) == vd.WHOLE, f"got {vd.shape(whole)}")
    empty = os.path.join(tmp, "empty.db")
    sqlite3.connect(empty).close()
    check(vd.shape(empty) == vd.EMPTY, f"got {vd.shape(empty)}")
    # A database missing some listener tables is still whole. Regression
    # test for a real break: this shape sits in `tools/` fixtures and the
    # first marker scheme refused to open it at all.
    partial = os.path.join(tmp, "partial", "library.db")
    os.makedirs(os.path.dirname(partial), exist_ok=True)
    make_partial(partial)
    check(vd.shape(partial) == vd.WHOLE,
          f"a whole database missing listener_play_history must read WHOLE, got {vd.shape(partial)}")
    conn = vd.connect(partial, vd.ROLE_LISTENER, writable=True)
    check([r[0] for r in conn.execute("PRAGMA database_list")] == [0],
          "and must open with nothing attached")
    conn.close()

    print()
    print("a whole database is opened exactly as before -- no attach, no authorizer")
    conn = vd.connect(whole, vd.ROLE_LIBRARY)
    check([r[0] for r in conn.execute("PRAGMA database_list")] == [0],
          "an unsplit installation must carry none of this")
    check(conn.execute("SELECT title FROM recordings WHERE mbid='m1'").fetchone()[0] == "A Real Title",
          "and must still read normally")
    conn.close()

    print()
    print("a split pair: either half may be named, and `role` decides which is main")
    for named in (lis, lib):
        conn = vd.connect(named, vd.ROLE_LIBRARY)
        dbs = {r[1]: r[2] for r in conn.execute("PRAGMA database_list")}
        check(os.path.basename(dbs["main"]) == "library.db",
              f"naming {os.path.basename(named)} with role=library must make library.db main, "
              f"got {os.path.basename(dbs['main'])}")
        check(conn.execute("SELECT COUNT(*) FROM listener_play_history").fetchone()[0] == 1,
              "and the listener half must still be readable through the attach")
        conn.close()

    conn = vd.connect(lis, vd.ROLE_LISTENER)
    dbs = {r[1]: r[2] for r in conn.execute("PRAGMA database_list")}
    check(os.path.basename(dbs["main"]) == "listener.db", "role=listener must make listener.db main")
    check(conn.execute("SELECT title FROM recordings WHERE mbid='m1'").fetchone()[0] == "A Real Title",
          "and the catalogue must be readable through the attach")
    conn.close()

    print()
    print("the authorizer: a shadow cannot be created, in either direction")
    conn = vd.connect(lis, vd.ROLE_LISTENER, writable=True)
    for stmt, why in [
        ("CREATE TABLE recordings (x TEXT)", "plain CREATE of a catalogue table"),
        ("CREATE TABLE IF NOT EXISTS flavor (x TEXT)", "IF NOT EXISTS of a catalogue table"),
        ("CREATE INDEX recordings ON listener_preferences(subject_id)",
         "an INDEX whose NAME collides with a catalogue table"),
    ]:
        try:
            conn.execute(stmt)
            check(False, f"{why} must be denied, but was allowed")
        except sqlite3.DatabaseError:
            pass
    # ...while a table that genuinely belongs to this half is unaffected.
    conn.execute("CREATE TABLE IF NOT EXISTS listener_flags (subject_id TEXT)")
    check("listener_flags" in {r[0] for r in conn.execute(
              "SELECT name FROM main.sqlite_master WHERE type='table'")},
          "a table belonging to this half must still be creatable")
    conn.close()

    print()
    print("an existing shadow is refused at open, by name")
    dirty = os.path.join(tmp, "dirty")
    os.makedirs(dirty, exist_ok=True)
    dlib, dlis = os.path.join(dirty, "library.db"), os.path.join(dirty, "listener.db")
    make_library(dlib)
    make_listener(dlis)
    c = sqlite3.connect(dlis)
    c.execute("CREATE TABLE recordings (mbid TEXT, title TEXT)")  # the shadow
    c.commit()
    c.close()
    try:
        vd.connect(dlis, vd.ROLE_LISTENER)
        check(False, "a pre-existing shadow must be refused, not read")
    except vd.SplitError as e:
        check("recordings" in str(e), f"the error must name the offending table, got {e}")

    print()
    print("tables(): existence answered across both halves, not just main")
    conn = vd.connect(lis, vd.ROLE_LISTENER)
    naive = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check("recordings" not in naive,
          "the naive check must be shown to MISS the attached half -- that is the bug")
    check(vd.has_table(conn, "recordings"),
          "a catalogue table must be found from the listener half")
    check(vd.has_table(conn, "listener_play_history"),
          "and a listener table must still be found")
    check(not vd.has_table(conn, "no_such_table"), "and a real absence must still read absent")
    # PRAGMA table_info DOES resolve through the attach chain, unlike
    # sqlite_master. Pinned because `has_column`-style helpers all over
    # tools/ rely on it and would need rewriting if it were not true.
    check(len(conn.execute("PRAGMA table_info(recordings)").fetchall()) > 0,
          "PRAGMA table_info must resolve across the attach, or every has_column() breaks")
    conn.close()
    # ...and the same answers with the roles reversed.
    conn = vd.connect(lib, vd.ROLE_LIBRARY)
    check(vd.has_table(conn, "listener_play_history") and vd.has_table(conn, "recordings"),
          "both halves must be visible whichever is main")
    conn.close()

    print()
    print("a table shared on purpose is not a shadow")
    shared = os.path.join(tmp, "shared")
    os.makedirs(shared, exist_ok=True)
    slib, slis = os.path.join(shared, "library.db"), os.path.join(shared, "listener.db")
    make_library(slib)
    make_listener(slis)
    for f in (slib, slis):
        c = sqlite3.connect(f)
        c.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT INTO schema_meta VALUES ('schema_version','1')")
        c.commit()
        c.close()
    try:
        conn = vd.connect(slis, vd.ROLE_LISTENER)
        check(conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'"
                           ).fetchone()[0] == "1",
              "schema_meta is in both halves by design and must read normally")
        conn.close()
    except vd.SplitError as e:
        check(False, f"a deliberately shared table must not be called a shadow: {e}")

    print()
    print("a half with no peer opens alone -- never worse than a plain connect")
    lonely = os.path.join(tmp, "lonely")
    os.makedirs(lonely, exist_ok=True)
    only = os.path.join(lonely, "listener.db")
    make_listener(only)
    # A catalogue-only or listener-only database is an ordinary thing --
    # snapshots, fixtures, a library ingested but never played -- and by
    # content it cannot be told from half a pair. Refusing to open it broke
    # four unrelated suites, so the old behaviour stands and the failure, if
    # there is one, happens where it always did: on a query for the other
    # side's tables.
    conn = vd.connect(only, vd.ROLE_LISTENER)
    check(conn.execute("SELECT COUNT(*) FROM listener_play_history").fetchone()[0] == 1,
          "its own half must still read normally")
    check([r[0] for r in conn.execute("PRAGMA database_list")] == [0],
          "and nothing must be attached")
    conn.close()
    # ...and an explicit path resolves it.
    conn = vd.connect(only, vd.ROLE_LISTENER, peer=lib)
    check(conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] >= 1,
          "an explicitly named peer must be used")
    conn.close()
    # ...as does the environment.
    os.environ["LEMPI_LIBRARY"] = lib
    try:
        conn = vd.connect(only, vd.ROLE_LISTENER)
        check(conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] >= 1,
              "LEMPI_LIBRARY must be honoured")
        conn.close()
    finally:
        del os.environ["LEMPI_LIBRARY"]

    print()
    print("read-only by default: a writable handle is asked for, never assumed")
    conn = vd.connect(lis, vd.ROLE_LISTENER)
    try:
        conn.execute("INSERT INTO listener_preferences VALUES ('x', 1.0)")
        check(False, "the default handle must not be writable")
    except sqlite3.OperationalError:
        pass
    conn.close()

    print()
    print("`writable` follows the ROLE, not whichever half was named")
    # The defect this pins, found live by `import_flags.py` on 2026-09-11:
    # naming the *peer* swapped the two flags, so the half the script owns
    # and means to write opened read-only while the half it must not touch
    # opened writable. Every earlier test here named a script's own half,
    # which is exactly why it went unseen. Both roles, both namings.
    def writable_halves(named, role, **kw):
        """Which halves a connection can actually write, by trying."""
        conn = vd.connect(named, role, **kw)
        out = set()
        for half, stmt in (("listener", "INSERT INTO listener_preferences VALUES ('x', 1.0)"),
                           ("library", "INSERT INTO recordings (mbid) VALUES ('probe')")):
            try:
                conn.execute(stmt)
                out.add(half)
            except sqlite3.OperationalError:
                pass
        conn.rollback()
        conn.close()
        return out

    for role, owns in ((vd.ROLE_LISTENER, "listener"), (vd.ROLE_LIBRARY, "library")):
        for named in (lis, lib):
            got = writable_halves(named, role, writable=True)
            check(got == {owns},
                  f"role={owns} writable=True naming {os.path.basename(named)} must make "
                  f"exactly the {owns} half writable, got {sorted(got) or 'nothing'}")
    # `peer_writable` is still the way to ask for both, from either naming.
    for named in (lis, lib):
        got = writable_halves(named, vd.ROLE_LISTENER, writable=True, peer_writable=True)
        check(got == {"listener", "library"},
              f"peer_writable=True naming {os.path.basename(named)} must open both, "
              f"got {sorted(got) or 'nothing'}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("lempi_db: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
