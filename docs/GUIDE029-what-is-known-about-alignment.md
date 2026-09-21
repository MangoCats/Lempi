# GUIDE029: What Is Known About Alignment

**Development Guidance — the standing model and the refuted register, current
2026-09-19. Supersede in place; do not fork.**

Five documents now describe parts of echo alignment and none states the whole
of it, so the current model has to be reassembled by reading all five in
order. Worse, most of what has been **disproven** lives only in the session
that disproved it. This is the board for both.

> **Related:** [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]` — the correction loop · [GUIDE025](GUIDE025-what-holds-the-alignment-back.md) `[GDE-ARC-043]` — what held it back · [GUIDE026](GUIDE026-placing-the-passage-exactly.md) `[GDE-ARC-051]` — the boundary actuators · [GUIDE027](GUIDE027-what-the-transition-achieved.md) `[GDE-ARC-057]` — measuring rather than restating · [GUIDE028](GUIDE028-learning-where-a-join-lands.md) `[GDE-ARC-061]` — the join learner

---

## 1. Why this document exists

**`[GDE-ARC-063]` A refuted theory is a finding, and it must be written down
where the next person will look before they re-derive it.** A register of
what is *not* true is worth as much as the model, and it decays faster,
because nothing in the code points at it.

On 2026-09-19 alone, six explanations were proposed, pursued, and killed by
measurement. Three had already been written down in the document they arose
in; three existed nowhere and were within an hour of being lost. Two were
close to being proposed a second time *in the same session* by the person who
had already disproven them.

The cost is asymmetric: a working theory that is wrong gets corrected by the
next measurement, while a refuted theory that is forgotten gets rebuilt from
scratch, deployed, and measured again.

## 2. The standing model

What is believed true today, with the measurement each rests on.

| | |
| :--- | :--- |
| **Steady state was solved on the build of 2026-09-19 morning.** | Six hours unattended, `bose` ↔ `lp3-wifi`: **p50 1 ms, p90 24 ms**, zero boundary corrections, zero ring cuts `[GDE-ARC-047]`. **This predates every commit below it** — the uncapped boundary, the silence actuator, the anchor gate and the join learner have had no unattended run. Treat it as the standing figure to beat, not as a property of the current tree. |
| **The clock is not the problem.** | chrony on every node against a LAN reference; nodes agree to 84 µs–1 ms `[GDE-ARC-038]`. Standard equipment. |
| **The boundary has an actuator in both directions, and the arithmetic is exact.** | Earlier: whole mix blocks of admission, remainder from the head. Later: silence before sample 0 `[GDE-ARC-051]`, `[GDE-ARC-052]`. *"Exact" is the arithmetic, not the delivered placement* — see §4, and the three qualifications below. |
| **"No cap" is loose talk.** | `OFFSET_REJOIN_BEYOND` is 1.5 s and `offset_fix` returns `Rejoin` above it, so the boundary is never asked for more. GUIDE026 §1's "five seconds is one transition" cannot happen. What was removed is `OFFSET_MAX_BITE`, the 500 ms *bite*. |
| **A join is placed from a computed depth, not a constant.** | `placement()` at the moment of the cut, after the preparation `[GDE-ARC-058]`. Removed a measured 386 ms. |
| **Joins still land ~600–800 ms behind, cause unknown.** | Measured repeatedly: 530, 611, 687, 712, 893. Absorbed by a learned per-node median `[GDE-ARC-061]`, not explained. |
| **Position corrections deliver only 55–80% of what is asked.** | Two independent actuators, same range. **The largest open question** — see §4. |
| **The anchor is the floor under everything.** | ~25 ms of ring-depth jitter; no control design gets below it without a frame-counter position `[GDE-ARC-047]`. |

## 3. The refuted register

**Do not re-propose these without new evidence.** Each was believed, acted
on, and killed.

| theory | how it died | recorded |
| :--- | :--- | :--- |
| A 500 ms cap protects the crossfade | Partly. The cap was about overlap and the "later" direction now has silence instead `[GDE-ARC-052]` — but "earlier" still spends **overlap**, so a 1.5 s correction extends a crossfade by up to 1.47 s, three times what the cap was written for, and no listening test has been run on that. Reopen if anyone reports a long crossfade | `[GDE-ARC-051]` |
| Silence at a transition is always a fault | `fade_out_ms` is 20 ms on 16,658 of 16,661 passages — *but the argument only held once `[GDE-ARC-070]` made the silence actually land at the transition; before that it was emitted an overlap early, into a passage still playing* | `[GDE-ARC-052]` |
| The follower can aim a boundary correction from the master's schedule | The schedule arrives *after* the follower has admitted that passage; the look-ahead is **negative** | `[GDE-ARC-056]` |
| Admission truncates corrections badly (the "6–63%" table) | Bad pairing across a timezone and a ring; instrumentation showed ~97 % delivery **on long pairs**. A short pair genuinely clamps — a measured 1034 of 1500 ms, 69 % — and the shortfall goes to the trim `[GDE-ARC-068]` | GUIDE027 §1 |
| Bursty mixing overshoots the admission threshold | `want` is capped at one mix block, so overshoot ≤46 ms | **here** |
| A join blinds the basis for minutes | `skip` → `admit_due` → `establish`; it is valid before `skip` returns. *Conditional: `admit_due` returns early on an empty queue, so a skip with nothing to admit does leave it voided — correctly, since nothing is sounding to anchor, and `[GDE-ARC-066]` restores it at the next admission* | `[GDE-ARC-059]` |
| The join bias is an under-estimated device delay | Both nodes report `Hardware` timestamps, 2043 and 1994 frames | **here** |
| `echo_delay_trim_ms` is a usable discriminator for §4 | It moves what a node *reports*, never the audio, so it cannot separate a scaled instrument from a weak actuator. *(It did not even do that until `[GDE-ARC-065]`; since that fix it does shift the reported position, so do not read this row as saying otherwise.)* | §4, `[GDE-ARC-065]` |
| A landing measures the bias | It measures what is *left* of it; record `applied + residual` | `[GDE-ARC-061]` |

## 4. The largest open question

**`[GDE-ARC-071]` Narrowed 2026-09-19 (evening), from corrections already in
the journal.** Eight clean converging sequences on `lp3-wifi`:

```
673→299 k=0.59   495→229 k=0.57   229→102 k=0.60   102→55 k=0.57
909→184 k=0.80   422→247 k=0.44   247→ 98 k=0.72    98→58 k=0.50
```

**Median 0.58, range 0.44–0.80, and the loss is proportional** — absolute
loss runs 36 to 1198 ms with a standard deviation of 372, so it is not a
fixed cost per boundary.

**This refutes the "instrument over-reads" branch outright.** If the residual
were measured ~1.7× too large, correcting by the read value would overshoot
the truth by 70 % and the next reading would come back with the **opposite
sign**; the measured `k` would be ~1.7. We see 0.58 with same-sign decay,
eight times running. So the actuator genuinely delivers about 58 % of what it
computes, and the search is confined to the path between `admit_due` and the
air.

*It is also what the listener hears:* error × 0.45 per passage is five
passages to settle from 700 ms, which is exactly the "about five new
passages" reported by ear. At full delivery it would be one.

**Current hypothesis, instrumented and not yet tested.** `prepare_next`
*opens* the incoming passage; opening is not decoding, and `top_up` fills it
over the following ticks. An admission brought forward by N ms only moves the
sound forward by N if N ms of audio is ready to mix at that instant —
otherwise `mix` contributes nothing for it and sample 0 lands later, losing
the advance. The neighbouring path already knows this failure: 
`cut_ring_to_incoming` tops up before a cut because "a skip landing in that
window found 882 samples where it wanted 132,300" `[PI-CHR-075]`. An ordinary
boundary admission does not. It would be proportional, which matches.

If confirmed, the fix is a bounded top-up at admission and carries **no
overshoot risk**. The fallback — a learned gain of 1/k, like the join
learner — settles in ~2 passages but overshoots by up to 36 % on a high-`k`
transition, which is why it is the second choice rather than the first.

### The original framing



**Two independent actuators each deliver 55–80% of what they are asked.**

| actuator | asked → measured |
| :--- | :--- |
| boundary admission | 534→303, 232→129, 100→81 |
| join content position | 605→443, 680→540, 680→380 *(derived: the second seeding run's applied/residual pairs 605/162, 680/140, 680/300 — recorded in GUIDE028 §4)* |

Two coincidences is not the likely reading. Either every position correction
lands at ~0.7 of nominal, or the residual is measured ~1.4× too large. It
also explains why the join learner's estimate climbs: correcting by `X`
removes `0.75X`, so the estimate converges on `B/k` rather than `B`.

The discriminator must exercise the **real audio path**, not a reporting
field — the attempt that used `echo_delay_trim_ms` failed for exactly that
reason. A seek moves the audio to a known content position and is the next
instrument to try: seek 500 ms and the measured skew must move 500 ms.

## 5. Known faults, and what became of them

**`[GDE-ARC-065]` A node had two notions of its own delay: scheduling used
`measured + trim`, `air_position` used `measured` alone.** `[SPEC-DLY-010]` is
explicit that there is only one — *"not a second notion of delay layered on
the first"* — so this was a spec violation, not a judgement call. Latent while
the trim is zero, which it is on the whole fleet, and precisely why it
survived: the two models agreed. It bites the first time somebody calibrates
a node by ear, which is what that control is *for*. **Fixed 2026-09-19**, and
found by a probe that was looking for something else and failed.

**`[GDE-ARC-064]` A seek cuts the ring and was cleaning up after neither
consequence.** `seek_to` reaches the same `cut_ring_to_incoming` as `skip`,
so everything true of the ring after one is true after the other — but it
neither voided the basis `[GDE-ECHO-360]` nor dropped the sounding claim
`[GDE-ARC-059]`. A listener's seek left the node comparing a frame clock
across discarded audio. **Fixed 2026-09-19**, found while trying to use a
seek as a measuring instrument.

**`air_position` had never been executed by a test.** It declines unless the
clock reports `Hardware`, which needs a delay seen to vary, which no test
ring produced — so every test that called it passed without reaching a line
of it. That blind spot hid both `[GDE-ARC-059]` and `[GDE-ARC-065]`. A
`#[cfg(test)]` hook on the frame clock now lets a test drive a varying delay;
the first test through it reproduced the live probe's null result exactly.

