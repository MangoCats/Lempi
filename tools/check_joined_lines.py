# SPDX-License-Identifier: AGPL-3.0-or-later
"""
No Rust string literal holds a run of eight or more spaces between two words,
unless its line says the columns are meant: a trailing `// columns` comment.

**The run is what a deleted line break leaves behind.** A literal continued
with `\\` and a new line reads as one space, because Rust drops the next
line's leading white space. Delete the backslash and the line end, and the
string still compiles and still runs, now holding the next line's whole
indent. Found 2026-11-13 in a review of `7759649..65abce9`, in fourteen
literals across the crate:

  * SQL, which runs the same and can no longer be read: `lyrics_sidecar.rs`'s
    query, a test's INSERT, the profanity and Christmas loaders;
  * messages, where the spaces reach the reader: "the player has stopped
    there 2 times in a row without getting                      past it".

Nothing caught them, because nothing is wrong with them that a compiler or a
test can see. **One way it happens was seen the same day:** Rust written
into an ordinary Python string, to be put in a file by a script. Python reads
a backslash at a line end as its own continuation and drops both, so the
script writes the joined line and reports success. The review's repair script
did exactly that to the code it was adding. The shape is always the same, so
the check is on the shape. The Lempi skin's HTML lost a line break too, and
that is not checked here: a joined HTML line holds only its indent.

**The exemption is declared on the line, not listed here.** Output laid out in
columns -- a probe's report, a fixture of `nmcli`'s padded fields -- ends with
`// columns`, so the reason and the exemption cannot drift apart
`[GDE-ARC-033]`.

Usage:
    python tools/check_joined_lines.py            every tracked .rs file (CI)
    python tools/check_joined_lines.py --staged   only the lines this commit adds
    python tools/check_joined_lines.py --min N    floor for a full scan (default 100)

A full scan reading fewer than `--min` files is reported as a broken scan, not
a clean tree (CLAUDE.md section 6). 122 are tracked on 2026-11-13.
"""

import argparse
import re
import subprocess
import sys

# Two non-space characters with eight or more spaces between them.
RUN = re.compile(r"(?<=\S)( {8,})(?=\S)")
EXEMPT = re.compile(r"//\s*columns\b")


def git(*args: str) -> str:
    """Run git, and refuse to continue on its failure rather than read empty
    output as 'nothing to check'."""
    r = subprocess.run(["git", *args], capture_output=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
        sys.exit(f"check_joined_lines: `git {' '.join(args)}` failed -- the check did not run")
    return r.stdout.decode("utf-8", "replace")


def joined(line: str) -> bool:
    """Whether `line` holds a run inside a string literal: one with an odd
    number of quotes before it. Approximate -- an escaped quote or a raw
    string can fool it -- and erring toward saying so is the safe side."""
    body = line.rstrip("\r\n")
    if body.lstrip().startswith("//") or EXEMPT.search(body):
        return False
    return any(body[: m.start(1)].count('"') % 2 == 1 for m in RUN.finditer(body))


def staged_lines():
    """(path, line number, text) for every line this commit adds to a .rs
    file, read from the index rather than the working tree."""
    diff = git("diff", "--cached", "-U0", "--diff-filter=ACMR", "--no-color", "--", "*.rs")
    path, n = None, 0
    for line in diff.split("\n"):
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            n = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            yield path, n, line[1:]
            n += 1


def tracked_lines():
    files = [p for p in git("ls-files", "-z", "--", "*.rs").split("\0") if p]
    for p in files:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    yield p, n, line
        except OSError as e:
            sys.exit(f"check_joined_lines: cannot read {p} ({e}) -- the check did not finish")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--staged", action="store_true", help="check only the lines this commit adds")
    ap.add_argument("--min", type=int, default=100,
                    help="full scan: fewer .rs files than this means the scan is broken")
    args = ap.parse_args()

    seen: set = set()
    bad = lines = 0
    for path, n, text in (staged_lines() if args.staged else tracked_lines()):
        seen.add(path)
        lines += 1
        if joined(text):
            bad += 1
            print(f"{path}:{n}: a string holds a run of spaces where a line break was")
            print(f"    {text.strip()[:120]}")

    scope = "added by this commit" if args.staged else "in tracked .rs files"
    print(f"check_joined_lines: {len(seen)} file(s), {lines} line(s) {scope}, {bad} joined")
    if bad:
        print("check_joined_lines: put the line break back (` \\` and a new line), or end the "
              "line with `// columns` where the spaces are meant")
    if not args.staged and len(seen) < args.min:
        print(f"check_joined_lines: only {len(seen)} file(s) read against a floor of {args.min} "
              "-- the scan is broken, not the tree")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
