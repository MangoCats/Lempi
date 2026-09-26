#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Merge the fleet's databases into one hub copy [SPEC046].

Reads verified snapshots only, opened immutable, so not even a -shm is
written beside an input [SPEC-STAR-020]. Writes a new directory, and refuses
one that already holds anything. Deterministic: nodes are taken in name
order, and every rule that ties breaks the tie by node name
[SPEC-STAR-030]. Every decision a rule makes is written to the report
[SPEC-STAR-060].

Beside the hub's own pair it writes each node's, in nodes/<name>/: the
merged household edits with that node's own plays [REQ-PD-113], and the
hub's catalogue with that node's own paths [SPEC-STAR-080].

Usage:
  python tools/star_merge.py MANIFEST.json --out DIR

MANIFEST.json:
  {"nodes": [{"name": "lempi02w",
              "listener": "path/to/listener.db",
              "backups": "path/to/listener-backups",   (optional)
              "catalogue": "its library.db",   (optional: translates its ids)
              "files": "a db holding its files table",  (optional: its
                                                 catalogue copy's own paths)
              "mirror": true,   (optional: it receives the hub's listener)
              "taken_at": "2026-09-26T15:32:35Z"}, ...],
   "hub_state_from": "desktop"}   the node whose own plays, player_* and
                                   selection_decisions the hub keeps
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys


def open_ro(path: str) -> sqlite3.Connection:
    """Immutable: SQLite then writes nothing, not even a -shm, beside it."""
    uri = pathlib.Path(path).resolve().as_uri() + "?immutable=1"
    return sqlite3.connect(uri, uri=True)


def norm_time(v) -> str:
    """A comparable form of the mixed timestamps the tables hold: epoch
    seconds, 'YYYY-MM-DD HH:MM:SS' and 'YYYY-MM-DDTHH:MM:SS' all appear."""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return str(v).replace("T", " ").rstrip("Z")[:19]


# ---------------------------------------------------------------- the rules --
# [SPEC-STAR-040]. key: the portable identity. stamp: the column last-write-
# wins reads, if any. removals: whether a row's absence can be a deletion the
# node's own backups can evidence [SPEC-STAR-050].
LWW = "lww"          # last write wins on `stamp`
UNION = "union"      # every key any node holds; field-wise, a value beats NULL
LOCAL = "local"      # each node's own plays, never merged or shared [REQ-PD-113]
HUB = "hub"          # the hub's own state, taken from `hub_state_from`

TABLES = {
    "listener_preferences": dict(rule=LWW, key=("subject_kind", "subject_id"), stamp="updated_at"),
    "listener_characteristics": dict(rule=LWW, key=("subject_kind", "subject_id",
                                                    "characteristic", "class"), stamp="updated_at"),
    "listener_settings": dict(rule=LWW, key=("id",), stamp="updated_at"),
    "listener_flags": dict(rule=UNION, key=("subject_kind", "subject_id"),
                           stamp="flagged_at", removals=True),
    "listener_occasions": dict(rule=UNION, key=("characteristic", "class"), removals=True),
    "listener_occasion_points": dict(rule=UNION, key=("characteristic", "class", "month", "day"),
                                     removals=True),
    "listener_programs": dict(rule=UNION, key=("program_id",), removals=True),
    "listener_program_seeds": dict(rule=UNION, key=("program_id", "mbid"), removals=True),
    "listener_likes": dict(rule=UNION, key=("mbid", "recorded_at")),
    "id_reviews": dict(rule=UNION, key=("passage_id", "decided_at"), latest=("applied_at",)),
    "boundary_reviews": dict(rule=UNION, key=("passage_id", "decided_at"), latest=("applied_at",)),
    "artist_reviews": dict(rule=UNION, key=("recording_mbid", "decided_at"), latest=("applied_at",)),
    "listener_play_history": dict(rule=LOCAL),
    "listener_rejections": dict(rule=LOCAL),
    "player_queue": dict(rule=HUB),
    "player_settings": dict(rule=HUB),
    "player_state": dict(rule=HUB),
    "selection_decisions": dict(rule=HUB),
    "schema_meta": dict(rule=HUB),
}


class Node:
    def __init__(self, spec: dict):
        self.name = spec["name"]
        self.path = spec["listener"]
        self.taken_at = norm_time(spec.get("taken_at"))
        self.db = open_ro(self.path)
        self.mirror = bool(spec.get("mirror"))
        self.catalogue = spec.get("catalogue")
        self.remap: dict = {}          # [SPEC-STAR-049]: node passage id -> hub's
        self.remapped: dict = {}       # table -> rows translated
        self.tables = {r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        # The node's own history, oldest first: each hourly backup, then now.
        self.history: list[tuple[str, sqlite3.Connection]] = []
        b = spec.get("backups")
        if b:
            for p in sorted(glob.glob(os.path.join(b, "listener-*.db"))):
                m = re.search(r"listener-(\d+)\.db$", p)
                if m:
                    self.history.append((norm_time(int(m.group(1))), open_ro(p)))
        self.history.sort(key=lambda h: h[0])

    def cols(self, table: str) -> list[str]:
        return [r[1] for r in self.db.execute(f"PRAGMA table_info({table})")]

    def rows(self, table: str) -> list[dict]:
        if table not in self.tables:
            return []
        cols = self.cols(table)
        rows = [dict(zip(cols, r)) for r in self.db.execute(f"SELECT * FROM {table}")]
        if self.remap:
            for r in rows:
                if r.get("passage_id") in self.remap:
                    r["passage_id"] = self.remap[r["passage_id"]]
                    self.remapped[table] = self.remapped.get(table, 0) + 1
                elif (r.get("subject_kind") == "passage"
                      and _int(r.get("subject_id")) in self.remap):
                    new = self.remap[_int(r["subject_id"])]
                    r["subject_id"] = type(r["subject_id"])(new)
                    self.remapped[table] = self.remapped.get(table, 0) + 1
        return rows


def keys_in(conn: sqlite3.Connection, table: str, key: tuple) -> set | None:
    try:
        return {tuple(r) for r in conn.execute(f"SELECT {', '.join(key)} FROM {table}")}
    except sqlite3.Error:
        return None


def removals(node: Node, table: str, key: tuple) -> dict:
    """[SPEC-STAR-050]: keys this node held in a backup and does not hold now,
    each with the time of the last backup that still held it -- the removal
    happened after that."""
    now = keys_in(node.db, table, key) or set()
    last_seen: dict = {}
    for when, conn in node.history:
        for k in keys_in(conn, table, key) or set():
            last_seen[k] = when
    return {k: t for k, t in last_seen.items() if k not in now}


class Report:
    def __init__(self):
        self.lines: list[str] = []
        self.data: dict = {"tables": {}, "decisions": []}

    def decide(self, table: str, key, what: str, rule: str):
        self.data["decisions"].append(dict(table=table, key=list(key) if isinstance(key, tuple) else key,
                                           what=what, rule=rule))


def merge_table(table: str, spec: dict, nodes: list[Node], hub: str, rep: Report):
    rule = spec["rule"]
    per_node = {n.name: n.rows(table) for n in nodes}
    counts = {name: len(rows) for name, rows in per_node.items()}
    if rule in (HUB, LOCAL):
        chosen = per_node.get(hub, [])
        rep.data["tables"][table] = dict(rule=rule, per_node=counts, merged=len(chosen))
        return chosen

    key = spec["key"]
    merged: dict = {}
    source: dict = {}
    holders: dict = {}
    for n in nodes:                                  # name order: the tie-break
        for row in per_node[n.name]:
            if any(k not in row for k in key):
                continue
            k = tuple(row[c] for c in key)
            holders.setdefault(k, set()).add(n.name)
            if k not in merged:
                merged[k], source[k] = dict(row), n.name
                continue
            have = merged[k]
            if rule == LWW:
                a, b = norm_time(have.get(spec["stamp"])), norm_time(row.get(spec["stamp"]))
                if b > a:
                    rep.decide(table, k, f"{n.name}'s over {source[k]}'s: newer {spec['stamp']} "
                               f"{row.get(spec['stamp'])}", "SPEC-PREF-105")
                    merged[k], source[k] = dict(row), n.name
                elif b == a and any(have.get(c) != row.get(c) for c in row if c in have):
                    rep.decide(table, k, f"tie at {a}, values differ; kept {source[k]}'s, "
                               f"{n.name} disagrees", "SPEC-STAR-030")
            else:  # UNION: field by field
                for c, v in row.items():
                    if c in spec.get("latest", ()) :
                        if norm_time(v) > norm_time(have.get(c)):
                            have[c] = v
                    elif have.get(c) is None and v is not None:
                        have[c] = v
                    elif v is not None and have.get(c) != v and c != "origin":
                        rep.decide(table, k, f"{c}: kept {source[k]}'s {have.get(c)!r}, "
                                   f"{n.name} has {v!r}", "SPEC-STAR-030")

    removed = 0
    if spec.get("removals"):
        stamp = spec.get("stamp")
        for n in nodes:
            for k, seen in removals(n, table, key).items():
                if k not in merged:
                    continue
                re_added = stamp and norm_time(merged[k].get(stamp)) > seen
                if re_added:
                    rep.decide(table, k, f"removed on {n.name} after {seen}, but "
                               f"{stamp} {merged[k].get(stamp)} is newer: kept", "SPEC-STAR-050")
                else:
                    del merged[k]
                    removed += 1
                    rep.decide(table, k, f"removed: {n.name} held it in its backup of "
                               f"{seen} and not since", "SPEC-STAR-050")

    rows = [merged[k] for k in sorted(merged, key=lambda k: tuple("" if x is None else str(x) for x in k))]
    only: dict = {}
    for k in merged:
        if len(holders[k]) == 1:
            (who,) = holders[k]
            only[who] = only.get(who, 0) + 1
    rep.data["tables"][table] = dict(rule=rule, per_node=counts, merged=len(rows),
                                     removed=removed, only_on=only)
    return rows


def build(manifest: dict, out: str) -> Report:
    rep = Report()
    os.makedirs(out, exist_ok=True)
    if os.listdir(out):
        raise SystemExit(f"{out} is not empty: the merge writes a new directory, never over one")
    # The catalogue first: the listener half translates passage ids against
    # it [SPEC-STAR-049].
    hub_cat = None
    if "catalogue" in manifest:
        build_catalogue(manifest["catalogue"], out, rep)
        hub_cat = os.path.join(out, "library.db")
    if "nodes" in manifest:
        build_listener(manifest, out, rep, hub_cat)
        if hub_cat:
            catalogue_copies(manifest, out, rep)
    write_report(rep, out)
    return rep


def passage_remap(node_cat: str, hub_cat: str):
    """[SPEC-STAR-049]: node passage id -> hub passage id, for ids whose file
    differs between the two catalogues; and the ids the hub no longer has."""
    q = ("SELECT p.passage_id, f.audio_md5, p.kind, p.start_ms FROM passages p "
         "JOIN files f USING (file_id)")
    node = {r[0]: r[1:] for r in open_ro(node_cat).execute(q)}
    hub = {r[0]: r[1:] for r in open_ro(hub_cat).execute(q)}
    def by_file(m):
        out = {}
        for pid, (md5, kind, start) in m.items():
            out.setdefault((md5, kind), []).append((start, pid))
        return {k: [p for _, p in sorted(v)] for k, v in out.items()}
    nf, hf = by_file(node), by_file(hub)
    remap, gone = {}, []
    for pid, (md5, kind, _) in node.items():
        if pid in hub and hub[pid][0] == md5:
            continue                          # same file: the same passage
        siblings, theirs = nf.get((md5, kind), []), hf.get((md5, kind), [])
        if pid not in hub and not theirs:
            gone.append(pid)
            continue
        i = siblings.index(pid)
        if pid in hub and i < len(theirs):
            remap[pid] = theirs[i]
        elif pid not in hub:
            gone.append(pid)                  # re-cut since: matches nothing
    return remap, sorted(gone)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build_listener(manifest: dict, out: str, rep: Report, hub_cat: str | None = None):
    nodes = sorted((Node(s) for s in manifest["nodes"]), key=lambda n: n.name)
    rep.data["translations"] = {}
    for n in nodes:
        if n.catalogue and hub_cat:
            n.remap, gone = passage_remap(n.catalogue, hub_cat)
            rep.data["translations"][n.name] = dict(
                ids=sorted([a, b] for a, b in n.remap.items()), unmatched=gone)
    hub = manifest["hub_state_from"]
    if hub not in {n.name for n in nodes}:
        raise SystemExit(f"hub_state_from names {hub!r}, which is not a node in the manifest")
    every = sorted(set().union(*(n.tables for n in nodes)))
    unknown = [t for t in every if t not in TABLES and not t.startswith("sqlite_")]
    if unknown:
        raise SystemExit(f"no rule for table(s) {unknown}: [SPEC-STAR-060] a decision "
                         "the report does not name is not made -- add a rule first")
    rep.data["integrity"] = write_listener(nodes, hub, os.path.join(out, "listener.db"), rep)
    rep.data["nodes"] = [dict(name=n.name, taken_at=n.taken_at, backups=len(n.history))
                         for n in nodes]
    for n in nodes:
        if n.name in rep.data["translations"]:
            rep.data["translations"][n.name]["rows"] = dict(n.remapped)

    # [SPEC-STAR-080]: each node's own listener -- the household's edits,
    # merged exactly as the hub's are, and that node's own plays and state
    # [REQ-PD-113], in the schema its own player made. A mirror of the hub
    # receives the hub's.
    rep.data["copies"] = {}
    for n in nodes:
        if n.name == hub:
            continue
        d = os.path.join(out, "nodes", n.name)
        os.makedirs(d)
        path = os.path.join(d, "listener.db")
        if n.mirror:
            shutil.copyfile(os.path.join(out, "listener.db"), path)
            rep.data["copies"][n.name] = dict(listener="the hub's: a mirror")
            continue
        scratch = Report()
        ok = write_listener(nodes, n.name, path, scratch, schema_of=n)
        own = {t: s["merged"] for t, s in scratch.data["tables"].items()
               if s["rule"] in (LOCAL, HUB)}
        rep.data["copies"][n.name] = dict(listener=f"its own, integrity {ok}", own=own)


def write_listener(nodes: list[Node], own: str, path: str, rep: Report,
                   schema_of: Node | None = None) -> str:
    """One listener database: the household's edits merged, and `own`'s own
    plays and state. `schema_of`: each table as that node defines it, where
    it has the table; otherwise the widest definition any node has."""
    dest = sqlite3.connect(path)
    for table in sorted(set().union(*(n.tables for n in nodes))):
        if table.startswith("sqlite_"):
            continue
        holders = [n for n in nodes if table in n.tables]
        if schema_of is not None and table in schema_of.tables:
            src = schema_of
        else:                       # a newer schema's columns win
            src = max(holders, key=lambda n: (len(n.cols(table)), n.name))
        sql = src.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                             (table,)).fetchone()[0]
        dest.execute(sql)
        for (isql,) in src.db.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,)):
            dest.execute(isql)
        cols = src.cols(table)
        rows = merge_table(table, TABLES[table], nodes, own, rep)
        dest.executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [tuple(r.get(c) for c in cols) for r in rows])
    dest.commit()
    check = dest.execute("PRAGMA integrity_check").fetchone()[0]
    dest.close()
    return check


