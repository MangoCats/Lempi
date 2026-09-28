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
