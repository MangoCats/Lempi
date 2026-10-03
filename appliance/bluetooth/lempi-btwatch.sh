#!/bin/sh
# Watch for a Bluetooth controller that has stopped answering, including
# while music is playing, and put it back if it is safe to `[PI3-FOUND-750]`.
#
# The gap this fills. On 2026-09-18 the adapter stopped acknowledging HCI
# commands and nothing noticed for two days `[PI3-FOUND-730]`. Every probe
# this appliance had fired only when something was about to act -- the keeper
# before it pages, the scan after it fails -- so a wedge that happened during
# playback would sit undetected until the next silence, and the first symptom
# would be a listener unable to find a speaker.
set -u

. "${LEMPI_COMMON:-/usr/local/lib/lempi-common.sh}" 2>/dev/null ||
    . "$(dirname "$0")/lempi-common.sh"

STATE_DIR="${LEMPI_RUN_DIR:-/run/lempi}"
MISSES_FILE="$STATE_DIR/btwatch-misses"

# Overridable so the tests can drive the whole policy without a radio, and so
# a different board with a different serdev driver needs no edit here
# `[PI3-AIM-100]`. The defaults are what a Pi Zero 2 W actually has: measured
# 2026-09-20, `/sys/class/bluetooth/hci0` resolves through
# `3f201000.serial/serial0/serial0-0`, bound to `hci_uart_bcm`.
DRIVER="${LEMPI_BT_DRIVER:-/sys/bus/serial/drivers/hci_uart_bcm}"
SERDEV="${LEMPI_BT_SERDEV:-serial0-0}"

# **One timeout is not a wedge.** The failure being watched for is permanent
# -- 7,348 consecutive timeouts, never recovering -- so requiring a few in a
# row costs nothing against the real thing and refuses to act on a blip.
THRESHOLD="${LEMPI_BTWATCH_MISSES:-3}"

# Whether this watcher may reset the adapter at all. On by default because
# the reset is proven and cheap (below); off is for anyone who would rather
# be told than have it handled.
MAY_RESET="${LEMPI_BTWATCH_RESET:-1}"

say() { echo "$*"; }

# **`[PI3-FOUND-730]` The one question the chip itself must answer.**
# `bluetoothctl show` and `btmgmt info` both answered correctly all through
# the real wedge, from bluetoothd's state and the kernel's cache
# respectively; `hcitool con` reads the kernel's connection list and never
# touches the chip either. Read Local Name is the cheapest command the
# controller cannot answer from anybody else's memory.
#
# Cheap enough to ask on a timer while music plays: measured on lempi02w
# 2026-09-20 against a live A2DP stream, 60 probes at 1 Hz, none slower than
# 50 ms, zero underruns, transport still active afterwards.
#
# `unknown` is never rounded to `alive` or `wedged` -- a guard that cannot
# run says so `[GDE-DEP-060]`.
# `LEMPI_HCICONFIG` exists so the "absent" branch below can actually be
# tested `[PI3-AIM-100]`. It cannot be tested by taking the tool off `PATH`:
# on any machine with bluez installed `hciconfig` lives in `/usr/bin` beside
# everything else the script needs, so removing it means removing them too,
# and a shim directory of symlinks does not survive Git Bash, which copies
# the binary and leaves it unable to find its own libraries. One named
# override is deterministic on every host, which the PATH games were not.
HCICONFIG="${LEMPI_HCICONFIG:-hciconfig}"

probe() {
    command -v "$HCICONFIG" >/dev/null 2>&1 || { echo unknown; return; }
    if timeout 15 "$HCICONFIG" hci0 name >/dev/null 2>&1; then echo alive; else echo wedged; fi
}

# **Is audio actually reaching a speaker right now?** Not "is something
# connected" -- `Connected: yes` survives perfectly well while the A2DP
# media profile underneath is idle or gone `[PI3-FOUND-070]`. The only
# honest answer is BlueZ's own MediaTransport1, and any transport in
# `active` counts, so this needs no device address.
#
# Timed out, because every call here goes to a daemon that may itself be
# stuck behind the wedged controller this is trying to diagnose.
audio_flowing() {
    _paths=$(timeout 5 busctl --list tree org.bluez 2>/dev/null |
             grep -E '/fd[0-9]+$') || return 1
    for _p in $_paths; do
        _s=$(timeout 5 busctl get-property org.bluez "$_p" \
             org.bluez.MediaTransport1 State 2>/dev/null |
             sed 's/^s "//; s/"$//')
        [ "$_s" = "active" ] && return 0
    done
    return 1
}

