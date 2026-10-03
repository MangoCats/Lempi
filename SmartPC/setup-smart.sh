#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# How `smartboardpc` is set up as a playback node, as a script -- the record
# of the node [SMT-SVC-010]. Only what makes it play, and the fleet clock it
# serves; the survey (SMART001) covers the rest of the machine.
#
#     bash SmartPC/setup-smart.sh            # --check: compare, change nothing
#     bash SmartPC/setup-smart.sh --go       # apply
#
#     HOST=someone@other bash SmartPC/setup-smart.sh ...   # default mango@smartboardpc
#
# Runs on the development host, over SSH, on build/setup-lib.sh, with what
# every source host has from build/source-host-items.sh [FLT-SHP-030] -- on
# them since 2026-10-02; it carried its own copy of the engine until then.
# The root is plain ext4, so an ordinary write is durable, and the engine
# says so before writing [GDE-DEP-060]. The player binary is built by
# build/update-source-host.sh.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

HOST="${HOST:-mango@smartboardpc}"
# The machine names itself "Smart", the fleet record's nickname for it.
NAME=Smart
# In the ssh user's home, where every remote command starts.
REPO=Dev/Lempi
MUSIC=/media/mango/PortableSSD
MUSIC_UUID=8824-790B
FSTAB="UUID=$MUSIC_UUID $MUSIC exfat defaults,nofail,x-systemd.device-timeout=15s,uid=1000,gid=1000,fmask=0022,dmask=0022,iocharset=utf8 0 0"
UNIT=.config/systemd/user/lempi.service
MODE=check
case "${1:---check}" in
    --check) MODE=check ;;
    --go)    MODE=go ;;
    *) echo "usage: setup-smart.sh [--check|--go]" >&2; exit 2 ;;
esac

SETUP_NAME=setup-smart
. build/setup-lib.sh
. build/source-host-items.sh

setup_target
[ -z "$P" ] || die "an overlay root: this machine has never had one, and the items below assume it does not"

say ""
say "identity"
item "hostname $NAME" "test \"\$(hostname)\" = $NAME" "" "renaming it is a decision, not a fix"

# The fleet's time server: the other nodes prefer it [GDE-ECHO-300].
source_host_items "$REPO" server

say ""
say "state and music"
# [SMT-DB-030]: the databases on the eMMC root; never on the exFAT volume [SMT-DB-010].
item "/var/lempi exists, owned by mango" "test \"\$(stat -c %U /var/lempi)\" = mango" \
     "sudo mkdir -p /var/lempi && sudo chown mango:mango /var/lempi"
# [SMT-DB-020]: by UUID, nofail, so boot neither waits on it nor fails
# without it. The options are the ones udisks mounted it with.
item "fstab mounts the music volume by UUID, nofail" "grep -qxF '$FSTAB' /etc/fstab" \
     "sudo cp -n /etc/fstab /etc/fstab.pre-lempi && echo '$FSTAB' | sudo tee -a /etc/fstab && sudo systemctl daemon-reload"
item "music volume mounted at $MUSIC" "mountpoint -q $MUSIC"

say ""
say "sound, with no one logged in"
# The speaker's device is otherwise reachable only through the ACL a local
# login grants; at boot there is none.
item "mango in group audio" "id -nG mango | grep -qw audio" "sudo usermod -aG audio mango"
# Starts mango's user manager -- PipeWire, and the player -- at boot.
item "linger on for mango" "test \"\$(loginctl show-user mango -p Linger --value)\" = yes" \
     "sudo loginctl enable-linger mango"

say ""
say "the player"
item "player built" "test -x $REPO/player/target/release/lempi" "" \
     "build/update-source-host.sh $HOST $REPO"
user_file_item "user unit lempi.service is this repository's" SmartPC/lempi.service "$UNIT" 644 \
    "systemctl --user daemon-reload"
item "user unit enabled" "systemctl --user is-enabled lempi.service" "systemctl --user enable lempi.service"

setup_finish
