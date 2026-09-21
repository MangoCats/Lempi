# PI012: The Stutter, Measured

**Appliance Record — the capture log**

Split from [PI011](PI011-two-speakers-and-placement.md) on 2026-09-10. Every
figure is bytes per second at the HCI layer against a baseline from **that
speaker** while it sounded right: there is no absolute threshold `[PI3-FOUND-570]`.

> **Related:** [PI011](PI011-two-speakers-and-placement.md) is the front door and lists the rest.

---

## 8. What the Bluetooth layer says about the two modes

**One measurable difference, and it is a connection
collision.** Both modes were captured with the persistent journal on. Filtered
of endpoint registration noise, the whole of what `bluetoothd` has to say:

```
Mode A (stuttered)                         Mode B (clean)
21.01 a2dp-sink connect failed:            20.87 a2dp-sink connect failed:
      Protocol not available                     Protocol not available
27.42 avdtp_connect_cb: Operation          (nothing)
      already in progress (114)
30.91 sep1/fd0: fd(29) ready               29.39 sep1/fd0: fd(29) ready
(nothing further, ever)                    (nothing further, ever)
```

Counted across each entire boot: **one AVDTP collision in mode A, none in
mode B**; one transport creation and one early profile failure in both.

Two things follow, and the second is the awkward one.

**The collision is real and mode-specific.** In mode A the speaker is
cold-booting, so both ends reach for each other at once and one attempt lands
on another already in flight — the same `Operation already in progress (114)`
signature that an over-eager chase produced in `[PI3-FOUND-140]`, arriving
this time from the speaker rather than from us.

**But it happens once, at 27 s, before audio starts at 32 s — and then
`bluetoothd` says nothing for the remaining three minutes** while the listener
hears stutters at 40, 55, 72, 87, 94, 110 and 119 s. So the collision does not
*cause* each stutter. The most it can be is the moment a link gets negotiated
badly and stays that way, which is a hypothesis this document has not yet
earned the right to state as fact.

**`[PI3-FOUND-370]` The trusted auto-connect fires before the endpoints
exist, in both modes.** `a2dp-sink profile connect failed ... Protocol not
available` at ~21 s is BlueZ acting on `Trusted=true` `[PI3-FOUND-130]` and
reaching for the speaker about two seconds *before* WirePlumber finishes
registering its A2DP endpoints at ~23 s. It is harmless here — something
reconnects later either way — but it means the trust-driven fast path, the one
that exists to avoid the power-up race, currently never succeeds on its first
attempt. Worth fixing on its own terms, and unrelated to the stutter, since
both modes do it identically.

**Baseline for the next comparison**, recorded from the clean mode B link:

| | |
|---|---|
| Configuration | `ay 4 17 21 2 53` — SBC, 48 kHz, joint stereo, 16 blocks, 8 subbands, loudness, bitpool 2–53 |
| State / Volume | `active` / 127 |
| ACL role | **CENTRAL** (`<` outgoing — the Pi initiated) |
| Underruns | 2.60 s |

> **The test that would settle it.** Read those same four values immediately
> after a mode A boot. If the negotiated configuration or the initiating role
> differs, the collision has a lasting consequence and there is something to
> fix. If they are identical, then a link that measures the same in every
> respect sounds different, and the difference is inside the speaker where
> nothing here can reach it.

### `[PI3-FOUND-430]` The stutter has a signature, measured 2026-09-10

A Mode A power cycle stuttered on the usual ~15 s spacing. `lempi-hci-capture`
was started mid-train, at uptime 123.8, and ran 180 s -- so it covers a
stuttering stretch and a clean stretch on the same link in the same boot,
which is the control this investigation never had. Raw log:
`logs/hci-20260910T135004Z.log`.

| region (uptime) | ear | bytes/s | pkt/s |
| --- | --- | --- | --- |
| 124-137 | stuttering | 40994 | 65.6 |
| 144-171 | stuttering | 38302 | 61.0 |
| 180-221 | clean | 46120 | 75.6 |

**The deficit is real.** Over 144-171 the host handed the controller 17% less
audio than during clean playback: 4.4 seconds of audio missing out of 26.

**It ends when the listener says it ends.** Throughput reaches a flat 75-77
pkt/s at 179.3 and never dips again through the remaining 124 s. The listener
reported clear from about 185-190. **This is the first instrument on this
appliance whose reading correlates with the ear** `[PI3-FOUND-420]`, and it is
the reason the five withdrawn theories above were expensive: every one of them
was argued from ear-points alone.

**The 15-second period is in the packet data.** Deepest dips at 129.8/131.9
and 146.0/148.0 -- 16.1 s apart -- with shallower minima at 161-163 and 175.

**What it does not settle.** The shortfall is visible at HCI TX, before the
air, so the speaker is not the thing discarding audio. Whether the host is
starving the encoder or the controller is refusing packets is **not
distinguished here**, and the obvious-looking evidence is worthless: the
completions column runs at exactly 0.500 of TX in both regimes across 98
samples. That precision means the ratio is structural -- completions batched
two packets at a time -- so it says nothing about credit availability. It is
recorded here so that nobody later mistakes it for flow-control health.

