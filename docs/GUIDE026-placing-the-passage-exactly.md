# GUIDE026: Placing The Passage Exactly

**Development Guidance — written 2026-09-19, the design change
[GUIDE025](GUIDE025-what-holds-the-alignment-back.md) `[GDE-ARC-046]` asked
for**

GUIDE025 measured a follower sitting **−448 ms** out and recovering at
0.09 ms/s — which is `ECHO_DEBT_PPM` almost exactly, and therefore proof that
*nothing else was acting*. The direction "sound later" had no boundary
actuator at all; the transition could only ever bring a passage **forward**, so
half the corrections the loop issued were delivered by the slowest thing in the
system. This is what replaced that.

> **Related:** [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]` — the loop the change lands in · [GUIDE025](GUIDE025-what-holds-the-alignment-back.md) `[GDE-ARC-046]` — the measurement that asked for it · [GUIDE027](GUIDE027-what-the-transition-achieved.md) `[GDE-ARC-057]` — why this document's success lines could not be wrong · [SPEC044](spec/SPEC044-echo-mode-control.md) `[SPEC-ECHO-030]` — what the listener is promised · [GUIDE029](GUIDE029-what-is-known-about-alignment.md) `[GDE-ARC-063]` — the standing model and the refuted register

---

## 1. The transition is the alignment opportunity

**`[GDE-ARC-051]` A passage transition can place the incoming passage at any
time the follower asks for, in either direction, and the loop should ask for
the whole error rather than a safe fraction of it.**

The old design read the transition as a *crossfade to be nudged*. That framing
carried two limits, and both were consequences of the framing rather than of
anything physical:

- the correction was capped at `OFFSET_MAX_BITE`, 500 ms, on the reasoning
  that half a second of crossfade made longer or shorter is imperceptible;
- and it could only spend **overlap that already existed**, about five
  milliseconds on this library, so the "later" direction had essentially
  nothing to spend and the "earlier" direction paid out of the passage's head.

Read instead as *the moment this node chooses when its next passage begins*,
neither limit survives. The passage begins at the right time or it does not;
there is no smaller or larger version of that question, so there is nothing
for a cap to be cautious about.

*What the follower may use as the target is a separate question, and a harder
one than it looked.* The master publishes a `sound_at` for each passage
`[GDE-ECHO-410]`, and steering on that rather than on the anchor would be
strictly better — but §5 is why the follower cannot yet aim a boundary
correction at it. Everything in this section is about the actuator; the
correction still comes from the measured residual.

So the cap is gone and the transition is asked for the entire residual. **886
ms is now one transition rather than two**, and the per-correction arithmetic
that made the loop take ten minutes to walk in a step it had measured in one
reading stops existing.

*Corrected 2026-09-19: this said "five seconds is one rather than ten".* It
cannot be. `offset_fix` returns `Rejoin` above `OFFSET_REJOIN_BEYOND`, 1.5 s,
so the boundary is never asked for more than that. What was removed is
`OFFSET_MAX_BITE`, the 500 ms **bite** — the ceiling on a single correction
inside the band, not the band.

## 2. Silence is the actuator for "later"

**`[GDE-ARC-052]` A follower that must sound later emits silence ahead of the
passage's first sample. It is an actuator, not a fault.**

This was rejected outright in the original design, and the rejection was
right about the case it considered: a gap between two passages that are
*crossfading* is a hole torn in the music. But the rejection was carried
forward to every transition, and most transitions on this library are not
crossfades — `fade_out_ms` is 20 ms on **16,658 of 16,661 passages**, and
`lead_out_ms` governs only when a neighbour *may* overlap, not whether the
outgoing passage has finished sounding. At a transition where the outgoing
passage has faded, a bounded silence before the next one is a slightly longer
pause, which is what a pause between tracks already is.

The mechanism is deliberately dull. `admit_due` converts the owed
milliseconds to frames and hands them to `echo_gap_frames`; the mixer spends
that budget as zeroed blocks **before** it touches the incoming passage, and
says so when it is spent. Frames rather than milliseconds because the mixer
counts in frames and a millisecond is not a whole number of them.

Two properties are worth stating because they are what make it safe:

- **The passage lands whole.** Nothing is skipped or faded; it simply starts
  later. The old "later" correction had to open the passage further in, which
  moved the node the *wrong* way — a node asked to wait 120 ms arriving 18 ms
  sooner `[GDE-ECHO-372]`.
- **The ring stays contiguous.** The silence is submitted through the same
  path as audio, so the output ring never has a hole in it and no underrun is
  manufactured. `a_pause_in_writing_leaves_no_gap_in_the_audio` pins this,
  because the failure mode it guards against would be inaudible in a test that
  only checked the timing.

## 3. What "earlier" spends, and in what order

The other direction has two things it can spend and a clear preference
between them. `split_shift` encodes it:

| spent | resolution | costs the listener |
| :--- | :--- | :--- |
| **admission**, starting the passage earlier against the outgoing one | one mix block, 46 ms at 44.1 kHz | more overlap |
| **origin**, opening the passage further in | one millisecond | that much of the track's head |

Admission is preferred and takes as much as it can express, which is whole
blocks; the origin takes only the remainder, which is always under one block.
A 120 ms correction is therefore **92 ms of admission and 28 ms of head**,
where it used to be 120 ms of head — the listener hears the same 120 ms of
correction and keeps 92 ms more of the track.

Admission is bounded, and by something real: the pair of passages has to be
able to sustain the overlap, so the ceiling is the shorter of the two. On a
long pair that is minutes. Whatever admission genuinely cannot spend falls to
the origin, and only what *neither* can deliver reaches the frame trim — which
is now a rare path rather than the default one.

## 4. Where the number comes from

**`[GDE-ARC-054]` The follower already computed the placement error at every
transition and threw it away.** Deciding *whether* to flow into a boundary and
deciding *when* to open it are the same measurement, and only the first was
being kept.

`flowing_would_do` asks one question in the lead-up to every transition: when
will this node's next passage begin to sound, and when did the master say its
own would? It compared the two against a deliberately generous five-second
tolerance — which separates an ordinary shared boundary from a skip that moved
the master's by minutes — and returned a **boolean**. Inside the tolerance the
node flowed, and the alignment was left to the residual loop.

That is the wrong source, and measurably so. The residual is derived from the
anchor, so it carries the output ring's depth jitter — about 25 ms, the floor
under everything `[GDE-ARC-047]`. The two terms `flowing_would_do` had in hand
are **both schedule quantities**: this node's own queue arithmetic, and the
instant the master published `[GDE-ECHO-410]`. Neither passes through an
anchor. Their difference *is* the placement error, at better precision than
anything the loop could measure, and it was being reduced to one bit.

So the comparison is kept. `flow_error_ms` returns the signed error and the
boolean is a thin wrapper on it. **Acting on it from there was then tried, and
withdrawn the same day** — see §5.

---

## 5. The look-ahead window is negative

**`[GDE-ARC-056]` The master publishes its schedule for a passage at its own
admission, by which time a follower on a similar ring has already admitted the
same passage. There is no lead time to look ahead in — the window is negative,
not merely small.**

The mechanism `[GDE-ARC-054]` describes was built, deployed, and measured. It
is the right number aimed at the wrong target, and the measurement is
unambiguous.

`EchoCorrectNextStart` is spent at the next **admission**. Both nodes run a
~15 s output ring; the master publishes its forward schedule when it admits,
and the follower admits the same passage about a second earlier. So the
correction computed for passage P was always applied to the passage *after* P.
On `lp3-wifi`, 2026-09-19, the chain is legible in a single log:

```
06:27:23  placed 11572 by  73 ms   echo-place: 11572 would sound 404 ms late
06:30:29  placed 5766  by 404 ms   <- the 404 computed for 11572
06:40:52  placed 11056 by  65 ms   echo-place: 11056 would sound 431 ms late
06:47:07  placed 2030  by 431 ms   <- the 431 computed for 11056
```

It converged anyway, because a systematic error is similar from one passage to
the next, so a correction one boundary late still points the right way — which
is why the run showed clean geometric convergence, **+481 → +255 → +133 → +82
→ +44 → +10 ms** across eight transitions, roughly halving rather than
resolving in one. A one-boundary-late loop halves; an aimed one finishes.

And it did real harm at the end. The node reached **+10 ms** on passage 11056
— at the anchor floor, which is as good as this system gets — and the
misapplied 431 ms then put it **−264 ms** out on 2030. That is a fresh
injector of exactly the kind `[GDE-ARC-043]` describes, built by the work
meant to remove one.

**Withdrawn rather than guarded.** A condition on the branch would be a
condition that is essentially never true, which is dead code wearing a
safety label. What the design needs is an actuator that can be aimed *after*
admission, and one already exists: the silence budget of `[GDE-ARC-052]` is
spent at **mix** time, a whole ring after admission. A follower that admitted
each passage with a small standing silence budget could revise that budget in
either direction — shorter to sound earlier, longer to sound later — right up
until the mixer reaches the passage's first sample. That is the ~15 s of
genuine look-ahead the boundary correction never had, and it is the next step
rather than a condition here.

## 6. What this removed

**The client no longer sheds a remainder to the trim.** With the cap gone the
shift the client asks for **is** the residual it measured, so the remainder it
used to compute was identically zero and the branch could not fire. It was
deleted rather than left in place.

It is worth being explicit that nothing was lost. That branch existed so a
listener who asked to be in step straight away had *something* acting between
boundaries `[GDE-ARC-041]`, and the something was the frame trim at 0.1 ms/s:
the 100 ms it was typically handed took **seventeen minutes**, which is longer
than waiting for the boundary it was supposed to beat. Believing otherwise was
`[GDE-ARC-043]` once more — an actuator sized for drift being read as an answer
to a step.

So "straight away" now means *the next transition takes all of it, exactly*,
and what still acts sooner than a transition is the mid-join, which is decided
elsewhere and carries its own cost `[GDE-ECHO-342]`.

## 7. One band, one controller

**`[GDE-ARC-055]` Placement acts above the endgame band and the frame trim
acts below it. Two actuators sharing one band is a design fault, not a tuning
problem.**

Found by running the deployed build, not by reading it, and it was live on
three appliances for about half an hour. The placement in §4 fires on every
pass — twice a second — and `EchoCorrectNextStart` **clears the outstanding
debt** along with setting the new plan, because a new boundary plan supersedes
the remainder of the old one `[GDE-ARC-041]`. Inside the endgame band,
`correct_offset` has already handed the error to the frame trim with
`EchoShedOffset` and returned. Placing on the same pass wiped that debt before
one 23 µs splice could be paid against it, then did it again half a second
later, indefinitely. **The band a follower spends almost all its time in would
never have converged.**

The fix needs no new constant: the partition already exists at
`OFFSET_ENDGAME` `[GDE-ECHO-349]`, and the placement simply respects it. What
it cost was the assumption that a correction issued continuously is harmless
because each one supersedes the last — true of the *plan*, false of the debt
travelling with it.

**The test that should have caught it passed first.** Its fixture set
`current`, `position_ms` and `queue` on the shared state, then ticked the
engine to establish a debt — and a tick **republishes the engine's own
snapshot over that shared state**. The engine had no passage and an empty
queue, so `own_next_starts_in_ms` returned `None`, the flow branch was never
reached, and the assertion held for a reason entirely unrelated to what it
claimed. A green test whose fixture has been erased is indistinguishable from
a green test, which is `[GDE-ARC-043]`'s shape in a new place: the reading was
right and it was measuring something else. Establish engine-side state first,
shared-state fixtures after the last tick.

## 8. What did not change, and why

**`OFFSET_REJOIN_BEYOND` is still 1.5 s, and its arithmetic is now a different
argument for the same number.** It used to be justified from below by the join
bias and from above by "five seconds takes forty minutes to nudge away". The
second half is dead: any offset the boundary can express is one transition.

What remains above is a *wait*. A correction lands at the follower's next
transition, four to six minutes on this library, so the question is whether
sitting `x` out for up to six minutes beats jumping now and landing within the
join bias. Below roughly twice that bias it does not — a rejoin from 700 ms to
500 ms buys nothing and cuts the ring `[GDE-ARC-042]` — and twice the worst
observed bias is about where 1.5 s already sat.

**The number that would move it is `placement()`** `[GDE-ARC-044]`. A join that
landed accurately would be worth reaching for much sooner, and the threshold
would drop with the bias. That remains unbuilt.

**The anchor is still the floor** `[GDE-ARC-047]`. Exact placement is exact
with respect to the master's published schedule; the residual it is *steering*
on still carries the output ring's own depth jitter, about 25 ms. No amount of
precision at the boundary gets below that, and the fix for it is still a
position taken from the device's frame counter.

---

## 9. What to take from it

**`[GDE-ARC-053]` A constraint inherited from a framing outlives the framing,
and reads afterwards as a law of the system.** Neither the 500 ms cap nor the
refusal to emit silence was ever measured against anything. Both followed
from describing the transition as a crossfade, both were then cited by later
work as fixed properties, and the loop was tuned around them for months.

The tell was the recovery rate. A node returning to alignment at *exactly*
`ECHO_DEBT_PPM` is not a node whose correction loop is running badly — it is a
node whose correction loop is not running at all, with the endgame trim the
only thing still moving it. A measured rate that matches a constant to two
significant figures is never a coincidence; it names the only actuator that
was working.
