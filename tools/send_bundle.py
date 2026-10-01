#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Put a bundle on a speaker and import it, in one command `[SPEC-STAR-090]`.

    python tools/send_bundle.py <bundle dir> <user@host>            # what it would do
    python tools/send_bundle.py <bundle dir> <user@host> --apply    # do it

What the Export page's *Send what's missing* prints for each speaker, run by a
person from this PC. Vipunen itself reaches no host to change it
`[SPEC-SUI-110]`.

**The audio goes where the speaker keeps its music, before the import.**
`import_bundle` records each file where it finds it -- it copies nothing --
so importing from a bundle's own `audio/` would leave the catalogue pointing
into a folder that is deleted afterwards. The audio is streamed straight into
the speaker's audio root (`/srv/library/audio`, beside its library), files
0644 and folders 0755; the payload and covers go to a staging folder under
`/tmp`, removed after. Then `import_bundle --audio-root` binds them, every
file verified by its hash.

**A library on a read-only mount -- bose's, by design -- is written inside
`BosePi/attended-import.sh`'s window** and nowhere else: it opens the mount,
runs the copy and the import, refuses to close over a WAL catalogue
`[BOS-RUN-092]`, closes it, and reindexes MPD. This script opens no window of
its own. The player is asked to reload only once the window is shut.

On a read-write library the player keeps playing throughout, as the import
was first run against lempi02w (PI005), and is asked to reload at the end.

Without `--apply`, it states what it found on the speaker and what it would
do, uploads nothing, and writes nothing.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import posixpath
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 5720           # the player's own, as the fleet runs it (fleet/targets.env)


def say(msg: str) -> None:
    print(msg, flush=True)


class Failed(Exception):
    pass


def ssh(host: str, cmd: str, stdin: bytes | None = None) -> tuple[int, str]:
    """Run `cmd` on `host`; its output read as UTF-8, as the remote writes it
    -- not in this PC's code page, which is how a curly apostrophe crashed the
    remote reads until 2026-10-01."""
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=10",
                        "-o", "BatchMode=yes", host, cmd], input=stdin, capture_output=True)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", errors="replace").rstrip()


def must(host: str, cmd: str, what: str, stdin: bytes | None = None) -> str:
    rc, out = ssh(host, cmd, stdin)
    if rc != 0:
        raise Failed(f"{what} on {host} failed (exit {rc}): {out[-300:]}")
    return out


def audio_tar(audio: str) -> tuple[bytes, int, int]:
    """The bundle's audio as a tar stream with sane modes -- 0644 files, 0755
    folders, whatever Windows reports -- so nothing lands world-writable, the
    fault that once made the whole library so."""
    buf = io.BytesIO()
    files = size = 0
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for root, dirs, names in os.walk(audio):
            dirs.sort()
            for d in dirs:
                ti = tar.gettarinfo(os.path.join(root, d), os.path.relpath(os.path.join(root, d), audio).replace(os.sep, "/"))
                ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = 0o755, 0, 0, "", ""
                tar.addfile(ti)
            for n in sorted(names):
                p = os.path.join(root, n)
                ti = tar.gettarinfo(p, os.path.relpath(p, audio).replace(os.sep, "/"))
                ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = 0o644, 0, 0, "", ""
                with open(p, "rb") as fh:
                    tar.addfile(ti, fh)
                files += 1
                size += ti.size
    return buf.getvalue(), files, size


def stage(host: str, bundle: str, where: str) -> None:
    """The payload, and covers if any, to `where` on the speaker -- small."""
    must(host, f"rm -rf {shlex.quote(where)} && mkdir -p {shlex.quote(where)}", "make the staging folder")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        tar.add(os.path.join(bundle, "payload.json"), "payload.json")
        covers = os.path.join(bundle, "covers")
        if os.path.isdir(covers):
            tar.add(covers, "covers")
    must(host, f"tar -xf - -C {shlex.quote(where)}", "stage the payload", buf.getvalue())


def import_run(host: str, library: str, where: str, audio_root: str, apply: bool) -> dict:
    cmd = (f"import_bundle --library {shlex.quote(library)} --bundle {shlex.quote(where)} "
           f"--audio-root {shlex.quote(audio_root)}" + (" --apply" if apply else ""))
    rc, out = ssh(host, cmd)
    for line in out.splitlines():
        say(f"    {line}")
    counts = {}
    for key in ("imported", "already", "awaiting", "corrupt"):
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == key and parts[1].isdigit():
                counts[key] = int(parts[1])
    counts["rc"] = rc
    return counts


