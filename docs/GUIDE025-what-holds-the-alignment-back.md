# GUIDE025: What Holds The Alignment Back

**Development Guidance — written 2026-09-19 from seventy minutes of one
follower's own log, with its clock finally right**

[GUIDE024](GUIDE024-measuring-two-speakers.md) `[GDE-ARC-038]` removed the
clock error and the measured skew fell from 688 ms to a median of 12 ms. This
asks the next question: with the clock no longer in the way, what is left
holding the alignment back? The answer is not what the correction loop was
tuned against.

> **Related:** [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]` — the loop · [GUIDE024](GUIDE024-measuring-two-speakers.md) `[GDE-ARC-038]` — the clock · [GUIDE021](GUIDE021-echo-review.md) `[GDE-ECHO-371]` — the read of the built path · `tools/echo_skew.py --clocks` — the instrument · [GUIDE029](GUIDE029-what-is-known-about-alignment.md) `[GDE-ARC-063]` — the standing model and the refuted register

---

## 1. The shape of it

**`[GDE-ARC-043]` The error injector is instantaneous and every error remover
is millimetres per second.** That ratio, not any constant in the loop, is what
a listener hears.

Seventy minutes of `lp3-wifi`, sub-millisecond clock throughout:

```
04:03:32  +117 ms  -> the boundary took all 117
04:04:33   +15 ms  -> trimming
04:05:43   +14 ms  -> trimming; converged and holding
04:11:59  scheduled start, passage 13796
04:12:13  join part-way into 5164     <- the passage it had just left
04:12:15  join part-way into 13796    <- and then the new one
04:12:35  +802 ms out
04:18:10  the boundary took 500 ms
04:19:00  +315 ms out
```

The loop holds 14 ms perfectly well. Then three ring cuts in three seconds
put it 802 ms out and it spends ten minutes crawling back.

| path | rate |
| :--- | :--- |
| a ring cut re-imposing the join bias `[GDE-ECHO-342]` | ~700 ms in ~1 s |
| the boundary shift, `OFFSET_MAX_BITE` over a ~300 s passage | **1.7 ms/s** |
| the frame trim at `ECHO_DEBT_PPM` | **0.1 ms/s** |

Recovery from one join is 800 / 1.7 ≈ **eight minutes**, which is what the log
shows. **No tuning of the removers answers a 500:1 mismatch.** The injector has
to stop. Everything in §3 is second order until it does, and reading the
constants as "too slow" is reading the wrong end of the problem — they were
sized against drift, and drift is not what is moving this node.

---

## 2. Why the cuts happen

**`[GDE-ARC-042]` The master truthfully reports two different passages for a
whole ring around every boundary, and the join guard cannot tell that from
being on the wrong passage.**

The anchor is what the master can *hear*: it lags admission by the output
ring, some fifteen seconds `[LOG-ECHO-020]`. The schedule is what the master
just *admitted*: it leads by the same ring. Between them lies a fifteen-second
window, after every transition, in which both statements are true and they
name different passages.

`coming_here` only ever asks about the anchor. Across a boundary it answers
**no to both passages in turn**:

- the follower has just taken the scheduled start, so the *old* passage is in
  neither `current` nor its queue — a join fires into the passage it just
  left;
- `mid_joined` was keyed on a passage id, so a commitment to the new passage
  could not suppress that join at all;
- and once that join lands, the node is on the old passage again, so the
  *new* one now fails the same test.

Three cuts in three seconds, each one re-imposing the join bias.

The steady-state version of exactly this was fixed by `[GDE-ECHO-343]` — that is
where `coming_here`'s queue check came from — but a commanded start empties
the state it reads. The guard was right about the case it was written for.

**Fixed 2026-09-19, two changes, both small.** A commitment now suppresses a
join to *any* passage while it holds, because after a commanded start the ring
is cut, the queue has advanced and the basis is voided, so no join decision
taken from that state is worth trusting. And a join is suppressed outright
while the master's own anchor and schedule disagree about which passage it is
on: that is the master saying it is mid-transition, the schedule path already
owns it, and a mid-join can only cut the ring a second time.

Neither needs a new constant. The second keys on a disagreement the master
already publishes — which is the shape to prefer, since a threshold would have
to be tuned against a ring depth that varies per node.

---

## 3. What is left once the cuts stop

Ranked by how much each still costs, with the caveat that all of them were
sized against an error source that should not have existed.

**`[GDE-ARC-044]` Every cut costs the join bias because `placement()` is still
uncalled.** `[GDE-ECHO-342]` names it the durable fix — derive the ring depth
from the target air time *at the moment the ring is cut*, after the
preparation rather than before — and records why a self-calibrating constant
was taken instead: `cut_ring_to_incoming` is the real-time path every ordinary
user skip also uses. That trade is what leaves a legitimate join costing a few
hundred milliseconds rather than nearly nothing. It also showed its edge here:
three joins in three seconds drove `echo_prep_ms` 295 → 162 → 195 → 158 → 124,
so the compensation was being thrashed by the storm it was meant to absorb.

> **`[GDE-ARC-058]` Taken 2026-09-19, and the constant it replaced was wrong
> by an order of magnitude.** `Engine::skip` now asks `placement()` for the
> depth at the moment of the cut, so the preparation is *measured* rather
> than predicted. What the prediction was actually doing, from this fleet's
> own log: `preparing took 37 ms (was assuming 400)`. It converged by
> quarters — ten joins to reach the truth — over a node that made three joins
> in seven hours, and **reset to the 400 ms cold guess at every restart**, so
> it never arrived. `echo_prep_ms` survives as a *budget*, deciding only how
> early to fire so there is time to reach the cut; being generous there now
> costs nothing, where being wrong used to cost the join its placement. The
> ordinary skip and seek path keeps the constant lead it always had.

**`[GDE-ARC-045]` The rate loop cannot run, because the loop that runs most
often clears it.** Zero `echo-rate:` lines in three hours, and zero in the
seventy minutes since the clock was fixed. `RateEstimate` needs fifteen
unbroken minutes `[GDE-ECHO-356]`, and it is cleared by every offset
correction and every clock step — three of those while chrony settled. A node
that is persistently out corrects every passage, so the window never reaches
its minimum span. The slope is therefore never learned, and the offset loop
re-does that work from scratch at each boundary. The two halves of
`[GDE-ECHO-340]` were meant to be independent; in practice the faster one
starves the slower one.

**`[GDE-ARC-049]` A late snapshot and a backward clock step are the same
observation, and only one of them was being considered.** The second reason
the rate loop never ran, and the one the storm fix does not touch.

`heard_at` is stamped as the master builds the snapshot, so transport delay
subtracts from every reading — which is exactly why `MasterClock::offset`
takes the **maximum** over its window, and the type says so. But
`observe` compared each single new reading against that maximum and declared
a step on any difference over one second. Delay is one-sided; the estimate
knew that and the step test did not. Any snapshot more than a second late
therefore read as the master's clock jumping backwards, and each one called
`rate.clear()` and sent `SetEchoRate(0.0)`.

Measured on `lp3-wifi` 2026-09-19: **four "a clock stepped" lines in thirty
minutes**, while chrony's own `Last offset` on that node was 465 µs and it
had stepped nothing at all. On wifi with 600 ms of round trip, a delay spike
over a second is routine rather than exceptional.

**Fixed by confirming.** A reading *above* the estimate needs no confirming —
no wire delivers a snapshot early, so that can only be real. A reading
*below* it opens a run, and a step is declared only when three consecutive
readings hold the shift, which is about a second and a half at the snapshot
cadence. One late packet resets the run and costs nothing.

One trap on the way, worth recording because the test caught it and reasoning
had not: a large step ages every older sample out of the window on its *first*
reading, so the estimate becomes the shifted level immediately. Judging the
run against the current estimate would then read "settled" and reset it,
confirming nothing ever. The run is judged against the offset that stood when
it opened.

**`[GDE-ARC-046]` The loop period is the passage length, and the continuous
actuator is the throttled one.** A correction is decided once per master
passage and lands at the next boundary: four to six minutes of loop delay on a
system whose actuator could act at any instant. And the two actuators are
sized the wrong way round — the boundary shift moves 1.7 ms/s and the frame
trim, which can work mid-passage, is capped seventeen times lower. Raising
`ECHO_DEBT_PPM` is the obvious lever and the obvious way to make it audible;
`[GDE-ECHO-349]` already says that wants a listening test rather than an
argument, and there are now two speakers side by side to run it on.

> **Answered 2026-09-19 — and not by tuning either constant.** A thirty-minute
> run after the §2 fix found the node at −448 ms recovering at **0.09 ms/s**,
> which is `ECHO_DEBT_PPM` to two significant figures and therefore says the
> boundary was contributing *nothing*: the "sound later" direction had no
> actuator there at all. The answer was to give it one and to stop capping the
> other — see `[GDE-ARC-051]` and `[GDE-ARC-052]` in
> [GUIDE026](GUIDE026-placing-the-passage-exactly.md). The boundary shift's 1.7 ms/s in the table
> above is a rate only because the correction was rationed; unrationed it is
> not a rate at all, it is a placement.

**`[GDE-ARC-047]` Under all of it, the anchor is the floor.**
`[GDE-ECHO-345]` said this before any of the rest was built: the anchor comes
from `audible_ms`, so it carries the output ring's own depth jitter, and
"finer alignment needs a more precise anchor, not a smaller deadband".
Measured with `tools/echo_skew.py`, the residual scatter is about **25 ms**
(p50 10–12 ms, p90 31–51, spread 61–92). No control design gets below that.
The stated fix is a position taken from the device's own frame counter,
sampled atomically with its timestamp and carrying no ring at all — still
unbuilt, and still the difference between audibly following and audibly one
speaker.

---

## 4. What to take from it

**`[GDE-ARC-048]` A control loop tuned against the wrong disturbance looks
badly tuned.** Every constant in §3 is defensible against the error the design
expected — crystal drift, tens of parts per million, accumulating slowly and
smoothly. None of them is defensible against a 700 ms step arriving from the
node's own join path, and reading the sawtooth as "the corrections are too
small" would have led to raising the bite, raising the trim budget, and making
the system audible while leaving the cause untouched.

The tell was in the data and not in the code: **the loop held 14 ms for seven
minutes.** A loop that can hold 14 ms is not a loop that is too slow; it is a
loop being knocked over. The question to ask of a sawtooth is always which
edge is the fault — and here the fast edge was ours.
