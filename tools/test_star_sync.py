#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `star_sync.py`'s pure parts [SPEC-STAR-085..086]: which backups
are kept, and the removal evidence a node's last-sent copy becomes. The stages
that reach the fleet are proven by running them; see FLEET001 [FLT-DAT-030].

    python tools/test_star_sync.py
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_merge as sm  # noqa: E402
import star_sync as ss  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def test_backup_proofs():
    """[GDE-INT-120], [GDE-INT-115]: each kept backup object re-read and proven
    against its own name, one that is not said and re-read until it is; and
    status saying both that and the library's last verification."""
    import hashlib
    import io
    import json
    import contextlib
    print("backup objects re-read against their names; status says it, and the library's verification")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        objs = os.path.join(root, "objects")
        os.makedirs(objs)
        names = []
        for body in (b"one", b"two", b"three"):
            sha = hashlib.sha256(body).hexdigest()
            with open(os.path.join(objs, f"{sha}.db"), "wb") as fh:
                fh.write(body)
            names.append(sha)
        with open(os.path.join(objs, f"{names[2]}.db"), "wb") as fh:
            fh.write(b"thr3e")                                   # rotted since it was stored
        st = ss.proofs(root)
        with contextlib.redirect_stdout(io.StringIO()):
            ss.prove_local(root, set(names), st)
        ss.save_proofs(root, st)
        check(sorted(st["local"]) == sorted(names[:2]) and st["local_bad"] == [names[2]],
              f"two proven, the rotted one named: {st}")
        check(ss.due(st, "local", set(names)) == [names[2]], "only the bad one is due again tomorrow")
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=ss.PROVE_DAYS + 1)).isoformat()
        st["local"][names[0]] = old
        check(names[0] in ss.due(st, "local", set(names)), "a proof older than PROVE_DAYS is due again")

        lib = os.path.join(root, "lib", "library.db")
        os.makedirs(os.path.join(root, "lib", "verify"))
        os.makedirs(os.path.join(root, "days"))
        with open(os.path.join(root, "days", "2026-10-15.json"), "w") as fh:
            json.dump({"taken_at": dt.datetime.now(dt.timezone.utc).isoformat()}, fh)
        with open(os.path.join(root, "lib", "verify", "2026-10-15.json"), "w") as fh:
            json.dump({"checked_at": dt.datetime.now().isoformat(timespec="seconds"), "files": 3, "ok": 2,
                       "bytes": 9, "seconds": 1, "unrecorded": 0, "retagged": [], "damaged": [{"path": "x"}],
                       "missing": [], "unreadable": [], "ok_whole": False}, fh)
        plan = {"backups": {"dir": root, "max_age_hours": 30}, "hub": {"library": lib},
                "runs": os.path.join(root, "runs")}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ss.status(plan)
        said = out.getvalue()
        check(rc == 1 and "1 backup object(s) here are not what they were stored as" in said,
              f"status names the bad object: {said}")
        check("library verified" in said and "found damaged, missing or unreadable" in said,
              f"and the library's verification, read with no offset on its time: {said}")


