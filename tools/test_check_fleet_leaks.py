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
    check(leaks("server 192.168.1.50 iburst"), "a 192.168.x address is flagged")
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
    check(leaks("/home/alice/Music"), "a real /home/<name> is flagged")
    check(not leaks("/home/pi/.ssh"), "/home/pi (the appliance default) is not flagged")
    check(not leaks("/home/<user>/Music"), "a redacted /home/<user> is not flagged")
    check(leaks(r"C:\Users\Alice\Dev"), "a real Windows user path is flagged")
    check(not leaks(r"C:\Users\<user>\Music"), "a redacted Windows path is not flagged")
    check(not leaks(r"C:\Users\Public\Music"), "C:\\Users\\Public is not flagged")

    # ssh-target user@host: (a real login is invisible to IP/MAC/path scans).
    check(leaks("scp x admin@node-x:/srv/data"), "a real user@host: is flagged")
    check(leaks("deploy to deploy@node-y:/srv"), "another real login is flagged")
    check(not leaks("ssh pi@speaker-a:/srv/library"), "a role-alias host is not flagged")
    check(not leaks("git@github.com:MangoCats/Lempi.git"), "the public git host is not flagged")
    check(not leaks("someone@workshop:/home/someone"), "a placeholder host is not flagged")
    check(not leaks("user@host:/path/to/library.db"), "the generic user@host: is not flagged")

    # The denylist path (compiled pattern), independent of the file.
    pat = re.compile("|".join(re.escape(t) for t in ["node-alpha", "buildbox"]), re.I)
    check(cfl.denylist_hits("deploy to pi@node-alpha now", pat), "a denylisted hostname is flagged")
    check(cfl.denylist_hits("the desktop BUILDBOX answered", pat),
          "the denylist is case-insensitive")
    check(not cfl.denylist_hits("nothing private here", pat), "a clean line with a denylist is clean")
    check(cfl.denylist_hits("x", None) == [], "no denylist means no denylist hits")

    # scan_line combines both.
    hits = cfl.scan_line("pi@node-alpha at 192.168.1.20", pat)
    check(len(hits) == 2, f"scan_line reports both the IP and the denylisted host: {hits}")

    # [SPEC-FCP-050], [SPEC-FCP-055]: the baseline. A finding is its file and
    # its line's text, never the number; editing the line makes it a new one;
    # the record is a hash, never the text.
    k = cfl.line_key("a/b.md", "host at 192.168.1.20")
    check(k == cfl.line_key("a/b.md", "host at 192.168.1.20  "), "trailing space does not make a new finding")
    check(k != cfl.line_key("a/b.md", "host at 192.168.1.21"), "an edited line is a new finding, not an accepted one")
    check(k != cfl.line_key("a/c.md", "host at 192.168.1.20"), "the same line in another file is its own finding")
    check("192" not in k and len(k) == 24, "the key carries no text of the line")
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "baseline.txt")
    check(cfl.read_baseline(p) is None, "no baseline file reads as None, not as empty")
    cfl.write_baseline(p, {k, "abc"})
    check(cfl.read_baseline(p) == {k, "abc"}, "the baseline round-trips, its header ignored")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("check_fleet_leaks: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
