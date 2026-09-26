#!/bin/sh
# Start of the 30-minute screen-off run [REQ-AND-530]: baseline readings.
DEV="$1"
A="adb -s $DEV"
# Connect first: wireless debugging drops with the screen off, and a script
# that assumed the old connection printed every reading as "not found".
adb connect "$DEV" >/dev/null; sleep 2
$A get-state >/dev/null 2>&1 || { echo "NO DEVICE at $DEV -- nothing read"; exit 1; }
PKG=io.github.mangocats.lempi
date -u +"t0 %Y-%m-%dT%H:%M:%SZ"
$A shell dumpsys battery | grep -E "AC powered|USB powered|level|Charge counter|temperature|voltage"
$A shell dumpsys batterystats --reset
$A forward tcp:5720 tcp:5720 >/dev/null
$A exec-out run-as $PKG cat app_webview/Default/Cookies > /s/cookies.db
python3 /w/android/spike/snap.py playing title position_ms underrun_samples out_recoveries plays
$A shell "dumpsys power | grep -E 'mWakefulness=|mStayOn'"
echo T0_DONE
