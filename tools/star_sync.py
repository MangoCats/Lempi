#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The routine star sync, and the hub's own backup [SPEC-STAR-085..087].

One command per stage, each working in a dated run folder under the plan's
`runs`, so a stage can be read, re-run or stopped between. Every node is a
member player, reached by signed requests [SPEC-STAR-100]; ssh only starts and
stops a player kept on demand [SPEC-STAR-101], and carries the hub's backup to
its mirror.

    python tools/star_sync.py PLAN snapshot [--nodes A,B]
                                                      a new run: the hub's pair here, and the shared
                                                      tables and catalogue summary of the nodes chosen
                                                      (all, if none are); a node not chosen is left
                                                      alone, untouched [SPEC-STAR-130]
    python tools/star_sync.py PLAN merge              the hub's pair and each node's copy, and the
                                                      items for a verdict [SPEC-STAR-110]
    python tools/star_sync.py PLAN items              each item, and where its verdict stands
    python tools/star_sync.py PLAN verdict ID V       one verdict: approved | chose:NODE
    python tools/star_sync.py PLAN approve-all        every item still without one takes its default
    python tools/star_sync.py PLAN patch              every patch, each proven (merging again if a
                                                      verdict changed an outcome)
    python tools/star_sync.py PLAN rehearse [NODE..]  read-only, against the live files
    python tools/star_sync.py PLAN commit [NODE..]    for real: the hub, then the nodes
                                                      [--approve-all] [--console-pid N]
    python tools/star_sync.py PLAN backup             the hub's daily backup, and its mirror
    python tools/star_sync.py PLAN status             exits 1 when a backup has gone stale

`merge`, `items`, `verdict`, `approve-all`, `patch`, `rehearse` and `commit` act
on the newest run unless `--run DIR` names one. Nothing is committed until a
person has read the run's SUMMARY.md and the merge's report.md, every item has
a verdict, the patches are proven and each node has been rehearsed clean
[SPEC-STAR-070, SPEC-STAR-116].

The plan is the fleet's real roster, so it lives untracked in `fleet/`;
`fleet-example/star-plan.json` shows its shape. The run's state -- what was
last sent to each node -- is `<runs>/state.json`.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import gc
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import star_distribute as sd  # noqa: E402  -- ssh, for a player's start and stop and the mirror
import star_merge as sm  # noqa: E402
import star_patch as sp  # noqa: E402
import mesh as meshmod  # noqa: E402  -- the signed transport, and phones by upload [REQ-AND-330]


SSH = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=5",
       "-o", "ServerAliveCountMax=6"]


def say(msg):
    enc = sys.stdout.encoding or "utf-8"
    print(str(msg).encode(enc, "replace").decode(enc), flush=True)