**`[GDE-ARC-066]` A basis voided by a cut comes back when the audio is
genuinely sounding — and `seek` had no way back at all.** Introduced by
`[GDE-ARC-064]` and caught within the hour by a measurement that returned
*no samples*: `skip` is re-established by the `admit_due` inside it, but a
seek admits nothing, so giving seek the voiding it was missing left the node
publishing no anchor until its next passage boundary. Recovery now happens
where `advance_shown` decides a passage is really sounding, which is the
honest moment for either path. Only a cut is recovered from this way — an
underrun, a device reopen or a pause are about the frame clock itself and
are not mended by a passage becoming audible. **Fixed 2026-09-19.**

*The lesson worth keeping is the shape:* a fix that adds a `void` must say
where the matching `establish` comes from, and the two had been separated by
several hundred lines and one function call for long enough that the pairing
was invisible.

**`[GDE-ARC-067]` A cut voids the alignment plan, not just the basis.**
`skip` clears `live` and calls `admit_due`, which admits at once and would
spend a pending `echo_next_shift_ms` on a transition the listener just
created — becoming, in the "later" direction, silence inside the opening of
the new track. 19756 frames of it, measured. `seek` gets the same treatment
and for the added reason that it re-announces the *same* passage, so
`fs.corrected` would stop the follower re-measuring and a stale shift would
survive. **Fixed 2026-09-19.**

