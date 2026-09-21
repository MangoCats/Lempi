# GUIDE019: How The Correction Loop Went Wrong

**Development Guidance — split from [GUIDE017](GUIDE017-echo-correction.md) 2026-09-18**

Three ways a control loop that looked right did not converge, kept for the
pattern rather than the particulars. Each was found by a listener or by an
independent instrument, and each had passed its own unit tests: they asserted
what the code intended rather than what reached the audio.

> **Related:** [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]` — what the loop is for · [LOG012](LOG012-phase-4-drift.md) `[LOG-P4-130]` — the drift being corrected · `tools/echo_skew.sh` — the instrument that settled each of these

---

**`[GDE-ECHO-351]` A log computed at a different point from the action is not
evidence of the action.** The coarse/fine split `[GDE-ECHO-347]` shipped with
the shift *consumed before the split that used it*, so the fine half was always
zero and **every late correction did nothing for four commits** -- while the
log line, which derived its own split a few lines earlier, dutifully reported
`500 ms into the passage` each time. A listener heard no change, the residual
did not move, and the log said the work was done.

The measurement that exposed it was not the log: `tools/echo_skew.sh` read
483, 544, 555, 512 ms across several minutes while corrections were "applied"
at every transition. A number that refuses to move under treatment is worth
more than a line that says treatment occurred.

The test written afterwards asserts on the **passage's actual origin** rather
than on a log line, and fails with `opened at 0 ms` against the old code. That
is the shape every test here should have taken: `[GDE-ECHO-343]`,
`[GDE-ECHO-344]` and this one all passed their unit tests while the system was
broken, because they asserted on what the code intended rather than on what
reached the audio.

**`[GDE-ECHO-346]` A fitted slope is the error in the correction, not the
drift, and applying it as an absolute settles at half.** The residual being
fitted is what remains *after* the current trim, so a loop that sends the fit
as the whole answer sets the trim to `R - A` when it already holds `A`. The
fixed point is `R/2` and the eigenvalue is -1: it sits at half-correction or
oscillates about it on the fit window's period.

At the +13.92 ppm measured for this pair `[LOG-P4-130]` that leaves ~7 ppm
uncorrected for ever -- **25 ms an hour**, which crosses the 40 ms offset
deadband every hour and a half, fires a shift, and clears the rate window,
starting the whole cycle again. That is a structural error and no amount of
threshold tuning reaches it.

Adding to what is applied is deadbeat in one window, and what remains is the
fit's own error rather than half the drift. The correction is then applied
**once per window and the window cleared**, because the plant's slope has just
stepped and a line across that step is not a slope. Shipped code sent it twice
a second.

**`[GDE-ECHO-344]` Two mechanisms, and the threshold between them must clear
the join bias.** `[GDE-ECHO-343]`'s guard was right about ordinary transitions
and wrong about everything else: suppressing the join whenever the node was
*coming* to that passage also removed the only thing that aligns a node
initially. After a restart a follower resumes its own programme, adopts the
master's queue, flows into the same passages -- and sits however far out it
happened to be, with 500 ms a transition to claw back. Measured at **4.8 s**,
which is forty minutes of nudging; the divergence got worse, not better.

Being *coming to a passage* is therefore only sufficient while the node is
roughly in the right place. Grossly out, it is playing the right passage at the
wrong moment, and only placing the first sample afresh fixes that.

The threshold between the two carries a constraint that is easy to miss: **it
must sit above the join bias.** A join lands a few hundred milliseconds to a
second late `[GDE-ECHO-342]`, so a threshold at or below that has every join
trigger the next one for ever. 1.5 s clears the worst observed bias with room,
so a join always lands *inside* the band and the nudges take it from there --
and is low enough that a node never faces forty minutes of nudging.
