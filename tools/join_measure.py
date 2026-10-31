#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure one join the player played against the disc it came from
`[SPEC-AGN-060]`.

    python tools/join_measure.py <render.wav> <disc.wav> --start-ms N --join-ms N [--label TEXT]

`render.wav` is what the engine's join probe wrote (`join_probe`, in
`player/src/engine/mod.rs`): two passages as the output ring received them.
`disc.wav` is the rip's own WAV, the disc as read. `--start-ms` is where on the
disc the first passage nominally begins, `--join-ms` where the two meet
(fractions allowed: a track starts on a sector, 1/75 s, not a millisecond).

Each side of the join is placed on the disc by cross-correlation, so an
offset the decoder adds -- an MP3's encoder delay -- is measured, not assumed.
What is said, in samples and in dB:

- **lag** of each side against its nominal place, and their difference: the
  disc time the join skips (positive) or repeats (negative);
- **residual**, what the render holds that the disc does not, in 2 ms windows:
  its level away from the join (the codec's own noise) and at it;
- **level**, the render against the disc, lowest over the 200 ms around the
  join: a fade to silence and back shows here as a deep dip.
"""

from __future__ import annotations

import argparse
import sys
import wave

import numpy as np

WIN = 88            # 2 ms at 44.1 kHz
NEAR_S = 0.15       # the join's neighbourhood, each side
SEARCH = 4000       # lag searched, each way, in samples


def read(path: str) -> tuple[np.ndarray, int]:
    """Mono float, -1..1, and the rate. Reads 16-bit PCM and 32-bit float."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[20:22] == b"\x03\x00":                       # IEEE float, as the probe writes
        ch = int.from_bytes(raw[22:24], "little")
        rate = int.from_bytes(raw[24:28], "little")
        at = raw.index(b"data") + 8
        x = np.frombuffer(raw[at:at + int.from_bytes(raw[at - 4:at], "little")], dtype="<f4")
    else:
        with wave.open(path) as w:
            ch, rate = w.getnchannels(), w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    return x.reshape(-1, ch).mean(axis=1), rate


def lag_of(seg: np.ndarray, disc: np.ndarray, nominal: int) -> int:
    """Where `seg` sits on `disc`, against `nominal`, by cross-correlation."""
    lo = max(nominal - SEARCH, 0)
    ref = disc[lo:nominal + SEARCH + len(seg)]
    n = 1 << int(np.ceil(np.log2(len(ref) + len(seg))))
    c = np.fft.irfft(np.fft.rfft(ref, n) * np.conj(np.fft.rfft(seg, n)), n)[:len(ref) - len(seg) + 1]
    return int(np.argmax(c)) + lo - nominal


def db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


def rms(x: np.ndarray) -> np.ndarray:
    n = len(x) // WIN
    return np.sqrt(np.mean(x[:n * WIN].reshape(n, WIN) ** 2, axis=1))


def measure(render: np.ndarray, disc: np.ndarray, rate: int, start_ms: float, join_ms: float) -> dict:
    start = round(start_ms * rate / 1000)
    j = round((join_ms - start_ms) * rate / 1000)         # the join, in the render
    near = int(NEAR_S * rate)
    la = lag_of(render[max(j - near - rate, 0):j - near], disc, start + max(j - near - rate, 0))
    lb = lag_of(render[j + near:j + near + rate], disc, start + j + near)
    idx = np.arange(len(render))
    lag = np.where(idx < j, la, lb)
    pos = np.clip(start + idx + lag, 0, len(disc) - 1)
    ref = disc[pos]
    res, lev, refl = rms(render - ref), rms(render), rms(ref)
    w = j // WIN
    nw = near // WIN
    far = np.r_[res[:max(w - 3 * nw, 0)], res[w + 3 * nw:]]
    base = float(np.median(far)) if len(far) else float("nan")
    around = res[max(w - nw, 0):w + nw]
    ratio = lev[max(w - nw // 2 * 2, 0):w + nw] / np.maximum(refl[max(w - nw // 2 * 2, 0):w + nw], 1e-9)
    loud = refl[max(w - nw, 0):w + nw] > 10 ** (-50 / 20)
    return {"lag_a": la, "lag_b": lb, "jump": lb - la,
            "residual_far_db": db(base), "residual_peak_db": db(float(around.max())),
            "disturbed_ms": float(np.sum(around > base * 10 ** (12 / 20)) * WIN * 1000 / rate),
            "level_min_db": db(float(ratio[loud[:len(ratio)]].min())) if loud[:len(ratio)].any() else None,
            "disc_at_join_db": db(float(refl[max(w - 5, 0):w + 5].mean())),
            "render_ms": len(render) * 1000 / rate}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("render")
    ap.add_argument("disc")
    ap.add_argument("--start-ms", type=float, required=True)
    ap.add_argument("--join-ms", type=float, required=True)
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    render, rate = read(a.render)
    disc, disc_rate = read(a.disc)
    if rate != disc_rate:
        print(f"the render is {rate} Hz and the disc {disc_rate} Hz: not compared", file=sys.stderr)
        return 2
    m = measure(render, disc, rate, a.start_ms, a.join_ms)
    lvl = "n/a (quiet)" if m["level_min_db"] is None else f"{m['level_min_db']:+6.1f} dB"
    print(f"{a.label:<34} lag {m['lag_a']:+5d}/{m['lag_b']:+5d}  jump {m['jump']:+5d} smp  "
          f"residual {m['residual_far_db']:6.1f} far, {m['residual_peak_db']:6.1f} peak, "
          f"{m['disturbed_ms']:5.1f} ms disturbed  level min {lvl}  "
          f"(disc {m['disc_at_join_db']:.0f} dBFS at the join)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
