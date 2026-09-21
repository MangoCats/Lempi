# PI011: Two Speakers, and Where the Stutters Were

**Appliance Record — the front door to the stutter investigation**

This document was 1,645 lines on 2026-09-10, against `[GOV-DOC-010]`'s
300-line limit. It had become a day's findings appended under one heading,
in the order they happened, with the truth and the withdrawn theories
interleaved. 
It is now the **front door**. What is settled is below; everything else is
split by genre, so a reader can tell at a glance whether they are reading a
measurement, a policy, or something that turned out to be false.

| Document | What is in it | Trust it? |
| --- | --- | --- |
| **PI011** (here) | What is settled, and what is still owed | Yes |
| [PI012](PI012-the-stutter-measured.md) | The capture log: every measurement and what it settled | Yes, with its baselines |
| [PI013](PI013-theories-withdrawn.md) | Placement, link quality, link direction, substitution | **No -- all withdrawn** |
| [PI014](PI014-two-speakers-and-the-choice.md) | The one-speaker policy, and the bugs that shaped it | Yes |
| [PI015](PI015-the-bluetooth-agent.md) | The agent, and three tries to reach it | Yes |
| [PI016](PI016-the-redial.md) | The redial: it worked, and was removed | Yes |
| [PI017](PI017-the-hop-map.md) | The hop map: a hypothesis, tested and killed | **No -- withdrawn** |

> **Related:** [PI004](PI004-speaker-operation.md) for operating the link ·
> [PI009](PI009-the-silence-of-2026-09-08.md) and
> [PI010](PI010-startup-time-and-stutter.md) for what came before

---

## 0. The short version

**The stutter is the Middleton's, and it is not this appliance's to fix.** A
completeness map closed on 2026-09-10: Mode A stutters at 87% of clean, three
times out of three, while Mode B and *both* Oontz modes sit at 100%. One cell
of four fails, and a second speaker cold-booted in the same room at the same
hour is flawless `[PI3-FOUND-580]`.

**Seven explanations were withdrawn getting there.** Placement, link quality,
buffer sizing, retry counts, link direction, the hop map, and the speaker's
retained channel state. Each was coherent and each was wrong; they are in
[PI013](PI013-theories-withdrawn.md) and [PI017](PI017-the-hop-map.md).

**There is now an instrument that sees it.** `lempi-hci-capture` reads the
deficit at the HCI layer, and on a full train seven of the listener's nine
ear-marks fell within two seconds of a measured minimum `[PI3-FOUND-460]`.
Before it, every hypothesis cost a three-minute listening test, which is why
so many of them were wrong.

**What to do about it, practically:** use the Oontz, or leave the Middleton
powered when restarting the Pi. The redial also worked, 4 of 4, and was
removed because half a minute of silence on every boot is worse than the
fault `[PI3-FOUND-450]`.

## 7. The power-cycle mode decides it, and nothing on the Pi explains why

**`[PI3-FOUND-350]` Two modes, measured back to back, 2026-09-09.** The
listener noticed that switching the speaker off — which cuts the Pi's supply,
since it is powered from the speaker's USB `[PI3-FOUND-090]` — correlated with
stuttering restarts, where power-cycling the Pi alone did not. Run as a
controlled pair with the appliance eighteen inches clear in both:

- **Mode A**, speaker switched off and on: connect at 35 s, then stutters at
  40, 55, 72, 87, 94, 110 and 119 s, continuing past 180 s before settling.
- **Mode B**, speaker left powered, Pi alone cycled: reconnect at 33 s, **no
  stuttering at all through 120 s+**.

The observation is real. **The explanation is not on this machine.** Sampled
unattended over an identical 35–120 s window:

| | Mode A (stuttered) | Mode B (clean) |
|---|---|---|
| Wi-Fi signal | **-58.6 dBm** | **-71.9 dBm** |
| Load average | 0.75 | **1.33** |
| Disk read | 1837 kB/s | **2333 kB/s** |
| Player CPU | 15.7 jiffies/s | 14.1 jiffies/s |

