# PI032: Transfers on the Shared Radio

**Appliance Record — what a bulk transfer to a node whose Wi-Fi shares its chip with the speaker does to the music, the speaker and the node's own keepers, from 2026-10-25**

Split from [PI031](PI031-settling-the-shared-radio.md) §7 on 2026-10-26, when
that record reached its 300-line limit `[GOV-DOC-010]`. The decisions of
2026-10-25 about the radio itself stay there; this is what a transfer asks
of it, and the export of 2026-10-26 to lempi02w that showed what it had
missed.

> **Related:** [PI031](PI031-settling-the-shared-radio.md) for the radio's
> settling and the bitpool-cut meter `[PI3-FOUND-870]` ·
> [PI030](PI030-when-the-wireless-froze.md) for the Wi-Fi freezes and their
> watchdog

---

## 1. The hold

**`[PI3-AIM-170]` A bulk transfer to a shared-radio node holds its playback.**
Every byte over Wi-Fi is airtime the speaker's audio does not get, and a
690 MB export under Bluetooth playback froze the Wi-Fi chip twice
`[PI3-FOUND-830]`. The sender asks `POST /transfer/hold` first and
`/transfer/release/<id>` after. The player pauses only if its radio is shared
-- its output a Bluetooth speaker, `radio_shared` in `transfer.rs`; a node
that cannot tell is not paused, after `bose` was, for every upload of its
2026-10-25 deploy -- and it is playing; the last release plays again, unless
the listener already did; a hold never released ends on its own. Held: the
player, fbui and `import_bundle` uploads (`build/lib-defaults.sh`), the star
sync's catalogue parts, `star_distribute`'s uploads and, since 2026-10-26,
the Export page's Send -- `send_bundle.py`'s audio copy, which had held
nothing (`tools/transfer_hold.py`). A player that restarts under a hold
forgets it, so the release then asks it to play. How long a hold lasts, who
may ask for one and what that exposes are written once, in
`player/src/web/transfer.rs`.

## 2. The speaker let go

**`[PI3-AIM-175]` The pause alone freed nothing, so a hold also lets the
speaker go.** Paused, the player feeds the device silence so the link
survives `[PI3-OPEN-020]`, and an SBC packet of silence is the size of one of
music: PI031 §9's cuts ran on through a pause. Since 2026-10-26 the first
hold on a shared radio also releases the output, playing or already paused,
and PipeWire suspends the sink; the last takes it back, paused or not, so a
long pause afterwards still holds the link. Pressing play mid-transfer
reopens it, as the listener's choice. Measured on lempi02w with the Boom 2,
`7ce4499`: 596 packets in 8 s playing, 603 paused, **0 in 10 s held** either
way; the speaker stayed connected, and the stream was back within 5 s of the
release. Not yet measured: whether a long hold's idle sink lets a speaker
switch itself off.

## 3. The keeper stands down

**`[PI3-AIM-176]` While a hold has the shared radio, the speaker keeper does
nothing.** Mid-export on 2026-10-26 the Boom 2 was switched off, and from
18:50 `lempi-speaker` paged it and the two other speakers it knows -- about
18 s a round, every tick for three minutes, then every three minutes, and
from 19:38 every ten -- on the radio the export's Wi-Fi shares. The player
now answers `GET /transfer` with whether a hold is in force and whether one
has the shared radio; while one has, a keeper tick pages nothing and moves
nothing, and says so once at each end. It is asked before the quiet tick
`[PI3-AIM-140]`, so a settled speaker stays settled across a hold, whose
released output would otherwise read as the stream moving. Off a shared
radio a hold leaves the keeper as it was.

## 4. A hold as long as its transfer

**`[PI3-AIM-177]` A long transfer renews its hold.** The player caps a hold
at an hour, and that export outlived it: at 19:44:59 the hold expired
mid-copy, and the player took its speaker back and played on. The Export
page's Send now asks for a ten-minute hold and, while the copy runs, takes a
fresh one every half of a hold's life and releases the one before. Only a
set's first hold pauses and only its last release resumes, so the
overlapping renewals change nothing the listener hears; if the sender dies,
the last hold ends by itself within ten minutes.

## 5. What else waits for a transfer

The integrity check reads nothing on a shared radio while a hold is in force
`[GDE-INT-138]`: the hold's pause had been the pause it waited for, and the
export of 2026-10-26 ran with the card read at 978 kB/s beside it. The Wi-Fi
watchdog no longer takes a link still receiving for a frozen one
`[PI3-FOUND-835]`: mid-export, three gateway pings went unanswered while the
copy arrived at 606 kB/s, and it rebooted the node at the hold's pause.

## 6. The export of 2026-10-26, measured

The exporter's own figure rose from about 0.2 MB/s to 0.44 MB/s over the
evening, as household Wi-Fi was reduced and four Wi-Fi cameras were moved to
5 GHz, which the Pi Zero 2 W does not use. Read on lempi02w during it:

| ruled out | how |
| :--- | :--- |
| the speaker | 0 A2DP packets while held |
| the card | receive queue 0, `tar` waiting for input, iowait about 0 |
| power save | 61-155 kB/s on, off and on again |
| the link rate | data at 26-52 Mb/s, signal −51 to −62 dBm; a 2.0 Mb/s reading was a beacon |
| CPU | 88% idle |

**What limited it: stalls.** Bursts of 485-1,266 kB/s between stretches of
nothing up to 20 s long; gateway pings 2.4-5.5 s and 40% lost during the
copy; TCP's retransmission timeout 5.4 s and then 11.2 s; failed sends 0.7-0.9
a second. Queueing in front of the Pi is the likeliest reading, not
confirmed, and whether lempi02w joins the router or a mesh satellite was not
checked. A rate cap at the sender was estimated at most 30% faster and
deferred by the maintainer; a USB-Ethernet adapter, about 10 MB/s, is the
remedy if large exports become regular.
