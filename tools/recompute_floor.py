# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[LOG-I5-050] Recompute the reproducibility floor on OUR library.

Every "vs floor" ratio reported so far uses constants measured on a generic
AcousticBrainz sample, single-submission versus single-submission. Two things
are wrong with that as a reference for Lempi:

  1. beta -- a characteristic's natural spread -- is a property of the corpus
     being searched. Ours is not the average corpus [SPEC-FD-051].
  2. The floor is single-vs-single, but the dump holds a mean of 77 submissions
     per library recording [LOG-FEX-057]. Averaging those cancels most encoding
     noise, so the honest reference is submission-vs-consensus, which is
     STRICTER. Current ratios are therefore flattering by an unmeasured margin.

This computes both, on the library's own 7,685 multi-submission recordings:

  floor_single  submission 0 vs submission 1        (comparable to the old 0.210)
  floor_mean    one submission vs the N-submission consensus   (the honest one)

Usage:
    python tools/recompute_floor.py
"""

import json
import random
import sqlite3
from collections import defaultdict

import numpy as np

DB = "data/flavor.db"


# Recordings carry a mean of 77 submissions [LOG-FEX-057]; loading all 43.7M
# values as nested dicts would need many GB. Cap submissions per recording --
# the consensus of 8 is already far less noisy than any single one, which is the
# whole point, and the marginal gain from 77 is small next to the memory cost.
MAX_SUBS = 8


def load():
    """{mbid: {submission: {characteristic: {class: value}}}} for multi-submission rows."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    multi = {r[0] for r in conn.execute(
        "SELECT recording_mbid FROM flavor GROUP BY recording_mbid "
        "HAVING COUNT(DISTINCT submission) > 1")}
    print(f"multi-submission library recordings: {len(multi):,}  (capping at "
          f"{MAX_SUBS} submissions each)")
    keep = {}
    for mbid, sub in conn.execute(
            "SELECT recording_mbid, submission FROM flavor "
            "GROUP BY recording_mbid, submission ORDER BY recording_mbid, submission"):
        if mbid in multi:
            keep.setdefault(mbid, set())
            if len(keep[mbid]) < MAX_SUBS:
                keep[mbid].add(sub)
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    n = 0
    for mbid, sub, char, cls, val in conn.execute(
            "SELECT recording_mbid, submission, characteristic, class, value FROM flavor"):
        if mbid in keep and sub in keep[mbid]:
            data[mbid][sub][char][cls] = val
            n += 1
    print(f"values loaded: {n:,}")
    return data


def tv(a, b):
    ks = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in ks)


def consensus(subs, char):
    """Mean distribution across all submissions carrying this characteristic."""
    dists = [s[char] for s in subs.values() if char in s]
    if not dists:
        return None
    keys = set().union(*dists)
    m = {k: float(np.mean([d.get(k, 0.0) for d in dists])) for k in keys}
    t = sum(m.values())
    return {k: v / t for k, v in m.items()} if t > 0 else None


def main():
    data = load()
    chars = sorted({c for subs in data.values() for s in subs.values() for c in s})
    random.seed(17)
    mbids = list(data)
    pairs = [(random.choice(mbids), random.choice(mbids)) for _ in range(30000)]
    pairs = [(a, b) for a, b in pairs if a != b][:20000]

    print(f"\n{'characteristic':22} {'beta_lib':>9} {'single':>8} {'consensus':>10} {'strictness':>11}")
    out, rows = {}, []
    for char in chars:
        within_s, within_m, between = [], [], []
        for mbid, subs in data.items():
            ks = sorted(subs)
            if len(ks) < 2 or char not in subs[ks[0]] or char not in subs[ks[1]]:
                continue
            within_s.append(tv(subs[ks[0]][char], subs[ks[1]][char]))
            con = consensus(subs, char)
            if con:
                within_m.append(tv(subs[ks[0]][char], con))
        for a, b in pairs:
            ca, cb = consensus(data[a], char), consensus(data[b], char)
            if ca and cb:
                between.append(tv(ca, cb))
        if len(within_s) < 50 or len(between) < 50:
            continue
        beta = float(np.mean(between))
        fs, fm = float(np.mean(within_s)) / beta, float(np.mean(within_m)) / beta
        out[char] = {"beta_library": beta, "floor_single": fs, "floor_consensus": fm,
                     "reliability": 1.0 - fs, "n_within": len(within_s)}
        rows.append((char, beta, fs, fm))
        print(f"{char:22} {beta:9.4f} {fs:8.3f} {fm:10.3f} {fs / fm:10.2f}x")

    med_s = float(np.median([r[2] for r in rows]))
    med_m = float(np.median([r[3] for r in rows]))
    print(f"\nmedian floor single-vs-single   : {med_s:.3f}   (old generic-sample value: 0.210)")
    print(f"median floor single-vs-consensus: {med_m:.3f}   <- the honest reference")
    print(f"ratios reported so far are flattering by {med_s / med_m:.2f}x")
    json.dump({"per_characteristic": out, "median_floor_single": med_s,
               "median_floor_consensus": med_m, "flattery_factor": med_s / med_m},
              open("data/reliability_library.json", "w"), indent=1)
    print("written: data/reliability_library.json")


if __name__ == "__main__":
    main()
