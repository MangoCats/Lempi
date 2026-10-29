#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the console's Sync page knows [SPEC-STAR-120..128].

The page drives `star_sync.py` and contains none of it [SPEC-SUI-015]: every
fact here is read from a run folder that tool wrote, and every action is that
tool's own function or a job that runs it. What is added is only what a person
needs in front of them -- where the run stands, each item in words, and what
stands between it and a commit.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import star_sync as ss  # noqa: E402

STAGES = ("snapshot", "merge", "patch", "rehearse", "commit")

# [SPEC-STAR-126]: while the hub's own pair is patched from inside the console
# that holds it, the console refuses its own writing routes.
_lock = threading.Event()


def lock(on: bool) -> None:
    (_lock.set if on else _lock.clear)()


def locked() -> bool:
    return _lock.is_set()


def plan_path() -> str:
    return os.environ.get("LEMPI_STAR_PLAN") or os.path.join(ROOT, "fleet", "star-plan.json")


def load_plan():
    """(plan, None), or (None, why) -- a missing plan is said, not raised [SPEC-STAR-128]."""
    p = plan_path()
    if not os.path.isfile(p):
        return None, (f"no plan at {p}: copy fleet-example/star-plan.json there and fill in the fleet "
                      "[SPEC-STAR-128]")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh), None
    except ValueError as err:
        return None, f"{p} is not JSON: {err}"


def newest(plan):
    runs = ss.at_root(plan["runs"])
    found = ss.run_names(runs)
    return os.path.join(runs, found[-1]) if found else None


def _json(path):
    return ss.read_json(path)


def _age_hours(iso):
    try:
        return round((ss.now() - dt.datetime.fromisoformat(iso)).total_seconds() / 3600, 1)
    except (TypeError, ValueError):
        return None


def hub_backup(plan):
    days = os.path.join(ss.at_root(plan["backups"]["dir"]), "days")
    have = sorted(f[:-5] for f in os.listdir(days) if f.endswith(".json")) if os.path.isdir(days) else []
    if not have:
        return {"newest": None, "hours": None}
    rec = _json(os.path.join(days, f"{have[-1]}.json")) or {}
    return {"newest": have[-1], "hours": _age_hours(rec.get("taken_at"))}


def committed_names(run):
    """The run's own plan of who is in it: the hub, the players and the phones."""
    d = _json(os.path.join(run, "distribute-plan.json"))
    if not d:
        return []
    return [d["hub"]] + sorted(d.get("signed", {})) + sorted(d.get("members", {}))


# ------------------------------------------------- the members' channel --
# A phone is not reached from here [REQ-AND-330]: a commit leaves its patch in
# the phone's outbox, and the phone fetches it over the members' channel that
# `intake.py` serves. The page shows what waits and whether anything is there
# to hand it over, and can start the intake when nothing is.
_intake = None                 # the intake this console started, if it did
START_WAIT_S = 10              # how long a started intake has to bind its members' port


def phone_patch(mdir, fp) -> dict:
    """What waits in a phone's outbox, and the last run it said it applied."""
    o = ss.meshmod.outbox(mdir, fp)
    waiting = ({"run": o["run"], "rows": sum(len(e["rows"]) for e in o["patch"].get("tables", []))}
               if o else None)
    d = os.path.join(ss.meshmod.member_dir(mdir, fp), "applied")
    done = sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []
    a = _json(os.path.join(d, done[-1])) if done else None
    return {"waiting": waiting, "applied": {"run": a["run"], "at": a.get("applied_at")} if a else None}


def _listening(port) -> bool:
    # Asked of the port, not of a process handle: an intake started by hand
    # answers the phone just as well as one started here [GDE-DEP-070].
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def channel(mdir) -> dict:
    port = ss.meshmod.MEMBER_PORT
    ours = _intake is not None and _intake.poll() is None
    return {"port": port, "listening": _listening(port), "started_here": ours,
            "log": os.path.join(mdir, "intake.log")}


