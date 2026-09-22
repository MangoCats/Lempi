#!/bin/sh
# Keep the chosen speaker connected, without anyone typing anything.
#
# The gap this fills [PI3-AIM-030]: BlueZ accepts a trusted device that comes
# to it, but nothing on the Pi ever reaches OUT. So a speaker switched on after
# boot stays unconnected, the player honestly reports silence, and a listener
# concludes it is broken.
#
# Deliberately dumb: check, connect if absent, tell the player to reopen, stop.
# A timer runs it. Nothing here retries in a loop, because a loop is a thing
# that can wedge and this must not be the reason audio stops.
set -u
# **`[PI3-FOUND-280]` The listener's settings moved and this did not follow.**
# `[IMPL-DBSPLIT-025]` split the database: the catalog stayed in
# `/srv/library/`, and everything the listener chooses -- volume, programme,
# and `speaker_address` -- moved to `/var/lempi/listener.db`. These scripts
# kept reading the pre-split file, which still exists and still holds a
# `speaker_address` row. It was the *same* address, so nothing looked wrong
# for weeks.
#
# It stopped being the same the moment a second speaker was chosen. Measured:
# the settings page moved the player to the OontZ and wrote that address to
# `listener.db`; this script read MIDDLETON out of the stale file, decided the
# routing disagreed with the speaker "on record", and asked the player to
# reopen -- fighting the listener's own choice every thirty seconds, on the
# authority of a file nothing had written to in weeks.
#
# Chosen by what exists rather than by a build-time flag, so one script serves
# a split appliance and an unsplit one without being told which it is on.
. "${LEMPI_COMMON:-/usr/local/lib/lempi-common.sh}" 2>/dev/null ||
    . "$(dirname "$0")/lempi-common.sh"
DB=$(lempi_db)
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

# The address is whatever the player last recorded through `use`/`pair`
# [PI3-AIM-020], [REQ-VIS-260] -- not a hard-coded guess. A speaker chosen
# once through the settings panel is the one this timer chases from then on,
# on any appliance, without editing this file or its unit. `SPEAKER` still
# overrides it, for a library with no player-chosen speaker yet, or a
# deliberate manual pin.
# **`[PI3-FOUND-700]` "No speaker chosen" and "could not read the choice" are
# not the same answer, and one of them must never be acted on.** Adoption was
# guarded on `SPEAKER` being empty, which conflates the two: a query that fails
# -- a locked database, a moment's contention with the player -- also yields an
# empty string, and the appliance then decides nobody ever chose a speaker and
# writes whatever happens to be connected over the listener's choice.
#
# Measured 2026-09-10 19:35:14, from the appliance's own log: *"adopted
# 08:EB:ED:26:14:12 as the speaker (none was chosen)"* -- while one very much
# was. That is the `[PI3-FOUND-310]` failure happening through the guard built
# to prevent it, and it silently changed which speaker the appliance prefers at
# every future boot.
#
# So the read's exit status is kept. `sqlite3` returns 0 for a query that
# matches no rows and non-zero when it could not ask, which is exactly the
# distinction needed.
if [ -n "${SPEAKER:-}" ]; then
    SPEAKER_KNOWN=yes
elif SPEAKER=$(lempi_speaker); then
    SPEAKER_KNOWN=yes
else
    SPEAKER=""
    SPEAKER_KNOWN=no
fi

