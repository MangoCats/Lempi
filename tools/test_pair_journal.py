#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `pair_journal.py` and what `star_sync.py` does about a pair's
journal mode `[SPEC-STAR-106]`.

    python tools/test_pair_journal.py
"""
import contextlib
import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pair_journal as pj  # noqa: E402
import star_patch as sp  # noqa: E402
import star_sync as ss  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def pair(root, mode="DELETE"):
    """A pair as the hub's was: both halves in `mode`, each with a row."""
    os.makedirs(root, exist_ok=True)
    lib, lis = os.path.join(root, "library.db"), os.path.join(root, "listener.db")
    for path, ddl in ((lib, "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT); "
                            "CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER); "
                            "INSERT INTO files VALUES (1, 'a');"),
                      (lis, "CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, played_at INTEGER); "
                            "CREATE TABLE player_state (id INTEGER PRIMARY KEY, playing INTEGER); "
                            "INSERT INTO player_state VALUES (1, 0);")):
        c = sqlite3.connect(path)
        c.execute(f"PRAGMA journal_mode={mode}")
        c.executescript(ddl)
        c.commit()
        c.close()
    return lib, lis


def run(*argv):
    """What `main` returns and prints. It returns its exit code for everything
    but a usage error, and nothing here maps one code to another: until
    2026-11-13 this turned every `SystemExit` into 2, which hid that the
    tool's refusals exited 1."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = pj.main(list(argv))
    return rc, out.getvalue()


def process(*argv):
    """The tool as a launcher or a script runs it: its real exit code."""
    done = subprocess.run([sys.executable, os.path.join(HERE, "pair_journal.py"), *argv],
                          capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout + done.stderr


def test_says_and_sets():
    print("a pair's journal mode is said, and set after a backup")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        lib, lis = pair(root)
        rc, out = run(lib)
        check(rc == 1 and "library: delete" in out and "listener: delete" in out and "NOT WAL" in out,
              f"a rollback-journal pair is said, and is exit 1: {rc} {out}")
        check(pj.mode_of(lib) == "delete", "and nothing was changed by asking")

        rc, out = run(lis, "--wal", "--date", "20261112")          # either half names the pair
        check(rc == 0 and "both halves are WAL" in out, f"set from the listener's path: {rc} {out}")
        check(pj.mode_of(lib) == "wal" and pj.mode_of(lis) == "wal", "both halves read back WAL")
        bk = os.path.join(root, "backups", "pre-wal-20261112")
        check(sorted(os.listdir(bk)) == ["library.db", "listener.db"], f"both were backed up first: {os.listdir(bk)}")
        check(pj.mode_of(os.path.join(bk, "library.db")) == "delete", "the backup is the pair as it was")
        c = sqlite3.connect(lis)
        check(c.execute("SELECT playing FROM player_state").fetchall() == [(0,)], "and the rows are as they were")
        c.close()

        rc, out = run(lib, "--wal", "--date", "20261112")
        check(rc == 0 and not os.path.exists(os.path.join(root, "backups", "pre-wal-20261112", "x")),
              f"a pair already WAL is left alone, and needs no second backup: {rc} {out}")
        rc, out = run(lib)
        check(rc == 0, f"and asking again is exit 0: {rc}")


def test_refuses_what_it_cannot_finish():
    print("a half another process holds is said, and left as it was")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        lib, lis = pair(root)
        holder = sqlite3.connect(lis)                   # a player, with the listener open and reading
        holder.execute("BEGIN")
        holder.execute("SELECT * FROM player_state").fetchall()
        real = pj.set_wal
        pj.set_wal = lambda path, timeout=0.2: real(path, timeout)     # not the five seconds a person waits
        try:
            rc, out = run(lib, "--wal", "--date", "20261112")
        finally:
            pj.set_wal = real
        holder.execute("COMMIT")
        holder.close()
        check(rc == 2 and "listener: NOT CHANGED" in out and "stop the player" in out,
              f"the held half is named, with what to do: {rc} {out}")
        check(pj.mode_of(lis) == "delete" and pj.mode_of(lib) == "wal",
              f"it is as it was, and the half that could be set is: {pj.mode_of(lis)} {pj.mode_of(lib)}")

        rc, out = run(os.path.join(root, "nothing.db"))
        check(rc == 2, f"a path that is no half of a pair is refused: {rc} {out}")


def test_could_not_run_is_exit_2():
    """The docstring's codes, from the process itself: 1 is *one half is not
    WAL*, which a launcher answers with `--wal`; 2 is *it could not run*. The
    three refusals below exited 1 until 2026-11-13."""
    print("what it cannot run on is exit 2 from the process, and never the 1 that means not WAL")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        rc, out = process(os.path.join(root, "nothing.db"))
        check(rc == 2 and "could not run" in out and "not one half of a pair" in out,
              f"a path that is no half of a pair: {rc} {out}")

        lib, lis = pair(os.path.join(root, "alone"))
        os.remove(lis)
        rc, out = process(lib)
        check(rc == 2 and "other half" in out, f"a half with no other beside it: {rc} {out}")

        lib, lis = pair(os.path.join(root, "kept"))
        os.makedirs(os.path.join(root, "kept", "backups", "pre-wal-20261113"))
        rc, out = process(lib, "--wal", "--date", "20261113")
        check(rc == 2 and "never written over" in out, f"a backup folder already there: {rc} {out}")
        check(pj.mode_of(lib) == "delete" and pj.mode_of(lis) == "delete", "and nothing was changed")

        rc, out = process(lib)
        check(rc == 1 and "NOT WAL" in out, f"a pair that is not WAL is still exit 1: {rc} {out}")
        rc, out = process(lib, "--wal", "--date", "20261114")
        check(rc == 0, f"and set, exit 0: {rc} {out}")
        rc, out = process()
        check(rc == 2 and "usage" in out.lower(), f"a usage error is argparse's 2: {rc} {out}")


def test_a_path_with_a_hash_is_the_file_it_names():
    """A pair under `Music #2%1?`: `#` would start a fragment, `?` a query and
    `%` an escape in an address written by hand. Until 2026-11-13 `mode_of`
    said `delete` of a WAL file there, `set_wal` said `read back as delete`
    after setting it, and `halves` saw no pair at all."""
    print("a pair in a folder named with #, % and ? is read, backed up and set as the files they are")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        # `?` cannot be in a Windows file name; `#` and `%` can be in any.
        folder = os.path.join(root, "Music #2 %41" + ("" if os.name == "nt" else " ?x"))
        lib, lis = pair(folder)
        check(pj.mode_of(lib) == "delete", f"asked of the file itself: {pj.mode_of(lib)}")
        rc, out = run(lis, "--wal", "--date", "20261113")
        check(rc == 0 and "both halves are WAL" in out, f"set, and read back as set: {rc} {out}")
        check(pj.mode_of(lib) == "wal" and pj.mode_of(lis) == "wal", "both read back WAL")
        check(os.listdir(root) == [os.path.basename(folder)], f"and no other file was made beside it: {os.listdir(root)}")
        bk = os.path.join(folder, "backups", "pre-wal-20261113")
        check(sorted(os.listdir(bk)) == ["library.db", "listener.db"]
              and pj.mode_of(os.path.join(bk, "listener.db")) == "delete", "the backup is the pair as it was")
        plan = {"hub": {"library": lib, "listener": lis}}
        for half in ("library", "listener"):
            check(pj.mode_of(ss.at_root(plan["hub"][half])) == "wal", f"and status reads its {half} as WAL")


def test_the_modes_differ_as_claimed():
    """The reason for all of this, held as a test: a reader's open transaction
    refuses a commit in rollback-journal mode and not in WAL."""
    print("a reader refuses a commit in rollback-journal mode, and not in WAL")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        for mode, expect in (("DELETE", False), ("WAL", True)):
            p = os.path.join(root, f"{mode}.db")
            w = sqlite3.connect(p, timeout=0.2)
            w.execute(f"PRAGMA journal_mode={mode}")
            w.execute("CREATE TABLE t (a)")
            w.execute("INSERT INTO t VALUES (1)")
            w.commit()
            r = sqlite3.connect(p, timeout=0.2)
            r.execute("BEGIN")
            r.execute("SELECT * FROM t").fetchall()
            try:
                w.execute("INSERT INTO t VALUES (2)")
                w.commit()
                wrote = True
            except sqlite3.OperationalError:
                wrote = False
                w.rollback()
            r.execute("COMMIT")
            r.close()
            w.close()
            check(wrote == expect, f"{mode}: a write beside an open read {'commits' if wrote else 'is refused'}")


def test_copies_stay_rollback_journal():
    """[SPEC-STAR-106]: the hub's own copies are rollback-journal files
    whatever the pair is in, whole when read `immutable`, and the same bytes
    for the same state -- which the daily store's names rest on."""
    print("a copy of a WAL pair is a rollback-journal file, whole, and the same bytes each time")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        lib, _ = pair(os.path.join(root, "live"), "WAL")
        live = sqlite3.connect(lib)                     # the player: open, its last write still in the WAL
        live.execute("INSERT INTO files VALUES (2, 'b')")
        live.commit()
        check(os.path.getsize(lib + "-wal") > 0, "the fixture's write is still in the WAL, not the main file")
        copies = [os.path.join(root, "copies", f"{n}.db") for n in (1, 2)]
        for c in copies:
            ss.local_backup(lib, c)
        live.close()
        check(pj.mode_of(copies[0]) == "delete", f"the copy's mode: {pj.mode_of(copies[0])}")
        check(sorted(os.listdir(os.path.join(root, "copies"))) == ["1.db", "2.db"], "with no -wal or -shm beside it")
        im = sp.open_ro(copies[0])
        check(im.execute("SELECT audio_md5 FROM files ORDER BY 1").fetchall() == [("a",), ("b",)],
              "read immutable, it holds what was still in the source's WAL")
        im.close()
        digest = [hashlib.sha256(open(c, "rb").read()).hexdigest() for c in copies]
        check(digest[0] == digest[1], "two copies of one state are the same bytes")


