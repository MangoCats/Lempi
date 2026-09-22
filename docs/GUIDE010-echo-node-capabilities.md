# GUIDE010: Echo Playback — What a Node Is, and What Its Audio Stack Will Tell You

**Development Guidance — split from [GUIDE008](GUIDE008-echo-playback-investigation.md) on 2026-09-11, which had reached the 300-line limit `[GOV-DOC-010]`**

Two things GUIDE008 needed and could not hold: the model that decides **whether
a given node can echo at all**, expressed in measured parameters rather than
device classes, and the `cpal`-level findings that say **what any node's audio
stack will honestly report** about itself.

> **Related:** [GUIDE008](GUIDE008-echo-playback-investigation.md) — the investigation this was carved out of · [GUIDE009](GUIDE009-echo-playback-plan.md) — the plan that measures every cell left empty here · [SMART001](../SmartPC/SMART001-survey.md) and [BOSE001](../BosePi/BOSE001-survey.md) — the per-machine surveys the roster draws on

---

## 1. A node is a set of measurements, not a kind of device

**`[GDE-ECHO-400]` Eligibility is a predicate over measured parameters.** An
allowlist of blessed hardware cannot survive its first unfamiliar node, and this
fleet acquired two unfamiliar nodes in a single afternoon. What decides whether
a machine can echo is not what it *is* but what is *known* about it:

| parameter | what it answers |
| :--- | :--- |
| `rate_error_ppm` | how fast it walks away, and in which direction |
| `presentation_offset` | submit-to-sound delay, in ms |
| `offset_stability` | whether that delay stays put |
| `clock_ownership` | self, host-slaved, or remote — *why* it drifts |
| `timestamp_source` | hardware, software, or none `[GDE-ECHO-290]` |
| `native_rates` | whether the library's rate survives untouched |

**`[GDE-ECHO-410]` Every node has a presentation offset, and no node is the
reference.** All participants agree on a common *sound time* and each schedules
backward by its own offset:

```
submit_time(node, sample N) = sound_time(sample N) − presentation_offset(node)
```

A master is the node whose **selection** is followed `[GDE-ECHO-020]`, not the
node whose latency defines the datum. This matters in the case that is otherwise
easy to get wrong: when the *master* is the high-latency node, a low-latency
echo node simply waits. Treating the master's offset as zero would have built a
system that works in one direction only.

**`[GDE-ECHO-420]` Offset magnitude is compensable; offset *variability* is what
disqualifies.** An echo node holds the audio on its own disk and knows the queue
ahead of time, so it can begin early by exactly its own offset — the capability a
streaming receiver categorically lacks, because it does not yet have the audio.
A fixed 250 ms is therefore a scheduling constant, not an obstacle.

This retires the categorical exclusion of Bluetooth. The real question for
`lempi02w` is not *how large* its A2DP latency is but *how often and how far it
moves*, and whether a move can be detected. That is a measurement nobody has
taken, not a property anyone should assume.

**`[GDE-ECHO-430]` The offset has a measured part and a calibrated part, and
pretending otherwise would hide the larger one.** ALSA reports its own delay
`[GDE-ECHO-160]`, which for an I²S DAC is essentially the whole story. For A2DP
the sink's internal buffering is opaque to the host, so a residual remains that
the stack will not report. `presentation_offset` is therefore *measured delay +
calibrated residual*, the residual established once per node and re-established
whenever the link renegotiates. A node that reports only the measured half is
declaring a number it cannot support `[GOV-SRC-030]`. The calibrated half is set
by hand where nothing can measure it, and its default is ranked by what actually
knows the delay -- [SPEC043](spec/SPEC043-node-delay-control.md)
`[SPEC-DLY-040]`.

**`[GDE-ECHO-440]` Clock ownership has three cases, and they drift for unrelated
reasons.** *Self-clocked* — an I²S DAC or an asynchronous USB device — drifts by
its own crystal. *Host-slaved* — an adaptive USB sink `[SMT-AUD-040]` — drifts
by the host controller's frame timing. *Remote* — A2DP — drifts by a clock in
another device entirely. Two nodes in different cases share no common cause, so
no measurement of one predicts the other. This is `[GDE-ECHO-060]` restated as
mechanism rather than caution.

