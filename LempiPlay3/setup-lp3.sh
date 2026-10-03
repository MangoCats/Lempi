#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# How `lempiplay3` is set up, as a script [LP3-SET-010] -- the record of the
# node, and the means of making another like it.
#
#     bash LempiPlay3/setup-lp3.sh            # --check: compare, change nothing
#     bash LempiPlay3/setup-lp3.sh --go       # apply -- on the locked card, to both layers
#     bash LempiPlay3/setup-lp3.sh --lock     # last: put the root on an overlay
#
#     HOST=pi@other bash LempiPlay3/setup-lp3.sh ...   # default pi@lp3-wifi
#
# Runs on the development host, over SSH, like BosePi's scripts.
#
# **Status, 2026-09-25.** `--check` was written against the live node and run
# against it: every item below was read off the durable layer of the card,
# and after two decided fixes it reports every one as recorded (see the note
# at the end). **`--go` and `--lock` have never run.**
# Their commands are the ones IMPL016 records being run by hand on
# 2026-09-15, with that document's own corrections, but no card has been
# built by this script. Read its output rather than trusting its exit.
#
# **What it assumes.** A card imaged with Raspberry Pi OS Lite (trixie,
# arm64) and partitioned as `bose` is -- p1 boot, p2 root, p3 f2fs STATE on
# /var/lempi, p4 ext4 LIBRARY on /srv/library [IMPL-VP3-010] -- which is what
# BosePi/prepare-card.sh makes. How this card got that layout on 2026-09-07
# is not recorded anywhere; the identical partition-table id to bose's
# suggests a copy of bose's card, and that is a guess. This script starts
# from the layout, not from that history.
#
# **What it does not do.** The player and fbui binaries are not installed
# here: `build/deploy-appliance.sh` builds and installs both, fbui because this
# node has fbui.service [GDE-DEP-120], [LP3-REP-040]. The library is seeded as bose's is
# (BosePi/seed-library.sh), and the database split is IMPL016's step 2.
# Wi-Fi credentials are the imager's, and are never in this repository.
# journald needs a setting of its own, and until 2026-09-25 lacked one: the
# image's `Storage=volatile` kept the journal in RAM, so binding /var/log onto
# STATE was not enough -- see appliance/journald-lempi.conf. (This header said the
# opposite when first written; the fleet audit that day found it wrong.)
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

HOST="${HOST:-pi@lp3-wifi}"
NAME=lempiplay3
CFG=/boot/firmware/config.txt
MODE=check
case "${1:---check}" in
    --check) MODE=check ;;
    --go)    MODE=go ;;
    --lock)  MODE=lock ;;
    *) echo "usage: setup-lp3.sh [--check|--go|--lock]" >&2; exit 2 ;;
esac

# The engine and what every appliance has [APP-SET-010]; this file is what
# lp3-wifi has besides.
SETUP_NAME=setup-lp3
. build/setup-lib.sh
. appliance/common.sh
. appliance/bluetooth/items.sh

# ------------------------------------------------------------ preconditions
setup_target
# --lock puts the overlay on; a card already on one has nothing to lock, and
# its boot partition is read-only besides.
[ "$MODE" = lock ] && [ -n "$P" ] && die "$HOST is already on an overlay; --lock is for a writable root"
PT=$(on "sudo blkid -s PTUUID -o value /dev/mmcblk0")
[ -n "$PT" ] || die "could not read the card's partition-table id"
say "card partition-table id $PT"

if [ "$MODE" = lock ]; then
    # [IMPL-VP3-170]: the overlay goes on last, in its own reboot, after one
    # that proved the binds. Everything else must already be right.
    say ""
    say "checking everything else first"
    # By path from the repository root, where the `cd` above left us -- "$0"
    # is relative to wherever the script was started.
    HOST="$HOST" bash LempiPlay3/setup-lp3.sh --check >/dev/null 2>&1 \
        || die "--check does not pass; fix that before locking"
    # [IMPL-VP3-050]: keep the command line verbatim before touching it.
    on "sudo cp -n /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.pre-overlay" \
        || die "could not keep a copy of cmdline.txt"
    if on "grep -q 'overlayroot=tmpfs' /boot/firmware/cmdline.txt"; then
        ok "overlay already on the command line"
    else
        on "sudo sed -i '1s/^/overlayroot=tmpfs:recurse=0 /' /boot/firmware/cmdline.txt" \
            || die "could not edit cmdline.txt"
        did "overlayroot=tmpfs:recurse=0 on the command line"
    fi
    say "Reboot to put the root on the overlay, then run --check."
    exit 0
