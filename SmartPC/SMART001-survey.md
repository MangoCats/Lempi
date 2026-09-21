# SMART001: What Is on `smartboardpc` Today

**Measurement — Tier 1 · surveyed on `mango@smartboardpc`, 2026-09-11**

A reconnaissance of the fourth node before anything is built for it. Most
figures here were read from the running machine; the four changes that were
*made* are marked as such in §4, because a survey that quietly includes its own
side effects is not a survey.

`smartboardpc` — "Smart" — is not an appliance and not a second
`teacherslounge`. It is an **x86_64 mini-PC that is intended to play audio**,
which makes it the first node that is both a build host and a playback target.
Its audio reaches a powered speaker through a **USB dongle whose playback
endpoint is adaptive**, and that single fact decides most of what
[GUIDE008](../docs/GUIDE008-echo-playback-investigation.md) can say about it.

> **Related:** [BOSE001](../BosePi/BOSE001-survey.md) — the same survey done for `bose`, whose I²S DAC is this machine's opposite in every respect that matters here · [GUIDE008](../docs/GUIDE008-echo-playback-investigation.md) `[GDE-ECHO-075]` — why the adaptive endpoint matters · [GUIDE009](../docs/GUIDE009-echo-playback-plan.md) — the measurement phase this node is a subject of · [build/deploy-everywhere.sh](../build/deploy-everywhere.sh) — the source-host leg it was added to

---

## 1. The machine

**`[SMT-HW-010]` An Intel Celeron N5105 under Linux 6.8, x86_64, on eMMC.**

| | |
| :--- | :--- |
| CPU | Intel Celeron N5105 @ 2.00 GHz (Jasper Lake, 4 core) |
| Kernel | 6.8.0-137-generic, x86_64 |
| Root | `/dev/mmcblk0p2`, 113 G, 45 G used, **63 G free** |
| `/boot/efi` | `/dev/mmcblk0p1`, 511 M — EFI boot, not a Pi's `config.txt` world |
| RAM | 7.7 G (from `tmpfs` sizing: `/dev/shm` 3.8 G) |

It boots UEFI from eMMC and shares none of the A/B/C overlay design
`[PI001]` that both appliances use. Nothing here is read-only; the root
filesystem is ordinary and writable.

---

## 2. Audio — the part that decides everything else

**`[SMT-AUD-010]` The onboard `HDA Intel PCH` has no analog output at all.** It
enumerates devices 3, 7, 8 and 9 — **HDMI 0 through HDMI 3, and nothing else**.
Any expectation that this machine has a motherboard headphone jack is wrong, and
was wrong in the brief that prompted this survey.

**`[SMT-AUD-020]` Sound reaches the powered speaker through card 1, an
`Anlya.cn ATE1133` on USB.** Two USB audio devices are present and both are
plausible candidates, so the answer was obtained by **listening, not by
inference** — alternating tone patterns were played on each card in turn and the
one that came out of the speaker was identified by ear, 2026-09-11.

| card | device | bus |
| ---: | :--- | :--- |
| 0 | `USB AUDIO CODEC` — Burr-Brown/TI | `usb-0000:00:14.0-3.1.3` |
| **1** | **`ATE1133` — Anlya.cn** ← the speaker | `usb-0000:00:14.0-7`, **full speed** |
| 2 | `HDA Intel PCH` | HDMI only `[SMT-AUD-010]` |

**`[SMT-AUD-030]` It must be selected by name, never left to a default.** The
player takes `--device` as a case-insensitive substring `[BOS-PWR-050]`, so
`--device ATE1133` is the correct configuration. A default would be a coin
toss between two real output devices, and `[BOS-PWR-050]` already recorded what
ALSA card renumbering does to an assumption like that.

**`[SMT-AUD-040]` Its playback endpoint is `ADAPTIVE`, so the sample clock is
the host's, not the device's.** Read from `/proc/asound/card1/stream0`:

```
Playback:
  Interface 1, Altset 1
    Format: S16_LE      Channels: 2
    Endpoint: 0x03 (3 OUT) (ADAPTIVE)
    Rates: 48000, 96000
```

