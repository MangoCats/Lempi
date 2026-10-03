# SPDX-License-Identifier: MIT
#
# Sourced, not run. What every source host has [FLT-SHP-030]: the Ubuntu
# machines that build Lempi from a checkout and take the Export page's sends
# -- smartboardpc and teacherslounge. The appliances' list is
# appliance/common.sh; almost none of it means anything here (no `pi`, no
# card, no overlay, no /srv/library), so this is its own, and short.
#
# Read off both on 2026-10-02 before it was written. The finding that made it
# worth writing: each kept the fleet clock in a file named for the project's
# earlier name, vaino-fleet.*, written by hand and recorded by no script, so a
# rebuild of either would have lost it.
#
# Needs build/setup-lib.sh sourced and `setup_target` run.

TIMEZONE="${TIMEZONE:-America/New_York}"

# source_host_items CHECKOUT ROLE -- CHECKOUT is the host's Lempi checkout;
# ROLE is `server` for the fleet's time server, `client` for the rest.
source_host_items() {
    local repo="$1" role="$2" sources conf
    say ""
    say "common to every source host (build/source-host-items.sh)"

    # python3 and sqlite3 for Vipunen's remote tooling over ssh; chrony for the
    # fleet clock [GDE-ECHO-300].
    for p in python3 sqlite3 chrony; do
        item "package $p" "dpkg -s $p" \
             "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $p"
    done
    item "timezone $TIMEZONE" \
         "test \"\$(readlink /etc/localtime)\" = /usr/share/zoneinfo/$TIMEZONE" \
         "sudo timedatectl set-timezone $TIMEZONE"

    # The checkout, and what it built. update-source-host.sh pulls, builds and
    # links import_bundle onto the PATH a non-interactive ssh has, where a
    # send looks for it [SPEC-STAR-094]; these say whether it has.
    item "checkout at $repo" "git -C $repo rev-parse --git-dir" "" \
         "clone the repository there"
    item "import_bundle on a non-interactive PATH" "command -v import_bundle" "" \
         "build/update-source-host.sh $HOST $repo"
    item "import_bundle built from the checkout" \
         "import_bundle --version | grep -qF \"\$(git -C $repo rev-parse --short=12 HEAD)\"" "" \
         "build/update-source-host.sh $HOST $repo"

    # The fleet clock [GDE-ECHO-300], rendered here from the templates and
    # fleet/targets.env [SPEC-FCP-030], under the project's own name.
    sources=$(mktemp); conf=$(mktemp)
    if [ "$role" = server ]; then
        build/render-fleet-sources.sh --server "$sources" "$conf" >/dev/null \
            || die "the time server's files could not be rendered"
        file_item "chrony: the fallbacks the fleet's server takes its time from" "$sources" \
            /etc/chrony/sources.d/lempi-fleet.sources 644
        file_item "chrony: serving the fleet's /24, through a WAN outage too" "$conf" \
            /etc/chrony/conf.d/lempi-fleet.conf 644
        retired_item "chrony: vaino-fleet.conf (the earlier name)" /etc/chrony/conf.d/vaino-fleet.conf
    else
        build/render-fleet-sources.sh "$sources" >/dev/null \
            || die "the fleet sources could not be rendered"
        file_item "chrony: the fleet's server preferred" "$sources" \
            /etc/chrony/sources.d/lempi-fleet.sources 644
    fi
    rm -f "$sources" "$conf"
    retired_item "chrony: vaino-fleet.sources (the earlier name)" /etc/chrony/sources.d/vaino-fleet.sources
    # chrony reads these at start, so it must have started after they last
    # changed. The settings in the renamed files are the ones it already
    # runs, so the restart moves nothing; it makes the files the ones in use.
    item "chrony started after its fleet files last changed" \
         "test \"\$(stat -c %Y /etc/chrony/sources.d/lempi-fleet.sources $([ "$role" = server ] && echo /etc/chrony/conf.d/lempi-fleet.conf) | sort -n | tail -1)\" -le \"\$(date -d \"\$(systemctl show chrony -p ActiveEnterTimestamp --value)\" +%s)\"" \
         "sudo systemctl restart chrony"
    item "chrony enabled" "systemctl is-enabled chrony" "sudo systemctl enable chrony"
}
