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
import posixpath
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_patch as sp  # noqa: E402
import star_sync as ss  # noqa: E402
import retired  # noqa: E402  -- the hub's ledger of retired files [SPEC-RTF-035]
import test_star_merge as tm  # noqa: E402  -- its schemas

FAILED = []
LIB = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, size_bytes INTEGER,
    mtime INTEGER, last_seen INTEGER);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER,
    end_ms INTEGER, passage_uid TEXT);
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
                    ("passages", ["passage_id", "file_id", "kind", "start_ms", "end_ms", "passage_uid"])):
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
        self.uids = set()           # ... and passage_uids [IMPL-UID-052]; `add` makes each current
        self.indexes = set()        # ... and their catalogue's indexes [IMPL-UID-056]
        self.readonly = set()       # players whose catalogue is on a read-only partition [SPEC-STAR-104]
        self.window = set()         # ... and which of them can open it themselves
        self.staged = {}            # (node, run) -> {n: (sha256, text)}  [SPEC-NKP-925]
        self.parts_applied = {}     # (node, run) -> the parts a commit applied
        self.rows_cap = {}          # node -> the `catalogue_rows` it reports [SPEC-STAR-945, SPEC-LNK-050]
        self.asked = []             # (node, want) for every `rows` request
        self.retire_cap = set()     # players that report retire_files [SPEC-RTF-060]
        self.disk = {}              # node -> {path: sha256}: what its music folder holds [SPEC-RTF-025]
        self.retire_asked = []      # (node, op, [audio_md5]) for every step carrying a retire list
        self.again_asked = []       # (node, op, again) for every step [SPEC-NSH-032]
        self.lose_answer = set()    # nodes whose next commit lands and whose answer is lost
        self.answers = {}           # (node, run) -> the answer a commit gave, kept as a current player keeps it
        self.broken_snapshot = set()  # nodes whose snapshot answer lacks its catalogue [SPEC-NSH-092]
        self.commit_again = set()   # players that report commit_again

    def rows(self, name, want):
        """`mesh_sync::catalogue_rows`, cut to the tables these tests hold."""
        c = sqlite3.connect(self.nodes[name]["library"])
        inl = lambda xs: ",".join("?" * len(xs)) or "NULL"         # noqa: E731
        recs = list(want.get("recordings", []))
        rels = list(want.get("releases", []))
        files = list(want.get("files", []))
        by = bool(want.get("by_recording")) and self.rows_cap.get(name, 0) >= 2
        if by:
            files += [r[0] for r in c.execute(
                "SELECT DISTINCT f.audio_md5 FROM passage_recordings pr JOIN passages p ON p.passage_id = pr.passage_id "
                f"JOIN files f ON f.file_id = p.file_id WHERE pr.mbid IN ({inl(recs)})", recs)]
        has = lambda t: c.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()  # noqa: E731
        tl = bool(want.get("tracklists")) and self.rows_cap.get(name, 0) >= 3 and has("release_recordings")
        if tl:
            recs += [r[0] for r in c.execute(
                f"SELECT DISTINCT mbid FROM release_recordings WHERE release_mbid IN ({inl(rels)})", rels)]
        fids = [r[0] for r in c.execute(f"SELECT file_id FROM files WHERE audio_md5 IN ({inl(files)})", files)]
        pids = [r[0] for r in c.execute(f"SELECT passage_id FROM passages WHERE file_id IN ({inl(fids)})", fids)]

        def dump(t, col, vals):
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
            if not cols:
                return None
            return {"name": t, "columns": cols, "key": [cols[0]],
                    "rows": [list(r) for r in c.execute(f"SELECT {', '.join(cols)} FROM {t} WHERE {col} IN ({inl(vals)})", vals)]}
        tables = [x for x in (dump("files", "audio_md5", files), dump("passages", "file_id", fids),
                              dump("passage_recordings", "passage_id", pids), dump("recordings", "mbid", recs),
                              dump("release_recordings", "mbid", recs), dump("releases", "mbid", rels)) if x]
        c.close()
        out = {"scope": {"files": files, "recordings": recs, "releases": rels, "artists": []}, "tables": tables}
        if self.rows_cap.get(name, 0) >= 2:
            out["by_recording"] = by
        if self.rows_cap.get(name, 0) >= 3:
            out["tracklists"] = tl
        return out

    def add(self, name, prefs, always_up=True):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        self.nodes[name] = dict(listener=os.path.join(d, "listener.db"), library=os.path.join(d, "library.db"))
        prefs_sql = [f"INSERT INTO listener_preferences VALUES ('artist','{i}',{r},{r},0,'{t}')" for i, r, t in prefs]
        make_db(self.nodes[name]["listener"], tm.SCHEMA, *prefs_sql)
        make_db(self.nodes[name]["library"], LIB,
                f"INSERT INTO files VALUES (1,'A','/{name}/a.mp3',100,1,1)",
                "INSERT INTO passages VALUES (1,1,'radio',0,1000,'u-A-radio-0-1000')",
                "INSERT INTO artists VALUES ('x','Ex','manual')")
        if always_up:
            self.up.add(name)
        # Current, as every node in the fleet is: a test that wants an old one says so.
        self.natural.add(name)
        self.uids.add(name)

    # -- what the code under test calls ---------------------------------
    def sync_players(self, mdir):
        return {f"fp-{n}": {"fingerprint": f"fp-{n}", "address": n, "web_port": 0} for n in sorted(self.up)}

    def sync_step(self, mdir, player, op, run, catalogue_patch=None, listener_patch=None, timeout=0, extra=None,
                  again=False):
        name = player["address"]
        self.again_asked.append((name, op, again))
        current = name in self.commit_again
        if current and (name, run) in self.answers:
            # As a current player answers the same run asked again [SPEC-NSH-032]: the
            # commit as it answered it, and its staged parts consumed.
            if op == "commit":
                return dict(self.answers[(name, run)], committed_earlier=True)
            if op == "staged":
                return {"catalogue": {}, "committed": True}
        got = self.step(name, op, run, catalogue_patch, listener_patch, extra)
        if name in self.broken_snapshot and op == "snapshot":
            del got["catalogue"]               # an answer not of the shape asked for [SPEC-NSH-092]
        if op == "commit" and current:
            self.answers[(name, run)] = got
        if op == "commit" and name in self.lose_answer:
            self.lose_answer.discard(name)
            raise ConnectionResetError("the commit landed, and its answer was lost on the way back")
        return got

    def step(self, name, op, run, catalogue_patch, listener_patch, extra):
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
            if name in self.uids:
                res["passage_uids"] = 1
            if name in self.indexes:
                c = sqlite3.connect(node["library"])
                res["catalogue_indexes"] = dict(c.execute("SELECT name, tbl_name FROM sqlite_master "
                                                           "WHERE type = 'index' AND sql IS NOT NULL"))
                c.close()
            if name in self.readonly:
                res["library_readonly"] = True
            if name in self.window:
                res["catalogue_window"] = 1
            if name in self.rows_cap:
                res["catalogue_rows"] = self.rows_cap[name]
            if name in self.retire_cap:
                res["retire_files"] = 1
            if name in self.commit_again:
                res["commit_again"] = 1
                res["last_run"] = max((r for (n, r) in self.answers if n == name), default=None)
            return res
        if op == "rows":
            self.asked.append((name, (extra or {})["want"]))
            return self.rows(name, extra["want"])
        self.log.append((name, op, run, bool(catalogue_patch) or bool(parts), bool(listener_patch)))
        retire = None
        if (extra or {}).get("retire"):
            # As star_node.rs answers [SPEC-RTF-025]: a file where it was sent, with the
            # hub's bytes, moved at a commit and only said at a rehearsal.
            self.retire_asked.append((name, op, [e["audio_md5"] for e in extra["retire"]]))
            disk, files = self.disk.setdefault(name, {}), []
            for e in extra["retire"]:
                have = disk.get(e["path"])
                outcome = "not there" if have is None else "bytes differ" if have != e["sha256"] else \
                    ("moved" if op == "commit" else "to move")
                to = None
                if outcome == "moved":
                    root = e["path"][:-len(e["rel_path"])]
                    to = f"{root}_Retired/{posixpath.dirname(e['rel_path'])}/{e['retired_at']}/{posixpath.basename(e['path'])}"
                    disk[to] = disk.pop(e["path"])
                files.append({"audio_md5": e["audio_md5"], "outcome": outcome, "from": e["path"], "to": to})
            retire = {"files": files}
        if not listener_patch:
            return {"listener": None, "catalogue": None, **({"retire": retire} if retire else {})}
        target = node["listener"]
        if op == "rehearse":
            target = target + ".rehearse"
            shutil.copyfile(node["listener"], target)
        counts = sp.apply_member(target, listener_patch, set(ss.SIGNED_TABLES))
        return {"listener": counts, "catalogue": None, **({"retire": retire} if retire else {})}

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
            "INSERT INTO passages VALUES (1,1,'radio',0,1000,'u-A-radio-0-1000')", "INSERT INTO artists VALUES ('x','Ex','manual')")
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
        vm = os.path.join(run, "void", "merge-1")
        kept = sorted(os.path.relpath(os.path.join(d, f), vm).replace("\\", "/") for d, _, fs in os.walk(vm) for f in fs)
        check(os.path.exists(os.path.join(run, "merge", "library.db"))
              and os.path.exists(os.path.join(run, "merge", "nodes", "a", "library.db"))
              and {"report.json", "report.md", "listener.db", "nodes/a/listener.db"} <= set(kept)
              and not any(os.path.basename(k).startswith("library.db") for k in kept),
              f"[SPEC-STAR-114] kept as its report and listener copies, its catalogue copies -- which every "
              f"merge makes -- dropped: {kept}")
        check("catalogue copies dropped" in out.getvalue(), "and the dropping said")
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


