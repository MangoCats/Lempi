#!/bin/sh
# Run the player's test suite on every supported target.
#
# Written after an audit found that "cross-compiles cleanly" had been standing
# in for "works": the suite had only ever RUN on Windows, aarch64 was compiled
# but never executed, and Linux x86_64 had never been built at all. Compiling is
# not testing.
#
# It also found a bug that only a real audio device could expose -- the Windows
# device opens at 48 kHz against a 44.1 kHz library, and the resampler was not
# wired into the playback path, so audio ran 8.8% fast. A null sink reports no
# rate, so it hid the fault entirely. Hence [D] below.
#
#   A  Linux x86_64            native in Docker
#   B  Linux aarch64           cross-compiled, executed under emulation
#   C  Windows x86_64          run on the host
#   D  real audio device       manual; a null sink cannot catch rate mismatches
#
# Usage:  sh build/verify-targets.sh            everything above
#         sh build/verify-targets.sh --quick    the two checks a commit hook runs
#
# `--quick` exists because of a specific failure. On 2026-09-22 `lempi-core`
# was committed naming `libc` in `#[cfg(unix)]` code without depending on it.
# Windows does not compile that branch, so a full host build, every feature
# combination, `clippy --all-targets` and 598 tests all passed on a crate that
# could not build for the appliance at all -- and it was found by
# `build/deploy-appliance.sh`, at the point of shipping. This file already
# says why: **compiling is not testing**, and it is equally true that
# compiling *here* is not compiling *there*.
#
# So the two checks that would have caught it -- a Linux compile and the
# `lempi-core` boundary -- are separable and fast enough to run before a
# commit. `.githooks/pre-commit` calls exactly this, and the full run stays
# what it was.
set -u

QUICK=0
case "${1:-}" in
    --quick) QUICK=1; shift ;;
    "") ;;
    *) echo "usage: $(basename "$0") [--quick]" >&2; exit 2 ;;
esac
# Two forms of the same path: POSIX for the shell, native for docker -v.
# Combining them with || in one command substitution ran BOTH branches.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DROOT=$(cd "$ROOT" && pwd -W 2>/dev/null) || DROOT=$ROOT
[ -n "$DROOT" ] || DROOT=$ROOT
fail=0

# Run a test suite and report **the suite's** status, never a filter's.
#
# Every stage below used to read, in effect:
#
#     docker run ... cargo test ... | grep -E "^test result: ok\.|FAILED" || fail=...
#
# and the pattern matches the FAILED line, so `grep` exited 0 on a red suite
# and the `||` never fired. Measured 2026-09-18: three failing stages printed
# their own failures in full, and this script then said ALL TARGETS PASS and
# exited 0. Two tests broken on 2026-09-06 sat twelve days behind it. That is
# the house rule -- "do not pipe a command through grep/tail and then read
# `$?`; that is the filter's status, not the command's" -- broken by the
# script written to enforce the discipline.
#
# So the status comes from the command and the grep only chooses what is
# shown. **An empty result is a failure too**: a run that printed no
# `test result:` line at all did not run, and a gate that cannot tell that
# from a pass is the same fault wearing a different hat.
run_suite() {
    label=$1
    shift
    out=$(mktemp)
    if "$@" >"$out" 2>&1; then status=0; else status=$?; fi
    grep -E "^test result:|FAILED|^SKIPPED " "$out" || {
        echo "  no 'test result' line at all -- the suite did not run"
        status=1
    }
    if [ "$status" -ne 0 ]; then
        echo "  ^ $label exited $status; full output kept at $out"
    else
        rm -f "$out"
    fi
    return "$status"
}

# The same discipline for a step that is not a test suite: its own status,
# and its output shown rather than piped into the status.
run_step() {
    label=$1
    shift
    out=$(mktemp)
    if "$@" >"$out" 2>&1; then status=0; else status=$?; fi
    tail -4 "$out"
    if [ "$status" -ne 0 ]; then
        echo "  ^ $label exited $status; full output kept at $out"
    else
        rm -f "$out"
    fi
    return "$status"
}

# ---- the two checks a commit is gated on --------------------------------
#
# Both are compile-or-resolve questions, so both are cheap; neither runs a
# test. That is deliberate. A pre-commit hook that runs a suite gets disabled
# within a week, and a disabled guard is worth less than no guard, because it
# still reads like one in the repository.