Every one of them points the wrong way. The clean boot had **thirteen
decibels worse signal**, higher load and more I/O than the boot that
stuttered.

**Signal level is therefore withdrawn `[PI3-FOUND-340]`.** It looked like the
predictor across four boots and is not one; it was the second metric offered
here to fail, after retry counts. Both failed the same way — a number that
correlated across a handful of boots, presented as though it explained
something, and falsified by the first experiment designed to test it rather
than to confirm it.

What is left is the variable the Pi cannot see: in mode A **the speaker is
cold-booting too**, its own radio and audio stack initialising, while in mode
B it is settled and only the link is re-made. Nothing in `/proc`, `pw-top` or
the player's counters observes that.

> **Next step, and what it needs.** The closest available proxy is
> `bluetoothd`'s own view — transport state changes, AVDTP negotiation, and
> whether anything else attaches to the speaker while it boots. Capturing that
> across both modes needs the persistent journal turned back on
> ([PI020](PI020-diagnostic-tools.md)), because the volatile default destroyed mode A's log the moment
> mode B booted. Turn it on, repeat the pair, and compare the Bluetooth logs
> rather than the Pi's own health.

### `[PI3-FOUND-580]` The completeness map: it is the Middleton

The listener closed the grid on 2026-09-10 by cold-booting the Oontz and the
Pi together -- the Mode A condition, reproduced deliberately for a speaker that
does not power the appliance. Logs: `logs/hci-20260910T172457Z-oontz-modeA.log`,
`logs/linkstate-20260910T172457Z-oontz-modeA.log`.

| speaker | mode | % of that speaker's own baseline | ear |
| --- | --- | --- | --- |
| Middleton | A | 87.1, 87.9, 86.9 | **stutters, 3 of 3** |
| Middleton | B | 100.1 | clean |
| Oontz | B | 100 (the baseline) | clean |
| **Oontz** | **A** | **100.2** | **clean** |

Every Oontz Mode A block sits between 98.4% and 101.1%. One cell of four
fails, and it fails every time.

**What this exonerates.** The room, and the interference in it: a second
speaker cold-booting in the same place at the same hour is flawless. The
cold-boot-together condition itself, which is identical for both. And this
appliance's own behaviour, which runs the same code path either way. Every
Pi-side explanation is now excluded by a control rather than by argument.

**What it implicates.** The Middleton, specifically, in how it comes up from
cold. `[PI3-FOUND-350]` said the power-cycle mode decides it, which was true
and incomplete: the mode decides it *for this speaker*.

**And the hop map dies a third time, most clearly here.** The Oontz cold-booted
to 54 channels and settled to 47 with scattered upper-band exclusions --
`000080bcdddefffbff3f`, the pattern that had looked like the fault signature --
and sounded perfect throughout. Scatter on a clean link is the cleanest
possible refutation `[PI3-FOUND-530]`. Its tight 20-channel map from the Mode B
run was an adapted state, not a property of the speaker.

**Controller health metrics now point the wrong way, consistently.** The Oontz
reported link quality 85-92 and RSSI -6 through clean audio; the Middleton
reports 255, the maximum, while stuttering.

**What this changes about the goal.** The stutter is a defect in one speaker's
cold-start behaviour, not something this appliance is doing wrong and not
something it can repair. The remaining choices are practical rather than
diagnostic: use the Oontz, use the Middleton in Mode B, or reinstate an
intervention like the redial `[PI3-FOUND-450]` that re-establishes the link
after the speaker has been awake a while -- which, read in this light, is
exactly what it was doing.

### `[PI3-FOUND-590]` Both speakers powered: three results in one sequence

The listener ran a mixed sequence on 2026-09-10 with both speakers in play.
Three separate things came out of it.

