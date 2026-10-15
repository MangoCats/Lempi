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
        ss.sd.ssh = lambda host, cmd, quiet=False: (255, "ssh: connect to host off: timed out")
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


def main() -> int:
    test_backup_proofs()
    test_mirror_when_reachable()
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
