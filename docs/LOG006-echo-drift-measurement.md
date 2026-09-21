# LOG006: Echo Playback — the Phase 1 Drift Campaign

**Experiment Record — opened and first two nodes read, 2026-09-12**

`[GUIDE009]`'s Phase 1 `[GDE-ECHO-270]`: measure each candidate node's clock
rate against the shared timebase, now that Phase 0's chrony is settled
`[GDE-ECHO-305]`. This file holds the **t₀ baselines**, so the campaign
survives the session that started it — a reading whose starting point lives
only in someone's scrollback is not a measurement.

> **Related:** [GUIDE009](GUIDE009-echo-playback-plan.md) — the phase this serves · [GUIDE010](GUIDE010-echo-node-capabilities.md) `[GDE-ECHO-450]` — the roster being filled in · [BOSE004](../BosePi/BOSE004-operating-health.md) `[BOS-OPS-020]` — the instrument · [LOG007](LOG007-drift-instrument-correction.md) `[LOG-FIX-030]` — **corrects every `bose` figure below**

---

## 1. Two methods, because the nodes report differently

**`[LOG-DRIFT-010]` A node running Lempi can be read from one sample; a node
under `aplay` cannot.** Lempi opens ALSA through cpal, which calls
`set_tstamp_mode(true)` `[GDE-ECHO-150]`, so `pcm0p/sub0/status` carries a
populated `tstamp` and `trigger_time` — the window is self-contained in a
single read. `aplay` does not enable timestamping: its `tstamp` reads
`0.000000000`, confirmed on `smartboardpc` 2026-09-12.

So:

| node kind | method |
| :--- | :--- |
| running Lempi (`bose`) | ~~one read: `(hw_ptr / rate) − (tstamp − trigger_time)`~~ **invalid, see `[LOG-FIX-010]`** |
| `aplay` probe (`smartboardpc`) | two reads: `(Δhw_ptr / rate) − Δuptime` |

**`[LOG-DRIFT-015]` The delta method is not merely a fallback — for a probe it
is the correct one.** `aplay` prefills its buffer before the stream triggers,
so `hw_ptr` counts frames not yet heard and the trigger-anchored form reads
absurdly high on a short window (+1530 ppm at 18 s on Smart, which is prefill,
not drift). Differencing two reads cancels the prefill exactly.

That reasoning turned out to understate the problem. The one-read form is not
merely prefill-contaminated on short windows, it is **circular at every
window**: `tstamp` is derived from `hw_ptr`, so both sides are the same
counter. The two-read form is the only valid one for any node. See
`[LOG-FIX-010]` and `[LOG-FIX-020]`; every `bose` number in this file predates
that discovery.

**`[LOG-DRIFT-020]` What voids a reading.** Any stream restart, device reopen,
underrun or reboot resets `trigger_time`/`hw_ptr`. `bose` rebooted four times
on 2026-09-11, so its window starts after the last of them. Check `state:
RUNNING` and an unchanged `trigger_time` before trusting a second read.

---

## 2. t₀ baselines

**`[LOG-DRIFT-030]` `bose` — Lempi's own stream, card resolved by name
`[BOS-PWR-050]`.**

```
2026-09-12T00:17:01Z   card 1 (sndrpihifiberry)   state: RUNNING   owner: lempi
trigger_time: 5072.382283402
tstamp      : 7472.805062731
hw_ptr      : 105858716        rate 44100
temp        : 60.3'C           chrony residual: +0.016 ppm
```

Sanity estimate over the 40 minutes elapsed so far: **+0.675 ppm** — an
artefact, per `[LOG-FIX-020]`; the true figure is ≈+14 ppm.
(quantisation 0.0094 ppm, so not the limit). Recorded as a plausibility check,
**not as the measurement** — it is roughly double `[BOS-OPS-020]`'s 0.35 ppm,
which is itself superseded because that figure was taken against a
`systemd-timesyncd` baseline `[GDE-ECHO-050]` and `bose` moved to chrony on
2026-09-11. The ruler changed; the DAC did not.

