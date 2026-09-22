# LOG011: The Lead Is a Ring, and What That Constrains

**Experiment Record — 2026-09-17**

Twenty-five consecutive passage admissions on `bose`, to test a load-bearing
claim in `[GDE-ECHO-310]`: that the schedule's lead leaves ample room for any
node's presentation offset. It does, but not for the reason the guide gives,
and the real reason constrains which node may be master.

> **Related:** [GUIDE016](GUIDE016-echo-playback-plan-build.md) `[GDE-ECHO-310]` — the claim under test · [GUIDE010](GUIDE010-echo-node-capabilities.md) `[GDE-ECHO-410]` — the offset arithmetic · [LOG009](LOG009-cpal-upgrade.md) `[LOG-CPAL-060]` — the two nodes' measured offsets

---

## 1. What was measured

`bose` logs `echo-schedule: passage=… sound_at=… rate=…` at every admission.
`sound_at` is absolute, so the lead is `sound_at` minus the journal's own
timestamp for that line — no extra instrument, and six hours of it already on
disk.

**`[LOG-ECHO-010]` The first reading was 0.9 s too generous, and the instrument
was my own `awk`.** It split the journal timestamp on `.` and used the integer
seconds, flooring it — which adds a uniform 0–1 s to every lead. The
distribution gave it away: leads spread evenly across 15.04–15.97 s, and a
*uniform* spread is the signature of a quantisation artefact, not of queueing
delay, which is one-sided with a floor. This is `[LOG-FIX-010]`'s lesson in
miniature — the first number a new instrument produces deserves a check against
what its error distribution ought to look like.

## 2. Result

| | |
| :--- | ---: |
| admissions | 25 |
| mean lead | **15.0028 s** |
| max lead | 15.0459 s |
| min lead | 14.9326 s |
| ring capacity, `BUFFER_FRAMES * 2` | 15.0000 s |
| `bose` device delay `[LOG-CPAL-060]` | 0.0463 s |
| **predicted ceiling** | **15.0463 s** |

**`[LOG-ECHO-020]` The lead is the output ring, at capacity, plus the device
delay — 15.046 s, with the maximum observed 0.4 ms under it.** The ring is
therefore full at every admission, as the mixer's fill-to-capacity policy
intends. Shortfalls below the ceiling are the ring being a few thousand frames
short at that instant; journald latency can only ever make the figure *smaller*,
so the maximum is the estimator that matters here, not the mean.

## 3. What the lead is actually made of

The guide sizes the lead against the *offset* — 15 s against a few hundred ms of
A2DP, "roughly sixty-fold". That comparison is sound, but only against
**network and scheduling lateness**, which spends the follower's ring depth one
second per second. There is about 15 s of it to spend, and the margin is as
large as claimed.

It is not the comparison that governs the offset, because a follower's own ring
is not a cost it pays in wall-clock time. **A ring drains in real time; it does
not fill that way.** A follower can deepen its ring at decode speed, so the
question is never "can it wait long enough" but "can it place sample 0 deep
enough".

**`[LOG-ECHO-030]` In steady state, ring depth is the only knob, and it caps
the fleet's common submit-to-air total.** *The second half of this entry as
first written -- that the cap constrains which node may be master -- is wrong
and is superseded by `[LOG-ECHO-035]` the same day.* Admission is not a free choice once a programme is
running: the ring is full of the previous passage and sample 0 goes in where
that passage ends. Two nodes admitting at the same point of the same programme
differ in air time by exactly their device delays, permanently — and waiting
cannot correct it, because waiting only makes a node later. Running the rings at
different depths can:

    depth(node) = Total - device_delay(node),   Total common to the fleet
    depth <= capacity   =>   Total <= capacity + min(device_delay)

So the node running the fullest ring is the one with the **smallest** device
delay. With `bose` at 46 ms and `lempi02w` at 355 ms, the cap is 15.046 s, `bose`
runs full and `lempi02w` runs 13 633 frames (309 ms) shallower.

**`[LOG-ECHO-035]` That cap is a property of the fleet, not a constraint on who
may be master.** The derivation above holds `depth <= capacity` for *every*
node, master included, so it yields a bound on the common total and nothing
about the announcing role. Reading it as a rule about the master conflated *who
announces* with *whose ring runs full*, which are independent.