An adaptive sink slaves its conversion rate to the rate the host feeds it. This
is the **reverse** of an asynchronous device, where a crystal in the dongle
would be master and the host would follow. Smart's drift is therefore a property
of the Intel USB controller's frame timing and the driver's feed — not of
anything in the dongle, and **not predicted by any figure measured on `bose`**,
whose I²S DAC is master of its own clock `[PI-BOS-020]`. The two nodes' rate
errors do not share a physical cause.

**`[SMT-AUD-050]` There is no 44100 — only 48000 and 96000.** The library is
44.1 kHz, so **every passage is resampled on this node, always**, through
`player/src/resample.rs`. There is no bit-exact pass-through case on Smart to
protect, which changes one of the two arguments in `[GDE-ECHO-210]` for this
machine specifically. Format is `S16_LE`, two channels; the capture endpoint is
asynchronous but is a microphone input and irrelevant here.

---

## 3. Time

**`[SMT-TIME-010]` chrony, since 2026-09-11 — it shipped on
`systemd-timesyncd`.** As found, chrony and chronyd were both inactive and
timesyncd was active with `timedatectl` reporting *System clock synchronized:
yes*. That satisfies a wall-clock requirement and silently fails a frequency
one: timesyncd is an SNTP client, correcting *what time it is* without
disciplining *how fast the clock runs*, which is the half `[GDE-ECHO-100]`
actually needs.

Installing chrony deactivates timesyncd on its own. Five seconds after start:

```
System time     : 0.000001428 seconds fast of NTP time
Frequency       : 5.043 ppm fast
Residual freq   : +305.427 ppm
```

**That residual is not converged and must not be read as a measurement.** It is
the figure Phase 1 would otherwise mistake for DAC drift, and chrony needs
considerably longer than five seconds to settle it — the point of recording it
here is that the number changes, so any drift measurement taken before it
stabilises is measuring chrony, not the hardware `[GOV-SRC-020]`.

---

## 4. What was changed, and why

Four changes were made on 2026-09-11 rather than merely observed. They are
listed because the survey framing — read-only — does not apply to them. The
fourth, chrony, is described in §3 where its effect belongs.

**`[SMT-BLD-010]` There was no Rust toolchain; rustup was installed.** The
machine had a clean checkout at `/home/mango/Dev/Lempi` on the correct origin,
already at `122518a`, but no `cargo`, no `rustc`, and nothing in `dpkg`. It
therefore could not have been a source host in
[build/deploy-everywhere.sh](../build/deploy-everywhere.sh)'s sense. Installed
to `~/.cargo/bin`: **cargo 1.98.1, rustc 1.98.1**. `cc` and `gcc` were already
present at `/usr/bin`, and no global `CC` is set.

**`[SMT-BLD-020]` `libasound2-dev` and `pkg-config` were missing, and were
installed.** Without them cpal's `alsa-sys` build script fails, so the
source-host leg would have failed on every run — which is worse than not having
the leg, for the reason `[SPEC-SUI-227]` already gives: a report that always
contains one failure trains people to stop reading it. `pkg-config
--modversion alsa` now answers 1.2.11. `mango` has NOPASSWD sudo.

**`[SMT-BLD-030]` It was added to `deploy-everywhere.sh` as a source host.**
`mango@smartboardpc:/home/mango/Dev/Lempi`, on the same leg as
`teacherslounge`: pull, rebuild, and verify by asking the **built binary** its
commit. That is the right verification *today*, because nothing is running. When
Smart actually plays, it becomes the first target that wants both checks — the
binary's commit and a running player's `/build` — and the script does not model
that yet.

**Verified rather than assumed**, 2026-09-11: the leg was run for real, built
from cold, and answered `lempi 0.1.0 (122518a1347f)` — matching HEAD. The
resulting binary links `libasound.so.2`, so `[SMT-BLD-020]`'s packages did the
job they were installed for and the node can actually open a device rather than
merely compile.

---

## 5. Where Lempi's own state must go

The audio stays on the PortableSSD `[SMT-OPN-010]`. The databases cannot follow
it, and the reason is not the 11 G.