**`[LOG-DRIFT-040]` `smartboardpc` — a silent ALSA-direct probe, deliberately
not PipeWire.**

```
2026-09-12T00:19:37Z   card 1 (ATE1133)   state: RUNNING
uptime_before: 9463.50
hw_ptr       : 879744          rate 48000
uptime_after : 9463.51         (0.01 s read gap)
chrony residual: -0.001 ppm
```

Started with `aplay -D hw:ATE1133,0 -f S16_LE -r 48000 -c 2 /dev/zero`, which
is digital silence — the speaker stays quiet. **To stop it:**
`pkill -f "aplay -D hw:ATE1133"`.

Card **1** on purpose. PipeWire idles a stream on card 0 (the Burr-Brown),
which is not the device feeding the speaker `[SMT-AUD-020]`, and PipeWire does
its own adaptive resampling — so `hw_ptr` under it reflects PipeWire's
correction as much as the hardware. An indicative read of card 0 under
PipeWire gave **+17.79 ppm**; it is recorded here only so that a later, clean
figure can be compared against it, and it should not be quoted as Smart's
drift.

---

## 2a. Results, 2026-09-12

**`[LOG-DRIFT-045]` Both windows completed undisturbed, through the storm.**

| node | window | drift | instrument floor |
| :--- | ---: | ---: | ---: |
| `bose` — HiFiBerry DAC+ Pro, I²S, self-clocked | 21.01 h | ~~+0.432 ppm~~ **+14** `[LOG-FIX-030]` | 0.0003 ppm (quantisation) |
| `smartboardpc` — ATE1133 USB, `ADAPTIVE`, host-slaved | 20.30 h | **+9.96 ppm** | ±0.096 ppm (read bracket) |
| **relative** | | ~~9.53~~ **~4 ppm** | |

`bose`'s `trigger_time` was unchanged across the window, so the stream never
restarted `[LOG-DRIFT-020]`. Smart's probe stayed alive for 22.9 h of uptime.

`bose` at +0.432 ppm sat close to `[BOS-OPS-020]`'s +0.35 ppm, which read as
reassuring — the ruler had changed from `systemd-timesyncd` to chrony and the
answer barely moved. **That agreement was the warning sign, not the
confirmation**: both figures came from the same circular formula, so neither
depended on the ruler at all `[LOG-FIX-020]`.

Smart's clean figure is **9.96 ppm, not the 17.79 ppm** the PipeWire card-0
read suggested `[LOG-DRIFT-040]`. Measuring the right card, ALSA-direct, was
worth doing: the indicative number was wrong by 80 %.

**`[LOG-DRIFT-048]` One hour would have been enough to decide.** The storm
prompted the question of how long a window must be. Smart's read-bracket floor
is ±1.94 ppm at one hour — ample to establish a ~10 ppm signal, which is the
only question that changes the design. The extra nineteen hours bought
precision (±0.096 ppm), not the decision.

---

## 3. What the readings settle

**`[LOG-DRIFT-050]` Per-passage resync does NOT suffice for this pair, and
`[GDE-ECHO-110]` is superseded — and, per `[LOG-FIX-040]`, for every pair,
not only those including Smart.** At the corrected ~4 ppm relative:

| | drift |
| :--- | ---: |
| across a 4-minute passage | **~1.0 ms** |
| across an hour | **~15 ms** |

~1.0 ms sits inside `[GDE-ECHO-050]`'s **1–5 ms comb-filtering band**, so a
listener hearing both would hear the colouration deepen across every passage
and snap back at each boundary — the changing artefact that is more noticeable
than a constant offset.

