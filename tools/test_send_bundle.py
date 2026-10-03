#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `send_bundle.py` `[SPEC-STAR-090]`, the one command that puts a
bundle on a speaker and imports it. ssh, the attended-import window and the
player's reload are faked; what is checked is the order and the shape: the
audio into the speaker's own music folder, files 0644 and folders 0755,
before an import that binds it there; a read-only library written only inside
attended-import.sh's window, and the reload only after it shuts; a dry run
that touches nothing.

    python tools/test_send_bundle.py
"""
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import send_bundle as sb  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def bundle(tmp):
    b = os.path.join(tmp, "missing-speaker-a-7", "bundle")
    album = os.path.join(b, "audio", "Artist", "Album (2001)")
    os.makedirs(album)
    for n in ("01. One.mp3", "02. It’s Two.mp3"):
        with open(os.path.join(album, n), "wb") as f:
            f.write(b"ID3" + b"\0" * 100)
    with open(os.path.join(b, "payload.json"), "w", encoding="utf-8") as f:
        json.dump({"encodings": [{"audio_md5": "a"}, {"audio_md5": "b"}]}, f)
    return b


class Fake:
    """Answers the speaker's questions; records what was asked, in order."""

    def __init__(self, ro=False, imported=2, awaiting=0, tool=True, root_exists=True, free=500 << 30):
        self.calls, self.ro, self.imported, self.awaiting, self.tool = [], ro, imported, awaiting, tool
        self.root_exists, self.free = root_exists, free

    def __call__(self, host, cmd, stdin=None):
        self.calls.append((cmd, stdin))
        if cmd.startswith("command -v import_bundle"):
            return 0, "/usr/local/bin/import_bundle" if self.tool else "MISSING"
        if cmd.startswith("findmnt"):
            return 0, f"/srv/library ext4 {'ro' if self.ro else 'rw'},noatime"
        if cmd.startswith("test -d"):
            return (0 if self.root_exists else 1), ""
        if cmd.startswith("df "):
            return 0, f"/dev/sda1 2000000000000 1 {self.free} 99% /media/mango/PortableSSD"
        if cmd.startswith("import_bundle"):
            return 0, f"  imported  {self.imported}\n  already   0\n  awaiting  {self.awaiting}\n  corrupt   0"
        return 0, ""

    def stream(self, host, cmd, feed):
        buf = io.BytesIO()
        feed(buf)
        return self(host, cmd, buf.getvalue())


def run(argv, fake, attended=None):
    saved = (sb.ssh, sb.ssh_stream, sb.subprocess.run, sys.argv, sb.urllib.request.urlopen)
    reloads = []
    sb.ssh, sb.ssh_stream = fake, fake.stream
    sb.subprocess.run = attended or saved[2]
    sb.urllib.request.urlopen = lambda req, timeout=0: reloads.append(req.full_url) or io.BytesIO(b"")
    sys.argv = ["send_bundle.py", *argv]
    try:
        rc = sb.main()
    finally:
        sb.ssh, sb.ssh_stream, sb.subprocess.run, sys.argv, sb.urllib.request.urlopen = saved
    return rc, reloads


