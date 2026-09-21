#!/bin/bash
# Stop the locally-running Lempi, rebuild it, and relaunch it with the same
# invocation -- the single most frequently repeated step of this project's
# own development loop, previously three manual steps and a Windows-specific
# gotcha (a locked .exe, and a globally-set CC pointed at MinGW) rediscovered
# by hand each time [see build/README.md and HOWTO.md #2 for the underlying
# `env -u CC` story].
#
# Run from anywhere; the repository root is found from this script's own
# location, the same way build/verify-targets.sh already does.
#
#     build/deploy-local.sh                        # the split pair, default port
#     build/deploy-local.sh mylib.db --port 6000    # anything else, passed straight through
#
# set -u only, not -e: `taskkill` legitimately exits non-zero when nothing
# was running, which is success here, not a failure to propagate.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
BIN="$ROOT/player/target/release/lempi.exe"

die() { echo "deploy-local: $*" >&2; exit 1; }

# A default invocation when none is given; anything the caller does pass is
# used verbatim instead -- the same shape `build/install-player.sh` already
# uses for its own $HOST default.
if [ "$#" -eq 0 ]; then
    # The split pair [IMPL-DBSPLIT-025], in the same shape both appliances'
    # units use -- every option named [GDE-CLI-020].
    set -- --listener "$ROOT/data/listener.db" --library "$ROOT/data/library.db"
fi

# The port to verify against below. Whatever follows --port if the caller
# named one; otherwise NOT a guess -- the player prints the port it actually
# bound, and that line is read out of the log further down. A script that
# computes what it expects and then checks that is checking its own
# arithmetic `[GDE-DEP-070]`.
. "$ROOT/build/lib-defaults.sh"
PORT=""
prev=""
for arg in "$@"; do
    [ "$prev" = "--port" ] && PORT="$arg"
    prev="$arg"
done

echo "deploy-local: stopping any running lempi.exe ..."
taskkill //IM lempi.exe //F >/dev/null 2>&1 || true
# Windows needs a moment to actually release the file handle after taskkill
# returns -- rebuilding immediately hits "Access is denied" removing the old
# .exe often enough to be worth one short, bounded retry below rather than a
# fixed sleep guessed once and never revisited.
sleep 1

echo "deploy-local: building (env -u CC, --features vipunen-support) ..."
built=0
for _ in 1 2 3; do
    if ( cd "$ROOT/player" && env -u CC cargo build --release --features vipunen-support ); then
        built=1
        break
    fi
    echo "deploy-local: build failed (binary likely still locked) -- retrying ..." >&2
    sleep 2
done
[ "$built" = 1 ] || die "build did not succeed after retries"
[ -f "$BIN" ] || die "build succeeded but $BIN is missing"

LOG="$ROOT/player/target/release/lempi-local.log"
echo "deploy-local: launching -- log at $LOG"
( cd "$ROOT" && nohup "$BIN" "$@" >"$LOG" 2>&1 & )

# Polled, not a fixed wait -- the same reasoning `install-player.sh` already
# gives for not guessing a sleep duration against a program director whose
# own startup time scales with library size.
DEADLINE=${LEMPI_DEPLOY_WAIT:-30}
got=""
for _ in $(seq 1 "$DEADLINE"); do
    # **Ask the process, do not compute it** `[GDE-DEP-070]`. The player
    # prints the port it actually bound; a script that recomputes the same
    # default and polls that is checking its own arithmetic, and would call
    # a healthy player dead the moment LEMPI_PORT or a stored setting moved
    # it `[GDE-CLI-090]`.
    if [ -z "$PORT" ] && [ -f "$LOG" ]; then
        PORT=$(sed -n 's#.*web UI on http://localhost:\([0-9][0-9]*\)/.*#\1#p' "$LOG" | head -1)
    fi
    if [ -n "$PORT" ]; then
        got=$(curl -s --max-time 2 "http://localhost:$PORT/build" 2>/dev/null)
        [ -n "$got" ] && break
    fi
    sleep 1
done
[ -n "$PORT" ] || die "the player never said which port it bound -- see $LOG"
[ -n "$got" ] || die "new process did not answer on port $PORT within ${DEADLINE}s -- see $LOG"

head_sha=$(cd "$ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "deploy-local: running -- $got"
case "$got" in
    *"$head_sha"*) echo "deploy-local: matches HEAD ($head_sha)" ;;
    *) echo "deploy-local: WARNING -- reported build does not mention HEAD ($head_sha); check for uncommitted changes" >&2 ;;
esac
