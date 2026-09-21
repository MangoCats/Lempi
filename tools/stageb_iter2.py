# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[GDE-FEX-100] Stage B iteration 2: attack the weak characteristics.

Iteration 1 (shared MLP, 928 non-band features) reached median err/beta 0.223
against AcousticBrainz's own 0.210 self-error floor, but error concentrated in
the genre classifiers -- genre_tzanetakis 3.35x the floor, genre_dortmund 2.46x,
gender 2.00x.

Three hypotheses, tested here on the two weakest characteristics:

  H1  Model class. The teacher is an RBF SVM; gradient boosting may capture its
      decision structure better than an MLP on tabular features.
  H2  Task interference. One shared trunk serves 18 tasks; a dedicated model per
      characteristic may do better on the ones being crowded out.
  H3  Missing band descriptors. Iteration 1 dropped barkbands/erbbands/melbands
      per the models' own `preprocessing: nobands`. If that reading is right, H3
      must FAIL -- the teacher cannot depend on features it never saw, so adding
      them should add noise, not signal. A win here would mean "nobands" has been
      misread, which matters for every characteristic.

Usage:
    python tools/stageb_iter2.py --chars genre_tzanetakis gender
"""

import argparse
import json
import time
from collections import defaultdict

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler


def tv_error(T, P):
    P = np.clip(P, 0.0, None)
    s = P.sum(axis=1, keepdims=True)
    s[s < 1e-9] = 1.0
    P = P / s
    return float((0.5 * np.abs(T - P).sum(axis=1)).mean())


def mean_r(T, P):
    out = []
    for i in range(T.shape[1]):
        a, b = T[:, i], P[:, i]
        if a.std() < 1e-12 or b.std() < 1e-12:
            continue
        out.append(((a - a.mean()) * (b - b.mean())).mean() / (a.std() * b.std()))
    return float(np.mean(out)) if out else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/stageb/dataset.npz")
    ap.add_argument("--chars", nargs="+", default=["genre_tzanetakis", "gender"])
    args = ap.parse_args()

    d = np.load(args.data, allow_pickle=True)
    X, Y = d["X"], d["Y"]
    names = list(d["feat_names"])
    target_cols = [t.split("|") for t in d["target_cols"]]
    groups = np.array([k.rsplit("-", 1)[0] for k in d["keys"]])
    rel = json.load(open("data/reliability.json"))

    by_char = defaultdict(list)
    for i, (c, k) in enumerate(target_cols):
        by_char[c].append(i)

    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=13)
                  .split(X, Y, groups))
    scaler = StandardScaler().fit(X[tr])
    Xtr = np.nan_to_num(scaler.transform(X[tr]), nan=0.0, posinf=0.0, neginf=0.0)
    Xte = np.nan_to_num(scaler.transform(X[te]), nan=0.0, posinf=0.0, neginf=0.0)
    print(f"train {len(tr):,}  test {len(te):,}  features {Xtr.shape[1]}\n")

    for char in args.chars:
        idx = by_char[char]
        Ttr, Tte = Y[np.ix_(tr, idx)], Y[np.ix_(te, idx)]
        beta = rel[char][2]
        floor = rel[char][1] / beta
        print(f"=== {char}  (K={len(idx)}, beta={beta:.4f}, AB self-error/beta={floor:.3f}) ===")

        # H2: dedicated MLP for this characteristic alone
        t0 = time.time()
        m = MLPRegressor(hidden_layer_sizes=(512, 256), max_iter=150,
                         early_stopping=True, n_iter_no_change=12,
                         random_state=13).fit(Xtr, Ttr)
        P = m.predict(Xte)
        print(f"  H2 dedicated MLP      r={mean_r(Tte, P):.4f}  "
              f"err/beta={tv_error(Tte, P) / beta:.3f}  ({time.time() - t0:.0f}s)")

        # H1: gradient boosting, one regressor per class
        t0 = time.time()
        P = np.column_stack([
            HistGradientBoostingRegressor(max_iter=300, learning_rate=0.1,
                                          random_state=13).fit(Xtr, Ttr[:, j]).predict(Xte)
            for j in range(len(idx))
        ])
        print(f"  H1 gradient boosting  r={mean_r(Tte, P):.4f}  "
              f"err/beta={tv_error(Tte, P) / beta:.3f}  ({time.time() - t0:.0f}s)")

    # H3: do the excluded band descriptors carry teacher-relevant signal?
    print("\n=== H3: band descriptors ===")
    band = [i for i, n in enumerate(names)
            if any(b in n for b in ("barkbands", "erbbands", "melbands"))]
    print(f"  band-derived features already present in the 928: {len(band)}")
    print("  (raw per-band ARRAYS were excluded; these are their scalar statistics)")
    if band:
        char = args.chars[0]
        idx = by_char[char]
        keep = [i for i in range(Xtr.shape[1]) if i not in set(band)]
        m = MLPRegressor(hidden_layer_sizes=(512, 256), max_iter=150, early_stopping=True,
                         n_iter_no_change=12, random_state=13).fit(Xtr[:, keep], Y[np.ix_(tr, idx)])
        P = m.predict(Xte[:, keep])
        beta = rel[char][2]
        print(f"  {char} WITHOUT band stats: r={mean_r(Y[np.ix_(te, idx)], P):.4f}  "
              f"err/beta={tv_error(Y[np.ix_(te, idx)], P) / beta:.3f}")


if __name__ == "__main__":
    main()
