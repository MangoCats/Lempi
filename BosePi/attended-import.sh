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
#     bash BosePi/attended-import.sh --check -- rsync -a --chmod=D755,F644 ./NewAlbum/ pi@bose:/srv/library/audio/NewAlbum/
#     bash BosePi/attended-import.sh --go    -- rsync -a --chmod=D755,F644 ./NewAlbum/ pi@bose:/srv/library/audio/NewAlbum/
#
# `--chmod` because `-a` copies the source's modes, and music on a Windows
# drive is 0777 to rsync -- how the whole library came to be world-writable
# until 2026-09-25.
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
#
# The catalogue guard [BOS-RUN-092], added 2026-09-25 after a window closed B
# over a WAL catalogue and bose went silent for 30 minutes: exercised on bose
# with the real catalogue (rollback, closed), a WAL file (switched, closed), a
# WAL file held open (locked, B left rw, exit 1) and no file (said so).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
. BosePi/lib.sh

HOST="${HOST:-pi@bose}"
MODE=""
UPDATE_MPD=1
CMD=()
# The catalogue B holds. It must not be WAL when B closes [BOS-RUN-092]: on
# ro media a WAL file opens only while its -shm/-wal exist, and a clean close
# of the last connection deletes them -- which is how bose went silent for 30
# minutes on 2026-09-25 [BOS-RUN-090].
CATALOGUE="${CATALOGUE:-/srv/library/library.db}"
GUARD_FAILED=0

# The journal mode from the file header itself (bytes 18-19: 1 rollback, 2
# WAL), read with od -- opening it with SQLite to ask would be a connection,
# and a connection is what creates and deletes the sidecars in question.
journal_of() {
    local b
    ssh "$HOST" "test -e '$CATALOGUE'" 2>/dev/null || { echo absent; return; }
    b=$(ssh "$HOST" "od -An -tu1 -j18 -N2 '$CATALOGUE'" 2>/dev/null | tr -s ' ' | sed 's/^ //;s/ $//')
    case "$b" in
        "1 1") echo rollback ;;
        "2 2") echo wal ;;
        *) echo "unknown (header bytes 18-19: '${b}')" ;;
    esac
}

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
say "catalogue $CATALOGUE: journal $(journal_of)"

step "Plan"
say "1. $([ "$WAS_RO" = 1 ] && echo "remount B rw (it is currently ro)" || echo "leave B as-is (already rw)")"
say "2. run: ${CMD[*]}"
say "3. $([ "$WAS_RO" = 1 ] && echo "catalogue must not be WAL: switch it to DELETE if it is, and keep B rw if that fails [BOS-RUN-092]" || echo "no catalogue check (B stays rw, where WAL is safe)")"
say "4. sync"
say "5. $([ "$WAS_RO" = 1 ] && echo "remount B back to ro" || echo "leave B as-is (it started rw)")"
say "6. $([ "$UPDATE_MPD" = 1 ] && echo "MPD reindex (best-effort)" || echo "skip MPD reindex (--no-mpd-update)")"

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
# Whether B may go back to ro [BOS-RUN-092]. A catalogue that is still WAL,
# or whose mode cannot be read, keeps B rw: rw is safe for the player (SQLite
# recreates the sidecars), ro over WAL is the outage. The failure this picks
# is the loud, harmless one.
catalogue_safe_to_close() {
    local mode out
    mode=$(journal_of)
    case "$mode" in
        rollback) say "catalogue: rollback journal -- safe on ro media"; return 0 ;;
        absent)   say "catalogue: NO FILE at $CATALOGUE -- the check had nothing to check; closing"; return 0 ;;
        wal)
            say "catalogue: WAL -- switching to DELETE before B closes [BOS-RUN-092]"
            out=$(ssh "$HOST" "sqlite3 '$CATALOGUE' 'PRAGMA journal_mode=DELETE;'" 2>&1)
            say "  sqlite3 said: $out"
            mode=$(journal_of)
            if [ "$mode" = rollback ]; then
                say "catalogue: now a rollback journal"
                return 0
            fi
            say "catalogue: STILL $mode -- is something holding it open (lempi)?" ;;
        *)
            say "catalogue: journal mode $mode" ;;
    esac
    return 1
}

close_b() {
    [ "$CLOSED" = 1 ] && return
    CLOSED=1
    say ""
    say "closing B (mount state restore, always attempted)"
    if [ "$WAS_RO" = 1 ] && ! catalogue_safe_to_close; then
        GUARD_FAILED=1
        ssh "$HOST" sync 2>/dev/null || true
        say "LEAVING B READ-WRITE: closing it over this catalogue would stop the"
        say "player at its next start [BOS-RUN-090]. Fix the journal mode, then:"
        say "  ssh $HOST sudo mount -o remount,ro /srv/library"
        return
    fi
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
    [ "$GUARD_FAILED" = 1 ] \
        && die "the wrapped command failed ($CMD_RC), and B was LEFT read-write -- see above; nothing else here ran"
    die "the wrapped command failed ($CMD_RC) -- B has still been closed; nothing else here ran"
fi
if [ "$GUARD_FAILED" = 1 ]; then
    die "the command succeeded, but B was left read-write because the catalogue is not safe on ro media [BOS-RUN-092]"
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
