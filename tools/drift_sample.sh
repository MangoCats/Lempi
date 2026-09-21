#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Append drift samples for one ALSA stream, playback or capture.
#
#     tools/drift_sample.sh <status-file> <out.tsv> [interval_s] [nominal_rate]
#     tools/drift_sample.sh /proc/asound/card0/pcm0c/sub0/status /tmp/adc.tsv 300 48000
#
# Read back with `tools/drift_analyze.py`, which pairs `hw_ptr` against
# `uptime` and nothing else.
#
# **This deliberately does not record `tstamp`** -- or rather, it records it
# under a name that cannot be mistaken for a clock. ALSA derives that field
# from `hw_ptr` at the nominal rate, so comparing the two compares the sample
# counter with itself; that is what made `bose` read +0.43 ppm when it runs
# at +14 `[LOG-FIX-010]`. A column called `tstamp` sitting beside `hw_ptr` is
# an invitation to divide one by the other, and the invitation was accepted
# once already.
#
# The other lesson this file exists for is `[LOG-FIX-070]`: the sampler that
# produced the bad figures lived only on the machine that ran it, so nobody
# reviewed the formula for weeks. This one is in the repository.
#
# `uptime` and the status file are read as close together as a shell can
# manage, which is not very close. The skew shows: hourly windows from this
# scatter ~17 ppm against the in-process frame clock's ~3. Use long windows.
set -u

STATUS="${1:?usage: drift_sample.sh <status-file> <out.tsv> [interval_s] [rate]}"
OUT="${2:?output tsv path}"
INTERVAL="${3:-300}"
RATE="${4:-44100}"

[ -e "$STATUS" ] || { echo "no such status file: $STATUS" >&2; exit 1; }

if [ ! -s "$OUT" ]; then
    printf 'iso\tuptime\tstate\ttrigger_time\thw_ptr\trate\ttstamp_derived_do_not_use\n' > "$OUT"
fi

field() {
    sed -n "s/^$1 *: *//p" "$STATUS" 2>/dev/null | head -1
}

while :; do
    # uptime first, status second, and the gap between them is the read skew
    # this method cannot get rid of -- stated rather than hidden.
    UP=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)
    STATE=$(sed -n 's/^state: *//p' "$STATUS" 2>/dev/null | head -1)
    if [ "${STATE:-}" = "RUNNING" ]; then
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
            "$UP" "$STATE" "$(field trigger_time)" "$(field hw_ptr)" \
            "$RATE" "$(field tstamp)" >> "$OUT"
    fi
    sleep "$INTERVAL"
done
