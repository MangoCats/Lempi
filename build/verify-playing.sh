#!/bin/sh
# Confirm an appliance is actually playing, not merely running.
#
# Written 2026-09-20, after a fleet deploy was verified by hand and every
# convenient check turned out to be a proxy:
#
#   * `systemctl is-active` says the process exists. `[GDE-ECHO-547]` is this
#     project's own case of a settle curve reading `0 underruns, 0 recoveries`
#     for five minutes on a stream that had never opened. A stream that does
#     not exist cannot underrun.
#   * Reading `/proc/asound/card0/...` because it is the first card found
#     `bose` silent: its HiFiBerry is card **2**, and card 0 is the unused
#     onboard jack. The index moves between images -- BOSE001 measured it
#     moving already -- so this searches for the PCM that is open instead of
#     guessing which one should be.
#   * `bluetoothctl` reported `Connected: yes` on `lempipi` while the same
#     machine's bluez node reported `connection = "disconnected"` and an empty
#     transport. Two sources, one question, and no way to rank them by asking
#     either one again `[GOV-SRC-010]`.
#
# So this asks for a **number that has to move**, and says which way it got it.
#
# Usage:  sh build/verify-playing.sh pi@bose
# Exit:   0 playing, 1 not playing or cannot tell.
set -u

HOST=${1:-}
[ -n "$HOST" ] || { echo "verify-playing: usage: verify-playing.sh HOST" >&2; exit 2; }
# The default port has ONE definition `[GDE-CLI-100]`, and writing 5720 here
# again broke the crate's own test that holds that line. Ask the helper.
. "$(dirname "$0")/lib-defaults.sh"
PORT=$(lempi_port)
say() { echo "verify-playing: $*"; }
die() { echo "verify-playing: $*" >&2; exit 1; }

# ---------------------------------------------------------------- layer 1
# A dummy sink accepts every frame and plays none of it. `[PI3-FOUND-110]` is
# the recorded case: a gate released the player onto a dummy 30 s before the
# speaker existed, and the player then "played" into nothing.
SINK=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" \
    "curl -s -m 4 http://127.0.0.1:$PORT/audio/sink" 2>/dev/null)
case "$SINK" in
    "")            say "no answer from /audio/sink -- the player's web interface is not up" ;;
    *'"dummy":true'*) die "$HOST is feeding a DUMMY sink; frames are consumed and nothing is audible" ;;
    *)             say "sink is not a dummy ($SINK)" ;;
esac