def start_channel(plan) -> dict:
    """`intake.py` on the hub's library, with the key beside it, as a person
    would start it; answered only once its members' port is bound, or with the
    reason it is not."""
    global _intake
    mdir = ss.mesh_of(plan)
    ch = channel(mdir)
    if ch["listening"]:
        return dict(ch, already=True)
    db = ss.at_root(plan["hub"]["library"])
    key = os.path.join(os.path.dirname(db), "intake.key")
    if not os.path.isfile(key):
        # intake.py refuses the published key on a LAN address [SPEC-SEC-020].
        raise SystemExit(f"no intake key at {key}: the intake will not serve the LAN without one")
    with open(ch["log"], "w", encoding="utf-8") as fh:
        _intake = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "intake.py"), db, "--key-file", key],
                                   stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT)
    for _ in range(START_WAIT_S * 4):
        if _intake.poll() is not None:
            break
        if _listening(ch["port"]):
            return channel(mdir)
        time.sleep(0.25)
    with open(ch["log"], encoding="utf-8", errors="replace") as fh:
        tail = fh.read()[-600:].strip()
    why = (f"the intake exited ({_intake.returncode})" if _intake.poll() is not None
           else f"the intake did not bind port {ch['port']} within {START_WAIT_S} s")
    raise SystemExit(f"{why}: {tail or 'it wrote nothing'} -- see {ch['log']}")


def state(plan) -> dict:
    """Everything the page's first paint needs, in one read."""
    st = ss.load_state(plan) if os.path.exists(os.path.join(ss.at_root(plan["runs"]), "state.json")) else {}
    run = newest(plan)
    missing = _json(os.path.join(run, "missing.json")) or {} if run else {}
    base = _json(os.path.join(run, "baselines.json")) or {} if run else {}
    old = _json(os.path.join(run, "old_players.json")) or {} if run else {}
    sel = _json(os.path.join(run, "selected.json")) if run else None     # [SPEC-STAR-130]
    left = set((sel or {}).get("left_alone", []))
    rid = os.path.basename(run) if run else None
    sent = st.get("nodes", {})
    nodes = []
    for name, n in sorted(plan["nodes"].items()):
        e = sent.get(name, {})
        b = base.get(name) or {}
        cat, cat_ok = None, None
        if "signed" in b:
            cat = ss.catalogue_words(b.get("catalogue") or {"state": "no-copy"})
            cat_ok = bool(b.get("catalogue_in_step"))
        nodes.append(dict(name=name, kind="player", on_demand=bool(n.get("on_demand")), enrolled=bool(n.get("member")),
                          last_sent=e.get("at"), hours=_age_hours(e.get("at")),
                          in_run="left alone" if name in left else "missing" if name in missing
                          else "taken" if name in base else None,
                          why=missing.get(name), catalogue=cat, catalogue_ok=cat_ok,
                          old_player=name in old, old_why=old.get(name)))
    for name in sorted(set(base) | set(missing)):
        if name not in plan["nodes"] and name != plan["hub"]["name"]:
            e = sent.get(name, {})
            nodes.append(dict(name=name, kind="phone", on_demand=False, enrolled=True, last_sent=e.get("at"),
                              hours=_age_hours(e.get("at")), in_run="missing" if name in missing else "taken",
                              why=missing.get(name), catalogue=None, old_player=False, old_why=None))
    for name in sorted(left - {n["name"] for n in nodes}):            # a phone left alone
        nodes.append(dict(name=name, kind="phone", on_demand=False, enrolled=True, last_sent=None, hours=None,
                          in_run="left alone", why=None, catalogue=None, old_player=False, old_why=None))
    # A phone's row is keyed by its short fingerprint; the person knows it by the name in the roster.
    mdir = ss.mesh_of(plan)
    phones = {m["node"]: m for m in ss.meshmod.sync_members(mdir)}
    for n in nodes:
        if n["kind"] == "phone":
            m = phones.get(n["name"]) or {}
            n["label"] = (m.get("name") or "").strip() or None
            n.update(phone_patch(mdir, m["fingerprint"]) if m else {"waiting": None, "applied": None})
    out = {"hub": plan["hub"]["name"], "backup": hub_backup(plan), "nodes": nodes, "run": None,
           "channel": channel(mdir)}
    if not run:
        return out
    info = {"id": rid, "old_players": old, "left_alone": sorted(left), "missing": missing,
            "snapshot": os.path.exists(os.path.join(run, "manifest.json")),
            "merged": os.path.exists(os.path.join(run, "merge", "items.json")),
            "proven": bool((_json(os.path.join(run, "proven.json")) or {}).get("ok")),
            "rehearsal": _json(os.path.join(run, "rehearsal.json")) or {},
            "committed": sorted(n for n, e in list(sent.items()) + [(plan["hub"]["name"], st.get("hub", {}))]
                                if e.get("run") == rid),
            "has_summary": os.path.exists(os.path.join(run, "SUMMARY.md")),
            "void_merges": len(os.listdir(os.path.join(run, "void"))) if os.path.isdir(os.path.join(run, "void")) else 0}
    if info["merged"]:
        rv = ss.review(run)
        info.update(items=len(rv["items"]), pending=len(rv["pending"]), stale=len(rv["stale"]), void=len(rv["void"]),
                    propagated=_json(os.path.join(run, "merge", "items.json")).get("propagated", {}))
    names = committed_names(run)
    info["names"] = names
    info["blockers"] = ss.gate_problems(run, names) if info["merged"] else ["the run has not been merged"]
    info["ready"] = bool(names) and not info["blockers"]
    out["run"] = info
    return out


