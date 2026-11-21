#!/bin/bash
# Restart the desktop's Vipunen console and its Lempi player, safely, in one
# command -- run from Git Bash. Until 2026-11-20 this was two scripts and a
# rule: `build/deploy-local.sh` for the player, `tools/launch_vipunen_console.bat`
# for the console (which starts one and stops none, and from cmd, whose PATH a
# console's jobs then inherit -- the reason a console is started from Git Bash).
#
#     build/restart-local.sh                  # both: the console stopped, the player rebuilt, the console started
#     build/restart-local.sh --console-only   # the console alone -- a change to tools/ needs no more
#     build/restart-local.sh --player-only    # the player alone, as build/deploy-local.sh
#
# Safe, which means four things:
#
# - **Nothing is stopped while a console job runs.** The console's own
#   shutdown refuses then `[SPEC-SUI-212]` -- a remote push stops a node's
#   player mid-sync, and a finish job writes the catalogue -- and this script
#   asks before touching the player, so a refusal leaves both running.
# - **The console comes back as it was.** Its database, its audio roots and
#   its port are what the running console says of itself (`/api/system`), not
#   what this script would guess `[GDE-DEP-070]`. With none running, the
#   defaults are said: the split pair in data/, and ~/Music where it exists.
# - **The player's playing state is its own.** The player resumes what it was
#   doing when it stopped; this reads its startup log and says which, and
#   never presses play.
# - **A failed player build still brings the console back**, and the script
#   then exits non-zero.
#
# Environment: VIPUNEN_PORT names the console's port when none is running to
# ask (otherwise console.py's own default); VIPUNEN_ROOT the audio root then.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
die() { echo "restart-local: $*" >&2; exit 1; }
say() { echo "restart-local: $*"; }

DO_CONSOLE=1
DO_PLAYER=1
case "${1:-}" in
    "") ;;
    --console-only) DO_PLAYER=0 ;;
    --player-only) DO_CONSOLE=0 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown option $1 -- see --help" ;;
esac

# One number, read from where it is defined, not written again here.
DEFAULT_CONSOLE_PORT=$(sed -n 's/^DEFAULT_PORT = \([0-9][0-9]*\).*/\1/p' "$ROOT/tools/console.py" | head -1)
[ -n "$DEFAULT_CONSOLE_PORT" ] || die "could not read DEFAULT_PORT from tools/console.py"
CPORT=${VIPUNEN_PORT:-$DEFAULT_CONSOLE_PORT}
LOG="$ROOT/data/vipunen-console.log"

