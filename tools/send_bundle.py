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
import tempfile
import time
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


class _Closed(Exception):
    """The far end stopped reading; its exit status and output say why."""


def ssh_stream(host: str, cmd: str, feed) -> tuple[int, str]:
    """`ssh`, with its input written by `feed(pipe)` as it is produced rather
    than held whole in memory first -- 712 MB was, on 2026-10-01, before a
    byte of it moved. Output goes to a file, so a chatty far end cannot fill
    a pipe and stall the send."""
    with tempfile.TemporaryFile() as out:
        p = subprocess.Popen(["ssh", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=10",
                              "-o", "BatchMode=yes", host, cmd],
                             stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT)
        try:
            feed(p.stdin)
            p.stdin.close()
        except _Closed:
            pass
        except BaseException:
            p.kill()
            p.wait()
            raise
        rc = p.wait()
        out.seek(0)
        return rc, out.read().decode("utf-8", errors="replace").rstrip()


class Progress:
    """A pipe that says how far it has got: about every tenth of the way, and
    at least every 30 s, with the rate and the time left. The same 712 MB took
    16 minutes to a Pi Zero 2W on wifi, and said nothing for all of them."""

    def __init__(self, pipe, total: int, every: float = 30.0):
        self.pipe, self.total, self.every = pipe, max(total, 1), every
        self.sent, self.start = 0, time.monotonic()
        self.said_at, self.said_tenth = self.start, 0

    def write(self, b) -> int:
        try:
            self.pipe.write(b)
        except OSError as e:          # BrokenPipeError, or EINVAL on Windows
            raise _Closed() from e
        self.sent += len(b)
        now, tenth = time.monotonic(), self.sent * 10 // self.total
        if tenth > self.said_tenth or now - self.said_at >= self.every:
            self.said_at, self.said_tenth = now, tenth
            rate = self.sent / max(now - self.start, 1e-6)
            left = max(self.total - self.sent, 0) / rate if rate else 0
            say(f"    sent {self.sent / 1e6:.1f} of {self.total / 1e6:.1f} MB "
                f"({min(self.sent * 100 // self.total, 100)}%), {rate / 1e6:.2f} MB/s"
                + (f", about {left / 60:.0f} min left" if left >= 60 else ""))
        return len(b)


def audio_size(audio: str) -> tuple[int, int]:
    """Files and bytes under `audio`, to say before the copy how big it is."""
    files = size = 0
    for root, _, names in os.walk(audio):
        for n in names:
            files += 1
            size += os.path.getsize(os.path.join(root, n))
    return files, size


def audio_tar(audio: str, out) -> None:
    """The bundle's audio as a tar stream into `out`, with sane modes -- 0644
    files, 0755 folders, whatever Windows reports -- so nothing lands
    world-writable, the fault that once made the whole library so."""
    with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
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
    audio = os.path.join(bundle, "audio")
    files, size = audio_size(audio)
    say(f"  copying {files} audio file(s), {size / 1e6:.1f} MB, into {audio_root} ...")
    rc, out = ssh_stream(host, f"mkdir -p {shlex.quote(audio_root)} && "
                               f"tar -xf - -C {shlex.quote(audio_root)} --no-same-owner",
                         lambda pipe: audio_tar(audio, Progress(pipe, size)))
    if rc != 0:
        raise Failed(f"copy the audio on {host} failed (exit {rc}): {out[-300:]}")
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
        # Every path here is the speaker's, so it is absolute POSIX or it is
        # wrong. On 2026-10-01 Git Bash, starting this script inside bose's
        # window, rewrote `/srv/library/library.db` to `C:/Program
        # Files/Git/srv/...`; on bose that is a relative path, and 680 MB
        # went into ~pi/C:, beside an empty library.db the import made there.
        for what, p in (("--library", library), ("the audio root", audio_root), ("the staging folder", where)):
            if not p.startswith("/") or ":" in p or "\\" in p:
                raise Failed(f"{what} {p!r} is not an absolute path on the speaker -- if it names this PC, "
                             f"a shell rewrote it (Git Bash does, without MSYS_NO_PATHCONV=1); nothing was sent")
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

        try:
            if ro:
                bash = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
                me = [sys.executable, os.path.abspath(__file__), bundle, host, "--library", library,
                      "--apply", "--inside-window"]
                # Git Bash rewrites a POSIX-looking argument it hands to a
                # Windows program -- this script, run inside the window -- into
                # a path on this PC. Every one here is the speaker's.
                env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
                r = subprocess.run([bash, os.path.join(os.path.dirname(HERE), "BosePi", "attended-import.sh"),
                                    "--host", host, "--go", "--", *me], env=env)
                if r.returncode != 0:
                    raise Failed(f"attended-import.sh exited {r.returncode} -- see above; it says whether B was closed")
            else:
                inside(host, bundle, library, audio_root, where)
        finally:
            # The staging folder goes whether or not the import landed; it
            # was left behind on bose by the failure above.
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
