# PI003: Choosing a Speaker

**Design — Tier 2**

How a listener picks their Bluetooth speaker from the Lempi settings panel, and
why the obvious implementation of that is broken.

> **Related:** [PI001 partitions](PI001-image-and-partitions.md) ·
> [PI002 test image](PI002-test-image-setup.md)

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-2 in [PI003](PI003-choosing-a-speaker.md), §3-5 in [PI022](PI022-the-players-speaker-contract.md).

## 1. What this has to survive

Every requirement below is the residue of a specific failure on real hardware,
recorded in [PI002 §6a](PI002-test-image-setup.md). None of them is
hypothetical, and the naive version of this feature reproduces all of them.

**`[PI3-WHY-010]` PipeWire always offers a `Dummy Output`.** When no sink is
present it invents one, and it is a perfectly good sink: a player attached to
it plays flawlessly, forever, into nothing, reporting itself healthy from every
angle. Two days of this problem were spent looking at Bluetooth because the
player insisted it was fine.

**`[PI3-WHY-020]` A stream sometimes follows a change of default sink, and
sometimes does not.** This is the awkward one, and the reason the fault took so
long to place.

Tested deliberately: with the speaker untrusted and disconnected, the player
starts and binds to `Dummy Output`. Reconnecting the speaker then relinked the
stream to MIDDLETON **by itself**, same node ids, no reopen. But the original
failure was precisely a stream sitting on `Dummy Output` while the speaker was
connected, staying there, and playing to nobody.

Both were observed on the same machine. So the relink is real but not
dependable -- plausibly a race between the dummy being removed and the sink
appearing -- and a design that relies on it works most of the time, which is
the worst frequency for a fault to have.

**Therefore "select a speaker" must reopen the output rather than change the
default and hope.** Not because the stream never follows, but because it
cannot be trusted to, and a cosmetic selection is silent.

**`[PI3-WHY-030]` A2DP dies when nothing feeds it.** A speaker with no audio
hangs up after a few seconds, and the listener hears a disconnection tone. This
makes every silent failure above *sound like* a Bluetooth fault, which is
exactly the wrong place to look.

**`[PI3-WHY-040]` `pair` and `trust` are separate, and only `trust` survives a
reboot.** Pairing without trusting yields a speaker that works beautifully until
the appliance restarts, then never reconnects.

**`[PI3-WHY-050]` A stale trusted-but-not-paired record blocks re-pairing
silently.** BlueZ keeps the entry, `pair` fails, and nothing explains why. The
fix is to `remove` the device first, which no listener will ever guess.

**`[PI3-WHY-060]` Pairing needs a registered agent.** Without
`agent NoInputNoOutput` the exchange never completes, and the failure is a
timeout rather than a message.

---

## 1a. What the listener should experience

**`[PI3-AIM-010]` Choose a speaker once. It is remembered. Pressing play
plays.** That is the whole requirement, and everything else in this document is
machinery in service of it. Written down because the parts can each work while
the experience still does not.

What that costs, beyond what is already built:

- **`[PI3-AIM-020]` Done, 2026-08-27.** The choice must be stored by Lempi, not
  inferred from BlueZ trust and PipeWire's default -- those agree only until a
  second speaker is ever tested. `use`/`pair` now write the address to
  `player_settings` `[REQ-VIS-260]`. Found by its absence: `lempi-speaker`
  (below) had been paging a hard-coded leftover from the `[PI3-WHY-020]`
  testing (`MIDDLETON`) every 30 s regardless of what was connected --
  stalling whatever *was* playing for several seconds each time, since paging
  an unreachable device ties up the one shared radio. Audible as a skip with
  the on-screen position frozen; invisible to the underrun counter, since the
  stall is on the radio and never touches the output ring.
- **`lempi-speaker`,** the timer that connects the stored speaker if absent and
  tells the player to reopen, now reads that stored address rather than a name
  compiled into the script. `SPEAKER` still overrides it for a library with no
  chosen speaker yet.
- **`[PI3-AIM-030]` Play must be willing to go and get the speaker.** The timer
  covers "off at boot." Still open: pressing play in the narrow window before
  its next tick still finds a dummy and correctly reports silence.
- **`[PI3-AIM-040]` Done, 2026-09-04.** `[PI3-AIM-020]`'s fault recurred for a
  new reason: not a hard-coded address this time, but a *stored* one gone
  stale -- `speaker_address` still named MIDDLETON while the appliance was
  actually connected to and playing through a different speaker (OontZ,
  paired straight through `bluetoothctl` rather than the settings panel's own
  `use`, the one path that keeps this row honest). Same symptom exactly:
  `lempi-speaker` paged the stored-but-wrong address every 30 s, stalling the
  speaker that *was* playing. Reading the correct value is not enough on its
  own -- the value can still drift out from under it. `lempi-speaker` now
  checks what BlueZ actually has connected *before* trusting what it
  remembers: if a real, audio-capable device is already connected, that
  settles it, whether or not it matches `SPEAKER` -- paging the stored
  address on top of a working connection was the disruption, not a fix for
  one. A mismatch is corrected silently (the database row alone); nothing is
  paged unless BlueZ reports nothing connected at all.
  **Narrowed 2026-09-08 by `[PI3-FOUND-310]`:** adopting *any* connected
  device overwrote a choice the listener had just made in the settings panel,
  when a second trusted speaker auto-connected and took the one A2DP
  transport. The injury this rule was written against — a stale address paged
  every thirty seconds — is now prevented at its source by `[PI3-AIM-060]`,
  which pages nothing while audio reaches a real sink, so adoption survives
  only for the case where no speaker has been chosen at all. A recorded
  choice now stands until the listener changes it.
