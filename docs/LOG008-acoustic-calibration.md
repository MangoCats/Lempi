# LOG008: Hearing `bose` Through `teacherslounge`

**Experiment Record — 2026-09-13**

Ten minutes of `bose`'s DAC recorded by `teacherslounge`'s microphone, to put a
second and independent instrument on the drift figures and to calibrate the
laptop's ADC — the reference `lempi02w`'s only measurement was taken against
`[LOG-DRIFT-062]`.

> **Related:** [LOG007](LOG007-drift-instrument-correction.md) `[LOG-FIX-030]` — the electrical figure this is compared with · [LOG006](LOG006-echo-drift-measurement.md) `[LOG-DRIFT-062]` — `lempi02w`'s run against the same microphone · [GUIDE014](GUIDE014-echo-phase-status.md) `[GDE-ECHO-540]` — the phase this serves

---

## 1. What was run

`lempi` stopped on `bose` (the `hw:` device is exclusive, and a resampler
anywhere in the chain would make this a measurement of the resampler);
`tools/click_emit.py`'s track played with `aplay` straight to
`hw:CARD=sndrpihifiberry,DEV=0` at the library's own 44100; `arecord` on
`teacherslounge` at `hw:0,0`, 48000, likewise raw. 600 clicks, one per second,
each a 12 ms Hann-windowed chirp from 1 to 6 kHz.

**`[LOG-CAL-010]` The first attempt heard nothing usable, and the instrument
said so instead of producing a number.** Two verification runs before the real
one: the first found 14 detections and no credible run at all, the second 5.
`bose` was already at 0 dB on both its mixers, so the only lever was click
energy — the chirp went from 4 ms at 0.5 amplitude to 12 ms at 0.9, about
+10 dB. That this failed loudly rather than quietly is the whole design
`[GOV-SRC-020]`: a fitted line through 14 spurious detections would have looked
like a measurement.

## 2. Result

| | |
| :--- | ---: |
| detections | 558 of ~615 |
| longest credible run | **426 consecutive, 425 s** |
| discarded outside the run | 132 |
| fitted spacing | 0.999988616 s |
| residual | **6.5 µs rms**, worst 13.9 µs |
| **`bose` − `teacherslounge` ADC** | **+11.384 ppm ± 0.003** |

**`[LOG-CAL-020]` On clean data the span estimator agrees, which is how you can
tell the data is clean.** It reads +11.389 against the fit's +11.384. On the
29 s verification run it read +6.733 against +10.209 — a 3.5 ppm disagreement
over a short window with a marginal signal. The two estimators converging is
evidence; either one alone is not `[LOG-DRIFT-064]`.

## 3. What this gives directly

**`[LOG-CAL-030]` `bose` ↔ `lempi02w` is +13.47 ppm ± 0.48, and the microphone
cancels out of it.** *Superseded by `[LOG-CAL-080]` the same day: the
subtraction is right, the ± is not, because `lempi02w` has no single rate to
subtract. Read the pair as 7–14 ppm.* Both nodes were measured against the *same* ADC, so
subtracting the two readings removes it entirely:

    bose − ADC        = +11.384 ± 0.003   (this run)
    lempi02w − ADC     =  −2.089 ± 0.477   `[LOG-DRIFT-062]`
    bose − lempi02w    = **+13.47 ± 0.48**

This is the assumption-free number, and it is the one echo actually needs: a
node pair's relative rate. It supersedes `[GDE-ECHO-550]`'s estimate of ~16 ppm
for this pair, which was arithmetic on two absolutes rather than a measurement
of the difference. One trimmed frame every **1.7 s**, not 1.4.

## 4. What it gives only with an assumption