**`[GDE-ARC-068]` The origin is bounded even when the ceiling binds.**
`origin_ms = want - by_admission` discarded 1900 ms of a track for a 2 s
correction against a short pair — against a comment promising "never more
than 46 ms", with both tests of that invariant using a ceiling where the
clamp cannot engage. Capped at one block; the remainder is owed to the trim.
**Fixed 2026-09-19.**

**`[GDE-ARC-069]` A landing must not sample the position the join replaces.**
`act` schedules the start a lead into the future and falls through to
`correct_offset` on the same pass, so the first readings are of the old
position. `[GDE-ARC-059]` guards the window after the cut; this is the one
before it. **Fixed 2026-09-19** — a second reviewer confirmed the chosen
duration errs long rather than short, and that the worst case leaves ~11 s
of headroom against `Landing::PATIENCE`.

**`[GDE-ARC-070]` A wait spends the overlap before it spends silence.**
Emitting the whole correction as silence put a hole in the *outgoing*
passage: admission happens an overlap before that passage ends and the gap
branch mixes nothing while it drains, so a 500 ms wait against a 3 s overlap
left a half-second hole three seconds before the end of a track that then
carried on playing. Narrowing the overlap is the same instrument the
"earlier" direction uses, in reverse, and it costs nothing; spending it
first puts admission at the outgoing passage's true end, so any remaining
silence lands where a gap belongs. **Fixed 2026-09-19**, found by a reviewer
probing the mechanism rather than the tests.

