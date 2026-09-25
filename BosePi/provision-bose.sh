#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Phase 3 of [BOSE002]: turn a freshly booted Bookworm into the appliance.
#
# **Runs on the development host, over SSH** [IMPL-BOS-100], against a `bose`
# already booted from the card `prepare-card.sh` made. This is the phase that
# can be driven from here, and it is deliberately the largest one.
#
#     BosePi/provision-bose.sh [host]
#
# Idempotent by construction: every step checks before it acts, so a run that
# fails halfway is fixed by running it again rather than by unpicking it.
#
# **It stops before making anything read-only.** Step 10 of [BOSE002 §6] --
# `fstab` ro and the overlay -- is not here, because after it a mistake costs a
# card swap and the machine should have been listened to first [IMPL-BOS-120].
# (That step now lives in `finalize-bose.sh`.)
#
# **Status: ran successfully against bose twice on 2026-09-06** -- once that
# found the `blkid` PATH bug and the broken `findmnt` verification lines
# (both fixed, both re-verified on the second run), and once clean. The
# `chown $LIB_MOUNT` and `mkdir $LIB_MOUNT/mpd` lines were added *after* that
# second run, to close gaps `seed-library.sh` hit by hand -- they have not
# themselves been exercised through this script yet, only as the equivalent
# manual commands. A different Raspberry Pi OS release's package set,
# default permissions, or `mpd` version could all change what these steps
# actually need to do; read what each step reports rather than only its
# aggregate exit code.
set -uo pipefail

HOST="${1:-pi@bose}"
BIN=player/target/aarch64-unknown-linux-gnu/release/lempi
LIB_MOUNT=/srv/library
STATE_MOUNT=/var/lempi

say()  { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf 'provision: %s\n' "$*" >&2; exit 1; }
on()   { ssh "$HOST" "$@"; }

say "-----------------------------------------------------------------"
say "The chown/mkdir lines added after this script's last real run have not"
say "themselves been exercised through it -- see the header. Read each"
say "step's own output below."
say "-----------------------------------------------------------------"

step "Reaching $HOST"
ssh -o ConnectTimeout=10 "$HOST" true 2>/dev/null || die "$HOST is not reachable"
say "$(on 'cat /proc/device-tree/model 2>/dev/null | tr -d "\0"; echo')"
say "$(on 'uname -m; . /etc/os-release; echo $PRETTY_NAME' | tr '\n' ' ')"

# The whole point of [IMPL-BOS-010] was to stop needing a second toolchain.
case "$(on 'uname -m')" in
    aarch64) ;;
    *) die "$HOST is not aarch64 -- it did not boot the new 64-bit card" ;;
esac

step "Partitions"
# Named by label, not by device: the reader may enumerate differently and a
# provisioning script that writes to the wrong partition is [IMPL-BOS-110]
# again, one phase later.
for label in SYSTEM STATE LIBRARY; do
    # sudo, not a bare call: blkid lives in /sbin, off a non-root PATH by
    # default -- the same gotcha [BOSE003 §1] already found once for bose's
    # own shell, missed here until this script's first real run.
    dev=$(on "sudo blkid -L $label 2>/dev/null")
    [ -n "$dev" ] || die "no partition labelled $label -- was prepare-card.sh run?"
    say "$(printf '%-8s %s' "$label" "$dev")"
done

step "Mounts"
on "sudo mkdir -p $LIB_MOUNT $STATE_MOUNT
    grep -q 'LABEL=LIBRARY' /etc/fstab || echo 'LABEL=LIBRARY $LIB_MOUNT ext4 defaults,noatime 0 2' | sudo tee -a /etc/fstab >/dev/null
    grep -q 'LABEL=STATE'   /etc/fstab || echo 'LABEL=STATE   $STATE_MOUNT f2fs defaults,noatime 0 2' | sudo tee -a /etc/fstab >/dev/null
    sudo mount -a
    # pi-writable now, while B is still meant to accept an import -- found
    # missing when seed-library.sh's rsync hit Permission denied creating
    # $LIB_MOUNT/audio. B still goes ro at finalize-bose.sh's --lock-in.
    sudo chown pi:pi $LIB_MOUNT" || die "mount failed"
