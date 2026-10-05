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
import hashlib
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
        self.natural = set()        # the players that report natural_keys [SPEC-NKP-075]
        self.staged = {}            # (node, run) -> {n: (sha256, text)}  [SPEC-NKP-925]
        self.parts_applied = {}     # (node, run) -> the parts a commit applied

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

    def sync_step(self, mdir, player, op, run, catalogue_patch=None, listener_patch=None, timeout=0, extra=None):
        name = player["address"]
        node = self.nodes[name]
        if op == "stage":
            part = (extra or {})["part"]
            if hashlib.sha256(part["text"].encode("utf-8")).hexdigest() != part["sha256"]:
                raise ValueError(f"part {part['n']} did not arrive intact")
            self.staged.setdefault((name, run), {})[part["n"]] = (part["sha256"], part["text"])
            self.log.append((name, "stage", run, True, False))
            return {"staged": part["n"], "of": part["of"], "bytes": len(part["text"])}
        if op == "staged":
            return {"catalogue": {str(n): sha for n, (sha, _) in self.staged.get((name, run), {}).items()}}
        parts = (extra or {}).get("catalogue_parts")
        if parts:
            have = self.staged.get((name, run), {})
            for i, sha in enumerate(parts["sha256"], 1):
                if i not in have or have[i][0] != sha:
                    raise ValueError(f"catalogue part {i} of {parts['of']} is not staged here")
            if op == "commit":
                self.parts_applied[(name, run)] = [json.loads(have[i][1]) for i in range(1, parts["of"] + 1)]
                del self.staged[(name, run)]
        if op == "snapshot":
            self.log.append((name, "snapshot", run, False, False))
            res = {"listener": export(node["listener"], ss.SIGNED_TABLES), "catalogue": summary(node["library"]),
                   "last_run": None}
            if name in self.natural:
                res["natural_keys"] = 1
            return res
        self.log.append((name, op, run, bool(catalogue_patch) or bool(parts), bool(listener_patch)))
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