def inside(host: str, bundle: str, library: str, audio_root: str, where: str) -> int:
    """The writes: the audio into place, the payload staged, the import. Run
    directly on a read-write library, or by attended-import.sh inside its
    window on a read-only one."""
    data, files, size = audio_tar(os.path.join(bundle, "audio"))
    say(f"  copying {files} audio file(s), {size / 1e6:.1f} MB, into {audio_root} ...")
    must(host, f"mkdir -p {shlex.quote(audio_root)} && tar -xf - -C {shlex.quote(audio_root)} --no-same-owner",
         "copy the audio", data)
    stage(host, bundle, where)
    say("  importing ...")
    c = import_run(host, library, where, audio_root, apply=True)
    if c["rc"] != 0 or c.get("corrupt") or c.get("awaiting"):
        raise Failed(f"the import did not land whole: {c}")
    say(f"  imported {c.get('imported', 0)}, already there {c.get('already', 0)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle", help="the bundle folder this PC built (payload.json, audio/)")
    ap.add_argument("host", help="the speaker, user@host")
    ap.add_argument("--library", default="/srv/library/library.db")
    ap.add_argument("--apply", action="store_true", help="do it; without it, only say what")
    ap.add_argument("--inside-window", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    bundle, host = os.path.abspath(args.bundle), args.host
    library = args.library
    audio_root = posixpath.join(posixpath.dirname(library), "audio")
    where = f"/tmp/lempi-bundle-{os.path.basename(os.path.dirname(bundle)) or 'x'}"
    try:
        if not os.path.isfile(os.path.join(bundle, "payload.json")):
            raise Failed(f"{bundle} has no payload.json -- is it a bundle folder?")
        if args.inside_window:
            return inside(host, bundle, library, audio_root, where)

        # Say what is assumed about the target before acting on it [GDE-DEP-060].
        ssh_ok, _ = ssh(host, "true")
        if ssh_ok != 0:
            raise Failed(f"cannot reach {host} over ssh")
        tool = must(host, "command -v import_bundle || echo MISSING", "look for import_bundle")
        if tool.endswith("MISSING"):
            raise Failed(f"{host} has no import_bundle -- deploy it: build/deploy-appliance.sh {host}")
        opts = must(host, f"findmnt -no TARGET,FSTYPE,OPTIONS --target {shlex.quote(posixpath.dirname(library))}",
                    "findmnt").split()
        ro = "ro" in opts[2].split(",")
        payload = json.load(open(os.path.join(bundle, "payload.json"), encoding="utf-8"))
        n = len(payload.get("encodings") or [])
        say(f"{host}: library {library} on {opts[1]} at {opts[0]}, mounted {'read-only' if ro else 'read-write'}")
        say(f"  bundle: {n} encoding(s); audio into {audio_root}; payload staged in {where}")
        say("  " + ("read-only: copy and import inside BosePi/attended-import.sh's window, then reload"
                    if ro else "read-write: copy and import with the player playing, then reload"))
        if not args.apply:
            say("\nNothing done. Re-run with --apply to send and import it.")
            return 0

        if ro:
            bash = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
            me = [sys.executable, os.path.abspath(__file__), bundle, host, "--library", library,
                  "--apply", "--inside-window"]
            r = subprocess.run([bash, os.path.join(os.path.dirname(HERE), "BosePi", "attended-import.sh"),
                                "--host", host, "--go", "--", *me])
            if r.returncode != 0:
                raise Failed(f"attended-import.sh exited {r.returncode} -- see above; it says whether B was closed")
        else:
            inside(host, bundle, library, audio_root, where)

        ssh(host, f"rm -rf {shlex.quote(where)}")
        name = host.rpartition("@")[2]
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://{name}:{PORT}/library/reload", method="POST"),
                                   timeout=15).read()
            say(f"  asked {name}'s player to reload its library")
        except OSError as e:
            say(f"  WARNING: could not ask {name}'s player to reload ({e}) -- the music is imported; "
                f"run: curl -X POST http://{name}:{PORT}/library/reload")
        say(f"\nDone: {n} encoding(s) on {host}.")
        return 0
    except Failed as e:
        say(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
