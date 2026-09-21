#!/bin/bash
# Build Lempi for an APPLIANCE and install it there, in one command.
#
#   build/deploy-appliance.sh                     latest, to pi@lempipi
#   build/deploy-appliance.sh pi@bose             latest, to a named appliance
#   build/deploy-appliance.sh <tag>               a tag, without touching your checkout
#   build/deploy-appliance.sh <tag> pi@bose       a tag, to a named appliance
#
# Merged 2026-09-11 from this script and the older `deploy-lempipi.sh`
# `[GDE-DEP-095]`. They overlapped only in the no-ref case, and the split had
# already begun to cost: the ref-capable half refused a dirty tree while the
# half `deploy-everywhere.sh` actually calls did not, so the FLEET path -- the
# one most likely to run unattended -- carried the weaker guard, and shipped a
# `+dirty` binary to bose on 2026-09-11.
#
# That guard now lives in `build/install-player.sh`, where both entry points
# converge and where it checks the ARTEFACT rather than this checkout's git
# state. The pre-build refusal below is kept too: failing before a 90-second
# cross-compile is worth more than failing after one.
#
# Everything past the build is `build/install-player.sh`, which owns the
# two-layer write an overlay-rooted appliance needs `[IMPL-BOS-185]`.
#
set -uo pipefail

# Git Bash on Windows "helpfully" rewrites any argument that looks like a
# bare Unix path -- including a container-side path such as `/w` or
# `/build.sh`, which have nothing to do with the host filesystem at all --
# into a Windows path. Inside a `-v` flag this only fires when the host-side
# half is ALSO a bare Unix path (`/tmp/...`); once the host half is already a
# real Windows path (`C:/...`, what `win_path` below produces), the
# container-side half of that flag is left alone. So every mount source in
# this script is put through `win_path` first. A bare container-only path
# used elsewhere on a command line -- e.g. `bash /build.sh` as the container
# command below -- still gets rewritten regardless, since MSYS does not know
# it is not this command's `-v` flag; those spots need `MSYS_NO_PATHCONV`
# scoped to just that one command (`VAR=val cmd`, not exported), since
# exporting it globally would also block the POSIX-style paths *this
# script's own* `git`/`pwd` calls use from becoming ones `git.exe` can open.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
REF="${1:-}"
HOST="${2:-pi@lempipi}"
# A lone argument that looks like `user@host` is a host, not a ref -- so
# "deploy latest, elsewhere" doesn't need the awkward `deploy.sh "" pi@x`.
case "$REF" in *@*) HOST="$REF"; REF="" ;; esac

# ---- does the target actually run the unit this build installs? ----------
#
# The version cross-check further down CANNOT answer this: it asks the target
# for `/usr/local/bin/lempi --version`, gets nothing on a node that has no such
# file, and takes its "predates --version" branch -- reporting a pass for a node
# it never verified. So ask systemd directly, and say what was found
# `[GDE-DEP-060]`.
#
# This began as a mixed-fleet handshake, comparing the unit a node ran against
# the one being installed while the two differed across the fleet. That window
# is closed and the comparison is gone with it; what remains is the part worth
# keeping, which is refusing to push a player at a machine that has nowhere to
# put it.
BUILD_UNIT=lempi.service
TARGET_UNIT=$(ssh -o ConnectTimeout=10 "$HOST" \
    "systemctl list-unit-files --no-legend '$BUILD_UNIT' 2>/dev/null | awk '{print \$1}' | head -1" 2>/dev/null)
if [ -z "$TARGET_UNIT" ]; then
    echo "deploy: $HOST has no $BUILD_UNIT -- this is a deploy, not a first install; refusing." >&2
    echo "deploy: install the unit first, or check you meant this host." >&2
    exit 1
else
    echo "deploy: $HOST runs $TARGET_UNIT, matching this build"
fi

die() { echo "deploy: $*" >&2; exit 1; }

