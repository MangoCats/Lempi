#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tell a *step* in a node's output delay from a *dropped* click.

    tools/click_steps.py recording.wav [expected_interval_s]

`click_analyze.py` reports the rate from the longest run of consistently spaced
detections, and discards the rest. That guard is right for a rate
`[LOG-DRIFT-064]`, but it throws away the evidence for a different question:
**did the delay move, or did the analyser just miss some clicks?**

Both look identical in the summary -- a short clean run and a large discard
count -- and they mean opposite things. A node that drops detections is a node
recorded at too low a level in a noisy room. A node that steps its output delay
is unusable for echo `[GDE-ECHO-420]`, because trimming corrects a slope and a
step is not one.

The discrimination is simple once every detection is kept. Give each detection
its integer click index and fit ONE line to all of them:

* a **dropped** click leaves a gap in the indices and costs the fit nothing --
  the residuals either side of the gap are unchanged;
* a **step** shifts every later click bodily, which no single line can absorb,
  so it appears as a plateau shift in the residuals at the moment it happened.

Written for `lempiplay3`, whose ALSA-reported delay wanders 9.37 ms
`[LOG-P4-080]` while its acoustic output holds 1.72 us rms `[LOG-P4-100]`. The
two claims are only compatible because the wander is in the reporting.
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import numpy as np                                        # noqa: E402
import click_analyze as ca                                # noqa: E402


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]
    interval = float(argv[1]) if len(argv) > 1 else 1.0

    rate, x = ca.read_wav(path)
    peaks, thresh = ca.detect(x, rate, interval)
    if len(peaks) < 4:
        print("%s: %d detections -- nothing to fit" % (path, len(peaks)))
        return 1
    t = np.array(sorted(peaks))

    idx = np.round((t - t[0]) / interval).astype(int)
    # Two detections landing on one index means a spurious extra; keep the
    # first and say so rather than letting it distort the fit.
    keep = np.concatenate(([True], np.diff(idx) > 0))
    dup = len(t) - int(keep.sum())
    t, idx = t[keep], idx[keep]

    # The first detection is routinely the recording's own edge -- a chirp
    # caught part-way through correlates off-centre. It is one sample of many
    # and excluding it is not tidying: including it moved the residual from
    # 1.7 us rms to 24 us on the run this was written for.
    t, idx = t[1:], idx[1:]

    A = np.vstack([idx, np.ones(len(idx))]).T
    slope, icept = np.linalg.lstsq(A, t, rcond=None)[0]
    res = (t - (slope * idx + icept)) * 1e6                # microseconds

    span = t[-1] - t[0]
    print("%s: %d detections over %.1f s (threshold %.1f, %d duplicate%s dropped)"
          % (path, len(t), span, thresh, dup, "" if dup == 1 else "s"))
    print("  fitted spacing : %.9f s  -> %+.3f ppm emitter - recorder"
          % (slope, (1 - slope / interval) * 1e6))
    print("  residual       : %.2f us rms, min %+.2f, max %+.2f, p-p %.2f us"
          % (np.sqrt((res ** 2).mean()), res.min(), res.max(), res.max() - res.min()))

    half = len(t) // 2
    a = (1 - np.polyfit(idx[:half], t[:half], 1)[0] / interval) * 1e6
    b = (1 - np.polyfit(idx[half:], t[half:], 1)[0] / interval) * 1e6
    print("  halves         : %+.3f ppm then %+.3f ppm (drift %+.3f)" % (a, b, b - a))

    # The verdict. A step big enough to matter for echo is milliseconds; the
    # noise floor of a clean acoustic run is microseconds `[LOG-CAL-020]`, so
    # there is no ambiguous middle to agonise over.
    jumps = np.abs(np.diff(res))
    big = np.nonzero(jumps > 200.0)[0]
    gaps = int((np.diff(idx) > 1).sum())
    missing = int((np.diff(idx) - 1)[np.diff(idx) > 1].sum())
    print()
    print("  dropped clicks : %d, in %d gap(s)" % (missing, gaps))
    if len(big) == 0:
        print("  STEPS          : none over %.0f s -- the delay held to %.1f us p-p"
              % (span, res.max() - res.min()))
        print("                   A large discard count here is a LEVEL problem,")
        print("                   not a stability one.")
    else:
        print("  STEPS          : %d discontinuit%s over %.0f s"
              % (len(big), "y" if len(big) == 1 else "ies", span))
        for i in big:
            print("      at %.1f s: %+.3f ms" % (t[i + 1], (res[i + 1] - res[i]) / 1e3))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