for t in "$LIB_MOUNT" "$STATE_MOUNT"; do
    say "$(on "findmnt -no TARGET,FSTYPE,OPTIONS $t" 2>/dev/null || echo "$t: not mounted")"
done

step "Grow A if prepare-card.sh couldn't  [IMPL-BOS-072]"
# bose's own bullseye e2fsprogs can't check a filesystem Bookworm's newer
# mkfs.ext4 wrote, so prepare-card.sh may have left A at its pre-grown size.
# This host IS the matching OS now, so its e2fsprogs can do it -- and growing
# a mounted root filesystem online is an ordinary, safe operation.
ROOT_DEV=$(on "findmnt -no SOURCE /" )
on "sudo resize2fs $ROOT_DEV" || die "could not grow A -- check with 'df -h /' on $HOST"
say "$(on "df -h / | tail -1")"

step "Packages"
# `sqlite3` is here for Vipunen's remote tooling, NOT for database recovery.
# `lempi-db-recover` resolves a runner and `python3` is a measured equal for
# that one act `[PI-PRE-030]`. But `tools/remote_peek.py` is literally
# `ssh <host> sqlite3 -json <path> "<sql>"`, so without the command
# `remote_flags`, `sync_preferences`, `mesh_diff`, `resolve_mesh_conflict`
# and `remote_snapshot` all report the appliance UNREACHABLE when it is
# merely missing a package `[BOS-IMG-020]` -- a false diagnosis that points
# at the network. It was installed by hand on 2026-09-11 and lost at the next
# reboot with everything else that went to the overlay `[IMPL-BOS-185]`;
# listing it here is what stops a rebuilt card from arriving without it.
#
# `chrony` is the fleet's clock `[GDE-ECHO-300]`, configured by the "Fleet
# clock" step below. It was installed by hand -- BOSE010 carries the command
# as a worked example -- until 2026-09-25.
on "sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        mpd f2fs-tools cloud-guest-utils alsa-utils sqlite3 chrony >/dev/null" \
    || die "package install failed"
say "mpd $(on 'mpd --version 2>/dev/null | head -1')"

step "The DAC"
# [PI-BOS-020]: the overlay lines are the ones the old install already proved
# on this hardware. Verify the card appeared rather than trusting config.txt.
CARD=$(on "cat /proc/asound/cards 2>/dev/null | grep -i hifiberry | head -1")
[ -n "$CARD" ] || die "no HiFiBerry card -- check dtoverlay=hifiberry-dacplus in config.txt"
say "$CARD"
MIXER=$(on "amixer -c 1 sget Digital 2>/dev/null | grep -o '\[[0-9]*%\]' | head -1")
if [ -n "$MIXER" ]; then
    # Reported, not acted on. Reaching this mixer means opening the card
    # directly, which forecloses the handoff crossfade -- see [IMPL-BOS-030]
    # and the note in BosePi/mpd.conf. The shipped config takes the shared sink.
    say "hardware mixer available at $MIXER (unused; see [IMPL-BOS-030])"
else
    say "no 'Digital' control found -- the shipped config does not need one"
fi

step "State layout  [BOSE002 §3]"
# **Not `chown -R pi:pi $STATE_MOUNT`, which this line was until 2026-09-25.**
# Recursive over the whole partition and run *before* the next step copies
# /etc/ssh, /home/pi and NetworkManager's profiles onto it, it was harmless
# on a first run and wrong on every later one -- and this script is meant to
# be re-run (it was, three times). Found on bose: its entire /etc/ssh,
# host private keys included, owned by pi, every entry changed in the same
# second as backup/, mpd/, log/ and home-pi/ on 2026-09-06, the signature of
# one recursive chown of this directory. It also handed pi /var/log (log/ is
# bound there). So: the top level and the two directories pi's work lives in,
# and nothing that later becomes someone else's.
on "sudo mkdir -p $STATE_MOUNT/log $STATE_MOUNT/mpd/playlists $STATE_MOUNT/backup
    sudo chown pi:pi $STATE_MOUNT
    sudo chown -R pi:pi $STATE_MOUNT/mpd $STATE_MOUNT/backup
    # /var/log becomes a bind mount onto C, so the overlay never holds it
    # [IMPL-BOS-060]. This is the single largest consumer left otherwise.
    grep -q '$STATE_MOUNT/log' /etc/fstab \
      || echo '$STATE_MOUNT/log /var/log none bind 0 0' | sudo tee -a /etc/fstab >/dev/null"
