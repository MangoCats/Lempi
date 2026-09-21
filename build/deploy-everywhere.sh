#!/bin/bash
# Rebuild and redeploy the player everywhere it runs or is built: this desktop,
# the lempipi and bose appliances, and the teacherslounge and smartboardpc
# source hosts. Written after the targets were left to drift more than once --
# code committed and pushed, but only actually running on some of them,
# discovered later by Lempi's own staleness check firing rather than by
# anyone remembering to look [SPEC-SUI-227].
#
#     build/deploy-everywhere.sh                        # every target below
#     build/deploy-everywhere.sh -- pi@bose             # one named target
#     build/deploy-everywhere.sh -- pi@one pi@two ...   # several
#
# Two kinds of target, because there are two kinds of machine:
#
#   APPLIANCES are aarch64 and run a service. They are sent a cross-compiled
#   binary and restarted, and they are verified by asking the RUNNING player
#   what it is -- the only thing that can catch a service still serving an
#   older binary than the one now on disk `[SPEC-APS-140]`.
#
#   That check is necessary and was not sufficient. It is structurally blind
#   to the inverse case -- where the disk is RAM -- because it passes
#   precisely when the ephemeral copy is the one running. Five days of
#   deploys to bose were verified green this way and evaporated at the next
#   reboot `[IMPL-BOS-185]`. So an overlay-rooted appliance is now ALSO
#   checked for live-vs-durable agreement below `[GDE-DEP-070]`.
#
#   SOURCE HOSTS have their own architecture and toolchain and a git checkout,
#   and usually nothing running at all. They pull and rebuild, and are verified
#   by asking the BUILT BINARY what commit it is. Sending one a cross-compiled
#   aarch64 binary is not merely wrong, it is refused: install-player.sh checks.
#
# A source host pulls from the git remote rather than from this machine, so
# `update-source-host.sh` refuses if HEAD has not been pushed. It does not push
# on your behalf -- publishing is a decision, not a step in a deploy.
#
# bose was added after doing exactly what this script exists to prevent: it
# sat four commits behind while lempipi was kept current, and nobody noticed
# until someone asked. It runs the SAME binary -- `[BOS-RUN-010]` verified
# that one aarch64 build serves both appliances, because the cross-build
# image is bookworm and the binary imports nothing above GLIBC_2.34, which
# both lempipi (2.36) and bose (2.41) satisfy.
#
# Every leg runs regardless of whether the others succeeded -- a broken local
# build is not a reason to leave an appliance on stale code, or the reverse --
# and the exit status is the number that failed, the same accumulate-then-
# report shape build/verify-targets.sh already uses across its own targets.
#
# Each appliance leg runs the full build-and-deploy rather than building once
# and pushing the artefact twice. The second cross-compile is very nearly
# free -- docker's image is cached and cargo finds nothing to redo -- and in
# exchange every leg independently guarantees it shipped a binary built from
# the tree as it stands, instead of one leg trusting an artefact another leg
# was supposed to have produced.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
# `pi@lp3-wifi` added 2026-09-18, and its absence is the exact fault this
# script exists to prevent `[SPEC-SUI-227]`. It is an appliance by every test
# that matters -- aarch64, an overlay root like bose, running `lempi.service`
# -- and it is the fleet's *follower*, so it is the one node where the echo
# correction path actually runs. It sat four commits behind while the two
# named here were kept current, and it was reachable the whole time under a
# name nobody had written down: `lempiplay3` answers ping but its ssh host key
# is under `lp3-wifi`, which only `tools/echo_skew.sh`'s usage line knew.
#
# It also runs `fbui.service` from a separate `/usr/local/bin/fbui` binary
# `[SPEC-FBUI-015]`, which `install-player.sh` does NOT replace. That binary
# reads the snapshot over the same WebSocket and tolerates fields it does not
# know, so it survives a `lempi` newer than itself -- but it does drift, and
# nothing here updates it.
APPLIANCES="pi@lempipi pi@bose pi@lp3-wifi"
# `host:path` -- a source host needs its checkout named, since unlike an
# appliance's `/usr/local/bin/lempi` there is no conventional location.
#
# `smartboardpc` is a source host here because of how it is *verified*, not
# because of what it runs: x86_64, its own toolchain, a checkout.
#
# **Corrected 2026-09-20: it does now run a player** -- pid 801098 serving
# :5720 from `./player/target/release/lempi`, started by hand rather than by
# systemd. That makes it the first target that is BOTH, which
# `[GDE-ECHO-075]` anticipated. It stays in SOURCES for one concrete reason:
# with no unit there is no `lempi.service` journal and no device of the
# player's own to sample -- `build/verify-playing.sh` reports "cannot tell"
# there, which is the honest answer and not one a deploy should treat as a
# pass. Give it a unit and it can move to APPLIANCES.
SOURCES="sw@teacherslounge:/home/sw/Dev/Lempi
mango@smartboardpc:/home/mango/Dev/Lempi"

