#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Write the fleet's chrony sources file, with the fleet's own time server in
# it, to OUT `[SPEC-FCP-030]`, `[GDE-ECHO-300]`.
#
#     build/render-fleet-sources.sh OUT
#
# The address is a household's own, so the tracked file is a template
# (`build/lempi-fleet.sources.in`) and the value lives in `fleet/targets.env`
# as `NTP_SERVER` -- or the environment, which outranks it `[GDE-CLI-090]`.
# Rendered here, on the operator's machine where `fleet/` exists; a node only
# ever receives the rendered file.
#
# A missing value stops with its name rather than rendering a placeholder that
# chrony would quietly fail to reach `[GDE-DEP-060]`. The one line printed says
# which server was written, so a wrong one is a visible line.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT="${1:?usage: build/render-fleet-sources.sh OUT}"
# shellcheck source=build/lib-defaults.sh
. "$ROOT/build/lib-defaults.sh"
NTP_SERVER="${NTP_SERVER:-$(lempi_target NTP_SERVER)}"
: "${NTP_SERVER:?NTP_SERVER is not set: put the fleet time server in fleet/targets.env}"
# A host name or an address, and nothing that sed or chrony would read as more.
case "$NTP_SERVER" in
    *[!A-Za-z0-9.:-]*) echo "render-fleet-sources: not a server name: $NTP_SERVER" >&2; exit 1 ;;
esac
sed "s/@NTP_SERVER@/$NTP_SERVER/" "$ROOT/build/lempi-fleet.sources.in" > "$OUT"
grep -q "^server $NTP_SERVER " "$OUT" || { echo "render-fleet-sources: $OUT does not name $NTP_SERVER" >&2; exit 1; }
echo "render-fleet-sources: $OUT prefers $NTP_SERVER"
