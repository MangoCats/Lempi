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
              "mirror": true,   (optional, unused since 2026-10-04 [SPEC-STAR-103]: it receives the hub's listener)
              "sent": "the copy it was last sent",   (optional: its baseline,
                                [SPEC-STAR-110]; none means in step with the hub)
              "sent_run": "20261112T2008Z",   (optional: the run that copy is from)
              "taken_at": "2026-09-26T15:32:35Z"}, ...],
   "newest_sent": {"run": "20261112T2008Z", "listener": "its merge/listener.db"},
                  (optional: the newest run any node was sent, whole, so a row
                   removed in a run some node missed stays removed [IMPL-UID-042])
   "verdicts": "path/to/verdicts.json",   (optional) [SPEC-STAR-114]
   "hub_state_from": "desktop",   the node whose own plays, player_* and
                                   selection_decisions the hub keeps
   "catalogue": {...} | "hub_library": "data/library.db"}
     a three-way merge of every copy [SPEC-STAR-047], or, routinely, the
     hub's own catalogue as it stands [SPEC-STAR-085]
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalogue_integrity as ci  # noqa: E402  -- links to nothing, found and decided [SPEC070]


def open_ro(path: str) -> sqlite3.Connection:
    """Immutable: SQLite then writes nothing, not even a -shm, beside it."""
    uri = pathlib.Path(path).resolve().as_uri() + "?immutable=1"
    return sqlite3.connect(uri, uri=True)


def norm_time(v) -> str:
    """A comparable form of the mixed timestamps the tables hold: epoch
    seconds, 'YYYY-MM-DD HH:MM:SS' and 'YYYY-MM-DDTHH:MM:SS' all appear.

    **And the fraction of a second, where a stamp has one.** `passage_lyrics`
    is stamped to the millisecond, so that two saves a moment apart read in
    their order [SPEC-LYR-140]. Until 2026-11-13 this cut every stamp to its
    second: two nodes saving one passage at 10:00:00.100 and 10:00:00.900 read
    as a tie, the merge kept the first node by name, and `approve-all` sent the
    older text everywhere. The fraction is kept without its trailing zeros, so
    `10:00:00`, `10:00:00.0` and `10:00:00.000` are one time, and the forms
    compare as text in time order: `…:00` < `…:00.1` < `…:00.9` < `…:01`.
    Anything after it -- a zone -- is dropped, as it always was."""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    s = str(v).replace("T", " ").rstrip("Z")
    m = re.match(r"\.(\d+)", s[19:])
    frac = m.group(1).rstrip("0") if m else ""
    return s[:19] + ("." + frac if frac else "")


