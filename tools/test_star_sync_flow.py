#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The signed star sync end to end, against a fake fleet [SPEC-STAR-100..126].

The players are stand-ins that answer `mesh.sync_step` as `star_node.rs` does:
a snapshot of the shared tables and a summary of the catalogue, and a
rehearsal or commit of a values patch under the member rule. Everything else is
the real thing: the merge, the items and verdicts, the gate, the patches and
their proofs, the hub's own patch, the state kept between runs, and an
on-demand player started and stopped around a stage.

    python tools/test_star_sync_flow.py
"""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_patch as sp  # noqa: E402
import star_sync as ss  # noqa: E402
import test_star_merge as tm  # noqa: E402  -- its schemas

FAILED = []
LIB = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, size_bytes INTEGER,
    mtime INTEGER, last_seen INTEGER);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER,
    end_ms INTEGER);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT, source TEXT);
"""


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def make_db(path, schema, *sql):
    c = sqlite3.connect(path)
    c.executescript(schema)
    for s in sql:
        c.execute(s)
    c.commit()
    c.close()


def export(db, tables):
    c = sqlite3.connect(db)
    out = []
    for t in tables:
        info = list(c.execute(f"PRAGMA table_info({t})"))
        if not info:
            continue
        cols = [r[1] for r in info]
        key = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]] or cols
        create = [r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL "
                                          "ORDER BY type DESC, name", (t,))]
        out.append({"name": t, "create": create, "columns": cols, "key": key,
                    "rows": [[sp.enc(v) for v in r] for r in c.execute(f"SELECT {', '.join(cols)} FROM {t}")]})
    c.close()
    return {"tables": out}


def summary(db):
    c = sqlite3.connect(db)
    out = []
    for t, cols in (("files", ["file_id", "audio_md5", "path", "size_bytes", "mtime", "last_seen"]),
                    ("passages", ["passage_id", "file_id", "kind", "start_ms", "end_ms"])):
        out.append({"name": t, "columns": cols, "key": [cols[0]],
                    "rows": [[sp.enc(v) for v in r] for r in c.execute(f"SELECT {', '.join(cols)} FROM {t}")]})
    c.close()
    return {"tables": out}


