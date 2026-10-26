#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `transfer_hold` `[PI3-AIM-170]`.

What the hub owes a node it sends a bulk transfer to: a hold first, and its
release after, whatever happened in between -- and when the player has
forgotten the hold (it restarted, or the hold ran out) and a hold had paused
it, a play. The player's side is `player/src/web/transfer.rs`'s own tests.

A fake player answers here, so no node is needed.

    python tools/test_transfer_hold.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import transfer_hold  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


class Player:
    """Answers the three paths as a player would."""

    def __init__(self, paused, knows=True, answers=True, quiet=True):
        self.paused, self.knows, self.answers, self.quiet = paused, knows, answers, quiet
        self.asked = []

    def post(self, path):
        self.asked.append(path)
        if not self.answers:
            return None
        if path.startswith("/transfer/hold"):
            return {"id": 7, "paused": self.paused, "quiet": self.quiet, "shared_radio": True}
        if path == "/transfer/release/7":
            return {"released": True, "resumed": self.paused} if self.knows else {"released": False}
        return {}


def run(p, fail=False):
    said = []
    try:
        with transfer_hold._held(p.post, "node", said.append):
            if fail:
                raise RuntimeError("the transfer failed")
    except RuntimeError:
        pass
    return said


print("transfer_hold")

p = Player(paused=True)
said = run(p)
check(p.asked[0] == "/transfer/hold", "a hold is asked for first, for the player's own default")
check(p.asked[1:] == ["/transfer/release/7"], "and released after")
check(any("paused" in s for s in said) and any("resumed" in s for s in said), "both said")

p = Player(paused=True)
run(p, fail=True)
check("/transfer/release/7" in p.asked, "a transfer that fails still releases")

p = Player(paused=True, knows=False)
said = run(p)
check(p.asked[-1] == "/command/play", "a forgotten hold that had paused is answered with a play")

p = Player(paused=False, knows=False)
run(p)
check("/command/play" not in p.asked, "one that had not paused starts nothing")

# [PI3-AIM-175]: paused before the transfer, the speaker is still released.
p = Player(paused=False)
said = run(p)
check(any("released its speaker" in s for s in said), "a paused node's released speaker is said")
check("/command/play" not in p.asked, "and nothing is started")

p = Player(paused=False, answers=False)
said = run(p)
check(p.asked == ["/transfer/hold"], "no player: nothing to release")
check(any("sending anyway" in s for s in said), "and it is said")

if FAILED:
    print(f"\n{len(FAILED)} check(s) failed")
    sys.exit(1)
print("transfer_hold: all checks passed")
