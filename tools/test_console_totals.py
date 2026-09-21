#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `console.py`'s `totals()` against a genuinely fresh library.

Found writing `HOWTO.md`: a library built from nothing but `sql/schema.sql`
-- which is every library before the fingerprint pass has ever run once --
has no `id_checks` table at all, and `totals()` named it unconditionally.
The console crashed on `GET /` for the exact case a first-time reader of the
guide would actually hit. The real schema is used here, not a hand-written
approximation of it, so this cannot pass by fixing the fixture instead of
the bug.

    python tools/test_console_totals.py
"""

import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import console  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(HERE), "sql", "schema.sql")

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "fresh.db")
        c = sqlite3.connect(db)
        c.executescript(open(SCHEMA, encoding="utf-8").read())
        c.commit()
        c.close()

        print("a library the fingerprint pass has never touched reports zero, not a crash")
        conn = sqlite3.connect(db)
        t = console.totals(conn)
        check(t["unchecked"] == 0, f"expected 0 on an empty, never-checked library, got {t}")
        check(console.completeness(conn)["id_checked"] == 0, "completeness must derive from the same fallback")
        conn.close()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console totals: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
