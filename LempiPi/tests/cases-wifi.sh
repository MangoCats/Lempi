# lempi-btctl's Wi-Fi verbs: forget and autoconnect. The web process runs
# these as root, and before `[SecurityReview C5]` they acted on any connection
# name a browser sent -- so a cross-site request could delete or disable a
# wired, VPN or other system profile the UI never listed. The guard is
# is_wifi_conn: only an 802-11-wireless connection may be forgotten or toggled.
group wifi || return 0
printf '\nwifi\n'

btctl() { bash "$BT/lempi-btctl" "$@" 2>&1; }

# A wifi connection that is not the active one: forgotten.
setup
printf 'Home=802-11-wireless\n' > "$VT_STATE/nm_types"
printf 'CurrentWifi\n' > "$VT_STATE/nm_active"
OUT=$(btctl wifi-forget Home)
assert_in "$OUT" '"ok":true' "a Wi-Fi connection can be forgotten"
assert_called "nmcli connection delete Home" "and the delete reaches nmcli"
teardown

# A wired connection: refused, and nothing is deleted.
setup
printf 'Wired=802-3-ethernet\n' > "$VT_STATE/nm_types"
printf 'CurrentWifi\n' > "$VT_STATE/nm_active"
OUT=$(btctl wifi-forget Wired)
assert_in "$OUT" "not a Wi-Fi connection" "a non-Wi-Fi connection cannot be forgotten"
assert_not_called "nmcli connection delete Wired" "and the delete never reaches nmcli"
teardown

# A name no connection has: refused before any delete.
setup
: > "$VT_STATE/nm_types"
printf 'CurrentWifi\n' > "$VT_STATE/nm_active"
OUT=$(btctl wifi-forget Ghost)
assert_in "$OUT" "not a Wi-Fi connection" "an unknown connection cannot be forgotten"
assert_not_called "nmcli connection delete Ghost" "and nothing is deleted"
teardown

# autoconnect off on a wired connection: refused, nothing modified.
setup
printf 'Wired=802-3-ethernet\n' > "$VT_STATE/nm_types"
OUT=$(btctl wifi-autoconnect Wired off)
assert_in "$OUT" "not a Wi-Fi connection" "a non-Wi-Fi connection's autoconnect cannot be toggled"
assert_not_called "nmcli connection modify Wired" "and modify never reaches nmcli"
teardown

# autoconnect off on a wifi connection: allowed.
setup
printf 'Home=802-11-wireless\n' > "$VT_STATE/nm_types"
OUT=$(btctl wifi-autoconnect Home off)
assert_in "$OUT" '"autoconnect":"off"' "a Wi-Fi connection's autoconnect can be toggled"
assert_called "nmcli connection modify Home autoconnect no" "and the modify reaches nmcli"
teardown

# The active connection is still refused (the type check passes, the in-use
# check catches it) -- the older guard is not lost.
setup
printf 'Home=802-11-wireless\n' > "$VT_STATE/nm_types"
printf 'Home\n' > "$VT_STATE/nm_active"
OUT=$(btctl wifi-forget Home)
assert_in "$OUT" "currently in use" "the connection in use is still refused"
teardown

# --- the Wi-Fi password never reaches an argv [SecurityReview C3] ---------
# The password arrives on stdin and is written into the keyfile, not passed to
# nmcli. `psk_in_keyfile` reads it back; the harness's own `called()` proves it
# is absent from every recorded argv.
psk_in_keyfile() { grep -q "^psk=$2\$" "$VT_STATE/nm/$1.nmconnection" 2>/dev/null; }

# wifi-connect with a password: the profile carries the real psk, and no argv did.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf 'sup3r-secret-psk' | btctl wifi-connect MyNet)
assert_in "$OUT" '"ok":true' "wifi-connect with a password succeeds"
if psk_in_keyfile wifi-MyNet "sup3r-secret-psk"; then ok "the real psk is in the keyfile"
else bad "the real psk is in the keyfile" "not found in $VT_STATE/nm/wifi-MyNet.nmconnection"; fi
assert_not_called "sup3r-secret-psk" "the real psk never appears in any nmcli argv"
assert_called "nmcli connection add" "the profile was created"
teardown

# An open network: no psk written, still succeeds.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf '' | btctl wifi-connect CafeOpen)
assert_in "$OUT" '"ok":true' "an open network (empty stdin) connects"
if grep -q '^psk=' "$VT_STATE/nm/wifi-CafeOpen.nmconnection" 2>/dev/null; then
    bad "an open network writes no psk" "a psk line is present"
else ok "an open network writes no psk"; fi
teardown