# **`[PI3-FOUND-130]` Trust is what lets the speaker reconnect to US**, and it
# is the first thing a recovery throws away.
#
# BlueZ auto-authorises an incoming service connection only from a device
# marked trusted. Untrusted, it asks an agent instead; this appliance
# registers one only for the duration of a `pair` `[PI3-WHY-060]`, so at every
# other moment there is nobody to ask and the request is refused outright.
# Measured 2026-09-08, on the boot after the speaker was hard-reset and
# re-paired by hand: the Middleton had an ACL link to the Pi by 20.8 s and
# then tried three times to bring up A2DP -- 28.9 s, 37.8 s, 46.7 s -- and was
# rejected every time with `Authentication attempt without agent` /
# `Access denied`. Its persisted record read `Trusted=false` while the link
# key beside it was perfectly good. Audio did not arrive until this script's
# own outbound connect won at 55.7 s: some thirty-five seconds spent refusing
# the speaker's own offers to connect.
#
# That matters beyond the delay. The speaker reaching out to us is the ONE
# path that does not have to win the power-up race `[PI3-FOUND-090]` -- it
# costs no paging, no shared-radio time, and it starts the moment the speaker
# is awake. Losing trust silently disables it and leaves only the race, which
# is the path this appliance loses by design.
#
# `use` has always trusted `[PI3-WHY-040]`, but a listener who recovers by
# forgetting the device and reconnecting -- through `bluetoothctl`, or through
# any path but that one verb -- lands on a bonded, untrusted speaker and
# nothing ever puts it back. So it is asserted here instead: every tick,
# idempotent, checked before it is set so a healthy appliance spends nothing,
# and loud when it actually had to repair something.
# **`[PI3-FOUND-230]` Ask bluetoothd once, not four times.** Every
# `bluetoothctl` invocation opens a D-Bus connection and enumerates the
# adapter's objects, and this script had grown to three or four of them per
# tick -- trust, connected-list, audio-sink, alias -- against a daemon that is
# at that moment carrying an A2DP stream. Measured: the listener's stutters at
# 120 s and 151 s land on the timer's own ticks at 118 s and 153 s. The
# information wanted is all in one `info` block, so it is fetched once and
# read several times. The keeper polls; polling should cost as little as it
# can while something is playing.
# Does PipeWire actually offer a sink by this name? Parsed from the
# numbered rows only, the same discipline as `lempi-wait-sink`
# `[PI3-FOUND-110]`, so no header or box-drawing line can be mistaken for a
# sink.
INFO=$(bluetoothctl info "${SPEAKER:-none}" 2>/dev/null)

# **`[PI3-FOUND-680]` Trust is not a fact about the chosen speaker, it is the
# enforcement.** This used to be asserted here, unconditionally, on the
# listener's chosen speaker every tick -- and that is why the agent turned out
# to be unreachable. BlueZ consults an agent only for an **untrusted** device;
# a trusted one is authorised without anybody being asked. Measured
# 2026-09-10: the Oontz was powered on while the Middleton held the audio,
# both read `Trusted: yes`, the agent was never called at all, and the
# incumbent went silent for thirty-five seconds until the keeper's backstop
# disconnected the intruder.
#
# So trust now follows the audio. Asserted below, once the incumbent is known:
# on the incumbent when there is one, on the chosen speaker when there is not,
# so that a speaker which is nobody's incumbent yet can still reconnect to us
# unprompted after a power cycle `[PI3-FOUND-130]` -- the one path that does
# not have to win the power-up race.
trust_only() {
    _keep="$1"
    [ -n "${_keep:-}" ] || return 0
    bluetoothctl info "$_keep" 2>/dev/null | grep -q 'Trusted: yes' ||
        { bluetoothctl trust "$_keep" >/dev/null 2>&1 &&
              echo "trusted $(lempi_alias "$_keep") so it can reconnect on its own"; }
    for _other in $(bluetoothctl devices Paired 2>/dev/null | awk '{print $2}'); do
        [ "$_other" = "$_keep" ] && continue
        _oi=$(bluetoothctl info "$_other" 2>/dev/null)
        echo "$_oi" | grep -q 'UUID: Audio Sink' || continue
        echo "$_oi" | grep -q 'Trusted: yes' || continue
        bluetoothctl untrust "$_other" >/dev/null 2>&1 &&
            echo "untrusted $(lempi_alias "$_other") -- only the speaker holding the audio may let itself in"
    done
}