def edited_since_backup(stamp, seen: str) -> bool:
    """Whether a row stamped `stamp` was written after the backup of `seen`
    [SPEC-STAR-050] -- re-added since, so a removal the backup evidences is
    not applied.

    **To the second, which is all a backup's time is.** `seen` comes from the
    epoch in a backup's name, `listener-<epoch>.db`, in whole seconds, while
    `norm_time` keeps a stamp's fraction since 2026-11-13. Compared whole, a
    row stamped 10:00:00.400 read as newer than a backup named for 10:00:00
    and taken at 10:00:00.9 -- which held it -- so its removal was skipped and
    the deleted row came back. Within the backup's own second the backup is
    taken to hold the row, as it always was before the fraction was kept."""
    return norm_time(stamp)[:19] > seen[:19]


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
    # `own`: columns that are each node's own and never a change to review
    # [SPEC-STAR-110] -- a player rewrites its zone's offset at every start.
    "listener_settings": dict(rule=LWW, key=("id",), stamp="updated_at", own=("utc_offset_minutes",)),
    "listener_flags": dict(rule=UNION, key=("subject_kind", "subject_id"),
                           stamp="flagged_at", removals=True),
    "listener_occasions": dict(rule=UNION, key=("characteristic", "class"), removals=True),
    "listener_occasion_points": dict(rule=UNION, key=("characteristic", "class", "month", "day"),
                                     removals=True),
    # A node's own since 2026-09-27 [SPEC-MTR-040]: each node defines and
    # edits its own programmes, and copying them between a node and the hub
    # is a person's act, never the merge's. Unioned before that, so every node
    # starts from the same set.
    "listener_programs": dict(rule=LOCAL),
    "listener_program_seeds": dict(rule=LOCAL),
    # The name and source of the programme a node runs [SPEC-PGM-220]: its
    # own, like the time slots it describes.
    "listener_programme": dict(rule=LOCAL),
    # What the hub sent and the node has not answered, or kept its own over
    # [SPEC-PGM-200]: the node's own question, never the merge's.
    "listener_programme_pending": dict(rule=LOCAL),
    "listener_likes": dict(rule=UNION, key=("mbid", "recorded_at")),
    # Words saved for a passage [SPEC-LYR-200]. Keyed by what the passage is,
    # with no passage number, so nothing here is translated between a node's
    # numbering and the hub's, and every node receives every row, whether or
    # not it holds the file. No row is ever deleted [SPEC-LYR-155], so there
    # are no removals to find. `newer`: a player from before 2026-11-12
    # neither sends this table nor accepts a patch that names it, so a node
    # that sent none is sent none (`star_sync.signed_tables`).
    #
    # `hub_sets` [IMPL-UID-063]: each row's `passage_uid`, which the hub alone
    # sets, once, for a row whose segment is a current passage's, and which
    # changes no save. Where the hub holds the row with a value, that value is
    # the merge's; a node's never competes and is never a change. A row the hub
    # lacks, or holds with none -- a node's new save, which records its
    # passage's uid -- keeps the winner's [IMPL-UID-065].
    "passage_lyrics": dict(rule=LWW, key=("audio_md5", "kind", "start_ms"), stamp="updated_at",
                           newer=True, hub_sets=("passage_uid",)),
    # A decision withdrawn stays withdrawn [IMPL-UID-042]. Until 2026-11-14
    # these took no removals, so a review deleted on the hub came back from
    # every node still holding it -- how 27 copied reviews outlived the
    # repair of 2026-10-18 [SPEC-STAR-051].
    # Keyed by the passage's uid [IMPL-UID-064]: the same on every node, so a
    # node's review and the hub's of one passage are one row, and nothing is
    # translated between numberings.
    "id_reviews": dict(rule=UNION, key=("passage_uid", "decided_at"), latest=("applied_at",), removals=True),
    "boundary_reviews": dict(rule=UNION, key=("passage_uid", "decided_at"), latest=("applied_at",), removals=True),
    "artist_reviews": dict(rule=UNION, key=("recording_mbid", "decided_at"), latest=("applied_at",)),
    "listener_play_history": dict(rule=LOCAL),
    "listener_rejections": dict(rule=LOCAL),
    # What a node found reading its own files back [GDE-INT-130]: about its
    # own disk, so its own; reported to the hub in the snapshot instead.
    "file_checks": dict(rule=LOCAL),
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
        self.sent_run = spec.get("sent_run")
        self.aliases: dict = {}        # [SPEC-RLK-155]: a retired audio_md5 -> the hub's
        # [IMPL-UID-080]: rows of a source still naming a passage by number -- a copy
        # last sent from before Stage B, as a phone not yet updated has -- left out,
        # or kept without a uid, and counted; never read through a map. Its snapshot
        # is never such a source: one still by number is refused (`refuse_by_number`).
        self.by_number: dict = {}      # table -> rows naming a passage by number
        self.aliased: dict = {}        # table -> rows whose retired audio_md5 was read as the hub's
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
        # [SPEC-STAR-110]: the copy this node was last sent, which is what it
        # held when it was last in step with the hub. A node with none is
        # taken as having been in step with the hub's current rows.
        sent = spec.get("sent")
        self.base = open_ro(sent) if sent and os.path.exists(sent) else None
        self.base_tables = ({r[0] for r in self.base.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")} if self.base else set())
        self._base_rows: dict = {}

    def close(self) -> None:
        """Let go of the inputs: a folder holding an open file cannot be moved on Windows."""
        for conn in [self.db, self.base, *(c for _, c in self.history)]:
            if conn is not None:
                conn.close()

    def cols(self, table: str) -> list[str]:
        return [r[1] for r in self.db.execute(f"PRAGMA table_info({table})")]

    def _read(self, conn: sqlite3.Connection, table: str, count: bool,
              translate: bool = True, tally: bool | None = None) -> list[dict]:
        """`tally`: whether rows read by number are counted for the report --
        the snapshot's and the copy last sent's, each read once; by default as
        `count`."""
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        rows = [dict(zip(cols, r)) for r in conn.execute(f"SELECT * FROM {table}")]
        if not translate:
            return rows
        return self._translate(rows, cols, table, count, count if tally is None else tally)

    def keys(self, conn: sqlite3.Connection, table: str, key: tuple) -> set | None:
        """`table`'s keys in `conn`, read as `rows` reads them -- a passage by
        its uid -- so they compare with the merge's. None where `conn` has no
        such table.

        [IMPL-UID-042]: until 2026-11-14 removal evidence compared these raw, in
        the node's own numbering, against the merge's keys in the hub's: a flag
        a node cleared on its passage 16407 deleted the hub's flag on its own
        16407, other music, and left the real one standing everywhere. By uid
        since Stage B, there is no numbering to confuse [IMPL-UID-064]."""
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            raw = [dict(zip(cols, r)) for r in conn.execute(f"SELECT * FROM {table}")]
        except sqlite3.Error:
            return None
        if not cols:
            return None
        kept = self._translate(raw, cols, table, False)
        return {tuple(r.get(c) for c in key) for r in kept if all(c in r for c in key)}

    def _translate(self, rows: list[dict], cols: list[str], table: str, count: bool,
                   tally: bool = False) -> list[dict]:
        if self.aliases and "audio_md5" in cols:
            for r in rows:
                if r["audio_md5"] in self.aliases:
                    r["audio_md5"] = self.aliases[r["audio_md5"]]
                    if count:
                        self.aliased[table] = self.aliased.get(table, 0) + 1
        return by_uid(rows, cols, table, self.by_number if tally else None)

    def rows(self, table: str) -> list[dict]:
        if table not in self.tables:
            return []
        return self._read(self.db, table, True)

    def own_rows(self, table: str) -> list[dict]:
        """This node's rows exactly as it holds them, in its own numbering --
        for the tables that are its own and never merged, written back to it
        unchanged rather than translated there and back."""
        if table not in self.tables:
            return []
        return self._read(self.db, table, False, translate=False)

    def base_rows(self, table: str, key: tuple) -> dict | None:
        """What this node held of `table` when last sent its copy, by key; None
        where it has no such copy. A table that copy lacks holds nothing."""
        if self.base is None:
            return None
        if table not in self._base_rows:
            self._base_rows[table] = {} if table not in self.base_tables else {
                tuple(r.get(c) for c in key): r for r in self._read(self.base, table, False, tally=True)
                if all(c in r for c in key)}
        return self._base_rows[table]


def keys_in(conn: sqlite3.Connection, table: str, key: tuple) -> set | None:
    try:
        return {tuple(r) for r in conn.execute(f"SELECT {', '.join(key)} FROM {table}")}
    except sqlite3.Error:
        return None


def removals(node: Node, table: str, key: tuple) -> dict:
    """[SPEC-STAR-050]: keys this node held in a backup and does not hold now,
    each with the time of the last backup that still held it -- the removal
    happened after that.

    A node whose copy lacks the table says nothing about it, as elsewhere in
    the merge: its history is not evidence that it removed every row there.
    A phone uploads its shared tables only [REQ-AND-330], and without this
    every occasion its history held would have read as removed by it.

    Every key is read in the hub's numbering, as the merge's are, and one
    naming a passage the hub cannot match is no evidence [IMPL-UID-042]."""
    if table not in node.tables:
        return {}
    now = node.keys(node.db, table, key)
    if now is None:
        return {}
    last_seen: dict = {}
    for when, conn in node.history:
        for k in node.keys(conn, table, key) or set():
            last_seen[k] = when
    return {k: t for k, t in last_seen.items() if k not in now}


def _is_uid(s) -> bool:
    """A uid's form: 32 lowercase hexadecimal characters [SPEC-UID-030]."""
    return isinstance(s, str) and len(s) == 32 and all(c in "0123456789abcdef" for c in s)


def by_uid(rows: list, cols: list, table: str, by_number: dict | None = None) -> list:
    """[IMPL-UID-080]: rows as Stage B names a passage -- by its uid. A source still
    naming one by number, from before Stage B, is not read through any map: where
    the passage is the row's key -- a review, a flag -- the row is left out, and
    anywhere else it keeps no uid, as the player's own conversion keeps a row it
    cannot place [IMPL-UID-062]. Each is counted in `by_number`. Until Stage D such a
    source was read through its own catalogue's map from number to uid, which only
    the six nodes' copies last sent before Stage B's round needed; a source by uid
    passes as it is."""
    def note():
        if by_number is not None:
            by_number[table] = by_number.get(table, 0) + 1
    if table in BY_UID_TABLES and "passage_id" in cols:
        keyed = "passage_uid" in TABLES.get(table, {}).get("key", ())
        out = []
        for r in rows:
            if r.pop("passage_id", None) is not None:
                note()
                if keyed:
                    continue
            r["passage_uid"] = None
            out.append(r)
        return out
    if table == "listener_flags":
        out = []
        for r in rows:
            if r.get("subject_kind") == "passage" and not _is_uid(r.get("subject_id")):
                note()
                continue
            out.append(r)
        return out
    return rows


class Newest:
    """[IMPL-UID-042]: the whole merge of the newest run any node was sent.

    A node that missed the run that removed a row still holds it, unchanged
    from the copy it was last sent, and its own history -- that copy -- holds
    it too, so `removals` sees nothing and the union brings it back. Found
    2026-11-14 as the reason a row removal lasted one run. This copy is what
    says it went: a row every holder holds unchanged from a copy older than
    this one, and this one lacks, was removed in a run those holders missed.
    Read as the hub numbers it, since the hub's own copy is the whole merge."""

    def __init__(self, spec: dict | None):
        self.run = (spec or {}).get("run")
        # Each node's snapshot that run merged, by when it was taken: a holder
        # bringing the very same one brings nothing that run had not weighed.
        self.weighed = {n: norm_time(t) for n, t in ((spec or {}).get("weighed") or {}).items()}
        path = (spec or {}).get("listener")
        self.db = open_ro(path) if self.run and path and os.path.exists(path) else None
        self.why_not = (None if self.db else
                        "no newest copy given" if not spec else f"the newest copy {path!r} is not there")
        self._keys: dict = {}

    def keys(self, table: str, key: tuple) -> set | None:
        if self.db is None:
            return None
        if table not in self._keys:
            try:
                cols = [r[1] for r in self.db.execute(f"PRAGMA table_info({table})")]
                rows = [dict(zip(cols, r)) for r in self.db.execute(f"SELECT * FROM {table}")] if cols else None
            except sqlite3.Error:
                rows = None
            self._keys[table] = None if rows is None else {
                tuple(r.get(c) for c in key) for r in by_uid(rows, cols, table)
                if all(c in r for c in key)}
        return self._keys[table]

    def close(self) -> None:
        if self.db is not None:
            self.db.close()


def missed(newest: Newest | None, table: str, key: tuple, held: dict, merged: dict,
           by_name: dict) -> dict:
    """[IMPL-UID-042]: key -> the nodes that missed its removal, for each row
    `newest` lacks that every node holding it holds unchanged from a copy
    older than `newest`. A row any holder changed or added since its copy,
    holds with no copy, or holds under a passage the hub cannot match, is
    not one: an edit or an addition is never undone by this.

    **A holder bringing the very snapshot `newest` merged is no evidence
    either way**, and is passed over. Found 2026-11-15 on a phone: its copy
    is recorded when it is *offered* a patch, not when it applies one, so a
    phone that never applied the offer still holds a flag its new copy
    lacks. That read as an addition, and the flag the hub cleared on
    2026-11-08 would have come back everywhere. Its upload was unchanged
    since 2026-10-29, and the newest run had already weighed it, and removed
    the flag."""
    have = newest.keys(table, key) if newest is not None else None
    if have is None:
        return {}
    out = {}
    for k in merged:
        if k in have:
            continue
        holders = held.get(k, {})
        if not holders:
            continue
        for nm, row in holders.items():
            n = by_name[nm]
            if newest.weighed.get(nm) and newest.weighed[nm] == n.taken_at:
                continue                       # the same snapshot the newest run already merged
            base = n.base_rows(table, key)
            if (base is None or k not in base or not _same(row, base[k])
                    or not n.sent_run or n.sent_run >= newest.run):
                break
        else:
            out[k] = sorted(holders)
    return out


def _jdefault(v):
    """A blob (cover art) as a digest, so an item's identity does not hold its bytes."""
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes {hashlib.sha256(bytes(v)).hexdigest()[:16]}>"
    return str(v)


def item_id(table: str, key, column, kind: str, candidates: dict) -> str:
    """[SPEC-STAR-112]: what a verdict is about -- the row, the kind, and every
    candidate's node and value, but not which the rule would take, so the same
    situation keeps the same id however it was decided."""
    blob = json.dumps([table, list(key) if isinstance(key, tuple) else key, column, kind,
                       sorted(candidates.items())], sort_keys=True, default=_jdefault)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class Report:
    def __init__(self, verdicts: dict | None = None, hub: str | None = None):
        self.lines: list[str] = []
        self.data: dict = {"tables": {}, "decisions": [], "items": [], "propagated": {}}
        # [SPEC-STAR-114]: id -> "approved" | "chose:<node>"
        self.verdicts = {i: (v["verdict"] if isinstance(v, dict) else v)
                         for i, v in (verdicts or {}).items()}
        self.hub = hub

    def decide(self, table: str, key, what: str, rule: str):
        self.data["decisions"].append(dict(table=table, key=list(key) if isinstance(key, tuple) else key,
                                           what=what, rule=rule))

    def propagated(self, table: str):
        self.data["propagated"][table] = self.data["propagated"].get(table, 0) + 1

    def item(self, table: str, key, kind: str, candidates: dict, default: str, rule: str,
             column=None, stamps: dict | None = None) -> str:
        """[SPEC-STAR-110]: a change the merge would discard. Records the item
        and returns the node whose value is taken -- the default, unless a
        verdict names another candidate."""
        ident = item_id(table, key, column, kind, candidates)
        verdict = self.verdicts.get(ident)
        chosen = default
        if verdict and verdict.startswith("chose:") and verdict[6:] in candidates:
            chosen = verdict[6:]
        stamps = stamps or {}
        self.data["items"].append(dict(
            id=ident, table=table, key=list(key) if isinstance(key, tuple) else key, column=column,
            kind=kind, rule=rule, default=default, chosen=chosen,
            candidates=[dict(node=n, value=v, stamp=stamps.get(n)) for n, v in sorted(candidates.items())]))
        return chosen


def _same(a, b, skip=()) -> bool:
    """Whether two rows hold the same values, over the columns both have and
    leaving out `skip`: the same settings made at two times are one change."""
    if a is None or b is None:
        return a is None and b is None
    return all(a[c] == b[c] for c in a.keys() & b.keys() if c not in skip)


def _was(rep: Report, node: Node, table: str, key: tuple, k: tuple, held: dict):
    """The row `node` held when last sent its copy. With no such copy, the
    hub's current row: the node is taken to have been in step with the hub, so
    only what it differs by counts as its change [SPEC-STAR-110]. The hub with
    none is unchanged by definition."""
    rows = node.base_rows(table, key)
    if rows is not None:
        return rows.get(k)
    if node.name == rep.hub:
        return held.get(node.name)
    return held.get(rep.hub)


def _order(k: tuple) -> tuple:
    return tuple("" if x is None else str(x) for x in k)


def merge_table(table: str, spec: dict, nodes: list[Node], hub: str, rep: Report,
                newest_copy: Newest | None = None):
    rule = spec["rule"]
    per_node = {n.name: n.rows(table) for n in nodes}
    counts = {name: len(rows) for name, rows in per_node.items()}
    if rule in (HUB, LOCAL):
        chosen = per_node.get(hub, [])
        rep.data["tables"][table] = dict(rule=rule, per_node=counts, merged=len(chosen))
        return chosen

    key = spec["key"]
    by_name = {n.name: n for n in nodes}
    merged: dict = {}
    source: dict = {}
    holders: dict = {}
    held: dict = {}          # key -> {node: its row as read}, for [SPEC-STAR-110]
    for n in nodes:                                  # name order: the tie-break
        for row in per_node[n.name]:
            if any(k not in row for k in key):
                continue
            k = tuple(row[c] for c in key)
            holders.setdefault(k, set()).add(n.name)
            held.setdefault(k, {})[n.name] = row
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
                elif b == a and any(have.get(c) != row.get(c) for c in row
                                    if c in have and c not in spec.get("hub_sets", ())):
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

    stamp = spec.get("stamp")
    if rule == LWW:
        if spec.get("own"):
            _own_is_no_write(table, spec, key, by_name, held, merged, source, rep)
        _items_lww(table, spec, key, by_name, held, merged, source, rep)
    else:
        _items_union(table, spec, key, by_name, held, merged, source, rep)
    # [IMPL-UID-065]: the hub's NULL is a value it never set, not one it set to
    # nothing, so it is no reason to replace the winner's -- a node's newer save
    # for a row the hub could not place records its own passage's uid. Until
    # 2026-11-17 the hub's NULL won, and every node was sent that save without it.
    for c in spec.get("hub_sets", ()):
        for k, row in merged.items():
            hub_row = held.get(k, {}).get(rep.hub)
            if hub_row is not None and hub_row.get(c) is not None:
                row[c] = hub_row[c]
    # [SPEC-STAR-111]: a node's own column is its own in its own copy. `hub` here is
    # the node whose copy this is, so the hub keeps its own too. Until 2026-11-17 the
    # winner's went into every copy: one node's zone offset onto every node.
    for c in spec.get("own", ()):
        for k, row in merged.items():
            mine = held.get(k, {}).get(hub)
            if mine is not None and c in mine:
                row[c] = mine[c]

    removed = 0
    if spec.get("removals"):
        for n in nodes:
            for k, seen in removals(n, table, key).items():
                if k not in merged:
                    continue
                re_added = stamp and edited_since_backup(merged[k].get(stamp), seen)
                # [SPEC-STAR-110]: a removal is only an item where another node
                # changed the same row, so an edit would be lost with it.
                editors = {nm: r for nm, r in held.get(k, {}).items() if nm != n.name
                           and not _same(r, _was(rep, by_name[nm], table, key, k, held[k]))}
                remove = not re_added
                if editors:
                    newest = sorted(editors, key=lambda nm: (norm_time(editors[nm].get(stamp)) if stamp else "", nm))[-1]
                    default = n.name if remove else newest
                    cands = {n.name: None, **editors}
                    took = rep.item(table, k, "delete-vs-edit", cands, default, "SPEC-STAR-050",
                                    stamps={nm: norm_time(r.get(stamp)) if stamp else None
                                            for nm, r in editors.items()})
                    remove = took == n.name
                if not remove:
                    rep.decide(table, k, f"removed on {n.name} after {seen}, but "
                               f"{stamp} {merged[k].get(stamp)} is newer: kept" if re_added else
                               f"removed on {n.name} after {seen}, but a verdict keeps it", "SPEC-STAR-050")
                else:
                    del merged[k]
                    removed += 1
                    rep.decide(table, k, f"removed: {n.name} held it in its backup of "
                               f"{seen} and not since", "SPEC-STAR-050")
        for k, who in missed(newest_copy, table, key, held, merged, by_name).items():
            del merged[k]
            removed += 1
            rep.decide(table, k, f"removed in run {newest_copy.run}, which {', '.join(who)} missed: "
                       f"held unchanged since an older copy, and not in that run's", "IMPL-UID-042")

    rows = [merged[k] for k in sorted(merged, key=_order)]
    only: dict = {}
    for k in merged:
        if len(holders[k]) == 1:
            (who,) = holders[k]
            only[who] = only.get(who, 0) + 1
    rep.data["tables"][table] = dict(rule=rule, per_node=counts, merged=len(rows),
                                     removed=removed, only_on=only)
    return rows


def _own_is_no_write(table, spec, key, by_name, held, merged, source, rep):
    """[SPEC-STAR-111]: a row a node changed since it was last sent only in its own
    columns -- the zone offset its player keeps in step with the OS -- is no write of
    the household's, so its newer stamp does not make it the row every node takes.
    The newest of the nodes that did change the household's columns is taken instead,
    by the rule the merge takes any row by: the newest stamp, the first node by name
    at a tie. Where none did, every holder holds what it was last sent, and the row
    taken is as good as any.

    Until 2026-11-17 the stamp alone decided. A player rewrites its offset whenever the
    OS's differs from the one stored -- a restart, a clock change, a node carried to
    another zone; on this fleet about weekly (lp3-wifi's of 2026-11-16 13:00 won every
    node's row in the next run). Had a time scale been set on the hub in that window,
    the offset's newer stamp would have taken the old scale back to every node, and the
    item's default would have been the revert."""
    stamp = spec["stamp"]
    skip = {stamp, *spec["own"], *spec.get("hub_sets", ())}
    for k in sorted(held, key=_order):
        rows = held[k]
        win = source[k]
        if win not in rows:
            continue
        wrote = {nm: r for nm, r in rows.items()
                 if not _same(r, _was(rep, by_name[nm], table, key, k, rows), skip)}
        if win in wrote or not wrote:
            continue
        best = None
        for nm in sorted(wrote):
            if best is None or norm_time(wrote[nm].get(stamp)) > norm_time(wrote[best].get(stamp)):
                best = nm
        rep.decide(table, k, f"{best}'s over {win}'s: {win} changed none of the household's columns since "
                   f"it was last sent, only its stamp or its own {', '.join(spec['own'])}", "SPEC-STAR-111")
        merged[k], source[k] = dict(rows[best]), best


def _items_lww(table, spec, key, by_name, held, merged, source, rep):
    """[SPEC-STAR-110] for a last-write-wins table: a row is an item where some
    node changed it to a value the merge does not take."""
    stamp = spec["stamp"]
    skip = {stamp, *spec.get("own", ()), *spec.get("hub_sets", ())}
    for k in sorted(held, key=_order):
        rows = held[k]
        changed = {nm: r for nm, r in rows.items()
                   if not _same(r, _was(rep, by_name[nm], table, key, k, rows), skip)}
        if not changed:
            continue
        win, win_row = source[k], merged[k]
        lost = {nm: r for nm, r in changed.items() if not _same(r, win_row, skip)}
        if not lost:
            rep.propagated(table)
            continue
        cands = dict(changed)
        cands.setdefault(win, win_row)
        stamps = {nm: norm_time(r.get(stamp)) for nm, r in cands.items()}
        kind = "tie" if any(stamps[nm] == stamps[win] for nm in lost) else "conflict"
        took = rep.item(table, k, kind, {nm: dict(r) for nm, r in cands.items()}, win,
                        "SPEC-STAR-030" if kind == "tie" else "SPEC-PREF-105", stamps=stamps)
        if took != win:
            merged[k], source[k] = dict(rows[took]), took
            rep.decide(table, k, f"verdict: {took}'s over {win}'s", "SPEC-STAR-114")


def _items_union(table, spec, key, by_name, held, merged, source, rep):
    """[SPEC-STAR-110] for a union table, per column: a column is an item where
    some node changed it to a value other than the one kept. A stamp, an
    `origin` and a `latest` column are timing and labels, never a choice."""
    skip = {*spec.get("latest", ()), "origin", *key, *([spec["stamp"]] if spec.get("stamp") else [])}
    for k in sorted(held, key=_order):
        rows = held[k]
        any_change = False
        lost_any = False
        for c in sorted({c for r in rows.values() for c in r} - skip):
            changed = {}
            for nm, r in rows.items():
                v = r.get(c)
                if v is None:
                    continue
                was = _was(rep, by_name[nm], table, key, k, rows)
                if was is None or was.get(c) != v:
                    changed[nm] = v
            if not changed:
                continue
            any_change = True
            kept = merged[k].get(c)
            lost = {nm: v for nm, v in changed.items() if v != kept}
            if not lost:
                continue
            lost_any = True
            first = sorted(nm for nm, r in rows.items() if r.get(c) == kept)
            default = first[0] if first else sorted(changed)[0]
            cands = dict(changed)
            cands.setdefault(default, kept)
            took = rep.item(table, k, "conflict", cands, default, "SPEC-STAR-030", column=c)
            if took != default:
                merged[k][c] = cands[took]
                rep.decide(table, k, f"verdict: {c} from {took}", "SPEC-STAR-114")
        if any_change and not lost_any:
            rep.propagated(table)


def load_verdicts(path: str | None) -> dict:
    """`verdicts.json` [SPEC-STAR-114]: id -> {"verdict": ..., "at": ..., "by": ...}."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build(manifest: dict, out: str) -> Report:
    rep = Report(load_verdicts(manifest.get("verdicts")), manifest.get("hub_state_from"))
    os.makedirs(out, exist_ok=True)
    if os.listdir(out):
        raise SystemExit(f"{out} is not empty: the merge writes a new directory, never over one")
    # The catalogue first: the listener half translates passage ids against
    # it [SPEC-STAR-049].
    hub_cat = None
    if "catalogue" in manifest:
        build_catalogue(manifest["catalogue"], out, rep)
        hub_cat = os.path.join(out, "library.db")
    elif "hub_library" in manifest:
        # [SPEC-STAR-085]: routinely only the hub authors the catalogue, so
        # its own is the merged one, taken as it stands; every node receives.
        hub_cat = os.path.join(out, "library.db")
        src = open_ro(manifest["hub_library"])
        dst = sqlite3.connect(hub_cat)
        src.backup(dst)
        rep.data["hub_library"] = dict(source=manifest["hub_library"],
                                       integrity=dst.execute("PRAGMA integrity_check").fetchone()[0])
        dst.close()
    if hub_cat and "integrity" in manifest:
        # [SPEC-LNK-020]: before any node's copy is made from it, so what a
        # person decided is in the hub's own patch and in every node's.
        rep.data["links"] = ci.correct(hub_cat, manifest["integrity"])
        with open(os.path.join(out, "integrity.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rep.data["links"], fh, indent=1, sort_keys=True, default=str)
    if "nodes" in manifest:
        build_listener(manifest, out, rep, hub_cat)
        if hub_cat:
            catalogue_copies(manifest, out, rep)
    write_report(rep, out)
    return rep


def aliases(hub_cat: str) -> dict:
    """[SPEC-RLK-155]: each retired `audio_md5` -> the one the hub keys that
    file by now. A node that has not yet received a re-key still holds the old
    value, and it is the same file, not a different one."""
    c = open_ro(hub_cat)
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'audio_md5_aliases'").fetchone():
            return {}
        return dict(c.execute("SELECT old_md5, new_md5 FROM audio_md5_aliases"))
    finally:
        c.close()


#: The listener tables Stage B gives a `passage_uid` in place of a number [IMPL-UID-060].
BY_UID_TABLES = ("listener_play_history", "listener_rejections", "selection_decisions", "player_state",
                 "player_queue", "id_reviews", "boundary_reviews", "artist_reviews")


def by_number_tables(conn: sqlite3.Connection) -> list[str]:
    """The tables of a listener still naming a passage by its number, as before Stage B."""
    out = []
    for t in BY_UID_TABLES:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        if "passage_id" in cols:
            out.append(t)
    return out


def refuse_by_number(nodes: list) -> None:
    """[IMPL-UID-064]: every snapshot merged names its passages by uid. A player
    from before Stage B still names them by number, and would be sent a copy in a
    shape it cannot read, so the run is refused by that node's name and nothing is
    merged -- deploy Stage B's player there, or leave it out of the run. Its copy
    last sent may still be by number; its rows naming a passage are left out
    (`by_uid`). A phone that sends no table naming a passage is not refused."""
    for n in nodes:
        found = [t for t in by_number_tables(n.db) if t in n.tables]
        if found:
            raise SystemExit(
                f"{n.name}'s listener names passages by number ({', '.join(found)}): its player is from "
                "before Stage B. Deploy Stage B's player there, or leave it out of this run. "
                "Nothing was merged [IMPL-UID-064].")


def build_listener(manifest: dict, out: str, rep: Report, hub_cat: str | None = None):
    nodes = sorted((Node(s) for s in manifest["nodes"]), key=lambda n: n.name)
    newest = Newest(manifest.get("newest_sent"))
    try:
        _build_listener(nodes, manifest, out, rep, hub_cat, newest)
    finally:
        for n in nodes:
            n.close()
        newest.close()


def _build_listener(nodes: list, manifest: dict, out: str, rep: Report, hub_cat: str | None,
                    newest: Newest):
    rep.data["translations"] = {}
    # [IMPL-UID-042], said either way: without the newest copy a node that
    # missed a run brings back what that run removed, and nothing else shows it.
    rep.data["newest_sent"] = (dict(run=newest.run) if newest.db is not None
                               else dict(missing=newest.why_not))
    hub = manifest["hub_state_from"]
    if hub not in {n.name for n in nodes}:
        raise SystemExit(f"hub_state_from names {hub!r}, which is not a node in the manifest")
    refuse_by_number(nodes)
    # [IMPL-UID-064], [IMPL-UID-080]: no translation between numberings, and no map from
    # a number: a source still by number contributes none of its rows naming a passage.
    for n in nodes:
        if hub_cat:
            n.aliases = aliases(hub_cat)
    every = sorted(set().union(*(n.tables for n in nodes)))
    unknown = [t for t in every if t not in TABLES and not t.startswith("sqlite_")]
    if unknown:
        raise SystemExit(f"no rule for table(s) {unknown}: [SPEC-STAR-060] a decision "
                         "the report does not name is not made -- add a rule first")
    rep.data["integrity"] = write_listener(nodes, hub, os.path.join(out, "listener.db"), rep, newest=newest)
    rep.data["nodes"] = [dict(name=n.name, taken_at=n.taken_at, backups=len(n.history))
                         for n in nodes]
    for n in nodes:
        if n.by_number or n.aliased:
            rep.data["translations"][n.name] = dict(by_number=dict(n.by_number), aliased=dict(n.aliased))

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
        scratch = Report(rep.verdicts, rep.hub)     # the same verdicts as the hub's own copy
        ok = write_listener(nodes, n.name, path, scratch, schema_of=n, newest=newest)
        own = {t: s["merged"] for t, s in scratch.data["tables"].items()
               if s["rule"] in (LOCAL, HUB)}
        rep.data["copies"][n.name] = dict(listener=f"its own, integrity {ok}", own=own)
        not_there = {t: s["not_on_node"] for t, s in scratch.data["tables"].items()
                     if s.get("not_on_node")}
        if not_there:
            rep.data["copies"][n.name]["not_on_node"] = not_there


def write_listener(nodes: list[Node], own: str, path: str, rep: Report,
                   schema_of: Node | None = None, newest: Newest | None = None) -> str:
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
        rows = merge_table(table, TABLES[table], nodes, own, rep, newest)
        # A node's own tables go back as it holds them. The shared ones name a
        # passage by uid, the same on every node, and go back as merged: no
        # numbering to translate them into [IMPL-UID-064].
        if schema_of is not None and schema_of.catalogue and TABLES[table]["rule"] in (LOCAL, HUB):
            rows = schema_of.own_rows(table)
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
    a file id is local too [SPEC-DF-035], and a node's retired `audio_md5`
    read as the hub's [SPEC-RLK-155]. A node names the database holding its
    own `files` table as `files`; one that names none gets no catalogue, and
    the report says so."""
    hub_cat = os.path.join(out, "library.db")
    hc = open_ro(hub_cat)
    held = {r[1] for r in hc.execute("PRAGMA table_info(files)")}
    hc.close()
    cols = sorted(MACHINE_SCOPE["files"] & held)
    for spec in sorted(manifest["nodes"], key=lambda s: s["name"]):
        name = spec["name"]
        if name == manifest["hub_state_from"]:
            continue
        entry = rep.data.setdefault("copies", {}).setdefault(name, {})
        if not spec.get("files"):
            entry["catalogue"] = "none: the manifest names no `files` for this node"
            continue
        alias = aliases(hub_cat)
        fc = open_ro(spec["files"])
        try:
            # What the node's summary has: a player older than [SPEC-NKP-050] sends
            # no `first_seen`, and the hub's value stands for it.
            have = {r[1] for r in fc.execute("PRAGMA table_info(files)")}
            use = [c for c in cols if c in have]
            theirs = {alias.get(r[0], r[0]): r[1:] for r in fc.execute(
                f"SELECT audio_md5, {', '.join(use)} FROM files")}
        finally:
            fc.close()
        d = os.path.join(out, "nodes", name)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "library.db")
        shutil.copyfile(hub_cat, path)
        dest = sqlite3.connect(path)
        md5s = [r[0] for r in dest.execute("SELECT audio_md5 FROM files ORDER BY file_id")]
        missing = [m for m in md5s if m not in theirs]
        if missing:
            # [SPEC-STAR-082]: music the hub has and this node does not is left
            # out of its copy, not a reason to sync nothing. Until 2026-10-14
            # this refused the whole run: every album added on the hub stopped
            # the routine sync until it had been sent to every speaker.
            entry["held_out"] = hold_out(dest, missing)
            md5s = [m for m in md5s if m in theirs]
        dest.executemany(f"UPDATE files SET {', '.join(c + ' = ?' for c in use)} WHERE audio_md5 = ?",
                         [(*theirs[m], m) for m in md5s])
        dest.commit()
        ok = dest.execute("PRAGMA integrity_check").fetchone()[0]
        dest.close()
        entry["catalogue"] = f"the hub's, with its own {', '.join(use)}; integrity {ok}"
        entry["only_theirs"] = len(set(theirs) - set(md5s))


def _chunks(xs: list, n: int = 500):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def hold_out(dest: sqlite3.Connection, md5s: list[str]) -> dict:
    """[SPEC-STAR-082]: every row of a node's catalogue copy about files the
    node does not hold, removed -- the files, their passages, and whatever
    names a file, a passage or an audio hash: tags, credits, checks, the
    file's release and art, the hub's own caches. The node's copy is then the
    hub's catalogue of the music the node has. Recordings, releases and
    artists stay: they are music's names, not a file's, and harmless unplayed.
    When the files are sent, the bundle writes their rows, and the next sync
    reconciles them through the node's own `rows` [SPEC-STAR-945].

    Returns what it removed and the album folders it came from, for the
    report."""
    fids, pids, folders = [], [], set()
    for part in _chunks(md5s):
        q = ",".join("?" * len(part))
        for fid, path in dest.execute(f"SELECT file_id, path FROM files WHERE audio_md5 IN ({q})", part):
            fids.append(fid)
            folders.add(os.path.basename(os.path.dirname(path.replace("\\", "/"))) or path)
    for part in _chunks(fids):
        q = ",".join("?" * len(part))
        pids += [r[0] for r in dest.execute(f"SELECT passage_id FROM passages WHERE file_id IN ({q})", part)]
    removed = {}
    for (t,) in dest.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        cols = {r[1] for r in dest.execute(f"PRAGMA table_info({t})")}
        col, vals = next(((c, v) for c, v in (("passage_id", pids), ("file_id", fids), ("audio_md5", md5s))
                          if c in cols), (None, None))
        n = 0
        if col is not None and vals:
            for part in _chunks(vals):
                n += dest.execute(f"DELETE FROM {t} WHERE {col} IN ({','.join('?' * len(part))})", part).rowcount
        # A table that names its subject by kind and id -- `flavor` -- names a passage
        # by its number, as text, where the kind is 'passage'. Found by column name
        # alone, those rows stayed, naming passages the copy no longer held, and the
        # natural patch, which cannot name such a row, withheld the node's whole
        # catalogue [SPEC-NKP-080] -- for an album it had simply not been sent yet.
        # Latent until 2026-11-17, when it was found: the hub's 847,848 flavor rows
        # are every one about a recording.
        if {"subject_kind", "subject_id"} <= cols and pids:
            for part in _chunks([str(p) for p in pids]):
                n += dest.execute(f"DELETE FROM {t} WHERE subject_kind = 'passage' AND subject_id IN "
                                  f"({','.join('?' * len(part))})", part).rowcount
        if n:
            removed[t] = n
    return {"files": len(md5s), "albums": sorted(folders), "rows": removed}


# ------------------------------------------------------------ catalogue half --
# [SPEC-STAR-047]: three ways, against the oldest common ancestor.

# Each machine's own, never merged [SPEC-DF-030]: the hub keeps its own.
# `first_seen` since [SPEC-NKP-050]: when this installation first saw the file.
MACHINE_SCOPE = {"files": {"path", "size_bytes", "mtime", "last_seen", "first_seen"}}
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
                    values = {n: (None if r is None else {c: r.get(c) for c in compare}) for n, r in changes.items()}
                    # [SPEC-STAR-110]: a catalogue conflict is an item, and a
                    # verdict may take any copy's change, a deletion included.
                    who = rep.item(table, k, "delete-vs-edit" if None in changes.values() else "conflict",
                                   values, who, "SPEC-STAR-047",
                                   stamps={n: row_time(r) for n, r in changes.items() if r is not None})
                    chosen = changes[who]
                    rep.data["catalogue"]["conflicts"].append(dict(
                        table=table, key=list(k), chose=who, values=values,
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


UID_FORM = re.compile(r"[0-9a-f]{32}")


def shown(v):
    """`v` for a person to read: every passage uid in it as its first eight
    characters [IMPL-UID-076], which is how a person is shown one. The JSON
    beside a report keeps the whole uid, for a machine."""
    if isinstance(v, str) and UID_FORM.fullmatch(v):
        return v[:8]
    if isinstance(v, (list, tuple)):
        return [shown(x) for x in v]
    if isinstance(v, dict):
        return {k: shown(x) for k, x in v.items()}
    return v


def _show(row) -> str:
    """A row for a person to read: a blob (cover art) as its size, not its
    bytes, and a uid by its first eight characters."""
    if row is None:
        return "none"
    return "{" + ", ".join(f"{k}: " + (f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray)) else repr(shown(v)))
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
    # [SPEC-STAR-112]: the items alone, so the console and the gate need not
    # read a report that holds the whole catalogue's decisions.
    with open(os.path.join(out, "items.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"items": d.get("items", []), "propagated": d.get("propagated", {})}, fh, indent=1,
                  sort_keys=True, default=lambda v: f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray)) else str(v))
    L = ["# Star merge report", "",
         "Read it before promoting this output to `data/` [SPEC-STAR-070]."]
    if "nodes" in d:
        L += listener_section(d)
    if "catalogue" in d:
        L += catalogue_section(d["catalogue"])
    if "hub_library" in d:
        L += ["", "# Catalogue half", "",
              f"The hub's own, taken as it stands [SPEC-STAR-085]: `{d['hub_library']['source']}`. "
              f"Integrity **{d['hub_library']['integrity']}**."]
    if "links" in d:
        L += ci.report_lines(d["links"])
    L += items_section(d)
    if d.get("copies"):
        L += copies_section(d["copies"])
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


def items_section(d: dict) -> list[str]:
    """[SPEC-STAR-110]: what needs a verdict, and what was only carried."""
    items = d.get("items", [])
    L = ["", "# Items for a verdict [SPEC-STAR-110]", "",
         "A change the merge would discard. Each takes its default, the most recent change, unless a",
         "verdict names another candidate; the gate wants one for every item [SPEC-STAR-116]."]
    prop = d.get("propagated", {})
    if prop:
        L += ["", "Carried without question, one node having changed the row: "
              + ", ".join(f"`{t}` {n}" for t, n in sorted(prop.items())) + "."]
    if not items:
        return L + ["", "None: nothing is being discarded."]
    kinds = {}
    for x in items:
        kinds.setdefault(x["kind"], []).append(x)
    for kind, xs in sorted(kinds.items()):
        L += ["", f"## {kind}: {len(xs)}", ""]
        for x in xs[:25]:
            col = f" `{x['column']}`" if x.get("column") else ""
            cands = "; ".join(
                f"{c['node']}: " + ("removed" if c["value"] is None else _show(c["value"]) if isinstance(c["value"], dict)
                                    else repr(shown(c["value"]))) + (f" ({c['stamp']})" if c.get("stamp") else "")
                for c in x["candidates"])
            L.append(f"- `{x['id']}` `{x['table']}` {shown(x['key'])}{col}: default **{x['default']}** -- {cands}")
        if len(xs) > 25:
            L.append(f"- ... and {len(xs) - 25} more in report.json")
    return L


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
        L.append(f"- `{x['table']}` {shown(x['key'])}: chose **{x['chose']}** -- " + "; ".join(
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
            L.append(f"  - {shown(x['key'])}: {n} has {_show(x['receiver_value'])}; merged "
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
        h = e.get("held_out")
        if h:
            # [SPEC-STAR-082]: said, never silent -- what the node is not
            # given, and what would give it.
            albums = h["albums"]
            L.append(f"  - **not on {n}, so left out of its copy:** {h['files']} of the hub's file(s), from "
                     + ", ".join(f"*{a}*" for a in albums[:8])
                     + (f" and {len(albums) - 8} more" if len(albums) > 8 else "")
                     + ". Send them from the Export page; the next sync takes them up.")
    return L


def listener_section(d: dict) -> list[str]:
    L = ["", "# Listener half", "",
         "Every row the hub took from one node over another, every tie and every removal.",
         "Read it before promoting this output to `data/` [SPEC-STAR-070].", "",
         "## Inputs", "", "| node | snapshot taken | backups read |", "| :--- | :--- | ---: |"]
    L += [f"| {n['name']} | {n['taken_at']} | {n['backups']} |" for n in d["nodes"]]
    ns = d.get("newest_sent") or {}
    L += ["", f"Removals a node missed [IMPL-UID-042]: read against run {ns['run']}'s merge." if ns.get("run") else
          f"Removals a node missed [IMPL-UID-042]: **NOT CHECKED** -- {ns.get('missing', 'no newest copy')}; "
          "a node that missed a run brings back what that run removed."]
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
    tr = d.get("translations", {})
    if tr:
        L += ["", "## Left out, by number [IMPL-UID-080]", "",
              "A copy last sent from before Stage B, as a phone not yet updated has, names passages by "
              "number. Its rows naming one are left out, or kept without a uid; none is read through a map.", ""]
        for n, v in sorted(tr.items()):
            L.append(f"- {n}: rows naming a passage by number {v.get('by_number', {})}; a retired file read "
                     f"as the hub's {v.get('aliased', {})}")
    L += ["", f"Integrity of the output: **{d['integrity']}**.", "", "## Decisions", ""]
    if not d["decisions"]:
        L.append("None: every table agreed wherever nodes overlapped.")
    for x in d["decisions"]:
        L.append(f"- `{x['table']}` {shown(x['key'])}: {x['what']} `[{x['rule']}]`")
    return L


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "--out":
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        manifest = json.load(fh)
    return run(manifest, argv[2])


def run(manifest: dict, out: str) -> int:
    """Build the merge into `out` from a manifest already in hand, and say what came of it."""
    rep = build(manifest, out)
    checks = {}
    if "integrity" in rep.data:
        checks["listener"] = rep.data["integrity"]
    if "catalogue" in rep.data:
        checks["catalogue"] = rep.data["catalogue"]["integrity"]
    if "hub_library" in rep.data:
        checks["catalogue"] = rep.data["hub_library"]["integrity"]
    conflicts = len(rep.data.get("catalogue", {}).get("conflicts", []))
    print(f"merged into {out}: integrity {checks}, {len(rep.data['decisions'])} listener "
          f"decision(s), {conflicts} catalogue conflict(s), {len(rep.data['items'])} item(s) "
          f"for a verdict -- see report.md")
    fs = rep.data.get("links", {}).get("findings", [])
    if fs:
        by = {}
        for f in fs:
            by[f["effect"]] = by.get(f["effect"], 0) + 1
        # [SPEC-LNK-024]: said, not a stop.
        print(f"links to nothing: {len(fs)} finding(s), {sum(len(f['rows']) for f in fs)} row(s) -- "
              + ", ".join(f"{n} {k}" for k, n in sorted(by.items())) + " [SPEC070]")
    return 0 if all(v == "ok" for v in checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
