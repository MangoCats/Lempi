#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Learn what EAC's multi-valued settings look like in its registry
(IMPL018 Phase 2, `[IMPL-CDI-300]`).

The import page's setup check reads EAC's on/off settings with confidence
(`ff` on, `00` off). A few -- the extraction mode, secure mode -- are codes
whose meaning is only known by watching them change. So, at the desk: take a
snapshot, change ONE setting in EAC and press OK, take another, and compare.
Read only; EAC's settings are never written.

    python tools/eac_calibrate.py snapshot before
    (change one setting in EAC, press OK)
    python tools/eac_calibrate.py snapshot after
    python tools/eac_calibrate.py diff before after

Snapshots are kept under `data/eac-calibration/`, which is not tracked.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cd_import  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "data", "eac-calibration")


def flat(eac: dict) -> dict:
    """`section/name -> value`, bytes as hex, so two snapshots compare as text."""
    out = {}
    for section, values in eac.items():
        if section == "drives":
            for drive, dv in values.items():
                for k, v in dv.items():
                    out[f"Drive Options/{drive}/{k}"] = v.hex(" ") if isinstance(v, (bytes, bytearray)) else v
        else:
            for k, v in values.items():
                out[f"{section}/{k}"] = v.hex(" ") if isinstance(v, (bytes, bytearray)) else v
    return out


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "snapshot":
        eac = cd_import.read_eac()
        if eac is None:
            print("EAC's settings cannot be read here (not Windows, or EAC never run)")
            return 1
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, argv[1] + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(flat(eac), fh, indent=1, sort_keys=True)
        print(f"{len(flat(eac))} values -> {path}")
        return 0
    if len(argv) == 3 and argv[0] == "diff":
        a, b = ({} if not os.path.exists(p) else json.load(open(p, encoding="utf-8"))
                for p in (os.path.join(OUT, n + ".json") for n in argv[1:]))
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if not changed:
            print("nothing changed -- was OK pressed in EAC, and was it closed and reopened?")
        for k in changed:
            print(f"{k}: {a.get(k)!r} -> {b.get(k)!r}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
