#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A catalogue row that names a row the catalogue does not hold [SPEC070].

Found at the merge of a sync, decided by a person, and carried by the commit
to the hub and every node taking part [SPEC-LNK-020]. Nothing here talks to a
node or writes the live library: `star_sync.py` asks the nodes and passes their
answers in; `correct` is given the merge's own copy of the hub's catalogue.

    find(conn)                      every finding in a catalogue [SPEC-LNK-022]
    want_of(findings)               what to ask each node about [SPEC-LNK-040]
    offers_of(findings, answers)    what each node holds of it
    effect(finding, decisions)      what a decision does in this run [SPEC-LNK-042]
    correct(catalogue, cfg)         the decided ones, applied; `integrity.json`

A finding's `effect` is one of `undecided` and `leave` (nothing is written),
`remove`, `repair` and `repair-files`, or `held` with the reason.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import posixpath
import sqlite3

# [SPEC-LNK-030]: a row that is a link and nothing more. Removing one loses only
# the link; a passage's credit pointing at nothing is a fault to repair instead.
REMOVABLE = {"release_recordings", "recording_artists", "recording_relations"}

# The parent tables a node can be asked about, and the `rows` scope that asks
# [SPEC-STAR-945].
SCOPE = {"recordings": "recordings", "releases": "releases", "artists": "artists"}

DECISIONS = ("leave", "remove", "repair:", "repair-files:")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _tables(c):
    return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                    "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _cols(c, t):
    return [r[1] for r in c.execute(f"PRAGMA table_info({t})")]


def _pk(c, t):
    return [r[1] for r in sorted(c.execute(f"PRAGMA table_info({t})"), key=lambda r: r[5]) if r[5]]


def _has(c, t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (t,)).fetchone() is not None


def finding_id(table, column, parent, owner, missing) -> str:
    """[SPEC-LNK-022]: the same situation keeps its id; a changed one gets a new one."""
    blob = json.dumps([table, column, parent, sorted(owner.items()), sorted(missing)], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ------------------------------------------------------------------ finding --
def find(conn) -> list[dict]:
    """Every row whose foreign key names nothing, grouped by what owns it: the
    row's key without the column that dangles. Multi-column keys are skipped:
    the catalogue has none."""
    out = []
    for t in _tables(conn):
        fks = {}
        for r in conn.execute(f"PRAGMA foreign_key_list({t})"):
            fks.setdefault(r[0], []).append(r)
        if not fks:
            continue
        cols, pk = _cols(conn, t), _pk(conn, t)
        for group in fks.values():
            if len(group) != 1:
                continue
            _id, _seq, parent, col, pcol = group[0][:5]
            if not _has(conn, parent):
                continue
            pcol = pcol or (_pk(conn, parent) or ["rowid"])[0]
            rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM {t} c WHERE c.{col} IS NOT NULL AND NOT EXISTS "
                f"(SELECT 1 FROM {parent} p WHERE p.{pcol} = c.{col})").fetchall()
            if not rows:
                continue
            owner_cols = [k for k in pk if k != col]
            groups = {}
            for r in rows:
                d = dict(zip(cols, r))
                groups.setdefault(tuple(d[k] for k in owner_cols), []).append(d)
            for key, ds in sorted(groups.items(), key=lambda kv: [str(x) for x in kv[0]]):
                owner = dict(zip(owner_cols, key))
                missing = sorted({d[col] for d in ds})
                f = {"id": finding_id(t, col, parent, owner, missing), "table": t, "column": col,
                     "parent": parent, "parent_column": pcol, "key": pk or cols, "owner": owner,
                     "missing": missing, "rows": ds, "removable": t in REMOVABLE}
                f["about"] = describe(conn, f)
                out.append(f)
    return out


def _one(conn, sql, *args):
    try:
        r = conn.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None
    return r