# ------------------------------------------------------------------ items --
def describe(conn, table: str, key) -> dict:
    """[SPEC-STAR-122]: what an item is, in words, from the hub's catalogue; a
    subject it lacks says its kind and id, marked unresolved [SPEC-STAR-930]."""
    key = list(key) if isinstance(key, (list, tuple)) else [key]

    def one(sql, *args):
        try:
            r = conn.execute(sql, args).fetchone()
        except Exception:                      # a catalogue without the table says nothing
            return None
        return r[0] if r and r[0] is not None else None

    def subject(kind, ident):
        if kind == "recording":
            t = one("SELECT title FROM recordings WHERE mbid = ?", ident)
            a = one("SELECT a.name FROM recording_artists ra JOIN artists a ON a.mbid = ra.artist_mbid "
                    "WHERE ra.mbid = ? ORDER BY ra.weight DESC LIMIT 1", ident) if t else None
            return (f"{t}" + (f" — {a}" if a else "")) if t else None
        if kind == "artist":
            return one("SELECT name FROM artists WHERE mbid = ?", ident)
        if kind == "passage":
            return one("SELECT r.title FROM passage_recordings pr JOIN recordings r USING (mbid) "
                       "WHERE pr.passage_id = ? LIMIT 1", ident)
        return None

    shown = None
    if table in ("listener_preferences", "listener_flags", "listener_characteristics") and len(key) >= 2:
        s = subject(key[0], key[1])
        extra = f" · {key[2]}" if table == "listener_characteristics" and len(key) > 2 else ""
        shown = f"{key[0]} “{s}”{extra}" if s else None
        label = f"{key[0]} {key[1]}{extra}"
    elif table == "listener_likes":
        s = subject("recording", key[0])
        shown, label = (f"like of “{s}”" if s else None), f"like of {key[0]}"
    elif table in ("id_reviews", "boundary_reviews"):
        s = subject("passage", key[0])
        shown, label = (f"{table.split('_')[0]} review of “{s}”" if s else None), f"{table} passage {key[0]}"
    elif table == "artist_reviews":
        s = subject("recording", key[0])
        shown, label = (f"artist review of “{s}”" if s else None), f"{table} {key[0]}"
    elif table in ("listener_occasions", "listener_occasion_points"):
        shown = label = f"occasion {key[0]}" + (f" ({key[2]}-{key[3]})" if table.endswith("points") and len(key) > 3 else "")
    else:
        shown = None
        label = f"{table} {key}"
    return {"text": shown or label, "resolved": shown is not None}


