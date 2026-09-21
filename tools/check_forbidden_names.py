#!/usr/bin/env python3
"""Fail if a retired project name appears anywhere but the one file allowed to
record it.

    python3 tools/check_forbidden_names.py            # report
    python3 tools/check_forbidden_names.py --strict   # report, exit 1 on any hit

`[GOV-FBN-010]` **The tokens are read from the document, not held here.** A
checker cannot search for a string without a copy of it, and a copy in this file
would put a retired name in a second place --- which is the exact condition this
guard exists to detect. So it parses them out of GUIDE015's `retired-names`
block at run time. The count stays at one, and the guard keeps working if the
list ever changes.

`[GOV-FBN-020]` **A surface it cannot scan reports BROKEN, never clean.** If the
source document is missing, or holds no token block, or the block is empty, that
is a hard failure. A guard that silently finds nothing to do is worse than no
guard: it reports success for work it never performed.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join("docs", "GUIDE015-the-earlier-names.md")

# Not searched: version control internals, dependency trees and build output are
# not this project's prose, and `target/` in particular holds compiled copies of
# every string in the crate.
SKIP_DIRS = {".git", "node_modules", "target", ".venv", "__pycache__", ".mypy_cache"}
# Binary-ish things a text search would only produce noise from.
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
            ".gz", ".xz", ".db", ".bin", ".wav", ".flac", ".mp3", ".m4a", ".ttf",
            ".woff", ".woff2", ".otf"}


def tokens_from_source(path):
    """Pull the retired names out of the fenced `retired-names` block."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # ```text ... retired-names <NAME> ... ```
    block = re.search(r"```[a-z]*\s*\r?\nretired-names\r?\n(.*?)```", text, re.S)
    if not block:
        return []
    out = []
    for line in block.group(1).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def main():
    strict = "--strict" in sys.argv
    src = os.path.join(ROOT, SOURCE)

    if not os.path.exists(src):
        print("check_forbidden_names: BROKEN -- %s does not exist, so there is "
              "nothing to read the tokens from." % SOURCE, file=sys.stderr)
        return 2
    names = tokens_from_source(src)
    if not names:
        print("check_forbidden_names: BROKEN -- %s has no `retired-names` block, "
              "or it is empty. Refusing to report a clean tree on a search that "
              "had no terms." % SOURCE, file=sys.stderr)
        return 2

    pattern = re.compile("|".join(re.escape(n) for n in names), re.I)
    hits = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            if rel == SOURCE.replace(os.sep, "/"):
                continue          # the one file allowed to hold them
            if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            try:
                with open(full, encoding="utf-8", errors="strict") as fh:
                    content = fh.read()
            except (UnicodeDecodeError, OSError):
                continue          # not text, or unreadable: not prose either way
            scanned += 1
            for lineno, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    hits.append((rel, lineno, line.strip()[:120]))

    label = ", ".join(names)
    print("check_forbidden_names: %d token(s) from %s: %s" % (len(names), SOURCE, label))
    print("check_forbidden_names: scanned %d text file(s)" % scanned)
    if not hits:
        print("check_forbidden_names: clean -- no retired name outside %s" % SOURCE)
        return 0

    print("check_forbidden_names: %d occurrence(s) OUTSIDE the one allowed file:"
          % len(hits), file=sys.stderr)
    for rel, lineno, line in hits:
        print("  %s:%d  %s" % (rel, lineno, line), file=sys.stderr)
    print("check_forbidden_names: a retired name belongs only in %s. If this is a "
          "citation, rewrite it as prose naming the previous repository instead."
          % SOURCE, file=sys.stderr)
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
