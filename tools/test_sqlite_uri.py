#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A SQLite address names the file it was given.

`lempi_db.sqlite_uri` is the one place under `tools/` an address is written.
Until 2026-11-13 some forty places wrote `f"file:{path}?mode=ro"`, which names
another file where the path holds `#`, `?` or `%`.

    python tools/test_sqlite_uri.py
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import check_node_ready  # noqa: E402
import lempi_db  # noqa: E402
import passage_orphans  # noqa: E402
import remote_peek  # noqa: E402
import split_database  # noqa: E402
import star_sync  # noqa: E402

FAILED = []

# A folder's name, each a way an address written by hand goes wrong or might.
# `?` and a backslash cannot be in a Windows file name.
NAMES = ["plain", "with space", "Music #2", "pct %41", "both #2 %41", "caf\u00e9 \u00fc", "a & b = c",
         "semi;colon", "plus+plus"] + ([] if os.name == "nt" else ["what ?x", "back\\slash"])


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def made(path, name):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE t (a)")
    c.execute("INSERT INTO t VALUES (?)", (name,))
    c.commit()
    c.close()


def test_the_address_names_the_file():
    print("an address opens the file it was given, to read and to write, by connect and by ATTACH")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        for name in NAMES:
            p = os.path.join(root, name, "x.db")
            made(p, name)
        before = sorted(os.listdir(root))
        for name in NAMES:
            p = os.path.join(root, name, "x.db")
            for how, kw in (("read-only", {}), ("immutable", {"immutable": True}), ("writable", {"read_only": False})):
                address = lempi_db.sqlite_uri(p, **kw)
                try:
                    c = sqlite3.connect(address, uri=True)
                    got = c.execute("SELECT a FROM t").fetchone()[0]
                    c.close()
                    a = sqlite3.connect(":memory:", uri=True)
                    a.execute("ATTACH DATABASE ? AS x", (address,))
                    got = (got, a.execute("SELECT a FROM x.t").fetchone()[0])
                    a.close()
                except sqlite3.Error as e:
                    got = f"{type(e).__name__}: {e}"
                check(got == (name, name), f"{name!r}, {how}: {address} gave {got!r}")
            c = sqlite3.connect(lempi_db.sqlite_uri(p, read_only=False), uri=True)
            c.execute("INSERT INTO t VALUES ('written')")
            c.commit()
            c.close()
            c = sqlite3.connect(p)
            check(c.execute("SELECT count(*) FROM t").fetchone()[0] == 2, f"{name!r}: a write lands in the file itself")
            c.close()
            try:
                sqlite3.connect(lempi_db.ro_uri(p), uri=True).execute("INSERT INTO t VALUES ('no')")
                check(False, f"{name!r}: a read-only address was written through")
            except sqlite3.OperationalError:
                pass
        check(sorted(os.listdir(root)) == before, f"and no other file was made beside them: {sorted(os.listdir(root))}")

        here = os.getcwd()
        os.chdir(root)
        try:
            rel = os.path.join("Music #2", "x.db")
            check(lempi_db.ro_uri(rel) == "file:" + rel.replace("#", "%23") + "?mode=ro",
                  f"a relative path stays relative: {lempi_db.ro_uri(rel)}")
            c = sqlite3.connect(lempi_db.ro_uri(rel), uri=True)
            check(c.execute("SELECT a FROM t").fetchone()[0] == "Music #2", "and opens from where it is run")
            c.close()
        finally:
            os.chdir(here)
    check(lempi_db.ro_uri("data/library.db") == "file:data/library.db?mode=ro"
          and lempi_db.sqlite_uri("a b.db", immutable=True) == "file:a b.db?mode=ro&immutable=1"
          and lempi_db.sqlite_uri("x.db", read_only=False) == "file:x.db",
          "a path with none of the three characters has the address it always had")
    check(lempi_db.ro_uri("50%25 #1?.db") == "file:50%2525 %231%3f.db?mode=ro",
          f"% is escaped first, so the other two are escaped once: {lempi_db.ro_uri('50%25 #1?.db')}")


def test_the_copy_in_split_database_is_the_same():
    """`split_database.py` is run on a node by itself (BOSE009), so it cannot
    import the helper and writes it out. The two give one answer."""
    print("split_database.py's own copy of the address agrees with lempi_db's")
    for name in NAMES + ["what ?x", "50%25 #1?.db", "D:\\Shared Music #2\\x.db"]:
        check(split_database._ro(name) == lempi_db.ro_uri(name),
              f"{name!r}: {split_database._ro(name)} against {lempi_db.ro_uri(name)}")