The favourable conclusion in `[GDE-ECHO-110]` was explicitly conditional on
both nodes being sub-ppm. **Neither is** — `bose` is ≈+14, Smart +9.96
`[LOG-FIX-030]`. The continuous trim of Phase 5 `[GDE-ECHO-340]` is therefore
the general requirement, not a concession to one bad node, and no node may be
adopted as a rate reference on the strength of its clock ownership alone.

The two nodes are not measuring the same physical thing, which is the point of
`[GDE-ECHO-440]`: `bose`'s I²S DAC is self-clocked, so its number is a crystal
— and a crystal is free to be inaccurate, which this one is `[LOG-FIX-040]`.
Smart's USB endpoints are both `ADAPTIVE`, so the device follows the host and
its number is the Intel USB controller's frame clock against disciplined time.
Two different causes, neither predicting the other.

---

## 3a. Phase 2, first result: the delay half does not work on this platform

**`[LOG-DRIFT-055]` `bose` reports `ts=Software`, and that is the detector
earning its place rather than a disappointment.** The frame clock shipped
2026-09-12; across **17,954 callbacks** it saw `delay=0` every time, while
`/proc/asound` on the same machine reported a real, varying `delay: 4092`.

So `[GDE-ECHO-160]`'s plan — take `playback − callback` from cpal, the one part
of its timestamp that is domain-independent — **does not yield a usable delay
here**, even though ALSA plainly has one. The information exists; cpal is not
passing it through.

Two consequences, and they pull in opposite directions:

- **The rate half is fine.** 13,209,552 frames in 300 s is 44,032/s against a
  nominal 44,100 — the counter tracks, and it is the only instrument `lempipi`
  can have `[LOG-DRIFT-070]`.
- **The offset half must come from somewhere else.** Presentation offset
  `[GDE-ECHO-430]` has to be read from `/proc/asound`'s `delay`, or from ALSA
  directly, not from cpal. That is a correction to the design, not a bug in it.

Had `[GDE-ECHO-290]`'s three-state detection not been built, `delay=0` would
have been recorded as a measurement and an offset of zero would have been
designed against. A constant is not a measurement, and the only reason anyone
knows that here is that the code was made to say so `[GOV-SRC-030]`.

`lempipi` was given the same build the same day and is logging; its verdict is
the more interesting one, since a `Software` result there would mean no
software instrument reaches it at all.

**`[LOG-DRIFT-058]` `lempipi` cannot be measured by software at all, and the
number that proves it looks like a success.** Given the same build 2026-09-12,
it reported **+0.0073 ppm over 4.08 h** — 107 µs of divergence in four hours,
a clock sixty times better than `bose`'s HiFiBerry DAC+ Pro, reached over
Bluetooth. That is not credible as a measurement of a Bluetooth speaker.

What it is measuring is the system clock against itself. PipeWire paces
Lempi's callback from a system timer, resamples downstream, and absorbs the
sink's real rate on the far side — so Lempi is feeding PipeWire, not the
speaker, and the counter advances at exactly the rate the clock it is being
compared against says it should. Zero by construction.

With `ts=Software` beside it (`delay=0` across 322,991 callbacks), every
software path into that node is now closed:

| instrument | result on `lempipi` |
| :--- | :--- |
| `/proc/asound` `hw_ptr` | no hardware PCM — only `vc4hdmi`, closed |
| cpal timestamps | `Software`; no delay information at all |
| the frame clock | PipeWire's timer, not the sink |

**A competing explanation is possible and does not change the conclusion.**
PipeWire's buffer is bounded, so one could argue Lempi's average consumption
must equal the sink's rate over hours, making +0.0073 ppm real. Adaptive
resampling exists precisely to decouple those two sides, so this is unlikely —
but it is unproven either way, and a measurement that cannot distinguish "the
clock is excellent" from "I am measuring nothing" is not a measurement
`[GOV-SRC-020]`.

## 3b. The acoustic method works, and lempipi is not zero after all

