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


def main() -> int:
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
