#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a blind listening test for `[GDE-ECHO-550]`: is a trimmed frame audible?

    tools/trim_audibility.py out.wav [seconds_per_segment] [trim_interval_s] [seed]

Phase 5 corrects a *rate* error by dropping or duplicating one frame on
submission `[GDE-ECHO-340]`. The plan sized that at "roughly one frame per
minute at 0.4 ppm" and was believed inaudible by inspection. At the drift
actually measured the interval is one frame every **1.7 to 5.7 s**
`[LOG-CAL-080]`, and once every 1.7 s is not obviously inaudible. This turns
that from an assumption into an answer, which decides whether Phase 5 can trim
frames or must resample fractionally.

**The signal is the worst case on purpose.** Sustained pure tones, where
removing one sample leaves a phase discontinuity with nothing to mask it. Real
music hides such things behind transients and noise; a sustained chord does
not. So a *negative* result here is strong -- if it cannot be heard on this, it
cannot be heard on music -- while a positive result only means the worst case
is audible, and should be retested on real material before concluding.

**Run it on a node with no codec and no resampler in the path.** `bose`'s I²S
DAC via `aplay -D hw:...` qualifies. `lempi02w` does not: SBC is lossy and
PipeWire's adaptive resampler is already inserting and dropping samples of its
own `[LOG-CAL-080]`, so the test would measure their sum.

The order of trimmed and clean segments is chosen by `seed` and printed at the
end. **Do not read it to the listener before they answer.**
"""
import math
import random
import struct
import sys
import wave

FREQS = (220.0, 277.2, 329.6)   # a sustained A major triad, no vibrato
AMPLITUDE = 0.20                 # modest: this is played near someone
MARKER_HZ = 1500.0
MARKER_MS = 70
GAP_MS = 130


def tone_frame(i, rate):
    """One frame of the sustained chord, from continuous phase."""
    v = 0.0
    for f in FREQS:
        v += math.sin(2.0 * math.pi * f * i / rate)
    return v / len(FREQS)


def marker(rate, count):
    """`count` short beeps, so the listener can name the segment."""
    out = []
    n = int(rate * MARKER_MS / 1000)
    g = int(rate * GAP_MS / 1000)
    for _ in range(count):
        for i in range(n):
            env = math.sin(math.pi * i / n)          # no click of its own
            out.append(math.sin(2.0 * math.pi * MARKER_HZ * i / rate) * env * 0.25)
        out.extend([0.0] * g)
    out.extend([0.0] * g)
    return out


def segment(rate, seconds, kind, interval_s):
    """A sustained chord, optionally trimmed every `interval_s`.

    The source phase runs continuously and frames are then skipped or
    repeated, which is exactly what a trim on submission does -- as opposed to
    resynthesising at a shifted phase, which would sound like something else.
    """
    total = int(rate * seconds)
    step = int(rate * interval_s)
    out = []
    i = 0
    while len(out) < total:
        out.append(tone_frame(i, rate))
        if kind != "clean" and step and i and i % step == 0:
            if kind == "drop":
                i += 1                       # skip a frame: position advances
            else:
                out.append(tone_frame(i, rate))   # repeat a frame
        i += 1
    return out[:total]


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]
    seconds = float(argv[1]) if len(argv) > 1 else 20.0
    interval = float(argv[2]) if len(argv) > 2 else 1.7
    seed = int(argv[3]) if len(argv) > 3 else 20260913
    rate = 44100

    kinds = ["clean", "drop", "dup"] * 2
    random.Random(seed).shuffle(kinds)

    print("trim audibility test: %d segments of %.0f s, one trim every %.2f s"
          % (len(kinds), seconds, interval))
    print("  signal    : sustained %s Hz triad, amplitude %.2f"
          % ("/".join("%.0f" % f for f in FREQS), AMPLITUDE))
    print("  play with : aplay -D hw:CARD=<name>,DEV=0 %s" % path)
    print("  NOT through a codec or resampler -- see this file's header.")
    print()

    frames = []
    for n, kind in enumerate(kinds, start=1):
        frames.extend(marker(rate, n))
        frames.extend(segment(rate, seconds, kind, interval))
        frames.extend([0.0] * int(rate * 0.4))

    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        buf = bytearray()
        for v in frames:
            s = struct.pack("<h", int(max(-1.0, min(1.0, v * AMPLITUDE)) * 32767))
            buf += s * 2
        w.writeframes(bytes(buf))

    print("  wrote     : %s  (%.0f s)" % (path, len(frames) / rate))
    print()
    print("  ANSWER KEY -- do not read this to the listener first:")
    for n, kind in enumerate(kinds, start=1):
        print("    segment %d (%d beep%s): %s"
              % (n, n, "" if n == 1 else "s", kind))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
