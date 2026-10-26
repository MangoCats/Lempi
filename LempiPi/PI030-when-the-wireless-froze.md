# PI030: When the Wireless Froze

**Appliance Record — lempi02w's Wi-Fi chip, twice in one night, 2026-10-15**

lempi02w dropped off the network twice in two hours while its music played
on. Neither time did anything on the node notice. This is what the logs
showed, what is known about it elsewhere, and what was built so the next
one clears itself.

> **Related:** [PI027](PI027-the-controller-wedge.md) for the Bluetooth half
> of the same chip wedging · [PI029](PI029-the-car-and-the-speaker-at-home.md)
> for the evening's other work ·
> [FLEET001](../fleet/FLEET001-the-fleet.md) for the node

---

## 1. Two freezes, both silent

**`[PI3-FOUND-830]` The Wi-Fi chip's SDIO link froze, and nothing reported
the link down.** Twice:

| when | what the kernel said, and only that |
| :--- | :--- |
| 21:50:28 | `mmc1: Controller never released inhibit bit(s).`, then `brcmf_sdio_read_control: read 2048 control bytes failed: -5` |
| 23:27:48 | `brcmf_sdio_bus_rxctl: resumed on timeout`, after three firmware commands had timed out (`-110`) at 22:39, 23:09 and 23:27 |

The first came 63% of the way through a 690 MB export, written to the SD card
at 0.37 MB/s. The second came twenty minutes after the re-run's copy had
finished. Both times the integrity check was reading the card at 1 MiB/s
and Bluetooth audio was playing; Wi-Fi power save was on, as it is here
deliberately `[PI3-FOUND-780]`; `vcgencmd get_throttled` read `0x0`.

After each, the driver said nothing more and NetworkManager logged nothing:
as far as the node knew, wlan0 was still connected. So the travel failover,
which believes NetworkManager, never acted -- and could not have helped,
since an access point needs the same frozen chip. Bluetooth, on its own UART,
played on. The first freeze lasted 45 minutes until a power cycle; after
the second the log shows no sign of recovery before the node was next
started, about ten hours later.

**A third, 2026-10-16 18:12:57, recovered by itself.** The first night's
signature exactly -- inhibit bit, `-5`, abort -- nineteen minutes after a
player deploy rather than during one, and without a reboot: SSH reached the
node at 18:36 on the same boot. So the kernel's lines alone do not mean a
link that stays down, which is why the watchdog asks the router too.

## 2. What is known elsewhere

Open upstream, unexplained, with no fix:
[raspberrypi/linux#5770](https://github.com/raspberrypi/linux/issues/5770)
(Zero 2 W, large transfers to SD or USB) and
[raspberrypi/firmware#1962](https://github.com/raspberrypi/firmware/issues/1962)
(Zero 2 W, AirPlay streaming). A maintainer wrote in #5770 that he had
never seen the error. Reported as useless there: `over_voltage=2`, lower
clocks, a stronger supply, power save off. Reported as helping: going back
to an older kernel or image (6.1 or 6.6, where 6.12 failed) -- by two people,
not confirmed by anyone else.

A theory put to us on 2026-10-15, checked against this node:

- **64-bit timing:** not supported -- #5770 saw it on 32-bit too.
- **SD writes and Wi-Fi saturating one bus:** the two are separate
  controllers here (`mmc0` on `sdhost-bcm2835`, `mmc1` on `mmc-bcm2835`), and
  this node froze at a tenth of the rate (~3 MB/s) one reporter needed.
- **`dtparam=sdio_overclock=25`:** a real parameter -- the SDIO/WLAN clock
  used when 50 MHz is asked for, which is what `mmc1` runs at -- but nothing
  ties it to this fault.
- **Downloading into RAM via `/tmp`:** `/tmp` is on the card here, and 185 MB
  of 464 MB was available.

## 3. What was built: a watchdog, and a reboot at the next pause

`lempi-wifiwatch`, every minute, as root. A freeze is **two things at once**:
an SDIO or brcmfmac failure in this boot's kernel log (both freezes' lines,
and any brcmfmac `-5` or `-110`), and a router that has not answered three
checks running while NetworkManager still calls wlan0 connected. Either
alone is not one: two of the `-110`s came while the link still worked, and a
router being restarted is silent too.

Then a reboot -- **at the next pause**, the listener's choice: never in the
middle of a song, and a player that answers without saying whether it is
playing is waited on, never taken for a pause. Not within fifteen minutes of
boot, so a freeze that comes straight back cannot loop. It says each step
once. No soft repair is attempted: upstream, reloading the driver did not
bring a hung controller back.

**`[PI3-FOUND-835]` It rebooted a working link, so a link still receiving is
not frozen.** 2026-10-26 17:56:52, mid-way through an export from the Export
page: three unanswered checks and an SDIO error somewhere in that boot's log
-- which line, and when, is lost; the journal kept six kernel lines of it. At
17:56:08, between the second miss and the third, the copy was arriving at
606 kB/s and an ssh session worked. Likeliest, not measured: the pings waited
behind the transfer's own traffic past their 3 s. The transfer hold had
paused the music `[PI3-AIM-170]`, which is the pause the reboot waits for.
Since then every check records what `wlan0` has received, and a link that
took in 1 MiB since the last check is busy, not frozen: the slowest export
brings in about 12 MB a minute, and a frozen chip nothing. An idle link
takes in less, so there the test is as it was.

## 4. Next, one variable at a time

The cause is unknown, so each change is a test, run one at a time so a
result can be attributed to it:

1. **Baseline.** Count freezes as they come, with the watchdog's lines as
   the record.
2. **No integrity reads during a transfer** -- `integrity_check_kib_per_s`
   at `0` for the length of an export.
3. **`dtparam=sdio_overclock=25`**, through `setup-appliance.sh`, never by
   hand. At −76 dBm the link carries far less than 25 MHz allows.
4. **Power save off for the length of a transfer only**, then back on.
5. **A rate limit on exports** -- weakest: this node froze at 0.37 MB/s.
6. **The kernel version**, the strongest lead upstream and the largest change,
   on a node kept as it is on purpose `[FLT-ISS-040]`.

Not proposed: a 32-bit kernel, caching a transfer in RAM, or chunked writes
with `flush()`, which only moves data into the kernel's page cache.
