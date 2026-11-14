#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `tools/staged.py`, the staged-diff reader the pre-commit checks share.

In a throwaway repository, one staged change of every shape -- an edit, an
added line that itself begins `++`, a deletion, a rename with an edit, a path
git would quote, a binary file -- read back under each diff setting that once
left the leak scan and the joined-line check reading nothing (2026-11-14).
The answer must be the same every time. And a read that cannot be trusted
must end as DID_NOT_RUN, never as an empty list (CLAUDE.md §6).

    python tools/test_staged.py
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import staged  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def git(repo, *args):
    subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=repo, check=True,
                   capture_output=True)


def write(repo, name, data):
    path = os.path.join(repo, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data.encode("utf-8") if isinstance(data, str) else data)


def read(repo):
    """added_lines, or the exit code it ended with."""
    try:
        return staged.added_lines(tool="test", cwd=repo)
    except SystemExit as e:
        return e.code


EXPECTED = sorted([
    ("a.txt", 2, "two, changed"),
    ("a.txt", 4, "++ b/not-a-header"),
    ("a.txt", 5, "+++ b/not one either"),
    ("moved.txt", 3, "third, edited in the move"),
    ("with space/é.txt", 1, "a quoted path"),
])


def main() -> int:
    staged.utf8_output()
    with tempfile.TemporaryDirectory() as repo:
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "test")
        write(repo, "a.txt", "one\ntwo\nthree\n")
        write(repo, "gone.txt", "going\n")
        write(repo, "old.txt", "first line of a long enough file\nsecond line of it\nthird\nfourth\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "baseline")

        write(repo, "a.txt", "one\ntwo, changed\nthree\n++ b/not-a-header\n+++ b/not one either\n")
        git(repo, "rm", "-q", "gone.txt")
        git(repo, "mv", "old.txt", "moved.txt")
        write(repo, "moved.txt", "first line of a long enough file\nsecond line of it\n"
                                 "third, edited in the move\nfourth\n")
        write(repo, "with space/é.txt", "a quoted path\n")
        write(repo, "blob.bin", b"\x00\x01\x02 binary\n")
        git(repo, "add", "-A")

        print("every shape of change, read back the same under each setting")
        got = read(repo)
        check(sorted(got) == EXPECTED if isinstance(got, list) else False,
              f"plain config: {got}")
        for key, value in (("diff.mnemonicPrefix", "true"), ("diff.noprefix", "true"),
                           ("diff.external", "true"), ("color.diff", "always"),
                           ("color.ui", "always"), ("core.quotePath", "true"),
                           ("diff.relative", "true"), ("diff.renames", "false")):
            git(repo, "config", key, value)
            got = read(repo)
            check(isinstance(got, list) and sorted(got) == EXPECTED, f"under {key}={value}: {got}")
            git(repo, "config", "--unset", key)

        print("a pathspec narrows it")
        got = staged.added_lines("*.txt", tool="test", cwd=repo)
        check(sorted(got) == EXPECTED, f"`*.txt` keeps every text file: {got}")
        got = staged.added_lines("a.txt", tool="test", cwd=repo)
        check([p for p, _n, _t in got] == ["a.txt"] * 3, f"one path names one file: {got}")

        print("the staged blob, not the working tree")
        write(repo, "a.txt", "edited after staging\n")
        check(staged.blob("test", "a.txt", cwd=repo).startswith("one\ntwo, changed"),
              "blob() reads the index")

    with tempfile.TemporaryDirectory() as bare:
        print("no repository: DID_NOT_RUN, not an empty answer")
        os.environ["GIT_DIR"] = os.path.join(bare, "no-such-repo")
        try:
            got = read(bare)
        finally:
            del os.environ["GIT_DIR"]
        check(got == staged.DID_NOT_RUN, f"git failing ends the read with exit 2: {got!r}")

    print("an exception inside a check is DID_NOT_RUN, not a finding")
    said = io.StringIO()
    try:
        with contextlib.redirect_stderr(said):
            staged.run("test", lambda: 1 // 0)
        code = None
    except SystemExit as e:
        code = e.code
    check(code == staged.DID_NOT_RUN, f"a crash exits 2: {code!r}")
    check("ZeroDivisionError" in said.getvalue() and "DID NOT RUN" in said.getvalue(),
          "and says what stopped it")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("staged: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