def _status(x, verdicts):
    v = verdicts.get(x["id"])
    if v is None:
        return "pending", None
    effective = ss.verdict_node(x, v)
    if effective != x["chosen"]:
        return "stale", v
    return ("approved" if v["verdict"] == "approved" else "changed"), v


def items_page(run, conn, kind=None, table=None, node=None, after=0, limit=100, status=None) -> dict:
    """One page of the run's items, filtered, with the counts of the whole run."""
    rv = ss.review(run)
    verdicts = rv["verdicts"]
    rows = []
    for x in rv["items"]:
        st, v = _status(x, verdicts)
        rows.append((x, st, v))
    counts = {"total": len(rows), "pending": 0, "approved": 0, "changed": 0, "stale": 0, "by_kind": {},
              "pending_by_kind": {}}
    for x, st, _ in rows:
        counts[st] += 1
        counts["by_kind"][x["kind"]] = counts["by_kind"].get(x["kind"], 0) + 1
        if st == "pending":
            counts["pending_by_kind"][x["kind"]] = counts["pending_by_kind"].get(x["kind"], 0) + 1
    keep = [(x, st, v) for x, st, v in rows
            if (not kind or x["kind"] == kind) and (not table or x["table"] == table)
            and (not node or any(c["node"] == node for c in x["candidates"])) and (not status or st == status)]
    page = []
    for x, st, v in keep[after:after + limit]:
        page.append(dict(x, name=describe(conn, x["table"], x["key"]), status=st, verdict=v))
    return {"items": page, "matching": len(keep), "after": after, "counts": counts,
            "tables": sorted({x["table"] for x, _, _ in rows}),
            "nodes": sorted({c["node"] for x, _, _ in rows for c in x["candidates"]}),
            "propagated": ss.merge_items(run).get("propagated", {})}


def set_verdicts(run, body: dict) -> dict:
    """{id: "approved" | "chose:NODE"}; each is validated against its item."""
    done, errors = 0, {}
    for ident, value in body.items():
        try:
            ss.give_verdict(run, ident, value, "item")
            done += 1
        except SystemExit as err:
            errors[ident] = str(err)
    return {"set": done, "errors": errors}


def pending_kinds(run) -> dict:
    kinds = {}
    for x in ss.review(run)["pending"]:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    return kinds


def stage_target(plan, run, stage, nodes):
    """(target JSON, None) for a `star-sync` job, or (None, why) -- refused before
    anything runs [SPEC-STAR-124]."""
    if stage not in STAGES:
        return None, f"not a stage: {stage}"
    if stage == "snapshot":
        # [SPEC-STAR-130]: the page never takes a snapshot of every node by default.
        if not nodes:
            return None, "choose the nodes this run takes part with: the others are left alone, untouched"
        known = set(plan["nodes"]) | {m["node"] for m in ss.meshmod.sync_members(ss.mesh_of(plan))}
        if [n for n in nodes if n not in known]:
            return None, f"not nodes of this fleet: {[n for n in nodes if n not in known]}"
        return json.dumps({"stage": stage, "nodes": nodes}), None
    unknown = [n for n in nodes if n not in plan["nodes"] and n != plan["hub"]["name"]
               and n not in ((_json(os.path.join(run, "baselines.json")) or {}) if run else {})]
    if unknown:
        return None, f"not in the plan or this run: {unknown}"
    out_of_run = [n for n in nodes if run and n not in committed_names(run) and committed_names(run)]
    if out_of_run:
        return None, f"left alone in this run, so not reached by it: {out_of_run}"
    if stage != "snapshot" and not run:
        return None, "there is no run: take a snapshot first"
    if stage == "commit":
        hub = plan["hub"]["name"]
        names = [hub] + [n for n in (nodes or committed_names(run)) if n != hub]    # the hub always takes part [SPEC-STAR-134]
        problems = ss.gate_problems(run, names)
        if problems:
            return None, "; ".join(problems)
    return json.dumps({"stage": stage, "nodes": nodes}), None


def summary(run, limit=60000) -> dict:
    def read(p):
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    return {"summary": read(os.path.join(run, "SUMMARY.md")), "report": read(os.path.join(run, "merge", "report.md"))}
