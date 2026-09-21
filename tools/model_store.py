# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Persistence for the Stage B distilled classifiers [LOG-FEX-065].

Design constraints, in priority order:

1. **Inference must not depend on scikit-learn.** These models may ship inside
   Vipunen `[GDE-ARC-010]`, onto machines we do not control, and be loaded years
   from now. sklearn pickles break across versions; raw weight arrays do not.
   An MLP forward pass is three matmuls and a ReLU -- see `predict_mlp`, which
   needs only numpy and is ~30 lines to port to Rust.

2. **Self-describing.** Each bundle carries its feature ordering, characteristic
   and class names, training configuration, and measured accuracy, so a stored
   model can be audited without reference to the code that made it. Supports the
   provenance and scorecard requirements `[GDE-FBD-020]`, `[GDE-CHT-030]`.

3. **Compact.** Raw fp32 weights are ~2.4 MB per characteristic against ~19.5 MB
   for a joblib dump of the same model -- joblib also pickles Adam optimizer
   state, which is dead weight after training.

Gradient-boosting models cannot be reduced to weight matrices and fall back to
joblib. Only `genre_tzanetakis` and `mood_party` currently need this, and they
cost more to store than all sixteen MLPs combined, so it is worth revisiting
after [LOG-NEXT-010].
"""

import json
import os

import numpy as np

FORMAT_VERSION = 1


def save_mlp(path, model, scaler, feat_names, characteristic, classes, meta):
    """Persist an MLPRegressor as raw fp32 arrays plus metadata."""
    payload = {
        "format_version": FORMAT_VERSION,
        "kind": "mlp",
        "characteristic": characteristic,
        "classes": np.array(classes),
        "feat_names": np.array(feat_names),
        "scaler_mean": scaler.mean_.astype(np.float32),
        "scaler_scale": scaler.scale_.astype(np.float32),
        "n_layers": len(model.coefs_),
        "activation": model.activation,
        "meta": np.array(json.dumps(meta)),
    }
    for i, (w, b) in enumerate(zip(model.coefs_, model.intercepts_)):
        payload[f"W{i}"] = w.astype(np.float32)
        payload[f"b{i}"] = b.astype(np.float32)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    np.savez_compressed(path, **payload)
    return os.path.getsize(path)


def load_mlp(path):
    d = np.load(path, allow_pickle=False)
    if int(d["format_version"]) != FORMAT_VERSION:
        raise ValueError(f"unsupported format_version {d['format_version']}")
    n = int(d["n_layers"])
    return {
        "kind": "mlp",
        "characteristic": str(d["characteristic"]),
        "classes": [str(c) for c in d["classes"]],
        "feat_names": [str(f) for f in d["feat_names"]],
        "scaler_mean": d["scaler_mean"],
        "scaler_scale": d["scaler_scale"],
        "activation": str(d["activation"]),
        "weights": [(d[f"W{i}"], d[f"b{i}"]) for i in range(n)],
        "meta": json.loads(str(d["meta"])),
    }


def predict_mlp(bundle, X):
    """Forward pass. numpy only -- no scikit-learn, no pickle.

    Mirrors sklearn's MLPRegressor: hidden layers use the stored activation,
    the output layer is identity (regression). Output is then projected back
    onto the simplex, matching how these models are used [MFL-DEF-040].
    """
    h = (np.asarray(X, dtype=np.float32) - bundle["scaler_mean"]) / bundle["scaler_scale"]
    h = np.nan_to_num(h, nan=0.0, posinf=0.0, neginf=0.0)
    weights = bundle["weights"]
    act = bundle["activation"]
    for w, b in weights[:-1]:
        h = h @ w + b
        if act == "relu":
            h = np.maximum(h, 0.0)
        elif act == "tanh":
            h = np.tanh(h)
        elif act == "logistic":
            h = 1.0 / (1.0 + np.exp(-h))
        else:
            raise ValueError(f"unsupported activation {act}")
    w, b = weights[-1]
    out = h @ w + b

    return _to_simplex(out)


def _to_simplex(out):
    """Project raw regressor output onto the probability simplex.

    Degenerate rows -- every output clipped to zero -- fall back to UNIFORM, not
    to a zero vector. A zero vector is not a distribution: it would violate
    [MFL-DEF-040] and make total-variation distance meaningless against it.
    Uniform is the honest answer when the model expresses nothing. Rare
    (~1 row in 4,000) but it must not be silently wrong.
    """
    out = np.clip(np.asarray(out, dtype=np.float64), 0.0, None)
    s = out.sum(axis=1, keepdims=True)
    degenerate = (s < 1e-9).ravel()
    if degenerate.any():
        out[degenerate] = 1.0 / out.shape[1]
        s = out.sum(axis=1, keepdims=True)
    return out / s


def save_gbm(path, models, scaler, feat_names, characteristic, classes, meta):
    """Fallback for gradient-boosting models, which have no weight-matrix form.

    Carries a scikit-learn dependency and version fragility -- deliberately kept
    distinct from the MLP path so the cost stays visible.
    """
    import joblib
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    joblib.dump({
        "format_version": FORMAT_VERSION, "kind": "gbm",
        "characteristic": characteristic, "classes": list(classes),
        "feat_names": list(feat_names),
        "scaler_mean": scaler.mean_.astype(np.float32),
        "scaler_scale": scaler.scale_.astype(np.float32),
        "models": models, "meta": meta,
    }, path, compress=3)
    return os.path.getsize(path)


def predict_gbm(bundle, X):
    h = (np.asarray(X, dtype=np.float32) - bundle["scaler_mean"]) / bundle["scaler_scale"]
    h = np.nan_to_num(h, nan=0.0, posinf=0.0, neginf=0.0)
    out = np.column_stack([m.predict(h) for m in bundle["models"]])
    return _to_simplex(out)
