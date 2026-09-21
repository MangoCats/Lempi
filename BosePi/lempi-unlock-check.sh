#!/bin/bash
# SPDX-License-Identifier: MIT
#
# The lock-in escape hatch [IMPL-BOS-160], [REQ-HW-155]. Runs at every boot,
# very early, via lempi-unlock-check.service -- does nothing unless a marker
# exists, in which case it undoes A's overlay and reboots.
#
# Installed on A. Must be in place *before* --lock-in ever runs: once A is
# the overlay's read-only lower layer, adding anything to it needs the
# overlay disabled first -- exactly what this script exists to do, so it
# cannot be its own bootstrap.
#
# KNOWN DEFECT [IMPL-BOS-175], found 2026-09-11: removing the cmdline token
# is only half the lock. BOSE003 step 10 also sets `ro` in fstab for A, which
# this script cannot touch (see the scope note below), and [IMPL-BOS-170]
# no-op'd systemd-remount-fs. So A comes back read-only with no upper layer
# -- worse than locked -- and on a wireless machine NetworkManager cannot
# write /var/lib/NetworkManager, so it never rejoins the network. For
# persistent changes to a locked card use `overlayroot-chroot` instead
# [IMPL-BOS-180]; it needs no reboot. Treat this hatch as unusable until it
# restores fstab too.
#
# Status: found broken on its first real test against an actual lock-in
# (2026-09-06). Originally called `raspi-config nonint do_overlayfs 1`;
# that function's own disable_overlayfs() does `sed
# 's/overlayroot=tmpfs \(.*\)/.../'` -- a literal match with nothing after
# "tmpfs ". [IMPL-BOS-165]'s own recurse=0 fix (needed so B and C don't
# also get overlaid) means cmdline.txt never looks like that again, so
# raspi-config's own sed silently substitutes zero times and reports
# success anyway. Found by checking cmdline.txt directly rather than
# trusting the exit code -- the same lesson [IMPL-BOS-165] had just
# taught, applied a second time in the same evening. Rewritten to edit
# cmdline.txt directly instead of going through raspi-config for this
# specific step, and to verify the actual file afterward. Confirmed
# working against a real enabled-to-disabled transition, not only the
# already-disabled no-op case the first version was tested against.
#
# Deliberately narrow, scoped down from an earlier draft that also tried
# to restore B's fstab to rw: any edit to a file that lives on A (fstab
# included) made while A is *still* overlaid lands in the RAM upper layer
# and is lost on the very reboot this script itself triggers -- the same
# transient-write trap this whole design exists to avoid, self-inflicted.
# Restoring A alone is real and durable, because cmdline.txt lives on the
# boot partition, which is never overlaid. Reopening B is
# attended-import.sh's job, unaffected by any of this.
set -uo pipefail

STATE_DIR=/var/lempi/unlock
MARK_C="$STATE_DIR/request"
MARK_BOOT=/boot/firmware/unlock-request
LOG="$STATE_DIR/log"
CMDLINE=/boot/firmware/cmdline.txt

mkdir -p "$STATE_DIR"

# Two independent locations, on purpose: C is reachable over SSH with no
# card needed at all; the boot partition is plain FAT, writable from any
# reader if SSH itself is what's broken.
if [ ! -e "$MARK_C" ] && [ ! -e "$MARK_BOOT" ]; then
    exit 0
fi

{
    echo "$(date -Is) unlock requested (C:$([ -e "$MARK_C" ] && echo yes || echo no) boot:$([ -e "$MARK_BOOT" ] && echo yes || echo no))"

    # Direct edit, not raspi-config -- see the header for why that no
    # longer works once recurse=0 is present. Matches "overlayroot=" plus
    # whatever follows up to the next space, regardless of what parameters
    # it carries, so it does not go stale again the next time this file's
    # own value changes.
    mount -o remount,rw /boot/firmware
    sed -i 's/overlayroot=[^ ]* //' "$CMDLINE"
    mount -o remount,ro /boot/firmware
    echo "cmdline.txt now: $(cat "$CMDLINE")"
} >> "$LOG" 2>&1

# Checked directly, not assumed from sed's own exit status, which reports
# success whether or not anything actually matched.
if ! grep -q 'overlayroot=' "$CMDLINE"; then
    rm -f "$MARK_C" "$MARK_BOOT"
    echo "$(date -Is) overlayroot removed, markers cleared, rebooting" >> "$LOG"
else
    echo "$(date -Is) overlayroot still present after the edit -- marker kept, will retry next boot" >> "$LOG"
fi

reboot
