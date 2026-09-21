#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Phase 5: bring lempi and mpd up, then -- only when explicitly told the
# machine has been heard playing -- lock the card down [IMPL-BOS-120].
#
# **Status: --start and --lock-in both run for real against bose, 2026-09-06.**
# --lock-in found this header's own earlier claim wrong, worth keeping
# visible rather than quietly rewritten away: reading do_overlayfs()'s
# *wrapper* logic is not the same as reading what
# enable_overlayfs() actually does, and on this trixie-era image it turned
# out to be Debian's own `overlayroot` package, not the historic Pi-specific
# initramfs hook -- installed live, on the spot (cryptsetup pulled in as a
# dependency). It worked, but its default (`recurse=1`) overlays *every*
# mount, not only `/` -- B and C both got wrapped in their own writable RAM
# layer, silently discarding every write to listener.db on the next reboot,
# exactly the failure this whole design exists to prevent. Found within
# minutes by checking `findmnt` rather than trusting the reboot's success,
# fixed live (`overlayroot=tmpfs:recurse=0` in cmdline.txt), and folded into
# this script below so it can't recur. `nonint` is raspi-config's stable
# public interface; what it delegates to underneath is not, and this is the
# second time this file has learned that the hard way rather than the first.
# Read this script's output, especially --lock-in's, rather than trusting
# its exit code alone.
#
#     bash BosePi/finalize-bose.sh --start
#     bash BosePi/finalize-bose.sh --lock-in --confirm-heard-it-play
#
# Two subcommands, not one script with a flag that defaults somewhere: --start
# is safe and idempotent, reversible by re-running it. --lock-in is the point
# of no easy return this project's own docs name explicitly -- after it, a
# mistake in /etc means another card swap, so it refuses to run without the
# one flag that cannot be typed by accident, and does not infer consent from
# anything else in this script's own state.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
. BosePi/lib.sh

HOST="${HOST:-pi@bose}"
MODE="${1:-}"

case "$MODE" in
    --start)   ;;
    --lock-in) [ "${2:-}" = "--confirm-heard-it-play" ] || die \
        "refusing: --lock-in requires --confirm-heard-it-play as well." \
        "This is deliberately not inferable from any other state -- see" \
        "[IMPL-BOS-120]. Play something through bose and listen to it first." ;;
    *) die "usage: finalize-bose.sh --start | --lock-in --confirm-heard-it-play" ;;
esac

caveat \
    "Both --start and --lock-in have run for real -- see the header for" \
    "the two bugs that run found and fixed. Watch each step below anyway;" \
    "a different Raspberry Pi OS release can still change what runs here."

step "Preconditions"
check "bose reachable"       ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" true
check "sudo works over SSH"  ssh "$HOST" sudo -n true
check "lempi binary present" ssh "$HOST" test -x /usr/local/bin/lempi
check "lempi.db present"     ssh "$HOST" test -f /var/lempi/listener.db
check "mpd installed"        ssh "$HOST" command -v mpd

if [ "$MODE" = "--start" ]; then
    step "Install the startup helpers the unit names"
    # The unit has two ExecStartPre lines, and systemd counts a missing
    # ExecStartPre as a failed start -- which with Restart=always is the very
    # crash loop lempi-db-recover exists to prevent. Ship them BEFORE the unit
    # that names them; half a mechanism is the recurring mistake here
    # [BOS-RUN-085].
    scp -q LempiPi/lempi-preflight LempiPi/lempi-db-recover "$HOST:/tmp/" ||
        die "scp helpers failed"
    for h in lempi-preflight lempi-db-recover; do
        run "install $h" ssh "$HOST" "sudo install -m755 /tmp/$h /usr/local/bin/$h"
    done

    step "Install lempi's unit"
    scp -q BosePi/lempi-bose.service "$HOST:/tmp/lempi.service" || die "scp failed"
    run "install unit + daemon-reload" ssh "$HOST" \
        "sudo cp /tmp/lempi.service /etc/systemd/system/lempi.service && sudo systemctl daemon-reload"

    step "Start mpd and lempi (not enabled yet -- that's --lock-in's job)"
    run "start mpd"   ssh "$HOST" "sudo systemctl unmask mpd.socket 2>/dev/null; sudo systemctl start mpd"
    run "start lempi" ssh "$HOST" "sudo systemctl start lempi"

    step "Check both are actually active, not just accepted"
    check "mpd is active"   ssh "$HOST" systemctl is-active --quiet mpd
    check "lempi is active" ssh "$HOST" systemctl is-active --quiet lempi
    say "Both running. Play something through bose before running --lock-in."
    exit 0