# ---------------------------------------------------------------- layer 2
# The number that has to move. Two ways to get it, and the one used is named
# in the output: a fallback that reads the same as the real thing is the fault
# `[GOV-SRC-010]` exists to prevent.
PROBE='
    # Whose PCM is it? A RUNNING device proves the machine is making sound,
    # not that the PLAYER is -- a browser tab would satisfy a naive check.
    #
    # ALSA reports owner_pid as the **thread id** that opened the device, not
    # the process id. On bose the owner is 457506 against a MainPID of 457497,
    # and that thread is the player`s own `lempi-path`. Comparing the two
    # numbers directly says "not the player" on a perfectly healthy node.
    pid=$(systemctl show -p MainPID --value lempi.service 2>/dev/null)
    [ "${pid:-0}" -gt 0 ] 2>/dev/null || pid=$(pgrep -f "release/lempi" | head -1)
    f=""; ownerdesc="none"
    for c in /proc/asound/card*/pcm*p/sub*/status; do
        [ -e "$c" ] || continue
        grep -q "^state: RUNNING" "$c" 2>/dev/null || continue
        own=$(sed -n "s/^owner_pid *: *//p" "$c")
        if [ -n "${pid:-}" ] && [ -d "/proc/$pid/task/$own" ]; then
            f=$c; tcomm=$(cat "/proc/$pid/task/$own/comm" 2>/dev/null); break
        fi
        ocomm=$(ps -o comm= -p "$own" 2>/dev/null | tr -d " ")
        ownerdesc="${ocomm:-unknown}/$own"
    done
    if [ -n "$f" ]; then
        a=$(sed -n "s/^hw_ptr *: *//p" "$f")
        sleep 2
        b=$(sed -n "s/^hw_ptr *: *//p" "$f")
        echo "ALSA $f $a $b $pid ${tcomm:-?}"
    else
        # Either nothing is open, or something else owns it -- PipeWire or
        # PulseAudio mediating is normal and not a fault. Fall back to the
        # player`s own counter, and NAME the owner so the fallback is visible
        # rather than silently equivalent [GOV-SRC-010].
        # Distinguish "the unit is silent" from "there is no unit". A host
        # running the player from a shell has no journal unit to read, and
        # reporting that as "the output never opened" would be a confident
        # wrong answer where "cannot tell" is the true one.
        if systemctl cat lempi.service >/dev/null 2>&1; then
            # Scoped to THIS invocation, not to a time window. A deploy
            # restarts the unit, and a `--since -6min` window happily returns
            # a clock line written by the process that was replaced -- large,
            # healthy-looking numbers proving the OLD build was playing. That
            # is `[IMPL-BOS-185]` in a different costume: verifying the thing
            # you just superseded.
            inv=$(systemctl show -p InvocationID --value lempi.service 2>/dev/null)
            if [ -n "$inv" ]; then
                line=$(journalctl "_SYSTEMD_INVOCATION_ID=$inv" --no-pager -o cat 2>/dev/null |
                       grep "^clock: frames=" | tail -1)
            else
                line=$(journalctl -u lempi.service --since "-6min" --no-pager -o cat 2>/dev/null |
                       grep "^clock: frames=" | tail -1)
            fi
            # How long this invocation has been up, so a small frame count can
            # be read as "just restarted" rather than as "barely playing".
            #
            # ActiveEnterTimestampMonotonic is the boot-relative instant the
            # unit STARTED, not its age -- on lempipi it read 28040 s against a
            # service that had been up 514. Subtract it from the current
            # monotonic clock here, where both numbers are on the same machine.
            started=$(systemctl show -p ActiveEnterTimestampMonotonic --value lempi.service 2>/dev/null)
            nowmono=$(cut -d" " -f1 /proc/uptime 2>/dev/null)
            age=0
            if [ -n "${started:-}" ] && [ -n "${nowmono:-}" ]; then
                age=$(( ${nowmono%.*} - started / 1000000 ))
                [ "$age" -ge 0 ] 2>/dev/null || age=0
            fi
            echo "CLOCK $ownerdesc $age $line"
        else
            echo "NOUNIT $ownerdesc"
        fi
    fi
'
OUT=$(ssh -o ConnectTimeout=15 -o BatchMode=yes "$HOST" "$PROBE" 2>/dev/null) \
    || die "could not probe $HOST"

case "$OUT" in
ALSA*)
    set -- $OUT
    node=$2; a=$3; b=$4; owner=$5; tcomm=$6
    [ -n "${b:-}" ] || die "$node is open but reported no hw_ptr -- cannot tell"
    delta=$((b - a))
    [ "$delta" -gt 0 ] || die "$node is RUNNING but hw_ptr did not move ($a -> $b); nothing is leaving the device"
    rate=$((delta / 2))
    say "playing -- $node advanced $delta frames in 2s (~${rate} Hz), on the player's own $tcomm thread (pid $owner)"
    # A null sink reports no rate at all, which is why build/verify-targets.sh
    # marks the real-device case manual. Here there IS a rate, so check it: a
    # 44.1 kHz library opened at 48 kHz once ran 8.8% fast and sounded fine to
    # every automated check.
    if [ "$rate" -lt 30000 ] || [ "$rate" -gt 60000 ]; then
        die "implied rate ${rate} Hz is outside anything this fleet plays -- suspect a rate mismatch"
    fi
    ;;