def split_pair(folder):
    os.makedirs(folder, exist_ok=True)
    lib, lis = os.path.join(folder, "library.db"), os.path.join(folder, "listener.db")
    c = sqlite3.connect(lib)
    c.executescript("CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT); "
                    "CREATE TABLE files (file_id INTEGER PRIMARY KEY); "
                    "CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER); "
                    "INSERT INTO recordings VALUES ('m1', 'A Real Title'); INSERT INTO passages VALUES (1, 1);")
    c.commit()
    c.close()
    c = sqlite3.connect(lis)
    c.executescript("CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, passage_id INTEGER); "
                    "CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT); "
                    "CREATE TABLE player_state (id INTEGER PRIMARY KEY, passage_id INTEGER); "
                    "INSERT INTO listener_play_history VALUES (1, 1), (2, 99); INSERT INTO player_state VALUES (1, 99);")
    c.commit()
    c.close()
    return lib, lis


def test_a_split_pair_in_such_a_folder():
    """`lempi_db.connect`, which nearly every tool opens its database through.
    Its writable address was the worst of them: under `Music #2` it made a
    file named `Music ` and wrote there, without an error."""
    print("lempi_db.connect reads and writes a split pair in a folder named with # and %")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        folder = os.path.join(root, "Music #2 %41")
        lib, lis = split_pair(folder)
        check(lempi_db.shape(lib) == lempi_db.ROLE_LIBRARY and lempi_db.shape(lis) == lempi_db.ROLE_LISTENER,
              f"each half is seen for what it is: {lempi_db.shape(lib)} {lempi_db.shape(lis)}")
        c = lempi_db.connect(lib, lempi_db.ROLE_LIBRARY)
        check(c.execute("SELECT title FROM recordings").fetchone()[0] == "A Real Title"
              and c.execute("SELECT count(*) FROM listener_play_history").fetchone()[0] == 2,
              "read: the catalogue as main, the listener attached beside it")
        c.close()
        c = lempi_db.connect(lib, lempi_db.ROLE_LISTENER, writable=True)       # named by its peer, as jobs.py does
        c.execute("INSERT INTO listener_flags VALUES ('recording', 'm1')")
        c.commit()
        c.close()
        c = sqlite3.connect(lis)
        check(c.execute("SELECT count(*) FROM listener_flags").fetchone()[0] == 1, "a write lands in the listener itself")
        c.close()
        c = lempi_db.connect(lib, lempi_db.ROLE_LIBRARY, writable=True, peer_writable=True)
        c.execute("INSERT INTO recordings VALUES ('m2', 'Another')")
        c.execute("INSERT INTO listener_flags VALUES ('recording', 'm2')")
        c.commit()
        c.close()
        c = sqlite3.connect(lis)
        check(c.execute("SELECT count(*) FROM listener_flags").fetchone()[0] == 2, "and with both halves writable, in both")
        c.close()
        alone = os.path.join(root, "alone #3", "library.db")
        made(alone, "x")                                                    # no table of either half: EMPTY
        c = lempi_db.connect(alone, lempi_db.ROLE_LIBRARY)
        check(c.execute("SELECT a FROM t").fetchone()[0] == "x", "a file that is neither half is opened as it is")
        c.close()
        check(sorted(os.listdir(root)) == ["Music #2 %41", "alone #3"]
              and sorted(os.listdir(folder)) == ["library.db", "listener.db"],
              f"and nothing else was made: {sorted(os.listdir(root))} {sorted(os.listdir(folder))}")