---

## 2. The fleet as it actually stands

**`[GDE-ECHO-450]` Five nodes, five different situations, one measured cell.**
Probed 2026-09-11.

| node | output | clock | offset | ppm | timestamps |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `bose` | HiFiBerry DAC+ Pro, I²S `[PI-BOS-020]` | self | **46.3 ms** (2043 fr) `[LOG-CPAL-060]` | **+14** `[LOG-FIX-030]` | **`Hardware`** |
| `teacherslounge` | Realtek ALC3246 analog | self | **~4080 fr (92 ms)** probe `[GDE-ECHO-458]` | — | **`Hardware`** |
| `smartboardpc` | ATE1133 USB, adaptive `[SMT-AUD-040]` | host-slaved | medium | **+9.96** `[LOG-DRIFT-045]` | ≥1 ms granularity |
| `lempi02w` | A2DP to the Middleton | remote | **355 ms** (15676 fr) `[LOG-CPAL-060]` | **no single rate**: +3.96 / −2.09 vs ADC in two sessions `[LOG-CAL-080]` | **`Hardware`** |
| desktop (`local`) | Windows WASAPI | self | unknown | — | estimate only `[GDE-ECHO-180]` |

**`[GDE-ECHO-458]` `teacherslounge` is eligible, and was never affected by the
HiFiBerry's defect.** Probed 2026-09-13 with the period pinned to the player's
2048: all four delay sources agree and are live, `status.get_delay()` included
-- so `[GDE-ECHO-545]`'s missing STATUS field is particular to the HiFiBerry
driver, not general. cpal reported the delay changing in 7 of 8 callbacks,
which is a `Hardware` verdict under `[GDE-ECHO-290]`.

Two caveats on the offset. It was measured by `delayprobe` writing in a loop,
where the delay sits near the full 4096-frame buffer; the player reports from
*inside* the callback with one period already consumed, which is why `bose`
reads one period (2043) rather than two. So the figure a running player would
publish here is likely nearer 46 ms than 92, and the row should be replaced
when the player actually runs on this node rather than inferred from the shape.

Its **rate** is still unmeasured for playback. `[LOG-CAL-050]`'s campaign
measures the *capture* clock; on an HDA codec the two directions usually share
a master clock, which makes that figure a reasonable prior for playback and
not a measurement of it.

Two numbers in the table are now measured, and they differ by a factor of 23 — one self-clocked, one host-slaved, which is `[GDE-ECHO-440]` arriving as data rather than argument. Three rows remain empty. That is the argument for
[GUIDE009](GUIDE009-echo-playback-plan.md)'s measurement phase existing, stated as a
table rather than as a worry.

**`[GDE-ECHO-470]` An echo node needs the same audio, and three nodes already
have it.** Surveyed 2026-09-11:

| node | library | audio files |
| :--- | :--- | ---: |
| desktop (`local`) | `C:\Users\Mango Cat\Music`, 44 G | 5,743 |
| `teacherslounge` | `/home/sw/Music`, 44 G | 5,743 — **identical to local** |
| `smartboardpc` | `/media/mango/PortableSSD/Media/Music`, 49 G | 5,754 — a variant, reconciled with local 2026-09-12 |
| `bose` | its own, 44 G `[BOSE001]` | — |

`teacherslounge` therefore needs no audio to become a playback node; it needs
chrony (installed 2026-09-11), a library database, and a service. Smart's
library sits on a volume that is **100 % full with 11 G spare**
`[SMT-STO-010]`, which is a provisioning problem of a different kind.

> **Smart was given a library on 2026-09-12, and the reconciliation is the
> lesson.** The local catalogue was snapshotted (`VACUUM INTO`, so a live WAL
> database could be copied consistently), transferred, paired with a fresh
> empty listener half from `schema.sql` → `split_database.py`, and bound with
> `relink` — which matches by **encoded-audio hash**, not by path
> `[SPEC012]`. Smart keeps its own listening history rather than inheriting
> the desktop's.
>
> Comparing the two collections **by path** claimed 877 audio files were
> missing. The hash said **35**. The other ~840 were already on Smart under
> different filenames, and copying them would have written ~1.7 GB of
> duplicates into that 11 G of headroom and left the library pointing at the
> wrong copies. `[GDE-ECHO-480]` said path comparison was unreliable; this is
> the number that says how unreliable. The 35 genuinely-absent files were five
> complete albums, copied into Smart's own root and structure, after which
> `relink --apply` bound **5,709 of 5,709** rows with zero missing.