# **Who is actually connected, which is not always who was chosen.**
#
# The history is worth keeping because it shaped everything below.
# `[PI3-AIM-040]`, 2026-09-04: `speaker_address` had gone stale (still
# MIDDLETON, from earlier testing) while the appliance was connected to and
# playing through a different, real speaker. Every tick, this script paged the
# stored address -- unreachable, since nothing was asking it to be reachable
# -- tying up the one shared radio and stalling the speaker that WAS playing,
# invisible to the output ring's own underrun counter. The conclusion drawn
# then was "believe whatever is connected over whatever is merely remembered",
# and this block adopted any connected audio device on the strength of it.
#
# **That conclusion no longer governs, and the code below no longer does it**
# `[PI3-FOUND-310]`. The injury it was written against is prevented at source
# by `[PI3-AIM-060]`: nothing is paged at all while audio reaches a real sink,
# so a stale address can no longer stall anything. What adoption still did was
# overwrite a choice the listener had just made in the settings panel, the
# moment a second trusted speaker connected itself. So what is computed here
# is only *who is connected*; what to do about it is decided below, and a
# recorded choice wins.
# **`[PI3-AIM-080]` The whole policy, in one sentence.**
#
# *There is at most one speaker connected at a time. It is chosen when none is
# connected -- the listener's speaker first, then any other known one -- and it
# is replaced only when it goes away.*
#
# That sentence replaces four rules that had grown separately and had begun to
# fight each other: a stickiness test, a viability test, a startup preference
# and a disconnect-the-others sweep. On 2026-09-10 they inverted themselves
# `[PI3-FOUND-640]` -- the sweep correctly kicked an intruder off a playing
# Middleton, the intruder reconnected and broke the Middleton's sink, the
# viability test then read the broken sink as licence to move the audio, and
# the next sweep kicked the Middleton instead. Every rule did what it said.
#
# The error was making *viability* the test for keeping audio where it is,
# because that hands victory to any intruder able to break the incumbent --
# which, on one adapter with one A2DP transport, is all of them. Incumbency is
# an identity, not a condition. A speaker whose sink was knocked out has not
# become unavailable; it has been attacked.
#
# So: whoever holds the audio is the incumbent, recorded by address. While the
# incumbent is still *connected*, nothing else may have the audio and nothing
# else may stay connected. A missing sink is something to wait for, never a
# reason to switch.
# Overridable so the test harness can run the real policy without touching
# the machine it runs on `[PI3-AIM-100]`.
INCUMBENT_DIR="${LEMPI_RUN_DIR:-/run/lempi}"
INCUMBENT="$INCUMBENT_DIR/incumbent"

# **`[PI3-FOUND-720]` A chase that never succeeds must get slower, because the
# controller does not survive being paged forever.**
#
# Every bound in this script was about a single tick: `[PI3-AIM-060]` stops a
# chase interrupting audio, `[PI3-FOUND-670]` stops a tick outlasting its
# timer, `[PI3-FOUND-140]` stops two pages overlapping. Each is correct. None
# of them looks at what a *succession* of failed ticks does, and the answer,
# measured 2026-09-20, is that it kills the adapter.
#
# The appliance sat for two days with both known speakers switched off. The
# timer fires every 30 s; a tick that finds nothing spends 15 s paging the
# chosen speaker and up to 10 s paging fallbacks, so each cycle ran ~45 s and
# the next was already due when it ended. The radio had **no idle time at
# all** for two days. At 2026-09-18 16:32:55 -- some 25 hours in -- the
# controller stopped acknowledging `HCI_Create_Connection` (`0x0405`), and it
# never started again: 7,348 `tx timeout` lines followed. By 2026-09-20
# 13:39:39 the wedge had reached `HCI_Inquiry` (`0x0401`), and from then on
# the appliance could not *discover* a speaker either -- BlueZ's discovery
# state machine stuck, every scan refused with `org.bluez.Error.InProgress`
# before anything reached the air. Nothing short of a reboot cleared it;
# `hciconfig hci0 up` could not re-init the device and even `HCI_Reset`
# (`0x0c03`) timed out.
#
# So the effort is capped over time as well as within a tick. This costs the
# appliance very little, because **outbound paging is not the only path and
# never was**: the chosen speaker is trusted precisely so it can connect to
# *us* unasked `[PI3-FOUND-130]`, which costs no radio time and starts the
# moment the speaker wakes. Backing the chase off leaves that path untouched.
# What it gives up is the power-up race `[PI3-FOUND-090]`, which is worth
# chasing hard for the first couple of minutes after a speaker goes missing
# and worth almost nothing an hour later.
CHASE_STATE="$INCUMBENT_DIR/chase-misses"

# Cleared the moment anything is actually connected, so a speaker that comes
# back is chased at full speed the next time it disappears.
chase_clear() { rm -f "$CHASE_STATE" 2>/dev/null; }