say "listener.db, logs, mpd state and backups all under $STATE_MOUNT"

step "SSH identity and known networks, off the overlay  [PI-A-025]"
# Found by building this very card: authorized_keys added while /home/pi sat
# on the overlay vanished on the next power cycle. Host keys and NM's
# connection profiles are the same class of loss -- credentials, not noise --
# so all three move to C, bind-mounted, seeded once from whatever this boot
# already generated. Idempotent: a mounted target means a prior run finished.
on "sudo mkdir -p $STATE_MOUNT/etc-ssh $STATE_MOUNT/home-pi $STATE_MOUNT/nm-connections
    mountpoint -q /etc/ssh || sudo cp -a /etc/ssh/. $STATE_MOUNT/etc-ssh/
    mountpoint -q /home/pi || sudo cp -a /home/pi/. $STATE_MOUNT/home-pi/
    mountpoint -q /etc/NetworkManager/system-connections \
      || sudo cp -a /etc/NetworkManager/system-connections/. $STATE_MOUNT/nm-connections/ 2>/dev/null
    sudo chown -R pi:pi $STATE_MOUNT/home-pi
    sudo chmod 700 $STATE_MOUNT/nm-connections
    grep -q '$STATE_MOUNT/etc-ssh' /etc/fstab \
      || echo '$STATE_MOUNT/etc-ssh /etc/ssh none bind 0 0' | sudo tee -a /etc/fstab >/dev/null
    grep -q '$STATE_MOUNT/home-pi' /etc/fstab \
      || echo '$STATE_MOUNT/home-pi /home/pi none bind 0 0' | sudo tee -a /etc/fstab >/dev/null
    grep -q '$STATE_MOUNT/nm-connections' /etc/fstab \
      || echo '$STATE_MOUNT/nm-connections /etc/NetworkManager/system-connections none bind 0 0' | sudo tee -a /etc/fstab >/dev/null
    sudo mount -a" || die "SSH/NM persistence setup failed"
for t in /etc/ssh /home/pi /etc/NetworkManager/system-connections; do
    say "$(on "findmnt -no TARGET,FSTYPE $t" 2>/dev/null || echo "$t: not mounted")"
done

step "pi's own SSH key"
# The key this node uses to reach other machines -- not its host keys, which
# sshd generated and the step above moved to C. No node had one until
# 2026-09-25, when bose was found without any and the maintainer asked that
# every node's script make one. After the step above on purpose: /home/pi is
# bound to C by now, so the key survives the overlay. Made once, never
# replaced -- a new key would silently undo wherever the old one was
# authorised. No passphrase: an appliance uses it unattended.
on 'test -f ~/.ssh/id_ed25519 || ssh-keygen -q -t ed25519 -N "" -C "pi@$(hostname)" -f ~/.ssh/id_ed25519' \
    || die "could not create pi's SSH key"
say "$(on 'ssh-keygen -l -f ~/.ssh/id_ed25519.pub')"
say "authorise it where this node must log in: ~/.ssh/id_ed25519.pub"

step "Journal  [supersedes PI-A-020]"
# Persistent on C, capped -- not Storage=volatile, because volatile is RAM and
# RAM is the resource this whole layout is defending.
on "sudo mkdir -p /etc/systemd/journald.conf.d
    printf '[Journal]\nStorage=persistent\nSystemMaxUse=64M\nRuntimeMaxUse=8M\n' \
      | sudo tee /etc/systemd/journald.conf.d/lempi.conf >/dev/null"
say "Storage=persistent, SystemMaxUse=64M, on C via /var/log"

