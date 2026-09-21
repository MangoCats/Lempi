#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Phase 1 and 2 of [BOSE002]: write a new microSD and lay out its partitions.
#
# **Runs on `bose`, against a card in a USB reader, while `bose` is booted from
# a different card** [IMPL-BOS-100]. It cannot run on the development host --
# that machine has no card slot -- and it cannot operate on the card the calling
# machine booted from, which is the constraint the whole phasing exists for.
#
# Invoke it over SSH from the development host:
#
#     scp BosePi/prepare-card.sh pi@bose:/tmp/
#     ssh pi@bose 'sudo bash /tmp/prepare-card.sh --device /dev/sda --check'
#     ssh pi@bose 'sudo bash /tmp/prepare-card.sh --device /dev/sda --go'
#
# `--check` prints what it would do and touches nothing. There is no default
# action: a script that destroys a disk must be asked twice.
#
# **Status: the partition/format sequence ran successfully against bose on
# 2026-09-06** (a 119 GB USB card, this exact firmware and e2fsprogs/f2fs-
# tools versions) -- see [BOSE003](BOSE003-build-procedure.md)'s corrections
# for what didn't match the plan on contact. The idempotent-rerun guard
# below (`ALREADY`) was added afterward and has not itself been exercised --
# it has never actually seen a card it should refuse to touch again. A
# different card size, a different Raspberry Pi OS release's partition
# layout, or different e2fsprogs/f2fs-tools versions on whatever machine
# runs this next could all change what "worked" looks like; read the
# `parted print` output this script shows before and after, not just its
# exit code.

set -euo pipefail

DEVICE=""
MODE="check"
IMAGE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64_latest"

# Partition sizes [BOSE002 §2]. A is generous because it is written once; C is
# sized for f2fs, which wants room to log into; B takes the rest.
SIZE_FIRMWARE="512MiB"
SIZE_SYSTEM="8GiB"
SIZE_STATE="4GiB"

usage() {
    cat <<'USAGE'
prepare-card.sh --device /dev/sdX [--check | --go] [--image URL] [--skip-write]

  --check       say what would happen, change nothing (default)
  --go          actually do it
  --skip-write  keep the card's existing OS image, only repartition
  --image URL   where to stream the OS from

Refuses any device that is not USB-attached, or that holds this machine's root.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --device) DEVICE="${2:-}"; shift 2 ;;
        --check)  MODE="check"; shift ;;
        --go)     MODE="go"; shift ;;
        --skip-write) SKIP_WRITE=1; shift ;;
        --image)  IMAGE_URL="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
    esac
done
SKIP_WRITE="${SKIP_WRITE:-0}"

say()  { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf 'prepare-card: %s\n' "$*" >&2; exit 1; }

# --- The guard [IMPL-BOS-110] ------------------------------------------------
#
# This runs as root on a machine whose own root filesystem is a card, and the
# difference between the target and the running system is one letter. Every
# condition below is *checked*, not assumed, and the script refuses rather than
# asks -- an unattended run must not sit at a prompt, and must not proceed.

