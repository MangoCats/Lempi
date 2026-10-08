#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for a phone's part in the star sync [REQ-AND-330], [SPEC-MTR-030].

A hub and one enrolled phone, and two sync rounds, through the same stages a
person runs: the phone's upload is merged as a node's listener, its patch is
proven and offered, the phone applies it by its own rule, and a flag it
later clears is removed from the household -- while the tables and rows it
never uploads (occasions, a passage's flag) are left alone.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mesh  # noqa: E402
import star_patch as sp  # noqa: E402
import star_sync as ss  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


LISTENER = """
CREATE TABLE listener_preferences (subject_kind TEXT, subject_id TEXT, rotation REAL, recovery REAL,
    restraint REAL, updated_at TEXT, PRIMARY KEY (subject_kind, subject_id));
CREATE TABLE listener_characteristics (subject_kind TEXT, subject_id TEXT, characteristic TEXT, class TEXT,
    value REAL, updated_at TEXT, PRIMARY KEY (subject_kind, subject_id, characteristic, class));
CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT, flagged_at TEXT, origin TEXT,
    PRIMARY KEY (subject_kind, subject_id));
CREATE TABLE listener_occasions (characteristic TEXT, class TEXT, interp TEXT, label TEXT);
CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, played_at INTEGER, passage_id INTEGER,
    mbid TEXT, heard_ms INTEGER, span_ms INTEGER, selected_by TEXT);
"""


def db(path, *sql, schema=LISTENER):
    c = sqlite3.connect(path)
    c.executescript(schema)
    for s in sql:
        c.execute(s)
    c.commit()
    c.close()


def rows(path, sql):
    c = sqlite3.connect(path)
    r = c.execute(sql).fetchall()
    c.close()
    return r


def upload_of(phone, dest):
    """What the phone's Rust `mesh_sync::snapshot` writes: the shared tables,
    their schema as the phone has it, and no passage row."""
    if os.path.exists(dest):
        os.remove(dest)
    c = sqlite3.connect(dest)
    c.execute("ATTACH DATABASE ? AS src", (phone,))
    for t in mesh.SHARED:
        c.execute(c.execute("SELECT sql FROM src.sqlite_master WHERE name=?", (t,)).fetchone()[0])
        c.execute(f"INSERT INTO main.{t} SELECT * FROM src.{t} WHERE subject_kind IS NOT 'passage'")
    c.commit()
    c.execute("DETACH DATABASE src")
    c.close()
    return open(dest, "rb").read()


def main() -> int:
    tmp = tempfile.mkdtemp()
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    hub_l, hub_c = os.path.join(data, "listener.db"), os.path.join(data, "library.db")
    db(hub_l, "INSERT INTO listener_preferences VALUES ('recording','shared',1,1,0,'2026-09-01 00:00:00')",
       "INSERT INTO listener_flags VALUES ('recording','keep','2026-08-01 00:00:00',NULL)",
       "INSERT INTO listener_flags VALUES ('passage','17','2026-08-01 00:00:00',NULL)",
       "INSERT INTO listener_occasions VALUES ('user.xmas','xmasy','linear','Christmas')")
    db(hub_c, schema="CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT);")
    mdir = mesh.mesh_dir(hub_c)
    mesh.init(mdir, "Test", "desktop")
    key_p, cert_p = os.path.join(tmp, "phone.key"), os.path.join(tmp, "phone.pem")
    mesh.make_identity(key_p, cert_p, "lempi-node")
    cert = mesh.cert_from_pem(open(cert_p).read())
    r = mesh.roster(mdir)
    r["members"].append(mesh.member_entry(cert, "phone", "Moto G"))
    mesh.publish(mdir, r)
    fp = mesh.fingerprint(cert)
    node = f"phone-{fp[:8]}"

    phone = os.path.join(tmp, "phone-listener.db")
    db(phone, "INSERT INTO listener_preferences VALUES ('recording','mine',3,1,0,'2026-09-20 00:00:00')",
       "INSERT INTO listener_preferences VALUES ('recording','shared',9,9,0,'2026-09-25 00:00:00')",
       "INSERT INTO listener_flags VALUES ('recording','phoneflag','2026-09-20 00:00:00',NULL)",
       "INSERT INTO listener_flags VALUES ('passage','3','2026-09-20 00:00:00',NULL)",
       "INSERT INTO listener_play_history VALUES (1,1,3,'mine',1000,1000,'auto')")

    # Refusals at the door.
    now_ms = int(time.time() * 1000)
    try:
        mesh.store_upload(mdir, fp, upload_of(phone, os.path.join(tmp, "up.db")), now_ms + 600_000)
        check(False, "a clock ten minutes out is refused")
    except ValueError as e:
        check("+600 s" in str(e), f"a clock ten minutes out is refused, saying by how much: {e}")
    try:
        mesh.store_upload(mdir, fp, open(phone, "rb").read(), now_ms)
        check(False, "an upload holding plays is refused")
    except ValueError as e:
        check("not shared" in str(e), f"an upload holding plays is refused: {e}")

    # The hub takes part in every run since `fb766de` [SPEC-STAR-134], so a
    # commit reaches `hub_apply`: it backs the hub's pair up first, and it
    # refuses while it can see a console or a player on this machine. The
    # first needs somewhere to write; the second is the machine running the
    # test, not the one under test, so it is stubbed as
    # `test_star_sync_flow.py` stubs it.
    plan = {"hub": {"name": "desktop", "listener": hub_l, "library": hub_c},
            "runs": os.path.join(tmp, "runs"), "prune": {"prefix": "pre-sync-", "keep": 3},
            "backups": {"dir": os.path.join(tmp, "backups"), "keep_daily": 14, "keep_monthly": 12},
            "nodes": {}}
    os.makedirs(plan["runs"])
    os.makedirs(plan["backups"]["dir"])
    with open(os.path.join(plan["runs"], "state.json"), "w") as fh:
        json.dump({"hub": {}, "nodes": {}}, fh)
    stamps = iter(["20260927T0001Z", "20260927T0002Z"])
    ss.stamp = lambda t=None: next(stamps)
    ss.console_running = lambda excuse=None: []

    def round_():
        rc = ss.snapshot(plan)
        run = sorted(os.path.join(plan["runs"], d) for d in os.listdir(plan["runs"]) if d.startswith("2026"))[-1]
        ss.merge(plan, run)
        ss.patch(plan, run)
        ss.distribute(plan, run, [node], commit=False)     # [SPEC-STAR-116]: rehearsed first
        ss.distribute(plan, run, [node], commit=True)
        return rc, run

    # Round 1.
    mesh.store_upload(mdir, fp, upload_of(phone, os.path.join(tmp, "up.db")), int(time.time() * 1000))
    rc, run = round_()
    merged = os.path.join(run, "merge", "listener.db")
    prefs = dict(((k, i), v) for k, i, v in rows(merged, "SELECT subject_kind, subject_id, rotation FROM listener_preferences"))
    check(rc == 0 and prefs.get(("recording", "mine")) == 3 and prefs.get(("recording", "shared")) == 9,
          f"the phone's edits reach the household, the newer winning: {prefs}")
    flags = {i for _, i in rows(merged, "SELECT subject_kind, subject_id FROM listener_flags")}
    check(flags == {"keep", "phoneflag", "17"}, f"its flag joins; its passage flag stays home: {flags}")
    check(not rows(merged, "SELECT count(*) FROM listener_play_history WHERE mbid='mine'")[0][0],
          "and its plays never leave it")
    summary = open(os.path.join(run, "SUMMARY.md"), encoding="utf-8").read()
    check(f"## {node}" in summary and "NOT PROVEN" not in summary, "its patch is listed, and proven")
    o = mesh.outbox(mdir, fp)
    check(o is not None and o["run"] == os.path.basename(run), "and offered in its outbox")
    names = {e["name"] for e in o["patch"]["tables"]}
    check(names <= set(mesh.SHARED), f"the offer holds shared tables only: {names}")
    counts = sp.apply_member(phone, o["patch"], mesh.SHARED)
    check(rows(phone, "SELECT count(*) FROM listener_flags WHERE subject_id='keep'")[0][0] == 1,
          f"applied on the phone, the household's flag arrives: {counts}")
    check(rows(phone, "SELECT count(*) FROM listener_flags WHERE subject_id='3'")[0][0] == 1
          and rows(phone, "SELECT count(*) FROM listener_flags WHERE subject_id='17'")[0][0] == 0,
          "the phone's passage flag is untouched, and the hub's never sent")
    check(mesh.outbox_applied(mdir, fp, o["run"], counts) and mesh.outbox(mdir, fp) is None,
          "the phone's word that it applied clears the outbox")

    # Round 2: the phone uploads what it now holds, clears 'keep', and uploads again.
    time.sleep(1.1)
    mesh.store_upload(mdir, fp, upload_of(phone, os.path.join(tmp, "up.db")), int(time.time() * 1000))
    c = sqlite3.connect(phone)
    c.execute("DELETE FROM listener_flags WHERE subject_id='keep'")
    c.commit()
    c.close()
    time.sleep(1.1)
    mesh.store_upload(mdir, fp, upload_of(phone, os.path.join(tmp, "up.db")), int(time.time() * 1000))
    rc, run = round_()
    merged = os.path.join(run, "merge", "listener.db")
    flags = {i for _, i in rows(merged, "SELECT subject_kind, subject_id FROM listener_flags")}
    check("keep" not in flags, f"a flag the phone cleared is removed from the household: {flags}")
    check("17" in flags, "a passage flag the phone never holds is not read as removed by it")
    check(rows(merged, "SELECT label FROM listener_occasions") == [("Christmas",)],
          "nor are the occasions it never uploads")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("mesh_sync: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
