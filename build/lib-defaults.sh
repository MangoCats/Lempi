# Values the shell side needs that the player defines in Rust.
#
# Sourced, never run. `. "$(dirname "$0")/lib-defaults.sh"`.
#
# **This is a mirror, and it is guarded** `[GDE-CLI-100]`. The definition of
# the default port is `default_port!` in `player/src/cli.rs`; a shell script
# cannot read a Rust macro, so the number is written once more here and the
# crate's own test `the_default_port_is_written_once_in_the_source` checks
# that this file agrees with it. Three scripts used to carry their own copy;
# now they carry none.
#
# Nothing here is a policy. To move the port, set `LEMPI_PORT` or pass
# `--port`: both outrank this `[GDE-CLI-090]`.
LEMPI_DEFAULT_PORT=5720

# What the player will actually serve on, as far as a script can know before
# it starts one: the environment if it is set, otherwise the built-in.
# A script that can read the player's own startup line should prefer that --
# it reports the port actually bound, which this cannot.
lempi_port() {
    # This carried a retired variable name as a middle term for as long as a
    # node's environment might still have exported it. Nothing does now, and it
    # is gone. If a name is ever retired again, the rule that applied to it
    # applies again: the retired spelling is never rewritten by a rename pass,
    # because collapsing `${NEW:-${OLD:-...}}` to `${NEW:-${NEW:-...}}` reads as
    # working and silently drops the fallback.
    echo "${LEMPI_PORT:-$LEMPI_DEFAULT_PORT}"
}

# ---- who the fleet is -------------------------------------------------------
#
# These were hardcoded in three scripts, and it cost exactly what hardcoding a
# name costs: each carried the same default target, for a host name that has
# never resolved on this network -- confirmed 2026-09-21 -- and three copies
# meant nothing noticed. The name itself is gone from the tree; the appliance
# is `lempi02w`, which is a DHCP reservation and does resolve.
#
# The roster does NOT live here. `[GDE-ARC-033]` already settled where a real
# hostname belongs -- `fleet/targets.env`, gitignored, this household's actual
# kit -- with `fleet-example/targets.env` tracked and deliberately generic, and
# `tools/console.py` reading whichever exists. Putting real machine names in
# tracked code is wrong for anyone else's fleet and wrong for this one the day
# a host is renamed, which is the day that just happened.
#
# So this reads the same two files in the same precedence the console uses:
# generic first, real wins. With no `fleet/targets.env` present a deploy aims
# at `pi@speaker-a`, which fails immediately and obviously, rather than at a
# real machine that might be the wrong one.
#
# An environment variable still outranks both `[GDE-CLI-090]`:
#
#     LEMPI_APPLIANCE=pi@192.168.67.20 build/deploy-appliance.sh
#     LEMPI_FLEET="pi@bose" build/deploy-everywhere.sh

# Where the roster lives. Tried in order, first one that actually holds
# `fleet-example/targets.env` wins.
#
# This is deliberately not just `$(dirname "$0")/..`. Sourced from a `build/`
# script that is how you reach the repository root, but sourced any other way
# -- `sh -c`, a subshell, a test harness -- `$0` is the shell rather than the
# script and the path silently resolves somewhere with no roster in it. The
# function would then return nothing and the caller would fall back to the
# generic names, which is a plausible-looking wrong answer rather than an
# error: a deploy aimed at `pi@speaker-a` while a real roster sat unread.
lempi_root() {
    for _r in "${REPO_ROOT:-}" "${ROOT:-}" "$(dirname "$0")/.." "$PWD" "$PWD/.."; do
        [ -n "$_r" ] || continue
        [ -f "$_r/fleet-example/targets.env" ] && { echo "$_r"; return 0; }
    done
    return 1
}

# Read one key out of the roster, generic first so the real file wins.
lempi_target() {
    _k=$1; _v=""
    _root=$(lempi_root) || { echo ""; return 0; }
    for _d in fleet-example fleet; do
        _f="$_root/$_d/targets.env"
        [ -f "$_f" ] || continue
        _line=$(grep -E "^${_k}=" "$_f" 2>/dev/null | tail -1)
        # `tr -d '\r'`, because `fleet-example/targets.env` is a tracked file
        # in a repository that is checked out on Windows. Without a
        # `.gitattributes` rule it arrives CRLF, the carriage return sits at
        # the END of the value, and `${_line#*=}` keeps it -- so the ssh
        # target becomes `pi@speaker-a\r`, which fails with a message naming
        # a host that looks correct. The rule now exists; this does not
        # depend on it, because a roster can also be hand-written or copied
        # in from anywhere.
        [ -n "$_line" ] && _v=$(printf '%s' "${_line#*=}" | tr -d '\r')
    done
    echo "$_v"
}

# The default single target: the appliance a plain `deploy-appliance.sh` means.
lempi_appliance() {
    _t=$(lempi_target LEMPI_APPLIANCE)
    echo "${LEMPI_APPLIANCE:-${_t:-pi@speaker-a}}"
}

# Every appliance a fleet-wide deploy touches, in the order it should touch
# them -- the overlay-rooted ones before the newest, so a surprise lands where
# there is most prior art.
lempi_fleet() {
    _t=$(lempi_target LEMPI_FLEET)
    echo "${LEMPI_FLEET:-${_t:-pi@speaker-a pi@speaker-b}}"
}
