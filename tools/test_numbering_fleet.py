#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fleet's cross-node paths, run over catalogues numbered unlike each other
`[SPEC-UID-020]`.

A passage number means nothing outside its own database `[SPEC-DF-035]`, and each
time that rule was broken the result looked right: echo followers played another
song for the same number (2026-11-07), and an id review copied itself onto eight
passages over three syncs `[SPEC-STAR-051]`. A test whose nodes happen to share
numbers cannot see either. So every node here holds the same music under numbers
that are deliberately wrong for the others:

    hub         in the order the music arrived
    shuffled    the same numbers given to other music: the dangerous one, since
                a number read on the wrong node still finds a passage
    distant     numbers no other node uses, so a stray one finds nothing
    partial     the hub's numbering, less one album, plus a passage of its own

    python tools/test_numbering_fleet.py
"""

import json
import os
import random
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_merge  # noqa: E402
import star_patch  # noqa: E402

FAILED = []

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE, path TEXT, duration_ms INTEGER);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT NOT NULL,
    start_ms INTEGER, end_ms INTEGER, boundary_src TEXT);
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT,
    PRIMARY KEY (passage_id, mbid));
"""

# (audio_md5, [(kind, start, end, mbid)]): single tracks, and a whole album in one
# file, which is where the same file holds many passages of a kind.
MUSIC = [(f"md5{n:02d}", [("radio", 0, 200_000 + n, f"rec{n}"), ("album", 0, 205_000 + n, f"rec{n}")]) for n in range(20)]
MUSIC.append(("albumfile", [(kind, s, e, f"cut{i}") for i, (s, e) in enumerate([(0, 100_000), (102_000, 200_000), (203_000, 300_000)])
                            for kind in ("radio", "album")]))


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


def rows_of(music):
    return [(md5, kind, s, e, mbid) for md5, cuts in music for kind, s, e, mbid in cuts]


def build(path, rows, number):
    """A catalogue of `rows`; `number(i)` is the number the i'th passage gets."""
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    files = {}
    for i, (md5, kind, s, e, mbid) in enumerate(rows):
        if md5 not in files:
            files[md5] = number(i) + 1_000_000
            c.execute("INSERT INTO files VALUES (?,?,?,?)", (files[md5], md5, f"/music/{md5}.mp3", 600_000))
        pid = number(i)
        c.execute("INSERT INTO passages VALUES (?,?,?,?,?,?)", (pid, files[md5], kind, s, e, "ingest"))
        c.execute("INSERT INTO passage_recordings VALUES (?,?,1.0,'test')", (pid, mbid))
    c.commit()
    c.close()


def names(path):
    c = sqlite3.connect(path)
    out = {r[0]: tuple(r[1:]) for r in c.execute(
        "SELECT p.passage_id, f.audio_md5, p.kind, p.start_ms FROM passages p JOIN files f USING (file_id)")}
    c.close()
    return out


def fleet(tmp):
    rows = rows_of(MUSIC)
    random.Random(20261107).shuffle(rows)
    hub = os.path.join(tmp, "hub.db")
    build(hub, rows_of(MUSIC), lambda i: i + 1)
    order = list(range(1, len(rows) + 1))
    shuffled = os.path.join(tmp, "shuffled.db")
    build(shuffled, rows, lambda i: order[i])
    distant = os.path.join(tmp, "distant.db")
    build(distant, rows, lambda i: i + 500_000)
    partial_rows = [r for r in rows_of(MUSIC) if r[0] != "md507"] + [("nodeonly", "radio", 0, 90_000, "nodeonly")]
    partial = os.path.join(tmp, "partial.db")
    build(partial, partial_rows, lambda i: i + 1)
    return hub, {"shuffled": shuffled, "distant": distant, "partial": partial}


