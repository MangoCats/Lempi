#!/bin/sh
# Put the spike's music and catalogue on the phone, and install the app.
# Runs inside lempi-android-app with /music = the PC's Music folder,
# /s = this directory, /w = the repository.
# Where it runs: inside lempi-adb by default. From the Windows host, over USB,
# set ADB to platform-tools' adb.exe, W to the repository, S to the work
# directory and PY to python -- see android/README.md.
ADB="${ADB:-adb}"; W="${W:-/w}"; S="${S:-/s}"; PY="${PY:-python3}"
# Git Bash rewrites /sdcard/... into a Windows path before adb.exe sees it.
export MSYS_NO_PATHCONV=1
set -u
DEV="$1"
PKG=io.github.mangocats.lempi
$ADB connect "$DEV" >/dev/null
$ADB -s "$DEV" get-state || { echo "NO DEVICE at $DEV"; exit 1; }
A="$ADB -s $DEV"

echo "== audio into /sdcard/Music/Lempi"
cut -f2 "$S/pairs.tsv" | sed 's|/[^/]*$||' | sort -u | while IFS= read -r album; do
    parent=$(dirname "$album")
    # </dev/null: $ADB reads stdin, and would eat the rest of this loop's list.
    $A shell mkdir -p "\"/sdcard/Music/Lempi/$parent\"" </dev/null
    $A push --sync "/music/$album" "/sdcard/Music/Lempi/$parent/" </dev/null | tail -1
done
# Tell MediaStore, so other players see it too [REQ-AND-210].
$A shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file:///sdcard/Music/Lempi >/dev/null 2>&1

echo "== install"
$A install -r "$W/android/app/build/outputs/apk/debug/app-debug.apk" || exit 1
$A shell pm grant $PKG android.permission.READ_EXTERNAL_STORAGE

echo "== catalogue into the app's private storage [REQ-AND-200]"
$A push "$S/library.db" "$S/listener.db" /data/local/tmp/ | tail -1
$A shell run-as $PKG mkdir -p files
for db in library.db listener.db; do
    $A shell "cat /data/local/tmp/$db | run-as $PKG sh -c 'cat > files/$db'"
done
$A shell rm /data/local/tmp/library.db /data/local/tmp/listener.db
$A shell run-as $PKG ls -l files
echo "== audio on the phone: $($A shell find /sdcard/Music/Lempi -name '*.mp3' | wc -l) mp3 files"
echo PUSH_DONE
