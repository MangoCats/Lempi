#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
No local number leaves its machine, and none is written where a passage's uid
belongs `[IMPL-UID-082]`, `[SPEC-UID-070]`.

A passage number or a file number means nothing outside its own database
`[SPEC-DF-035]`. Each time one crossed anyway, the result looked right: echo
followers played another song for the same number (2026-11-07), and an id
review copied itself onto eight passages over three syncs `[SPEC-STAR-051]`.
Stages A to D replaced every crossing with the passage's uid (IMPL023). This
refuses the four ways a number could come back:

  1. **a serialized `passage_id` or `file_id`**: a field of a Rust struct that
     derives `Serialize` and does not skip it, or a key in a `json!` value.
     What a struct serializes goes to a page, a peer or the wire; the engine
     keeps its number with `#[serde(skip)]` `[IMPL-UID-072]`.
  2. **a listener table declared with a `passage_id` column**, where it names
     a passage by its uid `[IMPL-UID-060]`.
  3. **a web handler binding a passage parameter as an integer**: every
     passage route takes a uid `[IMPL-UID-072]`.
  4. **a passage row written by hand**, outside the writers that give it its
     uid (`HELPERS`) `[IMPL-UID-038]`. `tools/test_passage_uid.py` reads the
     same list.

**Test code is outside it:** `tools/test_*.py`, a `tests/` folder, and a Rust
`#[cfg(test)]` module. They build the old shapes on purpose, to test their
conversion. A Rust test module is found by lexing, so a brace inside a string
does not end it early.

**There is no allow-list.** IMPL023 planned one that "can only shrink, and is
empty at the end of Stage C"; on 2026-11-16, the day this was written, a full
scan found nothing, so none was made. A finding is a number to replace, or a
writer to add to `HELPERS` with its reason.

Usage:
    python tools/check_local_ids.py            every tracked file (CI)
    python tools/check_local_ids.py --staged   only the lines this commit adds
    python tools/check_local_ids.py --min N    floor for a full scan (default 200)

