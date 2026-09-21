#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A migrated script must give the same answer split as it does whole.

This is how the local split's remaining migration is verified, and it is
deliberately not a unit test: it runs each script the way a person runs it,
against a real split pair and against the real single-file database the
pair was made from, and compares what comes out. Same answer, or the
migration is wrong.

It exists because the first migrated script *looked* fine and was not.
`export_flags.py` returned "nothing flagged there yet" against a split pair
holding thirteen flags -- its `sqlite_master` existence check answers only
for `main`, decided the table was absent, and reported an empty list as a
fact. Nothing raised, nothing logged. A unit test on the helper would not
have caught it; running the script and comparing did.

Read-only scripts only. Anything that writes is verified by other means --
its own tests, or a rehearsal against a copy -- because running it twice
against two databases would be two different sets of side effects.

    python tools/test_split_parity.py --pair DIR --whole lempi.db

`--pair` is a directory holding `library.db` and `listener.db`, as
`split_database.py --commit` produces. Build one from the database under
test so the two sides genuinely hold the same rows:

    python tools/split_database.py lempi.db \\
        --library-out pair/library.db --listener-out pair/listener.db --commit
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Volatile fields: a timestamp or a hostname differing between two runs is
# not a split defect. Stripped before comparing rather than ignored
# wholesale, so everything else still has to match exactly.
VOLATILE = re.compile(
    r'"(exported_at|generated_at|started|taken_at|at|host|hostname)"\s*:\s*("[^"]*"|\d+)')


def normalise(text: str) -> str:
    text = VOLATILE.sub(r'"\1": "-"', text)
    try:
        return json.dumps(json.loads(text), sort_keys=True, indent=0)
    except json.JSONDecodeError:
        return text.strip()


def run(script: str, db: str, extra: list[str], out_file: bool) -> tuple[int, str]:
    """One invocation. `out_file` scripts write JSON to `-o`; the rest print."""
    with tempfile.TemporaryDirectory() as tmp:
        argv = [sys.executable, os.path.join(HERE, script), db, *extra]
        if out_file:
            target = os.path.join(tmp, "out.json")
            argv += ["-o", target]
            r = subprocess.run(argv, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                return r.returncode, (r.stderr or r.stdout)
            with open(target, encoding="utf-8") as f:
                return 0, f.read()
        r = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        return r.returncode, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout))


# (script, extra args, writes-to--o). Grows as scripts are migrated; a
# migrated read-only script that is not in here is not actually verified.
CASES = [
    ("export_flags.py", [], True),
    ("export_changes.py", [], True),
    # Dry runs: without `--commit` these report what they would do and write
    # nothing, which is exactly the read-only shape this harness needs -- and
    # they report it from real pending work (99 id_reviews, 2 boundary edits
    # at the time of writing), not from a fixture.
    ("apply_reviews.py", [], False),
    ("apply_boundary_reviews.py", [], False),
    # Cross-half by construction: the registry it lists is listener-side,
    # the reach counts beside it join `passages` and `flavor`.
    ("load_occasions.py", [], False),
    ("audit_split_readiness.py", [], False),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", required=True, help="directory holding library.db and listener.db")
    ap.add_argument("--whole", required=True, help="the single-file database the pair was made from")
    args = ap.parse_args()

    library = os.path.join(args.pair, "library.db")
    if not os.path.exists(library):
        print(f"no library.db in {args.pair}")
        return 2

    failed = 0
    for script, extra, out_file in CASES:
        if script == "audit_split_readiness.py":
            # Reads the tree, not a database: run once, as a self-check that
            # the harness's own idea of the script list still parses.
            rc, _ = run(script, "", [], False)
            print(f"  {script:<30} {'ok' if rc == 0 else 'FAILED'}")
            failed += rc != 0
            continue
        rc_s, out_s = run(script, library, extra, out_file)
        rc_w, out_w = run(script, args.whole, extra, out_file)
        if rc_s != 0 or rc_w != 0:
            print(f"  {script:<30} FAILED to run (split rc={rc_s}, whole rc={rc_w})")
            print(f"      {(out_s or out_w).strip()[:200]}")
            failed += 1
            continue
        if normalise(out_s) != normalise(out_w):
            print(f"  {script:<30} DIFFERS between split and whole")
            a, b = normalise(out_s), normalise(out_w)
            print(f"      split: {a[:160]}")
            print(f"      whole: {b[:160]}")
            failed += 1
            continue
        size = len(normalise(out_s))
        print(f"  {script:<30} same answer both ways ({size} bytes compared)")

    print()
    if failed:
        print(f"{failed} script(s) differ or failed")
        return 1
    print(f"split parity: {len(CASES)} script(s) agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