# Corroboration, free and passive: the kernel says plainly what the wedge
# looks like from underneath. Only used to make the log line worth reading --
# the verdict is the probe's, never this.
recent_hci_errors() {
    timeout 5 dmesg 2>/dev/null |
        grep -cE 'hci0: (command 0x[0-9a-f]+ tx timeout|Opcode 0x[0-9a-f]+ failed)' ||
        echo 0
}

# **Reload the controller's firmware without a reboot `[PI3-FOUND-750]`.**
#
# Unbinding and rebinding the serdev driver re-runs the whole bring-up: the
# chip is re-probed and its firmware patch downloaded again. Proven on
# lempi02w 2026-09-20 -- `hci0` disappeared, came back, and the kernel logged
# `BCM43430A1 'brcm/BCM43430A1.raspberrypi,model-zero-2-w.hcd' Patch`, which
# is the firmware actually being written to the chip rather than a driver
# merely reattaching. Read Local Name answered afterwards, and the trusted
# speaker reconnected itself five seconds later with no underruns
# `[PI3-FOUND-130]`.
#
# **What this has NOT been shown to do is clear a real wedge.** It was proven
# against a healthy adapter, because the wedge took 25 hours to produce and
# was gone before the mechanism existed. It is the right thing to try first
# -- it costs one reconnect where the alternative costs a reboot -- and if it
# does not work the next line says so plainly rather than reporting success.
reset_controller() {
    [ -w "$DRIVER/unbind" ] || { say "cannot reset: $DRIVER/unbind is not writable"; return 1; }
    say "resetting the Bluetooth controller: unbind/bind $SERDEV on $(basename "$DRIVER")"
    echo "$SERDEV" > "$DRIVER/unbind" 2>/dev/null || { say "unbind failed"; return 1; }
    sleep 3
    echo "$SERDEV" > "$DRIVER/bind" 2>/dev/null || { say "bind failed -- the adapter is now down and needs a reboot"; return 1; }
    # The firmware download takes a few seconds; probing before it finishes
    # would report a failure that had not happened yet.
    sleep 8
    return 0
}

count() { cat "$MISSES_FILE" 2>/dev/null | tr -dc '0-9' | head -c 4; }

STATE=$(probe)

case "$STATE" in
    unknown)
        say "hciconfig is not installed, so the controller cannot be checked at all [PI3-FOUND-730]"
        exit 0
        ;;
    alive)
        # Only worth a line if it had been failing; a healthy appliance
        # should be silent.
        n=$(count)
        if [ -n "${n:-}" ] && [ "${n:-0}" -gt 0 ] 2>/dev/null; then
            say "the Bluetooth controller is answering again after $n failed checks"
        fi
        rm -f "$MISSES_FILE" 2>/dev/null
        exit 0
        ;;
esac

# Wedged.
n=$(count); n=$(( ${n:-0} + 1 ))
mkdir -p "$STATE_DIR" 2>/dev/null
printf '%s\n' "$n" > "$MISSES_FILE" 2>/dev/null

if [ "$n" -lt "$THRESHOLD" ]; then
    say "the Bluetooth controller did not answer (check $n of $THRESHOLD before acting)"
    exit 0
fi

say "the Bluetooth controller has not answered $n checks running; $(recent_hci_errors) HCI errors in the kernel log [PI3-FOUND-730]"

# **`[PI3-AIM-060]`, applied to the repair rather than the chase.** If audio
# is still reaching a speaker, the wedge is not costing the listener anything
# yet, and every repair available here begins by destroying that stream. So
# it waits. The control path being dead means the appliance cannot start
# anything new -- it cannot scan, pair, or reconnect -- but what is already
# playing keeps playing, and interrupting it to fix a problem the listener
# cannot hear is the wrong trade.
if audio_flowing; then
    say "audio is still flowing, so nothing is being reset; this will be repaired at the next silence"
    exit 0
fi

if [ "$MAY_RESET" != "1" ]; then
    say "automatic reset is switched off (LEMPI_BTWATCH_RESET); the adapter needs a reboot"
    exit 0
fi

if reset_controller && [ "$(probe)" = alive ]; then
    say "the Bluetooth controller is answering again after a reset [PI3-FOUND-750]"
    rm -f "$MISSES_FILE" 2>/dev/null
    exit 0
fi

# **Never reboot on its own.** A watcher that reboots is a watcher that can
# reboot in a loop, and an appliance stuck in a boot loop is a far worse
# failure than one whose Bluetooth is down -- the second still answers ssh
# and can be repaired. So this says what is needed and stops.
say "the reset did not bring the controller back; this needs a reboot [PI3-FOUND-750]"
exit 0
