#!/bin/bash
# Bring a machine that BUILDS Lempi from source up to the current commit, as
# opposed to one that is handed a finished binary.
#
#     build/update-source-host.sh sw@teacherslounge /home/sw/Dev/Lempi
#
# Why this is not another host in deploy-appliance.sh's list: the appliances
# are aarch64 and are sent a cross-compiled binary by build/install-player.sh,
# which refuses outright anything that is not aarch64. A source host is a
# different shape -- its own architecture, its own toolchain, a git checkout,
# and usually no service at all -- so there is nothing to scp and nothing to
# restart. It pulls and rebuilds.
#
# Verification asks the BUILT BINARY what commit it is, not a running player.
# That is deliberate, and it is a stronger check than the appliances get: a
# source host normally has nothing running, and a process that happened to be
# serving the right sha would not prove the binary on disk had been rebuilt.
#
# The host pulls from the git remote, NOT from this machine, so a commit that
# has not been pushed cannot reach it. This refuses up front rather than
# reporting success for having redeployed yesterday's code -- the failure that
# a "deploy" which quietly does nothing is worst at hiding.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
HOST="${1:-}"
REPO="${2:-}"

die() { echo "update-source-host: $*" >&2; exit 1; }

[ -n "$HOST" ] && [ -n "$REPO" ] || die "usage: $0 <user@host> <path-to-checkout>"

branch=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null) || die "not a git checkout"
head_sha=$(git -C "$ROOT" rev-parse HEAD)

# What the REMOTE actually has, asked of the server rather than of this
# machine's cached ref -- `origin/main` here can be hours stale and would
# answer this question wrongly in the one direction that matters.
echo "update-source-host: checking $branch is pushed ..."
remote_sha=$(git -C "$ROOT" ls-remote origin "refs/heads/$branch" 2>/dev/null | cut -f1)
[ -n "$remote_sha" ] || die "cannot reach origin, or it has no $branch"
if [ "$remote_sha" != "$head_sha" ]; then
    die "origin/$branch is $(echo "$remote_sha" | cut -c1-7), local HEAD is $(echo "$head_sha" | cut -c1-7)
    $HOST pulls from origin, so it cannot receive what has not been pushed.
    Run: git push origin $branch"
fi

echo "update-source-host: updating $HOST:$REPO to $(echo "$head_sha" | cut -c1-7) ..."

# One heredoc rather than a chain of ssh calls: the steps share state (the
# checkout's own directory, the toolchain PATH) and a partial run is easier to
# reason about when it is one script that stopped than five that half-ran.
ssh -o ConnectTimeout=10 "$HOST" bash -s -- "$REPO" "$branch" "$head_sha" <<'REMOTE'
set -uo pipefail
REPO="$1"; branch="$2"; want="$3"
say() { echo "  $*"; }
fail() { echo "  ! $*" >&2; exit 1; }

cd "$REPO" 2>/dev/null || fail "no checkout at $REPO"

# rustup installs into ~/.cargo/bin and puts it on PATH from the shell profile,
# which a NON-INTERACTIVE ssh does not read. Without this, cargo is simply not
# found on a machine that plainly has it.
export PATH="$HOME/.cargo/bin:$PATH"
command -v cargo >/dev/null || fail "cargo not found (looked in \$HOME/.cargo/bin)"

# Tracked files only. A source host accumulates untracked working files --
# local databases, backups, an ignored launcher -- and none of those are a
# reason to refuse. Modified TRACKED files are: they mean someone is working
# here, and a fast-forward over that would be taking their machine from them.
dirty=$(git status --porcelain --untracked-files=no)
[ -z "$dirty" ] || fail "working tree has local changes -- not touching it:
$dirty"

git fetch --quiet origin || fail "fetch failed"
# Fast-forward only. A merge commit made unattended on someone else's machine
# is not a thing this should ever create.
git merge --ff-only "origin/$branch" --quiet || fail "cannot fast-forward to origin/$branch"

got=$(git rev-parse HEAD)
[ "$got" = "$want" ] || fail "checkout is $(echo "$got" | cut -c1-7) after pulling, wanted $(echo "$want" | cut -c1-7)"
say "checkout at $(echo "$got" | cut -c1-7)"

say "building ..."
cargo build --release --manifest-path player/Cargo.toml --bin lempi 2>&1 \
    | grep -vE '^\s+Compiling|^\s+Finished|^warning|^\s+-->|^\s+\||^\s+=|^[0-9]+ \|' \
    | grep -v '^$' | tail -5
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "build failed"

# The binary's own answer, which is what actually proves the rebuild happened.
ver=$(./player/target/release/lempi --version 2>&1 | head -1)
case "$ver" in
    *"$(echo "$want" | cut -c1-12)"*) say "binary reports: $ver" ;;
    *) fail "binary reports '$ver', which is not $(echo "$want" | cut -c1-12) -- it did not rebuild" ;;
esac
REMOTE
status=$?
[ "$status" -eq 0 ] || die "$HOST did not update"
echo "update-source-host: $HOST is current"
