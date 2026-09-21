# SPEC020: The Node Delay Control

**Design Specification — per-node presentation delay, and who decides it**

Every echo node needs a presentation offset `[GDE-ECHO-410]`, and for some nodes
no part of the stack can report it. This specifies the control that lets a
listener supply it by ear, what its default is in each of the three cases that
arise, and the consequences a hand-set delay has for the rest of echo.

> **Related:** [GUIDE010](../GUIDE010-echo-node-capabilities.md) `[GDE-ECHO-430]` — the offset's measured and calibrated halves · [GUIDE016](../GUIDE016-echo-playback-plan-build.md) `[GDE-ECHO-315]` — the fleet cap this feeds · [LOG011](../LOG011-the-lead-is-a-ring.md) `[LOG-ECHO-030]` — where the depth arithmetic comes from

---

## 1. The control

**`[SPEC-DLY-010]` Every node exposes a delay trim of ±2000 ms in 1 ms steps,
and it is the calibrated half of `[GDE-ECHO-430]` made editable.** This is not a
second notion of delay layered on the first. `presentation_offset` is *measured
delay + calibrated residual*; the control sets the residual, and a node whose
stack reports nothing is simply one whose measured half is absent.

The range covers the worst case with room to spare — the largest offset measured
on this fleet is `lempipi`'s 355 ms `[LOG-CPAL-060]` — and extends negative
because the residual can run either way: a stack that over-reports its delay
needs a node pulled earlier, not later.

**`[SPEC-DLY-020]` One millisecond is the step because it is near the limit of
what the ear can place, and a finer control invites chasing noise.** At 44.1 kHz
a step is 44.1 frames, deliberately coarser than the trim loop's single frame
(23 µs `[GDE-ECHO-340]`). The two are different instruments: the trim loop
corrects a *slope* nobody can hear accumulating, while this control corrects a
*position* someone is standing in a room judging. Giving a human a control finer
than their own discrimination produces endless adjustment and no improvement.

**`[SPEC-DLY-030]` The total offset is clamped at zero, visibly.** A node cannot
sound before it submits, so a negative trim larger than the measured delay is
not a smaller number but an impossible one. The control accepts the entry,
clamps the total, and says it is clamping — it does not silently substitute a
different value `[GOV-SRC-030]`.

## 2. What "default" means, in three cases

**`[SPEC-DLY-040]` The default is resolved by rank, and the rank is by
measurement `[GOV-SRC-010]`.** In order:

| | condition | default |
| :--- | :--- | :--- |
| 1 | the delay is knowable from the hardware/software configuration | the **known delay** |
| 2 | not knowable, but a value was stored by this node previously | the **stored value** |
| 3 | not knowable, nothing stored — first power cycle | **0 ms** |

"Knowable" means the verdict of `[GDE-ECHO-290]`: ALSA's reported delay is real
because it was observed to *change*, not because a call returned a number. An
I²S DAC lands in case 1; an A2DP sink whose internal buffering is opaque to the
host lands in case 2 or 3 `[GDE-ECHO-430]`.

**`[SPEC-DLY-050]` Which case is in force must be visible in the UI, because
three different zeroes mean three different things `[GOV-SRC-040]`.** A measured
0 ms, a restored 0 ms and a never-configured 0 ms are indistinguishable as
numbers and completely different as claims. The control states its provenance —
*measured*, *restored*, or *unset* — beside the value. A listener wondering why
one speaker is late should be able to see that nothing has ever measured it,
rather than reading a confident zero.

**`[SPEC-DLY-060]` Reset restores the default as ranked above, not zero.** On a
node in case 1 the button discards hand tuning and returns to the measured
figure. On a node in case 2 it returns to the stored value. Only in case 3 is
reset the same as zero, and there it is zero because nothing better exists, not
because zero was chosen.

A node that acquires a known delay it did not have before — a device replaced, a
link renegotiated, a verdict reached after `[GDE-ECHO-290]`'s threshold — moves
to case 1, and a hand-set value is **kept, not overwritten**. The user's number
survives; the reset button is what surrenders it. Overwriting a deliberate
setting because a measurement arrived later would discard the one input the
machine cannot produce for itself.

## 3. Persistence

**`[SPEC-DLY-070]` The value is written when it changes, not at shutdown.** The
requirement is that a node come back with the delay it had; phrasing that as
"the value at last power down" assumes a shutdown that these appliances often do
not get. LOG010 puts unplanned power loss at the centre of this fleet's risk
model `[SD-RISK-010]`, and a setting saved only on the way down is a setting
lost exactly when it was last adjusted.

Writes are debounced — a listener dragging a slider must not produce a hundred
writes — and the debounce is the only delay between adjusting and durability.

