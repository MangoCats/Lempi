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

Each passage carries the uid its birth key gives it, the same on every node,
and every crossing names a passage by it `[IMPL-UID-084]`: a merge with a
removal, a passage only a node holds, a boundary edit, a remote unflag and a
member patch, each over `shuffled`, where a stray number still finds a
passage. A bundle carries no number at all; its import over unlike numbering
is `bundle.rs`'s `a_bundle_lands_under_the_nodes_own_number_by_its_uid`, and
echo's is `echo_client.rs`'s `a_followers_number_for_a_song_is_its_own_not_the_leaders`.

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
import console  # noqa: E402
import lempi_control  # noqa: E402
import mesh  # noqa: E402
import passage_uid  # noqa: E402
import star_merge  # noqa: E402
import star_patch  # noqa: E402
import test_natural_patch  # noqa: E402  -- its reference applier
import test_star_merge as tm  # noqa: E402  -- its listener schema

FAILED = []

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE, path TEXT, duration_ms INTEGER);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT NOT NULL,
    start_ms INTEGER, end_ms INTEGER, boundary_src TEXT, passage_uid TEXT UNIQUE);
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
        # Each passage the uid its birth key gives it, the same on every node [IMPL-UID-030].
        c.execute("INSERT INTO passages VALUES (?,?,?,?,?,?,?)",
                  (pid, files[md5], kind, s, e, "ingest", passage_uid.passage_uid(md5, kind, s, e)))
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


def edited(path, out):
    """`path` with one passage retimed, keeping its uid, and another added with its own, as the
    hub's edit would be."""
    d = sqlite3.connect(path)
    b = sqlite3.connect(out)
    d.backup(b)
    d.close()
    b.execute("UPDATE passages SET end_ms = end_ms + 4321 WHERE file_id = (SELECT file_id FROM files WHERE audio_md5 = 'md503') AND kind = 'radio'")
    fid = b.execute("SELECT file_id FROM files WHERE audio_md5 = 'md504'").fetchone()[0]
    b.execute("INSERT INTO passages (file_id, kind, start_ms, end_ms, boundary_src, passage_uid) "
              "VALUES (?, 'radio', 150000, 190000, 'edit', ?)", (fid, passage_uid.passage_uid("md504", "radio", 150000, 190000)))
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
        at = {c: cols.index(c) for c in ("file_id", "kind", "start_ms", "end_ms")}
        changes = [r for r in entry["rows"] if r["was"] and r["now"]]
        adds = [r for r in entry["rows"] if not r["was"]]
        drops = [r for r in entry["rows"] if not r["now"]]
        moved = [r for r in changes if r["now"][at["file_id"]] == "md503"]
        check(len(moved) == 1 and len(changes) == 1, f"{name}: one retimed passage, got {len(changes)} changes")
        if moved:
            check(moved[0]["now"][at["end_ms"]] - moved[0]["was"][at["end_ms"]] == 4321, f"{name}: the change is the retime")
            check(entry["key"] == ["passage_uid"] and len(moved[0]["key"][0]) == 32,
                  f"{name}: under the uid it was born with: {moved[0]['key']}")
        check(len(adds) == 1 and adds[0]["now"][at["file_id"]] == "md504", f"{name}: one new passage, got {len(adds)}")
        check(not drops, f"{name}: nothing is removed, got {len(drops)}")
        for r in entry["rows"]:
            json.dumps(r)
            check(all(not isinstance(v, int) or v < 400_000 for v in r["key"]),
                  f"{name}: a patch key carries what looks like a local number: {r['key']}")


def test_no_patch_when_only_the_numbers_differ(hub, nodes):
    print("catalogues that differ only in numbers have nothing to send [SPEC-NKP-010]")
    for name in ("shuffled", "distant"):
        check(patch_rows(nodes[name], hub) is None, f"{name}: the same music under other numbers made a patch")


def uids(path):
    """passage number -> (uid, (md5, kind, start))."""
    c = sqlite3.connect(path)
    out = {r[0]: (r[1], tuple(r[2:])) for r in c.execute(
        "SELECT p.passage_id, p.passage_uid, f.audio_md5, p.kind, p.start_ms FROM passages p JOIN files f USING (file_id)")}
    c.close()
    return out


