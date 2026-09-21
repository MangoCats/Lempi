#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `tools/push_file_tags.py` `[SPEC-DF-122]`.

`remote_peek.run_remote_sql` is faked, the same technique `test_remote_flags
.py` already uses -- no real `ssh` in any test here. `_scp`/`_ssh_apply` are
faked separately for the `--commit` wiring, so a test never actually shells
out to `scp`/`ssh` either.

    python tools/test_push_file_tags.py
"""

import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import push_file_tags as pft  # noqa: E402
import remote_peek as rp  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
CREATE TABLE file_tags (file_id INTEGER, title TEXT, artist TEXT, album TEXT,
    track_no INTEGER, disc_no INTEGER, has_art INTEGER, scanned_at INTEGER);
"""

MD5_FIXABLE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MD5_ALSO_EMPTY_HERE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
MD5_UNTOUCHED = "cccccccccccccccccccccccccccccccc"[:32]


def fixture_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.executescript(f"""
        -- this library knows this one -- the ordinary case
        INSERT INTO files VALUES (1, '{MD5_FIXABLE}');
        INSERT INTO file_tags VALUES (1, 'Better People', 'Xavier Rudd', 'White Moth', 1, 1, 0, 1000);
        -- this library is untagged for it too -- nothing to offer
        INSERT INTO files VALUES (2, '{MD5_ALSO_EMPTY_HERE}');
        INSERT INTO file_tags VALUES (2, NULL, NULL, NULL, NULL, NULL, 0, 1000);
        -- fully tagged locally, and the remote will report it as fine too --
        -- must never even appear in what the remote is asked about mattering
        INSERT INTO files VALUES (3, '{MD5_UNTOUCHED}');
        INSERT INTO file_tags VALUES (3, 'Something', 'Someone', 'Album', 1, 1, 0, 1000);
    """)
    c.commit()
    c.close()
    return path


def fake_run_remote_sql(gap_md5s, ok=True, error=None):
    def _fake(remote, sql, timeout=30.0):
        if not ok:
            return {"ok": False, "error": error or "no route to host"}
        check("file_tags" in sql and "files" in sql, f"gap query must join files/file_tags, got {sql}")
        check("IS NULL" in sql, f"gap query must filter on emptiness, got {sql}")
        return {"ok": True, "rows": [{"audio_md5": m} for m in gap_md5s]}
    return _fake


def test_remote_gaps_reports_error_cleanly() -> None:
    print("remote_gaps(): an unreachable remote raises, with the real error message")
    real = rp.run_remote_sql
    rp.run_remote_sql = fake_run_remote_sql([], ok=False, error="no route to host")
    try:
        try:
            pft.remote_gaps("pi@lempipi:/srv/library/library.db", 5.0)
            check(False, "must raise when the remote is unreachable")
        except RuntimeError as e:
            check("no route to host" in str(e), f"got {e}")
    finally:
        rp.run_remote_sql = real


def test_local_fixes_for_only_offers_what_it_has() -> None:
    print("local_fixes_for(): only a locally-non-empty row is offered, batched correctly")
    db = fixture_db()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    real_batch = pft.BATCH
    pft.BATCH = 2  # force multiple chunks over 3 ids, to exercise the loop
    try:
        fixes = pft.local_fixes_for(conn, [MD5_FIXABLE, MD5_ALSO_EMPTY_HERE, MD5_UNTOUCHED])
    finally:
        pft.BATCH = real_batch
        conn.close()
        os.remove(db)
    found = {r["audio_md5"] for r in fixes}
    check(found == {MD5_FIXABLE, MD5_UNTOUCHED},
          f"the genuinely-empty-here file must be excluded, got {found}")
    by_md5 = {r["audio_md5"]: r for r in fixes}
    check(by_md5[MD5_FIXABLE]["title"] == "Better People", f"got {by_md5[MD5_FIXABLE]}")


def test_build_patch_shape_and_quoting() -> None:
    print("build_patch(): one UPDATE per row, matched by audio_md5, quoting handled by SQLite itself")
    rows = [
        {"audio_md5": MD5_FIXABLE, "title": "Better People", "artist": "Xavier Rudd",
         "album": "White Moth", "track_no": 1, "disc_no": 1, "has_art": 0, "scanned_at": 1000},
        {"audio_md5": MD5_UNTOUCHED, "title": "O'Brien's Song", "artist": None,
         "album": None, "track_no": None, "disc_no": None, "has_art": 0, "scanned_at": 1000},
    ]
    patch = pft.build_patch(rows)
    check(patch.startswith("BEGIN IMMEDIATE;\n") and patch.rstrip().endswith("COMMIT;"),
          f"must be one bracketed transaction, got:\n{patch}")
    check(patch.count("UPDATE file_tags") == 2, f"expected exactly 2 UPDATEs, got:\n{patch}")
    check(f"audio_md5={MD5_FIXABLE!r}".replace('"', "'") in patch.replace('"', "'")
          or f"'{MD5_FIXABLE}'" in patch, f"must match by audio_md5, got:\n{patch}")
    check("O''Brien" in patch, f"an apostrophe in a title must be safely doubled, got:\n{patch}")
    check("NULL" in patch, f"a None field must render as SQL NULL, not a literal string, got:\n{patch}")


def run_main(argv):
    old_argv = sys.argv
    sys.argv = ["push_file_tags.py", *argv]
    try:
        return pft.main()
    finally:
        sys.argv = old_argv


