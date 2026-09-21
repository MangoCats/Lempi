#!/bin/sh
# SPDX-License-Identifier: MIT
#
# How far apart two echo nodes actually are, in milliseconds.
#
#   tools/echo_skew.sh pi@bose:80 pi@lp3-wifi:5720
#   tools/echo_skew.sh pi@bose:80 pi@lp3-wifi:5720 12 300   # 12 samples, 300 s apart
#
# Prints one line per sample: the passage both are on, and the second node's
# offset from the first. **Positive means the second node is BEHIND.**
#
# Why this exists. Through a long debugging session the only two ways to know
# the skew were a listener in the room and the follower's own residual, and the
# second is the thing under test -- it cannot be its own witness. This asks
# each node what it is playing and when it was asked, which is independent of
# every arithmetic path in `echo.rs`. On 2026-09-18 it agreed with the
# follower's own figure to 90 ms at 4.8 s of skew, which is what established
# that the measurement was sound and the control was not `[GDE-ECHO-344]`.
#
# The two nodes cannot be read at the same instant over ssh, so each reading is
# stamped with that node's own clock and the gap is subtracted out. Both nodes
# discipline to the same NTP sources, so their clocks agree far more closely
# than the skews this is used to chase -- but a node whose clock has not been
# stepped yet `[GDE-ECHO-365]` will produce nonsense here, and the raw
# positions are printed so that case is visible rather than silent.
#
# The websocket handshake is done by hand because the snapshot is only served
# over `/ws`; `strings` recovers the JSON from the frames without needing a
# websocket client on the appliance.
set -u

A="${1:?usage: echo_skew.sh user@host:port user@host:port [samples] [interval_s]}"
B="${2:?second node required}"
N="${3:-1}"
GAP="${4:-60}"

read_node() {
    host="${1%:*}"
    port="${1##*:}"
    ssh -o ConnectTimeout=8 -o BatchMode=yes "$host" \
        "T=\$(date +%s.%N); \
         timeout 4 curl -s --http1.1 -N \
            -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
            -H 'Sec-WebSocket-Version: 13' \
            -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
            http://127.0.0.1:$port/ws 2>/dev/null \
         | strings \
         | grep -oE '\"passage_id\":[0-9]+|\"position_ms\":[0-9]+' \
         | head -2 | tr '\n' ' '; \
         echo \"\$T\""
}

i=1
while [ "$i" -le "$N" ]; do
    ra=$(read_node "$A")
    rb=$(read_node "$B")
    echo "$ra|$rb" | awk -F'|' '
    function num(s, k,   t) { match(s, k "\":[0-9]+"); t = substr(s, RSTART, RLENGTH); sub(/.*:/, "", t); return t + 0 }
    function tm(s,   n) { n = split(s, f, " "); return f[n] + 0 }
    {
        pa = num($1, "passage_id"); posa = num($1, "position_ms"); ta = tm($1)
        pb = num($2, "passage_id"); posb = num($2, "position_ms"); tb = tm($2)
        if (ta == 0 || tb == 0) { print "  unreadable -- is the player up?"; next }
        # What the second node SHOULD read, had it been asked at the same
        # instant as the first.
        want = posa + (tb - ta) * 1000
        skew = want - posb
        same = (pa == pb) ? "" : sprintf("  [DIFFERENT PASSAGES %d vs %d]", pa, pb)
        printf "%s  passage %-7d  %8.0f ms behind%s\n", strftime("%H:%M:%S"), pa, skew, same
    }'
    i=$((i + 1))
    [ "$i" -le "$N" ] && sleep "$GAP"
done