def at_root(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def now():
    return dt.datetime.now(dt.timezone.utc)


def stamp(t=None):
    return (t or now()).strftime("%Y%m%dT%H%MZ")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def seal(path):
    os.chmod(path, 0o444)


def quiet_prints(db):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sp.fingerprint(db)
    return buf.getvalue()


def local_backup(src, dst):
    """The backup API, here: a consistent copy of a file something may be using."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    s = sp.open_current(src)
    d = sqlite3.connect(dst)
    s.backup(d)
    ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    d.close()
    s.close()
    if ok != "ok":
        raise SystemExit(f"the copy of {src} fails integrity_check: {ok}")


# ------------------------------------------------------------------ state --
def load_state(plan):
    p = os.path.join(at_root(plan["runs"]), "state.json")
    if not os.path.exists(p):
        raise SystemExit(f"{p} missing: it names what each node was last sent, and the "
                         "first routine sync needs it seeded from the last distribution")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(plan, state):
    p = os.path.join(at_root(plan["runs"]), "state.json")
    tmp = p + ".new"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, p)


def newest_run(plan, given=None):
    if given:
        return at_root(given)
    runs = at_root(plan["runs"])
    found = sorted(d for d in os.listdir(runs) if os.path.isdir(os.path.join(runs, d)))
    if not found:
        raise SystemExit(f"no run under {runs}: `snapshot` makes one")
    return os.path.join(runs, found[-1])



# --------------------------------------------------------------- the players --
# [SPEC-STAR-100]: every node is a member player, reached by signed requests.
# What the merge shares, and what a node keeps for itself, as lempi-core names
# them (held equal by test_star_merge).
SIGNED_TABLES = sorted(t for t, s in sm.TABLES.items() if s["rule"] in (sm.LWW, sm.UNION))
SIGNED_EXCLUDE = {"files": sorted(sm.MACHINE_SCOPE["files"])}
# [SPEC-NKP-082]: the hub's own bookkeeping, which no node needs and which travels in neither direction.
# `caa_asked_at` is when Vipunen last asked the Cover Art Archive; only `fetch_cover_art.py` reads it.
SIGNED_OMIT = {"cover_art": ["caa_asked_at"]}
NATURAL_KEYS_NEEDED = 1    # [SPEC-NKP-075]: the catalogue patch by natural key
CATALOGUE_PATCH_MAX = 16 << 20   # [SPEC-NKP-920]: the most a player is sent in one request, in bytes of JSON
PART_MAX = 8 << 20               # [SPEC-NKP-930]: the most JSON in one part of a patch sent in parts
NATURAL_PART_MAX = 32 << 20      # the natural entries stay whole in the first part; the player takes no more than this
PARTS_MAX = 400                  # and no more parts than this
CATALOGUE_TOTAL_MAX = 1 << 30    # a patch past this is not a patch: say so
START_WAIT_S = 90          # [SPEC-STAR-101]: how long an on-demand player has to answer


def mesh_of(plan):
    return meshmod.mesh_dir(at_root(plan["hub"]["library"]))


def db_of(export, path):
    """A database from `mesh_sync::export`'s shape. A table the export gives
    no creating statement for -- the catalogue summary's -- is made from its
    columns, keyed by its first."""
    c = sqlite3.connect(path)
    try:
        for t in export["tables"]:
            for s in t.get("create") or [f"CREATE TABLE {t['name']} ({', '.join(t['columns'])}, "
                                         f"PRIMARY KEY ({', '.join(t['key'])}))"]:
                c.execute(s)
            c.executemany(f"INSERT INTO {t['name']} ({', '.join(t['columns'])}) VALUES "
                          f"({', '.join('?' * len(t['columns']))})",
                          [[sp.dec(v) for v in r] for r in t["rows"]])
        c.commit()
    finally:
        c.close()


# What a node's catalogue summary covers [SPEC-NSH-060]: every file and passage, by
# the node's own ids and the columns that identify them. The natural keys are the
# ones that cross installations [SPEC-DF-035]: a file by its `audio_md5`, a passage
# by (`audio_md5`, kind, start, end).
SUMMARY_COLS = {"files": ["file_id", "audio_md5"], "passages": ["passage_id", "file_id", "kind", "start_ms", "end_ms"]}


def summary_rows(db):
    """(rows by id, natural keys) of the files and passages a summary or a catalogue holds."""
    c = sp.open_ro(db)
    try:
        by_id = {t: {tuple(r) for r in c.execute(f"SELECT {', '.join(cols)} FROM {t}")}
                 for t, cols in SUMMARY_COLS.items()}
        nat = {"files": {r[0] for r in c.execute("SELECT audio_md5 FROM files")},
               "passages": {tuple(r) for r in c.execute(
                   "SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms FROM passages p JOIN files f USING (file_id)")}}
    finally:
        c.close()
    return by_id, nat


CATALOGUE_STATES = {
    "as-sent": "as last sent",
    "holds-hub-rows": "holds the hub's new rows already",
    "other-ids": "the hub's music under other ids",
    "changed": "changed on the node",
    "no-copy": "no copy was last sent",
}


def catalogue_state(sent_lib, summary_db, hub_lib):
    """[SPEC-STAR-940]: where a node's catalogue stands against what the hub last
    sent it and what the hub holds now, as far as the summary shows.

    `as-sent` and `holds-hub-rows` can take the strict patch: every row the node
    has is one the patch expects or one it already makes. `other-ids` cannot, and
    is the common case (found 2026-10-05): the node holds the hub's own music,
    which a bundle send imported under the node's own ids. `changed` is a real
    difference. The player's own rehearsal is the check that decides; this says
    what to expect, and why."""
    if not sent_lib:
        return {"state": "no-copy"}
    node_id, node_nat = summary_rows(summary_db)
    sent_id, sent_nat = summary_rows(sent_lib)
    if node_id == sent_id:
        return {"state": "as-sent"}
    hub_id, hub_nat = summary_rows(hub_lib)
    if all(node_id[t] <= sent_id[t] | hub_id[t] for t in SUMMARY_COLS):
        return {"state": "holds-hub-rows"}
    added = {t: node_nat[t] - sent_nat[t] for t in node_nat}
    gone = {t: sent_nat[t] - node_nat[t] for t in node_nat}
    detail = {"files": len(added["files"]), "passages": len(added["passages"])}
    if all(added[t] <= hub_nat[t] for t in added) and not any(gone.values()):
        return {"state": "other-ids", **detail}
    return {"state": "changed", **detail, "gone_files": len(gone["files"]), "gone_passages": len(gone["passages"]),
            "not_the_hubs": sum(len(added[t] - hub_nat[t]) for t in added)}


def catalogue_words(st):
    """A catalogue state as a person reads it."""
    text = CATALOGUE_STATES[st["state"]]
    if st["state"] in ("other-ids", "changed"):
        text += f" ({st['files']} files, {st['passages']} passages)"
    return text


class PartsUnsupported(Exception):
    """A catalogue patch the hub will not send, even in parts, and why."""


def json_size(x):
    return len(json.dumps(x, separators=(",", ":")))


def split_catalogue(cat, limit=None):
    """[SPEC-NKP-930]: a catalogue patch cut into parts of at most `limit` bytes of JSON.

    The natural entries stay whole in the first part, so the node's check, which sees
    the database as it was before the patch, still sees every one of them. Every other
    table is cut by rows, in table order, and packed; a table's `add_columns` and `create`
    ride only in the first piece that names it, which the node applies before the rest."""
    limit = limit or PART_MAX
    natural = [e for e in cat["tables"] if "natural" in e]
    plain = [e for e in cat["tables"] if "natural" not in e]
    nat_bytes = sum(json_size(e) for e in natural)
    if nat_bytes > NATURAL_PART_MAX:
        raise PartsUnsupported(f"the tables that hold ids come to {nat_bytes / 1048576:.0f} MB, more than the "
                               f"{NATURAL_PART_MAX >> 20} MB one part may be, and cannot be cut: the node checks them together")
    pieces = []
    for e in plain:
        head = {k: v for k, v in e.items() if k != "rows"}
        budget = max(limit - json_size(head), 1)       # the table's header rides in every piece, so it counts
        chunk, chunk_bytes, first = [], 0, True

        def piece(rows, first):
            d = dict(head, rows=rows)
            if not first:
                d.pop("add_columns", None)
                d.pop("create", None)
            return d

        for r in e["rows"]:
            rb = json_size(r)
            if rb > NATURAL_PART_MAX:
                raise PartsUnsupported(f"one row of {e['name']} is {rb / 1048576:.0f} MB, more than a part may be")
            if chunk and chunk_bytes + rb > budget:
                pieces.append(piece(chunk, first))
                chunk, chunk_bytes, first = [], 0, False
            chunk.append(r)
            chunk_bytes += rb
        if chunk or first:                      # a table with only a schema change is still one piece
            pieces.append(piece(chunk, first))
    parts = [{"tables": natural}] if natural else []
    cur, cur_bytes = [], 0
    for pc in pieces:
        pb = json_size(pc)
        if cur and cur_bytes + pb > limit:
            parts.append({"tables": cur})
            cur, cur_bytes = [], 0
        cur.append(pc)
        cur_bytes += pb
    if cur:
        parts.append({"tables": cur})
    if len(parts) > PARTS_MAX:
        raise PartsUnsupported(f"{len(parts)} parts, more than the {PARTS_MAX} a node takes")
    return parts


def write_parts(pd, parts):
    """The parts as files beside the patch, and the manifest that names them with their digests."""
    for old in os.listdir(pd):
        if old.startswith("catalogue."):
            os.remove(os.path.join(pd, old))
    metas, total = [], 0
    for i, part in enumerate(parts, 1):
        name = f"catalogue.part-{i:03d}.json"
        write_json(os.path.join(pd, name), part)
        with open(os.path.join(pd, name), "rb") as fh:
            data = fh.read()
        total += len(data)
        metas.append({"file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest = {"of": len(parts), "total_bytes": total, "parts": metas}
    write_json(os.path.join(pd, "catalogue.parts.json"), manifest)
    return manifest


def stage_parts(plan, run, name, player, manifest, pd):
    """[SPEC-NKP-925]: send the parts the node does not hold, and only those. What a rehearsal
    staged is on the node still, so the commit sends nothing twice."""
    mdir = mesh_of(plan)
    have = (meshmod.sync_step(mdir, player, "staged", stamp_of(run)) or {}).get("catalogue", {})
    sent = 0
    for i, meta in enumerate(manifest["parts"], 1):
        if have.get(str(i)) == meta["sha256"]:
            continue
        with open(os.path.join(pd, meta["file"]), "rb") as fh:
            text = fh.read().decode("utf-8")
        say(f"  {name}: staging part {i} of {manifest['of']} ({len(text) / 1048576:.1f} MB)")
        meshmod.sync_step(mdir, player, "stage", stamp_of(run), timeout=1800.0, extra={"part": {
            "half": "catalogue", "n": i, "of": manifest["of"], "sha256": meta["sha256"],
            "total_bytes": manifest["total_bytes"], "text": text}})
        sent += 1
    return sent


def players_up(plan, names, started):
    """[SPEC-STAR-101]: each named node's player, as the members' query finds
    it, or None. A node planned `on_demand` whose player is down is started,
    over ssh, and waited for; `started` records which, so that only those are
    stopped afterwards."""
    mdir = mesh_of(plan)
    found = meshmod.sync_players(mdir)
    got, waiting = {}, []
    for name in names:
        n = plan["nodes"][name]
        fp = n.get("member")
        if fp and fp in found:
            got[name] = found[fp]
        elif fp and n.get("on_demand"):
            rc, out = sd.ssh(n["host"], n["on_demand"]["start"], quiet=True)
            if rc == 0:
                say(f"  {name}: its player started, on demand")
                started[name] = True
                waiting.append(name)
            else:
                say(f"  {name}: its player could not be started: {out}")
                got[name] = None
        else:
            got[name] = None
    deadline = time.time() + START_WAIT_S
    while waiting and time.time() < deadline:
        time.sleep(5)
        found = meshmod.sync_players(mdir)
        for name in list(waiting):
            fp = plan["nodes"][name]["member"]
            if fp in found:
                got[name] = found[fp]
                waiting.remove(name)
    for name in waiting:
        got[name] = None
    return got


def old_player_banner(old):
    """[SPEC-NKP-075]: an old player is said loudly, wherever a sync meets one.
    It still syncs its listener edits; what it cannot take is a catalogue patch."""
    if not old:
        return
    say("")
    say("!" * 78)
    say(f"!!!  OLD PLAYER{'S' if len(old) > 1 else ''}: {', '.join(sorted(old))}")
    for name, why in sorted(old.items()):
        say(f"!!!    {name}: {why}")
    say("!!!  Their listener edits sync as usual. Their catalogue is sent NO patch until they run")
    say("!!!  a current build [SPEC-NKP-075]: deploy one, then take a new snapshot.")
    say("!" * 78)
    say("")


def stop_started(plan, started):
    """Stop what this stage started, and only that [SPEC-STAR-101]."""
    for name in sorted(started):
        n = plan["nodes"][name]
        rc, out = sd.ssh(n["host"], n["on_demand"]["stop"], quiet=True)
        say(f"  {name}: its player stopped" if rc == 0 else
            f"  {name}: its player could NOT be stopped ({out}); stop it by hand")


# --------------------------------------------------------------- snapshot --
def history_of(sent, at, folder):
    """[SPEC-STAR-085]: what a node was last sent is what it held then -- the
    evidence of anything it has removed since [SPEC-STAR-050]."""
    os.makedirs(folder, exist_ok=True)
    if sent and os.path.exists(at_root(sent)):
        epoch = int(dt.datetime.fromisoformat(at).timestamp())
        dst = os.path.join(folder, f"listener-{epoch}.db")
        shutil.copyfile(at_root(sent), dst)
        seal(dst)
    return folder


def sent_path(entry, key):
    """The copy a node was last sent, if the state names one and it is still there."""
    p = (entry or {}).get(key)
    return at_root(p) if p and os.path.exists(at_root(p)) else None


def chosen(plan, only):
    """[SPEC-STAR-130]: the nodes a run is to take part with, validated; None is all.
    A node not chosen is not contacted, not started and not written to."""
    if only is None:
        return None
    only = set(only)
    known = set(plan["nodes"]) | {m["node"] for m in meshmod.sync_members(mesh_of(plan))}
    if not only:
        raise SystemExit("no node chosen: a run touches only the nodes it is given")
    unknown = sorted(only - known)
    if unknown:
        raise SystemExit(f"not nodes of this fleet: {unknown}")
    return only


def snapshot(plan, only=None):
    state = load_state(plan)
    only = chosen(plan, only)
    run = os.path.join(at_root(plan["runs"]), stamp())
    os.makedirs(run)
    say(f"run {run}")
    everyone = sorted(set(plan["nodes"]) | {m["node"] for m in meshmod.sync_members(mesh_of(plan))})
    take_part = everyone if only is None else sorted(only)
    write_json(os.path.join(run, "selected.json"), {"nodes": take_part,
                                                    "left_alone": [n for n in everyone if n not in take_part]})
    if only is not None:
        say(f"  taking part: {', '.join(take_part)}; left alone, untouched: "
            f"{', '.join(n for n in everyone if n not in take_part) or 'none'}")
    hub = plan["hub"]
    # The hub, here.
    hd = os.path.join(run, hub["name"])
    for half in ("listener", "library"):
        local_backup(at_root(hub[half]), os.path.join(hd, f"{half}.db"))
        seal(os.path.join(hd, f"{half}.db"))
    hs = state.get("hub", {})
    manifest = {"hub_library": os.path.join(hd, "library.db"), "hub_state_from": hub["name"],
                "nodes": [dict(name=hub["name"], listener=os.path.join(hd, "listener.db"),
                               taken_at=now().isoformat(), sent=sent_path(hs, "listener_sent"),
                               backups=history_of(hs.get("listener_sent"), hs.get("at", ""), os.path.join(hd, "history")))]}
    baselines = {hub["name"]: {"listener": os.path.join(hd, "listener.db"), "library": os.path.join(hd, "library.db")}}
    say(f"  {hub['name']}: both halves copied here")
    missing = {}
    # [SPEC-STAR-100]: every node, by signed request. An on-demand player is
    # started for this stage and stopped after it.
    mdir = mesh_of(plan)
    names = [n for n in sorted(plan["nodes"]) if only is None or n in only]
    started = {}
    old = {}
    try:
        up = players_up(plan, names, started)
        for name in names:
            n = plan["nodes"][name]
            ns = state["nodes"].get(name, {})
            player = up.get(name)
            if not n.get("member"):
                missing[name] = "no `member` fingerprint in the plan: it is not enrolled"
            elif player is None:
                missing[name] = "its player did not answer the members' query"
            else:
                try:
                    snap = meshmod.sync_step(mdir, player, "snapshot", stamp_of(run))
                except (ValueError, OSError) as err:
                    missing[name] = f"snapshot refused: {err} (a player too old for the sync routes answers like this)"
            if name in missing:
                say(f"  {name}: MISSING from this run -- {missing[name]}")
                continue
            nd = os.path.join(run, name)
            os.makedirs(nd)
            for part, fname in (("listener", "listener.db"), ("catalogue", "summary.db")):
                db_of(snap[part], os.path.join(nd, fname))
                seal(os.path.join(nd, fname))
            # [SPEC-STAR-940]: the catalogue is patched strictly, against what the
            # hub last sent. Whether it can be is read from the node's summary.
            cap = snap.get("natural_keys", 0) or 0
            if cap < NATURAL_KEYS_NEEDED:
                old[name] = "reports no natural_keys: a build older than the catalogue patch by natural key"
            sent_lib = sent_path(ns, "library_sent")
            cat = catalogue_state(sent_lib, os.path.join(nd, "summary.db"), os.path.join(hd, "library.db"))
            say(f"  {name}: {sum(len(t['rows']) for t in snap['listener']['tables'])} shared row(s), "
                f"catalogue {catalogue_words(cat)}")
            manifest["nodes"].append(dict(
                name=name, listener=os.path.join(nd, "listener.db"), taken_at=now().isoformat(),
                sent=sent_path(ns, "listener_sent"),
                backups=history_of(ns.get("listener_sent"), ns.get("at", ""), os.path.join(nd, "history")),
                catalogue=os.path.join(nd, "summary.db"), files=os.path.join(nd, "summary.db")))
            baselines[name] = {"listener": os.path.join(nd, "listener.db"), "signed": n["member"],
                               "library": sent_lib, "catalogue": cat, "natural_keys": cap,
                               "catalogue_in_step": cat["state"] in ("as-sent", "holds-hub-rows")
                               or (cat["state"] == "other-ids" and cap >= NATURAL_KEYS_NEEDED)}
    finally:
        stop_started(plan, started)
    # [REQ-AND-330]: enrolled phones, by what each last uploaded over the
    # members' channel -- a node reached by upload rather than by request. Only
    # the shared tables travel, so only they are merged for it.
    for m in meshmod.sync_members(mdir):
        name, fp = m["node"], m["fingerprint"]
        if only is not None and name not in only:
            say(f"  {name}: left alone, as chosen")
            continue
        up = meshmod.upload_of(mdir, fp)
        if up is None:
            missing[name] = "enrolled, but has not uploaded its edits yet"
            say(f"  {name} ({m['name'] or 'unnamed'}): MISSING from this run -- nothing uploaded yet")
            continue
        nd = os.path.join(run, name)
        os.makedirs(nd)
        shutil.copyfile(up["path"], os.path.join(nd, "listener.db"))
        seal(os.path.join(nd, "listener.db"))
        # Its history is its own earlier uploads, never the merged copy it was
        # last sent: that holds tables and passage rows it never uploads, which
        # would read as rows it had removed [SPEC-STAR-050].
        hist = os.path.join(nd, "history")
        os.makedirs(hist)
        for f in sorted(os.listdir(up["uploads"])):
            if f != f"listener-{up['epoch']}.db":     # earlier uploads: what it held then
                shutil.copyfile(os.path.join(up["uploads"], f), os.path.join(hist, f))
        manifest["nodes"].append(dict(name=name, listener=os.path.join(nd, "listener.db"),
                                      taken_at=up["uploaded_at"], backups=hist,
                                      sent=sent_path(state["nodes"].get(name), "listener_sent")))
        baselines[name] = {"listener": os.path.join(nd, "listener.db"), "member": fp}
        say(f"  {name} ({m['name'] or 'unnamed'}): its upload of {up['uploaded_at']}, "
            f"clock {up['clock_offset_ms']:+d} ms, {up['rows']}")
    for name, path in (("manifest.json", manifest), ("baselines.json", baselines),
                       ("missing.json", missing), ("old_players.json", old)):
        with open(os.path.join(run, name), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(path, fh, indent=1, sort_keys=True)
    old_player_banner(old)
    say(f"RESULT snapshot: {len(baselines)} taken" + (f", MISSING {sorted(missing)}" if missing else "")
        + (f", OLD PLAYER(S) {sorted(old)}" if old else ""))
    return 0 if not missing else 3


# ------------------------------------------------------------ merge, review --
def rm(path):
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=lambda f, p, e: (os.chmod(p, 0o644), f(p)))
    elif os.path.exists(path):
        os.chmod(path, 0o644)
        os.remove(path)


def void_merge(run):
    """[SPEC-STAR-114]: a merge made void by a verdict is kept beside the run,
    and with it goes everything made from it."""
    m = os.path.join(run, "merge")
    if os.path.isdir(m):
        vd = os.path.join(run, "void")
        os.makedirs(vd, exist_ok=True)
        n = 1
        while os.path.exists(os.path.join(vd, f"merge-{n}")):
            n += 1
        gc.collect()           # Windows will not move a folder holding a file something still has open
        try:
            os.replace(m, os.path.join(vd, f"merge-{n}"))
        except PermissionError:
            shutil.copytree(m, os.path.join(vd, f"merge-{n}"))
            rm(m)
    for f in ("patches", "SUMMARY.md", "distribute-plan.json", "proven.json", "rehearsal.json"):
        rm(os.path.join(run, f))


def merge(plan, run):
    with open(os.path.join(run, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    void_merge(run)
    if os.path.exists(verdicts_path(run)):
        manifest["verdicts"] = verdicts_path(run)
    return sm.run(manifest, os.path.join(run, "merge"))


def verdicts_path(run):
    return os.path.join(run, "verdicts.json")


def load_verdicts(run):
    if not os.path.exists(verdicts_path(run)):
        return {}
    with open(verdicts_path(run), encoding="utf-8") as fh:
        return json.load(fh)


def save_verdicts(run, verdicts):
    tmp = verdicts_path(run) + ".new"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(verdicts, fh, indent=1, sort_keys=True)
    os.replace(tmp, verdicts_path(run))


def merge_items(run):
    p = os.path.join(run, "merge", "items.json")
    if not os.path.exists(p):
        raise SystemExit(f"no merge in {run}: `merge` makes one")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def verdict_node(x, v):
    """The node whose value a verdict takes for item `x`."""
    return x["default"] if v["verdict"] == "approved" else v["verdict"][6:]


def review(run):
    """[SPEC-STAR-116]: where each item of the run's merge stands. `pending`
    has no verdict; `stale` has one the merge does not yet reflect; `void` are
    verdicts naming no item of this merge."""
    items = merge_items(run)["items"]
    verdicts = load_verdicts(run)
    ids = {x["id"] for x in items}
    return dict(items=items, verdicts=verdicts,
                pending=[x for x in items if x["id"] not in verdicts],
                stale=[x for x in items if x["id"] in verdicts and verdict_node(x, verdicts[x["id"]]) != x["chosen"]],
                void=sorted(set(verdicts) - ids))


def give_verdict(run, ident, value, by="item"):
    """[SPEC-STAR-114]: `approved`, or `chose:<node>` naming one of the item's candidates."""
    items = {x["id"]: x for x in merge_items(run)["items"]}
    if ident not in items:
        raise SystemExit(f"{ident} is not an item of this merge")
    x = items[ident]
    if value != "approved" and not (value.startswith("chose:") and value[6:] in {c["node"] for c in x["candidates"]}):
        raise SystemExit(f"a verdict is `approved` or `chose:NODE`, NODE one of "
                         f"{[c['node'] for c in x['candidates']]}: {value!r} is neither")
    v = load_verdicts(run)
    v[ident] = {"verdict": value, "at": now().isoformat(), "by": by}
    save_verdicts(run, v)
    return x


def approve_all(run, by="approve-all"):
    """[SPEC-STAR-118]: every item still without a verdict takes its default,
    the most recent change, and says it was approved in bulk. A verdict a
    person has given is never replaced."""
    rv = review(run)
    v = load_verdicts(run)
    for x in rv["pending"]:
        v[x["id"]] = {"verdict": "approved", "at": now().isoformat(), "by": by}
    save_verdicts(run, v)
    kinds = {}
    for x in rv["pending"]:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    return kinds


def show_items(run):
    rv = review(run)
    say(f"run {os.path.basename(run)}: {len(rv['items'])} item(s), {len(rv['pending'])} without a verdict, "
        f"{len(rv['stale'])} awaiting a new merge, {len(rv['void'])} void verdict(s)")
    for x in rv["items"]:
        v = rv["verdicts"].get(x["id"])
        col = f".{x['column']}" if x.get("column") else ""
        say(f"  {x['id']} {x['kind']:14} {x['table']} {x['key']}{col}: default {x['default']}"
            + (f" -> {v['verdict']} ({v['by']})" if v else " -- NO VERDICT"))
        for c in x["candidates"]:
            say(f"      {c['node']:15} {c['stamp'] or ''}  "
                + ("removed" if c["value"] is None else json.dumps(c["value"], default=str)[:160]))
    prop = merge_items(run).get("propagated", {})
    if prop:
        say("  carried without question: " + ", ".join(f"{t} {n}" for t, n in sorted(prop.items())))
    return 0


def gate(run, names):
    """[SPEC-STAR-116]: no commit until every item has a verdict the merge
    reflects, the patches are proven and each node has been rehearsed clean."""
    problems = gate_problems(run, names)
    if problems:
        raise SystemExit("refusing to commit [SPEC-STAR-116]:\n  " + "\n  ".join(problems))


def gate_problems(run, names):
    """What stands between this run and a commit, in words; empty when nothing does."""
    problems = []
    try:
        rv = review(run)
    except SystemExit as err:
        return [str(err)]
    if rv["pending"]:
        problems.append(f"{len(rv['pending'])} item(s) have no verdict: `items`, then `verdict` or `approve-all`")
    if rv["stale"]:
        problems.append(f"{len(rv['stale'])} verdict(s) change an outcome the merge does not reflect: `patch` merges again")
    pr = os.path.join(run, "proven.json")
    if not os.path.exists(pr) or not json.load(open(pr, encoding="utf-8")).get("ok"):
        problems.append("the patches are not proven: run `patch`")
    rh = os.path.join(run, "rehearsal.json")
    done = json.load(open(rh, encoding="utf-8")) if os.path.exists(rh) else {}
    for name in names:
        if done.get(name) != 0:
            problems.append(f"{name} has no clean rehearsal: run `rehearse {name}`")
    return problems


# ------------------------------------------------------------ merge, patch --
def targets(plan, run, name):
    m = os.path.join(run, "merge")
    d = m if name == plan["hub"]["name"] else os.path.join(m, "nodes", name)
    return {"listener": os.path.join(d, "listener.db"), "library": os.path.join(d, "library.db")}


def prove(base, patch, target):
    """baseline + patch == target, every table, before the patch leaves here."""
    with tempfile.TemporaryDirectory() as t:
        copy = os.path.join(t, "proof.db")
        shutil.copyfile(base, copy)
        os.chmod(copy, 0o644)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sp.apply(copy, patch, commit=True)
        return rc == 0 and quiet_prints(copy) == quiet_prints(target)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, separators=(",", ":"), sort_keys=True)


