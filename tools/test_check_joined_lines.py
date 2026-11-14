#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `tools/check_joined_lines.py`.

The lexer first, on the shapes a second review found the line-by-line version
blind to or wrong about (2026-11-14): a run on the second line of a literal,
an escaped quote, a char literal `'"'`, the marker inside a URL or a longer
comment, a join left by a four-space indent. Then the script itself in a
throwaway repository, because the thing worth proving is that it *fails* --
on a staged join, under each diff setting that once hid one, on a character
cp1252 cannot print -- and that a check which could not look says so with
exit 2 rather than 1. A guard only ever seen passing is not yet known to be
a guard (CLAUDE.md §5).

    python tools/test_check_joined_lines.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.path.join(HERE, "check_joined_lines.py")
sys.path.insert(0, HERE)
import check_joined_lines as cjl  # noqa: E402

FAILED = []
SP = " " * 9          # a run, written out rather than counted in each case


def check(cond, msg, seen=None):
    """`seen`, the checker's (exit code, output), is printed only on a failure."""
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)
        if seen is not None:
            print(f"     exit {seen[0]}:\n" + "\n".join("     | " + l for l in seen[1].splitlines()[-25:]))


def lines_of(src):
    return [line for line, _col, _w in cjl.runs(src)]


def lexer():
    check(lines_of(f'let s = "a b{SP}c";\n') == [1], "a run inside a one-line literal is found")
    check(lines_of(f'let s = "a b{SP}c"; // columns\n') == [],
          "a trailing `// columns` exempts its line")
    check(lines_of(f'let s = "a b{SP}c"; //columns\n') == [], "the marker without its space counts too")

    # A run on a literal's second line, after a `\` continuation: no quote on
    # that line, so the per-line quote count never saw it.
    src = f'let t = "first part \\\n             second part,{SP}joined third part";\n'
    check(lines_of(src) == [2], f"a run on a continued line is found: {lines_of(src)}")
    # The shape that shipped in player/android/src/lib.rs:371.
    src = 'info!("{} covers, \\\n     {} bad covers, \\n             {} passage set");\n'
    check(lines_of(src) == [2], "`\\n` and an indent where a line break was is found")

    check(lines_of(f'let u = "say \\"{SP}hi";\n') == [1], "an escaped quote does not hide a run")
    check(lines_of(f"let v = format!(\"{{}}\", '\"'); let w = 1;{SP}// aligned\n") == [],
          "a char literal '\"' does not open a string")
    check(lines_of(f"let c = '\\''; let s = \"a{SP}b\";\n") == [1], "an escaped char literal is passed over")
    check(lines_of(f"fn f<'a>(x: &'a str) {{ let s = \"a{SP}b\"; }}\n") == [1],
          "a lifetime is not a char literal, and the string after it is read")
    check(lines_of(f'eprintln!("{SP}hint: run it again");\n') == [],
          "spaces leading a literal are indentation, not a join")
    check(lines_of(f'let s = "trailing{SP}";\n') == [], "spaces ending a literal are not a join")

    check(lines_of(f'let q = "http://columns.example{SP}joined";\n') == [1],
          "`//columns` inside a string is not the marker")
    check(lines_of(f'let q = "a{SP}b"; // columns of the matrix\n') == [1],
          "a longer comment that begins with `columns` is not the marker")
    check(lines_of(f'/// say "{SP}hi\nfn f() {{}}\n') == [], "quotes in a doc comment open nothing")
    check(lines_of(f'/* " /* " */ " */ let s = "a{SP}b";\n') == [1],
          "a nested block comment is passed over, quotes and all")

    check(lines_of('const Q: &str = "SELECT a     FROM t";\n') == [1],
          "five spaces -- a module-level join, indent four -- are found")
    check(lines_of('const Q: &str = "SELECT a    FROM t";\n') == [], "four are not")

    check(lines_of(f'let s = "a\\x20{SP}b";\n') == [], "a run spelled `\\x20` first is meant")
    check(lines_of(f'let s = "a{SP}\\x20b";\n') == [1], "a `\\x20` after the run does not exempt it")

    # A literal that holds real line breaks is verbatim after its first line.
    src = f'conn.execute_batch("CREATE TABLE t (\n    a{SP}INTEGER,\n    b{SP}TEXT)")?;\n'
    check(lines_of(src) == [], "a schema laid out in columns inside a multi-line literal is not read")
    src = f'let s = "a{SP}b\n    c";\n'
    check(lines_of(src) == [1], "a multi-line literal's first line is still read")
    src = f'let s = "a \\\\\n    b{SP}c";\n'
    check(lines_of(src) == [], "an escaped backslash ends the line for real: the next is verbatim")

    check(lines_of(f'let r = r#"say "hi"{SP}there"#; let x = 1;{SP}// note\n') == [1],
          "a raw string is read to its own closing, quotes inside and all")
    check(lines_of(f'let r = r"a\n    b{SP}c";\n') == [], "a raw string has no continuations")
    check(lines_of(f'let r#type = 1; let s = "a{SP}b";\n') == [1], "a raw identifier is not a raw string")
    check(lines_of(f'let b = b"a{SP}b"; let c = c"x{SP}y";\n') == [1, 1],
          "byte and C strings are read")
    check(lines_of(f'let s = "a{SP}b";\r\nlet t = "c";\r\n') == [1], "CRLF sources number the same")


