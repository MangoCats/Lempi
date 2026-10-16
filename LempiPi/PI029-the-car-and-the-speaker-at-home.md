# PI029: The Car, and the Speaker That Was at Home

**Appliance Record — lempi02w driven for three days, 2026-10-12..14**

lempi02w rode in a car for three days, playing through a Bluetooth-to-FM
converter (a JR-CCB01). Everything worked, except that the first thirty
seconds after every start skipped. Read back on 2026-10-15, the appliance's
own logs said why: the keeper was paging the speaker at home while the
converter played.

> **Related:** [PI018](PI018-the-sink-gate.md) for the gate and the keeper ·
> [PI014](PI014-two-speakers-and-the-choice.md) for incumbency ·
> [PI027](PI027-the-controller-wedge.md) for the chase's backoff ·
> [PI022](PI022-the-players-speaker-contract.md) for `GET /audio/sink`

---

## 1. What the logs showed

**`[PI3-FOUND-810]` The keeper decided once whether paging was safe, before
the player had started, and never asked again.** Three logs, each answering a
different part of the question.

*The radio.* `lempi-hci-capture` counts the audio bytes handed to the
controller each second for the first three minutes of a boot
(`/var/log/lempi-hci.log`). Healthy playback runs 41-52 KB/s. Of the
eighteen boots logged from 2026-10-10 on, fifteen spent 16-32 of the seconds
between 25 s and 70 s below 30 KB/s: audio started at about 28 s and ran at
roughly 21 KB/s -- half rate -- until 60-65 s, then healthy for the rest of
the window. Three spent 2-8 seconds there: the boot at home on 2026-10-15,
and two whose whereabouts the logs cannot tell, since the clock was wrong.
Half the airtime is what one radio shared between a stream and a page looks
like.

*The keeper.* Six of those boots still had their user journal. Four read as
below, to within a second or two; in the other two the converter connected a
few seconds later, after the gate had already released the player onto the
dummy, and the paging overlapped less of the audio.

| s | what happened |
| ---: | :--- |
| 24 | `lempi-speaker` runs: nothing connected, player not started, so the full 15 s chase is allowed |
| 25 | the converter connects **itself** |
| 27-30 | `lempi-wait-sink` releases the player onto the converter's sink; audio starts |
| 24-42 | the keeper pages the speaker at home, which is not in the car |
| 42-50 | the fallback `[PI3-FOUND-560]` pages a second absent speaker |
| 54 | the fallback reaches the converter, already connected, and asks for a reopen |

The guard against exactly this, `[PI3-AIM-060]`, was read at 24 s: the
player was not yet on a real sink, so nothing was audible and paging cost
nothing. Four seconds later that was false, and nothing inside the chase
loop or the fallback looked again. The chase asked only whether the
*preferred* speaker had a link; the converter's link went unnoticed.

*What it was not.* The player's startup rebuild runs 33-41 s on every boot,
but it ran 35 s on the home boot of 2026-10-15 too, with clean audio. Wi-Fi
scans for a network it never finds in a car, and shares the radio, but
between 70 s and 190 s the eighteen boots show one slow second between them,
and a scan does not stop at a minute.

*A record that nearly was not there.* Only the last boot with a system
journal survived, and six with a user journal. Every boot in a car ends in a
power cut, so the next one starts a fresh 8 MB journal file, and
`SystemMaxUse=64M` holds about eight of them. The HCI and startup logs are
plain files and kept every boot. Not changed here.

## 2. What changed

**`[PI3-AIM-110]` The preferred speaker gets five seconds, then the speaker
that is connected keeps the audio.** The listener's request of 2026-10-15.
The chase budget, 15 s since `[PI3-FOUND-670]`, is now a setting: *Preferred
speaker wait* on the Settings page, 1-60 s, default 5, stored as
`preferred_speaker_wait_s` in `listener.db`. The fallback keeps a fixed 10 s.
A wait longer than about 20 s makes a tick outlast the timer's 30 s period;
that only delays the next tick, and the keeper unit's `TimeoutStartSec` is
raised from 45 s to 100 s so a long tick is not killed half-way.

Within that window, a speaker that connected on its own does not win
outright: nobody holds the audio yet and the player is not on a real sink,
so paging the preferred speaker interrupts nothing. When the window ends, a
speaker that is connected is held and nothing more is paged. The chase, the
fallback before every page, the backoff and the wedged-controller exit all
ask the same question -- *has anything connected?* -- and stop for it. Once
the player is audible on a speaker, that speaker is kept and the preferred
one is never paged over it.

