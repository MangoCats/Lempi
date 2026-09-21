# GUIDE018: What Echo Trusts, and When It Stops

**Development Guidance — split from [GUIDE016](GUIDE016-echo-playback-plan-build.md) 2026-09-18**

Phase 6 on its own. Everything here is about the moments a node's own readings
stop meaning what they usually mean — a device reopening, an underrun, a pause,
a skip, and the one that is different in kind from all of them, a wall clock
that moves. Grew past the plan it was inside once the clock cases were built
and the fleet found the failures they predict.

> **Related:** [GUIDE016](GUIDE016-echo-playback-plan-build.md) `[GDE-ECHO-330]` — the plan this is part of · [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]` — what a valid basis is corrected by · [SPEC011](spec/SPEC011-audio-path-supervisor.md) — the device lifecycle behind the first row

---

## 1. What invalidates a frame clock

**`[GDE-ECHO-360]` The frame clock is invalidated by more than it looks.** Each
of these voids the anchor and must force a rejoin at the next passage boundary
rather than a silent continuation on stale state:

| event | effect |
| :--- | :--- |
| device reopen `[SPEC-APS-010]` | frame counter and stream epoch both reset |
| underrun `[REQ-AUD-142]` | frames that were counted were never heard |
| pause | the device stops; the count stops with it |
| skip | the ring is cut `[REQ-AUD-158]`, so frames counted are discarded |
| master silent | the announced queue runs down, then the warm Director resumes — no timeout `[GDE-ECHO-500]` |
| wall-clock step | the shared datum moves under every timestamp at once `[GDE-ECHO-365]` |

The last row of the original set is the important one for `[GDE-ECHO-020]`: an
echo node whose master disappears must return to being an ordinary player, not
stop.

**`[GDE-ECHO-365]` A wall-clock step is different in kind from the rest, and no
node in this fleet has a clock that cannot step.** Every other row voids the
*frame* clock — a count of frames, local to one node's audio path. A step voids
the *shared datum*: `heard_at`, `sound_at` and `submit_at` are absolute
`SystemTime`, so a step makes all of them wrong simultaneously on a node whose
audio never faltered and whose frame clock is perfect.

Observed on `lempiplay3`, 2026-09-17. It has **no RTC** — nor do `bose` or
`lempipi` — so it booted with a restored time of Sep 15 22:35 and NTP stepped it
about two days forward roughly 130 s into the boot. The signature was systemd
reporting `lempi` started two days *before* the machine booted, with
`NRestarts=0`. The step is bounded only by how long a node sat powered off.

**Built 2026-09-18, after the failure it predicts happened.** `lempiplay3` was
unplugged and moved; on restart it kept its follow setting, connected to
`bose`, adopted its queue -- and played its own programme anyway, with no
error. Its journal shows the signature plainly: entries at `Sep 15 22:35`, then
a jump to `Sep 18 20:48`. Booted on a restored clock two days behind, it
derived a submission time from `bose`'s correct `sound_at` and sat waiting two
days for it. Setting the control again changed nothing, because the setting was
never the problem.

The guard is measured against the master rather than asked of the operating
system: the question is not *is this node disciplined* but *do these two
agree*, and the anchor already carries the other side's answer. Beyond 30 s of
skew a follower acts on nothing, clears its rate window, and says which way and
by how much. A committed start more than a minute out is dropped for the same
reason.

**`[GDE-ECHO-366]` A follower works in the master's frame of time, and its own
clock leaves the arithmetic.** The guard above stops a node acting on
nonsense; it does not let it play. Since none of these nodes has an RTC, that
means silence after every power cut until NTP catches up.

The fix is not to set the clock. A follower does not need its own clock to be
*right* -- it needs to *agree* with the node it follows, and those are
different problems of which only one needs privileges. Stepping the clock to
match would require root the player does not have and would fight the NTP
daemon already disciplining it, with one authority undoing the other.

So the follower carries the difference instead: `master - own`, taken from
every anchor, and every instant from the wire is converted through it. A node
two days behind computes exactly the same submission *interval* as a
disciplined one, from the first snapshot, with no privileges and nothing to
fight.

The offset is estimated by **maximum, not mean**: `heard_at` is stamped as the
master builds the snapshot, so each reading arrives one transport delay late,
and delay is one-sided. The largest difference is therefore the least
contaminated, where an average would bake the typical delay in. What survives
is a few milliseconds on a LAN, inside the anchor's own jitter, and not worth
a round trip to remove.

A step on either side clears the rate window, because a fit across a step is
not a fit. And `clocks_agree` survives as a **diagnosis** rather than a
refusal: a node now follows correctly with a wrong clock, and its listener
should still be told the clock is wrong.

Three requirements follow:

- **Intervals come from a monotonic clock**; only the shared datum is wall time.
  A rate regressed across a step is not a rate.
- **A step voids the basis**, exactly as an underrun does, and forces a rejoin at
  the next passage boundary.
- **A node publishes no echo state until its clock is synchronised.** Before
  that it is not a node with an unknown time, it is a node with a confidently
  wrong one, which is the worse of the two `[GOV-SRC-040]`. A master that steps
  announces a `sound_at` two days out and every follower reports `Missed`
  indefinitely.