def git(repo, *args):
    subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=repo, check=True,
                   capture_output=True)


def write(repo, name, text):
    path = os.path.join(repo, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def run(repo, *args, env=None):
    """Through a pipe, as the hook runs it -- on Windows the pipe is where
    cp1252 bit -- decoded here as the UTF-8 the checker now writes."""
    r = subprocess.run([sys.executable, CHECKER, *args], cwd=repo, capture_output=True,
                       env=None if env is None else {**os.environ, **env})
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def script():
    with tempfile.TemporaryDirectory() as repo:
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "test")
        write(repo, "src/old.rs", f'fn old() {{\n    let s = "already{SP}here";\n    let t = 1;\n}}\n')
        write(repo, "src/clean.rs", 'fn clean() { let s = "fine"; }\n')
        git(repo, "add", ".")

        print("full scan: names the join, and a floor too high is a scan that did not run")
        code, out = run(repo, "--min", "1")
        check(code == 1 and "src/old.rs:2:" in out, "a tracked join fails the scan", (code, out))
        code, out = run(repo, "--min", "50")
        check(code == 2 and "scan is broken" in out, "2 files against a floor of 50 is exit 2", (code, out))

        git(repo, "commit", "-q", "-m", "baseline")

        print("--staged: only the lines this commit adds")
        write(repo, "src/old.rs", f'fn old() {{\n    let s = "already{SP}here";\n    let t = 2;\n}}\n')
        git(repo, "add", "src/old.rs")
        code, out = run(repo, "--staged")
        check(code == 0, "a join already in the tree does not block an edit beside it", (code, out))

        write(repo, "src/new.rs", 'fn new() {\n    let m = "first, \\\n             second,'
                                  f'{SP}third";\n}}\n')
        git(repo, "add", "src/new.rs")
        code, out = run(repo, "--staged")
        check(code == 1 and "src/new.rs:3:" in out, "a staged join on a continued line is refused",
              (code, out))

        print("--staged: each diff setting that once hid the file still finds it")
        for key, value in (("diff.mnemonicPrefix", "true"), ("diff.noprefix", "true"),
                           ("diff.external", "true"), ("color.diff", "always"),
                           ("core.quotePath", "true"), ("diff.relative", "true")):
            git(repo, "config", key, value)
            code, out = run(repo, "--staged")
            check(code == 1 and "src/new.rs:3:" in out, f"under {key}={value}", (code, out))
            git(repo, "config", "--unset", key)

        print("--staged: a path git would quote")
        write(repo, "src/with space/é.rs", f'fn e() {{ let s = "a{SP}b"; }}\n')
        git(repo, "add", "src/with space/é.rs")
        code, out = run(repo, "--staged")
        check("src/with space/é.rs:1:" in out, "a quoted path is named as it is", (code, out))
        git(repo, "rm", "-q", "--cached", "src/with space/é.rs")

        print("--staged: a character cp1252 cannot print")
        write(repo, "src/arrow.rs", f'fn a() {{ let t = "a → b{SP}c"; }}\n')
        git(repo, "add", "src/arrow.rs")
        code, out = run(repo, "--staged")
        check(code == 1 and "Traceback" not in out and "joined" in out,
              "the report prints whole through a pipe", (code, out))
        git(repo, "rm", "-q", "--cached", "src/arrow.rs")

        print("--staged: what is staged, not the working tree")
        git(repo, "rm", "-q", "--cached", "src/new.rs")
        write(repo, "src/later.rs", 'fn later() { let s = "fine"; }\n')
        git(repo, "add", "src/later.rs")
        write(repo, "src/later.rs", f'fn later() {{ let s = "not{SP}staged"; }}\n')
        code, out = run(repo, "--staged")
        check(code == 0, "a join only in the working tree is not this commit's", (code, out))

        print("--staged: a deletion and a rename stage cleanly")
        git(repo, "rm", "-q", "src/clean.rs")
        git(repo, "mv", "src/old.rs", "src/moved.rs")
        code, out = run(repo, "--staged")
        check(code == 0, "nothing added, nothing found", (code, out))

    with tempfile.TemporaryDirectory() as bare:
        print("--staged outside a repository: the check did not run")
        # GIT_DIR, so a temporary folder that happens to sit inside some
        # repository cannot make this pass.
        code, out = run(bare, "--staged", env={"GIT_DIR": os.path.join(bare, "no-such-repo")})
        check(code == 2 and "DID NOT RUN" in out, "git failing is exit 2, not a finding", (code, out))


def main() -> int:
    cjl.staged.utf8_output()     # this test's own report names é and → too
    lexer()
    script()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("check_joined_lines: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