**Still open: gap silence is counted as passage audio by every position
calculation.** `audible_ms` is `played_ms` less the *whole* ring depth, and
the silence adds to that depth while advancing no passage's `frames_mixed` —
so a node arming a gap under-reports its own position by the gap, for a
ring's depth. It reaches the published anchor, the UI, the resume point, and
`retire_finished`, which freezes the wrong value into `draining` and carries
it across the transition by wall clock. Confirmed by A/B probe: identical
drain, reported position advanced 500 ms without a gap and 0 ms with one.
`[GDE-ARC-070]` shrinks the exposure — a pair with overlap to narrow now
needs no silence at all — but does not remove it on this library's 5 ms
median. **Not fixed, and not to be fixed naively:** during the ring's depth
the node's own loop reads "already corrected", which is what stops it
double-correcting while the gap is in flight. Removing the under-report
without also deferring the loop would trade a reporting error for an
oscillation.

**Still open: a join's depth is computed in frames and delivered in
milliseconds.** `join_lead_frames` rounds to the nearest frame, deliberately —
its comment warns that truncating "loses in one direction, making every node a
frame or two shallow and therefore early" — and the caller then divides by
1000 and back, truncating up to 44 frames, always downward. About 1 ms,
systematic, in the direction the rounding exists to prevent. Small against the
25 ms anchor floor, which is why it is recorded rather than rushed: fixing it
means taking frames through `begin_skip_transition`, which is on the
real-time path.

**Still open: `check_docs.py` never reads the code** `[GOV-DOC-010]`. A tag
cited in Rust and defined in no document is undetectable, and two tag
collisions reached a commit this way.

---

## 6. How to keep this true

Supersede entries in place and date them. When a theory in §2 is refuted it
moves to §3 with the measurement that killed it — **it is not deleted**, and
that is the whole point of the register.

The recurring discipline behind most of §3 is one sentence from CLAUDE.md §6:
*verify the thing, not a proxy for it.* Every entry there is a proxy that was
trusted — a log line restating its input, a display promise read as an
observation, a knob assumed to be connected to the thing being measured.