# `deploy-lempipi.sh` built appliances WITH this flag and this script built
# them WITHOUT it, so which binary an appliance received depended on which
# command you happened to type. Merging forces one answer; this preserves what
# the fleet runs today rather than changing behaviour inside a merge.
#
# Settled 2026-09-11 in favour of the documented design `[GDE-DEP-098]`:
# appliances build WITHOUT it. `[SPEC-SUI-196]` states the gate exists "so an
# appliance build never resolves or compiles an HTTP client it will never
# call", and `[SPEC-SUI-190]` measures the binary 3.05 MB smaller without it.
#
# The deciding argument was not size. On `bose` the catalogue is read-only
# twice over -- `/srv/library` mounted `ro`, and `attach_library()` attaching
# it `mode=ro` by construction -- while `record_review` and `edit_review` both
# write through the catalogue. So `/review` and `/edit` served a page, took
# input, and could not save it: a UI that looks like it works and does not,
# which is worse than a 404.
#
# Set `LEMPI_FEATURES="--features vipunen-support"` to put it back for a host
# that can actually write its catalogue.
FEATURES="${LEMPI_FEATURES:-}"

docker info >/dev/null 2>&1 \
    || die "Docker is not running -- start Docker Desktop (or dockerd) and try again"

# Git Bash on Windows needs the native path for Docker's bind mount; `pwd`
# alone gives the POSIX-style one Docker Desktop mismounts
# [build/README.md]. Plain Linux/macOS has no `-W` flag, hence the fallback.
win_path() { (cd "$1" && pwd -W 2>/dev/null) || echo "$1"; }
DOCKER_PATH="$(win_path "$REPO_ROOT")"

echo "deploy: building the aarch64 cross-compiler image (cached after the first run)"
docker build -t lempi-aarch64 -f "$DOCKER_PATH/build/Dockerfile.aarch64" "$DOCKER_PATH" \
    || die "docker build failed"

OUT="$REPO_ROOT/player/target/aarch64-unknown-linux-gnu/release/lempi"

if [ -z "$REF" ]; then
    # No ref named: build exactly what is checked out here, same as running
    # the two steps by hand always has. A dirty tree is refused rather than
    # silently stamped: the appliance's own self-report exists so a deploy
    # can be trusted later, and a build from edited sources is not the
    # commit it would otherwise claim to be [player/build.rs].
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ] && [ "${ALLOW_DIRTY:-}" != "1" ]; then
        die "uncommitted changes are present -- commit first, or re-run with ALLOW_DIRTY=1 to build this exact tree anyway"
    fi
    EXPECTED="$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD)"
    git -C "$REPO_ROOT" diff --quiet --ignore-cr-at-eol HEAD 2>/dev/null || EXPECTED="${EXPECTED}+dirty"

    echo "deploy: cross-compiling the currently checked-out tree ($EXPECTED)..."
    docker run --rm -v "$DOCKER_PATH:/w" lempi-aarch64 \
        cargo build --release --target aarch64-unknown-linux-gnu --manifest-path player/Cargo.toml $FEATURES \
        || die "cross-compile failed"
else
    # A named ref: built in a worktree that is created and used entirely
    # inside the container, so your own checkout is never touched and a
    # worktree's own gitdir pointer never has to resolve across the
    # host/container boundary at all -- the same class of path-translation
    # fault the mount above already has to work around.
    git -C "$REPO_ROOT" rev-parse --verify "$REF" >/dev/null 2>&1 \
        || die "no such ref: $REF (try 'git tag' or 'git log --oneline' to see what exists)"
    # `^{commit}` peels an annotated tag to what it actually points at --
    # without it, an annotated tag's OWN object hash gets compared against
    # the commit hash the checkout (correctly) lands on, and every annotated
    # tag would look like a mismatch even though the right thing was built.
    EXPECTED="$(git -C "$REPO_ROOT" rev-parse --short=12 "$REF^{commit}")"

    echo "deploy: cross-compiling $REF ($EXPECTED) in its own worktree..."
    # Written inside the repo itself (not `mktemp`, which on Git Bash lands
    # in MSYS's own virtual /tmp -- a path docker.exe cannot resolve at all,
    # so it silently mounts an empty directory at /build.sh instead of the
    # script) so that `win_path` can turn it into the real Windows path the
    # mount needs, the same way it already does for the repo checkout below.
    BUILD_SCRIPT="$REPO_ROOT/.lempi-deploy-build.sh"
    trap 'rm -f "$BUILD_SCRIPT"' EXIT
    cat > "$BUILD_SCRIPT" <<'EOS'
