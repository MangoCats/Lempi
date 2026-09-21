# lempi-btwatch: noticing a wedged controller while music is playing, and
# deciding whether it may be put back `[PI3-FOUND-750]`.
#
# The wedge this watches for took 25 hours to produce and two days to notice
# `[PI3-FOUND-730]`. None of that is reproducible here, and these do not try:
# what is checked is the policy -- how many failures before acting, what is
# never done while audio is flowing, and that a failed repair reports failure.
group btwatch || return 0
printf '\nbtwatch\n'

# A fake serdev driver directory, so the reset writes to files instead of
# sysfs and the test can read back what it would have done.
watch() {
    LEMPI_BT_DRIVER="$VT_STATE/driver" LEMPI_BT_SERDEV=serial0-0 \
    LEMPI_BTWATCH_MISSES="${MISSES:-3}" LEMPI_BTWATCH_RESET="${RESET:-1}" \
        sh "$PI/lempi-btwatch.sh" 2>&1
}

driver() { mkdir -p "$VT_STATE/driver"; : > "$VT_STATE/driver/unbind"; : > "$VT_STATE/driver/bind"; }

# --- 1. a healthy controller is silent ----------------------------------
setup
driver
OUT=$(watch)
assert_eq "$OUT" "" "a healthy controller produces no output at all"
assert_not_called "busctl" "and does not ask about audio it has no reason to touch"
teardown

# --- 2. one failure is not a wedge --------------------------------------
# The real failure was 7,348 consecutive timeouts. Requiring a few in a row
# costs nothing against that and refuses to act on a blip.
setup
driver
touch "$VT_STATE/hci_wedged"
OUT=$(watch)
assert_in "$OUT" "check 1 of 3" "counts the first failure without acting"
assert_eq "$(cat "$VT_STATE/driver/unbind")" "" "and resets nothing yet"
teardown

# --- 3. it acts once the threshold is reached ---------------------------
setup
driver
touch "$VT_STATE/hci_wedged"
MISSES=1 watch >/dev/null
assert_eq "$(cat "$VT_STATE/driver/unbind")" "serial0-0" "unbinds the serdev device"
assert_eq "$(cat "$VT_STATE/driver/bind")" "serial0-0" "and binds it again, reloading the firmware"
teardown

# --- 4. a reset that does not work says so ------------------------------
# Proven against a healthy adapter, never against a real wedge. A repair
# that reports success it cannot demonstrate is the whole failure this
# investigation is about.
setup
driver
touch "$VT_STATE/hci_wedged"
OUT=$(MISSES=1 watch)
assert_in "$OUT" "did not bring the controller back" "reports a failed reset as failed"
assert_in "$OUT" "needs a reboot" "and names what is actually required"
assert_not_in "$OUT" "answering again" "never claims a recovery it cannot show"
teardown

# --- 5. a reset that works is confirmed by the probe, not assumed -------
setup
driver
touch "$VT_STATE/hci_wedged"
# The stub adapter comes back the moment the fixture is removed, which is
# what binding does on the real one. Removed from underneath the run, during
# the reset's own settle, so the recovery is observed by the second probe
# rather than arranged before the first.
( sleep 2; rm -f "$VT_STATE/hci_wedged" ) &
OUT=$(MISSES=1 watch)
wait
assert_in "$OUT" "answering again after a reset" "confirms recovery by asking the chip again"
teardown

# --- 6. never reset while audio is flowing `[PI3-AIM-060]` --------------
# Every repair here begins by destroying the stream. A wedge that is not
# costing the listener anything yet is not worth interrupting music for: the
# control path is dead, so nothing new can start, but what plays keeps
# playing.
setup
driver
touch "$VT_STATE/hci_wedged"
printf 'active\n' > "$VT_STATE/transport"
OUT=$(MISSES=1 watch)
assert_in "$OUT" "audio is still flowing" "says why it is holding off"
assert_in "$OUT" "next silence" "and when it will act"
assert_eq "$(cat "$VT_STATE/driver/unbind")" "" "and touches nothing"
teardown

# An idle transport is not flowing audio, so the repair may proceed.
setup
driver
touch "$VT_STATE/hci_wedged"
printf 'idle\n' > "$VT_STATE/transport"
MISSES=1 watch >/dev/null
assert_eq "$(cat "$VT_STATE/driver/unbind")" "serial0-0" "an idle transport does not protect the radio"
teardown

# --- 7. the reset can be switched off entirely --------------------------
setup
driver
touch "$VT_STATE/hci_wedged"
OUT=$(MISSES=1 RESET=0 watch)
assert_in "$OUT" "automatic reset is switched off" "honours the opt-out"
assert_eq "$(cat "$VT_STATE/driver/unbind")" "" "and resets nothing"
teardown

# --- 8. recovery clears the count, and is worth one line ----------------
setup
driver
touch "$VT_STATE/hci_wedged"
watch >/dev/null
rm -f "$VT_STATE/hci_wedged"
OUT=$(watch)
assert_in "$OUT" "answering again after 1 failed checks" "notes that it recovered on its own"
assert_eq "$([ -e "$LEMPI_RUN_DIR/btwatch-misses" ] && echo present || echo gone)" "gone" \
          "and forgets the failures"
teardown

# --- 9. a probe that cannot run says so `[GDE-DEP-060]` -----------------
# The trap this whole investigation keeps meeting: a guard that cannot run
# must never report a plausible verdict instead.
setup
driver
OUT=$(PATH="$(echo "$PATH" | sed "s#$HERE/stubs:##")" \
      LEMPI_BT_DRIVER="$VT_STATE/driver" sh "$PI/lempi-btwatch.sh" 2>&1)
case "$OUT" in
    *"not installed"*) ok "says the controller could not be checked when hciconfig is absent" ;;
    *) bad "says the controller could not be checked when hciconfig is absent" "got: $OUT" ;;
esac
assert_eq "$(cat "$VT_STATE/driver/unbind")" "" "and never resets on a verdict it did not reach"
teardown
