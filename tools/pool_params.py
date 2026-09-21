# SPDX-License-Identifier: AGPL-3.0-or-later
"""Re-derive the pool parameters against the local library `[SPEC-DIR-200]`.

`excl_pool`, `rand_pool` and `decay` were tuned by MuLibPlay over 8,116
passages and **11 unweighted dimensions**. Lempi now has 18 characteristics,
scale-normalised and reliability-weighted on locally measured constants
`[SPEC-FD-084]`, which changes the distance distribution the parameters were
chosen against.

There is no ground truth for "the right pool size" — the parameters trade
character fidelity against variety, and only a listener can say where that
should sit. What *can* be measured is whether they still select the same kind
of neighbourhood. If rank 1000 sits at the same normalised distance under both
metrics, the values transfer and re-deriving them would be theatre.

Usage:
  python tools/pool_params.py <local.db> [inherited.db]
"""

from __future__ import annotations

import sqlite3
import statistics as st
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
from pathlib import Path

EXCL_POOL = 1000
RAND_POOL = 100
DECAY = 0.96


def load(con: sqlite3.Connection, local: bool) -> tuple[dict, dict]:
    q = ("SELECT subject_id, characteristic, class, value FROM flavor "
         "WHERE subject_kind='recording' AND source ")
    q += "LIKE 'local:%'" if local else "NOT LIKE 'local:%'"
    vecs: dict[str, dict[str, dict[str, float]]] = {}
    for sid, ch, cl, v in con.execute(q):
        vecs.setdefault(sid, {}).setdefault(ch, {})[cl] = v
    const = {r[0]: (r[1], r[2]) for r in
             con.execute("SELECT characteristic, beta, reliability FROM flavor_constants")}
    return vecs, const


def distance(A: dict, B: dict, const: dict) -> float | None:
    num = den = 0.0
    for ch in A.keys() & B.keys():
        beta, w = const.get(ch, (0.0, 0.0))
        if beta <= 0:
            continue
        tv = 0.5 * sum(abs(A[ch].get(k, 0.0) - B[ch].get(k, 0.0))
                       for k in A[ch].keys() | B[ch].keys())
        num += w * (tv / beta)
        den += w
    return num / den if den else None


def profile(label: str, vecs: dict, const: dict, seeds: list[str]) -> None:
    """Where the pool boundaries fall, in distance terms."""
    seeds = [s for s in seeds if s in vecs][:5]
    if not seeds:
        print(f"{label}: no seeds with flavor")
        return
    nearest: list[float] = []
    for sid, V in vecs.items():
        if sid in seeds:
            continue
        ds = [d for s in seeds if (d := distance(V, vecs[s], const)) is not None]
        if ds:
            nearest.append(min(ds))
    nearest.sort()
    n = len(nearest)

    def at(rank: int) -> str:
        return f"{nearest[min(rank, n) - 1]:.3f}" if n else "n/a"

    print(f"{label}")
    print(f"   candidates {n}   median nearest-seed distance {st.median(nearest):.3f}")
    print(f"   rank    10 -> d {at(10)}      rank  {RAND_POOL*2} -> d {at(RAND_POOL*2)}")
    print(f"   rank   100 -> d {at(100)}      rank {EXCL_POOL} -> d {at(EXCL_POOL)}")
    # What fraction of the library is within the excl_pool boundary distance?
    if n:
        cut = nearest[min(EXCL_POOL, n) - 1]
        tight = sum(1 for d in nearest if d < cut * 0.5)
        print(f"   within half that distance: {tight} passages "
              f"({100*tight/n:.1f}% of the library)")


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    # Read-only across both halves: programme seeds are listener-side,
    # the flavor vectors they are profiled against are not.
    loc = lempi_db.connect(args[0], lempi_db.ROLE_LIBRARY)
    seeds_by_prog: dict[int, list[str]] = {}
    for pid, m in loc.execute("SELECT program_id, mbid FROM listener_program_seeds"):
        seeds_by_prog.setdefault(pid, []).append(m)
    names = {r[0]: r[1] for r in loc.execute("SELECT program_id, name FROM listener_programs")}

    lv, lc = load(loc, True)
    print(f"local: {len(lv)} recordings, {len(lc)} constants\n")
    for pid in sorted(seeds_by_prog)[:3]:
        profile(f"[local 18] {names.get(pid, pid)}", lv, lc, seeds_by_prog[pid])
        print()

    if len(args) > 1:
        # The inherited database is a whole MuLibPlay-era file, not a half
        # of anything -- opened plainly, on purpose.
        inh = sqlite3.connect(f"file:{args[1]}?mode=ro", uri=True)
        iv, ic = load(inh, False)
        print(f"inherited: {len(iv)} recordings\n")
        for pid in sorted(seeds_by_prog)[:3]:
            profile(f"[inherited 11] {names.get(pid, pid)}", iv, ic, seeds_by_prog[pid])
            print()

    print(f"decay {DECAY} over rand_pool {RAND_POOL}: "
          f"rank 0 weight x1.000, rank 50 x{DECAY**50:.3f}, rank 99 x{DECAY**99:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
