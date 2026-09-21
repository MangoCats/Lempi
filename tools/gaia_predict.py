# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply a Gaia chain to lowlevel features, and check it against the reference.

Route 2 `[GDE-FEX-067]`. The predictor and its verification are one tool on
purpose: every number this produces is compared against AcousticBrainz's own
published output for the same recording before it is believed `[GDE-FEX-090]`.
A transform chain that is subtly wrong still emits plausible probabilities, so
"it ran" is not evidence of anything.

Usage:
  python tools/gaia_predict.py <classifier> [n]     # verify against the pairs
"""

from __future__ import annotations

import bisect
import json
import math
import os
import sqlite3
import sys
from pathlib import Path

_NORM = __import__("statistics").NormalDist()
_SQRT2 = math.sqrt(2.0)

sys.path.insert(0, str(Path(__file__).parent))
import gaia_history as gh  # noqa: E402

SVM_DIR = Path(os.environ.get("GAIA_SVM_DIR", "data/essentia/svm_beta1"))
LOWLEVEL_DIR = Path("data/essentia/ab_lowlevel")
REFERENCE_DB = Path("data/flavor-sample.db")


# ------------------------------------------------------------------ features


def flatten(doc: dict) -> dict[str, list[float]]:
    """Lowlevel JSON to Gaia descriptor names.

    Gaia addresses features as `.lowlevel.spectral_decrease.mean`, which is the
    JSON path with a leading dot. Every value becomes a list so scalars and
    vectors are handled identically — `.tonal.hpcp.mean` is 36 numbers and
    `.tonal.tuning_frequency` is one.
    """
    out: dict[str, list[float]] = {}

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            out[path] = [float(node)]
        elif isinstance(node, list) and node and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in node
        ):
            out[path] = [float(x) for x in node]

    walk(doc, "")
    return out


def flatten_strings(doc: dict) -> dict[str, str]:
    """String descriptors, for the enumerate step."""
    out: dict[str, str] = {}

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, str):
            out[path] = node

    walk(doc, "")
    return out


def enumerate_string(maps: dict, name: str, value: str) -> float:
    """The stored code for a string value `[GDE-FEX-097]`.

    The maps are read from the chain, never assumed. They are arbitrary and
    differ per descriptor -- `key_key` codes G# as 0 and A# as 4, while
    `chords_key` codes A# as 11 -- so no ordering rule would have produced
    them. An unknown value falls back to 0 rather than aborting.
    """
    return float(maps.get(name, {}).get(value, 0))


def build_vector(
    features: dict[str, list[float]],
    steps: list[dict],
    order: list[str],
    strings: dict[str, str] | None = None,
    enums: dict | None = None,
    gauss: dict[str, list[float]] | None = None,
) -> list[float]:
    """Normalised feature vector, in `order`.

    Each normalize step is `y = a·x + b` per component `[GDE-FEX-069]`, and
    where a chain has **two** they **compose in sequence** — they are not
    alternatives to choose between. `.lowlevel.spectral_spread.var` reads
    2.197e12 raw; step 0 takes it to 0.0064 and step 1 to 0.5016, which is
    inside the support vectors' observed [0, 11]. Applying only the second to
    the raw value yields 5.6e11, every kernel value underflows to zero, and the
    classifier returns a constant — which is exactly what it did.

    A descriptor the file lacks contributes zeros rather than aborting: partial
    input is a real case, and the verification will show if it matters.
    """
    vec: list[float] = []
    for name in order:
        comp0 = steps[0].get(name)
        if comp0 is None:
            # An enumerated string descriptor: one dimension, an integer code,
            # and NOT normalised -- the support vectors carry raw 0-11 at these
            # positions, so the normalize steps never touched them.
            raw = strings.get(name) if strings else None
            vec.append(enumerate_string(enums or {}, name, raw) if raw is not None else 0.0)
            continue
        xs = features.get(name)
        for i in range(len(comp0["a"])):
            v = xs[i] if xs and i < len(xs) else 0.0
            for si, c in enumerate(steps):
                cc = c.get(name)
                if cc is not None:
                    a, b = cc["a"], cc["b"]
                    j = i if i < len(a) else 0
                    v = a[j] * v + b[j]
                # gaussianize sits BETWEEN the two normalizes [GDE-FEX-098].
                if si == 0 and gauss:
                    table = gauss.get(f"{name}[{i}]")
                    if table:
                        v = gaussianize_value(v, table)
            vec.append(v)
    return vec


# Outlier clamp from Gaia's `distribute` applier. The rank is bounded away from
# both ends before mapping, which is also what keeps erfinv finite.
GAUSS_OUTLIERS = 1


def gaussianize_value(v: float, table: list[float]) -> float:
    """Gaia's `distribute` applier, transcribed `[GDE-FEX-101]`.

        rank    = lower_bound(distribution, v)
        rank    = clamp(rank, outliers, nPoints - outliers)
        normIdx = rank / nPoints
        out     = erfinv(2*normIdx - 1)

    `erfinv(2q-1)` is the inverse normal CDF scaled by 1/sqrt(2), which is the
    factor two earlier guesses missed. Python has no `erfinv`, so it is written
    through `NormalDist.inv_cdf`, which is the same function.
    """
    n = len(table)
    rank = bisect.bisect_left(table, v)
    lo, hi = GAUSS_OUTLIERS, n - GAUSS_OUTLIERS
    rank = lo if rank < lo else (hi if rank > hi else rank)
    return _NORM.inv_cdf(rank / n) / _SQRT2


def multiclass_probability(k: int, r: list[list[float]], iters: int = 100) -> list[float]:
    """Wu, Lin and Weng (2004) pairwise coupling, as libsvm implements it.

    Transcribed rather than derived: the update is a coordinate step on a
    quadratic, and an approximation that merely looked convergent would produce
    a plausible distribution over classes -- exactly the failure the whole
    harness exists to catch.
    """
    p = [1.0 / k] * k
    Q = [[0.0] * k for _ in range(k)]
    for t in range(k):
        Q[t][t] = sum(r[j][t] * r[j][t] for j in range(k) if j != t)
        for j in range(k):
            if j != t:
                Q[t][j] = -r[j][t] * r[t][j]
    for _ in range(iters):
        Qp = [sum(Q[t][j] * p[j] for j in range(k)) for t in range(k)]
        pQp = sum(p[t] * Qp[t] for t in range(k))
        if max(abs(Qp[t] - pQp) for t in range(k)) < 0.005 / k:
            break
        for t in range(k):
            if Q[t][t] <= 0:
                continue
            diff = (-Qp[t] + pQp) / Q[t][t]
            p[t] += diff
            pQp = (pQp + diff * (diff * Q[t][t] + 2 * Qp[t])) / (1 + diff) ** 2
            for j in range(k):
                Qp[j] = (Qp[j] + diff * Q[t][j]) / (1 + diff)
                p[j] /= 1 + diff
    total = sum(p)
    return [v / total for v in p] if total else [1.0 / k] * k


# ----------------------------------------------------------------- the model


class SvmModel:
    """A libsvm model, parsed from the text form Gaia stores."""

    def __init__(self, text: str):
        self.kernel = "rbf"
        self.gamma = 0.0
        self.degree = 3
        self.coef0 = 0.0
        self.nr_class = 2
        self.rho: list[float] = []
        self.label: list[int] = []
        self.probA: list[float] = []
        self.probB: list[float] = []
        self.nr_sv: list[int] = []
        self.sv_coef: list[list[float]] = []
        self.sv: list[dict[int, float]] = []

        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            i += 1
            if line == "SV":
                break
            if not line:
                continue
            key, _, rest = line.partition(" ")
            vals = rest.split()
            if key == "kernel_type":
                self.kernel = vals[0]
            elif key == "gamma":
                self.gamma = float(vals[0])
            elif key == "degree":
                self.degree = int(vals[0])
            elif key == "coef0":
                self.coef0 = float(vals[0])
            elif key == "nr_class":
                self.nr_class = int(vals[0])
            elif key == "rho":
                self.rho = [float(v) for v in vals]
            elif key == "label":
                self.label = [int(v) for v in vals]
            elif key == "probA":
                self.probA = [float(v) for v in vals]
            elif key == "probB":
                self.probB = [float(v) for v in vals]
            elif key == "nr_sv":
                self.nr_sv = [int(v) for v in vals]

        ncoef = self.nr_class - 1
        for line in lines[i:]:
            parts = line.split()
            if not parts:
                continue
            self.sv_coef.append([float(x) for x in parts[:ncoef]])
            self.sv.append(
                {int(k): float(v) for k, v in (p.split(":") for p in parts[ncoef:])}
            )

    def kernel_value(self, sv: dict[int, float], x: list[float]) -> float:
        if self.kernel == "rbf":
            # ||u-v||^2 over the union of indices; x is dense, sv is sparse.
            s = 0.0
            seen = set()
            for k, v in sv.items():
                xv = x[k - 1] if k - 1 < len(x) else 0.0
                s += (v - xv) * (v - xv)
                seen.add(k)
            for k in range(1, len(x) + 1):
                if k not in seen:
                    s += x[k - 1] * x[k - 1]
            return math.exp(-self.gamma * s)
        # polynomial: under the beta1 models actually in production, danceability,
        # mood_aggressive and mood_happy use this, not danceability alone
        # [GDE-FEX-100] (supersedes the beta5-specific claim in [GDE-FEX-068])
        dot = sum(v * (x[k - 1] if k - 1 < len(x) else 0.0) for k, v in sv.items())
        return (self.gamma * dot + self.coef0) ** self.degree

    def decision_binary(self, x: list[float]) -> float:
        k = [self.kernel_value(sv, x) for sv in self.sv]
        return sum(c[0] * kv for c, kv in zip(self.sv_coef, k)) - self.rho[0]

    def decision_values(self, x: list[float]) -> list[float]:
        """One decision value per class pair, as libsvm's `svm_predict_values`.

        Support vectors are grouped by class, and the pair (i, j) uses
        `sv_coef[j-1]` over class i's block and `sv_coef[i]` over class j's.
        That asymmetry is the part worth transcribing carefully rather than
        reconstructing from intuition.
        """
        kv = [self.kernel_value(sv, x) for sv in self.sv]
        start, acc = [], 0
        for c in self.nr_sv:
            start.append(acc)
            acc += c
        out: list[float] = []
        p = 0
        for i in range(self.nr_class):
            for j in range(i + 1, self.nr_class):
                si, sj = start[i], start[j]
                ci, cj = self.nr_sv[i], self.nr_sv[j]
                # sv_coef is stored here as [support_vector][coefficient],
                # transposed from libsvm's C layout of [coefficient][sv]. The
                # indices below are swapped accordingly.
                s = sum(self.sv_coef[si + k][j - 1] * kv[si + k] for k in range(ci))
                s += sum(self.sv_coef[sj + k][i] * kv[sj + k] for k in range(cj))
                out.append(s - self.rho[p])
                p += 1
        return out

    @staticmethod
    def _sigmoid(d: float, a: float, b: float) -> float:
        f = d * a + b
        return math.exp(-f) / (1.0 + math.exp(-f)) if f >= 0 else 1.0 / (1.0 + math.exp(f))

    def probability(self, x: list[float]) -> dict[int, float]:
        """Class probabilities. Binary uses Platt directly; multi-class uses
        libsvm's `multiclass_probability` — the Wu–Lin–Weng pairwise coupling.
        """
        if self.nr_class == 2:
            return self.probability_binary(x)
        dec = self.decision_values(x)
        k = self.nr_class
        # Pairwise probabilities, clamped exactly as libsvm does.
        r = [[0.0] * k for _ in range(k)]
        p = 0
        for i in range(k):
            for j in range(i + 1, k):
                v = min(max(self._sigmoid(dec[p], self.probA[p], self.probB[p]), 1e-7), 1 - 1e-7)
                r[i][j] = v
                r[j][i] = 1 - v
                p += 1
        probs = multiclass_probability(k, r)
        return {self.label[i]: probs[i] for i in range(k)}

    def probability_binary(self, x: list[float]) -> dict[int, float]:
        """P(label) via libsvm's Platt sigmoid, matching `sigmoid_predict`."""
        d = self.decision_binary(x)
        f = d * self.probA[0] + self.probB[0]
        p0 = math.exp(-f) / (1.0 + math.exp(-f)) if f >= 0 else 1.0 / (1.0 + math.exp(f))
        return {self.label[0]: p0, self.label[1]: 1.0 - p0}