**`[GDE-ECHO-480]` Comparing two libraries by path is unreliable, and failed
four distinct ways on one afternoon.** Establishing whether local and
`teacherslounge` differed produced three successive wrong answers before the
right one, each wrong for its own reason:

| apparent delta | actual cause |
| ---: | :--- |
| 685 files | the two sides sorted under different collations |
| 49 files | Windows cannot store a **trailing dot** in a directory name (`Garage Inc.`) |
| 21 files | a manual rename (`Born in the U.S.A.` → `Born in the USA`) |
| **0 files** | the truth — verified byte-identical at 5,171,539 bytes on a disputed track |

Comparing local against Smart added two more classes: a **colon** stripped from
a directory name, and an artist-folder convention (`The Beach Boys` against
`Beach Boys, The`) that alone accounted for roughly 150 apparent differences in
each direction. Normalised by album and track, the genuine delta is 36 files
one way and 12 the other.

Acting on any of the intermediate answers would have written hundreds of
duplicate files into differently-spelled directories. **`md5_encoded` is the
identity `[REQ-AUD-100]`, paths are not**, and `[SPEC012]`'s relink exists
precisely because a path is not portable between two machines. Any future
library reconciliation should hash rather than compare names; the figures above
are path-derived and are evidence of a difference, not proof of one.

**`[GDE-ECHO-460]` The fleet already owns an acoustic instrument, and the best
one is the laptop.** `teacherslounge` has a working built-in microphone on its
ALC3246, captures **stereo at up to 192 kHz** — 5.2 µs of sample resolution,
far finer than this needs — and is portable, which the rest of the fleet is
not. Proven 2026-09-12: playing a 1 kHz tone through its own speaker while
recording gave **4500.4** in the 1 kHz bin against 4.1 at 400 Hz and 1.0 at
1600 Hz, so the microphone is live, it hears the room, and the machine can
play and record at once — which is the whole requirement.

Smart's ATE1133 also offers an asynchronous mono capture endpoint at 48 kHz
`[SMT-AUD-050]` and remains a fallback, but a laptop that can be carried into
the room beats a mini-PC that cannot.

**`[GDE-ECHO-465]` Do not put the microphone on `lempi02w`, however tempting.**
It is the node that cannot be measured any other way `[LOG-DRIFT-058]`, so it
is the obvious place to attach one — and it is the worst host in the fleet for
the job. The Pi Zero 2 W's `dwc2` OTG controller has documented trouble with
isochronous transfers: audio arrives "pitched and sped up, as if some samples
are missing", reproducing across every capture tool and working correctly on a
Pi 4 with identical hardware. A dropped sample is the one error a timing
measurement cannot absorb. Measure lempi02w's room *from* the laptop instead.

**`[GDE-ECHO-468]` Keep the correlation differential, and clock accuracy stops
mattering.** Recording speaker A against speaker B in one capture makes a
sample-rate error a pure scale factor — 0.25 % of a 20 ms offset is 50 µs,
irrelevant. Correlating against a locally generated reference over a long
window turns that same 0.25 % into 25 ms of drift in ten seconds and destroys
the measurement. So: chirp bursts, both speakers in one recording, short
windows. This is why no measurement microphone need be bought: calibration and
frequency response are not what this depends on.

**`[GDE-ECHO-469]` Placement will dominate the error budget, not the
equipment.** Sound travels ~34 cm per millisecond, so sub-millisecond accuracy
needs the microphone equidistant from both speakers to within ~10 cm, or the
geometry measured and subtracted. No specification on any microphone helps
with this, and it is a larger term than clock drift, noise floor and frequency
response combined. A microphone on that input can record two speakers at once and
recover their true offset by cross-correlation — a measurement that depends on
no software estimate anywhere in either node's stack, and is therefore the
ground truth `[GOV-SRC-020]` asks for when ranking every other method. It is
also the only instrument here that can see `[GDE-ECHO-430]`'s uncalibrated
residual at all.

