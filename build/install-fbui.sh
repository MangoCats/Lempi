#!/bin/bash
# Install a cross-compiled `fbui` on an appliance that runs `fbui.service`
# [GDE-DEP-120], the framebuffer screen's own program [SPEC-FBUI-010].
#
#     build/install-fbui.sh pi@lp3-wifi
#
# Called by `build/deploy-appliance.sh` after the player, for a node that has
# the unit; not normally run by hand. It exists because `fbui` drifted: it is
# a separate binary behind the `fbui` feature, `install-player.sh` installs
# only the player, and nothing built or installed it -- lp3-wifi ran a build
# from 2026-09-21, the previous repository's, six days behind a fleet that was
# otherwise current, with no check reporting it.
#
# The same discipline as `install-player.sh`, step for step: refuse the wrong
# architecture; skip only when BOTH layers of an overlay root already hold
# these bytes; upload to /tmp and compare checksums; ask the staged copy its
# version and refuse a `+dirty` build; keep the outgoing binary; persist to
# the lower layer ONLY once the new build has shown it works, so a bad build
# never becomes the one the node boots into [IMPL-BOS-185].
#
# **What "works" means here.** `fbui` has no port to ask. It works when its
# unit is active and it has logged "fbui: connected" to the player's
# WebSocket SINCE this restart -- read from the journal from the restart's own
# timestamp, so a line from the previous process cannot pass for this one.
# Playback is never touched: `fbui` is a separate process precisely so that it
# can fail without stopping the music [SPEC-FBUI-010]. What a restart costs is
# the panel blanking for a moment.
set -uo pipefail

HOST="${1:-}"
BIN=player/target/aarch64-unknown-linux-gnu/release/fbui
REMOTE=/usr/local/bin/fbui
UNIT=fbui.service

die() { echo "deploy-fbui: $*" >&2; exit 1; }

[ -n "$HOST" ] || die "usage: $0 <user@host>"
[ -f "$BIN" ] || die "no binary at $BIN -- cross-compile it with --features fbui --bin fbui first"
case "$(file -b "$BIN" 2>/dev/null)" in
    *aarch64*) ;;
    *) die "$BIN is not an aarch64 binary" ;;
esac
ssh -o ConnectTimeout=10 "$HOST" true 2>/dev/null || die "$HOST is not reachable"

LOWER=""
if [ "$(ssh "$HOST" "findmnt -no FSTYPE /" 2>/dev/null)" = "overlay" ]; then
    LOWER=$(ssh "$HOST" "findmnt -no OPTIONS / | tr ',' '\n' | sed -n 's/^lowerdir=//p'" 2>/dev/null)
    [ -n "$LOWER" ] || die "overlay root on $HOST but no lowerdir found -- refusing to deploy blind"
    echo "deploy-fbui: $HOST has an overlay root; will persist through $LOWER"
else
    echo "deploy-fbui: $HOST has a plain root; $REMOTE is itself the durable copy"
fi

LOCAL_SUM=$(md5sum "$BIN" | cut -d' ' -f1)
[ -n "$LOCAL_SUM" ] || die "local checksum is empty -- refusing to verify an upload against nothing"
REMOTE_SUM=$(ssh "$HOST" "md5sum $REMOTE 2>/dev/null | cut -d' ' -f1")
PERSIST_SUM=""
[ -n "$LOWER" ] && PERSIST_SUM=$(ssh "$HOST" "sudo md5sum $LOWER$REMOTE 2>/dev/null | cut -d' ' -f1")
if [ "$LOCAL_SUM" = "$REMOTE_SUM" ] && { [ -z "$LOWER" ] || [ "$LOCAL_SUM" = "$PERSIST_SUM" ]; }; then
    echo "deploy-fbui: already running this build ($LOCAL_SUM)"
    exit 0
fi
echo "deploy-fbui: ${REMOTE_SUM:-absent} -> $LOCAL_SUM"