def crossed(hub, node):
    """A passage X whose number on `node` is, on the hub, other music Z -- the case a
    stray number gets wrong without a word. Returns (X's uid, Z's uid, Y's uid) with
    Y a third passage, and checks the premise holds."""
    h, n = uids(hub), uids(node)
    for pid, (uid, what) in sorted(n.items()):
        if pid in h and h[pid][0] != uid:
            z = h[pid][0]
            y = next(u for u, _ in h.values() if u not in (uid, z))
            return uid, z, y
    raise AssertionError("the fleet holds no number that is other music on the hub")


FLAG = "INSERT INTO listener_flags VALUES ('passage','{}','2026-08-01 00:00:00',NULL)"


def flags(path, kind="passage"):
    return sorted(r[0] for r in tm_rows(path, f"SELECT subject_id FROM listener_flags WHERE subject_kind = '{kind}'"))


def tm_rows(path, sql):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_a_merge_with_a_removal(hub, nodes, tmp):
    print("a merge with a removal: shuffled clears its flag on X, and only X's goes -- never Z's, "
          "the hub's passage under the number shuffled gives X [IMPL-UID-042]")
    x, z, y = crossed(hub, nodes["shuffled"])
    dl, sl = os.path.join(tmp, "m-hub.lis"), os.path.join(tmp, "m-shuffled.lis")
    tm.db(dl, *(FLAG.format(u) for u in (x, y, z)))
    tm.db(sl, *(FLAG.format(u) for u in (y, z)))                       # X cleared since its backup
    bk = tm.backup_of(tmp, "m-shuffled", 1789000000, *(FLAG.format(u) for u in (x, y, z)))
    out = os.path.join(tmp, "m-out")
    rep = star_merge.build({"hub_state_from": "desktop",
                            "nodes": [dict(name="desktop", listener=dl),
                                      dict(name="shuffled", listener=sl, backups=bk)]}, out)
    check(flags(os.path.join(out, "listener.db")) == sorted([y, z]), "the merge removes X's flag and keeps Z's and Y's")
    check(flags(os.path.join(out, "nodes", "shuffled", "listener.db")) == sorted([y, z]), "and so does shuffled's copy")
    gone = [d["key"] for d in rep.data["decisions"] if d["rule"] == "SPEC-STAR-050"]
    check(gone == [["passage", x]], f"the removal is reported as X's, by its uid: {gone}")


def test_a_passage_only_a_node_holds(hub, nodes, tmp):
    print("a passage only partial holds: its flag and its play keep its uid, and name nothing of the hub's")
    own = next(u for u, what in uids(nodes["partial"]).values() if what[0] == "nodeonly")
    check(own not in {u for u, _ in uids(hub).values()}, "the premise: the hub does not hold it")
    dl, pl = os.path.join(tmp, "o-hub.lis"), os.path.join(tmp, "o-partial.lis")
    tm.db(dl)
    tm.db(pl, FLAG.format(own), f"INSERT INTO listener_play_history VALUES (1, 1789000000, '{own}', 'nodeonly', 90000, 90000, 'auto')")
    out = os.path.join(tmp, "o-out")
    star_merge.build({"hub_state_from": "desktop",
                      "nodes": [dict(name="desktop", listener=dl), dict(name="partial", listener=pl)]}, out)
    check(flags(os.path.join(out, "listener.db")) == [own], "its flag reaches the hub under its own uid")
    mine = os.path.join(out, "nodes", "partial", "listener.db")
    check(flags(mine) == [own], "and stays on partial under it")
    check(tm_rows(mine, "SELECT passage_uid FROM listener_play_history") == [(own,)], "its play is its own, unchanged")
    check(tm_rows(os.path.join(out, "listener.db"), "SELECT count(*) FROM listener_play_history") == [(0,)],
          "and no play is the hub's")


