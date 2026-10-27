# lempi-held: a transfer's hold kept on the node's own loopback, for as
# long as the command runs, and released after it `[PI3-AIM-178]`.
group held || return 0
printf '\nheld\n'

held() { LEMPI_HOLD_SECS="${LIFE:-120}" sh "$BT/lempi-held" "$@"; }
live() { cat "$VT_STATE/holds_live" 2>/dev/null | grep -c .; }
taken() { grep -c "transfer/hold" "$VT_STATE/calls"; }

# --- 1. the command runs under a hold, and its status is the command's ---
setup
OUT=$(held sh -c 'exit 3' 2>&1)
RC=$?
assert_eq "$RC" "3" "the command's exit status is lempi-held's"
assert_in "$OUT" "hold 1: the player paused" "says what the hold did"
assert_called "transfer/release/1" "and releases it after"
assert_eq "$(live)" "0" "no hold is left in force"
teardown

# --- 2. the transfer's stream reaches the command ------------------------
# It runs in the foreground: an asynchronous one would read /dev/null.
setup
OUT=$(printf 'the stream\n' | held cat 2>/dev/null)
assert_eq "$OUT" "the stream" "the command reads lempi-held's own input"
teardown

# --- 3. renewed while it runs, and every renewal released ----------------
# lempi02w, 2026-10-26: renewed from the sender, the hold lapsed mid-copy.
setup
OUT=$(LIFE=3 held sleep 4 2>&1)
assert_eq "$([ "$(taken)" -ge 3 ] && echo renewed || echo "taken $(taken)")" "renewed" \
    "renewed every third of a hold's life while the command ran"
assert_eq "$(live)" "0" "every renewal released by the end"
assert_in "$OUT" "the command having exited (0)" "and says it released"
teardown

# --- 4. no player: the transfer still runs --------------------------------
setup
touch "$VT_STATE/no_player"
OUT=$(held sh -c 'echo ran; exit 0' 2>&1)
assert_in "$OUT" "running unheld" "says it could not hold"
assert_in "$OUT" "ran" "and runs the command anyway"
assert_not_called "transfer/release" "with nothing to release"
teardown

# --- 5. a renewal not answered is said, and the hold kept when it can be ---
setup
held_then_gone() {
    LEMPI_HOLD_SECS=3 sh "$BT/lempi-held" sh -c 'sleep 1.5; touch "$VT_STATE/no_player"; sleep 1.5; rm -f "$VT_STATE/no_player"; sleep 1.5'
}
OUT=$(held_then_gone 2>&1)
assert_in "$OUT" "a renewal was not answered" "a missed renewal is said"
assert_in "$OUT" "renewed again" "and so is the one after it that lands"
assert_eq "$(live)" "0" "and nothing is left held"
teardown

# --- 6. a usage error is not a transfer -----------------------------------
setup
OUT=$(held 2>&1)
RC=$?
assert_eq "$RC" "2" "no command is a usage error"
assert_not_called "transfer/hold" "and takes no hold"
teardown