scp -q "$BIN" "$HOST:/tmp/fbui.new" || die "upload failed"
UPLOADED=$(ssh "$HOST" "md5sum /tmp/fbui.new 2>/dev/null | cut -d' ' -f1")
[ "$UPLOADED" = "$LOCAL_SUM" ] || die "uploaded binary does not match (${UPLOADED:-nothing} != $LOCAL_SUM); not installing"

ssh "$HOST" "chmod 755 /tmp/fbui.new" || die "could not make the staged binary executable"
STAGED_VER=$(ssh "$HOST" "/tmp/fbui.new --version 2>/dev/null" | head -1)
case "$STAGED_VER" in
    *+dirty*)
        if [ "${ALLOW_DIRTY:-}" != "1" ]; then
            ssh "$HOST" "rm -f /tmp/fbui.new"
            die "refusing to install a dirty build ($STAGED_VER) on $HOST -- commit first, or re-run with ALLOW_DIRTY=1"
        fi
        echo "deploy-fbui: WARNING -- installing a DIRTY build ($STAGED_VER) because ALLOW_DIRTY=1" >&2 ;;
    "") ssh "$HOST" "rm -f /tmp/fbui.new"
        die "the staged binary reports no version -- it did not run; not installing" ;;
    *)  echo "deploy-fbui: staged binary reports $STAGED_VER" ;;
esac

# The restart's own moment, on the host's clock, so the journal is read from
# here and not from any earlier "connected".
SINCE=$(ssh "$HOST" "date +%s") || die "could not read $HOST's clock"
ssh "$HOST" "sudo cp -f $REMOTE ${REMOTE}.prev 2>/dev/null;
             sudo systemctl stop $UNIT;
             sudo install -m 755 /tmp/fbui.new $REMOTE;
             sudo systemctl start $UNIT" || die "install failed"

DEADLINE=${LEMPI_DEPLOY_WAIT:-60}
ok=""
for _ in $(seq 1 "$DEADLINE"); do
    if ssh "$HOST" "systemctl is-active --quiet $UNIT && \
            journalctl -u $UNIT --since @$SINCE --no-pager -o cat 2>/dev/null | grep -q '^fbui: connected'"; then
        ok=1; break
    fi
    sleep 1
done
if [ -z "$ok" ]; then
    echo "deploy-fbui: new build did not connect to the player within ${DEADLINE}s; rolling back" >&2
    ssh "$HOST" "journalctl -u $UNIT --since @$SINCE --no-pager -o cat | tail -5" >&2
    ssh "$HOST" "sudo systemctl stop $UNIT;
                 sudo install -m 755 ${REMOTE}.prev $REMOTE;
                 sudo systemctl start $UNIT"
    die "rolled back to the previous fbui"
fi
echo "deploy-fbui: running, and connected to the player"

# Persist only now [IMPL-BOS-185], and check what landed [GDE-DEP-070].
if [ -n "$LOWER" ]; then
    ssh "$HOST" "sudo mount -o remount,rw $LOWER \
        && sudo install -m 755 /tmp/fbui.new $LOWER$REMOTE \
        && sudo sync" || die "could not write $LOWER$REMOTE -- this deploy is RAM-only"
    PERSIST_SUM=$(ssh "$HOST" "sudo md5sum $LOWER$REMOTE 2>/dev/null | cut -d' ' -f1")
    [ "$PERSIST_SUM" = "$LOCAL_SUM" ] || die "persisted copy is ${PERSIST_SUM:-absent}, expected $LOCAL_SUM"
    echo "deploy-fbui: persisted to $LOWER$REMOTE ($PERSIST_SUM)"
    if ssh "$HOST" "sudo mount -o remount,ro $LOWER" 2>/dev/null; then
        echo "deploy-fbui: $LOWER returned to read-only"
    else
        echo "deploy-fbui: WARNING -- $LOWER left read-write; it returns to ro on the next reboot" >&2
    fi
fi
ssh "$HOST" "rm -f /tmp/fbui.new"
exit 0