# ap-start with a password: same guarantee.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf 'my-ap-passphrase' | btctl ap-start Studio)
assert_in "$OUT" '"ok":true' "ap-start with a password succeeds"
if psk_in_keyfile lempi-ap "my-ap-passphrase"; then ok "the AP's real psk is in the keyfile"
else bad "the AP's real psk is in the keyfile" "not found"; fi
assert_not_called "my-ap-passphrase" "the AP psk never appears in any nmcli argv"
teardown

# ap-start with empty stdin falls back to the published default.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf '' | btctl ap-start)
assert_in "$OUT" '"ok":true' "ap-start with no password uses the default"
if psk_in_keyfile lempi-ap "Lempi321"; then ok "the default AP psk is written to the keyfile"
else bad "the default AP psk is written to the keyfile" "not found"; fi
teardown

# A failed ap-start must not destroy the existing lempi-ap that revert relies
# on [SecurityReview3 R1a]: the new profile is built under a temporary name and
# only swapped in once its key is set.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
mkdir -p "$VT_STATE/nm"
printf '[connection]\nid=lempi-ap\nuuid=uuid-lempi-ap\n\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=oldpass99\n' \
    > "$VT_STATE/nm/lempi-ap.nmconnection"
export NM_UUID_MISMATCH=1
OUT=$(printf 'newpassword9' | btctl ap-start Studio)
unset NM_UUID_MISMATCH
assert_in "$OUT" "could not set the access point key" "a failed ap-start is reported"
if [ -f "$VT_STATE/nm/lempi-ap.nmconnection" ] && grep -q '^psk=oldpass99$' "$VT_STATE/nm/lempi-ap.nmconnection"; then
    ok "the existing lempi-ap survives a failed ap-start"
else bad "the existing lempi-ap survives a failed ap-start" "it was destroyed"; fi
if [ -e "$VT_STATE/nm/lempi-ap-pending.nmconnection" ]; then bad "the pending profile is cleaned up" "it remains"; else ok "the pending profile is cleaned up"; fi
teardown

# The swap renames before it deletes [SPEC-SEC-080]: a replaced lempi-ap
# carries the new key, and neither the old nor the pending profile is left.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
mkdir -p "$VT_STATE/nm"
printf '[connection]\nid=lempi-ap\nuuid=uuid-lempi-ap\n\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=oldpass99\n' \
    > "$VT_STATE/nm/lempi-ap.nmconnection"
OUT=$(printf 'newpassword9' | btctl ap-start Studio)
assert_in "$OUT" '"ok":true' "ap-start replaces an existing lempi-ap"
if psk_in_keyfile lempi-ap "newpassword9"; then ok "the replaced lempi-ap has the new key"
else bad "the replaced lempi-ap has the new key" "not found"; fi
assert_called "nmcli connection modify lempi-ap connection.id lempi-ap-old" "the old profile steps aside before anything is deleted"
if [ -e "$VT_STATE/nm/lempi-ap-old.nmconnection" ] || [ -e "$VT_STATE/nm/lempi-ap-pending.nmconnection" ]; then
    bad "no old or pending profile is left" "$(ls "$VT_STATE/nm")"
else ok "no old or pending profile is left"; fi
teardown

# A rename that fails puts the old lempi-ap back: at no step is there none.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
mkdir -p "$VT_STATE/nm"
printf '[connection]\nid=lempi-ap\nuuid=uuid-lempi-ap\n\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=oldpass99\n' \
    > "$VT_STATE/nm/lempi-ap.nmconnection"
export NM_RENAME_FAIL_FROM=lempi-ap-pending
OUT=$(printf 'newpassword9' | btctl ap-start Studio)
unset NM_RENAME_FAIL_FROM
assert_in "$OUT" "could not put the new access point profile in place" "a failed swap is reported"
if [ -f "$VT_STATE/nm/lempi-ap.nmconnection" ] && grep -q '^psk=oldpass99$' "$VT_STATE/nm/lempi-ap.nmconnection"; then
    ok "the old lempi-ap is put back when the swap fails"
else bad "the old lempi-ap is put back when the swap fails" "$(ls "$VT_STATE/nm")"; fi
if [ -e "$VT_STATE/nm/lempi-ap-old.nmconnection" ] || [ -e "$VT_STATE/nm/lempi-ap-pending.nmconnection" ]; then
    bad "a failed swap leaves no old or pending profile" "$(ls "$VT_STATE/nm")"
else ok "a failed swap leaves no old or pending profile"; fi
assert_not_called "nmcli connection up lempi-ap" "and the failed swap brings nothing up"
teardown

# A too-short AP password is refused before anything is created.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf 'short' | btctl ap-start Studio)
assert_in "$OUT" "8-63 printable characters or 64 hex" "a too-short AP password is refused"
teardown

