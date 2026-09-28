#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tools/check_fleet_leaks.py `[SecurityReview N1]`.

The line scanners, against leaks and against the documented placeholders that
must not be flagged. The git-diff and tree walks are thin wrappers over these,
so the logic that decides is what is pinned here.
"""
import re
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_fleet_leaks as cfl  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def leaks(line):
    return cfl.generic_hits(line)


def main() -> int:
    # Private IPv4 is a leak.
    check(leaks("server 192.168.67.93 iburst"), "a 192.168.x address is flagged")
    check(leaks("ssh pi@10.0.0.5"), "a 10.x address is flagged")
    check(leaks("172.16.4.4"), "a 172.16-31.x address is flagged")
    # Documented / non-leaking addresses are not.
    check(not leaks("e.g. 192.0.2.10"), "an RFC 5737 address is not flagged")
    check(not leaks("198.51.100.7 and 203.0.113.9"), "the other RFC 5737 ranges are not flagged")
    check(not leaks("ipv4.addresses 10.42.0.1/24"), "the AP's own 10.42.0.x subnet is not flagged")
    check(not leaks("bind 0.0.0.0 and 127.0.0.1 and 255.255.255.255"),
          "0.0.0.0 / loopback / broadcast are not flagged")

    # MAC addresses.
    check(leaks("Marshall 20:64:DE:CF:F3:AD"), "a full MAC is flagged")
    check(not leaks("keep the vendor 20:64:DE:xx:xx:xx"), "a masked MAC is not flagged")
    check(not leaks("placeholder aa:bb:cc:dd:ee:ff"), "a placeholder MAC is not flagged")

    # Personal paths.
    check(leaks("/home/mike/Music"), "a real /home/<name> is flagged")
    check(not leaks("/home/pi/.ssh"), "/home/pi (the appliance default) is not flagged")
    check(not leaks("/home/<user>/Music"), "a redacted /home/<user> is not flagged")
    check(leaks(r"C:\Users\Mango Cat\Dev"), "a real Windows user path is flagged")
    check(not leaks(r"C:\Users\<user>\Music"), "a redacted Windows path is not flagged")
    check(not leaks(r"C:\Users\Public\Music"), "C:\\Users\\Public is not flagged")

    # The denylist path (compiled pattern), independent of the file.
    pat = re.compile("|".join(re.escape(t) for t in ["lempi02w", "GMKtec"]), re.I)
    check(cfl.denylist_hits("deploy to pi@lempi02w now", pat), "a denylisted hostname is flagged")
    check(cfl.denylist_hits("the desktop GMKTEC answered", pat),
          "the denylist is case-insensitive")
    check(not cfl.denylist_hits("nothing private here", pat), "a clean line with a denylist is clean")
    check(cfl.denylist_hits("x", None) == [], "no denylist means no denylist hits")

    # scan_line combines both.
    hits = cfl.scan_line("pi@lempi02w at 192.168.67.20", pat)
    check(len(hits) == 2, f"scan_line reports both the IP and the denylisted host: {hits}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("check_fleet_leaks: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