What is actually defective in the reversed pairing is the **announcement**:
`schedule_for_admission` derives `sound_at` from the announcer's own full ring,
hard-wiring `Total = capacity + device_delay(announcer)`. A `lempi02w` announcing
that way asks for 15.355 s, which `bose` cannot reach, and `bose` would play
309 ms early on every passage forever -- looking like a drift problem rather
than a misconfigured total, which is why `placement` reports `TooShallow`
instead of clamping. Announcing the fleet's cap instead makes the same pair work
with the same two depths, whoever announces; there is a test to that effect.

The preference in fact runs the *other* way `[GDE-ECHO-315]`. A follower's
working room is `depth = Total - device_delay`, so the smaller a node's delay
the more ring it has to manoeuvre in. Putting the fleet's **longest** delay on
the master gives every follower the most room, and costs the master the least,
because the master originates the change and learns of it first. It is also the
safer default for the transient of `[GDE-ECHO-325]`: a longest-delay master's
naive "as soon as I can" target is reachable by every follower, where a
shortest-delay master's is not.

## 4. A trap in the existing arithmetic

**`[LOG-ECHO-040]` `submit_at` returns the instant sample 0 must reach the
*device*, and admitting the passage at that instant is wrong by a whole ring.**
The name and the type invite it — `Follow::StartAt { at }` reads as "start the
passage at this wall time" — but a passage admitted to the mixer at `at` puts
sample 0 behind 15.0 s of ring and sounds 15.0 s late.

`placement()` is the missing half: it converts a schedule into the depth sample 0
must sit at, and names the two ways that can fail rather than returning a number
that reads like an instruction `[GOV-SRC-040]`. Four tests cover it, including
the round-trip that exposed a systematic one-frame bias — `sound_at` is built
from frames divided into nanoseconds, and truncating on the way back made every
node a frame shallow, hence early. 23 µs is inaudible; a consistent sign is
still worth not having.

## 5. Open

**`[LOG-ECHO-050]` Built 2026-09-18.** *The lever below now exists:
`Command::SetEchoDepth` takes this node's calibrated offset and the fleet's
smallest, and the mixer holds the ring that far short of capacity. The cost is
one constant subtracted from the free-space figure it already had — `free`
is `capacity - buffered`, so `free - shortfall` is exactly
`target - buffered` — with no extra read of the ring. The one trap was the
cached free figure: refreshing it on the raw value rather than the usable one
lets a capped ring stall forever, since the cache only updates after a submit
that the cap prevents.* The original entry follows.

**Nothing yet runs its ring at a commanded depth.** `placement`
computes the target and is tested against it, but the mixer has no way to be
told "fill to 13 633 frames short of capacity" — it fills to capacity. Phase 4
needs that lever before a follower can hold sync across a passage boundary,
and it is the smallest remaining piece of engine work that echo depends on.

**`[LOG-ECHO-070]` The announcement was never the defect — the missing depth
control was.** This entry first said `schedule_for_admission` announces off its
own *full* ring and must be taught the fleet's cap. Reading the call site
corrected that: it is passed `out_buffered_frames()`, the ring's **actual**
depth, so it already announces whatever the node really does. A node holding its
ring short therefore announces the fleet's total with no change to the function
at all, and the whole fix is `[LOG-ECHO-050]`'s lever.

What remains is supplying the two numbers. They are **calibrated** offsets
`[GDE-ECHO-430]`, not live `delay` readings, and that is not fastidiousness:
`lempiplay3` reports a delay wandering 9.84 ms while its sound holds to 1.72 µs
`[LOG-P4-100]`, so driving ring depth from the live figure would manufacture ten
milliseconds of movement that is not otherwise there. `--echo-offset-frames`
and `--echo-fleet-min-frames` accept them for now; the settings page
`[SPEC-DLY-120]` is where they belong, and `lempiplay3`'s own calibrated figure
is not yet established because its reported delay is a distribution rather than
a number `[SPEC-DLY-040]`.

**`[LOG-ECHO-060]` The 15.0 s capacity is a constant, not a measurement.** Both
nodes take it from `BUFFER_FRAMES`, so the fleet-wide `Total` happens to be
uniform. A node built with a different constant would break the arithmetic
silently, because nothing publishes its capacity. If node shapes ever diverge,
capacity belongs on the wire beside the offset.