# Where a named host sends its work. A host named on the command line is
# looked up here, so `-- sw@teacherslounge` reaches the source-host leg with
# its configured path and needs no second argument to say so.
checkout_for() {
    for entry in $SOURCES; do
        case "$entry" in "$1":*) echo "${entry#*:}"; return 0 ;; esac
    done
    return 1
}

HOSTS="$APPLIANCES"
for entry in $SOURCES; do HOSTS="$HOSTS ${entry%%:*}"; done
if [ "${1:-}" = "--" ]; then
    shift
    # Everything after `--` is the host list, so naming one target means that
    # one alone rather than that one plus the defaults. A bare `--` with
    # nothing after it keeps them, rather than deploying to nowhere and
    # reporting success for having done so.
    if [ "$#" -gt 0 ]; then
        HOSTS="$*"
    fi
fi

fail=0

echo "== local =="
"$ROOT/build/deploy-local.sh" || fail=$((fail + 1))

for host in $HOSTS; do
    echo
    if checkout=$(checkout_for "$host"); then
        echo "== ${host#*@} ($host, source) =="
        "$ROOT/build/update-source-host.sh" "$host" "$checkout" || fail=$((fail + 1))
    else
        echo "== ${host#*@} ($host, appliance) =="
        "$ROOT/build/deploy-appliance.sh" "$host" || fail=$((fail + 1))
    fi
done

# A final, authoritative check against HEAD *right now* -- not each leg's own
# earlier-in-time verification. This is the only thing that can catch a
# commit landing between the legs above, or between this script starting
# and finishing; it CANNOT catch one landing after this script exits, which
# is exactly what happened the first time this script existed: it deployed
# `fe4f07d` correctly, and the very next commit (this file's own predecessor)
# moved HEAD again with nobody redeploying afterward. There is no script fix
# for that -- only a rule: run this LAST, after every commit in a change is
# already made, never before one more is still coming.
echo
echo "== verifying every target matches HEAD =="
head_sha=$(cd "$ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
. "$ROOT/build/lib-defaults.sh"
port=$(lempi_port)
local_build=$(curl -s --max-time 3 "http://localhost:$port/build" 2>/dev/null)

# Width of the widest label, so the report lines up however the hosts are
# named -- `pi@lempipi` and `pi@bose` are not the same length.
width=5
for host in $HOSTS; do
    name=${host#*@}
    [ "${#name}" -gt "$width" ] && width=${#name}
done

check_matches() {
    label="$1"; json="$2"
    case "$json" in
        *"$head_sha"*) printf '  %-*s : matches HEAD (%s)
' "$width" "$label" "$head_sha"; return 0 ;;
        *) printf '  %-*s : does NOT match HEAD (%s) -- got: %s
'                "$width" "$label" "$head_sha" "${json:-no answer}" >&2; return 1 ;;
    esac
}
mismatch=0
check_matches "local" "$local_build" || mismatch=$((mismatch + 1))
for host in $HOSTS; do
    if checkout=$(checkout_for "$host"); then
        # The binary on disk, not a running player: a source host normally has
        # nothing running, and asking a process that happened to be up would
        # not prove the binary had been rebuilt in any case.
        answer=$(ssh -o ConnectTimeout=5 "$host"             "cd '$checkout' && ./player/target/release/lempi --version" 2>/dev/null | head -1)
    else
        answer=$(ssh -o ConnectTimeout=5 "$host" "curl -s --max-time 3 http://localhost:$port/build" 2>/dev/null)
        # And, on an overlay root, whether what is running is what will still
        # be there after a reboot. Cheap: two checksums, no second build.
        durable=$(ssh -o ConnectTimeout=5 "$host" '
            L=$(findmnt -no OPTIONS / | tr "," "
" | sed -n "s/^lowerdir=//p")
            [ -n "$L" ] || { echo same; exit 0; }
            a=$(md5sum /usr/local/bin/lempi 2>/dev/null | cut -d" " -f1)
            b=$(sudo md5sum "$L/usr/local/bin/lempi" 2>/dev/null | cut -d" " -f1)
            [ "$a" = "$b" ] && echo same || echo "EPHEMERAL live=$a durable=${b:-absent}"' 2>/dev/null)
        case "$durable" in
            same|"") ;;
            *) printf '  %-*s : %s -- this deploy would NOT survive a reboot
'                    "$width" "${host#*@}" "$durable" >&2
               mismatch=$((mismatch + 1)) ;;
        esac
    fi
    check_matches "${host#*@}" "$answer" || mismatch=$((mismatch + 1))
done
fail=$((fail + mismatch))

echo
if [ "$fail" -eq 0 ]; then
    echo "deploy-everywhere: every target is current, matching HEAD ($head_sha)"
else
    echo "deploy-everywhere: $fail target(s) failed or do not match HEAD -- see above" >&2
    [ "$mismatch" -gt 0 ] && echo "(a commit landing mid-run is the usual cause -- re-run this script)" >&2
fi
exit "$fail"