# How long to wait after `n` consecutive ticks that paged and got nothing.
# Zero for the first four, which covers the power-up race at full speed; then
# two minutes; then ten. At the far end the radio spends ~25 s paging every
# 10 minutes rather than continuously -- about a 4% duty cycle instead of
# 100%, which is the difference the measurement above is about.
chase_delay() {
    if   [ "$1" -lt 5 ];  then echo 0
    elif [ "$1" -lt 21 ]; then echo 120
    else                       echo 600
    fi
}

# Every connected device that can actually play music, one address per line.
SPEAKERS=$(lempi_connected_speakers)
HELD=$(cat "$INCUMBENT" 2>/dev/null)

# Who holds the audio. The recorded incumbent keeps it for as long as it is
# connected -- that is the whole point. Only when it is gone does anything else
# become eligible, and then the listener's choice is preferred.
CONNECTED=""
if [ -n "${HELD:-}" ] && printf '%s\n' "$SPEAKERS" | grep -qxF "$HELD"; then
    CONNECTED="$HELD"
elif [ -n "${SPEAKER:-}" ] && printf '%s\n' "$SPEAKERS" | grep -qxF "$SPEAKER"; then
    CONNECTED="$SPEAKER"
else
    CONNECTED=$(printf '%s\n' "$SPEAKERS" | head -1)
fi

if [ -n "$CONNECTED" ]; then
    # Something is connected, so whatever the chase was backing off from is
    # over `[PI3-FOUND-720]`.
    chase_clear
    if [ "$CONNECTED" != "${HELD:-}" ]; then
        mkdir -p "$INCUMBENT_DIR" 2>/dev/null
        printf '%s\n' "$CONNECTED" > "$INCUMBENT" 2>/dev/null
    fi

    # Trust follows the audio, so the agent is actually reachable for everyone
    # else `[PI3-FOUND-680]`.
    trust_only "$CONNECTED"

    # **Exactly one.** Anything else that has managed to connect is shown the
    # door, whatever the audio is currently doing -- a second speaker is not a
    # guest to be tolerated, it is the thing that breaks the first one
    # `[PI3-FOUND-600]`. The agent `[PI3-FOUND-630]` should have refused it
    # before it got this far; this is the tidy-up for the ones that slip past.
    printf '%s\n' "$SPEAKERS" | while read -r other; do
        [ -n "$other" ] || continue
        [ "$other" = "$CONNECTED" ] && continue
        echo "$(lempi_alias "$other") connected while $(lempi_alias "$CONNECTED") holds the audio -- disconnecting it"
        bluetoothctl disconnect "$other" >/dev/null 2>&1
    done

    # Bookkeeping, only where it cannot contradict anybody: adopt into the
    # database when no speaker was ever chosen. A recorded choice stands until
    # the listener changes it `[PI3-FOUND-310]`.
    if [ "$SPEAKER_KNOWN" = yes ] && [ -z "${SPEAKER:-}" ]; then
        case "$CONNECTED" in
            ??:??:??:??:??:??)
                sqlite3 "$DB" "INSERT INTO player_settings (key, value, updated_at) \
                     VALUES ('speaker_address', '$CONNECTED', datetime('now')) \
                     ON CONFLICT(key) DO UPDATE SET \
                         value = excluded.value, updated_at = excluded.updated_at" \
                    2>/dev/null \
                    && echo "adopted $CONNECTED as the speaker (none was chosen)"
                ;;
        esac
    fi

    # Route the audio at the incumbent, and at nothing else. A sink that is not
    # there yet is waited for: `Connected: yes` means BlueZ has a link and says
    # nothing about whether PipeWire has anywhere to send audio
    # `[PI3-FOUND-590]`, and a reopen aimed at a sink that does not exist is an
    # instruction to abandon working audio.
    ALIAS=$(lempi_alias "$CONNECTED")
    ROUTED=$(lempi_routed)
    if [ -n "$ALIAS" ] && lempi_sink_present "$ALIAS" &&
       [ "$(printf '%s' "$ROUTED" | tr a-z A-Z)" != "$(printf '%s' "$ALIAS" | tr a-z A-Z)" ]; then
        lempi_reopen
        echo "moved the stream from '${ROUTED:-nothing}' to '$ALIAS', which holds the audio"
    elif [ -n "$ALIAS" ] && ! lempi_sink_present "$ALIAS"; then
        echo "'$ALIAS' holds the audio but has no sink in PipeWire yet -- waiting, not switching"
    fi
    exit 0
