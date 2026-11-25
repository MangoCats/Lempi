#!/bin/sh
# A phone from before Stage A onto a current build: its catalogue given its
# passage uids in place, on the hub, before the new app first opens it
# [IMPL-UID-066]. tools/phone_uids.py does the work; this moves the files.
#
#   android/phone-uids.sh pull   <device> <work dir>
#   android/phone-uids.sh commit <device> <work dir> <passages> <apk>
#
# pull: stops the app, copies its pair (with any -wal and -shm) into
#   <work dir>/pulled, untouched, and into <work dir>/work, checkpointed to one
#   file each; then rehearses. Nothing on the phone is written. The app stays
#   stopped -- it starts again when a person opens it.
# commit: gives the uids in <work dir>/work (backed up first, beside it), then,
#   with the app stopped: installs <apk>, keeping its data; puts the catalogue
#   back; starts the app, which converts its listener on open; and reads the
#   phone's own disk back. The install comes first: if it fails nothing has
#   been put back, and the old app opens the old catalogue as it was. A
#   catalogue that fails to arrive leaves the new app refusing to open,
#   having changed nothing [IMPL-UID-060], and this can be run again.
#
# From Git Bash on the hub, as android/README.md says for spike/:
#   ADB=<platform-tools>/adb.exe PY=python sh android/phone-uids.sh ...
ADB="${ADB:-adb}"; PY="${PY:-python3}"
export MSYS_NO_PATHCONV=1
set -u
# `pwd -W` in Git Bash: MSYS_NO_PATHCONV, above, leaves a /c/... path
# unconverted for a Windows python, which then finds no tools/ (2026-11-25).
HERE=$(cd "$(dirname "$0")/.." && { pwd -W 2>/dev/null || pwd; })
PKG=io.github.mangocats.lempi
STEP="${1:-}"; DEV="${2:-}"; DIR="${3:-}"
[ -n "$STEP" ] && [ -n "$DEV" ] && [ -n "$DIR" ] || { sed -n 6,7p "$0"; exit 2; }
A="$ADB -s $DEV"

die() { echo "STOPPED: $*"; exit 1; }

$A get-state </dev/null >/dev/null 2>&1 || die "no device $DEV (for wireless debugging: $ADB connect <address>:<port>)"
$A shell pm path $PKG </dev/null >/dev/null || die "$PKG is not installed on $DEV"
model=$($A shell getprop ro.product.model </dev/null | tr -d '\r')
ver=$($A shell dumpsys package $PKG </dev/null | sed -n 's/^ *versionName=//p' | head -1 | tr -d '\r')
echo "== $DEV: $model, Lempi $ver"
echo "   assumed: the app's pair is files/library.db and files/listener.db, reached by run-as (a debug build)"

case "$STEP" in
pull)
    [ -e "$DIR/pulled" ] && die "$DIR/pulled exists; a pull is never written over another"
    mkdir -p "$DIR/pulled" "$DIR/work" || die "cannot make $DIR"
    echo "== stopping the app, so its pair holds still"
    $A shell am force-stop $PKG </dev/null
    listing=$($A shell run-as $PKG ls -l files </dev/null | tr -d '\r')
    echo "$listing" | grep -E ' (library|listener)\.db' | sed 's/^/   /'
    for f in library.db library.db-wal library.db-shm listener.db listener.db-wal listener.db-shm; do
        want=$(echo "$listing" | awk -v f="$f" '$NF == f { print $5 }')
        [ -n "$want" ] || continue
        $A exec-out run-as $PKG cat "files/$f" > "$DIR/pulled/$f" </dev/null
        got=$(wc -c < "$DIR/pulled/$f" | tr -d ' ')
        [ "$got" = "$want" ] || die "$f: $got bytes arrived of $want"
        echo "   pulled $f, $got bytes, as the phone lists it"
    done
    [ -s "$DIR/pulled/library.db" ] && [ -s "$DIR/pulled/listener.db" ] || die "the pair did not arrive whole"
    cp "$DIR/pulled/"* "$DIR/work/"
    "$PY" - "$DIR/work" <<'EOF' || die "the copies do not check"
