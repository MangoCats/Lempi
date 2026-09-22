# LOG009: The cpal Upgrade, and the Buffer It Shrank

**Experiment Record — 2026-09-13**

cpal 0.15.3 → 0.18.2, to obtain the presentation offset `[GDE-ECHO-545]` found
unreadable through the old version. It worked, and it broke `lempi02w` on the
way, for a reason that was nobody's mistake.

> **Related:** [GUIDE014](GUIDE014-echo-phase-status.md) `[GDE-ECHO-545]` — the blocker this collects the fix for · [GUIDE009](GUIDE009-echo-playback-plan.md) `[GDE-ECHO-290]` — the eligibility rule that was firing on a broken input · [LOG007](LOG007-drift-instrument-correction.md) `[LOG-FIX-030]` — the rate figures that constrain the sample rate

---

## 1. Why, and the order it was done in

The HiFiBerry driver never fills the STATUS ioctl field cpal 0.15.3 reads, so
the delay term was a constant zero on every node and Phase 1's offset column
could not be filled anywhere. Upstream had already replaced that call with
`snd_pcm_avail_delay()`, which `delayprobe` measured returning a live value on
that very driver, 861 of 861.

**`[LOG-CPAL-010]` The sample rate was pinned first, as its own change against
the old version.** cpal 0.17 changed the default config to prefer 48 kHz, and
`[GDE-ECHO-050]`'s DAC+ Pro carries separate 44.1 and 48 kHz oscillators — so
accepting the new default would have moved `bose` onto a crystal nobody has
measured and resampled every passage to get there, silently. Landing that
first meant the rate was not a variable when the version moved.

## 2. What the upgrade guide does not say

**`[LOG-CPAL-020]` Four breaking changes were undocumented or mis-documented,
and one of them is not an API error at all.**

| change | how it presents |
| :--- | :--- |
| `alsa` must move in lockstep, `^0.9` → `^0.11` | **a dependency resolution failure**, not a compile error |
| `DeviceTrait::name()` → `description()` + `Display` | method not found, no trait hint |
| `SampleRate` is a type alias for `u32`, not a newtype | every `.0` breaks |
| `StreamTrait::start()` | **does not exist in 0.18.2** |

The first is the one that costs time. `alsa-sys` carries `links = "alsa"`, and
cargo permits exactly one package in a graph to claim a given `links` value, so
a stale pin fails resolution with a message about native libraries rather than
about versions. The reason now sits in `Cargo.toml` beside the pin.

The last is a documentation fault upstream: `UPGRADING.md` describes `play()`
as renamed to `start()`, which is an unreleased v0.19 change. Following the
guide literally does not compile against the version it is filed under.

## 3. The regression

**`[LOG-CPAL-030]` `lempi02w` played badly: distorted, gaps, stuttery bursts,
895 underruns in eight minutes, and it never settled.** Rolled back it was
silent-clean. The cause is in cpal 0.18's own source comment:

> For BufferSize::Default, constrain to device's configured period with
> 2-period buffering. PipeWire-ALSA picks a good period size but pairs it with
> many periods (huge buffer). We need to … constrain properly.

0.18 deliberately clamps the buffer to two periods where 0.15 left PipeWire's
large one alone. On `lempi02w` that came out as:

| | cpal 0.15.3 | cpal 0.18.2 |
| :--- | ---: | ---: |
| frames per callback | 2049 | **512** |
| buffer | PipeWire's own, large | **1024 frames — 23 ms** |
| underruns | ~0 | 895 in 8 min |

23 ms of slack on an A2DP path carrying **321 ms** of latency, fed by a Pi
Zero 2W. Upstream's decision is right for a wired low-latency device and
precisely wrong here — and the same change read as an *improvement* on
`smartboardpc`, where callbacks rose tenfold with no ill effect whatever.
**One node is not a fleet**, which is the entire argument for the test order.

**`[LOG-CPAL-040]` The period is now asked for, not accepted.** Same reasoning
as the sample rate: on the weakest node a default is a decision. In this
backend `BufferSize::Fixed(x)` means period = x and buffer = 2x, and cpal
validates x against the device and refuses cleanly when it is out of range —
so `attach` tries pinned and falls back to the device's own choice, reporting
the refusal rather than absorbing it. A node that will not open at all is a
worse failure than one that underruns, and `bose` is an appliance.

2048 frames is 46 ms per callback and a 93 ms buffer: what 0.15 negotiated on
that node for months without complaint.

## 3a. The second regression, which was worse

**`[GDE-ECHO-547]` `bose` came up with no audio at all: "unsupported format
I32".** cpal 0.18 enumerates more sample formats than 0.15 did, and
`default_output_config()` now returns the HiFiBerry's **native I32** where the
old version offered something the player already handled. The callback has arms
for F32, I16 and U16; anything else hits a catch-all that refuses to open the
device.

