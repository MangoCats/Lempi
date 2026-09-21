#!/bin/bash
# Read the speaker's rocker and act on it [PI3-ROCKER-010].
#
# Also the characterisation tool: EVERY key seen is logged with its code and
# name, whether or not it is mapped. The mapping from the Middleton's five
# gestures to key codes has never been observed, so the first run of this is
# the measurement, and an unmapped press is a finding rather than a fault.
#
# Safety: whenever this lowers Wi-Fi it FIRST arms a detached restore timer.
# The radio comes back even if this script is killed, wedges, or the machine
# does something unexpected -- a headless box that can silence its own way in
# must never depend on a running process to undo that.
set -uo pipefail
PORT="${LEMPI_PORT:-5720}"
SPEAKER="${SPEAKER:-20:64:DE:CF:F3:AD}"
IFACE="${IFACE:-wlan0}"
DEADMAN="${DEADMAN:-900}"          # wifi returns after this no matter what
WAIT_FOR="${WAIT_FOR:-180}"        # how long to wait for the AVRCP device
MAP_ONLY="${MAP_ONLY:-0}"          # 1 = log keys, take no action
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

# Log lines go to stderr, never stdout. await_dev returns the device name by
# echoing it, so anything else written to stdout is captured into that name --
# which produced a first run reading '/dev/input/22:25:46 waiting...event2'.
say() { logger -t lempi-rocker "$*"; echo "$(date +%T) $*" >&2; }
post() { curl -s -o /dev/null -m 4 -X POST "http://localhost:$PORT/command/$1"; }

# BlueZ creates the AVRCP uinput keyboard when the speaker connects and takes
# it away when it goes, so the device's absence is a normal moment in its life
# rather than an error. Read the whole record at once (blank-line separated) so
# the Name and its Handlers line cannot be matched across a device boundary.
find_dev() {
    awk -v RS='' '/AVRCP/ && match($0, /event[0-9]+/) \
                  { print substr($0, RSTART, RLENGTH); exit }' \
        /proc/bus/input/devices
}

# Wait for it, rather than giving up the instant it is missing.
#
# This script is started around a link that comes and goes, and exiting on the
# first look cost three characterisation runs in one evening: each time the
# speaker connected seconds later, to nothing listening. A deadline keeps it
# from waiting forever on a speaker that is switched off.
await_dev() {
    local deadline=$(( SECONDS + WAIT_FOR ))
    local dev announced=0
    while :; do
        dev="$(find_dev)"
        [ -n "$dev" ] && { echo "$dev"; return 0; }
        [ "$announced" = 0 ] && { say "waiting up to ${WAIT_FOR}s for the speaker"; announced=1; }
        [ "$SECONDS" -ge "$deadline" ] && return 1
        sleep 2
    done
}

arm_deadman() {
    sudo systemd-run --on-active="$DEADMAN" --timer-property=AccuracySec=5s \
        --unit=lempi-rocker-wifi --quiet \
        /bin/sh -c "nmcli radio wifi on 2>/dev/null || ip link set $IFACE up" \
        2>/dev/null
}

wifi_down() {
    arm_deadman || { say "REFUSING to lower wifi: no restore timer"; return 1; }
    say "wifi down"
    sudo nmcli radio wifi off 2>/dev/null || sudo ip link set "$IFACE" down
    sleep 2
    # Bluetooth is established only AFTER the radio is quiet, so the link is
    # not negotiated under the interference that [PI3-FOUND-010] measured.
    bluetoothctl info "$SPEAKER" 2>/dev/null | grep -qi 'Connected: yes' \
        || bluetoothctl connect "$SPEAKER" >/dev/null 2>&1
    sleep 5
    if bluetoothctl info "$SPEAKER" 2>/dev/null | grep -qi 'Connected: yes'; then
        post reopen-output
        say "bluetooth up with wifi down"
    else
        # Silent AND unreachable is the worst state available, and it is
        # reached by someone switching the speaker off. Do not sit in it.
        say "bluetooth did not come up; restoring wifi"
        wifi_up
    fi
}

wifi_up() {
    sudo nmcli radio wifi on 2>/dev/null || sudo ip link set "$IFACE" up
    sudo systemctl stop lempi-rocker-wifi.timer 2>/dev/null
    sudo systemctl reset-failed lempi-rocker-wifi.timer 2>/dev/null
    say "wifi up"
}

act_on() {
    local name="$1"
    case "$name" in
        KEY_PLAYPAUSE|KEY_PLAYCD|KEY_PAUSECD|KEY_PLAY|KEY_PAUSE)
            if [ "$PLAYING" = 1 ]; then
                post pause; PLAYING=0; wifi_up
            else
                post play;  PLAYING=1; wifi_down
            fi ;;
        KEY_NEXTSONG|KEY_FORWARD)  post skip ;;
        KEY_PREVIOUSSONG|KEY_BACK) say "left: reserved, ignored" ;;
        *) say "unmapped: $name" ;;
    esac
}

PLAYING=1        # the server is started playing for this test
[ "$MAP_ONLY" = 1 ] && say "MAP_ONLY: logging keys, acting on none"

# Outer loop: evtest ends when the device vanishes, which happens on every
# disconnect. That is a reason to wait again, not a reason to stop.
while :; do
    DEV="$(await_dev)" || { say "no AVRCP device after ${WAIT_FOR}s; giving up"; exit 1; }
    say "reading /dev/input/$DEV"
    sudo stdbuf -oL evtest "/dev/input/$DEV" 2>/dev/null | while read -r line; do
        case "$line" in *"(EV_KEY)"*", value 1"*) ;; *) continue ;; esac
        CODE=$(echo "$line" | sed -n 's/.*code \([0-9]*\) (\([A-Z_0-9]*\)).*/\1 \2/p')
        NAME=${CODE#* }
        # Logged before anything is decided, so a press is recorded even when
        # the action it triggers takes the machine off the network.
        say "KEY $CODE"
        [ "$MAP_ONLY" = 1 ] || act_on "$NAME"
    done
    say "input device went away"
done
