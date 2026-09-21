# GUIDE028: Learning Where A Join Lands

**Development Guidance — written 2026-09-19, after the calculable part of the
join error was removed and a larger part was still there**

[GUIDE025](GUIDE025-what-holds-the-alignment-back.md) `[GDE-ARC-058]` gave a
commanded start its depth from `placement()` instead of a constant, which
removed a measured 386 ms — the first join under the new code computed a depth
of **886 ms** where the constant would have used **500**, so that is the error
the constant was making on that join. (The **363 ms** figure quoted in the code
is a different measurement of the same fault: `echo_prep_ms` assuming 400 ms
against a real 37. The two are not the same number and neither is a typo for
the other.) What remained was **530 ms and 712 ms** on the two
joins that could then be measured — against a steady state of **1 ms**. This
is what was built about that, and it does not claim to know the cause.

> **Related:** [GUIDE025](GUIDE025-what-holds-the-alignment-back.md) `[GDE-ARC-058]` — the calculable half, removed · [GUIDE027](GUIDE027-what-the-transition-achieved.md) `[GDE-ARC-059]` — why the first post-join reading is now honest · [GOV002](GOV002-sources-of-truth.md) `[GOV-SRC-040]` — rank by measurement · [GUIDE029](GUIDE029-what-is-known-about-alignment.md) `[GDE-ARC-063]` — the standing model and the refuted register

---

## 1. Remove what is calculable, then learn what is left

**`[GDE-ARC-061]` A join is placed from the master's *reported* position, and
the report is not the truth. What each join lands at is recorded, and the
next join is aimed that much further on.**

The order mattered and was deliberate. `echo_prep_ms` was a self-calibrating
constant standing in for a quantity — the preparation before a cut — that
could be computed exactly, and computing it is strictly better than learning
it: **learning a bias a correct calculation would remove is fitting a constant
to a bug.** So `placement()` came first.

What is left is not calculable, because nothing in either node measures its
own *absolute* air position. Both report a position derived the same way, and
a common lag between report and truth is invisible to every instrument here —
it cancels in the difference that the residual loop and `echo_skew.py` both
take. See §4.

So it is learned. Each join's landing error is measured once and recorded;
the median of what this node has seen is added to the next join's target
position.

## 2. The middle, not the mean, and not the last one

`JoinBias` keeps the last **9** landings and returns their **median**.

That shape is chosen against a specific failure. Three joins in three seconds
once drove `echo_prep_ms` 295 → 162 → 195 → 158 → 124 `[GDE-ARC-044]` — the
compensation thrashed by the storm it was meant to absorb, because an
exponential average is *defined* by its willingness to be moved by the most
recent reading. A median over a window cannot be dragged that way: one wild
landing among steady ones moves the figure by one rank, and the test pins
exactly that.

Three further rules, each for a reason:

- **Nothing until three samples.** One landing is not a distribution. Waiting
  for the full window instead would leave a fresh node uncorrected for nine
  joins, which on a quiet follower is weeks.
- **Beyond ±5 s is not a landing.** That is a stale schedule or an unsettled
  clock, not a placement error, and averaging it in teaches the next join a
  lie.
- **Persisted, and written the moment it changes.** This is the whole of why
  `echo_prep_ms` never converged: it reset to its cold guess at every restart
  `[GDE-ARC-058]`. Joins are rare — three in seven hours on a quiet
  follower — so a sample lost to a restart can be a day's learning.

## 3. Measuring a landing without measuring the loop

The reading has to be taken in a narrow window: after the join has actually
sounded, and before anything else moves the node.

- **It is possible at all only since `[GDE-ARC-059]`.** The first anchor after
  a cut used to be a promise about the display rather than an observation of
  the air, so the first thing the filter saw after a join was fiction.
