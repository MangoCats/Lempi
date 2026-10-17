# LP3002: Power, and the Bluetooth Glitches

**Appliance Record — lp3-wifi playing to a Bluetooth speaker, 2026-10-16..17**

lp3-wifi played to the soundcore Boom 2 with glitches and dropouts the
listener could hear, before and after a reboot. This is what was measured,
what is still open, and what now records it.

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

**Whether they made the glitches is open.** The listener heard the glitches
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
is open. That night ran four times the rate -- 145 to 181 an hour -- and may
have crossed a line today did not; or the cause was not power at all.

## 4. What now records it, and what to look at next

`lempi-health` runs here from 2026-10-17 `[APP-HLT-010]`: one line a minute
in `/var/lempi/health.log`, with the throttle bits, the CPU clock, what is
playing and where, the links, and the events since the line before. A glitch
heard at a given minute is read against that minute's line:

- **Dips there, well above today's one every two minutes:** the supply. The
  fix is a 5.1 V, 2.5 A supply and a short, thick cable. A dip or two alone
  says little: 26 came in 38 glitch-free minutes `[LP3-PWR-030]`.
- **No dips, but `ev_bt` or `ev_underrun`:** the Bluetooth link or the player,
  not the supply.
- **Nothing at all:** the speaker's side. lempi02w is the suspect -- it runs
  from the Boom 2's USB port and still prefers the Boom 2, so its keeper keeps
  paging a speaker that is streaming here; unplugging it for a while tests
  that.
