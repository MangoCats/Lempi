#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Put ONE file on an appliance so that it is still there after a reboot.
#
#     build/install-config.sh HOST LOCAL REMOTE [MODE] [--daemon-reload]
#
#     build/install-config.sh pi@bose BosePi/lempi-bose.service \
#         /etc/systemd/system/lempi.service 644 --daemon-reload
#
# This exists because `scp` and `install` do NOT do that on an overlay-rooted
# appliance, and say nothing about it `[IMPL-BOS-185]`. On `bose`, `/` is an
# overlay whose upper layer is tmpfs: an ordinary write succeeds, is readable,
# survives a service restart, passes every check anyone thought to run -- and
# is gone at the next power cycle. Five days of player deploys went that way,
# and so did the unit file that knew the database had been split, which is the
# one that actually stopped the music.
#
# So the rule `[GDE-DEP-070]`: write BOTH layers, and verify the DURABLE one.
# The live copy is what runs now; the copy on the lower filesystem is what runs
# after a reboot, and only the second is evidence of anything.
#
# `[GDE-DEP-060]`: it says which kind of target it thinks it has before acting.
# A script that silently assumes produces a log indistinguishable from one that
# assumed correctly.
#
# Non-overlay hosts (lempi02w, and any ordinary machine) take the plain path and
# are told so -- the detection is on the target's actual mount, never on its
# name, which is the mistake this whole family of scripts is recovering from.
set -uo pipefail

HOST="${1:-}"
LOCAL="${2:-}"
REMOTE="${3:-}"
MODE="${4:-644}"
RELOAD=""
for a in "$@"; do [ "$a" = "--daemon-reload" ] && RELOAD=1; done
case "$MODE" in --daemon-reload) MODE=644 ;; esac

die() { echo "install-config: $*" >&2; exit 1; }

[ -n "$HOST" ] && [ -n "$LOCAL" ] && [ -n "$REMOTE" ] \
    || die "usage: install-config.sh HOST LOCAL REMOTE [MODE] [--daemon-reload]"
[ -f "$LOCAL" ] || die "no such local file: $LOCAL"
case "$REMOTE" in /*) ;; *) die "REMOTE must be absolute, got: $REMOTE" ;; esac

ssh -o ConnectTimeout=10 "$HOST" true 2>/dev/null || die "$HOST is not reachable"

# What kind of target is this? Announced, not assumed.
LOWER=""
if [ "$(ssh "$HOST" "findmnt -no FSTYPE /" 2>/dev/null)" = "overlay" ]; then
    LOWER=$(ssh "$HOST" "findmnt -no OPTIONS / | tr ',' '\n' | sed -n 's/^lowerdir=//p'" 2>/dev/null)
    [ -n "$LOWER" ] || die "overlay root on $HOST but no lowerdir -- refusing to write blind"
    echo "install-config: $HOST has an OVERLAY root; durable copy goes to $LOWER"
else
    echo "install-config: $HOST has a plain writable root; one copy is enough"
fi

LOCAL_SUM=$(md5sum "$LOCAL" | cut -d' ' -f1)
STAGE=/tmp/install-config.$$
scp -q "$LOCAL" "$HOST:$STAGE" || die "upload failed"
ssh "$HOST" "md5sum $STAGE | grep -q $LOCAL_SUM" \
    || { ssh "$HOST" "rm -f $STAGE"; die "uploaded file does not match; nothing written"; }

# The live copy: what is running now.
ssh "$HOST" "sudo install -D -m $MODE $STAGE $REMOTE" \
    || { ssh "$HOST" "rm -f $STAGE"; die "could not write $REMOTE"; }
echo "install-config: wrote $REMOTE (live)"

# The durable copy: what runs after a reboot. Skipped entirely off an overlay.
if [ -n "$LOWER" ]; then
    ssh "$HOST" "sudo mount -o remount,rw $LOWER \
        && sudo install -D -m $MODE $STAGE $LOWER$REMOTE \
        && sudo sync" \
        || { ssh "$HOST" "rm -f $STAGE"; die "could not write $LOWER$REMOTE -- this change is RAM-only"; }
    GOT=$(ssh "$HOST" "sudo md5sum $LOWER$REMOTE 2>/dev/null | cut -d' ' -f1")
    [ "$GOT" = "$LOCAL_SUM" ] \
        || { ssh "$HOST" "rm -f $STAGE"; die "durable copy is ${GOT:-absent}, expected $LOCAL_SUM"; }
    echo "install-config: wrote $LOWER$REMOTE (durable, $GOT)"
    # Best effort. An overlay holds its own lower layer, so this can return
    # EBUSY -- observed on bose after an apt run in the chroot. Say which
    # happened rather than implying the card is protected again.
    if ssh "$HOST" "sudo mount -o remount,ro $LOWER" 2>/dev/null; then
        echo "install-config: $LOWER returned to read-only"
    else
        echo "install-config: WARNING -- $LOWER left read-write; returns to ro at the next reboot" >&2
    fi
fi

ssh "$HOST" "rm -f $STAGE"

if [ -n "$RELOAD" ]; then
    ssh "$HOST" "sudo systemctl daemon-reload" || die "daemon-reload failed"
    echo "install-config: systemd reloaded"
fi