step "Wi-Fi country"
# The regulatory country the radio obeys. The imager's customisation screen
# set it on this card (`cfg80211.ieee80211_regdom=US` in cmdline.txt) and
# nothing in the repository did, so a card imaged without that screen came up
# with whatever the default was. Stated here, through raspi-config, which owns
# that line -- found only on the card, 2026-09-25.
WIFI_COUNTRY="${WIFI_COUNTRY:-US}"
if [ "$(on "sudo raspi-config nonint get_wifi_country" 2>/dev/null)" = "$WIFI_COUNTRY" ]; then
    say "already $WIFI_COUNTRY"
else
    on "sudo raspi-config nonint do_wifi_country $WIFI_COUNTRY" || die "could not set Wi-Fi country"
    say "set to $WIFI_COUNTRY (takes effect at the next boot)"
fi

step "Fleet clock  [GDE-ECHO-300]"
# One LAN reference for every node, and the same fallbacks on every node: the
# fleet file's, plus the distribution's own pool, left on -- the maintainer's
# choice, 2026-09-25, after it was found commented out by hand here and on
# lempi02w but not on lempiplay3. Done by hand until then (BOSE010's worked
# example); the file is identical in every machine folder.
[ -f BosePi/lempi-fleet.sources ] || die "BosePi/lempi-fleet.sources missing"
scp -q BosePi/lempi-fleet.sources "$HOST:/tmp/lempi-fleet.sources" || die "upload failed"
on "sudo install -D -m644 /tmp/lempi-fleet.sources /etc/chrony/sources.d/lempi-fleet.sources
    rm -f /tmp/lempi-fleet.sources
    sudo sed -i 's/^#.*\(pool 2\.debian\.pool\.ntp\.org\)/\1/' /etc/chrony/chrony.conf
    sudo systemctl restart chrony" || die "chrony configuration failed"
say "verify: chronyc sources shows ^* 192.168.67.93"

step "Overlay-safe remount-fs  [IMPL-BOS-170, found live on bose 2026-09-07]"
# systemd-remount-fs.service tries to remount / per fstab's overlay entry --
# not just at boot, but every time anything pulls in local-fs.target, which
# includes every new SSH login's session scope. It fails every single time:
# `fsconfig() failed: overlay: No changes allowed in reconfigure` (exit 32),
# because an overlay refuses post-mount reconfiguration outright. Harmless on
# its own, except rpi-resize-swap-file.service and rpi-setup-loop@var-swap
# both hard-Require= it, so its failure cascades into dev-zram0.swap never
# coming up -- found by `free -h` showing 0B swap on a locked-in card despite
# /etc/rpi/swap.conf enabling zram+file, and confirmed by watching a fresh
# `Dependency failed for dev-zram0.swap` land on every single SSH login.
# The remount itself is meaningless for an overlay root, so the fix is a
# no-op override, not a real remount: let the step report success instead of
# actually attempting anything. Must land on A before finalize-bose.sh's
# --lock-in enables the overlay [IMPL-BOS-160 is the precedent for "why here,
# why now"] -- once A is the overlay's read-only lower layer, adding this
# needs `overlayroot-chroot` instead (see the live fix applied to bose itself,
# same date).
on "sudo mkdir -p /etc/systemd/system/systemd-remount-fs.service.d
    printf '[Service]\nExecStart=\nExecStart=/bin/true\n' \
      | sudo tee /etc/systemd/system/systemd-remount-fs.service.d/overlayroot-noop.conf >/dev/null
    sudo systemctl daemon-reload"
say "remount-fs is now a no-op; verify after the next boot with:"
say "  systemctl is-active dev-zram0.swap && free -h"

