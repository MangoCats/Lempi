# lempi-wifiwatch: a frozen Wi-Fi chip noticed, and rebooted at the next
# pause `[PI3-FOUND-830]`.
#
# The freezes themselves are not reproducible here. What is checked is the
# policy: that it takes both a kernel signature and a silent router, that a
# song is never interrupted, and that a fresh boot cannot loop.
group wifiwatch || return 0
printf '\nwifiwatch\n'

wwatch() {
    PROC_ROUTE="$VT_STATE/route" PROC_UPTIME="$VT_STATE/uptime" SYS_WLAN_RX="$VT_STATE/rx_bytes" \
    LEMPI_WIFIWATCH_MISSES="${MISSES:-3}" sh "$BT/lempi-wifiwatch.sh" 2>&1
}
# wlan0's receive counter now, and as the previous check recorded it.
received() {
    printf '%s\n' "$1" > "$LEMPI_RUN_DIR/wifiwatch-rx"
    printf '%s\n' "$2" > "$VT_STATE/rx_bytes"
}

# The node on its home network: wlan0 connected, gateway 192.0.2.1 (as
# /proc/net/route spells it, little-endian hex), up an hour.
online() {
    printf 'Iface\tDestination\tGateway\tFlags\nwlan0\t00000000\t010200C0\t0003\n' > "$VT_STATE/route"
    printf 'wlan0:connected\n' > "$VT_STATE/nm_dev_state"
    printf '3600.00 1000.00\n' > "$VT_STATE/uptime"
}
# The two signatures lempi02w left, 2026-10-15.
inhibit() {
    printf '%s\n' "mmc1: Controller never released inhibit bit(s)." \
        "brcmfmac: brcmf_sdio_read_control: read 2048 control bytes failed: -5" > "$VT_STATE/dmesg"
}
rxctl() {
    printf '%s\n' "ieee80211 phy0: brcmf_cfg80211_dump_station: BRCMF_C_GET_ASSOCLIST failed, err=-110" \
        "brcmfmac: brcmf_sdio_bus_rxctl: resumed on timeout" > "$VT_STATE/dmesg"
}
paused()  { : > "$VT_STATE/routed"; }

# --- 1. a healthy link is silent and touches nothing ---------------------
setup
online
OUT=$(wwatch)
assert_eq "$OUT" "" "a router that answers produces no output"
assert_not_called "systemctl reboot" "and nothing is rebooted"
teardown

# --- 2. not connected is the failover's business, not this ---------------
setup
online
printf 'wlan0:disconnected\n' > "$VT_STATE/nm_dev_state"
touch "$VT_STATE/ping_fails"
inhibit
MISSES=1 wwatch >/dev/null
assert_not_called "ping" "does not even ask when wlan0 is not connected"
assert_not_called "systemctl reboot" "and reboots nothing"
teardown

# --- 3. a silent router alone is not a freeze ----------------------------
# A router being restarted looks exactly like this.
setup
online
touch "$VT_STATE/ping_fails"
OUT=$(MISSES=1 wwatch)
assert_in "$OUT" "kernel shows no Wi-Fi freeze" "says the kernel shows nothing"
assert_not_called "systemctl reboot" "and does not reboot"
teardown

# --- 4. both signatures, paused: reboot ----------------------------------
for sig in inhibit rxctl; do
    setup
    online
    touch "$VT_STATE/ping_fails"
    $sig
    paused
    OUT=$(MISSES=1 wwatch)
    assert_in "$OUT" "has frozen" "names the freeze ($sig)"
    assert_called "systemctl reboot" "reboots at a pause ($sig)"
    teardown
done

# --- 5. counted before acting --------------------------------------------
setup
online
touch "$VT_STATE/ping_fails"
inhibit
paused
OUT=$(wwatch)
assert_in "$OUT" "check 1 of 3" "the first miss is only counted"
assert_not_called "systemctl reboot" "and nothing is rebooted yet"
teardown

