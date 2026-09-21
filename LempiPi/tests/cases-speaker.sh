# lempi-speaker: the policy of `[PI3-AIM-080]` as a truth table.
#
#   There is at most one speaker connected at a time. It is chosen when none
#   is connected -- the listener's speaker first, then any other known one --
#   and it is replaced only when it goes away.
group speaker || return 0
printf '\nspeaker\n'

keeper() { LEMPI_TICK_SECONDS=4 LEMPI_CHASE_SECONDS=2 sh "$PI/lempi-speaker.sh" 2>&1; }

# --- 1. nothing connected, the chosen speaker is reachable ----------------
# The regression of 2026-09-10 made this path unreachable: an `exit 0` above
# the chase meant nothing ever connected, and a tick with nothing to do looks
# exactly like a tick that did nothing.
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$OONTZ" > "$VT_STATE/reachable"
sinks "Dummy Output"
OUT=$(keeper)
assert_called "bluetoothctl connect $OONTZ" "chases the chosen speaker when nothing is connected"
assert_in "$OUT" "connected $OONTZ" "reports the connection"
teardown

# --- 2. chosen speaker absent, another known one available ----------------
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
speaker "$MIDDL" "MIDDLETON" no no
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$MIDDL" > "$VT_STATE/reachable"
sinks "Dummy Output"
OUT=$(keeper)
assert_called "bluetoothctl connect $OONTZ" "tries the listener's choice first"
assert_called "bluetoothctl connect $MIDDL" "falls back to another known speaker"
assert_in "$OUT" "is absent; trying known speaker" "says it is falling back"
teardown

# --- 3. two connected: the incumbent keeps the audio ----------------------
# The inversion of `[PI3-FOUND-640]`: an intruder must not win by breaking the
# incumbent, and must not be adopted because it is the listener's choice.
setup
speaker "$MIDDL" "MIDDLETON" yes yes
speaker "$OONTZ" "OontZ_Angle 3 U412" yes yes
printf '%s\n' "$MIDDL" > "$LEMPI_RUN_DIR/incumbent"
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf 'MIDDLETON\n' > "$VT_STATE/routed"
sinks "MIDDLETON" "OontZ_Angle 3 U412"
OUT=$(keeper)
assert_called "bluetoothctl disconnect $OONTZ" "disconnects the speaker that is not holding the audio"
assert_not_called "bluetoothctl disconnect $MIDDL" "never disconnects the incumbent"
assert_eq "$(cat "$LEMPI_RUN_DIR/incumbent")" "$MIDDL" "incumbency stays with the holder"
assert_not_in "$OUT" "moved the stream" "does not move audio that is already playing"
teardown

# --- 4. incumbent connected but its sink has not appeared ----------------
# A missing sink is something to wait for, never a reason to switch: a reopen
# aimed at a sink that does not exist abandons working audio `[PI3-FOUND-590]`.
setup
speaker "$MIDDL" "MIDDLETON" yes yes
printf '%s\n' "$MIDDL" > "$LEMPI_RUN_DIR/incumbent"
printf '%s\n' "$MIDDL" > "$VT_STATE/db_speaker"
: > "$VT_STATE/routed"
sinks "Dummy Output"
OUT=$(keeper)
assert_in "$OUT" "waiting, not switching" "waits for the sink instead of switching"
assert_not_called "command/reopen-output" "does not ask for a reopen it cannot satisfy"
teardown

# --- 5. trust follows the audio ------------------------------------------
# BlueZ consults an agent only for untrusted devices, so trust is the
# enforcement `[PI3-FOUND-680]`.
setup
speaker "$MIDDL" "MIDDLETON" yes no
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$MIDDL" > "$LEMPI_RUN_DIR/incumbent"
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf 'MIDDLETON\n' > "$VT_STATE/routed"
sinks "MIDDLETON"
keeper >/dev/null
assert_called "bluetoothctl trust $MIDDL" "trusts whoever holds the audio"
assert_called "bluetoothctl untrust $OONTZ" "untrusts everyone else, so the agent is reachable"
teardown

# --- 6. an unreadable database must not overwrite the choice -------------
# `[PI3-FOUND-700]`: "nobody chose" and "I could not ask" are different
# answers, and only one of them may be acted on.
setup
speaker "$MIDDL" "MIDDLETON" yes yes
printf '%s\n' "$MIDDL" > "$LEMPI_RUN_DIR/incumbent"
printf 'MIDDLETON\n' > "$VT_STATE/routed"
sinks "MIDDLETON"
touch "$VT_STATE/db_fails"
OUT=$(keeper)
assert_eq "$(wc -l < "$VT_STATE/db_writes")" "0" "writes nothing when the database cannot be read"
assert_not_in "$OUT" "adopted" "does not adopt a speaker it could not check for"
teardown

# --- 7. no speaker ever chosen: adoption is allowed ----------------------
setup
speaker "$MIDDL" "MIDDLETON" yes yes
printf '%s\n' "$MIDDL" > "$LEMPI_RUN_DIR/incumbent"
: > "$VT_STATE/db_speaker"
printf 'MIDDLETON\n' > "$VT_STATE/routed"
sinks "MIDDLETON"
OUT=$(keeper)
assert_in "$OUT" "adopted" "adopts when the database says nobody has chosen"
teardown

