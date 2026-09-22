#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Does `click_analyze.py` recover a rate error that was put there on purpose?

An instrument that has only ever been pointed at unknown quantities has not
been tested. This synthesises a recording whose answer is known, including the
two ways the real one goes wrong -- a spurious detection at the edge, which is
what `[LOG-DRIFT-064]` actually was, and a missing click in the middle.

    tools/test_click_analyze.py
"""
import os
import sys
import tempfile
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from click_analyze import main as analyze_main  # noqa: E402
from click_emit import CHIRP_HI, CHIRP_LO, CHIRP_MS  # noqa: E402

REC_RATE = 48000


def chirp_at(rate):
    n = max(1, int(round(rate * CHIRP_MS / 1000.0)))
    i = np.arange(n)
    t = i / rate
    dur = n / rate
    phase = 2.0 * np.pi * (CHIRP_LO * t + (CHIRP_HI - CHIRP_LO) * t * t / (2.0 * dur))
    return np.sin(phase) * (0.5 - 0.5 * np.cos(2.0 * np.pi * i / n))


def synth(path, ppm, seconds=120, noise=0.02, spurious=(), drop=()):
    """A recording of a click track whose emitter is `ppm` fast."""
    # Emitter `ppm` fast means each nominal second takes 1/(1+ppm) real
    # seconds; the recorder is the reference here, so that is what it measures.
    spacing = 1.0 / (1.0 + ppm * 1e-6)
    n = int(seconds * REC_RATE)
    x = np.random.default_rng(7).normal(0.0, noise, n)
    burst = chirp_at(REC_RATE)
    # A fixed flight time from speaker to microphone -- the constant this
    # method is supposed to be blind to.
    flight = 0.0037
    count = int(seconds / spacing) - 1
    for k in range(count):
        if k in drop:
            continue
        at = int(round((k * spacing + flight) * REC_RATE))
        if 0 <= at < n - len(burst):
            x[at : at + len(burst)] += burst
    for s in spurious:
        at = int(round(s * REC_RATE))
        if 0 <= at < n - len(burst):
            x[at : at + len(burst)] += burst
    pcm = np.clip(x, -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(REC_RATE)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    return count


def run(label, path, want, tol):
    from io import StringIO

    held, sys.stdout = sys.stdout, StringIO()
    try:
        analyze_main([path])
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = held
    got = None
    for line in out.splitlines():
        if "emitter - recorder" in line:
            got = float(line.split("=")[1].split("ppm")[0].strip().replace("+", ""))
    if got is None:
        print("FAIL %-34s no figure reported\n%s" % (label, out))
        return False
    ok = abs(got - want) <= tol
    print("%s %-34s want %+8.2f  got %+8.2f  (tol %.2f)"
          % ("ok  " if ok else "FAIL", label, want, got, tol))
    if not ok:
        print(out)
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="clicktest")
    cases = [
        ("clean, +14.2 ppm", dict(ppm=14.2), 14.2, 0.6),
        ("clean, -2.089 ppm", dict(ppm=-2.089), -2.089, 0.6),
        ("clean, zero", dict(ppm=0.0), 0.0, 0.6),
        # The failure that produced +12,574 ppm: extra detections landing
        # outside the run, at both ends, where a span estimator anchors.
        ("spurious at both edges", dict(ppm=14.2, spurious=(0.4, 119.6)), 14.2, 0.6),
        ("a dropped click", dict(ppm=14.2, drop=(50,)), 14.2, 0.6),
        ("noisier room", dict(ppm=14.2, noise=0.08), 14.2, 1.0),
    ]
    good = True
    for label, kw, want, tol in cases:
        p = os.path.join(tmp, label.replace(" ", "_").replace(",", "") + ".wav")
        synth(p, **kw)
        good &= run(label, p, want, tol)
        os.remove(p)
    os.rmdir(tmp)
    print("\n%s" % ("all passed" if good else "FAILURES -- do not trust the instrument"))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