- **`[PI3-AIM-050]` Done, 2026-09-08.** `[PI3-AIM-040]`'s "audio is already
  flowing correctly" was an assumption, not a check, and it was wrong twice
  over on a lempi02w boot that a listener experienced as "can't connect to
  Middleton" even though the radio link was fine the whole time. First:
  `lempi-wait-sink` releases the player on the first *any* real sink it
  sees, and on this hardware that can be the onboard HDMI output --
  observed 28 s before Middleton's own A2DP transport came up, meaning the
  player had already opened its stream onto the wrong sink before Middleton
  was even reachable. Second: BlueZ reconnects a trusted device
  autonomously, with nobody having asked `lempi-speaker` to act at all, so
  its "already connected" branch ran and found nothing to do -- by design,
  the device link genuinely was fine -- while the stream stayed exactly
  where boot had left it. Both land in the same place: BlueZ reports
  "connected," and the player is talking to a different sink regardless.
  `lempi-speaker` now checks one layer further in on every tick, connected
  or not: whether the player's own stream (`GET /audio/sink`
  `[SPEC-APS-060]`) is actually linked to the device BlueZ has connected,
  by comparing PipeWire's sink name against the device's Bluetooth alias.
  A mismatch -- including a dummy or absent sink -- gets exactly the
  existing `reopen-output` treatment `[PI3-WHY-020]`, whether this script's
  own connect put the device there or BlueZ did it unasked.
- **`[PI3-AIM-060]` Done, 2026-09-08. How hard to chase is set by what
  chasing can cost.** `[PI3-AIM-020]` and `[PI3-AIM-040]` both ended in the
  same injury — paging a device that could not answer tied up the shared
  radio and stalled the speaker that *was* playing — and the conclusion drawn
  from them, try once and stop, was right about that risk and wrong as a
  general policy. It also governs the case where nothing is playing at all,
  where there is no audio to protect and a single attempt simply loses. On
  this appliance losing is the default: `[PI3-FOUND-090]`, the speaker powers
  the Pi from its own USB port, so they can only power up together, and the
  thirty seconds the Pi needs to reach a working Bluetooth stack are thirty
  seconds in which any already-awake phone takes the speaker — after which it
  stops answering pages entirely and the appliance concludes it is switched
  off. The keeper now spends a wall-clock budget staying after the speaker
  rather than making one attempt, **but only while the player is on a dummy
  or on nothing**. Audible audio keeps the old timidity exactly as it was.
  The lesson those two findings taught is not weakened by this; it is given
  the condition it always implied.
- **Failure has to stay legible.** A speaker that is off, flat, or in pairing
  mode cannot be reached by any amount of retrying, and the panel should say
  which of those it looks like rather than spinning `[PI3-UI-010]`. Half of
  this arrived with `[PI3-FOUND-070]`: reports now separate a speaker that
  never answered from one that answered and refused, and carry whether A2DP
  is actually streaming rather than merely linked.

## 2. The shape of it

**Built, but not yet seen.** The panel is written and serves correctly; it has
not been rendered in a browser by anyone. Treat the layout as unverified.

**`[PI3-UI-010]` One list, showing what is true.** The settings panel shows
every known and discoverable speaker in a single list, each with a state that
is *observed* rather than assumed:

| State | Meaning | Offered action |
|---|---|---|
| `playing` | connected, and Lempi's stream is attached to it | Forget |
| `connected` | linked, but audio is going elsewhere | Use this one |
| `paired` | known, not currently linked | Connect |
| `found` | in range, never paired | Pair |
| `stale` | trusted but not paired `[PI3-WHY-050]` | Repair |

The distinction between `playing` and `connected` is the entire lesson of
`[PI3-WHY-010]` made visible. A user who sees "connected" and hears nothing has
been told something useful; a user who sees a checkmark has been lied to.

**`[PI3-UI-020]` Selecting a speaker performs the whole sequence.** Connect,
trust, make default, **and reopen Lempi's output** -- the last step being the
one `[PI3-WHY-020]` makes mandatory and the one nobody would think to expose.
A selection that stops short of it appears to work and is silent.

**`[PI3-UI-030]` Confirm or revert, as for the network settings
`[PI-SET-014]`.** Switching speakers can destroy the means of hearing whether
the switch worked. So the new speaker is adopted provisionally, the panel asks
"can you hear this?" over a test tone, and an unanswered prompt returns to the
previous sink after 30 seconds. The listener cannot strand themselves in
silence by choosing wrongly.

**`[PI3-UI-040]` Pairing is a mode, not a button.** The panel offers "put my
speaker in pairing mode, then press this", scans for the ~20 s a speaker takes
to appear, and registers an agent for the duration `[PI3-WHY-060]`. Discovery
stops afterwards, because a permanently scanning radio is a permanently
degraded one on a shared antenna.

---

> **Split on 2026-09-10.** What the player must provide, privilege, the
> reopened stream and the deferred work are now
> [PI022](PI022-the-players-speaker-contract.md).