fi

# Nothing connected, so the incumbency is over and the next tick may pick
# freely. Cleared here rather than left to go stale, so a speaker that comes
# back does not inherit a claim it no longer has.
rm -f "$INCUMBENT" 2>/dev/null

# Nobody holds the audio, so the listener's choice gets the standing invitation
# back: it is the one path that does not have to win the power-up race. Skipped
# entirely when the choice could not be read `[PI3-FOUND-700]` -- handing the
# invitation to a guess is the same mistake in a quieter form.
[ "$SPEAKER_KNOWN" = yes ] && trust_only "${SPEAKER:-}"

# **Absent is a real answer, not an error.** Paging a device the shared
# Bluetooth radio cannot reach stalls whatever the appliance IS playing for
# several seconds -- measured as an audible skip with the position display
# frozen, and invisible to the player's own underrun counter, because the
# stall happens on the radio and never touches the output ring at all. A
# stale or empty address must do nothing, not page something.
[ -n "$SPEAKER" ] || exit 0

# **`[PI3-AIM-060]` How hard to chase depends on what chasing can cost.**
#
# `[PI3-AIM-020]`/`[PI3-AIM-040]` both ended in the same injury: paging a
# device that could not answer tied up the shared radio and stalled the
# speaker that WAS playing. The conclusion drawn then -- try once, briefly,
# and stop -- was right about the risk and wrong as a general policy, because
# it also governs the case where nothing is playing at all, where there is
# no audio to protect and the timid attempt simply loses.
#
# And on this appliance losing is the default. `[PI3-FOUND-090]`: the
# Middleton powers the Pi from its own USB port, so the two can only power up
# together; the Pi needs ~30 s to reach a working Bluetooth stack, and any
# phone already awake in the room has had the speaker since second two. The
# speaker then stops answering pages entirely, so the Pi's one attempt comes
# back `br-connection-page-timeout` and the appliance concludes, every single
# power cycle, that its speaker is switched off.
#
# So the cost is what sets the effort, not a fixed rule: if the player is on
# a real sink right now, something is audible and the old timidity is exactly
# right. If it is on a dummy or on nothing, there is no audio to interrupt,
# and the radio's time is better spent staying after the speaker than idle.
# **`[PI3-FOUND-670]` A tick must finish inside the timer's period.** The
# chase had 22 s and the fallback another 20, so a tick where the chosen
# speaker is switched off could spend 42 s in a service fired every 30 -- and
# systemd will not start a tick while the last one is still running. Measured
# 2026-09-10, when the listener powered down the speaker holding the audio:
# the service sat in `activating` and no tick completed for minutes, while the
# same work run by hand recovered the appliance in 25 seconds. The appliance
# was not slow to recover; it was not running.
#
# So there is one budget for the tick, shared. The chase gets the larger part
# because it is the common case, and the fallback keeps a share of its own: a
# single shared deadline would let a chosen speaker that is switched off eat
# the whole tick, every tick, and the fallback would never run at all.
TICK_BUDGET="${LEMPI_TICK_SECONDS:-25}"
CHASE_BUDGET="${LEMPI_CHASE_SECONDS:-15}"
FALLBACK_BUDGET="${LEMPI_FALLBACK_SECONDS:-$(( TICK_BUDGET - CHASE_BUDGET ))}"

ROUTED=$(lempi_routed)
case "${ROUTED:-none}" in
    none|"Dummy Output") BUDGET="$CHASE_BUDGET" ;;
    *)                   BUDGET=0 ;;
esac

# How many ticks in a row have paged and got nothing, and the earliest time
# the next page is allowed `[PI3-FOUND-720]`. A stored value that is not a
# number is treated as no value: this file lives in tmpfs and is read on
# every tick, and a half-written line must not be able to disable the chase.
MISSES=0
HOLD=0
if [ -r "$CHASE_STATE" ]; then
    read -r MISSES HOLD < "$CHASE_STATE" 2>/dev/null || { MISSES=0; HOLD=0; }
