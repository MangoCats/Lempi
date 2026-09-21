# lempi-common.sh: the facts every helper shares. One copy each, so one bug
# each -- and the sink parser has already been wrong once in a way that let
# the player open onto a dummy and report success `[PI3-FOUND-110]`.
group common || return 0
printf '\ncommon\n'

setup
. "$LEMPI_COMMON"

sinks "MIDDLETON" "Dummy Output" "OontZ_Angle 3 U412"
assert_eq "$(lempi_sinks | tr '\n' ',')" "MIDDLETON,OontZ_Angle 3 U412," \
    "lempi_sinks lists real sinks and drops the dummy"
if lempi_sink_present "MIDDLETON"; then ok "sink_present finds a sink"; else bad "sink_present finds a sink" "not found"; fi
if lempi_sink_present "Dummy Output"; then bad "sink_present rejects the dummy" "accepted"; else ok "sink_present rejects the dummy"; fi
if lempi_sink_present "Nonexistent"; then bad "sink_present rejects an unknown name" "accepted"; else ok "sink_present rejects an unknown name"; fi
if lempi_sink_present ""; then bad "sink_present rejects an empty name" "accepted"; else ok "sink_present rejects an empty name"; fi

# A header line must never be mistaken for a sink: that exact confusion is
# what `[PI3-FOUND-110]` was.
assert_not_in "$(lempi_sinks)" "Sinks:" "lempi_sinks never returns a header row"

# The database the listener's choice actually lives in `[PI3-FOUND-280]`.
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
assert_eq "$(lempi_speaker)" "$OONTZ" "lempi_speaker reads the chosen address"
touch "$VT_STATE/db_fails"
if lempi_speaker >/dev/null 2>&1; then
    bad "lempi_speaker fails loudly when the database cannot be read" "exited 0"
else
    ok "lempi_speaker fails loudly when the database cannot be read"
fi
rm -f "$VT_STATE/db_fails"

# AFH decoding: three readers, one hex parser. The settled map measured on the
# appliance is the fixture.
MAP=000000fcffffffffff3f
assert_eq "$(lempi_afh_channels $MAP)" "52" "lempi_afh_channels counts the settled map"
assert_eq "$(lempi_afh_channels ffffffffffffffffff7f)" "79" "lempi_afh_channels counts a naive map"
assert_eq "$(lempi_afh_bytes $MAP)" "0x00 0x00 0x00 0xfc 0xff 0xff 0xff 0xff 0xff 0x3f " \
    "lempi_afh_bytes renders octets for hcitool"
# Octet 9's top bit is reserved and must be cleared before it reaches the
# controller.
assert_eq "$(lempi_afh_bytes ffffffffffffffffffff | awk '{print $10}')" "0x7f" \
    "lempi_afh_bytes clears the reserved bit"
assert_in "$(lempi_afh_excluded $MAP)" "2402-2427 MHz" "lempi_afh_excluded names the WiFi band"
assert_in "$(lempi_afh_excluded $MAP)" "2480-2480 MHz" "lempi_afh_excluded names the top channel"
# The bug shipped on 2026-09-10 was a doubled backslash in this awk, which
# produced no output at all rather than failing.
if [ -n "$(lempi_afh_excluded $MAP)" ]; then ok "lempi_afh_excluded produces output"; else bad "lempi_afh_excluded produces output" "empty"; fi

printf 'MIDDLETON\n' > "$VT_STATE/routed"
assert_eq "$(lempi_routed)" "MIDDLETON" "lempi_routed reads the player's sink"
teardown