CLOCK*)
    rest=${OUT#CLOCK }
    owner=${rest%% *}
    rest=${rest#* }
    upmono=${rest%% *}
    line=${rest#* }
    [ "$line" = "$rest" ] && line=""
    if [ "$owner" = "none" ]; then
        say "no ALSA PCM is open here -- the path is PipeWire or Bluetooth"
    else
        say "the open PCM belongs to $owner, not the player -- it is mediating, so measuring the player's own clock instead"
    fi
    [ -n "$line" ] || die "no ALSA PCM is open and this invocation of the player has logged no clock line at all -- it may never have opened its output [GDE-ECHO-547]. The clock line is written every 300 s, so a unit restarted moments ago will legitimately have none yet; re-run once it has been up that long."
    frames=$(echo "$line" | sed -n "s/.*frames=\([0-9]*\).*/\1/p")
    cbs=$(echo "$line" | sed -n "s/.*callbacks=\([0-9]*\).*/\1/p")
    ts=$(echo "$line" | sed -n "s/.*ts=\([A-Za-z]*\).*/\1/p")
    [ "${frames:-0}" -gt 0 ] 2>/dev/null || die "the player's clock reports ${frames:-no} frames -- the output never opened"
    [ "${cbs:-0}" -gt 0 ] 2>/dev/null || die "the player's clock reports no callbacks -- the device is not asking for audio"
    [ "$ts" = "Hardware" ] || say "WARNING: clock timestamps are '$ts', not Hardware -- the sink may not be a real device"
    # Frames are only meaningful against how long this invocation has run. 6144
    # frames is 0.14 s of audio: healthy seconds after a restart, alarming on a
    # unit that has been up an hour.
    say "playing -- this invocation's clock reports $frames frames over $cbs callbacks (ts=$ts)"
    if [ "${upmono:-0}" -gt 0 ] 2>/dev/null; then
        say "         after ${upmono}s of uptime (~$(( frames / 44100 ))s of audio)"
    fi
    # Everything above rests on a log line that is written every 300 s, so the
    # newest one can be five minutes stale -- a player that stopped four
    # minutes ago passes every test above it. On 2026-09-21 that read as a
    # stalled counter during the lempipi migration and cost a real diversion:
    # two samples eight seconds apart are inside one emission window, so of
    # course they matched. The period is the measurement's resolution, and a
    # number that cannot change on this timescale must not be sampled as if it
    # could. So ask the graph what is happening NOW instead.
    GRAPH=$(ssh -o ConnectTimeout=15 -o BatchMode=yes "$HOST"         'export XDG_RUNTIME_DIR=/run/user/1000
         command -v pw-top >/dev/null 2>&1 || { echo NOPWTOP; exit 0; }
         timeout 14 pw-top -b -n 3 2>/dev/null |
             awk "/alsa_playback|bluez_output/ {print \$1, \$NF}" | tail -4' 2>/dev/null)
    case $GRAPH in
        NOPWTOP|"")
            say "WARNING: could not sample the PipeWire graph (no pw-top, or it returned nothing)."
            say "         The clock line above may be up to 300 s old and is the ONLY evidence." ;;
        *)
            # `R` is a node the graph is driving this cycle; `C` is one that is
            # merely configured. A player node present but never R is attached
            # and silent, which is the exact state the clock line cannot rule
            # out.
            running=$(echo "$GRAPH" | awk '$1=="R"' | wc -l)
            say "PipeWire graph, sampled live:"
            echo "$GRAPH" | sed 's/^/  verify-playing:   /'
            if [ "${running:-0}" -gt 0 ]; then
                say "the graph is driving $running node-cycle(s) right now -- audio is moving, not merely configured"
            else
                die "the player's clock line says it played, but NO node is running in the graph right now -- it is attached and silent [GDE-ECHO-547]"
            fi ;;
    esac
    say "NOTE: no ALSA PCM was open, so this is the player's report rather than a device measurement."
    say "      It proves frames left the engine. It does not prove the speaker made a sound."
    ;;
NOUNIT*)
    set -- $OUT
    die "$HOST does not run the player as a systemd service (owner of any open PCM: $2), so there is no journal to measure and no device of the player's own to sample -- cannot tell whether it is playing, which is not the same as knowing it is not"
    ;;
*)
    die "probe returned nothing recognisable -- refusing to call that healthy"
    ;;
esac