The floor is the radio's: one page that goes unanswered costs the
controller about 5 s, so a 1 s wait still spends that one page.

**`[PI3-AIM-120]` The player waits until the speaker is settled.**
`lempi-wait-sink` used to release the player onto any real sink after 3 s.
It now releases at once onto the preferred speaker, or onto whichever
speaker the keeper has claimed as the incumbent -- and the keeper claims the
moment it decides, not on its next tick. At boot this costs a few seconds of
silence, since the player is not ready before about 27 s anyway, and in
return nothing plays while the radio is paging. Two bounds keep
`[PI3-FOUND-550]`'s promise about the web interface: with no real sink at all
it still gives up at 5 s, and a real sink the keeper never claims is taken
after the preferred wait plus ten seconds, with a line saying so.

**`[PI3-AIM-130]` A speaker played on for five minutes becomes the preferred
one.** Counted by the keeper in time *heard*: a tick adds the seconds since the
last one only when the player was playing into that speaker at both ends.
`GET /audio/sink` now carries `"playing"` for that, so a shell script need not
hold the websocket open. A gap longer than 90 s is not counted, because the
Pi has no clock of its own and chrony steps it. At five minutes the keeper writes
`speaker_address`, and `active_output` too where that already named a
speaker. It never does so on a choice it could not read `[PI3-FOUND-700]`,
nor over an address pinned in the environment.

This overrides a choice the listener made, which `[PI3-FOUND-310]` refused to
do for a speaker that merely connected. The five minutes are the difference:
listening is a choice made with the ears.

## 3. What it costs

- **Changing places costs one wait each way.** Back home after the car, the
  converter is preferred: the first boot looks for it for 5 s, then falls
  back to the home speaker, and five minutes later the home speaker is
  preferred again.
- **A slow preferred speaker can lose.** The power-up race `[PI3-FOUND-090]`
  is now run for 5 s rather than 15. If another known speaker answers first,
  it holds the audio, and after five minutes of playing it is the preferred
  one -- so getting the first one back means choosing it by hand.

## 4. Not yet measured

Written and tested against the stubs (`LempiPi/tests/run speaker`,
`waitsink`) on 2026-10-15, and deployed to lempi02w that evening on
`5331809`, where a keeper tick held the Boom 2 as before. Not yet measured in
the car. The Wi-Fi failover reached lempi02w the same evening: with no known
network in a car it raises `lempi-ap` about a minute after boot, on the radio
the audio shares, which the same log will show too. The proof is the same
`lempi-hci.log` that found it: a car boot whose bytes per second are healthy
from the moment audio starts, and a journal reading *"did not connect within
5s; staying with JR-CCB01, which is connected"*.

## 5. A node with no DAC

**`[PI3-FOUND-820]` The player could store "dac" on a node that has none, and
the keeper then stood aside for it.** Found reviewing this change against its
documentation, 2026-10-15; not seen to happen on lempi02w, which had no
`active_output` stored. Four paths wrote it: a speaker chosen in Settings that
failed to connect, forgetting the speaker in use, the DAC entry -- drawn on
every node -- and a "Can you hear it?" that ran out with no earlier speaker to
go back to. `lempi-speaker` exits at once whenever the output is "dac"
`[SPEC-POV-020.3]`, so on lempi02w one failed choice in the car would have
left nothing reconnecting any speaker until a later choice succeeded.

*What a DAC is.* A sink PipeWire offers whose node name is `alsa_output.*`,
read from `pw-cli ls Node` by the player (`sink::local_output`) and the
scripts (`lempi_local_sink`) alike. Not the kernel's card list: lempi02w's
kernel has an HDMI card, but with no display PipeWire offers no sink for it.
Read on 2026-10-15, lempi02w offered only `bluez_output.*` and lp3-wifi an
`alsa_output.*` for its headphone jack, so only lempi02w changes behaviour.

*Fixed both ways.* The player never stores "dac" where there is none: a
failed choice changes nothing, forgetting the speaker in use clears the
choice, `POST /audio/output/use/dac` answers `409`, and every speaker reply
carries `has_dac` so the skin leaves the entry out. And a "dac" already stored
is ignored where there is none: the keeper goes on looking for a speaker, and
says so once a boot; the gate does not take the first real sink on its
strength. Where PipeWire cannot be asked, the answer is *unknown*, and both
keep the behaviour they had before, so a host that cannot tell is never
treated as one without a DAC.
