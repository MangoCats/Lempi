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
SKIP_DIRS = {".git", "node_modules", "target", ".venv", "__pycache__", ".mypy_cache",
             ".gradle"}
# Gradle's build output, by path: `build` alone cannot be skipped by name, since
# the top-level `build/` holds this project's own scripts. Compiled copies, like
# `target/` -- and on 2026-09-26 the reason a local run reported 34 unreadable
# files that CI, on a clean checkout, never saw.
SKIP_PATHS = {"android/build", "android/app/build", "android/app/src/main/jniLibs",
              # Untracked recovery evidence [SPEC-STAR-075]: logs of real paths on
              # nodes whose data folders still carry the retired name. Rewriting
              # them would falsify the record they exist to be.
              "data/recovery"}
# Binary-ish things a text search would only produce noise from.
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
            ".gz", ".xz", ".db", ".bin", ".wav", ".flac", ".mp3", ".m4a", ".ttf",
            ".woff", ".woff2", ".otf",
            ".jar",  # android/gradle/wrapper/gradle-wrapper.jar, 2026-09-26
            # SQLite's sidecars: binary like the `.db` beside them, and found
            # 2026-09-26 in the recovery snapshots under data/.
            ".db-shm", ".db-wal"}


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
    unreadable = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        here = os.path.relpath(dirpath, ROOT).replace(os.sep, "/")
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and ("%s/%s" % (here, d)).lstrip("./") not in SKIP_PATHS]
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
            except UnicodeDecodeError:
                # **Counted and named, never passed over in silence.** This
                # used to `continue`, so a file this guard could not read
                # contributed nothing to the scan and nothing to the output
                # either -- a surface it cannot scan reporting clean, which
                # is the exact thing `[GOV-FBN-020]` above refuses, in the
                # checker that exists to enforce it. There are zero such
                # files today, so the list starts empty and any future one
                # is a deliberate decision rather than an accident.
                unreadable.append(rel)
                continue
            except OSError:
                continue          # genuinely unopenable: not prose either way
            scanned += 1
            for lineno, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    hits.append((rel, lineno, line.strip()[:120]))

    label = ", ".join(names)
    print("check_forbidden_names: %d token(s) from %s: %s" % (len(names), SOURCE, label))
    print("check_forbidden_names: scanned %d text file(s)" % scanned)
    if unreadable:
        print("check_forbidden_names: BROKEN -- %d file(s) could not be read as "
              "UTF-8 and were therefore NOT searched:" % len(unreadable),
              file=sys.stderr)
        for rel in unreadable:
            print("  %s" % rel, file=sys.stderr)
        print("check_forbidden_names: a file this cannot read is a file it "
              "cannot clear [GOV-FBN-020]. Add it to SKIP_EXT if it is "
              "genuinely binary.", file=sys.stderr)
        return 2
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