---

## 2b. Going independent, when the master goes away

The motivating case is literal: `lempi02w` may be installed in a car and driven
out of range. It must not degrade — it should simply resume choosing for
itself. Three decisions make that free rather than merely possible.

**`[GDE-ECHO-490]` The Program Director stays loaded and inactive. This costs
nothing, because it is already the status quo.** Measured on `lempi02w`
2026-09-12 with the Director resident: **118 MB RSS, 25 % of its 464 MB, 234 MB
still free** — matching `[IMPL-SUI-075]`'s figure exactly, and inside
`[REQ-HW-100]`'s 150 MB process target. A running player has already paid for
it. So an echo node does not *acquire* a warm Director; it merely declines to
discard the one it has, and the 9.86 s `Director::load` a Pi Zero 2W would
otherwise face `[IMPL-SUI-075]` never arises at all.

**`[GDE-ECHO-495]` The master broadcasts its queue, not just the passage in
hand.** `[GDE-ECHO-310]`'s schedule announces one passage on mixer admission,
roughly 15 s of lead — enough to *start* together, not enough to survive a
departure. Sending the queue costs nothing new: `QUEUE_SHOWN`'s 12 entries
already go to every connected browser twice a second, so the data and the
cadence both exist. It also lets an echo node pre-open and pre-decode the way
`[REQ-AUD-160]` already does locally.

**`[GDE-ECHO-500]` The queue depth is the hysteresis, so there is no timeout to
tune.** An earlier draft had the node fall back after "2 anchors" of silence —
about one second at twice-a-second cadence. That is right for a car leaving and
badly wrong for a Wi-Fi hiccup in the house, which would trip a fallback and
diverge a whole passage before rejoining.

Replaced by a rule that needs no threshold: **follow announcements while any
remain, and top the queue up locally rather than letting it drain.** As each
passage completes the next is taken from the queue; if no announcement has
arrived to replace it, the warm Director selects one so the queue stays at
`QUEUE_DEPTH`. The queue never runs dry, so there is no moment of decision and
no cliff — announced entries drain out of the front while locally-chosen ones
fill in behind, and the changeover is a blend rather than an event.

With five passages typically announced, a node can lose contact for roughly
twenty minutes before a single local selection is even needed, and a momentary
dropout is invisible because the next announcement arrives long before the
runway is spent.

**`[GDE-ECHO-510]` Rejoining is the mirror of leaving, and it is not
symmetrical in the obvious way.** When contact returns, the master's announced
queue **takes precedence immediately** and locally-chosen entries still waiting
are discarded — they were only ever filling a gap.

But the passage *in progress* is not terminated. It plays to its natural end,
or until Skip `[REQ-AUD-162]`, and only then does the node take up the master's
programme. Cutting a passage short to rejoin would make reconnection audible
for no benefit, and the whole point of the local library is that the node was
never playing anything wrong — only something different.

By then the master is part-way through a passage of its own, so the node joins
**mid-passage, at a computed offset**, rather than starting that passage over.
This is the capability `[GDE-ECHO-330]` had deferred, and the rejoin case
promotes it from optional to required. It is also cheaper than that deferral
assumed: it is `[REQ-AUD-162]`'s skip — cut the ring, fade, overlay the
incoming passage — with the incoming passage opened at a position instead of at
zero, which `resume_at` and `seek_to` already do `[REQ-AUD-140]`. Both halves
exist; only their combination is new.

So the transition is a handover, not a restart: no rebuild, no gap, no silence
`[REQ-AUD-142]`. And because `[GDE-ECHO-030]` logs plays locally throughout the
mirrored period, the Director's own eligibility and frequency inputs
`[SPEC-FREQ-010]` are current at the moment it takes over — it is not resuming
from stale state, it is resuming from its own honest record of what that room
has heard.

> **What the audio stack will report about any of this** — cpal's timestamps,
> its silent fallbacks, and which of its numbers survived contact with a real
> appliance — is its own subject: see
> [GUIDE013](GUIDE013-audio-stack-reporting.md).
