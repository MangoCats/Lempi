# GUIDE020: Making A Control Change Reach Another Node

**Development Guidance — split from [GUIDE016](GUIDE016-echo-playback-plan-build.md) 2026-09-18**

What it took to make a follower play the master's queue, flow through its
transitions, and follow a skip. `[GDE-ECHO-320]` made propagation itself
trivial — every message is absolute and rides a snapshot arriving twice a
second — so nothing here is about delivery. Every entry is about **when** a
change takes effect, and every one was found by a listener rather than a test.

> **Related:** [GUIDE016](GUIDE016-echo-playback-plan-build.md) `[GDE-ECHO-325]` — planned transitions against user input · [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]` — the correction that cleans up afterwards · [SPEC044](spec/SPEC044-echo-mode-control.md) `[SPEC-ECHO-030]` — the control that starts it

---

## 1. Following what the other node plays

**`[GDE-ECHO-336]` A follower adopts the master's queue, and not doing so was
audible.** Observed on `lempiplay3` 2026-09-18, the first time two nodes ran
this: the follower played the right passage at the right moment, and its
*Coming Up* showed entirely different tracks.

It had been choosing its own next passage all along and being pulled off it at
each boundary, one passage at a time. That pull goes through `skip`, which cuts
the ring `[REQ-AUD-158]`, so the node lost its whole buffer at every transition
and started each track from an empty one -- heard as a stutter at the opening
of the new song, which is exactly what was reported.

The master's queue was already on the wire, carried for the browser's sake on
the very socket the follower reads; the client simply ignored the field, and
`reconcile_queue` had never been called by anything. A follower now takes the
announced queue as its own, dropping only passages its library lacks, and a
scheduled start is **suppressed when the node is already heading to that
passage**. It then flows into each transition instead of skipping into it, and
the alignment a start would have imposed is left to the overlap
`[GDE-ECHO-340]` -- which is inaudible where the skip was not.

**`[GDE-ECHO-337]` A commanded start lands late by the skip's own lead, and
must be fired early by it.** `EchoStartAt` goes through `skip`, and a
skipped-to passage is not audible until `skip_lead_ms` has passed
`[REQ-AUD-162]` -- 500 ms on this fleet. The caller asks for a time the audio
should *sound*; what the engine controls is when it begins arranging it, and
nothing was converting between the two. Every commanded start therefore landed
exactly one lead late.

*This is a partial explanation, not a complete one.* The first two-node run
showed a **+1028 ms** residual after a mid-passage join, and the lead accounts
for 500 of it. The remainder is not yet understood and is recorded here
unexplained rather than rounded off.

A related fault was found by testing rather than by listening: `skip` recorded
the incoming passage's audible position as **zero** even when it had been
opened part-way in, so a node joining ten seconds into a passage published an
anchor ten seconds behind itself. `advance_shown` corrects it within a tick, so
the window is brief and it is *not* established as the cause of the 1028 ms --
but it misreports the display and any resume point taken in that window, and it
was never only an echo fault.

**`[GDE-ECHO-352]` A skip must announce when the audio will really sound, not
where the ring happened to be.** `skip` calls `admit_due` -- which emits the
schedule -- and *then* cuts the ring, so the schedule was computed from the
pre-cut depth and announced a sound time some **fourteen seconds** later than
the truth. The incoming passage is laid only `skip_lead_ms` in
`[REQ-AUD-162]`, so it sounds in half a second where the ring still holds two.

The schedule is now published from the depth the first sample *actually*
occupies, and re-published after the cut. Both places a passage is placed
somewhere other than behind the whole ring -- a skip and a seek -- do it, and
both are user input a follower is meant to mirror `[GDE-ECHO-325]`.

**`[GDE-ECHO-353]` Being next is not enough to justify flowing; being next
*at about the right time* is.** The follower suppressed a scheduled start
whenever the announced passage was already its own next one `[GDE-ECHO-343]`
-- correct for an ordinary boundary and wrong for a skip, where that passage
is still minutes away. Reported from the room: a skip on the master left the
follower playing calmly on.

It now compares the announced instant against its own projected transition and
flows only if the two are within a few seconds. Wider than any offset the
correction loop deals with, narrower than any skip -- the two cases are far
apart, and the test only has to separate them rather than measure either. With
either figure missing it believes the master rather than a guess about its own
future `[GOV-SRC-040]`.

**`[GDE-ECHO-354]` A commanded start must be visible to the thing that would
otherwise duplicate it.** With the two fixes above in place a skip did
propagate -- and landed badly anyway. The journals show why:

    bose   echo-schedule: passage=9852 sound_at=...661914 depth=660315   (pre-cut, wrong)
    bose   echo-schedule: passage=9852 sound_at=...647449 depth=22050    (post-cut, right)
    lp3    echo-follow: starting passage 9852 at sample 0 in 0.468s      (took the right one)
    lp3    echo-follow: joining part-way into passage 9852 at sample 51332

The follower did the right thing and then undid it one second later. A start
is *queued for a future instant*, so between committing and firing the passage
is in neither `current` nor the queue -- `coming_here` `[GDE-ECHO-353]` says
no, and the mid-passage join fires into a passage already committed to,
replacing an accurate placement at sample 0 with an approximate one part-way
in.

The two paths now record their commitment in the same place, so each can see
the other. That both existed independently is the underlying fault: two ways
to place a passage, neither aware of the other, and a race that only appears
when a control change makes both eligible at once.

**`[GDE-ECHO-355]` A master must announce a moment its followers can reach,
and a commitment must expire.** With the race closed `[GDE-ECHO-354]` the skip
stopped propagating *at all*, and the follower played something else entirely
-- worse than before the fix, which is how the real constraint surfaced.

A skip sounds on the master in `skip_lead_ms`: half a second. **No follower
can meet that.** Its own skip costs the same lead plus its preparation, and it
may wait half a second again simply to hear about the skip at the snapshot's
cadence. The target was declined as unreachable, and the commitment marker --
newly added -- then blocked the mid-passage join that had previously rescued
it. Two correct-looking changes combining into a worse failure than either
alone.

Both halves are fixed. A skip is now announced a couple of seconds *further
into the same passage*, which is the shape `[GDE-ECHO-325]` was written for --
*passage P, sample S, at time T* -- and is no fabrication, because by T the
master really is at S. Erring long costs nothing, since a follower that
arrives early waits; erring short costs the whole skip.

And a commitment is a claim with a lifetime rather than a fact. It suppresses
the other placement path for a few seconds, long enough to cover a start's own
lead, and then lapses -- so a placement the engine declined is retried instead
of stranding the node.