def catalogue_scenario(tmp, b_current, artists=0):
    """a holds the hub's new music under the hub's ids, b under its own -- the case found on every player on
    2026-10-05. a's player is old; b's is current if `b_current`."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False), "c": (same, False)})
    if b_current:
        fleet.natural.add("b")
    new = lambda fid, pid: (f"INSERT INTO files VALUES ({fid},'C','/x/c.mp3',100,1,1)",
                            f"INSERT INTO passages VALUES ({pid},{fid},'radio',0,500)")
    for db, (fid, pid) in ((hub["library"], (9, 20)), (fleet.nodes["a"]["library"], (9, 20)),
                           (fleet.nodes["b"]["library"], (7, 15)), (fleet.nodes["c"]["library"], (9, 20))):
        c = sqlite3.connect(db)
        for q in new(fid, pid):
            c.execute(q)
        c.commit()
        c.close()
    if artists:
        c = sqlite3.connect(hub["library"])
        for i in range(artists):
            c.execute(f"INSERT INTO artists VALUES ('mb{i:03d}', 'Artist number {i}', 'manual')")
        c.commit()
        c.close()
    # c also holds a file the hub has never heard of.
    c = sqlite3.connect(fleet.nodes["c"]["library"])
    c.execute("INSERT INTO files VALUES (10,'D','/x/d.mp3',100,1,1)")
    c.commit()
    c.close()
    with faked(fleet):
        # c has a file the hub lacks, so its own copy cannot be made: leave it out of this merge.
        ss.snapshot(plan, {"a", "b"})
        run = ss.newest_run(plan)
        base = json.load(open(os.path.join(run, "baselines.json")))
        ss.merge(plan, run)
        ss.patch(plan, run)
    return fleet, plan, hub, run, base


def test_catalogue_states(tmp):
    """[SPEC-STAR-940], [SPEC-NKP-075]: what a node's catalogue summary says about it, and what each kind
    of player is sent for it."""
    fleet, plan, hub, run, base = catalogue_scenario(tmp, b_current=True)
    check(base["a"]["catalogue"]["state"] == "holds-hub-rows" and base["a"]["catalogue_in_step"],
          f"a holds the hub's new rows under the hub's ids: patchable, the rehearsal decides: {base['a']['catalogue']}")
    check(base["b"]["catalogue"] == {"state": "other-ids", "files": 1, "passages": 1} and base["b"]["catalogue_in_step"],
          f"b holds the same music under its own ids, and its player is current, so it can be patched: {base['b']['catalogue']}")
    check(ss.catalogue_words(base["b"]["catalogue"]) == "the hub's music under other ids (1 files, 1 passages)", "and says so in words")
    pa = json.load(open(os.path.join(run, "patches", "a", "catalogue.values.json")))
    pb = json.load(open(os.path.join(run, "patches", "b", "catalogue.values.json")))
    check(not any("natural" in e for e in pa["tables"]), "an old player is sent the strict patch by id")
    check({e["name"] for e in pb["tables"] if "natural" in e} == {"files", "passages"},
          f"a current one is sent the tables that hold ids by natural key: {[e['name'] for e in pb['tables']]}")
    fe = next(e for e in pb["tables"] if e["name"] == "files")
    check("file_id" not in fe["columns"] and fe["rows"][0]["key"] == ["C"] and "also" in fe["rows"][0],
          f"carrying no id, the new file's machine-scope values as `also`: {fe}")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("by natural key" in summary, f"the summary says so: {summary}")
    check("WARNING -- OLD PLAYER: a" in summary and "OLD PLAYER: b" not in summary, "and warns of a alone, which is old")
    # And the direct call, for the case the merge could not take.
    ch = ss.catalogue_state(os.path.join(tmp, "sent", "c.library.db"), os.path.join(run, "a", "summary.db"),
                            os.path.join(run, "desktop", "library.db"))
    check(ch["state"] == "holds-hub-rows", f"the classifier on its own: {ch}")
    cn = os.path.join(tmp, "c-summary.db")
    shutil.copyfile(fleet.nodes["c"]["library"], cn)
    ch = ss.catalogue_state(os.path.join(tmp, "sent", "c.library.db"), cn, os.path.join(run, "desktop", "library.db"))
    check(ch["state"] == "changed" and ch["not_the_hubs"] == 1, f"a file the hub lacks is a real change: {ch}")
    check(ss.catalogue_state(None, cn, cn) == {"state": "no-copy"}, "and with nothing last sent it says so")


def test_catalogue_old_player(tmp):
    """An old player holding the music under other ids is sent no catalogue patch, and is told so loudly."""
    fleet, plan, hub, run, base = catalogue_scenario(tmp, b_current=False)
    check(not base["b"]["catalogue_in_step"] and base["b"]["natural_keys"] == 0, f"{base['b']}")
    check(not os.path.exists(os.path.join(run, "patches", "b", "catalogue.values.json")), "no patch for b")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("OLD: it cannot take one" in summary and "WARNING -- OLD PLAYERS: a, b" in summary,
          f"the summary says why, and names both old players: {summary}")


def test_catalogue_in_parts(tmp):
    """[SPEC-NKP-920..935]: a patch too big for one request is cut, the natural entries whole in the first part; the parts
    are staged once, a rehearsal's upload is not repeated by the commit, and one commit applies them all."""
    real = (ss.CATALOGUE_PATCH_MAX, ss.PART_MAX)
    ss.CATALOGUE_PATCH_MAX, ss.PART_MAX = 50, 600
    try:
        fleet, plan, hub, run, base = catalogue_scenario(tmp, b_current=True, artists=40)
    finally:
        ss.CATALOGUE_PATCH_MAX, ss.PART_MAX = real
    pd = os.path.join(run, "patches", "b")
    man = json.load(open(os.path.join(pd, "catalogue.parts.json")))
    check(man["of"] > 2 and not os.path.exists(os.path.join(pd, "catalogue.values.json")), f"b's catalogue goes in parts: {man['of']}")
    parts = [json.load(open(os.path.join(pd, m["file"]))) for m in man["parts"]]
    check({e["name"] for e in parts[0]["tables"]} == {"files", "passages"} and all("natural" in e for e in parts[0]["tables"]),
          f"the natural entries are whole, in the first part: {[e['name'] for e in parts[0]['tables']]}")
    check(all("natural" not in e for p in parts[1:] for e in p["tables"]) and
          sum(len(e["rows"]) for p in parts[1:] for e in p["tables"] if e["name"] == "artists") == 40,
          "and the other table is cut by rows, every row once")
    check(all(m["bytes"] <= 600 + 200 for m in man["parts"][1:]), f"each cut part is about the size asked: {[m['bytes'] for m in man['parts']]}")
    check(man["total_bytes"] == sum(m["bytes"] for m in man["parts"]), "the manifest holds the total")
    for m in man["parts"]:
        check(hashlib.sha256(open(os.path.join(pd, m["file"]), "rb").read()).hexdigest() == m["sha256"], f"{m['file']} is as its digest says")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check(f"in {man['of']} parts, staged on the node and committed there in one transaction" in summary, f"the summary says so: {summary}")
    with faked(fleet):
        stages(plan, "b")
        staged_after_rehearsal = sum(1 for e in fleet.log if e[0] == "b" and e[1] == "stage")
        check(staged_after_rehearsal == man["of"], f"a rehearsal stages every part: {staged_after_rehearsal}")
        check(stages(plan, "b", commit=True) == 0, "the commit succeeds")
        total = sum(1 for e in fleet.log if e[0] == "b" and e[1] == "stage")
        check(total == man["of"], f"and sends none of them again: {total} stage requests in all")
    applied = fleet.parts_applied.get(("b", os.path.basename(run)))
    check(applied is not None and len(applied) == man["of"], "one commit applied every part")
    st = ss.load_state(plan)
    check(st["nodes"]["b"]["library_sent"].endswith("library.db") and st["nodes"]["b"]["run"] == os.path.basename(run),
          "and b's last-sent catalogue is now this one")