def catalogue_copies(manifest: dict, out: str, rep: Report):
    """[SPEC-STAR-080]: each node's catalogue is the hub's, with the node's
    own machine-scope columns [SPEC-DF-030] -- matched by `audio_md5`, since
    a file id is local too [SPEC-DF-035]. A node names the database holding
    its own `files` table as `files`; one that names none gets no catalogue,
    and the report says so."""
    hub_cat = os.path.join(out, "library.db")
    held = {r[1] for r in open_ro(hub_cat).execute("PRAGMA table_info(files)")}
    cols = sorted(MACHINE_SCOPE["files"] & held)
    for spec in sorted(manifest["nodes"], key=lambda s: s["name"]):
        name = spec["name"]
        if name == manifest["hub_state_from"]:
            continue
        entry = rep.data.setdefault("copies", {}).setdefault(name, {})
        if not spec.get("files"):
            entry["catalogue"] = "none: the manifest names no `files` for this node"
            continue
        theirs = {r[0]: r[1:] for r in open_ro(spec["files"]).execute(
            f"SELECT audio_md5, {', '.join(cols)} FROM files")}
        d = os.path.join(out, "nodes", name)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "library.db")
        shutil.copyfile(hub_cat, path)
        dest = sqlite3.connect(path)
        md5s = [r[0] for r in dest.execute("SELECT audio_md5 FROM files ORDER BY file_id")]
        missing = [m for m in md5s if m not in theirs]
        if missing:
            dest.close()
            raise SystemExit(f"{name} has no file for {len(missing)} of the hub's, e.g. {missing[:3]}: "
                             "its catalogue would name paths it does not have [SPEC-STAR-080]")
        dest.executemany(f"UPDATE files SET {', '.join(c + ' = ?' for c in cols)} WHERE audio_md5 = ?",
                         [(*theirs[m], m) for m in md5s])
        dest.commit()
        ok = dest.execute("PRAGMA integrity_check").fetchone()[0]
        dest.close()
        entry["catalogue"] = f"the hub's, with its own {', '.join(cols)}; integrity {ok}"
        entry["only_theirs"] = len(set(theirs) - set(md5s))


