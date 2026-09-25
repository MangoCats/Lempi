# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Every tracked file that opens with `#!` is committed executable (100755),
unless its own header says "Sourced, not run".

**Reads the mode from git's index, not from the filesystem.** That is the
whole reason this exists as well as the CI job it replaced the body of. The
job's comment said the check "cannot live anywhere else", because Windows has
no executable bit and `[ -x "$f" ]` there passes for every file -- true of the
working tree, and not of the index, where git records `100644`/`100755` on
every platform. So the same rule can run in the pre-commit hook on the
Windows machine where both incidents started:

  * 2026-09-21, `a3c9ce7`: 151 scripts carried a shebang and no executable
    bit, found by running `LempiPi/tests/run` on Linux for the first time.
  * 2026-09-22, `175c3ef`: `.githooks/pre-commit` itself was committed
    `100644` -- a hook git will not run on Linux, added to catch a different
    fault that only showed on Linux. Fixed before push in `a437940`, whose
    message claimed nothing mechanical checked this. That was wrong: the CI
    job did, but only after a push.

**The exemption is declared by the file, not listed here.** `BosePi/lib.sh`
carries a shebang for shellcheck and is `.`-ed, never run; its header says
"Sourced, not run". Any file whose first lines say so is exempt, so the reason
and the exemption cannot drift apart the way a path list here and a comment
there could `[GDE-ARC-033]`.

Usage:
    python tools/check_exec_bits.py             every tracked file (CI)
    python tools/check_exec_bits.py --staged    what this commit adds or changes
    python tools/check_exec_bits.py --min N     floor for a full scan (default 100)

A full scan finding fewer than `--min` shebang files is reported as a broken
scan, not a clean tree: the job's own floor, kept, because an empty result
with a zero status is not success (CLAUDE.md §6).
"""

import argparse
import subprocess
import sys

MARKER = b"Sourced, not run"
HEADER_LINES = 12


def git(*args: str) -> bytes:
    """Run git, and refuse to continue on its failure rather than read empty
    output as 'nothing to check'."""
    r = subprocess.run(["git", *args], capture_output=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
        sys.exit(f"check_exec_bits: `git {' '.join(args)}` failed -- the check did not run")
    return r.stdout


def index_entries() -> dict:
    """path -> (mode, blob sha), from the index."""
    out = {}
    for rec in git("ls-files", "-s", "-z").split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, sha, _stage = meta.split()
        out[path.decode("utf-8", "surrogateescape")] = (mode.decode(), sha.decode())
    return out


def staged_paths() -> list:
    raw = git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    return [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]


def head_of_blob(sha: str) -> bytes:
    # What is being committed, which may differ from the working tree.
    return git("cat-file", "blob", sha)[:4096]


def head_of_file(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(4096)
    except OSError:
        return b""


def exempt(head: bytes) -> bool:
    return MARKER in b"\n".join(head.split(b"\n")[:HEADER_LINES])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--staged", action="store_true",
                    help="check only paths this commit adds or changes, reading staged content")
    ap.add_argument("--min", type=int, default=100,
                    help="full scan: fewer shebang files than this means the scan is broken")
    args = ap.parse_args()

    entries = index_entries()
    if args.staged:
        paths = [p for p in staged_paths() if p in entries]
        read = lambda p: head_of_blob(entries[p][1])
    else:
        paths = list(entries)
        read = head_of_file

    shebang = bad = skipped = 0
    for p in paths:
        mode, _sha = entries[p]
        if mode not in ("100644", "100755"):
            continue  # symlink or submodule: no content of its own to judge
        head = read(p)
        if not head.startswith(b"#!"):
            continue
        shebang += 1
        if mode == "100755":
            continue
        if exempt(head):
            skipped += 1
            continue
        bad += 1
        print(f"not executable: {p}")
        print(f"    fix: git update-index --chmod=+x -- \"{p}\"")

    scope = f"{len(paths)} staged path(s)" if args.staged else f"{len(paths)} tracked file(s)"
    print(f"check_exec_bits: {scope}, {shebang} open with #!, "
          f"{skipped} exempt (\"Sourced, not run\"), {bad} not executable")

    if not args.staged and shebang < args.min:
        print(f"check_exec_bits: only {shebang} shebang file(s) found against a floor of "
              f"{args.min} -- the scan is broken, not the tree")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