class Fleet:
    """Players that answer as the real ones do, and the ssh that starts them."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.up = set()
        self.log = []
        self.ssh_log = []
        self.nodes = {}
        self.runs = []

    def add(self, name, prefs, always_up=True):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        self.nodes[name] = dict(listener=os.path.join(d, "listener.db"), library=os.path.join(d, "library.db"))
        prefs_sql = [f"INSERT INTO listener_preferences VALUES ('artist','{i}',{r},{r},0,'{t}')" for i, r, t in prefs]
        make_db(self.nodes[name]["listener"], tm.SCHEMA, *prefs_sql)
        make_db(self.nodes[name]["library"], LIB,
                f"INSERT INTO files VALUES (1,'A','/{name}/a.mp3',100,1,1)",
                "INSERT INTO passages VALUES (1,1,'radio',0,1000)",
                "INSERT INTO artists VALUES ('x','Ex','manual')")
        if always_up:
            self.up.add(name)

    # -- what the code under test calls ---------------------------------
    def sync_players(self, mdir):
        return {f"fp-{n}": {"fingerprint": f"fp-{n}", "address": n, "web_port": 0} for n in sorted(self.up)}

    def sync_step(self, mdir, player, op, run, catalogue_patch=None, listener_patch=None, timeout=0):
        name = player["address"]
        node = self.nodes[name]
        if op == "snapshot":
            return {"listener": export(node["listener"], ss.SIGNED_TABLES), "catalogue": summary(node["library"]),
                    "last_run": None}
        self.log.append((name, op, run, bool(catalogue_patch), bool(listener_patch)))
        if not listener_patch:
            return {"listener": None, "catalogue": None}
        target = node["listener"]
        if op == "rehearse":
            target = target + ".rehearse"
            shutil.copyfile(node["listener"], target)
        counts = sp.apply_member(target, listener_patch, set(ss.SIGNED_TABLES))
        return {"listener": counts, "catalogue": None}

    def ssh(self, host, cmd, quiet=False):
        self.ssh_log.append((host, cmd))
        name = host.split("@")[1]
        if cmd.startswith("start"):
            self.up.add(name)
        elif cmd.startswith("stop"):
            self.up.discard(name)
        return 0, ""


def prefs_of(db):
    c = sqlite3.connect(db)
    r = dict(c.execute("SELECT subject_id, rotation FROM listener_preferences"))
    c.close()
    return r


def build(tmp, fleet, hub_prefs, nodes):
    hub = dict(listener=os.path.join(tmp, "hub-listener.db"), library=os.path.join(tmp, "hub-library.db"))
    make_db(hub["listener"], tm.SCHEMA,
            *[f"INSERT INTO listener_preferences VALUES ('artist','{i}',{r},{r},0,'{t}')" for i, r, t in hub_prefs])
    make_db(hub["library"], LIB, "INSERT INTO files VALUES (1,'A','C:/a.mp3',100,1,1)",
            "INSERT INTO passages VALUES (1,1,'radio',0,1000)", "INSERT INTO artists VALUES ('x','Ex','manual')")
    runs = os.path.join(tmp, "runs")
    os.makedirs(os.path.join(tmp, "backups"))
    os.makedirs(runs)
    state = {"hub": {}, "nodes": {}}
    sent = os.path.join(tmp, "sent")
    os.makedirs(sent)
    shutil.copyfile(hub["listener"], os.path.join(sent, "hub.listener.db"))
    shutil.copyfile(hub["library"], os.path.join(sent, "hub.library.db"))
    state["hub"] = {"at": "2026-10-01T00:00:00+00:00", "listener_sent": os.path.join(sent, "hub.listener.db"),
                    "library_sent": os.path.join(sent, "hub.library.db"), "run": "20261001T0000Z"}
    plan = {"hub": {"name": "desktop", **hub}, "runs": runs, "prune": {"prefix": "pre-sync-", "keep": 3},
            "backups": {"dir": os.path.join(tmp, "backups"), "keep_daily": 14, "keep_monthly": 12}, "nodes": {}}
    for name, (prefs, on_demand) in nodes.items():
        fleet.add(name, prefs, always_up=not on_demand)
        n = fleet.nodes[name]
        # What it was last sent: its own pair as it stood then.
        shutil.copyfile(n["listener"], os.path.join(sent, f"{name}.listener.db"))
        shutil.copyfile(n["library"], os.path.join(sent, f"{name}.library.db"))
        state["nodes"][name] = {"at": "2026-10-01T00:00:00+00:00", "run": "20261001T0000Z",
                                "listener_sent": os.path.join(sent, f"{name}.listener.db"),
                                "library_sent": os.path.join(sent, f"{name}.library.db")}
        plan["nodes"][name] = {"host": f"u@{name}", "member": f"fp-{name}"}
        if on_demand:
            plan["nodes"][name]["on_demand"] = {"start": "start-player", "stop": "stop-player"}
    with open(os.path.join(runs, "state.json"), "w") as fh:
        json.dump(state, fh)
    return plan, hub


@contextlib.contextmanager
def faked(fleet):
    real = (ss.meshmod.sync_players, ss.meshmod.sync_step, ss.sd.ssh, ss.console_running, ss.stamp, time.sleep)
    counter = iter(range(1, 99))
    ss.meshmod.sync_players, ss.meshmod.sync_step = fleet.sync_players, fleet.sync_step
    ss.sd.ssh = fleet.ssh
    ss.console_running = lambda excuse=None: []
    ss.stamp = lambda t=None: f"2026100{next(counter) % 10}T1200Z"
    time.sleep = lambda s: None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            yield buf
    finally:
        (ss.meshmod.sync_players, ss.meshmod.sync_step, ss.sd.ssh, ss.console_running, ss.stamp, time.sleep) = real


def stages(plan, *names, commit=False, bulk=False):
    run = ss.newest_run(plan)
    return ss.distribute(plan, run, list(names), commit, bulk=bulk)


def refused(fn):
    try:
        fn()
    except SystemExit as err:
        return str(err)
    return None


def test_quiet(tmp):
    """Nothing changed anywhere: no item, and the whole routine runs without a verdict."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False)})
    with faked(fleet) as out:
        check(ss.snapshot(plan) == 0, "a snapshot of every node, signed")
        run = ss.newest_run(plan)
        check(ss.merge(plan, run) == 0, "merge")
        check(ss.merge_items(run)["items"] == [], "nothing to approve when every node holds what the hub holds")
        check(ss.patch(plan, run) == 0, "every patch proven")
        check(stages(plan) == 0, "rehearsal clean everywhere")
        check(stages(plan, commit=True) == 0, "and commit, with no verdict asked for")
    st = ss.load_state(plan)
    check(all(st["nodes"][n]["run"].startswith("2026100") for n in ("a", "b")) and st["hub"]["run"].startswith("2026100"),
          "the state names this run as last sent, for the hub and every node")


