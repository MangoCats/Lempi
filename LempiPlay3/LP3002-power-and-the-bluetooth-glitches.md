# LP3002: Power, and the Bluetooth Glitches

**Appliance Record — lp3-wifi playing to a Bluetooth speaker, 2026-10-16..17**

lp3-wifi played to the soundcore Boom 2 with glitches and dropouts the
listener could hear, before and after a reboot. This is what was measured,
what it turned out to be -- the supply -- and what now records it.

> **Related:** [LP3001](LP3001-the-node-and-its-units.md) for the node ·
> [appliance/README](../appliance/README.md) for the health log, `[APP-HLT-010]` ·
> [PI030](../LempiPi/PI030-when-the-wireless-froze.md) for the Wi-Fi chip's
> other failure

---

## 1. The supply sags under load

**`[LP3-PWR-010]` lp3-wifi's supply fell below the Pi's threshold over a
thousand times in one night.** The kernel's own count, `hwmon: Undervoltage
detected!`:

| when | events |
| :--- | :--- |
| boot of 2026-10-16 21:20, to 09:27 next morning | 1,104 -- 145 to 181 an hour all night |
| first | 21:39:51, a minute after a player restart and its library rebuild, half an hour before the Boom 2 was paired (22:09) |
| last | 04:36:33, after the Boom 2 had dropped and playback ended; none for the five hours after |
| boot of 09:29 | 12 in its first four minutes |

At 09:35 `vcgencmd get_throttled` read `0x50005` -- undervoltage and
throttling both in force -- and the CPU was held at 600 MHz. The night's one
Bluetooth transport failure, `bluez_output … running -> error` at 23:51:26,
fell among the dips. So the sags follow load, not the speaker: they came with
playing and stopped with it.

**Whether they made the glitches was left open here**, and is settled by a
change of supply `[LP3-PWR-040]`. The listener heard the glitches
almost stop from about 09:36, while the dips went on (17 in the five minutes
before, 11 in the five after). Then lp3-wifi was taken out of its case for a
photograph and put back, and after its 09:49 start the dips all but stopped
for a quarter of an hour. That was read at the time as a re-seated
micro-USB plug, and it was not: they were back at 10:15 under the load of an
install (3 and then 5 in a minute) and went on at about one every two
minutes. Over 09:54-10:39, 21 events in 14 of 45 minutes.

## 2. The display, and its backlight

**`[LP3-PWR-020]` The backlight cannot be switched, and the driver's pin for
it is not wired.** The screen is a generic "3.5inch RPi Display" -- ILI9486
panel, XPT2046 touch, an AMS1117 regulator making its 3.3 V from the Pi's
5 V -- with no backlight switch, jumper or transistor. It runs on the
`piscreen2r` overlay, written for OzzMaker's PiScreen, whose `led-gpios` is
GPIO 22. Tested 2026-10-17: GPIO 22 held low for five minutes, and the
backlight stayed on. Switching it off on idle, waking on touch, would need a
transistor in the backlight feed and a free GPIO -- worth building only if
the screen's draw is shown to matter.

## 3. Measured: the Pi's side keeps up, and the dips alone are not glitches

**`[LP3-PWR-030]` Undervoltage at about forty events an hour leaves the audio
clean.** Three measurements, 2026-10-17, each on the Boom 2 playing:

- **The player's buffer never ran short.** Its ring was about 15 s deep
  (`depth=659968` frames). Since the 09:49 start it had counted 23,904
  samples of underrun -- half a second, all at start-up -- and the whole of
  the glitching night three short ones. PipeWire showed no errors on the
  player's stream or the Bluetooth output, the latter busy about 1 ms of
  each 46 ms cycle.
- **What reached the radio held steady through the dips.** 12:00-12:10,
  counted with `btmon` (`LempiPi/lempi-hci-capture`): 76 packets and
  46.5 KB a second, every second, with 12 undervoltage events and 41 seconds
  of throttling in the window -- the same rate within three seconds of each
  dip as everywhere else. A second capture, of ten seconds, counted 750
  packets sent and 375 completion reports, each of those read covering two.
  Completion is not delivery: these packets are flushable, so a packet the
  air could not carry is completed too.
- **An ear, 13:02-13:41: no glitch heard.** In the same 38 minutes the
  health log counted 26 undervoltage events in 22 of them, and four of its
  samples found the CPU held to 800 or 900 MHz. Nothing else at all: no
  transport error, underrun, recovery or xrun.

So a larger buffer would not help: everything before the radio keeps up,
and the only buffer that could bridge a gap in the air is the speaker's own,
which A2DP cannot fill ahead of time. What the glitches of the night were
was left open: that night ran four times the rate -- 145 to 181 an hour --
and may have crossed a line this test did not. The evening answered it.

## 4. The verdict: the supply

**`[LP3-PWR-040]` The skipping was the supply, and a USB-C charger on a
shorter cable ended it.** By the evening of 2026-10-17 lp3-wifi was powered
from the Boom 2's USB-A port, the Boom 2 itself on its charger -- battery
reported full (BlueZ `0x64`), its volume unchanged all evening. Then three
supplies, one after another, under the same load -- playing to the Boom 2,
the screen on:

| supply | measured | heard |
| :--- | :--- | :--- |
| the Boom 2's USB-A port | undervoltage climbing all evening: 79 an hour at 19:00, then 126, 149, 178 by 20:30, and 186-189; one A2DP transport failure at 22:04:03 (`error 24`), the speaker back at 22:04:17 | skipping, a lot of it |
| another charger's USB-A port, boot 22:38:28 | 13 dips in about 16 minutes, but 894 s of them below the threshold; `get_throttled` `0x50005` -- undervoltage and throttling in force -- at every check | clean |
| one wall charger's USB-C output, a shorter cable, boot 22:56:28 | no undervoltage at all; `0x0` at every minute, 22:57-23:12, playing throughout | clean |

The Boom 2's port could not hold a Pi 3 B and its screen above the Pi's
4.63 V threshold, and held it less well as the evening went on. The skipping
came at the rates the night had, 145 to 189 an hour, and not at the forty an
hour of `[LP3-PWR-030]`; where the line lies between those is not measured.
The night of 10-16 is most likely the same cause, though what fed lp3-wifi
then is not recorded here.

The second supply is the warning in this table. It sat below the threshold
rather than crossing it, so it counted few events while the CPU stayed
throttled the whole time -- clean for a quarter of an hour, and no supply to
keep. **Read the event count with `thr=` and the time spent below the
threshold, never alone.**

lempi02w had run from the Boom 2's port, and has been off the network since
the evening of 10-16 -- most likely unpowered since lp3-wifi took the port.

## 5. What now records it, and what to look at next

`lempi-health` runs here from 2026-10-17 `[APP-HLT-010]`: one line a minute
in `/var/lempi/health.log`, with the throttle bits, the CPU clock, what is
playing and where, the links, and the events since the line before. A glitch
heard at a given minute is read against that minute's line:

- **Dips there, well above one every two minutes, or `thr=` showing
  undervoltage now:** the supply. The fix is a 5.1 V, 2.5 A supply and a
  short, thick cable, as it was `[LP3-PWR-040]`. A dip or two alone says
  little: 26 came in 38 glitch-free minutes `[LP3-PWR-030]`.
- **No dips, but `ev_bt` or `ev_underrun`:** the Bluetooth link or the player,
  not the supply.
- **Nothing at all:** the speaker's side. lempi02w is the suspect whenever
  it is powered near the Boom 2 -- it still prefers the Boom 2, so its keeper
  keeps paging a speaker that is streaming here; unplugging it for a while
  tests that.

## 6. Glitches with a good supply: the radio, 2026-10-23

**`[LP3-RAD-010]` After a connection, Wi-Fi's own background scanning is the
likeliest source, and nothing of this project's sets it.** The listener heard
glitches well into the third song after connecting the Boom 2. Measured on
the node, `aabc18d`, twenty minutes after boot:

- **Bluetooth asked for nothing.** `btmon`, three minutes: no inquiry, page,
  remote-name request or LE scan; BlueZ `Discovering: no`. The only commands
  were `lempi-btwatch`'s `Read Local Name`, once a minute. The keeper
  (`lempi-speaker`) pages only while nothing is audible, and with a speaker
  holding the audio its tick takes the `hold` path and exits.
- **Wi-Fi scanned every 300 s.** `iw event`, ten minutes: scans at 16:28:57
  and 16:33:58 and none between, at −66 to −72 dBm.
- **NetworkManager's default `bgscan simple:30:-65:300`** is what runs: below
  −65 dBm it scans every 30 s, and wpa_supplicant logs *"Failed to enable
  signal strength monitoring"*, so it cannot react to the signal. The 300 s
  seen above is its backed-off state. The 30 s phase after each association
  was not observed directly; it is how `bgscan simple` behaves, and it fits
  the journal: two reassociations in the five minutes after boot (16:11:11,
  an authentication timeout; 16:13:55, a roam). The network has four access
  points sharing `MangoCats-g` on channel 1. On a Pi 3 the Wi-Fi and
  Bluetooth radios share one chip and antenna, so each scan takes the radio
  from the speaker.
- **The Bluetooth channel map was at 21 of 79 channels**
  (`0x000080fc0000fc3c3300`), against a minimum of 20: the low band, Wi-Fi's
  channel 1, and much of the rest excluded. A link with that little room to
  hop has little left for anything that interrupts it.

Not yet tried: locking the connection to one access point (stops the roams
that restart the 30 s phase), turning `bgscan` down through a hook, Wi-Fi
power save off (on now, the default). The seed `[LP3-BT-020]` is installed for
its own trial. Not checked: whether `lempi02w`, which still prefers the Boom 2,
was powered and paging it -- it did not answer that afternoon.

**`[LP3-RAD-020]` The seed, tried on a reconnection: it kept more channels, and
neither arm stalled (2026-10-23, evening).** The map saved from the settled
link was 54 of 79 channels, only 2402-2426 MHz excluded -- Wi-Fi's channel 1.
Two five-minute arms, the Boom 2 disconnected and reconnected before each;
measured were the A2DP bytes handed to the controller per second (`btmon`,
every second counted, a second with nothing sent as zero), the link's map
every 10 s, and Wi-Fi scans:

| arm | map over 5 min | median | seconds under half the median | Wi-Fi scans |
| :--- | :--- | ---: | ---: | ---: |
| no seed | 48 → 20-22 | 38,313 B/s | 0 | 1 |
| seed applied | 51 → 30-31 | 45,900 B/s | 0 (the first, partial second aside) | 1 |

**What it shows.** With the seed the controller still excluded channels as it
went, as on lempi02w `[PI3-FOUND-530]`, but held about ten more at five
minutes. Neither arm had a second the radio refused data in, so neither shows
the glitch. The medians differ more than a map should explain -- likelier the
bitrate each connection negotiated -- and are not read as the seed's.

**Why it does not settle the glitches.** A Bluetooth reconnection does not
make Wi-Fi reassociate, so both arms ran in Wi-Fi's backed-off 300 s phase
(one scan each), not the 30 s phase suspected after a boot or a roam
`[LP3-RAD-010]`. One-second buckets also hide a stall shorter than about half a
second. The trial is on the seed alone; afterwards the host classification
was cleared to all channels, the unit left disabled, and the saved map kept on
STATE for a trial that reproduces the conditions -- a boot, with the unit
enabled.