# A JSON field of stdin, by a Python expression over `d`; empty when absent.
json() { python -I -c "import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
v = $1
if isinstance(v, list):
    print('\n'.join(v))
elif v is not None:
    print(v)"; }

listening() { netstat -ano 2>/dev/null | grep -E "(127\.0\.0\.1|0\.0\.0\.0):$1 .*LISTENING" >/dev/null; }
alive() { tasklist //FI "PID eq $1" //NH 2>/dev/null | grep -q " $1 "; }

# ---- the console, as it is now ----------------------------------------------
SYSTEM=$(curl -s --max-time 3 "http://127.0.0.1:$CPORT/api/system" 2>/dev/null)
OLD_PID=""
if [ -n "$SYSTEM" ] && OLD_PID=$(echo "$SYSTEM" | json 'd["pid"]'); then
    DB=$(echo "$SYSTEM" | json 'd["db_path"]')
    mapfile -t ROOTS < <(echo "$SYSTEM" | json 'd.get("roots") or []')
    active=$(echo "$SYSTEM" | json '" (job ".join(str(x) for x in ((d.get("active_job") or {}).get("kind"), (d.get("active_job") or {}).get("job_id"))) + ")" if d.get("active_job") else None')
    say "the console is running: pid $OLD_PID on port $CPORT, $(echo "$SYSTEM" | json 'd["build"].get("commit_short")'), db $DB, roots ${ROOTS[*]:-(none)}"
    [ -z "$active" ] || die "$active is still running on the console -- nothing was stopped; wait for it or stop it on the Jobs page"
else
    OLD_PID=""
    listening "$CPORT" && die "something listens on port $CPORT but does not answer as the console -- nothing was stopped"
    DB="$ROOT/data/library.db"
    ROOTS=()
    want_root=${VIPUNEN_ROOT:-$HOME/Music}
    [ -d "$want_root" ] && ROOTS=("$want_root")
    [ "$DO_CONSOLE" = 1 ] && say "no console answers on port $CPORT; will start one: db $DB, roots ${ROOTS[*]:-(none -- the console takes the folder every file lies under)}"
fi

# ---- stop the console first: with it down, no job can start under the player --
if [ "$DO_CONSOLE" = 1 ] && [ -n "$OLD_PID" ]; then
    code=$(curl -s -o /tmp/restart-local-shutdown.$$ -w '%{http_code}' --max-time 5 -X POST \
        "http://127.0.0.1:$CPORT/api/system/shutdown")
    body=$(cat /tmp/restart-local-shutdown.$$ 2>/dev/null); rm -f /tmp/restart-local-shutdown.$$
    [ "$code" = 200 ] || die "the console refused to stop ($code): $body -- nothing was stopped"
    for _ in $(seq 1 20); do
        listening "$CPORT" || break
        sleep 1
    done
    listening "$CPORT" && die "the console accepted the shutdown, but port $CPORT is still held after 20 s"
    alive "$OLD_PID" && say "WARNING: port $CPORT is free but pid $OLD_PID is still alive; it is not killed here" >&2
    say "the console stopped"
fi

# ---- the player ----------------------------------------------------------------
player_failed=0
if [ "$DO_PLAYER" = 1 ]; then
    say "the player: build/deploy-local.sh (stop, rebuild, start, verify)"
    if "$ROOT/build/deploy-local.sh"; then
        PLOG="$ROOT/player/target/release/lempi-local.log"
        # What it did about playback, in its own words.
        for _ in $(seq 1 10); do
            state=$(grep -Eo "resuming playback[^\"]*|starting paused[^\"]*" "$PLOG" 2>/dev/null | head -1)
            [ -n "$state" ] && break
            sleep 1
        done
        say "the player: ${state:-did not say it resumed -- it was not playing when it stopped (see $PLOG)}"
    else
        player_failed=1
        say "the player did NOT come back -- see the output above" >&2
    fi
fi

# ---- start the console -------------------------------------------------------------
if [ "$DO_CONSOLE" = 1 ]; then
    args=("$DB")
    for r in "${ROOTS[@]}"; do args+=(--root "$r"); done
    args+=(--port "$CPORT")
    say "starting the console -- log at $LOG"
    echo "---- $(date '+%Y-%m-%dT%H:%M:%S') restart-local.sh: python tools/console.py ${args[*]}" >>"$LOG"
    ( cd "$ROOT" && nohup python tools/console.py "${args[@]}" >>"$LOG" 2>&1 & )
    NEW=""
    for _ in $(seq 1 30); do
        NEW=$(curl -s --max-time 2 "http://127.0.0.1:$CPORT/api/system" 2>/dev/null)
        new_pid=$(echo "$NEW" | json 'd["pid"]' 2>/dev/null)
        [ -n "$new_pid" ] && [ "$new_pid" != "$OLD_PID" ] && break
        new_pid=""
        sleep 1
    done
    [ -n "$new_pid" ] || die "the console did not answer on port $CPORT within 30 s -- see $LOG"
    built=$(echo "$NEW" | json 'd["build"].get("commit_short")')
    head_sha=$(cd "$ROOT" && git rev-parse --short HEAD 2>/dev/null || echo unknown)
    say "the console is up: pid $new_pid, http://127.0.0.1:$CPORT/, $built" \
        "$([ "$built" = "$head_sha" ] && echo "(HEAD)" || echo "(WARNING: HEAD is $head_sha)")"
    # What the .bat asked first, said here last: whether the hub needs attention.
    hub=$(cd "$ROOT" && python tools/star_sync.py fleet/star-plan.json status 2>&1 | grep '^RESULT' | tail -1)
    [ -z "$hub" ] || say "hub: ${hub#RESULT }"
fi

[ "$player_failed" = 0 ] || exit 1
