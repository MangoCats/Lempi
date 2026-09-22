#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
#
# !! DO NOT USE THIS TO INSTALL SOFTWARE. Use `overlayroot-chroot` instead:
# !!
# !!     ssh pi@bose sudo overlayroot-chroot apt-get install -y <pkg>
# !!
# !! That writes through to A persistently, with NO reboot. The unlock cycle
# !! is not needed for it, and on this machine the cycle does not even leave
# !! A writable: it clears cmdline.txt's overlayroot token but NOT fstab's
# !! `ro` on A, and [IMPL-BOS-170] made the one service that would remount /
# !! rw a no-op. A then boots read-only with no tmpfs upper layer, and since
# !! bose is wireless [PI-BOS-020], NetworkManager cannot write
# !! /var/lib/NetworkManager, so it comes up with no Wi-Fi and no way in.
# !!
# !! Done on 2026-09-11 to install chrony. bose did not come back; recovery
# !! took a card and a reader -- the exact outcome this hatch exists to
# !! avoid. See [IMPL-BOS-175] / [IMPL-BOS-180] in BOSE006.
#
# The human-facing half of the lock-in escape hatch [IMPL-BOS-160]. Writes
# the C-side marker over SSH and reboots -- the boot-partition marker is for
# when SSH itself is what's broken, and is written by hand from a reader,
# not by this script.
#
#     bash BosePi/request-unlock.sh --check
#     bash BosePi/request-unlock.sh --go
#
# Asked twice, the same discipline every script here uses before undoing
# something deliberate: this requests undoing --lock-in, on the next two
# reboots, not immediately.
#
# Status: run for real against bose three times, 2026-09-06. The first two
# (before bose was --lock-in'd) only proved the already-unlocked no-op
# case, and did so against a version of lempi-unlock-check.sh that called
# `raspi-config nonint do_overlayfs 1` -- which turned out to silently do
# nothing once locked for real (see that script's own header for why). The
# third run, after the fix, proved the actual enabled-to-disabled
# transition: bose was genuinely --lock-in'd, this wrote the marker, and
# two real reboots later A was genuinely writable again. That third run
# needed recovering by hand first, since the still-broken escape hatch on
# bose at the time could not yet unlock itself -- the fixed script had to
# be deployed while A was briefly writable through a manual equivalent of
# what it now does automatically.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
. BosePi/lib.sh

HOST="${HOST:-pi@bose}"
MODE="${1:-}"
[ "$MODE" = "--check" ] || [ "$MODE" = "--go" ] || die "usage: request-unlock.sh --check|--go"

caveat \
    "Proven for real, including the actual enabled-to-disabled transition" \
    "-- see the header for the bug that first two attempts found and fixed."

step "Preconditions"
check "bose reachable"      ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" true
check "sudo works over SSH" ssh "$HOST" sudo -n true
check "the escape hatch is actually installed" ssh "$HOST" test -x /usr/local/sbin/lempi-unlock-check.sh

step "Plan"
say "1. write /var/lempi/unlock/request on $HOST"
say "2. reboot -- this boot notices the marker, removes overlayroot= from"
say "   cmdline.txt, reboots again"
say "3. a second reboot lands in a writable A and B"
say "bose will be briefly unreachable through both reboots."

if [ "$MODE" != "--go" ]; then
    say ""
    say "Check only. Re-run with --go to perform this."
    exit 0
fi

run "write the marker" ssh "$HOST" "sudo mkdir -p /var/lempi/unlock && sudo touch /var/lempi/unlock/request"
run "reboot" ssh "$HOST" "sudo reboot" || true

say ""
say "Requested. bose will reboot twice on its own; check back with:"
say "  ssh $HOST findmnt -no OPTIONS /srv/library"
say "  ssh $HOST cat /var/lempi/unlock/log"
say "Log: $LOG_FILE"