# --- the key is validated and escaped before it is written [SecurityReview2 R1] ---

# A newline in the key (the keyfile-injection vector) is refused, and no
# profile is created.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf 'abcdefgh\n[ipv4]\nmethod=manual' | btctl wifi-connect Evil)
assert_in "$OUT" "printable characters" "a newline in the key is refused"
if [ -e "$VT_STATE/nm/wifi-Evil.nmconnection" ]; then bad "no profile is left" "one exists"; else ok "no profile is left"; fi
teardown

# A 7-character key is refused; a 64-hex key is accepted.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf 'sevench' | btctl wifi-connect Net7)
assert_in "$OUT" "printable characters" "a 7-character key is refused"
OUT=$(printf '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef' | btctl wifi-connect NetHex)
assert_in "$OUT" '"ok":true' "a 64-hex key is accepted"
teardown

# A backslash in the key is refused: NetworkManager's keyfile reader cannot
# store one faithfully (measured on lempi02w 2026-09-28), so it is rejected
# rather than mangled, and nothing is created.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf 'pa\\ss12word' | btctl wifi-connect BackNet)
assert_in "$OUT" "printable characters" "a key with a backslash is refused"
if [ -e "$VT_STATE/nm/wifi-BackNet.nmconnection" ]; then bad "no profile is left for a backslash key" "one exists"; else ok "no profile is left for a backslash key"; fi
teardown

# A leading space is encoded as \s (NM strips a raw leading space); the raw key
# is never on an argv.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf ' leadpass12' | btctl wifi-connect LeadNet)
assert_in "$OUT" '"ok":true' "a key with a leading space connects"
if grep -q '^psk=\\sleadpass12$' "$VT_STATE/nm/wifi-LeadNet.nmconnection" 2>/dev/null; then
    ok "the leading space is written as \\s"
else bad "the leading space is written as \\s" "keyfile: $(grep '^psk=' "$VT_STATE/nm/wifi-LeadNet.nmconnection" 2>/dev/null)"; fi
assert_not_called ' leadpass12' "the raw key never appears in an nmcli argv"
teardown

# When set_psk cannot find the keyfile, the throwaway profile is deleted, not
# left with psk=00000000.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq" NM_UUID_MISMATCH=1
OUT=$(printf 'goodpassword' | btctl wifi-connect Orphan)
assert_in "$OUT" "could not set the network key" "a set_psk failure is reported"
if [ -e "$VT_STATE/nm/wifi-Orphan.nmconnection" ]; then bad "the placeholder profile is deleted" "it remains"; else ok "the placeholder profile is deleted"; fi
unset NM_UUID_MISMATCH
teardown

# --- wifi-pending and ap-failover [SPEC061], [IMPL022] ------------------

# wifi-pending returns false when no switch is pending.
setup
OUT=$(btctl wifi-pending)
assert_in "$OUT" '"pending":false' "wifi-pending returns false when no switch is pending"
teardown

# wifi-pending returns true with remaining seconds when a switch is pending.
setup
now=$(date +%s)
exp=$((now + 120))
printf 'change_id=test123\nexpires_epoch=%s\nssid=Hotspot\n' "$exp" > "$LEMPI_RUN_DIR/wifi_pending"
OUT=$(btctl wifi-pending)
assert_in "$OUT" '"pending":true' "wifi-pending returns true when a switch is pending"
assert_in "$OUT" '"change_id":"test123"' "wifi-pending returns change_id"
assert_in "$OUT" '"ssid":"Hotspot"' "wifi-pending returns target ssid"
teardown

# wifi-pending returns false when pending switch is expired.
setup
now=$(date +%s)
exp=$((now - 10))
printf 'change_id=test123\nexpires_epoch=%s\nssid=Hotspot\n' "$exp" > "$LEMPI_RUN_DIR/wifi_pending"
OUT=$(btctl wifi-pending)
assert_in "$OUT" '"pending":false' "wifi-pending returns false when switch is expired"
teardown

# wifi-confirm removes wifi_pending file.
setup
touch "$LEMPI_RUN_DIR/wifi_pending"
OUT=$(btctl wifi-confirm test123)
assert_in "$OUT" '"ok":true' "wifi-confirm succeeds"
if [ ! -f "$LEMPI_RUN_DIR/wifi_pending" ]; then
    ok "wifi-confirm removes wifi_pending file"
else
    bad "wifi-confirm removes wifi_pending file" "file still exists"
fi
teardown

