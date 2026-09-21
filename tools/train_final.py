# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Retrain the final Stage B model selection and PERSIST it [LOG-I5-030].

Iterations 1-5 measured which model family wins per characteristic but never
saved the fitted models, so every follow-up experiment costs an 8-hour retrain.
This run repopulates that gap once, writing sklearn-free bundles via
`model_store` so subsequent work is cheap.

Reproducibility: same split (GroupShuffleSplit by recording MBID, seed 13) and
same hyperparameters as the iteration that measured each winner, so the err/beta
reported here should match `[LOG-I5-030]`. Any divergence is a bug worth
investigating, and is flagged in the output.

Usage:
    python tools/train_final.py
"""

import json
import os
import sys
import time
from collections import defaultdict

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_store as ms

OUT_DIR = "data/models"


def main():
    d = np.load("data/stageb/dataset.npz", allow_pickle=True)
    X, Y = d["X"], d["Y"]
    feat_names = [str(f) for f in d["feat_names"]]
    target_cols = [t.split("|") for t in d["target_cols"]]
    groups = np.array([k.rsplit("-", 1)[0] for k in d["keys"]])
    rel = json.load(open("data/reliability.json"))
    final = json.load(open("data/stageb/stageb_final.json"))["per_characteristic"]

    by_char = defaultdict(list)
    for i, (c, k) in enumerate(target_cols):
        by_char[c].append(i)

    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=13)
                  .split(X, Y, groups))
    scaler = StandardScaler().fit(X[tr])
    Xtr = np.nan_to_num(scaler.transform(X[tr]), nan=0.0, posinf=0.0, neginf=0.0)

    print(f"train {len(tr):,}  test {len(te):,}  features {len(feat_names)}")
    print(f"{'characteristic':22} {'family':>14} {'err/beta':>9} {'expected':>9} {'MB':>6} {'time':>7}")

    manifest, total_bytes, t_start = {}, 0, time.time()
    for char in sorted(by_char):
        idx = by_char[char]
        classes = [target_cols[i][1] for i in idx]
        beta = rel[char][2]
        family = final[char]["winner"]
        expected = final[char]["best"]
        t0 = time.time()

        meta_common = {
            "characteristic": char, "beta": beta, "floor": final[char]["floor"],
            "trained": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_train": int(len(tr)), "split_seed": 13,
            "dataset": "acousticbrainz-sample-20220623 paired lowlevel/highlevel",
        }

        if family == "gbm":
            models = [HistGradientBoostingRegressor(max_iter=400, learning_rate=0.08,
                                                    random_state=13).fit(Xtr, Y[tr, i])
                      for i in idx]
            path = os.path.join(OUT_DIR, f"{char}.gbm.joblib")
            bundle_pred = lambda Xr: ms.predict_gbm(
                {"scaler_mean": scaler.mean_.astype(np.float32),
                 "scaler_scale": scaler.scale_.astype(np.float32),
                 "models": models}, Xr)
            meta = {**meta_common, "family": "gbm", "max_iter": 400, "learning_rate": 0.08}
            size = ms.save_gbm(path, models, scaler, feat_names, char, classes, meta)
        else:
            m = MLPRegressor(hidden_layer_sizes=(512, 256), max_iter=150,
                             early_stopping=True, n_iter_no_change=12,
                             random_state=13).fit(Xtr, Y[np.ix_(tr, idx)])
            path = os.path.join(OUT_DIR, f"{char}.mlp.npz")
            meta = {**meta_common, "family": "mlp-dedicated",
                    "hidden": [512, 256], "n_iter": int(m.n_iter_)}
            size = ms.save_mlp(path, m, scaler, feat_names, char, classes, meta)
            bundle_pred = lambda Xr, _b=ms.load_mlp(path): ms.predict_mlp(_b, Xr)

        # Score through the PERSISTED path, not the in-memory model: this
        # verifies the bundle itself, which is the artifact that matters.
        P = bundle_pred(X[te])
        err = float((0.5 * np.abs(Y[np.ix_(te, idx)] - P).sum(axis=1)).mean()) / beta
        drift = abs(err - expected)
        flag = "" if drift < 0.005 else f"  DRIFT {drift:+.3f}"

        total_bytes += size
        manifest[char] = {"path": os.path.basename(path), "family": family,
                          "err_beta": err, "expected": expected,
                          "floor": final[char]["floor"], "bytes": size,
                          "classes": classes}
        print(f"{char:22} {family:>14} {err:9.3f} {expected:9.3f} {size / 1e6:6.2f} "
              f"{time.time() - t0:6.0f}s{flag}", flush=True)

    med = float(np.median([v["err_beta"] for v in manifest.values()]))
    at = sum(1 for v in manifest.values() if v["err_beta"] <= v["floor"])
    print(f"\nmedian err/beta {med:.3f}   at-or-below floor {at}/18")
    print(f"total persisted: {total_bytes / 1e6:.1f} MB   elapsed {(time.time() - t_start) / 3600:.1f} h")

    with open(os.path.join(OUT_DIR, "manifest.json"), "w") as fh:
        json.dump({"format_version": ms.FORMAT_VERSION, "median_err_beta": med,
                   "at_or_below_floor": at, "total_bytes": total_bytes,
                   "models": manifest}, fh, indent=1)
    print(f"manifest: {OUT_DIR}/manifest.json")


if __name__ == "__main__":
    main()
