#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# How `smartboardpc` is set up as a playback node, as a script -- the record
# of the node [SMT-SVC-010]. Only what makes it play; the survey (SMART001)
# covers the rest of the machine.
#
#     bash SmartPC/setup-smart.sh            # --check: compare, change nothing
#     bash SmartPC/setup-smart.sh --go       # apply
#
#     HOST=someone@other bash SmartPC/setup-smart.sh ...   # default mango@smartboardpc
#
# Runs on the development host, over SSH. The root is plain ext4, so an
# ordinary write is durable; the script checks that before writing
# [GDE-DEP-060]. The player binary is built by build/update-source-host.sh.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

HOST="${HOST:-mango@smartboardpc}"
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

DIFFER=0
on()    { ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" "$@"; }
say()   { printf '%s\n' "$*"; }
ok()    { printf '  %-58s ok\n' "$1"; }
did()   { printf '  %-58s CHANGED\n' "$1"; }
differ(){ DIFFER=$((DIFFER + 1)); printf '  %-58s DIFFERS%s\n' "$1" "${2:+ -- $2}"; }
die()   { printf 'setup-smart: %s\n' "$*" >&2; exit 1; }

# item NAME CHECK [APPLY] -- CHECK and APPLY are commands run on the node.
item() {
    if on "$2" >/dev/null 2>&1; then ok "$1"; return; fi
    if [ "$MODE" = go ] && [ -n "${3:-}" ]; then
        if on "$3" >/dev/null 2>&1 && on "$2" >/dev/null 2>&1; then did "$1"
        else differ "$1" "apply failed"; fi
    else
        differ "$1"
    fi
}

say "setup-smart $MODE against $HOST"
on true || die "$HOST is not reachable; nothing was checked"
ROOTFS=$(on "findmnt -no FSTYPE /")
say "$HOST has a $ROOTFS root"
[ "$ROOTFS" != overlay ] || die "an overlay root: this script writes / directly and would be lost at reboot"

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
item "player built" "test -x Dev/Lempi/player/target/release/lempi" ""
want=$(md5sum < SmartPC/lempi.service | cut -c1-32)
have=$(on "test -f $UNIT && md5sum < $UNIT | cut -c1-32")
if [ "$want" = "$have" ]; then
    ok "user unit lempi.service is this repository's"
elif [ "$MODE" = go ] && scp -q SmartPC/lempi.service "$HOST:/tmp/lempi.service" \
     && on "mkdir -p .config/systemd/user && install -m 644 /tmp/lempi.service $UNIT && rm -f /tmp/lempi.service && systemctl --user daemon-reload"; then
    did "user unit lempi.service is this repository's"
else
    differ "user unit lempi.service is this repository's"
fi
item "user unit enabled" "systemctl --user is-enabled lempi.service" "systemctl --user enable lempi.service"

say ""
if [ "$DIFFER" -eq 0 ]; then say "All items as recorded."; else say "$DIFFER item(s) differ from this script."; fi
[ "$DIFFER" -eq 0 ]