def test_a_patch_that_cannot_be_cut(tmp):
    """[SPEC-NKP-930]: natural entries that pass a part's limit cannot be cut, and are not sent; the listener half is."""
    real = (ss.CATALOGUE_PATCH_MAX, ss.NATURAL_PART_MAX)
    ss.CATALOGUE_PATCH_MAX, ss.NATURAL_PART_MAX = 50, 10
    try:
        fleet, plan, hub, run, base = catalogue_scenario(tmp, b_current=True)
    finally:
        ss.CATALOGUE_PATCH_MAX, ss.NATURAL_PART_MAX = real
    pd = os.path.join(run, "patches", "b")
    check(not [f for f in os.listdir(pd) if f.startswith("catalogue.")], "no catalogue patch for b")
    check(os.path.exists(os.path.join(pd, "listener.values.json")), "but its listener patch is made")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("cannot be sent in parts" in summary and "cannot be cut" in summary, f"and the summary says why: {summary}")


def test_split_catalogue():
    """[SPEC-NKP-930]: the splitter, on its own."""
    def entry(name, n, **more):
        return dict({"name": name, "columns": ["a", "b"], "key": ["a"], "rows": [{"key": [i], "was": None, "now": [i, "x" * 20]} for i in range(n)]}, **more)
    nat = entry("files", 3, natural={"surrogate": "file_id", "refs": []})
    cat = {"tables": [entry("artists", 30, add_columns=["c TEXT"]), nat, entry("releases", 2, create=["CREATE TABLE releases (a)"])]}
    parts = ss.split_catalogue(cat, limit=500)
    check([e["name"] for e in parts[0]["tables"]] == ["files"], "the natural entries come first, and whole")
    rest = [e for p in parts[1:] for e in p["tables"]]
    check([e["name"] for e in rest][:1] == ["artists"] and sum(len(e["rows"]) for e in rest if e["name"] == "artists") == 30,
          "the others in table order, every row once")
    first = [e for e in rest if e["name"] == "artists"]
    check("add_columns" in first[0] and all("add_columns" not in e for e in first[1:]), "a table's schema change rides in its first piece only")
    check(any(e["name"] == "releases" and "create" in e for e in rest), "and a small table keeps its `create`")
    check(all(ss.json_size(p) <= 500 + 80 for p in parts[1:]), f"parts about the limit: {[ss.json_size(p) for p in parts]}")
    check(ss.split_catalogue({"tables": [entry("artists", 1)]}, limit=10)[0]["tables"][0]["rows"][0]["key"] == [0],
          "a row bigger than the limit is a part of its own, not an error")
    real = ss.NATURAL_PART_MAX
    ss.NATURAL_PART_MAX = 10
    try:
        ss.split_catalogue({"tables": [nat]})
        check(False, "natural entries past the limit must be refused")
    except ss.PartsUnsupported as err:
        check("cannot be cut" in str(err), f"{err}")
    finally:
        ss.NATURAL_PART_MAX = real
    ss.PARTS_MAX, real_max = 3, ss.PARTS_MAX
    try:
        ss.split_catalogue({"tables": [entry("artists", 30)]}, limit=100)
        check(False, "too many parts must be refused")
    except ss.PartsUnsupported as err:
        check("parts, more than" in str(err), f"{err}")
    finally:
        ss.PARTS_MAX = real_max
    check(ss.split_catalogue({"tables": []}) == [], "an empty patch has no parts")


