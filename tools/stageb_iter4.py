# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[GDE-FEX-100] Stage B iteration 4: close the gap iteration 3 left open.

Iteration 3 compared, per characteristic, a dedicated gradient-boosting model
against iteration 1's SHARED MLP -- and never tried a DEDICATED MLP. That was an
oversight: iteration 2 [LOG-I2-020] had already shown dedicated beats shared.

The cost is visible on `gender`, where iteration 3 selected GBM at 0.390 over the
shared MLP's 0.392, while iteration 2's dedicated MLP had already reached 0.337.
Every characteristic where iteration 3's winner was "mlp-shared" is therefore
suspect: a dedicated MLP would plausibly beat it.

This run fits a dedicated MLP for those characteristics plus `gender`, and takes
the best of all three candidates.

Usage:
    python tools/stageb_iter4.py
"""

import json
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
    i3 = json.load(open("data/stageb/stageb_iter3.json"))["per_characteristic"]

    # Characteristics where a dedicated MLP was never tried and might win.
    todo = sorted([c for c, v in i3.items() if v["winner"] == "mlp-shared"] + ["gender"])
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
    out = dict(i3)
    for char in todo:
        idx = by_char[char]
        beta = rel[char][2]
        t0 = time.time()
        m = MLPRegressor(hidden_layer_sizes=(512, 256), max_iter=150, early_stopping=True,
                         n_iter_no_change=12, random_state=13).fit(Xtr, Y[np.ix_(tr, idx)])
        P = normalise(m.predict(Xte))
        ded = float((0.5 * np.abs(Y[np.ix_(te, idx)] - P).sum(axis=1)).mean()) / beta

        prev = i3[char]["best"]
        best = min(prev, ded)
        winner = "mlp-dedicated" if ded < prev else i3[char]["winner"]
        out[char] = {**i3[char], "iter4_mlp_dedicated": ded, "best": best,
                     "winner": winner, "vs_floor": best / i3[char]["floor"]}
        print(f"{char:22} {prev:10.3f} {ded:8.3f} {best:9.3f} {i3[char]['floor']:7.3f} "
              f"{best / i3[char]['floor']:8.2f}x  <- {winner}  ({time.time() - t0:.0f}s)", flush=True)

    med = float(np.median([v["best"] for v in out.values()]))
    floor = float(np.median([v["floor"] for v in out.values()]))
    at = sum(1 for v in out.values() if v["vs_floor"] <= 1.0)
    print(f"\nmedian err/beta: iter1 0.223 -> iter3 0.192 -> iter4 {med:.3f}   (floor {floor:.3f})")
    print(f"characteristics at or below their own floor: {at}/18")

    with open("data/stageb/stageb_iter4.json", "w") as fh:
        json.dump({"per_characteristic": out, "median_best": med,
                   "median_floor": floor, "at_or_below_floor": at}, fh, indent=1)


if __name__ == "__main__":
    main()
