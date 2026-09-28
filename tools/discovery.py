#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Look for Lempi nodes on this network [SPEC050].

A query goes out once, on UDP 13492, to the broadcast address of the local
segment; each node that answers does so directly to the asker. Nothing
connects to anything because it answered: this only lists [SPEC-MTR-330].

    python tools/discovery.py candidates      players not in any mesh
    python tools/discovery.py hubs            hubs a device could ask to join

A segment is usually one floor or one VLAN; a node beyond a router is
reached by typing its name, as before [GDE-NDS-920].
"""
from __future__ import annotations

import json
import secrets
import socket
import sys
import time

PORT = 13492
KINDS = ("candidates", "hubs")


def query(kind: str, timeout: float = 2.0, targets=("255.255.255.255",)) -> list[dict]:
    """Every answer to one query, each with the address it came from."""
    if kind not in KINDS:
        raise ValueError(f"not a query: {kind}")
    nonce = secrets.token_hex(8)
    msg = json.dumps({"lempi": 1, "q": kind, "nonce": nonce}).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("0.0.0.0", 0))
    try:
        for t in targets:
            s.sendto(msg, (t, PORT))
        found, until = {}, time.monotonic() + timeout
        while (left := until - time.monotonic()) > 0:
            s.settimeout(left)
            try:
                data, (ip, _) = s.recvfrom(4096)
            except (socket.timeout, TimeoutError):
                break
            except OSError:
                continue          # a port-unreachable from somewhere: not an answer
            try:
                a = json.loads(data)
            except ValueError:
                continue
            # Only an answer to this query: the nonce is how an answer recorded
            # earlier, or meant for someone else, is told apart.
            if a.get("lempi") == 1 and a.get("nonce") == nonce and isinstance(a.get("fingerprint"), str):
                found[(a["fingerprint"], ip)] = dict(a, address=ip)
        return sorted(found.values(), key=lambda a: (a.get("name") or "", a["address"]))
    finally:
        s.close()


def main(argv: list[str]) -> int:
    kind = argv[0] if argv else "candidates"
    for a in query(kind):
        extra = f"  mesh '{a.get('mesh')}'" if a.get("a") == "hub" else ""
        print(f"{a.get('name') or '(unnamed)':20} {a['address']:16} {a['fingerprint'][:4]} {a['fingerprint'][4:8]}"
              f"  web :{a.get('web_port')}  {str(a.get('version'))[:8]}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
