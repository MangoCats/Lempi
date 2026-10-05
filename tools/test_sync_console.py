#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Sync page's logic [SPEC-STAR-120..128], over the fake fleet of
test_star_sync_flow.py: where a run stands, items in words and in pages,
verdicts, and what may be submitted as a stage.

    python tools/test_sync_console.py
"""
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_sync as ss  # noqa: E402
import sync_console as sc  # noqa: E402
import test_star_sync_flow as fl  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def catalogue():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT, weight REAL);
        CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
        INSERT INTO recordings VALUES ('r1','Blue in Green');
        INSERT INTO artists VALUES ('a1','Miles Davis');
        INSERT INTO recording_artists VALUES ('r1','a1',1.0);
        INSERT INTO passage_recordings VALUES (7,'r1');
    """)
    return c


def test_describe():
    c = catalogue()
    d = sc.describe(c, "listener_preferences", ["recording", "r1"])
    check(d == {"text": "recording “Blue in Green — Miles Davis”", "resolved": True}, f"a recording by title and artist: {d}")
    d = sc.describe(c, "listener_flags", ["artist", "a1"])
    check(d["text"] == "artist “Miles Davis”" and d["resolved"], f"an artist by name: {d}")
    d = sc.describe(c, "listener_preferences", ["passage", 7])
    check(d["resolved"] and "Blue in Green" in d["text"], f"a passage by its recording: {d}")
    d = sc.describe(c, "listener_preferences", ["recording", "nope"])
    check(d == {"text": "recording nope", "resolved": False}, f"a subject the hub lacks says its id, unresolved: {d}")
    d = sc.describe(sqlite3.connect(":memory:"), "listener_preferences", ["recording", "r1"])
    check(not d["resolved"], "a catalogue without the tables does not raise")
    d = sc.describe(c, "listener_occasions", ["user.xmas", "xmasy"])
    check(d["text"] == "occasion user.xmas", f"an occasion: {d}")


def test_page():
    tmp = tempfile.mkdtemp()
    fleet = fl.Fleet(tmp)
    base = [("X", 1, "2026-09-01 00:00:00"), ("Y", 1, "2026-09-01 00:00:00")]
    plan, hub = fl.build(tmp, fleet, base, {"a": (base, False), "b": (base, False)})
    for n, (r, t) in (("a", (3, "2026-10-02 00:00:00")), ("b", (5, "2026-10-03 00:00:00"))):
        c = sqlite3.connect(fleet.nodes[n]["listener"])
        c.execute(f"UPDATE listener_preferences SET rotation={r}, recovery={r}, updated_at='{t}'")
        c.commit()
        c.close()
    os.environ["LEMPI_STAR_PLAN"] = os.path.join(tmp, "plan.json")
    check(sc.load_plan()[0] is None and "no plan" in sc.load_plan()[1], "a missing plan is said, not raised")
    json.dump(plan, open(os.environ["LEMPI_STAR_PLAN"], "w"))
    check(sc.load_plan()[0] is not None, "and a present one loads")

    st = sc.state(plan)
    check(st["run"] is None and [n["name"] for n in st["nodes"]] == ["a", "b"], "before any run there are nodes and no run")
    with fl.faked(fleet):
        why = sc.stage_target(plan, None, "merge", [])[1]
        check(why and "no run" in why, f"a stage before a snapshot is refused: {why}")
        check(sc.stage_target(plan, None, "snapshot", [])[0] is not None, "a snapshot may always be asked for")
        check(sc.stage_target(plan, None, "wipe", [])[1].startswith("not a stage"), "an unknown stage is refused")
        ss.snapshot(plan)
        run = sc.newest(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)
        st = sc.state(plan)
        r = st["run"]
        check(r["merged"] and r["proven"] and r["items"] == 2 and r["pending"] == 2 and not r["ready"],
              f"merged, proven, two items waiting, not ready: {r}")
        check(any("no verdict" in b for b in r["blockers"]), f"and the blocker says so: {r['blockers']}")
        check(sc.stage_target(plan, run, "commit", [])[0] is None, "a commit is refused while the gate stands")
        check(all(n["catalogue"] == "as last sent" and n["in_run"] == "taken" for n in st["nodes"]), "both nodes taken, catalogue in step")

        page = sc.items_page(run, catalogue(), after=0, limit=1)
        check(page["matching"] == 2 and len(page["items"]) == 1 and page["counts"]["pending"] == 2, f"paged, counts for the whole run: {page['counts']}")
        check(page["items"][0]["status"] == "pending" and page["counts"]["pending_by_kind"] == {"conflict": 2},
              "pending, and counted by kind for the approve-all confirmation")
        p2 = sc.items_page(run, catalogue(), after=1, limit=1)
        check(p2["items"][0]["id"] != page["items"][0]["id"], "the second page is the other item")
        check(sc.items_page(run, catalogue(), node="a")["matching"] == 2 and sc.items_page(run, catalogue(), node="zz")["matching"] == 0,
              "filtered by node")
        check(sc.items_page(run, catalogue(), kind="tie")["matching"] == 0, "filtered by kind")

        first = page["items"][0]
        out = sc.set_verdicts(run, {first["id"]: "chose:a", "nope": "approved", p2["items"][0]["id"]: "chose:zz"})
        check(out["set"] == 1 and set(out["errors"]) == {"nope", p2["items"][0]["id"]}, f"verdicts validated one by one: {out}")
        pg = sc.items_page(run, catalogue())
        st_of = {x["id"]: x["status"] for x in pg["items"]}
        check(st_of[first["id"]] == "stale" and pg["counts"]["stale"] == 1,
              f"a verdict that changes the outcome is awaiting a new merge: {st_of}")
        kinds = sc.pending_kinds(run)
        check(kinds == {"conflict": 1}, f"one item still without a verdict: {kinds}")
        check(ss.approve_all(run) == {"conflict": 1}, "approve-all takes the rest")
        pg = sc.items_page(run, catalogue())
        by = {x["id"]: x["verdict"]["by"] for x in pg["items"]}
        check(sorted(by.values()) == ["approve-all", "item"], f"and the record says which was which: {by}")
        s = sc.summary(run)
        check(s["summary"] and "Sync summary" in s["summary"] and s["report"], "the summary and the report are readable")

        ss.patch(plan, run)                     # merges again for the verdict
        ss.distribute(plan, run, [], False)
        r = sc.state(plan)["run"]
        check(r["ready"] and not r["blockers"] and r["void_merges"] == 1, f"ready, one merge kept void: {r}")
        t, why = sc.stage_target(plan, run, "commit", [])
        check(t and json.loads(t) == {"stage": "commit", "nodes": []}, f"and a commit may now be submitted: {why}")
        check(sc.stage_target(plan, run, "commit", ["ghost"])[1].startswith("not in the plan"), "a node nobody knows is refused")

    sc.lock(True)
    check(sc.locked(), "the console's lock can be taken")
    sc.lock(False)
    check(not sc.locked(), "and lifted")


def main() -> int:
    test_describe()
    test_page()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("sync_console: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
