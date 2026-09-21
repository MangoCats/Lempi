# lempi-btctl: the verbs the settings panel calls. This is the surface the
# listener actually touches, it writes `speaker_address`, and its address
# argument is the only untrusted input this appliance takes -- it arrives from
# a browser.
group btctl || return 0
printf '\nbtctl\n'

btctl() { LEMPI_DB="$VT_STATE/fake.db" bash "$PI/lempi-btctl" "$@" 2>&1; }

# --- the untrusted input ------------------------------------------------
# Shape-checked before it reaches bluetoothctl or SQL. Every one of these
# would be harmless on its own and none should reach a command line.
setup
sinks "MIDDLETON"
for bad_addr in "not-an-address" "20:64:DE:CF:F3" "20:64:DE:CF:F3:AD:99" \
                "\$(touch $VT_STATE/pwned)" "20:64:DE:CF:F3:AD; rm -rf /" ""; do
    OUT=$(btctl use "$bad_addr")
    case "$OUT" in
        *"not a device address"*) ok "rejects a malformed address: ${bad_addr:-<empty>}" ;;
        *) bad "rejects a malformed address: ${bad_addr:-<empty>}" "got: $OUT" ;;
    esac
done
if [ -f "$VT_STATE/pwned" ]; then
    bad "a command substitution in the address is never evaluated" "it ran"
else
    ok "a command substitution in the address is never evaluated"
fi
assert_not_called "bluetoothctl connect not-an-address" "never passes a rejected address to bluetoothctl"
teardown

# --- use: connect, trust, and record the choice -------------------------
setup
speaker "$MIDDL" "MIDDLETON" no no
printf '%s\n' "$MIDDL" > "$VT_STATE/reachable"
sinks "MIDDLETON"
btctl use "$MIDDL" >/dev/null
assert_called "bluetoothctl trust $MIDDL" "use trusts the chosen speaker, so it can reconnect after a power cut"
assert_called "bluetoothctl connect $MIDDL" "use connects it"
teardown

# --- forget: the recovery path the listener reaches for -----------------
setup
speaker "$MIDDL" "MIDDLETON" yes yes
sinks "MIDDLETON"
btctl forget "$MIDDL" >/dev/null
assert_called "bluetoothctl remove $MIDDL" "forget removes the pairing"
teardown

# --- status answers without touching the radio --------------------------
# The settings panel polls this; it must not page anything.
setup
speaker "$MIDDL" "MIDDLETON" yes yes
sinks "MIDDLETON"
OUT=$(btctl status "$MIDDL")
assert_in "$OUT" "{" "status answers in JSON"
assert_not_called "bluetoothctl connect" "status never pages a device"
teardown

# --- scan: a cache must never be returned as a scan result --------------
# `[PI3-FOUND-740]`. The scan discarded bluetoothctl's output and then listed
# BlueZ's cache, so a scan that never reached the air returned ok:true with
# the remembered speakers in it. On 2026-09-20 the listener saw exactly that
# seven times while the adapter was wedged, and the panel answered "put yours
# in pairing mode".
scanctl() { SCAN_SECONDS=0 LEMPI_DB="$VT_STATE/fake.db" bash "$PI/lempi-btctl" scan 2>&1; }

setup
speaker "$MIDDL" "MIDDLETON" no yes
sinks "MIDDLETON"
OUT=$(scanctl)
assert_in "$OUT" '"ok":true' "a scan that started reports success"
assert_in "$OUT" "$MIDDL" "and lists what is known"
assert_not_in "$OUT" '"error"' "with no error attached"
assert_called "bluetoothctl<transport bredr" "asks for BR/EDR, or it cannot see a speaker"
teardown

# The whole point: discovery refused, so this is NOT a scan result.
setup
speaker "$MIDDL" "MIDDLETON" no yes
sinks "MIDDLETON"
touch "$VT_STATE/scan_fails"
OUT=$(scanctl)
assert_in "$OUT" '"ok":false' "a scan that never started does not report success"
assert_in "$OUT" "refused to scan" "and says the adapter refused"
assert_in "$OUT" "org.bluez.Error.InProgress" "quoting BlueZ's own reason"
assert_in "$OUT" "not a scan result" "and marks the list as remembered, not found"
assert_in "$OUT" "$MIDDL" "while still offering the remembered speakers"
teardown

# A wedged controller names something the listener can act on; the raw D-Bus
# error names nothing `[PI3-FOUND-730]`.
setup
speaker "$MIDDL" "MIDDLETON" no yes
sinks "MIDDLETON"
touch "$VT_STATE/scan_fails"
touch "$VT_STATE/hci_wedged"
OUT=$(scanctl)
assert_in "$OUT" '"ok":false' "a wedged adapter is a failed scan"
assert_in "$OUT" "needs a reboot" "and is reported as the adapter, not the speaker"
assert_not_in "$OUT" "pairing mode" "never sends the listener after the speaker"
teardown

# The health probe costs a live scan nothing: it is only asked on failure.
setup
speaker "$MIDDL" "MIDDLETON" no yes
sinks "MIDDLETON"
scanctl >/dev/null
assert_not_called "hciconfig" "a scan that worked never pays for the health probe"
teardown

# The JSON must survive a reason containing characters that would break it.
setup
speaker "$MIDDL" "MIDDLETON" no yes
sinks "MIDDLETON"
touch "$VT_STATE/scan_fails"
# stdout only, deliberately: the other cases fold stderr in with `2>&1` and
# match substrings, which tolerates it, but the contract being checked here
# is that *stdout alone* is a JSON document a caller can parse.
OUT=$(SCAN_SECONDS=0 LEMPI_DB="$VT_STATE/fake.db" bash "$PI/lempi-btctl" scan 2>/dev/null)
if printf '%s' "$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
    ok "a failed scan is still valid JSON on stdout alone"
else
    bad "a failed scan is still valid JSON on stdout alone" "did not parse: $OUT"
fi
teardown
