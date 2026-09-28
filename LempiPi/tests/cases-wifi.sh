# lempi-btctl's Wi-Fi verbs: forget and autoconnect. The web process runs
# these as root, and before `[SecurityReview C5]` they acted on any connection
# name a browser sent -- so a cross-site request could delete or disable a
# wired, VPN or other system profile the UI never listed. The guard is
# is_wifi_conn: only an 802-11-wireless connection may be forgotten or toggled.
group wifi || return 0
printf '\nwifi\n'

btctl() { bash "$PI/lempi-btctl" "$@" 2>&1; }

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

# A too-short AP password is refused before anything is created.
setup
export LEMPI_NM_DIR="$VT_STATE/nm" LEMPI_DNSMASQ_DIR="$VT_STATE/dnsmasq"
OUT=$(printf 'short' | btctl ap-start Studio)
assert_in "$OUT" "at least 8 characters" "a too-short AP password is refused"
teardown
