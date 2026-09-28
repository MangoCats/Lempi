#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail if private fleet detail is about to be committed `[SecurityReview N1]`.

Two instruments, as the review's §4.4 asks:

**A denylist of the real tokens**, read from the *gitignored*
`fleet/private-tokens.txt` (one token per line, `#` comments) -- real
hostnames, users, LAN addresses, MACs, the Windows user name. Holding the list
in a gitignored file rather than here keeps the checker itself from becoming a
second place the private strings live -- the same discipline
`check_forbidden_names.py` follows for the retired names `[GOV-FBN-010]`.

**Generic patterns**, always on, for the leaks a denylist would miss because no
one thought to add them: private IPv4 outside the documentation ranges, full
MAC addresses, personal `/home/<name>` and `C:\\Users\\<name>` paths, and an
ssh-target `user@host:` naming a host that is not a documented placeholder. A
small allow-list keeps the documented placeholders (RFC 5737 addresses, the
appliance's own `10.42.0.x` access-point subnet, `pi`, `<user>`, the
`speaker-*` role aliases) from being reported.

`[GOV-FBN-020]` **It says which instruments ran.** In CI there is no `fleet/`,
so the denylist is empty and only the generic patterns run -- and it prints
that it ran generic-only, rather than reporting a clean tree on half a search.

Modes:

    python tools/check_fleet_leaks.py --staged [--strict]   # added lines in the index
    python tools/check_fleet_leaks.py [--strict]             # the whole tree

`--staged` scans only the lines a commit *adds*, so it blocks a new leak
without tripping over everything already in the tree (history is left as-is,
decided 2026-09-28). The pre-commit hook uses `--staged --strict`.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DENYLIST = os.path.join("fleet", "private-tokens.txt")

SKIP_DIRS = {".git", "node_modules", "target", ".venv", "__pycache__",
             ".mypy_cache", ".gradle", "fleet"}
SKIP_PATHS = {"android/build", "android/app/build", "android/app/src/main/jniLibs",
              "data/recovery"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
            ".gz", ".xz", ".db", ".bin", ".wav", ".flac", ".mp3", ".m4a", ".ttf",
            ".woff", ".woff2", ".otf", ".jar", ".db-shm", ".db-wal"}
# This file names every pattern it hunts for, so it would flag itself; and the
# remediation plan quotes real values on purpose. Neither is a leak.
SKIP_SELF = {"tools/check_fleet_leaks.py", "tools/test_check_fleet_leaks.py"}

# --- generic patterns -------------------------------------------------------

PRIVATE_IP = re.compile(
    r"\b(?:192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")
MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
HOME = re.compile(r"/home/([a-z_][a-z0-9_-]*)", re.I)
WINUSER = re.compile(r"[Cc]:[\\/]+Users[\\/]+([^\\/\r\n\"']+)")
# An ssh-target shape: `user@host:` `[SecurityReview2 R5]`. This is what the
# generic patterns missed before -- a real login like `sw@teacherslounge:` is
# invisible to an IP/MAC/path scan. Only flagged for a host that is not a
# documented placeholder, so `pi@speaker-a:` and `someone@workshop:` pass.
USERHOST = re.compile(r"\b([a-z_][a-z0-9_.-]*)@([a-z0-9][a-z0-9.-]*):", re.I)

# Documented, non-leaking values.
IP_OK = re.compile(r"^(?:192\.0\.2\.|198\.51\.100\.|203\.0\.113\."   # RFC 5737
                   r"|10\.42\.0\."                                    # NM AP subnet
                   r"|0\.0\.0\.0$|127\.0\.0\.1$|255\.255\.255\.255$)")
MAC_OK = {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "aa:bb:cc:dd:ee:ff",
          "de:ad:be:ef:00:00", "12:34:56:78:9a:bc"}
NAME_OK = {"user", "users", "public", "default", "pi", "root", "shared",
           "someone", "you", "example"}
# Placeholder / public hosts for the `user@host:` check -- the role aliases the
# fleet-example files use, and the public git host. A real node name is not here.
USERHOST_HOST_OK = {"host", "example", "example.com", "localhost", "hub", "mirror",
                    "build-host", "workshop", "speaker-a", "speaker-b", "speaker-c",
                    "speaker-bt", "speaker-dac", "speaker-fb", "github.com", "gitlab.com"}


