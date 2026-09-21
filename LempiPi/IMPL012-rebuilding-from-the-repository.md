# IMPL012: Rebuilding the Appliance from the Repository

**Implementation Guide — a fresh card, and nothing but this tree**

Split from [IMPL001](IMPL001-appliance-setup.md) on 2026-09-10, which had
reached 305 lines against `[GOV-DOC-010]`'s 300-line limit. This is the
reproducibility claim and what it rests on, kept separate because it is the
part a reader checks rather than follows.

> **Related:** [IMPL001](IMPL001-appliance-setup.md) for the setup itself ·
> [PI001](PI001-image-and-partitions.md) for the partitions

---

## 9. Open

1. ~~**`[IMPL-OPN-010]`** Which audio output~~ — **DECIDED: Profile A (Bluetooth) is tested first** `[IMPL-PROF-020]`, with B–D kept supported and configurable `[IMPL-PROF-030]`. Its boot delay is accepted for that profile alone.
2. ~~**`[IMPL-OPN-040]`** bluez-alsa vs PipeWire~~ — **DECIDED (revised 2026-09-02): PipeWire**, built and measured on real hardware `[PI2-KNOWN-010]`, [PI006](PI006-appliance-characterisation.md). The original footprint-driven `bluez-alsa` decision below did not survive contact with the device — see §5's superseded note.
2. **`[IMPL-OPN-020]`** Whether 64-bit is right at 512 MB. It matches the verified build, but 32-bit uses less memory for pointer-heavy work. Lempi's footprint is buffer-dominated, so the difference should be small — worth measuring both if the margin proves tight.
3. **`[IMPL-OPN-030]`** Where the class-D export goes off-device `[SPEC-DF-094]`, since on-card backups die with the card.

## 10. Rebuilding this appliance from the repository

**`[PI3-REPRO-010]` Audited 2026-09-09, and it did not reproduce.** A fresh
card built from this repository alone would have come up without the entire
reconnection mechanism. `setup-lempipi.sh` installed six helpers and never
mentioned `lempi-speaker`, its service, or its timer — so the appliance would
not have re-asserted trust `[PI3-FOUND-130]`, would not have chased the
speaker after a power cycle `[PI3-FOUND-090]`, and would not have noticed the
player's stream sitting on the wrong sink `[PI3-AIM-050]`. Nothing would have
reported the absence; it would simply never have reconnected.

Four more pieces existed only on the SD card:

| Missing | What its absence costs |
|---|---|
| `lempi.service.d/mpd-guest.conf` | The **real command line**. The base unit's argument is the pre-split single database, so the player would read the catalog as its listener store `[IMPL-DBSPLIT-025]` |
| `lempi.service.d/20-lempi-io.conf` | The player's I/O precedence |
| `mpd.service.d/10-lempi-polite.conf` | `mpd` competing for the card during startup — 19 s of it `[PI3-FOUND-210]` |
| `/etc/tmpfiles.d/lempi-readahead.conf` | `bfq`, without which every I/O-priority setting here is inert `[PI3-FOUND-250]` |

And two working tools, `lempi-rocker` and `lempi-radio-test`, existed nowhere
but the card despite being the instruments behind `[PI3-ROCKER-010]` and the
interference measurements in section 1.

All of the above is now staged in `LempiPi/` and installed by
`setup-lempipi.sh`, which also gained the `add-wants` that `[PI3-FOUND-030]`'s
remedy actually needs. Verified by generating the units and comparing them
byte-for-byte against the running appliance, and by exercising the installer's
idempotence rather than assuming it.

**Per-instance state is deliberately not reproduced.** The databases and the
BlueZ link keys in `/var/lib/bluetooth` are built fresh on each player — a new
instance scans its own library and pairs its own speaker, and copying either
between machines is how `speaker_address` went stale in the first place
`[PI3-AIM-040]`.

**Not a gap after all: the kernel command line.** An earlier draft of this
section listed `snd_bcm2835.enable_hdmi=0` and `enable_headphones=0` as
settings the repository failed to prescribe. They are not settings. The
firmware derives them from the board and `config.txt`, so a rebuilt card gets
them automatically and behaves identically — see
`[IMPL-AUD-005]` in section 5, which records what actually makes this a
Bluetooth-only player.

