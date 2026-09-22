# PI021: Interference, and What Shares the Antenna

**Appliance Record — one radio, two protocols, and a room full of others**

Split from [PI004](PI004-speaker-operation.md) on 2026-09-10, which had
reached 367 lines against `[GOV-DOC-010]`'s 300-line limit. PI004 is the
operating record -- what the link does and how it fails; this is the
measured background it fails against.

> **Related:** [PI004](PI004-speaker-operation.md) for operating the link ·
> [PI011](PI011-two-speakers-and-placement.md) for the stutter investigation

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §0 and §2-3 in [PI004](PI004-speaker-operation.md), §1 in [PI021](PI021-interference-and-the-antenna.md).

## 1. Interference, and what shares the antenna

**`[PI3-FOUND-010]` Interference is the cause.** Measured 2026-08-16 with the
dark arm at ten minutes, four times the observed failure interval:

    connected while dark: 200/200
    flow while dark:      2 rows
    errors while dark:    (none)

With Wi-Fi down the link is perfect. With Wi-Fi up it had been dropping every
two and a half minutes all evening. One antenna serves both radios on a Pi Zero
2 W, and every drop today with ssh idle says it is **association and beaconing**
that does it, not traffic -- which rules out the cheapest possible fix.

A control arm at the same ten minutes was owed and never run — moot once the
result below withdrew the finding entirely.

**Withdrawn. `[PI3-FOUND-040]` explains this result without interference.**
The dark arm scored 200/200 because Wi-Fi was down, which meant **nobody could
ssh in** -- and an ssh login or logout is what tore the link down. The
experiment removed the cause along with the radio, and credited the radio.

Every drop "with ssh idle" was idle only in the sense that no bytes were
moving; sessions were opening and closing throughout to take the very samples
that recorded the drops. The measurement was the fault. Nothing here supports
buying a dongle, and the numbers above measure the method rather than the
hardware.

Remedies were proposed, best first: a **USB Wi-Fi dongle on 5 GHz** (the Zero
2 W is 2.4 GHz only), a **USB Bluetooth dongle** giving the radios separate
antennas, or **toggling Wi-Fi off during playback** `[PI3-FOUND-020]` — which
costs no hardware but costs reachability, and an appliance unreachable while
playing cannot be debugged in the state that matters.

> **Partly vindicated, 2026-09-08, by a different mechanism** — the placement
> finding `[PI3-FOUND-320]`, in
> [PI013](PI013-theories-withdrawn.md). The withdrawal above
> is still correct: those numbers
> measured the method, not the hardware, and Wi-Fi *association* was never
> shown to drop the link. But the single shared antenna this section worried
> about does matter, and measurably — resting the appliance on the speaker
> cost 11-16 dB of signal and raised Wi-Fi retries roughly thirtyfold, which
> was audible as stuttering for most of a day. The cheapest remedy turned out
> to be none of the three listed here: move the box eighteen inches.

**`[PI3-FOUND-030]` WirePlumber was demolishing the link, on its own schedule.**
Found 2026-08-16. `bluetoothd` shows the A2DP endpoints being unregistered and
re-registered in sweeps of nineteen, and the media transport rebuilt each time
-- `sep1/fd7`, `fd8`, `fd9`, `fd10` in the space of a minute. A2DP cannot
survive its endpoints being withdrawn, so every sweep is a dropped speaker, and
at rest the sweeps came about every two and a half minutes: **the same period
as the drops, arriving with the radio idle.**

Each sweep pairs, to the second, with

    Failed to get percentage from UPower: org.freedesktop.DBus.Error.NameHasNoOwner

`upower` was not installed. The package alone is not the fix: its unit ships
disabled and static, so D-Bus activation still finds no owner and the error
continues unchanged. It must be `enable --now`.

Two things this cost, worth naming. The single WirePlumber process reports
`NRestarts=0` throughout, so anything watching systemd for restarts sees a
healthy service -- an earlier session recorded "0 restarts" and ruled the
audio stack out on exactly that evidence. The fault is visible only in
`bluetoothd`'s log, from a process that never died. And the drops it causes are
indistinguishable by ear from interference, which is how an entire evening was
spent measuring a radio.

**`[PI3-FOUND-040]` Every ssh login and logout dropped the speaker.** Found
2026-08-16, and the cause of the drops.

WirePlumber gates the whole BlueZ monitor on logind seat state:

    -- /usr/share/wireplumber/scripts/monitors/bluez.lua:285
    logind_plugin = Plugin.find("logind")
    logind_plugin:connect("state-changed", function(p, s) startStopMonitor(s) end)

with `["with-logind"] = true` in `50-bluez-config.lua`, whose own comment
explains the purpose: arbitrating which of several logged-in users owns
Bluetooth audio, "particularly useful if you are using GDM". On an appliance
with one user and no display manager it arbitrates nothing and costs
everything -- stopping the monitor unregisters all nineteen A2DP endpoints,
and A2DP does not survive that.

