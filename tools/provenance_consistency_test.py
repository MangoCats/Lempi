# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[SPEC-FD-130] Does provenance consistency beat per-track accuracy?

Hypothesis under test: for judging similarity WITHIN our library, absolute
agreement with AcousticBrainz is not what matters -- internal consistency is. A
library scored entirely by one local model is self-consistent by construction,
whereas a library mixing dump values and locally extracted values pays both an
encoding difference and a model difference on every cross-provenance comparison.

If true, this inverts the role of the harvested dumps [GDE-FEX-050]: they become
ground truth for validation rather than production flavor data.

Method -- the SPEC005 retrieval test [SPEC-FD-060], run under three provenance
regimes. Query is submission 0 of a recording; the target is submission 1 of the
SAME recording (a different encode, standing in for "the same song scored from
different audio"); distractors are other recordings.

  A  all-teacher   query, target and distractors all from AcousticBrainz
  B  all-student   all three from the local model          (consistent local)
  C  mixed         student query against a teacher library (the naive plan)

Prediction: B >= A, and C materially worse than both.

Usage:
    python tools/provenance_consistency_test.py
"""

import json
import random
from collections import defaultdict

import numpy as np
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler


def load():
    d = np.load("data/stageb/dataset.npz", allow_pickle=True)
    return (d["X"], d["Y"], [t.split("|") for t in d["target_cols"]],
            np.array([k.rsplit("-", 1)[0] for k in d["keys"]]),
            np.array([int(k.rsplit("-", 1)[1]) for k in d["keys"]]))


def main():
    X, Y, target_cols, mbids, subs = load()
    rel = json.load(open("data/reliability.json"))

    by_char = defaultdict(list)
    for i, (c, k) in enumerate(target_cols):
        by_char[c].append(i)
    chars = sorted(by_char)
    betas = np.array([rel[c][2] for c in chars])
    weights = np.array([rel[c][3] for c in chars])  # reliability, per [SPEC-FD-050]

    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=13)
                  .split(X, Y, mbids))
    scaler = StandardScaler().fit(X[tr])
    Xtr = np.nan_to_num(scaler.transform(X[tr]), nan=0.0, posinf=0.0, neginf=0.0)
    Xte = np.nan_to_num(scaler.transform(X[te]), nan=0.0, posinf=0.0, neginf=0.0)

    print("training student (shared MLP) ...")
    student = MLPRegressor(hidden_layer_sizes=(512, 256), max_iter=120, early_stopping=True,
                           n_iter_no_change=10, random_state=13).fit(Xtr, Y[tr])
    P = np.clip(student.predict(Xte), 0.0, None)
    for c in chars:
        idx = by_char[c]
        s = P[:, idx].sum(axis=1, keepdims=True)
        s[s < 1e-9] = 1.0
        P[:, idx] /= s
    T = Y[te]

    def dist(a_vecs, b_vecs):
        """SPEC005 flavor distance: reliability-weighted, scale-normalised TV."""
        out = np.zeros(len(b_vecs))
        for ci, c in enumerate(chars):
            idx = by_char[c]
            tv = 0.5 * np.abs(a_vecs[:, idx] - b_vecs[:, idx]).sum(axis=1)
            out += weights[ci] * (tv / betas[ci])
        return out / weights.sum()

    # Recordings in the test split that have two submissions.
    pos = defaultdict(dict)
    for row, (m, s) in enumerate(zip(mbids[te], subs[te])):
        pos[m][s] = row
    pairs = [(v[min(v)], v[sorted(v)[1]]) for v in pos.values() if len(v) > 1]
    print(f"test recordings with 2 submissions: {len(pairs):,}")

    random.seed(29)
    POOL, TRIALS = 500, min(1500, len(pairs))
    trials = random.sample(pairs, TRIALS)
    all_rows = np.arange(len(te))

    regimes = {
        "A  all-teacher (dump only)":      (T, T, T),
        "B  all-student (local only)":     (P, P, P),
        "C  mixed (student vs dump lib)":  (P, T, T),
    }

    print(f"\nRetrieval of the same recording's other submission among {POOL} candidates,"
          f" {TRIALS} queries")
    print(f"{'provenance regime':32} {'top-1':>7} {'top-5':>7} {'MRR':>7} {'medRank':>8}")
    results = {}
    for name, (Q, Tg, Ds) in regimes.items():
        ranks = []
        for q_row, t_row in trials:
            distract = np.random.choice(all_rows, POOL, replace=False)
            distract = distract[distract != t_row][:POOL - 1]
            cand = np.vstack([Tg[t_row][None, :], Ds[distract]])
            d = dist(np.repeat(Q[q_row][None, :], len(cand), axis=0), cand)
            ranks.append(1 + int((d[1:] < d[0]).sum()))
        ranks = np.array(ranks)
        results[name] = {
            "top1": float((ranks == 1).mean()), "top5": float((ranks <= 5).mean()),
            "mrr": float((1.0 / ranks).mean()), "median_rank": float(np.median(ranks)),
        }
        print(f"{name:32} {results[name]['top1'] * 100:6.1f}% {results[name]['top5'] * 100:6.1f}% "
              f"{results[name]['mrr']:7.3f} {results[name]['median_rank']:8.1f}")

    with open("data/stageb/provenance_test.json", "w") as fh:
        json.dump(results, fh, indent=1)


if __name__ == "__main__":
    main()
