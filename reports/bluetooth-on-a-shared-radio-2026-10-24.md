# Bluetooth audio on a shared radio: what breaks it, and what was measured (2026-10-24)

What stands between a Raspberry Pi and clean Bluetooth audio when one chip
carries both Wi-Fi and Bluetooth, gathered from the appliances' own records.
Every claim cites the finding it rests on; the open questions stay in those
documents. Measured on two models only: `lempi02w`, a Pi Zero 2 W, and
`lp3-wifi`, a Pi 3 B (a BCM43438, by its own kernel log). What follows
applies, by construction, to any Pi whose Wi-Fi and Bluetooth share a chip
and an antenna -- the Zero W and Zero 2 W, the 3 B, and the 3 B+, 4 and 5 on
2.4 GHz. That a dual-band model's Wi-Fi on 5 GHz removes most of the
contention follows from the same physics, and has not been measured here.

---

## 1. The one fact everything else follows from

**One radio, time-shared.** Wi-Fi and Bluetooth take turns on one chip and
one antenna. A2DP needs the controller fed continuously -- healthy playback
here runs 41-52 KB/s into it `[PI3-FOUND-810]` -- and anything else that
takes the radio takes that time from the speaker. The listener hears a
stutter, a skip, or half-rate audio.

**The player cannot see it.** When the radio stalls, the player keeps writing,
PipeWire reports no xruns, the transport stays `active`, and the underrun
counter stays still `[PI3-FOUND-300]`, `[PI3-FOUND-340]`. The fault is
measured only at the HCI: the bytes handed to the controller each second.
A full stutter train was a 17% deficit there `[PI3-FOUND-430]`; a page during
playback, half rate `[PI3-FOUND-810]`.

## 2. What takes the radio away

**Searching for a speaker while one plays.** Paging (connecting to an address)
and inquiry (discovery) are the most expensive things the radio does.
- A boot that went on paging an absent preferred speaker while another played
  ran at about 21 KB/s -- half rate -- for 30-40 s, on fifteen of eighteen
  boots `[PI3-FOUND-810]`. Now: a short window for the preferred speaker, then
  whatever connected is kept `[PI3-AIM-110]`.
- Paging a device the controller is already linking to collides with the
  negotiation in flight and delays it `[PI3-FOUND-140]`.
- Paging with no idle time, tick after tick, **wedged the controller** after
  about 25 hours: no command answered, discovery dead, only a reboot cleared
  it `[PI3-FOUND-720]`. Now: misses back the chase off to every 10 minutes, and
  the controller is asked whether it answers before it is paged
  `[PI3-FOUND-730]`.

**Wi-Fi scanning and roaming.** NetworkManager's default background scan,
`bgscan simple:30:-65:300`, means a scan every 30 s below −65 dBm and every
300 s above. On `lp3-wifi`, at −66 to −72 dBm and with wpa_supplicant unable
to monitor the signal, scans were measured 300 s apart twenty minutes after
its last reassociation; that it scans every 30 s for a while after each
reassociation is how `bgscan simple` behaves, inferred and not captured. With
four access points sharing one network name, a weak node roams, and each roam
is a reassociation. There, glitches were heard well into the third song after
a speaker connected, while Bluetooth was measured asking for nothing
`[LP3-RAD-010]`. Nothing in this
project sets `bgscan`, locks an access point, or chooses the Wi-Fi band.

**A weak link.** Signal strength predicts the stutter better than anything
else measured: −50 dBm clean, −59 to −66 dBm stuttering, with the box in the
same place `[PI3-FOUND-340]`. A Pi resting on its speaker -- magnet, driver,
grille, and the speaker's own radio a couple of inches away -- stuttered every
~15 s with Wi-Fi retries up some thirty-fold; eighteen inches clear, none
`[PI3-FOUND-320]`.

**A second connected speaker.** Not degraded audio: none. With a second
speaker holding a link, the playing one received zero bytes
`[PI3-FOUND-600]`. One speaker at a time, enforced `[PI3-AIM-070]`.

