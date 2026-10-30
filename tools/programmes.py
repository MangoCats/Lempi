#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hub's library of programmes, and which node runs which [SPEC062].

A programme is a named, complete day: its time slots and their seeds
[SPEC-PGM-010]. The hub keeps each under its name with the versions it has had
[SPEC-PGM-020], assigns one to a node [SPEC-PGM-040], and hands it over: to a
phone over the members' channel when it asks [SPEC-PGM-300], and to a player,
which has no client for that channel, signed, on its own port, as the roster
goes [SPEC-PGM-310]. Either way the node asks a person before anything
replaces what it runs [SPEC-PGM-050].

    python tools/programmes.py data/library.db list
    python tools/programmes.py data/library.db add-from-listener data/listener.db [--name WKMP]
    python tools/programmes.py data/library.db assign <member> <programme>
    python tools/programmes.py data/library.db unassign <member>
    python tools/programmes.py data/library.db push [<member>]

`assign` hands a player its programme at once; `push` hands it to every player
not yet running or asked about its current version (the console does so at
start and hourly), or to one member, always.

`<member>` is a name the mesh's roster holds, or the start of a fingerprint.

**Kept in Vipunen's sidecar**, `<library>.console.db` [SPEC-PGM-210], the
file the console already keeps its jobs in: the library is the hub's, not
any one listener's, and star sync never reads the sidecar [SPEC-PGM-220].

