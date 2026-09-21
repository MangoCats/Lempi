# LOG007: The Drift Instrument Was Measuring Itself

**Experiment Record — correction to LOG006, 2026-09-13**

The Phase 2 gate `[GDE-ECHO-280]` compared Lempi's in-process frame clock
against `/proc/asound`'s `hw_ptr` and found them 13.19 ppm apart, far outside
the 1 ppm it demands. The gate was read as condemning the frame clock. It was
not: **the frame clock is correct, and the reference it was measured against
is circular.** `bose`'s published drift figure is wrong by a factor of 33.

> **Related:** [LOG006](LOG006-echo-drift-measurement.md) `[LOG-DRIFT-045]` — the campaign whose `bose` figure this supersedes · [GUIDE009](GUIDE009-echo-playback-plan.md) `[GDE-ECHO-280]` — the gate that caught it · [BOSE004](../BosePi/BOSE004-operating-health.md) `[BOS-OPS-020]` — the earlier figure, wrong the same way

---

## 1. `tstamp` is not a clock

**`[LOG-FIX-010]` `/proc/asound/.../status`'s `tstamp` is derived from
`hw_ptr`, so comparing the two compares the sample counter against itself.**
ALSA's default audio timestamp is the frame count converted to time at the
*nominal* rate. It therefore cannot express the difference between nominal and
actual rate — which is the entire quantity being measured.

Measured on `bose`, 191 samples over 15.9 h (2026-09-12 21:52Z → 2026-09-13
13:43Z), one stream, no restart:

| regression | slope | max residual |
| :--- | ---: | ---: |
| `tstamp` vs `hw_ptr / 44100` | 0.999999628 (−0.372 ppm) | **3.4 ms** |
| `tstamp` vs `uptime` | 1.000013584 (+13.584 ppm) | 158 ms |

A genuine clock read, sampled whenever `hw_ptr` updates, would scatter against
the frame count by milliseconds of scheduling jitter *at random* and hold slope
1.000000 against `uptime`. `tstamp` does the opposite: it tracks the frame
count to 3.4 ms across sixteen hours while sliding steadily away from the
system clock — `tstamp − uptime` grows monotonically from +1.101 s to +1.894 s.
It is locked to the counter, not to time.

`uptime` is the trustworthy side here. It agrees with the chrony-disciplined
wall clock to **0.113 ppm** over the same window; both are NTP-disciplined
(Linux slews `CLOCK_MONOTONIC` as well as `CLOCK_REALTIME`), and chrony's own
residual ran ±0.05 ppm throughout.

## 2. What the circular formula actually reports

The one-read form of `[LOG-DRIFT-010]` — `(hw_ptr / rate) − (tstamp −
trigger_time)` — was used for every `bose` figure ever published. Run against
the two-read form on **identical rows**:

| window | one-read (LOG006's `bose` method) | two-read (LOG006's probe method) |
| ---: | ---: | ---: |
| 2.67 h | +1.195 ppm | +14.490 ppm |
| 5.34 h | +0.789 ppm | +13.713 ppm |
| 8.00 h | +0.649 ppm | +13.956 ppm |
| 10.67 h | +0.562 ppm | +13.525 ppm |
| 13.34 h | +0.523 ppm | +13.781 ppm |
| 15.93 h | **+0.450 ppm** | **+14.136 ppm** |

**`[LOG-FIX-020]` The one-read figure decays toward zero as the window grows;
a rate does not.** It is a fixed frame offset divided by an increasing elapsed
time — it measures prefill, and asymptotes to nothing. The two-read figure is
flat across the same rows, which is what an actual rate looks like.

`tools/drift_analyze.py` now carries the valid method, refuses `tstamp`, and
drops each stream's prefill window; it is in the repository precisely because
the original sampler was not `[LOG-FIX-070]`.

This is why the bad number was so convincing. It was stable, it had a tiny
quantisation floor, and two separate windows agreed closely (+0.432 and
+0.675 ppm) — agreement guaranteed by construction, since both were the same
tautology. `[LOG-DRIFT-015]` came within one step of this, catching the prefill
contamination at 18 s on Smart (+1530 ppm) but reading it as a short-window
artefact rather than as the whole method failing.

## 3. Corrected figures

**`[LOG-FIX-030]` `bose` runs about +14 ppm fast, not +0.432 ppm.**
*Independently confirmed 2026-09-13 by a route sharing no code and no machine
with this one: `[LOG-CAL-110]` puts it at +12.3 ± 0.2 acoustically, and the
discredited +0.432 would have required `teacherslounge`'s ADC to be −10.95 ppm
where it measures +0.9.* Four estimates here, two instruments, two separate
streams:

