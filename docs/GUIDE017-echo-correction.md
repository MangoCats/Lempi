# GUIDE017: Echo Correction — Rate and Offset

**Development Guidance — split from [GUIDE016](GUIDE016-echo-playback-plan-build.md) 2026-09-18**

Phase 5 on its own, because building it grew it past what belonged inside the
plan. Two errors, corrected in two places, for reasons that took some finding:
a **rate** is a slope and is trimmed continuously, an **offset** is a position
and is shed at a passage boundary. Confusing them is how a correction loop
acquires a fault that only shows after hours.

> **Related:** [GUIDE016](GUIDE016-echo-playback-plan-build.md) `[GDE-ECHO-330]` — the phase this follows · [LOG012](LOG012-phase-4-drift.md) `[LOG-P4-130]` — the +13.92 ppm this corrects · [LOG011](LOG011-the-lead-is-a-ring.md) `[LOG-ECHO-030]` — why depth cannot serve · [SPEC021](spec/SPEC021-echo-mode-control.md) `[SPEC-ECHO-030]` — the control that starts it · [GUIDE029](GUIDE029-what-is-known-about-alignment.md) `[GDE-ARC-063]` — the standing model and the refuted register

---

## 1. What is corrected, and where

**`[GDE-ECHO-340]` Rate and offset are different errors, corrected in different
places, and separating them is what makes the ring's depth stop mattering.** A
*rate* error accumulates slowly and is corrected by trimming: drop or duplicate
one frame on submission into the output ring, on the mixer thread, at whatever
interval the measured ppm calls for — one frame every 5.7 s at the ~4 ppm now
measured for `bose`↔`smartboardpc`, and every 1.4 s for `bose`↔`lempipi`, not
the one per minute this plan assumed from the discredited 0.4 ppm
`[GDE-ECHO-550]`.
That the correction is heard fifteen seconds later is irrelevant, because what
is being corrected is a slope, not a position. An *offset* error is corrected
only at a passage boundary, by opening the next passage a few milliseconds
adjusted, where it costs nothing and is inaudible by construction.

This split is why `[REQ-AUD-164]`'s buffer-depth trap does not bite here as it
did four times before `[GDE-ECHO-220]`: the one correction that would need to be
heard immediately is the one never applied mid-passage.

*Offset correction built 2026-09-18, and building it settled how it has to
work.* **Ring depth cannot do it.** For a gapless stream a sample's air time is
fixed by where sample 0 was placed plus the device rate; the next passage is
appended contiguously, so its alignment is inherited and changing the ring's
depth mid-stream shifts nothing. Depth decides alignment at a *fresh start* and
nowhere else, which is why `[LOG-ECHO-030]` holds at a join and why this
correction must move the **transition** instead.

*A first attempt moved the position inside the file — opening the next passage
a few milliseconds further in — and was one-directional, since there is no
negative position to open at.* The knob that actually serves is **where the
incoming passage sits inside the overlap**. Every transition has one, and
starting the passage earlier overlaps a few milliseconds more and catches up,
while starting it later overlaps a few less and waits. Nothing is skipped and
nothing is repeated, so it is inaudible in both directions. It is a signed
nudge to `should_admit`'s threshold, clamped so it can neither ask for audio
that does not exist nor narrow through zero into a gap, which would be a worse
fault than the offset it was correcting.

**Corrected 2026-09-18 `[GDE-ECHO-372]`.** This paragraph claimed the knob was
"symmetric by construction — there is always a slightly smaller overlap."
*There is not:* `min(lead_out(A), lead_in(B))` against a **5 ms** lead-in
median is the whole range of the "later" direction, so the clamp is where that
correction ends rather than a rail around it. What the transition cannot
absorb now goes to the frame trim `[GDE-ECHO-349]`.

