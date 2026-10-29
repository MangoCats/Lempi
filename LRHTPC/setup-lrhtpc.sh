#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# How `lrhtpc` is set up as a playback node, as a script -- the record of the
# node [LRH-SET-010]. Only what makes it play; the machine is the living
# room's Kodi box, and everything else on it is Kodi's.
#
#     bash LRHTPC/setup-lrhtpc.sh            # --check: compare, change nothing
#     bash LRHTPC/setup-lrhtpc.sh --go       # apply
#
#     HOST=someone@other bash LRHTPC/setup-lrhtpc.sh ...   # default mango@lrhtpc
#
# Runs on the development host, over SSH, on build/setup-lib.sh. The root is
# plain ext4, so an ordinary write is durable, and the engine says so before
# writing [GDE-DEP-060]. **Nothing here needs root**: sudo asks for a password
# there, so the data lives in mango's home [LRH-DAT-010], and linger is one a
# user may set on itself. The binaries are root's, in /usr/local/bin, put
# there by LRHTPC/deploy-lrhtpc.sh under a sudo granted for it [LRH-DEP-020];
# this script only checks them.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

HOST="${HOST:-mango@lrhtpc}"
# The machine names itself in capitals; the network, and this repository, do not.
NAME=LRHTPC
BIN=/usr/local/bin
# Where the binaries were until 2026-10-29 [LRH-DEP-020].
OLD_BIN=lempi/bin
# In the ssh user's home, where every remote command starts.
DATA=lempi-data
UNIT=.config/systemd/user/lempi.service
MODE=check
case "${1:---check}" in
    --check) MODE=check ;;
    --go)    MODE=go ;;
    *) echo "usage: setup-lrhtpc.sh [--check|--go]" >&2; exit 2 ;;
esac

SETUP_NAME=setup-lrhtpc
# No system reload at the end: nothing here is the system's [LRH-DAT-010].
SETUP_NO_ROOT=1
. build/setup-lib.sh

setup_target
[ -z "$P" ] || die "an overlay root: this machine has never had one, and the items below assume it does not"

say ""
say "identity"
item "hostname $NAME" "test \"\$(hostname)\" = $NAME" "" "renaming it is a decision, not a fix"

say ""
say "its library [LRH-DAT-010]"
# The catalogue is the hub's, bound to this machine's paths by relink
# [SPEC012]; the player makes the listener on its first start [HOWTO.md].
item "$DATA/library.db" "test -s $DATA/library.db" "" "seed it -- LRH001 section 3"
item "music folder ~/Music" "test -d Music" "" "the catalogue's audio_root; seed it -- LRH001 section 3"

say ""
say "sound, with no one logged in"
# Starts mango's user manager -- PulseAudio, and the player -- at boot. Set
# by the user on itself: polkit allows set-self-linger here without a password.
item "linger on for mango" "test \"\$(loginctl show-user mango -p Linger --value)\" = yes" \
     "loginctl enable-linger"
# Kodi plays through PulseAudio too, not straight to the HDMI device, so the
# two share the mixer and neither locks the other out [LRH-AUD-010].
item "Kodi plays through PulseAudio [LRH-AUD-010]" \
     "grep -q 'id=\"audiooutput.audiodevice\">PULSE:' .kodi/userdata/guisettings.xml" "" \
     "Kodi opens the device itself: the player and Kodi would lock each other out"

say ""
say "the player"
item "player installed in $BIN" "test -x $BIN/lempi" "" "bash LRHTPC/deploy-lrhtpc.sh"
# Where a send looks for it [SPEC-STAR-094]: ~/lempi/bin was on no PATH an
# ssh command has, so the Export page could not send here [LRH-OPN-020].
item "import_bundle on a non-interactive PATH" "test \"\$(command -v import_bundle)\" = $BIN/import_bundle" "" \
     "bash LRHTPC/deploy-lrhtpc.sh"
# The old copies, once the new ones are there: a second, staler player is
# one a hand-typed command could start.
item "no old copies in ~/$OLD_BIN" "! test -e $OLD_BIN" \
     "test -x $BIN/lempi && test -x $BIN/import_bundle && rm -rf $OLD_BIN && rmdir lempi"
user_file_item "user unit lempi.service is this repository's" LRHTPC/lempi.service "$UNIT" 644 \
    "XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user daemon-reload"
item "user unit enabled" "XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user is-enabled lempi.service" \
     "XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user enable lempi.service"

setup_finish
