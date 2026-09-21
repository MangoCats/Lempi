# PI018: The Sink Gate, and the Keeper That Stopped

**Appliance Record — what holds the player back, and for how long**

Split from [PI010](PI010-startup-time-and-stutter.md) on 2026-09-10, which had reached 687 lines against `[GOV-DOC-010]`'s 300-line limit.

The gate decides when the player may start, and therefore when the web
interface answers. Twice it was wrong in a way that looked like patience:
once counting loop passes as though they were seconds, and once holding
the controls hostage to a speaker that was switched off.

> **Related:** [PI010](PI010-startup-time-and-stutter.md) for the startup investigation these came out of · [PI011](PI011-two-speakers-and-placement.md) for the stutter

---

## 1. The gate, the deadline, and the fallback

**`[PI3-FOUND-540]` The sink gate counted passes and called them seconds.**
Measured 2026-09-10, on a boot whose chosen speaker had been switched off:
`lempi.service` took 1 min 8 s, and the gate ran from 17:02:19 to 17:04:42
before printing *"no real sink after 60s"*. It had waited **143 seconds**.

The loop incremented a counter once per iteration and treated it as a second,
but each pass also runs `wpctl status`, which on a Pi Zero 2W during boot costs
well over a second by itself. So `DEADLINE=60` bought roughly 143 s of waiting,
and every comment in the file describing the deadline in seconds was wrong --
including the ones weighing 15 s against 45 s in `[PI3-FOUND-260]`, whose real
figures were larger by the same factor.

**What made it matter is where the gate sits.** It is an `ExecStartPre` for
`lempi.service`, so it does not merely delay audio -- it holds the web
interface down with it. On that boot the listener could not reach the page
that would have let them choose a speaker that was actually present, for the
entire time the appliance spent waiting for one that was not. A gate meant to
protect the audio had taken the controls away.

The deadline now measures elapsed wall-clock time, which is what the file
always claimed. The fast path is unchanged and still exits the moment the
speaker's sink appears -- verified on the appliance at 0 s with the speaker
present.

> **Worth revisiting separately.** Waiting is defensible while the chosen
> speaker is *coming*; it is pure loss when that speaker is switched off, and
> the appliance cannot tell the two apart. Nothing here changes that, and the
> interface stays gated behind the audio wait.

**`[PI3-FOUND-550]` The gate now waits five seconds, not sixty.** The 60 s
deadline came from `[PI3-FOUND-260]`, which measured that starting before the
speaker exists costs a reopen and 26 s of underrun while starting after costs
only silence that was silent anyway. That is still true about *audio*, and it
was the wrong thing to optimise: the gate is an `ExecStartPre`, so it was
buying quieter audio with the listener's access to the controls
`[PI3-FOUND-540]`. The listener's judgement settled it -- five seconds is
acceptable, 140 is not.

The fast path is unchanged: a speaker already present releases the gate
immediately, measured at 0 s. What changes is the failure case, which now
costs five seconds instead of over two minutes. The audio is no longer the
gate's problem; the keeper connects the speaker and asks the player to reopen.

**`[PI3-FOUND-560]` And a missing speaker no longer means silence.** Asked for
directly after the same boot: the appliance sat waiting on a speaker that had
been switched off while a second, known speaker was awake in the same room. It
had everything it needed to make sound and made none.

`lempi-speaker` now tries the other known speakers when the remembered one
does not answer. Deliberately narrow:

- Only after the chase has already failed, and only when `BUDGET` is non-zero
  -- which means nothing is currently audible. It can never interrupt playback
  to go hunting, which was the injury behind `[PI3-AIM-060]`.
- Only devices that are **paired and advertise an Audio Sink**. Something
  deliberately introduced to this appliance, never something merely in range.
- Bounded at 20 s total, three seconds per candidate.
- **It does not rewrite `speaker_address`.** The listener's choice still stands
  and is preferred again on the next boot. This is a stand-in for a missing
  speaker, not a new decision about which speaker this is.

**Trust was the obvious test and it was the wrong one.** The first version
required `Trusted: yes` and would have skipped exactly the speaker it existed
to reach: this appliance untrusts every speaker but the chosen one, so that
only the chosen one may reconnect to *us* unasked `[PI3-FOUND-130]`, and the
Middleton read `Trusted: no` the moment the listener switched to the Oontz.
Trust governs an inbound connection; this one is outbound, and a bond is what
says a device is known. Caught by checking the candidate list against the real
adapter rather than trusting the predicate: it returned one speaker where it
should have returned two.

**Exercised for real, 2026-09-10.** The Oontz -- the remembered speaker -- was
powered down while it was playing. The Middleton was untrusted, so nothing but
this path could have done it, and audio was playing from the Middleton about
forty seconds later with no intervention `[PI3-FOUND-590]`.

**The gate change measured, on the boot of 2026-09-10 17:16.** The journal
reads what it was built to read:

    17:16:28  lempi-wait-sink: no real sink after 5s; starting anyway
    17:16:28  Started lempi.service
    17:16:29  connected 08:EB:ED:26:14:12 after 6s and asked the player to reopen

`lempi.service` no longer appears in `systemd-analyze blame` at all, against
1 min 8 s on the boot that prompted this. The gate released at five seconds,
the keeper landed the speaker a second later and asked for the reopen, and the
listener heard clean audio at +36 with no stutters -- **so the reopen this
trade was expected to cost was not audible**. That is the `[PI3-FOUND-260]`
worry answered on its own terms rather than argued away.


## 2. The keeper that stopped without failing

**`[PI3-FOUND-670]` The keeper stopped running, and nothing said so.** The
listener powered down the speaker holding the audio. Four minutes later
nothing had reconnected, the incumbent file was stale, and the stream sat on
`Dummy Output` -- while the same work run by hand recovered the appliance in
25 seconds. **The appliance was not slow to recover. It was not running.**

`systemctl` showed the service stuck in `activating`, and the cause is a
default worth knowing: **systemd sets `TimeoutStartSec` to infinity for
`Type=oneshot`.** A tick that blocks inside `bluetoothctl` is therefore never
killed, and a timer cannot fire while the previous tick is still running -- so
one blocked call silently stops every future tick. Every layer looked healthy:
the timer was `active`, the service had not failed, and nothing was logged,
because a process that is stuck logs nothing.

Two fixes, because the budgets and the timeout answer different failures:

- `TimeoutStartSec=45` on the service. systemd now kills an overrunning tick
  and the next one proceeds. This is the one that matters -- no arrangement of
  internal deadlines can bound a single call that never returns.
- One budget for the tick, shared: 15 s for the chase and the remainder for
  the fallback, 25 s total against a 30 s period. Previously they were
  independent -- 22 s and 20 s -- so a healthy tick with the chosen speaker
  switched off could legitimately want 42 s in a service fired every 30. The
  two shares stay separate rather than sharing one deadline, because a chosen
  speaker that is switched off would otherwise eat the whole tick every time
  and the fallback would never run.

A settled tick costs 0 s, measured; only the absent case spends anything.

