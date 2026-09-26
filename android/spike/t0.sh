#!/bin/sh
# Start of the 30-minute screen-off run [REQ-AND-530]: baseline readings.
DEV="$1"
A="adb -s $DEV"
PKG=io.github.mangocats.lempi
date -u +"t0 %Y-%m-%dT%H:%M:%SZ"
$A shell dumpsys battery | grep -E "AC powered|USB powered|level|Charge counter|temperature|voltage"
$A shell dumpsys batterystats --reset
$A forward tcp:5720 tcp:5720 >/dev/null
$A exec-out run-as $PKG cat app_webview/Default/Cookies > /s/cookies.db
python3 /w/android/spike/snap.py playing title position_ms underrun_samples out_recoveries plays
$A shell "dumpsys power | grep -E 'mWakefulness=|mStayOn'"
echo T0_DONE
