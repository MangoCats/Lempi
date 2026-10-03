#!/bin/bash
# Bring a machine that BUILDS Lempi from source up to the current commit, as
# opposed to one that is handed a finished binary.
#
#     build/update-source-host.sh sw@teacherslounge /home/sw/Dev/Lempi
#
# Why this is not another host in deploy-appliance.sh's list: the appliances
# are aarch64 and are sent a cross-compiled binary by build/install-player.sh,
# which refuses outright anything that is not aarch64. A source host is a
# different shape -- its own architecture, its own toolchain, a git checkout --
# so there is nothing to scp. It pulls and rebuilds.
#
# Verification asks the BUILT BINARY what commit it is: a process that
# happened to be serving the right sha would not prove the binary on disk had
# been rebuilt. **But that alone is not enough where a player RUNS from the
# checkout**, as smartboardpc's `lempi.service` does. Found 2026-09-27: the
# rebuild replaced the file under a player started at 10:10, which went on
# serving the old code from a deleted inode while this reported "current" --
# the check of a stand-in, not of the thing, that CLAUDE.md section 6 warns of.
# So after the build it looks for a player on this checkout's binary:
#
#   - a running `lempi.service` (user or system) whose ExecStart is that
#     binary is restarted, and the new process confirmed to run the rebuilt
#     file;
#   - a unit that is stopped is left stopped -- teacherslounge is manual-start
#     only, by the maintainer's choice;
#   - a player started by hand on the replaced file fails this, by pid: there
#     is no unit to restart it with, and "current" would be untrue.
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

# ---- import_bundle, for the Export page's sends [SPEC-STAR-094] -----------
# A source host that keeps music is a speaker a bundle can be sent to, and
# send_bundle.py asks `command -v import_bundle` over a non-interactive ssh,
# whose PATH has /usr/local/bin and nothing of $HOME. So it is built here and
# linked there -- a link, not a copy, so every later update keeps it current.
# Missing on smartboardpc and teacherslounge until 2026-10-02, when they were
# made speakers.
cargo build --release --manifest-path player/Cargo.toml --bin import_bundle 2>&1 \
    | grep -vE '^\s+Compiling|^\s+Finished|^warning|^\s+-->|^\s+\||^\s+=|^[0-9]+ \|' \
    | grep -v '^$' | tail -5
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "import_bundle build failed"
ib="$(pwd)/player/target/release/import_bundle"
if [ "$(readlink /usr/local/bin/import_bundle 2>/dev/null)" != "$ib" ]; then
    sudo -n ln -sfn "$ib" /usr/local/bin/import_bundle \
        || fail "could not link /usr/local/bin/import_bundle (needs passwordless sudo)"
    say "linked /usr/local/bin/import_bundle -> $ib"
fi
ibver=$(import_bundle --version 2>&1 | head -1)
case "$ibver" in
    *"$(echo "$want" | cut -c1-12)"*) say "import_bundle reports: $ibver" ;;
    *) fail "import_bundle reports '$ibver', which is not $(echo "$want" | cut -c1-12)" ;;
esac

# ---- a player running from this checkout (see the header) -----------------
bin="$(pwd)/player/target/release/lempi"
# Processes still on the file the build replaced: the kernel names their
# executable "<path> (deleted)". Read from /proc, so it needs nothing installed.
stale_pids() {
    for p in /proc/[0-9]*; do
        [ "$(readlink "$p/exe" 2>/dev/null)" = "$bin (deleted)" ] && echo "${p#/proc/}"
    done
}
# The unit that runs this binary, if one is active: "user" or "system".
unit=""
for scope in user system; do
    flag=""; [ "$scope" = user ] && flag="--user"
    systemctl $flag is-active --quiet lempi.service 2>/dev/null || continue
    systemctl $flag show -p ExecStart --value lempi.service 2>/dev/null | grep -qF "path=$bin " || continue
    unit=$scope; break
done
if [ -n "$unit" ]; then
    say "a $unit lempi.service runs this binary -- restarting it onto the new build"
    if [ "$unit" = user ]; then
        systemctl --user restart lempi.service || fail "could not restart the user lempi.service"
        pidof_unit() { systemctl --user show -p MainPID --value lempi.service; }
    else
        sudo -n systemctl restart lempi.service \
            || fail "lempi.service is a system unit and sudo -n cannot restart it -- it still runs the OLD binary"
        pidof_unit() { systemctl show -p MainPID --value lempi.service; }
    fi
    running=""
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pid=$(pidof_unit)
        if [ -n "$pid" ] && [ "$pid" != 0 ] && [ "$(readlink "/proc/$pid/exe" 2>/dev/null)" = "$bin" ]; then
            running=$pid; break
        fi
        sleep 1
    done
    [ -n "$running" ] || fail "restarted, but no lempi.service process is running the rebuilt $bin"
    say "player restarted: pid $running runs the rebuilt binary"
fi
left=$(stale_pids | tr '\n' ' ')
if [ -n "$left" ]; then
    fail "player(s) still running the binary this build replaced, not under an active lempi.service: pid $left
    restart them by hand -- this host is NOT running $(echo "$want" | cut -c1-7)"
fi
[ -n "$unit" ] || say "no player runs from this checkout; nothing to restart"
REMOTE
status=$?
[ "$status" -eq 0 ] || die "$HOST did not update"
echo "update-source-host: $HOST is current"
