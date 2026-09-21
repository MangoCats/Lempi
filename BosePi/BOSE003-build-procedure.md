# BOSE003: Building the Card, Step by Step

**Implementation plan — written 2026-08-23. In progress since 2026-09-06 —
phases 1–5 executed, by hand and partly by script; see the "Corrected" notes
below and [BosePi/README.md](README.md)'s status table for exactly what has
and hasn't been proven.**

**`[IMPL-BOS-140]` Found the first time `lempi` actually ran here: card
numbering on this Bookworm/trixie image is not what `[PI-BOS-020]`'s survey
measured on the old 32-bit install.** There, HiFiBerry was card 1. Here it's
**card 2** — `card 0` is the Pi's own onboard `bcm2835 Headphones`, present
and enumerated first, nothing connected to it. `lempi-bose.service` had no
`--device` flag at all, so it opened whatever ALSA offered first: played
without error, reported healthy, resumed a passage, produced not one audible
sample — `[IMPL-AUD-010]`'s dummy-output hazard, in a new shape (a real,
present, working device that just isn't the one anyone can hear). Fixed with
`--device hifiberry`, a substring match against the card name, robust to
whatever number it enumerates as. Confirmed by device ownership
(`sudo fuser -v /dev/snd/*`) moving to `pcmC2D0p`, not by trusting the log
line alone.

