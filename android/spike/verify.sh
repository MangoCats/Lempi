#!/bin/sh
# Check the two fixes on the phone in one session:
#   the start-up underrun [LOG-SPK-910] -- a cold start and a pause/resume
#   should add none;
#   the skin's cost [LOG-SPK-900] -- WebView CPU with the skin on screen.
# The phone should be on its charger, so the screen-off step does not also
# switch wireless debugging off.
# Where it runs: inside lempi-adb by default. From the Windows host, over USB,
# set ADB to platform-tools' adb.exe, W to the repository, S to the work
# directory and PY to python -- see android/README.md.
ADB="${ADB:-adb}"; W="${W:-/w}"; S="${S:-/s}"; PY="${PY:-python3}"
# Git Bash rewrites /sdcard/... into a Windows path before adb.exe sees it.
export MSYS_NO_PATHCONV=1
DEV="$1"
A="$ADB -s $DEV"
PKG=io.github.mangocats.lempi
SP="$W/android/spike"
$ADB connect "$DEV" >/dev/null; sleep 2
$A get-state >/dev/null 2>&1 || { echo "NO DEVICE at $DEV -- nothing checked"; exit 1; }
# The WebView writes its cookie out lazily: just after a launch the file can
# still hold the previous launch's key, which the player refuses. One retry
# after 5 s was not enough on 2026-09-26 -- the cold-start reading came back
# empty and pause/resume were refused -- so wait until the player accepts
# the copied key (GET / answers 200, not 403), and say so if it never does.
key() {
    for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
        $A exec-out run-as $PKG cat app_webview/Default/Cookies > "$S/cookies.db"
        K=$($PY -c "import sqlite3; print(sqlite3.connect('$S/cookies.db').execute('select value from cookies').fetchone()[0])" | tr -d '\r')
        [ "$(curl -s -o /dev/null -w '%{http_code}' -H "x-lempi-key: $K" http://127.0.0.1:5720/)" = 200 ] && return 0
        sleep 5
    done
    echo "KEY NOT ACCEPTED after 60 s -- the readings below are not valid"
}
say() { $PY "$SP/snap.py" playing title position_ms underrun_samples out_recoveries; }
cmd() {
    # tr: a Windows python ends the line with \r\n, and $(...) strips only
    # the \n -- a key sent with a trailing \r is refused (403), found 2026-09-26.
    K=$($PY -c "import sqlite3; print(sqlite3.connect('$S/cookies.db').execute('select value from cookies').fetchone()[0])" | tr -d '\r')
    curl -s -o /dev/null -w "$1: %{http_code}\n" -X POST -H "x-lempi-key: $K" "http://127.0.0.1:5720/command/$1"
}

echo "== install"
$A shell input keyevent KEYCODE_WAKEUP
$A install -r "$W/android/app/build/outputs/apk/debug/app-debug.apk" || exit 1
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
sh "$SP/cpu.sh" "$DEV" 20 | head -9

echo "== CPU, screen off"
$A shell input keyevent KEYCODE_SLEEP; sleep 5
sh "$SP/cpu.sh" "$DEV" 20 | head -5
say
$A shell input keyevent KEYCODE_WAKEUP
echo VERIFY_DONE
