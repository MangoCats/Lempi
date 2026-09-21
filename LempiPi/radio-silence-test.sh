#!/bin/bash
# Hold a Bluetooth playback test with Wi-Fi switched OFF, then bring it back.
#
# The Pi Zero 2 W has ONE radio shared between Wi-Fi and Bluetooth, so every
# measurement taken over ssh is taken while the thing under test is competing
# with the measurement itself. Idling the ssh session narrows that; taking the
# interface down removes it.
#
# The obvious risk is locking yourself out of a headless machine. Restoring
# Wi-Fi is therefore NOT this script's job to remember: it is armed as a
# separate detached timer before the radio goes down, so the network returns
# even if this script is killed, panics, or the shell running it dies.
#
# Idempotent: re-runnable, leaves no state behind but its log.
set -uo pipefail

# Must run as the LOGIN user, never under sudo. PipeWire is per-user: under
# sudo, XDG_RUNTIME_DIR resolves to root's and every flow check reads zero
# while the link samples still look perfect -- a false pass of precisely the
# kind this script exists to prevent. It sudoes internally where it needs to.
if [ "$(id -u)" = "0" ]; then
    echo "run as the login user, not with sudo (PipeWire is per-user)" >&2
    exit 1
fi

# KEEP_WIFI=1 runs the identical measurement with the radio left UP -- the
# control arm. Comparing a dark run against a remembered impression of a
# previous one compares two different procedures; running the same sampling,
# the same flow checks and the same log format either way is what makes the
# two numbers subtractable.
KEEP_WIFI="${KEEP_WIFI:-0}"
SPEAKER="${SPEAKER:-20:64:DE:CF:F3:AD}"
SECONDS_DOWN="${SECONDS_DOWN:-120}"
IFACE="${IFACE:-wlan0}"
LOG="/var/log/lempi-radio-test.$(date +%Y%m%d-%H%M%S).log"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

say() { echo "$*" | sudo tee -a "$LOG" >/dev/null; }

# The deadman. Armed FIRST and independent of this process: whatever happens
# below, the interface comes back up at the deadline. Not needed for the
# control arm, which never lowers the interface and so can never strand the
# machine.
[ "$KEEP_WIFI" = "1" ] || sudo systemd-run --on-active="$((SECONDS_DOWN + 30))" --timer-property=AccuracySec=1s \
     --unit=lempi-wifi-restore --quiet \
     /bin/sh -c "rfkill unblock wifi; ip link set $IFACE up; systemctl restart wpa_supplicant 2>/dev/null; nmcli radio wifi on 2>/dev/null" \
  || { echo "could not arm the restore timer; refusing to take the radio down"; exit 1; }
[ "$KEEP_WIFI" = "1" ] && LOG="${LOG%.log}.wifi-up.log"

sudo touch "$LOG"; sudo chmod 644 "$LOG"
say "=== radio silence test $(date -Is) ==="
say "speaker=$SPEAKER down=${SECONDS_DOWN}s iface=$IFACE"

bluetoothctl info "$SPEAKER" | grep -qi 'Connected: yes' || bluetoothctl connect "$SPEAKER" >/dev/null 2>&1
sleep 6
say "before: $(wpctl status | sed -n '/Sinks:/,/Sink endpoints/p' | grep '\*' | tr -s ' ')"
say "stream: $(wpctl status | grep -A2 'Streams:' | grep -c playback) links"

systemctl is-active --quiet lempi || sudo systemctl start lempi
sleep 3

# The control route is /command/:name -- there is no /api prefix, and state
# arrives over a websocket rather than by GET. Worth being exact about: a test
# that silently 404s its own play command measures a PAUSED stream holding an
# idle link, which looks like a pass and proves nothing.
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST localhost:5720/command/play || echo 000)
say "play command: HTTP $code"
if [ "$code" != "204" ]; then
    say "ABORT: play was not accepted; the test would measure silence"
    sudo systemctl stop lempi-wifi-restore.timer 2>/dev/null
    exit 1
fi

# Prove audio is actually moving before taking the radio down. pw-top reports
# per-node quantum activity, so a node that is merely connected reads zero
# where one that is playing does not.
sleep 4
flow_before=$(pw-top -b -n 2 2>/dev/null | grep -ci 'alsa_playback.lempi' || echo 0)
say "flow check before: $flow_before lempi rows in pw-top"
if [ "$flow_before" -lt 1 ]; then
    say "ABORT: no audio flowing; the test would measure silence"
    sudo systemctl stop lempi-wifi-restore.timer 2>/dev/null
    exit 1
fi

# Everything from here runs with no network. Sample locally into the log.
if [ "$KEEP_WIFI" = "1" ]; then
    say "--- wifi UP (control arm) $(date -Is) ---"
    say "link: $(iw dev "$IFACE" link 2>/dev/null | grep -i signal | tr -s ' ')"
else
    say "--- wifi down $(date -Is) ---"
    sudo nmcli radio wifi off 2>/dev/null || sudo ip link set "$IFACE" down
fi

ok=0; n=$((SECONDS_DOWN / 3))
for _ in $(seq 1 "$n"); do
    bluetoothctl info "$SPEAKER" 2>/dev/null | grep -qi 'Connected: yes' && ok=$((ok + 1))
    sleep 3
done
arm=$([ "$KEEP_WIFI" = "1" ] && echo "wifi up" || echo "dark")
say "connected while $arm: $ok/$n"
say "flow while $arm:      $(pw-top -b -n 2 2>/dev/null | grep -ci 'alsa_playback.lempi') rows"
say "links while $arm:     $(wpctl status | grep -c 'MIDDLETON:playback')"
say "errors while $arm:"
journalctl -u lempi --since "-${SECONDS_DOWN}s" --no-pager 2>/dev/null \
  | grep -iE 'error|recover' | tail -10 | sudo tee -a "$LOG" >/dev/null

if [ "$KEEP_WIFI" != "1" ]; then
    say "--- wifi up $(date -Is) ---"
    sudo nmcli radio wifi on 2>/dev/null || sudo ip link set "$IFACE" up
    sudo systemctl stop lempi-wifi-restore.timer 2>/dev/null
    sudo systemctl reset-failed lempi-wifi-restore.timer 2>/dev/null
    for _ in $(seq 1 30); do ping -c1 -W1 1.1.1.1 >/dev/null 2>&1 && break; sleep 2; done
    say "=== done, network back $(date -Is) ==="
else
    say "=== done, radio never lowered $(date -Is) ==="
fi
echo "$LOG"