def values_patch(base, target):
    """A player's listener patch [SPEC-NSH-070]: the shared tables as values,
    proven here -- the member rule applied to a copy of what it sent must give
    the merge's shared tables -- before it is offered."""
    p = sp.make_values(base, target, tables=SIGNED_TABLES)
    with tempfile.TemporaryDirectory() as t:
        copy = os.path.join(t, "proof.db")
        shutil.copyfile(base, copy)
        os.chmod(copy, 0o644)
        sp.apply_member(copy, p, set(SIGNED_TABLES))
        good = sp.make_values(copy, target, tables=SIGNED_TABLES)["tables"] == []
    return p, good


def patch(plan, run):
    rv = review(run)
    if rv["stale"]:
        say(f"  {len(rv['stale'])} verdict(s) change an outcome: merging again")
        if merge(plan, run):
            return 1
    with open(os.path.join(run, "baselines.json"), encoding="utf-8") as fh:
        baselines = json.load(fh)
    for f in ("proven.json", "rehearsal.json"):
        rm(os.path.join(run, f))
    rv = review(run)
    summary = ["# Sync summary", "", f"Run `{os.path.basename(run)}`. Read this and `merge/report.md` "
               "before `commit` [SPEC-STAR-070].", ""]
    summary += [f"**Items for a verdict:** {len(rv['items'])}, {len(rv['pending'])} without one "
                "[SPEC-STAR-116].", ""]
    old = read_json(os.path.join(run, "old_players.json")) or {}
    if old:
        summary += ["**WARNING -- OLD PLAYER" + ("S" if len(old) > 1 else "") + ": " + ", ".join(sorted(old))
                    + ".** Their listener edits sync as usual. Their catalogue is sent no patch until they "
                    "run a current build [SPEC-NKP-075].", ""]
    left = (read_json(os.path.join(run, "selected.json")) or {}).get("left_alone", [])
    if left:
        summary += ["**Left alone, as chosen** -- not read, not written, not started: " + ", ".join(left)
                    + " [SPEC-STAR-130].", ""]
    with open(os.path.join(run, "missing.json"), encoding="utf-8") as fh:
        missing = json.load(fh)
    if missing:
        summary += ["**Missing from this run** -- they receive nothing until the next:", ""]
        summary += [f"- {n}: {why}" for n, why in sorted(missing.items())] + [""]
    ok = True
    members, signed = {}, {}
    hub = plan["hub"]["name"]
    for name in sorted(baselines):
        b = baselines[name]
        pd = os.path.join(run, "patches", name)
        os.makedirs(pd, exist_ok=True)
        t = targets(plan, run, name)
        summary += [f"## {name}", ""]
        if "member" in b:
            good, line = member_patch(b["listener"], t["listener"], os.path.join(pd, "member.patch.json"))
            ok = ok and good
            summary += [line, ""]
            members[name] = b["member"]
            say(f"  {name} {line[2:]}")
        elif "signed" in b:
            lis, good = values_patch(b["listener"], t["listener"])
            ok = ok and good
            write_json(os.path.join(pd, "listener.values.json"), lis)
            rows = {e["name"]: len(e["rows"]) for e in lis["tables"]}
            summary.append("- shared edits: " + (", ".join(f"`{k}` {v}" for k, v in sorted(rows.items())) or "nothing to send")
                           + ("" if good else " -- **NOT PROVEN: do not commit**"))
            cat, unsupported, parts = None, None, None
            for old in os.listdir(pd):                # nothing of an earlier patch lingers
                if old.startswith("catalogue."):
                    os.remove(os.path.join(pd, old))
            if b.get("catalogue_in_step"):
                # A player that can apply it gets the tables that hold ids named by
                # what identifies them everywhere [SPEC-NKP-075]; any other, the
                # strict id-keyed patch, which only reaches a catalogue numbered as ours.
                natural = (b.get("natural_keys", 0) or 0) >= NATURAL_KEYS_NEEDED
                try:
                    cat = sp.make_values(b["library"], t["library"], exclude=SIGNED_EXCLUDE, natural=natural, omit=SIGNED_OMIT)
                except sp.NaturalUnsupported as err:
                    unsupported = f"the hub cannot name what changed: {err} [SPEC-NKP-080]"
            if cat is not None:
                size = json_size(cat)
                if size > CATALOGUE_PATCH_MAX:
                    # A Pi has little memory to parse a request in, and the player's own limit is 64 MB. The first
                    # patch after a long gap is mostly cover art, so it goes in parts, staged on the node and
                    # committed there in one transaction [SPEC-NKP-920].
                    try:
                        if size > CATALOGUE_TOTAL_MAX:
                            raise PartsUnsupported(f"the patch is {size / 1048576:.0f} MB, more than the "
                                                   f"{CATALOGUE_TOTAL_MAX >> 30} GB that will be sent")
                        parts = split_catalogue(cat)
                    except PartsUnsupported as err:
                        unsupported = f"the patch is {size / 1048576:.0f} MB and cannot be sent in parts: {err} [SPEC-NKP-930]"
                        cat = None
            if cat is not None and parts is not None:
                man = write_parts(pd, parts)
                crow = {e["name"]: len(e["rows"]) for e in cat["tables"]}
                summary.append("- catalogue: " + ", ".join(f"`{k}` {v}" for k, v in sorted(crow.items()))
                               + (" by natural key" if any("natural" in e for e in cat["tables"]) else "")
                               + f", {size / 1048576:.0f} MB in {man['of']} parts, staged on the node and committed there in one "
                                 "transaction [SPEC-NKP-920] (proven by the player's own rehearsal, strictly)")
            elif cat is not None:
                write_json(os.path.join(pd, "catalogue.values.json"), cat)
                crow = {e["name"]: len(e["rows"]) for e in cat["tables"]}
                summary.append("- catalogue: " + (", ".join(f"`{k}` {v}" for k, v in sorted(crow.items())) or "nothing to send")
                               + (" by natural key" if any("natural" in e for e in cat["tables"]) else "")
                               + " (proven by the player's own rehearsal, strictly)")
            elif unsupported:
                summary.append(f"- catalogue: **none sent** -- {unsupported}")
                say(f"  {name}: NO CATALOGUE PATCH -- {unsupported}")
            else:
                cs = b.get("catalogue") or {"state": "no-copy"}
                why = catalogue_words(cs)
                if cs["state"] == "other-ids":
                    why += (": the same music as the hub, numbered differently, which only a patch by natural key "
                            "reaches, and this player is OLD: it cannot take one" if b.get("natural_keys", 0) < NATURAL_KEYS_NEEDED
                            else ": the same music as the hub, numbered differently")
                summary.append(f"- catalogue: **none sent** -- {why} [SPEC-STAR-940]")
            say(f"  {name}: {sum(rows.values())} shared row(s), {'proven' if good else 'NOT PROVEN'}")
            signed[name] = b["signed"]
        else:                                    # the hub, whose pair is here
            for half in ("listener", "library"):
                out = os.path.join(pd, f"{half}.patch.json")
                with contextlib.redirect_stdout(io.StringIO()):
                    sp.make(b[half], t[half], out)
                with open(out, encoding="utf-8") as fh:
                    p = json.load(fh)
                rows = {e["name"]: len(e["rows"]) for e in p["tables"]}
                if not p["tables"]:
                    summary.append(f"- {half}: nothing to send")
                    continue
                good = prove(b[half], out, t[half])
                ok = ok and good
                summary.append(f"- {half}: " + ", ".join(f"`{k}` {v}" for k, v in sorted(rows.items()))
                               + ("" if good else " -- **NOT PROVEN: do not commit**"))
                say(f"  {name} {half}: {sum(rows.values())} row(s), {'proven' if good else 'NOT PROVEN'}")
        summary.append("")
    with open(os.path.join(run, "SUMMARY.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(summary) + "\n")
    write_json(os.path.join(run, "proven.json"), {"ok": ok, "at": now().isoformat()})
    dplan = {"hub": hub, "signed": signed, "members": members, "prune": plan.get("prune")}
    with open(os.path.join(run, "distribute-plan.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dplan, fh, indent=1, sort_keys=True)
    say(f"RESULT patch: {'every patch proven' if ok else 'a patch is NOT PROVEN'}; read {run}/SUMMARY.md")
    return 0 if ok else 1


def member_patch(base, target, out):
    """A member's patch [SPEC-MTR-030]: the shared tables only, as values, and
    proven here -- the phone's own rule applied to a copy of what it uploaded
    must give the merge's shared tables -- before it is offered."""
    p = sp.make_member(base, target, meshmod.SHARED)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(p, fh, separators=(",", ":"), sort_keys=True)
    rows = {e["name"]: len(e["rows"]) for e in p["tables"]}
    if not rows:
        return True, "- shared edits: nothing to send"
    with tempfile.TemporaryDirectory() as t:
        copy = os.path.join(t, "proof.db")
        shutil.copyfile(base, copy)
        os.chmod(copy, 0o644)
        sp.apply_member(copy, p, meshmod.SHARED)
        good = sp.make_member(copy, target, meshmod.SHARED)["tables"] == []
    return good, ("- shared edits: " + ", ".join(f"`{t}` {k}" for t, k in sorted(rows.items()))
                  + ("" if good else " -- **NOT PROVEN: do not commit**"))


def member_offer(plan, run, name, fp, commit):
    """[REQ-AND-330]: a member is not reached from here. Its patch waits in
    its outbox on the members' channel until it fetches it."""
    say(f"== {name} (a phone, by its next contact) -- {'COMMIT' if commit else 'rehearsal'}")
    with open(os.path.join(run, "patches", name, "member.patch.json"), encoding="utf-8") as fh:
        p = json.load(fh)
    n = sum(len(e["rows"]) for e in p["tables"])
    if not commit:
        say(f"RESULT {name}: {n} row(s) to offer; checked by the phone's own rule when it applies them")
        return 0
    meshmod.put_outbox(meshmod.mesh_dir(at_root(plan["hub"]["library"])), fp, stamp_of(run), p)
    say(f"RESULT {name}: OFFERED -- {n} row(s) wait for it on the members' channel")
    return 0


def stamp_of(run):
    return os.path.basename(os.path.normpath(run))


# ----------------------------------------------------------- a player, signed --
def read_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def has_rows(p):
    return bool(p and p.get("tables"))


def player_step(plan, run, name, player, commit):
    """[SPEC-STAR-100]: one player's rehearsal or commit, by signed request.
    The player applies its own patch, and is not stopped."""
    mdir = mesh_of(plan)
    pd = os.path.join(run, "patches", name)
    lis = read_json(os.path.join(pd, "listener.values.json"))
    cat = read_json(os.path.join(pd, "catalogue.values.json"))
    parts = read_json(os.path.join(pd, "catalogue.parts.json"))
    say(f"== {name} (signed) -- {'COMMIT' if commit else 'rehearsal: nothing is kept'}")
    if player is None:
        say(f"RESULT {name}: REFUSED -- its player did not answer the members' query")
        return 1
    if not has_rows(lis) and not has_rows(cat) and not parts:
        say(f"RESULT {name}: nothing to send")
        return 0
    try:
        extra = None
        if parts:
            sent = stage_parts(plan, run, name, player, parts, pd)
            extra = {"catalogue_parts": {"of": parts["of"], "sha256": [p["sha256"] for p in parts["parts"]]}}
            say(f"  {name}: the catalogue's {parts['of']} parts are staged ({sent} sent now, "
                f"{parts['of'] - sent} already there)")
        got = meshmod.sync_step(mdir, player, "commit" if commit else "rehearse", stamp_of(run),
                                cat if has_rows(cat) else None, lis if has_rows(lis) else None,
                                timeout=3600.0 if parts else 600.0, extra=extra)
    except (ValueError, OSError) as err:
        say(f"RESULT {name}: REFUSED -- {err}")
        return 1
    kept = (got.get("listener") or {}).get("kept", 0)
    note = f"; {kept} row(s) it had changed since its snapshot were kept, and travel next time" if kept else ""
    warn = got.get("warning")
    say(f"RESULT {name}: " + (f"COMMITTED {got}" if commit else f"rehearsal CLEAN {got}") + note
        + (f"; WARNING {warn}" if warn else ""))
    return 0


# ---------------------------------------------------------- the hub, here --
def console_running(excuse=None):
    """The Vipunen console writes to the hub's pair, and so does a player beside
    it; neither must be running while the hub is patched. `excuse` is the pid
    of a console that has paused its player and will reload it
    [SPEC-STAR-126]: that process, and the co-resident `lempi` it paused, are
    not counted; any other still is. None when this cannot be told -- said so."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "
                            "'console\\.py|\\\\lempi\\.exe' } | ForEach-Object "
                            "{ \"$($_.ProcessId) $($_.Name)\" }"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    found = []
    for line in r.stdout.splitlines():
        pid, _, name = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        if excuse and (int(pid) == int(excuse) or name.lower().startswith("lempi")):
            continue
        found.append(pid)
    return found


def hub_apply(plan, run, commit, excuse=None):
    hub = plan["hub"]
    pd = os.path.join(run, "patches", hub["name"])
    halves = [h for h in ("listener", "library") if sd.patch_rows(os.path.join(pd, f"{h}.patch.json"))]
    say(f"== {hub['name']} (here) -- {'COMMIT' if commit else 'rehearsal'}")
    if not halves:
        say(f"RESULT {hub['name']}: nothing to send")
        return 0
    if not commit:
        rc = 0
        for h in halves:
            rc |= sp.check(at_root(hub[h]), os.path.join(pd, f"{h}.patch.json"))
        say(f"RESULT {hub['name']}: rehearsal {'CLEAN' if rc == 0 else 'NOT CLEAN'}")
        return rc
    busy = console_running(excuse)
    if busy is None:
        raise SystemExit("cannot tell whether the Vipunen console is running: refusing to patch the hub")
    if busy:
        raise SystemExit(f"process(es) {busy} may be using the hub's pair: close the console first")
    if excuse:
        say(f"  run from the console (pid {excuse}), which has paused its player [SPEC-STAR-126]")
    bk = os.path.join(at_root(plan["backups"]["dir"]), f"pre-sync-{stamp_of(run)}")
    if os.path.exists(bk):
        raise SystemExit(f"{bk} exists: a backup is never written over")
    for h in halves:
        local_backup(at_root(hub[h]), os.path.join(bk, f"{h}.db"))
    say(f"  the hub's pair before this sync: {bk}")
    for h in halves:
        if sp.apply(at_root(hub[h]), os.path.join(pd, f"{h}.patch.json"), commit=False):
            raise SystemExit(f"the hub's {h} patch does not rehearse cleanly; nothing written")
    landed = []
    for h in halves:
        if sp.apply(at_root(hub[h]), os.path.join(pd, f"{h}.patch.json"), commit=True):
            for done in landed:
                sp.restore(os.path.join(bk, f"{done}.db"), at_root(hub[done]))
            raise SystemExit(f"the hub's {h} patch failed; {landed or 'nothing'} restored")
        landed.append(h)
    ok = True
    for h in ("listener", "library"):
        got = {l.split()[0]: l for l in quiet_prints(at_root(hub[h])).splitlines()}
        want = {l.split()[0]: l for l in quiet_prints(targets(plan, run, hub["name"])[h]).splitlines()}
        bad = [t for t in want if got.get(t) != want[t] and not (h == "listener" and t in sd.OWN)]
        if bad and h in halves:
            # As on a node: changed here since the snapshot, in rows the
            # patch never named, is kept and reported, not a failure.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sp.check(at_root(hub[h]), os.path.join(pd, f"{h}.patch.json"))
            if " 0 to apply" in buf.getvalue() or ": 0 to apply" in buf.getvalue():
                say(f"  {h}: every row the patch names holds its target; {bad} changed here "
                    "since the snapshot, and were kept")
                continue
        ok = ok and not bad
        say(f"  {h}: " + ("every governed table identical to the target" if not bad else f"DIFFERS {bad}"))
    if ok and plan.get("prune"):
        parent, keep = os.path.dirname(bk), plan["prune"]["keep"]
        mine = sorted(d for d in os.listdir(parent) if d.startswith("pre-sync-"))
        for old in mine[:-keep]:
            shutil.rmtree(os.path.join(parent, old), onerror=lambda f, p, e: (os.chmod(p, 0o644), f(p)))
            say(f"  pruned {old}")
    say(f"RESULT {hub['name']}: {'COMMITTED and verified' if ok else 'COMMITTED but a table DIFFERS'}")
    return 0 if ok else 1


def distribute(plan, run, names, commit, excuse=None, bulk=False):
    with open(os.path.join(run, "distribute-plan.json"), encoding="utf-8") as fh:
        dplan = json.load(fh)
    hub = plan["hub"]["name"]
    signed, members = dplan.get("signed", {}), dplan.get("members", {})
    names = names or [hub] + sorted(signed) + sorted(members)
    old_player_banner({n: w for n, w in (read_json(os.path.join(run, "old_players.json")) or {}).items() if n in names})
    if commit:
        if bulk:
            kinds = approve_all(run)
            say("  approve-all: " + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "no item was waiting"))
        gate(run, names)
    state = load_state(plan) if commit else None
    done = read_json(os.path.join(run, "rehearsal.json")) or {}
    started = {}
    worst = 0
    try:
        up = players_up(plan, [n for n in names if n in signed], started) if any(n in signed for n in names) else {}
        for name in names:
            if name == hub:
                rc = hub_apply(plan, run, commit, excuse)
            elif name in members:
                rc = member_offer(plan, run, name, members[name], commit)
            elif name in signed:
                rc = player_step(plan, run, name, up.get(name), commit)
            else:
                say(f"RESULT {name}: not in this run")
                rc = 1
            if not commit:
                done[name] = rc
            if commit and rc == 0:
                t = targets(plan, run, name)
                old = state["nodes"].get(name, {}) if name != hub else state.get("hub", {})
                lib_sent = old.get("library_sent")
                pdn = os.path.join(run, "patches", name)
                if name == hub or (name in signed and (os.path.exists(os.path.join(pdn, "catalogue.values.json"))
                                                       or os.path.exists(os.path.join(pdn, "catalogue.parts.json")))):
                    lib_sent = os.path.relpath(t["library"], ROOT)
                entry = {"listener_sent": os.path.relpath(t["listener"], ROOT), "library_sent": lib_sent,
                         "at": now().isoformat(), "run": stamp_of(run)}
                if name == hub:
                    state["hub"] = entry
                else:
                    state["nodes"][name] = entry
                save_state(plan, state)
            worst = max(worst, rc)
    finally:
        stop_started(plan, started)
        if not commit:
            write_json(os.path.join(run, "rehearsal.json"), done)
    return worst


# ----------------------------------------------------------------- backup --
def retained(dates, keep_daily, keep_monthly):
    """[SPEC-STAR-086]: the newest `keep_daily` days, and the earliest day of
    each of the newest `keep_monthly` months. `dates` are YYYY-MM-DD."""
    ds = sorted(set(dates))
    keep = set(ds[-keep_daily:]) if keep_daily > 0 else set()
    firsts = {}
    for d in ds:
        firsts.setdefault(d[:7], d)
    for m in sorted(firsts)[-keep_monthly:] if keep_monthly > 0 else []:
        keep.add(firsts[m])
    return keep


MESH_FILES = ("node.key", "node.pem", "mesh.key", "roster.json")


def mesh_archive(mdir: str, dest: str) -> None:
    """The hub's mesh keys and roster [SPEC-MTR-110], as an SQLite archive:
    `sqlite3 FILE -Ax` extracts it. An SQLite file so that it is stored,
    mirrored, kept and pruned exactly as the two databases are. Deterministic,
    so an unchanged mesh is the same object and is stored once. Enrolments in
    progress are not kept: they last minutes."""
    c = sqlite3.connect(dest)
    c.execute("CREATE TABLE sqlar(name TEXT PRIMARY KEY, mode INT, mtime INT, sz INT, data BLOB)")
    for name in MESH_FILES:
        with open(os.path.join(mdir, name), "rb") as fh:
            data = fh.read()
        c.execute("INSERT INTO sqlar VALUES (?, ?, 0, ?, ?)", (name, 0o100600, len(data), data))
    c.commit()
    c.close()


def backup(plan):
    b = plan["backups"]
    root = at_root(b["dir"])
    objs = os.path.join(root, "objects")
    days = os.path.join(root, "days")
    os.makedirs(objs, exist_ok=True)
    os.makedirs(days, exist_ok=True)
    today = now().strftime("%Y-%m-%d")
    entry = os.path.join(days, f"{today}.json")
    if os.path.exists(entry):
        say(f"  today's backup exists: {entry}")
    else:
        rec = {"taken_at": now().isoformat()}
        for h in ("listener", "library"):
            with tempfile.TemporaryDirectory(dir=root) as t:
                copy = os.path.join(t, f"{h}.db")
                local_backup(at_root(plan["hub"][h]), copy)
                h_sha = sha256(copy)
                dst = os.path.join(objs, f"{h_sha}.db")
                if not os.path.exists(dst):
                    shutil.move(copy, dst)
                    seal(dst)
                    say(f"  {h}: stored {h_sha[:12]}, {os.path.getsize(dst) >> 20} MB")
                else:
                    say(f"  {h}: unchanged, {h_sha[:12]} already held")
                rec[h] = h_sha
        # [SPEC-MTR-110]: losing the mesh key means enrolling every member
        # again, so it is kept with the pair it belongs beside.
        mdir = os.path.join(os.path.dirname(at_root(plan["hub"]["library"])), "mesh")
        if os.path.isfile(os.path.join(mdir, "roster.json")):
            with tempfile.TemporaryDirectory(dir=root) as t:
                copy = os.path.join(t, "mesh.db")
                mesh_archive(mdir, copy)
                m_sha = sha256(copy)
                dst = os.path.join(objs, f"{m_sha}.db")
                if not os.path.exists(dst):
                    shutil.move(copy, dst)
                    seal(dst)
                    say(f"  mesh keys and roster: stored {m_sha[:12]}")
                else:
                    say(f"  mesh keys and roster: unchanged, {m_sha[:12]} already held")
                rec["mesh"] = m_sha
        with open(entry, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rec, fh, indent=1, sort_keys=True)
    # Retention, then the objects nothing refers to any more.
    have = sorted(f[:-5] for f in os.listdir(days) if f.endswith(".json"))
    keep = retained(have, b["keep_daily"], b["keep_monthly"])
    for d in have:
        if d not in keep:
            os.remove(os.path.join(days, f"{d}.json"))
            say(f"  retired the backup of {d}")
    wanted = set()
    for d in keep:
        with open(os.path.join(days, f"{d}.json"), encoding="utf-8") as fh:
            rec = json.load(fh)
        wanted |= {rec["listener"], rec["library"]} | ({rec["mesh"]} if rec.get("mesh") else set())
    for f in os.listdir(objs):
        if f.endswith(".db") and f[:-3] not in wanted:
            os.chmod(os.path.join(objs, f), 0o644)
            os.remove(os.path.join(objs, f))
    rc = mirror(plan, keep, wanted)
    return max(rc, status(plan))


def mirror(plan, keep, wanted):
    """The same days and objects on the mirror host; an object is sent once,
    so an unchanged catalogue costs nothing after its first night."""
    m = plan["backups"].get("mirror")
    if not m:
        say("  no mirror in the plan")
        return 0
    host, rd = m["host"], m["dir"]
    root = at_root(plan["backups"]["dir"])
    try:
        sd.must(host, f"mkdir -p {rd}/objects {rd}/days", "reach the mirror", quiet=True)
        there = set(sd.must(host, f"ls -1 {rd}/objects {rd}/days", "list the mirror", quiet=True).split())
        for o in sorted(wanted):
            if f"{o}.db" in there:
                continue
            src = os.path.join(root, "objects", f"{o}.db")
            subprocess.run(["scp", "-q", *SSH, src, f"{host}:{rd}/objects/{o}.db.part"], timeout=7200)
            got = sd.must(host, f"sha256sum {rd}/objects/{o}.db.part", "hash", quiet=True).split()[0]
            if got != o:
                raise sd.Failed(f"object {o[:12]} did not arrive intact")
            sd.must(host, f"mv {rd}/objects/{o}.db.part {rd}/objects/{o}.db", "place", quiet=True)
            say(f"  mirror: sent {o[:12]}")
        for d in sorted(keep):
            src = os.path.join(root, "days", f"{d}.json")
            subprocess.run(["scp", "-q", *SSH, src, f"{host}:{rd}/days/{d}.json"], timeout=600)
        keep_names = " ".join(f"{d}.json" for d in keep)
        want_names = " ".join(f"{o}.db" for o in wanted)
        sd.must(host, f"cd {rd}/days && for f in *.json; do case \" {keep_names} \" in *\" $f \"*) ;; "
                      f"*) rm -f \"$f\" ;; esac; done; cd {rd}/objects && for f in *.db; do "
                      f"case \" {want_names} \" in *\" $f \"*) ;; *) rm -f \"$f\" ;; esac; done; true",
                "prune the mirror", quiet=True)
        say(f"  mirror {host}:{rd} holds the same {len(keep)} day(s)")
        return 0
    except (sd.Failed, subprocess.SubprocessError, OSError) as err:
        say(f"  MIRROR FAILED: {err}")
        return 1


def status(plan):
    """[SPEC-STAR-086]: loud when the backup or its mirror has stopped."""
    b = plan["backups"]
    limit = dt.timedelta(hours=b.get("max_age_hours", 30))
    days = os.path.join(at_root(b["dir"]), "days")
    bad = []
    newest = sorted(f[:-5] for f in os.listdir(days) if f.endswith(".json")) if os.path.isdir(days) else []
    if not newest:
        bad.append("the hub has never been backed up")
    else:
        with open(os.path.join(days, f"{newest[-1]}.json"), encoding="utf-8") as fh:
            age = now() - dt.datetime.fromisoformat(json.load(fh)["taken_at"])
        say(f"  hub backup: newest {newest[-1]}, {age.total_seconds() / 3600:.0f} h old")
        if age > limit:
            bad.append(f"the newest hub backup is {age.days} day(s) old")
    m = b.get("mirror")
    if m:
        rc, out = sd.ssh(m["host"], f"ls -1 {m['dir']}/days", quiet=True)
        there = sorted(f[:-5] for f in out.split() if f.endswith(".json")) if rc == 0 else []
        say(f"  mirror: newest {there[-1] if there else 'NONE'}" + ("" if rc == 0 else " (unreachable)"))
        if not there or (newest and there[-1] != newest[-1]):
            bad.append("the mirror is behind the hub's backups, or unreachable")
    runs = at_root(plan["runs"])
    if os.path.exists(os.path.join(runs, "state.json")):
        st = load_state(plan)
        for name, e in sorted(st.get("nodes", {}).items()):
            age = now() - dt.datetime.fromisoformat(e["at"])
            say(f"  {name}: last synced {age.days} day(s) ago")
    for x in bad:
        say(f"  STALE: {x}")
    say("RESULT status: " + ("STALE -- " + "; ".join(bad) if bad else "backups current"))
    return 1 if bad else 0



def take(rest, flag, valued=True):
    """Remove `flag` (and its value) from `rest`: its value, or whether it was there."""
    if flag not in rest:
        return (None if valued else False), rest
    i = rest.index(flag)
    if not valued:
        return True, rest[:i] + rest[i + 1:]
    if i + 1 >= len(rest):
        raise SystemExit(f"{flag} takes a value")
    return rest[i + 1], rest[:i] + rest[i + 2:]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(at_root(argv[0]), encoding="utf-8") as fh:
        plan = json.load(fh)
    cmd, rest = argv[1], argv[2:]
    given, rest = take(rest, "--run")
    excuse, rest = take(rest, "--console-pid")
    bulk, rest = take(rest, "--approve-all", valued=False)
    by, rest = take(rest, "--by")
    only, rest = take(rest, "--nodes")
    if only is not None:
        only = [n for n in only.split(",") if n]
    if cmd == "snapshot":
        return snapshot(plan, only)
    if cmd == "backup":
        return backup(plan)
    if cmd == "status":
        return status(plan)
    if cmd not in ("merge", "items", "verdict", "approve-all", "patch", "rehearse", "commit"):
        print(__doc__)
        return 2
    run = newest_run(plan, given)
    if cmd == "merge":
        return merge(plan, run)
    if cmd == "items":
        return show_items(run)
    if cmd == "verdict":
        if len(rest) != 2:
            raise SystemExit("verdict takes an item id and `approved` or `chose:NODE`")
        x = give_verdict(run, rest[0], rest[1], by or "item")
        say(f"{x['table']} {x['key']}: {rest[1]}")
        return 0
    if cmd == "approve-all":
        kinds = approve_all(run, by or "approve-all")
        say("approved: " + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "nothing was waiting"))
        return 0
    if cmd == "patch":
        return patch(plan, run)
    return distribute(plan, run, rest, cmd == "commit", excuse, bulk)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
