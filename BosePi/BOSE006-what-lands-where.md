# BOSE006: What Lands Where on the Card

**Appliance Record — the contents of each partition, and why each is there**

Split from [BOSE002](BOSE002-image-build.md) on 2026-09-10, which had reached
327 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [BOSE002](BOSE002-image-build.md) for the layout ·
> [BOSE003](BOSE003-build-procedure.md) for building it

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-2 and §4 in [BOSE002](BOSE002-image-build.md), §3 in [BOSE006](BOSE006-what-lands-where.md).

## 2a. The four bind mounts, which are not optional

**`[BOS-LAND-060]` A read-only root needs four directories bound from the
state partition, and until 2026-09-15 that was recorded only in `bose`'s live
`fstab`.**

    /var/lempi/log            /var/log
    /var/lempi/etc-ssh        /etc/ssh
    /var/lempi/home-pi        /home/pi
    /var/lempi/nm-connections /etc/NetworkManager/system-connections

Without them logs vanish at every boot and NetworkManager can never save a
change -- and that last path is exactly what stranded this machine in
`[IMPL-BOS-175]`. They total about 1.3 MB to seed.

Written down because a plan to give another node this same shape compared the
*partition tables*, found them identical, and concluded the conversion was "a
package and a kernel parameter" `[IMPL-VP3-160]`. The tables were identical.
The fstab was not, and nothing outside the machine said so.

## 3. What lands where

**A — system, read-only.** Kernel, Bookworm Lite, `lempi` binary and unit,
`mpd` and its unit drop-in, `/etc`. `[PI-A-030]`: nothing about the library or
the listener.

**`[IMPL-BOS-185]` Nothing reaches A after lock-in unless it is written through
the overlay on purpose — and for five days nothing was.** Every deploy to
`bose` between lock-in on 2026-09-06 and 2026-09-11 wrote only to the overlay's
tmpfs upper layer: the service restarted, ran the new binary, `/build` answered
with the new commit, `deploy-everywhere.sh` printed *matches HEAD*, and the
whole thing evaporated at the next reboot. Found when `bose` was rebooted for
the first time since, and came back reporting `358c5b176833` with the binary on
A dated **Sep 6 16:56**.

`deploy-everywhere.sh` could not have caught it. Its verification asks the
**running** player, which its own header rightly calls the only check that
catches a service still serving something older than what is on disk. That is
true, and structurally blind to the inverse — *the disk is RAM* — because the
check passes precisely when the ephemeral copy is the one running.

**The binary was the lesser half. The unit file reverted too, and that is what
stopped the music.** `bose`'s database had been split into
`/var/lempi/listener.db` (C) and `/srv/library/library.db` (B) — both on
persistent partitions, both intact. But the `lempi.service` that knew about the
split lives in `/etc/systemd/system`, which is **on A**. It reverted to the
Sep 6 version pointing at the pre-split `/var/lempi/listener.db`; the player found
no such file, created an empty 98 KB one, and logged `refill: query: no such
table: passages` with no catalogue at all. Restored 2026-09-11 by writing the
split `ExecStart` to both A and the live overlay (originals kept as
`.pre-split-revert`).

`build/install-player.sh` now detects an overlay root on the target, reads its
`lowerdir`, and writes the binary to both layers — **after** the new binary has
answered, so A always keeps the last build that actually started. Proven across
a reboot: live and A both `ba3d3e95…`, `/build` reporting the deployed commit
rather than reverting.

**Still open: nothing persists unit or config changes.** `provision-bose.sh`
writes them, but only ever pre-lock-in. Any `/etc` edit made on a locked `bose`
by any route other than `overlayroot-chroot` `[IMPL-BOS-180]` is a write to RAM
that will look correct until the next reboot.

**B — library, read-only except during import.** The 44 GB of audio and cover
art, and the `.cue` sheets if they are ever enabled `[REQ-VIS-205]`. MPD's own
database — its index — belongs here too and not on A: it is derived from B's
contents, it is rebuilt by `update`, and it changes exactly when B changes.

**`[IMPL-BOS-078]` `lempi.db` itself goes on C, not B — a correction, found
building this card.** This section originally listed `library.db` as living
on B, per `[PI-DB-010]`'s split design.

> **Superseded 2026-09-11.** This paragraph went on to say the split "was
> never built: the player still opens one `lempi.db`, no `ATTACH`". Both
> halves of that are now false. `attach_library()` landed 2026-09-06 in
> `ec03371`, was threaded through the catalog queries in `2f2e81c`, and
> `269fa10` fixed a foreign-key problem "found live on lempi02w's real split"
> the next day. `bose` itself runs the split today —
> `/var/lempi/listener.db` on C, `/srv/library/library.db` on B — as the
> RAM-only deploy `[IMPL-BOS-185]` found the hard way. The conclusion below
> still holds for the **listener** half, and for the same reason: every play is a write.

Whichever file holds listener state has to go somewhere that stays writable,
because every play is a write to it.

