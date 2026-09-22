#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How far apart two echo nodes really are, sampled continuously.

    python tools/echo_skew.py bose lp3-wifi:5720
    python tools/echo_skew.py bose lp3-wifi:5720 --seconds 600 --quiet

Prints one line per sample and a distribution at the end. **Positive means
the second node is BEHIND the first** -- further back in the passage, so it
must catch up. Same sign convention as `tools/echo_skew.sh`, which this
replaces for anything longer than a spot check.

Why this exists when `echo_skew.sh` already did `[GDE-ARC-037]`
-------------------------------------------------------------
That script ssh'es into each node in turn, curls the WebSocket by hand, and
stamps each reading with `date` on the node. It works, and it settled
`[GDE-ECHO-344]`. It has three limits that matter once the question moves
from "are they seconds apart" to "are they forty milliseconds apart":

  * **It reads the two nodes one after the other**, seconds apart, and
    subtracts the gap using each node's own `date`. That is a correction, and
    corrections have error.
  * **It reads `position_ms`**, which is the engine's `audible_ms`: mixed
    position less the output ring. That deliberately does NOT subtract the
    device's own presentation delay `[AirPosition]`, so two nodes with
    different delays disagree by the difference even when the sound is
    identical.
  * **One sample is not a measurement.** The anchor carries the output ring's
    depth jitter -- tens of milliseconds `[LOG-P4-010]` -- so a single
    reading cannot tell 40 ms of skew from 40 ms of noise. Only a
    distribution can.

This reads the `echo.anchor` each node publishes instead. An anchor is that
node's own statement that *it had reached sample S of passage P at its own
wall time T*, with the device delay already subtracted `[air_position]`. Both
timestamps are the nodes' own, so this program's polling jitter and the
network delay drop out of the arithmetic entirely rather than being corrected
for. What is left is the anchor's own precision, which is the thing
`[GDE-ECHO-345]` says is the floor until a frame-counter anchor exists.

It also records what the follower *believes* its own residual to be, beside
what this measures. Those two disagreeing is a finding, not a glitch: a
correction loop cannot be its own witness `[GDE-ECHO-378]`.

**What this cannot separate, and neither can the player** `[GDE-ARC-038]`
-------------------------------------------------------------------------
Both timestamps are the nodes' own wall clocks, so **every reading here is
alignment-as-the-nodes'-clocks-see-it**. If the two clocks disagree by X, this
reads X of skew that no listener can hear -- and, far worse, the follower
reads the same X and *corrects real audio* to remove it, putting the sound X
out in the other direction. A clock error does not stay a clock error; it
becomes a playback error.

`[GDE-ECHO-160]` calls `WallNanos` "the chrony-disciplined wall clock", and
the whole design rests on that. Confirm it per node rather than assuming --
`--clocks` does exactly that, and it is cheap. Measured 2026-09-18, `bose`
ran chrony against a LAN reference at 55 us while `lp3-wifi` ran
systemd-timesyncd against a public pool server over the internet: 20 ms
standing offset, 22 ms jitter, a poll interval up to 34 minutes, and free
crystal drift between polls. The skew this tool measured between them was
688 ms. Putting `lp3-wifi` on chrony against the same LAN reference took it
to a median of 12 ms without touching a line of player code.

`clocks_agree` will not catch a fault like that: its tolerance is thirty
seconds, sized for a node booted without an RTC `[GDE-ECHO-365]`, not for the
tens of milliseconds that matter to a listener.

**The only instrument that can separate the two is a microphone.** Record both
speakers on one device and cross-correlate; that answers "what does a person
hear" without asking either node what time it is. Everything here, and
everything the player does, is downstream of the clocks agreeing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time

try:
    import websockets
except ImportError:  # pragma: no cover - environment, not logic
    sys.exit("needs `pip install websockets`")


