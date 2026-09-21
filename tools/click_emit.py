#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate the click track for an acoustic drift measurement.

    tools/click_emit.py out.wav [minutes] [rate] [channels] [amplitude]

Play it on the node under test and record it on a node with a microphone, then
read the recording with `tools/click_analyze.py`. What comes back is the
emitter's sample rate minus the recorder's, in ppm -- a *relative* figure, which
is the honest thing an acoustic measurement can produce `[GDE-ECHO-460]`.

Why this works at all: the measurement reads the *spacing* between clicks, not
their absolute arrival. Speaker-to-microphone distance adds the same constant
to every click and cancels in the differencing; a sample-rate error does not
cancel, because it scales the spacing. So the geometry can be sloppy -- put the
speaker near the microphone and do not measure the distance -- while the
quantity of interest survives.

**Match the target's native rate.** The default is 44100 because that is what
`bose` runs and what the library is. If any resampler stands between this file
and the DAC -- PipeWire, `plug:`, a mismatched rate -- it will impose its own
clock and the measurement becomes a reading of the resampler, which is exactly
how `lempipi`'s first figure came out as a meaningless +0.0073 ppm
`[LOG-DRIFT-058]`. Play it with `aplay -D hw:...` on the raw device, or accept
that the number describes the software chain rather than the hardware.

Each click is a short Hann-windowed chirp rather than an impulse. A chirp
survives room reverberation with a sharper correlation peak than a click of the
same energy, and its bandwidth keeps it clear of whatever hum and traffic noise
the room contributes.
"""
import math
import struct
import sys
import wave

# One second between clicks, exactly. The analyser needs no more than a rough
# expectation to find them, but an exact integer spacing means the emitted
# sample index of click n is n * rate with no rounding to argue about later.
INTERVAL_S = 1.0
CHIRP_MS = 12.0
CHIRP_LO = 1000.0
CHIRP_HI = 6000.0
# Default level. **Set it for the distance**: 0.9 was needed to carry across a
# room to a laptop microphone, and the same 0.9 with the speaker beside the
# microphone is painful to be near and clips the recording -- which costs the
# measurement too, because a clipped chirp correlates badly against a clean
# template. Start low, and raise it only if the analyser cannot find a run.
AMPLITUDE = 0.35


def chirp(rate, ms=CHIRP_MS, lo=CHIRP_LO, hi=CHIRP_HI):
    """A Hann-windowed linear sweep -- the same shape the analyser rebuilds."""
    n = max(1, int(round(rate * ms / 1000.0)))
    out = []
    for i in range(n):
        t = i / rate
        dur = n / rate
        # Linear sweep: instantaneous frequency goes lo -> hi across the burst,
        # so phase is the integral of that.
        phase = 2.0 * math.pi * (lo * t + (hi - lo) * t * t / (2.0 * dur))
        window = 0.5 - 0.5 * math.cos(2.0 * math.pi * i / n)
        out.append(math.sin(phase) * window)
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]
    minutes = float(argv[1]) if len(argv) > 1 else 10.0
    rate = int(argv[2]) if len(argv) > 2 else 44100
    channels = int(argv[3]) if len(argv) > 3 else 2
    amplitude = float(argv[4]) if len(argv) > 4 else AMPLITUDE

    burst = chirp(rate)
    gap = int(round(INTERVAL_S * rate))
    clicks = int(round(minutes * 60.0 / INTERVAL_S))
    if len(burst) >= gap:
        print("chirp longer than the interval; nothing to measure")
        return 1

    print("click track: %d clicks, %.0f s, %d Hz, %d ch, %.1f ms chirp %.0f-%.0f Hz, amp %.2f"
          % (clicks, clicks * INTERVAL_S, rate, channels, CHIRP_MS, CHIRP_LO, CHIRP_HI, amplitude))
    print("  spacing    : %d samples (exactly %.3f s at nominal rate)" % (gap, INTERVAL_S))
    print("  play with  : aplay -D hw:CARD=<name>,DEV=0 %s" % path)
    print("  NOT through a resampler -- see this file's header.")

    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        # Build one interval's worth of frames and write it `clicks` times,
        # rather than assembling the whole track in memory: ten minutes of
        # stereo 44.1k is 100 MB of Python floats otherwise.
        period = bytearray()
        for i in range(gap):
            v = burst[i] * amplitude if i < len(burst) else 0.0
            s = struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
            period += s * channels
        period = bytes(period)
        for _ in range(clicks):
            w.writeframes(period)
    print("  wrote      : %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