# ap-failover performs standing bringup without scheduling a revert timer.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(btctl ap-failover)
assert_in "$OUT" '"ok":true' "ap-failover succeeds"
assert_not_called "systemd-run" "ap-failover does not schedule a revert timer"
assert_called "nmcli connection up lempi-ap" "ap-failover brings up lempi-ap"
if [ -f "$LEMPI_DNSMASQ_DIR/lempi.conf" ]; then
    ok "ap-failover writes dnsmasq configuration"
else
    bad "ap-failover writes dnsmasq configuration" "missing"
fi
teardown

# lempi-wifi-revert never sets autoconnect yes on lempi-ap and cleans up pending state.
setup
touch "$LEMPI_RUN_DIR/wifi_pending"
OUT=$(sh "$PI/lempi-wifi-revert" lempi-ap TargetNet)
assert_not_called "nmcli connection modify lempi-ap autoconnect yes" "lempi-wifi-revert never sets autoconnect yes on lempi-ap"
assert_called "nmcli connection up lempi-ap" "lempi-wifi-revert restores lempi-ap"
assert_called "nmcli connection modify TargetNet autoconnect no" "lempi-wifi-revert sets target autoconnect no"
if [ ! -f "$LEMPI_RUN_DIR/wifi_pending" ]; then
    ok "lempi-wifi-revert cleans up wifi_pending"
else
    bad "lempi-wifi-revert cleans up wifi_pending" "file remains"
fi
teardown

# lempi-wifi-revert restores autoconnect yes on normal infrastructure profile.
setup
OUT=$(sh "$PI/lempi-wifi-revert" HomeNet TargetNet)
assert_called "nmcli connection modify HomeNet autoconnect yes" "lempi-wifi-revert restores autoconnect yes on home connection"
assert_called "nmcli connection up HomeNet" "lempi-wifi-revert restores home connection"
teardown

# lempi-wifi-failover honors rfkill soft-block.
setup
mkdir -p "$VT_STATE/rfkill/rfkill0"
printf 'wlan\n' > "$VT_STATE/rfkill/rfkill0/type"
printf '1\n' > "$VT_STATE/rfkill/rfkill0/soft"
SYS_CLASS_RFKILL="$VT_STATE/rfkill" sh "$BT/lempi-wifi-failover"
assert_not_called "ap-failover" "lempi-wifi-failover honors rfkill soft block"
teardown

# lempi-wifi-failover honors boot grace period.
setup
printf '20.5 10.2\n' > "$VT_STATE/uptime"
PROC_UPTIME="$VT_STATE/uptime" sh "$BT/lempi-wifi-failover"
assert_not_called "ap-failover" "lempi-wifi-failover waits out boot grace period"
teardown

# lempi-wifi-failover takes no action when connected.
setup
printf '60.0 50.0\n' > "$VT_STATE/uptime"
printf 'wlan0:connected\n' > "$VT_STATE/nm_dev_state"
PROC_UPTIME="$VT_STATE/uptime" sh "$BT/lempi-wifi-failover"
assert_not_called "ap-failover" "lempi-wifi-failover takes no action when connected"
teardown

# lempi-wifi-failover records timestamp on first drop without failing over yet.
setup
printf '60.0 50.0\n' > "$VT_STATE/uptime"
printf 'wlan0:disconnected\n' > "$VT_STATE/nm_dev_state"
PROC_UPTIME="$VT_STATE/uptime" LEMPI_BTCTL="$BT/lempi-btctl" sh "$BT/lempi-wifi-failover"
if [ -f "$LEMPI_RUN_DIR/wifi_disconnected_since" ]; then
    ok "lempi-wifi-failover records first drop timestamp"
else
    bad "lempi-wifi-failover records first drop timestamp" "debounce file not written"
fi
assert_not_called "ap-failover" "and does not fail over on first drop"
teardown

# lempi-wifi-failover brings up lempi-ap once debounce threshold is met.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
printf '60.0 50.0\n' > "$VT_STATE/uptime"
printf 'wlan0:disconnected\n' > "$VT_STATE/nm_dev_state"
now=$(date +%s)
past=$((now - 35))
printf '%s\n' "$past" > "$LEMPI_RUN_DIR/wifi_disconnected_since"
PROC_UPTIME="$VT_STATE/uptime" LEMPI_BTCTL="$BT/lempi-btctl" sh "$BT/lempi-wifi-failover"
assert_called "nmcli connection up lempi-ap" "lempi-wifi-failover brings up lempi-ap after debounce threshold"
if [ ! -f "$LEMPI_RUN_DIR/wifi_disconnected_since" ]; then
    ok "lempi-wifi-failover removes debounce file on failover"
else
    bad "lempi-wifi-failover removes debounce file on failover" "debounce file remains"
fi
teardown