import os, sqlite3, sys
for db in ("library.db", "listener.db"):
    p = os.path.join(sys.argv[1], db)
    c = sqlite3.connect(p)
    ok = c.execute("PRAGMA quick_check").fetchone()[0]
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    c.close()
    for s in ("-wal", "-shm"):
        if os.path.exists(p + s) and os.path.getsize(p + s) == 0:
            os.remove(p + s)
    left = [s for s in ("-wal", "-shm") if os.path.exists(p + s)]
    print(f"   {db}: quick_check {ok}, {mode}, one file{'' if not left else ' NOT: ' + ', '.join(left)}")
    if ok != "ok" or left:
        sys.exit(1)
EOF
    echo "== rehearsal"
    "$PY" "$HERE/tools/phone_uids.py" "$DIR/work/library.db" --listener "$DIR/work/listener.db" \
        || die "the rehearsal did not run; the pull in $DIR stands, and the app is left stopped"
    echo "== the app is left stopped; nothing on $DEV was written"
    ;;
commit)
    N="${4:-}"; APK="${5:-}"
    [ -n "$N" ] && [ -f "$APK" ] || die "commit needs the rehearsal's passage count and the apk"
    [ -f "$DIR/work/library.db" ] || die "no $DIR/work/library.db: pull first"
    "$PY" "$HERE/tools/phone_uids.py" "$DIR/work/library.db" --commit --expect "$N" || die "phone_uids.py refused; nothing on $DEV changed"
    local_sha=$(sha256sum "$DIR/work/library.db" | cut -d' ' -f1)
    echo "== stopping the app"
    $A shell am force-stop $PKG </dev/null
    # The phone must not have moved on since the pull: each file of its pair is
    # still the bytes pulled, and none has appeared. Otherwise the app ran.
    for f in library.db library.db-wal listener.db listener.db-wal; do
        now=$($A exec-out run-as $PKG sh -c "'test -f files/$f && sha256sum files/$f'" </dev/null | cut -d' ' -f1)
        was=$([ -f "$DIR/pulled/$f" ] && sha256sum "$DIR/pulled/$f" | cut -d' ' -f1)
        [ "$now" = "$was" ] || die "the phone's $f changed since the pull (${was:-absent}, now ${now:-absent}): pull again"
    done
    echo "   the phone's pair is the one pulled"
    echo "== installing $APK, keeping the app's data"
    $A install -r "$APK" </dev/null || die "the install failed; the phone keeps its old app and catalogue"
    $A shell am force-stop $PKG </dev/null
    echo "== the catalogue back, as one file"
    $A push "$DIR/work/library.db" /data/local/tmp/lempi-library.db </dev/null | tail -1
    $A shell "run-as $PKG rm -f files/library.db files/library.db-wal files/library.db-shm; cat /data/local/tmp/lempi-library.db | run-as $PKG sh -c 'cat > files/library.db'" </dev/null
    $A shell rm -f /data/local/tmp/lempi-library.db </dev/null
    there=$($A exec-out run-as $PKG sha256sum files/library.db </dev/null | cut -d' ' -f1)
    [ "$there" = "$local_sha" ] || die "the catalogue on the phone is not the one given uids ($there): run commit's push again"
    echo "   files/library.db on the phone: $there, the copy given uids"
    echo "== starting the app (a locked screen keeps it from coming forward)"
    $A shell am start -n $PKG/.MainActivity </dev/null
    sleep 15
    echo "== from the phone's own disk"
    $A exec-out run-as $PKG ls -l files </dev/null | tr -d '\r' | grep -E 'listener|library' | sed 's/^/   /'
    $A exec-out run-as $PKG cat files/lempi.log </dev/null | tr -d '\r' \
        | grep -iE 'uid|convert|refus|error|panic|first start' | tail -20 | sed 's/^/   log: /'
    ver=$($A shell dumpsys package $PKG </dev/null | sed -n 's/^ *versionName=//p' | head -1 | tr -d '\r')
    echo "   installed: Lempi $ver"
    $A exec-out run-as $PKG sh -c "'test -f files/listener.pre-uid.db'" </dev/null \
        || die "no files/listener.pre-uid.db: the listener has not converted (screen locked? read the log above)"
    $A exec-out run-as $PKG cat files/lempi.log </dev/null | grep -q 'converted to passage uids' \
        || die "the log says no conversion, though its copy is there: read the log"
    echo "== converted: the listener names passages by uid, its copy before at files/listener.pre-uid.db"
    ;;
*)
    sed -n 6,7p "$0"; exit 2 ;;
esac