# --- 6. never in the middle of a song ------------------------------------
# The listener's choice, 2026-10-15: reboot at the next pause.
setup
online
touch "$VT_STATE/ping_fails" "$VT_STATE/playing"
inhibit
printf 'soundcore Boom 2\n' > "$VT_STATE/routed"
OUT=$(MISSES=1 wwatch)
assert_in "$OUT" "rebooting at the next pause" "says it is waiting for a pause"
assert_not_called "systemctl reboot" "and does not reboot while playing"
OUT=$(MISSES=1 wwatch)
assert_not_in "$OUT" "next pause" "says so once, not every minute"
rm -f "$VT_STATE/playing"
MISSES=1 wwatch >/dev/null
assert_called "systemctl reboot" "and reboots once the music pauses"
teardown

# --- 7. a player that cannot say is waited on ----------------------------
# An older build answers /audio/sink without "playing"; that is not a pause.
setup
online
touch "$VT_STATE/ping_fails"
inhibit
mkdir -p "$VT_STATE/bin"
cat > "$VT_STATE/bin/curl" <<'EOF'
#!/bin/sh
printf '%s\n' "curl $*" >> "$VT_STATE/calls"
echo '{"sink":"x","dummy":false,"known":true}'
EOF
chmod +x "$VT_STATE/bin/curl"
OUT=$(PATH="$VT_STATE/bin:$PATH" MISSES=1 wwatch)
assert_not_called "systemctl reboot" "an answer without 'playing' is never taken for a pause"
teardown

# --- 8. not straight after a boot ----------------------------------------
setup
online
printf '300.00 100.00\n' > "$VT_STATE/uptime"
touch "$VT_STATE/ping_fails"
inhibit
paused
OUT=$(MISSES=1 wwatch)
assert_in "$OUT" "so a freeze at boot cannot loop" "says why it waits"
assert_not_called "systemctl reboot" "and does not reboot within the first fifteen minutes"
teardown

# --- 9. a router that answers again clears the count ---------------------
setup
online
printf '2\n' > "$LEMPI_RUN_DIR/wifiwatch-misses"
OUT=$(wwatch)
assert_in "$OUT" "router at 192.0.2.1 is answering again after 2 failed checks" \
    "says it recovered, naming the gateway read from the route table"
assert_eq "$([ -e "$LEMPI_RUN_DIR/wifiwatch-misses" ] && echo present || echo gone)" "gone" "and the count is cleared"
teardown

# --- 10. a link still receiving is busy, not frozen [PI3-FOUND-835] ------
# lempi02w, 2026-10-26: rebooted mid-export, the copy arriving at 606 kB/s.
setup
online
touch "$VT_STATE/ping_fails"
inhibit
paused
printf '2\n' > "$LEMPI_RUN_DIR/wifiwatch-misses"
received 1000000 37000000
OUT=$(MISSES=1 wwatch)
assert_in "$OUT" "busy, not frozen" "says the link is busy"
assert_not_called "systemctl reboot" "and does not reboot a link that is receiving"
assert_eq "$([ -e "$LEMPI_RUN_DIR/wifiwatch-misses" ] && echo present || echo gone)" "gone" "its misses are cleared"
printf '%s\n' 73000000 > "$VT_STATE/rx_bytes"
OUT=$(MISSES=1 wwatch)
assert_not_in "$OUT" "busy" "said once, not every minute"
assert_not_called "systemctl reboot" "and still not rebooted while it keeps receiving"
teardown

# --- 11. a link that has stopped receiving still is ----------------------
setup
online
touch "$VT_STATE/ping_fails"
inhibit
paused
received 37000000 37004000
OUT=$(MISSES=1 wwatch)
assert_in "$OUT" "has frozen" "a trickle under RX_ALIVE exempts nothing"
assert_called "systemctl reboot" "and it reboots at a pause"
teardown

# --- 12. every check records the counter, so it covers one interval ------
# An exemption measured against an hour-old reading would let the traffic
# before a freeze excuse the freeze.
setup
online
received 5 900000000
wwatch >/dev/null
assert_eq "$(cat "$LEMPI_RUN_DIR/wifiwatch-rx")" "900000000" "a healthy check records what it read"
touch "$VT_STATE/ping_fails"
inhibit
paused
OUT=$(MISSES=1 wwatch)
assert_in "$OUT" "has frozen" "so a link silent since the last check is not excused by the hour before"
teardown

# --- 13. a counter that went back is unknown, and excuses nothing --------
setup
online
touch "$VT_STATE/ping_fails"
inhibit
paused
received 900000000 4000000
OUT=$(MISSES=1 wwatch)
assert_called "systemctl reboot" "a reset counter is not read as traffic"
teardown
