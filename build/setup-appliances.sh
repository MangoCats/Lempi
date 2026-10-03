#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Check or bring level every appliance, one after another, each by its own
# setup script on the shared engine [APP-SET-010].
#
#     bash build/setup-appliances.sh                     # --check all three
#     bash build/setup-appliances.sh --go                # apply on all three
#     bash build/setup-appliances.sh --go bose lp3-wifi  # just these
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
    -*) echo "usage: setup-appliances.sh [--check|--go] [lempi02w] [bose] [lp3-wifi]" >&2; exit 2 ;;
esac
[ $# -gt 0 ] || set -- lempi02w bose lp3-wifi

script_for() {
    case "$1" in
        lempi02w) echo LempiPi/setup-lempi02w.sh ;;
        bose)     echo BosePi/setup-bose.sh ;;
        lp3-wifi) echo LempiPlay3/setup-lp3.sh ;;
        *)        return 1 ;;
    esac
}
for n in "$@"; do
    script_for "$n" >/dev/null || { echo "setup-appliances: no setup script for '$n'" >&2; exit 2; }
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
        summary="$summary$(printf '  %-10s as recorded' "$n")\n"
    else
        summary="$summary$(printf '  %-10s DIFFERS or failed -- read its section above' "$n")\n"
        worst=1
    fi
done

printf '\n======== summary (%s)\n' "$MODE"
printf '%b' "$summary"
exit "$worst"
