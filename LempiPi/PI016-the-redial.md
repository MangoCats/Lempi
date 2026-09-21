# PI016: The Redial, and the Silence It Did Not Cause

**Appliance Record — a mitigation that worked and was removed anyway**

Split from [PI011](PI011-two-speakers-and-placement.md) on 2026-09-10, which had reached 1,645 lines against `[GOV-DOC-010]`'s 300-line limit by accumulating a day of dated findings under a single heading.

The redial is the only intervention in this whole investigation that
demonstrably changed the symptom -- and it was taken out. Both halves are
here, with the startup work that preceded it and the attributable silence
that followed.

> **Related:** [PI011](PI011-two-speakers-and-placement.md) is the front door and lists the rest.

---

## 1. Politeness that was not enforced, and a deadline cut on stale reasoning

**`[PI3-FOUND-250]` `IOSchedulingClass=idle` did nothing, because the
scheduler in use has no idea what it means.** The Director rebuild was made
idle-class so it would yield the card to the decoder `[PI3-FOUND-220]`. It
did not yield. Sampled at one-second resolution with nobody connected, its
block-device reads run:

```
mono   read_kb        lempi_jiffies
35.73   14516          28
44.16  136468         502
54.29  270824         997
```

**256 MB pulled off the SD card in about eighteen seconds — roughly 15 MB/s
sustained — while using half a core**, straight through the start of
playback, by a thread that was nominally at idle priority.

The cause is the elevator: `/sys/block/mmcblk0/queue/scheduler` was
`mq-deadline`, which implements deadlines and nothing else. **I/O priority
classes are implemented by BFQ and by essentially nothing else in a modern
kernel**, so `ioprio_set(IDLE)` on the rebuild thread was a request nobody
was listening to. Switching the card to `bfq` is what makes an I/O-priority
class mean anything at all here; without it, every such change in this
document is decoration.

Set through `tmpfiles.d` alongside the 512 KB readahead, so it survives a
reboot on an appliance that gets power-cut rather than shut down.

**What `bfq` did not do is fix the stutters, and the measurement says so.**
On the boot after the switch the rebuild still read ~255 MB at ~15 MB/s,
essentially unchanged — correctly, because BFQ's idle class defers only to
*competing* I/O, and the decoder was reading from the page cache rather than
the card, so there was nothing to defer to. The stutters had another cause
entirely `[PI3-FOUND-320]`. The change is kept because the reasoning holds —
an idle-class request that no scheduler honours is a bug whatever else is
true — but it is a correctness fix, not the remedy for anything audible.

**`[PI3-FOUND-260]` The boot gate's deadline was cut on reasoning that had
already expired.** `[PI3-FOUND-150]` shortened it from 45 s to 15 s because
the player took twenty seconds to start and the wait could hide inside that.
Then `[PI3-FOUND-210]` established that the twenty seconds were never the
player's own work — they were contention — and once `mpd` was made to yield,
the same work measured 120 ms. The premise was gone; the change outlived it.

What that cost, measured on the next boot: the speaker took 63 s to answer,
having been taken by another device `[PI3-FOUND-090]`; the gate gave up at
15 s; the player opened onto a dummy and played into it for twenty-seven
seconds until `lempi-speaker` reconnected and asked for a reopen. That boot
recorded **26.45 s of underrun, against 2.79 s** on a boot where the speaker
was present before the player started.

Restored to 60 s, with 45 s of first refusal for the chosen speaker. It exits
the instant that sink appears — 0.14 s on a healthy boot — so the higher
number costs nothing when things are well, and only stops the player
committing to a dummy when they are not. Starting early buys nothing here and
costs a reopen; starting late costs only silence that was silent anyway.

## 9. The fix: redial the link outbound, once

**`[PI3-FOUND-380]` A link the speaker opened is worse than one we opened, and
it measures identically.** Reading the mode A link immediately after a boot,
against the mode B baseline of section 8:

| | Mode B (clean) | Mode A (stuttering) |
|---|---|---|
| Configuration | `ay 4 17 21 2 53` | `ay 4 17 21 2 53` |
| State / Codec / Volume | active / SBC / 127 | active / SBC / 127 |
| **ACL direction** | **`<` outgoing** | **`>` incoming** |

Every negotiated parameter is byte-identical. The one thing that differs is
who opened the link: in mode A the cold-booting speaker reaches out to its
last device; in mode B this appliance reaches out to a speaker already awake.

