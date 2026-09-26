#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Carry a star merge to one node by patch, keeping the node's data as it was
[SPEC-STAR-080].

Rehearses by default, with the player still running: backs up the node's live
pair into a scratch folder beside its backup folder, applies both patches to
that copy *on the node*, compares every table the patch governs with the
target's fingerprints, and removes the scratch copy. Nothing live is touched.

With --commit: stops the player, backs up the live pair into the backup
folder -- where it stays, the node's data before the merge -- rehearses both
patches against the live pair, commits both, and compares the fingerprints of
the files on disk with the target's [GDE-DEP-070]. If the second patch fails
after the first landed, the first file is restored from the backup: a node
never keeps half a switch. The player is started again whatever happened.

    python tools/star_distribute.py PLAN.json NODE [--commit]

PLAN.json, one entry per node:
  {"merge": "path/to/merge-output",   the merge's nodes/<name>/ are targets
   "patches": "path/to/patches",       <patches>/<name>/{listener,library}.patch.json
   "nodes": {"bose": {"host": "pi@bose",
                      "listener": "/var/lempi/listener.db",
                      "library": "/srv/library/library.db",
                      "backup": "/var/lempi/pre-star-2026-09-26",
                      "player": {"service": "lempi"}}}}
  "player" is {"service": UNIT}, {"stop": CMD, "start": CMD}, or {"none": true}
  -- the last checked: no process may hold either file open.