def test_hub_edited_during_sync(tmp):
    """The hub's catalogue edited after the snapshot, while the hub's patch is
    the listener's alone: the commit lands, the edit is kept and said, and the
    commit is not failed for it -- 2026-11-01, a recording swap made three
    minutes after the snapshot read `COMMITTED but a table DIFFERS`."""
    fleet = Fleet(tmp)
    base = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, base, {"a": (base, False)})
    c = sqlite3.connect(fleet.nodes["a"]["listener"])
    c.execute("UPDATE listener_preferences SET rotation=4, recovery=4, updated_at='2026-10-02 00:00:00' "
              "WHERE subject_id='X'")
    c.commit()
    c.close()
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)
        check(stages(plan) == 0, "rehearsal clean")
        c = sqlite3.connect(hub["library"])
        c.execute("UPDATE artists SET name = 'Ex, renamed on the hub' WHERE mbid = 'x'")
        c.commit()
        c.close()
        check(stages(plan, commit=True) == 0, "the commit is not failed by the hub's own edit")
    said = out.getvalue()
    check("library: no patch for it; ['artists'] changed here since the snapshot, and were kept" in said
          and "COMMITTED and verified" in said, f"the edit said, and kept: {said[-900:]}")
    c = sqlite3.connect(hub["library"])
    check(c.execute("SELECT name FROM artists WHERE mbid = 'x'").fetchone() == ("Ex, renamed on the hub",),
          "the hub's edit stands")
    c.close()
    check(prefs_of(hub["listener"])["X"] == 4, "and the node's change reached the hub")