**1. The Middleton disturbs a link it is not part of.** At 5:32:01 the
Middleton was powered on while the Oontz was connected and playing. The Oontz
then stuttered -- small ones at 5:32:32, 5:32:49 and 5:33:13, a bigger one at
5:33:45 -- and was smooth again by 5:34:45. The Middleton was not connected to
anything during this; it was simply coming up.

That is a Middleton cold start degrading a **different speaker's** link in the
same room, and it corroborates `[PI3-FOUND-580]` from an angle the completeness
map could not reach: the speaker is not merely fragile on its own link, it is
an active disturbance in the band while it starts. It also fits the shape of
the Mode A fault, where the Middleton's cold start overlaps the appliance's.

**2. The fallback works, exercised for real.** The Oontz was powered down at
5:34:45. `speaker_address` still named the Oontz, and the Middleton was
untrusted -- so nothing but `[PI3-FOUND-560]` could have done it. Audio was
playing smoothly from the Middleton by 5:35:25, about forty seconds later,
with no intervention. First real exercise of the path that could not be tested
when it was written, because it refuses to run while anything is audible.

**3. Two connected audio devices, and silence from both.** At 5:35:45 the
Oontz was powered back on. Within seconds the audio stopped, both speakers
showed connected, and raising every volume did nothing. The diagnosis took one
command:

    < eSCO 08:EB:ED:26:14:12 handle 6 state 1 lm CENTRAL
         48. output_MONO  > OontZ_Angle 3 U412:playback_MONO  [active]

An eSCO link and a **MONO** port: the Oontz had been taken as an HSP/HFP
headset rather than an A2DP sink, which is `[PI3-FOUND-290]` returning under a
condition it was not found in. Zero `ACL Data TX` in five seconds confirmed
nothing was reaching the air at all.

**The recovery was a guess, and it is recorded as one.** `wpctl set-profile 53
0` was issued without checking what index 0 meant; it means **off**. It
restored audio only because PipeWire then fell back to the Middleton. It left
the Oontz holding an ACL link with a dead profile, which produced the next
finding, and a considered fix would have disconnected the device instead.

**And that dead profile exposed a real defect.** With the Oontz holding a link
but offering no sink, the keeper's routing check saw a mismatch it could never
resolve and demanded a reopen **every thirty seconds, indefinitely**, against a
device with nowhere to send audio -- while the stream was already playing
happily on the other speaker:

    17:39:34  08:EB:ED:26:14:12 is connected but the stream was on 'MIDDLETON' ... asked the player to reopen
    17:40:08  (same)
    17:40:41  (same)

`Connected: yes` means BlueZ has a link. It does not mean PipeWire has anywhere
to send audio. A reopen that cannot succeed is not a harmless no-op -- it is an
instruction to abandon working audio. The check now requires the target sink to
actually exist, and says so plainly when it does not. Verified under the
service's own user: `MIDDLETON` present, `OontZ_Angle 3 U412` absent, a
nonsense name absent.

**A smaller one fixed alongside.** The interloper marker used
`${XDG_RUNTIME_DIR:-/tmp}`, but that variable is *set* under a root run and
names `/run/user/0`, which does not exist -- so the fallback never triggered
and every write failed. It now tests the directory rather than the variable.

## 11. Where this ended, and what is still owed

**`[PI3-FOUND-400]` The redial works, three times out of three, and the cure
is uncomfortable.** *(Superseded: a fourth trial agreed, `[PI3-FOUND-440]`
measured what it did, and `[PI3-FOUND-450]` removed it for exactly the
discomfort this section names.)* On every mode A power cycle since it was made
unconditional, the boot comes up stuttering, the keeper redials at around 50 s,
and the audio is smooth afterwards. But the listener's summary is the honest
measure of it:

> *"the audio is smooth after a redial-reconnect, but that takes up to 95
> seconds post power to achieve and involves significant distracting
> start/stop/restart audio in the process."*

So it is a mitigation, not a fix. Audio arrives at ~37 s, stutters, is cut off
for several seconds around 50 s, returns, and only settles by ~95 s. Polling
BlueZ instead of sleeping on a fixed clock cut the interruption from about
eleven seconds to eight; the rest is inherent to tearing a link down and
rebuilding it.

