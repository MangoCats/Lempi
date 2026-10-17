#!/bin/sh
# One line a minute: what this node's power, radios and audio were doing
# `[APP-HLT-010]`.
#
# **Why.** lp3-wifi, 2026-10-16..17: 1,104 undervoltage events in one boot,
# Bluetooth glitches the listener could hear, and no way to say afterwards
# whether the two went together `[LP3-PWR-010]`. The journal holds the
# events, but not the state between them -- whether music was playing, on
# what, at what clock -- and an ear is not always there to listen. So this
# writes both, once a minute, to a file on the writable partition that
# outlives a reboot, for a listener's "it glitched at 14:20" to be set against
# later.
#
# **Events are counted once.** A journal cursor is kept beside the log, so each
# line counts what happened since the line before -- across a reboot too,
# since the journal persists -- never a fixed window that two runs could both
# count or both miss.
#
# **Cheap, and bounded.** A handful of short commands once a minute, one small
# append, and at MAXBYTES the log moves to `.1` -- about two weeks in all.
#
# Each line is `key=value` pairs, spaces in values written as `_`:
#   thr        vcgencmd get_throttled: bit 0 undervoltage now, bit 2 throttled
#              now, bits 16/18 the same since boot
#   arm        CPU clock, MHz -- 600 on a Pi 3 B means it is being held down
#   play/sink  what the player says it is playing, and where
#   acl        Bluetooth baseband links up
#   wifi/sig/gw  wlan0's state, signal in dBm, and whether the router answered
#   ev_*       events since the previous line: uv undervoltage, bt a Bluetooth
#              transport failing, ring/underrun/recov the player's output,
#              sdio the Wi-Fi chip's bus `[PI3-FOUND-830]`, xrun PipeWire's
set -u

OUT="${LEMPI_HEALTH_OUT:-/var/lempi/health.log}"
CURSOR="${LEMPI_HEALTH_CURSOR:-/var/lempi/health.cursor}"
MAXBYTES="${LEMPI_HEALTH_MAXBYTES:-2000000}"
PORT="${LEMPI_PORT:-5720}"

one() { tr ' ' '_' | head -1; }

# Power.
THR=$(vcgencmd get_throttled 2>/dev/null | sed -n 's/^throttled=//p')
# The kernel's own figure, in kHz. `vcgencmd measure_clock arm` answered 0
# one time in five on lp3-wifi, 2026-10-17, which would read as a stopped CPU.
ARM=$(cat "${LEMPI_CPUFREQ:-/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq}" 2>/dev/null)
case "${ARM:-}" in ''|*[!0-9]*) ARM='?' ;; *) ARM=$(( ARM / 1000 )) ;; esac
TEMP=$(vcgencmd measure_temp 2>/dev/null | sed -n "s/^temp=\([0-9.]*\).*/\1/p")
VOLT=$(vcgencmd measure_volts core 2>/dev/null | sed -n 's/^volt=\([0-9.]*\)V.*/\1/p')
LOAD=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)
UP=$(cut -d. -f1 /proc/uptime 2>/dev/null)

# Audio, as the player sees it. Nothing answering is `down`, not `0`.
S=$(curl -s -m 5 "http://localhost:$PORT/audio/sink" 2>/dev/null)
case "$S" in
    *'"playing":true'*)  PLAY=1 ;;
    *'"playing":false'*) PLAY=0 ;;
    '')                  PLAY=down ;;
    *)                   PLAY='?' ;;
esac
SINK=$(printf '%s' "$S" | sed -n 's/.*"sink":"\([^"]*\)".*/\1/p' | one)

# Radios.
ACL=$(hcitool con 2>/dev/null | grep -c 'ACL')
WIFI=$(nmcli -t -f DEVICE,STATE dev 2>/dev/null | sed -n 's/^wlan0://p' | one)
SIG=$(iw dev wlan0 link 2>/dev/null | sed -n 's/^[[:space:]]*signal: \(-*[0-9]*\).*/\1/p')
GWIP=$(ip -4 route show default 2>/dev/null | awk '{ print $3; exit }')
GW='-'
if [ -n "$GWIP" ]; then
    if ping -c 1 -W 2 "$GWIP" >/dev/null 2>&1; then GW=1; else GW=0; fi
fi

# Events since the previous line. A cursor that the journal no longer holds --
# rotated away -- falls back to the last minute, and the line says so.
EV=$(mktemp 2>/dev/null) || EV="/tmp/lempi-health.$$"
FROM=cursor
C=$(cat "$CURSOR" 2>/dev/null)
if [ -z "$C" ] || ! journalctl --after-cursor="$C" --show-cursor -q -o cat --no-pager > "$EV" 2>/dev/null; then
    FROM=minute
    journalctl --since "-60s" --show-cursor -q -o cat --no-pager > "$EV" 2>/dev/null
fi
NEWC=$(sed -n 's/^-- cursor: //p' "$EV" | tail -1)
[ -n "$NEWC" ] && printf '%s\n' "$NEWC" > "$CURSOR" 2>/dev/null
n() { grep -cE "$1" "$EV"; }
UV=$(n 'Undervoltage detected')
BTE=$(n 'bluez_output.*-> error|spa\.bluez5.*error [0-9]')
RING=$(n 'missed ring lock')
UNDER=$(n 'Some\(Underrun\)|underrun')
RECOV=$(n 'output recovered')
SDIO=$(n 'mmc1: Controller never released inhibit bit|brcmf_sdio_bus_rxctl: resumed on timeout|brcmf.*(failed|err)[^0-9-]*-(5|110)\b')
XRUN=$(n 'xrun')
rm -f "$EV"

if [ -f "$OUT" ] && [ "$(wc -c < "$OUT")" -gt "$MAXBYTES" ]; then
    mv -f "$OUT" "$OUT.1"
fi
[ -f "$OUT" ] || printf '# lempi-health: one line a minute [APP-HLT-010]\n' > "$OUT"
printf '%s up=%s thr=%s arm=%s temp=%s volt=%s load=%s play=%s sink=%s acl=%s wifi=%s sig=%s gw=%s from=%s ev_uv=%s ev_bt=%s ev_ring=%s ev_underrun=%s ev_recov=%s ev_sdio=%s ev_xrun=%s\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S')" "${UP:-?}" "${THR:-?}" "$ARM" "${TEMP:-?}" "${VOLT:-?}" \
    "${LOAD:-?}" "$PLAY" "${SINK:--}" "$ACL" "${WIFI:--}" "${SIG:--}" "$GW" "$FROM" \
    "$UV" "$BTE" "$RING" "$UNDER" "$RECOV" "$SDIO" "$XRUN" >> "$OUT"