**An anomaly, flagged and not used.** From 222.6 the completions column reads
zero for 80 consecutive seconds while TX stays at a healthy 75-77. A host
cannot transmit indefinitely without credits returning, so this is almost
certainly an artifact of the capture. The TX column is not implicated, but the
completions column should not be trusted until a second run either reproduces
this or does not.

Single run. `btmon` was running during it, at `Nice=10`; the stutters preceded
it and continued through it, so it did not cause them, but it cannot be ruled
out of their depth.

### `[PI3-FOUND-440]` What the redial does, in packets, measured 2026-09-10

The second Mode A cycle captured from boot, so it holds the redial itself.
Raw log: `logs/hci-20260910T135726Z.log`.

| region (uptime) | ear | bytes/s | pkt/s |
| --- | --- | --- | --- |
| 30.9-51.1 pre-redial | a couple of stutters | 44635 | 73.5 |
| 52.1-88.3 | silence | 0 | 0 |
| 89.3-199.2 post-redial | smooth | 46057 | 75.5 |

**The redial restores the full rate, and it holds.** 110 seconds after it, not
one dip. Before it, the mean was only 3% down but with local dips to about
36000 B/s at 49-51 -- which is what "a couple of stutters" looks like as
packets. Against `[PI3-FOUND-430]`'s 17% over a full train, severity tracks
the ear in both directions. `[PI3-FOUND-400]` was ear-only until now; it has a
mechanism.

**The redial waits on the wrong signal.** The gap was 36.2 s. `lempi-speaker.sh`
polls for `Connected: yes` with a ten-iteration ceiling, sleeps one second and
posts `reopen-output` -- so the reopen went out roughly 24 s before the
speaker was carrying audio. Audio came back clean anyway, but by a later
tick's routing check `[PI3-AIM-050]` rather than by design. What matters for
audio is the `MediaTransport1` state, not the ACL link, and `lempi-btctl`
already has `transport_state()` for it. **Not changed yet**: it would confound
a capture series in progress.

**Read rates as totals over elapsed time, never off one row.** In steady state
the per-second rows alternate about 68 and 85. That is not a dip. The tick
marker runs at 1.005-1.03 s rather than exactly 1 s, so counts alias across
the boundary while the pair-sum stays at 76/s. Every figure in this section is
total bytes divided by elapsed time.

**The completions anomaly reproduced, and was wrongly dismissed.** It was
called an artifact of a single capture when first seen. It appeared again here
with the same signature -- one partial second (16, against 15 the first time)
then zero for the rest of the run while TX stays at a healthy 75-77. Onsets
were 98.8 s and 135.1 s into their captures, both during settled playback,
neither overlapping a stutter, so nothing above depends on it. It is
repeatable behaviour of the capture or the stack, not noise, and reading the
raw `btmon` output either side of that second is a cheap next experiment.

### `[PI3-FOUND-460]` A full train, captured whole, 2026-09-10

The first Mode A boot recorded from power-on with nothing intervening -- the
redial having been removed the same afternoon `[PI3-FOUND-450]`. Raw log:
`logs/hci-20260910T140551Z.log`. Clean reference 45750 B/s / 74.8 pkt/s, from
the two smooth stretches of the earlier runs.

| region | % of clean | ear |
| --- | --- | --- |
| run 2 post-redial | 100.1 | smooth |
| run 1 post-settle | 99.9 | smooth |
| run 2 pre-redial | 94.7 | a couple of stutters |
| **run 3, no redial, whole window** | **87.1** | continuous, every ~15 s |
| run 1 stutter train | 83.4 | full train |

**Severity tracks the ear across five regions and three boots, both ways.**

**The deficit is present in the first packets and never clears.** Every
20-second block runs 86-94% of clean from audio start to the end of the
window. Over 170 s the appliance handed the controller 12.9% less audio than a
healthy link -- about 22 seconds of audio missing. Nothing arrives partway
through to cause it. **This is evidence against the library-load explanation**
(the second future path below), which predicts onset at playback rather than a
deficit already in place before the first note is audible. Not a refutation --
the load also begins before audio -- but the experiment now has a prediction
to fail.

**The ear and the minima agree event by event.** Fitting the wall-clock offset
against the listener's marks gives 4.0 s, which is firmware plus bootloader on
a Pi Zero 2W, and at that offset **7 of 9 marks fall within 2 s of a measured
throughput minimum**. Earlier runs agreed only on boundaries; this one agrees
stutter by stutter. Mean period 14.7 s. The unmatched marks are +99 and one of
the +136/140 pair; minima at 61, 75 and 179 fall in stretches the listener
summarised as "roughly 15 second intervals" rather than listing.

**The redial is confirmed by its absence.** With it: 45800 B/s flat, zero
dips, 110 s. Without it, same speaker, same mode, same day: 39840 B/s, dips
throughout, no recovery inside 180 s. That is as close to a controlled A/B as
this appliance allows, and it means the removal was a real trade rather than a
no-op.

