# LP3001: `lempiplay3` — what it is, and the units that are only here

**Machine notes — written 2026-09-21, when this folder was created.**

`lempiplay3` is the third appliance: a Pi with a 480×320 SPI framebuffer panel
and a touchscreen, which is what distinguishes it from `bose` (a HiFiBerry DAC
and no display) and from `lempi02w` (a Bluetooth speaker and no display). It runs
the same player as both, plus [fbui.service](fbui.service), which draws the
now-playing display and takes touch input.

This folder exists because until it was created, **this machine's units lived
only on the machine**. `[LP3-REP-010]` below is the whole reason for it.

---

## 1. Why this folder exists

**`[LP3-REP-010]` A unit that exists only on the SD card is one card failure
away from being gone.** `bose` has [BosePi](../BosePi/), `lempi02w` has
[LempiPi](../LempiPi/), and each carries the units and helper scripts that
machine runs. `lempiplay3` had neither — its `lempi.service` and `fbui.service`
were written directly on the device and never committed anywhere.

Nothing was lost, but nothing was reproducible either: rebuilding this node from
the repository would have produced a machine that plays and has a blank screen,
with no record of what the missing unit had said. The two units are now here,
captured from the running machine 2026-09-21 and byte-identical to what it runs.

---

## 2. The two units

**`[LP3-REP-020]` `lempi.service` deliberately carries no database override.**
It names the split halves on the command line — the listener database under
`/var/lempi` and the catalogue under `/srv/library` — and nothing else. An
earlier version of this unit set an environment variable pointing at the
pre-split monolith; that file was deleted once all 38 tables and 1,109,356 rows
were confirmed present in the two halves, and the variable went with it rather
than being carried forward to name a file that no longer exists. A setting that
looks deliberate and resolves to nothing is worse than no setting.

**`[LP3-REP-030]` `fbui.service` orders itself after the player and owns the
console.** It `Wants=` and is `After=` the player, so a boot brings the display
up behind the audio rather than racing it. Its `ExecStartPre` turns off the
blinking fbcon cursor on `tty1` with a `+` prefix, running that one line as root
while the service itself runs as `pi` — `/dev/tty1` is not pi-writable, and the
`|| true` means a tty that is not ready yet never blocks the UI from starting.

The player URL is given as `--url`, named rather than positional. The bare form
still works and warns that it will stop working `[GDE-CLI-040]`; the value is
identical either way, so this is the spelling changing and not the behaviour.

---

## 3. What is not in this folder, and why

**`[LP3-REP-040]` The framebuffer binary is built from the player crate, not
kept here.** `fbui` is a `[[bin]]` target of the same crate as the player,
behind the `fbui` feature, and its source is
[player/src/bin/fbui.rs](../player/src/bin/fbui.rs). Two consequences worth
knowing before deploying to this node:

- **`install-player.sh` does not replace it.** It installs the player and
  nothing else, so `fbui` drifts until it is built and installed deliberately.
- **It compiles its calibration path in.** The saved touch calibration lives at
  `/var/lempi/touch-calibration.toml` as a `const`, so a change to where the
  state directory lives cannot be fixed by editing anything on the machine —
  the binary has to be rebuilt. This is the one file on this node where a path
  is not reachable from configuration.

**`[LP3-REP-050]` `systemd-remount-fs.service` is masked here, on purpose.**
This node has an overlay root, and that unit tries to remount `/` from the
`fstab` entry; overlay refuses to be reconfigured, so it failed on every boot.
It has nothing to do on this machine — `/` is already `rw` when it runs, and
`local-fs.target` reaches active without it — but a permanently failed unit
makes `systemctl --failed` useless as a signal, which is worth more than the
unit is. Masked on **both** overlay layers, since a mask written only to the
live layer evaporates at the next reboot.

---

## 4. The rest of the node, as a script

**`[LP3-SET-010]` [`setup-lp3.sh`](setup-lp3.sh) is the record of how this
node is set up, beyond its two units.** Written 2026-09-25, at the
maintainer's direction that the setup scripts, not the cards, hold how each
node was built. Until then everything else here — the panel overlays, the
partitions and binds, the overlay, the disabled services, the clock — lived
only on the card and in [IMPL016](../docs/IMPL016-converting-lempiplay3.md)'s
prose. It covers identity, packages, the panel, the fstab and its binds, the
overlay, the services, the helpers and the clock and swap; each item is
compared on the card's **durable** layer. Run against the live card it agreed
on every item but two, both deliberate: see the next paragraph. `--go` and
`--lock` carry IMPL016's commands and have never built a card.

**`[LP3-SET-020]` On a locked card the script only checks.** Its `--go`
refuses an overlay root, because a write to `/` there is gone at the next
reboot `[IMPL-BOS-185]`; one file goes through `build/install-config.sh`,
which writes both layers.

Two items differ on the live card, and are recorded as the fix rather than
as found, pending the maintainer: **no swap at all** — bose's `[IMPL-BOS-170]`
fault, fixed by [`rpi-swap-lempi.conf`](rpi-swap-lempi.conf) — and **Debian's
own NTP pool still on**, where the other two nodes have it commented out so
every node falls back identically `[GDE-ECHO-300]`.
