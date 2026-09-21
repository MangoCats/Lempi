# PI004: Operating the Speaker Link

**Appliance Record — what running the Bluetooth speaker actually taught**

Split from [PI003](PI003-choosing-a-speaker.md) on 2026-08-20, which had reached
506 lines against `[GOV-DOC-010]`'s 300-line limit. The seam was already there:
PI003 says what the speaker link must do, and its section 5 -- titled
*"Deliberately not now"* -- had quietly filled with measured findings and things
marked **Built**, which is the opposite of not now.

Everything here is dated and measured. Nothing here is a plan.

> **Related:** [PI003](PI003-choosing-a-speaker.md) for the design and the
> player's contract · [PI001](PI001-image-and-partitions.md) · `[SPEC-APS-060]`
> for the audio path supervisor these findings kept meeting

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §0 and §2-3 in [PI004](PI004-speaker-operation.md), §1 in [PI021](PI021-interference-and-the-antenna.md).

## 0. Where this stands, and where the rest of it lives

This document is the **operating record**: what the speaker link does, how it
fails, and what was built so it stops failing that way. The dated
investigations behind it were split out on 2026-09-08, when this file reached
1,112 lines against `[GOV-DOC-010]`'s limit — [PI009](PI009-the-silence-of-2026-09-08.md),
[PI010](PI010-startup-time-and-stutter.md) and
[PI011](PI011-two-speakers-and-placement.md). Read those as experiment
records: several reach answers a later section overturns, each says so where
it stands, and a paragraph is evidence of what was believed on a date.

Current understanding, for a reader who needs only that:

| Symptom | Cause | Where |
|---|---|---|
| Periodic stuttering for ~3 min after boot | **The Middleton, cold-starting.** Not the room and not this appliance: an Oontz cold-booted alongside the Pi in the same room ran at 100.2% of its own baseline and sounded clean, while the Middleton runs at 87% and stutters, 3 of 3. Unfixed here because it is not this appliance's to fix | `[PI3-FOUND-580]`, [PI011](PI011-two-speakers-and-placement.md) |
| A second speaker steals the audio | **Fixed.** The policy is one speaker at a time, held by whoever has the audio until it goes away, and an agent refuses every audio profile from anyone else before a transport is handed over | `[PI3-AIM-080]`, `[PI3-FOUND-630]` |
| Two speakers connected, silence from both | **The second one took an HSP/HFP link**, and a synchronous link pre-empts A2DP rather than sharing with it: 0 packets against 375 with it disconnected. The keeper now disconnects a speaker holding a link while another plays | `[PI3-FOUND-600]` |
| Connected, progress bar advancing, no sound | **The speaker, not the appliance.** Confirmed by counting packets on the air while every appliance layer read healthy. Fixed by disconnect/reconnect | `[PI3-FOUND-480]` |
| Player wedged after a power cut, 23 restarts | Hot SQLite journal, unrecoverable through a read-only attach | `[PI3-FOUND-120]`, PI009 |
| Speaker never reconnects after a power cycle | The speaker powers the Pi, so the Pi is always late — and it had lost `Trusted` | `[PI3-FOUND-090]`/`-130`, PI009 |
| ~19 s of startup that was not work | Contention with `mpd`, which now yields | `[PI3-FOUND-210]`, PI010 §2 |
| Second speaker silent at full volume | It connected as an HSP headset, not A2DP | `[PI3-FOUND-290]`, [PI014](PI014-two-speakers-and-the-choice.md) |
| Chosen speaker replaced by another | The keeper adopted any connected device | `[PI3-FOUND-310]`, [PI014](PI014-two-speakers-and-the-choice.md) |

**Three theories cost real time and are kept rather than deleted**, each being
one somebody else would reach for: that the 15-second stutter period matched
`BUFFER_FRAMES` (the ring is topped up every 10 ms and never drains on a
cycle); that the boot gate's wait should be shortened (`[PI3-FOUND-260]`); and
that setting an idle I/O class made anything polite (`[PI3-FOUND-250]` — no
scheduler in use honoured it).

**If it is connected and silent, count the packets before touching
anything** `[PI3-FOUND-480]`. Five seconds answers whether the appliance is
transmitting, which is the one thing the sink, the stream, the player and
BlueZ can all report wrongly at once:

    sudo timeout 5 btmon | grep -c 'ACL Data TX'

About 370 means the air is carrying a healthy stream and the fault is in the
speaker -- reconnect it, and if that fails the speaker needs its own attention.
Near zero means the fault is on this side, and the sink and routing checks
above become the ones that matter.

**If stutters return, the first question is how it was power-cycled**
`[PI3-FOUND-350]`. Switching the speaker off cuts the Pi's supply, so both
cold-boot together and the boot stutters for about three minutes; cycling the
Pi alone, leaving the speaker powered, has been clean every time. **And it is
the Middleton, not the mode in general**: an Oontz cold-booted alongside the Pi
in the same room, which is the same condition, ran at 100.2% of its own
baseline and sounded clean `[PI3-FOUND-580]`. So the practical answers are to
use the Oontz, to leave the Middleton powered when restarting the Pi, or to
re-establish the link once the speaker has been awake a while. **It is
currently unmitigated**, deliberately. A once-per-boot redial did stop it, 4 of 4, but was
removed on 2026-09-10 because it cost about half a minute of silence on every
boot including the ones that would have been clean `[PI3-FOUND-450]`.