held_disks() {
    # Every whole-disk device this machine depends on, by every route we have.
    #
    # **An overlay root defeats the obvious one.** `findmnt /` answers
    # `overlay`, and `overlay` has no PKNAME -- so a guard written only against
    # `/` returns empty and passes everything, on precisely the machine this
    # script runs on. Measured on `bose`: `/` -> overlay -> (nothing), while
    # `/boot` -> mmcblk0p1 -> mmcblk0. A check that cannot fail is not a check
    # `[PI3-API-030]`, so this asks several ways and `guard` refuses if they all
    # come back empty.
    local mp src pk root dev
    for mp in / /boot /boot/firmware /lower /var/log /srv/library /var/lempi; do
        src="$(findmnt -no SOURCE "$mp" 2>/dev/null | head -1)"
        [ -n "$src" ] || continue
        case "$src" in overlay|tmpfs|none|proc|sysfs) continue ;; esac
        pk="$(lsblk -no PKNAME "$src" 2>/dev/null | head -1)"
        [ -n "$pk" ] && printf '%s\n' "$pk"
    done
    # The kernel's own idea of where it booted from, which no overlay can hide.
    root="$(sed -n 's/.*[ ]root=\([^ ]*\).*/\1/p' /proc/cmdline 2>/dev/null | head -1)"
    if [ -n "$root" ]; then
        dev=""
        case "$root" in
            PARTUUID=*|UUID=*|LABEL=*) dev="$(blkid -t "$root" -o device 2>/dev/null | head -1)" ;;
            /dev/*) dev="$root" ;;
        esac
        [ -n "$dev" ] && lsblk -no PKNAME "$dev" 2>/dev/null | head -1
    fi
}

guard() {
    [ -n "$DEVICE" ] || die "no --device given"
    [ -b "$DEVICE" ] || die "$DEVICE is not a block device"

    local name transport
    name="$(basename "$DEVICE")"

    case "$name" in
        mmcblk*|nvme*)
            die "$DEVICE is an internal card or SSD. This script only writes USB-attached readers." ;;
        *[0-9]) die "$DEVICE looks like a partition. Give the whole disk, e.g. /dev/sda" ;;
    esac

    transport="$(lsblk -dno TRAN "$DEVICE" 2>/dev/null || true)"
    [ "$transport" = "usb" ] || die "$DEVICE reports transport '${transport:-unknown}', not usb. Refusing."

    # Belt as well as braces: no disk this machine depends on may be the target.
    local held_list held
    held_list="$(held_disks | sort -u | tr '\n' ' ')"
    held_list="${held_list% }"

    # If we could not work out what this machine runs from, we cannot say the
    # target is safe -- and "cannot say" must not read as "yes".
    [ -n "$held_list" ] \
        || die "cannot determine which disk this machine runs from. Refusing rather than guessing."

    for held in $held_list; do
        [ "$held" = "$name" ] \
            && die "$DEVICE holds this machine's filesystems ($held). Refusing."
    done

    # A mounted target is either the wrong disk or one still in use.
    if lsblk -no MOUNTPOINT "$DEVICE" | grep -q .; then
        die "$DEVICE has mounted partitions. Unmount them first, or it is the wrong disk."
    fi

    say "device      $DEVICE ($(lsblk -dno SIZE "$DEVICE" | tr -d ' '), transport usb)"
    say "model       $(lsblk -dno MODEL "$DEVICE" | sed 's/  */ /g')"
    say "this machine runs from: $held_list -- and the target is not among them"
}

say "-----------------------------------------------------------------"
say "This ran successfully once, on bose, 2026-09-06. The idempotent-rerun"
say "check just below has not itself been exercised. Read this script's"
say "own output at each step rather than only trusting its exit code."
say "-----------------------------------------------------------------"

step "Checking the target"
guard

step "Checking whether this card is already prepared"
# Not idempotent past this point, and said so rather than pretended
# otherwise: a second run would `parted rm 2` an already-grown A and then
# `mkpart` C and B on top of partitions that already occupy that space.
# Detecting "already labelled" and stopping is the honest version of
# idempotent here -- fixing a *wrong* existing layout is not attempted.
ALREADY=1
for lbl in SYSTEM STATE LIBRARY; do
    dev="$(blkid -L "$lbl" 2>/dev/null)" || { ALREADY=0; break; }
    case "$dev" in "$DEVICE"*) ;; *) ALREADY=0; break ;; esac
done
if [ "$ALREADY" = "1" ]; then
    say "SYSTEM, STATE and LIBRARY are already on $DEVICE -- nothing to do."
    say "If the layout is actually wrong (wrong sizes, wrong filesystems),"
    say "this script will not repair it: wipe the table by hand first"
    say "(e.g. 'sgdisk --zap-all $DEVICE') and re-run."
    exit 0
fi

step "Plan"
say "1. stream $IMAGE_URL onto $DEVICE"
say "   (streamed, never staged: bose's only writable space is RAM [PI-BOS-050])"
say "2. partitions: firmware $SIZE_FIRMWARE | A $SIZE_SYSTEM | C $SIZE_STATE | B the remainder"
say "3. grow A's filesystem to fill its partition; mkfs C as f2fs, B as ext4"
say "4. disable first-boot root auto-expand, or Pi OS eats C and B"
say "5. seed first boot: user, ssh key, wifi, hostname, hifiberry overlay"
[ "$SKIP_WRITE" = "1" ] && say "(--skip-write: step 1 will be skipped)"

if [ "$MODE" != "go" ]; then
    printf '\nCheck only. Re-run with --go to perform this.\n'
    exit 0
fi

[ "$(id -u)" = "0" ] || die "must run as root"
for t in parted sgdisk partprobe mkfs.ext4 mkfs.f2fs resize2fs; do
    command -v "$t" >/dev/null 2>&1 || die "missing $t (apt install parted gdisk e2fsprogs f2fs-tools)"
done

step "1. Writing the OS image"
if [ "$SKIP_WRITE" = "1" ]; then
    say "skipped"