def describe(conn, f) -> dict:
    """What a person needs to judge a finding: what owns it, in words, and what
    removing its rows would leave."""
    t, owner = f["table"], f["owner"]
    if t == "release_recordings" and "release_mbid" in owner:
        rel = owner["release_mbid"]
        r = _one(conn, "SELECT title, artist_credit, release_date, track_count FROM releases WHERE mbid = ?", rel)
        listed = _one(conn, "SELECT COUNT(*) FROM release_recordings WHERE release_mbid = ?", rel)[0]
        files = (_one(conn, "SELECT COUNT(*) FROM file_releases WHERE release_mbid = ?", rel) or [0])[0] \
            if _has(conn, "file_releases") else 0
        chosen = _one(conn, "SELECT COUNT(*) FROM release_recordings WHERE release_mbid = ? AND chosen = 1", rel)
        tracks = []
        for d in sorted(f["rows"], key=lambda d: (d.get("disc") or 0, d.get("position") or 0)):
            ms = d.get("track_length_ms")
            tracks.append({"disc": d.get("disc"), "position": d.get("position"), "mbid": d["mbid"],
                           "length": f"{ms // 60000}:{ms // 1000 % 60:02d}" if ms else None})
        return {"kind": "release", "mbid": rel, "title": r[0] if r else None, "credit": r[1] if r else None,
                "date": r[2] if r else None, "track_count": r[3] if r else None, "listed": listed,
                "dangling": len(f["rows"]), "left_after": listed - len(f["rows"]), "files_from_it": files,
                "chosen": chosen[0] if chosen else 0, "tracks": tracks}
    if "mbid" in owner and _has(conn, "recordings"):
        r = _one(conn, "SELECT title FROM recordings WHERE mbid = ?", owner["mbid"])
        return {"kind": "recording", "mbid": owner["mbid"], "title": r[0] if r else None}
    return {"kind": t, "owner": owner}


def words(f) -> str:
    """One line for a finding, as the summary and the CLI say it."""
    a = f["about"]
    n, m = len(f["rows"]), len(f["missing"])
    what = f"{n} `{f['table']}` row(s) naming {m} {f['parent'][:-1] if f['parent'].endswith('s') else f['parent']}(s) " \
           f"not in `{f['parent']}`"
    if a.get("kind") == "release":
        name = f"*{a['title'] or a['mbid']}*" + (f", {a['credit']}" if a["credit"] else "") + (f" ({a['date']})" if a["date"] else "")
        left = "nothing of its tracklist would be left" if a["left_after"] == 0 else f"{a['left_after']} of its tracklist would be left"
        files = f"; {a['files_from_it']} file(s) taken from it" if a["files_from_it"] else "; no file taken from it"
        return f"{name}: {what}; {left}{files}"
    if a.get("kind") == "recording":
        return f"recording {a['title'] or a['mbid']}: {what}"
    return f"`{f['table']}` {f['owner']}: {what}"


# ---------------------------------------------------------- asking the nodes --
def want_of(findings) -> dict:
    """The keys the hub is missing, by the `rows` scope that asks for them."""
    want = {s: set() for s in SCOPE.values()}
    for f in findings:
        if f["parent"] in SCOPE:
            want[SCOPE[f["parent"]]] |= set(f["missing"])
    return {k: sorted(v) for k, v in want.items() if v}


def _answer_rows(answer, table):
    for t in (answer or {}).get("tables", []):
        if t["name"] == table:
            return [dict(zip(t["columns"], r)) for r in t["rows"]]
    return []


def offers_of(findings, answers) -> dict:
    """{finding id: [offer]} -- per node, the missing keys it holds and, where it
    could say, its files that name them [SPEC-LNK-040, SPEC-LNK-050].

    `answers` is {node: {"answer": its `rows` answer, "by_recording": whether it
    listed files by recording, "fetchable": whether its audio can be copied,
    "root": its music root}}."""
    out = {}
    for name, a in sorted(answers.items()):
        ans = a.get("answer") or {}
        files = {d["file_id"]: d for d in _answer_rows(ans, "files")}
        passages = {d["passage_id"]: d for d in _answer_rows(ans, "passages")}
        credits = _answer_rows(ans, "passage_recordings")
        for f in findings:
            if f["parent"] not in SCOPE:
                continue
            pcol = f["parent_column"]
            held = sorted({d[pcol] for d in _answer_rows(ans, f["parent"])} & set(f["missing"]))
            if not held:
                continue
            fl = []
            if f["parent"] == "recordings" and a.get("by_recording"):
                fids = {passages[c["passage_id"]]["file_id"] for c in credits
                        if c["mbid"] in held and c["passage_id"] in passages}
                fl = [{"audio_md5": files[i]["audio_md5"], "path": files[i].get("path"),
                       "size_bytes": files[i].get("size_bytes"), "sha256": files[i].get("sha256")}
                      for i in sorted(fids) if i in files]
            out.setdefault(f["id"], []).append({
                "node": name, "keys": held, "files": fl,
                "files_known": bool(a.get("by_recording")) and f["parent"] == "recordings",
                "fetchable": bool(a.get("fetchable")) and bool(a.get("root"))})
    return out


