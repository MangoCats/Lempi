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