def test_status_says_the_mode():
    print("status says each half's mode, and a pair not in WAL is not current")
    import datetime as dt
    import json
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        lib, lis = pair(os.path.join(root, "lib"))
        os.makedirs(os.path.join(root, "lib", "verify"))
        os.makedirs(os.path.join(root, "days"))
        with open(os.path.join(root, "days", "d.json"), "w") as fh:
            json.dump({"taken_at": dt.datetime.now(dt.timezone.utc).isoformat()}, fh)
        with open(os.path.join(root, "lib", "verify", "v.json"), "w") as fh:
            json.dump({"checked_at": dt.datetime.now().astimezone().isoformat(), "files": 0, "ok": 0, "bytes": 0,
                       "seconds": 0, "unrecorded": 0, "retagged": [], "damaged": [], "missing": [],
                       "unreadable": [], "ok_whole": True}, fh)
        plan = {"backups": {"dir": root, "max_age_hours": 30}, "hub": {"library": lib, "listener": lis},
                "runs": os.path.join(root, "runs")}

        def status():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = ss.status(plan)
            return rc, out.getvalue()

        rc, out = status()
        check(rc == 1 and "hub listener: journal mode delete" in out and "pair_journal.py" in out,
              f"a rollback-journal pair is STALE, with the command that sets it: {rc} {out}")
        for path in (lib, lis):
            check(pj.set_wal(path) is None, f"set {path}")
        rc, out = status()
        check(rc == 0 and "hub library: journal mode wal" in out and "the pair in WAL mode" in out,
              f"and current once it is WAL: {rc} {out}")

        # [GDE-DEP-060]: a half that is not there. Until 2026-11-13 it was
        # passed over, and this read "the pair in WAL mode", exit 0.
        gone = dict(plan, hub={"library": lib, "listener": os.path.join(root, "lib", "moved-away.db")})
        plan, kept = gone, plan
        rc, out = status()
        check(rc == 1 and "hub listener: journal mode absent" in out and "is not there" in out
              and "the pair in WAL mode" not in out,
              f"a half that is not there is STALE, and no mode is claimed for it: {rc} {out}")
        plan = dict(kept, hub={"library": lib})
        rc, out = status()
        check(rc == 1 and "NOT NAMED" in out and "the pair in WAL mode" not in out,
              f"and so is one the plan does not name: {rc} {out}")
        with open(os.path.join(root, "lib", "not-a-database.db"), "w") as fh:
            fh.write("this is no SQLite file, and is longer than its header would be" * 4)
        plan = dict(kept, hub={"library": lib, "listener": os.path.join(root, "lib", "not-a-database.db")})
        rc, out = status()
        check(rc == 1 and "could not be asked" in out and "the pair in WAL mode" not in out,
              f"and one that cannot be asked: {rc} {out}")