def test_dry_run_reports_without_touching_remote() -> None:
    print("main(): dry run reports what it would fix, and never calls _scp/_ssh_apply")
    db = fixture_db()
    real_rrs = rp.run_remote_sql
    real_scp, real_apply = pft._scp, pft._ssh_apply
    rp.run_remote_sql = fake_run_remote_sql([MD5_FIXABLE, MD5_ALSO_EMPTY_HERE])
    called = []
    pft._scp = lambda *a, **kw: called.append("scp")
    pft._ssh_apply = lambda *a, **kw: called.append("ssh_apply")
    try:
        rc = run_main([db, "--target", "pi@lempipi:/srv/library/library.db", "--json"])
    finally:
        rp.run_remote_sql = real_rrs
        pft._scp, pft._ssh_apply = real_scp, real_apply
        os.remove(db)
    check(rc == 0, f"expected 0, got {rc}")
    check(called == [], f"a dry run must never touch the remote, got {called}")


def test_commit_builds_and_applies_the_patch() -> None:
    print("main() --commit: the fixable gap is pushed, sudo systemctl by default")
    db = fixture_db()
    real_rrs = rp.run_remote_sql
    real_scp, real_apply = pft._scp, pft._ssh_apply
    rp.run_remote_sql = fake_run_remote_sql([MD5_FIXABLE, MD5_ALSO_EMPTY_HERE])
    calls = {}

    def fake_scp(local_path, host, timeout):
        calls["scp"] = (local_path, host)
        check(os.path.exists(local_path), "the patch file must exist when scp is called")

    def fake_apply(host, remote_path, sudo, timeout):
        calls["apply"] = (host, remote_path, sudo)

    pft._scp, pft._ssh_apply = fake_scp, fake_apply
    try:
        rc = run_main([db, "--target", "pi@lempipi:/srv/library/library.db", "--commit"])
    finally:
        rp.run_remote_sql = real_rrs
        pft._scp, pft._ssh_apply = real_scp, real_apply
        os.remove(db)
    check(rc == 0, f"expected 0, got {rc}")
    check(calls.get("scp") is not None, "scp must run when there is something fixable")
    check(calls.get("apply") == ("pi@lempipi", "/srv/library/library.db", True),
          f"sudo must default on, got {calls.get('apply')}")


def test_no_sudo_flag_is_honoured() -> None:
    print("main() --commit --no-sudo: the sudo prefix is dropped")
    db = fixture_db()
    real_rrs = rp.run_remote_sql
    real_scp, real_apply = pft._scp, pft._ssh_apply
    rp.run_remote_sql = fake_run_remote_sql([MD5_FIXABLE])
    calls = {}
    pft._scp = lambda *a, **kw: None
    pft._ssh_apply = lambda host, remote_path, sudo, timeout: calls.__setitem__("sudo", sudo)
    try:
        run_main([db, "--target", "pi@lempipi:/srv/library/library.db", "--commit", "--no-sudo"])
    finally:
        rp.run_remote_sql = real_rrs
        pft._scp, pft._ssh_apply = real_scp, real_apply
        os.remove(db)
    check(calls.get("sudo") is False, f"expected sudo=False, got {calls.get('sudo')}")


def test_nothing_to_fix_leaves_remote_untouched() -> None:
    print("main(): the remote reporting no gaps at all is a clean, quiet no-op")
    db = fixture_db()
    real_rrs = rp.run_remote_sql
    real_scp, real_apply = pft._scp, pft._ssh_apply
    rp.run_remote_sql = fake_run_remote_sql([])
    called = []
    pft._scp = lambda *a, **kw: called.append("scp")
    pft._ssh_apply = lambda *a, **kw: called.append("ssh_apply")
    try:
        rc = run_main([db, "--target", "pi@lempipi:/srv/library/library.db", "--commit", "--json"])
    finally:
        rp.run_remote_sql = real_rrs
        pft._scp, pft._ssh_apply = real_scp, real_apply
        os.remove(db)
    check(rc == 0, f"expected 0, got {rc}")
    check(called == [], f"nothing to fix must never touch the remote, got {called}")


def test_sync_remote_from_sidecar() -> None:
    print("sync_remote_from_sidecar(): reads the console's own remembered remote, or None cleanly")
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    sidecar = os.path.splitext(db)[0] + ".console.db"
    try:
        check(pft.sync_remote_from_sidecar(db) is None,
              "no sidecar file at all must report None, not raise")
        c = sqlite3.connect(sidecar)
        c.execute("CREATE TABLE remote_config (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT INTO remote_config VALUES ('sync_remote', 'pi@lempipi:/srv/library/library.db')")
        c.commit()
        c.close()
        check(pft.sync_remote_from_sidecar(db) == "pi@lempipi:/srv/library/library.db",
              f"got {pft.sync_remote_from_sidecar(db)!r}")
    finally:
        os.remove(db)
        if os.path.exists(sidecar):
            os.remove(sidecar)


def main() -> int:
    test_remote_gaps_reports_error_cleanly()
    test_local_fixes_for_only_offers_what_it_has()
    test_build_patch_shape_and_quoting()
    test_dry_run_reports_without_touching_remote()
    test_commit_builds_and_applies_the_patch()
    test_no_sudo_flag_is_honoured()
    test_nothing_to_fix_leaves_remote_untouched()
    test_sync_remote_from_sidecar()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("push_file_tags: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
