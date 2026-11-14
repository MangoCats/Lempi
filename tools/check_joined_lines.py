# SPDX-License-Identifier: AGPL-3.0-or-later
"""
No Rust string literal holds a run of five or more spaces between two words
on a line where a line break could have been, unless the line says the spaces
are meant.

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
did exactly that to the code it was adding. The Lempi skin's HTML lost a line
break too, and that is not checked here: a joined HTML line holds only its
indent.

**The file is lexed, not read line by line.** The first version counted
quotes on each line, and a second review found it blind three ways
(2026-11-14): to a run on the second line of a literal, where no quote opens
it -- which is how `player/android/src/lib.rs`'s import summary kept a `\\n`
and thirteen spaces where its line break was -- and to an escaped quote or a
char literal `'"'`, each of which flips the count. Comments, strings, raw
strings, char literals and lifetimes are told apart here as rustc tells them.

**Which lines of a literal are read.** Its first, and any after a `\\`
continuation: the lines whose leading space Rust drops, so a run there is
either typed on purpose or left by a deletion. A line after a real line break
inside a literal is verbatim -- Rust keeps it as written, and the author sees
what the reader gets. The 27 runs of that kind in the tree on 2026-11-14 are
all `CREATE TABLE` schemas laid out in columns.

**Why five.** A deleted continuation leaves the space before its `\\` plus the
next line's indent. Over the 607 continuations inside literals in the tree on
2026-11-14 that is 5 at the least -- a module-level `const`, its next line
indented four -- or 0 and 1, where the next line is not indented, which
leaves nothing to find. Eight, this check's first floor, missed fifteen.

**Where the spaces are meant, the source says so, not a list here**
`[GDE-ARC-033]`:

  * output laid out in columns -- a probe's report, a fixture of `nmcli`'s
    padded fields -- ends its line with a comment that is exactly
    `// columns`. Anchored: the word in a URL or in a longer comment is not
    the marker. Check the columns line up before writing it; the marker
    records that they do.
  * any other run of spaces that is meant spells its first space `\\x20`,
    Rust's own way of keeping a space a continuation would drop. A deletion
    never writes one, and it serves a line inside a literal, which has no
    room for a comment.

Usage:
    python tools/check_joined_lines.py            every tracked .rs file (CI)
    python tools/check_joined_lines.py --staged   only the lines this commit adds
    python tools/check_joined_lines.py --min N    floor for a full scan (default 100)

Exit 0 clean, 1 a joined string found, 2 the check could not run (git failed,
a file could not be read, the staged diff could not be read back, or a full
scan read fewer than `--min` files -- a broken scan, not a clean tree,
CLAUDE.md section 6). 122 .rs files are tracked on 2026-11-14.
"""

import argparse
import bisect
import re
import sys

import staged

TOOL = "check_joined_lines"
RUN = re.compile(r"(?<=\S)( {5,})(?=\S)")
MARK = "columns"
SPELLED = "\\x20"


def tokens(src: str):
    """(kind, start, end) for each line comment ("comment", the text after
    `//`), string literal ("str") and raw string literal ("raw"), the span
    being the literal's content between its delimiters. Block comments, char
    literals and lifetimes are passed over, so that nothing inside them is
    read as a string."""
    n, i = len(src), 0
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            yield "comment", i + 2, j
            i = j
        elif src.startswith("/*", i):                  # nested, as in Rust
            depth, i = 1, i + 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif src.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            yield "str", i + 1, min(j, n)
            i = j + 1
        elif c == "'":
            if src.startswith("\\", i + 1):            # '\n', '\'', '\u{..}'
                j = src.find("'", i + 3)
                i = n if j < 0 else j + 1
            elif i + 2 < n and src[i + 2] == "'":      # 'x', '"'
                i += 3
            else:                                      # a lifetime or a label
                i += 1
        elif c.isalnum() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word, k = src[i:j], j
            if word in ("r", "br", "cr"):
                while k < n and src[k] == "#":
                    k += 1
                if k < n and src[k] == '"':            # r"..", r#".."#, br"..", cr".."
                    close = '"' + "#" * (k - j)
                    e = src.find(close, k + 1)
                    e = n if e < 0 else e
                    yield "raw", k + 1, e
                    j = e + len(close)
            i = j                                      # b".." and c".." lex as ".."
        else:
            i += 1


def runs(src: str):
    """(line, column, width) of every run in a literal that could be a
    joined line, its exemptions applied; numbered from 1."""
    starts = [0] + [m.end() for m in re.finditer("\n", src)]
    found = list(tokens(src))
    marked = {bisect.bisect_right(starts, s) for kind, s, e in found
              if kind == "comment" and src[s:e].strip() == MARK}
    for kind, s, e in found:
        if kind == "comment":
            continue
        first = bisect.bisect_right(starts, s)
        read, offset = True, s
        for k, seg in enumerate(src[s:e].split("\n")):
            body = seg.rstrip("\r")
            line = first + k
            if read and line not in marked:
                for m in RUN.finditer(body):
                    if not body[:m.start(1)].endswith(SPELLED):
                        yield line, offset - starts[line - 1] + m.start(1) + 1, len(m.group(1))
            # An odd number of trailing backslashes is a continuation; an even
            # number is escaped backslashes and a real line break.
            read = kind == "str" and (len(body) - len(body.rstrip("\\"))) % 2 == 1
            offset += len(seg) + 1


def sources(args):
    """(path, text, the line numbers to report or None for all)."""
    if args.staged:
        added = {}
        for path, n, _text in staged.added_lines("*.rs", tool=TOOL):
            added.setdefault(path, set()).add(n)
        for path in sorted(added):
            yield path, staged.blob(TOOL, path), added[path]
        return
    listed = staged.git(TOOL, "ls-files", "-z", "--", "*.rs").decode("utf-8", "surrogateescape")
    for path in (p for p in listed.split("\0") if p):
        try:
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                yield path, f.read(), None
        except OSError as e:
            staged.did_not_run(TOOL, f"cannot read {path} ({e})")


def main() -> int:
    staged.utf8_output()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--staged", action="store_true", help="check only the lines this commit adds")
    ap.add_argument("--min", type=int, default=100,
                    help="full scan: fewer .rs files than this means the scan is broken")
    args = ap.parse_args()

    files = lines = bad = 0
    for path, src, only in sources(args):
        files += 1
        lines += len(only) if only is not None else src.count("\n") + 1
        text = src.split("\n")
        for line, col, width in runs(src):
            if only is not None and line not in only:
                continue
            bad += 1
            print(f"{path}:{line}:{col}: {width} spaces inside a string, where a line break may have been")
            print(f"    {text[line - 1].strip()[:120]}")

    scope = "added by this commit" if args.staged else "in tracked .rs files"
    print(f"{TOOL}: {files} file(s), {lines} line(s) {scope}, {bad} joined")
    if bad:
        print(f"{TOOL}: put the line break back (` \\` and a new line). Where the spaces are "
              "meant: end a line in columns with `// columns`, or spell the run's first space `\\x20`")
    if not args.staged and files < args.min:
        staged.did_not_run(TOOL, f"only {files} file(s) read against a floor of {args.min}; "
                                 "the scan is broken, not the tree")
    return 1 if bad else 0


if __name__ == "__main__":
    staged.run(TOOL, main)