# Does the crate compile for the platform it actually ships to?
#
# `--all-targets` so tests and binaries are checked too, not just the lib, and
# a persistent host-side target dir so the second run is seconds rather than a
# minute. Measured 2026-09-22: 46 s cold, 22 s warm no-op, 14 s after a core
# edit.
#
# **Docker not running is a FAILURE, not a skip** `[GDE-DEP-060]`. The whole
# lesson of the bug this exists for is that a check which quietly did not
# happen reads exactly like one that passed.
linux_compiles() {
    echo "== Linux compile (the cfg(unix) paths Windows never sees) =="
    if ! docker info >/dev/null 2>&1; then
        echo "  Docker is not running, so this check DID NOT RUN."
        echo "  That is a failure, not a pass: the cfg(unix) code is unchecked."
        echo "  Start Docker, or commit with LEMPI_SKIP_VERIFY=1 to say so out loud."
        return 1
    fi
    docker build -q -t lempi-linux -f "$ROOT/build/Dockerfile.linux" "$ROOT" >/dev/null || {
        echo "  the Linux image would not build; the check did not run"
        return 1
    }
    out=$(mktemp)
    if MSYS_NO_PATHCONV=1 docker run --rm -v "$DROOT":/w -w /w lempi-linux \
        cargo check --manifest-path player/Cargo.toml --all-targets \
        --target-dir player/target/gate-linux >"$out" 2>&1
    then
        echo "  compiles for linux x86_64"
        rm -f "$out"
        return 0
    fi
    grep -E "^error" "$out" | head -10
    echo "  ^ full output kept at $out"
    return 1
}

# The lempi-core boundary `[GDE-AND-045]`. The whole point of splitting the
# selection engine out was to stop the Director's independence from the audio
# path being a thing someone re-establishes by reading imports -- measured
# that way on 2026-08-20, 2026-09-02 and 2026-09-22. So it is checked here.
#
# `cargo tree` resolves the real graph, which is what makes this different
# from grepping for `use`: a crate reached three levels down through a
# dependency's own default features would not appear in any source file, and
# would appear here.
#
# **A guard that cannot run says so and fails** `[GDE-DEP-060]`. An empty tree
# is the failure mode to fear: `grep -q` against nothing found is
# indistinguishable from a clean result, which is CLAUDE.md §6's whole subject.
core_boundary() {
    echo "== lempi-core boundary =="
    core_tree=$(cd "$ROOT/player" && env -u CC cargo tree -p lempi-core --prefix none 2>/dev/null \
                | sed 's/ (\*)//' | awk '{print $1}' | sort -u)
    core_n=$(printf '%s\n' "$core_tree" | grep -c . || true)
    if [ "$core_n" -lt 5 ]; then
        echo "  cargo tree returned $core_n crates -- the check did not run, it did not pass"
        return 1
    fi
    bad=""
    for c in cpal symphonia rubato axum tokio hyper alsa reqwest; do
        printf '%s\n' "$core_tree" | grep -qx "$c" && bad="$bad $c"
    done
    if [ -n "$bad" ]; then
        echo "  lempi-core can reach:$bad"
        echo "  selection must not depend on sounding; see player/core/Cargo.toml"
        return 1
    fi
    echo "  $core_n crates, and none of cpal/symphonia/rubato/axum/tokio/hyper/alsa/reqwest"
    return 0
}

if [ "$QUICK" -eq 1 ]; then
    linux_compiles || fail=$((fail+1))
    core_boundary  || fail=$((fail+1))
    echo
    if [ "$fail" -eq 0 ]; then
        echo "QUICK CHECKS PASS"
        exit 0
    fi
    echo "$fail QUICK CHECK(S) FAILED"
    exit 1
fi

echo "== A: Linux x86_64 =="
docker build -q -t lempi-linux -f "$ROOT/build/Dockerfile.linux" "$ROOT" >/dev/null || fail=$((fail+1))
run_suite "A" env MSYS_NO_PATHCONV=1 docker run --rm -v "$DROOT":/w -w /w lempi-linux \
    cargo test --release --manifest-path player/Cargo.toml --target-dir /tmp/t \
    || fail=$((fail+1))

echo "== B: Linux aarch64 (cross-compiled, run under emulation) =="
docker build -q -t lempi-aarch64 -f "$ROOT/build/Dockerfile.aarch64" "$ROOT" >/dev/null || fail=$((fail+1))
MSYS_NO_PATHCONV=1 docker run --rm -v "$DROOT":/w -w /w lempi-aarch64 \
    cargo test --release --no-run --target aarch64-unknown-linux-gnu \
    --manifest-path player/Cargo.toml >/dev/null 2>&1 || fail=$((fail+1))
BIN=$(ls -t "$ROOT"/player/target/aarch64-unknown-linux-gnu/release/deps/lempi_player-* 2>/dev/null \
      | grep -v '\.d$' | head -1)
