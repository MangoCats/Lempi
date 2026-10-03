#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# What `lempi02w` is, as a script [PI-SET-090]: the record of the node,
# checked against it, as LempiPlay3/setup-lp3.sh and BosePi/setup-bose.sh are
# for the other two.
#
#     bash LempiPi/setup-lempi02w.sh            # --check: compare, change nothing
#     bash LempiPi/setup-lempi02w.sh --go       # apply
#
#     HOST=pi@other bash LempiPi/setup-lempi02w.sh ...   # default pi@lempi02w
#
# Runs on the development host, over SSH, on appliance/setup-lib.sh, with
# everything every appliance has from appliance/common.sh [APP-SET-020].
#
# **Two halves, unlike the other two.** This node's own part -- its
# instruments and their units, the ACT LED, the boot tuning, WirePlumber 0.4 --
# is LempiPi/setup-appliance.sh, which runs *on* the node and has no check
# mode. --go stages it and runs it there; --check cannot look inside it, and
# says so rather than reporting it as agreed. What --check does compare from
# that half is the unit, which is the line that decides what the player
# opens.
#
# Written 2026-10-02. **--go has not run**: until it does, its staging step
# is the part to watch.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

HOST="${HOST:-pi@lempi02w}"
# The machine's own name. `lempi02w` is what the network calls it; the image
# was named LempiPiHost and nothing has changed it. Recorded as it is.
NAME=LempiPiHost
MODE=check
case "${1:---check}" in
    --check) MODE=check ;;
    --go)    MODE=go ;;
    *) echo "usage: setup-lempi02w.sh [--check|--go]" >&2; exit 2 ;;
esac

SETUP_NAME=setup-lempi02w
. appliance/setup-lib.sh
. appliance/common.sh
. appliance/bluetooth/items.sh

setup_target

# ------------------------------------------------------------------ identity
say ""
say "identity"
item "hostname $NAME" "test \"\$(cat $P/etc/hostname)\" = $NAME" "" \
     "renaming it is a decision, not a fix"

common_items
# Before its own part: the helpers and units the on-node script expects to
# find, and the player's ExecStartPre lempi-wait-sink among them.
bluetooth_items
# Its own Bluetooth piece: WirePlumber 0.4, configured in Lua on bookworm,
# written by setup-appliance.sh [PI3-FOUND-040].
item "WirePlumber 0.4: bluez monitor not tied to seats" \
     "test -f $P/etc/wireplumber/bluetooth.lua.d/51-lempi-no-logind.lua" "" \
     "setup-appliance.sh writes it"

# ------------------------------------------------------------- its own part
say ""
say "its own part (LempiPi/setup-appliance.sh)"
if [ "$MODE" = go ]; then
    # Staged beside the script, as its own header expects: every file it
    # installs from `$HERE`. Then run as root, on the node.
    stage=/tmp/lempi-setup
    on "rm -rf $stage && mkdir -p $stage" || die "could not make $stage on $HOST"
    scp -q LempiPi/setup-appliance.sh LempiPi/lempi-* LempiPi/*.conf "$HOST:$stage/" \
        || die "could not stage LempiPi/ on $HOST"
    on "sudo bash $stage/setup-appliance.sh --no-boot-tune" \
        || differ "setup-appliance.sh" "it reported a failure -- read its output above"
else
    say "  setup-appliance.sh has no check mode; its items are NOT checked here"
fi

# The unit, as setup-appliance.sh writes it: rendered here from that script,
# so this compares against exactly what a run would install [PI-PRE-080].
unit=$(mktemp)
awk '/^read -r -d .. WANT_UNIT <<.EOF. \|\| true$/{f=1;next} f&&/^EOF$/{exit} f' \
    LempiPi/setup-appliance.sh | sed 's/RUNUSER/pi/g; s/RUNUID/1000/g' > "$unit"
[ -s "$unit" ] || die "could not render lempi.service from setup-appliance.sh"
file_item "lempi.service (the whole command line)" "$unit" /etc/systemd/system/lempi.service 644
rm -f "$unit"
# Retired 2026-10-02 with the single-file mode: the drop-in that carried the
# real command line, its pre-migration copy, and the text stub left where the
# pre-split database was.
retired_item "drop-in mpd-guest.conf (retired)" /etc/systemd/system/lempi.service.d/mpd-guest.conf
retired_item "drop-in mpd-guest.conf.pre-cli-migration (retired)" \
    /etc/systemd/system/lempi.service.d/mpd-guest.conf.pre-cli-migration
retired_item "single-file stub /srv/library/lempi.db (retired)" /srv/library/lempi.db
for u in lempi mpd; do
    item "$u enabled" "test -L $P/etc/systemd/system/multi-user.target.wants/$u.service" \
         "sudo systemctl enable $u.service"
done
item "lempi installed" "test -x $P/usr/local/bin/lempi" "" \
     "build/deploy-appliance.sh $HOST"

setup_finish