def test_passage_orphans_clears_what_it_counted():
    """`passage_orphans.py --commit --library`: its writable connection was
    opened without `uri=True`, so the catalogue's address was taken for a
    file's name. On Windows, 2026-11-13: an absolute path was refused, `unable
    to open database`; a relative one attached an empty database beside a
    stray file named `file`, every table read as absent, and nothing was
    cleared -- where the dry run before it counted orphans."""
    print("passage_orphans: a commit over a split pair clears what its dry run counted")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        for folder in ("plain", "Music #2 %41"):
            lib, lis = split_pair(os.path.join(root, folder))
            conn = passage_orphans.open_pair(lis, lib, writable=False)
            counted = passage_orphans.clear(conn, dry_run=True)
            conn.close()
            check(counted == {"listener_play_history": 1, "player_state": 1}, f"{folder}: the dry run counts: {counted}")
            conn = passage_orphans.open_pair(lis, lib, writable=True)
            cleared = passage_orphans.clear(conn, dry_run=False)
            conn.commit()
            conn.close()
            check(cleared == counted, f"{folder}: the commit clears the same: {cleared}")
            c = sqlite3.connect(lis)
            left = c.execute("SELECT passage_id FROM listener_play_history ORDER BY play_id").fetchall()
            c.close()
            check(left == [(1,), (None,)], f"{folder}: the orphan is cleared and the live reference kept: {left}")
            check(sorted(os.listdir(os.path.join(root, folder))) == ["library.db", "listener.db"],
                  f"{folder}: and no stray file was made: {os.listdir(os.path.join(root, folder))}")
        check(sorted(os.listdir(root)) == ["Music #2 %41", "plain"], f"nor beside them: {os.listdir(root)}")


def test_the_scripts_a_node_runs():
    """Three scripts are sent to a node as text and run by its `python3`,
    where there is no `lempi_db`: each escapes its own address. Run here as
    the node runs them."""
    print("the scripts a node is sent open the file they are given")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        folder = os.path.join(root, "srv #2 %41")
        p = os.path.join(folder, "library.db")
        made(p, "on the node")
        run = lambda *argv, text=None: subprocess.run([sys.executable, *argv], input=text, capture_output=True,  # noqa: E731
                                                      text=True, timeout=120)
        r = run("-", p, text=check_node_ready.LISTENER_SCRIPT)
        check(r.returncode == 0 and r.stdout.split() == ["t"], f"check_node_ready's table list: {r.stdout!r} {r.stderr[-300:]!r}")
        dst = os.path.join(root, "copy.db")
        r = run("-", p, dst, text=star_sync.ADOPT_SCRIPT)
        check(r.returncode == 0 and r.stdout.split()[:1] == ["ok"] and r.stdout.split()[1:] == [star_sync.sha256(dst)],
              f"star_sync's adopt copy: {r.stdout!r} {r.stderr[-300:]!r}")
        c = sqlite3.connect(dst)
        check(c.execute("SELECT a FROM t").fetchone()[0] == "on the node", "and the copy is of that file")
        c.close()
        r = run("-c", remote_peek._PY_FALLBACK, p, "SELECT a FROM t")
        check(r.returncode == 0 and json.loads(r.stdout or "null") == [{"a": "on the node"}],
              f"remote_peek's fallback: {r.stdout!r} {r.stderr[-300:]!r}")
        check(sorted(os.listdir(root)) == ["copy.db", "srv #2 %41"], f"and nothing else was made: {os.listdir(root)}")


# An address written without the helper, wherever one is still right. Each is
# (file, text on the line): a line is passed only for that text.
BY_HAND = [
    # The helper itself, and the copy a node runs alone.
    ("tools/lempi_db.py", 'return "file:" + escaped'),
    ("tools/split_database.py", 'return "file:" + os.fspath(path).replace("%", "%25")'),
    # Sent to a node as text: escaped where they are written.
    ("tools/check_node_ready.py", '"file:" + sys.argv[1].replace("%", "%25")'),
    ("tools/star_sync.py", '"file:" + src.replace("%", "%25")'),
    ("tools/remote_peek.py", "'file:'+sys.argv[1].replace('%','%25')"),
    # A constant, `data/flavor.db`: it holds none of the three characters.
    ("tools/recompute_floor.py", 'f"file:{DB}?mode=ro"'),
    # Not an address: a traceback as a node once printed it, kept as it came.
    ("tools/test_mesh_diff.py", "c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)\")"),
]
# This file is not read: the list above quotes every form it looks for.
SELF = "tools/test_sqlite_uri.py"
WRITTEN = re.compile(r"""file:\{|["']file:["']\s*\+""")


