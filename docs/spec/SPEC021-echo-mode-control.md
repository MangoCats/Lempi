# SPEC021: Choosing Whether A Node Follows

**Design Specification — independent or follower, and whom to follow**

A node plays its own programme or echoes another's. Until 2026-09-18 nothing
in the interface said which, or let anyone change it: the only way to make a
node follow was the startup flag `--follow`, so a decision about what a speaker
plays in a room could only be made by restarting a process over ssh. This
specifies the control that fixed that, and the several things about echo that
make it more than a text box and a checkbox. All of it is built.

> **Related:** [SPEC020](SPEC020-node-delay-control.md) `[SPEC-DLY-120]` — the other per-node echo control, in the same panel · [GUIDE016](../GUIDE016-echo-playback-plan-build.md) `[GDE-ECHO-315]` — which node may be master · [GUIDE010](../GUIDE010-echo-node-capabilities.md) `[GDE-ECHO-450]` — the roster this needs

---

## 1. The control

**`[SPEC-ECHO-010]` Two fields and a state, in the same settings panel as the
delay `[SPEC-DLY-120]`. Built 2026-09-18.** A mode — *independent* or *following* — and the
address of the node to follow. They belong together because neither is usable
alone, and beside the delay because all three are this node's place in the
fleet.

The address is a host, not a URL. Every node serves the same socket at the same
path, so asking for `ws://bose:5720/ws` asks a listener to know three things
that cannot vary usefully. `bose` is the whole input; the client composes the
rest — **with a port when the fleet needs one**, since `bose` answers on 80 and
`lempiplay3` on 5720, so `lempiplay3:5720` is accepted and a bare name is not
quietly assumed to be either.

**`[SPEC-ECHO-020]` The control shows what following is actually doing, because
"following" is not a state that can be assumed from a setting.** The client
already distinguishes four conditions and each means something different to
whoever is standing in the room:

| shown | means |
| :--- | :--- |
| *following* | connected, schedules arriving and being acted on |
| *master holds* | connected; the master says it cannot place itself `[GDE-ECHO-360]` |
| *schedules arriving late* | this node's offset is too large to meet them `[GDE-ECHO-410]` |
| *passage not in this library* | independent libraries have diverged `[GDE-ECHO-420]` |
| *not connected* | no socket; this node is playing its own programme |

A mode set to *following* with no connection is a node playing its own
programme, and the interface must say so rather than showing the setting and
letting the listener infer success from it `[GOV-SRC-040]`.

## 2. What changing it does

**`[SPEC-ECHO-030]` Entering follower mode joins at once by default, with
waiting for the followed node's next passage offered beside it. Built
2026-09-18.** *This entry previously specified the opposite default; changed on
the judgement that someone who has just typed a node's name wants to hear
whether it worked.*

*Extended 2026-09-18 `[GDE-ARC-041]`: the setting governs the **alignment**
as well as the join, and is labelled for both.* It used to govern only the
first moment. After that, a residual between the endgame band and the rejoin
threshold was corrected **solely** at a passage boundary, at most
`OFFSET_MAX_BITE` at a time — so on this library's four-to-six-minute
passages a listener who had asked to be in step straight away heard the node
sit hundreds of milliseconds out for minutes, with nothing acting in between.
Reported by one, and it was the honest reading of the control.

*Revised 2026-09-19 `[GDE-ARC-051]`.* The fix above was to hand whatever the
boundary would not take to the frame trim immediately. That is gone, and the
reason is worth recording rather than quietly dropping: the cap it worked
around has been removed, so the boundary now takes **the whole** residual and
there is no remainder to hand anywhere. Nor was the trim ever the faster path
— at `ECHO_DEBT_PPM` the 100 ms it was typically given took seventeen minutes,
against the four to six of the boundary it was meant to beat.

So both settings now correct in full at the next transition, and the choice is
about the **join**: straight away cuts the ring to get there now, waiting does
not. See [GUIDE026](../GUIDE026-placing-the-passage-exactly.md).

**"Straight away" still cannot mean "instantly", and the panel must not
imply it.** The reason moved with `[GDE-ARC-051]` and the conclusion did not.
It used to be the actuator: mid-passage convergence was capped at
`ECHO_DEBT_PPM` and gradual by construction. It is now the *cadence* — an
exact placement is exact, but it happens when the passage changes, which is
minutes away. Either way, alignment sooner than that costs an audible cut,
which is exactly what the *join* half spends and why that half is described as
cutting "the way a skip does". The label says "straight away, and stay there"
rather than "instantly" for that reason.