def test_retired_files_moved(tmp):
    """[SPEC-RTF-020..060]: a merge retired two files on the hub -- one whose row
    the nodes still hold, one whose row an earlier run already took, as Highway
    61's. A player that can is told both, the second found beside the disc that
    replaced it; it moves the one there, says the other is not, and neither is
    asked about again. A player that cannot is sent nothing, and the files are
    named loudly."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False)})
    fleet.retire_cap.add("a")

    hub_sent = os.path.join(tmp, "sent", "hub.library.db")
    for p in (hub["library"], hub_sent):
        c = sqlite3.connect(p)
        c.execute("INSERT INTO files VALUES (2,'D','C:/Music/Artist/Album/disc.mp3',900,1,1)")
        c.execute("INSERT INTO files VALUES (3,'R','C:/Music/Artist/Album/old.mp3',100,1,1)")
        c.commit()
        c.close()
    c = sqlite3.connect(hub["library"])     # the merge, on the hub only
    c.execute("DELETE FROM files WHERE audio_md5 = 'R'")
    c.execute("CREATE TABLE ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL, "
              "stage TEXT NOT NULL, outcome TEXT NOT NULL, confidence REAL, detail TEXT, decided_at TEXT)")
    for md5, rel in (("R", "Artist/Album/old.mp3"), ("R2", "Artist/Album/older.mp3")):
        c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, detail, decided_at) "
                  "VALUES (?1, 'retire', 'retired', ?2, '2026-10-30T22:19:02')",
                  (md5, json.dumps({"sha256": f"sha-{md5}", "size_bytes": 100, "rel_path": rel, "replaced_by": "D"})))
    c.commit()
    c.close()
    for n in ("a", "b"):
        for p in (fleet.nodes[n]["library"], os.path.join(tmp, "sent", f"{n}.library.db")):
            c = sqlite3.connect(p)
            c.execute(f"INSERT INTO files VALUES (2,'D','/{n}/Music/Artist/Album/disc.mp3',900,1,1)")
            c.execute(f"INSERT INTO files VALUES (3,'R','/{n}/Music/Artist/Album/old.mp3',100,1,1)")
            c.commit()
            c.close()
        fleet.disk[n] = {f"/{n}/Music/Artist/Album/disc.mp3": "sha-D", f"/{n}/Music/Artist/Album/old.mp3": "sha-R"}
    hub_lib = hub["library"]
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)
        told = ss.read_json(os.path.join(run, "patches", "a", ss.RETIRE_FILE)) or {}
        by = {e["audio_md5"]: e for e in told.get("files", [])}
        check(sorted(by) == ["R", "R2"] and by["R"]["path"] == "/a/Music/Artist/Album/old.mp3"
              and by["R"]["found_by"] == "its row" and by["R2"]["path"] == "/a/Music/Artist/Album/older.mp3"
              and by["R2"]["found_by"] == "beside its replacement" and by["R"]["replacement_here"],
              f"a is told both: the one by its row, the other beside its replacement: {told}")
        check(not os.path.exists(os.path.join(run, "patches", "b", ss.RETIRE_FILE)), "b, which cannot, is told nothing")
        summary_ = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
        check("OLD PLAYER: 2 retired file(s) left on its disk" in summary_ and "/b/Music/Artist/Album/old.mp3" in summary_,
              "and its files are named for a person, loudly")
        check(stages(plan) == 0 and fleet.disk["a"].get("/a/Music/Artist/Album/old.mp3") == "sha-R",
              "a rehearsal moves nothing")
        check(stages(plan, commit=True) == 0, "commit")
        said = out.getvalue()
        ss.snapshot(plan)                   # the next run, in the same clock
        run2 = ss.newest_run(plan)
        ss.merge(plan, run2)
        ss.patch(plan, run2)
    check("a: retired files: 1 moved, 1 not there" in said, f"each outcome counted: {said[-1200:]}")
    check(fleet.disk["a"].get("/a/Music/_Retired/Artist/Album/2026-10-30/old.mp3") == "sha-R"
          and "/a/Music/Artist/Album/old.mp3" not in fleet.disk["a"], "the file moved to a's own _Retired")
    check([x for x in fleet.retire_asked if x[0] == "b"] == [], "b was never sent a retire list")
    led = retired.ledger(hub_lib)
    check(sorted((e["node"], e["audio_md5"], e["outcome"]) for e in led) == [("a", "R", "moved"), ("a", "R2", "not there")],
          f"the hub keeps what a answered: {led}")
    check(run2 != run and not os.path.exists(os.path.join(run2, "patches", "a", ss.RETIRE_FILE)),
          "the next run asks a about neither: both are settled there")


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


def test_the_copy_sent_is_dated_by_its_snapshot(tmp):
    """[SPEC-STAR-050]: the copy a node was last sent describes it at the run's snapshot, so the next run
    reads it as of then: the bundles sent to it since, and the backup its removals are dated by. Until
    2026-11-17 the commit's time was used -- a send made while the run was reviewed was never asked about,
    and a row re-added during the review lost to the removal. A state from before keeps the commit's
    time, and says so."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False)})
    asked = []
    real = ss.bundle_ledger.touched_since

    def touched(library, node, since):
        asked.append((node, since))
        return set()
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)
        stages(plan)
        stages(plan, commit=True)
        st = ss.load_state(plan)
        taken = {n["name"]: n["taken_at"] for n in ss.read_json(os.path.join(run, "manifest.json"))["nodes"]}
        check(st["nodes"]["a"]["snapshot_at"] == taken["a"] and st["hub"]["snapshot_at"] == taken["desktop"]
              and st["nodes"]["a"]["at"] > taken["a"],
              f"the state keeps when each snapshot was taken, beside when the commit landed: {st['nodes']['a']}")
        ss.bundle_ledger.touched_since = touched
        try:
            before = len(out.getvalue())
            ss.snapshot(plan)
            run2 = ss.newest_run(plan)
            said = out.getvalue()[before:]
            # A state from before 2026-11-17: the commit's time stands in, said.
            st["nodes"]["a"].pop("snapshot_at")
            ss.save_state(plan, st)
            before = len(out.getvalue())
            ss.snapshot(plan)
            said_old = out.getvalue()[before:]
        finally:
            ss.bundle_ledger.touched_since = real
    epoch = int(ss.dt.datetime.fromisoformat(taken["a"]).timestamp())
    check(os.listdir(os.path.join(run2, "a", "history")) == [f"listener-{epoch}.db"],
          f"the copy last sent is a's backup as of its snapshot: {os.listdir(os.path.join(run2, 'a', 'history'))}")
    check(asked[:1] == [("a", taken["a"])], f"and the sends to it are asked about from then: {asked}")
    check("stands in for it" not in said and "a: the state records when its last commit landed" in said_old
          and asked[1:] == [("a", st["nodes"]["a"]["at"])],
          f"a state with only the commit's time uses it, and says so: {said_old[-400:]}")


def test_the_hubs_commit_is_verified_by_its_counts(tmp):
    """[SPEC-STAR-129]: after the hub's commit, a row its patch names that is not at its target fails the
    verification. Until 2026-11-17 the verification looked for " 0 to apply" in the check's printed line,
    which a check finding a row changed since printed too: here, a row written again the moment the commit
    landed, and the commit read "verified"."""
    fleet = Fleet(tmp)
    base = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, base, {"a": (base, False)})
    c = sqlite3.connect(fleet.nodes["a"]["listener"])
    c.execute("UPDATE listener_preferences SET rotation=4, recovery=4, updated_at='2026-10-02 00:00:00' WHERE subject_id='X'")
    c.commit()
    c.close()
    real = ss.sp.apply
    mine = os.path.normcase(os.path.abspath(hub["listener"]))

    def apply_then_written(db, patch_path, commit):
        rc = real(db, patch_path, commit)
        if commit and os.path.normcase(os.path.abspath(db)) == mine:
            w = sqlite3.connect(db)
            w.execute("UPDATE listener_preferences SET rotation=7, recovery=7 WHERE subject_id='X'")
            w.commit()
            w.close()
        return rc
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)
        check(stages(plan) == 0, "rehearsal clean")
        ss.sp.apply = apply_then_written
        try:
            rc = stages(plan, commit=True)
        finally:
            ss.sp.apply = real
    said = out.getvalue()
    check(rc == 1 and "1 changed since, read again after the commit -- not verified" in said
          and "COMMITTED but a table DIFFERS" in said and "COMMITTED and verified" not in said,
          f"a row the patch named, not at its target after the commit, fails it: {said[-700:]}")
    check(ss.load_state(plan)["hub"]["run"] != ss.stamp_of(run) and prefs_of(hub["listener"])["X"] == 7,
          "the hub's state does not name the run, and the later write stands")
    counts = ss.sp.tally(hub["listener"], os.path.join(run, "patches", "desktop", "listener.patch.json"))
    check(counts == {"apply": 0, "already": 0, "conflict": 1}, f"tally gives the numbers themselves: {counts}")


