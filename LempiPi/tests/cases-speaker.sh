# lempi-speaker: the policy of `[PI3-AIM-080]` as a truth table.
#
#   There is at most one speaker connected at a time. It is chosen when none
#   is connected -- the listener's speaker first, then any other known one --
#   and it is replaced only when it goes away.
group speaker || return 0
printf '\nspeaker\n'

keeper() { LEMPI_FALLBACK_SECONDS=2 LEMPI_CHASE_SECONDS=2 sh "$BT/lempi-speaker.sh" 2>&1; }

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
LEMPI_FALLBACK_SECONDS=3 LEMPI_CHASE_SECONDS=3 sh "$BT/lempi-speaker.sh" >/dev/null 2>&1
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

# --- 15. DAC selected: do not chase or page Bluetooth [SPEC-POV-020.3] ---
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$OONTZ" > "$VT_STATE/reachable"
printf 'dac\n' > "$VT_STATE/db_active_output"
sinks "Dummy Output"
keeper >/dev/null
assert_not_called "bluetoothctl connect $OONTZ" "does not chase Bluetooth when active output is dac"
assert_not_called "bluetoothctl trust $OONTZ" "does not trust speakers when active output is dac"
teardown


# --- 16. the car boot: another speaker connected first [PI3-FOUND-810] ---
# lempi02w in a car, 2026-10-12..14. The FM converter connects itself at
# boot; the remembered speaker is at home. The preferred speaker gets its
# window `[PI3-AIM-110]` -- the player is held by the gate, so paging costs
# nothing audible -- and then the converter holds the audio and nothing else
# is paged.
CONV=AA:BB:CC:DD:EE:01
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
speaker "$MIDDL" "MIDDLETON" no no
speaker "$CONV" "JR-CCB01" yes no
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
sinks "JR-CCB01"
OUT=$(keeper)
assert_called "bluetoothctl connect $OONTZ" "the preferred speaker gets its window first"
assert_not_called "bluetoothctl connect $MIDDL" "pages nothing else once the window is over"
assert_in "$OUT" "staying with JR-CCB01" "says it is staying with the connected speaker"
assert_eq "$(cat "$LEMPI_RUN_DIR/incumbent" 2>/dev/null)" "$CONV" \
    "claims it at once, so the gate can release the player [PI3-AIM-120]"
assert_not_called "bluetoothctl disconnect $CONV" "never disconnects the speaker it settled on"
teardown

# --- 17. the car boot: the converter arrives during the chase ------------
# What the journal actually showed: nothing connected when the tick began,
# the converter a second later, and the keeper paging two absent speakers
# for twenty-five seconds more while it played.
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
speaker "$MIDDL" "MIDDLETON" no no
speaker "$CONV" "JR-CCB01" no no
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$CONV" > "$VT_STATE/arrives_on_page"
sinks "Dummy Output"
OUT=$(keeper)
assert_not_called "bluetoothctl connect $MIDDL" "stops paging once a speaker has connected itself"
assert_eq "$(cat "$LEMPI_RUN_DIR/incumbent" 2>/dev/null)" "$CONV" "the speaker that arrived holds the audio"
assert_not_in "$OUT" "nothing has answered" "records no miss when something did answer"
teardown

# --- 18. already playing on another speaker: no window ------------------
# The window is for a boot, where nothing is audible. Once the player is on a
# real sink, paging the preferred speaker would be the skip `[PI3-AIM-060]`
# exists to prevent.
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
speaker "$CONV" "JR-CCB01" yes yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf 'JR-CCB01\n' > "$VT_STATE/routed"
sinks "JR-CCB01"
keeper >/dev/null
assert_not_called "bluetoothctl connect $OONTZ" "never pages the preferred speaker over one that is playing"
assert_eq "$(cat "$LEMPI_RUN_DIR/incumbent" 2>/dev/null)" "$CONV" "keeps the one that is playing"
teardown

