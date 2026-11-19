#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `mb_pace.py`, the machine's one MusicBrainz pace `[SPEC-MBP-010..040]`.

Times are given rather than waited for, except where waiting is the point:
three real processes sharing one pace file, at a short interval, must still
take their turns `[SPEC-MBP-030]`. No request reaches the network.

    python tools/test_mb_pace.py
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mb_pace  # noqa: E402
import fetch_releases  # noqa: E402
import fetch_works  # noqa: E402
import fetch_chosen_tracks  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def test_the_interval() -> None:
    print("the interval is MusicBrainz's one a second with a fifth to spare, and every tool uses it")
    check(abs(mb_pace.MIN_INTERVAL_S - 1.2) < 1e-9, f"1.2 s, got {mb_pace.MIN_INTERVAL_S}")
    for mod in (fetch_releases, fetch_works, fetch_chosen_tracks):
        check(mod.RATE_S == mb_pace.MIN_INTERVAL_S, f"{mod.__name__}.RATE_S is the pace's: {mod.RATE_S}")


def test_which_requests_wait() -> None:
    print("only musicbrainz.org waits")
    for url in ("https://musicbrainz.org/ws/2/release/x", "http://musicbrainz.org/ws/2/discid/y",
                "https://beta.musicbrainz.org/ws/2/release/x", "https://MusicBrainz.org/ws/2/x"):
        check(mb_pace.paced(url), f"{url} waits")
    for url in ("http://127.0.0.1:8123/ws/2/release/x", "https://coverartarchive.org/release/x",
                "https://api.acoustid.org/v2/lookup", "https://musicbrainz.org.example.com/ws/2/x", ""):
        check(not mb_pace.paced(url), f"{url!r} does not")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "pace")
        t = time.time()
        for _ in range(3):
            mb_pace.wait("http://127.0.0.1:1/ws/2/release/x", path)
        check(time.time() - t < 0.5 and not os.path.exists(path),
              "a test server's requests neither wait nor touch the pace file")


def test_turns() -> None:
    print("reserve(): the next turn is the interval after the last, and a late one is at once")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "sub", "pace")      # a folder not there yet is made
        check(mb_pace.reserve(path, 1.2, now=1000.0) == 1000.0, "the first asks at once")
        b = mb_pace.reserve(path, 1.2, now=1000.1)
        check(abs(b - 1001.2) < 1e-6, f"0.1 s later waits for 1001.2: {b}")
        c = mb_pace.reserve(path, 1.2, now=1000.2)
        check(abs(c - 1002.4) < 1e-6, f"a third queues behind the second: {c}")
        check(mb_pace.reserve(path, 1.2, now=1010.0) == 1010.0, "one long after asks at once")
        with open(path, encoding="ascii") as f:
            check(f.read() == "1010.000", "written as the player reads it: seconds, three places")
        # What the player writes, read here (musicbrainz.rs's own test is the mirror).
        with open(path, "w", encoding="ascii") as f:
            f.write("2000.500")
        got = mb_pace.reserve(path, 1.2, now=2000.0)
        check(abs(got - 2001.7) < 1e-6, f"the player's request at 2000.5 holds the next until 2001.7: {got}")
        with open(path, "w", encoding="ascii") as f:
            f.write("999999.000")
        check(mb_pace.reserve(path, 1.2, now=1000.0) == 1000.0, "a clock set back is forgotten, not waited out")
        with open(path, "w", encoding="ascii") as f:
            f.write("half-writ")
        check(mb_pace.reserve(path, 1.2, now=1000.0) == 1000.0, "and a damaged file is a new one")


def test_wait_waits() -> None:
    print("wait(): a second request at the real interval waits about 1.2 s")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "pace")
        mb_pace.wait("https://musicbrainz.org/ws/2/x", path)
        t = time.time()
        waited = mb_pace.wait("https://musicbrainz.org/ws/2/x", path)
        took = time.time() - t
        check(1.1 <= took <= 1.6 and abs(waited - took) < 0.1, f"waited {took:.2f} s, said {waited:.2f}")


CHILD = """
import sys, time
sys.path.insert(0, sys.argv[1])
import mb_pace
for _ in range(3):
    mb_pace.wait("https://musicbrainz.org/ws/2/x", sys.argv[2], interval=float(sys.argv[3]))
    print(repr(time.time()), flush=True)
"""


def test_processes_take_turns() -> None:
    print("three processes, one pace file: every request at least the interval after the last")
    interval = 0.3
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "pace")
        script = os.path.join(d, "child.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(CHILD)
        procs = [subprocess.Popen([sys.executable, script, HERE, path, str(interval)],
                                  stdout=subprocess.PIPE, text=True) for _ in range(3)]
        times = []
        for p in procs:
            out, _ = p.communicate(timeout=60)
            check(p.returncode == 0, f"a child exited {p.returncode}")
            times += [float(x) for x in out.split()]
    times.sort()
    gaps = [b - a for a, b in zip(times, times[1:])]
    check(len(times) == 9, f"nine requests, got {len(times)}")
    # A process can print a few ms after its turn; the turns themselves are the
    # interval apart, so a gap may read a little short, never half.
    check(all(g >= interval - 0.05 for g in gaps), f"each the interval after the last: {[round(g, 3) for g in gaps]}")


def main() -> int:
    test_the_interval()
    test_which_requests_wait()
    test_turns()
    test_wait_waits()
    test_processes_take_turns()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("mb_pace: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