def test_a_big_step_is_held(tmp):
    """[PI3-AIM-170], [PI3-AIM-177]: a step carrying a megabyte or more holds the node's playback, renewed
    while it runs, however the patch is sent; a smaller one is sent without. Until 2026-11-17 only a
    catalogue in parts was held, for a fixed hour with no renewal, and b's whole patch here went with none."""
    fleet, plan, hub, run, base = catalogue_scenario(tmp, b_current=True, artists=40)
    pd = os.path.join(run, "patches", "b")
    check(os.path.exists(os.path.join(pd, "catalogue.values.json"))
          and not os.path.exists(os.path.join(pd, "catalogue.parts.json")), "b's catalogue goes whole, not in parts")
    sent = ss.step_bytes(pd, None)
    files = [os.path.join(pd, f) for f in ("catalogue.values.json", "listener.values.json")]
    check(sent == sum(os.path.getsize(p) for p in files if os.path.exists(p)) and sent > 0,
          f"a step's size is its patches as written: {sent}")
    check(ss.step_bytes(pd, {"total_bytes": 5 << 20}) == sent + (5 << 20), "and a catalogue in parts by its total")
    asked = []
    real = (ss.transfer_hold.held, ss.HOLD_BYTES)

    def held(address, port=None, say=None, seconds=None, renew=False):
        asked.append((address, seconds, renew))
        return contextlib.nullcontext()
    ss.transfer_hold.held = held
    try:
        with faked(fleet):
            ss.HOLD_BYTES = sent              # b's step carries exactly this much: held
            stages(plan, "b")
            once = list(asked)
            ss.HOLD_BYTES = sent + 1          # a byte more than it carries: sent without
            stages(plan, "b")
    finally:
        ss.transfer_hold.held, ss.HOLD_BYTES = real
    check(once == [("b", ss.HOLD_SECS, True)], f"a whole patch of the size is held, the hold renewed: {once}")
    check(asked == once, f"and a step under the size is sent without one: {asked}")
    check((ss.HOLD_BYTES, ss.HOLD_SECS) == (1 << 20, 600), "a megabyte, and ten minutes renewed, as the Send's")


def test_a_commit_answer_lost(tmp):
    """[SPEC-NSH-032], [SPEC-NSH-094]: b's commit lands and its answer is lost. The next
    snapshot says so. The same run's commit tried again for b does not repeat the hub's
    step -- which met its own backup and ended the run before reaching b -- and b, asked
    again, answers as it did, and the hub records it."""
    fleet = Fleet(tmp)
    base = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, base, {"a": (base, False), "b": (base, False)})
    fleet.commit_again.add("b")
    # Edited on the hub, so that both nodes are sent it, and a -- an older build -- commits too.
    c = sqlite3.connect(hub["listener"])
    c.execute("UPDATE listener_preferences SET rotation=4, recovery=4, updated_at='2026-10-02 00:00:00' WHERE subject_id='X'")
    c.commit()
    c.close()
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.approve_all(run)
        ss.patch(plan, run)
        check(stages(plan) == 0, "rehearsal clean")
        fleet.lose_answer.add("b")
        check(stages(plan, commit=True) == 1, "the commit says b was refused: its answer never came")
        st = ss.load_state(plan)
        check((st["hub"]["run"], st["nodes"]["a"]["run"], st["nodes"]["b"]["run"])
              == (ss.stamp_of(run), ss.stamp_of(run), "20261001T0000Z"), f"so the hub recorded b as not sent: {st}")
        check(prefs_of(fleet.nodes["b"]["listener"])["X"] == 4, "though b holds the run")
        hub_at = st["hub"]["at"]
        ss.snapshot(plan)
        check(f"its last committed run is {ss.stamp_of(run)}, newer than the 20261001T0000Z the hub recorded"
              in out.getvalue(), "the next snapshot says the answer was lost, and how to record it")
        before = len(out.getvalue())
        check(ss.distribute(plan, run, ["b"], True) == 0, "the same run's commit, tried again for b, completes")
        said = out.getvalue()[before:]
        check("COMMITTED earlier in this run" in said and "not applied again" in said,
              f"the hub's own step is not repeated: {said}")
        check("committed there earlier, and its answer lost" in said, "b answers from its own record, and says so")
        st = ss.load_state(plan)
        check(st["nodes"]["b"]["run"] == ss.stamp_of(run), "and the hub records it")
        check(st["hub"]["at"] == hub_at, "leaving its own record as its commit made it")
    commits = [(n, again) for n, op, again in fleet.again_asked if op == "commit"]
    check(("b", True) in commits and ("a", False) in commits and all(again == (n in fleet.commit_again) for n, again in commits)
          and not any(op != "commit" and again for _n, op, again in fleet.again_asked),
          f"a commit is asked again only of a node that says it can answer one: {fleet.again_asked}")


def test_a_snapshot_answer_unusable(tmp):
    """[SPEC-NSH-092]: a's snapshot answer is not of the shape asked for. a is missing,
    its half-made folder gone, and every other node's part is taken; the run merges.
    And a run whose snapshot ended part way is refused by name, not read."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False)})
    fleet.broken_snapshot.add("a")
    with faked(fleet) as out:
        rc = ss.snapshot(plan)
        run = ss.newest_run(plan)
        check(rc == 3, "the snapshot ends, with a node missing")
        missing = ss.read_json(os.path.join(run, "missing.json"))
        check("KeyError" in missing.get("a", ""), f"a is missing, and why is said: {missing}")
        names = [n["name"] for n in ss.read_json(os.path.join(run, "manifest.json"))["nodes"]]
        check(names == ["desktop", "b"], f"b's part taken, and nothing of a's: {names}")
        check(not os.path.exists(os.path.join(run, "a")) and "a" not in ss.read_json(os.path.join(run, "baselines.json")),
              "a's half-made folder is gone")
        check(ss.merge(plan, run) == 0, "the run merges without it")
    part_way = os.path.join(plan["runs"], "20261009T1200Z")
    os.makedirs(part_way)
    why = refused(lambda: ss.merge(plan, part_way))
    check(bool(why) and "did not finish" in why, f"a run whose snapshot ended part way is refused by name: {why}")


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
    c.execute("INSERT INTO passages VALUES (2,1,'album',0,500,'u-A-album-0-500')")   # b ingested something on its own
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


def give_uids(db, index=False):
    """Every passage its uid from its span, as Stage A's window gave them [IMPL-UID-052];
    with `index`, the unique index the hub's tool makes beside them."""
    import assign_passage_uids as apu
    c = sqlite3.connect(db)
    c.execute("UPDATE passages SET passage_uid = NULL")
    p = apu.plan(c)
    have = {r[1] for r in c.execute("PRAGMA table_info(passages)")}
    for col, decl in apu.COLUMNS:
        if col not in have:
            c.execute(f"ALTER TABLE passages ADD COLUMN {col} {decl}")
    c.executemany("UPDATE passages SET passage_uid = ?, birth_md5 = ?, birth_kind = ?, birth_start_ms = ?, "
                  "birth_end_ms = ?, birth_salt = 0 WHERE passage_id = ?",
                  [(uid, m, k, s, e, pid) for pid, uid, m, k, s, e in p])
    if index:
        c.execute(apu.SCHEMA[0])
    c.commit()
    c.close()