def test_mirror_when_reachable():
    """[SPEC-STAR-088]: a mirror that cannot be reached is not a failure, and
    status judges it by the backup it last took -- current within 48 h, STALE
    once a day is missed -- without asking a host that may be off."""
    import io
    import json
    import contextlib
    print("the mirror: off is not a failure; status reads what it last took")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        os.makedirs(os.path.join(root, "days"))
        os.makedirs(os.path.join(root, "objects"))
        lib = os.path.join(root, "lib", "library.db")
        os.makedirs(os.path.join(root, "lib", "verify"))
        with open(os.path.join(root, "lib", "verify", "v.json"), "w") as fh:
            json.dump({"checked_at": dt.datetime.now().astimezone().isoformat(), "files": 0, "ok": 0, "bytes": 0,
                       "seconds": 0, "unrecorded": 0, "retagged": [], "damaged": [], "missing": [],
                       "unreadable": [], "ok_whole": True}, fh)
        utc = dt.datetime.now(dt.timezone.utc)
        for days_ago in (0, 3):
            d = (utc - dt.timedelta(days=days_ago)).strftime("%Y-%m-%d")
            with open(os.path.join(root, "days", f"{d}.json"), "w") as fh:
                json.dump({"taken_at": (utc - dt.timedelta(days=days_ago)).isoformat(),
                           "listener": "l" * 64, "library": "b" * 64}, fh)
        plan = {"backups": {"dir": root, "max_age_hours": 30, "keep_daily": 14, "keep_monthly": 12,
                            "mirror": {"host": "x@off", "dir": "/m"}},
                "hub": {"library": lib}, "runs": os.path.join(root, "runs")}
        real = ss.sd.ssh
        ss.sd.ssh = lambda host, cmd, quiet=False, timeout=None: (255, "ssh: connect to host off: timed out")
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ss.mirror_now(plan)
            check(rc == 0 and "not reachable now" in out.getvalue(), f"off is said, not failed: {rc}")

            def said():
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = ss.status(plan)
                return rc, buf.getvalue()
            rc, text = said()
            check(rc == 1 and "has not been brought up to date" in text, f"never written: STALE: {text}")
            st = ss.proofs(root)
            st["mirror_newest"], st["mirror_at"] = (utc - dt.timedelta(days=0)).strftime("%Y-%m-%d"), utc.isoformat()
            ss.save_proofs(root, st)
            rc, text = said()
            check(rc == 0 and "holds the backup of" in text, f"today's backup held: current: {text}")
            st["mirror_newest"] = (utc - dt.timedelta(days=3)).strftime("%Y-%m-%d")
            ss.save_proofs(root, st)
            rc, text = said()
            check(rc == 1 and "has not taken a newer one" in text and "mirror.log" in text,
                  f"a day missed: STALE, and it names no cause it did not check: {text}")
        finally:
            ss.sd.ssh = real


def test_mirror_limits():
    """Every mirror step has a limit, and ssh an empty standard input: on
    2026-10-15 a `mv` on teacherslounge finished and the ssh that ran it never
    returned, twice, and every quarter-hour's run after it was refused."""
    import hashlib
    import io
    import json
    import subprocess
    import contextlib
    print("the mirror: a step that never returns is ended, said, and the run gives way")
    calls = []

    def hangs(argv, **kw):
        calls.append((argv, kw))
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    real_run = ss.sd.subprocess.run
    ss.sd.subprocess.run = hangs
    try:
        rc, out = ss.sd.ssh("x@h", "mv a b", quiet=True, timeout=5)
    finally:
        ss.sd.subprocess.run = real_run
    argv, kw = calls[0]
    check(rc == 124 and "within 5 s" in out, f"a call past its limit answers 124, said: {rc} {out}")
    check("-n" in argv and kw.get("stdin") is subprocess.DEVNULL and kw.get("timeout") == 5,
          f"with an empty standard input and the limit: {argv} {kw}")
    check(ss.mirror_limit(1_270_000_000, ss.MIRROR_COPY_BPS) == ss.MIRROR_SHORT_S + 1270,
          "a copy's limit grows with its size")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        os.makedirs(os.path.join(root, "objects"))
        os.makedirs(os.path.join(root, "days"))
        names = []
        for body in (b"listener" * 1000, b"library" * 3000):
            sha = hashlib.sha256(body).hexdigest()
            with open(os.path.join(root, "objects", f"{sha}.db"), "wb") as fh:
                fh.write(body)
            names.append(sha)
        with open(os.path.join(root, "days", "2026-10-15.json"), "w") as fh:
            json.dump({"listener": names[0], "library": names[1], "taken_at": "2026-10-15T07:30:00+00:00"}, fh)
        plan = {"backups": {"dir": root, "keep_daily": 14, "keep_monthly": 12,
                            "mirror": {"host": "x@h", "dir": "/m"}}}
        seen, copies, commands = [], [], []

        def far_end(host, cmd, quiet=False, timeout=None):
            seen.append((cmd.split()[0], timeout))
            commands.append((cmd, len(copies)))
            if cmd.startswith("sha256sum"):
                return 0, cmd.split()[-1].rsplit("/", 1)[-1][:-len(".db.part")] + "  x"
            if cmd.startswith("mv"):
                return 124, f"no answer from {host} within {timeout:.0f} s"   # the hang, now ended
            return 0, ""

        def scp(argv, **kw):
            copies.append((argv[-1], kw.get("timeout"), kw.get("stdin")))
            return subprocess.CompletedProcess(argv, 0, "", "")
        real = (ss.sd.ssh, ss.subprocess.run)
        ss.sd.ssh, ss.subprocess.run = far_end, scp
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                rc = ss.mirror_now(plan)
        finally:
            ss.sd.ssh, ss.subprocess.run = real
        said = buf.getvalue()
        check(rc == 1 and "MIRROR FAILED: place: no answer from x@h within 120 s" in said,
              f"the run ends, failed and said, instead of waiting: {rc} {said}")
        check(all(t is not None for _c, t in seen), f"every ssh step had a limit: {seen}")
        cleared = [(c, n) for c, n in commands if ".db.part' -mmin" in c]
        check(len(cleared) == 1 and cleared[0][1] == 0 and "-delete" in cleared[0][0],
              f"an interrupted copy's partial is cleared, before anything is copied: {commands[:4]}")
        check(not any(c.startswith("rm ") and ".part" in c for c, _n in commands),
              f"and only an old one: a live copy's is not this run's to remove: {commands[:4]}")
        size = os.path.getsize(os.path.join(root, "objects", f"{names[0]}.db"))
        check(copies and copies[0][1] == ss.mirror_limit(size, ss.MIRROR_COPY_BPS)
              and copies[0][2] is subprocess.DEVNULL, f"the copy's limit is its size's: {copies}")


