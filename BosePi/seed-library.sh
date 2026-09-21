#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Phase 4: seed bose's library from the local, already-Vipunen-processed
# library, instead of copying raw files off the old card and re-ingesting
# from scratch [IMPL-BOS-085].
#
# **Status: run for real against bose 2026-09-06** -- 5,709 paths bound by
# relink, the relinked db swapped in as /var/lempi/listener.db. Run once, so
# treat its output as something to read, not a black box: `docker`/Alpine
# package names, this dev host's own `$HOME/Music` layout, and relink's
# `ffmpeg` dependency are all things that can drift out from under it on a
# different machine or a later date, unexercised until they are.
#
# Runs on the development host. Idempotent in every step except the last,
# which is deliberately not: swapping the relinked database in as the live
# one must never silently clobber an appliance's own accumulated play
# history [PI-C-020], so a second run refuses rather than overwrites.
#
#     bash BosePi/seed-library.sh [--force-swap]
#
# --force-swap allows the final step to overwrite an existing
# /var/lempi/listener.db. There is no default for this -- a script that can
# destroy irreplaceable listening data must be asked twice, same discipline
# as prepare-card.sh's --check/--go split.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
. BosePi/lib.sh

HOST="${HOST:-pi@bose}"
MUSIC_DIR="${MUSIC_DIR:-$HOME/Music}"
DB="${DB:-data/library.db}"
FORCE_SWAP=0
[ "${1:-}" = "--force-swap" ] && FORCE_SWAP=1

caveat \
    "Run once for real already -- see the header for what that proved and" \
    "what it didn't. Watch each step below rather than trusting a clean" \
    "exit at the end." \
    "" \
    "PREDATES THE SPLIT [BOS-RUN-080]. Its final step installs one whole" \
    "database as /var/lempi/listener.db, which is NOT what bose runs since" \
    "2026-09-11: it opens /var/lempi/listener.db with /srv/library/library.db" \
    "as its catalogue, and the whole file was deleted. Seeding a fresh" \
    "card needs this for the audio, then split_database.py inside an" \
    "attended window -- BOSE009 section 4. Do NOT run the swap step as is."

step "Preconditions"
check "bose reachable"         ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" true
check "bose is aarch64"        bash -c "[ \"\$(ssh $HOST uname -m)\" = aarch64 ]"
check "sudo works over SSH"    ssh "$HOST" sudo -n true
check "/srv/library mounted"   ssh "$HOST" findmnt /srv/library
check "/var/lempi mounted"     ssh "$HOST" findmnt /var/lempi
check "local library exists"   test -d "$MUSIC_DIR"
check "local db exists"        test -f "$DB"
check "docker available"       docker version
say "music root: $MUSIC_DIR"
say "database:   $DB"

step "B is writable for this seed [attended import, PI-B-030]"
# chown, not a permission bit flip -- idempotent either way, and matches how
# C was already made pi-writable in provision-bose.sh's State layout step.
run "chown /srv/library to pi" ssh "$HOST" sudo chown pi:pi /srv/library

step "ffmpeg on bose, for relink's audio hashing [SPEC012]"
run "apt install ffmpeg" ssh "$HOST" \
    "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg"

step "Cross-compile relink for aarch64"
# Always redone, not gated on an existence check: a stale relink binary
# silently verifying against old logic is a worse failure than 30 extra
# seconds, and the build is cheap enough that gating it buys nothing.
docker build -t lempi-aarch64 -f build/Dockerfile.aarch64 . >>"$LOG_FILE" 2>&1 \
    || die "docker build failed (see $LOG_FILE)"
run "cargo build --bin relink" env MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$(pwd -W 2>/dev/null || pwd):/w" lempi-aarch64 \
    cargo build --release --target aarch64-unknown-linux-gnu \
        --manifest-path player/Cargo.toml --bin relink
RELINK_BIN=player/target/aarch64-unknown-linux-gnu/release/relink
check "relink binary is aarch64" bash -c "file '$RELINK_BIN' | grep -q aarch64"

step "Transfer: audio, then the staged database [music before db, IMPL-BOS-085]"
mkdir -p /srv/library/mpd 2>/dev/null # harmless if this path doesn't exist locally
run "mkdir /srv/library/mpd on bose" ssh "$HOST" mkdir -p /srv/library/mpd
if command -v rsync >/dev/null 2>&1; then
    # A Linux dev host: rsync is just here.
    run "rsync audio -> $HOST:/srv/library/audio/" \
        rsync -a --partial --stats --human-readable \
            --exclude=.DS_Store --exclude=Thumbs.db \
            -e 'ssh -o StrictHostKeyChecking=accept-new' \
            "$MUSIC_DIR"/ "$HOST":/srv/library/audio/
    run "rsync db -> $HOST:/srv/library/library-new.db" \
        rsync -a --partial --human-readable \
            -e 'ssh -o StrictHostKeyChecking=accept-new' \
            "$DB" "$HOST":/srv/library/library-new.db
else
    # No rsync on this host (Windows/Git Bash) -- confirmed absent building
    # this card, same as prepare-card.sh's reader gap [IMPL-BOS-100]. Borrow
    # rsync from a throwaway Alpine container instead, the same pattern
    # scratch/transfer.sh already used for lempipi.
    say "no native rsync -- using a throwaway Alpine container"
    run "container transfer" env MSYS_NO_PATHCONV=1 docker run --rm \
        -v "$MUSIC_DIR:/music:ro" \
        -v "$(pwd)/data:/s:ro" \
        -v "$HOME/.ssh:/keys:ro" \
        alpine:latest sh -c "
            apk add -q rsync openssh-client >/dev/null 2>&1
            mkdir -p /root/.ssh && cp /keys/id_ed25519 /root/.ssh/ 2>/dev/null
            chmod 700 /root/.ssh; chmod 600 /root/.ssh/* 2>/dev/null
            SSH='ssh -o StrictHostKeyChecking=accept-new'
            rsync -a --partial --stats --human-readable \
                --exclude=.DS_Store --exclude=Thumbs.db \
                -e \"\$SSH\" /music/ $HOST:/srv/library/audio/ &&
            rsync -a --partial --human-readable \
                -e \"\$SSH\" /s/$(basename "$DB") $HOST:/srv/library/library-new.db
        "
fi

caveat \
    "relink hashes each file's *encoded* audio stream, not its bytes -- a" \
    "different ffmpeg version decoding the same file can theoretically hash" \
    "differently. Read the apply report below rather than only its exit" \
    "code: 'missing'/'corrupt' counts above zero mean look, not proceed."
step "Relink: bind the staged db to bose's own paths [SPEC012]"
scp -q "$RELINK_BIN" "$HOST:/tmp/relink" || die "scp of relink failed"
run "relink --apply" ssh "$HOST" \
    "chmod +x /tmp/relink && /tmp/relink --library /srv/library/library-new.db --audio-root /srv/library/audio --apply"

step "Swap in as the live database [deliberate, not automatic -- PI-C-020]"
if ssh "$HOST" test -e /var/lempi/listener.db; then
    if [ "$FORCE_SWAP" != "1" ]; then
        die "/var/lempi/listener.db already exists on $HOST -- refusing to overwrite" \
            "possible listener history. Re-run with --force-swap only if you" \
            "have confirmed there is nothing there worth keeping."
    fi
    say "WARNING: --force-swap given, overwriting an existing live database"
fi
run "install as /var/lempi/listener.db" ssh "$HOST" \
    "sudo mv /srv/library/library-new.db /var/lempi/listener.db && sudo chown pi:pi /var/lempi/listener.db"

say "Seeded. Log: $LOG_FILE"