fi

# --lock-in from here.
step "One more readiness check specific to locking in"
check "mpd is active"   ssh "$HOST" systemctl is-active --quiet mpd
check "lempi is active" ssh "$HOST" systemctl is-active --quiet lempi

step "B read-only in fstab [BOSE003 step 10]"
# sed, not a blind append: idempotent against a re-run, and against this
# already having been done by hand. Matches the exact line
# provision-bose.sh's own fstab step writes -- if that line's format ever
# changes, this pattern needs to change with it; it will silently do nothing
# rather than error, which is why the actual fstab line is always echoed
# below instead of trusting the sed's own exit status.
run "set LIBRARY ro in /etc/fstab" ssh "$HOST" \
    "grep -q 'LABEL=LIBRARY.*[^r][^o][[:space:]]' /etc/fstab && sudo sed -i \
        's#\\(LABEL=LIBRARY[[:space:]]\\+/srv/library[[:space:]]\\+ext4[[:space:]]\\+\\)defaults#\\1ro#' \
        /etc/fstab || true"
say "verify this line actually says 'ro', not just that the command exited 0:"
say "$(ssh "$HOST" grep LIBRARY /etc/fstab)"

caveat \
    "About to enable the overlay and write-protect /boot/firmware. This is" \
    "[IMPL-BOS-120]'s point of no easy return -- after this, a mistake in" \
    "/etc means another card swap. The call below was verified by reading" \
    "raspi-config's source on THIS card on 2026-09-06, not by having run it."
step "Enable overlay on A + write-protect /boot/firmware [do_overlayfs]"
run "raspi-config nonint do_overlayfs 0" ssh "$HOST" sudo raspi-config nonint do_overlayfs 0
say "Read the output above: raspi-config prints what it actually did."

caveat \
    "do_overlayfs's own default overlays every mount, not only A -- found" \
    "live, the hard way. Scoping it to A alone before this ever reboots."
step "Scope the overlay to A only [IMPL-BOS-165, found live 2026-09-06]"
# overlayroot's default is recurse=1: B and C would both get wrapped in
# their own writable RAM layer too, discarding every write to listener.db
# on the next reboot -- exactly the failure this whole design exists to
# prevent. /boot/firmware is a plain vfat mount, not itself overlaid, so a
# direct remount reaches it even though do_overlayfs just made it ro.
run "add recurse=0 to overlayroot's cmdline.txt parameter" ssh "$HOST" \
    "sudo mount -o remount,rw /boot/firmware && \
     (grep -q 'overlayroot=tmpfs:recurse=' /boot/firmware/cmdline.txt || \
      sudo sed -i 's/overlayroot=tmpfs\\([: ]\\)/overlayroot=tmpfs:recurse=0\\1/' /boot/firmware/cmdline.txt); \
     sudo mount -o remount,ro /boot/firmware"
say "verify this actually says recurse=0, not just that the command exited 0:"
say "$(ssh "$HOST" "grep -o 'overlayroot=[^ ]*' /boot/firmware/cmdline.txt")"

step "Enable lempi and mpd at boot (not done until now, deliberately)"
# Found live: `systemctl enable mpd` re-enables mpd.socket too, via Debian's
# sysv-install compat shim -- undoing provision-bose.sh's own
# `systemctl mask mpd.socket` (the shared-sink arrangement [IMPL-BOS-030]
# needs mpd bound directly, not socket-activated). Re-masked immediately
# after, not left to whatever the next boot would have raced.
run "systemctl enable mpd lempi" ssh "$HOST" "sudo systemctl enable mpd lempi"
run "re-mask mpd.socket" ssh "$HOST" "sudo systemctl mask mpd.socket"

step "Reboot into the locked-down card"
say "This is the point of no easy return [IMPL-BOS-120]. Rebooting now."
ssh "$HOST" "sudo reboot" || true
say "bose is rebooting. Give it a minute, then verify:"
say "  ssh $HOST 'findmnt -no FSTYPE,OPTIONS /; findmnt -no FSTYPE,OPTIONS /srv/library; findmnt -no FSTYPE,OPTIONS /var/lempi; systemctl is-active lempi mpd'"
say "/ should say overlay. /srv/library and /var/lempi should NOT -- if either"
say "shows fstype overlay with a lowerdir=/upperdir= in its options, B or C got"
say "wrapped in overlayroot's own writable RAM layer too [IMPL-BOS-165]."
say "Log: $LOG_FILE"