def test_progress():
    print("a long copy says how far it has got, not nothing for 16 minutes")
    said, saved = [], sb.say
    sb.say = said.append
    try:
        p = sb.Progress(io.BytesIO(), 1000, every=3600)
        for _ in range(100):
            p.write(b"x" * 10)
    finally:
        sb.say = saved
    check(len(said) == 10 and "(100%)" in said[-1] and "MB/s" in said[0], f"once a tenth: {said}")

    class Shut:
        def write(self, b):
            raise BrokenPipeError()
    try:
        sb.Progress(Shut(), 10).write(b"x")
        check(False, "a closed far end is not swallowed as written")
    except sb._Closed:
        pass


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        b = bundle(tmp)

        print("a dry run states what it found and would do, and writes nothing")
        f = Fake()
        rc, reloads = run([b, "pi@speaker-a"], f)
        check(rc == 0 and not reloads and all(not c.startswith(("tar", "mkdir", "import_bundle", "rm"))
                                              for c, _ in f.calls), f"read-only questions only: {f.calls}")

        print("--apply on a read-write library: audio into the music folder, then the import, then a reload")
        f = Fake()
        rc, reloads = run([b, "pi@speaker-a", "--apply"], f)
        cmds = [c for c, _ in f.calls]
        audio = next(i for i, c in enumerate(cmds) if c.startswith("mkdir -p /srv/library/audio"))
        imp = next(i for i, c in enumerate(cmds) if c.startswith("import_bundle"))
        check(rc == 0 and audio < imp, f"the audio is in place before the import: {cmds}")
        check("--audio-root /srv/library/audio" in cmds[imp] and "--apply" in cmds[imp]
              and "--bundle /tmp/lempi-bundle-missing-speaker-a-7" in cmds[imp], f"bound where it lives: {cmds[imp]}")
        with tarfile.open(fileobj=io.BytesIO(f.calls[audio][1])) as t:
            members = {m.name: m for m in t.getmembers()}
        check(set(members) == {"Artist", "Artist/Album (2001)", "Artist/Album (2001)/01. One.mp3",
                               "Artist/Album (2001)/02. It’s Two.mp3"}, f"the bundle's own layout: {sorted(members)}")
        check(all(m.mode == (0o755 if m.isdir() else 0o644) for m in members.values()),
              "files 0644, folders 0755 -- never world-writable")
        check(reloads == ["http://speaker-a:5720/library/reload"]
              and any(c.startswith("rm -rf /tmp/lempi-bundle") for c in cmds), f"reloaded, staging removed: {reloads}")

        print("an import that does not land whole is a failure, said")
        rc, reloads = run([b, "pi@speaker-a", "--apply"], Fake(imported=1, awaiting=1))
        check(rc == 1 and not reloads, "awaiting audio: exit 1, no reload")

        print("a read-only library: written only inside attended-import.sh, the reload after it shuts")
        f = Fake(ro=True)
        seen = []

        envs = []

        def attended(argv, **kw):
            seen.append(argv)
            envs.append(kw.get("env") or {})
            return subprocess.CompletedProcess(argv, 0)
        rc, reloads = run([b, "pi@speaker-b", "--apply"], f, attended)
        cmds = [c for c, _ in f.calls]
        check(rc == 0 and seen and seen[0][1].endswith(os.path.join("BosePi", "attended-import.sh"))
              and seen[0][2:6] == ["--host", "pi@speaker-b", "--go", "--"] and "--inside-window" in seen[0],
              f"the window is attended-import.sh's: {seen}")
        check(not any(c.startswith(("tar", "import_bundle")) for c in cmds),
              f"nothing written outside the window: {cmds}")
        check(reloads == ["http://speaker-b:5720/library/reload"], f"and the reload after it: {reloads}")
        check(envs and envs[0].get("MSYS_NO_PATHCONV") == "1" and envs[0].get("MSYS2_ARG_CONV_EXCL") == "*",
              "Git Bash told not to rewrite the speaker's paths into this PC's")

        print("a speaker path a shell rewrote into this PC's is refused before anything is sent")
        for argv in ([b, "pi@speaker-b", "--library", "C:/Program Files/Git/srv/library/library.db",
                      "--apply", "--inside-window"],
                     [b, "pi@speaker-a", "--library", "srv/library/library.db", "--apply"]):
            f = Fake()
            rc, reloads = run(argv, f)
            check(rc == 1 and not f.calls and not reloads, f"refused, nothing asked of the speaker: {argv[3]} {f.calls}")

        print("a failed import still removes its staging folder")
        f = Fake(imported=1, awaiting=1)
        rc, _ = run([b, "pi@speaker-a", "--apply"], f)
        check(rc == 1 and any(c.startswith("rm -rf /tmp/lempi-bundle") for c, _ in f.calls), f"cleaned: {f.calls}")

        print("a speaker with no import_bundle is told so, before anything is sent")
        f = Fake(tool=False)
        rc, _ = run([b, "pi@speaker-a", "--apply"], f)
        check(rc == 1 and not any(c.startswith(("tar", "mkdir")) for c, _ in f.calls), f"refused: {f.calls}")

        print("a bundle of holds alone copies nothing, and imports [SPEC-HOLD-080]")
        hb = os.path.join(tmp, "missing-speaker-a-8", "bundle")
        os.makedirs(hb)
        with open(os.path.join(hb, "payload.json"), "w", encoding="utf-8") as fh:
            json.dump({"encodings": [{"audio_md5": "a"}]}, fh)
        f = Fake(imported=0)
        rc, reloads = run([hb, "pi@speaker-a", "--apply"], f)
        cmds = [c for c, _ in f.calls]
        check(rc == 0 and not any(c.startswith("mkdir -p /srv/library/audio") for c in cmds)
              and any(c.startswith("import_bundle") for c in cmds) and reloads,
              f"no copy, the import, a reload: {cmds}")

        print("a gzipped payload is staged gzipped, and opened on the speaker")
        import gzip
        gb = os.path.join(tmp, "missing-speaker-a-9", "bundle")
        os.makedirs(gb)
        doc = json.dumps({"encodings": [{"audio_md5": "a"}]}).encode()
        open(os.path.join(gb, "payload.json"), "wb").write(doc)
        open(os.path.join(gb, "payload.json.gz"), "wb").write(gzip.compress(doc))
        f = Fake(imported=0)
        rc, _ = run([gb, "pi@speaker-a", "--apply"], f)
        staged = next((c, s) for c, s in f.calls if c.startswith("tar -xf - -C /tmp/lempi-bundle-missing-speaker-a-9"))
        with tarfile.open(fileobj=io.BytesIO(staged[1])) as t:
            names = t.getnames()
        check(rc == 0 and names == ["payload.json.gz"]
              and staged[0].endswith("gunzip -f /tmp/lempi-bundle-missing-speaker-a-9/payload.json.gz"),
              f"the small one sent, opened there: {names} {staged[0]}")

        print("a speaker's own music folder [SPEC-STAR-094]: used, and checked before anything is sent")
        music = "/media/mango/PortableSSD/Media/Music"
        f = Fake()
        rc, _ = run([b, "pi@speaker-c", "--library", "/var/lempi/library.db", "--audio-root", music, "--apply"], f)
        cmds = [c for c, _ in f.calls]
        check(rc == 0 and any(c.startswith(f"mkdir -p {music}") for c in cmds)
              and any("--audio-root " + music in c for c in cmds if c.startswith("import_bundle")),
              f"copied into it and bound there: {cmds}")
        f = Fake(root_exists=False)
        rc, _ = run([b, "pi@speaker-c", "--library", "/var/lempi/library.db", "--audio-root", music, "--apply"], f)
        check(rc == 1 and not any(c.startswith(("mkdir", "tar", "import_bundle")) for c, _ in f.calls),
              f"absent -- an unmounted drive -- refused, nothing sent: {[c for c, _ in f.calls]}")
        f = Fake(free=(1 << 30) // 2)
        rc, _ = run([b, "pi@speaker-c", "--apply"], f)
        check(rc == 1 and not any(c.startswith(("mkdir", "tar", "import_bundle")) for c, _ in f.calls),
              "too little room after the send: refused, nothing sent")
        f = Fake(ro=True)
        seen.clear()
        rc, _ = run([b, "pi@speaker-b", "--audio-root", music, "--apply"], f, attended)
        check(seen and seen[-1][-2:] == ["--audio-root", music], f"the window is given the folder too: {seen[-1:]}")

    test_progress()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("send_bundle: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