**`[GDE-ECHO-345]` The anchor's precision, not the ear's, sets how well this can
work.** The residual is measured from a `DriftAnchor`, whose position comes from
`audible_ms` and therefore carries the output ring's own depth jitter — tens of
milliseconds `[LOG-P4-010]`. The deadband is 40 ms for that reason alone, well
above anything a listener would call aligned. Correcting below the noise is the
hunting `[GDE-ECHO-350]` exists to prevent, so **finer alignment needs a more
precise anchor, not a smaller deadband**: a position derived from the device's
own frame counter, which is sampled atomically with its timestamp and carries no
ring at all `[LOG-P4-010]`. That is the next thing worth building, and until it
exists a following node is audibly following rather than audibly one speaker.

A defect surfaced on the way. The residual compared the *timestamps* of two
anchors, which are never about the same sample — the master's crossed a network
and describes a moment already past. On a quiet network that difference is
mostly transport delay, so the loop would have chased the network. The local
reading is now carried along its own playback to the sample the master named
before the two are compared.

*Rate correction built 2026-09-18.* The trim is applied on the mixer thread,
one frame per interval, in `apply_trim` — which refuses rather than forces when
a block is too small to drop from or has no room for a duplicate, because a
trim skipped now simply happens a few milliseconds later and the schedule is a
rate, not a queue of debts.

*Corrected 2026-09-18 `[GDE-ECHO-373]`.* **A refusal has to be one the caller
can see.** The mixer handed `apply_trim` a block exactly as long as its own
buffer — `2048 * channels` against a threshold of 4096, the same number on a
stereo device — so every *duplicate* was refused for want of one frame and a
node running early could not be trimmed back at all. The block is now sized in
frames with a frame of headroom, and the caller reads what `apply_trim`
returned: a refused trim credits no debt, keeps its turn, and says so once.

**`[GDE-ECHO-356]` The trim runs on a fitted slope, never on the latest
residual.** *Renumbered from 355 on 2026-09-18: it was defined here and in
[GUIDE020](GUIDE020-control-propagation.md), every citation meant GUIDE020's,
and this one had none — so this was the edit nothing had to follow.* A single reading carries the anchor's ring jitter `[LOG-P4-010]`,
so a position servo at the 500 us deadband the `Follower` was built with would
fire most seconds in whichever direction the noise last pointed. `RateEstimate`
fits a line across an hour instead, and the interval that slope calls for *is*
the rate limit — there is no second governor to disagree with it.

The window is set by arithmetic rather than taste: the slope's standard error
is `s / (sd(t) * sqrt(n))` for scatter `s` across span `T` with `sd(t) =
T/sqrt(12)`, which at 30 ms sampled twice a second is **5 ppm over ten minutes
and 0.35 ppm over an hour**. Ten minutes measures nothing useful about a 14 ppm
drift. An hour is also about the timescale the rate itself wanders on
`[LOG-P4-130]`, so averaging further would smooth a quantity that has moved.

Anything that *steps* the residual — an offset correction, a rejoin — clears
the window, because a straight line through a step reads as a large rate that
never happened: a 40 ms correction inside a half-hour window fits to about
33 ppm.

**`[GDE-ECHO-341]` Every correction must shrink the error it was given, which
means an offset is bitten off and never escalated.** Found by running two
nodes: `lempiplay3` sat a steady **0.9 s** behind `bose` while correcting
continuously and reducing it not at all.

The loop had two moves and reached for the wrong one. A shift spends the
transition's overlap and always reduces the offset by however much it spends. A
rejoin throws the node's first sample down afresh -- and *inherits whatever
error that placement carries*. When the residual exceeded what one transition
could absorb the loop escalated to a rejoin, the rejoin landed with the same
systematic bias, and the next measurement found the same offset again. It could
run for ever without converging, and did.

So a large offset is corrected by the transition rather than by escalating.
`Rejoin` is reserved for a residual past the threshold in `[GDE-ECHO-344]` --
1.5 s -- where the node is probably not playing what it thinks it is and
placing the first sample afresh is the honest answer rather than a smaller
correction.

*Superseded in part 2026-09-19, in this entry's own direction.* It used to read
"886 ms is two transitions at half a second each": the correction was capped at
`OFFSET_MAX_BITE` and walked in across passages four to six minutes apart. The
cap is gone, the transition is asked for the whole residual, and **886 ms is
one transition** — so "every correction shrinks the error" is now met by taking
it to zero. See `[GDE-ARC-051]` in
[GUIDE026](GUIDE026-placing-the-passage-exactly.md), and `[GDE-ARC-052]` for
the actuator the "later" direction had been missing entirely.