`lempi02w` ran the same single file under `/srv/library` until its own split on
2026-09-10 — it now opens `/var/lempi/listener.db --library
/srv/library/library.db`, confirmed live 2026-09-12 — and got away with it
(`[PI002](../LempiPi/PI002-test-image-setup.md)`,
`[PI005](../LempiPi/PI005-appliance-library.md)`) only because that partition
is never actually made read-only in practice — `lempi02w` has no C, and its
data partition just stays `rw` forever. `bose` is not that: step 10 of
`[BOSE003](BOSE003-build-procedure.md)` genuinely flips B to `ro`. Putting
`lempi.db` there would mean every play after that point fails to write. So
until the split is built, `lempi.db` lives at **`/var/lempi/listener.db`** — C,
not B — and B holds only audio, cover art, and MPD's own derived index.

> **The split was built on 2026-09-11, and the halves did not both go to C.**
> Current paths on `bose`: the **catalogue** is `/srv/library/library.db` on
> **B** (`/dev/mmcblk0p4`, `ext4 ro`), and the **listener** half is
> `/var/lempi/listener.db` on **C** (`/dev/mmcblk0p3`, `f2fs rw`). The
> paragraph above still explains why the *listener* half cannot live on B; the
> catalogue can, because after the split nothing writes it in normal
> operation. Every `/var/lempi/listener.db` elsewhere in this directory is a
> record of what was done before that date and is left as written
> `[GOV-DOC-050]`.
>
> One consequence is live and unresolved: because B is genuinely `ro`, a
> **Vipunen push cannot land on `bose`** — `sqlite3` against the catalogue half
> fails every time, so `bose` is excluded from pushes `[SPEC-DF-126]` until
> that path learns `attended-import.sh`'s remount window `[IMPL-BOS-150]`.
> Pulling *from* `bose` is unaffected.

**`[IMPL-BOS-150]` The attended-import operation, finally built.**
`[PI-B-030]` said what this should be — "remount rw, run the ingest, sync,
remount ro" — from this project's earliest design pass; nothing carried it
out until now. Asked directly, checking the actual scripts rather than the
design doc, found the gap: B isn't even set `ro` by default
(`provision-bose.sh`'s own `fstab` line is plain `defaults,noatime`, `rw`
until `[BOSE003]` step 10's `--lock-in` flips it once), and nothing
performed the reopen-import-reclose cycle, because `seed-library.sh` never
needed to — it always ran before B was ever `ro` in the first place.
`BosePi/attended-import.sh` (dev host, over SSH, the same home every other
phase script has) closes it:

1. Reads B's *current* mount options and remembers them, whatever they are
   — `rw` pre-lock-in, `ro` after. This is what makes "restore afterward"
   correct in both worlds, rather than assuming lock-in has already
   happened.
2. Remounts `rw` only if it was not already — an asked-twice `--check`/
   `--go` decision, the same discipline `[IMPL-BOS-110]`'s destructive
   scripts already use: a wrapped command that misbehaves can corrupt
   exactly the partition this whole design protects, so nothing here
   defaults to acting.
3. Runs the caller's command verbatim inside the window — new audio via
   `rsync`, a mesh-approved bundle's `audio/` directory, whatever is
   needed. The script has no opinion on what belongs in the window, only
   that the window exists and closes.
4. `sync`s, then restores exactly the options step 1 captured — via a
   trap, so a wrapped command that fails or is interrupted still closes
   the window rather than leaving B open indefinitely.
5. Best-effort MPD reindex afterward (skippable with `--no-mpd-update`) —
   B's own index changed if the audio did, and every caller adding audio
   needs this, so it is default-on rather than one more step to remember.

**Run for real against `bose` 2026-09-06, four ways**: a dry run, a
successful command, and a failing one (confirmed B still closes and nothing
after it runs) against a hand-simulated `ro`, before `--lock-in` had ever
run — then a fourth run against `bose`'s own real `--lock-in`'d `ro` once
it existed, confirming the hand-simulated tests generalized correctly.
MPD's reindex trick needed no `mpc` client
(`provision-bose.sh` never installs one) — bash's own `/dev/tcp` speaks the
control port directly.

**`[IMPL-BOS-155]` This is the missing half of a mesh-approved import, not a
separate feature.** `[SPEC035](../docs/spec/SPEC035-mesh-library-sync.md)`'s
`mesh_diff.py`/`resolve_mesh_conflict.py` only ever touch
`/var/lempi/listener.db` — correct, since C is what stays writable regardless
of lock-in — but a `local_only`/`peer_only` bundle's audio bytes still need
somewhere to land, and B was the half nothing wrote yet.
`attended-import.sh` is that half, run the same way the original seed
(`[IMPL-BOS-085]`) did before B was ever locked: `rsync` the bundle's
`audio/` into `/srv/library/audio/` inside the window it opens.

> **How to change any of this after `--lock-in` is its own subject**, with its
> own traps: see [BOSE010](BOSE010-changing-a-locked-card.md). The short form
> is `overlayroot-chroot` `[IMPL-BOS-190]`, never the escape hatch.
