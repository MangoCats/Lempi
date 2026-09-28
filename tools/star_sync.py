#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The routine star sync, and the hub's own backup [SPEC-STAR-085..087].

One command per stage, each working in a dated run folder under the plan's
`runs`, so a stage can be read, re-run or stopped between:

    python tools/star_sync.py PLAN snapshot           a new run: every node's listener,
                                                      and its catalogue only if it moved
    python tools/star_sync.py PLAN merge              the hub's pair and each node's copy
    python tools/star_sync.py PLAN patch              every patch, each proven offline
    python tools/star_sync.py PLAN rehearse [NODE..]  read-only, against the live files
    python tools/star_sync.py PLAN commit [NODE..]    for real: the hub, then the nodes
    python tools/star_sync.py PLAN shadow             the signed transport beside ssh, read-only
    python tools/star_sync.py PLAN backup             the hub's daily backup, and its mirror
    python tools/star_sync.py PLAN status             exits 1 when a backup has gone stale

`merge`, `patch`, `rehearse` and `commit` act on the newest run unless
`--run DIR` names one. Nothing is committed until a person has read the
run's SUMMARY.md and the merge's report.md [SPEC-STAR-070].

The plan is the fleet's real roster, so it lives untracked in `fleet/`;
`fleet-example/star-plan.json` shows its shape. The run's state -- what was
last sent to each node -- is `<runs>/state.json`.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import star_distribute as sd  # noqa: E402
import star_merge as sm  # noqa: E402
import star_patch as sp  # noqa: E402
import mesh as meshmod  # noqa: E402  -- members reached by upload [REQ-AND-330]

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


