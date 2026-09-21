# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[GDE-FEX-065] Route 3, step 1: build the paired lowlevel -> highlevel dataset.

We do not need to *be* Gaia, we need to *predict what Gaia predicted*. The paired
AcousticBrainz sample dumps give ~88k recordings with both the lowlevel feature
vector and AcousticBrainz's own highlevel output -- a fully labelled dataset with
a deterministic teacher.

Feature selection follows the models' own `.history.param`, which specifies
`preprocessing: nobands`: the raw barkbands / erbbands / melbands arrays are
excluded, while their derived scalar statistics (flatness_db, kurtosis, skewness,
spread, crest) are kept.

Usage:
    python tools/build_stageb_dataset.py --dumps data/ab-dumps --out data/stageb
"""

import argparse
import json
import os
import re
import sys
import tarfile

import numpy as np
import zstandard

NAME_RE = re.compile(r"^([0-9a-f-]{36})-(\d+)\.json$")

# Raw per-band arrays Gaia's "nobands" preprocessing drops. Derived scalar stats
# over these bands (e.g. lowlevel.barkbands_kurtosis.mean) are NOT matched here
# and are therefore retained.
BAND_ARRAYS = re.compile(r"\.(barkbands|erbbands|melbands|spectral_contrast_coeffs|spectral_contrast_valleys)\.")

# Variable-length or structural fields carrying no fixed-width signal.
SKIP = re.compile(r"(beats_position|\.cov$|\.icov$|^metadata)")

KEYS = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]
SCALES = ["major", "minor"]


def featurise(doc):
    """Flatten one lowlevel document into an ordered (name, value) list."""
    out = []

    def walk(node, prefix=""):
        for k, v in sorted(node.items()):
            path = f"{prefix}.{k}" if prefix else k
            if SKIP.search(path):
                continue
            if isinstance(v, dict):
                walk(v, path)
            elif isinstance(v, bool):
                continue
            elif isinstance(v, (int, float)):
                out.append((path, float(v)))
            elif isinstance(v, list):
                if BAND_ARRAYS.search(path + "."):
                    continue
                # Fixed-width numeric vectors only (mfcc/gfcc means, hpcp, etc.)
                if v and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
                    for i, x in enumerate(v):
                        out.append((f"{path}[{i}]", float(x)))

    walk(doc)

    # Categorical tonal fields -> one-hot, so key is not treated as ordinal.
    tonal = doc.get("tonal", {})
    for field, vocab in (("key_key", KEYS), ("chords_key", KEYS),
                         ("key_scale", SCALES), ("chords_scale", SCALES)):
        val = tonal.get(field)
        for token in vocab:
            out.append((f"tonal.{field}={token}", 1.0 if val == token else 0.0))
    return out


def stream(path, handler):
    """Stream a .tar.zst dump, calling handler(mbid, submission, doc)."""
    dctx = zstandard.ZstdDecompressor()
    n = 0
    with open(path, "rb") as fh, dctx.stream_reader(fh) as reader:
        with tarfile.open(fileobj=reader, mode="r|") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                m = NAME_RE.match(os.path.basename(member.name))
                if not m:
                    continue
                try:
                    doc = json.load(tf.extractfile(member))
                except (json.JSONDecodeError, OSError):
                    continue
                handler(m.group(1), int(m.group(2)), doc)
                n += 1
                if n % 20000 == 0:
                    print(f"    ...{n:,}", file=sys.stderr, flush=True)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumps", default="data/ab-dumps")
    ap.add_argument("--out", default="data/stageb")
    ap.add_argument("--limit", type=int, default=0, help="cap recordings (0 = all)")
    args = ap.parse_args()

    hl_path = os.path.join(args.dumps, "acousticbrainz-highlevel-sample-json-20220623-0.tar.zst")
    ll_path = os.path.join(args.dumps, "ll-sample.tar.zst")

    # --- highlevel targets first (85 MB, cheap) ---
    print("Reading highlevel labels...", file=sys.stderr)
    labels = {}

    def take_hl(mbid, sub, doc):
        hl = doc.get("highlevel")
        if hl:
            labels[(mbid, sub)] = {c: v.get("all", {}) for c, v in hl.items()}

    stream(hl_path, take_hl)
    print(f"  labelled: {len(labels):,}", file=sys.stderr)

    # Fix a stable characteristic/class ordering from the most common shape.
    chars = {}
    for lab in labels.values():
        for c, classes in lab.items():
            chars.setdefault(c, set()).update(classes)
    char_classes = {c: sorted(ks) for c, ks in sorted(chars.items())}
    target_cols = [(c, k) for c, ks in char_classes.items() for k in ks]
    print(f"  characteristics: {len(char_classes)}  target dims: {len(target_cols)}", file=sys.stderr)

    # --- lowlevel features, joined on (mbid, submission) ---
    print("Reading lowlevel features...", file=sys.stderr)
    feat_names, X, Y, keys = None, [], [], []

    def take_ll(mbid, sub, doc):
        nonlocal feat_names
        lab = labels.get((mbid, sub))
        if lab is None:
            return
        if args.limit and len(X) >= args.limit:
            return
        feats = featurise(doc)
        names = [n for n, _ in feats]
        if feat_names is None:
            feat_names = names
        elif names != feat_names:
            return  # shape mismatch; skip rather than misalign columns
        y = [lab.get(c, {}).get(k, np.nan) for c, k in target_cols]
        if any(np.isnan(y)):
            return
        X.append([v for _, v in feats])
        Y.append(y)
        keys.append(f"{mbid}-{sub}")

    stream(ll_path, take_ll)

    X = np.asarray(X, dtype=np.float32)
    Y = np.asarray(Y, dtype=np.float32)
    print(f"\n  paired samples: {X.shape[0]:,}   features: {X.shape[1]:,}   targets: {Y.shape[1]}",
          file=sys.stderr)

    os.makedirs(args.out, exist_ok=True)
    np.savez_compressed(
        os.path.join(args.out, "dataset.npz"),
        X=X, Y=Y,
        feat_names=np.array(feat_names),
        target_cols=np.array([f"{c}|{k}" for c, k in target_cols]),
        keys=np.array(keys),
    )
    print(f"  written: {args.out}/dataset.npz", file=sys.stderr)


if __name__ == "__main__":
    main()
