# PI001: LempiPi Image and Partition Design

**Design Specification — Tier 2 · PROVISIONAL**

How a Raspberry Pi Zero 2W is laid out so that pulling its power is a normal
event rather than an incident. Implements the three-partition model this
document proposes to satisfy `[REQ-HW-120]` ("survive repeated hard power loss
without database corruption") and the storage discussion in
[embedded-hardware.md](embedded-hardware.md). *(Corrected 2026-08-30: this
previously cited REQ001's own `[REQ-HW-020]`, deleted the same day per
`[GDE-DIS-010]` — REQ001 named the three-partition model directly, where
REQ002 states the outcome and leaves the mechanism to this document.)*

> **Related:** [IMPL001 appliance setup](IMPL001-appliance-setup.md) ·
> [REQ002 §6 Appliance](../docs/spec/REQ002-functional-requirements.md#6-appliance--hw) ·
> [SPEC006 data flow](../docs/spec/SPEC006-data-flow-and-portability.md) ·
> [SPEC008 schema](../docs/spec/SPEC008-database-schema.md)

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-4 and §6-7 in [PI001](PI001-image-and-partitions.md), §5 and §5a in [PI023](PI023-the-database-split-on-disk.md), §5b in [PI024](PI024-appliance-settings.md).

## 1. The problem being solved

An appliance has no shutdown button anybody uses. It is switched off at the
wall, mid-track, mid-write, and is expected to come back playing. Everything
below follows from taking that literally.

**`[PI-PART-010]` The failure to design against is a write in flight, not a
disk wearing out.** An interrupted write can corrupt a filesystem structure,
not merely lose the bytes being written. So the layout's job is to ensure that
at any instant, the number of partitions with a write in progress is as close
to zero as it can be, and that whatever *was* being written is the thing we
can most afford to lose.

**`[PI-PART-020]` Three partitions, ordered by how often they are written.**

| | Mount | Written | On corruption |
| :--- | :--- | :--- | :--- |
| **A — system** | `/` read-only + overlay | only on software update | reflash |
| **B — library** | `/srv/library` ro, rw during import | only on music import | rebuild from Vipunen |
| **C — state** | `/var/lempi` read-write | continuously | reinitialise; the player still runs |

The ordering is the design. A is never written, so it cannot be corrupted. B
is written on a deliberate, attended action. C absorbs every unattended write
in the system, and is the one partition whose total loss is survivable.

---

## 2. Partition A — system and application

**`[PI-A-010]` Read-only root with an overlay**, the arrangement `raspi-config`
calls Overlay File System. Writes land in a RAM overlay and are discarded at
reboot; the SD card is never written during normal running.

**`[PI-A-020]` The overlay is RAM, and RAM is the scarce resource.** A Pi Zero
2W has 512 MB, and `[REQ-HW-100]` budgets the player under 150 MB RSS.
*(Corrected 2026-09-06: this previously cited `[REQ-HW-010A]`'s 30 MB figure,
a tag that no longer exists in `REQ002` — renumbered, and loosened 5x, when
`REQ001` was retired. The argument below is unaffected; the number was
wrong.)* An overlay that accumulates writes competes directly with the audio
buffers. Everything that writes routinely must therefore be moved *off* the
overlay rather than allowed to fill it:

- `/var/log` → bind-mounted to partition C. Left alone this is the single
  largest consumer, and it grows without bound on a machine that never reboots.
- `/tmp` → `tmpfs` with an explicit `size=` cap, so a runaway cannot take the
  memory the mixer needs.
- systemd journal → `Storage=volatile`, `RuntimeMaxUse=8M`.

**`[PI-A-025]` Host identity and credentials are a different class of write
from logs and temp files, and the overlay destroys them the same way.**
Everything in `[PI-A-020]`'s list is routine noise the machine can afford to
lose. This is not: it is the material that lets the appliance be reached and
trusted at all, and losing it on every power cycle does not degrade the
appliance — it locks it out, a strictly worse failure than anything C is
designed to absorb.

Found in practice, not in design, during the `bose` build: `/home/pi/.ssh`
sat on the overlay, so an `authorized_keys` line added for that build vanished
on the very next power cycle. An appliance switched off at the wall lost its
own means of being reached the same way it would have lost anything else
written there.

What belongs on C instead, bind-mounted before the first thing that depends on
it runs:

- **`/etc/ssh`**, host keys included. Left on the overlay, the machine's SSH
  identity changes every boot — worse than losing `authorized_keys`, because
  it makes every client that has ever connected actively distrust the host
  rather than merely fail to log in.
- **`/home/pi`**, the whole home directory rather than only `.ssh` —
  bind-mounted before the user is created at first boot, so `authorized_keys`
  and anything else cloud-init or the listener writes there persists by
  construction instead of by remembering to special-case one subdirectory.
- **NetworkManager's connection profiles**
  (`/etc/NetworkManager/system-connections`) — `[SPEC034]`'s confirm-or-revert
  flow lets a listener join the appliance to a house network from the web UI.
  Left on the overlay, the appliance forgets that network the moment it is
  switched off at the wall, which is the exact event this whole design exists
  to survive.

The mechanism is the one `[PI-SET-040]` already uses for Bluetooth's pairing
keys: an empty directory pre-created on C, an `fstab` bind mount ordered ahead
of whatever first populates it, and nothing about the service or package
needs to know its state moved.

**`[PI-A-030]` Nothing about the library lives here.** The binary, the unit
file and the OS. A software update is a new image or an `rw` remount performed
deliberately, never a background package upgrade.

---

## 3. Partition B — library

**`[PI-B-010]` Mounted read-only; remounted read-write only for an import.**
`ext4`, journalled. The music files and everything derived from them.

**`[PI-B-020]` This partition is a cache, not a source** `[SPEC-SC-010]`.
Every byte on it re-derives from the audio and from Vipunen, so its corruption
costs time rather than data. That is what permits a plain filesystem here
instead of anything exotic.

Contents:

- the audio files themselves;
- `library.db` — see §5;
- cover art, which lives in `library.db` rather than beside the audio because
  the media is read-only at runtime and cannot be written to `[REQ-VIS-170]`.

**`[PI-B-030]` An import is an attended state change.** Remount `rw`, run the
ingest, `sync`, remount `ro`. The window in which this partition can be
corrupted is the window in which someone is standing there.

---

## 4. Partition C — listener state

**`[PI-C-010]` The only partition written while unattended, and the only one
whose loss is survivable.** Every unattended write in the system lands here by
construction, which is what keeps A and B safe.

Contents: `listener.db` (§5), logs, and the backup snapshots `[REQ-LIB-160]`
already produces.

**`[PI-C-020]` It holds the only irreplaceable data in the system**
`[SPEC-SC-090]` — 37,237 plays, 3,261 preferences, the programmes. Nothing
re-derives it. This is the tension the whole design turns on: the most
volatile partition carries the least replaceable data.

Three things follow.

**`[PI-C-030]` Off-device backup is not optional here.** `[REQ-LIB-160]`'s
snapshots must be copied off the Pi, not merely written to partition C, or
they share the fate of what they protect.

**`[PI-C-040]` The player must start without it.** A corrupt or absent
partition C means: recreate an empty `listener.db`, log loudly, and play. A
Lempi that refuses to make sound because it has lost its play counts has
mistaken its own bookkeeping for its purpose.

**`[PI-C-050]` `synchronous=FULL` here, unlike the library.** WAL with
`synchronous=NORMAL` can lose the last transactions on power loss. For the
library that is meaningless — it is a cache. For the listening it is the loss
of exactly the data nothing can rebuild, so this partition pays the fsync.

---

> **Split on 2026-09-10.** The database split and its filesystem are now
> [PI023](PI023-the-database-split-on-disk.md), and the tuned settings
> [PI024](PI024-appliance-settings.md).
## 6. Toward an image build

**`[PI-IMG-010]` The deliverable is a script, not a procedure.** A documented
sequence of manual steps is a procedure nobody performs identically twice. Not
yet written — a future *LempiPi/build-image.sh* (no such file exists yet; not a
citation) should take a Pi OS Lite image and a prepared `library.db` and
produce a flashable `.img`.

Sketch, in the order the steps depend on each other (unimplemented — no such
script exists in the tree yet):

1. start from Pi OS Lite (64-bit, Bookworm), `aarch64`;
2. repartition: A ~4 GB, C ~1 GB, B the remainder;
3. install `lempi` (built via `build/Dockerfile.aarch64`) and its unit file;
4. `/var/log` → bind mount to C; journal volatile; `/tmp` capped;
5. seed B with `library.db` and the audio; seed C with an empty `listener.db`;
6. enable the overlay on A **last** — every step above needs a writable root;
7. first-boot service: verify C, recreate it if absent `[PI-C-040]`, start
   playing before the network is up `[REQ-HW-110]`.

**`[PI-IMG-020]` Order matters at step 6.** Enabling the overlay before the
seeding is done leaves an image that boots, appears correct, and has silently
discarded everything written after the overlay went on.

---

## 7. What is not yet decided

- ~~**Filesystem for C.**~~ Answered in §5a: **f2fs**, because partition C is
  already declared expendable `[PI-C-040]`, which is what makes its thinner
  recovery tooling an acceptable trade. `data=journal` is rejected as
  redundant with SQLite's own durability. Still unmeasured `[PI-FS-050]`.
- **Whether B holds the audio at all.** A USB stick or network share would
  make B small and the image portable between libraries.
- **`[PI-IMG-030]` No ARM64 build has been produced or run.**
  `build/Dockerfile.aarch64` exists as a build target and has not been
  exercised. Every measurement in this project is from x86 Windows, and
  nothing in this document is validated on the target hardware. The filesystem
  recommendation in §5a and the RAM budget in §2 are the two places where that
  matters most: both are reasoning, and both are testable.
