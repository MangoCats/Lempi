# SPDX-License-Identifier: MIT
#
# Sourced, not run. The engine every appliance's setup script is built on
# [APP-SET-010]: it runs on the development host and reaches the node over
# SSH, so a node is checked and changed by the same lines.
#
#     --check   compare the node with the repository; change nothing (default)
#     --go      apply what differs, on a node with a writable root
#
# Taken from LempiPlay3/setup-lp3.sh, where it was first written, on
# 2026-10-02, when the maintainer asked that the three appliances be set up
# the same way wherever a difference bought nothing.
#
# A caller sets HOST and MODE, sources this, and calls `setup_target` before
# any item. It must have `cd`'d to the repository root: file items name their
# local copy by path from there.

DIFFER=0
on()    { ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" "$@"; }
say()   { printf '%s\n' "$*"; }
ok()    { printf '  %-58s ok\n' "$1"; }
did()   { printf '  %-58s CHANGED\n' "$1"; }
differ(){ DIFFER=$((DIFFER + 1)); printf '  %-58s DIFFERS%s\n' "$1" "${2:+ -- $2}"; }
die()   { printf '%s: %s\n' "${SETUP_NAME:-setup}" "$*" >&2; exit 1; }

# setup_target -- reach the node and say what it is before acting on it
# [GDE-DEP-060]. On an overlay root the durable files are under
# /media/root-ro, and those are what is checked [GDE-DEP-070]: a write to /
# there would vanish at the next reboot [IMPL-BOS-185], so --go refuses.
# Sets P (the durable layer's prefix, "" on a writable root), ROOTFS and
# CODENAME.
setup_target() {
    say "${SETUP_NAME:-setup} $MODE against $HOST"
    on true || die "$HOST is not reachable; nothing was checked"
    ROOTFS=$(on "findmnt -no FSTYPE /")
    CODENAME=$(on ". /etc/os-release && echo \$VERSION_CODENAME")
    if [ "$ROOTFS" = overlay ]; then
        P=/media/root-ro
        say "$HOST has an OVERLAY root: checking the durable layer under $P"
        [ "$MODE" = check ] || die "--$MODE needs a writable root. On a locked card use --check, and build/install-config.sh for a single file [LP3-SET-020]."
    else
        P=""
        say "$HOST has a writable root ($ROOTFS): an ordinary write is durable"
    fi
    say "$HOST runs $CODENAME"
}

# item NAME CHECK [APPLY] [HINT] -- CHECK and APPLY are commands run on the
# node. With no APPLY, a difference is reported with HINT and never fixed.
item() {
    if on "$2" >/dev/null 2>&1; then ok "$1"; return; fi
    if [ "$MODE" = go ] && [ -n "${3:-}" ]; then
        if on "$3" >/dev/null 2>&1 && on "$2" >/dev/null 2>&1; then did "$1"
        else differ "$1" "apply failed"; fi
    else
        differ "$1" "${4:-}"
    fi
}

# file_item NAME LOCAL REMOTE MODE -- the node's file must be this file, byte
# for byte. A copy that differs only in its comments still differs: what is
# on the node is either the repository's file or it is not.
file_item() {
    local want have
    [ -f "$2" ] || die "$2 missing from the repository"
    want=$(md5sum < "$2" | cut -c1-32)
    # The file is md5sum's argument, not a redirect: a redirect is opened by
    # the unprivileged shell, and bose's /etc/mpd.conf is 640 root.
    have=$(on "sudo test -f '$P$3' && sudo md5sum '$P$3' | cut -c1-32")
    if [ "$want" = "$have" ]; then ok "$1"; return; fi
    if [ "$MODE" = go ]; then
        if scp -q "$2" "$HOST:/tmp/lempi-setup.part" \
           && on "sudo install -D -m $4 /tmp/lempi-setup.part '$3' && rm -f /tmp/lempi-setup.part"; then
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
# be gone, or it goes on taking effect beside its replacement.
retired_item() {
    item "$1" "! test -e '$P$2'" "sudo rm -f '$2'" "retired, still present"
}

# setup_finish -- the summary, and the exit status that matches it.
setup_finish() {
    say ""
    if [ "$DIFFER" -eq 0 ]; then
        say "All items as recorded."
    else
        say "$DIFFER item(s) differ from this script."
    fi
    [ "$DIFFER" -eq 0 ]
}