# --- 19. the wait is the listener's setting, clamped ---------------------
setup
. "$LEMPI_COMMON"
assert_eq "$(lempi_preferred_wait)" "5" "five seconds when nobody has set it [PI3-AIM-110]"
printf '9\n' > "$VT_STATE/db_preferred_wait"
assert_eq "$(lempi_preferred_wait)" "9" "the stored wait is used"
printf '0\n' > "$VT_STATE/db_preferred_wait"
assert_eq "$(lempi_preferred_wait)" "1" "never less than a second"
printf '99\n' > "$VT_STATE/db_preferred_wait"
assert_eq "$(lempi_preferred_wait)" "60" "never more than the keeper unit allows for [PI3-FOUND-670]"
printf 'soon\n' > "$VT_STATE/db_preferred_wait"
assert_eq "$(lempi_preferred_wait)" "5" "a value that is not a number is no value"
teardown

setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '1\n' > "$VT_STATE/db_preferred_wait"
sinks "Dummy Output"
OUT=$(LEMPI_FALLBACK_SECONDS=2 sh "$BT/lempi-speaker.sh" 2>&1)
assert_in "$OUT" "did not answer in 1s" "the keeper's window is the stored wait"
teardown

# --- 20. five minutes heard on a speaker makes it the preferred one -----
# `[PI3-AIM-130]`. Seeded twenty seconds short of the threshold, with the
# last tick playing too; this tick, playing, crosses it.
dwelling() { # addr seconds was-playing
    printf '%s %s %s %s\n' "$1" "$2" "$(( $(date +%s) - 20 ))" "$3" > "$LEMPI_RUN_DIR/dwell"
}
on_the_converter() {
    speaker "$OONTZ" "OontZ_Angle 3 U412" no no
    speaker "$CONV" "JR-CCB01" yes yes
    printf '%s\n' "$CONV" > "$LEMPI_RUN_DIR/incumbent"
    printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
    printf 'JR-CCB01\n' > "$VT_STATE/routed"
    sinks "JR-CCB01"
}
setup
on_the_converter
touch "$VT_STATE/playing"
dwelling "$CONV" 290 1
OUT=$(keeper)
assert_in "$OUT" "now the preferred speaker" "adopts after five minutes of playing"
assert_in "$(cat "$VT_STATE/db_writes")" "'speaker_address', '$CONV'" "records it as the speaker"
assert_in "$(cat "$VT_STATE/db_writes")" "'bt:$CONV'" "and as the output, where the output was a speaker"
assert_eq "$([ -e "$LEMPI_RUN_DIR/dwell" ] && echo present || echo gone)" "gone" "and starts counting afresh"
teardown

# --- 21. paused time is not listening -----------------------------------
setup
on_the_converter
dwelling "$CONV" 290 1
keeper >/dev/null
assert_eq "$(wc -l < "$VT_STATE/db_writes")" "0" "does not adopt while paused"
assert_eq "$(cut -d' ' -f2,4 "$LEMPI_RUN_DIR/dwell")" "290 0" "keeps the count, and notes it was not playing"
teardown

setup
on_the_converter
touch "$VT_STATE/playing"
dwelling "$CONV" 290 0
keeper >/dev/null
assert_eq "$(wc -l < "$VT_STATE/db_writes")" "0" "does not count the stretch since a paused tick"
teardown

# --- 22. the count belongs to one speaker -------------------------------
setup
on_the_converter
touch "$VT_STATE/playing"
dwelling "$MIDDL" 290 1
keeper >/dev/null
assert_eq "$(wc -l < "$VT_STATE/db_writes")" "0" "another speaker's minutes do not carry over"
assert_eq "$(cut -d' ' -f1,2 "$LEMPI_RUN_DIR/dwell")" "$CONV 0" "the count restarts for the new speaker"
teardown

# --- 23. never on a choice that could not be read [PI3-FOUND-700] -------
setup
on_the_converter
touch "$VT_STATE/playing" "$VT_STATE/db_fails"
dwelling "$CONV" 290 1
keeper >/dev/null
assert_eq "$(wc -l < "$VT_STATE/db_writes")" "0" "adopts nothing when the preference could not be read"
teardown

