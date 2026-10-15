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
sys.path.insert(0, HERE)
# The player's own port, from the one Python mirror of it `[GDE-CLI-100]` --
# written out here as a number, it was a second definition the crate's
# guard test refused.
from lempi_control import LEMPI_DEFAULT_PORT as PORT  # noqa: E402


def say(msg: str) -> None:
    print(msg, flush=True)


class Failed(Exception):
    pass


def git_bash() -> str:
    """Git Bash, which `attended-import.sh` is written for -- never Windows'
    own `bash.exe`, in System32 or WindowsApps, which launches WSL. Which of
    them `bash` names depends on how the console was started: from Git Bash,
    Git's; from PowerShell, WSL's, and the send to bose failed
    "execvpe(/bin/bash) failed" on 2026-10-14 after the console was restarted
    from PowerShell. A POSIX host's own `bash` is used as it is."""
    if os.name != "nt":
        return shutil.which("bash") or "/bin/bash"
    windows = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, "bash.exe")
        low = os.path.normcase(os.path.abspath(p)) if d else ""
        if d and os.path.isfile(p) and not low.startswith(windows) and "windowsapps" not in low:
            return p
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", "")):
        p = os.path.join(root, "Git", "bin", "bash.exe")
        if root and os.path.isfile(p):
            return p
    raise Failed("Git Bash was not found -- attended-import.sh needs it, and Windows' own bash.exe "
                 "would start WSL instead")


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


class Stopped(Failed):
    """A person asked the console to stop the send [REQ-LIB-132]."""


# The console's job runner names a file that appears when a person presses
# Stop; a send run by hand has none, and is stopped with Ctrl-C as before.
STOP_FILE = os.environ.get("LEMPI_STOP_FILE")
_IN_FLIGHT = {"path": None}       # the audio file the copy is part-way through


def stop_asked() -> bool:
    return bool(STOP_FILE) and os.path.exists(STOP_FILE)


def check_stop(before: str) -> None:
    """Stop here if asked to: between steps, never inside one that would be
    left half-done."""
    if stop_asked():
        raise Stopped(f"stopped at your request, before {before}")


