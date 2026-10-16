# lempi-wait-sink: the gate that holds the player, and therefore the web
# interface, until there is somewhere audible to send audio.
group waitsink || return 0
printf '\nwaitsink\n'

# deadline, preferred wait, and the settle deadline -- which defaults to the
# preferred wait plus ten, too slow to wait out in every case [PI3-AIM-120].
gate() {
    LEMPI_SINK_WAIT="${1:-5}" LEMPI_SPEAKER_WAIT="${2:-3}" LEMPI_SETTLE_WAIT="${3:-}" \
        sh "$BT/lempi-wait-sink" 2>&1
}

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
# Anything real is accepted once the settle deadline has passed, even if the
# keeper never chose it [PI3-AIM-120], so the player is never held for ever.
setup
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
sinks "MIDDLETON"
OUT=$(gate 6 1 2)
assert_in "$OUT" "real sink present" "accepts another real sink after the settle deadline"
teardown

# --- no speaker chosen at all: anything real will do ---------------------
setup
: > "$VT_STATE/db_speaker"
sinks "MIDDLETON"
OUT=$(gate 5 3)
assert_in "$OUT" "real sink present" "accepts any real sink when nobody has chosen"
teardown

# --- the keeper has chosen another speaker: release at once --------------
# `[PI3-AIM-120]`: the incumbent is the keeper saying the choice is made.
setup
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
speaker AA:BB:CC:DD:EE:01 "JR-CCB01" yes no
printf 'AA:BB:CC:DD:EE:01\n' > "$LEMPI_RUN_DIR/incumbent"
sinks "JR-CCB01"
OUT=$(gate 5 5)
assert_in "$OUT" "JR-CCB01 present after 0s, chosen by lempi-speaker" "releases when the keeper has chosen"
teardown

# --- another speaker, not yet chosen: hold, then give up loudly ---------
# The car boot `[PI3-FOUND-810]`: a sink that is not the preferred one is
# not taken until the keeper's window has run, and a keeper that never
# decides cannot hold the player for ever.
setup
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
sinks "JR-CCB01"
START=$(date +%s)
OUT=$(gate 5 1 3)
ELAPSED=$(( $(date +%s) - START ))
if [ "$ELAPSED" -ge 3 ]; then
    ok "holds an unchosen speaker until the settle deadline (${ELAPSED}s)"
else
    bad "holds an unchosen speaker until the settle deadline" "released after ${ELAPSED}s"
fi
assert_in "$OUT" "had not chosen a speaker" "says the keeper had not chosen"
teardown

# --- the DAC was chosen: the keeper never claims, so do not wait for it --
setup
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf 'dac\n' > "$VT_STATE/db_active_output"
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
sinks "Built-in Audio Stereo"
OUT=$(gate 5 5)
assert_in "$OUT" "real sink present after 0s" "takes the DAC at once [SPEC-POV-020.3]"
teardown
