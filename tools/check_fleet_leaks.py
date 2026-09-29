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
    python tools/check_fleet_leaks.py --strict --baseline tools/fleet-leaks-baseline.txt
                                                   # the tree, the accepted findings aside (CI)
    python tools/check_fleet_leaks.py --write-baseline tools/fleet-leaks-baseline.txt [--admit-new]
                                                   # shrink it; --admit-new, deliberately, to grow it

"The whole tree" is what git tracks: an ignored file cannot be leaked by a
commit, and it is what CI's checkout holds [SPEC-FCP-050].

`--staged` scans only the lines a commit *adds*, so it blocks a new leak
without tripping over everything already in the tree (history is left as-is,
decided 2026-09-28). The pre-commit hook uses `--staged --strict`.
"""
import hashlib
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


def tracked_files():
    """The files git tracks, or None outside a repository. The tree scan reads
    these and only these: a gitignored file -- `secrets/`, `data/`, `fleet/`
    -- cannot be leaked by a commit, and CI's checkout holds exactly these, so
    a baseline written here matches what CI scans [SPEC-FCP-050]."""
    r = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        return None
    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]


def walk_tree():
    files = tracked_files()
    if files is not None:
        for rel in sorted(files):
            top = rel.split("/", 1)[0]
            if (top in SKIP_DIRS or rel in SKIP_SELF or any(rel.startswith(p + "/") for p in SKIP_PATHS)
                    or os.path.splitext(rel)[1].lower() in SKIP_EXT):
                continue
            try:
                with open(os.path.join(ROOT, rel), encoding="utf-8", errors="strict") as fh:
                    for lineno, line in enumerate(fh, 1):
                        yield rel, lineno, line.rstrip("\n")
            except (UnicodeDecodeError, OSError):
                continue
        return
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


# --- the baseline [SPEC-FCP-050], [SPEC-FCP-055] ------------------------------
#
# The findings already in the tree and accepted as they stand (history left
# as-is, decided 2026-09-28), each recorded as a hash of its file and its line's
# text -- never the text. A finding whose hash is listed passes; any other
# fails. Editing an accepted line changes its hash, so touching an old leak
# removes it rather than re-accepting it.


def line_key(path: str, line: str) -> str:
    """A finding's identity: its file and its line's text, not the line's
    number, which moves whenever anything above it does."""
    return hashlib.sha256(f"{path}\0{line.rstrip()}".encode("utf-8")).hexdigest()[:24]


def read_baseline(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return {l.strip() for l in fh if l.strip() and not l.startswith("#")}


def write_baseline(path, keys):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Accepted fleet-leak findings, as hashes of file and line [SPEC-FCP-050].\n"
                 "# It only shrinks: `--write-baseline` keeps what is still found, and\n"
                 "# admits a new finding only with `--admit-new`, in a commit of its own\n"
                 "# [SPEC-FCP-055]. Written by tools/check_fleet_leaks.py; do not edit.\n")
        for k in sorted(keys):
            fh.write(k + "\n")


def arg_after(flag):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv and sys.argv.index(flag) + 1 < len(sys.argv) else None


def main():
    strict = "--strict" in sys.argv
    staged = "--staged" in sys.argv
    base_path = arg_after("--baseline")
    write_path = arg_after("--write-baseline")
    if (base_path or write_path) and staged:
        print("check_fleet_leaks: a baseline is for the whole tree, not --staged", file=sys.stderr)
        return 2

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
                hits.append((path, None, why, line.strip()[:100], None))
    else:
        print("check_fleet_leaks: scanning the whole tree.")
        for rel, lineno, line in walk_tree():
            for why in scan_line(line, deny_pat):
                hits.append((rel, lineno, why, line.strip()[:100], line_key(rel, line)))

    if write_path:
        old = read_baseline(write_path)
        found = {h[4] for h in hits}
        admit = "--admit-new" in sys.argv
        if old is None and not admit:
            print(f"check_fleet_leaks: {write_path} does not exist; creating a baseline admits every "
                  "finding, so it needs --admit-new", file=sys.stderr)
            return 2
        keep = found if admit else found & old
        write_baseline(write_path, keep)
        gone = len(old - found) if old is not None else 0
        new = len(found - old) if old is not None else len(found)
        print(f"check_fleet_leaks: wrote {write_path}: {len(keep)} accepted, {gone} no longer found and "
              f"dropped, {new if admit else 0} newly admitted" + ("" if admit or not new else
              f"; {new} new finding(s) NOT admitted -- they still fail"))
        return 0

    if base_path is not None:
        accepted = read_baseline(base_path)
        if accepted is None:
            print(f"check_fleet_leaks: BROKEN -- no baseline at {base_path}", file=sys.stderr)
            return 2
        found = {h[4] for h in hits}
        before = len(hits)
        hits = [h for h in hits if h[4] not in accepted]
        stale = len(accepted - found)
        print(f"check_fleet_leaks: {before - len(hits)} finding(s) accepted by {base_path}, "
              f"{len(hits)} not" + (f"; {stale} accepted line(s) no longer found -- "
              "`--write-baseline` drops them" if stale else "") + ".")

    if not hits:
        print("check_fleet_leaks: clean.")
        return 0

    print(f"check_fleet_leaks: {len(hits)} possible leak(s):", file=sys.stderr)
    for path, lineno, why, snippet, _key in hits:
        where = f"{path}:{lineno}" if lineno else path
        print(f"  {where}  [{why}]  {snippet}", file=sys.stderr)
    print("check_fleet_leaks: use a documentation address (192.0.2.x), a role "
          "alias, or move the detail into the gitignored fleet/. If it is a "
          "documented placeholder, add it to the allow-list here.", file=sys.stderr)
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
