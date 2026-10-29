#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Build the player for lrhtpc and put it there [LRH-DEP-010].
#
#     bash LRHTPC/deploy-lrhtpc.sh            # HEAD
#     bash LRHTPC/deploy-lrhtpc.sh 1a2b3c4    # any commit or tag
#
#     HOST=someone@other bash LRHTPC/deploy-lrhtpc.sh ...   # default mango@lrhtpc
#
# Runs on the development host. lrhtpc is neither an appliance nor a source
# host: it has no toolchain, so the player is built here, in the same
# bookworm image build/verify-targets.sh tests in -- the same Debian release
# the machine runs, and so the same glibc -- and installed in /usr/local/bin,
# the PATH a send's non-interactive ssh has [LRH-DEP-020]. That needs sudo,
# which asks for a password there unless granted for the deploy; the script
# asks first and builds nothing without it. Always from a commit, in a
# worktree made inside the container, so a dirty checkout here is never what
# is built, and the build stamp names a commit that exists [build/README.md].
#
# The unit starts the player --paused [LRH-SVC-010], so a restart would
# silence a player that was playing. Whether it was is read from its own
# listener before the restart, and Play is pressed after if it was.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
REPO_ROOT="$(pwd)"

HOST="${HOST:-mango@lrhtpc}"
REF="${1:-HEAD}"
BIN=/usr/local/bin
BINS="lempi import_bundle relink restore_listener"
LISTENER=lempi-data/listener.db
. build/lib-defaults.sh
PORT=$(lempi_port)

say() { printf 'deploy-lrhtpc: %s\n' "$*"; }
die() { printf 'deploy-lrhtpc: %s\n' "$*" >&2; exit 1; }
on()  { ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" "$@"; }
user_systemctl() { on "XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user $*"; }

# ---- what the target is, said before acting on it [GDE-DEP-060] -----------
on true || die "$HOST is not reachable; nothing was done"
ROOTFS=$(on "findmnt -no FSTYPE /")
[ "$ROOTFS" != overlay ] || die "$HOST has an overlay root, which this script does not handle -- see CLAUDE.md section 4"
ARCH=$(on "uname -m")
[ "$ARCH" = x86_64 ] || die "$HOST is $ARCH; this script builds x86_64 only"
say "$HOST has a writable root ($ROOTFS), $ARCH: an ordinary write to $BIN is durable"
on "sudo -n true" 2>/dev/null \
    || die "sudo on $HOST asks for a password, and $BIN is root's: grant it for the deploy (LRH001 [LRH-DEP-020]); nothing was built"
say "sudo on $HOST answers without a password: will install into $BIN"

EXPECTED=$(git rev-parse --short=12 "$REF^{commit}" 2>/dev/null) || die "no such ref: $REF"
docker info >/dev/null 2>&1 || die "Docker is not running -- start Docker Desktop and try again"

# ---- build ------------------------------------------------------------------
# Git Bash needs the native path for Docker's bind mount [build/README.md].
DOCKER_PATH="$(pwd -W 2>/dev/null || pwd)"
say "building the Linux image (cached after the first run)"
docker build -q -t lempi-linux -f "$DOCKER_PATH/build/Dockerfile.linux" "$DOCKER_PATH" >/dev/null \
    || die "docker build failed"

# Inside the repository, not mktemp: Git Bash's /tmp is a path docker.exe
# cannot mount [build/deploy-appliance.sh].
BUILD_SCRIPT="$REPO_ROOT/.lempi-lrhtpc-build.sh"
OUT="$REPO_ROOT/.deploy-out-lrhtpc"
trap 'rm -f "$BUILD_SCRIPT"' EXIT
rm -rf "$OUT"
cat > "$BUILD_SCRIPT" <<'EOS'
set -e
git config --global --add safe.directory '*'
cd /w
git worktree add --detach --force /tmp/lempi-build "$1" >/dev/null
cd /tmp/lempi-build
cargo build --release --manifest-path player/Cargo.toml
mkdir -p /w/.deploy-out-lrhtpc
for b in $2; do cp player/target/release/$b /w/.deploy-out-lrhtpc/$b; done
EOS
say "building $REF ($EXPECTED) for x86_64 Linux, in its own worktree"
MSYS_NO_PATHCONV=1 docker run --rm \
    -e LEMPI_BUILD_GIT="$EXPECTED" -e LEMPI_BUILD_DIRTY_FILES=0 \
    -v "$DOCKER_PATH:/w" -v "$DOCKER_PATH/.lempi-lrhtpc-build.sh:/build.sh:ro" lempi-linux \
    bash /build.sh "$EXPECTED" "$BINS" \
    || die "build failed"
# The worktree was made at a container path, so the record git keeps of it
# here points nowhere once the container is gone.
git worktree prune
for b in $BINS; do [ -s "$OUT/$b" ] || die "the build produced no $b"; done
BUILT=$(cd "$OUT" && file lempi)
case "$BUILT" in *x86-64*) ;; *) die "lempi is not an x86-64 binary: $BUILT" ;; esac

