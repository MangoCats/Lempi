#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Check or bring level every machine that plays -- the three appliances and
# the two source hosts -- one after another, each by its own setup script on
# the shared engine [APP-SET-010], [FLT-SHP-030]. Named setup-appliances.sh
# until 2026-10-02, when the source hosts joined.
#
#     bash build/setup-fleet.sh                      # --check all five
#     bash build/setup-fleet.sh --go                 # apply on all five
#     bash build/setup-fleet.sh --go bose lp3-wifi   # just these
#
# Each node's script says what its target is before acting [GDE-DEP-060],
# and on an overlay root writes both layers and reads the durable copy back
# [APP-SET-030]. This only runs them in turn and adds up what they said:
# a node that differs or fails does not stop the next, and the summary at the
# end names each one. The exit status is non-zero if any node differs.
#
# Nothing here restarts the player. Units that changed are reloaded; what
# they run takes the new form at its next start.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

MODE=--check
case "${1:-}" in
    --check|--go) MODE=$1; shift ;;
    -*) echo "usage: setup-fleet.sh [--check|--go] [lempi02w] [bose] [lp3-wifi] [smartboardpc] [teacherslounge]" >&2; exit 2 ;;
esac
[ $# -gt 0 ] || set -- lempi02w bose lp3-wifi smartboardpc teacherslounge

script_for() {
    case "$1" in
        lempi02w)       echo LempiPi/setup-lempi02w.sh ;;
        bose)           echo BosePi/setup-bose.sh ;;
        lp3-wifi)       echo LempiPlay3/setup-lp3.sh ;;
        smartboardpc)   echo SmartPC/setup-smart.sh ;;
        teacherslounge) echo TeachersLounge/setup-tl.sh ;;
        *)              return 1 ;;
    esac
}
for n in "$@"; do
    script_for "$n" >/dev/null || { echo "setup-fleet: no setup script for '$n'" >&2; exit 2; }
done

summary=""
worst=0
for n in "$@"; do
    s=$(script_for "$n")
    printf '\n======== %s (%s %s)\n' "$n" "$s" "$MODE"
    # Run directly, not through a pipe: its exit status is the node's
    # verdict, and a filter's would stand in for it (CLAUDE.md section 6).
    bash "$s" "$MODE"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        summary="$summary$(printf '  %-15s as recorded' "$n")\n"
    else
        summary="$summary$(printf '  %-15s DIFFERS or failed -- read its section above' "$n")\n"
        worst=1
    fi
done

printf '\n======== summary (%s)\n' "$MODE"
printf '%b' "$summary"
exit "$worst"