**The fingerprint is the player's**, computed here without sharing its code
[SPEC-PGM-110]: SHA-256 over the slots in start-time order (slots chosen
only by hand last, by name), each `{"name", "seeds", "start"}` with its seeds
as a sorted set [SPEC-PGM-115], written as Python's sorted, compact JSON.
`lempi-core`'s `programme::canonical` writes the same text, and both are
tested against one example.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mesh as meshmod  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS hub_programmes (
    programme_id INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    name_key     TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hub_programme_versions (
    programme_id INTEGER NOT NULL REFERENCES hub_programmes(programme_id),
    version      INTEGER NOT NULL,
    fingerprint  TEXT NOT NULL,
    slots        TEXT NOT NULL,
    made_at      TEXT NOT NULL,
    origin       TEXT NOT NULL,
    PRIMARY KEY (programme_id, version),
    UNIQUE (programme_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS hub_programme_assignments (
    member           TEXT PRIMARY KEY,
    programme_id     INTEGER NOT NULL REFERENCES hub_programmes(programme_id),
    assigned_at      TEXT NOT NULL,
    sent_version     INTEGER,
    sent_at          TEXT,
    runs_name        TEXT,
    runs_fingerprint TEXT,
    runs_source      TEXT,
    runs_version     INTEGER,
    waiting          TEXT,
    reported_at      TEXT
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def sidecar_for(db: str) -> str:
    """Vipunen's sidecar beside the library, named after it, as `console.py`
    names it."""
    return os.path.splitext(os.path.abspath(db))[0] + ".console.db"


def connect(db: str) -> sqlite3.Connection:
    c = sqlite3.connect(sidecar_for(db), timeout=10)
    c.executescript(DDL)
    return c


def name_key(name: str) -> str:
    """One programme whatever its case or surrounding space [SPEC-PGM-100]."""
    return name.strip().casefold()


def normal_start(t):
    """`HH:MM` from `9:05` or `09:05`, as the player reads a start; `None` for
    a slot chosen only by hand, or a value that is not a time of day."""
    if t is None or not str(t).strip():
        return None
    try:
        h, m = (int(x) for x in str(t).strip().split(":"))
    except ValueError:
        return None
    return f"{h:02d}:{m:02d}" if 0 <= h < 24 and 0 <= m < 60 else None


def tidy(slots: list) -> list:
    """Slots as the player would hold them: names trimmed, starts normal,
    seeds without blanks or repeats."""
    out = []
    for s in slots:
        seeds = []
        for m in s.get("seeds") or []:
            m = str(m).strip()
            if m and m not in seeds:
                seeds.append(m)
        out.append({"name": str(s["name"]).strip(), "start": normal_start(s.get("start")), "seeds": seeds})
    return out


def canonical(slots: list) -> str:
    """The one canonical form of a programme's content [SPEC-PGM-110]."""
    ordered = sorted(tidy(slots), key=lambda s: (s["start"] is None, s["start"] or "", s["name"].lower()))
    return json.dumps([{"name": s["name"], "seeds": sorted(set(s["seeds"])), "start": s["start"]}
                       for s in ordered], sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(slots: list) -> str:
    return hashlib.sha256(canonical(slots).encode("utf-8")).hexdigest()


def store(c: sqlite3.Connection, name: str, slots: list, origin: str) -> dict:
    """Keep `slots` as a version of the programme called `name`, new or not.

    A version is content the hub has held [SPEC-PGM-120]: the same content
    again is the version it already is, wherever it came from."""
    name = name.strip()
    if not name:
        raise ValueError("a programme needs a name")
    slots = tidy(slots)
    if not slots:
        raise ValueError(f"{name} has no time slots")
    fp = fingerprint(slots)
    row = c.execute("SELECT programme_id, name FROM hub_programmes WHERE name_key = ?",
                    (name_key(name),)).fetchone()
    if row is None:
        pid = c.execute("INSERT INTO hub_programmes (name, name_key, created_at) VALUES (?,?,?)",
                        (name, name_key(name), now())).lastrowid
        shown = name
    else:
        pid, shown = row
    held = c.execute("SELECT version FROM hub_programme_versions WHERE programme_id = ? AND fingerprint = ?",
                     (pid, fp)).fetchone()
    if held:
        c.commit()
        return {"programme": shown, "version": held[0], "fingerprint": fp, "new": False}
    v = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM hub_programme_versions WHERE programme_id = ?",
                  (pid,)).fetchone()[0]
    c.execute("INSERT INTO hub_programme_versions VALUES (?,?,?,?,?,?)",
              (pid, v, fp, json.dumps(slots, ensure_ascii=False), now(), origin))
    c.commit()
    return {"programme": shown, "version": v, "fingerprint": fp, "new": True}


def read_listener(listener: str) -> tuple[str | None, list]:
    """The programme a listener file runs: its recorded name, if any, and its
    slots with their seeds."""
    uri = "file:" + os.path.abspath(listener).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    c = sqlite3.connect(uri, uri=True)
    try:
        slots = {pid: {"name": n, "start": t, "seeds": []}
                 for pid, n, t in c.execute("SELECT program_id, name, start_time FROM listener_programs")}
        for pid, m in c.execute("SELECT program_id, mbid FROM listener_program_seeds ORDER BY program_id, position"):
            if pid in slots:
                slots[pid]["seeds"].append(m)
        try:
            row = c.execute("SELECT name FROM listener_programme WHERE id = 1").fetchone()
        except sqlite3.OperationalError:
            row = None
        return (row[0] if row else None), list(slots.values())
    finally:
        c.close()


def latest(c: sqlite3.Connection, pid: int) -> tuple | None:
    return c.execute("SELECT version, fingerprint, slots FROM hub_programme_versions WHERE programme_id = ? "
                     "ORDER BY version DESC LIMIT 1", (pid,)).fetchone()


def assign(c: sqlite3.Connection, member: str, name: str) -> dict:
    """Assign the programme `name` to the member with fingerprint `member`
    [SPEC-PGM-040]. Its current version is what the member is handed next."""
    row = c.execute("SELECT programme_id, name FROM hub_programmes WHERE name_key = ?", (name_key(name),)).fetchone()
    if row is None:
        raise ValueError(f"the hub has no programme called {name!r}")
    c.execute("INSERT INTO hub_programme_assignments (member, programme_id, assigned_at) VALUES (?,?,?) "
              "ON CONFLICT(member) DO UPDATE SET programme_id = excluded.programme_id, "
              "assigned_at = excluded.assigned_at, sent_version = NULL, sent_at = NULL",
              (member, row[0], now()))
    c.commit()
    return {"member": member, "programme": row[1], "version": latest(c, row[0])[0]}


def unassign(c: sqlite3.Connection, member: str) -> bool:
    n = c.execute("DELETE FROM hub_programme_assignments WHERE member = ?", (member,)).rowcount
    c.commit()
    return n > 0


def for_member(c: sqlite3.Connection, member: str) -> dict | None:
    """What `GET /member/programme` answers [SPEC-PGM-300]: the current
    version of the programme assigned to `member`, or `None`."""
    row = c.execute("SELECT a.programme_id, p.name FROM hub_programme_assignments a "
                    "JOIN hub_programmes p USING (programme_id) WHERE a.member = ?", (member,)).fetchone()
    if row is None:
        return None
    v, fp, slots = latest(c, row[0])
    c.execute("UPDATE hub_programme_assignments SET sent_version = ?, sent_at = ? WHERE member = ?",
              (v, now(), member))
    c.commit()
    return {"name": row[1], "version": v, "fingerprint": fp, "slots": json.loads(slots)}


def record_state(c: sqlite3.Connection, member: str, body: dict) -> bool:
    """What `POST /member/programme/state` says: the programme the member runs
    now, and what of the hub's waits there for an answer or was kept over."""
    waiting = body.get("waiting")
    n = c.execute(
        "UPDATE hub_programme_assignments SET runs_name = ?, runs_fingerprint = ?, runs_source = ?, "
        "runs_version = ?, waiting = ?, reported_at = ? WHERE member = ?",
        (body.get("name"), body.get("fingerprint"), body.get("source"), body.get("hub_version"),
         json.dumps(waiting) if waiting else None, now(), member)).rowcount
    c.commit()
    return n > 0


# What a player said it did with what it was handed, in the phone's words (`Programmes.kt`).
OUTCOMES = {
    "asking": "asked whether to replace its programme with {name} version {version}",
    "already_runs": "runs {name} version {version}",
    "kept_before": "keeps its own over {name} version {version}, as answered before",
}


def hand_over(c: sqlite3.Connection, mdir: str, member: str, player: dict) -> str:
    """[SPEC-PGM-310] for a player, which has no client for the members'
    channel: its assigned programme, signed by the mesh key, to its own port,
    as the roster goes. The player only holds it until a person answers
    there [SPEC-PGM-050]; its signed answer says what it runs and what waits,
    and is recorded as a phone's report is. A sentence saying what happened."""
    p = for_member(c, member)
    prog = None if p is None else {"name": p["name"], "version": p["version"], "slots": p["slots"]}
    got = meshmod.signed_step(mdir, player, "/mesh/programme", "programme", {"programme": prog}, timeout=20.0)
    cur = got.get("current") or {}
    record_state(c, member, {"name": cur.get("name"), "fingerprint": cur.get("fingerprint"),
                             "source": cur.get("source"), "hub_version": cur.get("hub_version"),
                             "waiting": got.get("waiting")})
    if p is None:
        return "assigned nothing; it runs " + (cur.get("name") or "an unnamed programme")
    return OUTCOMES.get(got.get("outcome"), "answered {outcome!r} for {name} version {version}").format(
        outcome=got.get("outcome"), **p)


def push(c: sqlite3.Connection, mdir: str, only: str | None = None) -> dict:
    """Hand each player its assigned programme, unless it already runs the
    current version or has been asked about it -- or `only` that member,
    always, as an assignment does. A player not found on the network is said
    so, and is handed it at the next push. By name, a sentence each."""
    players = {m["fingerprint"]: m for m in meshmod.roster(mdir)["members"] if m["role"] == "player"}
    due = []
    for member, pid, runs_fp, waiting in c.execute(
            "SELECT member, programme_id, runs_fingerprint, waiting FROM hub_programme_assignments").fetchall():
        if member not in players or (only and member != only):
            continue
        w = json.loads(waiting) if waiting else {}
        if not only and latest(c, pid)[1] in (runs_fp, w.get("fingerprint")):
            continue
        due.append(member)
    if not due:
        return {}
    found = meshmod.sync_players(mdir)
    out = {}
    for member in due:
        name = players[member].get("name") or meshmod.show_fp(member, True)
        if member not in found:
            out[name] = "not found on the network: handed it at the next push"
            continue
        try:
            out[name] = hand_over(c, mdir, member, found[member])
        except (ValueError, OSError) as e:
            # A player older than the hand-over answers the step it does not know as `unknown`.
            out[name] = ("its player is older than the hand-over: a current build takes it" if str(e) == "unknown"
                         else f"not handed over: {e}")
    return out


def push_for(db: str, mdir: str, only: str | None = None) -> dict:
    """`push` on a connection of its own, for a thread or the command line."""
    if not meshmod.initialised(mdir):
        return {}
    c = connect(db)
    try:
        return push(c, mdir, only)
    finally:
        c.close()


def standing(c: sqlite3.Connection, mdir: str | None = None) -> dict:
    """The library and its assignments, for a person [SPEC-PGM-440]: each
    programme with its versions, and each assigned node with how what it
    runs stands against what it was assigned."""
    programmes = []
    for pid, name in c.execute("SELECT programme_id, name FROM hub_programmes ORDER BY name_key").fetchall():
        versions = [{"version": v, "fingerprint": fp, "made_at": t, "origin": o, "slots": len(json.loads(s))}
                    for v, fp, s, t, o in c.execute(
                        "SELECT version, fingerprint, slots, made_at, origin FROM hub_programme_versions "
                        "WHERE programme_id = ? ORDER BY version", (pid,))]
        programmes.append({"name": name, "versions": versions})
    nodes = []
    for (member, pid, name, assigned_at, sent_v, sent_at, runs_fp, runs_name, runs_src, runs_v, waiting,
         reported_at) in c.execute(
            "SELECT a.member, a.programme_id, p.name, a.assigned_at, a.sent_version, a.sent_at, a.runs_fingerprint, "
            "a.runs_name, a.runs_source, a.runs_version, a.waiting, a.reported_at "
            "FROM hub_programme_assignments a JOIN hub_programmes p USING (programme_id)").fetchall():
        cur_v, cur_fp, _ = latest(c, pid)
        w = json.loads(waiting) if waiting else None
        if runs_fp is None:
            state = "not heard from yet"
        elif runs_fp == cur_fp:
            state = f"runs version {cur_v}, the current one"
        elif w and not w.get("kept"):
            state = f"asked about version {w.get('hub_version')}; no answer yet"
        elif w and w.get("kept"):
            state = f"kept its own over version {w.get('hub_version')}"
        elif runs_src == "hub" and runs_v:
            state = f"runs version {runs_v}, an older one"
        else:
            state = "runs its own programme, edited on the node"
        m = meshmod.member(mdir, member) if mdir else None
        nodes.append({"member": member, "node": (m or {}).get("name") or meshmod.show_fp(member, True),
                      "programme": name, "current_version": cur_v, "sent_version": sent_v, "sent_at": sent_at,
                      "runs": runs_name, "state": state, "reported_at": reported_at})
    return {"programmes": programmes, "nodes": nodes}


def find_member(mdir: str, who: str) -> dict:
    """A roster member by its name, or by the start of its fingerprint; one,
    or a refusal that says why."""
    ms = meshmod.roster(mdir)["members"]
    hits = [m for m in ms if (m.get("name") or "").strip().casefold() == who.strip().casefold()]
    if not hits:
        hits = [m for m in ms if m["fingerprint"].startswith(who.lower().replace(":", ""))]
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} members match {who!r}; give a fingerprint's start to choose one")
    return hits[0]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db", help="the hub's library, beside which the sidecar lies")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add-from-listener", help="keep the programme a listener file runs as a version")
    a.add_argument("listener")
    a.add_argument("--name", help="its name; default: the name the listener file records")
    b = sub.add_parser("assign")
    b.add_argument("member")
    b.add_argument("programme")
    d = sub.add_parser("unassign")
    d.add_argument("member")
    e = sub.add_parser("push", help="hand each player its assigned programme [SPEC-PGM-310]")
    e.add_argument("member", nargs="?")
    args = ap.parse_args(argv)
    mdir = meshmod.mesh_dir(args.db)
    c = connect(args.db)
    # Where this writes, said before it does [GDE-DEP-060].
    print(f"programmes: sidecar {sidecar_for(args.db)}")
    try:
        if args.cmd == "add-from-listener":
            recorded, slots = read_listener(args.listener)
            name = args.name or recorded
            if not name:
                print(f"{args.listener} runs an unnamed programme; give it one with --name", file=sys.stderr)
                return 1
            out = store(c, name, slots, f"listener file {os.path.abspath(args.listener)}")
            print(f"{out['programme']} version {out['version']}"
                  f"{'' if out['new'] else ' (already held: the same content)'}, {len(slots)} time slot(s), "
                  f"fingerprint {out['fingerprint'][:12]}")
            return 0
        if args.cmd in ("assign", "unassign", "push"):
            if not meshmod.initialised(mdir):
                print(f"no mesh in {mdir}", file=sys.stderr)
                return 1
            m = find_member(mdir, args.member) if args.member else None
            if args.cmd == "unassign":
                print(f"{m.get('name') or m['fingerprint'][:12]}: "
                      f"{'no longer assigned a programme' if unassign(c, m['fingerprint']) else 'was not assigned one'}")
                return 0
            if args.cmd == "assign":
                out = assign(c, m["fingerprint"], args.programme)
                print(f"{m.get('name') or m['fingerprint'][:12]} ({m['role']}) is assigned {out['programme']}, "
                      f"version {out['version']}; its person is asked before it replaces anything")
                if m["role"] != "player":
                    print("  handed over when it next asks")
                    return 0
            elif m is not None and m["role"] != "player":
                print(f"{m.get('name') or m['fingerprint'][:12]} is a {m['role']}: it asks for its programme itself",
                      file=sys.stderr)
                return 1
            said = push(c, mdir, m["fingerprint"] if m else None)
            for name, line in said.items():
                print(f"  {name}: {line}")
            if not said:
                print("  every assigned player runs, or was asked about, its current version")
            return 0 if all(not s.startswith(("not ", "its player")) for s in said.values()) else 1
        s = standing(c, mdir if meshmod.initialised(mdir) else None)
        if not s["programmes"]:
            print("no programmes in the library yet: add-from-listener keeps one")
        for p in s["programmes"]:
            last = p["versions"][-1]
            print(f"{p['name']}: {len(p['versions'])} version(s), current {last['version']} "
                  f"({last['slots']} time slot(s), {last['fingerprint'][:12]})")
        for n in s["nodes"]:
            print(f"  {n['node']}: {n['programme']} -- {n['state']}")
        return 0
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