**`[LOG-DRIFT-062]` Measured acoustically 2026-09-12: `lempipi` runs
−2.089 ppm ±0.477 against `teacherslounge`'s ADC.** Opportunistic — the
Middleton happened to be placed beside the laptop — and it reaches the quantity
`[LOG-DRIFT-058]` had just concluded no instrument could.

Method: `lempipi` played a click track through the Middleton (one impulse per
44,100 samples, mixed with the music by PipeWire so nothing was interrupted)
while `teacherslounge` recorded at 48 kHz. The **spacing** between clicks is
what is read, not their arrival time, so the speaker-to-microphone distance
cancels entirely and `[GDE-ECHO-469]`'s placement problem does not apply to a
rate measurement.

```
clean run       : 130 intervals
fitted interval : 47999.8997 samples (expected 48000)
per-click jitter: 2.1 samples = 44 us
```

**So the software figure was an artefact, confirmed rather than suspected.**
+0.0073 ppm was PipeWire's timer measured against the clock it is derived
from; the real path drifts, at more than four standard errors from zero. The
competing explanation `[LOG-DRIFT-058]` recorded — that PipeWire's bounded
buffer forces the average to match — is now disfavoured by measurement.

It also lands usefully between the others, though not where this file first
placed it: below Smart's +9.96 ppm and below `bose`'s corrected ≈+14, so a
`bose`↔`lempipi` pair drifts **~16 ppm** relative, about 3.9 ms across a
four-minute passage — the worst pair measured, and well inside the comb band
rather than under it `[LOG-FIX-030]`. The
A2DP *offset* stability remains unmeasured and is the separate question
`[GDE-ECHO-420]` that decides whether Bluetooth can echo at all.

**`[LOG-DRIFT-064]` The first analysis of this same recording said +12,574 ppm,
and the data was never the problem.** Computing the rate from the first and
last detected click let three spurious detections — music transients passing
the threshold — set both endpoints. The tell was in the same output: a median
gap of *exactly* 48000.0 samples, which is a perfect result sitting beside an
absurd one. The correct estimator is a least-squares fit over the longest run
of consecutive clean gaps; endpoints are the one thing a span-based figure
cannot afford to get wrong. Recorded because the wrong number was plausible
enough to have been believed.

**`[LOG-DRIFT-066]` 44 µs of jitter sets the duration for any future run.**
A2DP's codec smears each transient, but smears every one the same way, so the
systematic part cancels and only jitter costs precision.

| span | precision |
| :--- | ---: |
| 130 s (this run) | ±0.48 ppm |
| 5 min | ±0.21 ppm |
| **10 min** | **±0.10 ppm** |
| 30 min | ±0.03 ppm |

Ten minutes buys a figure comparable to the `/proc`-based ones. The number
above is relative to `teacherslounge`'s own converter, whose rate is unknown;
recording `bose` the same way cancels it, since `bose`'s true rate is already
known `[LOG-DRIFT-045]` — one session, two unknowns, both resolved.

---

## 4. Open

**`[LOG-DRIFT-060]` Re-read over a longer window, and at a different room
temperature.** Both figures come from a single ~21 h window at one ambient.
`[GDE-ECHO-060]` warns that a crystal moves with temperature, and `bose` was at
60.3 °C at t₀ — a second window in different conditions is what would turn
these into a range rather than a point. A sampler now appends to
`/var/lempi/drift-samples.tsv` on `bose` every 5 minutes, on C so it survives a
reboot, which makes any later pair of rows a fresh window at no further
effort.

**`[LOG-DRIFT-070]` Three nodes still have no plan.** `lempipi`'s A2DP path
has no `hw_ptr` to read at all — its output is not a hardware PCM
`[GDE-ECHO-070]` — and `teacherslounge` and the desktop are unstarted.
Presentation offset `[GDE-ECHO-430]` is unmeasured everywhere, and the
acoustic cross-correlation `[GDE-ECHO-460]` that would give ground truth
independent of every software estimate has not been attempted.
