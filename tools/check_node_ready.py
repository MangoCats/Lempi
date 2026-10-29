#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a node is ready for the star sync and the Export page [GDE-ADD-140].

The last step of adding a node (docs/GUIDE040-adding-a-node-to-the-fleet.md).
Each line is one thing a sync or a send will need, read from where it actually
lives -- the plan, the mesh roster, the sync's state, the console's speaker
registry, and the node itself over ssh -- and each that is missing names its
remedy. It changes nothing anywhere.

Every check here is one that was missing on `lrhtpc` and found only by its
first failure: a send that could not find `import_bundle` [LRH-DEP-020], a
catalogue never patched because no copy had been sent [SPEC-STAR-946], a
listener without the tables a sync writes [LRH-SEED-030].

    python tools/check_node_ready.py fleet/star-plan.json NODE

Exits 0 when every check passes, 1 when one does not, 2 when it cannot ask.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mesh as meshmod  # noqa: E402
import split_database  # noqa: E402  -- the listener half's tables
import star_sync as ss  # noqa: E402

LISTENER_SCRIPT = """
import sqlite3, sys
c = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
print("\\n".join(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")))
"""

failed = []


def ok(what):
    ss.say(f"  ok       {what}")


def missing(what, remedy):
    failed.append(what)
    ss.say(f"  MISSING  {what} -- {remedy}")


def on(host, cmd, stdin=None):
    r = subprocess.run(["ssh", *ss.SSH, host, cmd], input=stdin, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout.strip()


def registry_row(plan, name):
    """The node's row in the console's speaker registry [FLT-DAT-015], or None."""
    sidecar = os.path.splitext(ss.at_root(plan["hub"]["library"]))[0] + ".console.db"
    if not os.path.exists(sidecar):
        return None
    c = sqlite3.connect(f"file:{sidecar}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT * FROM sync_peers WHERE name = ?", (name,)).fetchone()
    finally:
        c.close()
    return dict(r) if r else None


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(ss.at_root(argv[0]), encoding="utf-8") as fh:
        plan = json.load(fh)
    name = argv[1]
    ss.say(f"is {name} ready for the star sync and the Export page?")

    ss.say("the sync")
    node = plan["nodes"].get(name)
    if node is None:
        missing(f"{name} in {argv[0]}", "add it with its host and member [GDE-ADD-110]")
        return 1
    ok(f"in the plan, as {node.get('host')}")
    fp = node.get("member")
    if not fp or fp.startswith("<"):
        missing("its member fingerprint in the plan", "from the console's Members list [GDE-ADD-110]")
    elif meshmod.member(ss.mesh_of(plan), fp) is None:
        missing("enrolled in the mesh", "open its pairing window, then invite it [GDE-ADD-090]")
    else:
        ok(f"enrolled in the mesh, {fp[:8]}")
    state = ss.load_state(plan)["nodes"].get(name, {})
    if ss.sent_path(state, "library_sent"):
        ok(f"a copy last sent, {state['library_sent']}" + (" (adopted)" if state.get("adopted") else ""))
    else:
        missing("a copy of its catalogue last sent: no sync can patch it",
                f"python tools/star_sync.py {argv[0]} adopt {name} --library /path/on/node/library.db [SPEC-STAR-946]")

    host = node["host"]
    rc, _ = on(host, "true")
    if rc != 0:
        missing(f"{host} reachable over ssh", "nothing else on the node can be checked")
        return 1
    rc, root = on(host, "findmnt -no FSTYPE /")
    ss.say(f"  {host}: root is {root or 'unknown'}" + (" -- writes need both layers, CLAUDE.md section 4"
                                                       if root == "overlay" else ""))
    rc, where = on(host, "command -v lempi; command -v import_bundle")
    found = where.split("\n") if where else []
    if any(p.endswith("/import_bundle") for p in found):
        ok(f"import_bundle on a non-interactive ssh's PATH, {found[-1]}")
    else:
        missing("import_bundle on a non-interactive ssh's PATH: a send looks for it there",
                "install the binaries in /usr/local/bin [GDE-ADD-055], [LRH-DEP-020]")

    ss.say("the Export page")
    row = registry_row(plan, name)
    if row is None:
        missing("a speaker in the console's registry", "Flags -> speakers, with its music folder [GDE-ADD-150]")
    else:
        lib = row["remote"].partition(":")[2]
        ok(f"a speaker in the registry, {lib}"
           + ("; in the older push" if row.get("enabled") else "; out of the older push"))
        rc, _ = on(host, f"test -s {ss.shq(lib)}")
        (ok if rc == 0 else lambda w: missing(w, "the registry names a path the node does not have"))(
            "its catalogue where the registry says")
        # No audio root is the Pis' layout: the audio in `audio/` beside the catalogue [SPEC-STAR-094].
        audio = row.get("audio_root") or lib.rsplit("/", 1)[0] + "/audio"
        rc, _ = on(host, f"test -d {ss.shq(audio)}")
        if rc == 0:
            ok(f"its music folder, {audio}" + ("" if row.get("audio_root") else " (the default, beside its catalogue)"))
        else:
            missing(f"its music folder, {audio}: a send puts the audio there",
                    "set the speaker's audio root on Flags -> speakers [SPEC-STAR-094]")
        listener = (row.get("remote_listener") or "").partition(":")[2]
        if listener:
            rc, out = on(host, f"python3 - {ss.shq(listener)}", stdin=LISTENER_SCRIPT)
            if rc != 0:
                missing("its listener's tables could be read", f"python3 on the node could not open {listener}")
            else:
                lack = sorted(set(split_database.LISTENER_TABLES + split_database.BOTH) - set(out.split("\n")))
                if lack:
                    missing(f"a whole listener half: it lacks {', '.join(lack)}",
                            "deploy a current player, which completes it on open [LRH-SEED-030]")
                else:
                    ok("a whole listener half")

    ss.say("")
    ss.say(f"RESULT {name}: " + ("ready" if not failed else f"NOT READY -- {len(failed)} missing"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
