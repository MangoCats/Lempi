# GUIDE021: Where The Echo Corrections Stop Short Of The Audio

**Development Guidance — written 2026-09-18 from a read of the built code.
Every finding checked against a failing test and fixed the same day; the
findings are left as written, with what each turned out to be recorded under
it `[GDE-ECHO-379]`.**

A review of the echo path as it is written, not as it is specified. Nothing
here was found by listening; everything was found by following a command from
the decision that issues it to the sample it changes, and noticing where the
two stop meeting.

**Every claim below held.** Each was reproduced by a test written before the
fix, and each of those tests failed with the message the finding predicted —
including the arithmetic, which is the part a read of code most easily gets
wrong. The two places the review was itself inaccurate are recorded in
[GUIDE022](GUIDE022-the-follower-nothing-builds.md) `[GDE-ECHO-379]`, and
neither changes a verdict.

The shape of every finding is the same, and it is the shape
[GUIDE019](GUIDE019-correction-faults.md) `[GDE-ECHO-351]` already named: the
decision is right, is tested, and is logged — and something downstream declines
it without saying so. Three were recorded there. The pattern did not stop at
three, which is why this is a separate document rather than three more entries.

> **Related:** [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]` — the loop being reviewed · [GUIDE019](GUIDE019-correction-faults.md) `[GDE-ECHO-351]` — the same pattern, found by listening · [GUIDE020](GUIDE020-control-propagation.md) `[GDE-ECHO-336]` — when a change takes effect · `tools/echo_skew.sh` — the instrument that settled the earlier three

---

## 1. What the built path does

**`[GDE-ECHO-371]` The loop decides in four commands and acts through two
knobs, and every finding below lives in the join between them.** Worth stating
plainly because no document does: the arithmetic is spread over three files and
the actuators over two, and which is which is not obvious from either end.

A follower's `act()` in `player/src/echo_client.rs` runs once per snapshot,
about twice a second. It estimates the master's clock offset by maximum over a
60 s window `[GDE-ECHO-366]`, optionally joins mid-passage, carries its own
anchor to the master's named sample to get a residual, feeds that residual to
an hour-long least-squares fit and a 120 s median, and finally acts on the
forward schedule. What leaves is `SetEchoRate` (ppm), `EchoShedOffset` (a
position debt in ms), `EchoCorrectNextStart` (a boundary shift in ms) and
`EchoStartAt` (a committed instant).

`player/src/engine/mod.rs` reduces all four to two actuators. `due_trim` sums
the rate ppm and the debt ppm into **one** interval and hands a single frame to
`apply_trim` in `player/src/mixer.rs` `[GDE-ECHO-349]`; `echo_split` divides a
boundary shift into a coarse admission nudge, spent through
`should_admit_nudged` in `player/core/src/queue.rs`, and a fine "open the passage
this far in" `[GDE-ECHO-347]`.

That division is sound, and the parts a sign error would hide in — the
symmetric `submit_at` `[GDE-ECHO-410]`, working in the master's frame, the
deadbeat trim `[GDE-ECHO-346]`, clearing the fit across a step, keeping air
position distinct from `audible_ms` — are right, and tested. The losses are all
at the far end.

---

## 2. Corrections that do not reach the audio

**`[GDE-ECHO-372]` The coarse knob spends an overlap this library does not
have, and the fine half then moves the node the wrong way.** The most
consequential finding here, and it makes the "later" direction of
`[GDE-ECHO-340]`'s offset correction worse than absent.

`should_admit_nudged` computes `want = (overlap + nudge).clamp(0, ceiling)`.
The overlap it is spending is `min(lead_out(A), lead_in(B))`, and
`player/core/src/queue.rs` records the measured library at the top of the file:
**lead-in median 5 ms**, deliberately, because the ramps exist to hide pops
rather than to cross-fade. So a typical pair has about **5 ms** of overlap to
spend, and `[GDE-ECHO-340]`'s claim that the knob is "symmetric by
construction — there is always a slightly smaller overlap" describes five
milliseconds of range, not a free parameter.

Follow a 100 ms *early* node, which must start its next passage 100 ms later.
`echo_split(-100)` returns `(-138, +38)`: back three whole 46 ms chunks, then
pull 38 ms of overshoot forward. The coarse half reaches
`want = (5 - 138).clamp(0, …)` = **0**, which delays admission by 5 ms and not
138. The fine half is applied unconditionally as the passage's origin. The node
therefore moves about **33 ms earlier** in answer to a request to move 100 ms
later, and the next measurement finds a larger error than the last.

Since `echo_split` returns a coarse zero for every *late* correction, the
coarse knob is now only ever called in the one direction its clamp destroys.

The test gap is precisely `[GDE-ECHO-351]`'s. `echo_split`'s own test asserts
that coarse and fine sum to the request — they do — and the only test that
follows a correction into the audio,
`a_late_correction_opens_the_next_passage_further_in`, uses `+120`, the
direction where coarse is zero and the clamp is never reached.

> **Confirmed and fixed.** The counterpart test at `-120` opened the next
> passage 18 ms in, against a transition that could not be delayed at all.
> `split_shift` now caps the backstep at whole chunks the pair's own overlap
> can afford and never hands the fine knob an overshoot the coarse half was
> clamped out of; `delivered_shift_ms` reads the same clamp `should_admit`
> does, so a test can assert across the boundary §5 says has no assertion
> across it. What the transition cannot take becomes a position debt for the
> frame trim, which is the direction `[GDE-ECHO-373]` had to be fixed for
> first. The split is also now taken **once**, before `queue.advance()`: it
> had been re-derived afterwards, which was harmless only while it ignored
> the queue.

**`[GDE-ECHO-373]` A duplicated frame is refused at exactly the block size the
engine always submits, and the debt is credited anyway.** So the endgame
`[GDE-ECHO-349]` is one-directional in a way nothing reports.

`scratch` is `2048 * channels` samples and `MIN_SUBMIT` is 4096, so on a stereo
device they are the same number. `mix_and_submit` takes
`want = min(room, scratch.len())` and returns early below `MIN_SUBMIT`, which
makes `want` **always exactly `scratch.len()`**; back-pressure keeps the stream
rings full, so `filled == want` outside the moments around a passage end.
`apply_trim`'s duplicate branch is guarded by `filled + ch > buf.len()` and
therefore refuses every time. The mixer's own test asserts that refusal —
`"nowhere to put it"` — with `filled` equal to the buffer length, which is the
production case; nobody connected the two numbers.

The consequence is that a node running **early** cannot be trimmed back at all,
and `EchoShedOffset` with a negative figure does nothing whatever. The second
half is worse than the first: `due_trim` returning `Some` is enough for
`mix_and_submit` to decrement `echo_debt_frames` *before* `apply_trim` sees it,
so a negative debt counts itself down to zero over the eight minutes
`[GDE-ECHO-349]` budgets, the log says frames are being trimmed, and no sample
anywhere has changed. The comment on that line — "the actuator delivered one
frame and the position moved by one frame" — is a statement of intent standing
in for a return value that was available.

> **Confirmed and fixed**, and it was worse than stated: the same refusal
> silenced the *rate* trim in the early direction too, so a node with a
> negative ppm was never trimmed either. The block is now `MIX_FRAMES` — a
> figure in **frames**, so it is 46 ms whatever the channel count — with one
> frame of headroom in `scratch` beyond it. `mix_and_submit` compares
> `apply_trim`'s return against what it passed in: a refusal credits no debt,
> does not restart the interval clock, and is reported once per episode. A
> mono device, incidentally, never submitted at all under the old arithmetic
> (2048 samples against a 4096 threshold); nothing in the fleet runs one.

**`[GDE-ECHO-374]` While the filter is measuring, the follower is not
following.** The residual block in `act()` returns early when the median is not
yet available, and again when it hands an endgame offset to the trim. Both
returns sit *above* the schedule handling, so for that pass the master's
forward schedule is not read at all.

The filter is cleared on every commanded start, every shift and every rejoin,
and needs 60 samples — about 30 seconds — to answer again. Ordinary passage
boundaries survive this, because the two nodes then name different passages and
the block is skipped entirely. What does not survive is a **seek**: it
re-announces the passage both nodes are already on `[GDE-ECHO-325]`, which is
exactly the condition that reaches the early return. A seek landing in that
window is dropped silently, and the next snapshot's schedule is identical, so
`Follower::on_state`'s dedup will not re-offer it.

> **Confirmed and fixed.** The residual block is now `correct_offset`, a
> function, so its two returns leave it rather than leaving `act`. Both cases
> are pinned by a test that hands the follower a schedule it cannot meet and
> asserts the node says so — a verdict that needs no library behind it, which
> is what made the follower testable at all.

---

## 3. Two numbers in two files that have to agree

**`[GDE-ECHO-375]` The mid-join budget and the engine's own measured
preparation are two models of one quantity, and they disagree by a factor of
two.** `[GDE-ECHO-342]` made the join's preparation measured rather than
assumed, which was right; it left a constant upstream that was sized for the
assumption.

`MID_JOIN_MARGIN` is 1 s. The offset term in the lead cancels inside
`join_mid_passage`, so that second is the entire budget. The engine then fires
early by `skip_lead_ms + echo_prep_ms` — 500 ms plus a measured figure that
starts at 400 and is explicitly permitted to reach `ECHO_PREP_MAX_MS`, **2000**.
Above about 500 ms of measured preparation every mid-join lands `TooLate` and is
dropped, and because `echo_prep_ms` is only updated by a join that actually
fires, the estimate never falls back. A node that pays for one slow seek stops
being able to join at all.

This disables the one mechanism `[GDE-ECHO-344]` identifies as the only thing
that aligns a node initially, and it does so quietly: the log says the passage
was missed, which reads like the master's fault.

> **Confirmed and fixed.** The engine publishes `start_lead_ms` —
> `skip_lead_ms + echo_prep_ms`, the one figure `fire_echo_start` subtracts —
> and the follower sizes its budget from that plus a second of slack, floored
> at the old constant and capped at 5 s, which is *above* the engine's own
> worst case of 4 s so the cap can never reintroduce the same fault at a
> different number. The threshold is 600 ms of preparation rather than 500:
> `ECHO_START_LATE_LIMIT` grants 100 ms. And the ratchet is broken at both
> ends — a start dropped as `TooLate` now decays `echo_prep_ms` towards the
> cold guess, because an estimate large enough to prevent every join could
> otherwise never be revised by one.

**`[GDE-ECHO-376]` "Would flowing do?" compares this node's passage ending
against another node's submission.** `[GDE-ECHO-353]` needed a test of whether
a suppressed join would land near the announced time, and the one written
compares `own_transition_in_ms` — time until *this* node's current passage ends
— against the time until the master's *submit* instant. Those differ by the
transition's overlap plus this node's presentation offset.

It passes today only because both terms are small against the 5 s
`FLOW_TOLERANCE`. A passage with the three-to-five-second lead-out the library
calls the rare-but-wanted case makes the comparison fail at every boundary,
which reinstates `[GDE-ECHO-343]` — a ring cut and the join bias at every
transition — and starves the rate fit besides, since a commanded start clears a
window that needs fifteen unbroken minutes to produce anything.

> **Confirmed and fixed.** Both sides are now this node's own air times:
> `own_next_starts_in_ms` subtracts the transition's overlap, and the master's
> instant has this node's presentation offset added back to turn a submit
> time into a sound time. A five-second lead-out meeting a five-second lead-in
> failed the old test by 355 ms of margin and now matches exactly; the case
> the test exists to separate — a skip that moved the master's boundary by
> minutes — is asserted alongside it, unchanged.

---

## 4. What the module carries and no longer calls

**`[GDE-ECHO-377]` Three residual formulas, one of them live, and the other two
wrong for the caller that would reach them.** `residual_ns` subtracts two
timestamps, which `[GDE-ECHO-345]` records as the defect that measured the
network; `Follower::trim_for` carries the reading to the master's sample but
does no clock translation `[GDE-ECHO-366]`; the live one is written inline in
`act()` and does both. Only `echoprobe` still calls the second, so the
difference is invisible until someone reuses the name that sounds right.

The same drift has left `reconcile_queue`, `rejoin_action` and `residual_ns`
reachable only from tests, and `Follower`'s `basis`, `deadband`,
`min_trim_interval` and `last_trim` set up per connection and consulted by
nothing — the follower establishes a basis once and comments that it does not
trim. `placement()` is also uncalled, but honestly so: `[GDE-ECHO-342]` says it
is the durable fix and says why it was not taken. The rest carry no such note,
and a reader cannot tell a deferred design from an abandoned one.

> **Confirmed; one deleted, the rest given the note they lacked.**
> `residual_ns` is gone — it was the only one that was a *trap* rather than
> merely quiet, and it had no caller outside its own tests, which are
> rewritten against the live formula. `trim_for`, `reconcile_queue`,
> `rejoin_action` and `placement` now each say who calls them, why they are
> kept, and what an adopter must add first — for `trim_for`, the clock
> translation, without which it measures the two nodes' clocks rather than
> their alignment `[GDE-ECHO-366]`.

---

## 5. What to take from it

**`[GDE-ECHO-378]` A test that stops at the decision cannot see an actuator
decline it, and every actuator here declines silently.** `should_admit_nudged`
clamps, `apply_trim` refuses, `act()` returns early: three different mechanisms,
none of which reports anything, each sitting one call below a function whose
tests pass. That is the same finding as `[GDE-ECHO-351]`, arrived at from the
other direction — it was found there by an instrument and here by reading, and
the shared cause is that the boundary between deciding and acting is the only
place in this subsystem with no assertion across it.

The cheap general fix is for the three to return what they did rather than
`()`, and for the caller to log a refusal. `[GDE-ECHO-373]`'s debt would then
be credited by the frame that moved rather than by the intention to move one.

All five were taken, in that order — the clamped coarse knob `[GDE-ECHO-372]`
needing the refused duplicate `[GDE-ECHO-373]` fixed first, since the remainder
it hands on has nowhere else to go.

**The governance note is settled.** `[GDE-ECHO-355]` was defined twice — the
fitted-slope rule in GUIDE017 and the reachable-announcement rule in GUIDE020.
Every citation in the code meant GUIDE020's and GUIDE017's definition had
none, so GUIDE017's became `[GDE-ECHO-356]` and nothing had to follow it.

**Three things this review could not see**, because a read of source cannot
see what is not compiled, are in
[GUIDE022](GUIDE022-the-follower-nothing-builds.md) `[GDE-ECHO-379]`: two
small corrections to the findings above, and the fact that the whole of
`echo_client.rs` sat behind a cargo feature nothing in the tree turned on — so
§2's and §3's faults were never once *built* by a test run, and one appliance
carried a follower while another did not because the difference was an
environment variable. The feature is now default. A third instance of the
same shape turned up while shipping, on the wire this time
`[GDE-ECHO-385]`.
