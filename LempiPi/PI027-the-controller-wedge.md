# PI027: The Controller Wedge, and the Chase That Caused It

**Appliance Record — two days of paging killed the adapter, and the log said "speaker"**

The listener put a new Bluetooth speaker into pairing mode four feet from
`lempi02w` and *Find a Speaker* showed nothing. The speaker was fine. The
adapter had been dead for two days, and the keeper had spent those two days
reporting it as a speaker that was switched off.

> **Related:** [PI016](PI016-the-redial.md) for the chase this bounds ·
> [PI015](PI015-the-bluetooth-agent.md) for the agent and for trust `[PI3-FOUND-130]`,
> the inbound path a backoff leaves alone ·
> [PI020](PI020-diagnostic-tools.md) for the instruments that did *not* catch
> this · [PI021](PI021-interference-and-the-antenna.md) for the shared antenna

---

## 1. What the listener saw

*Find a Speaker* returned the two speakers already registered — both switched
off — and nothing else, with no error. Pressed again, same answer. That is
indistinguishable from a scan that ran correctly and found nothing, which is
why it sent the investigation at the speaker first.

**`[PI3-FOUND-740]` A cache was being returned as a scan result.** It is a
shape this project has met enough times to write down as house rule — a
command whose real output is discarded, leaving a plausible answer and a zero
exit status standing in for it — wearing a new costume. `lempi-btctl scan`
sent `bluetoothctl` to `/dev/null` and then called `json_devices`, which
lists BlueZ's *cache*. So a scan that never reached the air still returned
`{"ok":true,...}` with the two remembered devices. Exit 0, plausible content,
no scan — and the panel's own reply, *"put yours in pairing mode, then
look"*, was the most confident sentence of the afternoon. Fixed the same day;
see §5 and `[REQ-VIS-268]`.

## 2. The timeline, from the appliance's own journal

Measured 2026-09-20, from the boot that began 2026-09-17 15:17.

| when | what |
| :--- | :--- |
| 2026-09-18 16:32:55 | first `hci0: command 0x0405 tx timeout` — `HCI_Create_Connection` stops being acknowledged, ~25 h into the boot |
| 2026-09-18 → 09-20 | **7,348** further `tx timeout` lines; the controller never recovers |
| 2026-09-20 13:39:39 | first `hci0: Opcode 0x0401 failed: -110` — the wedge reaches `HCI_Inquiry`, and discovery dies too |
| 2026-09-20 ~13:40+ | **7** inquiry failures, one per press of *Find a Speaker* |

Before the reboot the adapter had transmitted 1,646,286,166 bytes across
2,575,776 ACL packets. `bluetoothctl show` read `Powered: yes`,
`Discovering: no`, and a full, healthy UUID list throughout.

**`[PI3-FOUND-720]` The cause was the keeper's own cadence, and no single
bound was wrong.** Every limit in [`lempi-speaker.sh`](lempi-speaker.sh) is
scoped to one tick: `[PI3-AIM-060]` keeps a chase from interrupting audio,
`[PI3-FOUND-670]` keeps a tick inside its timer's period, `[PI3-FOUND-140]`
keeps two pages from overlapping. Each holds. None of them asks what a
*succession* of failed ticks costs.

