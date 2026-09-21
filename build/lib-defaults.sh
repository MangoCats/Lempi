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