# ---------------------------------------------------------------- comparison


def reference(mbid: str, characteristic: str) -> dict[int, dict[str, float]]:
    """Published values per submission: {submission: {class: value}}."""
    con = sqlite3.connect(REFERENCE_DB)
    out: dict[int, dict[str, float]] = {}
    for cls, val, sub in con.execute(
        "SELECT class, value, submission FROM flavor WHERE recording_mbid=? AND characteristic=?",
        (mbid, characteristic),
    ):
        out.setdefault(sub, {})[cls] = val
    return out


def enumerated_descriptors(hist: Path) -> list[str]:
    """The string descriptors the `enumerate` step converts.

    Taken from the chain rather than hardcoded: beta1 converts four, beta5
    eight `[GDE-FEX-092]`, and the difference is exactly the version trap.
    """
    coeffs = gh.normalize_coeffs(hist)[0]
    for i in range(14):
        v = gh.read_param_at(hist, "descriptorNames", occurrence=i)
        if v is None:
            break
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            extra = [x for x in v if x.startswith(".") and x not in coeffs]
            if extra and len(extra) <= 12 and set(v) >= set(coeffs):
                return sorted(extra)
    return []


def verify(classifier: str, limit: int = 60) -> tuple[int, int, float, float]:
    """Predict and compare against the reference. Returns (exact, n, median, max).

    Class names map to model labels **by value, not by position**: the class
    sorted at index i corresponds to model label `i`. Comparing against
    `label[i]` instead made every classifier whose labels read `[1, 0]` appear
    catastrophically wrong -- six of them -- while the chain was correct
    `[GDE-FEX-102]`.
    """
    hist = SVM_DIR / f"{classifier}.history"
    steps = gh.normalize_coeffs(hist)
    coeffs = steps[0]
    enums = gh.enum_maps(hist)
    enames = enumerated_descriptors(hist)
    gauss = gh.gaussianize_tables(hist)
    order = sorted(list(coeffs.keys()) + enames)
    model = SvmModel(gh.extract_svm_model(hist))

    errs: list[float] = []
    for f in sorted(LOWLEVEL_DIR.glob("*.json"))[:limit]:
        doc = json.load(open(f, encoding="utf-8"))
        x = build_vector(flatten(doc), steps, order, flatten_strings(doc), enums, gauss)
        pr = model.probability(x)
        ref = reference(f.stem, classifier)
        if not ref:
            continue
        names = sorted(next(iter(ref.values())).keys())
        # Best over submissions: which one a lowlevel file is remains unknown.
        errs.append(
            min(
                max(abs(cls[names[i]] - pr.get(i, 0.0)) for i in range(len(names)))
                for cls in ref.values()
            )
        )
    if not errs:
        return (0, 0, float("nan"), float("nan"))
    errs.sort()
    return (sum(1 for e in errs if e < 0.001), len(errs), errs[len(errs) // 2], errs[-1])


def main() -> int:
    names = sys.argv[1:] or sorted(p.stem for p in SVM_DIR.glob("*.history"))
    print("%-20s %-9s %10s %10s" % ("classifier", "exact", "median", "max"))
    bad = 0
    for n in names:
        ex, tot, med, mx = verify(n)
        flag = "" if mx < 0.01 else "   <-- FAIL"
        if mx >= 0.01:
            bad += 1
        print("%-20s %4d/%-4d %10.6f %10.4f%s" % (n, ex, tot, med, mx, flag))
    print()
    print(f"{len(names) - bad}/{len(names)} reproduce")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