Measured, with the trigger under our own control rather than waited for:

    before:  20 of 22 endpoint sweeps within 2s of "Removed session"
             (the other 4 all predate the upower fix [PI3-FOUND-030])
    after:   13 session teardowns, 0 unregistrations, link held

`linger` does not help. It keeps the graph alive across logouts; it does not
stop WirePlumber reacting to them. The remedy is one property, in
`/etc/wireplumber/bluetooth.lua.d/51-lempi-no-logind.lua`, applied by
`setup-appliance.sh`.

**What this cost, and why.** Two evenings went to measuring a radio because
checking the link is what broke it: every `ssh pi@lempi02w 'bluetoothctl info'`
opened a session, and closing it killed the speaker seconds later. The drops
therefore tracked *our sampling cadence*, which is why they looked periodic,
and why they always seemed to arrive just after a clean window closed. It is
the third instance in two days of a diagnostic causing the fault it measures
`[GDE-FBD-110]`, and by far the most expensive.

**`[PI3-FOUND-020]` Speaker-side transport controls.** The Middleton's rocker
offers five gestures, which under AVRCP arrive as ordinary key events from a
uinput device BlueZ creates -- play/pause, previous/next, volume. Worth building
regardless of the interference question: an appliance whose only control
surface is a web page is a poor appliance, and it is the thing that makes any
Wi-Fi-off-while-playing scheme usable at all.

**`[PI3-ROCKER-010]` The rocker's assignment. Measured 2026-08-16.** Five
gestures were pressed in order; three arrived as key events and two produced
nothing at all:

| Gesture | Code | Key | Function |
|---|---|---|---|
| Centre press | 200 | `KEY_PLAYCD` | Toggle play/pause |
| Right | 163 | `KEY_NEXTSONG` | Skip, identical to the existing control |
| Left | 165 | `KEY_PREVIOUSSONG` | Reserved for a "like", unassigned for now |
| Volume up | -- | none | Absolute volume, over the media transport |
| Volume down | -- | none | Absolute volume, over the media transport |

The volume silence is the useful half of that result. Volume is not a key event
here: AVRCP carries it as absolute volume on the media transport, so it reaches
the sink without passing through this device at all. The intention to leave
volume alone therefore costs nothing and cannot be got wrong -- there is no
event to swallow by accident, and exactly three signals exist to spend.

Left is deliberately left dead rather than given a placeholder. A control that
does something surprising is worse than one that does nothing, and reserving it
in writing is what stops it being spent on something lesser later.

**`[PI3-ROCKER-030]` The uinput device appears with audio, not with the
connection.** Anything reading it must wait for it, and must wait again when it
goes: it is removed on every disconnect.

**`[PI3-ROCKER-020]` Play and pause switch the radios. Withdrawn 2026-08-16.**
Centre press is a plain play/pause toggle and touches no radio.

The scheme was designed against `[PI3-FOUND-010]`, believing the drops were
interference and the only cure without new hardware was to stop transmitting.
`[PI3-FOUND-030]` then found software destroying the same link on a similar
period, so the share attributable to the radio is unknown — a poor trade
against an unmeasured disease.

Recorded rather than deleted for the three safeties it named, which are the
right ones if it is revived: a failed connection must raise Wi-Fi again, or
the appliance is silent *and* unreachable, reached by the ordinary path of
someone switching the speaker off; the HTTP response must be sent before the
interface drops, or pressing play reads as a crash; and the panel must say
what play will do, because a control that disconnects you is fine when
expected and alarming when not.

**`[PI3-LED-010]` The ACT LED tracks the Wi-Fi radio. Built.** The Pi Zero 2 W
exposes one controllable LED, and the kernel does the entire job: `rfkill1` is
`phy0`, so binding that trigger makes the light follow **the radio itself
rather than our intention about it**. Nothing polls, nothing can drift, and it
stays correct when something other than Lempi switches the radio.

    echo rfkill1 > /sys/class/leds/ACT/trigger

That property is worth more than the convenience. It was built when
`[PI3-ROCKER-020]` meant to take Wi-Fi down during playback, where an
unreachable appliance otherwise just looks broken; that scheme is withdrawn,
and the LED is kept anyway. It costs nothing, it tells the truth about the
radio without anything having to remember to update it, and a status flag we
set ourselves could be wrong in exactly the situation where being wrong costs
most. This one is read from the hardware.

`/sys` does not survive a reboot, so it is a unit rather than a one-off write,
and it hands the LED back on stop. Verified across a real reboot: the trigger
reads `[rfkill1]` and the unit is active.

The cost is the card-activity indication, which shares the one LED. On an
appliance that is a fair trade -- radio state is something a listener can act
on, card access is not -- but it is a real loss when diagnosing a card.

**Polarity still wants one observation.** The trigger should light when the
radio is unblocked, so LED on means Wi-Fi up. Confirming it needs only the next
dark-arm run: watch the LED at the moment the radio drops.


---

