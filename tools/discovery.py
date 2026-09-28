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

import hashlib
import hmac
import json
import secrets
import socket
import sys
import time

PORT = 13492
KINDS = ("candidates", "hubs", "members")


def local_addresses() -> list[str]:
    """This machine's own IPv4 addresses, loopback aside."""
    have = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    try:                       # the one the default route leaves by; sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            have.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in have if not a.startswith("127."))


# A query is sent again at these offsets, under the same nonce. Measured
# 2026-09-28: lempi02w, a Pi Zero 2 W on Wi-Fi, answered one query in two;
# Wi-Fi delivers a broadcast to a sleeping radio unreliably, and answers to
# one nonce are counted once however many times it is asked [GDE-NDS-910].
REPEATS = (0.0, 0.5, 1.2)


def query(kind: str, timeout: float = 3.0, targets=("255.255.255.255",),
          mesh_fp: str | None = None, key: bytes | None = None) -> list[dict]:
    """Every answer to one query, each with the address it came from.

    Sent once from each of this machine's addresses, not once from any: a
    broadcast to 255.255.255.255 leaves Windows by one adapter only. Measured
    2026-09-28 on the desktop: it chose a Hyper-V virtual adapter
    (172.19.112.1), and none of the four players on 192.168.67.0/24 heard it.
    Bound to each address, the query leaves by that adapter."""
    if kind not in KINDS:
        raise ValueError(f"not a query: {kind}")
    import selectors
    nonce = secrets.token_hex(8)
    q = {"lempi": 1, "q": kind, "nonce": nonce}
    if kind == "members":
        # [SPEC-MTR-310]: proven with the mesh's discovery key, so only its own
        # members answer, and every answer proves membership back.
        if not (mesh_fp and key):
            raise ValueError("a members' query needs the mesh and its discovery key")
        q.update(mesh=mesh_fp, proof=hmac.new(key, f"lempi-members|{mesh_fp}|{nonce}".encode(), hashlib.sha256).hexdigest())
    msg = json.dumps(q).encode()
    sel = selectors.DefaultSelector()
    socks = []
    for addr in local_addresses() or ["0.0.0.0"]:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((addr, 0))
        except OSError:
            s.close()
            continue
        s.setblocking(False)
        sel.register(s, selectors.EVENT_READ)
        socks.append(s)
    try:
        found, start = {}, time.monotonic()
        until, due = start + timeout, list(REPEATS)
        while (left := until - time.monotonic()) > 0 and socks:
            if due and time.monotonic() - start >= due[0]:
                due.pop(0)
                for s in socks:
                    for t in targets:
                        try:
                            s.sendto(msg, (t, PORT))
                        except OSError:
                            pass
            wait = min(left, max(0.0, due[0] - (time.monotonic() - start))) if due else left
            for key, _ in sel.select(wait):
                try:
                    data, (ip, _) = key.fileobj.recvfrom(4096)
                except OSError:
                    continue      # a port-unreachable from somewhere: not an answer
                try:
                    a = json.loads(data)
                except ValueError:
                    continue
                # Only an answer to this query: the nonce is how an answer
                # recorded earlier, or meant for someone else, is told apart.
                if a.get("lempi") == 1 and a.get("nonce") == nonce and isinstance(a.get("fingerprint"), str):
                    if kind == "members":
                        want = hmac.new(key, f"lempi-member|{nonce}|{a['fingerprint']}".encode(),
                                        hashlib.sha256).hexdigest()
                        if not hmac.compare_digest(want, str(a.get("proof", ""))):
                            continue
                    found[(a["fingerprint"], ip)] = dict(a, address=ip)
        # The same node heard on two adapters answers twice: one row, the LAN's.
        by_node = {}
        for a in sorted(found.values(), key=lambda a: a["address"].startswith("172.")):
            by_node.setdefault(a["fingerprint"], a)
        return sorted(by_node.values(), key=lambda a: (a.get("name") or "", a["address"]))
    finally:
        for s in socks:
            sel.unregister(s)
            s.close()
        sel.close()


def main(argv: list[str]) -> int:
    kind = argv[0] if argv else "candidates"
    for a in query(kind):
        extra = f"  mesh '{a.get('mesh')}'" if a.get("a") == "hub" else ""
        print(f"{a.get('name') or '(unnamed)':20} {a['address']:16} {a['fingerprint'][:4]} {a['fingerprint'][4:8]}"
              f"  web :{a.get('web_port')}  {str(a.get('version'))[:8]}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
