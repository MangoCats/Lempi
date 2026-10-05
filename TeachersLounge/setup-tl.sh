#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# What `teacherslounge` is, as a script [TL-SET-010]: the record of the
# node, checked against it, as each appliance and smartboardpc have.
#
#     bash TeachersLounge/setup-tl.sh            # --check: compare, change nothing
#     bash TeachersLounge/setup-tl.sh --go       # apply
#
#     HOST=someone@other bash TeachersLounge/setup-tl.sh ...   # default sw@teacherslounge
#
# Runs on the development host, over SSH, on build/setup-lib.sh, with what
# every source host has from build/source-host-items.sh [FLT-SHP-030].
# Written 2026-10-02; until then nothing recorded how this machine was set up.
#
# **It records an absence on purpose.** This machine's player is started by
# hand, by the maintainer's choice [TL-OPN-020], so a player service here is
# a difference, not a fix to make: the item says so and --go leaves it alone.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

HOST="${HOST:-sw@teacherslounge}"
# The machine names itself with capitals; the network, and this repository, do not.
NAME=TeachersLounge
# In the ssh user's home, where every remote command starts.
REPO=Dev/Lempi
DATA=lempi-data
MODE=check
case "${1:---check}" in
    --check) MODE=check ;;
    --go)    MODE=go ;;
    *) echo "usage: setup-tl.sh [--check|--go]" >&2; exit 2 ;;
esac

SETUP_NAME=setup-tl
. build/setup-lib.sh
. build/source-host-items.sh

setup_target
[ -z "$P" ] || die "an overlay root: this machine has never had one, and the items below assume it does not"

say ""
say "identity"
item "hostname $NAME" "test \"\$(hostname)\" = $NAME" "" "renaming it is a decision, not a fix"

source_host_items "$REPO" client

say ""
say "its library and its player"
# Its own pair [FLT-SHP-010], in the user's own data folder.
for f in library.db listener.db; do
    item "$DATA/$f" "test -s $DATA/$f" "" "a mirror without its $f -- see FLEET001"
done
# The Export page's sends put music here [FLT-DAT-015].
item "music folder ~/Music" "test -d Music" "" "the sends' audio_root; create it, or change the peer"
item "player built" "test -x $REPO/player/target/release/lempi" "" \
     "build/update-source-host.sh $HOST $REPO"
# Started by hand, or on demand for a sync, and never at boot [TL-OPN-020]:
# no unit that starts the player by itself, user or system.
item "no player service at boot, by choice [TL-OPN-020]" \
     "! systemctl --user is-enabled lempi.service && ! systemctl is-enabled lempi.service" "" \
     "a unit starts the player here, which was decided against"
# The on-demand unit [TL-OPN-040]: this repository's, and never enabled -- it
# has no [Install] section, so nothing can wire it to a target.
user_file_item "user unit lempi-ondemand.service is this repository's" \
    TeachersLounge/lempi-ondemand.service .config/systemd/user/lempi-ondemand.service 644 \
    "XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user daemon-reload"
item "lempi-ondemand.service is wanted by no target" \
     "! ls .config/systemd/user/*.wants/lempi-ondemand.service"  "" \
     "it is enabled: undo it, a sync starts this player and stops it after"
# The player must know --paused [TL-OPN-040]: an older build would refuse it.
item "the player accepts --paused" "$REPO/player/target/release/lempi --help 2>&1 | grep -q -- --paused" "" \
     "build/update-source-host.sh $HOST $REPO"

setup_finish
