# lempi-wait-sink: the gate that holds the player, and therefore the web
# interface, until there is somewhere audible to send audio.
group waitsink || return 0
printf '\nwaitsink\n'

gate() { LEMPI_SINK_WAIT="${1:-5}" LEMPI_SPEAKER_WAIT="${2:-3}" sh "$PI/lempi-wait-sink" 2>&1; }

# --- the chosen speaker is already there: release immediately ------------
setup
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
speaker "$OONTZ" "OontZ_Angle 3 U412" yes yes
sinks "OontZ_Angle 3 U412"
OUT=$(gate)
assert_in "$OUT" "present after 0s" "releases at once when the chosen speaker is there"
teardown

# --- only the dummy exists: it is not a real sink ------------------------
# The gate this replaced reported success on a dummy and released the player
# onto nothing audible, with every layer reporting success `[PI3-FOUND-110]`.
setup
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
sinks "Dummy Output"
START=$(date +%s)
OUT=$(gate 3 2)
ELAPSED=$(( $(date +%s) - START ))
assert_not_in "$OUT" "present after" "never reports a dummy as a real sink"
assert_in "$OUT" "no real sink" "says plainly that it gave up"

# --- and the deadline is seconds, not passes ----------------------------
# It counted loop iterations and called them seconds, so a 60 s deadline waited
# 143 s and held the web interface down for all of it `[PI3-FOUND-540]`.
if [ "$ELAPSED" -le 8 ]; then
    ok "the deadline is measured in seconds, not passes (${ELAPSED}s for a 3s deadline)"
else
    bad "the deadline is measured in seconds, not passes" "a 3s deadline took ${ELAPSED}s"
fi
teardown

# --- another speaker is present, the chosen one is not -------------------
# Anything real is accepted once the grace period has passed, so a speakerless
# appliance still boots promptly.
setup
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
sinks "MIDDLETON"
OUT=$(gate 6 1)
assert_in "$OUT" "real sink present" "accepts another real sink after the grace period"
teardown

# --- no speaker chosen at all: anything real will do ---------------------
setup
: > "$VT_STATE/db_speaker"
sinks "MIDDLETON"
OUT=$(gate 5 3)
assert_in "$OUT" "real sink present" "accepts any real sink when nobody has chosen"
teardown
