# GUIDE009: Echo Playback — Plan, Phases 0-2

**Development Guidance — planned 2026-09-11, concluding [GUIDE008](GUIDE008-echo-playback-investigation.md); split 2026-09-13**

The build order for echo playback, with the gate that can stop each phase. The
plan is shaped by one fact from the investigation: the only drift figure in
evidence belongs to one machine `[GDE-ECHO-060]`, so the early phases produce
measurements and the design of the correction is not settled until it has them.
The timebase comes first, because every measurement after it is expressed
against it `[GDE-ECHO-305]`.

**This file holds the gating and phases 0-2, which measure and are met.** The
phases that build — 3 through 6, the testing strategy, the deferrals and the
risk register — continue in
[GUIDE016](GUIDE016-echo-playback-plan-build.md), split there 2026-09-13 at
`[GOV-DOC-010]`'s line limit.

> **Related:** [GUIDE016](GUIDE016-echo-playback-plan-build.md) `[GDE-ECHO-310]` — phases 3-6, where this plan continues · [GUIDE014](GUIDE014-echo-phase-status.md) `[GDE-ECHO-520]` — **where each phase actually stands, and the revised critical path** · [GUIDE008](GUIDE008-echo-playback-investigation.md) — the findings this rests on · [GUIDE010](GUIDE010-echo-node-capabilities.md) — the node model Phase 1 fills in · [REQ003](spec/REQ003-audio-playback.md) — the buffer-depth rule every correction obeys · [SPEC011](spec/SPEC011-audio-path-supervisor.md) — the device lifecycle that invalidates a frame clock · [BOSE004](../BosePi/BOSE004-operating-health.md) — the independent instrument Phase 2 is validated against · [PI026](../LempiPi/PI026-startup-preflight.md) — the preflight shape Phase 0 borrows

---

## 1. How the plan is gated

**`[GDE-ECHO-250]` Six phases, each ending in a measurement that can stop the
project.** No phase begins while the previous one's gate is unmet. Phases 0–2
are useful on their own and leave the player better instrumented even if echo
playback is never built, which is deliberate: the expensive, irreversible work
is deferred behind the cheap, independently valuable work.

**`[GDE-ECHO-260]` "Done" is a sustained measurement, not a demonstration.**
The acceptance criterion for the whole effort is: **two nodes, playing one
programme, holding within 1 ms of each other for 24 unbroken hours across
passage changes, with the residual offset logged throughout.** One ms is chosen
because it is comfortably inside the comb-filtering threshold `[GDE-ECHO-050]`
and comfortably outside what `[GDE-ECHO-110]` predicts is achievable; a target
the prediction only just meets would not distinguish success from luck. A demo
that sounds right for one song proves nothing about drift and must not be
accepted as evidence `[GOV-SRC-020]`.

---

## 2. Phase 0 — The shared timebase

**`[GDE-ECHO-300]` chrony on every node, verified at boot, reported in the same
place as everything else.** Install and configure chrony across the fleet with a
common upstream, then extend the startup preflight `[PI-PRE-010]` with a check
that it is running and converged. What must be recorded is not chrony's own
estimate of its accuracy but the observed offset between nodes over a week —
`[GOV-SRC-020]` again: a daemon's self-report is a claim, not a measurement.

*The half of this that was not done cost eight days, 2026-09-18.* The rule was
right, the reasoning below it was right, and **the verification it asks for in
its own second sentence was never written** — so nothing noticed that
`lempiplay3` had been left on `systemd-timesyncd` when the other three moved.
It was found by a listener hearing the follower wander, not by any check here.
Measured: 688 ms of skew against `bose`, of which the clock was most; on
chrony against the same LAN reference, a median of **12 ms**, with no change
to a line of player code `[GDE-ARC-038]`. The preflight check now exists
`[PI-PRE-052]`, and `tools/echo_skew.py --clocks` is the week-long
between-node measurement this paragraph asked for.

**A standard that only one participant can see is not one.** Three nodes
moving and a fourth not was invisible precisely because "chrony on every
node" lived in a plan and in three machines' filesystems, and in nothing that
runs. That is the same lesson `CLAUDE.md` opens with about `cargo fmt`.

**`systemd-timesyncd` is the default, and it is the wrong tool here** — for a
specific reason rather than a general one: it is an SNTP client that corrects
the *time* without disciplining the *frequency*, which is precisely the half
`[GDE-ECHO-100]` needs. A node left on timesyncd satisfies the wall-clock
requirement and silently fails the frequency one.

Every node shipped that way. **Three moved to chrony on 2026-09-11, with
`smartboardpc` serving the LAN** (`allow`, plus `local stratum 10` so it keeps
serving through a WAN outage). `teacherslounge` and `lempipi` `prefer` it and
carry an identical, non-leap-smearing fallback set, so a Smart outage degrades
the fleet together rather than splitting it: one shared server makes its own
error **common-mode**, and common-mode cancels in the node-to-node comparison
that is the only one echo depends on. Differential error does not.

**`bose` joined 2026-09-12** through `overlayroot-chroot` `[IMPL-BOS-180]`,
after the escape hatch took it off the network `[IMPL-BOS-175]`. Gate met.