Everything downstream of that behaved exactly as designed, and it was still
seven minutes of silence:

- the catch-all refused rather than guessing, and named the format
- the player stayed up and said `no audio device (...); running without output`
  rather than exiting `[REQ-VIS-140]`
- `install-player.sh` had already persisted the durable copy, so the *rollback*
  had to restore both layers -- the live one to fix it now and
  `/media/root-ro` to survive a reboot `[IMPL-BOS-185]`

**A loud failure on an appliance is still a silent room.** The settle curve
showed `0 underruns, 0 recoveries` for five minutes and that was true: a stream
that never opened cannot underrun. Both readings are consistent with perfect
health and with total silence, and nothing in the check distinguished them.
What did was asking whether the PCM was open at all.

The fix is two-sided. `pick_config` now chooses only formats the callback can
write, in a fixed preference order rather than whichever the device names
first, so the choice is stable across cpal versions that enumerate differently.
And an I32 arm is added, since it is this DAC's own format and converting to it
asks the driver to do less. The ring is f32 either way, whose 24-bit mantissa
is the real resolution limit.

## 4. Result

| node | before | after |
| :--- | :--- | :--- |
| `lempi02w` A2DP | `delay=0`, `Software` | **`delay≈15676` (355 ms), `Hardware`** |
| `bose` I²S | `delay=0`, `Software` | **`delay=2043` (46.3 ms), `Hardware`** |
| `smartboardpc` | n/a (no player) | probe: all sources agree |
| desktop, Windows | 48 kHz only | opens the preferred 44100 |

`lempi02w` settled to **0 underruns and 0 recoveries for five consecutive
minutes** at 2048 frames per callback, and is left on the new build.

`bose` took the format fix and is on the new build, durable copy persisted and
`/media/root-ro` read-only. Six minutes at `pcm=RUNNING` with `hw_ptr`
advancing 13.36 M frames, 0 underruns after the startup transient, 2048 frames
per callback.

**`[LOG-CPAL-060]` Both halves of `[GDE-ECHO-400]`'s model are now measured on
both nodes, which is what this upgrade was for.**

| node | presentation offset | rate |
| :--- | ---: | ---: |
| `bose` — I²S | **2043 frames, 46.3 ms** | ≈+14 ppm |
| `lempi02w` — A2DP | **15676 frames, 355 ms** | −2.09 vs the ADC |
| **difference** | **13633 frames, 309 ms** | **+13.47 relative** |

That 309 ms is the quantity `[GDE-ECHO-410]` is built around: `lempi02w` must
submit 309 ms *earlier* than `bose` for the two to be heard together. Against
the forward schedule's ~15 s of lead `[GDE-ECHO-310]` that is a margin of
roughly fifty to one, so the compensation the design turns on is not close to
its limit -- which could only be asserted before today, and is measured now.

`bose`'s offset being almost exactly one period (2043 against a pinned 2048) is
the expected shape for a buffer of two periods, and it varies, which is why
`[GDE-ECHO-290]` now says `Hardware` rather than `Software`.

The node the design was least sure could ever echo now reports a presentation
offset. `[GDE-ECHO-070]` deferred Bluetooth pending exactly this measurement.

**`[LOG-CPAL-070]` The upgrade did not move the rate it was measured with.**
Worth checking rather than assuming: the period changed from 1472 frames to a
pinned 2048, and an instrument that quietly shifts its own measurement is the
fault this project has already been bitten by twice `[LOG-FIX-010]`.

| `bose` stream | window | ppm |
| :--- | ---: | ---: |
| 0.15.3 | 3,302 s | +12.974 |
| 0.15.3 | 58,532 s | +13.965 |
| **0.18.2, pinned period** | 3,601 s | **+13.216** |

The new figure sits inside the old range. Same crystal, same answer.

## 5. How it was nearly missed

**`[LOG-CPAL-050]` The fault was called a regression, then not a regression,
then a regression, and the listener settled it before the logs did.** Both
wrong turns came from counting journal lines without anchoring the windows to
service-restart times: a freshly restarted A2DP stream throws a burst of
underruns for a minute or two whatever binary is running, so comparing a
restarted stream against a settled one is not a comparison. The old binary
settled to zero within two minutes; the new one never settled. Only the second
fact distinguishes them, and it takes several minutes to observe.

A 42-second stream was also attributed to the rolled-back binary when it
belonged to the new one, which briefly killed the correct hypothesis.

The practice this argues for is in the numbers above: **sample a settle curve,
minute by minute, rather than take one reading** — and on an A2DP node treat
anything within two minutes of a restart as measuring the reconnect.