def catalogue_scenario(tmp, b_current, artists=0, uids=None):
    """a holds the hub's new music under the hub's ids, b under its own -- the case found on every player on
    2026-10-05. a's player is old; b's is current if `b_current`. Every catalogue carries its passages'
    uids; with `uids`, b reports its indexes too, and the catalogues carry the uids the hub's tool gives:
    "all" every one, "hub" the hub's alone, the copies last sent none."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False), "c": (same, False)})
    fleet.natural.discard("a")
    fleet.uids.discard("a")
    if not b_current:
        fleet.natural.discard("b")
        fleet.uids.discard("b")
    if uids:
        fleet.indexes.add("b")
    new = lambda fid, pid: (f"INSERT INTO files VALUES ({fid},'C','/x/c.mp3',100,1,1)",
                            f"INSERT INTO passages VALUES ({pid},{fid},'radio',0,500,'u-C-radio-0-500')")
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
    if uids:
        give_uids(hub["library"], index=True)
        for n in ("a", "b"):
            give_uids(fleet.nodes[n]["library"])
            if uids == "all":
                give_uids(os.path.join(tmp, "sent", f"{n}.library.db"))
            else:
                c = sqlite3.connect(os.path.join(tmp, "sent", f"{n}.library.db"))
                c.execute("UPDATE passages SET passage_uid = NULL")
                c.commit()
                c.close()
    with faked(fleet) as out:
        # c has a file the hub lacks, so its own copy cannot be made: leave it out of this merge.
        ss.snapshot(plan, {"a", "b"})
        run = ss.newest_run(plan)
        base = json.load(open(os.path.join(run, "baselines.json")))
        ss.merge(plan, run)
        ss.patch(plan, run)
    fleet.said = out.getvalue()
    return fleet, plan, hub, run, base


def test_catalogue_states(tmp):
    """[SPEC-STAR-940], [SPEC-NKP-075]: what a node's catalogue summary says about it, and what each kind
    of player is sent for it."""
    fleet, plan, hub, run, base = catalogue_scenario(tmp, b_current=True)
    check(base["a"]["catalogue"]["state"] == "holds-hub-rows" and base["a"]["catalogue_in_step"],
          f"a holds the hub's new rows under the hub's ids: patchable, the rehearsal decides: {base['a']['catalogue']}")
    # Until Stage D, b's own numbering made it a state of its own ("other-ids"). By uid it holds
    # the hub's rows like a, and only the player decides what it is sent [IMPL-UID-080].
    check(base["b"]["catalogue"] == {"state": "holds-hub-rows"} and base["b"]["catalogue_in_step"],
          f"b holds the same music under its own ids, and by uid that is in step: {base['b']['catalogue']}")
    check(ss.catalogue_words(base["b"]["catalogue"]) == "holds the hub's new rows already", "and says so in words")
    pb = json.load(open(os.path.join(run, "patches", "b", "catalogue.values.json")))
    check(not os.path.exists(os.path.join(run, "patches", "a", "catalogue.values.json")),
          "an old player is sent no catalogue patch: until Stage D, the strict patch by id")
    check({e["name"] for e in pb["tables"] if "natural" in e} == {"files", "passages"},
          f"a current one is sent the tables that hold ids by natural key: {[e['name'] for e in pb['tables']]}")
    fe = next(e for e in pb["tables"] if e["name"] == "files")
    check("file_id" not in fe["columns"] and fe["rows"][0]["key"] == ["C"] and "also" in fe["rows"][0],
          f"carrying no id, the new file's machine-scope values as `also`: {fe}")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("by natural key, passages by uid" in summary, f"the summary says so: {summary}")
    check("WARNING -- OLD PLAYER: a" in summary and "OLD PLAYER: b" not in summary, "and warns of a alone, which is old")
    check("older than the patch that names a passage by its uid" in summary, f"and says why a is sent none: {summary}")
    # And the direct call, for the case the merge could not take.
    ch = ss.catalogue_state(os.path.join(tmp, "sent", "c.library.db"), os.path.join(run, "a", "summary.db"),
                            os.path.join(run, "desktop", "library.db"))
    check(ch["state"] == "holds-hub-rows", f"the classifier on its own: {ch}")
    cn = os.path.join(tmp, "c-summary.db")
    shutil.copyfile(fleet.nodes["c"]["library"], cn)
    ch = ss.catalogue_state(os.path.join(tmp, "sent", "c.library.db"), cn, os.path.join(run, "desktop", "library.db"))
    check(ch["state"] == "changed" and ch["not_the_hubs"] == 1, f"a file the hub lacks is a real change: {ch}")
    check(ss.catalogue_state(None, cn, cn) == {"state": "no-copy"}, "and with nothing last sent it says so")


def apu_schema_index():
    import assign_passage_uids as apu
    return apu.SCHEMA[0]


def test_catalogue_by_uid(tmp):
    """[IMPL-UID-052] step 6: a player that reports passage_uids, both catalogues carrying them, is sent its
    passages by uid; against a copy without them it is sent none, and told why -- never by span, which
    went with Stage D [IMPL-UID-080]."""
    os.makedirs(os.path.join(tmp, "all"))
    fleet, plan, hub, run, base = catalogue_scenario(os.path.join(tmp, "all"), b_current=True, uids="all")
    check(base["b"]["passage_uids"] == 1 and base["a"]["passage_uids"] == 0, f"each node's report is kept: {base['a']}")
    pb = json.load(open(os.path.join(run, "patches", "b", "catalogue.values.json")))
    pe = next(e for e in pb["tables"] if e["name"] == "passages")
    c = sqlite3.connect(hub["library"])
    uid = c.execute("SELECT passage_uid FROM passages WHERE passage_id = 20").fetchone()[0]
    c.close()
    check(pe["key"] == ["passage_uid"] and [r["key"] for r in pe["rows"]] == [[uid]],
          f"b's new passage is named by the uid the hub gave it: {pe}")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("by natural key, passages by uid" in summary, f"and the summary says so: {summary}")
    # [IMPL-UID-056]: b reports its catalogue's indexes, and lacks the hub's unique one on uids.
    ix = [e for e in pb["tables"] if "add_indexes" in e]
    check([(e["name"], e["add_indexes"]) for e in ix] == [("passages", [apu_schema_index()])],
          f"b is sent the index it reports lacking: {ix}")
    check("makes the index(es) `passages.passages_uid` the node lacks" in summary, f"and told so: {summary}")
    check(not os.path.exists(os.path.join(run, "patches", "a", "catalogue.values.json")),
          "a, an old player, is sent no catalogue, and so no index")
    os.makedirs(os.path.join(tmp, "hub"))
    fleet, plan, hub, run, base = catalogue_scenario(os.path.join(tmp, "hub"), b_current=True, uids="hub")
    check(not os.path.exists(os.path.join(run, "patches", "b", "catalogue.values.json")),
          "against a copy last sent without uids, b is sent no catalogue")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("the copy last sent has a passage without its uid" in summary and "none sent" in summary,
          f"and the summary says why, not just that none went: {summary}")
    check("the copy last sent has a passage without its uid" in fleet.said, f"and the run's own output says it too: {fleet.said}")


def test_catalogue_old_player(tmp):
    """An old player holding the music under other ids is sent no catalogue patch, and is told so loudly."""
    fleet, plan, hub, run, base = catalogue_scenario(tmp, b_current=False)
    check(base["b"]["natural_keys"] == 0, f"{base['b']}")
    check(not os.path.exists(os.path.join(run, "patches", "b", "catalogue.values.json")), "no patch for b")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("older than the patch that names a passage by its uid" in summary and "WARNING -- OLD PLAYERS: a, b" in summary,
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
    fleet.commit_again.add("b")             # a current build, which keeps its commit's answer [SPEC-NSH-032]
    with faked(fleet) as out:
        stages(plan, "b")
        staged_after_rehearsal = sum(1 for e in fleet.log if e[0] == "b" and e[1] == "stage")
        check(staged_after_rehearsal == man["of"], f"a rehearsal stages every part: {staged_after_rehearsal}")
        check(stages(plan, "b", commit=True) == 0, "the commit succeeds")
        total = sum(1 for e in fleet.log if e[0] == "b" and e[1] == "stage")
        check(total == man["of"], f"and sends none of them again: {total} stage requests in all")
        # The same run's commit asked again, as after a lost answer: the parts it consumed are
        # not staged again, and b answers from its own record.
        before = len(out.getvalue())
        check(stages(plan, "b", commit=True) == 0, "the same run's commit, asked again, is answered")
        said = out.getvalue()[before:]
        check(sum(1 for e in fleet.log if e[0] == "b" and e[1] == "stage") == total
              and "committed there already, its parts consumed: nothing staged" in said,
              f"and nothing is staged for it: {said}")
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


def test_read_only_catalogue(tmp):
    """[SPEC-STAR-104]: a catalogue on a read-only partition is said at the snapshot. A player that
    can open it itself is told so and sent its patch; one that cannot is warned about loudly and
    sent no catalogue patch, instead of being staged 14 parts and refusing at the first."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False), "c": (same, False)})
    fleet.natural.update({"a", "b", "c"})
    fleet.readonly.update({"a", "b"})          # a and b are on read-only partitions, c is not
    fleet.window.update({"b", "c"})            # b's build can open its own; a's cannot
    with faked(fleet) as out:
        ss.snapshot(plan)
        text = out.getvalue()
        run = ss.newest_run(plan)
    check("!!!  OLD PLAYER: a" in text and "!!!    a: its catalogue is on a read-only partition" in text,
          f"a is named loudly with the reason: {text}")
    check("b: its catalogue is on a read-only partition: the player opens it" in text and "OLD PLAYER: b" not in text
          and "OLD PLAYER: c" not in text, f"b is told the window is the player's, and not warned: {text}")
    base = json.load(open(os.path.join(run, "baselines.json")))
    check(base["a"]["catalogue_blocked"] and not base["a"]["catalogue_in_step"], f"a is in no catalogue step: {base['a']}")
    check(base["b"]["catalogue_blocked"] is None and base["c"]["catalogue_blocked"] is None, "b and c are not blocked")
    check(sorted(json.load(open(os.path.join(run, "old_players.json")))) == ["a"], "only a is warned about")


