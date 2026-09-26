#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Merge the fleet's databases into one hub copy [SPEC046].

Reads verified snapshots only, opened immutable, so not even a -shm is
written beside an input [SPEC-STAR-020]. Writes a new directory, and refuses
one that already holds anything. Deterministic: nodes are taken in name
order, and every rule that ties breaks the tie by node name
[SPEC-STAR-030]. Every decision a rule makes is written to the report
[SPEC-STAR-060].

This is the listener half. The catalogue half follows the same shape.

Usage:
  python tools/star_merge.py MANIFEST.json --out DIR

MANIFEST.json:
  {"nodes": [{"name": "lempi02w",
              "listener": "path/to/listener.db",
              "backups": "path/to/listener-backups",   (optional)
              "taken_at": "2026-09-26T15:32:35Z"}, ...],
   "hub_state_from": "desktop"}   the node whose player_* and
                                   selection_decisions the hub keeps
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import pathlib
import re
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
EVENTS = "events"    # union, deduplicated; the hub keeps all, nodes keep theirs
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
    "listener_play_history": dict(rule=EVENTS, key=("played_at", "passage_id", "mbid"),
                                  larger=("heard_ms", "span_ms"), renumber="play_id"),
    "listener_rejections": dict(rule=EVENTS, key=("rejected_at", "kind", "passage_id", "mbid"),
                                larger=("heard_ms", "span_ms"), renumber="rejection_id"),
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
        return [dict(zip(cols, r)) for r in self.db.execute(f"SELECT * FROM {table}")]


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
    if rule == HUB:
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
            else:  # UNION and EVENTS: field by field
                for c, v in row.items():
                    if c in spec.get("latest", ()) :
                        if norm_time(v) > norm_time(have.get(c)):
                            have[c] = v
                    elif c in spec.get("larger", ()):
                        if v is not None and (have.get(c) is None or v > have[c]):
                            have[c] = v
                    elif c == spec.get("renumber"):
                        continue
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
    if spec.get("renumber"):
        for i, r in enumerate(rows, 1):
            r[spec["renumber"]] = i
    only: dict = {}
    for k in merged:
        if len(holders[k]) == 1:
            (who,) = holders[k]
            only[who] = only.get(who, 0) + 1
    rep.data["tables"][table] = dict(rule=rule, per_node=counts, merged=len(rows),
                                     removed=removed, only_on=only)
    return rows


def build(manifest: dict, out: str) -> Report:
    nodes = sorted((Node(s) for s in manifest["nodes"]), key=lambda n: n.name)
    hub = manifest["hub_state_from"]
    if hub not in {n.name for n in nodes}:
        raise SystemExit(f"hub_state_from names {hub!r}, which is not a node in the manifest")
    rep = Report()
    os.makedirs(out, exist_ok=True)
    if os.listdir(out):
        raise SystemExit(f"{out} is not empty: the merge writes a new directory, never over one")
    dest = sqlite3.connect(os.path.join(out, "listener.db"))

    every = sorted(set().union(*(n.tables for n in nodes)))
    unknown = [t for t in every if t not in TABLES and not t.startswith("sqlite_")]
    if unknown:
        raise SystemExit(f"no rule for table(s) {unknown}: [SPEC-STAR-060] a decision "
                         "the report does not name is not made -- add a rule first")
    for table in every:
        if table.startswith("sqlite_"):
            continue
        # The widest definition any node has: a newer schema's columns win.
        holders = [n for n in nodes if table in n.tables]
        widest = max(holders, key=lambda n: (len(n.cols(table)), n.name))
        sql = widest.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                                (table,)).fetchone()[0]
        dest.execute(sql)
        for (isql,) in widest.db.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,)):
            dest.execute(isql)
        cols = widest.cols(table)
        rows = merge_table(table, TABLES[table], nodes, hub, rep)
        dest.executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [tuple(r.get(c) for c in cols) for r in rows])
    dest.commit()
    check = dest.execute("PRAGMA integrity_check").fetchone()[0]
    dest.close()
    rep.data["integrity"] = check
    rep.data["nodes"] = [dict(name=n.name, taken_at=n.taken_at, backups=len(n.history))
                         for n in nodes]
    write_report(rep, out)
    return rep


def write_report(rep: Report, out: str):
    d = rep.data
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(d, fh, indent=1, sort_keys=True, default=str)
    L = ["# Star merge report — listener half", "",
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
    L += ["", f"Integrity of the output: **{d['integrity']}**.", "", "## Decisions", ""]
    if not d["decisions"]:
        L.append("None: every table agreed wherever nodes overlapped.")
    for x in d["decisions"]:
        L.append(f"- `{x['table']}` {x['key']}: {x['what']} `[{x['rule']}]`")
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "--out":
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        manifest = json.load(fh)
    rep = build(manifest, argv[2])
    print(f"merged into {argv[2]}: integrity {rep.data['integrity']}, "
          f"{len(rep.data['decisions'])} decision(s) in report.md")
    return 0 if rep.data["integrity"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
