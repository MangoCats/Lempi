# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[GDE-FEX-100] Stage B iteration 5: complete the factorial.

Iteration 4 fit dedicated MLPs for the seven characteristics whose iteration-3
winner was the SHARED MLP -- and the dedicated MLP won 7 out of 7, sometimes by a
lot (mood_happy 0.147 -> 0.076).

That result implicates the rest of the table. The ten characteristics where GBM
won in iteration 3 were only ever compared against the SHARED MLP; a dedicated
MLP was never fit for them either. Only `gender` and `genre_tzanetakis` currently
have all three candidate families measured -- and `gender` flipped to the
dedicated MLP once it was tried.

This run fits dedicated MLPs for the remaining characteristics so that every cell
of the comparison finally comes from the same candidate set [LOG-I3-030].

Usage:
    python tools/stageb_iter5.py
"""

import json
import os
import time
from collections import defaultdict

import numpy as np
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neural_network import MLPRegressor
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
    prev = json.load(open("data/stageb/stageb_iter4.json"))["per_characteristic"]

    # Everything that has not yet had a dedicated MLP fitted. genre_tzanetakis
    # already has one from iteration 2 (0.527, lost to GBM's 0.462).
    done = {c for c, v in prev.items() if "iter4_mlp_dedicated" in v} | {"genre_tzanetakis"}
    todo = sorted(set(prev) - done)
    print(f"fitting dedicated MLPs for {len(todo)}: {', '.join(todo)}\n")

    by_char = defaultdict(list)
    for i, (c, k) in enumerate(target_cols):
        by_char[c].append(i)

    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=13)
                  .split(X, Y, groups))
    scaler = StandardScaler().fit(X[tr])
    Xtr = np.nan_to_num(scaler.transform(X[tr]), nan=0.0, posinf=0.0, neginf=0.0)
    Xte = np.nan_to_num(scaler.transform(X[te]), nan=0.0, posinf=0.0, neginf=0.0)

    print(f"{'characteristic':22} {'prev best':>10} {'ded.MLP':>8} {'new best':>9} {'floor':>7} {'vs floor':>9}")
    out = dict(prev)
    for char in todo:
        idx = by_char[char]
        beta = rel[char][2]
        t0 = time.time()
        m = MLPRegressor(hidden_layer_sizes=(512, 256), max_iter=150, early_stopping=True,
                         n_iter_no_change=12, random_state=13).fit(Xtr, Y[np.ix_(tr, idx)])
        P = normalise(m.predict(Xte))
        ded = float((0.5 * np.abs(Y[np.ix_(te, idx)] - P).sum(axis=1)).mean()) / beta

        pb = prev[char]["best"]
        best = min(pb, ded)
        winner = "mlp-dedicated" if ded < pb else prev[char]["winner"]
        out[char] = {**prev[char], "iter5_mlp_dedicated": ded, "best": best,
                     "winner": winner, "vs_floor": best / prev[char]["floor"]}
        flag = "  IMPROVED" if ded < pb else ""
        print(f"{char:22} {pb:10.3f} {ded:8.3f} {best:9.3f} {prev[char]['floor']:7.3f} "
              f"{best / prev[char]['floor']:8.2f}x  <- {winner}{flag}  ({time.time() - t0:.0f}s)",
              flush=True)

    med = float(np.median([v["best"] for v in out.values()]))
    floor = float(np.median([v["floor"] for v in out.values()]))
    at = sum(1 for v in out.values() if v["vs_floor"] <= 1.0)
    print(f"\nmedian err/beta: 0.223 -> 0.192 -> 0.174 -> {med:.3f}   (floor {floor:.3f})")
    print(f"characteristics at or below their own floor: {at}/18")
    print("\nfinal model selection:")
    for c, v in sorted(out.items(), key=lambda kv: kv[1]["vs_floor"]):
        print(f"  {c:22} {v['best']:6.3f}  {v['vs_floor']:5.2f}x floor   {v['winner']}")

    with open("data/stageb/stageb_final.json", "w") as fh:
        json.dump({"per_characteristic": out, "median_best": med,
                   "median_floor": floor, "at_or_below_floor": at}, fh, indent=1)


if __name__ == "__main__":
    main()