# ------------------------------------------------------------ catalogue half --
# [SPEC-STAR-047]: three ways, against the oldest common ancestor.

# Each machine's own, never merged [SPEC-DF-030]: the hub keeps its own.
MACHINE_SCOPE = {"files": {"path", "size_bytes", "mtime", "last_seen"}}
TIME_COLUMNS = ("updated_at", "fetched_at", "decided_at", "applied_at", "scanned_at")
PROVENANCE = ("source", "boundary_src")


def _synced(row: dict | None) -> bool:
    """Received from elsewhere, not made here: `synced:<origin>`."""
    src = (row or {}).get("source") or (row or {}).get("boundary_src") or ""
    return src.startswith("synced:")


def rank(row: dict | None) -> int:
    """Provenance rank of a row [SPEC-DF-070]: manual > synced > computed >
    inherited/local. A deletion (None) ranks lowest of all."""
    if row is None:
        return -1
    src = row.get("source") or row.get("boundary_src") or ""
    if src == "manual":
        return 4
    if src.startswith("synced:"):
        return 3
    if src.startswith(("inherited:", "local:")) or src == "":
        return 1
    return 2


def row_time(row: dict | None) -> str:
    if row is None:
        return ""
    for c in TIME_COLUMNS:
        if row.get(c) is not None:
            return norm_time(row[c])
    return ""


