# BOSE007: Migrating the Library

**Appliance Record — moving the audio and the database onto the card**

Split from [BOSE003](BOSE003-build-procedure.md) on 2026-09-10, which had
reached 340 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [BOSE003](BOSE003-build-procedure.md) for the build itself ·
> [BOSE002](BOSE002-image-build.md) for the layout

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-2 in [BOSE003](BOSE003-build-procedure.md), §3-4 in [BOSE007](BOSE007-migrating-the-library.md).

## 3. Migrating the library

**`[IMPL-BOS-085]` Superseded 2026-09-06 — the local library was already
Vipunen-processed, so that is what got deployed, not the old card's raw files.**
This section originally planned to copy MuLibPlay's raw audio off the old
card and run Vipunen's full ingest (segmentation, MusicBrainz identification,
flavor extraction) on `bose` from scratch. That plan was never exercised,
because a better source turned out to already exist: `data/lempi_new.db` —
the canonical, live library `[data/README.md](../data/README.md)` — already
carries 5,709 files, flavor for 99.93% of passages, and years of relinked
listening history, sourced from `C:\Users\<dev host>\Music`. Deploying that
instead skips the entire ingest pipeline.

The procedure actually used, over WiFi from the dev host (no reader needed for
this part — see the caveat below):

1. `rsync` the local audio root to `pi@bose:/srv/library/audio/`, then
   `data/lempi_new.db` to `pi@bose:/srv/library/library-new.db` — **staged, not
   live**, same discipline `scratch/transfer.sh` already established for
   `lempi02w`: "the file it would replace holds this appliance's own play
   history." For a brand-new `bose` there is no history yet to protect, but
   the staging step stays, so the discipline does not depend on remembering
   which cards need it.
2. Cross-compile `relink` (`player/src/bin/relink.rs`, `[SPEC012]`) for
   aarch64 and run it on `bose` against the staged db and `/srv/library/audio`
   — it rebinds every row by content hash rather than the dev-host path baked
   into the db, and needs `ffmpeg` on `PATH` to do it.
3. Install the relinked db as `/var/lempi/listener.db` — C, not B, per
   `[IMPL-BOS-078]` — as the deliberate "swap it in" step.

**This host has no `rsync`** — confirmed while building this card, the same
absence `[IMPL-BOS-100]` §1 already found for a card reader. The transfer ran
inside a throwaway Alpine container (`apk add rsync openssh-client`), mounting
the local audio root, `data/`, and `.ssh` read-only — the exact pattern
`scratch/transfer.sh` already used for `lempi02w`, just pointed at `bose`.

**`[IMPL-BOS-086]` Left undone: ~1,500 files on the old card never reached
`lempi_new.db`.** The old card holds 7,238 files against the 5,745 in the
local library — tracks that were apparently never carried into the canonical
db. Deferred deliberately rather than blocking this build; reconciling them
is an ordinary Vipunen ingest pass, not a reason to hold up a working appliance.
If it's ever done by copying straight off the old card instead, `[IMPL-BOS-080]`
below still describes how.

**`[IMPL-BOS-080]` The reader-based copy, kept as the alternative.** With a
USB reader, the **old** card can go into it on `bose` (after the swap), and a
copy off it is local — SD to USB3 — rather than over WiFi `[PI-BOS-050]`.
`rsync -aH --info=progress2` from the old card's `/home/pi/Music` to B's
`/srv/library/audio`, resumable, verifiable, and driven over SSH from here.
This is the right tool specifically for reconciling `[IMPL-BOS-086]`'s
leftover files, or for a future second appliance with no pre-existing local
library to draw on.

Without a reader the alternative is two WiFi transfers — off `bose` to this host
before the swap, and back afterwards — which is hours each way and has no
resumable middle. That is the comparison that justifies buying one.

> **The old card is the backup**, and remains one until the new image has played
> for a week. Do not reformat it to make anything easier. `bose` is somebody's
> working music player, with MuLibPlay running on it now, per `[PI-BOS-040]`;
> the swap is a physical card change and is reversible in thirty seconds by
> swapping back, which is the strongest reason to build a new card rather than
> convert this one.

---

## 4. Open, and to be measured before it is claimed

- **`[IMPL-BOS-130]` A USB SD card reader has to be obtained.** Nothing in §1
  or §3 happens without one, and no software substitute was found: this host
  has no card slot, and its WSL carries only `docker-desktop`.

- **`[IMPL-BOS-095]` Does the seek fault exist here?** `[SPEC-MPD-135]` works
  around an output wedged by `seekid`, measured against PipeWire on Bluetooth
  `[PI-CHR-100]`. `bose` has a local I²S card and may not need PipeWire at all.
  Measure it — with a silent baseline first — before deciding whether the
  workaround is load-bearing on this machine.
- **Whether PipeWire is wanted at all.** If nothing needs to mix, MPD and Lempi
  could take turns on ALSA directly. That would remove a whole layer, and it
  would also remove the ability for both to sound at once, which the handoff
  depends on `[SPEC-BK-030]`. Not free either way.
- **f2fs for C remains unmeasured** `[PI-FS-050]`. The power-pull rig described
  there has still not been built.
- **The DAC's real rate and format range** `[PI-BOS-060]`, once PulseAudio is
  not holding the device.
- **Whether 8 GB for A is right.** Guessed from Bookworm's footprint, not
  measured.

---

**Traceability:** `[IMPL-BOS-100..130]` · applies `[PI-PART-020]`'s A/B/C ·
supersedes `[PI-A-020]`'s volatile journal · survey in
[BOSE001](BOSE001-survey.md) · mixer per `[SPEC-MPD-140]` · build tooling owes
`[PI3-API-030]` the same honesty as the player

