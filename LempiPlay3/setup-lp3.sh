#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# How `lempiplay3` is set up, as a script [LP3-SET-010] -- the record of the
# node, and the means of making another like it.
#
#     bash LempiPlay3/setup-lp3.sh            # --check: compare, change nothing
#     bash LempiPlay3/setup-lp3.sh --go       # apply, on a card with a writable root
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
# **What it does not do.** The player and fbui binaries are built and
# installed separately: `build/deploy-appliance.sh` for the player, and fbui
# built `--features fbui` [LP3-REP-040]. The library is seeded as bose's is
# (BosePi/seed-library.sh), and the database split is IMPL016's step 2.
# Wi-Fi credentials are the imager's, and are never in this repository.
# journald needs a setting of its own, and until 2026-09-25 lacked one: the
# image's `Storage=volatile` kept the journal in RAM, so binding /var/log onto
# STATE was not enough -- see journald-lempi.conf. (This header said the
# opposite when first written; the fleet audit that day found it wrong.)
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

HOST="${HOST:-pi@lp3-wifi}"
WIFI_COUNTRY="${WIFI_COUNTRY:-US}"
NAME=lempiplay3
CFG=/boot/firmware/config.txt
MODE=check
case "${1:---check}" in
    --check) MODE=check ;;
    --go)    MODE=go ;;
    --lock)  MODE=lock ;;
    *) echo "usage: setup-lp3.sh [--check|--go|--lock]" >&2; exit 2 ;;
esac

