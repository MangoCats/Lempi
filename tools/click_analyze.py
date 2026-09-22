#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read a recording of `click_emit.py`'s track and report the rate difference.

    tools/click_analyze.py recording.wav [expected_interval_s]

Reports **emitter ppm minus recorder ppm**. An acoustic measurement cannot give
either one alone: it compares two clocks and has no third to appeal to. To make
a figure absolute, measure one side electrically and subtract -- which is the
whole point of running `bose` past a microphone that has already heard
`lempi02w` `[GDE-ECHO-570]`.

The arithmetic. Clicks leave the emitter `interval` nominal-seconds apart, so
the real time between them is `interval / (1 + e_emit)`. The recorder counts
that in its own samples and reports `interval * (1 + e_rec) / (1 + e_emit)`. Fit
the reported spacing `a` and:

    e_emit - e_rec  ~=  (1 - a) * 1e6  ppm

**How this is fitted matters more than how the clicks are found.** The first
analysis of the `lempi02w` recording reported +12,574 ppm from good data
`[LOG-DRIFT-064]`: it took the rate from the first and last detection, and three
spurious detections set those endpoints. A span estimator hands its entire
answer to its two most fragile points. So this one finds the longest run of
consecutive clicks whose spacing is credible, fits least squares across all of
it, and prints the residual so a bad fit cannot masquerade as a good one.
"""
import statistics
import sys
import wave

try:
    import numpy as np
except ImportError:
    print("needs numpy: pip install numpy")
    sys.exit(2)

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from click_emit import CHIRP_HI, CHIRP_LO, CHIRP_MS, INTERVAL_S  # noqa: E402

# A detection is credible if its spacing from the previous one is within this
# of the expectation. 2 ms at a 1 s interval is 2000 ppm -- far wider than any
# clock error, and far narrower than a missed or doubled click.
TOLERANCE_S = 0.002


def template(rate):
    n = max(1, int(round(rate * CHIRP_MS / 1000.0)))
    i = np.arange(n)
    t = i / rate
    dur = n / rate
    phase = 2.0 * np.pi * (CHIRP_LO * t + (CHIRP_HI - CHIRP_LO) * t * t / (2.0 * dur))
    return np.sin(phase) * (0.5 - 0.5 * np.cos(2.0 * np.pi * i / n))


def read_wav(path):
    with wave.open(path, "rb") as w:
        rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise SystemExit("unsupported sample width: %d bytes" % width)
    x = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    if width == 1:
        x -= 128.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return rate, x


def detect(x, rate, interval):
    """Coarse peaks from a matched filter, refined to sub-sample by parabola."""
    tmpl = template(rate)
    tmpl = tmpl - tmpl.mean()
    # FFT correlation: ten minutes at 48 kHz is 29 M samples, which np.correlate
    # would grind through pairwise.
    n = len(x) + len(tmpl) - 1
    size = 1 << (n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(x, size) * np.conj(np.fft.rfft(tmpl, size)), size)
    corr = np.abs(corr[: len(x)])

    # Threshold well above the bulk of the signal, then take the strongest
    # sample in each neighbourhood as one detection.
    med = np.median(corr)
    mad = np.median(np.abs(corr - med)) or 1.0
    thresh = med + 8.0 * 1.4826 * mad
    guard = int(0.5 * interval * rate)          # half an interval
    peaks = []
    idx = np.flatnonzero(corr > thresh)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] - idx[j] < guard:
            j += 1
        seg = idx[i : j + 1]
        k = int(seg[np.argmax(corr[seg])])
        # Parabolic interpolation through the three samples at the peak: the
        # recording is sampled, the arrival is not, and at 48 kHz one sample is
        # 21 microseconds -- 20 ppm across a 1 s interval if it is thrown away.
        if 0 < k < len(corr) - 1:
            a, b, c = corr[k - 1], corr[k], corr[k + 1]
            denom = a - 2 * b + c
            k = k + (0.5 * (a - c) / denom if denom else 0.0)
        peaks.append(k / rate)
        i = j + 1
    return peaks, float(thresh)


def longest_clean_run(times, interval):
    """The longest stretch whose consecutive spacings are all credible."""
    best = cur = [0]
    for i in range(1, len(times)):
        if abs((times[i] - times[i - 1]) - interval) <= TOLERANCE_S:
            cur.append(i)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [i]
    return best if len(best) >= len(cur) else cur


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]
    interval = float(argv[1]) if len(argv) > 1 else INTERVAL_S

    rate, x = read_wav(path)
    print("%s: %d Hz, %.1f s" % (path, rate, len(x) / rate))
    times, thresh = detect(x, rate, interval)
    print("  detections : %d  (expected ~%d)" % (len(times), int(len(x) / rate / interval)))
    if len(times) < 10:
        print("  too few to fit -- is the microphone hearing the speaker?")
        return 1

    run = longest_clean_run(times, interval)
    print("  clean run  : %d consecutive, %.1f s" % (len(run), times[run[-1]] - times[run[0]]))
    if len(run) < 10:
        print("  no usable run; spacings are not credible. Check for a resampler,")
        print("  a dropout, or a second sound source in the room `[GOV-SRC-020]`.")
        return 1
    dropped = len(times) - len(run)
    if dropped:
        print("  discarded  : %d detection(s) outside the run -- this is the guard"
              % dropped)
        print("               that `[LOG-DRIFT-064]` was written about.")

    # Least squares over the whole run: spacing `a` seconds per click index.
    idx = np.array([i - run[0] for i in run], dtype=np.float64)
    t = np.array([times[i] for i in run], dtype=np.float64)
    a, b = np.polyfit(idx, t, 1)
    resid = t - (a * idx + b)
    rms = float(np.sqrt(np.mean(resid**2)))

    ppm = (interval - a) / interval * 1e6
    # Standard error of the slope, converted to ppm. With residuals this is an
    # honest precision statement rather than the quantisation floor, which is
    # the number that flattered the earlier readings `[LOG-FIX-020]`.
    n = len(idx)
    se_a = rms * np.sqrt(n / (n * np.sum(idx**2) - np.sum(idx) ** 2)) if n > 2 else 0.0
    se_ppm = float(se_a) / interval * 1e6

    print()
    print("  spacing    : %.9f s  (nominal %.6f)" % (a, interval))
    print("  residual   : %.1f us rms, worst %.1f us"
          % (rms * 1e6, float(np.max(np.abs(resid))) * 1e6))
    print()
    print("  emitter - recorder = %+.3f ppm  +/- %.3f" % (ppm, se_ppm))
    print()
    if rms > 0.0005:
        print("  NOTE residual over 500 us: the fit is not clean. Reverberation,")
        print("  a moving microphone, or a resampler in the chain. Treat the")
        print("  figure as indicative and say so `[GOV-SRC-020]`.")
    span_ppm = (interval - (t[-1] - t[0]) / idx[-1]) / interval * 1e6
    print("  (span-only estimator would say %+.3f ppm; it is printed for" % span_ppm)
    print("   comparison and must not be the number reported `[LOG-DRIFT-064]`.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
