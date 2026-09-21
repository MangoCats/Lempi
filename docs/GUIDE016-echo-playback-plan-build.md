# GUIDE016: Echo Playback — Plan, Phases 3-6

**Development Guidance — split from [GUIDE009](GUIDE009-echo-playback-plan.md) 2026-09-13**

The phases that **build**, continuing [GUIDE009](GUIDE009-echo-playback-plan.md),
which holds the gating and the three measurement phases. Those are met
`[GDE-ECHO-520]`, so this is the live half of the plan and the half that grows.

> **Related:** [GUIDE009](GUIDE009-echo-playback-plan.md) `[GDE-ECHO-250]` — the gating and phases 0-2 · [GUIDE014](GUIDE014-echo-phase-status.md) `[GDE-ECHO-520]` — where each phase actually stands · [GUIDE010](GUIDE010-echo-node-capabilities.md) `[GDE-ECHO-450]` — the node roster these phases schedule against · [REQ003](spec/REQ003-audio-playback.md) — the buffer-depth rule every correction obeys · [SPEC011](spec/SPEC011-audio-path-supervisor.md) — the device lifecycle that invalidates a frame clock

---

## 1. Phase 3 — The wire

**`[GDE-ECHO-310]` A master broadcasts two kinds of anchor; it sends no
commands.** One kind is not enough, because a backward-looking statement cannot
serve a node whose presentation offset exceeds the master's `[GDE-ECHO-410]`:
such a node would have needed to submit *before the master did*, and learns so
too late to act.

**The schedule, forward-looking.** *Passage P, sample 0, will be heard at wall
time T.* Emitted when the master admits P to the mixer — which by
`[REQ-AUD-160]` and the ring's depth is roughly **15 s before anyone hears it**,
against an A2DP buffer of at most a few hundred ms. That lead is measured at
**15.046 s** and is the output ring at capacity plus the device delay
`[LOG-ECHO-020]`; it exists only because an echo node holds the file locally and
knows the queue `[GDE-ECHO-420]`. It buys tolerance of **network and scheduling
lateness**, which spends ring depth a second per second — sixty-fold is right
for that. It does *not* make an arbitrary offset compensable: in steady state a
follower's ring is full of the previous passage, so offsets are absorbed by
running the ring at different depths, and `depth <= capacity` caps the fleet's
common submit-to-air total at `capacity + min(device delay)` `[LOG-ECHO-030]`.
Each node schedules its own submission at `T − presentation_offset`.

**The drift anchor, backward-looking.** *Sample N of P was heard at T*, repeated
periodically, carrying the master's measured ppm. Computed from `audible_ms`,
never `played_ms`, because those differ by the ring's depth and only one of them
describes sound `[REQ-AUD-164]`.

Cadence follows Phase 0's measured stability; twice a second for the drift
anchor, matching the existing snapshot rate, costs nothing new because it rides
the WebSocket the browser snapshot already uses. The schedule is emitted on
admission rather than on a clock.

**The binding constraint is lead time against *lateness*, and it is never
close.** A node can only start as early as its foreknowledge allows; with ~15 s
of lead against a worst-case spread of a few hundred ms, the margin is roughly
sixty-fold. A design that instead treated the master's offset as zero would have
worked in one direction only.

**`[GDE-ECHO-315]` Any node may be master, and the best default is the node with
the fleet's *longest* device delay.** `[LOG-ECHO-035]` shows the depth cap is a
fleet property and says nothing about the announcing role. Two things then argue
for the slowest device announcing. A follower's working room is
`depth = Total − device_delay`, so the shorter a node's delay the more ring it
has to manoeuvre in; putting the longest delay on the master leaves every
follower the most room, and costs the master the least, since the master
originates each change and learns of it first. And for the resync of
`[GDE-ECHO-325]`, a longest-delay master's naive *as soon as I can* target is by
construction reachable by every follower — a shortest-delay master's is not.

What a master must **not** do is derive its announcement from its own full ring.
That hard-wires `Total = capacity + its own delay`, which any shorter-delay
follower then cannot reach. `schedule_for_admission` does exactly this today and
must take the fleet's cap instead `[LOG-ECHO-070]`.

**`[GDE-ECHO-325]` A planned transition holds sync; a skip or seek buys it back
going forward, and may be briefly out.** The two cases are not the same promise
and should not be given the same mechanism.

A passage-to-passage transition that the queue already knew about is *planned*:
every node has the file, the lead, and the depth arithmetic, so synchrony is
maintained straight through it with no audible event.

A skip or seek is user input arriving with no lead at all. Here the fleet is
permitted a bounded interval of asynchrony — **nominally up to 5 s** — during
which no claim is made about what any node is playing. What each node targets is
synchrony *from a stated point onward*: the master announces a target of the
form *passage P, offset 3000 ms, at wall time T*, picks the soonest `T` it can
itself meet, and fills toward it; every follower does the same against the same
target. Nothing coordinates the journey, only the destination. This is why the
schedule carries a **start sample** rather than implying sample 0 — a seek is
the same message with a non-zero offset and a nearer `T`, not a new message
type.

