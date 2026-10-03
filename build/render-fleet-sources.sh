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
if [ "$OUT" != --server ]; then
    sed "s/@NTP_SERVER@/$NTP_SERVER/" "$ROOT/build/lempi-fleet.sources.in" > "$OUT"
    grep -q "^server $NTP_SERVER " "$OUT" || { echo "render-fleet-sources: $OUT does not name $NTP_SERVER" >&2; exit 1; }
    echo "render-fleet-sources: $OUT prefers $NTP_SERVER"
    exit 0
fi

# --server OUT_SOURCES OUT_CONF: the time server's own two files, since
# 2026-10-02 [FLT-SHP-030]. Its sources are the fallbacks alone -- it does
# not prefer itself -- and its conf serves the fleet's /24, derived from the
# server's own address so no address is written into the repository.
SOURCES="${2:?usage: build/render-fleet-sources.sh --server OUT_SOURCES OUT_CONF}"
CONF="${3:?usage: build/render-fleet-sources.sh --server OUT_SOURCES OUT_CONF}"
case "$NTP_SERVER" in
    [0-9]*.[0-9]*.[0-9]*.[0-9]*) NTP_SUBNET="${NTP_SERVER%.*}.0/24" ;;
    *) echo "render-fleet-sources: --server needs NTP_SERVER as an IPv4 address, to serve its /24" >&2; exit 1 ;;
esac
cp "$ROOT/build/lempi-fleet-server.sources.in" "$SOURCES"
sed "s|@NTP_SUBNET@|$NTP_SUBNET|" "$ROOT/build/lempi-fleet-server.conf.in" > "$CONF"
grep -q "^allow $NTP_SUBNET\$" "$CONF" || { echo "render-fleet-sources: $CONF does not allow $NTP_SUBNET" >&2; exit 1; }
echo "render-fleet-sources: the server's files serve $NTP_SUBNET"