# --- 8. a tick must not outlast the timer that fires it ------------------
# `[PI3-FOUND-670]`: a tick longer than the period means the next one never
# starts, and the appliance stops reacting without failing.
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
speaker "$MIDDL" "MIDDLETON" no no
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
sinks "Dummy Output"
START=$(date +%s)
LEMPI_TICK_SECONDS=6 LEMPI_CHASE_SECONDS=3 sh "$PI/lempi-speaker.sh" >/dev/null 2>&1
ELAPSED=$(( $(date +%s) - START ))
if [ "$ELAPSED" -le 14 ]; then
    ok "a tick with nothing reachable stays inside its budget (${ELAPSED}s)"
else
    bad "a tick with nothing reachable stays inside its budget" "took ${ELAPSED}s"
fi
teardown

# --- 9. never page while audio is playing `[PI3-AIM-060]` ----------------
# Paging a device the radio cannot reach stalls whatever IS playing for
# several seconds -- measured as an audible skip with the position display
# frozen, and invisible to the underrun counter because the stall is on the
# radio and never touches the output ring. So when the stream is on a real
# sink, the chase budget is zero and nothing is paged.
setup
speaker "$MIDDL" "MIDDLETON" yes yes
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$MIDDL" > "$LEMPI_RUN_DIR/incumbent"
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf 'MIDDLETON\n' > "$VT_STATE/routed"
sinks "MIDDLETON"
keeper >/dev/null
assert_not_called "bluetoothctl connect $OONTZ" "never pages the absent chosen speaker while audio plays"
teardown

# --- 10. never page a device that already has a link `[PI3-FOUND-140]` ---
# `Connected` on the D-Bus device goes true only when a PROFILE connects, so
# it reads false through the whole of A2DP negotiation. An earlier chase took
# that as "absent" and paged again every two seconds; every one of those
# collided with the negotiation already in flight -- eight collisions in one
# boot, and A2DP that had completed at 75 s did not finish until 88. The
# controller's own view, `hcitool con`, is the honest question.
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf 'Connections:\n\t< ACL %s handle 11 state 1 lm CENTRAL\n' "$OONTZ" > "$VT_STATE/hci_con"
sinks "Dummy Output"
keeper >/dev/null
assert_called "hcitool con" "asks the controller whether a link already exists"
assert_not_called "bluetoothctl connect $OONTZ" "keeps out of the way of a negotiation in flight"
teardown

# --- 11. a wedged controller is named, not blamed on the speaker ---------
# `[PI3-FOUND-730]`: for two days the appliance logged "did not answer ...
# (held by another device, or asleep)" -- a sentence about a speaker -- while
# the adapter had stopped acknowledging HCI commands entirely. Paging a dead
# controller accomplishes nothing, so the tick says what is wrong and stops.
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$OONTZ" > "$VT_STATE/reachable"
touch "$VT_STATE/hci_wedged"
sinks "Dummy Output"
OUT=$(keeper)
assert_in "$OUT" "not answering HCI commands" "names the controller, not the speaker"
assert_in "$OUT" "needs a reboot" "says what actually clears it"
assert_not_called "bluetoothctl connect $OONTZ" "does not page a controller that cannot page"
teardown

# --- 12. the chase backs off after repeated misses `[PI3-FOUND-720]` -----
# Two days of paging every 45 s is what wedged the adapter. Once enough ticks
# have found nothing, the radio is left alone -- the speaker can still reach
# US, because trust is untouched `[PI3-FOUND-130]`.
setup
# Untrusted, so the standing invitation is something this tick has to assert:
# trusting is idempotent and skipped when it is already true.
speaker "$OONTZ" "OontZ_Angle 3 U412" no no
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$OONTZ" > "$VT_STATE/reachable"
printf '9 %s\n' "$(( $(date +%s) + 300 ))" > "$LEMPI_RUN_DIR/chase-misses"
sinks "Dummy Output"
keeper >/dev/null
assert_not_called "bluetoothctl connect $OONTZ" "does not page while inside the backoff window"
assert_called "bluetoothctl trust $OONTZ" "still leaves the standing invitation in place"
teardown

# --- 13. a miss is recorded; a connection clears it ----------------------
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
speaker "$MIDDL" "MIDDLETON" no no
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
sinks "Dummy Output"
keeper >/dev/null
assert_eq "$(cut -d' ' -f1 "$LEMPI_RUN_DIR/chase-misses" 2>/dev/null)" "1" \
          "a tick that paged and got nothing records the miss"
teardown

setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$OONTZ" > "$VT_STATE/reachable"
printf '3 0\n' > "$LEMPI_RUN_DIR/chase-misses"
sinks "Dummy Output"
keeper >/dev/null
assert_eq "$([ -e "$LEMPI_RUN_DIR/chase-misses" ] && echo present || echo gone)" "gone" \
          "a connection puts the chase back to full speed"
teardown

# --- 14. a corrupt backoff file must not disable the chase ---------------
# It lives in tmpfs and is read on every tick; a half-written line is a thing
# that happens, and it must not be able to stop the appliance chasing.
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$OONTZ" > "$VT_STATE/reachable"
printf 'garbage\n' > "$LEMPI_RUN_DIR/chase-misses"
sinks "Dummy Output"
keeper >/dev/null
assert_called "bluetoothctl connect $OONTZ" "treats an unreadable backoff state as no backoff"
teardown