**Tested as a same-boot intervention, which is as controlled as this gets.**
On a boot stuttering on schedule, the link was disconnected and reconnected
outbound — nothing else touched. The direction flipped `>` to `<`, the
configuration did not change, **and the stuttering stopped**: none heard, and
the underrun counter flat across the following sixty seconds.

So `lempi-speaker` redialled once per boot when it found an inbound link,
leaving an outbound one alone. **That redial has since been removed entirely**
`[PI3-FOUND-450]`: the gap it cost turned out to be half a minute rather than
a few seconds, and it was paid on every boot. The paragraphs below are the
record of what it did while it was there.

> **What this is not.** Why an inbound link should sound worse is *not*
> established. The one visible correlate is the AVDTP collision of
> `[PI3-FOUND-360]`, which an outbound redial resolves by making the
> negotiation single-sided — but that is a plausible story, not a measurement,
> and two plausible stories have already been withdrawn from this document.
> What is established is narrower and worth stating exactly: **redialling
> outbound stops it, once, reproducibly on the boot it was tried.** One trial.

### `[PI3-FOUND-450]` The redial is removed, on the listener's judgement

It worked. Four Mode A cycles came up smooth once it had fired, and
`[PI3-FOUND-440]` measured what it did: a flat 46057 B/s for 110 s with no
dips. It is the only intervention in this investigation that demonstrably
changed the symptom.

It was removed anyway, on 2026-09-10, because the cure is more disruptive than
the disease. It tears down working audio and rebuilds it, and the hole is not
small -- 36.2 s on the captured boot, against the eleven seconds the first
version was tuned down from -- and it is paid on every boot, including the
Mode B boots that never stuttered at all. A stutter every fifteen seconds is
irritating. Half a minute of silence in the middle of a track, every time, is
worse. That is a judgement about how the appliance should feel to use, which
is the listener's to make and not a measurement's.

**This is not a finding about the stutter.** The stutter is unfixed and will
return on Mode A boots. What is gone is a mitigation whose price was declined.

Two things kept, so this is not relearned: the reasoning stays in
`lempi-speaker.sh` where the code was, and anyone reinstating it must wait on
the `MediaTransport1` state rather than `Connected: yes` -- the removed
version posted `reopen-output` about 24 s before the speaker carried audio,
and recovered by a later tick's routing check `[PI3-AIM-050]` rather than by
design.

Removing it also buys the capture series something it wanted: a Mode A boot
recorded from power-on with a full stutter train and nothing intervening in
it.

### `[PI3-FOUND-480]` A silence that could be attributed, 2026-09-10

The Mode B cycle after `[PI3-FOUND-470]` connected and then played nothing.
Power on 2:23:45, connect tone 2:24:16, still silent at 2:25:25 with the
progress bar advancing and volume raised at both ends.

Every layer on the appliance reported success, which is the `[PI3-FOUND-030]`
signature exactly:

| Check | Reading |
| --- | --- |
| `wpctl` sink | MIDDLETON, vol 0.83 |
| `wpctl` streams | both channels linked to MIDDLETON playback, active |
| player `/audio/sink` | `{"sink":"MIDDLETON","dummy":false,"known":true}` |
| BlueZ | Connected: yes, Trusted: yes, codec SBC |
| connected devices | MIDDLETON only -- no interloper `[PI3-FOUND-090]` |

**And this time there was one more question to ask.** 373 `ACL Data TX`
packets in 5 s -- 74.6/s, indistinguishable from the clean Mode B run. The air
was carrying a healthy stream and the speaker was not making sound. **The
fault was in the speaker**, and no work on the Pi would have fixed it.

That attribution had never been possible before. PI009's silence had every
layer reporting success and no way to separate a truthful report from a lie;
the whole appliance-side supervisor exists because of it. A five-second packet
count now answers the question those five layers could not.

Recovery was a manual disconnect and reconnect -- both returned in under a
second -- followed by `reopen-output`. Audio returned immediately, and the
rate after was 375 packets in 5 s.

**Two honest limits on this entry.** The fault state was destroyed before the
transport's `State` and SBC `Configuration` were read: a path-extraction query
failed and restoring the listener's audio was chosen over digging. If it
recurs, those two properties are the first to capture, before touching
anything. And it revises `[PI3-FOUND-470]`: Mode B is reliably *not
stuttering*, which is narrower than reliably good. This was a different fault,
not a stutter, but the single-observation caution recorded there was
warranted.