def generic_hits(line: str):
    """The generic-pattern reasons this line leaks, if any."""
    out = []
    for ip in PRIVATE_IP.findall(line):
        if not IP_OK.match(ip):
            out.append(f"private IP {ip}")
    for mac in MAC.findall(line):
        if mac.lower() not in MAC_OK and "x" not in mac.lower():
            out.append(f"MAC {mac}")
    for name in HOME.findall(line):
        if name.lower() not in NAME_OK and not name.startswith("<"):
            out.append(f"/home/{name}")
    for name in WINUSER.findall(line):
        name = name.strip()
        if name.lower() not in NAME_OK and not name.startswith("<"):
            out.append(rf"C:\Users\{name}")
    for user, host in USERHOST.findall(line):
        if host.lower() not in USERHOST_HOST_OK and not host.startswith("<"):
            out.append(f"{user}@{host}:")
    return out


def load_denylist():
    path = os.path.join(ROOT, DENYLIST)
    if not os.path.exists(path):
        return None
    tokens = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                tokens.append(line)
    return tokens


def denylist_hits(line: str, pattern):
    return [f"denylisted {m.group(0)!r}" for m in pattern.finditer(line)] if pattern else []


def scan_line(line, deny_pat):
    return generic_hits(line) + denylist_hits(line, deny_pat)


def staged_added_lines():
    """(path, added-line) for every line a staged commit adds. Renames and
    binary files carry no `+` text lines, so they contribute nothing."""
    diff = subprocess.run(["git", "diff", "--cached", "--unified=0", "--no-color"],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            if path and path not in SKIP_SELF:
                yield path, line[1:]


def walk_tree():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        here = os.path.relpath(dirpath, ROOT).replace(os.sep, "/")
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and (f"{here}/{d}").lstrip("./") not in SKIP_PATHS]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace(os.sep, "/")
            if rel in SKIP_SELF or os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8", errors="strict") as fh:
                    for lineno, line in enumerate(fh, 1):
                        yield rel, lineno, line.rstrip("\n")
            except (UnicodeDecodeError, OSError):
                continue


def main():
    strict = "--strict" in sys.argv
    staged = "--staged" in sys.argv

    tokens = load_denylist()
    if tokens is None:
        print(f"check_fleet_leaks: generic-only -- no {DENYLIST} (as in CI); "
              f"denylisted names are NOT checked here [GOV-FBN-020].")
        deny_pat = None
    elif not tokens:
        print(f"check_fleet_leaks: BROKEN -- {DENYLIST} exists but is empty. "
              f"Remove it to run generic-only, or fill it.", file=sys.stderr)
        return 2
    else:
        print(f"check_fleet_leaks: {len(tokens)} denylisted token(s) from {DENYLIST}, "
              f"plus the generic patterns.")
        deny_pat = re.compile("|".join(re.escape(t) for t in tokens), re.I)

    hits = []
    if staged:
        print("check_fleet_leaks: scanning the lines this commit adds.")
        for path, line in staged_added_lines():
            for why in scan_line(line, deny_pat):
                hits.append((path, None, why, line.strip()[:100]))
    else:
        print("check_fleet_leaks: scanning the whole tree.")
        for rel, lineno, line in walk_tree():
            for why in scan_line(line, deny_pat):
                hits.append((rel, lineno, why, line.strip()[:100]))

    if not hits:
        print("check_fleet_leaks: clean.")
        return 0

    print(f"check_fleet_leaks: {len(hits)} possible leak(s):", file=sys.stderr)
    for path, lineno, why, snippet in hits:
        where = f"{path}:{lineno}" if lineno else path
        print(f"  {where}  [{why}]  {snippet}", file=sys.stderr)
    print("check_fleet_leaks: use a documentation address (192.0.2.x), a role "
          "alias, or move the detail into the gitignored fleet/. If it is a "
          "documented placeholder, add it to the allow-list here.", file=sys.stderr)
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
