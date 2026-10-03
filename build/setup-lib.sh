# SPDX-License-Identifier: MIT
#
# Sourced, not run. The engine every appliance's setup script is built on
# [APP-SET-010]: it runs on the development host and reaches the node over
# SSH, so a node is checked and changed by the same lines.
#
#     --check   compare the node with the repository; change nothing (default)
#     --go      apply what differs -- on an overlay root, to BOTH layers
#
# Taken from LempiPlay3/setup-lp3.sh, where it was first written, on
# 2026-10-02, when the maintainer asked that the three appliances be set up
# the same way wherever a difference bought nothing.
#
# **--go on an overlay root, since the same evening** [APP-SET-030]. An
# ordinary write to / there lands in a tmpfs and is gone at the next reboot
# [IMPL-BOS-185], so every change is made twice: once live, for what runs
# now, and once on the durable layer, for what runs after a reboot -- the
# way build/install-config.sh writes one file, and verified the same way, by
# reading the durable copy back [GDE-DEP-070]. The maintainer asked for it
# after lempi02w's --go, so that bose and lp3-wifi could be brought level by
# one command each, not a file at a time.
#
# **Every machine that plays, since the same evening** [FLT-SHP-030]: moved
# here from appliance/ when smartboardpc and teacherslounge were set up on it
# too, since it is no longer an appliance's alone.
#
# A caller sets HOST and MODE, sources this, and calls `setup_target` before
# any item and `setup_finish` after the last. It must have `cd`'d to the
# repository root: file items name their local copy by path from there.