With both known speakers switched off, a tick spent 15 s paging the chosen
speaker and up to 10 s paging fallbacks. `lempi-speaker.timer` fires every
30 s; the observed cycle ran ~45 s (14:38:10 → 14:38:55 in the keeper's log),
so the next tick was already due the moment one ended. **The radio had no
idle time for two days.** On a Pi Zero 2 W that radio is also shared with
Wi-Fi `[PI3-RF-010]`.

## 3. Why every instrument said the wrong thing

**`[PI3-FOUND-730]` The keeper logged 4,967 lines about a speaker while the
adapter was the problem.** Each failed tick printed *"did not answer in 15s
(held by another device, or asleep)"* — an accurate sentence about a speaker
that is off, and a completely wrong one about a controller that has stopped
acknowledging commands. Both parentheses were false. Nothing in the script
read `dmesg`, where the truthful line sat the whole time.

Three sources agreed and two of them were lying:

- `bluetoothctl show` — healthy. Served from `bluetoothd`'s state, not the chip.
- `btmgmt info` — returned `name LempiPiHost` correctly. Served from the
  **kernel's cache**, not the chip.
- `hciconfig hci0 name` — `Can't read local name on hci0: Connection timed
  out (110)`. This is the only one that made the controller answer.

That ranking is [GOV002](../docs/GOV002-sources-of-truth.md)'s discipline
applied to an adapter: when several sources answer one question, rank them by
which one actually measured something. `hcitool con`, used by the chase
itself `[PI3-FOUND-140]`, reads the kernel's connection list and never
touches the chip — it answered perfectly throughout and cannot serve as a
liveness probe.

The discovery failure had its own disguise. With the inquiry command timing
out, BlueZ's discovery state machine stuck: every later scan was refused with
`org.bluez.Error.InProgress` *before anything reached the air*, while the
adapter still reported `Discovering: no`.

## 4. Nothing short of a reboot cleared it

Tried in order, with the keeper stopped so nothing competed:

- `hciconfig hci0 name` — still timed out. Not contention.
- `btmgmt find -b` — `Unable to start discovery. status 0x05`.
- `hciconfig hci0 down` succeeded; `hciconfig hci0 up` gave `Can't init
  device hci0: Connection timed out (110)`.
- `systemctl restart hciuart` — the unit does not exist on this image; the
  controller is attached through serdev (`3f201000.serial` → `serial0-0`).
- Even `HCI_Reset` (`0x0c03`) timed out.

After a reboot the adapter answered immediately and the kernel logged no
timeouts. A 25 s unfiltered scan heard 9 devices; a 30 s BR/EDR-only inquiry
heard 0, correctly, because nothing nearby was discoverable on BR/EDR. Once
the listener powered the new speaker on, the same inquiry returned
`soundcore Boom 2` (`2C:8D:48:36:EA:2F`) at RSSI −39.

**The speaker was never the problem, and no amount of looking at it would
have said so.**

## 5. What changed

**The chase now backs off `[PI3-FOUND-720]`.** A counter in
`/run/lempi/chase-misses` records consecutive ticks that paged and got
nothing: the first four page at full speed, then every 120 s, then every
600 s. Any connection clears it. At the far end the radio pages for ~25 s
every 10 minutes — roughly a 4% duty cycle where it was 100%.

This costs the appliance very little, because **outbound paging was never the
only path**. The chosen speaker is trusted precisely so it can reach *us*
unasked `[PI3-FOUND-130]`, which spends no radio time and starts the moment
the speaker wakes; the backoff leaves that untouched. What it gives up is the
power-up race `[PI3-FOUND-090]` — worth chasing hard for the first two
minutes, worth almost nothing an hour later.

**The keeper asks whether the controller is alive before paging it
`[PI3-FOUND-730]`.** `hciconfig hci0 name` runs first; a controller that will not
answer gets a named, loud line pointing at `dmesg` and saying a reboot is
needed, and the tick pages nothing. Where `hciconfig` is not installed the
probe reports `unknown` and the chase proceeds *loudly* rather than
concluding the adapter is dead — a guard that cannot run must say so
`[GDE-DEP-060]`.

Both are covered in [`tests/cases-speaker.sh`](tests/cases-speaker.sh) cases
11–14, against a new [`tests/stubs/hciconfig`](tests/stubs/hciconfig).

**The scan reports its own failure `[PI3-FOUND-740]`, and that is now
required `[REQ-VIS-268]`.** `lempi-btctl scan` keeps `bluetoothctl`'s output
and requires `Discovery started` in it as proof the radio looked. Without it
the answer is `ok:false` carrying BlueZ's own reason, the remembered speakers
are labelled *"not a scan result"*, and a controller failing the health probe
is named — *"the Bluetooth adapter has stopped responding and needs a
reboot"* — because that is actionable where `org.bluez.Error.InProgress` is
not. The health probe is asked only on the failure path, so a working scan
pays nothing for it. [`skin.js`](../player/src/web/skins/lempi/skin.js) shows
`error` ahead of its empty-list message, which is the sentence that did the
damage. Covered in [`tests/cases-btctl.sh`](tests/cases-btctl.sh), including
that a failed scan is still parseable JSON on stdout alone.

**The probe is cheap enough to run during playback.** Measured on `lempi02w`
2026-09-20 against a live A2DP stream to the soundcore: 60 `Read Local Name`
probes at 1 Hz, none slower than 50 ms, zero failures, `underrun_samples`
unchanged at 428930 across the run, transport still `active` afterwards.
That is what makes a standing wedge watcher possible rather than only a
before-we-page check.

## 6. `lempi-btwatch`, and what it will and will not do

**`[PI3-FOUND-750]` A wedge during playback is now noticed, and the adapter
can be reloaded without a reboot.** `lempi-btwatch` runs every minute from
its own timer, as root, because putting the controller back means writing
the serdev driver's `unbind`/`bind`. Its policy is four rules:

- **Three consecutive failures before acting.** The real fault was 7,348
  timeouts in a row and never recovered, so requiring a few costs nothing
  against it and refuses to act on a blip.
- **Never reset while audio is flowing.** This is `[PI3-AIM-060]` applied to
  the repair rather than the chase. A wedged control path means nothing new
  can start — no scan, no pair, no reconnect — but what is already playing
  keeps playing, and every repair here begins by destroying that stream.
  Judged on BlueZ's own `MediaTransport1`, since `Connected: yes` says
  nothing about whether audio is moving `[PI3-FOUND-070]`. It waits for the
  next silence.
- **Reset, then ask the chip again.** Success is the second probe answering,
  never the reset returning.
- **Never reboot on its own.** A watcher that reboots can reboot in a loop,
  and an appliance in a boot loop is a far worse failure than one whose
  Bluetooth is down — the second still answers ssh. It says a reboot is
  needed and stops. `LEMPI_BTWATCH_RESET=0` switches the reset off entirely.

**The reset reloads the firmware, and that was measured.** Unbinding and
rebinding `serial0-0` on `hci_uart_bcm`, 2026-09-20: `hci0` disappeared, came
back, and the kernel logged `BCM43430A1
'brcm/BCM43430A1.raspberrypi,model-zero-2-w.hcd' Patch` — the firmware being
written to the chip, not a driver merely reattaching. Read Local Name
answered afterwards, and the trusted speaker reconnected *itself* five
seconds later with no underruns, which is `[PI3-FOUND-130]`'s inbound path
doing exactly what the backoff above assumes it will.

Verified in place: four timer runs across 200 s of live playback produced no
output and left `underrun_samples` at 428930 with the transport still
`active`. Policy covered by [`tests/cases-btwatch.sh`](tests/cases-btwatch.sh),
which drives the whole thing against a fake serdev directory and a `busctl`
stub — no radio.

## 7. What is still open

- **The reset has never met a real wedge.** It is proven to reload the
  firmware on a *healthy* adapter, which is why it is worth trying first —
  it costs one reconnect where the alternative costs a reboot. Whether it
  clears a chip in the 2026-09-18 state is unknown, and cannot be known
  until that state happens again. The watcher is written so a reset that
  does not work reports failure and names the reboot, rather than reporting
  the reset it performed `[PI3-FOUND-750]`.
- **Why 25 hours.** Whether the wedge is a count of failed pages, a duration,
  or a thermal effect is not known. The backoff makes reaching it far less
  likely without explaining it.
- **The panel cannot trigger a reset.** `lempi-btwatch` acts on its own timer
  only. Now that a scan names a wedged adapter `[REQ-VIS-268]`, the obvious
  next step is a "reset the adapter" button beside that message — which
  means a `recover` verb on `lempi-btctl`, not a second privileged path.
