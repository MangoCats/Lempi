# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[GDE-FEX-100] Stage B iteration 3: consolidate iteration 2's confirmed findings.

Iteration 2 established two things on the weakest characteristics:
  - Dedicated per-characteristic models beat one shared trunk (task interference
    is real): genre_tzanetakis 0.604 -> 0.527, gender 0.392 -> 0.337 err/beta.
  - The better model class is characteristic-dependent: gradient boosting won on
    genre_tzanetakis (0.462 vs 0.527), the MLP won on gender (0.337 vs 0.393).

So: train a dedicated gradient-boosting model per characteristic, and keep it only
where it beats iteration 1's shared MLP on held-out err/beta. Error is normalised
by each characteristic's natural spread and judged against AcousticBrainz's own
submission-to-submission floor [GDE-FEX-085] -- the point below which further
effort is chasing encoding noise.

Usage:
    python tools/stageb_iter3.py
"""

import json
import os
import time
from collections import defaultdict

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler


def normalise(P):
    P = np.clip(P, 0.0, None)
    s = P.sum(axis=1, keepdims=True)
    s[s < 1e-9] = 1.0
    return P / s


def main():
    d = np.load("data/stageb/dataset.npz", allow_pickle=True)
    X, Y = d["X"], d["Y"]
    target_cols = [t.split("|") for t in d["target_cols"]]
    groups = np.array([k.rsplit("-", 1)[0] for k in d["keys"]])
    rel = json.load(open("data/reliability.json"))
    base = json.load(open("data/stageb/stageb_results.json"))["per_characteristic"]

    by_char = defaultdict(list)
    for i, (c, k) in enumerate(target_cols):
        by_char[c].append(i)

    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=13)
                  .split(X, Y, groups))
    scaler = StandardScaler().fit(X[tr])
    Xtr = np.nan_to_num(scaler.transform(X[tr]), nan=0.0, posinf=0.0, neginf=0.0)
    Xte = np.nan_to_num(scaler.transform(X[te]), nan=0.0, posinf=0.0, neginf=0.0)

    print(f"train {len(tr):,}  test {len(te):,}  features {Xtr.shape[1]}")
    print(f"\n{'characteristic':22} {'iter1':>7} {'iter3':>7} {'best':>7} {'floor':>7} {'vs floor':>9}")

    out, t_start = {}, time.time()
    for char, idx in sorted(by_char.items()):
        Ttr, Tte = Y[np.ix_(tr, idx)], Y[np.ix_(te, idx)]
        beta = rel[char][2]
        floor = rel[char][1] / beta

        P = normalise(np.column_stack([
            HistGradientBoostingRegressor(max_iter=400, learning_rate=0.08,
                                          random_state=13).fit(Xtr, Ttr[:, j]).predict(Xte)
            for j in range(len(idx))
        ]))
        gbm = float((0.5 * np.abs(Tte - P).sum(axis=1)).mean()) / beta
        mlp = base[char]["tv"] / beta
        best, winner = (gbm, "gbm") if gbm < mlp else (mlp, "mlp-shared")

        out[char] = {"iter1_mlp": mlp, "iter3_gbm": gbm, "best": best,
                     "winner": winner, "floor": floor, "vs_floor": best / floor}
        print(f"{char:22} {mlp:7.3f} {gbm:7.3f} {best:7.3f} {floor:7.3f} {best / floor:8.2f}x"
              f"  <- {winner}", flush=True)

    med_best = float(np.median([v["best"] for v in out.values()]))
    med_floor = float(np.median([v["floor"] for v in out.values()]))
    med_i1 = float(np.median([v["iter1_mlp"] for v in out.values()]))
    at_floor = sum(1 for v in out.values() if v["vs_floor"] <= 1.0)

    print(f"\nmedian err/beta  iter1={med_i1:.3f}  ->  iter3={med_best:.3f}   "
          f"(AB self-error floor {med_floor:.3f})")
    print(f"characteristics at or below the floor: {at_floor}/18")
    print(f"elapsed {time.time() - t_start:.0f}s")

    with open("data/stageb/stageb_iter3.json", "w") as fh:
        json.dump({"per_characteristic": out, "median_best": med_best,
                   "median_floor": med_floor, "median_iter1": med_i1,
                   "at_or_below_floor": at_floor}, fh, indent=1)


if __name__ == "__main__":
    main()