def pk_of(conn: sqlite3.Connection, table: str) -> list[str]:
    info = list(conn.execute(f"PRAGMA table_info({table})"))
    return [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]] or [r[1] for r in info]


def load(conn: sqlite3.Connection, table: str, key: list[str]) -> dict | None:
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if not cols:
            return None
        out = {}
        for r in conn.execute(f"SELECT * FROM {table}"):
            d = dict(zip(cols, r))
            out[tuple(d.get(k) for k in key)] = d
        return out
    except sqlite3.Error:
        return None


def build_catalogue(cat: dict, out: str, rep: Report):
    base = open_ro(cat["base"])
    copies = sorted(((c["name"], open_ro(c["library"])) for c in cat["nodes"]), key=lambda x: x[0])
    # [SPEC-STAR-048]: an appliance receives the catalogue and never authors it.
    receivers = {c["name"] for c in cat["nodes"] if c.get("role") == "receiver"}
    home = cat["machine_from"]
    names = [n for n, _ in copies]
    if home not in names:
        raise SystemExit(f"machine_from names {home!r}, which is not a catalogue node")
    dest = sqlite3.connect(os.path.join(out, "library.db"))
    everyone = [("base", base)] + copies
    tables_of = lambda c: {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Only tables some catalogue copy holds. The ancestor may be a whole,
    # pre-split database whose listener tables belong to the other half.
    tables = sorted(set().union(*(tables_of(c) for _, c in copies)) - {"sqlite_sequence"})
    rep.data["catalogue"] = {"base": cat["base"], "nodes": names, "tables": {}, "conflicts": [],
                             "receivers": sorted(receivers), "receiver_differences": [],
                             "ancestor_only": sorted(tables_of(base) - set(tables) - {"sqlite_sequence"})}
    for table in tables:
        holders = [(n, c) for n, c in everyone if [1 for _ in c.execute(f"PRAGMA table_info({table})")]]
        wname, widest = max(holders, key=lambda h: (len(list(h[1].execute(f"PRAGMA table_info({table})"))), h[0]))
        cols = [r[1] for r in widest.execute(f"PRAGMA table_info({table})")]
        dest.execute(widest.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                                    (table,)).fetchone()[0])
        for (isql,) in widest.execute("SELECT sql FROM sqlite_master WHERE type='index' "
                                      "AND tbl_name=? AND sql IS NOT NULL", (table,)):
            dest.execute(isql)
        key = pk_of(widest, table)
        machine = MACHINE_SCOPE.get(table, set())
        compare = [c for c in cols if c not in machine]
        b = load(base, table, key) or {}
        # A column the ancestor predates is baselined at the default its
        # migration gives it, not NULL: filling in `fade_in_ms = 20` is a
        # schema change, not an edit, and must not read as one -- while a
        # copy holding anything else there still counts as having changed it.
        base_cols = {r[1] for r in base.execute(f"PRAGMA table_info({table})")}
        if base_cols:
            defaults = {r[1]: _literal(r[4]) for r in widest.execute(f"PRAGMA table_info({table})")
                        if r[1] not in base_cols}
            for row in b.values():
                for c, v in defaults.items():
                    row.setdefault(c, v)
        per = {n: load(c, table, key) for n, c in copies}
        per = {n: d for n, d in per.items() if d is not None}   # a copy without the table says nothing
        authors = {n: d for n, d in per.items() if n not in receivers}
        # A table no author holds -- `works`, which exists only on the
        # appliances -- is decided among the receivers: nothing outranks them.
        receivers_only = not authors
        if receivers_only:
            authors = per
        listen = {n: d for n, d in per.items() if n not in authors}
        view = lambda row: None if row is None else tuple(row.get(c) for c in compare)
        # The same row without its provenance label, to tell one edit seen
        # through two transports from two different edits.
        content = lambda row: None if row is None else tuple(
            row.get(c) for c in compare if c not in PROVENANCE)
        merged, taken, conflicts, deleted, relabelled, diffs = {}, {}, 0, 0, 0, 0
        order = lambda k: tuple("" if x is None else str(x) for x in k)
        for k in sorted(set(b).union(*authors.values()), key=order):
            base_row = b.get(k)
            changes = {n: d.get(k) for n, d in authors.items() if view(d.get(k)) != view(base_row)}
            if not changes:
                chosen, who = base_row, None
            else:
                distinct = {view(r) for r in changes.values()}
                same_content = len({content(r) for r in changes.values()}) == 1
                if len(distinct) == 1:
                    who = sorted(changes)[0]
                    chosen = changes[who]
                elif same_content:
                    # One edit, labelled differently where it travelled: the
                    # original says `review:acoustid`, a spoke that received it
                    # says `synced:GMKtec`. The hub keeps the original.
                    who = sorted(changes, key=lambda n: (_synced(changes[n]), -rank(changes[n]), n))[0]
                    chosen = changes[who]
                    relabelled += 1
                else:
                    conflicts += 1
                    mods = {n: r for n, r in changes.items() if r is not None}
                    pool = mods or changes     # a modification outranks a deletion
                    who = sorted(pool, key=lambda n: (-rank(pool[n]), _neg(row_time(pool[n])), n))[0]
                    chosen = pool[who]
                    rep.data["catalogue"]["conflicts"].append(dict(
                        table=table, key=list(k), chose=who,
                        values={n: (None if r is None else {c: r.get(c) for c in compare}) for n, r in changes.items()},
                        base=None if base_row is None else {c: base_row.get(c) for c in compare}))
                if chosen is None:
                    deleted += 1
                taken[who] = taken.get(who, 0) + 1
            for n, d in listen.items():
                rv = d.get(k)
                if rv is not None and content(rv) not in (content(chosen), content(base_row)):
                    diffs += 1
                    rep.data["catalogue"]["receiver_differences"].append(dict(
                        table=table, key=list(k), receiver=n,
                        receiver_value={c: rv.get(c) for c in compare},
                        merged_value=None if chosen is None else {c: chosen.get(c) for c in compare}))
            if chosen is None:
                continue
            row = {c: chosen.get(c) for c in cols}
            if machine:
                own = per.get(home, {}).get(k) or base_row or chosen
                for c in machine:
                    row[c] = own.get(c)
            merged[k] = row
        # Rows only a receiver holds: what it was sent and no author kept, or
        # never had. Listed, never taken [SPEC-STAR-048].
        for n, d in listen.items():
            for k in sorted(set(d) - set(merged) - set(b), key=order):
                diffs += 1
                rep.data["catalogue"]["receiver_differences"].append(dict(
                    table=table, key=list(k), receiver=n,
                    receiver_value={c: d[k].get(c) for c in compare}, merged_value=None))
        dest.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         [tuple(r[c] for c in cols) for r in merged.values()])
        rep.data["catalogue"]["tables"][table] = dict(
            base=len(b), per_node={n: len(d) for n, d in per.items()}, merged=len(merged),
            changes_from=taken, conflicts=conflicts, deleted=deleted, relabelled=relabelled,
            receiver_differences=diffs, receivers_only=receivers_only)
    dest.commit()
    rep.data["catalogue"]["integrity"] = dest.execute("PRAGMA integrity_check").fetchone()[0]
    dest.close()