Joining at once means joining **part-way through** what the other node is
already playing, which is the capability `[GDE-ECHO-330]` deferred and
`[GDE-ECHO-510]` made necessary. The schedule cannot serve it: a schedule
describes a passage about to start, and the moment somebody switches a speaker
into follower mode is almost never one. `join_mid_passage` extrapolates the
master's position from its last anchor to an instant a second ahead, and
submits so that sample lands then.

It costs precision, and the amount is known rather than guessed. The anchor
carries the ring's own depth jitter -- tens of milliseconds `[LOG-P4-010]` --
where the schedule used at a boundary is exact at a known instant. The
extrapolation itself is negligible beside that: 14 ppm `[LOG-P4-130]` over a
one-second lead is 14 microseconds. And every join, either kind, is already
limited by the tick and by the time the engine takes to open the file, which
lands directly on the air time.

So an immediate join is right to within tens of milliseconds. *Corrected
2026-09-18: this used to end "and stays there, because `[GDE-ECHO-340]`'s
correction at passage boundaries is not built" — that correction was built
the same day and this sentence outlived it.* The join's own error is now
taken off at the following boundaries, and with "straight away" selected the
part a boundary cannot take is shed continuously in between. Waiting for the
next passage remains the more accurate way in, and is one selection away.

The immediate path is attempted **once per master passage**. A node whose
library lacks that passage must not retry twice a second forever, and when the
master moves on the ordinary schedule path takes over anyway. A reconnection
resets it, because whatever the node played while disconnected is by then its
own programme.

**`[SPEC-ECHO-040]` Leaving follower mode is always immediate and always
safe.** The node stops acting on schedules and keeps playing what it has; its
own Director is warm and its queue never stopped being maintained
`[GDE-ECHO-500]`. There is nothing to restart and no boundary to wait for,
which is the same property that makes a master going away a non-event.

**`[SPEC-ECHO-050]` A node must refuse to follow itself.** Its own address in
that field produces a socket to its own snapshot, a schedule derived from its
own ring, and a join triggered by its own admission — a feedback loop that
would skip continuously. It is an easy thing to type and a confusing thing to
watch, so it is rejected at the field.

## 3. What the interface cannot show

**`[SPEC-ECHO-060]` A master cannot list its followers, and the interface must
not pretend otherwise.** `[GDE-ECHO-020]`'s master is stateless: it broadcasts
and keeps no record of who reads it, which is exactly why a follower appearing,
vanishing or reconnecting costs the master nothing. The price is that *"who is
following me"* has no answer on the master's side. A fleet view that showed a
follower list would be inventing one from a count of sockets, which is not the
same question — a browser holds one too.

What a *follower* knows, it shows `[SPEC-ECHO-020]`. What a master knows about
its followers is nothing, and the honest interface on a master says only that
it is available to be followed.

**`[SPEC-ECHO-070]` Nothing here can tell whether the fleet is actually in
sync.** The controls set intent; alignment is a measurement, and the node's own
residual is the only evidence of it. Until `[GDE-ECHO-260]`'s residual is
logged and surfaced, a listener's ears are the instrument, and the interface
should not imply otherwise by showing a confident *following* and nothing else.

## 4. Persistence and scope

**`[SPEC-ECHO-080]` Both fields persist exactly as the delay does
`[SPEC-DLY-070]`** — written when changed rather than at shutdown, because
these appliances often do not get a clean one, and on an overlay-root node
written to the state partition or lost at the next reboot `[SPEC-DLY-080]`.

A node that was following before a power cut should come back following. That
is the behaviour a listener expects of a speaker, and it is also the one that
makes an unattended fleet survive a power event without someone reaching for
ssh.

**`[SPEC-ECHO-090]` The fleet minimum offset is roster knowledge and does not
belong in this control.** `--echo-fleet-min-frames` exists because the depth
arithmetic needs it `[LOG-ECHO-030]`, but it is a property of the *fleet*, not
of this node, and asking each node's settings page for it invites two nodes to
be told different values — which silently breaks the very alignment the figure
exists to produce. It belongs wherever the roster comes to live
`[GDE-ECHO-450]`, and until that exists it stays a flag rather than becoming a
control that looks authoritative.

## 5. Open

**`[SPEC-ECHO-100]` Discovery is not specified and may not be wanted.** Typing
a hostname is unglamorous and completely reliable; mDNS browsing would save
four keystrokes and introduce a way for the wrong node to be selected silently.
This is worth revisiting only once there are enough nodes that typing is the
actual complaint.
