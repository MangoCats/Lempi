#!/bin/bash
# Install a cross-compiled `import_bundle` on an appliance [SPEC-STAR-090].
#
#     build/install-import-bundle.sh pi@bose
#
# Called by `build/deploy-appliance.sh` after the player; not normally run by
# hand. It exists because nothing installed it: the player build has always
# compiled `import_bundle` beside `lempi`, but only `lempi` (and `fbui`) were
# ever put on an appliance, so a bundle that reached a speaker had nothing to
# import it. Found 2026-10-01, when the Export page's "Send what's missing"
# commands met "import_bundle: command not found" on bose -- and on all three,
# once asked.
#
# The same discipline as `install-fbui.sh`, less the restart -- it is a
# command a person runs, not a service: refuse the wrong architecture; skip
# only when BOTH layers of an overlay root already hold these bytes; upload to
# /tmp and compare checksums; ask the staged copy its version and refuse a
# `+dirty` build; install; persist to the lower layer, and check what landed
# there [IMPL-BOS-185], [GDE-DEP-070].
set -uo pipefail

HOST="${1:-}"
BIN=player/target/aarch64-unknown-linux-gnu/release/import_bundle
REMOTE=/usr/local/bin/import_bundle

die() { echo "deploy-import_bundle: $*" >&2; exit 1; }

[ -n "$HOST" ] || die "usage: $0 <user@host>"
[ -f "$BIN" ] || die "no binary at $BIN -- the player's cross-compile builds it; run that first"
case "$(file -b "$BIN" 2>/dev/null)" in
    *aarch64*) ;;
    *) die "$BIN is not an aarch64 binary" ;;
esac
ssh -o ConnectTimeout=10 "$HOST" true 2>/dev/null || die "$HOST is not reachable"

LOWER=""
if [ "$(ssh "$HOST" "findmnt -no FSTYPE /" 2>/dev/null)" = "overlay" ]; then
    LOWER=$(ssh "$HOST" "findmnt -no OPTIONS / | tr ',' '\n' | sed -n 's/^lowerdir=//p'" 2>/dev/null)
    [ -n "$LOWER" ] || die "overlay root on $HOST but no lowerdir found -- refusing to deploy blind"
    echo "deploy-import_bundle: $HOST has an overlay root; will persist through $LOWER"
else
    echo "deploy-import_bundle: $HOST has a plain root; $REMOTE is itself the durable copy"
fi

LOCAL_SUM=$(md5sum "$BIN" | cut -d' ' -f1)
[ -n "$LOCAL_SUM" ] || die "local checksum is empty -- refusing to verify an upload against nothing"
REMOTE_SUM=$(ssh "$HOST" "md5sum $REMOTE 2>/dev/null | cut -d' ' -f1")
PERSIST_SUM=""
[ -n "$LOWER" ] && PERSIST_SUM=$(ssh "$HOST" "sudo md5sum $LOWER$REMOTE 2>/dev/null | cut -d' ' -f1")
if [ "$LOCAL_SUM" = "$REMOTE_SUM" ] && { [ -z "$LOWER" ] || [ "$LOCAL_SUM" = "$PERSIST_SUM" ]; }; then
    echo "deploy-import_bundle: already this build ($LOCAL_SUM)"
    exit 0
fi
echo "deploy-import_bundle: ${REMOTE_SUM:-absent} -> $LOCAL_SUM"

scp -q "$BIN" "$HOST:/tmp/import_bundle.new" || die "upload failed"
UPLOADED=$(ssh "$HOST" "md5sum /tmp/import_bundle.new 2>/dev/null | cut -d' ' -f1")
[ "$UPLOADED" = "$LOCAL_SUM" ] || die "uploaded binary does not match (${UPLOADED:-nothing} != $LOCAL_SUM); not installing"

ssh "$HOST" "chmod 755 /tmp/import_bundle.new" || die "could not make the staged binary executable"
STAGED_VER=$(ssh "$HOST" "/tmp/import_bundle.new --version 2>/dev/null" | head -1)
case "$STAGED_VER" in
    *+dirty*)
        if [ "${ALLOW_DIRTY:-}" != "1" ]; then
            ssh "$HOST" "rm -f /tmp/import_bundle.new"
            die "refusing to install a dirty build ($STAGED_VER) on $HOST -- commit first, or re-run with ALLOW_DIRTY=1"
        fi
        echo "deploy-import_bundle: WARNING -- installing a DIRTY build ($STAGED_VER) because ALLOW_DIRTY=1" >&2 ;;
    "") ssh "$HOST" "rm -f /tmp/import_bundle.new"
        die "the staged binary reports no version -- it did not run; not installing" ;;
    *)  echo "deploy-import_bundle: staged binary reports $STAGED_VER" ;;
esac

ssh "$HOST" "sudo install -m 755 /tmp/import_bundle.new $REMOTE" || die "install failed"
INSTALLED=$(ssh "$HOST" "md5sum $REMOTE 2>/dev/null | cut -d' ' -f1")
[ "$INSTALLED" = "$LOCAL_SUM" ] || die "installed copy is ${INSTALLED:-absent}, expected $LOCAL_SUM"

if [ -n "$LOWER" ]; then
    ssh "$HOST" "sudo mount -o remount,rw $LOWER \
        && sudo install -m 755 /tmp/import_bundle.new $LOWER$REMOTE \
        && sudo sync" || die "could not write $LOWER$REMOTE -- this install is RAM-only"
    PERSIST_SUM=$(ssh "$HOST" "sudo md5sum $LOWER$REMOTE 2>/dev/null | cut -d' ' -f1")
    [ "$PERSIST_SUM" = "$LOCAL_SUM" ] || die "persisted copy is ${PERSIST_SUM:-absent}, expected $LOCAL_SUM"
    echo "deploy-import_bundle: persisted to $LOWER$REMOTE ($PERSIST_SUM)"
    if ssh "$HOST" "sudo mount -o remount,ro $LOWER" 2>/dev/null; then
        echo "deploy-import_bundle: $LOWER returned to read-only"
    else
        echo "deploy-import_bundle: WARNING -- $LOWER left read-write; it returns to ro on the next reboot" >&2
    fi
fi
ssh "$HOST" "rm -f /tmp/import_bundle.new"
echo "deploy-import_bundle: installed${LOWER:+ and persisted} -- $REMOTE"
exit 0