def _show(row) -> str:
    """A row for a person to read: a blob (cover art) as its size, not its bytes."""
    if row is None:
        return "none"
    return "{" + ", ".join(f"{k}: " + (f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray)) else repr(v))
                           for k, v in row.items()) + "}"


def _literal(dflt):
    """A column's DEFAULT as PRAGMA table_info reports it: '20', "'exponential'",
    or None."""
    if dflt is None:
        return None
    t = str(dflt)
    if len(t) >= 2 and t[0] == t[-1] == "'":
        return t[1:-1]
    for kind in (int, float):
        try:
            return kind(t)
        except ValueError:
            pass
    return t


def _neg(s: str) -> str:
    """Sort key making a later time sort first."""
    return "".join(chr(0x10FFFF - ord(ch)) for ch in s)


def write_report(rep: Report, out: str):
    d = rep.data
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8", newline="\n") as fh:
        # A blob (cover art) as its size, as `_show` gives it: its bytes made
        # this file 600 MB on 2026-09-26.
        json.dump(d, fh, indent=1, sort_keys=True,
                  default=lambda v: f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray)) else str(v))
    L = ["# Star merge report", "",
         "Read it before promoting this output to `data/` [SPEC-STAR-070]."]
    if "nodes" in d:
        L += listener_section(d)
    if "catalogue" in d:
        L += catalogue_section(d["catalogue"])
    if d.get("copies"):
        L += copies_section(d["copies"])
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