def test_the_fleet_is_numbered_as_this_test_claims(hub, nodes):
    print("the fleet really is numbered unlike itself")
    h = names(hub)
    s = names(nodes["shuffled"])
    other = sum(1 for n, v in s.items() if n in h and h[n] != v)
    check(other > len(s) // 2, f"most of `shuffled`'s numbers must be other music on the hub, only {other} of {len(s)} are")
    check(not set(names(nodes["distant"])) & set(h), "`distant` must share no number with the hub")


def test_a_node_number_reaches_the_hub_as_the_same_music(hub, nodes):
    print("a node's number, read on the way in, is the same music on the hub [SPEC-STAR-049]")
    h = names(hub)
    for name, path in nodes.items():
        remap, gone = star_merge.passage_remap(path, hub)
        for pid, what in names(path).items():
            if pid in gone:
                check(what[0] not in {v[0] for v in h.values()}, f"{name}: {what} was called gone, and the hub holds it")
                continue
            hub_pid = remap.get(pid, pid)
            check(h.get(hub_pid) == what, f"{name}: {pid} is {what}, read as hub {hub_pid}, {h.get(hub_pid)}")


def test_a_hub_number_goes_back_as_the_same_music(hub, nodes):
    print("a hub number, written into a node's copy, is the same music there, and reads back [SPEC-STAR-051]")
    h = names(hub)
    for name, path in nodes.items():
        n = names(path)
        remap, _ = star_merge.passage_remap(path, hub)
        unmap, claimed = star_merge.passage_unmap(path, hub, remap)
        check(claimed == 0, f"{name}: {claimed} hub numbers were claimed twice")
        for hub_pid, what in h.items():
            mine = unmap.get(hub_pid, hub_pid)
            if what in n.values():
                check(n.get(mine) == what, f"{name}: hub {hub_pid} is {what}, written as {mine}, {n.get(mine)}")
                check(remap.get(mine, mine) == hub_pid, f"{name}: hub {hub_pid} does not read back from {mine}")


def edited(path, out):
    """`path` with one passage retimed and another added, as the hub's edit would be."""
    d = sqlite3.connect(path)
    b = sqlite3.connect(out)
    d.backup(b)
    d.close()
    b.execute("UPDATE passages SET end_ms = end_ms + 4321 WHERE file_id = (SELECT file_id FROM files WHERE audio_md5 = 'md503') AND kind = 'radio'")
    fid = b.execute("SELECT file_id FROM files WHERE audio_md5 = 'md504'").fetchone()[0]
    b.execute("INSERT INTO passages (file_id, kind, start_ms, end_ms, boundary_src) VALUES (?, 'radio', 150000, 190000, 'edit')", (fid,))
    b.commit()
    b.close()


def patch_rows(baseline, target):
    b, t = sqlite3.connect(baseline), sqlite3.connect(target)
    entry = star_patch.natural_entry(b, t, "passages", set())
    b.close()
    t.close()
    return entry


def test_a_patch_names_rows_by_what_they_are(hub, nodes, tmp):
    print("a patch to a node numbered otherwise is one change, not the same music under another name [SPEC-NKP-046]")
    target = os.path.join(tmp, "hub-edited.db")
    edited(hub, target)
    for name in ("shuffled", "distant"):
        entry = patch_rows(nodes[name], target)
        cols = entry["columns"]
        at = {c: cols.index(c) for c in ("kind", "start_ms", "end_ms")}
        changes = [r for r in entry["rows"] if r["was"] and r["now"]]
        adds = [r for r in entry["rows"] if not r["was"]]
        drops = [r for r in entry["rows"] if not r["now"]]
        moved = [r for r in changes if r["key"][0] == "md503"]
        check(len(moved) == 1 and len(changes) == 1, f"{name}: one retimed passage, got {len(changes)} changes")
        if moved:
            check(moved[0]["now"][at["end_ms"]] - moved[0]["was"][at["end_ms"]] == 4321, f"{name}: the change is the retime")
        check(len(adds) == 1 and adds[0]["key"][0] == "md504", f"{name}: one new passage, got {len(adds)}")
        check(not drops, f"{name}: nothing is removed, got {len(drops)}")
        for r in entry["rows"]:
            json.dumps(r)
            check(all(not isinstance(v, int) or v < 400_000 for v in r["key"]),
                  f"{name}: a patch key carries what looks like a local number: {r['key']}")


def test_no_patch_when_only_the_numbers_differ(hub, nodes):
    print("catalogues that differ only in numbers have nothing to send [SPEC-NKP-010]")
    for name in ("shuffled", "distant"):
        check(patch_rows(nodes[name], hub) is None, f"{name}: the same music under other numbers made a patch")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        hub, nodes = fleet(tmp)
        test_the_fleet_is_numbered_as_this_test_claims(hub, nodes)
        test_a_node_number_reaches_the_hub_as_the_same_music(hub, nodes)
        test_a_hub_number_goes_back_as_the_same_music(hub, nodes)
        test_a_patch_names_rows_by_what_they_are(hub, nodes, tmp)
        test_no_patch_when_only_the_numbers_differ(hub, nodes)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("numbering fleet: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