# --- 24. playing on the preferred speaker has nothing to earn ----------
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" yes yes
printf '%s\n' "$OONTZ" > "$LEMPI_RUN_DIR/incumbent"
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf 'OontZ_Angle 3 U412\n' > "$VT_STATE/routed"
sinks "OontZ_Angle 3 U412"
touch "$VT_STATE/playing"
dwelling "$CONV" 100 1
keeper >/dev/null
assert_eq "$([ -e "$LEMPI_RUN_DIR/dwell" ] && echo present || echo gone)" "gone" \
    "a count for another speaker ends when the preferred one plays"
teardown

# --- 25. a stored "dac" on a node with no DAC [PI3-FOUND-820] ------------
# lempi02w has none. The player stored "dac" after a failed choice, and the
# keeper, standing aside for the DAC, reconnected nothing until a later
# choice succeeded. Where PipeWire offers no local output, it is ignored.
nodes_bluetooth_only() {
    cat > "$VT_STATE/pw_nodes" <<'EOF'
	id 30, type PipeWire:Interface:Node/3
 		node.name = "Dummy-Driver"
	id 49, type PipeWire:Interface:Node/3
 		node.name = "alsa_playback.lempi"
 		media.class = "Stream/Output/Audio"
	id 55, type PipeWire:Interface:Node/3
 		node.name = "bluez_output.AA_BB_CC_DD_EE_01.1"
 		media.class = "Audio/Sink"
EOF
}
nodes_with_jack() {
    cat > "$VT_STATE/pw_nodes" <<'EOF'
	id 30, type PipeWire:Interface:Node/3
 		node.name = "Dummy-Driver"
	id 48, type PipeWire:Interface:Node/3
 		node.name = "alsa_output.platform-3f00b840.mailbox.stereo-fallback"
 		media.class = "Audio/Sink"
EOF
}
setup
. "$LEMPI_COMMON"
assert_eq "$(lempi_local_sink)" "unknown" "no PipeWire to ask is not 'no DAC'"
nodes_bluetooth_only
assert_eq "$(lempi_local_sink)" "no" "a Bluetooth sink and the player's own stream are not a DAC"
nodes_with_jack
assert_eq "$(lempi_local_sink)" "yes" "an ALSA sink is a DAC"
: > "$VT_STATE/pw_nodes"
assert_eq "$(lempi_local_sink)" "unknown" "an empty listing is not an answer"
teardown

setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$OONTZ" > "$VT_STATE/reachable"
printf 'dac\n' > "$VT_STATE/db_active_output"
nodes_bluetooth_only
sinks "Dummy Output"
OUT=$(keeper)
assert_called "bluetoothctl connect $OONTZ" "ignores a stored DAC on a node that has none"
assert_in "$OUT" "this node has none" "and says so"
OUT=$(keeper)
assert_not_in "$OUT" "this node has none" "once per boot, not every tick"
teardown

setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
printf '%s\n' "$OONTZ" > "$VT_STATE/reachable"
printf 'dac\n' > "$VT_STATE/db_active_output"
nodes_with_jack
sinks "Built-in Audio Stereo"
keeper >/dev/null
assert_not_called "bluetoothctl connect $OONTZ" "still obeys a stored DAC on a node that has one"
teardown

# --- 20. the early judgement and its one redial `[PI3-AIM-150]` -----------