DIFFER=0
on()    { ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" "$@"; }
say()   { printf '%s\n' "$*"; }
ok()    { printf '  %-58s ok\n' "$1"; }
did()   { printf '  %-58s CHANGED\n' "$1"; }
differ(){ DIFFER=$((DIFFER + 1)); printf '  %-58s DIFFERS%s\n' "$1" "${2:+ -- $2}"; }
die()   { printf 'setup-lp3: %s\n' "$*" >&2; exit 1; }

# item NAME CHECK [APPLY] -- CHECK and APPLY are commands run on the node.
item() {
    if on "$2" >/dev/null 2>&1; then ok "$1"; return; fi
    if [ "$MODE" = go ] && [ -n "${3:-}" ]; then
        if on "$3" >/dev/null 2>&1 && on "$2" >/dev/null 2>&1; then did "$1"
        else differ "$1" "apply failed"; fi
    else
        differ "$1" "${4:-}"
    fi
}

# file_item NAME LOCAL REMOTE MODE -- the node's file must be this file.
file_item() {
    local want have
    [ -f "$2" ] || die "$2 missing from the repository"
    want=$(md5sum < "$2" | cut -c1-32)
    have=$(on "test -f '$P$3' && md5sum < '$P$3' | cut -c1-32")
    if [ "$want" = "$have" ]; then ok "$1"; return; fi
    if [ "$MODE" = go ]; then
        if scp -q "$2" "$HOST:/tmp/setup-lp3.part" \
           && on "sudo install -D -m $4 /tmp/setup-lp3.part '$3' && rm -f /tmp/setup-lp3.part"; then
            did "$1"
        else
            differ "$1" "apply failed"
        fi
    elif [ -z "$have" ]; then
        differ "$1" "absent"
    else
        differ "$1" "not this repository's file"
    fi
}

# ------------------------------------------------------------ preconditions
say "setup-lp3 $MODE against $HOST"
on true || die "$HOST is not reachable; nothing was checked"
ROOTFS=$(on "findmnt -no FSTYPE /")
# Say what the target is before acting on it [GDE-DEP-060]. On an overlay
# root the durable files are under /media/root-ro, and those are what is
# checked [GDE-DEP-070]; a write to / there would vanish at the next reboot
# [IMPL-BOS-185], so --go refuses.
if [ "$ROOTFS" = overlay ]; then
    P=/media/root-ro
    say "$HOST has an OVERLAY root: checking the durable layer under $P"
    [ "$MODE" = check ] || die "--$MODE needs a writable root. On a locked card use --check, and build/install-config.sh for a single file [LP3-SET-020]."
else
    P=""
    say "$HOST has a writable root ($ROOTFS)"
fi
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
# The imager set it; stated here so the script is the record [PI3-FOUND-770].
item "Wi-Fi country $WIFI_COUNTRY" \
     "grep -q 'cfg80211.ieee80211_regdom=$WIFI_COUNTRY' /boot/firmware/cmdline.txt" \
     "sudo raspi-config nonint do_wifi_country $WIFI_COUNTRY"

# pi's own SSH key, for reaching other machines -- not the host keys. No node
# had one until 2026-09-25; the maintainer asked that every node's script make
# one. Here, before the binds below: on a new card it is made in /home/pi and
# copied onto STATE with the rest of it; once the bind is up, /home/pi *is*
# STATE, so it is checked there directly, not under $P. Made once, never
# replaced -- a new key would silently undo wherever the old one was
# authorised.
item "pi's ed25519 SSH key" "test -f /home/pi/.ssh/id_ed25519 && test -f /home/pi/.ssh/id_ed25519.pub" \
     "ssh-keygen -q -t ed25519 -N '' -C pi@$NAME -f /home/pi/.ssh/id_ed25519"

# ------------------------------------------------------------------ packages
say ""
say "packages"
# overlayroot for the root, f2fs-tools for STATE, chrony for the fleet clock,
# python3 for lempi-db-recover -- the only runner here, since this node has no
# sqlite3 [PI-PRE-030].
for p in overlayroot f2fs-tools chrony python3; do
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
    "/var/lempi/nm-connections /etc/NetworkManager/system-connections none bind 0 0"; do
    item "fstab: ${line%% none bind*}" \
         "awk '{\$1=\$1};1' $P/etc/fstab | grep -qxF '$line'" \
         "echo '$line' | sudo tee -a /etc/fstab"
done
# [IMPL-VP3-160]: the binds are what make a read-only root livable -- logs, ssh
# host keys, the home directory and NetworkManager's saved networks. Their
# contents are copied onto STATE before the binds first mount.
for pair in log:/var/log etc-ssh:/etc/ssh home-pi:/home/pi \
            nm-connections:/etc/NetworkManager/system-connections; do
    d=${pair%%:*}; src=${pair#*:}
    item "STATE holds /var/lempi/$d" "test -d /var/lempi/$d" \
         "sudo mkdir -p /var/lempi/$d && sudo cp -a $src/. /var/lempi/$d/"
done
# What moved onto STATE must keep its owner. bose's provisioning once handed
# pi its whole /etc/ssh and /var/log with a recursive chown of STATE
# (fixed 2026-09-25); checked on the live paths, which *are* STATE here.
item "/etc/ssh all owned by root" "test -z \"\$(sudo find /etc/ssh -not -user root)\"" \
     "sudo chown -R root:root /etc/ssh"
item "/var/log owned by root" "test \"\$(stat -c %U /var/log)\" = root" \
     "sudo chown root:root /var/log"
item "saved networks' directory owned by root" \
     "test \"\$(stat -c %U /etc/NetworkManager/system-connections)\" = root" \
     "sudo chown root:root /etc/NetworkManager/system-connections"
# pi's passwordless sudo: made at image time here, typed by hand on bose
# [IMPL-BOS-090b], where it came out 644. 440 is what every other sudoers file
# in the fleet is.
item "sudoers: pi's drop-in is root, mode 440" \
     "test \"\$(stat -c %U:%a $P/etc/sudoers.d/010-pi-nopasswd)\" = root:440" \
     "sudo chmod 440 /etc/sudoers.d/010-pi-nopasswd"
item "/var/lempi owned by pi" "test \"\$(stat -c %U /var/lempi)\" = pi" "sudo chown pi:pi /var/lempi"
# Nothing on STATE or LIBRARY writable by everyone. A copy from a Windows
# drive arrives 0777 under `rsync -a`, which is how bose's and lempi02w's
# libraries ended up (2026-09-25); symlinks and sticky directories excepted.
item "nothing world-writable on STATE or LIBRARY" \
     "test -z \"\$(sudo find /var/lempi /srv/library -xdev -perm -0002 ! -type l ! -perm -1000 -print -quit)\"" \
     "sudo find /var/lempi /srv/library -xdev -type d -perm -0002 ! -perm -1000 -exec chmod 755 {} + ; sudo find /var/lempi /srv/library -xdev -type f -perm -0002 -exec chmod 644 {} +"
item "/srv/library owned by pi" "test \"\$(stat -c %U /srv/library)\" = pi" "sudo chown pi:pi /srv/library"

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
for u in lempi fbui chrony; do
    item "$u enabled" "test -L $P/etc/systemd/system/multi-user.target.wants/$u.service" \
         "sudo systemctl enable $u.service"
done
# No speaker on this node: the audio is the Pi's own output.
item "bluetooth disabled" "! test -e $P/etc/systemd/system/bluetooth.target.wants/bluetooth.service" \
     "sudo systemctl disable bluetooth.service"
# fbui owns tty1 [LP3-REP-030]; a login prompt would draw over it.
item "getty on tty1 disabled" "! test -e $P/etc/systemd/system/getty.target.wants/getty@tty1.service" \
     "sudo systemctl disable getty@tty1.service"
# Disabled by hand on 2026-09-08, after the imager's first boot had done its
# work; otherwise it re-runs its datasource on every boot.
item "cloud-init disabled" "test -e $P/etc/cloud/cloud-init.disabled" \
     "sudo touch /etc/cloud/cloud-init.disabled"

# --------------------------------------------------------------------- helpers
say ""
say "helpers and binaries"
file_item "lempi-preflight" LempiPi/lempi-preflight /usr/local/bin/lempi-preflight 755
file_item "lempi-db-recover" LempiPi/lempi-db-recover /usr/local/bin/lempi-db-recover 755
item "lempi installed" "test -x $P/usr/local/bin/lempi" "" \
     "build/deploy-appliance.sh $HOST"
item "fbui installed" "test -x $P/usr/local/bin/fbui" "" \
     "build it --features fbui --bin fbui [LP3-REP-040]"

# ------------------------------------------------------------ clock and swap
say ""
say "clock and swap"
file_item "chrony fleet sources [GDE-ECHO-300]" LempiPlay3/lempi-fleet.sources \
    /etc/chrony/sources.d/lempi-fleet.sources 644
# Debian's own pool stays on, as one more fallback every node shares -- the
# maintainer's choice, 2026-09-25. This card always had it; the other two had
# it commented out by hand, and now have it back.
item "chrony distribution pool on" \
     "grep -q '^pool 2\.debian\.pool\.ntp\.org' $P/etc/chrony/chrony.conf" \
     "sudo sed -i 's/^#.*\(pool 2\.debian\.pool\.ntp\.org\)/\1/' /etc/chrony/chrony.conf"
file_item "journal kept on STATE (overrides the image's volatile)" \
    LempiPlay3/journald-lempi.conf /etc/systemd/journald.conf.d/lempi.conf 644
file_item "swap: zram, 1x RAM, <= 2 GiB" LempiPlay3/rpi-swap-lempi.conf \
    /etc/rpi/swap.conf.d/10-lempi.conf 644

say ""
if [ "$DIFFER" -eq 0 ]; then
    say "All items as recorded."
else
    say "$DIFFER item(s) differ from this script."
fi
[ "$DIFFER" -eq 0 ]

# --check against the live card, 2026-09-25: first run, every item agreed but
# two -- no swap at all (the [IMPL-BOS-170] fault) and Debian's pool, which
# this card had on and the others off. The maintainer chose the fix for the
# first and this card's way for the second; after the swap drop-in went on
# both layers and a reboot, `free` showed 904 MB of zram swap and --check
# reported every item as recorded.