| estimate | window | ppm |
| :--- | ---: | ---: |
| `hw_ptr` vs `uptime`, stream 2 | 56,430 s | +14.33 |
| `hw_ptr` vs `uptime`, stream 1 (earlier, independent) | 3,302 s | +12.97 |
| frame clock, from 900 s after trigger | 56,401 s | +13.73 |
| frame clock, hours 3–16 | 50,101 s | +14.20 |

**Take it as +14 ppm ±0.7.** The spread is window choice and read noise, not
disagreement about the answer; the hourly scatter (frame clock sd 2.2 ppm)
averages out over a long window. Quoting three decimals off any single window
would overstate what one ambient temperature and two streams can support
`[LOG-DRIFT-060]`.

| node | was | is | method |
| :--- | ---: | ---: | :--- |
| `bose` — HiFiBerry DAC+ Pro, I²S, self-clocked | +0.432 ppm | **+14 ppm** | two-read |
| `smartboardpc` — ATE1133 USB, host-slaved | +9.96 ppm | +9.96 ppm (unchanged) | two-read |
| **relative** | 9.53 ppm | **~4.2 ppm** | |

Smart's figure was always measured the right way and stands.

**`[LOG-FIX-040]` Self-clocked does not mean accurate, and the taxonomy should
stop implying it.** `[GDE-ECHO-440]` ranks clock ownership — self-clocked,
host-slaved, remote — and the design leaned on `bose` as the clean reference
because its I²S DAC is self-clocked. Self-clocked describes what a device
*ignores*, not how good its crystal is. `bose` is the worst drifter measured
so far, and +14 ppm is unremarkable for a consumer crystal specified at ±50.

The design consequence points the same way as before but for a firmer reason.
`[LOG-DRIFT-050]` concluded that continuous trim was needed because Smart was
the outlier while `bose` was sub-ppm. **No node measured so far is sub-ppm**,
so the continuous trim of Phase 5 `[GDE-ECHO-340]` is the general case rather
than a concession to one bad node, and no node should be adopted as an
unverified rate reference. Relative drift for the measured pair *falls* from
9.53 to ~4 ppm — ~1.0 ms across a 4-minute passage, still inside
the 1–5 ms comb band of `[GDE-ECHO-050]`, so the conclusion itself survives.

## 4. The frame clock is sound

**`[LOG-FIX-050]` Interpolated onto the `/proc` sample times, the frame clock
and `hw_ptr` agree to +0.33 ppm over 15.8 h** — net +836 frames out of
2,515,091,500. The gate passes. Excursions reach ±0.9 s but are zero-mean and
non-cumulative, which is interpolation error across 300 s brackets, not
counting error.

The partial-write hypothesis that motivated this dig is disproved at the
source: cpal 0.15.3 sizes the callback buffer to exactly
`avail_frames × channels` and, on a short `writei`, calls `error_callback`
rather than dropping frames silently. Counting `out.len() / channels` cannot
over-count.

One transient is real and must be excluded from any rate fit: the **first
300 s window of a stream runs −169.9 ppm**, a one-time 29,103-frame (0.66 s)
deficit as the stream fills. Every interval after it sits within 0.02 s. Fit
from the second window onward.

## 5. Open

**`[LOG-FIX-080]` The temperature question cannot be answered by waiting, and
250 samples now show why.** `[LOG-DRIFT-060]` asked for a second window at a
different ambient, on the reasoning that a crystal moves with temperature. The
sampler has since logged 16.5 continuous hours, and the slope over 2-hour
windows is **−0.82 ± 2.77 ppm per °C** — |slope|/se of 0.30, which is no
measurement at all.

The reason is not noise but leverage: `bose` sits in a case at a near-constant
58–61 °C, so the *window means* spread over only **0.82 °C**. A campaign that
waits will accumulate precision about a temperature that never changes.
Answering it needs the ambient deliberately moved, which is an experiment
rather than an observation, and it stays open as one.

**`[LOG-FIX-060]` `delay` reads 0 on every callback, while `/proc` reports
3540.** *Resolved 2026-09-13 by the cpal upgrade; see
[LOG009](LOG009-cpal-upgrade.md).* `tick_clock` takes `playback.duration_since(&callback)`, which is
cpal's `status.get_delay()` clamped at zero; the hw subdevice simultaneously
reports `delay: 3540` frames (80 ms). Whatever the player opens is not
reporting the delay the hardware knows about. This is why Phase 1's
presentation-offset column `[GDE-ECHO-430]` is empty on every node — the
instrument for it returns a constant zero, which `[GDE-ECHO-290]`'s verdict
correctly labels `Software` without explaining why. Next thread.

**`[LOG-FIX-070]` The sampler lives only on `bose` and was never in the
repo.** That is how a circular formula ran for weeks unreviewed. It also pairs
`hw_ptr` with a separately-read `uptime`, and the read skew shows: its hourly
scatter is 17.6 ppm against the frame clock's 2.9 ppm over the same hours.
Usable for long windows, not for hourly ones.