else
    # Streamed: decompress and write in one pass, no room needed for the image.
    curl -fsSL "$IMAGE_URL" | xz -dc | dd of="$DEVICE" bs=4M conv=fsync status=progress
    partprobe "$DEVICE"; sleep 2
    say "written"
fi

step "2. Partitioning"
#
# **Two things here are easy to get wrong and destroy the OS just written.**
#
# 1. `mkpart` takes a *start* and an *end*, not a start and a size. Writing
#    `mkpart ... 8GiB 4GiB` asks for a partition that ends before it begins.
#    So the ends are accumulated here rather than reusing the size constants.
# 2. Partition 2 is **removed and recreated at exactly its original start**, so
#    that the filesystem the image wrote is still where its new table entry
#    says it is -- this is a resize, dressed as a delete. Hardcoding the start
#    would misalign it: Pi OS images do not begin p2 where a round number
#    suggests. Read it back instead.
say "before:"
parted -sm "$DEVICE" unit MiB print | sed 's/^/    /'

START_A="$(parted -sm "$DEVICE" unit MiB print | awk -F: '$1=="2" {sub(/MiB$/,"",$2); print $2}')"
[ -n "$START_A" ] || die "cannot read partition 2's start -- refusing to guess where the OS is"
say "p2 starts at ${START_A}MiB (preserved, so the image's filesystem survives)"

mib() { printf '%s' "${1%GiB}" | awk '{printf "%d", $1 * 1024}'; }
END_A=$(awk -v s="$START_A" -v n="$(mib "$SIZE_SYSTEM")" 'BEGIN{printf "%d", s + n}')
END_C=$(awk -v s="$END_A"   -v n="$(mib "$SIZE_STATE")"  'BEGIN{printf "%d", s + n}')

parted -s "$DEVICE" rm 2
parted -s "$DEVICE" mkpart primary ext4 "${START_A}MiB" "${END_A}MiB"
parted -s "$DEVICE" mkpart primary "${END_A}MiB"        "${END_C}MiB"
parted -s "$DEVICE" mkpart primary "${END_C}MiB"        100%
partprobe "$DEVICE"; sleep 2
say "after:"
parted -sm "$DEVICE" unit MiB print | sed 's/^/    /'
say "p1 firmware | p2 A system | p3 C state | p4 B library   [BOSE002 §2]"

step "3. Filesystems"
P="$DEVICE"; case "$DEVICE" in *[0-9]) P="${DEVICE}p" ;; esac

# A is the image's own root, resized in place -- never re-made, or the OS goes.
# Labelling first: it just writes a superblock field and works regardless of
# what comes next, so it is not left stranded if the resize below is skipped.
e2label "${P}2" SYSTEM
# [IMPL-BOS-072] Found building this on bose: its own e2fsprogs is bullseye-
# era and cannot even *check* a filesystem a newer mkfs.ext4 wrote (refuses
# with "unsupported feature(s)"), let alone resize it -- and that is a reason
# to skip, not to force. This host's tools are not the ones to trust with a
# filesystem newer than they are.
if e2fsck -fp "${P}2" && resize2fs "${P}2"; then
    say "A's filesystem grown in place to fill its partition"
else
    say "WARNING: could not grow A here -- this host's e2fsprogs is older than"
    say "  the tool that built this image and cannot check or resize it safely."
    say "  A stays at its original (small) size for now. Growing a *mounted* root"
    say "  filesystem is an ordinary, safe operation, so this is deferred rather"
    say "  than forced: run 'sudo resize2fs ${P}2' once the new card is booted on"
    say "  its own hardware -- provision-bose.sh does this first, before installing"
    say "  any package, since there is no room for one until A is grown."
fi
# C and B are new and empty, so these are the only mkfs calls in this script.
mkfs.f2fs -f -l STATE   "${P}3" >/dev/null || die "mkfs.f2fs failed"
mkfs.ext4 -qF -L LIBRARY "${P}4"           || die "mkfs.ext4 failed"
say "C f2fs, B ext4"

step "4. and 5. First-boot configuration"
say "MOUNT ${P}1 and ${P}2 and apply, before the card is ever booted:"
say "  - cmdline.txt: remove the init=...firstboot clause  [auto-expand off]"
say "  - config.txt:  dtoverlay=hifiberry-dacplus"
say "                 dtoverlay=vc4-kms-v3d,audio=off      [PI-BOS-020]"
say "  - userconf.txt, ssh, wifi credentials, hostname     [bose is wireless]"
say ""
say "Not automated here on purpose: these carry a password hash and a WiFi"
say "secret, and this file is in a public repository. See [BOSE002 §6]."

printf '\nDone. Swap the cards, boot, then run provision-bose.sh from the dev host.\n'