def test_old_players(tmp):
    """[SPEC-NKP-075]: an old player is said loudly -- at the snapshot, in the summary, and again
    before a rehearsal or a commit -- and a current one is not."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False)})
    fleet.natural.discard("a")                 # b is current; a is not
    fleet.uids.discard("a")
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


LINKS = """
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL);
CREATE TABLE release_recordings (release_mbid TEXT NOT NULL REFERENCES releases(mbid) ON DELETE CASCADE,
    mbid TEXT NOT NULL REFERENCES recordings(mbid) ON DELETE CASCADE, position INTEGER, source TEXT NOT NULL,
    PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL REFERENCES passages(passage_id) ON DELETE CASCADE,
    mbid TEXT NOT NULL REFERENCES recordings(mbid), weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
    PRIMARY KEY (passage_id, mbid)) WITHOUT ROWID;
INSERT INTO recordings VALUES ('rec-ok', 'Pinky', 233000, 'musicbrainz');
INSERT INTO releases VALUES ('rel-c', 'Caribou', 'musicbrainz');
INSERT INTO release_recordings VALUES ('rel-c', 'rec-ok', 2, 'musicbrainz');
INSERT INTO release_recordings VALUES ('rel-c', 'rec-ghost', 9, 'musicbrainz');
"""


def links_fleet(tmp, b_holds):
    """[SPEC070]: the hub, a and b, and every copy last sent, hold Caribou's tracklist with a track 9
    naming a recording their catalogues do not hold. With `b_holds`, b's own catalogue holds it, and a
    file of b's own credits it."""
    fleet = Fleet(tmp)
    same = [("X", 1, "2026-09-01 00:00:00")]
    plan, hub = build(tmp, fleet, same, {"a": (same, False), "b": (same, False)})
    fleet.rows_cap = {"a": 2, "b": 2}
    music = os.path.join(tmp, "hubmusic")
    hub_dbs = [hub["library"], os.path.join(tmp, "sent", "hub.library.db")]
    node_dbs = [p for n in ("a", "b") for p in (fleet.nodes[n]["library"], os.path.join(tmp, "sent", f"{n}.library.db"))]
    for db in hub_dbs + node_dbs:
        c = sqlite3.connect(db)
        c.executescript(LINKS)
        if db in hub_dbs:             # the hub's music under a root of two folders, as a library's is
            c.execute("UPDATE files SET path = ? WHERE file_id = 1", (os.path.join(music, "Elton John", "a.mp3"),))
            c.execute("INSERT INTO files VALUES (2, 'F', ?, 100, 1, 1)", (os.path.join(music, "Foghat", "f.mp3"),))
        c.commit()
        c.close()
    if b_holds:
        c = sqlite3.connect(fleet.nodes["b"]["library"])
        c.execute("INSERT INTO recordings VALUES ('rec-ghost', 'Ticking', 335000, 'musicbrainz')")
        c.execute("INSERT INTO files VALUES (5, 'G', '/b/Elton John/09 Ticking.mp3', 100, 1, 1)")
        c.execute("INSERT INTO passages VALUES (50, 5, 'album', 0, 335000, 'u-G-album-0-335000')")
        c.execute("INSERT INTO passage_recordings VALUES (50, 'rec-ghost', 1.0, 'musicbrainz')")
        c.commit()
        c.close()
    return fleet, plan, hub, music


