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
            check(rc == 1 and "has not been reachable to take a newer one" in text, f"a day missed: STALE: {text}")
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
        seen, copies = [], []

        def far_end(host, cmd, quiet=False, timeout=None):
            seen.append((cmd.split()[0], timeout))
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


def main() -> int:
    test_backup_proofs()
    test_mirror_when_reachable()
    test_mirror_limits()
    test_node_integrity()
    days = [f"2026-{m:02d}-{d:02d}" for m in (7, 8, 9) for d in (1, 10, 20, 28)]
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