fi

# ------------------------------------------------------------------ identity
say ""
say "identity"
item "hostname $NAME" "test \"\$(cat $P/etc/hostname)\" = $NAME" \
     "echo $NAME | sudo tee /etc/hostname && sudo hostname $NAME"
item "/etc/hosts names it" "grep -q '^127.0.1.1[[:space:]]*$NAME\$' $P/etc/hosts" \
     "sudo sed -i 's/^127\.0\.1\.1.*/127.0.1.1\t$NAME/' /etc/hosts"

# Everything every appliance has, but the items about STATE and LIBRARY,
# which follow the binds below. pi's SSH key is in here, so on a new card it
# is made in /home/pi before that is copied onto STATE.
common_base_items

# ------------------------------------------------------------------ packages
say ""
say "packages"
# overlayroot for the root and f2fs-tools for STATE; chrony, python3 and
# sqlite3 are every appliance's (appliance/common.sh). sqlite3 was missing
# here until 2026-10-02, with no reason but that this list never had it:
# this node's queries ran through python3's fallback, and the Export page's
# diff failed on it when a table was new.
#
# No mpd, by the maintainer's decision of 2026-10-02: this node plays through
# the player alone and has no use for the MPD backend [SPEC-BK-030].
#
# On the locked card, sqlite3 went in through `overlayroot-chroot` [IMPL-BOS-180]
# -- but not by apt-get there: the durable layer's own resolv.conf does not
# resolve, so apt fetched nothing, and overlayroot-chroot still exited 0 and
# left /media/root-ro read-write ("mount point is busy"). What worked: install
# live (apt-get, which fetches), then `dpkg -i` the two cached .debs inside
# overlayroot-chroot, offline. Verified on the durable layer, 2026-10-02.
#
# The Bluetooth stack is every Bluetooth appliance's (appliance/bluetooth),
# below.
for p in overlayroot f2fs-tools; do
    item "package $p" "dpkg --admindir=$P/var/lib/dpkg -s $p" \
         "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $p"
done

# ------------------------------------------------------------ the panel [LP3]
say ""
say "display (config.txt)"
# The touchscreen is the reason this node differs [IMPL-VP3-020].
for line in "dtparam=spi=on" "dtoverlay=piscreen2r,rotate=90,speed=16000000,fps=20"; do
    item "config.txt: $line" "grep -qxF '$line' $CFG" "echo '$line' | sudo tee -a $CFG"
done
# No HDMI on this appliance; off for boot speed [IMPL-VP3-020]. Its absence is
# also why the panel is fb1, not fb0 (fbui finds it by name).
item "config.txt: vc4-kms-v3d off" "! grep -qE '^dtoverlay=vc4-kms-v3d' $CFG" \
     "sudo sed -i 's/^dtoverlay=vc4-kms-v3d/#&/' $CFG"

# ---------------------------------------------------------- state and library
say ""
say "partitions and binds (fstab)"
# Compared with runs of whitespace collapsed, so the column alignment of the
# file on the card is not a difference.
for line in \
    "PARTUUID=$PT-03 /var/lempi f2fs defaults,noatime 0 2" \
    "PARTUUID=$PT-04 /srv/library ext4 defaults,noatime 0 2" \
    "/var/lempi/log /var/log none bind 0 0" \
    "/var/lempi/etc-ssh /etc/ssh none bind 0 0" \
    "/var/lempi/home-pi /home/pi none bind 0 0" \
    "/var/lempi/nm-connections /etc/NetworkManager/system-connections none bind 0 0" \
    "/var/lempi/bluetooth /var/lib/bluetooth none bind 0 0"; do
    item "fstab: ${line%% none bind*}" \
         "awk '{\$1=\$1};1' $P/etc/fstab | grep -qxF '$line'" \
         "echo '$line' | sudo tee -a /etc/fstab"