if [ -n "$BIN" ]; then
    REL=${BIN#"$ROOT"/}
    # `LEMPI_EMULATED` tells the suite it is somewhere wall-clock measurements
    # do not mean what they say, so a test of a *timing* property says so and
    # stops rather than asserting one it cannot observe. Nothing else reads
    # it, and a native run never sets it.
    run_suite "B" env MSYS_NO_PATHCONV=1 docker run --rm --platform linux/arm64 \
        -v "$DROOT":/w -w /w debian:bookworm-slim sh -c \
        "apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq --no-install-recommends libasound2 ffmpeg >/dev/null 2>&1; LEMPI_EMULATED=1 ./$REL --show-output" \
        || fail=$((fail+1))
else
    echo "  aarch64 test binary not found"; fail=$((fail+1))
fi

echo "== C: host (Windows or Linux) =="
# `env -u CC`: a globally-set CC makes the cc crate compile bundled SQLite
# with MinGW while rustc links with MSVC, which fails on ___chkstk_ms. Unset,
# the cc crate finds MSVC itself and it builds. Cleared here so the result
# does not depend on the developer's environment.
run_suite "C" sh -c "cd '$ROOT/player' && env -u CC cargo test --release" \
    || fail=$((fail+1))

# The same boundary check the commit hook runs, defined once above.
echo
core_boundary || fail=$((fail+1))

# The bounded-decode gate. It needs a long file from a real library, which no
# build machine has by default, so it is opt-in via LEMPI_LONG_FILE -- and a run
# without one reports SKIPPED rather than passing quietly. `[REQ-AUD-110]`
echo
echo "== Bounded decode (optional: set LEMPI_LONG_FILE) =="
mem_note=""
if [ -n "${LEMPI_LONG_FILE:-}" ]; then
    if [ -f "$LEMPI_LONG_FILE" ]; then
        # `run_step`, not a pipe: `... | tail -4` reports tail's status, and
        # tail succeeds at printing nothing. That is the third disguise
        # CLAUDE.md §6 lists by name, and it was still here after the same
        # fault was fixed in the three stages above `[GDE-ARC-035]`.
        run_step "bounded decode" sh -c \
            "cd '$ROOT/player' && env -u CC cargo run --release --quiet --bin memcheck -- --file \"\$1\"" \
            memcheck "$LEMPI_LONG_FILE" || fail=$((fail+1))
    else
        echo "  LEMPI_LONG_FILE is set but does not exist: $LEMPI_LONG_FILE"
        fail=$((fail+1))
    fi
else
    mem_note="bounded decode NOT checked (LEMPI_LONG_FILE unset)"
    echo "  $mem_note"
fi

# The skins are HTML, CSS and JavaScript, so cargo cannot reach them. Optional
# because the player needs neither node nor jsdom to run; a skip is reported as
# a skip, never folded into the pass.
echo
echo "== Skins (optional: needs node + jsdom) =="
skins_note=""
if command -v node >/dev/null 2>&1; then
    node "$ROOT/build/verify-skins.js"
    case $? in
        0) ;;
        2) skins_note="skins NOT checked (jsdom missing)" ;;
        *) fail=$((fail+1)) ;;
    esac
else
    skins_note="skins NOT checked (node missing)"
fi

# The Python tools are outside cargo's reach too. `apply_reviews` rewrites what
# a passage IS, and shipped once in a state where it could not write at all, so
# it does not get to be untested `[REQ-LIB-165]`.
echo
echo "== Tools (optional: needs python) =="
tools_note=""
if command -v python >/dev/null 2>&1; then
    python "$ROOT/tools/test_apply_reviews.py" || fail=$((fail+1))
elif command -v python3 >/dev/null 2>&1; then
    python3 "$ROOT/tools/test_apply_reviews.py" || fail=$((fail+1))
else
    tools_note="tools NOT checked (python missing)"
fi

echo
if [ -n "$mem_note" ]; then
    echo "$mem_note"
fi
if [ -n "$tools_note" ]; then
    echo "$tools_note"
fi
if [ -n "$skins_note" ]; then
    echo "$skins_note"
fi
if [ "$fail" -eq 0 ]; then
    echo "ALL TARGETS PASS"
else
    echo "$fail target check(s) failed"
fi
echo
echo "NOT covered here -- must be run by hand on a machine with audio:"
echo "  D) play a passage through a REAL device and confirm the rate is converted."
echo "     A null sink reports no device rate and cannot catch a resampling fault."
exit "$fail"