step "zram-only swap, not zram+file  [IMPL-BOS-170]"
# Unblocking remount-fs above still wasn't enough on its own: the "+file"
# half of rpi-swap's default zram+file mechanism wants a writeback file at
# /var/swap, which lives on / -- the RAM-backed overlay itself
# (`overlayroot 923M 1.3M 922M 1% /`). A RAM-sized swap file cannot fit in a
# ~900M tmpfs, and even if it did, backing RAM-compressed swap with a file
# that itself lives in RAM defeats the point. `rpi-resize-swap-file.service`
# failed with `truncate: cannot open '/var/swap' for writing: No space left
# on device` -- found live, right after the remount-fs fix above stopped
# masking it. Simplest correct fix for a read-only appliance: drop the file
# half entirely, pure zram.
#
# Since 2026-09-25 a tracked drop-in, stating the size as well as the
# mechanism, rather than a sed on the package's own file: the size had been
# whatever rpi-swap's defaults gave, recorded nowhere.
[ -f BosePi/rpi-swap-lempi.conf ] || die "BosePi/rpi-swap-lempi.conf missing"
scp -q BosePi/rpi-swap-lempi.conf "$HOST:/tmp/rpi-swap-lempi.conf" || die "upload failed"
on "sudo install -D -m644 /tmp/rpi-swap-lempi.conf /etc/rpi/swap.conf.d/10-lempi.conf
    rm -f /tmp/rpi-swap-lempi.conf
    sudo systemctl daemon-reload"
say "zram, 1x RAM, at most 2048 MiB (BosePi/rpi-swap-lempi.conf)"
say "verify after the next boot: systemctl cat dev-zram0.swap | grep zram)"
say "should say '(zram)', not '(zram+file)', with no rpi-setup-loop binding"

step "MPD"
[ -f BosePi/mpd.conf ] || die "BosePi/mpd.conf missing"
scp -q BosePi/mpd.conf "$HOST:/tmp/mpd.conf" || die "upload failed"
on "sudo cp /tmp/mpd.conf /etc/mpd.conf
    sudo mkdir -p /etc/systemd/system/mpd.service.d
    # mpd.conf's own db_file lives here [BOSE002 §3] -- mpd fails to start
    # without it, found missing on this build's first --start.
    mkdir -p $LIB_MOUNT/mpd
    sudo systemctl mask mpd.socket >/dev/null 2>&1
    sudo systemctl daemon-reload"
say "installed; db_file on B, state on C  [BOSE002 §3]"

step "Lempi"
[ -f "$BIN" ] || die "no binary at $BIN -- cross-compile first (see build/README.md)"
case "$(file -b "$BIN" 2>/dev/null)" in
    *aarch64*) ;;
    *) die "$BIN is not an aarch64 binary" ;;
esac
scp -q "$BIN" "$HOST:/tmp/lempi.new" || die "upload failed"
on "sudo install -m755 /tmp/lempi.new /usr/local/bin/lempi"
say "$(on '/usr/local/bin/lempi --version 2>/dev/null')"

step "Lock-in escape hatch  [IMPL-BOS-160]"
# Must land on A before --lock-in ever runs -- once A is the overlay's
# read-only lower layer, adding anything to it needs the overlay disabled
# first, which is exactly what this script exists to do. Idempotent: a
# later re-run just overwrites the same two files and re-enables an
# already-enabled unit.
scp -q BosePi/lempi-unlock-check.sh "$HOST:/tmp/lempi-unlock-check.sh" || die "upload failed"
scp -q BosePi/lempi-unlock-check.service "$HOST:/tmp/lempi-unlock-check.service" || die "upload failed"
on "sudo install -m755 /tmp/lempi-unlock-check.sh /usr/local/sbin/lempi-unlock-check.sh
    sudo install -m644 /tmp/lempi-unlock-check.service /etc/systemd/system/lempi-unlock-check.service
    sudo mkdir -p $STATE_MOUNT/unlock
    sudo systemctl daemon-reload
    sudo systemctl enable lempi-unlock-check.service"
say "installed; checked on every boot, does nothing unless a marker exists"

step "What is NOT done here"
say "- the library copy: BOSE002 §7, from the old card in the USB reader"
say "- the resume interval: [IMPL-BOS-070], set it once listener.db exists"
say "- read-only fstab and the overlay: [IMPL-BOS-120], deliberately last,"
say "  and only after somebody has heard this machine play."

printf '\nProvisioned. Play something before closing the door.\n'