def clock_offset(host: str, user: str) -> tuple[str, float] | None:
    """What this node's own chrony says its clock error is, in ms.

    **Not a round trip.** An ssh-timed comparison has to correct for the path,
    and on a wifi node with 600 ms of RTT the asymmetry swamps the thing being
    measured -- an earlier attempt from a Windows box reported 90-105 ms of
    inter-node error, consistently, and consistency there was stable
    asymmetry rather than accuracy. The reference machine's own clock moved
    52 ms between two sessions while the node it was measuring sat at 55 us.

    Every node is disciplined to one LAN server `[GDE-ECHO-300]`, so each
    node's offset is measured against the *same* reference and the difference
    between two of them is the inter-node error, common-mode cancelled, with
    no network timing in the path. It is still a daemon's self-report -- a
    claim, not a measurement, exactly as `[GDE-ECHO-300]` says -- but it is a
    claim from the thing actually steering the clock, at microsecond
    resolution, and the skew figures beside it are the independent check.
    """
    bare = host.split(":")[0]
    try:
        out = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
             f"{user}@{bare}", "chronyc tracking"],
            capture_output=True, text=True, timeout=25).stdout
    except Exception:  # noqa: BLE001 -- an unreachable node is a fact, not a crash
        return None
    ref = off = None
    for line in out.splitlines():
        if line.startswith("Reference ID"):
            ref = line.split(":", 1)[1].strip()
        elif line.startswith("System time"):
            body = line.split(":", 1)[1].strip()
            try:
                secs = float(body.split()[0])
            except (ValueError, IndexError):
                continue
            off = -secs * 1000.0 if "slow" in body else secs * 1000.0
    return (ref or "unknown", off) if off is not None else None


def _ms(v: float) -> str:
    """Microseconds below a millisecond, milliseconds above.

    A chrony-disciplined node sits in the tens of microseconds and a badly
    disciplined one in the tens of milliseconds; one fixed format cannot show
    both without either losing the good case to rounding or drowning the bad
    one in zeroes.
    """
    return f"{v * 1000:+.1f} us" if abs(v) < 1.0 else f"{v:+.3f} ms"


def report_clocks(a: str, b: str, user: str) -> None:
    """Print both nodes' clock discipline, and the gap between them."""
    ca, cb = clock_offset(a, user), clock_offset(b, user)
    for host, c in ((a, ca), (b, cb)):
        if c is None:
            print(f"  {host}: no chrony answer -- is it disciplined at all? "
                  f"[GDE-ECHO-300]")
        else:
            print(f"  {host}: chrony ref {c[0]}, own error {_ms(c[1])}")
    if ca and cb:
        if ca[0] != cb[0]:
            print(f"  ** different references ({ca[0]} vs {cb[0]}) -- their errors "
                  f"do NOT cancel [GDE-ECHO-300]")
        print(f"  clock gap between them: {_ms(cb[1] - ca[1])} "
              f"(subtract from the skew below to get audio error)")
    print()


def url_for(host: str) -> str:
    """`bose` -> ws://bose/ws, `lp3-wifi:5720` -> ws://lp3-wifi:5720/ws.

    A bare name gets no port, which is port 80 -- what `bose` serves. The
    fleet is not uniform and pretending otherwise would make this work on
    some nodes and silently not on others `[SPEC-ECHO-010]`.
    """
    return f"ws://{host}/ws"


class Node:
    """The latest thing a node said about itself."""

    def __init__(self, host: str):
        self.host = host
        self.anchor: dict | None = None
        self.voided: str | None = None
        self.status: str = ""
        self.seen = 0
        self.no_anchor = 0

    def position_ms_at(self, t_ns: int) -> float | None:
        """Where this node's audio had reached at wall time `t_ns`.

        Extrapolated from its own anchor at its own timestamp. Positions
        advance at one millisecond per millisecond, so this is arithmetic
        rather than a model -- the same reasoning `[local_at_sample]` uses.
        """
        a = self.anchor
        if not a:
            return None
        rate = max(1, a.get("rate") or 44100)
        at_anchor = a["sample"] * 1000.0 / rate
        return at_anchor + (t_ns - a["heard_at"]) / 1e6