def test_node_integrity():
    """[GDE-INT-145]: status says each node's file checks from its newest sync,
    a node the newest run missed from the run before; STALE on a file not whole
    or a pass unfinished in PASS_DAYS; a build that does not report is said,
    not stale, and neither is a node with the check switched off."""
    import io
    import json
    import contextlib
    print("status: each node's reading of its own files, from its newest sync")
    utc = dt.datetime.now(dt.timezone.utc)
    recent = (utc - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (utc - dt.timedelta(days=ss.PASS_DAYS + 5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        runs = os.path.join(root, "runs")
        older = {"gone": {"integrity": {"files": 9, "checked": 9, "oldest": recent, "not_ok": []}},
                 "fine": {"integrity": {"files": 9, "checked": 1, "oldest": old, "not_ok": []}}}
        newer = {"fine": {"integrity": {"files": 9, "checked": 9, "oldest": recent, "rate_kib_per_s": 1024,
                                        "not_ok": [{"outcome": "retagged", "path": "t.mp3"}]}},
                 "oldbuild": {"integrity": None},
                 "rotten": {"integrity": {"files": 9, "checked": 9, "oldest": recent,
                                          "not_ok": [{"outcome": "damaged", "path": "x.mp3"}]}},
                 "slow": {"integrity": {"files": 9, "checked": 4, "oldest": old, "not_ok": []}},
                 "off": {"integrity": {"files": 9, "checked": 4, "oldest": old, "rate_kib_per_s": 0,
                                       "not_ok": []}},
                 "locked": {"integrity": {"error": "database is locked"}},
                 "busy": {"integrity": {"files": None, "files_note": "the catalogue could not be counted",
                                        "checked": 9, "oldest": recent, "not_ok": []}},
                 "untagged": {"integrity": {"files": 9, "checked": 9, "oldest": recent, "not_ok": [],
                                            "untagged": 340}}}
        for stamp, b in (("20261001T0100Z", older), ("20261015T2005Z", newer)):
            os.makedirs(os.path.join(runs, stamp))
            with open(os.path.join(runs, stamp, "baselines.json"), "w") as fh:
                json.dump(b, fh)
        os.makedirs(os.path.join(runs, "20261016T0000Z"))           # a run that stopped before baselines
        plan = {"runs": runs}
        got = ss.node_integrity(plan)
        check(got["gone"][0] == "20261001T0100Z" and got["fine"][0] == "20261015T2005Z",
              f"the newest run naming each node: {[(n, r) for n, (r, _i) in sorted(got.items())]}")
        stale = {n: ss.integrity_stale(n, ic, utc) for n, (_r, ic) in got.items() if ic is not None}
        check(stale["fine"] == [] and stale["gone"] == [] and stale["off"] == [],
              f"whole, a re-tag, or switched off: not stale: {stale}")
        check(len(stale["rotten"]) == 1 and "1 file(s) not whole" in stale["rotten"][0], f"{stale['rotten']}")
        check(len(stale["slow"]) == 1 and f"in {ss.PASS_DAYS} days" in stale["slow"][0], f"{stale['slow']}")
        check(len(stale["locked"]) == 1 and "could not be read" in stale["locked"][0],
              f"checks that could not be read are stale, the hub not knowing: {stale['locked']}")
        check(stale["busy"] == [], f"a count that could not be made is not stale: {stale['busy']}")
        words = {n: ss.integrity_words(ic) for n, (_r, ic) in got.items() if ic is not None}
        check("could not be read (database is locked)" in words["locked"], words["locked"])
        check("could not be counted" in words["busy"] and "9 (its" in words["busy"], words["busy"])
        check("1 re-tagged (audio intact, still plays)" in words["fine"] and "all whole" in words["fine"],
              f"a re-tag is shown, and is not damage: {words['fine']}")
        check("340 without tags" in words["untagged"] and stale["untagged"] == [],
              f"files without tags are said, not stale: {words['untagged']} {stale['untagged']}")

        os.makedirs(os.path.join(root, "days"))
        with open(os.path.join(root, "days", "d.json"), "w") as fh:
            json.dump({"taken_at": utc.isoformat()}, fh)
        lib = os.path.join(root, "lib", "library.db")
        os.makedirs(os.path.join(root, "lib", "verify"))
        with open(os.path.join(root, "lib", "verify", "v.json"), "w") as fh:
            json.dump({"checked_at": dt.datetime.now().astimezone().isoformat(), "files": 0, "ok": 0, "bytes": 0,
                       "seconds": 0, "unrecorded": 0, "retagged": [], "damaged": [], "missing": [],
                       "unreadable": [], "ok_whole": True}, fh)
        plan = {"backups": {"dir": root, "max_age_hours": 30}, "hub": {"library": lib}, "runs": runs}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = ss.status(plan)
        said = out.getvalue()
        check(rc == 1 and "oldbuild: does not report reading its files back (sync of 2026-10-15 20:05Z)" in said
              and "STALE: rotten holds 1 file(s) not whole" in said and "STALE: slow has not read" in said
              and "STALE: oldbuild" not in said, f"status: {said}")


def test_prune_snapshots():
    """[SPEC-STAR-084]: a snapshot past `keep_snapshot_days` is removed unless it
    is among the newest few, holds a KEEP file, or holds anything but database
    copies; one whose name carries no date is never touched, and the daily
    store's own folders are not snapshots. Without the setting, nothing goes."""
    import io
    import contextlib
    print("snapshots pruned past their days, never one kept for a reason")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        made = {
            "pre-rekey-20260927T0415Z": ["library.db", "listener.db"],
            "pre-covers-20260927": ["library.db"],
            "prime-time-original-20260927": ["Prime Time.mp3"],
            "before-merge-2026-09-30": ["library.db", "KEEP"],
            "pre-repair-20261018T2158Z": ["library.db"],
            "pre-sync-20261106T0118Z": ["listener.db"],
            "pre-fade-ends-20261107": ["library.db", "library.db-wal"],
            "hand-copy": ["library.db"],
            "pre-bad-20261399": ["library.db"],
            "objects": ["a.db"],
            "days": ["2026-09-01.json"],
        }
        for d, files in made.items():
            os.makedirs(os.path.join(root, d))
            for f in files:
                p = os.path.join(root, d, f)
                open(p, "wb").write(b"x" * 10)
                ss.seal(p)
        open(os.path.join(root, "backup.log"), "w").write("a file, not a snapshot")
        plan = {"backups": {"dir": root, "keep_snapshot_days": 20, "keep_snapshots": 2}}
        today = dt.date(2026, 11, 8)
        gone, kept = ss.snapshots_to_prune(plan, today)
        check(gone == ["pre-covers-20260927", "pre-rekey-20260927T0415Z", "pre-repair-20261018T2158Z"],
              f"past 20 days and only database copies: {gone}")
        check("Prime Time.mp3" in kept.get("prime-time-original-20260927", ""), f"a song is kept, said: {kept}")
        check("KEEP" in kept.get("before-merge-2026-09-30", ""), f"a KEEP file keeps it: {kept}")
        check("no date" in kept.get("hand-copy", "") and "no date" in kept.get("pre-bad-20261399", ""),
              f"no date, or none that is real, is never pruned: {kept}")
        check("objects" not in kept and "days" not in kept and "pre-fade-ends-20261107" not in kept,
              f"the daily store and the young are not listed: {kept}")
        plan["backups"]["keep_snapshots"] = 4
        gone, kept = ss.snapshots_to_prune(plan, today)
        check(gone == ["pre-covers-20260927"]
              and kept.get("pre-repair-20261018T2158Z") == "one of the newest 4"
              and kept.get("pre-rekey-20260927T0415Z") == "one of the newest 4"
              and "KEEP" in kept.get("before-merge-2026-09-30", ""),
              f"the newest few copies of the pair are kept whatever their age, and a snapshot kept "
              f"for another reason takes no place among them: {gone} {kept}")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ss.prune_snapshots(plan, commit=False, today=today)
        check(len(os.listdir(root)) == len(made) + 1 and "would prune" in out.getvalue(),
              "without --commit nothing is removed")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ss.prune_snapshots(plan, today=today)
        left = set(os.listdir(root))
        check(left == set(made) - {"pre-covers-20260927"} | {"backup.log"},
              f"removed, sealed files and all: {sorted(left)}\n{out.getvalue()}")
        plan["prune"] = {"prefix": "pre-sync-", "keep": 3}
        plan["backups"]["keep_snapshots"] = 0
        gone, kept = ss.snapshots_to_prune(plan, dt.date(2027, 1, 1))
        check("pre-sync-20261106T0118Z" not in gone + list(kept),
              f"a sync's own copies are left to the sync's own rule: {gone} {kept}")
        del plan["backups"]["keep_snapshot_days"]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ss.prune_snapshots(plan, today=today)
        check(ss.snapshots_to_prune(plan, today) == ([], {}) and "none is pruned" in out.getvalue(),
              f"no setting, no pruning, and it says so: {out.getvalue()}")


def test_prune_runs():
    """[SPEC-STAR-089]: the newest `keep` runs kept, and every older one a copy last
    sent lies in, a kept run's baseline names, or the commit has in hand; the rest
    removed, sealed files and all. `adopted/` is not a run, and is never touched."""
    import io
    import json
    import contextlib
    print("runs pruned to the newest few, never one still needed")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        runs = os.path.join(root, "data", "sync")
        stamps = [f"202610{d:02d}T0100Z" for d in range(1, 10)]
        for s in stamps + ["adopted"]:
            os.makedirs(os.path.join(runs, s, "merge", "nodes", "n"))
            p = os.path.join(runs, s, "merge", "nodes", "n", "library.db")
            open(p, "wb").write(b"x" * 10)
            ss.seal(p)
        rel = lambda s, f: os.path.relpath(os.path.join(runs, s, "merge", "nodes", "n", f), root)
        win = lambda p: p.replace("/", "\\")                     # as the hub writes it
        state = {"hub": {"listener_sent": win(rel(stamps[8], "listener.db")), "library_sent": win(rel(stamps[8], "library.db"))},
                 "nodes": {"old": {"listener_sent": win(rel(stamps[0], "listener.db")), "library_sent": None},
                           "cat": {"listener_sent": rel(stamps[8], "listener.db"), "library_sent": rel(stamps[2], "library.db")},
                           "adopt": {"library_sent": rel("adopted", "library.db")}}}
        json.dump(state, open(os.path.join(runs, "state.json"), "w"))
        # the newest kept run's baseline still names the copy in stamps[1]
        json.dump({"cat": {"library": os.path.join(runs, stamps[1], "merge", "nodes", "n", "library.db")}},
                  open(os.path.join(runs, stamps[8], "baselines.json"), "w"))
        plan = {"runs": runs, "hub": {"name": "desk"}}
        real = ss.ROOT
        ss.ROOT = root
        try:
            gone, pinned = ss.runs_to_prune(plan, 5, spare=[stamps[3]])
            check(gone == [], f"every run older than the newest 5 is still needed here: {gone}")
            check(sorted(pinned) == stamps[:4], f"pinned: {pinned}")
            check(any("old's listener" in w for w in pinned[stamps[0]])
                  and any("cat's library" in w for w in pinned[stamps[2]])
                  and any("baseline in" in w for w in pinned[stamps[1]])
                  and pinned[stamps[3]] == ["the run in hand"], f"and each says why: {pinned}")
            gone, pinned = ss.runs_to_prune(plan, 3)
            check(gone == [stamps[3], stamps[4], stamps[5]], f"past the newest 3, the unneeded: {gone}")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                ss.prune_runs(plan, 3, commit=False)
            check(all(os.path.isdir(os.path.join(runs, s)) for s in stamps) and "would prune" in out.getvalue(),
                  "without --commit nothing is removed")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                ss.prune_runs(plan, 3)
            left = sorted(os.listdir(runs))
            check(left == sorted(stamps[:3] + stamps[6:] + ["adopted", "state.json"]),
                  f"removed, sealed files and all; adopted/ and state.json stay: {left}\n{out.getvalue()}")
            try:
                ss.runs_to_prune(plan, 0)
                check(False, "keeping no run is refused")
            except SystemExit:
                pass
        finally:
            ss.ROOT = real


def test_an_older_player_is_sent_no_table_it_would_refuse():
    """[SPEC-LYR-200]: a player's patch may name only the tables its own list
    allows, and one naming another is refused whole. So a node whose snapshot
    holds no `passage_lyrics` -- a player from before the table -- is sent
    none of it, with its other shared edits, and a current one is sent it."""
    import sqlite3
    print("an older player is sent no table it would refuse; a current one is sent it")
    ddl = ("CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT, flagged_at TEXT, origin TEXT, "
           "PRIMARY KEY (subject_kind, subject_id))")
    lyrics = ("CREATE TABLE passage_lyrics (audio_md5 TEXT NOT NULL, kind TEXT NOT NULL, start_ms INTEGER NOT NULL, "
              "end_ms INTEGER NOT NULL, mbid TEXT, text TEXT NOT NULL, no_words INTEGER NOT NULL DEFAULT 0, "
              "updated_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (audio_md5, kind, start_ms))")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        def make(name, with_table, *sql):
            p = os.path.join(root, name)
            c = sqlite3.connect(p)
            c.execute(ddl)
            if with_table:
                c.execute(lyrics)
            for s in sql:
                c.execute(s)
            c.commit()
            c.close()
            return p
        flag = "INSERT INTO listener_flags VALUES ('recording','r1','2026-11-12 09:00:00',NULL)"
        words = ("INSERT INTO passage_lyrics VALUES ('A','radio',0,1000,'rec','typed',0,"
                 "'2026-11-12 10:00:00.000','hub')")
        target = make("target.db", True, flag, words)
        older = make("older.db", False)
        current = make("current.db", True)

        check("passage_lyrics" in ss.SIGNED_TABLES, "the merge shares the table")
        tables, held_back = ss.signed_tables(older)
        check(held_back == ["passage_lyrics"] and "passage_lyrics" not in tables and "listener_flags" in tables,
              f"held back from a node that sent none: {held_back}")
        p, good, held_back = ss.values_patch(older, target)
        named = sorted(e["name"] for e in p["tables"])
        check(good and named == ["listener_flags"] and held_back == ["passage_lyrics"],
              f"its patch carries its other edits and never names the table: {named}, proven {good}")

        tables, held_back = ss.signed_tables(current)
        check(held_back == [] and "passage_lyrics" in tables, f"nothing is held back from a current player: {held_back}")
        p, good, held_back = ss.values_patch(current, target)
        named = sorted(e["name"] for e in p["tables"])
        check(good and named == ["listener_flags", "passage_lyrics"] and held_back == [],
              f"a current player is sent the words: {named}, proven {good}")
        sent = [e for e in p["tables"] if e["name"] == "passage_lyrics"][0]
        check(sent["key"] == ["audio_md5", "kind", "start_ms"] and len(sent["rows"]) == 1 and "create" not in sent,
              f"by its own key, as rows, the table being there already: {sent.get('key')}")


def main() -> int:
    test_an_older_player_is_sent_no_table_it_would_refuse()
    test_backup_proofs()
    test_mirror_when_reachable()
    test_mirror_limits()
    test_node_integrity()
    test_prune_runs()
    test_prune_snapshots()
    days =[f"2026-{m:02d}-{d:02d}" for m in (7, 8, 9) for d in (1, 10, 20, 28)]
    keep = ss.retained(days, 3, 2)
    check({"2026-09-10", "2026-09-20", "2026-09-28"} <= keep, f"the newest 3 days are kept: {sorted(keep)}")
    check("2026-08-01" in keep and "2026-09-01" in keep, "and the first day of each of the 2 newest months")
    check("2026-07-01" not in keep and "2026-08-28" not in keep, "and nothing else")
    check(ss.retained(days, 0, 0) == set(), "keeping nothing keeps nothing")
    check(ss.retained(["2026-09-26", "2026-09-26"], 14, 12) == {"2026-09-26"}, "a repeated day counts once")

    # The copy a node was last sent is read as its backup from that moment:
    # named so the merge finds it, and dated when it was sent.
    tmp = tempfile.mkdtemp()
    sent = os.path.join(tmp, "sent.db")
    open(sent, "wb").close()
    at = "2026-09-26T20:13:38+00:00"
    folder = ss.history_of(sent, at, os.path.join(tmp, "history"))
    names = os.listdir(folder)
    epoch = int(dt.datetime.fromisoformat(at).timestamp())
    check(names == [f"listener-{epoch}.db"], f"history is listener-<epoch>.db: {names}")
    node = sm.Node({"name": "n", "listener": sent, "backups": folder})
    check([h[0] for h in node.history] == [sm.norm_time(epoch)], "and the merge reads it as a backup of that time")
    check(ss.history_of(None, "", os.path.join(tmp, "none")) and
          os.listdir(os.path.join(tmp, "none")) == [], "a node never sent anything has no history")

    # [SPEC-MTR-110]: the mesh's keys and roster, kept with the pair.
    import mesh
    import sqlite3
    mdir = os.path.join(tmp, "mesh")
    mesh.init(mdir, "Backup test", "hub")
    os.makedirs(os.path.join(mdir, "enrol"), exist_ok=True)
    open(os.path.join(mdir, "enrol", "x.json"), "w").write("{}")
    a, b = os.path.join(tmp, "m1.db"), os.path.join(tmp, "m2.db")
    ss.mesh_archive(mdir, a)
    ss.mesh_archive(mdir, b)
    check(ss.sha256(a) == ss.sha256(b), "an unchanged mesh archives to the same bytes, so it is stored once")
    c = sqlite3.connect(a)
    got = {n: d for n, d in c.execute("SELECT name, data FROM sqlar")}
    c.close()
    check(sorted(got) == sorted(ss.MESH_FILES)
          and got["mesh.key"] == open(os.path.join(mdir, "mesh.key"), "rb").read(),
          f"the archive holds the keys and roster, byte for byte, and no enrolment: {sorted(got)}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("star_sync: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
