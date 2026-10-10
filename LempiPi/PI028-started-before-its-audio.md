# PI028: Started Before Its Audio, and the Door That Only Opened One Way

**Appliance Record — what one reboot of lp3-wifi found, 2026-10-10**

lp3-wifi was brought level with the repository and rebooted to prove it had
kept everything. It had. It also came up silent, and the player spent the
next seven minutes writing music nobody heard into the listener's history.
Reading the travel Wi-Fi failover for the same day's question -- can we tell
who is on the access point? -- found that its way home had never worked.

> **Related:** [PI026](PI026-startup-preflight.md) for what runs before the
> player starts · [PI020](PI020-diagnostic-tools.md) for the quiet timer
> ticks `[PI3-FOUND-760]` · [SPEC061](../docs/spec/SPEC061-travel-wifi-failover.md)
> for the failover · [FLEET001](../fleet/FLEET001-the-fleet.md) for the node

---

## 1. The player that started before PipeWire

**`[PI3-FOUND-790]` A first open that failed was permanent, and it raced.**
The boot, from the journal:

    17:35:28  lempi-wait-sink: no real sink after 5s; starting anyway
    17:35:29  systemd: Started lempi.service
    17:35:31  lempi: no audio device (... 'Host is down (112)'); running without output
    17:35:33  systemd: user@1000.service active      -- pi's session, where PipeWire runs

(The clock reads September: the Pi has no clock of its own, and chrony stepped
it 24.7 days forward at 09:19:58 local, after these lines.)

The player started four seconds before the user session that runs PipeWire,
so it found no audio service at all -- not a missing speaker, which
`lempi-wait-sink` and the keeper are built for, but nothing to open. Three
faults followed, one from the other:

- **The output thread exited.** `path::supervise` returned when the first
  open failed, so every reopen after it -- the keeper asked every thirty
  seconds, "moved the stream from 'nothing' to 'OontZ_Angle 3 U412'" -- went
  to a channel nobody read. Only a restart could bring the audio back.
- **It played anyway.** `PathHandle::audible` answered *true* for a missing
  ring, the answer meant for the tests' deliberate discard sink. With no
  device to pace it, the engine mixed into nothing as fast as it could
  decode: 35 rows in the listener's history in seven and a half minutes,
  "heard" for 103 minutes between them, against 2 in the same span before the
  reboot. They were deleted with the maintainer's leave (play history is
  node-local and never merged), kept first in
  `/var/lempi/runaway-plays-2026-10-10.csv`.
- **Little said so.** `/audio/sink` read "the player's stream is linked to
  nothing", and the keeper's line every thirty seconds claimed a move that
  never happened.

The fault is as old as the repository. Earlier boots won the race;
`lempi02w` had logged "no real sink after 5s" at each of its last three, and
its PipeWire was up each time.

**What changed, three layers:**

1. *Hold, don't race.* A handle whose first open failed answers `audible()`
   false, and the engine -- which already advances nothing that is not
   audible `[PI3-API-030]` -- holds. The discard sink keeps its old answer.
   Tested both ways in one test, and mutation-checked: with the old answer
   restored, it fails.
2. *Keep trying.* The output thread stays, retries on `recover`'s spacing
   (2 s doubling to 30 s) and at once on a reopen. The engine's rate and
   channel count are fixed when it is built, so it cannot take up a device
   that opens later; the player restarts to use it -- itself under systemd,
   by the settings page's own `sudo -n systemctl restart lempi`, and
   elsewhere by saying so.
3. *Start after the session.* Both appliance units want and follow
   `user@1000.service`. `lempi-wait-sink` still bounds the wait for a
   *speaker* at five seconds `[PI3-FOUND-550]`; this waits for PipeWire.

**Measured on lp3-wifi, `148b6e2`, the same morning.** With pi's PipeWire
stopped and the player restarted, it met the same "Host is down" and held: 45
seconds, the same process, not one row of history. PipeWire started again; 12
seconds later the player said it was restarting to use the device, did so
through sudo, and came back on the OontZ at passage 16056, 92.1 s -- where it
had held -- its position then advancing at the wall clock's rate. A reboot
after that: `user@1000` active at 34.0 s, the player started at 34.0 s, its
first open succeeded, and no row of history was written before it played.
Its setup `--check` read 95 items as recorded.

## 2. The way home from the access point

**`[PI3-FOUND-800]` "Stop AP, return to Wi-Fi" could never find the way.**
`lempi-btctl ap-stop` chose the network to return to with

    nmcli -t -f NAME,TYPE,AUTOCONNECT connection show | awk -F: '$2=="wifi" ...'

`wifi` is a type name nmcli *accepts*; the terse listing *prints*
`802-11-wireless`, so on the machine it matched nothing and the button always
answered "no known network is set to connect automatically". The tests
passed because their `nmcli` stub never answered that query at all. Measured
on lp3-wifi: the listing gives `MangoCats-g:802-11-wireless:yes`, and the
filter, run there, printed nothing.

Together with a failover that never returned on its own, the access point was
a door that only opened one way: a router restarted for thirty seconds left
an appliance off the household's network until someone rebooted it.

**Built `[SPEC-WFO-080]`:** one lookup, `home_network`, for both ways home;
`ap-return`, which is `ap-stop` without the confirm-or-revert timer (nobody is
there to confirm, so it would always revert) and bounded by `--wait 30`; and
in the keeper, while the access point is up, its clients counted from
`iw dev wlan0 station dump`, each change said in the journal, and after
fifteen minutes with none, one try for home. A client resets the clock; a
failed try restarts it; clients that cannot be listed are not taken for none.
The stub now answers as the real tool does, and the suite gained nineteen
checks, the first of which fails against the old filter.
