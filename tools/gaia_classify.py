# SPDX-License-Identifier: AGPL-3.0-or-later
"""Classify lowlevel features into the 71-dimension flavor vector.

The production path for route 2 `[LOG-FEX-102]`: load the 18 Gaia chains once,
then map any lowlevel JSON — from the archive or from our own extractor — to
the same highlevel values AcousticBrainz publishes, verified to within 0.0072.

Loading is the expensive part (83 MB of models, ~10 s); classification is
milliseconds. So a `Classifier` is built once and reused across a library.

Usage:
  python tools/gaia_classify.py <lowlevel.json> [more.json ...]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gaia_history as gh  # noqa: E402
import gaia_predict as gp  # noqa: E402

# The extractor and the models are committed, in `vendor/essentia/` -- they
# lived untracked in `data/essentia/` until 2026-10-01, and were lost with it
# when the repository was re-seeded; flavor extraction then failed on every
# passage without saying why. Found from the repository, not the working
# directory, so it does not depend on where a tool is started.
# `LEMPI_ESSENTIA_DIR` names another copy.
ESSENTIA_DIR = Path(os.environ.get("LEMPI_ESSENTIA_DIR")
                    or Path(__file__).resolve().parent.parent / "vendor" / "essentia")
MODEL_DIR = ESSENTIA_DIR / "svm_beta1"
CHAINS = 18


class ModelsMissing(RuntimeError):
    """Fewer classifier models than the 18 flavor needs. Raised rather than
    classifying with what is there: with none, every passage would come out
    "classified" into nothing, be cached, and never be tried again."""


class Chain:
    """One classifier: its transform chain and its SVM, ready to apply."""

    def __init__(self, path: Path):
        self.name = path.stem
        self.steps = gh.normalize_coeffs(path)
        self.enums = gh.enum_maps(path)
        self.gauss = gh.gaussianize_tables(path)
        enames = gp.enumerated_descriptors(path)
        self.order = sorted(list(self.steps[0].keys()) + enames)
        self.model = gp.SvmModel(gh.extract_svm_model(path))
        self.classes = gh.class_mapping(path)

    def apply(self, doc: dict) -> dict[str, float]:
        x = gp.build_vector(
            gp.flatten(doc), self.steps, self.order,
            gp.flatten_strings(doc), self.enums, self.gauss,
        )
        pr = self.model.probability(x)
        # Label VALUE indexes the sorted class names, not label position
        # [LOG-FEX-102] -- the bug that made six classifiers look broken.
        return {self.classes[i]: pr.get(i, 0.0) for i in range(len(self.classes))}


class Classifier:
    """All 18 chains, loaded once."""

    def __init__(self, model_dir: Path = MODEL_DIR):
        found = sorted(Path(model_dir).glob("*.history"))
        if len(found) != CHAINS:
            raise ModelsMissing(
                f"{len(found)} of {CHAINS} flavor classifier models found in {model_dir} -- "
                "see vendor/essentia/README.md")
        self.chains = [Chain(p) for p in found]

    def classify(self, doc: dict) -> dict[str, dict[str, float]]:
        return {c.name: c.apply(doc) for c in self.chains}


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 2
    import time

    t0 = time.time()
    clf = Classifier()
    print(f"loaded {len(clf.chains)} classifiers in {time.time() - t0:.1f}s\n")

    for p in paths:
        doc = json.load(open(p, encoding="utf-8"))
        t1 = time.time()
        out = clf.classify(doc)
        ms = (time.time() - t1) * 1000
        print(f"{p.name}  ({ms:.0f} ms)")
        for name in sorted(out):
            top = max(out[name].items(), key=lambda kv: kv[1])
            print(f"   {name:<20} {top[0]:<16} {top[1]:.3f}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