def test_a_boundary_edit(hub, nodes, tmp):
    print("a boundary edit on the hub reaches shuffled as one change to its own row, by uid, and "
          "nothing under the hub's number for it there [SPEC-NKP-045]")
    target = os.path.join(tmp, "b-hub-edited.db")
    edited(hub, target)
    h = uids(hub)
    md503 = [(pid, u) for pid, (u, what) in h.items() if what[0] == "md503" and what[1] == "radio"]
    hub_pid, uid = md503[0]
    node = os.path.join(tmp, "b-shuffled.db")
    src = sqlite3.connect(nodes["shuffled"])
    dst = sqlite3.connect(node)
    src.backup(dst)
    src.close()
    dst.close()
    before = uids(node)
    patch = star_patch.make_values(nodes["shuffled"], target, tables=["files", "passages", "passage_recordings"],
                                   natural=True)
    test_natural_patch.apply_natural(node, patch)
    after = uids(node)
    mine = next(pid for pid, (u, _) in before.items() if u == uid)
    check(mine != hub_pid and before.get(hub_pid, (None,))[0] != uid,
          "the premise: shuffled numbers X otherwise, and the hub's number for X is other music there")
    end = lambda path, pid: tm_rows(path, f"SELECT end_ms FROM passages WHERE passage_id = {pid}")[0][0]
    check(end(node, mine) - end(nodes["shuffled"], mine) == 4321 and after[mine][0] == uid,
          "X's own row on shuffled has the new end and keeps its uid")
    check(after.get(hub_pid) == before.get(hub_pid), "the music under the hub's number for X is untouched there")
    added = set(after) - set(before)
    check(len(added) == 1 and after[added.pop()][1][0] == "md504", "and the new passage arrives under a number of shuffled's own")


def test_a_remote_unflag(hub, nodes, tmp):
    print("a remote unflag clears X's flag on shuffled by its uid, and not the flag under the hub's number for X")
    x, z, y = crossed(hub, nodes["shuffled"])
    remote = os.path.join(tmp, "u-shuffled.lis")
    tm.db(remote, *(FLAG.format(u) for u in (x, z)))
    conn = sqlite3.connect(hub)
    conn.row_factory = sqlite3.Row
    pid = conn.execute("SELECT passage_id FROM passages WHERE passage_uid = ?", (x,)).fetchone()[0]
    subjects = console.passage_flag_subjects(conn, pid)
    conn.close()
    sent = []

    def set_remote(where, port, kind, sid, flagged, timeout=8.0):
        sent.append((kind, sid))
        c = sqlite3.connect(remote)
        c.execute("DELETE FROM listener_flags WHERE subject_kind = ? AND subject_id = ?", (kind, sid))
        c.commit()
        c.close()
        return True

    real = (lempi_control._lempi_set_flag, lempi_control._remote_set_flag)
    lempi_control._lempi_set_flag = lambda *a, **k: True
    lempi_control._remote_set_flag = set_remote
    try:
        r = lempi_control.unflag_everywhere(subjects, "pi@speaker-a:/srv/library/library.db",
                                            {"reachable": True, "remote_held": True, "remote_mbids": []})
    finally:
        lempi_control._lempi_set_flag, lempi_control._remote_set_flag = real
    check(("passage", x) in sent and not any(sid.isdigit() for _, sid in sent), f"the remote is sent X's uid, no number: {sent}")
    check(flags(remote) == [z], f"X's flag goes from the remote and Z's stays: {flags(remote)}")
    check(r["remote"]["ok"], f"{r}")


def test_a_member_patch(hub, nodes, tmp):
    print("a member patch to a phone, which numbers its own catalogue and has not updated, carries no passage row")
    x, _, _ = crossed(hub, nodes["shuffled"])
    base, target = os.path.join(tmp, "p-phone.lis"), os.path.join(tmp, "p-merged.lis")
    tm.db(base)
    tm.db(target, FLAG.format(x), "INSERT INTO listener_flags VALUES ('recording','rec3','2026-08-01 00:00:00',NULL)")
    patch = star_patch.make_member(base, target, mesh.SHARED)
    sent = [r["key"] for e in patch["tables"] for r in e["rows"]]
    check(sent == [["recording", "rec3"]], f"the recording's flag goes, X's is held back both ways: {sent}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        hub, nodes = fleet(tmp)
        test_the_fleet_is_numbered_as_this_test_claims(hub, nodes)
        test_a_patch_names_rows_by_what_they_are(hub, nodes, tmp)
        test_no_patch_when_only_the_numbers_differ(hub, nodes)
        test_a_merge_with_a_removal(hub, nodes, tmp)
        test_a_passage_only_a_node_holds(hub, nodes, tmp)
        test_a_boundary_edit(hub, nodes, tmp)
        test_a_remote_unflag(hub, nodes, tmp)
        test_a_member_patch(hub, nodes, tmp)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("numbering fleet: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