**`[GDE-ECHO-305]` The timebase precedes the measurement, and an earlier
revision of this plan had that backwards.** A node's ppm is expressed
*against* the clock discipline in force, so measuring first and installing
chrony afterwards invalidates the measurement without changing the hardware —
the DAC would not move; the ruler would. Observed on `smartboardpc` minutes
apart: residual frequency **+305.427 ppm, then −213.530 ppm, skew still at
10⁶ ppm** — chrony saying it has no estimate yet. The fleet must be **entirely
on chrony and settled** before Phase 1 begins, and a mixed fleet cannot be
compared across at all `[GOV-SRC-020]`.

**Gate.** Node-to-node wall-clock agreement within 1 ms, sustained, across a
reboot of each node and across a Wi-Fi reconnect. If Wi-Fi proves too unstable
to hold that, the finding belongs in this document before any wire format is
designed, because it changes how often the anchor in Phase 3 must be resent.

---

## 3. Phase 1 — Measure every candidate node

**`[GDE-ECHO-270]` A per-node ppm campaign, using the instrument that already
exists, before any player code changes.** For each node that might participate,
read the running stream's `hw_ptr` — the card found **by name**, per
`[BOS-PWR-050]` — at two points at least 24 hours apart and difference it
against `/proc/uptime`, using `tools/drift_analyze.py`. **Never against the
status file's `tstamp`**, which ALSA derives from `hw_ptr` itself: that is
circular, and is how this phase's first results came out wrong by a factor of
33 `[LOG-FIX-010]`. Record ambient temperature at both readings, because a
crystal moves with it and a single-temperature figure is what `[GDE-ECHO-060]`
warns against.

Each node must yield **both halves of `[GDE-ECHO-400]`'s model**, not ppm
alone: its rate error, its presentation offset, whether that offset holds across
a device reopen and a reconnect, its clock ownership, its timestamp source, and
whether it has the library's native rate. An earlier revision of this phase
measured rate only, which would have left the offset column empty for every
node and the Bluetooth question unanswerable.

The roster is five nodes and one measurement `[GDE-ECHO-450]`. Rate error is
obtained by regression against the shared timebase; offset by ALSA's reported
delay plus a calibrated residual `[GDE-ECHO-430]`, with the acoustic
cross-correlation of `[GDE-ECHO-460]` as the independent check that ranks the
rest `[GOV-SRC-020]`.

**Gate.** Two or more wired nodes measured below 2 ppm relative: proceed as
planned. Any intended node above 20 ppm: proceed, but Phase 5 becomes mandatory
rather than optional, and `[GDE-ECHO-110]`'s "per-passage resync would have been
enough" conclusion is void for that pair. A node that cannot be measured at all
is not a candidate. **Outcome: the middle case, and the offset half is
blocked** — see `[GDE-ECHO-530]` in [GUIDE014](GUIDE014-echo-phase-status.md).

---

## 4. Phase 2 — The frame clock

**`[GDE-ECHO-280]` Count frames where they leave for the device, and nowhere
else.** In `player/src/output.rs`, `fill` gains three atomics alongside the
existing `Counts`: frames emitted since stream start, the disciplined wall-clock
reading taken in the same callback, and the ALSA delay as reported by
`playback − callback` `[GDE-ECHO-160]`. The callback's existing contract is
unchanged and non-negotiable — no allocation, no blocking, no lock — so these
are relaxed atomic stores of values already in hand, and the regression that
turns them into a rate estimate happens on an ordinary thread.

The callback signature changes from `move |out, _|` to bind the
`&OutputCallbackInfo` it currently discards `[GDE-ECHO-140]`. Only the delay
difference is read from it; the absolute instants are not consumed
`[GDE-ECHO-200]`.

**`[GDE-ECHO-290]` Publish whether the timestamps are real, and treat "unknown"
as unknown.** cpal silently substitutes a software clock when hardware
timestamps are unavailable `[GDE-ECHO-170]`, and offers no way to ask which
happened. Detect it by observation rather than by interrogation: if the reported
delay never varies across many callbacks, it is not coming from hardware.
Publish the verdict as a three-state fact — hardware, software, undetermined —
surfaced the way every other audio-path fact already is `[REQ-VIS-250]`. A node
reporting anything but *hardware* is not eligible to echo, and says so. Absent
is not zero `[GOV-SRC-040]`. **This currently disqualifies every node, `bose`
included** — the delay term reads a constant 0 while the kernel reports a live
one; the rule is right and its input is broken `[GDE-ECHO-535]`.

**Gate.** The in-process frame clock agrees with `/proc/asound`'s `hw_ptr`
within 1 ppm over 24 hours on `bose`, **both referred to the system clock** —
`hw_ptr` is independent only when it is not compared against a timestamp
derived from itself `[LOG-FIX-010]`. Two instruments, one answer.
**Met, at +0.33 ppm** `[LOG-FIX-050]`; it first appeared to fail by 13.19 ppm,
which was the reference and not the instrument.

---

---

## 5. The rest of the plan

Phases 3 through 6, how the whole is tested, what is deferred from v1, and the
risk register are in
[GUIDE016](GUIDE016-echo-playback-plan-build.md). The split is mechanical, at
`[GOV-DOC-010]`'s 300-line limit, and falls on a real seam: everything here is
measurement and is finished, everything there is construction and is not.
