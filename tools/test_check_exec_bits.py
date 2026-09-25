#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `tools/check_exec_bits.py`.

Built in a throwaway git repository rather than against this one, because the
thing worth proving is that the check *fails* -- on a mode-644 script, on a
staged one, on a scan too small to be real -- and this repository, correctly,
has nothing for it to fail on. A guard only ever seen passing is not yet known
to be a guard (CLAUDE.md §5).

Modes are set with `git update-index --chmod`, which is the index and not the
filesystem, so this runs the same on Windows as on Linux -- the property the
checker itself depends on.

    python tools/test_check_exec_bits.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.path.join(HERE, "check_exec_bits.py")

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


def git(repo, *args):
    subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=repo,
                   check=True, capture_output=True)


def write(repo, name, text):
    with open(os.path.join(repo, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def run(repo, *args):
    r = subprocess.run([sys.executable, CHECKER, *args], cwd=repo,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main() -> int:
    with tempfile.TemporaryDirectory() as repo:
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "test")
        git(repo, "config", "core.fileMode", "false")

        write(repo, "good.sh", "#!/bin/sh\necho good\n")
        write(repo, "bad.sh", "#!/bin/sh\necho bad\n")
        write(repo, "lib.sh", "#!/usr/bin/env bash\n#\n# Helpers. Sourced, not run.\n")
        write(repo, "late.sh", "#!/bin/sh\n" + "#\n" * 30 + "# Sourced, not run.\n")
        write(repo, "notes.txt", "no shebang here\n")
        git(repo, "add", ".")
        git(repo, "update-index", "--chmod=+x", "good.sh")

        print("full scan: one 644 script, one declared-sourced, one marker too late")
        code, out = run(repo, "--min", "1")
        check(code == 1, f"a 644 script must fail the scan, got exit {code}")
        check("not executable: bad.sh" in out, "bad.sh must be named")
        check("not executable: lib.sh" not in out,
              "a header saying 'Sourced, not run' must exempt the file")
        check("not executable: late.sh" in out,
              "the marker counts only in the header, not anywhere in the file")
        check("not executable: good.sh" not in out, "a 755 script must pass")
        check("notes.txt" not in out, "a file with no shebang is not judged")

        print("full scan after fixing both: passes")
        git(repo, "update-index", "--chmod=+x", "bad.sh", "late.sh")
        code, out = run(repo, "--min", "1")
        check(code == 0, f"every script executable must pass, got exit {code}: {out}")

        print("full scan below its floor: reported as a broken scan, not a clean tree")
        code, out = run(repo, "--min", "50")
        check(code == 1, f"3 shebang files against a floor of 50 must fail, got {code}")
        check("scan is broken" in out, "the floor failure must say what it means")

        git(repo, "commit", "-q", "-m", "baseline")

        print("--staged: a new 644 script in this commit is refused")
        write(repo, "new.sh", "#!/bin/sh\necho new\n")
        git(repo, "add", "new.sh")
        code, out = run(repo, "--staged")
        check(code == 1, f"a staged 644 script must fail, got {code}")
        check("not executable: new.sh" in out, "new.sh must be named")
        check("1 staged path(s)" in out, f"only the staged path is examined: {out}")

        print("--staged: after update-index --chmod=+x, it passes")
        git(repo, "update-index", "--chmod=+x", "new.sh")
        code, out = run(repo, "--staged")
        check(code == 0, f"the fixed staged script must pass, got {code}: {out}")

        print("--staged: judges what is staged, not the working tree")
        write(repo, "flip.sh", "plain text, no shebang yet\n")
        git(repo, "add", "flip.sh")
        write(repo, "flip.sh", "#!/bin/sh\n")  # edited after staging, not re-added
        code, out = run(repo, "--staged")
        check(code == 0, "the staged blob has no shebang, so nothing is wrong yet")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("check_exec_bits: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