**`[SMT-DB-010]` No SQLite file may live on the PortableSSD, because it is
exFAT.** `findmnt` reports `/dev/sda1 exfat rw,nosuid,nodev,relatime,uid=1000,…`.
SQLite's locking is built on POSIX advisory locks, and its WAL mode additionally
needs a shared-memory file; exFAT offers neither with the semantics SQLite
requires, and has no ownership model of its own — the `uid=1000` above is a
mount option, not a property of the files. This is a **correctness** objection,
not a performance one, and it would hold even on an empty 1.9 T volume.

**`[SMT-DB-020]` The library path is a udisks automount, which no boot-time
service can depend on.** `/media/mango/PortableSSD` appears in no `fstab`; it is
mounted by the desktop session. A `lempi` unit starting at boot would find an
empty directory and, worse, could create one — the failure mode being a player
that starts healthily with no library rather than one that refuses. Before Smart
plays anything, that volume needs an `fstab` entry **by UUID, with `nofail`**,
and the unit needs to require it. The same instinct as `[BOS-PWR-050]`'s "find
the card by name": never depend on a path someone else chose for you.

**`[SMT-DB-030]` The databases go on the eMMC root, at `/var/lempi/`.** 61 G
free, `ext4`, present at boot, and outside `/home/mango/Dev/Lempi` so that
`update-source-host.sh`'s `git pull` can never interact with runtime state. The
path follows `bose`'s convention `[IMPL-BOS-078]` rather than inventing a
third — Smart has no A/B/C partitioning to reason about, so the only thing the
convention has to buy is consistency, and it costs nothing. Cover-art and lyrics
caches, `[REQ-LIB-160]` backups and logs land beside it.

Sizing, from the desktop's own files: a catalogue of this size is ~1.2 GB and
listener state ~6.5 MB. Both are comfortable against 61 G. Whether Smart uses
one `lempi.db` or the split pair is open — see `[SMT-OPN-040]`.

---

## 6. Standing findings not related to audio

**`[SMT-STO-010]` `/media/mango/PortableSSD` is full.** 1.9 T, **100 % used, 11
G available**. Whatever it is for, it has no room left.

**`[SMT-STO-020]` `/mnt/piback-backups` returns `Input/output error`.** `df`
cannot stat it. A stale or dead mount, reported here because a backup target
that errors on inspection is the kind of thing that is discovered when it is
needed rather than when it breaks.

---

## 7. Open

**`[SMT-OPN-010]` Smart has a candidate music library, on a volume with no room
left.** `/media/mango/PortableSSD/Media/Music` — **49 G, 5,719 audio files**,
the same Mac-origin library the rest of the fleet carries, in a variant
revision. Compared against the desktop's copy by album and track, the genuine
delta is **36 files it lacks** (Xavier Rudd's *White Moth* and *Storm Boy*, two
Gerardo Frisina albums, *Mangocats/Tropicat*) and **12 it has that the desktop
does not** (Thomas Dolby's *The Golden Age of Wireless*, and two singles) — a
path-derived figure, not a hash, per `[GDE-ECHO-480]`.

The problem is the volume, not the content: `[SMT-STO-010]` has it at 100 % with
11 G spare, so the library cannot grow where it sits and Lempi cannot write
beside it. Root has 63 G free, which would hold the library but leaves little
margin. Until that is resolved Smart is a build host that could play, not a
playback node.

**`[SMT-OPN-020]` Whether it joins the mesh as a Vipunen-capable peer is
undecided.** [SPEC035](../docs/spec/SPEC035-mesh-library-sync.md)'s membership
list is explicit and human-maintained `[SPEC-MESH-025]`; Smart is not on it, and
adding it is a decision about what this machine is *for*, not a consequence of
it existing.

**`[SMT-OPN-040]` One `lempi.db` or the split pair is undecided.** `bose` runs
a single file `[IMPL-BOS-078]`; the desktop runs `listener.db` plus
`--library library.db`, and `attach_library()` has supported the split since
2026-09-06. Nothing about Smart's storage forces the choice, since both files
would sit on the same `ext4` root `[SMT-DB-030]` — so it should follow whatever
the fleet settles on rather than being decided here.

**`[SMT-OPN-030]` Its drift has not been measured.** It is a Phase 1 subject in
[GUIDE009](../docs/GUIDE009-echo-playback-plan.md), and `[SMT-AUD-040]` is the
reason its number cannot be guessed from `bose`'s.