# ---------------------------------------------------------------- deciding --
def valid(value: str) -> bool:
    return value in ("leave", "remove") or any(value.startswith(p) and value[len(p):] for p in DECISIONS[2:])


def effect(f, decisions: dict, offers: list) -> dict:
    """What `decisions` does to finding `f` in this run [SPEC-LNK-042]:
    {"effect", "node", "why"}."""
    d = decisions.get(f["id"])
    if not d:
        return {"effect": "undecided", "node": None, "why": None}
    v = d["decision"]
    if v == "leave":
        return {"effect": "leave", "node": None, "why": None}
    by = {o["node"]: o for o in offers}
    if v == "remove":
        if not f["removable"]:
            return {"effect": "held", "node": None, "why": f"a `{f['table']}` row is not a link only: repair it or leave it"}
        new = sorted(set(by) - set(d.get("offers_seen") or []))
        if new:
            who = ", ".join(new)
            one = len(new) == 1
            return {"effect": "held", "node": None,
                    "why": f"{who} hold{'s' if one else ''} what is missing, so the removal would take a working link "
                           f"from {'it' if one else 'them'}: decide again"}
        return {"effect": "remove", "node": None, "why": None}
    kind, node = v.split(":", 1)
    o = by.get(node)
    if o is None:
        return {"effect": "held", "node": node, "why": f"{node} is not in this run, or no longer holds what is missing"}
    if kind == "repair-files":
        if not o["files_known"]:
            return {"effect": "held", "node": node, "why": f"{node}'s build cannot say which of its files name them"}
        if not o["fetchable"]:
            return {"effect": "held", "node": node, "why": f"{node}'s files cannot be copied from here (no ssh host, or no music root)"}
    return {"effect": kind, "node": node, "why": None}


def load_decisions(path) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("decisions", {})


def save_decisions(path, decisions):
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"decisions": decisions}, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def decide(path, f, value, offers, by="person"):
    """One decision, kept beside the runs [SPEC-LNK-030]. `offers_seen` is what
    the person had in front of them: a node found holding the keys later holds
    a removal back [SPEC-LNK-042]."""
    if not valid(value):
        raise SystemExit(f"a decision is leave, remove, repair:NODE or repair-files:NODE: {value!r} is none of them")
    if value == "remove" and not f["removable"]:
        raise SystemExit(f"`{f['table']}` rows are not links only, and are repaired or left, never removed")
    if ":" in value and value.split(":", 1)[1] not in {o["node"] for o in offers}:
        raise SystemExit(f"{value.split(':', 1)[1]} holds nothing of this finding: offered by "
                         f"{[o['node'] for o in offers] or 'no node'}")
    ds = load_decisions(path)
    ds[f["id"]] = {"decision": value, "at": now(), "by": by, "what": words(f),
                   "offers_seen": sorted(o["node"] for o in offers)}
    save_decisions(path, ds)
    return ds[f["id"]]


def mark_committed(path, findings, run) -> int:
    """The run whose commit carried each applied decision to the hub, kept on it."""
    ds = load_decisions(path)
    n = 0
    for f in findings:
        if f["effect"] in ("remove", "repair", "repair-files") and f["id"] in ds:
            ds[f["id"]]["committed"] = run
            n += 1
    if n:
        save_decisions(path, ds)
    return n


# ---------------------------------------------------------------- applying --
def _plain(v):
    """A value from a node's answer as SQLite takes it; a blob sent as its hash
    cannot be written, and is None [SPEC-STAR-945]."""
    if isinstance(v, dict):
        return base64.b64decode(v["b64"]) if "b64" in v else None
    return v