*Built 2026-09-18.* `Schedule.start_sample` rides the wire, the master fills it
from the resume offset it was already about to use, and a follower reports it.
Two consequences were not obvious until it was written. The follower's
act-once key had to move from `passage_id` to the **whole schedule**, because a
seek re-announces the passage already playing and a key on the id alone
discards exactly the message a seek exists to deliver — which is also the
natural reading of `[GDE-ECHO-320]`, where every message is absolute and any
difference is a new instruction. And the master must take its resume offset
*before* building the schedule: announcing sample 0 for a passage that begins
three minutes in tells every follower to play the wrong audio at the right
time.

Making any of that actually reach another node took six findings of its own,
every one of them about *when* rather than about delivery:
[GUIDE020](GUIDE020-control-propagation.md) `[GDE-ECHO-336]`.

**`[GDE-ECHO-335]` A queue edit outside the ring's window has no synchrony
consequence at all.** Reordering, inserting or removing anything that is neither
playing now nor already scheduled within the ring's depth arrives in time to be
absorbed by the ordinary planned transition, and is therefore invisible to
synchrony. Only an edit reaching *into* the ring window — which is a skip in
everything but name — falls under `[GDE-ECHO-325]`. This keeps the common case
of a user rearranging what plays next entirely off the synchrony path, and is
the invariant `reconcile_queue` `[GDE-ECHO-500]` has to preserve.

**`[GDE-ECHO-320]` Every message is absolute, idempotent and independently
sufficient, so a lost one is harmless.** No deltas, no sequence-dependent state,
no acknowledgement, no retransmission. An echo node that misses a message simply
uses the next; one that receives a stale message discards it by its own
timestamp. This is what lets `[GDE-ECHO-020]`'s stateless master hold — and it
is the property that makes the transport choice uninteresting, which is the
point.

---

## 2. Phase 4 — Echo, measured but uncorrected

**`[GDE-ECHO-330]` Join only at a passage start, using the seek path that
already exists.** An echo node receiving an anchor for a passage it is not
playing computes the offset it would need and opens that passage there, through
the same `resume_at` path a restart already uses `[REQ-AUD-140]`. Mid-passage
joining was deferred here; `[GDE-ECHO-510]`'s rejoin case promotes it to
required, and it is skip plus a seek, both of which already exist.

Then measure and do not correct. Log the residual offset between the two nodes
continuously across many passages and compare it against Phase 1's predicted
drift.

**Gate.** Observed drift matches prediction within a factor of two. A mismatch
means something in the chain is not understood — and shipping a correction loop
over a misunderstood error is how a system acquires a fault that only appears
after hours, which is the hardest kind to find.

---

---

## 3. Phase 5 — Correction

Split out in full: [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]`.
Rate is a slope and is trimmed continuously; offset is a position and is shed
at a passage boundary. Both are built.

---

---

## 4. Phase 6 — What happens when it goes wrong

Split out in full: [GUIDE018](GUIDE018-echo-invalidation.md) `[GDE-ECHO-360]`.
What voids a frame clock, why a wall-clock step is different in kind from all
of it, and how a follower works in the master's frame so its own clock stops
mattering `[GDE-ECHO-366]`.

---

## 5. How it is tested

**`[GDE-ECHO-370]` Most of it is testable on one machine; the part that is not
must be honest about it.** The regression, the anchor arithmetic, the hysteresis
and every row of `[GDE-ECHO-360]` are unit-testable against a synthetic clock
that can be told to run fast — and should be, because they are the parts where a
sign error is invisible in listening and obvious in a test. What cannot be
faked is two real DACs over a real network, so the acceptance criterion
`[GDE-ECHO-260]` stays an integration measurement on the fleet, and the residual
offset is logged rather than merely observed, so a regression months later has a
baseline to fail against.

---

## 6. Explicitly not in v1

**`[GDE-ECHO-380]` Deferred, with reasons, so they are not mistaken for
oversights.** Bluetooth output as an echo node — deferred pending an
offset-stability measurement, **not excluded by category** `[GDE-ECHO-070]`;
mid-passage joining `[GDE-ECHO-330]`; Windows as an echo node, where the delay
term is an estimate `[GDE-ECHO-180]`; more than one master on a network; any
attempt to sync *volume*, which is a listening control and belongs to each room
`[REQ-AUD-152]`; and any Sendspin interoperation, which remains a separate
question answered on its own terms in
[SPIN001](../sendspin/SPIN001-protocol-and-integration-analysis.md).

---

## 7. Risk register

**`[GDE-ECHO-390]` What would actually sink this, ranked by how early it shows.**

| risk | shows in | if it happens |
| :--- | :--- | :--- |
| A node's ALSA gives no hardware timestamps | Phase 2 | that node cannot echo; `[GDE-ECHO-290]` makes it say so rather than drift silently |
| Wi-Fi cannot hold 1 ms wall-clock agreement | Phase 0 | anchor cadence rises, or wired-only is accepted |
| Measured drift contradicts Phase 1 | Phase 4 | stop; the chain is not understood `[GDE-ECHO-330]` |
| Crystal drifts with room temperature more than expected | Phase 4 | the trim loop absorbs it, which is what a loop is for |
| Correction loop hunts | Phase 5 | deadband widened `[GDE-ECHO-350]` |
| cpal panics on the audio thread | any | `[GDE-ECHO-190]`; do not increase how often that path is called |