**`[GDE-ECHO-343]` "Is it playing this?" is the wrong question to ask a
follower; "is it coming to this?" is the right one.** The mid-passage join was
guarded on the *currently playing* passage. But `current` is the **audible**
passage, and the output ring is some fifteen seconds deep `[LOG-ECHO-020]`, so
for a long window after a follower admits a passage it still names the previous
one. The master's anchor switches as soon as its own ring drains, which is
sooner.

So at every ordinary transition the guard saw a mismatch and joined into a
passage the node was already flowing into perfectly well -- and every join cuts
the ring and re-imposes the join bias `[GDE-ECHO-342]`. That is how a node held
a steady 0.9 s of lag *through a correction loop that was working*: the loop
shed the offset over a couple of transitions, and the next transition put it
straight back.

The queue is the rest of the answer. A passage already admitted but not yet
audible sits at the front of the published queue, ahead of what is still
merely queued, so asking "playing **or** coming" covers both the ring window
and the ordinary wait. A passage already coming needs no join, only patience.

**`[GDE-ECHO-347]` The loop's floor was its actuator's quantum, and the
deadband was smaller than it.** Measured on the fleet 2026-09-18, and this is
the number that explains what a listener still hears. After converging from
924 ms in two transitions, the residual sat and dithered:

    +441 -> admitted 440 ms early
     +51 -> admitted  50 ms early
     -43 -> admitted  43 ms late
     -44 -> admitted  43 ms late
     -49 -> admitted  48 ms late
     +43 -> admitted  42 ms early

A correction at *every* transition, each applied, each measuring back the same
size. Not noise -- every one of those figures is one mix chunk. The mixer runs
only with `MIN_SUBMIT` of room, so an incoming passage's first sample lands on
a chunk boundary and admission timing can only shift in steps of **46.4 ms** at
44.1 kHz. The loop had reached its actuator's resolution.

And it could never come to rest, because the deadband it was asked to reach
(40 ms) is **smaller than the smallest step it can take** (46 ms). That is the
same class as `[GDE-ECHO-344]` -- a threshold inside another mechanism's error
band -- with the mechanism being quantisation rather than noise, which is why
no amount of filtering would have found it.

Opening the passage part-way in is not quantised. Skipping `o` of the content
makes everything after it sound `o` earlier, to the millisecond, and a few tens
of milliseconds is inside the lead-in where nothing has begun. So the
correction now splits: **admission for the coarse part, origin for the fine**.
Being late needs only the fine knob. Being early goes back a whole chunk on the
coarse one and pulls the overshoot forward on the fine, since there is no
negative position to open at -- the asymmetry `[GDE-ECHO-340]` first ran into,
now put to work rather than worked around.

**And the fine knob may only spend what the coarse one can pay for**
`[GDE-ECHO-372]` — the condition this was written without. On a 5 ms pair
`(-138, +38)` delayed the transition by 5 ms and pulled the content forward by
38, so a node asked to wait 100 ms arrived 33 ms sooner. The backstep is now
capped at whole chunks the overlap can afford.

**`[GDE-ECHO-349]` Below the mix quantum only the frame trim will do, and it
was already there.** A frame is 23 us and the trim already drops or duplicates
one, inaudibly, on the mixer thread -- it was simply wired to *rate* alone.
Nothing about the actuator is rate-specific: N frames dropped is N/44100
seconds of position.

So the correction now hands over. Above one mix quantum a passage boundary
shifts the whole error at once `[GDE-ECHO-347]`. Below it the coarse step is
larger than the error, and the trim pays the remainder off one frame at a time.
The two share an actuator, so they are summed into a single interval rather
than run as two timers that would double the splice rate and argue about
direction.

*Extended 2026-09-18 `[GDE-ECHO-372]`.* The handover is no longer only
downwards: whatever a transition **could not absorb** — on 5 ms pairs, the
whole of any "later" correction — becomes a position debt at the same
admission, *set* from the latest measurement rather than added, so repeating
the measurement cannot compound it. Slow, and monotonic and inaudible, which
the boundary knob in that direction was not.

