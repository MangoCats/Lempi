# GUIDE027: What The Transition Achieved

**Development Guidance — written 2026-09-19, from a log line that could not
be wrong**

[GUIDE026](GUIDE026-placing-the-passage-exactly.md) `[GDE-ARC-051]` gave the
passage transition an uncapped actuator in both directions, and the engine
duly reported placing every correction in full. The field measurement
disagreed with the engine, repeatedly, and the engine had no way to be wrong.

> **Related:** [GUIDE026](GUIDE026-placing-the-passage-exactly.md) `[GDE-ARC-051]` — the actuator · [GUIDE025](GUIDE025-what-holds-the-alignment-back.md) `[GDE-ARC-043]` — the ratio this is a case of · [GOV002](GOV002-sources-of-truth.md) `[GOV-SRC-040]` — rank by measurement · [GUIDE029](GUIDE029-what-is-known-about-alignment.md) `[GDE-ARC-063]` — the standing model and the refuted register

---

## 1. A claim that restates its own input

**`[GDE-ARC-057]` `should_admit_nudged` fires on `remaining <= overlap +
nudge`, so it can only act at or *after* the instant it wants. A correction
that arrives with less of the outgoing passage left than it asked to spend is
truncated to whatever was left — and the engine computed its success line from
the request, so it reported the full figure either way.**

The line read:

```
echo-offset: passage 9914 asked for 565 ms earlier; placed by 552 ms of
overlap + 13 ms origin + 0 ms silence = 565 ms, 0 ms left to the frame trim
```

Every term after `placed by` comes from `spend_overlap_ms` applied to the
request. Nothing in it was observed. `0 ms left to the frame trim` is not a
finding that the correction landed; it is arithmetic restating that 552 + 13
= 565.

**Which end was wrong could not be established from inside the engine**,
because the engine held no measurement of the achieved figure to compare
against — the classic shape of `[GDE-ARC-043]`, and the specific failure
CLAUDE.md §6 exists to prevent: verify the thing, not a proxy for it.

*An earlier revision of this section carried a table here showing deliveries
of 6 % to 63 % against the ask, and used it to argue that admission was being
badly truncated. **That table was wrong and is withdrawn.*** It was built by
pairing correction lines from the follower's journal — which displays
`+01:00` — against `echo_skew.py` samples stamped in EDT, across the ~15 s
ring between admission and sound. The pairing was off, and the conclusion
drawn from it did not survive the instrumentation written to test it, which
is the right order for that to happen in but not a reason to have published
the figure.

## 2. What was added

`achieved_shift_ms` takes the outgoing passage's **real remaining play time at
the instant admission fired**, captured in `admit_due` before `advance()`
removes the pair it was measured from. Without a nudge that instant is the
overlap, so the shift admission really bought is the difference:

```
achieved = (remaining − overlap) + origin − gap
```

`delivered_shift_ms` stays, and stays the right tool for *testing the
decision*: it reads the same clamp the actuator reads, so a test can follow a
correction from decision to intent. What it cannot do is disagree with the
request, which is exactly what was needed.

Three consequences, all of them the point:

- **The shortfall reaches the trim.** `echo_debt_frames` now takes
  `asked − achieved` rather than `asked − intended`, so a truncated
  correction is repaid instead of silently lost.
- **The log carries both numbers**, plus the remaining time and overlap it
  judged them from, so the next disagreement is diagnosable from one line.
- **A clean transition is not exact either, and now says so.** The engine's
  own test found a residue of about 2 ms — admission firing one evaluation
  late. Bounded by one mix block, 46 ms, and previously rounded away by the
  claim rather than by any rounding.

## 3. What it measured, and the gap that is left

Deployed and read back the same day. **Admission is not being truncated:**

```
asked 547 ms, achieved 534 (498 ms of the outgoing passage left, overlap 5)
asked 239 ms, achieved 232 (225 ms left, overlap 2)
asked 108 ms, achieved 100 ( 84 ms left, overlap 0)
```

Shortfalls of 7–13 ms, every one inside a single mix block, and the outgoing
passage had hundreds of milliseconds left in each case. The transition does
what it is asked to about 97 % of the time it is asked for it, and
`[GDE-ARC-057]`'s truncation is real but small.

**A gap remains and it is not this one.** Pairing those same three
corrections against the skew steps measured across the same transitions, in
one run and one clock:

| asked | achieved (engine) | skew step (instrument) |
| ---: | ---: | ---: |
| 547 ms | 534 ms | 303 ms |
| 239 ms | 232 ms | 129 ms |
| 108 ms | 100 ms | 81 ms |

So the engine moves the passage's start by very nearly the full amount, and
the measured alignment improves by roughly 55–80 % of that. The direction is
right every time and the loop converges cleanly — **+2128 → +540 → +239 →
+110 → +29 → +1 → −6 → 0 ms** across the eight transitions after a restart,
settling at `p50 −2 ms, |p90| 29` — but a correction that moves the start
534 ms should reduce the skew by 534, not 303.

**No mechanism is proposed here.** Two candidate explanations in this series
have already failed under measurement — a truncating admission, and bursty
mixing overshooting the threshold — and a third guess is worth less than the
next reading. What would settle it is comparing the passage's *air* start
time against the master's published `sound_at` for the same passage directly,
rather than inferring the step from per-passage skew medians.

It is also worth stating plainly that this gap costs the listener very
little: the endgame trim closes what the boundary leaves, and after six hours
of unattended running the pair measured **p50 1 ms, p90 24 ms** with zero
boundary corrections and zero ring cuts `[GDE-ARC-047]`.

## 4. The same fault in the anchor

**`[GDE-ARC-059]` After a cut, `shown` is a promise about the display and
`air_position` reported it as an observation of the air.**

The same shape as §1, found while chasing something else entirely and worth
recording with the wrong turn intact.

The premise was that a join **blinds** the follower: `Voided::Skip` says the
ring was cut and counted frames were never heard, and `Basis::establish` is
documented as reachable only from a clean passage boundary `[GDE-ECHO-360]`.
On that reading the loop could not correct for four to six minutes after a
join, and the landing error could not be measured at all.

**It is not so, and the code says it plainly.** `skip` calls `admit_due`, and
`admit_due` establishes the basis. It is valid again before `skip` returns. A
test written to prove the blinding failed on its second line, which is the
only reason the reading was corrected rather than published.

What is actually wrong is the mirror image. `skip` sets `shown` to the
incoming passage immediately, so the button stays honest `[REQ-AUD-164]` —
but that passage does not sound until the lead has drained, a computed
**886 ms** on the join measured here. Through that window `air_position`
answered *this passage, at its origin, now*: a position the node had not
reached. It is published as this node's anchor, so every follower of it sees
it; and it is the first thing this node's own residual filter sees after a
join clears the filter.

`advance_shown` already decides when a passage is genuinely sounding — its
first sample has left the ring — so the fix is to record which branch set
`shown` and decline in `air_position` while it is a promise. `None` is what
that function says everywhere else when it has no honest answer
`[GOV-SRC-040]`.

*Scope, stated because it is narrow:* only a cut sets the flag false. A
passage still draining after the mixer has finished with it keeps it, which
is correct — that one really is sounding.

*Coverage, stated because it is partial:* the flag's semantics are pinned by
a test; the guard that uses it is one line and is **not** covered, because
`air_position` requires `Timestamps::Hardware` and a test ring reports none,
so any test calling it would pass without reaching the guard. That is the
`[GDE-ARC-055]` trap — a green test whose subject was never executed — and
naming it here is preferable to a test that appears to cover it.