def _insert(conn, t, d, replace=None) -> bool:
    """`d` into `t`, over the columns both have, only where no row holds its key."""
    if not _has(conn, t):
        return False
    have = _cols(conn, t)
    row = dict(d, **(replace or {}))
    cols = [c for c in have if c in row]
    vals = [_plain(row[c]) for c in cols]
    cur = conn.execute(f"INSERT OR IGNORE INTO {t} ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    return cur.rowcount > 0


def remove(conn, f) -> int:
    n = 0
    for d in f["rows"]:
        n += conn.execute(f"DELETE FROM {f['table']} WHERE " + " AND ".join(f"{k} = ?" for k in f["key"]),
                          [d[k] for k in f["key"]]).rowcount
    return n


def repair(conn, f, offer, answer) -> dict:
    """[SPEC-LNK-044]: the node's rows for the keys it holds, added where the hub
    has none, parents first; a child only where its parent is then held."""
    keys = set(offer["keys"])
    added = {}

    def add(t, d):
        if _insert(conn, t, d):
            added[t] = added.get(t, 0) + 1

    pcol = f["parent_column"]
    if f["parent"] == "recordings":
        credits = [d for d in _answer_rows(answer, "recording_artists") if d["mbid"] in keys]
        need = {d["artist_mbid"] for d in credits}
        for d in _answer_rows(answer, "artists"):
            if d["mbid"] in need:
                add("artists", d)
    for d in _answer_rows(answer, f["parent"]):
        if d[pcol] in keys:
            add(f["parent"], d)
    if f["parent"] == "recordings":
        for d in credits:
            if conn.execute("SELECT 1 FROM artists WHERE mbid = ?", (d["artist_mbid"],)).fetchone():
                add("recording_artists", d)
        for d in _answer_rows(answer, "flavor"):
            if d.get("subject_kind") == "recording" and d.get("subject_id") in keys:
                add("flavor", d)
    return added


def _music_root(conn) -> str:
    """The one folder every catalogued file lies under: of their folders, so a
    catalogue of one file is not its own root."""
    dirs = [os.path.dirname(r[0]) for r in conn.execute("SELECT path FROM files") if r[0]]
    try:
        return os.path.commonpath(dirs) if dirs else ""
    except ValueError:
        return ""


def place(node_path, node_root, hub_root) -> str | None:
    """[SPEC-LNK-052]: where a node's file goes on the hub -- the same path under
    the hub's music root as it has under the node's."""
    p, r = node_path.replace("\\", "/"), node_root.replace("\\", "/").rstrip("/")
    if not r or not p.startswith(r + "/"):
        return None
    rel = posixpath.relpath(p, r)
    if rel.startswith(".."):
        return None
    return os.path.join(hub_root, *rel.split("/"))


def with_files(conn, offer, answer, node_root, hub_root) -> tuple[dict, list]:
    """The node's files naming the keys, catalogued on the hub from the node's
    own rows under new ids, and what to copy. A file the hub already holds is
    left as it is."""
    added, fetch = {}, []
    nfiles = {d["file_id"]: d for d in _answer_rows(answer, "files")}
    npass = _answer_rows(answer, "passages")
    want = {x["audio_md5"] for x in offer["files"]}
    nxt_f = (conn.execute("SELECT MAX(file_id) FROM files").fetchone()[0] or 0) + 1
    nxt_p = (conn.execute("SELECT MAX(passage_id) FROM passages").fetchone()[0] or 0) + 1
    stamp = now()
    for nid, d in sorted(nfiles.items()):
        md5 = d["audio_md5"]
        if md5 not in want or conn.execute("SELECT 1 FROM files WHERE audio_md5 = ?", (md5,)).fetchone():
            continue
        dest = place(d["path"], node_root, hub_root)
        if dest is None:
            raise ValueError(f"{d['path']} is not under {node_root}, the node's music root")
        fid, nxt_f = nxt_f, nxt_f + 1
        _insert(conn, "files", d, {"file_id": fid, "path": dest, "first_seen": stamp, "last_seen": stamp})
        added["files"] = added.get("files", 0) + 1
        pids = {}
        for p in npass:
            if p["file_id"] == nid:
                pids[p["passage_id"]], nxt_p = nxt_p, nxt_p + 1
                _insert(conn, "passages", p, {"passage_id": pids[p["passage_id"]], "file_id": fid})
                added["passages"] = added.get("passages", 0) + 1
        for t, col, ids in (("file_tags", "file_id", {nid: fid}), ("passage_recordings", "passage_id", pids),
                            ("id_checks", "passage_id", pids)):
            for r in _answer_rows(answer, t):
                if r.get(col) in ids:
                    if t == "passage_recordings" and not conn.execute(
                            "SELECT 1 FROM recordings WHERE mbid = ?", (r["mbid"],)).fetchone():
                        continue
                    if _insert(conn, t, r, {col: ids[r[col]]}):
                        added[t] = added.get(t, 0) + 1
        for r in _answer_rows(answer, "file_releases"):
            if r["audio_md5"] == md5 and _insert(conn, "file_releases", r):
                added["file_releases"] = added.get("file_releases", 0) + 1
        fetch.append({"audio_md5": md5, "src": d["path"], "dest": dest, "size_bytes": d.get("size_bytes"),
                      "sha256": d.get("sha256") if isinstance(d.get("sha256"), str) else None})
    return added, fetch


def correct(catalogue: str, cfg: dict) -> dict:
    """[SPEC-LNK-020]: every finding in `catalogue` (the merge's copy of the hub's),
    each decided one applied to it, and the account of it. `cfg`:
    {"decisions": path, "answers": {node: path to its saved answer},
     "carried_to": [nodes this run patches], "not_carried": {node: why}}."""
    answers = {}
    for name, p in sorted((cfg.get("answers") or {}).items()):
        with open(p, encoding="utf-8") as fh:
            answers[name] = json.load(fh)
    decisions = load_decisions(cfg.get("decisions"))
    # Autocommit: each finding is its own savepoint, so one that cannot be
    # applied is rolled back alone and the others stand.
    conn = sqlite3.connect(catalogue, isolation_level=None)
    try:
        findings = find(conn)
        offers = offers_of(findings, answers)
        hub_root = _music_root(conn)
        fetch = []
        for f in findings:
            f["offers"] = offers.get(f["id"], [])
            f["decision"] = decisions.get(f["id"])
            e = effect(f, decisions, f["offers"])
            # What the decision asked for, kept apart from what came of it: one that
            # could not be applied is held, and is not a merge gone stale.
            f["intended"] = {"effect": e["effect"], "node": e["node"]}
            if e["effect"] in ("remove", "repair", "repair-files"):
                conn.execute("SAVEPOINT finding")
                try:
                    if e["effect"] == "remove":
                        e["applied"] = {f["table"]: -remove(conn, f)}
                    else:
                        a = answers[e["node"]]
                        o = next(o for o in f["offers"] if o["node"] == e["node"])
                        e["applied"] = repair(conn, f, o, a["answer"])
                        if e["effect"] == "repair-files":
                            more, got = with_files(conn, o, a["answer"], a["root"], hub_root)
                            for k, v in more.items():
                                e["applied"][k] = e["applied"].get(k, 0) + v
                            for g in got:
                                g["node"] = e["node"]
                            e["fetch"] = got
                            fetch += got
                    conn.execute("RELEASE finding")
                except (sqlite3.Error, ValueError, KeyError) as err:
                    conn.execute("ROLLBACK TO finding")
                    conn.execute("RELEASE finding")
                    e = {"effect": "held", "node": e["node"], "why": f"could not be applied: {err}"}
            f.update(e)
        after = find(conn)
    finally:
        conn.close()
    return {"findings": findings, "fetch": fetch, "hub_root": hub_root,
            "remaining": len(after), "asked": {n: {"by_recording": bool(a.get("by_recording")),
                                                   "fetchable": bool(a.get("fetchable"))}
                                               for n, a in answers.items()},
            "not_asked": cfg.get("not_asked", {}), "carried_to": cfg.get("carried_to", []),
            "not_carried": cfg.get("not_carried", {})}


def report_lines(res: dict) -> list[str]:
    """[SPEC-LNK-024]: the merge report's section, and the summary's."""
    fs = res.get("findings", [])
    L = ["", "# Links to nothing [SPEC070]", ""]
    if not fs:
        return L + ["None: every foreign key in the catalogue names a row it holds."]
    by = {}
    for f in fs:
        by.setdefault(f["effect"], []).append(f)
    L.append(", ".join(f"{len(v)} {k}" for k, v in sorted(by.items())) + f" ({sum(len(f['rows']) for f in fs)} row(s)).")
    if res.get("carried_to"):
        L.append(f"What is applied goes to the hub and to {', '.join(res['carried_to'])} in this commit; "
                 + (", ".join(f"{n} ({w})" for n, w in sorted(res['not_carried'].items())) + " at their next sync."
                    if res.get("not_carried") else "every other node at its next sync."))
    for f in fs:
        tag = f["effect"] + (f" from {f['node']}" if f.get("node") and f["effect"] != "held" else "")
        L.append(f"- `{f['id']}` **{tag}** -- {words(f)}" + (f" -- held: {f['why']}" if f.get("why") else ""))
        for o in f.get("offers", []):
            L.append(f"  - {o['node']} holds {len(o['keys'])} of the {len(f['missing'])} missing"
                     + (f", and {len(o['files'])} file(s) naming them" if o["files"] else "")
                     + ("" if o["files_known"] or f["parent"] != "recordings" else "; which of its files name them, its build cannot say"))
        if f.get("fetch"):
            L.append(f"  - to be copied to the hub at the commit: {len(f['fetch'])} file(s); the Export page then "
                     "sends them to the nodes without them [SPEC-LNK-054]")
    for n, why in sorted(res.get("not_asked", {}).items()):
        L.append(f"- not asked: {n} -- {why}")
    return L
