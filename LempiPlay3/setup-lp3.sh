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
# **What it does not do.** The player and fbui binaries are not installed
# here: `build/deploy-appliance.sh` builds and installs both, fbui because this
# node has fbui.service [GDE-DEP-120], [LP3-REP-040]. The library is seeded as bose's is
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
TIMEZONE="${TIMEZONE:-America/New_York}"
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
# python3 for lempi-db-recover [PI-PRE-030], sqlite3 for Vipunen's remote
# tooling (`ssh <host> sqlite3 -json ...`), as bose and the other appliances
# have it [BOS-IMG-020]. Missing here until 2026-10-02, with no reason but
# that the list never had it: this node's queries ran through python3's
# fallback, and the Export page's diff failed on it when a table was new.
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
# And a Bluetooth speaker as an output option, since 2026-10-02 [LP3-BT-010]:
# lempi02w's audio stack (LempiPi/setup-appliance.sh says why each is there)
# -- PipeWire with its ALSA and Bluetooth plugins, WirePlumber, upower (whose
# absence makes WirePlumber tear down every A2DP endpoint [PI3-FOUND-030]),
# and the D-Bus bindings lempi-bt-agent is written in. bluez and alsa-utils
# came with the image; they are listed so the record says so. They went onto
# the durable layer by apt-get in overlayroot-chroot, with the live
# resolv.conf's nameserver lent for the run and the layer's own put back.
for p in overlayroot f2fs-tools chrony python3 sqlite3 \
         bluez alsa-utils pipewire pipewire-pulse pipewire-alsa wireplumber \
         libspa-0.2-bluetooth upower python3-dbus python3-gi; do
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
# A Bluetooth speaker is an output option since 2026-10-02 [LP3-BT-010]; until
# then this said "No speaker on this node" and kept bluetooth disabled. The
# units are lempi02w's (setup-appliance.sh writes them there): the keeper
# that reconnects the chosen speaker, the watchdog for a wedged controller,
# and the agent that decides who may connect.
for pair in lempi-speaker.service lempi-speaker.timer lempi-btwatch.service \
            lempi-btwatch.timer lempi-bt-agent.service; do
    file_item "$pair" "LempiPlay3/$pair" "/etc/systemd/system/$pair" 644
done
file_item "/run/lempi for the agent" LempiPlay3/lempi-tmpfiles.conf /etc/tmpfiles.d/lempi.conf 644
item "bluetooth enabled" "test -L $P/etc/systemd/system/bluetooth.target.wants/bluetooth.service" \
     "sudo systemctl enable bluetooth.service"
# upower ships wanted only by graphical.target, never reached here
# [PI3-FOUND-030]; multi-user.target is told to want it.
item "upower wanted by multi-user.target" \
     "test -L $P/etc/systemd/system/multi-user.target.wants/upower.service" \
     "sudo systemctl enable upower.service && sudo systemctl add-wants multi-user.target upower.service"
item "lempi-bt-agent enabled" "test -L $P/etc/systemd/system/multi-user.target.wants/lempi-bt-agent.service" \
     "sudo systemctl enable lempi-bt-agent.service"
for t in lempi-speaker lempi-btwatch; do
    item "$t.timer enabled" "test -L $P/etc/systemd/system/timers.target.wants/$t.timer" \
         "sudo systemctl enable $t.timer"
done
# pi's PipeWire session, kept without a login: the player and the keeper both
# reach it as pi.
item "linger for pi" "test -f $P/var/lib/systemd/linger/pi" "sudo loginctl enable-linger pi"
# The Pi 3's radio is on its UART; the image saved it blocked, and
# systemd-rfkill restores what is saved at every boot.
item "bluetooth radio unblocked at boot" \
     "test \"\$(cat $P/var/lib/systemd/rfkill/platform-soc-amba-3f201000.serial:bluetooth)\" = 0" \
     "echo 0 | sudo tee /var/lib/systemd/rfkill/platform-soc-amba-3f201000.serial:bluetooth"
file_item "WirePlumber: bluez monitor not tied to seats [PI3-FOUND-040]" \
    LempiPlay3/wireplumber-no-seat.conf /etc/wireplumber/wireplumber.conf.d/51-lempi-no-seat.conf 644
file_item "WirePlumber: a new output starts at the jack's 0 dB" \
    LempiPlay3/wireplumber-volume.conf /etc/wireplumber/wireplumber.conf.d/52-lempi-volume.conf 644
file_item "PipeWire: 44.1 kHz, lempi02w's quantum" \
    LempiPlay3/pipewire-lempi.conf /etc/pipewire/pipewire.conf.d/10-lempi.conf 644
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
# The speaker helpers, as lempi02w has them [LP3-BT-010].
file_item "lempi-common.sh" LempiPi/lempi-common.sh /usr/local/lib/lempi-common.sh 644
file_item "lempi-wait-sink" LempiPi/lempi-wait-sink /usr/local/bin/lempi-wait-sink 755
file_item "lempi-btctl" LempiPi/lempi-btctl /usr/local/bin/lempi-btctl 755
file_item "lempi-speaker" LempiPi/lempi-speaker.sh /usr/local/bin/lempi-speaker 755
file_item "lempi-btwatch" LempiPi/lempi-btwatch.sh /usr/local/bin/lempi-btwatch 755
file_item "lempi-bt-agent" LempiPi/lempi-bt-agent /usr/local/bin/lempi-bt-agent 755
item "lempi installed" "test -x $P/usr/local/bin/lempi" "" \
     "build/deploy-appliance.sh $HOST"
item "fbui installed" "test -x $P/usr/local/bin/fbui" "" \
     "build/deploy-appliance.sh $HOST (installs fbui where fbui.service exists) [GDE-DEP-120]"

# ------------------------------------------------------------ clock and swap
say ""
say "clock and swap"
# Rendered here from the template and `fleet/targets.env` `[SPEC-FCP-030]`, so
# the node is compared with -- and receives -- the finished file.
LP3_SOURCES=$(mktemp)
build/render-fleet-sources.sh "$LP3_SOURCES" >/dev/null || die "the fleet sources could not be rendered"
file_item "chrony fleet sources [GDE-ECHO-300]" "$LP3_SOURCES" \
    /etc/chrony/sources.d/lempi-fleet.sources 644
rm -f "$LP3_SOURCES"
# Debian's own pool stays on, as one more fallback every node shares -- the
# maintainer's choice, 2026-09-25. This card always had it; the other two had
# it commented out by hand, and now have it back.
item "chrony distribution pool on" \
     "grep -q '^pool 2\.debian\.pool\.ntp\.org' $P/etc/chrony/chrony.conf" \
     "sudo sed -i 's/^#.*\(pool 2\.debian\.pool\.ntp\.org\)/\1/' /etc/chrony/chrony.conf"
# The household's zone, as the other nodes have. The image came up in
# Europe/London and nothing here said otherwise, so lp3's listener recorded
# +60 minutes where every other node recorded -240, and anything that follows
# the clock ran five hours off [FLT-ISS-030]. Found 2026-09-26.
item "timezone $TIMEZONE" \
     "test \"\$(readlink $P/etc/localtime)\" = /usr/share/zoneinfo/$TIMEZONE && test \"\$(cat $P/etc/timezone)\" = $TIMEZONE" \
     "sudo timedatectl set-timezone $TIMEZONE && echo $TIMEZONE | sudo tee /etc/timezone"
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
