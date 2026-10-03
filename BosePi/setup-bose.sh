#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# What `bose` is, as a script [BOS-SET-010]: the record of the node, checked
# against it, as LempiPlay3/setup-lp3.sh is for lp3-wifi.
#
#     bash BosePi/setup-bose.sh            # --check: compare, change nothing
#     bash BosePi/setup-bose.sh --go       # apply -- on the locked card, to both layers
#
#     HOST=pi@other bash BosePi/setup-bose.sh ...   # default pi@bose
#
# Runs on the development host, over SSH, on appliance/setup-lib.sh, with
# everything every appliance has from appliance/common.sh [APP-SET-020].
#
# **This is not how a bose card is built.** That is the five-phase pipeline
# build-bose-card.sh drives (README), with a physical card swap in the middle,
# and it is proven as it stands. This is what the finished node must look
# like, so a difference shows as a line rather than as a surprise. Written
# 2026-10-02 from the node's durable layer and this folder's own files.
#
# The node is locked [IMPL-BOS-120]: --go writes every change live and to the
# durable layer, as build/install-config.sh does for one file, and reads the
# durable copy back [APP-SET-030].
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

HOST="${HOST:-pi@bose}"
NAME=bose
CFG=/boot/firmware/config.txt
MODE=check
case "${1:---check}" in
    --check) MODE=check ;;
    --go)    MODE=go ;;
    *) echo "usage: setup-bose.sh [--check|--go]" >&2; exit 2 ;;
esac

SETUP_NAME=setup-bose
. appliance/setup-lib.sh
. appliance/common.sh

setup_target

# ------------------------------------------------------------------ identity
say ""
say "identity"
item "hostname $NAME" "test \"\$(cat $P/etc/hostname)\" = $NAME" \
     "echo $NAME | sudo tee /etc/hostname && sudo hostname $NAME"

common_items

# ------------------------------------------------------------------ packages
say ""
say "packages"
# overlayroot for the root and f2fs-tools for STATE, as lp3-wifi; mpd for the
# guest backend, kept by the maintainer's decision of 2026-10-02 [SPEC-BK-020].
# No PipeWire: the player opens the DAC through ALSA directly, the one
# difference between the appliances that buys something [BOS-SET-010].
for p in overlayroot f2fs-tools mpd; do
    item "package $p" "dpkg --admindir=$P/var/lib/dpkg -s $p" \
         "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $p"
done

# ----------------------------------------------------------------- the DAC
say ""
say "the DAC (config.txt)"
item "config.txt: dtoverlay=hifiberry-dacplus" "grep -qxF 'dtoverlay=hifiberry-dacplus' $CFG" \
     "echo 'dtoverlay=hifiberry-dacplus' | sudo tee -a $CFG"

# ---------------------------------------------------------- state and library
say ""
say "partitions and binds (fstab)"
# By label here, where lp3-wifi's are by PARTUUID: the two cards share one
# partition-table id. LIBRARY read-only, opened for an import by
# attended-import.sh [BOS-RUN-080].
for line in \
    "LABEL=LIBRARY /srv/library ext4 ro,noatime 0 2" \
    "LABEL=STATE /var/lempi f2fs defaults,noatime 0 2" \
    "/var/lempi/log /var/log none bind 0 0" \
    "/var/lempi/etc-ssh /etc/ssh none bind 0 0" \
    "/var/lempi/home-pi /home/pi none bind 0 0" \
    "/var/lempi/nm-connections /etc/NetworkManager/system-connections none bind 0 0"; do
    item "fstab: ${line%% none bind*}" \
         "awk '{\$1=\$1};1' $P/etc/fstab | grep -qxF '$line'" \
         "echo '$line' | sudo tee -a /etc/fstab"
done

# -------------------------------------------------------------------- overlay
say ""
say "overlay"
item "overlay on the command line, recurse=0 [IMPL-BOS-165]" \
     "grep -q 'overlayroot=tmpfs:recurse=0' /boot/firmware/cmdline.txt"
# bose's way of keeping remount-fs off an overlay [IMPL-BOS-170]: a no-op
# drop-in, where lp3-wifi masks the unit. The same effect.
item "remount-fs a no-op on the overlay" \
     "test -f $P/etc/systemd/system/systemd-remount-fs.service.d/overlayroot-noop.conf"
file_item "lempi-unlock-check.sh (the escape hatch) [IMPL-BOS-160]" \
    BosePi/lempi-unlock-check.sh /usr/local/sbin/lempi-unlock-check.sh 755
file_item "lempi-unlock-check.service" BosePi/lempi-unlock-check.service \
    /etc/systemd/system/lempi-unlock-check.service 644
item "lempi-unlock-check enabled" \
     "test -L $P/etc/systemd/system/sysinit.target.wants/lempi-unlock-check.service" \
     "sudo systemctl enable lempi-unlock-check.service"

# ------------------------------------------------------------------- services
say ""
say "services"
file_item "lempi.service" BosePi/lempi-bose.service /etc/systemd/system/lempi.service 644
file_item "mpd.conf" BosePi/mpd.conf /etc/mpd.conf 640
for u in lempi mpd; do
    item "$u enabled" "test -L $P/etc/systemd/system/multi-user.target.wants/$u.service" \
         "sudo systemctl enable $u.service"
done
item "lempi installed" "test -x $P/usr/local/bin/lempi" "" \
     "build/deploy-appliance.sh $HOST"

setup_finish
