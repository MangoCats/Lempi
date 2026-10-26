#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hub repairs its own audio from a speaker `[GDE-INT-170]`.

The hub's audio has no backup of its own; the speakers hold the copies, and
each reads its own back against the record `[GDE-INT-130]`. So a file the
hub's nightly verify found damaged, missing or unreadable `[GDE-INT-115]` is
fetched from a speaker that holds it whole, and put back:

  1. the newest verify's findings, each with the SHA-256 its row records;
  2. each speaker of the star plan asked, over ssh, what its own last check
     of that file found and where it keeps it -- only an `ok` is taken;
  3. that copy hashed again on the speaker, now, so the bytes fetched are
     proven at the moment they are taken, not as of a week ago;
  4. fetched (`ssh ... cat`, so no remote path is quoted twice), under the
     speaker's transfer hold -- a shared radio pauses for it `[PI3-AIM-170]`;
  5. hashed again here, and only then put in place. A damaged copy is kept
     beside it as `<name>.damaged-<date>`, never deleted.

A dry run unless `--go`. Every file it cannot repair is said, with why, and
the exit says whether anything is left: 0 nothing to repair or all repaired,
1 some not, 2 it could not run.

    python tools/repair_library.py fleet/star-plan.json [--go]
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import lempi_db         # noqa: E402
import transfer_hold    # noqa: E402  -- a shared radio pauses for the fetch [PI3-AIM-170]
import verify_library   # noqa: E402

# Where every node keeps its own checks: FLEET001's listener column, the same
# on all of them.
NODE_LISTENER = "/var/lempi/listener.db"
MD5 = re.compile(r"^[0-9a-f]{32}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
# Long enough for the largest file over Wi-Fi; a fetch past it is ended.
FETCH_TIMEOUT = 1800


def say(text):
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def at_root(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def ssh(host, cmd, stdout=None, timeout=60):
    """`cmd` on `host`: (exit code, output). With `stdout`, the output goes to
    that file instead, as bytes. Empty standard input, as star_distribute's."""
    argv = ["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, cmd]
    try:
        if stdout is not None:
            r = subprocess.run(argv, stdout=stdout, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               timeout=timeout)
            return r.returncode, r.stderr.decode("utf-8", "replace").strip()
        r = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 124, str(e)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def findings(library, verify):
    """What the verify found not whole, each with its row's record."""
    out = []
    conn = lempi_db.connect(library, lempi_db.ROLE_LIBRARY)
    try:
        for kind in verify_library.BAD:
            for x in verify.get(kind, []):
                row = conn.execute("SELECT audio_md5, sha256, path FROM files WHERE file_id = ?1",
                                   (x["file_id"],)).fetchone()
                if row is None:
                    continue                      # gone from the catalogue since: nothing to repair
                out.append({"kind": kind, "file_id": x["file_id"], "audio_md5": row[0], "sha256": row[1],
                            "path": row[2]})
    finally:
        conn.close()
    return out


def find_copy(f, nodes, run=ssh):
    """The first node holding `f` whole: its own last check `ok`, and its bytes
    hashing to the record now. Returns (host, path, why-not lines)."""
    why = []
    if not (f["audio_md5"] and MD5.match(f["audio_md5"]) and f["sha256"] and SHA.match(f["sha256"])):
        return None, None, ["its row has no usable hash to prove a copy against"]
    q = f"SELECT path, outcome, checked_at FROM file_checks WHERE audio_md5 = '{f['audio_md5']}'"
    for name, host in nodes:
        rc, out = run(host, f"sqlite3 -readonly {NODE_LISTENER} {shlex.quote(q)}")
        if rc != 0:
            why.append(f"{name}: could not be asked ({out.splitlines()[-1] if out else rc})")
            continue
        if not out:
            why.append(f"{name}: has not checked it")
            continue
        path, outcome, at = (out.splitlines()[0].split("|") + ["", "", ""])[:3]
        if outcome != "ok":
            why.append(f"{name}: its own check found it {outcome} ({at})")
            continue
        rc, out = run(host, f"sha256sum -- {shlex.quote(path)}", timeout=600)
        got = out.split()[0] if rc == 0 and out else ""
        if got != f["sha256"]:
            why.append(f"{name}: its copy no longer matches the record" if got
                       else f"{name}: could not hash its copy ({out[-120:] if out else rc})")
            continue
        return (name, host), path, why
    return None, None, why


def put_back(f, host, remote, run=ssh, today=None, hold=transfer_hold.held_ssh):
    """Fetch, prove, and put in place. Returns None, or why it did not."""
    target = f["path"]
    os.makedirs(os.path.dirname(target), exist_ok=True)
    part = target + ".repair-part"
    with hold(host, say=say, seconds=FETCH_TIMEOUT):
        with open(part, "wb") as fh:
            rc, err = run(host, f"cat -- {shlex.quote(remote)}", stdout=fh, timeout=FETCH_TIMEOUT)
    if rc != 0:
        os.remove(part)
        return f"the fetch failed ({err[-120:] if err else rc})"
    if sha256_of(part) != f["sha256"]:
        os.remove(part)
        return "what arrived does not match the record"
    if os.path.exists(target):
        aside = f"{target}.damaged-{today or dt.date.today().isoformat()}"
        os.replace(target, aside)
        say(f"    the copy it replaces is kept as {aside}")
    os.replace(part, target)
    return None


def repair(plan, go=False, run=ssh, today=None, hold=transfer_hold.held_ssh):
    library = at_root(plan["hub"]["library"])
    verify = verify_library.newest(library)
    if verify is None:
        say("no verify result to repair from -- run tools/verify_library.py first")
        return 2
    say(f"from the verify of {verify['checked_at']}")
    todo = findings(library, verify)
    if not todo:
        say("nothing to repair: every file the verify read was whole")
        return 0
    nodes = [(n, v["host"]) for n, v in plan.get("nodes", {}).items() if v.get("member") and v.get("host")]
    left = 0
    for f in todo:
        say(f"  {f['kind']}: {f['path']}")
        src, remote, why = find_copy(f, nodes, run)
        for w in why:
            say(f"    {w}")
        if src is None:
            say("    NOT REPAIRED: no speaker holds it whole")
            left += 1
            continue
        name, host = src
        if not go:
            say(f"    would fetch it from {name} ({remote}) -- --go to repair")
            continue
        err = put_back(f, host, remote, run, today, hold)
        if err:
            say(f"    NOT REPAIRED from {name}: {err}")
            left += 1
        else:
            say(f"    REPAIRED from {name}, its bytes the record's [GDE-INT-170]")
    return 1 if left else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("plan")
    ap.add_argument("--go", action="store_true", help="fetch and put back; without it, say what would be")
    args = ap.parse_args()
    try:
        with open(args.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
        return repair(plan, args.go)
    except Exception as e:                                   # noqa: BLE001
        say(f"ERROR: could not repair: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