def test_status_says_each_nodes_mode():
    """[SPEC-STAR-107]: each node's journal modes, from its newest sync. A
    listener not in WAL is STALE; a catalogue not in WAL never is -- bose's
    must not be [BOS-RUN-092]; a build that does not report is said."""
    print("status says each node's journal modes, and a node's listener not in WAL is not current")
    import json
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        runs = os.path.join(root, "runs")
        older = {"gone": {"signed": "fp", "journal_mode": {"listener": "delete", "library": "wal"}}}
        newer = {"fine": {"signed": "fp", "journal_mode": {"listener": "wal", "library": "wal"}},
                 "bose": {"signed": "fp", "journal_mode": {"listener": "wal", "library": "delete"}},
                 "seeded": {"signed": "fp", "journal_mode": {"listener": "delete", "library": "wal"}},
                 "unasked": {"signed": "fp", "journal_mode": {"listener": None, "library": "wal"}},
                 "oldbuild": {"signed": "fp"},
                 "phone": {"member": "fp"}}
        for stamp, b in (("20261101T0100Z", older), ("20261113T2005Z", newer)):
            os.makedirs(os.path.join(runs, stamp))
            with open(os.path.join(runs, stamp, "baselines.json"), "w") as fh:
                json.dump(b, fh)
        got = ss.node_reports({"runs": runs})
        check(got["gone"][0] == "20261101T0100Z" and got["fine"][0] == "20261113T2005Z",
              f"the newest run naming each node: {[(n, r) for n, (r, _b) in sorted(got.items())]}")
        stale = {n: ss.journal_stale(n, b.get("journal_mode")) for n, (_r, b) in got.items()}
        check(stale["fine"] == [] and stale["bose"] == [] and stale["oldbuild"] == [] and stale["phone"] == [],
              f"WAL, a rollback-journal catalogue, or a build that does not say: not stale: {stale}")
        check(len(stale["seeded"]) == 1 and "not in WAL mode (delete)" in stale["seeded"][0], f"{stale['seeded']}")
        check(len(stale["gone"]) == 1, f"a node the newest run missed, by the run before: {stale['gone']}")
        check(len(stale["unasked"]) == 1 and "could not be asked" in stale["unasked"][0], f"{stale['unasked']}")
        check(ss.journal_words(newer["bose"]["journal_mode"]) == "journal mode: listener wal, catalogue delete"
              and "does not report" in ss.journal_words(None), "said in words")

        # And through `status` itself, over a hub pair that is in order.
        lib, lis = pair(os.path.join(root, "lib"), "WAL")
        os.makedirs(os.path.join(root, "lib", "verify"))
        os.makedirs(os.path.join(root, "days"))
        import datetime as dt
        with open(os.path.join(root, "days", "d.json"), "w") as fh:
            json.dump({"taken_at": dt.datetime.now(dt.timezone.utc).isoformat()}, fh)
        with open(os.path.join(root, "lib", "verify", "v.json"), "w") as fh:
            json.dump({"checked_at": dt.datetime.now().astimezone().isoformat(), "files": 0, "ok": 0, "bytes": 0,
                       "seconds": 0, "unrecorded": 0, "retagged": [], "damaged": [], "missing": [],
                       "unreadable": [], "ok_whole": True}, fh)
        plan = {"backups": {"dir": root, "max_age_hours": 30}, "hub": {"library": lib, "listener": lis}, "runs": runs}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ss.status(plan)
        out = out.getvalue()
        check(rc == 1 and "seeded's listener is not in WAL mode (delete)" in out
              and "bose: journal mode: listener wal, catalogue delete" in out
              and "oldbuild: does not report its journal modes" in out
              and "phone: journal" not in out and "phone: does not report its journal" not in out,
              f"status names the node, says the rest, and leaves a phone out: {rc} {out}")
        for name in ("seeded", "unasked"):
            newer[name]["journal_mode"]["listener"] = "wal"
        with open(os.path.join(runs, "20261113T2005Z", "baselines.json"), "w") as fh:
            json.dump(newer, fh)
        os.remove(os.path.join(runs, "20261101T0100Z", "baselines.json"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ss.status(plan)
        check(rc == 0 and "the pair in WAL mode" in out.getvalue(), f"and current once each is WAL: {rc} {out.getvalue()}")


def main() -> int:
    test_the_modes_differ_as_claimed()
    test_says_and_sets()
    test_refuses_what_it_cannot_finish()
    test_could_not_run_is_exit_2()
    test_a_path_with_a_hash_is_the_file_it_names()
    test_copies_stay_rollback_journal()
    test_status_says_the_mode()
    test_status_says_each_nodes_mode()
    print()
    if FAILED:
        print(f"pair_journal: {len(FAILED)} check(s) FAILED")
        return 1
    print("pair_journal: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