async def follow(node: Node, stop: asyncio.Event) -> None:
    """Keep `node` current until told to stop. Reconnects; never raises."""
    while not stop.is_set():
        try:
            async with websockets.connect(url_for(node.host)) as ws:
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), 10)
                    snap = json.loads(raw)
                    echo = snap.get("echo") or {}
                    node.anchor = echo.get("anchor")
                    node.voided = echo.get("voided_by")
                    node.status = (snap.get("echo_node") or {}).get("follow_status", "")
                    node.seen += 1
                    if not node.anchor:
                        node.no_anchor += 1
        except Exception as e:  # noqa: BLE001 -- a probe must not die on a blip
            if not stop.is_set():
                print(f"  [{node.host}: {type(e).__name__}; retrying]", flush=True)
                await asyncio.sleep(2)


async def run(a: Node, b: Node, seconds: float, every: float, quiet: bool) -> int:
    stop = asyncio.Event()
    tasks = [asyncio.create_task(follow(n, stop)) for n in (a, b)]
    samples: list[float] = []
    mismatched = 0
    unreadable = 0
    deadline = time.time() + seconds
    try:
        # A moment for both sockets to deliver their first snapshot.
        await asyncio.sleep(1.5)
        while time.time() < deadline:
            now = time.time_ns()
            pa, pb = a.position_ms_at(now), b.position_ms_at(now)
            if pa is None or pb is None:
                unreadable += 1
                if not quiet:
                    why = []
                    for n in (a, b):
                        if not n.anchor:
                            why.append(f"{n.host} has no anchor"
                                       + (f" (voided by {n.voided})" if n.voided else ""))
                    print(f"{time.strftime('%H:%M:%S')}  -- {'; '.join(why)}", flush=True)
            elif a.anchor["passage_id"] != b.anchor["passage_id"]:
                mismatched += 1
                if not quiet:
                    print(f"{time.strftime('%H:%M:%S')}  -- different passages "
                          f"({a.anchor['passage_id']} vs {b.anchor['passage_id']})", flush=True)
            else:
                skew = pa - pb
                samples.append(skew)
                if not quiet:
                    print(f"{time.strftime('%H:%M:%S')}  passage {a.anchor['passage_id']:<7d} "
                          f"{skew:8.0f} ms behind   [{b.host} says: {b.status or '-'}]",
                          flush=True)
            await asyncio.sleep(every)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    print()
    print(f"samples {len(samples)}   different-passage {mismatched}   no-anchor {unreadable}")
    for n in (a, b):
        missing = f", {n.no_anchor} without an anchor" if n.no_anchor else ""
        print(f"  {n.host}: {n.seen} snapshots{missing}")
    if not samples:
        # An empty result is not agreement `[GDE-ECHO-547]`.
        print("NO USABLE SAMPLES -- this is not a measurement of zero skew.")
        return 1
    srt = sorted(samples)
    print(f"  skew ms: min {srt[0]:.0f}  p50 {statistics.median(srt):.0f}  "
          f"p90 {srt[int(len(srt) * 0.9)]:.0f}  max {srt[-1]:.0f}  "
          f"spread {srt[-1] - srt[0]:.0f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("first", help="reference node, e.g. bose")
    ap.add_argument("second", help="node measured against it, e.g. lp3-wifi:5720")
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--every", type=float, default=2.0)
    ap.add_argument("--quiet", action="store_true", help="the distribution only")
    ap.add_argument("--clocks", action="store_true",
                    help="ask each node's chrony what its own clock error is first")
    ap.add_argument("--ssh-user", default="pi", help="for --clocks (default: pi)")
    args = ap.parse_args()
    if args.clocks:
        report_clocks(args.first, args.second, args.ssh_user)
    return asyncio.run(run(Node(args.first), Node(args.second),
                           args.seconds, args.every, args.quiet))


if __name__ == "__main__":
    sys.exit(main())
