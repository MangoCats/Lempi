#!/bin/sh
# Running a player by hand, on a laptop or a desktop. Generic example.
#
# No systemd, no unit file: the whole configuration is the command line and
# the shell. 13491 is not the default port.
set -eu

ROOT=${1:-"$HOME/music"}

# Layer 3 for this shell only. Everything started from here sees it.
export LEMPI_PORT=13491

# Layer 1 beats it, so this run serves 13492 whatever the export says --
# and the player prints which layer answered, so that is visible rather
# than puzzling [GOV-SRC-040]:
#
#   lempi: --port = 13492, from the command line
#
exec lempi \
    --listener "$ROOT/listener.db" \
    --library  "$ROOT/library.db" \
    --port     13492 \
    --depth    5
