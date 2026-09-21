# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[SPEC-SA-090] Does slice duration degrade flavor?

Vipunen must extract features PER PASSAGE, not per file: a 40-track DAO file needs
40 extractor runs over 40 slices. But the classifiers were distilled from
features computed over WHOLE recordings as submitted to AcousticBrainz, and
MuLibPlay's radio passages run to a 12-second minimum with 2.5% under 90 s.

So: is a short slice in-distribution, and where does it stop being so?

Design -- duration is varied while everything else is held constant. Each track
is truncated to a series of centred excerpts and compared against its OWN
full-length flavor, so the measurement isolates duration from the confound of
different audio. Distance is SPEC005 flavor distance [SPEC-FD-040], the quantity
the Program Director actually consumes, expressed in units of the reproducibility
floor so the result is directly interpretable: below 1.0 means the truncation
costs less than AcousticBrainz's own submission-to-submission variance.

Usage:
    python tools/passage_duration_experiment.py --tracks 8
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_store as ms
from build_stageb_dataset import featurise

EXTRACTOR = os.path.join("data", "essentia", "streaming_extractor_music.exe")
MODEL_DIR = os.path.join("data", "models")
DURATIONS = [180, 90, 45, 20, 12]          # seconds; None = whole track


def slice_audio(src, out_wav, centre_s, dur_s):
    """Centred excerpt, decoded to 44.1k stereo WAV."""
    start = max(0.0, centre_s - dur_s / 2.0)
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur_s:.3f}",
         "-i", src, "-ar", "44100", "-ac", "2", out_wav],
        capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000


def extract(path, out_json):
    r = subprocess.run([EXTRACTOR, path, out_json], capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(out_json)


def load_models():
    man = json.load(open(os.path.join(MODEL_DIR, "manifest.json")))["models"]
    out = {}
    for char, meta in man.items():
        if meta["path"].endswith(".npz"):
            out[char] = ("mlp", ms.load_mlp(os.path.join(MODEL_DIR, meta["path"])), meta)
        else:
            import joblib
            out[char] = ("gbm", joblib.load(os.path.join(MODEL_DIR, meta["path"])), meta)
    return out


def flavor_of(lowlevel_json, models, feat_names):
    """71-dim flavor from a lowlevel document, as {characteristic: {class: value}}."""
    doc = json.load(open(lowlevel_json))
    feats = dict(featurise(doc))
    x = np.array([[feats.get(n, 0.0) for n in feat_names]], dtype=np.float32)
    out = {}
    for char, (kind, bundle, meta) in models.items():
        p = ms.predict_mlp(bundle, x) if kind == "mlp" else ms.predict_gbm(bundle, x)
        out[char] = dict(zip(meta["classes"], p[0]))
    return out


def flavor_distance(a, b, rel):
    """SPEC005: reliability-weighted, scale-normalised total variation."""
    num = den = 0.0
    for char in a:
        if char not in b or char not in rel:
            continue
        _, _, beta, w = rel[char]
        classes = set(a[char]) | set(b[char])
        tv = 0.5 * sum(abs(a[char].get(k, 0.0) - b[char].get(k, 0.0)) for k in classes)
        num += w * (tv / beta)
        den += w
    return num / den if den else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", type=int, default=8)
    args = ap.parse_args()

    rel = json.load(open("data/reliability.json"))
    floor = float(np.median([v[1] / v[2] for v in rel.values()]))
    models = load_models()
    feat_names = next(iter(models.values()))[1]["feat_names"] \
        if next(iter(models.values()))[0] == "mlp" else None
    if feat_names is None:
        feat_names = [str(f) for f in np.load("data/stageb/dataset.npz",
                                              allow_pickle=True)["feat_names"]]

    cand = json.load(open("data/essentia/stageA_candidates.json"))
    picked = [(m, p) for m, p in cand.items() if os.path.exists(p)][:args.tracks]
    print(f"tracks: {len(picked)}   durations: full + {DURATIONS}")
    print(f"reference floor (median AB self-error/beta): {floor:.3f}\n")

    results = defaultdict(list)
    tmp = tempfile.mkdtemp(prefix="lempi_slice_")
    t0 = time.time()
    for i, (mbid, path) in enumerate(picked, 1):
        base_json = os.path.join(tmp, f"{i}_full.json")
        if not extract(path, base_json):
            print(f"[{i}] FULL EXTRACT FAILED {os.path.basename(path)[:40]}")
            continue
        total = json.load(open(base_json))["metadata"]["audio_properties"]["length"]
        base = flavor_of(base_json, models, feat_names)
        print(f"[{i}/{len(picked)}] {os.path.basename(path)[:44]}  {total:.0f}s", flush=True)
        for d in DURATIONS:
            if d >= total:
                continue
            wav = os.path.join(tmp, f"{i}_{d}.wav")
            js = os.path.join(tmp, f"{i}_{d}.json")
            if not slice_audio(path, wav, total / 2.0, d) or not extract(wav, js):
                print(f"      {d:4d}s  EXTRACTION FAILED")
                results[d].append(None)
                continue
            dist = flavor_distance(base, flavor_of(js, models, feat_names), rel)
            results[d].append(dist)
            print(f"      {d:4d}s  distance {dist:.4f}  = {dist / floor:.2f}x floor", flush=True)
            for f in (wav, js):
                os.path.exists(f) and os.remove(f)

    print(f"\n=== flavor distance from full-length, by slice duration ===")
    print(f"{'duration':>9} {'n':>3} {'median':>8} {'vs floor':>9} {'failures':>9}")
    summary = {}
    for d in DURATIONS:
        vals = [v for v in results[d] if v is not None]
        fails = sum(1 for v in results[d] if v is None)
        if not vals:
            print(f"{d:8d}s {0:3d} {'-':>8} {'-':>9} {fails:9d}")
            continue
        med = float(np.median(vals))
        summary[d] = {"n": len(vals), "median": med, "vs_floor": med / floor, "failures": fails}
        print(f"{d:8d}s {len(vals):3d} {med:8.4f} {med / floor:8.2f}x {fails:9d}")
    print(f"\nelapsed {time.time() - t0:.0f}s")
    json.dump({"floor": floor, "by_duration": summary},
              open("data/stageb/passage_duration.json", "w"), indent=1)


if __name__ == "__main__":
    main()