**`[PI3-FOUND-410]` Two earlier findings are confounded by the power-cycle
mode and are withdrawn as causes.** Neither was tested against it, because it
was not known to be a variable at the time:

- **Placement `[PI3-FOUND-320]`.** The clean runs that established it were
  mode B — the listener's own note records the speaker staying powered, since
  it only announced the disconnect when the Pi came back. Mode B is clean at
  any placement. Sitting on the speaker does measurably cost 11–16 dB of
  signal, which is worth knowing, but signal level was itself refuted as a
  predictor of the stutter `[PI3-FOUND-390]`.
- **The graph quantum `[PI3-FOUND-330]`.** Its evidence was "removed it, the
  stuttering returned" — on boots that were mode A and would have stuttered
  regardless. The quantum is left at 2048 because a wider margin is defensible
  on hardware this small, not because it is known to do anything.

**`[PI3-FOUND-420]` There is no instrument for this symptom.** *(True when
written; closed on 2026-09-10 by `lempi-hci-capture`, which does track it
`[PI3-FOUND-430]`. The table below remains accurate about everything above the
HCI layer, and is why the search had to go below it.)* Every detector tried
sits either before the loss or measures something that does not track it:

| Instrument | Verdict |
|---|---|
| `lempi-underruns` | Does not correlate — 11.58 s sounded perfect, 1.64 s stuttered |
| `pw-top` xruns | Always zero, clean and stuttering alike |
| Sink-monitor capture | Measures audio *before* SBC encode and transmit; always continuous |
| Signal level, retry counts | Both tried, both refuted |
| `debugfs` `hci0` | Configuration knobs only; no throughput or error counters |
| PipeWire `wchar` | Reads zero while playing; not a proxy |

**The listener's ear was the only detector**, which is why every hypothesis
here cost a three-minute listening test, why the data points are few and
noisy, and why five explanations were published and withdrawn. The advice that
closed this section -- budget for that, or build an instrument first -- was
taken: the instrument is `lempi-hci-capture` `[PI3-FOUND-430]`. The ear is
still what says *when* a stutter happened; the capture is what says how much
audio went missing while it did.

### Two paths worth taking, neither of them a fix

**The HCI layer.** `btmon` sees actual ACL data packets and the controller's
own flow control — around 75 packets/s during healthy playback. It is the one
layer between the SBC encoder and the air that has not been observed during a
stuttering boot, and the only remaining candidate for a real instrument. It is
privileged and heavy enough to perturb what it measures, which has already
happened twice here `[PI3-FOUND-170]`, `[PI3-FOUND-240]`, so it wants a
bounded startup capture rather than a permanent watcher.

That capture is now built: `lempi-hci-capture`, installed and disabled, one
fixed window, filtered to two line types before a byte is stored, the raw
stream kept in tmpfs, and aggregated only after the window closes. See PI010
section 3. It has now been run during a stuttering boot and it worked: see
`[PI3-FOUND-430]`. The path is no longer speculative, and the next reading
owed is a second run, to settle the completions anomaly and to catch a train
from boot rather than from the middle. What it decides is which side of the controller the loss sits on:
a dip or a gap in the per-second count means the packets are not getting out,
and a rate that stays flat through a stutter the listener can hear means the
loss is past the controller and inside the speaker. Either answer is progress.
This investigation has never had one.

**Suspending the library load entirely.** The Director rebuild reads ~256 MB
at ~15 MB/s while using half a core `[PI3-FOUND-250]`, overlapping exactly the
window in which the stuttering happens. Making it *fully* wait — not merely
yield, which `bfq` and per-thread idle priority already arrange — would say
whether the startup work contributes at all. It is not a shipping design: an
appliance that will not choose its next passage until minutes after boot has
traded one fault for another. But as an experiment it cleanly separates "the
link is bad" from "the machine is too busy to feed it", which nothing so far
has managed.