def rows_of(db, sql):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_links_removed(tmp):
    """[SPEC070]: found at the merge, said, not a stop; removed by a decision, carried by the commit."""
    fleet, plan, hub, _ = links_fleet(tmp, b_holds=False)
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)
    said = out.getvalue()
    check("1 catalogue row(s) name something it does not hold" in said and "a: holds none of the 1 key(s)" in said,
          f"the snapshot says what it found and what each node said: {said}")
    check([(n, w.get("recordings"), w.get("by_recording")) for n, w in fleet.asked]
          == [("a", ["rec-ghost"], True), ("b", ["rec-ghost"], True)], f"each node asked by recording: {fleet.asked}")
    res = ss.links_of(run)
    f = res["findings"][0] if res["findings"] else {}
    check(len(res["findings"]) == 1 and f["effect"] == "undecided" and f["offers"] == [],
          f"one finding, undecided, nobody holding it: {res['findings']}")
    check(res["carried_to"] == ["a", "b"], f"this run's commit would carry a correction to a and b: {res['carried_to']}")
    check(not any("links" in p for p in ss.gate_problems(run, ["desktop", "a", "b"])), "an undecided finding stops nothing")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("**Links to nothing [SPEC070]:** 1 finding(s), 1 row(s) -- 1 undecided" in summary, summary)
    ss.decide_link(run, f["id"], "remove")
    check(len(ss.links_stale(run)) == 1 and any("links to nothing" in p for p in ss.gate_problems(run, ["desktop"])),
          "a removal decided after the merge leaves it stale, and the gate says so")
    with faked(fleet):
        ss.patch(plan, run)
        stages(plan)
        stages(plan, commit=True)
    check(ss.links_stale(run) == [] and ss.links_of(run)["findings"][0]["effect"] == "remove", "merged again, removed")
    hp = json.load(open(os.path.join(run, "patches", "desktop", "library.patch.json")))
    check(ss.table_rows(hp) == {"release_recordings": 1}, f"the hub's own patch removes it: {ss.table_rows(hp)}")
    pa = json.load(open(os.path.join(run, "patches", "a", "catalogue.values.json")))
    rr = [r for e in pa["tables"] if e["name"] == "release_recordings" for r in e["rows"]]
    check(len(rr) == 1 and rr[0]["now"] is None, f"and a's removes it: {rr}")
    check(rows_of(hub["library"], "SELECT mbid FROM release_recordings ORDER BY mbid") == [("rec-ok",)],
          "on the hub, after the commit, only the whole row is left")
    check(ci_load(run)[f["id"]].get("committed") == ss.stamp_of(run), "and the decision says which commit carried it")


def ci_load(run):
    return ss.ci.load_decisions(ss.links_path(run))


def test_links_repaired_with_files(tmp):
    """[SPEC-LNK-042, SPEC-LNK-052]: a node holding what the hub lacks holds a removal back; its repair, with
    its file, brings the audio to the hub before the hub is patched."""
    fleet, plan, hub, music = links_fleet(tmp, b_holds=True)
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)
    check("b: HOLDS 1 of the 1 key(s)" in out.getvalue() and "1 of its file(s) name them" in out.getvalue(),
          f"the snapshot says b holds it, and its file: {out.getvalue()}")
    f = ss.links_of(run)["findings"][0]
    check([(o["node"], o["keys"], [x["audio_md5"] for x in o["files"]]) for o in f["offers"]]
          == [("b", ["rec-ghost"], ["G"])], f"b offers the recording and its file: {f['offers']}")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check("**b holds 1**" in summary, f"the summary names it: {summary}")
    # A removal decided without b's offer in front of the person -- in an earlier run -- is held.
    ss.ci.decide(ss.links_path(run), f, "remove", [])
    with faked(fleet):
        ss.patch(plan, run)
    f = ss.links_of(run)["findings"][0]
    check(f["effect"] == "held" and "b holds" in f["why"], f"held, and why: {f['effect']} {f.get('why')}")
    check(len(rows_of(os.path.join(run, "merge", "library.db"), "SELECT * FROM release_recordings")) == 2,
          "and nothing removed")
    ss.decide_link(run, f["id"], "repair-files:b")
    calls = []

    def fake_run(argv, stdout=None, **kw):
        calls.append(argv)
        stdout.write(b"x" * 100)
        return type("R", (), {"returncode": 0, "stderr": b""})()
    real = ss.subprocess.run
    ss.subprocess.run = fake_run
    try:
        with faked(fleet):
            ss.patch(plan, run)
            stages(plan)
            stages(plan, commit=True)
    finally:
        ss.subprocess.run = real
    f = ss.links_of(run)["findings"][0]
    dest = os.path.join(music, "Elton John", "09 Ticking.mp3")
    check(f["effect"] == "repair-files" and [os.path.normpath(x["dest"]) for x in f.get("fetch", [])] == [os.path.normpath(dest)],
          f"repaired from b, its file placed as under b's root: {f['effect']} {f.get('why')} {f.get('fetch')}")
    check(len(calls) == 1 and calls[0][0] == "ssh" and "u@b" in calls[0] and calls[0][-1] == "cat -- '/b/Elton John/09 Ticking.mp3'",
          f"copied from b over ssh: {calls}")
    check(os.path.getsize(dest) == 100, "the audio is on the hub")
    check(rows_of(hub["library"], "SELECT title FROM recordings WHERE mbid = 'rec-ghost'") == [("Ticking",)]
          and rows_of(hub["library"], "SELECT path FROM files WHERE audio_md5 = 'G'") == [(dest,)],
          "the hub catalogues the recording, and the file where it put it")
    lib = sqlite3.connect(hub["library"])
    check(ss.ci.find(lib) == [], "and the hub's catalogue has no link to nothing left")
    lib.close()


