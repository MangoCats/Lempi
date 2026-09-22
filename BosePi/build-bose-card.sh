#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Master orchestrator for building a Lempi card for bose. Detects which
# phase comes next from observable system state and either runs it or tells
# you the one physical action needed to reach the next state -- it cannot
# skip that action, because [IMPL-BOS-100] is a fact about the hardware
# (a running system cannot repartition the card it booted from), not a
# limitation of this script.
#
# **Status: run for real, 2026-09-06** -- drove phases 3 through 5's
# `--start` to completion in one pass (found and fixed a `findmnt -q` bug
# along the way, since fixed here too). Only run once, from one specific
# starting state (bose mid-build, not yet provisioned); the *detection
# logic* for other starting states -- a fresh card not yet attached, a USB
# disk already labelled, more than one USB disk present -- is still
# reasoned from what that one build looked like, not exercised. If its
# guess about what comes next looks wrong for a state this hasn't seen yet,
# trust your own read of the state over this script's, and fix the
# detection rather than working around it quietly.
#
#     bash BosePi/build-bose-card.sh
#
# Safe to re-run at any point: it re-detects state each time rather than
# remembering where it left off, so interrupting it and running it again is
# the normal way to resume, not a special case.
#
# Deliberately stops before finalize-bose.sh --lock-in every time, no matter
# what else is ready -- that step is the point of no easy return
# [IMPL-BOS-120] and is never invoked except by a human, on purpose, having
# actually heard the thing play.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
. BosePi/lib.sh

HOST="${HOST:-pi@bose}"

caveat \
    "Run for real once, from one specific starting state -- see the header" \
    "for which state that was. Read what it says it found below, don't" \
    "just watch for it to finish."

step "Where things stand"

if ! ssh -o ConnectTimeout=8 -o BatchMode=yes "$HOST" true 2>/dev/null; then
    say "bose is not reachable at $HOST."
    say "NEXT: if a fresh card is in a reader on this host, run:"
    say "  powershell -File BosePi/patch-boot-image.ps1 -DriveLetter D"
    say "then move it to bose's own reader and power bose on."
    exit 0
fi
say "bose is reachable."

ROOT_LABEL="$(ssh "$HOST" "sudo blkid -o value -s LABEL \$(findmnt -no SOURCE /) 2>/dev/null")"
if [ "$ROOT_LABEL" != "SYSTEM" ]; then
    say "bose is still booted from its old card (root label: '${ROOT_LABEL:-none}')."
    NEWDEV="$(ssh "$HOST" "lsblk -dno NAME,TRAN | awk '\$2==\"usb\"{print \"/dev/\"\$1}'")"
    NEWDEV_COUNT="$(printf '%s\n' "$NEWDEV" | grep -c .)"
    if [ -z "$NEWDEV" ]; then
        say "NEXT: attach the new card (in its USB reader) to bose, then re-run this script."
    elif [ "$NEWDEV_COUNT" != "1" ]; then
        say "Multiple USB disks attached to bose -- ambiguous. Run prepare-card.sh"
        say "by hand against the right one:"
        say "  scp BosePi/prepare-card.sh $HOST:/tmp/"
        say "  ssh $HOST 'sudo bash /tmp/prepare-card.sh --device /dev/sdX --check'"
    else
        LBL="$(ssh "$HOST" "sudo blkid -L SYSTEM 2>/dev/null")"
        if [ -n "$LBL" ]; then
            say "$NEWDEV is already partitioned (SYSTEM/STATE/LIBRARY present)."
            say "NEXT: power bose off, move this card from its reader into bose's"
            say "internal slot, set the old card safely aside, and power bose back on."
        else
            step "Running prepare-card.sh on bose against $NEWDEV"
            scp -q BosePi/prepare-card.sh "$HOST:/tmp/" || die "scp failed"
            run "prepare-card.sh --check" ssh "$HOST" \
                "sudo bash /tmp/prepare-card.sh --device $NEWDEV --check --skip-write"
            run "prepare-card.sh --go" ssh "$HOST" \
                "sudo bash /tmp/prepare-card.sh --device $NEWDEV --go --skip-write"
            say "NEXT: power bose off, move $NEWDEV from its reader into bose's"
            say "internal slot, set the old card safely aside, and power bose back on."
        fi
    fi
    exit 0
fi
say "bose is booted from the new card (root label: SYSTEM)."

check "sudo works over SSH" ssh "$HOST" sudo -n true

LEMPI_DB_PRESENT=0
ssh "$HOST" test -f /var/lempi/listener.db && LEMPI_DB_PRESENT=1

MOUNTS_OK=0
ssh "$HOST" "findmnt /srv/library >/dev/null 2>&1 && findmnt /var/lempi >/dev/null 2>&1" && MOUNTS_OK=1

if [ "$MOUNTS_OK" != "1" ]; then
    run "provision-bose.sh (phase 3)" bash BosePi/provision-bose.sh "$HOST"
    step "Where things stand"
fi

if [ "$LEMPI_DB_PRESENT" != "1" ]; then
    run "seed-library.sh (phase 4)" bash BosePi/seed-library.sh
fi

LEMPI_ACTIVE=0
ssh "$HOST" systemctl is-active --quiet lempi && LEMPI_ACTIVE=1
if [ "$LEMPI_ACTIVE" != "1" ]; then
    run "finalize-bose.sh --start (phase 5)" bash BosePi/finalize-bose.sh --start
fi

say ""
say "lempi and mpd are running on bose, unlocked (B is still rw, overlay off)."
say "Listen to it. When you're satisfied, lock the card down yourself:"
say "  bash BosePi/finalize-bose.sh --lock-in --confirm-heard-it-play"
say "This script will not do that step on its own [IMPL-BOS-120]."
say "Log: $LOG_FILE"
