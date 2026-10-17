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
photograph and put back, and after its 09:49 start the dips all but stopped:
one in the next four minutes, during start-up. The likeliest change is the
micro-USB plug, re-seated -- an inference; nothing measured the plug. Two
things moved, at two different times, and an ear was not there for the
second.

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

## 3. What now records it, and what to look at next

`lempi-health` runs here from 2026-10-17 `[APP-HLT-010]`: one line a minute
in `/var/lempi/health.log`, with the throttle bits, the CPU clock, what is
playing and where, the links, and the events since the line before. A glitch
heard at a given minute is read against that minute's line:

- **Dips there (`ev_uv` above 0, `thr` bit 0 set):** the supply. The fix is a
  5.1 V, 2.5 A supply and a short, thick cable.
- **No dips, but `ev_bt` or `ev_underrun`:** the Bluetooth link or the player,
  not the supply.
- **Nothing at all:** the speaker's side. lempi02w is the suspect -- it runs
  from the Boom 2's USB port and still prefers the Boom 2, so its keeper keeps
  paging a speaker that is streaming here; unplugging it for a while tests
  that.