**The completions anomaly reproduced a third time**, onset 136.0, same
signature -- one partial second then zero while TX stays healthy. Three for
three, always in settled playback, never overlapping a stutter.

**An arithmetic correction to the earlier figures.** Rates were being computed
by dividing n+1 samples' bytes by n intervals, which biases every figure high
-- most visibly a "105.7% of clean" tail block that was pure artifact. All
numbers in these sections now exclude the first sample's bytes. The earlier
findings move by under a percent and none of their conclusions change.

### `[PI3-FOUND-470]` Mode B measured, and the pair is matched

Mode B captured the same afternoon as `[PI3-FOUND-460]`, same speaker, same
code, no redial in either. Raw log: `logs/hci-20260910T141610Z-modeB.log`.

| | Mode A (run 3) | Mode B (run 4) |
| --- | --- | --- |
| whole window | 39840 B/s, 87.1% of clean | 45803 B/s, 100.1% |
| 20 s blocks | 86-94% throughout | 99.4-100.6% throughout |
| dips under 40000 B/s | every ~14.7 s | none |
| audio missing | ~22 s in 170 | none |
| ear | stuttering to +171 at least | smooth |

**The mode decides it, measured rather than heard.** That was the claim of
`[PI3-FOUND-350]`, argued from listening; the separation here is total rather
than marginal.

**The deficit and the audible stutter are one phenomenon.** It was worth
asking whether Mode B might carry dips the listener could not hear -- that
would have meant two things were being conflated. It carries none.

**It is decided at connection and does not change.** Mode B's first block is
already at 100.5%; Mode A's first block is already deficient; neither drifts
across 180 s. Whatever differs is established while the link is being set up
and then held. That is why every "something arrives later and disturbs it"
theory has failed to predict anything, the library load included.

**The completions anomaly is an instrument defect with a signature.** Across
the three captures that recorded from boot, its onset varies by 41 s of wall
clock and by 1.5% of cumulative packets:

    run 2   uptime 155.89   6541 packets transmitted
    run 3   uptime 135.99   6639 packets
    run 4   uptime 114.68   6577 packets

It fires at about 6600 packets, not at a time -- a counter filling or wrapping,
almost certainly in `btmon` or the monitor socket rather than in the
controller. Run 1 looked arbitrary only because its capture began mid-playback
and its count started from an unknown offset. Nothing in these findings rests
on the completions column, and now nothing needs to: what it measures is the
capture, not the link.

**Timing, for the record.** Mode B connected at +29 and audio began at +34,
against +35 and +39 for Mode A -- the speaker being already awake rather than
cold-booting alongside the Pi. The modes differ in more than one variable,
which is why the packet rate is the comparison that counts and the timings are
not.

### `[PI3-FOUND-570]` The Oontz sounds clean at the Middleton's stuttering rate

First cold-boot capture of the second speaker, Mode B, 2026-09-10. Logs:
`logs/hci-20260910T171700Z-oontz-modeB.log`,
`logs/linkstate-20260910T171700Z-oontz-modeB.log`.

| | Middleton | Oontz |
| --- | --- | --- |
| AFH channels | 41-50 | **20 -- the specification's minimum** |
| link quality | 255 (max) throughout | **95**, then 255 |
| SBC configuration | `4 17 21 2 53` | `4 17 21 2 45` |
| AVRCP volume | 106 | **absent** |
| throughput | 45750 clean / ~39900 stuttering | **39502** |
| ear | stutters on Mode A | **no stutters** |

**The absolute byte rate is not the predictor, and very nearly fooled me.**
39502 B/s is a clean Oontz. 39770 and 40199 B/s were stuttering Middletons.
The instrument measures a deficit *against that speaker's own baseline*, and a
figure from one speaker says nothing about another. The Oontz's lower rate is
explained by its codec, not by any fault: bitpool 45 against 53 predicts 84.9%
of the Middleton's rate and 39502/45750 is 86.3%.

**Flatness does not rescue it either.** Block-to-block variation was tried as a
speaker-independent measure and does not separate: run 4, clean, sits at 5.4%
while run 3, stuttering, sits at 6.8%. There is no absolute threshold and no
variance threshold. **Every future reading needs a baseline captured from that
speaker, sounding right.**

**Two more mechanisms fall out.** The Oontz hops on **20 channels**, the
fewest the specification permits, and sounds perfect -- so a restricted hop map
does not cause this, independently confirming `[PI3-FOUND-530]`. And its
controller reports link quality 95 where the Middleton reports a flawless 255
while stuttering, which finishes link quality as a predictor: it reads *worse*
on the speaker that works.

**What this is not.** It is not the Middleton-versus-environment test. Mode A
is defined by the speaker's power cut taking the Pi with it, which happens only
because the Pi runs off the Middleton's USB port. The equivalent for the Oontz
is deliberately cold-booting both together, and that has not been done.

