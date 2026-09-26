#!/bin/sh
# Check the two fixes on the phone in one session:
#   the start-up underrun [LOG-SPK-910] -- a cold start and a pause/resume
#   should add none;
#   the skin's cost [LOG-SPK-900] -- WebView CPU with the skin on screen.
# The phone should be on its charger, so the screen-off step does not also
# switch wireless debugging off.
DEV="$1"
A="adb -s $DEV"
PKG=io.github.mangocats.lempi
SP=/w/android/spike
adb connect "$DEV" >/dev/null; sleep 2
$A get-state >/dev/null 2>&1 || { echo "NO DEVICE at $DEV -- nothing checked"; exit 1; }
key() { $A exec-out run-as $PKG cat app_webview/Default/Cookies > /s/cookies.db; }
# The WebView writes its cookie out lazily: just after a launch the file can
# still hold the previous key, which the player refuses. Re-read once.
say() {
    python3 $SP/snap.py playing title position_ms underrun_samples out_recoveries 2>/dev/null \n        || { sleep 5; key; python3 $SP/snap.py playing title position_ms underrun_samples out_recoveries; }
}
cmd() {
    K=$(python3 -c "import sqlite3; print(sqlite3.connect('/s/cookies.db').execute('select value from cookies').fetchone()[0])")
    curl -s -o /dev/null -w "$1: %{http_code}\n" -X POST -H "x-lempi-key: $K" "http://127.0.0.1:5720/command/$1"
}

echo "== install"
$A shell input keyevent KEYCODE_WAKEUP
$A install -r /w/android/app/build/outputs/apk/debug/app-debug.apk || exit 1
$A shell dumpsys package $PKG | grep -m1 versionName
$A shell pm grant $PKG android.permission.READ_EXTERNAL_STORAGE

echo "== cold start: underruns after 12 s"
$A shell am force-stop $PKG
$A shell run-as $PKG rm -f files/lempi.log
$A shell am start -W -n $PKG/.MainActivity >/dev/null
sleep 12
$A forward tcp:5720 tcp:5720 >/dev/null
key; say
$A shell run-as $PKG sh -c "'grep -c Underrun files/lempi.log'" | sed 's/^/log lines naming Underrun, this launch: /'

echo "== pause 5 s, resume, 5 s"
cmd pause; sleep 5; cmd play; sleep 5; say

echo "== CPU, skin on screen, playing"
sh $SP/cpu.sh "$DEV" 20 | head -9

echo "== CPU, screen off"
$A shell input keyevent KEYCODE_SLEEP; sleep 5
sh $SP/cpu.sh "$DEV" 20 | head -5
say
$A shell input keyevent KEYCODE_WAKEUP
echo VERIFY_DONE
