# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[GDE-FEX-065] Route 3, step 2: distil AcousticBrainz's highlevel classifiers.

Trains a model mapping lowlevel features -> the 71 highlevel dimensions, using
AcousticBrainz's own outputs as labels. Evaluated per [GDE-FEX-090] Stage B: the
teacher is deterministic, so a good distillation should reproduce it closely.

Splits are grouped by recording MBID, never by row -- multiple submissions of one
recording share a source work and would leak across the split otherwise.

Reported metrics are the ones that matter downstream per SPEC005: per-dimension
Pearson r, and total-variation distance between the predicted and teacher
distributions -- the quantity flavor distance is actually built from.

Usage:
    python tools/train_stageb.py --data data/stageb/dataset.npz
"""

import argparse
import json
import os
import time
from collections import defaultdict

import numpy as np
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler


def pearson(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    sa, sb = a.std(), b.std()
    if sa < 1e-12 or sb < 1e-12:
        return float("nan")
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/stageb/dataset.npz")
    ap.add_argument("--out", default="data/stageb")
    ap.add_argument("--hidden", type=int, nargs="+", default=[512, 256])
    ap.add_argument("--max-iter", type=int, default=120)
    args = ap.parse_args()

    d = np.load(args.data, allow_pickle=True)
    X, Y = d["X"], d["Y"]
    target_cols = [t.split("|") for t in d["target_cols"]]
    groups = np.array([k.rsplit("-", 1)[0] for k in d["keys"]])  # recording MBID

    # Group by recording so no MBID appears in both train and test.
    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=13)
                  .split(X, Y, groups))
    print(f"train {len(tr):,} rows / {len(set(groups[tr])):,} recordings")
    print(f"test  {len(te):,} rows / {len(set(groups[te])):,} recordings")
    assert not (set(groups[tr]) & set(groups[te])), "group leakage"

    # Features span wildly different scales (Hz, dB, ratios); standardise.
    scaler = StandardScaler().fit(X[tr])
    Xtr = np.nan_to_num(scaler.transform(X[tr]), nan=0.0, posinf=0.0, neginf=0.0)
    Xte = np.nan_to_num(scaler.transform(X[te]), nan=0.0, posinf=0.0, neginf=0.0)

    print(f"\nTraining MLP {args.hidden} on {Xtr.shape[1]} features -> {Y.shape[1]} targets...")
    t0 = time.time()
    model = MLPRegressor(
        hidden_layer_sizes=tuple(args.hidden),
        activation="relu",
        max_iter=args.max_iter,
        early_stopping=True,
        n_iter_no_change=10,
        random_state=13,
        verbose=False,
    ).fit(Xtr, Y[tr])
    print(f"  done in {time.time() - t0:.0f}s ({model.n_iter_} iters)")

    P = model.predict(Xte)

    # Renormalise each characteristic back onto the simplex: clip negatives,
    # rescale to sum 1.0, matching the [MFL-DEF-040] invariant.
    by_char = defaultdict(list)
    for i, (c, k) in enumerate(target_cols):
        by_char[c].append(i)
    P = np.clip(P, 0.0, None)
    for c, idx in by_char.items():
        s = P[:, idx].sum(axis=1, keepdims=True)
        s[s < 1e-9] = 1.0
        P[:, idx] /= s

    T = Y[te]
    print(f"\n{'characteristic':22} {'K':>2} {'mean r':>7} {'min r':>7} {'TV':>7} {'top1':>7}")
    results = {}
    for c, idx in sorted(by_char.items()):
        rs = [pearson(T[:, i], P[:, i]) for i in idx]
        tv = float((0.5 * np.abs(T[:, idx] - P[:, idx]).sum(axis=1)).mean())
        top1 = float((T[:, idx].argmax(1) == P[:, idx].argmax(1)).mean())
        results[c] = {"K": len(idx), "mean_r": float(np.nanmean(rs)),
                      "min_r": float(np.nanmin(rs)), "tv": tv, "top1": top1}
        print(f"{c:22} {len(idx):2d} {np.nanmean(rs):7.4f} {np.nanmin(rs):7.4f} "
              f"{tv:7.4f} {top1 * 100:6.1f}%")

    overall_r = float(np.nanmean([v["mean_r"] for v in results.values()]))
    overall_tv = float(np.mean([v["tv"] for v in results.values()]))
    overall_top1 = float(np.mean([v["top1"] for v in results.values()]))
    print(f"\n{'OVERALL':22} {'':2} {overall_r:7.4f} {'':7} {overall_tv:7.4f} {overall_top1 * 100:6.1f}%")

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "stageb_results.json"), "w") as fh:
        json.dump({"per_characteristic": results, "overall_mean_r": overall_r,
                   "overall_tv": overall_tv, "overall_top1": overall_top1,
                   "n_train": len(tr), "n_test": len(te),
                   "hidden": args.hidden}, fh, indent=1)
    print(f"\nwritten: {args.out}/stageb_results.json")


if __name__ == "__main__":
    main()
