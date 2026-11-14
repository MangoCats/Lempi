# SPDX-License-Identifier: AGPL-3.0-or-later
"""
What a commit stages, read the same way by every pre-commit check: the lines
it adds, a staged file's content, and git itself. Not run on its own.

**Written because the two checks that read added lines each parsed `git diff`
themselves, and both trusted its look.** Each found the file from a `+++ b/`
header, so `diff.mnemonicPrefix` (`+++ i/`), `diff.noprefix` (`+++ path`) or
a quoted path left no file named -- every added line was skipped and the scan
reported a clean commit. Measured 2026-11-14 in a scratch repository: with
either setting, a staged joined literal gave `0 file(s) ... 0 joined` and
exit 0, and the commit went through. The fleet-leak scan had the same parser,
so a privacy check could pass on reading nothing (CLAUDE.md sections 5, 6).

Three things now stop that:

  * the options a user's config can change are given on the command line --
    the prefixes, `core.quotePath`, an external diff, a textconv filter,
    `diff.relative`;
  * a hunk is read by its own counts, so an added line that begins `++` is
    not mistaken for a header, and a hunk that runs short is a failure;
  * what was parsed is compared, file by file, with `git diff --numstat`,
    which reports the same diff as counts. Any difference ends the run with
    DID_NOT_RUN rather than an answer.

**Exit codes, shared by every check that uses this:** 0 clean, 1 found
something, DID_NOT_RUN (2) when the check could not look. The hook tells 1 and
2 apart, so a check that could not run is never reported as a finding, nor a
finding as a check that could not run.
"""

import re
import subprocess
import sys
import traceback

DID_NOT_RUN = 2

# What a user's config may change about `git diff`'s output, fixed here.
DIFF = ["-c", "core.quotePath=false", "diff", "--cached", "--no-color", "--no-ext-diff",
        "--no-textconv", "--no-relative", "--src-prefix=a/", "--dst-prefix=b/", "-M"]

HUNK = re.compile(rb"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def utf8_output():
    """Print UTF-8 whatever the console. On Windows a pipe -- the hook under
    Git Bash -- defaults to cp1252, and printing a line holding a character
    such as an arrow raised UnicodeEncodeError mid-report, measured
    2026-11-14: the offender's path printed, then a traceback, and the
    summary never did."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def did_not_run(tool: str, why: str):
    sys.stdout.flush()
    sys.stderr.write(f"{tool}: {why} -- the check DID NOT RUN\n")
    sys.exit(DID_NOT_RUN)


def git(tool: str, *args: str, cwd=None) -> bytes:
    """Run git; its failure ends the check as DID_NOT_RUN, never as empty
    output read as 'nothing to check'."""
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if r.returncode != 0:
        # Its first lines: outside a repository `git diff` answers with its
        # whole usage, and the line that says why is the first.
        said = r.stderr.decode("utf-8", "replace").splitlines()
        sys.stderr.write("".join(f"  {l}\n" for l in said[:6]))
        if len(said) > 6:
            sys.stderr.write(f"  ... {len(said) - 6} more line(s) from git\n")
        did_not_run(tool, f"`git {' '.join(args)}` failed (exit {r.returncode})")
    return r.stdout


def run(tool: str, main):
    """`sys.exit(main())`, but an exception is DID_NOT_RUN, not exit 1 --
    which the hook would read as a finding."""
    try:
        code = main()
    except Exception:  # noqa: BLE001 -- every failure here means the same
        traceback.print_exc()
        did_not_run(tool, "it stopped on an error")
    sys.exit(code)


def _unquote(raw: bytes) -> bytes:
    """A path git wrote C-quoted: `"b/a\\"b"`, `"b/\\303\\251"`."""
    if not (raw.startswith(b'"') and raw.endswith(b'"')):
        return raw
    out, i, body = bytearray(), 0, raw[1:-1]
    simple = {ord("n"): 10, ord("t"): 9, ord("r"): 13, ord("a"): 7, ord("b"): 8,
              ord("f"): 12, ord("v"): 11, ord("\\"): 92, ord('"'): 34}
    while i < len(body):
        c = body[i]
        if c == 92 and i + 1 < len(body):
            nxt = body[i + 1]
            if 48 <= nxt <= 55 and i + 4 <= len(body):   # \ooo, one byte
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
            out.append(simple.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return bytes(out)


def _name(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _numstat(tool, pathspecs, cwd) -> dict:
    """path -> lines added, or None for a binary file."""
    out = git(tool, *DIFF, "--numstat", "-z", "--", *pathspecs, cwd=cwd)
    tokens, counts, i = out.split(b"\0"), {}, 0
    while i < len(tokens):
        t = tokens[i]
        i += 1
        if not t:
            continue
        added, _deleted, path = t.split(b"\t", 2)
        if not path:            # a rename or copy: the two names follow
            path = tokens[i + 1]
            i += 2
        counts[_name(path)] = None if added == b"-" else int(added)
    return counts


def added_lines(*pathspecs: str, tool: str, cwd=None) -> list:
    """(path, line number, text) for every line the index adds against HEAD,
    in the staged file's numbering. `pathspecs` narrow it, as for git."""
    expected = _numstat(tool, pathspecs, cwd)
    lines = git(tool, *DIFF, "-U0", "--", *pathspecs, cwd=cwd).split(b"\n")
    found, got, path, i = [], {}, None, 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith(b"diff --git "):
            path = None
        elif line.startswith(b"+++ "):
            name = _unquote(line[4:].rstrip(b"\t"))
            path = _name(name[2:]) if name.startswith(b"b/") else None
        elif line.startswith(b"@@"):
            m = HUNK.match(line)
            if not m:
                did_not_run(tool, f"could not read the staged diff at {line[:80]!r}")
            old = int(m.group(1)) if m.group(1) is not None else 1
            new = int(m.group(3)) if m.group(3) is not None else 1
            n = int(m.group(2))
            if new and path is None:    # a deletion's `+++ /dev/null` adds nothing
                did_not_run(tool, f"a hunk adds lines to no file it could name, at {line[:80]!r}")
            while old or new:
                if i >= len(lines):
                    did_not_run(tool, f"the staged diff of {path} ended inside a hunk")
                h = lines[i]
                i += 1
                if h.startswith(b"-") and old:
                    old -= 1
                elif h.startswith(b"+") and new:
                    new -= 1
                    found.append((path, n, h[1:].rstrip(b"\r").decode("utf-8", "replace")))
                    got[path] = got.get(path, 0) + 1
                    n += 1
                elif not h.startswith(b"\\"):   # "\ No newline at end of file"
                    did_not_run(tool, f"the staged diff of {path} has a hunk that does not add up")
    for p, want in expected.items():
        if want is not None and got.get(p, 0) != want:
            did_not_run(tool, f"read {got.get(p, 0)} added line(s) of {p} from the staged diff, "
                              f"where git counts {want}")
    stray = sorted(set(got) - set(expected))
    if stray:
        did_not_run(tool, f"the staged diff names {stray[0]}, which git's count of it does not")
    return found


def blob(tool: str, path: str, cwd=None) -> str:
    """A file as it is staged, which may differ from the working tree."""
    return git(tool, "cat-file", "blob", f":{path}", cwd=cwd).decode("utf-8", "replace")
