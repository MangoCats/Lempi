#!/bin/sh
# End of the screen-off run [REQ-AND-530]: what played, what underran, what
# it cost. Pair with t0.sh's readings.
# Where it runs: inside lempi-adb by default. From the Windows host, over USB,
# set ADB to platform-tools' adb.exe, W to the repository, S to the work
# directory and PY to python -- see android/README.md.
ADB="${ADB:-adb}"; W="${W:-/w}"; S="${S:-/s}"; PY="${PY:-python3}"
# Git Bash rewrites /sdcard/... into a Windows path before adb.exe sees it.
export MSYS_NO_PATHCONV=1
DEV="$1"
A="$ADB -s $DEV"
# Connect first: wireless debugging drops with the screen off, and a script
# that assumed the old connection printed every reading as "not found".
$ADB connect "$DEV" >/dev/null; sleep 2
$A get-state >/dev/null 2>&1 || { echo "NO DEVICE at $DEV -- nothing read"; exit 1; }
PKG=io.github.mangocats.lempi
date -u +"t1 %Y-%m-%dT%H:%M:%SZ"
$A shell dumpsys battery | grep -E "AC powered|USB powered|level|Charge counter|temperature|voltage"
$A shell "dumpsys power | grep -E 'mWakefulness='"
$A forward tcp:5720 tcp:5720 >/dev/null
$A exec-out run-as $PKG cat app_webview/Default/Cookies > "$S/cookies.db"
$PY "$W/android/spike/snap.py" playing title position_ms underrun_samples out_recoveries plays
echo "== history: passages played since t0"
$A shell run-as $PKG sh -c "'ls -la files'"
echo "== the player's log, warnings and errors"
$A shell run-as $PKG cat files/lempi.log | grep -viE "chromium|variations_seed|DnsConfig|linker: Warning" | grep -iE "warn|error|underrun|recover|lost|stall" | tail -20
echo "== CPU now, screen off"
sh "$W/android/spike/cpu.sh" "$DEV" 20 | head -8
echo "== batterystats for the app since t0"
U=$($A shell dumpsys package $PKG | grep -m1 userId= | sed 's/.*userId=//')
$A shell dumpsys batterystats --charged $PKG | grep -E "Estimated power use|Uid u0a|Computed drain|Capacity|Wake lock|Foreground service|Audio|Cpu:|Total cpu time" | head -25
echo "uid $U"
echo T1_DONE