def prose_and_comments(source):
    """Which lines of `source` are prose -- inside a docstring -- and, for each
    other line, the column its comment starts at: (lines, {line: column}).
    None where the source does not tokenize.

    Until 2026-11-14 the scan below took any line holding a backtick for
    prose, so a line of code with a backtick in its trailing comment, or in
    a string, was never read -- the fault it is there to stop could return in
    exactly such a line."""
    import io
    import tokenize
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(source).readline)
                if t.type not in (tokenize.NL, tokenize.ENCODING)]
    except (tokenize.TokenError, SyntaxError):
        return None
    comment_at = {}
    for t in toks:
        if t.type == tokenize.COMMENT:
            comment_at.setdefault(t.start[0], t.start[1])
    # A docstring is a string that is a whole statement on its own.
    sig = [t for t in toks if t.type != tokenize.COMMENT]
    starts = {tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
    prose = set()
    for i, t in enumerate(sig):
        if t.type != tokenize.STRING:
            continue
        before = sig[i - 1].type if i else tokenize.NEWLINE
        after = sig[i + 1].type if i + 1 < len(sig) else tokenize.ENDMARKER
        if before in starts and after in (tokenize.NEWLINE, tokenize.ENDMARKER):
            prose.update(range(t.start[0], t.end[0] + 1))
    return prose, comment_at


def code_of(line, n, comment_at):
    """Line `n` without its comment."""
    return line[:comment_at[n]] if n in comment_at else line


def test_prose_is_told_from_code():
    """The scan's reading of a file: a docstring and a comment are prose, and
    a line of code is code whatever its comment says."""
    print("a docstring and a comment are prose; code is code, backticks or not")
    src = ('def f(p):\n'
           '    """Built as `f"file:{p}?mode=ro"`."""\n'
           '    # `f"file:{p}?mode=ro"`, by hand\n'
           '    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)  # see `lempi_db`\n'
           '    x = 1  # f"file:{p}"\n')
    shape = prose_and_comments(src)
    check(shape is not None, "the sample did not tokenize")
    prose, comment_at = shape or (set(), {})
    lines = src.split("\n")
    read = [n for n, line in enumerate(lines, 1)
            if n not in prose and WRITTEN.search(code_of(line, n, comment_at))]
    check(read == [4], f"the lines read as code writing an address: {read}, where only 4 is")


def test_no_address_is_written_by_hand():
    """What keeps the forty from coming back: every tracked Python file is
    read for an address put together in place."""
    print("no address is put together by hand, outside the helper and what a node is sent")
    listed = subprocess.run(["git", "-C", ROOT, "ls-files", "*.py"], capture_output=True, text=True)
    files = listed.stdout.split("\n") if listed.returncode == 0 else []
    files = [f for f in files if f and os.path.exists(os.path.join(ROOT, f))]
    if len(files) < 50:
        # Not in a clone: read what is here, and say so rather than pass on nothing.
        print("  git lists no files here: reading tools/ and LempiPi/ as they lie")
        files = [os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/")
                 for top in ("tools", "LempiPi") for d, _dirs, names in os.walk(os.path.join(ROOT, top))
                 for f in names if f.endswith(".py")]
    check(len(files) >= 50, f"only {len(files)} Python files were found to read")
    used = set()
    for rel in sorted(set(files) - {SELF}):
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        shape = prose_and_comments(source)
        if shape is None:
            print(f"  {rel} does not tokenize: every line of it is read as code")
            shape = (set(), {})
        prose, comment_at = shape
        for n, line in enumerate(source.split("\n"), 1):
            # Prose -- a docstring, a comment -- names the old form; code is read.
            if n in prose or not WRITTEN.search(code_of(line, n, comment_at)):
                continue
            allowed = [i for i, (f, text) in enumerate(BY_HAND) if f == rel and text in line]
            used.update(allowed)
            check(allowed, f"{rel}:{n} writes an address by hand -- use lempi_db.sqlite_uri: {line.strip()[:110]}")
    unused = [BY_HAND[i] for i in range(len(BY_HAND)) if i not in used]
    check(not unused, f"an exception that no longer matches a line: {unused}")


def main() -> int:
    test_the_address_names_the_file()
    test_the_copy_in_split_database_is_the_same()
    test_a_split_pair_in_such_a_folder()
    test_passage_orphans_clears_what_it_counted()
    test_the_scripts_a_node_runs()
    test_prose_is_told_from_code()
    test_no_address_is_written_by_hand()
    print()
    if FAILED:
        print(f"sqlite_uri: {len(FAILED)} check(s) FAILED")
        return 1
    print("sqlite_uri: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
