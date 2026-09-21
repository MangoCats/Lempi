#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The attended-import operation [PI-B-030], [REQ-HW-150], [IMPL-BOS-150]:
# remount B rw, run one command, sync, remount back to whatever it was
# before -- never longer open than the command itself takes.
#
# Runs on the development host, over SSH, the same home every other phase
# script has. B's mount state is read fresh each time, not assumed: this
# works identically whether B is still pre-lock-in `rw` (`defaults`) or
# post-`--lock-in` `ro` -- "restore what was actually there" is correct in
# both worlds, "assume ro" is not.
#
#     bash BosePi/attended-import.sh --check -- rsync -a ./NewAlbum/ pi@bose:/srv/library/audio/NewAlbum/
#     bash BosePi/attended-import.sh --go    -- rsync -a ./NewAlbum/ pi@bose:/srv/library/audio/NewAlbum/
#
# `--check` prints the plan and touches nothing. There is no default action:
# a script that reopens the one partition this whole design keeps closed
# must be asked twice, the same discipline prepare-card.sh's --check/--go
# already uses. The command after `--` runs exactly as given -- this script
# has no opinion on what belongs in the window, only that the window exists
# and closes. A trap restores B's mount state on any exit, success, failure,
# or interruption, so a misbehaving command cannot leave B open indefinitely.
#
# Status: run for real against bose 2026-09-06, four ways -- a dry run, a
# successful command (wrote and removed a real file inside the window,
# confirmed the MPD reindex trick works with no mpc installed), a failing
# command (confirmed B still closes and nothing after it runs), and a
# fourth run against bose's own real --lock-in'd ro once it existed, not
# only the earlier hand-simulated one. Not yet exercised over a
# long-running or interrupted command. See BosePi/README.md's table.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
. BosePi/lib.sh

HOST="${HOST:-pi@bose}"
MODE=""
UPDATE_MPD=1
CMD=()

while [ $# -gt 0 ]; do
    case "$1" in
        --check) MODE="check"; shift ;;
        --go) MODE="go"; shift ;;
        --no-mpd-update) UPDATE_MPD=0; shift ;;
        --host) HOST="$2"; shift 2 ;;
        --) shift; CMD=("$@"); break ;;
        *) die "unknown argument: $1 (usage: --check|--go [--no-mpd-update] -- <command...>)" ;;
    esac
done
[ -n "$MODE" ] || die "usage: attended-import.sh --check|--go [--no-mpd-update] -- <command...>"
[ "${#CMD[@]}" -gt 0 ] || die "no command given after --"

caveat \
    "Run for real three ways already (dry run, success, failure) -- see" \
    "the header. Not yet against a real --lock-in's own ro, or a long or" \
    "interrupted command. Read this run's own output, don't just trust it."

step "Preconditions"
check "bose reachable"      ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" true
check "sudo works over SSH" ssh "$HOST" sudo -n true
check "B is mounted"        ssh "$HOST" findmnt /srv/library

ORIG_OPTS="$(ssh "$HOST" findmnt -no OPTIONS /srv/library)"
case ",$ORIG_OPTS," in
    *,ro,*) WAS_RO=1 ;;
    *) WAS_RO=0 ;;
esac
say "B's current mount options: $ORIG_OPTS (currently $([ "$WAS_RO" = 1 ] && echo read-only || echo read-write))"

step "Plan"
say "1. $([ "$WAS_RO" = 1 ] && echo "remount B rw (it is currently ro)" || echo "leave B as-is (already rw)")"
say "2. run: ${CMD[*]}"
say "3. sync"
say "4. $([ "$WAS_RO" = 1 ] && echo "remount B back to ro" || echo "leave B as-is (it started rw)")"
say "5. $([ "$UPDATE_MPD" = 1 ] && echo "MPD reindex (best-effort)" || echo "skip MPD reindex (--no-mpd-update)")"

if [ "$MODE" != "go" ]; then
    say ""
    say "Check only. Re-run with --go to perform this."
    exit 0
fi

step "Opening B"
if [ "$WAS_RO" = 1 ]; then
    run "remount rw" ssh "$HOST" "sudo mount -o remount,rw /srv/library"
else
    say "already rw, nothing to change"
fi

# The trap is what makes this safe against the command failing, hanging and
# being killed, or this script itself being interrupted -- B closes again
# regardless of how the command exit. Idempotent by construction: running
# the remount-back a second time (e.g. if the trap fires after an already-
# clean close) is a no-op, not an error.
CLOSED=0
close_b() {
    [ "$CLOSED" = 1 ] && return
    CLOSED=1
    say ""
    say "closing B (mount state restore, always attempted)"
    ssh "$HOST" sync 2>/dev/null || true
    if [ "$WAS_RO" = 1 ]; then
        if ssh "$HOST" "sudo mount -o remount,ro /srv/library" 2>/dev/null; then
            say "B restored to read-only"
        else
            say "WARNING: could not remount B back to read-only -- check by hand:"
            say "  ssh $HOST findmnt -no OPTIONS /srv/library"
        fi
    fi
}
trap close_b EXIT INT TERM

step "Running the command"
"${CMD[@]}"
CMD_RC=$?
say "command exited $CMD_RC"

close_b
trap - EXIT INT TERM

if [ "$CMD_RC" != 0 ]; then
    die "the wrapped command failed ($CMD_RC) -- B has still been closed; nothing else here ran"
fi

if [ "$UPDATE_MPD" = 1 ]; then
    step "MPD reindex (best-effort -- B's own index changed if the audio did)"
    # No mpc client installed [provision-bose.sh installs mpd, not mpc] --
    # bash's own /dev/tcp needs no extra package, speaking MPD's protocol
    # directly on its control port.
    if ssh "$HOST" 'bash -c "exec 3<>/dev/tcp/127.0.0.1/6600 && printf \"update\n\" >&3 && head -n2 <&3"' 2>/dev/null; then
        say "update requested"
    else
        say "could not reach MPD to request an update -- check by hand once B settles"
    fi
fi

say ""
say "Done. Log: $LOG_FILE"