set -e
cd /w
git worktree add --detach --force /tmp/lempi-build "$1" >/dev/null
cd /tmp/lempi-build
cargo build --release --target aarch64-unknown-linux-gnu --manifest-path player/Cargo.toml $2
mkdir -p /w/.deploy-out
cp player/target/aarch64-unknown-linux-gnu/release/lempi /w/.deploy-out/lempi
cd /w
git worktree remove --force /tmp/lempi-build
EOS
    # `MSYS_NO_PATHCONV` scoped to this one command: both mount sources above
    # are already Windows-style paths, so nothing here needs the POSIX-to-
    # Windows conversion MSYS normally does -- but MSYS applies that same
    # conversion to a bare-looking argument anywhere in the command line, not
    # just inside `-v` flags, and would otherwise turn the trailing `bash
    # /build.sh` (a path that only exists inside the container) into a
    # nonexistent path on the host's own filesystem.
    MSYS_NO_PATHCONV=1 docker run --rm \
        -v "$DOCKER_PATH:/w" -v "$DOCKER_PATH/.lempi-deploy-build.sh:/build.sh:ro" lempi-aarch64 \
        bash /build.sh "$REF" "$FEATURES" \
        || die "cross-compile failed"
    mkdir -p "$(dirname "$OUT")"
    mv "$REPO_ROOT/.deploy-out/lempi" "$OUT"
    rmdir "$REPO_ROOT/.deploy-out" 2>/dev/null || true
fi

echo "deploy: putting it on $HOST"
# install-player.sh looks for the binary at a path relative to wherever it is
# run from, not relative to itself -- so this only works run from anywhere
# (including from inside LempiPi/ itself) because of the `cd` here.
(cd "$REPO_ROOT" && "$REPO_ROOT/build/install-player.sh" "$HOST") || die "install-player.sh failed; see above"

# The checksum-and-restart above proves *a* new binary answers; this proves
# it is the *right* one, by asking the same way a person checking by hand
# would [SPEC-APS-140].
# On an overlay root `/usr/local/bin/lempi` is the EPHEMERAL copy, so asking it
# proves what is running now and nothing about what survives a reboot
# `[GDE-DEP-070]`. install-player.sh has already written the same bytes to both
# layers by this point, so ask the durable one: a disagreement here means that
# write did not take, which is exactly the fault `[IMPL-BOS-185]` was.
VBIN=/usr/local/bin/lempi
LOWER="$(ssh "$HOST" "findmnt -no OPTIONS / | tr ',' '\n' | sed -n 's/^lowerdir=//p'" 2>/dev/null)"
if [ -n "$LOWER" ]; then
    VBIN="$LOWER$VBIN"
    echo "deploy: $HOST has an overlay root -- asking the DURABLE copy at $VBIN"
fi
REPORTED="$(ssh "$HOST" "sudo $VBIN --version" 2>/dev/null \
            | grep -o '[0-9a-f]\{12\}\(+dirty\)\?')"
if [ "$REPORTED" = "$EXPECTED" ]; then
    echo "deploy: confirmed -- $HOST is running $EXPECTED"
elif [ -z "$REPORTED" ]; then
    # `--version` itself was added in 421f7c1 [REQ-VIS-200]; a ref older than
    # that has nothing to ask. deploy-player.sh already confirmed the upload
    # checksum and a live restart, so the deploy is not in question -- only
    # this last, stricter cross-check is unavailable for a build this old.
    echo "deploy: $HOST is running the new build (checksum-verified), but it predates --version [421f7c1] and cannot self-report which commit that is"
else
    die "$HOST reports '$REPORTED', expected '$EXPECTED' -- something is not what it seems"
fi

# The binary is right. That is not the same as audible `[GDE-ECHO-547]`, and
# this fleet has twice been verified by a proxy that agreed with a silent
# machine. Ask for a number that has to move.
#
# Reported, not fatal, and deliberately so: a listener who has PAUSED the
# player leaves no open PCM and no advancing frame count, which is
# indistinguishable from a broken output at this level. Aborting a fleet
# deploy on a paused node would be a confident wrong answer. The line below is
# the evidence a person reads; `[D]` in build/verify-targets.sh is still the
# only thing that proves a speaker made a sound.
if [ -x "$(dirname "$0")/verify-playing.sh" ] || [ -f "$(dirname "$0")/verify-playing.sh" ]; then
    sh "$(dirname "$0")/verify-playing.sh" "$HOST" ||
        echo "deploy: WARNING -- $HOST installed correctly but could not be shown to be playing (see above; a paused player reads the same way)"
fi