fi
case "${MISSES:-}" in ''|*[!0-9]*) MISSES=0 ;; esac
case "${HOLD:-}"   in ''|*[!0-9]*) HOLD=0   ;; esac
NOW=$(date +%s)

# **`[PI3-FOUND-730]` Ask the controller whether it is answering before
# spending a tick paging it.**
#
# For two days this script issued a `connect` every 45 s into a controller
# that had stopped acknowledging the command, and every one of those ticks
# logged the ordinary *"did not answer in 15s (held by another device, or
# asleep)"* -- a sentence describing a **speaker** problem, printed 4,967
# times by an **adapter** problem. The truthful line was in `dmesg` the whole
# time and nothing here was reading it.
#
# `hcitool con` cannot serve as this probe: it reads the kernel's connection
# list and never touches the chip, so it answered perfectly throughout. The
# question has to be an HCI command the controller itself must reply to, and
# `hciconfig hci0 name` (`Read Local Name`) is the cheapest one. Note that
# `btmgmt info` is *not* usable either -- it kept returning the adapter's
# name from the kernel's cache while the chip was dead, which is exactly the
# empty-result-with-a-zero-status trap this project keeps relearning.
#
# A guard that cannot run says so rather than inventing a verdict
# `[GDE-DEP-060]`: with no `hciconfig` installed the probe reports `unknown`
# and the chase proceeds, loudly, rather than silently concluding the
# adapter is dead and never paging again.
controller_state() {
    command -v hciconfig >/dev/null 2>&1 || { echo unknown; return; }
    if hciconfig hci0 name >/dev/null 2>&1; then echo alive; else echo wedged; fi
}

if [ "$BUDGET" -gt 0 ]; then
    case "$(controller_state)" in
        wedged)
            echo "the Bluetooth controller is not answering HCI commands -- not paging anything." \
                 "Check 'dmesg | grep hci0' for tx timeouts; this state needs a reboot [PI3-FOUND-730]"
            MISSES=$(( MISSES + 1 ))
            printf '%s %s\n' "$MISSES" "$(( NOW + $(chase_delay "$MISSES") ))" \
                > "$CHASE_STATE" 2>/dev/null
            exit 0
            ;;
        unknown)
            echo "hciconfig is not installed, so the controller's health could not be checked" \
                 "before paging [PI3-FOUND-730]"
            ;;
    esac
fi

# Still inside the backoff window from earlier misses. The standing invitation
# asserted above is untouched, so a speaker that wakes can still reconnect to
# us unasked `[PI3-FOUND-130]`; this only declines to spend the radio paging
# it `[PI3-FOUND-720]`.
if [ "$BUDGET" -gt 0 ] && [ "$NOW" -lt "$HOLD" ]; then
    exit 0
fi

# Bounded by wall clock, not by a count of tries: each attempt costs whatever
# the page timeout happens to be, and the number that must stay under the
# timer's own period is seconds. Nothing here loops without a deadline --
# a wedged keeper is the one thing this must never become.
# **`[PI3-FOUND-140]` Never page a device the controller already has a link
# to.** `Connected` on the D-Bus device goes true when a PROFILE connects, so
# it reads false through the whole of A2DP negotiation -- and the first
# version of this chase took that as "absent" and paged again every two
# seconds. Every one of those collided with the negotiation already in
# flight: `avdtp_connect_cb() ... Operation already in progress (114)`, eight
# times in one boot, and A2DP that had previously completed at 75 s did not
# finish until 88 s. The chase delayed the thing it existed to hurry.
#
# `hcitool con` is the honest question, because it asks the controller
# whether a baseband link exists rather than asking BlueZ whether a profile
# finished. A link present means a connection is up or coming up, and the
# only useful thing to do is keep out of its way.
started=$(date +%s)
while :; do
    if hcitool con 2>/dev/null | grep -qi "$SPEAKER"; then
        sleep 3
    else
        bluetoothctl connect "$SPEAKER" >/dev/null 2>&1
        # Long enough that two pages cannot overlap: a page that goes
        # unanswered costs the controller its own timeout, and starting the
        # next one on top of it is what produced the collisions above.
        sleep 3
    fi
    if bluetoothctl info "$SPEAKER" 2>/dev/null | grep -qi 'Connected: yes'; then
        # Connected. The stream does not dependably follow a change of default
        # sink [PI3-WHY-020], so the player is told explicitly -- and only
        # after a connection actually succeeded, so a reopen is never spent on
        # nothing.
        chase_clear
        lempi_reopen
        echo "connected $SPEAKER after $(( $(date +%s) - started ))s and asked the player to reopen"
        exit 0
    fi
    [ $(( $(date +%s) - started )) -lt "$BUDGET" ] || break
