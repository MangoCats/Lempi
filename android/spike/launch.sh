#!/bin/sh
# Catalogue in, app launched, first evidence out -- in one adb session.
set -u
DEV="$1"
PKG=io.github.mangocats.lempi
adb connect "$DEV"
sleep 2
adb -s "$DEV" get-state || { echo "NO DEVICE at $DEV"; exit 1; }
A="adb -s $DEV"

echo "== every catalogued file on the phone?"
missing=0
cut -f2 /s/pairs.tsv | grep -E '^(10,000_Maniacs|4 Non Blondes|Beatles|Cure)/' > /tmp/want
while IFS= read -r rel; do
    if ! $A shell "test -f \"/sdcard/Music/Lempi/$rel\"" </dev/null; then
        echo "  MISSING $rel"; missing=$((missing+1))
    fi
done < /tmp/want
echo "  $(wc -l < /tmp/want) catalogued, $missing missing"

echo "== installed? (not reinstalled: a bulk transfer drops wireless debugging on this phone)"
$A shell pm path $PKG </dev/null || { echo "NOT INSTALLED"; exit 1; }
$A shell pm grant $PKG android.permission.READ_EXTERNAL_STORAGE </dev/null
$A shell am force-stop $PKG </dev/null

echo "== catalogue"
$A push /s/library.db /s/listener.db /data/local/tmp/ </dev/null | tail -1
for db in library.db listener.db; do
    $A shell "run-as $PKG rm -f files/$db files/$db-wal files/$db-shm; cat /data/local/tmp/$db | run-as $PKG sh -c 'cat > files/$db'" </dev/null
done
$A shell rm -f /data/local/tmp/library.db /data/local/tmp/listener.db </dev/null
$A shell run-as $PKG rm -f files/lempi.log </dev/null

echo "== launch"
$A shell am start -W -n $PKG/.MainActivity </dev/null
sleep 20
echo "== the player's log"
$A shell run-as $PKG cat files/lempi.log </dev/null
echo "== the process, and its audio"
$A shell "pidof $PKG; dumpsys media.aaudio 2>/dev/null | head -20" </dev/null
$A shell "dumpsys audio | grep -iE 'player|usage=USAGE_MEDIA' | head -8" </dev/null
echo LAUNCH_DONE