Exit 0 clean, 1 a finding, 2 the check could not run (git failed, a file could
not be read, or a full scan read fewer than `--min` files -- a broken scan,
not a clean tree, CLAUDE.md section 6).
"""

import argparse
import bisect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_joined_lines as cjl  # noqa: E402  -- the Rust lexer
import staged  # noqa: E402

TOOL = "check_local_ids"

#: The writers that give a passage its uid as they write it [IMPL-UID-038].
HELPERS = {
    "tools/passage_uid.py": "the helper itself",
    "tools/remote_snapshot.py": "a scratch copy of a remote, its rows written with the uids the remote holds",
    "player/core/src/bundle.rs": "writes the hub's row, then db::stamp_birth stores the uid it was sent",
    "player/src/found.rs": "writes a phone-born row, then db::stamp_birth gives it its uid",
}

#: The listener tables that name a passage, each by its uid since Stage B [IMPL-UID-060].
LISTENER = ("listener_play_history", "listener_rejections", "selection_decisions", "player_state",
            "player_queue", "id_reviews", "boundary_reviews", "artist_reviews", "passage_lyrics")

DERIVE = re.compile(r"#\[derive\(([^)]*)\)\]")
STRUCT = re.compile(r"^\s*(?:pub(?:\([a-z]+\))?\s+)?struct\s+\w+[^;{]*\{", re.M)
FIELD = re.compile(r"^\s*(?:pub(?:\([a-z]+\))?\s+)?(passage_id|file_id)\s*:", re.M)
SKIP = re.compile(r"#\[serde\([^)]*\bskip(?:_serializing)?\b(?!_if)")
JSON_KEY = re.compile(r"\"(passage_id|file_id)\"\s*:")
CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(", re.I)
PATH = re.compile(r"Path\(\s*\(?([^)]*?)\)?\s*\)\s*:\s*(?:axum::extract::)?Path<\s*\(?([^>]*?)\)?\s*>")
INSERT = re.compile(r"INSERT(?:\s+OR\s+[A-Z]+)?\s+INTO\s+passages\b", re.I)
INTEGER = {"i64", "u64", "i32", "u32", "usize", "isize"}


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or "/tests/" in f"/{path}"


def rust_test_spans(src: str) -> list:
    """(start, end) of each `#[cfg(test)] mod` in `src`, braces counted outside
    strings and comments."""
    hidden = [(s, e) for kind, s, e in cjl.tokens(src)]
    starts = [s for s, _ in hidden]

    def hidden_at(i):
        k = bisect.bisect_right(starts, i) - 1
        return k >= 0 and hidden[k][0] <= i < hidden[k][1]

    out = []
    for m in re.finditer(r"#\[cfg\(test\)\]\s*(?:pub(?:\([a-z]+\))?\s+)?mod\s+\w+\s*\{", src):
        depth, i = 1, m.end()
        while i < len(src) and depth:
            if not hidden_at(i):
                depth += {"{": 1, "}": -1}.get(src[i], 0)
            i += 1
        out.append((m.start(), i))
    return out


def balanced(src: str, i: int, open_="(", close=")") -> int:
    """The index just past the bracket matching the one at `i`."""
    depth = 0
    for j in range(i, len(src)):
        depth += {open_: 1, close: -1}.get(src[j], 0)
        if depth == 0:
            return j + 1
    return len(src)


def findings(path: str, src: str):
    """(offset, what) for each finding in one file's text."""
    if is_test_file(path):
        return
    spans = rust_test_spans(src) if path.endswith(".rs") else []

    def in_test(i):
        return any(s <= i < e for s, e in spans)

    if path.endswith(".rs") and (path.startswith("player/src/") or path.startswith("player/core/src/")):
        # 1: a struct that serializes a number.
        for m in STRUCT.finditer(src):
            if in_test(m.start()):
                continue
            before = src[:m.start()].rstrip().split("\n")[-6:]
            derives = [d.group(1) for line in before for d in DERIVE.finditer(line)]
            if not any("Serialize" in d for d in derives):
                continue
            body_end = balanced(src, m.end() - 1, "{", "}")
            body = src[m.end():body_end]
            for f in FIELD.finditer(body):
                above = body[:f.start()].rstrip().split("\n")
                attrs = []
                for line in reversed(above):
                    t = line.strip()
                    if t.startswith("#[") or t.startswith("//") or t.startswith("///"):
                        attrs.append(t)
                    else:
                        break
                if not any(SKIP.search(a) for a in attrs):
                    yield m.end() + f.start(1), (f"a serialized {f.group(1)}: what this struct serializes leaves the "
                                                 "machine; name the row by what it is (a passage's uid, a file's "
                                                 "audio_md5), or keep the number with #[serde(skip)]")
        # ... and a json! value keyed by one.
        for m in re.finditer(r"\bjson!\s*\(", src):
            if in_test(m.start()):
                continue
            end = balanced(src, m.end() - 1)
            for k in JSON_KEY.finditer(src, m.end(), end):
                yield k.start(), f"a json! value keyed by {k.group(1)}: a number reaches whoever reads it"
        # 3: a web handler taking a passage as an integer.
        if path.startswith("player/src/web/"):
            for m in PATH.finditer(src):
                if in_test(m.start()):
                    continue
                names = [n.strip() for n in m.group(1).split(",")]
                types = [t.strip() for t in m.group(2).split(",")]
                for n, t in zip(names, types):
                    if n.startswith("passage") and t in INTEGER:
                        yield m.start(), (f"a passage route taking {n} as {t}: a passage is named in a URL by its "
                                          "uid, through web/passage_ref.rs [IMPL-UID-072]")
    # 2: a listener table by number.
    if path.endswith((".rs", ".py", ".sql")):
        for m in CREATE.finditer(src):
            if m.group(1).lower() not in LISTENER or in_test(m.start()):
                continue
            body = src[m.end():balanced(src, m.end() - 1)]
            col = re.search(r"(?:^|[(,\s])passage_id\s+INTEGER", body, re.I)
            if col:
                yield m.end() + col.start(), (f"{m.group(1)} declared with a passage_id column: a listener names a "
                                              "passage by its uid [IMPL-UID-060]")
    # 4: a passage row written by hand.
    if path.endswith((".rs", ".py")) and path not in HELPERS and not path.startswith("fixtures/"):
        for m in INSERT.finditer(src):
            if not in_test(m.start()):
                yield m.start(), ("a passage row written by hand: write it through a helper that gives it its uid, "
                                  "or add this writer to HELPERS with its reason [IMPL-UID-038]")


def sources(args):
    """(path, text, the line numbers to report or None for all)."""
    globs = ("*.rs", "*.py", "*.sql")
    if args.staged:
        added = {}
        for path, n, _text in staged.added_lines(*globs, tool=TOOL):
            added.setdefault(path, set()).add(n)
        for path in sorted(added):
            yield path, staged.blob(TOOL, path), added[path]
        return
    listed = staged.git(TOOL, "ls-files", "-z", "--", *globs).decode("utf-8", "surrogateescape")
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
    ap.add_argument("--min", type=int, default=200,
                    help="full scan: fewer files than this means the scan is broken")
    args = ap.parse_args()

    files = bad = 0
    for path, src, only in sources(args):
        files += 1
        starts = [0] + [m.end() for m in re.finditer("\n", src)]
        text = src.split("\n")
        for offset, what in findings(path, src):
            line = bisect.bisect_right(starts, offset)
            if only is not None and line not in only:
                continue
            bad += 1
            print(f"{path}:{line}: {what}")
            print(f"    {text[line - 1].strip()[:120]}")

    scope = "with lines added by this commit" if args.staged else "tracked"
    print(f"{TOOL}: {files} file(s) {scope}, {bad} finding(s)")
    if not args.staged and files < args.min:
        staged.did_not_run(TOOL, f"only {files} file(s) read against a floor of {args.min}; "
                                 "the scan is broken, not the tree")
    return 1 if bad else 0


if __name__ == "__main__":
    staged.run(TOOL, main)