def test_selection(tmp):
    """[SPEC-STAR-130]: a run takes part with the nodes it is given, and leaves the rest untouched --
    not contacted, not started, not written, and their last-sent record as it was."""
    fleet = Fleet(tmp)
    base = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, base, {"a": (base, False), "b": (base, False), "t": (base, True)})
    for n in ("a", "b", "t"):
        c = sqlite3.connect(fleet.nodes[n]["listener"])
        c.execute("UPDATE listener_preferences SET rotation=7, recovery=7, updated_at='2026-10-02 00:00:00'")
        c.commit()
        c.close()
    state_before = json.load(open(os.path.join(plan["runs"], "state.json")))
    b_before = open(fleet.nodes["b"]["listener"], "rb").read()
    with faked(fleet):
        for bad, word in ((set(), "no node chosen"), ({"desktop"}, "at least one other node"),
                          ({"ghost"}, "not nodes of this fleet")):
            why = refused(lambda: ss.snapshot(plan, bad))
            check(why and word in why, f"a choice that is empty or names a stranger is refused: {why}")
        ss.snapshot(plan, {"a"})
        run = ss.newest_run(plan)
        check([e[0] for e in fleet.log if e[1] == "snapshot"] == ["a"], f"only a was asked: {fleet.log}")
        check(fleet.ssh_log == [] and "t" not in fleet.up, f"the on-demand player was never started: {fleet.ssh_log}")
        sel = json.load(open(os.path.join(run, "selected.json")))
        check(sel == {"nodes": ["a"], "left_alone": ["b", "t"]}, f"the run records who took part and who was left alone: {sel}")
        check(sorted(json.load(open(os.path.join(run, "baselines.json")))) == ["a", "desktop"], "and only they are in it")
        ss.merge(plan, run)
        ss.patch(plan, run)
        ss.approve_all(run)
        check(stages(plan) == 0 and stages(plan, commit=True) == 0, "rehearsed and committed")
        check(ss.distribute(plan, run, ["b"], False) == 1, "a node left alone is not in the run, and is not reached by naming it")
        done = json.load(open(os.path.join(run, "rehearsal.json")))
        check(done.get("a") == 0 and done.get("desktop") == 0, f"the hub always takes part, named or not [SPEC-STAR-134]: {done}")
        check("at least one other node" in (refused(lambda: ss.distribute(plan, run, ["desktop"], False)) or ""),
              "and the hub alone is not a run")
    check(open(fleet.nodes["b"]["listener"], "rb").read() == b_before, "b's database is byte for byte as it was")
    check(json.load(open(os.path.join(plan["runs"], "state.json")))["nodes"]["b"] == state_before["nodes"]["b"]
          and json.load(open(os.path.join(plan["runs"], "state.json")))["nodes"]["t"] == state_before["nodes"]["t"],
          "and the record of what b and t were last sent is as it was")
    check(prefs_of(fleet.nodes["a"]["listener"])["X"] == 7 and prefs_of(hub["listener"])["X"] == 7, "while a and the hub took part")
    check(not [e for e in fleet.log if e[0] in ("b", "t")], f"nothing was asked of b or t at any stage: {fleet.log}")


def test_old_players(tmp):
    """[SPEC-NKP-075]: an old player is said loudly -- at the snapshot, in the summary, and again
    before a rehearsal or a commit -- and a current one is not."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False)})
    fleet.natural.add("b")                     # b is current; a is not
    with faked(fleet) as out:
        ss.snapshot(plan)
        text = out.getvalue()
        check("!!!  OLD PLAYER: a" in text and "OLD PLAYER(S) ['a']" in text and "b: reports no" not in text,
              f"the snapshot names a, loudly, and not b: {text}")
        run = ss.newest_run(plan)
        check(json.load(open(os.path.join(run, "old_players.json"))).keys() == {"a"}, "and keeps the list with the run")
        ss.merge(plan, run)
        out.truncate(0)
        out.seek(0)
        ss.patch(plan, run)
        summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
        check("WARNING -- OLD PLAYER: a" in summary, f"the summary opens with it: {summary[:300]}")
        out.truncate(0)
        out.seek(0)
        ss.distribute(plan, run, [], False)
        check("OLD PLAYER: a" in out.getvalue(), "and a rehearsal says it again before it reaches anyone")
        out.truncate(0)
        out.seek(0)
        ss.distribute(plan, run, ["b"], False)
        check("OLD PLAYER" not in out.getvalue(), "but not when only the current player is named")
    fleet2 = Fleet(tempfile.mkdtemp())
    plan2, _ = build(fleet2.tmp, fleet2, same, {"a": (same, False)})
    fleet2.natural.add("a")
    with faked(fleet2) as out:
        ss.snapshot(plan2)
        check("OLD PLAYER" not in out.getvalue(), "a fleet of current players says nothing")


def main() -> int:
    test_split_catalogue()
    for test in (test_quiet, test_conflict, test_approve_all, test_on_demand, test_catalogue_moved,
                 test_catalogue_states, test_catalogue_old_player, test_catalogue_in_parts, test_a_patch_that_cannot_be_cut, test_selection,
                 test_old_players):
        tmp = tempfile.mkdtemp()
        try:
            test(tmp)
        except Exception as err:             # a test that breaks is a failure with its cause shown
            import traceback
            FAILED.append(f"{test.__name__} raised {err!r}")
            traceback.print_exc()
    print()
    if FAILED:
        for m in FAILED:                     # again, since a check inside the fake fleet's redirect is not seen
            print(f"  FAIL  {m}", file=sys.__stdout__)
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("star_sync flow: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