The budget is **100 ppm** -- one part in ten thousand, inaudible as pitch by a
wide margin, about 4.4 frames a second so each splice is well clear of the
last, and a whole quantum shed in about eight minutes. Raising it is the
obvious way to converge faster and the obvious way to make it audible; that
wants a listening test rather than an argument.

**`[GDE-ECHO-348]` The endgame needs a measurement finer than the actuator it
drives, which a single reading is not.** One anchor reading carries tens of
milliseconds of ring jitter `[LOG-P4-010]`, so correcting a 10 ms position from
one would be chasing noise -- and the deadband could not be lowered below that
noise, which is why it sat at 40 ms and why 40 ms was smaller than the coarse
knob's own step.

A median over two minutes fixes it. Median, not mean, because the ring's
shortfall is bounded one side and not the other. The window is derived: filtered
noise falls as `1.25 s / sqrt(2T)` while drift accrued *during* the window grows
as `r·T`, and at 30 ms of scatter and 13.92 ppm they cross near 130 s at about
2 ms each. The deadband follows the filter down, from 40 ms to 8 ms. Anything
that steps the residual clears the window, exactly as it clears the rate fit.

Three faults in this loop are recorded separately, because the pattern in them
outlasts the particulars: [GUIDE019](GUIDE019-correction-faults.md)
`[GDE-ECHO-351]`. A later read of the built code found the same pattern in
three more places, including a correction that increases the error it was
given: [GUIDE021](GUIDE021-echo-review.md) `[GDE-ECHO-372]`.

**`[GDE-ECHO-342]` The join bias, measured rather than assumed.** *Deferred
through several rounds and fixed 2026-09-18, when it became the whole of what
a listener could hear: a consistent ~900 ms, confirmed by ear, by
`tools/echo_skew.sh` (900/875/967 ms) and by the follower's own residual
(+845, +953) all at once.*

A commanded start goes through `skip`, which applies `skip_lead_ms` -- a
constant -- and then does the work: open the file, seek, build the resampler,
and top the decoder up so the overlay is not silence `[PI-CHR-075]`. That work
lands **directly on the air time**, and firing early by a constant cannot
compensate a variable. The residual of a join therefore *is* its preparation
time, identically; everything else in the arithmetic cancels.

The compensation is now measured. Each join times its own preparation and
folds it into a smoothed estimate, weighted towards history so one slow seek
moves the figure rather than replacing it, and clamped so a pathological join
cannot leave every later one firing seconds early. The first join on a cold
node guesses 400 ms and is wrong once; every join after it uses what this node
actually costs.

This is deliberately *not* the durable fix. That is to derive the placement
from the target air time at the moment the ring is cut -- after the work, not
before -- which `placement()` already computes and nothing calls. It was not
taken here because `cut_ring_to_incoming` is the real-time path every ordinary
user skip also uses, and a self-calibrating constant reaches most of the
benefit without touching it. If the measured preparation turns out to vary
widely *within* a node rather than between nodes, the estimate will not hold
and the durable fix becomes necessary.

**Superseded note:** the join bias itself is real and no longer unfixed. A
commanded start lands late by a variable few hundred milliseconds to a second.
`skip_lead_ms` is compensated `[GDE-ECHO-337]`, but before cutting the ring the
engine synchronously tops the incoming decoder up, and that takes as long as it
takes -- "seconds", on an appliance seeking into a long capture
`[PI-CHR-075]`. A correction loop that always converges makes this survivable
rather than fatal, which is why it is recorded here and not patched in a hurry:
the durable fix is to place the incoming audio at a computed ring depth
`[placement]` rather than to fire early by a constant and hope.

**`[GDE-ECHO-350]` Hysteresis and a rate limit, or the loop will hunt.** Trim
only while the estimated offset exceeds a deadband comfortably larger than the
measurement noise, never more than one frame per correction interval, and never
on an estimate younger than the regression window. A correction loop that reacts
to its own measurement noise produces exactly the slow periodic wobble it was
built to remove.