- **Its window is deliberately smaller than the correction loop's.**
  `RESIDUAL_MIN_SAMPLES` is 60 over 120 s, sized to resolve single-digit
  milliseconds for a loop that must not hunt. A landing is several hundred
  milliseconds against ~25 ms of anchor scatter `[GDE-ARC-047]`, so **11
  samples** resolve it to about 8 ms — six seconds at the snapshot cadence.
  Waiting two minutes would mean waiting through the corrections that make
  the reading meaningless.
- **It is abandoned rather than guessed at.** A boundary correction, a trim,
  or a clock step while a landing is being watched discards it and says so.
  A join that cannot be measured within 25 s is not learned from.
- **The join clears `corrected`, `filtered` and `rate`.** The first two were
  describing a position the node no longer holds. `corrected` matters most:
  it gates the residual block on "already corrected this master passage", and
  left set it would have stopped the node measuring anything at all until the
  master moved on — the measurement would have silently never happened.

## 4. Seeding it, and the bug that found

Five joins were forced on `lp3-wifi`, ninety seconds apart, and the learner
was read back:

| join | correction applied | landed |
| ---: | ---: | ---: |
| 1 | 0 | +611 ms |
| 2 | 0 | +687 ms |
| 3 | 0 | +893 ms |
| 4 | **+687** | **+133 ms** |
| 5 | **+687** | **+174 ms** |

The mechanism works: the median of the first three cut the next two from
about 690 ms to about 150, a four- to five-fold improvement on the quantity
a listener would hear as a botched join.

**And the median then moved the wrong way, 687 → 611.** A landing measures
what is *left* of the bias, not the bias. A join aimed 687 ms on that still
lands 133 ms behind is evidence the bias is **820**; filing 133 tells the
next join the error shrank, when all that happened is that it was partly
corrected. The window then holds corrected and uncorrected samples together,
the median falls back towards whichever there are more of, and the loop hunts
instead of settling.

This is `[GDE-ECHO-346]` exactly — and that entry is already written down, in
the same file, about the rate trim:

> **Added to what is already applied, not substituted for it.** The residual
> being fitted is what remains AFTER the current trim, so the slope is the
> error in the correction rather than the drift. Sending it as an absolute
> sets the trim to `R - A` when it already holds `A`: the fixed point is half
> the drift and the map oscillates about it.

A documented lesson, repeated one function away, by someone who had read it.
The fix is to record `applied + residual`, and the test pins the property
that matters: a join that lands perfectly while aiming 800 must **confirm**
800, not erase it.

## 5. What this does not know

**The cause.** The leading hypothesis is that both nodes' reported air
position lags the truth by a common amount, which would produce exactly this
signature — steady state at zero, joins out by that amount — and would
explain why the join bias has been described as "a few hundred milliseconds"
since `[GDE-ECHO-342]` without ever being located. It is a hypothesis: four
proposed mechanisms in this series died under measurement, and it is recorded
here to be tested, not believed.

It predicts two things worth checking. The bias should be about equal on both
directions of a master/follower swap; and it should be close to the amount by
which a node's reported position lags an *external* reference, which would
need a loopback or acoustic measurement that does not exist yet.

**Whether it is one number.** The bias mixes this node's own pipeline with
whatever its master's reported position is worth, and only the first half
travels. One bias per node is stored, and it is **forgotten when the master
changes** — re-setting the same host is not a change, so a reconnect keeps
the history. Followers swap master rarely, so relearning then is cheaper than
carrying a number quietly about somebody else.

---

## 6. What to take from it

**`[GDE-ARC-062]` A self-calibrating constant is the right answer only for
the part that cannot be calculated, and it must be persisted or it is not
calibration at all.** `echo_prep_ms` failed both tests: it stood in for a
computable quantity, and it forgot itself at every restart, converging by
quarters over joins that happen a few times a day. It was assuming 400 ms
against a real 33–37 for as long as anyone had been watching.

The replacement is the same idea applied where it belongs — after the
arithmetic has taken everything it can, on a quantity nothing here can
compute, with the result written down.
