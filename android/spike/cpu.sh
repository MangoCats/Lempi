#!/bin/sh
# Per-thread CPU over a window, as % of one core, from /proc ticks (100 Hz).
# Usage: cpu.sh <device> <seconds>
DEV="$1"; WIN="${2:-30}"
A="adb -s $DEV"
PKG=io.github.mangocats.lempi
P=$($A shell pidof $PKG </dev/null)
R=$($A shell "ps -A -o PID,NAME | grep 'webview:sandboxed' | awk '{print \$1}'" </dev/null | head -1)
snap() {
    $A shell run-as $PKG sh -c "'for t in /proc/$P/task/*; do echo \$(cat \$t/comm) \$(cut -d\" \" -f14,15 \$t/stat); done'" </dev/null
    [ -n "$R" ] && echo "RENDERER $($A shell cat /proc/$R/stat </dev/null | cut -d' ' -f14,15)"
}
snap > /tmp/a; sleep "$WIN"; snap > /tmp/b
echo "pid $P, renderer ${R:-none}, window ${WIN}s  (% of one core)"
awk -v w="$WIN" 'NR==FNR { a[$1" "NR]=$2+$3; n[NR]=$1; next }
     { t=$2+$3; b[FNR]=t; nm[FNR]=$1 }
     END { for (i in nm) { d=b[i]-a[nm[i]" "i]; if (d>0) printf "%6.1f%%  %s\n", d/w, nm[i] } }' /tmp/a /tmp/b | sort -rn | head -12
