# lempi-health: one line a minute of power, radios and audio, with each
# event counted once `[APP-HLT-010]`.
group health || return 0
printf '\nhealth\n'

health() {
    printf '1200000\n' > "$VT_STATE/cpufreq"
    LEMPI_HEALTH_OUT="$VT_STATE/health.log" LEMPI_HEALTH_CURSOR="$VT_STATE/health.cursor" \
    LEMPI_CPUFREQ="$VT_STATE/cpufreq" LEMPI_HEALTH_MAXBYTES="${MAXBYTES:-2000000}" \
        sh "$BT/lempi-health.sh" 2>&1
}
last() { tail -1 "$VT_STATE/health.log"; }

# --- 1. a playing node with an undervoltage in the journal ---------------
# lp3-wifi's state on 2026-10-17: the Boom 2 playing, voltage dipping.
setup
printf '0x50005\n' > "$VT_STATE/throttled"
printf 'soundcore Boom 2\n' > "$VT_STATE/routed"
touch "$VT_STATE/playing"
printf 'Connections:\n\t> ACL AA:BB:CC:DD:EE:10 handle 11 state 1 lm CENTRAL\n' > "$VT_STATE/hci_con"
printf 'wlan0:connected\n' > "$VT_STATE/nm_dev_state"
printf '\tsignal: -62 dBm\n' > "$VT_STATE/iw_link"
printf 'default via 192.0.2.1 dev wlan0 proto dhcp\n' > "$VT_STATE/ip_route"
printf '%s\n' "hwmon hwmon1: Undervoltage detected!" "hwmon hwmon1: Voltage normalised" \
    "hwmon hwmon1: Undervoltage detected!" \
    "pw.node: (bluez_output.AA_BB_CC_DD_EE_10.1-86) running -> error (Received error event)" \
    "lempi[1]: output recovered on Default Audio Device" > "$VT_STATE/journal"
OUT=$(health)
assert_eq "$OUT" "" "writes to its log, not to the journal"
L=$(last)
for f in "thr=0x50005" "arm=1200" "play=1" "sink=soundcore_Boom_2" "acl=1" "wifi=connected" \
         "sig=-62" "gw=1" "ev_uv=2" "ev_bt=1" "ev_recov=1" "ev_sdio=0"; do
    assert_in "$L" "$f" "records $f"
done
assert_in "$L" "from=minute" "the first run, with no cursor, says it read the last minute"
T=$(printf '%s' "$L" | sed -n 's/.* t=\([0-9]*\) .*/\1/p')
NOW=$(date +%s)
if [ -n "$T" ] && [ $(( NOW - T )) -ge 0 ] && [ $(( NOW - T )) -le 60 ]; then
    ok "carries t=, the same moment in Unix seconds [APP-PWR-010]"
else
    bad "carries t=, the same moment in Unix seconds" "got '${T:-none}' at $NOW"
fi
teardown

# --- 2. the cursor: each event counted once ------------------------------
setup
printf 'hwmon hwmon1: Undervoltage detected!\n' > "$VT_STATE/journal"
health >/dev/null
: > "$VT_STATE/journal"
health >/dev/null
L=$(last)
assert_in "$L" "from=cursor" "the second run reads from where the first stopped"
assert_in "$L" "ev_uv=0" "and does not count the first run's event again"
teardown

# --- 3. a cursor the journal no longer holds -----------------------------
setup
printf 's=gone\n' > "$VT_STATE/health.cursor"
printf 's=gone' > "$VT_STATE/bad_cursor"
printf 'hwmon hwmon1: Undervoltage detected!\n' > "$VT_STATE/journal"
health >/dev/null
assert_in "$(last)" "from=minute" "falls back to the last minute, and says so"
assert_in "$(last)" "ev_uv=1" "and still counts what it read"
teardown

# --- 4. a player that does not answer is down, not stopped ---------------
setup
mkdir -p "$VT_STATE/bin"
printf '#!/bin/sh\nexit 7\n' > "$VT_STATE/bin/curl"
chmod +x "$VT_STATE/bin/curl"
PATH="$VT_STATE/bin:$PATH" health >/dev/null
assert_in "$(last)" "play=down" "no answer from the player reads as down"
teardown

# --- 5. the log is bounded -----------------------------------------------
setup
health >/dev/null
health >/dev/null
MAXBYTES=10 health >/dev/null
assert_eq "$([ -f "$VT_STATE/health.log.1" ] && echo kept || echo none)" "kept" \
    "past its size the log moves to .1"
assert_eq "$(grep -c '^20' "$VT_STATE/health.log")" "1" "and a fresh one starts"
teardown

# --- 6. the gateway: no route is not a failed ping -----------------------
setup
: > "$VT_STATE/ip_route"
health >/dev/null
assert_in "$(last)" "gw=-" "with no default route there is nothing to ask"
assert_not_called "ping" "and nothing is pinged"
teardown
