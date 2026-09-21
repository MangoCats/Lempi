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
# name costs. `deploy-appliance.sh`, `install-player.sh` and
# `deploy-everywhere.sh` each carried `pi@lempipi` as a default target for a
# name that has never resolved: the node answered to one name before the
# project was renamed and to `lempi02w` after, and `lempipi` was only ever the
# name the documentation used. Three copies meant nothing noticed.
#
# The names live here now, once, and every one of them is overridable. A second
# machine, a test rig, or a node reached by address instead of name needs an
# environment variable rather than an edit:
#
#     LEMPI_APPLIANCE=pi@192.168.67.20 build/deploy-appliance.sh
#     LEMPI_FLEET="pi@bose" build/deploy-everywhere.sh
#
# `lempipi` remains what the prose calls the first appliance, and that is fine:
# a document naming a machine is a label, not a connection. Only these values
# are dialled.

# The default single target: the Pi Zero 2W with the Bluetooth speaker.
lempi_appliance() {
    echo "${LEMPI_APPLIANCE:-pi@lempi02w}"
}

# Every appliance a fleet-wide deploy touches, in the order it should touch
# them. `bose` is second because its overlay root makes it the most likely to
# teach something, and the framebuffer node is last because it is the newest.
lempi_fleet() {
    echo "${LEMPI_FLEET:-pi@lempi02w pi@bose pi@lp3-wifi}"
}