**`[IMPL-BOS-170]` Found live on the locked-in card, 2026-09-07: zram swap
never actually comes up, and every SSH login makes it fail again in the
journal.** `systemd-remount-fs.service` tries to remount `/` per `fstab`'s
overlay entry on every pass through `local-fs.target` — not only at boot,
but on every new SSH session too — and fails every time (`fsconfig() failed:
overlay: No changes allowed in reconfigure`, exit 32): an overlay refuses
post-mount reconfiguration outright, so this step can never succeed against
one. Harmless by itself, except `rpi-resize-swap-file.service` and
`rpi-setup-loop@var-swap.service` both hard-`Requires=` it, so its failure
cascades into `dev-zram0.swap` never starting — `free -h` showed 0B swap
despite `/etc/rpi/swap.conf` enabling zram+file, and each SSH login logged a
fresh `Dependency failed for dev-zram0.swap`. Not dangerous today (1.8 GiB
RAM, nothing under memory pressure), but no OOM cushion at all, and
unbounded journal noise for the card's lifetime. Fixed with a no-op
`ExecStart=` override for `systemd-remount-fs.service` — the remount is
meaningless against an overlay root, so let the step report success instead
of attempting one. Folded into `provision-bose.sh`, right after the journal
step, so it lands on A before `finalize-bose.sh --lock-in` ever runs — same
reasoning as `[IMPL-BOS-160]`'s escape hatch about what "before lock-in"
buys. For a card already locked in, `sudo overlayroot-chroot` (the tool
`/etc/fstab`'s own header comment names for exactly this) writes the same
drop-in through to A's real lower filesystem without needing the full
unlock/reboot/relock cycle `[IMPL-BOS-160]` exists for.

Unblocking `systemd-remount-fs.service` wasn't the whole fix, though —
it had been masking a second problem. With the dependency chain able to run
at all, `rpi-resize-swap-file.service` failed on its own: `truncate: cannot
open '/var/swap' for writing: No space left on device`. The default
mechanism is zram**+file** — a writeback file at `/var/swap`, which lives on
`/`, the RAM-backed overlay itself (`overlayroot 923M 1.3M 922M 1% /`). A
RAM-sized swap file cannot fit in a ~900M tmpfs, and backing RAM-compressed
swap with a file that itself lives in RAM would be circular even if it did.
Fixed by dropping the file half: `Mechanism=zram` in `/etc/rpi/swap.conf`,
same two-write treatment (`overlayroot-chroot` for A, plus the live overlay
directly so it took effect without a reboot). Both fixes applied live to
`bose` itself 2026-09-07, verified with `free -h` showing `1.8Gi` zram swap
and `systemctl --failed` clean, in addition to being folded into
`provision-bose.sh` for the next card.

Where each phase runs, what runs it, and in what order. The design it carries
out is [BOSE002](BOSE002-image-build.md); the machine it targets is described in
[BOSE001](BOSE001-survey.md).

**Updated 2026-09-06 — this plan is no longer purely hypothetical, but it is
not proven either.** `[IMPL-BOS-130]`'s card reader turned out to be
unnecessary in the form expected — a USB reader was already attached to the
*development* host rather than `bose`, which changed where phase 1 ran (see
the correction below) without changing what it accomplished. Phases 1–4 have
each been carried out successfully once, by hand or by a script exercised for
the first time; none of it has been repeated on a second card, so read this
document as "here is what worked once, on this hardware, on this day" rather
than as a settled procedure. [BosePi/README.md](README.md)'s table says
precisely which of the *scripts* below have themselves been run versus which
only encode a manual sequence that worked.

> **Scripts:** [`prepare-card.sh`](prepare-card.sh) for phase 2 ·
> [`patch-boot-image.ps1`](patch-boot-image.ps1) for phase 1b ·
> [`provision-bose.sh`](provision-bose.sh) for phase 3 ·
> [`seed-library.sh`](seed-library.sh) for phase 4 ·
> [`finalize-bose.sh`](finalize-bose.sh) for phase 5 ·
> [`build-bose-card.sh`](build-bose-card.sh) orchestrates all of the above ·
> [`mpd.conf`](mpd.conf) for the guest

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-2 in [BOSE003](BOSE003-build-procedure.md), §3-4 in [BOSE007](BOSE007-migrating-the-library.md).

## 1. Where the build actually runs

**`[IMPL-BOS-100]` There is no single machine that can do all of it, because a
running system cannot repartition the card it booted from.** That constraint,
not tooling, is what splits the work into three phases with different homes.

| Phase | Runs on | Target | Why there |
| :--- | :--- | :--- | :--- |
| **1 — write** | `bose`, driven over SSH | new card in a USB reader | needs a card slot; see below |
| **2 — partition and seed** | `bose`, driven over SSH | same card, still in the reader | the card is not booted, so it is free to be repartitioned |
| **3 — provision** | this host, over SSH | `bose` booted from the new card | exactly how `lempipi` is deployed today |

**The development host cannot host phase 1 or 2.** It is Windows with Docker and
a WSL that carries only the `docker-desktop` distro, and — checked — **it has no
card reader**: the only removable-capable device is an external SSD. Passing a
reader into WSL would need `usbipd-win`, which is not installed. So the natural
Linux host for an image build is not available, and inventing one is more work
than the alternative.

**`bose` can host both, and needs one accessory: a USB SD card reader.** It is a
Pi 4 with USB3 root hubs `[PI-BOS-020]`, it reaches
`downloads.raspberrypi.com` (HTTP/2 200), and — once `PATH` includes `sbin`,
which is why a first check appeared to show them missing — it already has
`parted`, `sgdisk`, `resize2fs`, `mkfs.ext4`, `partprobe`, `dd` and `xz`. Its
kernel lists `f2fs`. Two packages are needed: `f2fs-tools` and
`cloud-guest-utils` for `growpart`.

That reader is the one piece of hardware to obtain, and it earns its cost twice:
it is also what makes the 44 GB migration a local copy instead of a night of
WiFi (§3 below).

> **The answer to "can this host drive it over SSH?" is: phases 2 and 3 yes,
> phase 1 yes but only by using `bose` as the writer.** No step needs a person
> at a keyboard on either machine. Every step needs the reader.

**`[IMPL-BOS-110]` The script must refuse to write to `mmcblk*`.** Phase 1 and 2
run as root on a machine whose own root filesystem is a card, and the difference
between the target and the running system is one letter. `prepare-card.sh`
takes a device, resolves it, and **aborts unless it is USB-attached and is not
the device holding `/`** — checked, not documented. The obvious form of that
check does not work here: `findmnt /` answers `overlay`, which has no parent
disk, so a guard written against `/` alone passes everything on the very
machine that runs it. It asks `/boot` and `/proc/cmdline` too, and refuses if
they all come back empty. `[PI3-API-030]` applies to
build tooling as much as to a player: a script that will cheerfully destroy the
running system if mistyped is not fit to be run unattended.

---

## 2. Build sequence

Following `[PI-IMG-010]`, **two scripts rather than one**, because they run in
different places and at different times:

**[`prepare-card.sh`](prepare-card.sh)** — runs on `bose` (booted from the *old* card),
against the new card in the reader:

1. Stream Pi OS Lite 64-bit (Bookworm) straight onto the card, no staging file:
   `bose` has 505 MB of writable space and it is RAM `[PI-BOS-050]`, so
   `curl … | xz -dc | dd of=/dev/sdX` is not an optimisation but a requirement.
2. Partition to [BOSE002 §2](BOSE002-image-build.md) — `p1` firmware, `p2` A, `p3` C, `p4` B — and label them
   `SYSTEM`, `STATE`, `LIBRARY`. Grow `p2`'s filesystem to 8 GB.
3. **Disable first-boot root auto-expand.** Raspberry Pi OS otherwise grows
   `p2` to fill the card on first boot, which would consume the space C and B
   are meant to occupy. Remove the `init=…/firstboot` clause from
   `cmdline.txt`.
4. Pre-seed first boot while the card is mounted here: `userconf.txt` for the
   `pi` user, `wpa_supplicant`/NetworkManager for WiFi, `ssh` enabled, the
   development host's public key, hostname. **`bose` is wireless with no wired
   fallback** `[PI-BOS-010]` — if this is wrong the appliance does not come
   back, and the only recovery is another card swap.
5. `dtoverlay=hifiberry-dacplus` and `dtoverlay=vc4-kms-v3d,audio=off` in
   `/boot/firmware/config.txt` — the same two lines the current install runs
   `[PI-BOS-020]`, which are known to work on this hardware.

> **Corrected 2026-09-06, mechanism only, first real run against `bose`:**
>
> - **Step 1 ran on the development host, not `bose`.** A USB reader turned
>   out to be attached there already, so Raspberry Pi Imager wrote the image
>   directly — no RAM-streaming `dd` needed, since that constraint was
>   `bose`'s, not this host's. Steps 3–5 followed as direct edits to the
>   written card's boot partition (plain FAT, host-readable) before it ever
>   moved to `bose`.
> - **Step 3's mechanism is stale.** This Bookworm image has no
>   `init=…/firstboot` clause in `cmdline.txt` — auto-expand is instead a bare
>   `resize` token in the kernel command line, removed the same way.
> - **Step 4 is cloud-init now, not `userconf.txt`/`wpa_supplicant.conf`.**
>   Raspberry Pi Imager v2 writes `user-data`/`network-config`/`meta-data`
>   (the NoCloud datasource) to the boot partition instead. Same outcome —
>   hostname, user, key-only SSH, Wi-Fi — different files.
> - **`[IMPL-BOS-072]` Step 2's "grow p2" could not run on `bose`.** Its own
>   e2fsprogs is bullseye-era and cannot check, let alone resize, a filesystem
>   a newer `mkfs.ext4` wrote — see `prepare-card.sh`'s handling. Deferred to
>   `provision-bose.sh`'s first step instead, run against the new card's own
>   matching tools once it is what `bose` actually boots.
> - **"Bookworm" itself is stale.** The image Raspberry Pi Imager actually
>   fetched reports as Debian 13 "trixie" — Raspberry Pi OS has moved on since
>   this document was written. Doesn't affect the aarch64 glibc toolchain;
>   every "Bookworm" reference above should be read as "whatever Raspberry Pi
>   OS's current 64-bit release is."
> - **`[IMPL-BOS-090b]` `sudo` needed a manual, one-time fix.** Imager v2's
>   cloud-init `user:` module sets `sudo: null` — it creates `pi` as an
>   ordinary user, not with the `NOPASSWD` sudoers drop-in older
>   `raspi-config`-driven images baked in by default. `provision-bose.sh` (like
>   `deploy-player.sh` before it) assumes passwordless `sudo` over SSH
>   throughout, with no path for an interactive password prompt. Fixed by hand
>   this time: `ssh pi@bose 'echo "pi ALL=(ALL) NOPASSWD:ALL" | sudo tee
>   /etc/sudoers.d/010-pi-nopasswd'`, typed interactively. **For next time**,
>   this is avoidable at image-write time: set `user.sudo:` in Imager's
>   generated `user-data` to `['ALL=(ALL) NOPASSWD:ALL']` instead of leaving it
>   `null`, before the card ever reaches `bose` — no manual bridge step needed.
>
> None of this changes what the steps are *for* — only what carries them out.
> Recorded here rather than silently fixed, per the same discipline
> [PI001 §5b](../LempiPi/PI001-image-and-partitions.md#5b-appliance-settings)
> already models.

Then swap the cards and boot. From here **[`provision-bose.sh`](provision-bose.sh)** runs on
the development host, over SSH, in the shape `deploy-player.sh` already has:

6. Install `mpd`, `f2fs-tools`, `cloud-guest-utils`; install `mpd.conf` and the
   systemd drop-in — [`mpd.conf`](mpd.conf) carries the split paths from
   [BOSE002 §3](BOSE002-image-build.md) and the mixer decision `[IMPL-BOS-030]`.
7. Deploy `lempi`, cross-built `aarch64` with `--features mpd` — the same
   artefact and the same Docker toolchain `lempipi` uses today.
8. Seed B (§3 below) and C: empty `listener.db`, the tree from
   [BOSE002 §3](BOSE002-image-build.md), and the resume
   interval from `[IMPL-BOS-070]`. Run `mpd --update` while B is still
   writable — budget five minutes, from 242 s for 5,758 songs on `lempipi`
   `[PI-CHR-085]`.
9. Acceptance: it plays, over the DAC, before anything is made read-only.
10. **Last:** set `ro` in `fstab` for A and B, and enable the overlay on A.
    Enabling it earlier leaves an image that boots, looks right, and has
    silently discarded steps 6–9 `[PI-IMG-020]`.

**`[IMPL-BOS-120]` Step 10 is the point of no easy return**, and it is also the
step that makes the appliance an appliance. Everything before it is recoverable
over SSH; after it, a mistake in `/etc` means another card swap. So step 9 is
not a formality: play something, hear it, then close the door.

**`[IMPL-BOS-165]` Step 10 nearly created the exact failure it exists to
prevent, and would have gone unnoticed without checking `findmnt` rather
than trusting the reboot.** Run for real 2026-09-06: `raspi-config nonint
do_overlayfs 0` on this trixie-era image installs Debian's own `overlayroot`
package (`enable_overlayfs()`'s actual body, not the wrapper logic
`[IMPL-BOS-120]`'s earlier text had read — reading a function's caller is
not reading the function). Its default, `recurse=1`, overlays **every**
mount, not only A — B and C both came back wrapped in their own writable
RAM layer, `/var/lempi/listener.db` included. Every write to it would have
been silently discarded on the next reboot: `[REQ-HW-120]`'s own
requirement, violated by the mechanism meant to protect it, within one
reboot of being enabled. Found in minutes, not months, because the
post-reboot check was `findmnt`'s actual options, not just "did it
reboot." Fixed live (`overlayroot=tmpfs:recurse=0` in `cmdline.txt`) and
folded into `finalize-bose.sh` so it happens automatically, before the
reboot, every time — see the script's own updated header for the full
account.

---

> **Split on 2026-09-10.** Migrating the library, and the open questions
> after it, are now [BOSE007](BOSE007-migrating-the-library.md).
