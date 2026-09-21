#!/bin/bash
# SPDX-License-Identifier: MIT
#
# MOVED 2026-09-11 to build/deploy-appliance.sh `[GDE-DEP-095]`.
#
# This script and `deploy-lempipi.sh` were two entry points to one job that
# overlapped in the no-ref case and diverged everywhere else -- different
# guards, different cargo features, and after `[IMPL-BOS-185]` a correctness
# fix that landed in one and not the other. They are one script now.
#
# Kept as a forwarder rather than deleted, because the old path is named in
# HOWTO.md, BOSE008 and BOSE009, and in whatever anyone has in their shell
# history. It passes every argument through unchanged, so `deploy.sh <tag>`
# and `deploy.sh <tag> pi@host` still mean what they meant.
#
# It also stops being generic tooling that lives in one machine's folder,
# which is `[GDE-DEP-040]`'s whole point: `LempiPi/` should hold material
# about lempipi, and a script that deploys to any appliance never did.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL="$(cd "$HERE/.." && pwd)/build/deploy-appliance.sh"

[ -x "$REAL" ] || [ -f "$REAL" ] || {
    echo "deploy: build/deploy-appliance.sh is missing -- this forwarder has nothing to call" >&2
    exit 1
}

echo "deploy: LempiPi/deploy.sh has moved to build/deploy-appliance.sh; forwarding" >&2
exec bash "$REAL" "$@"