done

# Said once per tick, and only when there was nothing to lose by trying, so the
# log shows a speaker being waited for rather than an appliance repeating that
# it has failed.
[ "$BUDGET" -gt 0 ] && echo "$SPEAKER did not answer in ${BUDGET}s (held by another device, or asleep)"

# **`[PI3-FOUND-560]` A remembered speaker that is switched off should not
# leave the appliance silent when another one is sitting there paired.**
#
# Asked for directly by the listener, after a boot spent waiting on a speaker
# that had been powered down while a second, known speaker was awake in the
# same room `[PI3-FOUND-540]`. The appliance had everything it needed to make
# sound and made none.
#
# Deliberately narrow. It runs only when the chase has already failed and only
# when `BUDGET` is non-zero, which means nothing is currently audible -- so it
# can never interrupt playback to go hunting, which is the injury of
# `[PI3-AIM-060]`. It considers only devices that are **paired and advertise
# an Audio Sink**: things somebody deliberately introduced to this
# appliance, never something merely in range.
#
# **Not trusted, deliberately.** Trust was the obvious test and it is the
# wrong one. This appliance untrusts every speaker but the chosen one, so
# that only the chosen one may reconnect to *us* unasked `[PI3-FOUND-130]`,
# and the Middleton duly read `Trusted: no` the moment the listener
# switched to the Oontz. Requiring trust here would have skipped precisely
# the speaker this exists to fall back to. Trust governs an inbound
# connection; this one is outbound, and a bond is what says it is known.
#
# It does not rewrite the listener's choice. `speaker_address` still names the
# speaker they picked, so when that one comes back it is preferred again on
# the next boot. This is a stand-in for a missing speaker, not a new decision
# about which speaker this is.
if [ "$BUDGET" -gt 0 ]; then
    FALLBACK="$FALLBACK_BUDGET"
    fstart=$(date +%s)
    for addr in $(bluetoothctl devices Paired 2>/dev/null |
                  sed -n 's/^Device \([0-9A-F:]*\) .*/\1/p'); do
        [ "$addr" = "$SPEAKER" ] && continue
        [ $(( $(date +%s) - fstart )) -lt "$FALLBACK" ] || break
        cand=$(bluetoothctl info "$addr" 2>/dev/null)
        echo "$cand" | grep -q 'Audio Sink' || continue
        echo "$cand" | grep -qE 'Paired: yes|Bonded: yes' || continue
        echo "$SPEAKER is absent; trying known speaker $addr"
        bluetoothctl connect "$addr" >/dev/null 2>&1
        sleep 3
        if bluetoothctl info "$addr" 2>/dev/null | grep -qi 'Connected: yes'; then
            curl -s -o /dev/null -X POST \
                "http://localhost:${LEMPI_PORT:-5720}/command/reopen-output"
            chase_clear
            echo "connected known speaker $addr instead, and asked the player to reopen"
            exit 0
        fi
    done
fi

# Nothing answered. Record the miss and set the earliest time the next page is
# allowed `[PI3-FOUND-720]`.
#
# The cadence change is announced only when it actually changes, so the log
# shows the appliance easing off once rather than saying so every tick -- the
# opposite of the 4,967 identical lines that hid `[PI3-FOUND-730]`.
if [ "$BUDGET" -gt 0 ]; then
    WAS=$(chase_delay "$MISSES")
    MISSES=$(( MISSES + 1 ))
    NEXT=$(chase_delay "$MISSES")
    printf '%s %s\n' "$MISSES" "$(( $(date +%s) + NEXT ))" > "$CHASE_STATE" 2>/dev/null
    [ "$NEXT" != "$WAS" ] &&
        echo "nothing has answered in $MISSES ticks; paging every ${NEXT}s from now until something does"
fi
exit 0
