#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Shared helpers for the bose build scripts that run on the development host
# (seed-library.sh, finalize-bose.sh, build-bose-card.sh). Sourced, not run.
#
# prepare-card.sh deliberately does NOT source this: it is scp'd alone to
# bose per its own header comment, and a second file to transfer would break
# that. It keeps its own copies of say/step/die instead -- duplication that
# is the correct trade against a dependency that cannot be satisfied where
# the script runs.
#
# Every phase script gets its own timestamped log under BosePi/logs/, in
# addition to stdout -- "logging the result" per this project's own build
# discipline, not just printing it.
#
# `caveat()` below exists because every script sourcing this file encodes a
# procedure worked out once, by hand, against one card on one day -- not a
# tool proven by repeated use. Different Raspberry Pi OS releases, different
# tool versions, different hardware (a different DAC, a different card size)
# can all change what "correct" looks like out from under a script that
# still exits 0. Read this file's own helpers as: check what you can, log
# everything, say plainly what wasn't checked, and stop rather than guess.

BOSEPI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$BOSEPI_DIR/logs"
mkdir -p "$LOG_DIR"
SCRIPT_NAME="$(basename "${0:-script}" .sh)"
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}-$(date -u +%Y%m%dT%H%M%SZ).log"

log()  { printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
step() { printf '\n== %s\n' "$*" | tee -a "$LOG_FILE"; }
say()  { printf '  %s\n' "$*" | tee -a "$LOG_FILE"; }
die()  { printf '%s: %s\n' "$SCRIPT_NAME" "$*" | tee -a "$LOG_FILE" >&2; exit 1; }
on()   { ssh "${HOST:-pi@bose}" "$@"; }

# caveat <lines...>
#
# A visible, logged reminder that this script encodes a procedure someone
# worked out once, by hand, on one machine's software as it existed on one
# day -- not a tool proven across runs. Called at the top of every script in
# this family that has not itself been exercised end to end, and again right
# before any step that leans on something version- or hardware-specific
# enough that a plausible-looking failure could pass for success. Printed,
# not just documented in a header comment nobody re-reads mid-run.
caveat() {
    say "-----------------------------------------------------------------"
    for line in "$@"; do say "$line"; done
    say "-----------------------------------------------------------------"
}

# check <description> <command...>
#
# A precondition, checked rather than assumed [PI3-API-030], logged either
# way. Failure stops the whole script -- "only proceed if the last step
# succeeded" applies to readiness checks as much as to the actions
# themselves, so a script that can't confirm it's safe to act does not act.
check() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        say "OK   $desc"
    else
        say "FAIL $desc"
        die "precondition failed: $desc"
    fi
}

# run <description> <command...>
#
# The action itself, always logged (stdout and stderr, to the log file and
# the screen), dying loudly on failure rather than leaving a script to carry
# on past a step that didn't actually happen.
run() {
    local desc="$1"; shift
    step "$desc"
    # PIPESTATUS, not $? -- a plain pipeline's $? is tee's exit status, not
    # the command's, and tee practically never fails. Trusting it would
    # defeat the whole "stop on failure" contract silently.
    "$@" 2>&1 | tee -a "$LOG_FILE"
    local rc=${PIPESTATUS[0]}
    if [ "$rc" -eq 0 ]; then
        say "done"
    else
        die "step failed ($rc): $desc (see $LOG_FILE)"
    fi
}