def catalogue_section(c: dict) -> list[str]:
    names = c["nodes"]
    L = ["", "# Catalogue half", "",
         "Three ways against the common ancestor [SPEC-STAR-047]. `changes from` counts the",
         "rows the hub took from each copy's own change; `conflicts` are rows two copies",
         "changed differently, each listed below with the rule's choice.", "",
         f"Ancestor: `{c['base']}`", "",
         "`relabelled` are one edit carried with two provenance labels, where the hub keeps",
         "the original's rather than a spoke's `synced:` copy.", "",
         "| table | base | " + " | ".join(names) + " | merged | changes from | conflicts | relabelled | deleted |",
         "| :--- | ---: | " + " | ".join("---:" for _ in names) + " | ---: | :--- | ---: | ---: | ---: |"]
    for t, s in sorted(c["tables"].items()):
        ch = ", ".join(f"{n} {k}" for n, k in sorted(s["changes_from"].items()))
        L.append(f"| `{t}` | {s['base']} | " + " | ".join(str(s["per_node"].get(n, "-")) for n in names)
                 + f" | {s['merged']} | {ch} | {s['conflicts']} | {s.get('relabelled', 0)} | {s['deleted']} |")
    if c.get("receivers"):
        L += ["", "Receivers (appliances, whose catalogue is sent to them, never authored there "
              "[SPEC-STAR-048]): " + ", ".join(c["receivers"]) + ". A table marked *receivers only* "
              "was decided among them; no author holds it."]
        ro = [t for t, s in sorted(c["tables"].items()) if s.get("receivers_only")]
        if ro:
            L.append("Receivers only: " + ", ".join(f"`{t}`" for t in ro))
        L += ["", "Tables only the ancestor holds, not merged: " + ", ".join(f"`{t}`" for t in c["ancestor_only"])]
    L += ["", f"Integrity of the output: **{c['integrity']}**.", "", "## Conflicts", ""]
    if not c["conflicts"]:
        L.append("None: wherever two copies changed a row, they changed it the same way.")
    for x in c["conflicts"]:
        L.append(f"- `{x['table']}` {x['key']}: chose **{x['chose']}** -- " + "; ".join(
            f"{n}: {'deleted' if v is None else _show(v)}" for n, v in sorted(x["values"].items())))
    rd = c.get("receiver_differences", [])
    L += ["", "## Receiver differences, for the person", "",
          "Values an appliance holds that differ from both the ancestor and the merged result.",
          "None was taken. Each is either something the appliance was sent that no author kept,",
          "or an edit that reached only the appliance -- which is the question for the person.", ""]
    if not rd:
        L.append("None.")
    by = {}
    for x in rd:
        by.setdefault((x["table"], x["receiver"]), []).append(x)
    for (t, n), xs in sorted(by.items()):
        L.append(f"- `{t}` on {n}: {len(xs)} row(s)")
        for x in xs[:25]:
            L.append(f"  - {x['key']}: {n} has {_show(x['receiver_value'])}; merged "
                     f"{'has none' if x['merged_value'] is None else 'has ' + _show(x['merged_value'])}")
        if len(xs) > 25:
            L.append(f"  - ... and {len(xs) - 25} more in report.json")
    return L