**`[SPEC-DLY-080]` On an overlay-root node the value must be written to the
state partition, or it will survive every test and vanish at reboot.** `bose`
writes to `/` land in a tmpfs upper layer `[IMPL-BOS-185]`. A delay stored there
reads back correctly for as long as anyone is likely to check and is gone at the
next power cycle — the exact failure this specification exists to prevent, in
the exact place it is easiest to introduce. It belongs with the node's other
durable state, on a path bind-mounted from the state partition.

## 4. Consequences for the rest of echo

**`[SPEC-DLY-090]` A trim changes the fleet's depth cap, so the announced total
must be recomputed.** The common submit-to-air total is bounded by
`capacity + min(device delay)` across the roster `[LOG-ECHO-030]`. Trimming *any*
node changes its delay, and trimming the node that currently holds the minimum
changes the bound itself. A master that announced a total before the change will
announce one a follower cannot reach, and that follower reports `TooShallow`
rather than playing early `[GDE-ECHO-315]`.

So a delay change is a roster event: it propagates to whatever computes the
announced total, and it does so before the next admission. This is the
non-obvious cost of a user-facing control on a quantity the scheduler depends on.

**`[SPEC-DLY-100]` A trim steps the residual, and the trim loop must not read
that step as drift.** Changing the offset moves this node's air position
immediately and by construction — it is not an error, and correcting it would be
correcting the user. The frame clock is untouched, so the basis is *not* voided
`[GDE-ECHO-360]`; what resets is the residual estimator's window, the same reset
a rejoin performs `[GDE-ECHO-510]`. A rate estimator fed a step it believes is
drift will chase it for as long as its window is deep.

**`[SPEC-DLY-110]` Trimming the master moves the whole fleet; trimming a
follower moves only that node.** Both are legitimate and the UI should not
pretend otherwise. The master's offset enters the sound time every node
schedules against `[GDE-ECHO-410]`, so adjusting it shifts the programme in time
for everyone — which is what a listener wants when the whole house is late
against something external, and emphatically not what they want when one speaker
is out. The control therefore says which node it is adjusting and whether that
node is currently the master.

## 5. Open

**`[SPEC-DLY-120]` The control lives in the per-node Lempi skin settings page**
— `#panel-settings` in
[skin.html](../../player/src/web/skins/lempi/skin.html), served by
[settings.rs](../../player/src/web/settings.rs). **Built 2026-09-18.**

*This entry twice said so before it was true.* It was first recorded
2026-09-17 from a statement rather than from the code; reading the code on
2026-09-18 found the panel carrying seven settings, none of them a delay, and
nothing in `player/src/web/` mentioning an offset at all. It is now genuinely
there: a millisecond field with a **Default** button, `POST /echo/trim/:ms` and
`/echo/trim/reset`, persisted through `player_settings` beside every other
setting `[SPEC-DLY-070]` — which puts it in `listener.db` under `/var/lempi`,
bind-mounted from the state partition, satisfying `[SPEC-DLY-080]` by reuse
rather than by a second mechanism.

The provenance line `[SPEC-DLY-050]` is rendered from `measured_frames`, which
is `None` rather than zero on a node whose timestamp verdict is not `Hardware`
`[GDE-ECHO-290]`, so *"nothing here measures this device's delay"* and
*"measured at 46.3 ms"* are different sentences and not the same zero. The
clamp `[SPEC-DLY-030]` is reported in that line too, never applied silently.

**`[SPEC-DLY-125]` The control is live, and that took a second pass.** The
first version stored the value and rendered it and changed nothing: the
follower's presentation offset still came from `--echo-offset-frames`, read
once at startup. A control that persists, displays, and does not act is worse
than no control, because it invites a listener to calibrate by ear against a
figure the scheduler never sees. The follower now reads
`offset_frames` -- measured plus calibrated, already clamped -- on every pass,
so moving the slider moves the next passage's timing.

`--echo-offset-frames` remains for `[SPEC-ECHO-090]`'s pairing with the fleet
minimum, which is roster knowledge rather than this node's own.

What remains open is only the *aggregate*: a fleet-wide view showing every
node's delay side by side would suit the actual task — aligning speakers against
each other, which is a comparison — but it needs a roster surface that does not
yet exist. Per-node costs nothing that view would later have to undo, since the
value is the node's own either way `[SPEC-DLY-070]`.

**`[SPEC-DLY-130]` Nothing yet measures whether 1 ms is the right step for this
room.** The figure is taken from the general limit of interaural discrimination,
not from a listening test on this fleet, and the useful step may be coarser for
speakers in different rooms than for two in one. It is cheap to revisit once
`[GDE-ECHO-260]`'s acceptance test gives a fleet worth listening to.