**One instrument now detects it: `lempi-hci-capture`** `[PI3-FOUND-430]`. A
stutter train reads as a throughput deficit at the HCI layer, and a matched
pair captured on 2026-09-10 settles the mode question in packets: Mode A ran
at 87.1% of the clean rate with a dip every ~15 s, Mode B at 100.1% with none
`[PI3-FOUND-470]`. Severity tracks the ear in both directions, and the link is
either good or bad from its first packets -- it does not degrade. Everything *above* that
layer is still blind: signal level, load and disk I/O all measured *better* on
boots that stuttered, and the player's own underrun counter does not correlate
at all `[PI3-FOUND-420]`. Budget for that before forming a theory -- five have
been published here and withdrawn, every one of them argued from ear-points
alone, which is the thing the capture exists to stop.

Two older answers are **confounded** by that variable and no longer claimed
as causes `[PI3-FOUND-410]`: placement `[PI3-FOUND-320]` (its clean runs were
all the harmless power-cycle mode) and the graph quantum `[PI3-FOUND-330]`
(its evidence came from boots that would have stuttered regardless). Sitting
the Pi on the speaker still costs 11-16 dB of signal and is worth avoiding on
its own terms.

Diagnostic tools, and how to switch them on: [PI020](PI020-diagnostic-tools.md).

---

> **Split on 2026-09-10.** Section 1, the interference survey, is now
> [PI021](PI021-interference-and-the-antenna.md); this document keeps the
> operating record.
## 2. A blocked radio looks exactly like a broken button

**`[PI3-FOUND-050]` `Connect` did nothing because there was no radio.**
*(Diagnosed 2026-08-20.)* The speaker was in pairing mode, its light flashing,
and clicking **Connect** in the settings screen had no visible effect. Nothing
about the pairing was wrong: MIDDLETON was `Paired`, `Bonded`, `Trusted`,
unblocked, advertising A2DP Audio Sink, and the BCM43436 firmware had loaded
cleanly at boot.

The controller was **soft-blocked at the rfkill layer** — `rfkill0: hci0
type=bluetooth soft=1 hard=0` — so `bluetoothctl power on` answered
`org.bluez.Error.Failed` and every connection attempt returned
`br-connection-adapter-not-powered`. Clearing `/sys/class/rfkill/rfkill0/soft`
made the whole chain work first time: powered → connected → PipeWire routed the
player's stream to MIDDLETON → the supervisor logged **`output recovered on
default`** by itself, which is `[SPEC-APS-060]` doing exactly its job.

**`[PI3-FOUND-055]` The setup script could not have fixed it, and was written
not to say so.** The line was `rfkill unblock bluetooth 2>/dev/null || true`,
and **`rfkill` is not installed on this image** — so it found no command,
discarded the error, returned true, and reported success. A step that cannot
fail cannot report failure either. It now writes sysfs, which is always present,
and reports `CHANGED` or `FAILED`.

**`[PI3-FOUND-060]` The state persists, in both directions.** `systemd-rfkill`
saves the block under `/var/lib/systemd/rfkill/` and restores it at boot, which
is how one block outlived every reboot since. It cuts the other way now that the
file reads `0`: the fix survives a reboot, and — because the file is already
written rather than written at shutdown — an unclean power loss too, which is
the failure mode an appliance actually has `[SPEC-DF-094]`.

> **What to check first, next time.** `Powered: no` in `bluetoothctl show` means
> look at rfkill before looking at pairing. The pairing was never the problem
> and would have absorbed an evening.

---

## 3. What was built so it cannot look like that again

**`[PI3-RF-010]` The settings panel shows every radio and whether it is on.**
`GET /audio/radios` fronts a new `radios` verb on `lempi-btctl`, which reads
`/sys/class/rfkill/*` and reports name, kind, soft block, hard block, and one
computed fact — whether blocking it would cut the route the answer is
travelling over. Measured on the appliance:

    {"radios":[{"name":"hci0","kind":"bluetooth","soft":0,"hard":0,"carries_route":false},
               {"name":"phy0","kind":"wlan",     "soft":0,"hard":0,"carries_route":true}]}

The panel reads *"A radio is switched off. Nothing on it can connect until it
is on."* when any is blocked. That one line is the whole of what `[PI3-FOUND-050]`
cost an evening for.

**`[PI3-RF-020]` Bluetooth can be switched from the panel; the route's radio
cannot.** `POST /audio/radio/<kind>/<on|off>`. Kind and state are checked
against closed lists in the player *and* again in the helper, which is the side
that holds privilege.

**`[PI3-RF-030]` The refusal is a property of the route, not a ban on Wi-Fi.**
The helper refuses to soft-block whichever radio carries the default route. On
this appliance that is always `wlan0` — it is the only interface, there is no
ethernet, and switching it off from a web page would sever the connection to
the page holding the switch with no way back but physical access. **On a machine
with wired ethernet the same code permits it**, because there the cost is
nothing. Encoding the condition rather than the platform is what makes it true
in both places.

rfkill is joined to the route through sysfs rather than by assuming `phy0` is
`wlan0`: `/sys/class/net/<if>/phy80211` points back at the wireless phy, and a
Bluetooth radio carries no route at all.

The rule lives in the helper alone. The player does not repeat it, so a second
caller — a person at a terminal — cannot be told something different.

> **Verified on hardware 2026-08-20.** `radios` lists both correctly;
> `radio wlan off` is refused with *"that radio carries the default route"*,
> through the player as well as directly; `radio ethernet off` and
> `radio bluetooth maybe` are refused as a bad kind and a bad state; a
> hardware-blocked radio is refused separately, because it cannot be overridden
> from software at all. The Bluetooth toggle was exercised at the helper —
> off, on, and the speaker reconnected — rather than a second time through the
> route, which would have interrupted music being listened to for a branch
> already proven.

---