def ssh_stream(host: str, cmd: str, feed) -> tuple[int, str]:
    """`ssh`, with its input written by `feed(pipe)` as it is produced rather
    than held whole in memory first -- 712 MB was, on 2026-10-01, before a
    byte of it moved. Output goes to a file, so a chatty far end cannot fill
    a pipe and stall the send."""
    with tempfile.TemporaryFile() as out:
        # Two minutes of silence before giving up, not ssh's thirty seconds:
        # lempi02w's wifi, at -76 dBm, went quiet that long 67 minutes into a
        # 1.8 GB copy, 2026-10-15, and the whole copy was lost with it.
        p = subprocess.Popen(["ssh", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=10",
                              "-o", "ServerAliveCountMax=12", "-o", "BatchMode=yes", host, cmd],
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
        self.looked = self.start

    def write(self, b) -> int:
        # A stop is looked for twice a second while the bytes go [REQ-LIB-132]:
        # the copy is the long part, 16 minutes to a Pi Zero on wifi.
        if time.monotonic() - self.looked >= 0.5:
            self.looked = time.monotonic()
            if stop_asked():
                raise Stopped("stopped at your request, part-way through the copy")
        try:
            self.pipe.write(b)
        except OSError as e:          # BrokenPipeError, or EINVAL on Windows
            raise _Closed() from e
        self.sent += len(b)
        # Capped: a tar's headers carry it past the files' own total, and
        # past 10 it said "100%" once more for each tenth over.
        now, tenth = time.monotonic(), min(self.sent * 10 // self.total, 10)
        if tenth > self.said_tenth or now - self.said_at >= self.every:
            self.said_at, self.said_tenth = now, tenth
            rate = self.sent / max(now - self.start, 1e-6)
            left = max(self.total - self.sent, 0) / rate if rate else 0
            say(f"    sent {self.sent / 1e6:.1f} of {self.total / 1e6:.1f} MB "
                f"({min(self.sent * 100 // self.total, 100)}%), {rate / 1e6:.2f} MB/s"
                + (f", about {left / 60:.0f} min left" if left >= 60 else ""))
        return len(b)


def audio_size(audio: str, skip: frozenset = frozenset()) -> tuple[int, int]:
    """Files and bytes under `audio`, to say before the copy how big it is --
    less those in `skip`, by their path under `audio`."""
    files = size = 0
    for root, _, names in os.walk(audio):
        for n in names:
            if os.path.relpath(os.path.join(root, n), audio).replace(os.sep, "/") in skip:
                continue
            files += 1
            size += os.path.getsize(os.path.join(root, n))
    return files, size


def already_there(host: str, bundle: str, audio_root: str) -> frozenset:
    """The bundle's audio files the speaker already holds whole: at their
    place under `audio_root`, the size the bundle's copy is, and -- hashed on
    the speaker -- the bytes the payload records [REQ-LIB-134]. They are not
    sent again. A copy cut short is a file of the wrong size or hash, and is.
    Until 2026-10-15 a send started over from nothing: lempi02w's second try
    re-sent all 1,842 MB after the first had landed 88% of it."""
    payload = json.load(open(os.path.join(bundle, "payload.json"), encoding="utf-8"))
    audio = os.path.join(bundle, "audio")
    want = {}
    for e in payload.get("encodings") or []:
        rel, sha = e.get("bundle_path"), e.get("sha256")
        local = os.path.join(audio, *rel.split("/")) if rel else ""
        if rel and sha and os.path.isfile(local):
            want[rel] = (sha, os.path.getsize(local))
    if not want:
        return frozenset()
    listing = "\n".join(want).encode("utf-8") + b"\n"
    rc, out = ssh(host, f"cd {shlex.quote(audio_root)} 2>/dev/null || exit 0; "
                        "while IFS= read -r f; do [ -f \"$f\" ] && printf '%s\\t%s\\n' \"$(stat -c %s -- \"$f\")\" \"$f\"; "
                        "done; true", stdin=listing)
    # Each line names its own file, so the answer is read whatever the exit
    # status: a loop whose last file is absent exits 1, and until this was
    # seen, on lempi02w, that threw the whole answer away.
    same_size = []
    for line in out.splitlines():
        size, _, rel = line.partition("\t")
        if rel in want and size.isdigit() and int(size) == want[rel][1]:
            same_size.append(rel)
    if not same_size:
        return frozenset()
    say(f"  {len(same_size)} of the bundle's {len(want)} audio file(s) are on {host} already at full size; "
        "checking their bytes there ...")
    rc, out = ssh(host, f"cd {shlex.quote(audio_root)} && tr '\\n' '\\0' | xargs -0 sha256sum --",
                  stdin="\n".join(same_size).encode("utf-8"))
    whole = set()
    for line in out.splitlines():
        sha, _, rel = line.partition("  ")
        if rel in want and sha == want[rel][0]:
            whole.add(rel)
    return frozenset(whole)


def audio_tar(audio: str, out, skip: frozenset = frozenset()) -> None:
    """The bundle's audio as a tar stream into `out`, with sane modes -- 0644
    files, 0755 folders, whatever Windows reports -- so nothing lands
    world-writable, the fault that once made the whole library so. A file in
    `skip`, already on the speaker whole, is left out."""
    with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for root, dirs, names in os.walk(audio):
            dirs.sort()
            for d in dirs:
                ti = tar.gettarinfo(os.path.join(root, d), os.path.relpath(os.path.join(root, d), audio).replace(os.sep, "/"))
                ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = 0o755, 0, 0, "", ""
                tar.addfile(ti)
            for n in sorted(names):
                p = os.path.join(root, n)
                if os.path.relpath(p, audio).replace(os.sep, "/") in skip:
                    continue
                ti = tar.gettarinfo(p, os.path.relpath(p, audio).replace(os.sep, "/"))
                ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = 0o644, 0, 0, "", ""
                _IN_FLIGHT["path"] = ti.name
                with open(p, "rb") as fh:
                    tar.addfile(ti, fh)
                _IN_FLIGHT["path"] = None


def stage(host: str, bundle: str, where: str) -> None:
    """The payload, and covers if any, to `where` on the speaker.

    Not small, and so streamed with its progress said, as the audio is: a
    covers send to lempi02w on 2026-10-02 staged a 33.5 MB payload and
    84.8 MB of covers, four minutes over wifi with nothing on the page, and
    was taken for a hang."""
    must(host, f"rm -rf {shlex.quote(where)} && mkdir -p {shlex.quote(where)}", "make the staging folder")
    # The gzipped payload where the bundle has one, opened on the speaker:
    # the same 2026-10-02 payload was 33.4 MB as JSON and 2.4 MB gzipped.
    gz = os.path.join(bundle, "payload.json.gz")
    payload = gz if os.path.isfile(gz) else os.path.join(bundle, "payload.json")
    name = os.path.basename(payload)
    covers = os.path.join(bundle, "covers")
    files, size = audio_size(covers) if os.path.isdir(covers) else (0, 0)
    size += os.path.getsize(payload)
    say(f"  staging the payload{f' and {files} cover file(s)' if files else ''}, {size / 1e6:.1f} MB ...")

    def feed(pipe):
        with tarfile.open(fileobj=Progress(pipe, size), mode="w|", format=tarfile.PAX_FORMAT) as tar:
            tar.add(payload, name)
            if files:
                tar.add(covers, "covers")
    unpack = f"tar -xf - -C {shlex.quote(where)}" + (
        f" && gunzip -f {shlex.quote(posixpath.join(where, name))}" if name.endswith(".gz") else "")
    rc, out = ssh_stream(host, unpack, feed)
    if rc != 0:
        raise Failed(f"stage the payload on {host} failed (exit {rc}): {out[-300:]}")


# What a send leaves free on the speaker's music drive, at the least. The drive
# smartboardpc keeps its music on had 11 GB free of 1.9 TB on 2026-10-02.
FLOOR = 1 << 30


def room(host: str, audio_root: str, configured: bool, library: str, need: int) -> None:
    """Whether the audio fits where it is going, said before anything is sent
    [SPEC-STAR-094]. A configured music folder must already exist: on a drive
    mounted on demand -- smartboardpc's -- an absent folder means the drive is
    not mounted, and writing there would fill the disk underneath its mount
    point instead. The default, beside the library, is made if need be, so its
    room is measured at the library's folder."""
    if configured:
        rc, _ = ssh(host, f"test -d {shlex.quote(audio_root)}")
        if rc != 0:
            raise Failed(f"{audio_root} does not exist on {host} -- is its drive mounted? Nothing was sent")
    at = audio_root if configured else posixpath.dirname(library)
    out = must(host, f"df -P -B1 {shlex.quote(at)} | tail -1", "measure free space").split()
    free, mount = int(out[3]), out[5]
    say(f"  room: {free / 1e9:.1f} GB free on {mount}; {need / 1e6:.1f} MB of audio to send")
    if need and need + FLOOR > free:
        raise Failed(f"not enough room on {host}'s {mount}: {need / 1e6:.0f} MB of audio would leave less "
                     f"than {FLOOR >> 30} GiB free ({free / 1e9:.1f} GB now). Nothing was sent")


def import_run(host: str, library: str, where: str, audio_root: str, apply: bool) -> dict:
    cmd = (f"import_bundle --library {shlex.quote(library)} --bundle {shlex.quote(where)} "
           f"--audio-root {shlex.quote(audio_root)}" + (" --apply" if apply else ""))
    rc, out = ssh(host, cmd)
    for line in out.splitlines():
        say(f"    {line}")
    counts = {}
    for key in ("imported", "already", "awaiting", "corrupt", "holds"):
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
    check_stop("the copy")
    have = already_there(host, bundle, audio_root) if os.path.isdir(audio) else frozenset()
    if have:
        say(f"  {len(have)} audio file(s) already there whole, from an earlier send: not sent again")
    files, size = audio_size(audio, have)
    check_stop("the copy")
    if not files:
        # A bundle of what changed about music the speaker already has -- its
        # holds `[SPEC-HOLD-080]` -- carries no audio at all.
        say("  no audio to copy: the speaker has these files already")
    else:
        say(f"  copying {files} audio file(s), {size / 1e6:.1f} MB, into {audio_root} ...")
        try:
            rc, out = ssh_stream(host, f"mkdir -p {shlex.quote(audio_root)} && "
                                       f"tar -xf - -C {shlex.quote(audio_root)} --no-same-owner",
                                 lambda pipe: audio_tar(audio, Progress(pipe, size), have))
        except Stopped:
            # The file cut short goes, so nothing on the speaker is half a
            # song under a whole one's name; the ones that arrived whole stay,
            # and the next send finds them there [REQ-AND-240].
            cut = _IN_FLIGHT["path"]
            if cut:
                rc, _ = ssh(host, f"rm -f {shlex.quote(posixpath.join(audio_root, cut))}")
                say(f"  removed the file the copy was part-way through: {cut}" if rc == 0
                    else f"  WARNING: could not remove the file cut short: {cut}")
            raise
        if rc != 0:
            raise Failed(f"copy the audio on {host} failed (exit {rc}): {out[-300:]}")
    check_stop("staging the payload")
    stage(host, bundle, where)
    # The import is never stopped part-way: it is one transaction on the
    # speaker, short beside the copy, and finished is better than half-done.
    check_stop("the import")
    say("  importing ...")
    c = import_run(host, library, where, audio_root, apply=True)
    if stop_asked():
        say("  (a stop was asked during the import, which was let finish)")
    if c["rc"] != 0 or c.get("corrupt") or c.get("awaiting"):
        raise Failed(f"the import did not land whole: {c}")
    say(f"  imported {c.get('imported', 0)}, already there {c.get('already', 0)}"
        + (f", holds set {c['holds']}" if "holds" in c else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle", help="the bundle folder this PC built (payload.json, audio/)")
    ap.add_argument("host", help="the speaker, user@host")
    ap.add_argument("--library", default="/srv/library/library.db")
    ap.add_argument("--audio-root",
                    help="the speaker's music folder, where the audio goes; it must exist already. "
                         "Default: an `audio` folder beside --library, as on the Pis")
    ap.add_argument("--apply", action="store_true", help="do it; without it, only say what")
    ap.add_argument("--inside-window", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    bundle, host = os.path.abspath(args.bundle), args.host
    library = args.library
    # A speaker's own music folder where it has one elsewhere [SPEC-STAR-094]:
    # smartboardpc keeps its music on a USB drive, teacherslounge in ~/Music.
    # The default is the Pis' layout, /srv/library/audio beside the library.
    audio_root = args.audio_root or posixpath.join(posixpath.dirname(library), "audio")
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
        room(host, audio_root, bool(args.audio_root), library, audio_size(os.path.join(bundle, "audio"))[1])
        say("  " + ("read-only: copy and import inside BosePi/attended-import.sh's window, then reload"
                    if ro else "read-write: copy and import with the player playing, then reload"))
        if not args.apply:
            say("\nNothing done. Re-run with --apply to send and import it.")
            return 0
        check_stop("anything was sent")

        try:
            if ro:
                bash = git_bash()
                me = [sys.executable, os.path.abspath(__file__), bundle, host, "--library", library,
                      "--apply", "--inside-window"] + (["--audio-root", args.audio_root] if args.audio_root else [])
                # Git Bash rewrites a POSIX-looking argument it hands to a
                # Windows program -- this script, run inside the window -- into
                # a path on this PC. Every one here is the speaker's.
                env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
                r = subprocess.run([bash, os.path.join(os.path.dirname(HERE), "BosePi", "attended-import.sh"),
                                    "--host", host, "--go", "--", *me], env=env)
                if r.returncode != 0:
                    if stop_asked():
                        raise Stopped("stopped at your request inside the library's window; "
                                      "attended-import.sh says above whether B was closed")
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
    except Stopped as e:
        # Its own exit status, so the console reads a stop and not a failure.
        say(f"STOPPED: {e}. What was copied whole stays; the next send takes up the rest.")
        return 3
    except Failed as e:
        say(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
