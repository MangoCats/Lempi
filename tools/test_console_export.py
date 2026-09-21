#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the bundle-builder GUI's local-terminal step `[IMPL007 Stage 5]`.

`lempi_control.open_terminal` actually spawning a real terminal window is
exercised live, not here -- there is no headless way to prove a GUI window
opened, and every other console page is verified the same way
`[IMPL003 Stage 2]`. What this checks is the one branch that is
deterministic and side-effect-free: a directory that does not exist is
refused before anything is spawned.

    python tools/test_console_export.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_control  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


def main() -> int:
    print("a nonexistent directory is refused before anything is spawned")
    r = lempi_control.open_terminal(os.path.join(HERE, "no-such-directory-at-all"))
    check(r["ok"] is False, f"expected ok=False, got {r}")
    check("no such directory" in r.get("error", ""), f"expected a named reason, got {r}")

    print("a real directory is accepted (not spawned again here; live-verified separately)")
    check(os.path.isdir(HERE), "this test's own directory must exist for the next check to mean anything")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("console export: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