# ---- was it playing? ---------------------------------------------------------
WAS=$(on "test -f $LISTENER && python3 -c \"import sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); r=c.execute('select playing from player_state').fetchone(); print(r[0] if r else 0)\" $LISTENER" 2>/dev/null)
case "$WAS" in
    1) say "the player was playing (player_state in its listener): Play will be pressed after the restart" ;;
    0) say "the player was paused (player_state in its listener): it is left paused" ;;
    *) WAS=0; say "no listener yet, or it cannot be read: the player is left paused" ;;
esac

# ---- install ----------------------------------------------------------------
# Each binary beside its old one, then renamed over it: a rename leaves a
# running process its own copy, and a half-copied file is never the one run.
# scp cannot write root's folder, so each goes by way of /tmp.
for b in $BINS; do
    scp -q "$OUT/$b" "$HOST:/tmp/lempi-$b.new" || die "copy of $b failed"
    on "sudo install -o root -g root -m 755 /tmp/lempi-$b.new $BIN/$b.new && sudo mv -f $BIN/$b.new $BIN/$b; rc=\$?; rm -f /tmp/lempi-$b.new; exit \$rc" \
        || die "install of $b failed"
    want=$(md5sum < "$OUT/$b" | cut -c1-32)
    have=$(on "md5sum $BIN/$b | cut -c1-32")
    [ "$want" = "$have" ] || die "$b on $HOST is ${have:-absent}, expected $want"
done
rm -rf "$OUT"
VERSION=$(on "$BIN/lempi --version")
case "$VERSION" in *"($EXPECTED)"*) say "installed: $VERSION" ;;
    *) die "the installed copy reports '$VERSION', expected $EXPECTED" ;; esac

# ---- restart, and check what is running -------------------------------------
if ! user_systemctl is-enabled lempi.service >/dev/null 2>&1; then
    say "lempi.service is not enabled yet: installed only -- run LRHTPC/setup-lrhtpc.sh --go"
    exit 0
fi
# A unit still naming another copy would restart that one, and the check
# below would only say the wrong build answered.
case "$(user_systemctl show -p ExecStart --value lempi.service)" in
    *"path=$BIN/lempi "*) ;;
    *) die "lempi.service does not start $BIN/lempi: installed only -- run LRHTPC/setup-lrhtpc.sh --go, then deploy again" ;;
esac
user_systemctl restart lempi.service || die "the restart failed"
# The Program Director is built before the web server binds; ~15 s on the
# appliances' catalogue [build/install-player.sh].
RUNNING=""
for _ in $(seq 1 90); do
    RUNNING=$(on "curl -s -m 3 http://127.0.0.1:$PORT/build" 2>/dev/null)
    [ -n "$RUNNING" ] && break
    sleep 1
done
case "$RUNNING" in
    "") die "the player did not answer /build within 90 s" ;;
    *"$EXPECTED"*) say "running, and answering as $EXPECTED" ;;
    *) die "the running player answers as something else: $RUNNING" ;;
esac
if [ "$WAS" = 1 ]; then
    CODE=$(on "curl -s -o /dev/null -w '%{http_code}' -m 3 -X POST http://127.0.0.1:$PORT/command/play")
    [ "$CODE" = 204 ] || die "Play answered $CODE"
    say "Play pressed; check it with: sh build/verify-playing.sh $HOST"
fi