done
# [IMPL-VP3-160]: the binds are what make a read-only root livable -- logs, ssh
# host keys, the home directory and NetworkManager's saved networks. Their
# contents are copied onto STATE before the binds first mount.
# And Bluetooth's pairings [LP3-BT-010]: on the overlay, a speaker paired
# today would be forgotten at the next reboot.
for pair in log:/var/log etc-ssh:/etc/ssh home-pi:/home/pi \
            nm-connections:/etc/NetworkManager/system-connections bluetooth:/var/lib/bluetooth; do
    d=${pair%%:*}; src=${pair#*:}
    state_item "STATE holds /var/lempi/$d" "test -d /var/lempi/$d" \
         "sudo mkdir -p /var/lempi/$d && sudo cp -a $src/. /var/lempi/$d/"
done
# What every appliance has about STATE and LIBRARY: ownership, sudoers,
# nothing world-writable (appliance/common.sh). Checked on the live paths,
# which *are* STATE here.
common_data_items
# lp3-wifi binds NetworkManager's saved networks onto STATE, and what moved
# there must keep its owner.
state_item "saved networks' directory owned by root" \
     "test \"\$(stat -c %U /etc/NetworkManager/system-connections)\" = root" \
     "sudo chown root:root /etc/NetworkManager/system-connections"

# -------------------------------------------------------------------- overlay
say ""
say "overlay"
file_item "overlayroot.conf" LempiPlay3/overlayroot.conf /etc/overlayroot.conf 644
# [LP3-REP-050]: masked, on both layers. It tries to remount / from fstab on
# every pass through local-fs.target, and an overlay refuses.
item "systemd-remount-fs masked" \
     "test \"\$(readlink $P/etc/systemd/system/systemd-remount-fs.service)\" = /dev/null" \
     "sudo systemctl mask systemd-remount-fs.service"
if [ "$ROOTFS" = overlay ]; then
    item "overlay on the command line" "grep -q 'overlayroot=tmpfs:recurse=0' /boot/firmware/cmdline.txt"
else
    say "  (the overlay itself is --lock, last, after a reboot has proved the binds)"
fi

# ------------------------------------------------------------------- services
say ""
say "services"
file_item "lempi.service" LempiPlay3/lempi.service /etc/systemd/system/lempi.service 644
file_item "fbui.service" LempiPlay3/fbui.service /etc/systemd/system/fbui.service 644
for u in lempi fbui; do
    item "$u enabled" "test -L $P/etc/systemd/system/multi-user.target.wants/$u.service" \
         "sudo systemctl enable $u.service"
done
# A Bluetooth speaker is an output option since 2026-10-02 [LP3-BT-010]; until
# then this said "No speaker on this node" and kept bluetooth disabled. What
# every Bluetooth appliance has is appliance/bluetooth's [APP-BT-010]; what
# follows is this node's own: its radio, and WirePlumber 0.5's configuration.
bluetooth_items
# The Pi 3's radio is on its UART; the image saved it blocked, and
# systemd-rfkill restores what is saved at every boot.
item "bluetooth radio unblocked at boot" \
     "test \"\$(cat $P/var/lib/systemd/rfkill/platform-soc-amba-3f201000.serial:bluetooth)\" = 0" \
     "echo 0 | sudo tee /var/lib/systemd/rfkill/platform-soc-amba-3f201000.serial:bluetooth"
file_item "WirePlumber: bluez monitor not tied to seats [PI3-FOUND-040]" \
    LempiPlay3/wireplumber-no-seat.conf /etc/wireplumber/wireplumber.conf.d/51-lempi-no-seat.conf 644
file_item "WirePlumber: a new output starts at the jack's 0 dB" \
    LempiPlay3/wireplumber-volume.conf /etc/wireplumber/wireplumber.conf.d/52-lempi-volume.conf 644
# fbui owns tty1 [LP3-REP-030]; a login prompt would draw over it.
item "getty on tty1 disabled" "! test -e $P/etc/systemd/system/getty.target.wants/getty@tty1.service" \
     "sudo systemctl disable getty@tty1.service"

# --------------------------------------------------------------------- helpers
say ""
say "helpers and binaries"
item "lempi installed" "test -x $P/usr/local/bin/lempi" "" \
     "build/deploy-appliance.sh $HOST"
item "fbui installed" "test -x $P/usr/local/bin/fbui" "" \
     "build/deploy-appliance.sh $HOST (installs fbui where fbui.service exists) [GDE-DEP-120]"

setup_finish

# --check against the live card, 2026-09-25: first run, every item agreed but
# two -- no swap at all (the [IMPL-BOS-170] fault) and Debian's pool, which
# this card had on and the others off. The maintainer chose the fix for the
# first and this card's way for the second; after the swap drop-in went on
# both layers and a reboot, `free` showed 904 MB of zram swap and --check
# reported every item as recorded.