"""
import contextlib
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_merge  # noqa: E402  -- which listener tables are a node's own
import star_patch  # noqa: E402

OWN = sorted(t for t, s in star_merge.TABLES.items() if s["rule"] in (star_merge.LOCAL, star_merge.HUB))


def say(msg):
    print(msg, flush=True)


def ssh(host, cmd, quiet=False):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=10",
                        "-o", "ServerAliveCountMax=6", host, cmd],
                       capture_output=True, text=True)
    out = (r.stdout + r.stderr).rstrip()
    if not quiet and out:
        for line in out.splitlines():
            say(f"    {line}")
    return r.returncode, out


def must(host, cmd, what):
    rc, out = ssh(host, cmd)
    if rc != 0:
        raise Failed(f"{what}: exit {rc}")
    return out


class Failed(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload(host, local, remote):
    r = subprocess.run(["scp", "-q", "-o", "ServerAliveInterval=10", local, f"{host}:{remote}"])
    rc, out = ssh(host, f"sha256sum {shlex.quote(remote)}", quiet=True)
    if r.returncode or rc or out.split()[0] != sha256(local):
        raise Failed(f"upload of {local} did not arrive intact")


def local_prints(db):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        star_patch.fingerprint(db)
    return {line.split()[0]: line for line in buf.getvalue().splitlines()}


def compare(host, tool, remote_db, target_db, skip):
    """[GDE-DEP-070]: the file on the node, read back, against the target."""
    rc, out = ssh(host, f"python3 {tool} fingerprint {shlex.quote(remote_db)}", quiet=True)
    if rc:
        raise Failed(f"fingerprint of {remote_db} failed: {out}")
    got = {line.split()[0]: line for line in out.splitlines() if line.strip()}
    want = local_prints(target_db)
    bad = [t for t in sorted(want) if t not in skip and got.get(t) != want[t]]
    for t in bad:
        say(f"    DIFFERS {t}\n      node:   {got.get(t)}\n      target: {want[t]}")
    same = len([t for t in want if t not in skip]) - len(bad)
    say(f"  {os.path.basename(remote_db)}: {same} table(s) identical to the target"
        + (f", {len(bad)} differ" if bad else "")
        + (f"; not compared, the node's own: {', '.join(t for t in sorted(skip) if t in want)}" if skip else ""))
    return not bad


def holders(host, paths):
    probe = " ".join(shlex.quote(p) for p in paths)
    cmd = (f"for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -qF -e {probe.replace(' ', ' -e ')} "
           "&& echo ${p#/proc/}; done; true")   # the loop's status is the last grep's, not sudo's
    rc, out = ssh(host, f"sudo -n sh -c {shlex.quote(cmd)}", quiet=True)
    if rc:
        say("  NOTE: no sudo -- only this user's processes were checked for open files")
        rc, out = ssh(host, cmd, quiet=True)
    return [p for p in out.split() if p.isdigit()]


def mount_of(host, path):
    out = must(host, f"findmnt -no FSTYPE,OPTIONS --target {shlex.quote(os.path.dirname(path))}", "findmnt")
    fstype, opts = out.split()[0], out.split()[1].split(",")
    return fstype, "ro" in opts


def run(plan, name, commit):
    n = plan["nodes"][name]
    host, lis, lib, bk = n["host"], n["listener"], n["library"], n["backup"]
    target = os.path.join(plan["merge"], "nodes", name)
    patches = os.path.join(plan["patches"], name)
    player = n["player"]
    tools = bk + "-tools"
    tool = f"{tools}/star_patch.py"

    # [GDE-DEP-060]: say what is assumed about the target before acting on it.
    say(f"== {name} ({host}) -- {'COMMIT' if commit else 'rehearsal: nothing live is written'}")
    root = must(host, "findmnt -no FSTYPE /", "findmnt /").strip()
    remount = False
    for p in (lis, lib):
        fstype, ro = mount_of(host, p)
        say(f"  {p}: on {fstype}{', mounted read-only' if ro else ''} (root is {root})")
        if fstype == "overlay":
            raise Failed(f"{p} is on an overlay: a write there vanishes at reboot -- CLAUDE.md section 4")
        remount = remount or (ro and p == lib)
    if "service" in player:
        say(f"  player: systemd unit {player['service']}, "
            f"{ssh(host, 'systemctl is-active ' + shlex.quote(player['service']), quiet=True)[1]}")
    elif "none" in player:
        say("  player: none, to be checked by open files")
    else:
        say(f"  player: stopped by `{player['stop']}`, started by `{player['start']}`")
    must(host, f"python3 --version", "python3")

    rc, _ = ssh(host, f"test -e {shlex.quote(bk)}", quiet=True)
    if commit and rc == 0:
        raise Failed(f"{bk} exists: a backup is never written over")
    must(host, f"mkdir -p {shlex.quote(tools)}", "mkdir")
    upload(host, os.path.join(HERE, "star_patch.py"), tool)
    for half in ("listener", "library"):
        upload(host, os.path.join(patches, f"{half}.patch.json"), f"{tools}/{half}.patch.json")
    say(f"  uploaded star_patch.py and both patches to {tools}, hashes verified")

    if not commit:
        scratch = bk + "-rehearsal"
        ssh(host, f"rm -rf {shlex.quote(scratch)} && mkdir -p {shlex.quote(scratch)}", quiet=True)
        try:
            ok = True
            for half, live in (("listener", lis), ("library", lib)):
                copy = f"{scratch}/{half}.db"
                must(host, f"python3 {tool} backup {shlex.quote(live)} {copy}", f"copy of {half}")
                rc, _ = ssh(host, f"python3 {tool} apply {copy} {tools}/{half}.patch.json --commit")
                ok = ok and rc == 0 and compare(host, tool, copy, os.path.join(target, f"{half}.db"),
                                                OWN if half == "listener" else [])
        finally:
            ssh(host, f"rm -rf {shlex.quote(scratch)}", quiet=True)
        say(f"RESULT {name}: rehearsal {'CLEAN' if ok else 'NOT CLEAN'}; nothing live written")
        return 0 if ok else 1

    stopped, remounted, ok = False, False, False
    try:
        if "service" in player:
            must(host, f"sudo systemctl stop {shlex.quote(player['service'])}", "stop the player")
            stopped = True
        elif "stop" in player:
            ssh(host, player["stop"])
            stopped = True
        time.sleep(3)
        busy = holders(host, [lis, lib])
        if busy:
            raise Failed(f"process(es) {busy} still hold the databases open")
        say("  player stopped; nothing holds either file open")
        must(host, f"mkdir -p {shlex.quote(bk)}", "mkdir backup")
        for half, live in (("listener", lis), ("library", lib)):
            must(host, f"python3 {tool} backup {shlex.quote(live)} {shlex.quote(bk)}/{half}.db",
                 f"backup of {half}")
        if remount:
            must(host, f"sudo mount -o remount,rw --target {shlex.quote(os.path.dirname(lib))}", "remount rw")
            remounted = True
            say(f"  {os.path.dirname(lib)} remounted read-write for the patch")
        for half, live in (("listener", lis), ("library", lib)):
            rc, _ = ssh(host, f"python3 {tool} apply {shlex.quote(live)} {tools}/{half}.patch.json")
            if rc:
                raise Failed(f"the {half} patch does not rehearse cleanly against the live file; nothing written")
        landed = []
        for half, live in (("listener", lis), ("library", lib)):
            rc, _ = ssh(host, f"python3 {tool} apply {shlex.quote(live)} {tools}/{half}.patch.json --commit")
            if rc:
                for h, lv in landed:
                    ssh(host, f"python3 {tool} restore {shlex.quote(bk)}/{h}.db {shlex.quote(lv)}")
                raise Failed(f"the {half} patch failed; {[h for h, _ in landed] or 'nothing'} restored")
            landed.append((half, live))
        ok = compare(host, tool, lis, os.path.join(target, "listener.db"), OWN)
        ok = compare(host, tool, lib, os.path.join(target, "library.db"), []) and ok
    finally:
        if remounted:
            rc, _ = ssh(host, f"sudo mount -o remount,ro --target {shlex.quote(os.path.dirname(lib))}")
            say(f"  {os.path.dirname(lib)} back to read-only" if rc == 0 else
                f"  WARNING: {os.path.dirname(lib)} could NOT be remounted read-only")
        if stopped:
            if "service" in player:
                ssh(host, f"sudo systemctl start {shlex.quote(player['service'])}")
                time.sleep(8)
                state = ssh(host, f"systemctl is-active {shlex.quote(player['service'])}", quiet=True)[1]
                say(f"  player started again: {state}")
            else:
                # Detached whole: a backgrounded command that keeps ssh's
                # output open holds the session until it exits -- found on
                # smartboardpc 2026-09-26, where the player ran and ssh waited.
                ssh(host, f"( {player['start']} ) > /dev/null 2>&1 < /dev/null &")
                time.sleep(8)
                say(f"  player started again; holding the files: {holders(host, [lis]) or 'NOTHING'}")
    say(f"RESULT {name}: {'COMMITTED and verified' if ok else 'COMMITTED but a table DIFFERS'}; "
        f"the node's data before the merge is in {bk}")
    return 0 if ok else 1


def main(argv):
    if len(argv) not in (2, 3) or argv[2:] not in ([], ["--commit"]):
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        plan = json.load(fh)
    try:
        return run(plan, argv[1], argv[2:] == ["--commit"])
    except Failed as err:
        say(f"RESULT {argv[1]}: REFUSED -- {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