def test_conflict(tmp):
    """Two nodes change one recording differently: an item, defaulting to the later change; a verdict
    for the other forces a new merge; the gate refuses until every item has one."""
    fleet = Fleet(tmp)
    base = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, base, {"a": (base, False), "b": (base, False)})
    # Each node edits X after its last sync, differently.
    for n, (r, t) in (("a", (3, "2026-10-02 00:00:00")), ("b", (5, "2026-10-03 00:00:00"))):
        c = sqlite3.connect(fleet.nodes[n]["listener"])
        c.execute(f"UPDATE listener_preferences SET rotation={r}, recovery={r}, updated_at='{t}' WHERE subject_id='X'")
        c.commit()
        c.close()
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        items = ss.merge_items(run)["items"]
        check(len(items) == 1 and items[0]["default"] == "b" and items[0]["kind"] == "conflict",
              f"one item, the later change b the default: {items}")
        ss.patch(plan, run)
        why = refused(lambda: stages(plan, commit=True))
        check(why and "no verdict" in why, f"the gate refuses while an item has no verdict: {why}")
        ss.give_verdict(run, items[0]["id"], "chose:a")
        rv = ss.review(run)
        check(len(rv["stale"]) == 1 and not rv["pending"], "a verdict that changes the outcome is awaiting a new merge")
        why = refused(lambda: stages(plan, commit=True))
        check(why and "does not reflect" in why, f"and the gate says so: {why}")
        check(ss.patch(plan, run) == 0, "patch merges again by itself")
        check(os.path.isdir(os.path.join(run, "void", "merge-1")), "the earlier merge is kept beside it, void")
        check(not ss.review(run)["stale"] and not ss.review(run)["pending"], "now the verdict is reflected")
        why = refused(lambda: stages(plan, commit=True))
        check(why and "no clean rehearsal" in why, f"the gate wants a rehearsal after the last merge: {why}")
        check(stages(plan) == 0, "rehearsal clean")
        check(stages(plan, commit=True) == 0, "commit")
    check(prefs_of(fleet.nodes["a"]["listener"])["X"] == 3 and prefs_of(fleet.nodes["b"]["listener"])["X"] == 3,
          "both nodes now hold the change the verdict chose")
    check(prefs_of(hub["listener"])["X"] == 3, "and so does the hub")
    v = ss.load_verdicts(run)
    check(list(v.values())[0]["by"] == "item", "the verdict records that a person gave it")