**Software that disturbs BlueZ.**
- WirePlumber tied its Bluetooth monitor to login seats, so every ssh login
  and logout withdrew all nineteen A2DP endpoints and dropped the speaker
  `[PI3-FOUND-040]`; a missing `upower` caused sweeps every 2.5 minutes
  `[PI3-FOUND-030]`.
- The speaker keeper's own polling was audible: stutters landed on its 30 s
  ticks until four `bluetoothctl` calls a tick became one `[PI3-FOUND-230]`.
- Watching over ssh perturbs what is being watched `[PI3-FOUND-170]`.

## 3. Modes: how the pair powered up

When the speaker powers the Pi from its USB port, switching the speaker off
cuts both, and the two restart together **(Mode A)**; cycling only the Pi
leaves the speaker up **(Mode B)**. Mode A stuttered at ~15 s spacing for
three minutes and more; Mode B, none `[PI3-FOUND-350]`. A redial -- dropping
and reconnecting the link -- restored the full rate and held
`[PI3-FOUND-440]`, `[PI3-FOUND-400]`.

The speaker keeps its adapted channel map across a Mode B restart
`[PI3-FOUND-510]`, but handing the controller a saved map did not cure
Mode A: the seed reached the controller, the controller excluded channels of
its own within thirty seconds, and the audio stuttered as before -- **a
narrow channel map is a symptom of failing packets, not their cause**
`[PI3-FOUND-530]`. Tried again on `lp3-wifi` on a reconnection: the seed held
about ten more channels, and neither arm stalled `[LP3-RAD-020]`.

Speakers differ too. One stuttered where another sounded clean at the same
byte rate, so the absolute rate is not the predictor `[PI3-FOUND-570]`; on one
occasion the stutters were the speaker's, not the Pi's `[PI3-FOUND-300]`.

## 4. Power and the chips themselves

**The supply.** `lp3-wifi`, powered from its speaker's USB port, went below the
Pi's threshold over a thousand times in a night and skipped; a USB-C charger on
a shorter cable ended the skipping `[LP3-PWR-040]`. Dips alone, at about forty
an hour, left the audio clean `[LP3-PWR-030]`.

**Wi-Fi power save** stays on where the Pi runs from the speaker's battery, by
decision `[PI3-FOUND-780]`; its effect on coexistence has not been measured.

**Silent chip failures.** The Wi-Fi chip's SDIO link froze twice with nothing
reporting the link down, while Bluetooth played on `[PI3-FOUND-830]` -- now a
watchdog reboots at the next pause. The Bluetooth controller's wedge
`[PI3-FOUND-720]` is now noticed during playback `[PI3-FOUND-750]`.

## 5. What makes a fault hard to see

- **A2DP hangs up when nothing feeds it**, with the speaker's disconnection
  tone, so a player that stopped sounds like a Bluetooth fault
  `[PI3-WHY-030]`.
- **Pi-side counters stay clean through radio stalls** (§1). Measure at the
  HCI instead: bytes per second to the controller (`lempi-hci-capture`, or
  `btmon` counted per second, with silent seconds as zero), the link's channel
  map (`hcitool afh`), Wi-Fi scans (`iw event`), and signal (`iw dev wlan0
  link`).
- **At least seven theories were withdrawn** on the way here `[PI3-FOUND-530]`; read
  [PI013](../LempiPi/PI013-theories-withdrawn.md) before proposing an eighth.

## 6. Rules that follow

1. Never page or scan for a speaker while one is playing; back off when
   pages fail; never page a device already linking.
2. One connected speaker at a time.
3. Keep the Pi off the speaker, and the Wi-Fi signal strong: at −50 dBm the
   links here were clean, in the −60s they were not.
4. Give the Pi a supply that holds under load -- not a speaker's USB port.
5. Keep host software off BlueZ's back: no seat monitoring, `upower` present,
   one `bluetoothctl` per poll at most.
6. Judge a fix at the HCI, against a control, not by the player's counters.

**Open, and owned elsewhere:** the 30 s Wi-Fi scan phase captured directly,
and whether locking an access point or tuning `bgscan` ends the post-connect
glitches `[LP3-RAD-010]`; a seed trial over a boot rather than a reconnection
`[LP3-RAD-020]`; the cause of the Mode A stutter itself `[PI3-FOUND-530]`.