def copies_section(c: dict) -> list[str]:
    L = ["", "# Each node's copy [SPEC-STAR-080]", "",
         "In `nodes/<name>/`: the household's edits as merged above, with the node's own plays,",
         "rejections and player state [REQ-PD-113], and the hub's catalogue with the node's own",
         "paths. The hub's own pair is the one beside this report.", ""]
    for n, e in sorted(c.items()):
        own = ", ".join(f"`{t}` {k}" for t, k in sorted(e.get("own", {}).items()))
        L.append(f"- **{n}**: listener {e.get('listener', 'none')}" + (f" ({own})" if own else "")
                 + f"; catalogue {e.get('catalogue', 'none')}"
                 + (f"; {e['only_theirs']} file(s) it holds that the hub does not" if e.get("only_theirs") else ""))
    return L


def listener_section(d: dict) -> list[str]:
    L = ["", "# Listener half", "",
         "Every row the hub took from one node over another, every tie and every removal.",
         "Read it before promoting this output to `data/` [SPEC-STAR-070].", "",
         "## Inputs", "", "| node | snapshot taken | backups read |", "| :--- | :--- | ---: |"]
    L += [f"| {n['name']} | {n['taken_at']} | {n['backups']} |" for n in d["nodes"]]
    names = [n["name"] for n in d["nodes"]]
    L += ["", "## Tables", "", "| table | rule | " + " | ".join(names) + " | merged | removed |",
          "| :--- | :--- | " + " | ".join("---:" for _ in names) + " | ---: | ---: |"]
    for t, s in sorted(d["tables"].items()):
        L.append(f"| `{t}` | {s['rule']} | " + " | ".join(str(s["per_node"].get(x, "—")) for x in names)
                 + f" | {s['merged']} | {s.get('removed', '')} |")
    L += ["", "## Rows only one node held", ""]
    for t, s in sorted(d["tables"].items()):
        if s.get("only_on"):
            L.append(f"- `{t}`: " + ", ".join(f"{n} {c}" for n, c in sorted(s["only_on"].items())))
    tr = {k: v for k, v in d.get("translations", {}).items() if v["ids"] or v["unmatched"]}
    if tr:
        L += ["", "## Passage ids translated to the hub's [SPEC-STAR-049]", ""]
        for n, v in sorted(tr.items()):
            L.append(f"- {n}: {len(v['ids'])} id(s) translated {v['ids']}; rows translated "
                     f"{v.get('rows', {})}; {len(v['unmatched'])} id(s) the hub no longer has "
                     f"(rows naming them keep the id and match nothing)")
    L += ["", f"Integrity of the output: **{d['integrity']}**.", "", "## Decisions", ""]
    if not d["decisions"]:
        L.append("None: every table agreed wherever nodes overlapped.")
    for x in d["decisions"]:
        L.append(f"- `{x['table']}` {x['key']}: {x['what']} `[{x['rule']}]`")
    return L


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "--out":
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        manifest = json.load(fh)
    rep = build(manifest, argv[2])
    checks = {}
    if "integrity" in rep.data:
        checks["listener"] = rep.data["integrity"]
    if "catalogue" in rep.data:
        checks["catalogue"] = rep.data["catalogue"]["integrity"]
    conflicts = len(rep.data.get("catalogue", {}).get("conflicts", []))
    print(f"merged into {argv[2]}: integrity {checks}, {len(rep.data['decisions'])} listener "
          f"decision(s), {conflicts} catalogue conflict(s) -- see report.md")
    return 0 if all(v == "ok" for v in checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