# --------------------------------------------------------------- snapshot --
def remote_fetch(host, src, dest, work, tool):
    """[SPEC-STAR-075]: backup API on the node, gzip there, bring the .gz,
    keep it only if both hashes match the node's own."""
    name = os.path.basename(src)
    line = sd.must(host, f"rm -f {work}/{name} {work}/{name}.gz && "
                         f"python3 {tool} backup {shlex.quote(src)} {work}/{name}", f"snapshot {src}", quiet=True)
    parts = line.split()
    if parts[0] != "BACKUP" or parts[-1] != "ok":
        raise sd.Failed(f"snapshot of {src}: {line}")
    db_sha = parts[3]
    gz_sha = sd.must(host, f"gzip -1 -c {work}/{name} > {work}/{name}.gz && sha256sum {work}/{name}.gz",
                     "gzip", quiet=True).split()[0]
    got = None
    for attempt in range(4):
        try:
            subprocess.run(["scp", "-q", *SSH, f"{host}:{work}/{name}.gz", dest + ".gz"], timeout=3600)
        except subprocess.TimeoutExpired:
            pass
        got = sha256(dest + ".gz") if os.path.exists(dest + ".gz") else None
        if got == gz_sha:
            break
        say(f"    retry {attempt + 1}: {name}.gz did not arrive intact")
    if got != gz_sha:
        raise sd.Failed(f"{name}: no intact copy after 4 tries")
    with gzip.open(dest + ".gz", "rb") as fi, open(dest, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    os.remove(dest + ".gz")
    if sha256(dest) != db_sha:
        raise sd.Failed(f"{name}: decompressed copy differs from the node's")
    seal(dest)
    sd.ssh(host, f"rm -f {work}/{name} {work}/{name}.gz", quiet=True)
    return db_sha


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


def snapshot(plan):
    state = load_state(plan)
    run = os.path.join(at_root(plan["runs"]), stamp())
    os.makedirs(run)
    say(f"run {run}")
    hub = plan["hub"]
    taken = {}
    # The hub, here.
    hd = os.path.join(run, hub["name"])
    for half in ("listener", "library"):
        local_backup(at_root(hub[half]), os.path.join(hd, f"{half}.db"))
        seal(os.path.join(hd, f"{half}.db"))
    hs = state.get("hub", {})
    manifest = {"hub_state_from": hub["name"], "hub_library": os.path.join(hd, "library.db"),
                "nodes": [dict(name=hub["name"], listener=os.path.join(hd, "listener.db"),
                               taken_at=now().isoformat(),
                               backups=history_of(hs.get("listener_sent"), hs.get("at", ""), os.path.join(hd, "history")))]}
    baselines = {hub["name"]: {"listener": os.path.join(hd, "listener.db"), "library": os.path.join(hd, "library.db")}}
    say(f"  {hub['name']}: both halves copied here")
    missing = {}
    for name, n in sorted(plan["nodes"].items()):
        nd = os.path.join(run, name)
        os.makedirs(nd)
        ns = state["nodes"].get(name, {})
        work = f"{n['backup_root']}/star-snap"
        tool = f"{work}/star_patch.py"
        try:
            sd.must(n["host"], f"mkdir -p {work}", "reach", quiet=True)
            sd.upload(n["host"], os.path.join(HERE, "star_patch.py"), tool)
            sha = remote_fetch(n["host"], n["listener"], os.path.join(nd, "listener.db"), work, tool)
            say(f"  {name}: listener {sha[:12]}, verified")
            # The catalogue only if it moved: its fingerprint on the node,
            # read with its WAL, against what it was last sent.
            sent = ns.get("library_sent")
            rc, out = sd.ssh(n["host"], f"python3 {tool} fingerprint {shlex.quote(n['library'])}", quiet=True)
            if sent and rc == 0 and out.strip() == quiet_prints(at_root(sent)).strip():
                lib = at_root(sent)
                say(f"  {name}: catalogue exactly as last sent, not copied")
            else:
                lib = os.path.join(nd, "library.db")
                sha = remote_fetch(n["host"], n["library"], lib, work, tool)
                say(f"  {name}: catalogue has moved since it was last sent -- copied, {sha[:12]}")
            sd.ssh(n["host"], f"rm -rf {work}", quiet=True)
        except (sd.Failed, OSError, subprocess.SubprocessError) as err:
            missing[name] = str(err)
            say(f"  {name}: MISSING from this run -- {err}")
            shutil.rmtree(nd, ignore_errors=True)
            continue
        manifest["nodes"].append(dict(
            name=name, listener=os.path.join(nd, "listener.db"), taken_at=now().isoformat(),
            backups=history_of(ns.get("listener_sent"), ns.get("at", ""), os.path.join(nd, "history")),
            catalogue=lib, files=lib, mirror=bool(n.get("mirror"))))
        baselines[name] = {"listener": os.path.join(nd, "listener.db"), "library": lib}
    # [REQ-AND-330]: enrolled phones, by what each last uploaded over the
    # members' channel -- a node reached by upload rather than ssh. Only the
    # shared tables travel, so only they are merged for it.
    mdir = meshmod.mesh_dir(at_root(hub["library"]))
    for m in meshmod.sync_members(mdir):
        name, fp = m["node"], m["fingerprint"]
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
                                      taken_at=up["uploaded_at"], backups=hist))
        baselines[name] = {"listener": os.path.join(nd, "listener.db"), "member": fp}
        say(f"  {name} ({m['name'] or 'unnamed'}): its upload of {up['uploaded_at']}, "
            f"clock {up['clock_offset_ms']:+d} ms, {up['rows']}")
    for name, path in (("manifest.json", manifest), ("baselines.json", baselines),
                       ("missing.json", missing)):
        with open(os.path.join(run, name), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(path, fh, indent=1, sort_keys=True)
    say(f"RESULT snapshot: {len(baselines)} taken" + (f", MISSING {sorted(missing)}" if missing else ""))
    return 0 if not missing else 3


# ------------------------------------------------------------ merge, patch --
def merge(plan, run):
    with open(os.path.join(run, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    return sm.main([os.path.join(run, "manifest.json"), "--out", os.path.join(run, "merge")])


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


def patch(plan, run):
    with open(os.path.join(run, "baselines.json"), encoding="utf-8") as fh:
        baselines = json.load(fh)
    summary = ["# Sync summary", "", f"Run `{os.path.basename(run)}`. Read this and `merge/report.md` "
               "before `commit` [SPEC-STAR-070].", ""]
    with open(os.path.join(run, "missing.json"), encoding="utf-8") as fh:
        missing = json.load(fh)
    if missing:
        summary += ["**Missing from this run** -- they receive nothing until the next:", ""]
        summary += [f"- {n}: {why}" for n, why in sorted(missing.items())] + [""]
    ok = True
    members = {}
    for name in sorted(baselines):
        pd = os.path.join(run, "patches", name)
        os.makedirs(pd, exist_ok=True)
        summary += [f"## {name}", ""]
        if "member" in baselines[name]:
            good, line = member_patch(baselines[name]["listener"], targets(plan, run, name)["listener"],
                                      os.path.join(pd, "member.patch.json"))
            ok = ok and good
            summary += [line, ""]
            members[name] = baselines[name]["member"]
            say(f"  {name} {line[2:]}")
            continue
        for half in ("listener", "library"):
            out = os.path.join(pd, f"{half}.patch.json")
            with contextlib.redirect_stdout(io.StringIO()):
                sp.make(baselines[name][half], targets(plan, run, name)[half], out)
            with open(out, encoding="utf-8") as fh:
                p = json.load(fh)
            rows = {e["name"]: len(e["rows"]) for e in p["tables"]}
            if not p["tables"]:
                summary.append(f"- {half}: nothing to send")
                continue
            good = prove(baselines[name][half], out, targets(plan, run, name)[half])
            ok = ok and good
            summary.append(f"- {half}: " + ", ".join(f"`{t}` {k}" for t, k in sorted(rows.items()))
                           + ("" if good else " -- **NOT PROVEN: do not commit**"))
            say(f"  {name} {half}: {sum(rows.values())} row(s), {'proven' if good else 'NOT PROVEN'}")
        summary.append("")
    with open(os.path.join(run, "SUMMARY.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(summary) + "\n")
    st = stamp_of(run)
    dplan = {"merge": os.path.join(run, "merge"), "patches": os.path.join(run, "patches"),
             "prune": plan.get("prune"), "nodes": {}, "members": members}
    for name, n in plan["nodes"].items():
        if name in baselines:
            dplan["nodes"][name] = dict(
                host=n["host"], listener=n["listener"], library=n["library"], player=n["player"],
                backup=f"{n['backup_root']}/{plan['prune']['prefix']}{st}",
                library_backup=f"{n.get('library_backup_root', n['backup_root'])}/{plan['prune']['prefix']}{st}")
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
    say(f"== {name} (a mesh member, by its next contact) -- {'COMMIT' if commit else 'rehearsal'}")
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


# ---------------------------------------------------------- the hub, here --
def console_running():
    """The Vipunen console writes to the hub's pair; it must not be running
    while the hub is patched. None when this cannot be told -- said so."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "
                            "'console\\.py|\\\\lempi\\.exe' } | ForEach-Object { $_.ProcessId }"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in r.stdout.split() if p.isdigit() and int(p) != os.getpid()]


def hub_apply(plan, run, commit):
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
    busy = console_running()
    if busy is None:
        raise SystemExit("cannot tell whether the Vipunen console is running: refusing to patch the hub")
    if busy:
        raise SystemExit(f"process(es) {busy} may be using the hub's pair: close the console first")
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


def distribute(plan, run, names, commit):
    with open(os.path.join(run, "distribute-plan.json"), encoding="utf-8") as fh:
        dplan = json.load(fh)
    hub = plan["hub"]["name"]
    members = dplan.get("members", {})
    names = names or [hub] + sorted(dplan["nodes"]) + sorted(members)
    state = load_state(plan) if commit else None
    worst = 0
    for name in names:
        if name == hub:
            rc = hub_apply(plan, run, commit)
        elif name in members:
            rc = member_offer(plan, run, name, members[name], commit)
        elif name not in dplan["nodes"]:
            say(f"RESULT {name}: not in this run")
            rc = 1
        else:
            try:
                rc = sd.run(dplan, name, commit)
            except sd.Failed as err:
                say(f"RESULT {name}: REFUSED -- {err}")
                rc = 1
        if commit and rc == 0:
            t = targets(plan, run, name)
            entry = {"listener_sent": os.path.relpath(t["listener"], ROOT),
                     "library_sent": None if name in members else os.path.relpath(t["library"], ROOT),
                     "at": now().isoformat(), "run": stamp_of(run)}
            if name == hub:
                state["hub"] = entry
            else:
                state["nodes"][name] = entry
            save_state(plan, state)
        worst = max(worst, rc)
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


# ---------------------------------------------- the signed transport, shadowed --
# [IMPL-NSH-300]: before any run depends on it, each player's snapshot is
# taken both ways and each patch rehearsed both ways, and the two must agree.
# Nothing here writes to a node: a rehearsal checks and keeps nothing.

# The merge's shared listener tables and per-machine catalogue columns, which
# lempi-core names as MERGED and MACHINE_SCOPE (held equal by test_star_merge).
SIGNED_TABLES = sorted(t for t, s in sm.TABLES.items() if s["rule"] in (sm.LWW, sm.UNION))
SIGNED_EXCLUDE = {"files": sorted(sm.MACHINE_SCOPE["files"])}


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


def differs(a_db, b_db, spec):
    """Rows held by one side and not the other, per table: `spec` maps a table
    to the columns compared. Values compared as a patch carries them."""
    out = {}
    a, b = sp.open_ro(a_db), sp.open_ro(b_db)
    try:
        for t, cols in spec.items():
            def rows(c):
                if t not in sp.tables_of(c):
                    return set()
                have = [x for x in cols if x in sp.columns(c, t)]
                return {json.dumps([sp.enc(v) for v in r]) for r in c.execute(f"SELECT {', '.join(have)} FROM {t}")}
            ra, rb = rows(a), rows(b)
            if ra != rb:
                out[t] = {"only_first": len(ra - rb), "only_second": len(rb - ra)}
    finally:
        a.close()
        b.close()
    return out


def shadow(plan, run):
    """Every planned player with a `member` fingerprint, over the signed
    transport, beside what the ssh path took for this run."""
    with open(os.path.join(run, "baselines.json"), encoding="utf-8") as fh:
        baselines = json.load(fh)
    mdir = meshmod.mesh_dir(at_root(plan["hub"]["library"]))
    found = meshmod.sync_players(mdir)
    out = os.path.join(run, "shadow")
    os.makedirs(out, exist_ok=True)
    report = ["# Signed transport, shadowed", "",
              f"Run `{stamp_of(run)}`, beside its ssh snapshot and patches [IMPL-NSH-300]. "
              "Rows that moved on a node between the two snapshots show as differences; "
              "take the shadow soon after the snapshot.", ""]
    ok = True
    for name, n in sorted(plan["nodes"].items()):
        fp = n.get("member")
        if not fp:
            report += [f"## {name}", "", "- skipped: no `member` fingerprint in the plan", ""]
            continue
        report += [f"## {name}", ""]
        if name not in baselines:
            report += ["- skipped: missing from this run's ssh snapshot", ""]
            continue
        if fp not in found:
            report += [f"- MISSING: {fp[:8]} did not answer the members' query", ""]
            ok = False
            continue
        nd = os.path.join(out, name)
        os.makedirs(nd, exist_ok=True)
        try:
            snap = meshmod.sync_step(mdir, found[fp], "snapshot", stamp_of(run))
        except (ValueError, OSError) as err:
            report += [f"- snapshot REFUSED: {err}", ""]
            ok = False
            continue
        for part, fname in (("listener", "listener.db"), ("catalogue", "summary.db")):
            p = os.path.join(nd, fname)
            if os.path.exists(p):
                os.remove(p)
            db_of(snap[part], p)
        # The inputs: the shared tables, and the catalogue as the merge reads it.
        c = sp.open_ro(baselines[name]["listener"])
        try:
            spec = {t: sp.columns(c, t) for t in SIGNED_TABLES if t in sp.tables_of(c)}
        finally:
            c.close()
        d_lis = differs(baselines[name]["listener"], os.path.join(nd, "listener.db"), spec)
        summ = {t["name"]: t["columns"] for t in snap["catalogue"]["tables"]}
        d_cat = differs(at_root(baselines[name]["library"]), os.path.join(nd, "summary.db"), summ)
        report.append("- snapshot, shared tables: " + ("the same both ways" if not d_lis else f"DIFFER {d_lis}"))
        report.append("- snapshot, catalogue as the merge reads it: "
                      + ("the same both ways" if not d_cat else f"DIFFER {d_cat}"))
        # The patches, as values, against the same target the ssh patch was made for.
        t = targets(plan, run, name)
        lis = sp.make_values(os.path.join(nd, "listener.db"), t["listener"], tables=SIGNED_TABLES)
        cat = sp.make_values(at_root(baselines[name]["library"]), t["library"], exclude=SIGNED_EXCLUDE)
        for half, p in (("listener", lis), ("catalogue", cat)):
            with open(os.path.join(nd, f"{half}.values.json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump(p, fh, separators=(",", ":"), sort_keys=True)
        counts = lambda p: {e["name"]: len(e["rows"]) for e in p["tables"]}       # noqa: E731
        ssh = {}
        for half, f in (("listener", "listener.patch.json"), ("catalogue", "library.patch.json")):
            pth = os.path.join(run, "patches", name, f)
            if os.path.isfile(pth):
                with open(pth, encoding="utf-8") as fh:
                    ssh[half] = {e["name"]: len(e["rows"]) for e in json.load(fh)["tables"]
                                 if half == "catalogue" or e["name"] in SIGNED_TABLES}
            else:
                ssh[half] = {}
        for half, p in (("listener", lis), ("catalogue", cat)):
            same = counts(p) == ssh[half]
            report.append(f"- {half} patch: {counts(p) or 'nothing'}"
                          + ("" if same else f" -- the ssh patch has {ssh[half] or 'nothing'}"))
            ok = ok and same
        try:
            got = meshmod.sync_step(mdir, found[fp], "rehearse", stamp_of(run),
                                    cat if cat["tables"] else None, lis if lis["tables"] else None)
            report.append(f"- signed rehearsal: CLEAN {got}")
        except (ValueError, OSError) as err:
            report.append(f"- signed rehearsal: REFUSED -- {err}")
            ok = False
        ok = ok and not d_lis and not d_cat
        report.append("")
        say(f"  {name}: " + ("agrees" if not (d_lis or d_cat) else "DIFFERS") + " -- see the report")
    with open(os.path.join(out, "REPORT.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(report) + "\n")
    say(f"RESULT shadow: {'every player agrees both ways' if ok else 'a player DIFFERS or refused'}; "
        f"read {out}/REPORT.md")
    return 0 if ok else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(at_root(argv[0]), encoding="utf-8") as fh:
        plan = json.load(fh)
    cmd, rest = argv[1], argv[2:]
    given = None
    if "--run" in rest:
        i = rest.index("--run")
        given = rest[i + 1]
        rest = rest[:i] + rest[i + 2:]
    if cmd == "snapshot":
        return snapshot(plan)
    if cmd == "merge":
        return merge(plan, newest_run(plan, given))
    if cmd == "patch":
        return patch(plan, newest_run(plan, given))
    if cmd in ("rehearse", "commit"):
        return distribute(plan, newest_run(plan, given), rest, cmd == "commit")
    if cmd == "shadow":
        return shadow(plan, newest_run(plan, given))
    if cmd == "backup":
        return backup(plan)
    if cmd == "status":
        return status(plan)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