# A fake lempi-btctl whose `a2dp-sample` answers what the fixture says.
sampler() { # packets modal_dlen at_modal
    printf '#!/bin/sh\nprintf "btctl %%s\n" "$*" >> "%s/calls"\nprintf "{\\"ok\\":true,\\"packets\\":%s,\\"modal_dlen\\":%s,\\"at_modal\\":%s}\n"\n' \
        "$VT_STATE" "$1" "$2" "$3" > "$VT_STATE/btctl"
    chmod +x "$VT_STATE/btctl"
}
# MIDDLETON holding the audio for `$1` seconds, the player sending to it.
holding() {
    speaker "$MIDDL" "MIDDLETON" yes yes
    printf '%s\n' "$MIDDL" > "$VT_STATE/db_speaker"
    printf '%s\n' "$MIDDL" > "$LEMPI_RUN_DIR/incumbent"
    printf '%s %s\n' "$MIDDL" "$(( $(date +%s) - $1 ))" > "$LEMPI_RUN_DIR/held_since"
    printf 'MIDDLETON\n' > "$VT_STATE/routed"
    : > "$VT_STATE/playing"
    sinks "MIDDLETON"
    printf 'Connections:\n\t< ACL %s handle 11 state 1 lm CENTRAL\n' "$MIDDL" > "$VT_STATE/hci_con"
}
judge() { LEMPI_BTCTL="$VT_STATE/btctl" keeper; }

# Too early: nothing sampled.
setup
holding 5; sampler 1100 612 1095
judge >/dev/null
assert_not_called "a2dp-sample" "a link five seconds old is not sampled yet"
teardown

# A steady link: judged once, never redialled.
setup
holding 30; sampler 1100 612 1095
OUT=$(judge)
assert_called "btctl a2dp-sample 15" "a link thirty seconds in is sampled"
assert_in "$OUT" "MIDDLETON is steady: 1095 of 1100" "and a steady one is said to be"
assert_not_called "bluetoothctl disconnect $MIDDL" "and is not redialled"
: > "$VT_STATE/calls"
judge >/dev/null
assert_not_called "a2dp-sample" "judged once, not every tick"
teardown

# A failing link -- the packet size moving -- is redialled, once.
setup
holding 30; sampler 900 612 400
printf '%s\n' "$MIDDL" > "$VT_STATE/reachable"
# The kernel's link list as it is once the disconnect has gone through.
: > "$VT_STATE/hci_con"
OUT=$(judge)
assert_in "$OUT" "MIDDLETON is failing: 400 of 900" "a moving packet size is a failing link"
assert_called "bluetoothctl disconnect $MIDDL" "it is dropped"
assert_called "bluetoothctl connect $MIDDL" "and dialled again, outbound"
assert_called "reopen-output" "and the player sent back to it"
assert_in "$OUT" "its sink was back" "with how long the sink took to return"
# Still failing after it: said, and not redialled again.
grep -q "$MIDDL" "$VT_STATE/connected" || printf 'Device %s MIDDLETON\n' "$MIDDL" >> "$VT_STATE/connected"
printf '%s %s\n' "$MIDDL" "$(( $(date +%s) - 30 ))" > "$LEMPI_RUN_DIR/held_since"
: > "$VT_STATE/calls"
OUT=$(judge)
assert_in "$OUT" "still failing after its redial" "a second failing sample is said"
assert_not_called "bluetoothctl disconnect $MIDDL" "and never redials twice"
teardown

# Not playing: not judged, so silence cannot pass for a steady link.
setup
holding 30; sampler 1100 612 1095
rm -f "$VT_STATE/playing"
judge >/dev/null
assert_not_called "a2dp-sample" "a paused player is not sampled"
teardown

# A sample that cannot be taken says so, and is not a clean bill.
setup
holding 30
printf '#!/bin/sh\necho "{\\"ok\\":false,\\"error\\":\\"btmon is not installed\\"}"\n' > "$VT_STATE/btctl"
chmod +x "$VT_STATE/btctl"
OUT=$(judge)
assert_in "$OUT" "could not sample the audio to MIDDLETON" "a sample that could not be taken is said"
assert_not_in "$OUT" "is steady" "and not reported as steady"
teardown

# Past the window unjudged: no sample, and the keeper may settle.
setup
holding 400; sampler 1100 612 1095
OUT=$(judge)
assert_not_called "a2dp-sample" "a link past three minutes is not sampled"
assert_in "$OUT" "settled, the keeper goes quiet" "and settles"
teardown

# --- 21. settled, then quiet `[PI3-AIM-140]` ------------------------------