DIFFER=0
CHANGED=0
on()    { ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" "$@"; }
say()   { printf '%s\n' "$*"; }
ok()    { printf '  %-58s ok\n' "$1"; }
did()   { CHANGED=$((CHANGED + 1)); printf '  %-58s CHANGED\n' "$1"; }
differ(){ DIFFER=$((DIFFER + 1)); printf '  %-58s DIFFERS%s\n' "$1" "${2:+ -- $2}"; }
die()   { printf '%s: %s\n' "${SETUP_NAME:-setup}" "$*" >&2; exit 1; }

# setup_target -- reach the node and say what it is before acting on it
# [GDE-DEP-060]. On an overlay root the durable files are under the overlay's
# lower directory, read from the mount itself rather than assumed, and those
# are what is checked [GDE-DEP-070]. Sets P (the durable layer's prefix, ""
# on a writable root), ROOTFS and CODENAME.
setup_target() {
    say "${SETUP_NAME:-setup} $MODE against $HOST"
    on true || die "$HOST is not reachable; nothing was checked"
    ROOTFS=$(on "findmnt -no FSTYPE /")
    CODENAME=$(on ". /etc/os-release && echo \$VERSION_CODENAME")
    if [ "$ROOTFS" = overlay ]; then
        P=$(on "findmnt -no OPTIONS / | tr ',' '\n' | sed -n 's/^lowerdir=//p'")
        [ -n "$P" ] || die "$HOST has an overlay root but no lowerdir -- refusing to go on blind"
        if [ "$MODE" = check ]; then
            say "$HOST has an OVERLAY root: checking the durable layer under $P"
        else
            say "$HOST has an OVERLAY root: every change goes live AND to the durable layer under $P"
        fi
    else
        P=""
        say "$HOST has a writable root ($ROOTFS): an ordinary write is durable"
    fi
    say "$HOST runs $CODENAME"
}

# durable CMD -- run CMD (a shell command, on the node, as root) against the
# durable layer: remounted read-write first, every time, because
# overlayroot-chroot puts it back read-only when it leaves. Nothing on a
# writable root.
durable() {
    [ -n "$P" ] || return 0
    on "sudo mount -o remount,rw '$P' && $1 && sudo sync"
}

# item NAME CHECK [APPLY] [HINT] -- CHECK and APPLY are commands run on the
# node. With no APPLY, a difference is reported with HINT and never fixed.
# On an overlay root APPLY runs live, then again inside overlayroot-chroot,
# which is the durable layer as a root of its own [IMPL-BOS-180]; CHECK reads
# the durable layer, so it is what decides whether the item took.
item() {
    if on "$2" >/dev/null 2>&1; then ok "$1"; return; fi
    if [ "$MODE" = go ] && [ -n "${3:-}" ]; then
        if apply_both "$3" && on "$2" >/dev/null 2>&1; then did "$1"
        else differ "$1" "apply failed"; fi
    else
        differ "$1" "${4:-}"
    fi
}

# state_item -- item, for what lives on STATE or LIBRARY rather than on the
# root: pi's home, /etc/ssh and /var/log (bound onto STATE on an overlay
# node), and the data trees themselves. A live write there is already
# durable, and the same command run again in the chroot would act on the
# empty directories the binds cover.
state_item() {
    _LIVE_ONLY=1
    item "$@"
    _LIVE_ONLY=""
}

# apply_both CMD -- run CMD live and, on an overlay root, in the durable
# layer. The command goes over as a file, not inside a quoted string: it
# carries quotes of its own, and a quoting mistake usually still runs.
apply_both() {
    printf '%s\n' "$1" | on "cat > /tmp/lempi-setup-apply.sh" || return 1
    on "sh /tmp/lempi-setup-apply.sh" >/dev/null 2>&1 || { on "rm -f /tmp/lempi-setup-apply.sh"; return 1; }
    if [ -n "$P" ] && [ -z "${_LIVE_ONLY:-}" ]; then
        # The chroot's copy runs as root already, so `sudo` in it is made a
        # no-op rather than trusted to work inside a chroot.
        printf 'sudo() { "$@"; }\n%s\n' "$1" | on "cat > /tmp/lempi-setup-apply-root.sh" \
            && durable "sudo cp /tmp/lempi-setup-apply-root.sh '$P/tmp/lempi-setup-apply.sh'" \
            && on "sudo overlayroot-chroot sh /tmp/lempi-setup-apply.sh" >/dev/null 2>&1 \
            || { on "rm -f /tmp/lempi-setup-apply.sh /tmp/lempi-setup-apply-root.sh"
                 durable "sudo rm -f '$P/tmp/lempi-setup-apply.sh'" >/dev/null 2>&1
                 return 1; }
        durable "sudo rm -f '$P/tmp/lempi-setup-apply.sh'" >/dev/null 2>&1
    fi
    on "rm -f /tmp/lempi-setup-apply.sh /tmp/lempi-setup-apply-root.sh"
}

# place LOCAL REMOTE MODE [visudo] -- put a file on the node, live and, on an
# overlay root, durable. With "visudo" the staged copy is checked first, and
# nothing is placed if it fails: a malformed sudoers file takes sudo away.
place() {
    local stage=/tmp/lempi-setup.part
    scp -q "$1" "$HOST:$stage" || return 1
    # Owned and moded as it will be placed before anything reads it: visudo
    # refuses a sudoers file that is not root's and 440, whatever it says.
    on "sudo chown root:root $stage && sudo chmod $3 $stage" || { on "rm -f $stage"; return 1; }
    if [ "${4:-}" = visudo ] && ! on "sudo visudo -cf $stage >/dev/null"; then
        on "sudo rm -f $stage"; return 1
    fi
    on "sudo install -D -o root -g root -m $3 $stage '$2'" || { on "sudo rm -f $stage"; return 1; }
    durable "sudo install -D -o root -g root -m $3 $stage '$P$2'" || { on "sudo rm -f $stage"; return 1; }
    on "sudo rm -f $stage"
}

# file_item NAME LOCAL REMOTE MODE [visudo] -- the node's file must be this
# file, byte for byte. A copy that differs only in its comments still
# differs: what is on the node is either the repository's file or it is not.
# After --go the durable copy is read back: only that is evidence.
file_item() {
    local want have
    [ -f "$2" ] || die "$2 missing from the repository"
    want=$(md5sum < "$2" | cut -c1-32)
    # The file is md5sum's argument, not a redirect: a redirect is opened by
    # the unprivileged shell, and bose's /etc/mpd.conf is 640 root.
    have=$(on "sudo test -f '$P$3' && sudo md5sum '$P$3' | cut -c1-32")
    if [ "$want" = "$have" ]; then ok "$1"; return; fi
    if [ "$MODE" = go ]; then
        if place "$2" "$3" "$4" "${5:-}" \
           && [ "$(on "sudo md5sum '$P$3' | cut -c1-32")" = "$want" ]; then
            did "$1"
        else
            differ "$1" "apply failed${5:+ (or $5 refused it)}"
        fi
    elif [ -z "$have" ]; then
        differ "$1" "absent"
    else
        differ "$1" "not this repository's file"
    fi
}

# user_file_item NAME LOCAL REMOTE MODE [AFTER] -- file_item for a file in
# the SSH user's own home: REMOTE is relative to it, and the file is the
# user's, not root's -- a systemd user unit, say. AFTER, if given, runs on
# the node once the file is placed (a `systemctl --user daemon-reload`).
user_file_item() {
    local want have
    [ -f "$2" ] || die "$2 missing from the repository"
    want=$(md5sum < "$2" | cut -c1-32)
    have=$(on "test -f '$3' && md5sum '$3' | cut -c1-32")
    if [ "$want" = "$have" ]; then ok "$1"; return; fi
    if [ "$MODE" = go ]; then
        if scp -q "$2" "$HOST:/tmp/lempi-setup.part" \
           && on "mkdir -p \"\$(dirname '$3')\" && install -m $4 /tmp/lempi-setup.part '$3' && rm -f /tmp/lempi-setup.part" \
           && { [ -z "${5:-}" ] || on "$5"; } \
           && [ "$(on "md5sum '$3' | cut -c1-32")" = "$want" ]; then
            did "$1"
        else
            differ "$1" "apply failed"
        fi
    elif [ -z "$have" ]; then
        differ "$1" "absent"
    else
        differ "$1" "not this repository's file"
    fi
}

# retired_item NAME REMOTE -- a file the repository no longer installs must
# be gone, or it goes on taking effect beside its replacement. Removed from
# both layers on an overlay root; checked on the durable one.
retired_item() {
    if on "! test -e '$P$2'"; then ok "$1"; return; fi
    if [ "$MODE" = go ]; then
        if on "sudo rm -f '$2'" && durable "sudo rm -f '$P$2'" && on "! test -e '$P$2'"; then
            did "$1"
        else
            differ "$1" "apply failed"
        fi
    else
        differ "$1" "retired, still present"
    fi
}

# setup_finish -- after --go, reload systemd for any unit that changed and
# put the durable layer back read-only; then the summary, and the exit status
# that matches it.
setup_finish() {
    if [ "$MODE" = go ] && [ "$CHANGED" -gt 0 ]; then
        say ""
        if on "sudo systemctl daemon-reload"; then
            say "  systemd reloaded"
        else
            differ "systemd reload" "failed"
        fi
        say "  the journal's settings take effect when journald next starts, at the latest the next boot"
    fi
    if [ "$MODE" = go ] && [ -n "$P" ]; then
        # Best effort, as build/install-config.sh's: an overlay holds its own
        # lower layer, and the remount can be refused as busy. Said, either way.
        if on "sudo mount -o remount,ro '$P'" 2>/dev/null; then
            say "  $P returned to read-only"
        else
            say "  WARNING: $P left read-write; it returns to read-only at the next reboot"
        fi
    fi
    say ""
    if [ "$DIFFER" -eq 0 ]; then
        say "All items as recorded."
    else
        say "$DIFFER item(s) differ from this script."
    fi
    [ "$DIFFER" -eq 0 ]
}
