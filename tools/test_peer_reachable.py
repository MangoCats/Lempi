#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `lempi_control.peer_reachable`/`peer_host` `[SPEC-MESH-090]`.

The online dot on Vipunen's node list. Two things are worth pinning and one
thing is worth refusing to pin:

  * `peer_host` must split a `user@host:/path` the way `scp`/`ssh` do --
    on the FIRST colon, because a Windows-side path can contain more, and
    the `user@` part never does. Getting this wrong points the probe at the
    wrong name, which fails open in the most confusing possible way: a green
    dot for a host nobody is talking to.
  * `peer_reachable` must answer, never raise. An unresolvable name, a down
    host and a firewalled port are all simply "not reachable" to a person
    looking at a list, and an exception here would take the whole table down
    with it.

What is deliberately NOT tested is whether any particular real host is up.
This suite binds a listener on loopback and asks about that, so it gives the
same answer on a laptop in a tunnel as it does on the bench. A test that
needed `bose` to be plugged in would fail for reasons that have nothing to
do with this code.

    python tools/test_peer_reachable.py
"""

import os
import socket
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_control  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def test_peer_host():
    print("peer_host: split on the first colon, drop the user")
    cases = [
        ("pi@bose:/srv/library/library.db", "bose"),
        ("sw@teacherslounge:/home/sw/lempi-data/library.db", "teacherslounge"),
        ("pi@lempipi:/var/lempi/listener.db", "lempipi"),
        # No user given: the whole first field is the host.
        ("lempipi:/srv/library/library.db", "lempipi"),
        # A path carrying its own colons must not confuse the split.
        ("mango@smartboardpc:C:/Users/x/library.db", "smartboardpc"),
        # Degenerate input answers rather than raising.
        ("", ""),
        (None, ""),
    ]
    for remote, want in cases:
        got = lempi_control.peer_host(remote)
        check(got == want, f"peer_host({remote!r}) should be {want!r}, got {got!r}")


def test_reachable_against_a_real_listener():
    print()
    print("peer_reachable: a socket that is actually listening reads as up")
    # A real listener on loopback, on an ephemeral port -- so this measures
    # the function rather than the state of the house.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def accept_once():
        srv.settimeout(5)
        try:
            c, _ = srv.accept()
            c.close()
        except OSError:
            pass
        stop.set()

    threading.Thread(target=accept_once, daemon=True).start()
    try:
        # `peer_reachable` hard-codes 22, so the probe is exercised through a
        # port it can actually be pointed at.
        ok = lempi_control._reachable_on("127.0.0.1", port, timeout=2.0)
        check(ok is True, f"a listening socket must read as reachable, got {ok!r}")
    finally:
        srv.close()
        stop.wait(1)


def test_unreachable_answers_rather_than_raising():
    print()
    print("peer_reachable: nothing listening, and a name that cannot resolve, both answer False")
    # A port nothing is on: bound, read, then closed, so it is known free.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    check(lempi_control._reachable_on("127.0.0.1", dead_port, timeout=1.0) is False,
          "a closed port must read as not reachable")

    # RFC 6761 reserves .invalid; it can never resolve, on any network.
    check(lempi_control.peer_reachable("pi@nonexistent.invalid:/srv/x.db", timeout=2.0) is False,
          "an unresolvable host must be False, not an exception")
    check(lempi_control.peer_reachable("", timeout=1.0) is False,
          "an empty address must be False, not an exception")
    check(lempi_control.peer_reachable("no-colon-at-all", timeout=1.0) is False,
          "a malformed address must be False, not an exception")


def main() -> int:
    test_peer_host()
    test_reachable_against_a_real_listener()
    test_unreachable_answers_rather_than_raising()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("peer_reachable: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