# Two minutes held and judged: settled.
setup
holding 130; sampler 1100 612 1095
: > "$LEMPI_RUN_DIR/judged"
OUT=$(judge)
assert_in "$OUT" "MIDDLETON has held the audio for" "two minutes held and judged settles"
# The next tick asks bluetoothd nothing.
: > "$VT_STATE/calls"
OUT=$(judge)
assert_eq "$OUT" "" "a quiet tick says nothing"
assert_not_called "bluetoothctl" "and never runs bluetoothctl"
assert_called "hcitool con" "it asks the kernel's link list"
teardown

# Not yet judged -- too little sent to judge -- is not settled, however long.
setup
holding 130; sampler 50 612 50
OUT=$(LEMPI_EARLY_UNTIL=999 judge)
assert_not_in "$OUT" "goes quiet" "an unjudged link does not settle"
teardown

# A second link ends the quiet, and the full tick sends it away.
setup
holding 130
speaker "$OONTZ" "OontZ_Angle 3 U412" yes no
printf '%s\nMIDDLETON\n' "$MIDDL" > "$LEMPI_RUN_DIR/settled"
printf 'Connections:\n\t< ACL %s handle 11 state 1 lm CENTRAL\n\t> ACL %s handle 12 state 1 lm PERIPHERAL\n' \
    "$MIDDL" "$OONTZ" > "$VT_STATE/hci_con"
OUT=$(keeper)
assert_in "$OUT" "no longer settled (the links changed)" "a second link ends the quiet"
assert_called "bluetoothctl disconnect $OONTZ" "and the full tick shows it the door"
teardown

# The stream moved off the settled speaker: full ticks again.
setup
holding 130
printf '%s\nMIDDLETON\n' "$MIDDL" > "$LEMPI_RUN_DIR/settled"
printf 'Dummy Output\n' > "$VT_STATE/routed"
OUT=$(keeper)
assert_in "$OUT" "no longer settled (the stream moved)" "a moved stream ends the quiet"
teardown

# Another speaker chosen on the settings page: full ticks again.
setup
holding 130
printf '%s\nMIDDLETON\n' "$MIDDL" > "$LEMPI_RUN_DIR/settled"
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
OUT=$(keeper)
assert_in "$OUT" "no longer settled (the listener's choice changed)" "a new choice ends the quiet"
teardown

# --- a transfer holding the shared radio stands the keeper down [PI3-AIM-176]
# lempi02w, 2026-10-26: the Boom 2 off mid-export, and the keeper paging it
# and two others on the radio the export needed.
setup
speaker "$OONTZ" "OontZ_Angle 3 U412" no yes
speaker "$MIDDL" "MIDDLETON" no no
printf '%s\n' "$OONTZ" > "$VT_STATE/db_speaker"
sinks "Dummy Output"
touch "$VT_STATE/transfer_held"
OUT=$(keeper)
assert_in "$OUT" "a transfer holds the shared radio" "says it stands down"
assert_not_called "bluetoothctl connect" "and pages nothing while the hold has the radio"
OUT=$(keeper)
assert_not_in "$OUT" "a transfer holds" "said once, not every tick"
rm -f "$VT_STATE/transfer_held"
printf '%s\n' "$OONTZ" > "$VT_STATE/reachable"
OUT=$(keeper)
assert_in "$OUT" "hold has ended" "says it is back"
assert_called "bluetoothctl connect $OONTZ" "and chases the speaker again once the hold ends"
teardown

# A settled speaker stays settled across a hold: the hold releases the
# output, which must not read as the stream moving.
setup
holding 130
printf '%s\nMIDDLETON\n' "$MIDDL" > "$LEMPI_RUN_DIR/settled"
printf 'Dummy Output\n' > "$VT_STATE/routed"
touch "$VT_STATE/transfer_held"
OUT=$(keeper)
assert_not_in "$OUT" "no longer settled" "a released output under a hold does not unsettle"
assert_eq "$([ -e "$LEMPI_RUN_DIR/settled" ] && echo kept || echo gone)" "kept" "and the settled mark is kept"
teardown