def test_approve_all(tmp):
    """Approve all takes every default, records it, and keeps a verdict already given."""
    fleet = Fleet(tmp)
    base = [("X", 1, "2026-09-01 00:00:00"), ("Y", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, base, {"a": (base, False), "b": (base, False)})
    for n, (r, t) in (("a", (3, "2026-10-02 00:00:00")), ("b", (5, "2026-10-03 00:00:00"))):
        c = sqlite3.connect(fleet.nodes[n]["listener"])
        c.execute(f"UPDATE listener_preferences SET rotation={r}, recovery={r}, updated_at='{t}'")
        c.commit()
        c.close()
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        items = ss.merge_items(run)["items"]
        check(len(items) == 2, f"two recordings in conflict: {len(items)}")
        ss.give_verdict(run, items[0]["id"], "approved")
        kinds = ss.approve_all(run)
        v = ss.load_verdicts(run)
        by = sorted(x["by"] for x in v.values())
        check(kinds == {"conflict": 1} and by == ["approve-all", "item"],
              f"only the item still waiting was approved in bulk, and says so: {kinds} {by}")
        ss.patch(plan, run)
        stages(plan)
        check(stages(plan, commit=True) == 0, "commit after approve-all")
    check(prefs_of(fleet.nodes["a"]["listener"]) == {"X": 5, "Y": 5}, "each recording took the later change")


def test_on_demand(tmp):
    """[SPEC-STAR-101]: a player kept on demand is started for a stage and stopped after it, and only if
    the stage started it."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "t": (same, True)})
    with faked(fleet):
        check("t" not in fleet.up, "its player is down")
        ss.snapshot(plan)
        check(fleet.ssh_log == [("u@t", "start-player"), ("u@t", "stop-player")] and "t" not in fleet.up,
              f"started for the snapshot and stopped after it: {fleet.ssh_log}")
        run = ss.newest_run(plan)
        check(os.path.isdir(os.path.join(run, "t")), "and it was snapshotted")
        fleet.ssh_log.clear()
        fleet.up.add("t")                      # now somebody has started it by hand
        ss.merge(plan, run)
        ss.patch(plan, run)
        stages(plan)
        check(fleet.ssh_log == [] and "t" in fleet.up, f"a player already up is left up: {fleet.ssh_log}")
    # An on-demand node whose player never answers is missing, not an error for the others.
    fleet2 = Fleet(tempfile.mkdtemp())
    plan2, _ = build(fleet2.tmp, fleet2, same, {"a": (same, False), "t": (same, True)})
    fleet2.ssh = lambda host, cmd, quiet=False: (fleet2.ssh_log.append((host, cmd)) or (0, ""))   # starts nothing
    with faked(fleet2) as out:
        fleet2.ssh = lambda host, cmd, quiet=False: (fleet2.ssh_log.append((host, cmd)) or (0, ""))
        ss.sd.ssh = fleet2.ssh
        rc = ss.snapshot(plan2)
    check(rc == 3, "a player that does not answer leaves the node missing from the run")
    run2 = ss.newest_run(plan2)
    missing = json.load(open(os.path.join(run2, "missing.json")))
    check(list(missing) == ["t"] and "did not answer" in missing["t"], f"and says why: {missing}")


def test_catalogue_moved(tmp):
    """[SPEC-STAR-940]: a node whose catalogue summary is not what was last sent is sent no catalogue patch."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False)})
    c = sqlite3.connect(fleet.nodes["b"]["library"])
    c.execute("INSERT INTO passages VALUES (2,1,'album',0,500)")      # b ingested something on its own
    c.commit()
    c.close()
    with faked(fleet):
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("catalogue: **none sent**" in summary and "changed on the node" in summary,
          f"the summary says b gets no catalogue, and why: {summary}")
    check(os.path.exists(os.path.join(run, "patches", "a", "catalogue.values.json"))
          and not os.path.exists(os.path.join(run, "patches", "b", "catalogue.values.json")),
          "a catalogue patch for a, none for b")


def test_catalogue_states(tmp):
    """[SPEC-STAR-940]: what a node's catalogue summary says about it. One node holds the hub's new music
    under the hub's ids, one under its own -- the case found on every player on 2026-10-05."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False), "c": (same, False)})
    new = lambda fid, pid: (f"INSERT INTO files VALUES ({fid},'C','/x/c.mp3',100,1,1)",
                            f"INSERT INTO passages VALUES ({pid},{fid},'radio',0,500)")
    for db, (fid, pid) in ((hub["library"], (9, 20)), (fleet.nodes["a"]["library"], (9, 20)),
                           (fleet.nodes["b"]["library"], (7, 15)), (fleet.nodes["c"]["library"], (9, 20))):
        c = sqlite3.connect(db)
        for q in new(fid, pid):
            c.execute(q)
        c.commit()
        c.close()
    # c also holds a file the hub has never heard of.
    c = sqlite3.connect(fleet.nodes["c"]["library"])
    c.execute("INSERT INTO files VALUES (10,'D','/x/d.mp3',100,1,1)")
    c.commit()
    c.close()
    with faked(fleet):
        # c has a file the hub lacks, so its own copy cannot be made: leave it out of this merge.
        plan["nodes"].pop("c")
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        base = json.load(open(os.path.join(run, "baselines.json")))
        ss.merge(plan, run)
        ss.patch(plan, run)
    check(base["a"]["catalogue"]["state"] == "holds-hub-rows" and base["a"]["catalogue_in_step"],
          f"a holds the hub's new rows under the hub's ids: patchable, the rehearsal decides: {base['a']['catalogue']}")
    check(base["b"]["catalogue"] == {"state": "other-ids", "files": 1, "passages": 1} and not base["b"]["catalogue_in_step"],
          f"b holds the same music under its own ids: {base['b']['catalogue']}")
    check(ss.catalogue_words(base["b"]["catalogue"]) == "the hub's music under other ids (1 files, 1 passages)", "and says so in words")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("numbered differently" in summary, f"the summary says why a strict patch cannot reach it: {summary}")
    check(os.path.exists(os.path.join(run, "patches", "a", "catalogue.values.json"))
          and not os.path.exists(os.path.join(run, "patches", "b", "catalogue.values.json")),
          "a catalogue patch for a, none for b")
    # And the direct call, for the case the merge could not take.
    ch = ss.catalogue_state(os.path.join(tmp, "sent", "c.library.db"), os.path.join(run, "a", "summary.db"),
                            os.path.join(run, "desktop", "library.db"))
    check(ch["state"] == "holds-hub-rows", f"the classifier on its own: {ch}")
    cn = os.path.join(tmp, "c-summary.db")
    shutil.copyfile(fleet.nodes["c"]["library"], cn)
    ch = ss.catalogue_state(os.path.join(tmp, "sent", "c.library.db"), cn, os.path.join(run, "desktop", "library.db"))
    check(ch["state"] == "changed" and ch["not_the_hubs"] == 1, f"a file the hub lacks is a real change: {ch}")
    check(ss.catalogue_state(None, cn, cn) == {"state": "no-copy"}, "and with nothing last sent it says so")


def main() -> int:
    for test in (test_quiet, test_conflict, test_approve_all, test_on_demand, test_catalogue_moved,
                 test_catalogue_states):
        tmp = tempfile.mkdtemp()
        try:
            test(tmp)
        except Exception as err:             # a test that breaks is a failure with its cause shown
            import traceback
            FAILED.append(f"{test.__name__} raised {err!r}")
            traceback.print_exc()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("star_sync flow: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