Combined with the electrical figure for `bose` `[LOG-FIX-030]`:

    teacherslounge ADC = 14 − 11.384  ≈ **+2.6 ppm**  (± 0.7, all of it bose's)
    lempi02w, absolute  = −2.089 + 2.6 ≈ **+0.5 ppm**  (± 0.9)

**`[LOG-CAL-040]` This does not by itself settle whether `bose` is +14 or
+0.43, and should not be reported as though it does.** An acoustic run compares
two clocks and has no third to appeal to. It is consistent with +14 if the
laptop's ADC is +2.6 ppm, which is an unremarkable figure for a consumer
codec — but it is equally consistent with the discredited +0.43 if that ADC is
−11 ppm, which is also within what a consumer crystal is quoted at. The
electrical argument in `[LOG-FIX-010]` is what rules the old figure out; this
run neither adds to nor subtracts from it.

What would settle it is in `[LOG-CAL-050]` below.

## 4a. `lempi02w` heard the same way, 2026-09-13

Twelve minutes with the Middleton beside the microphone, played through the
same PipeWire → A2DP path the music takes.

**`[LOG-CAL-070]` `lempi02w` − ADC = +3.959 ppm ± 0.029** over the settled
window (243–394 s), residual 15.7 µs rms. That is sixteen times tighter than
`[LOG-DRIFT-062]`'s ±0.477 from 130 seconds — and it does not agree with it.
The earlier figure was **−2.089**; these are six ppm apart and both error bars
are far too small to cover it.

**`[LOG-CAL-080]` The explanation is that `lempi02w` does not have *a* rate.**
Three observations from this one run, each measured rather than inferred:

| | |
| :--- | :--- |
| first 30 s of playback | **+1586 ppm** |
| across a 90 s window, by thirds | +194 → +131 → **+73 ppm** |
| within the settled 151 s, by sixths | +2.3 … +6.0 ppm |

PipeWire's adaptive resampler chases the Bluetooth sink's clock, takes
**minutes** to converge after playback starts, and then continues to wander by
a couple of ppm. Where it settles is a property of the session, not of the
hardware — which is why two honest measurements of the same node disagree by
six ppm. Neither is wrong; the quantity is not constant.

So `bose` ↔ `lempi02w` is **+7.4 ppm this session and +13.5 the last**, and
`[LOG-CAL-030]`'s single figure should be read as a range of roughly 7–14 ppm
rather than a measurement. `bose`'s own ±0.003 is not the limit on that pair
and never was.

For the design this sharpens `[GDE-ECHO-420]` rather than answering it: a
Bluetooth node cannot be given a rate constant, needs the continuous trim of
Phase 5 unconditionally, and needs **minutes of settling** before its rate
means anything — which bears directly on the rejoin case `[GDE-ECHO-510]`,
where a node returning to the fleet would otherwise be trimmed against a figure
taken during its own convergence.

**`[LOG-CAL-090]` `lempi-speaker.timer` will interrupt an acoustic run on
`lempi02w`.** It fires every 30 s to keep the speaker attached, and from 480 s
into this run it began moving the PipeWire stream out from under `aplay` —
detections fell from 119 per 120 s to zero. It runs whether or not `lempi` is
running, so a future run should stop the timer, not just the player.

Level matters too, in both directions: 0.9 amplitude beside a microphone is
painful and clips the capture, while 0.04 detected 90 of 90 at close range.
`click_emit.py` takes amplitude as an argument for this reason.

## 5. Open

**`[LOG-CAL-050]` Measure `teacherslounge`'s ADC electrically and every figure
here becomes absolute.** It is an ordinary Linux box with chrony and a hardware
capture PCM, so the two-read method `[LOG-DRIFT-015]` applies unchanged —
`hw_ptr` on `pcm0c` against `/proc/uptime`, through `tools/drift_analyze.py`.
That yields the ADC against NTP-disciplined time, and then this run's +11.384
gives `bose` acoustically, independently of the frame clock and of `/proc` on
`bose`. Two instruments sharing no code and no machine, which is the standard
`[GDE-ECHO-280]` was written to hold things to.

**Answered 2026-09-13** -- `arecord` to `/dev/null` with
`tools/drift_sample.sh` appending every 5 minutes, run for **9.67 continuous
hours, 117 samples**.

**`[LOG-CAL-100]` `teacherslounge`'s ADC is +0.9 ppm.** Least squares
+0.999 +/- 0.139, endpoint +0.761, residual 15.2 ms rms -- which is the read
skew between `/proc/uptime` and the status file, not instability. Hourly
windows scatter +/-8 ppm and are useless individually, exactly as
`[LOG-FIX-070]` predicted; the long window is the measurement.

**`[LOG-CAL-110]` This settles `[LOG-CAL-040]`, which could not settle
itself.** That entry said an acoustic run alone cannot choose between `bose` at
+14 and the discredited +0.43, because both are consistent with *some* ADC. The
ADC is now measured rather than assumed:

| | |
| :--- | ---: |
| `bose` - ADC, acoustically | +11.384 +/- 0.003 |
| ADC, electrically | **+0.9 +/- 0.2** |
| **`bose` absolute, by the acoustic route** | **+12.3 +/- 0.2 ppm** |
| `bose` absolute, electrically | +13.7 (range 12.97-14.33) |
| what +0.432 would have required of the ADC | **-10.95 ppm** |

Two instruments sharing no code and no machine -- one counting frames against
`/proc`, one counting clicks through the air -- agree on about +13 ppm and
exclude +0.43 by some sixty standard errors. `[LOG-FIX-010]`'s correction is
independently confirmed.

The 1.4 ppm between the two routes is not a disagreement to resolve. `bose`'s
own hourly scatter is sd 2.87 ppm `[LOG-FIX-030]` and the acoustic figure comes
from a single 425 s window inside that. Both routes say "about +13", and
neither can say more than that about a clock which moves a few ppm from hour to
hour.

**`[LOG-CAL-120]` `lempi02w` absolute, for both sessions: -1.2 ppm and
+4.9 ppm.** The spread is the finding, not the mean `[LOG-CAL-080]`.

**`[LOG-CAL-060]` 132 detections fell outside the clean run.** The fit is
sound — 426 consecutive at 6.5 µs — but a fifth of the track was disturbed,
and the cause was not investigated. Room noise during the run is the obvious
candidate. It matters only if a future run needs the full window.