EMPTY = """
CREATE TABLE cover_art (release_mbid TEXT PRIMARY KEY, front BLOB, back BLOB, source TEXT NOT NULL);
INSERT INTO releases VALUES ('rel-org', 'Org', 'musicbrainz');
INSERT INTO cover_art VALUES ('rel-org', NULL, NULL, 'caa');
"""


def test_empty_release_removed(tmp):
    """[SPEC-LNK-070..074]: a release no tracklist names is found, each node asked for a tracklist of its own;
    removed by a decision, its cover goes with it, on the hub and on the nodes."""
    fleet, plan, hub, _ = links_fleet(tmp, b_holds=False)
    fleet.rows_cap = {"a": 3, "b": 2}            # b's build cannot say whether it holds a tracklist
    for db in [hub["library"], os.path.join(tmp, "sent", "hub.library.db")] + \
              [p for n in ("a", "b") for p in (fleet.nodes[n]["library"], os.path.join(tmp, "sent", f"{n}.library.db"))]:
        c = sqlite3.connect(db)
        c.executescript(EMPTY)
        c.commit()
        c.close()
    with faked(fleet) as out:
        ss.snapshot(plan)
        run = ss.newest_run(plan)
        ss.merge(plan, run)
        ss.patch(plan, run)
    said = out.getvalue()
    check("1 release(s) with no tracklist" in said, f"the snapshot says what it found: {said}")
    check("b: holds none" in said and "whether it holds a tracklist for the 1 release(s) with none, this build cannot say" in said,
          f"and that b's build cannot say: {said}")
    check(all(w.get("tracklists") and "rel-org" in w.get("releases", []) for _, w in fleet.asked),
          f"each node asked for the release's tracklist: {fleet.asked}")
    e = next((f for f in ss.links_of(run)["findings"] if f.get("empty")), None)
    check(e and e["removable"] and e["effect"] == "undecided" and e["cannot_say"] == ["b"],
          f"one empty release, removable, undecided, b unable to say: {e}")
    ss.decide_link(run, e["id"], "remove")
    with faked(fleet):
        ss.patch(plan, run)
        stages(plan)
        stages(plan, commit=True)
    e = next(f for f in ss.links_of(run)["findings"] if f.get("empty"))
    check(e["effect"] == "remove" and e["applied"] == {"cover_art": -1, "releases": -1}, f"removed with its cover: {e}")
    check(rows_of(hub["library"], "SELECT mbid FROM releases ORDER BY mbid") == [("rel-c",)]
          and rows_of(hub["library"], "SELECT * FROM cover_art") == [], "on the hub, the release and its cover are gone")
    for n in ("a", "b"):
        p = json.load(open(os.path.join(run, "patches", n, "catalogue.values.json")))
        got = [(e["name"], [r["now"] for r in e["rows"]]) for e in p["tables"]]
        check(got == [("cover_art", [None]), ("releases", [None])], f"{n}'s patch removes the cover, then the release: {got}")


def test_lost_answer():
    """A node whose answer to the members' query is lost once is asked again,
    not marked MISSING for the run -- lp3-wifi, 2026-10-15."""
    print("a lost discovery answer is asked again, not taken as a node away")
    asked = []

    def flaky(mdir):
        asked.append(1)
        nodes = ["a"] if len(asked) == 1 else ["a", "b"]
        return {f"fp-{n}": {"fingerprint": f"fp-{n}", "address": n} for n in nodes}
    plan = {"mesh": "m", "nodes": {"a": {"member": "fp-a"}, "b": {"member": "fp-b"}}}
    real = (ss.meshmod.sync_players, ss.mesh_of)
    ss.meshmod.sync_players, ss.mesh_of = flaky, (lambda p: "m")
    try:
        got = ss.players_up(plan, ["a", "b"], {})
        check(got["b"] is not None and len(asked) == 2, f"b found on the second asking: {got} {len(asked)}")
        asked.clear()
        ss.meshmod.sync_players = lambda mdir: (asked.append(1) or {"fp-a": {"fingerprint": "fp-a", "address": "a"}})
        got = ss.players_up(plan, ["a", "b"], {})
        check(got["b"] is None and len(asked) == 3, f"one that never answers: asked three times, then away: {len(asked)}")
    finally:
        ss.meshmod.sync_players, ss.mesh_of = real


def test_hub_writers():
    """[SPEC-STAR-127]: a shell that launched the console is not a second
    console -- the process table found on the desktop, 2026-10-14."""
    print("hub_writers: what can write the hub's pair, and what only names it")
    shell = (16752, 6136, "bash.exe", 'bash.exe -c "... python tools/console.py data/library.db > log 2>&1 & ..."')
    console = (5652, 16752, "python.exe", r"C:\Python314\python.exe tools/console.py data/library.db")
    player = (27100, 19772, "lempi.exe", r'"C:\Lempi\lempi.exe" --listener l.db --library library.db')
    nohup = (19772, 1, "nohup.exe", '"nohup.exe" "/c/Lempi/lempi.exe" --listener l.db')
    me = (900, 5652, "python.exe", r"C:\Python314\python.exe tools/star_sync.py commit --console-pid 5652")
    procs = [shell, console, player, nohup, me]
    check(ss.hub_writers(procs, 900, excuse=5652) == [],
          f"from the console: its launching shell, itself and its player excused -- {ss.hub_writers(procs, 900, 5652)}")
    check(ss.hub_writers(procs, 900) == ["5652", "27100"],
          f"from a terminal: the console and the player count, the shells do not -- {ss.hub_writers(procs, 900)}")
    other = (7000, 1, "pythonw.exe", r"pythonw C:\Lempi\tools\console.py other.db")
    check(ss.hub_writers(procs + [other], 900, excuse=5652) == ["7000"],
          "a second console still refuses, excuse or not")
    check(ss.hub_writers([(8000, 1, "python.exe", "python tools/test_console_import.py")], 900) == [],
          "a test of the console is not the console")


def main() -> int:
    test_hub_writers()
    test_lost_answer()
    test_split_catalogue()
    for test in (test_quiet, test_conflict, test_hub_edited_during_sync, test_retired_files_moved, test_approve_all,
                 test_a_commit_answer_lost, test_a_snapshot_answer_unusable, test_the_hubs_commit_is_verified_by_its_counts,
                 test_the_copy_sent_is_dated_by_its_snapshot,
                 test_a_big_step_is_held, test_on_demand, test_catalogue_moved,
                 test_catalogue_states, test_catalogue_by_uid, test_catalogue_old_player,test_catalogue_in_parts, test_a_patch_that_cannot_be_cut, test_selection, test_read_only_catalogue,
                 test_old_players, test_links_removed, test_links_repaired_with_files, test_empty_release_removed):
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
