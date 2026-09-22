# LOG010: Power-Loss Risk on the Two Pi Nodes

**Measured survey + bounded estimate — `bose` and `lempi02w`, 2026-09-15**

A *rate* is what `[PI-FS-050]` has asked for since PI023: how often does a Pi
losing power with the database open cost something. [BOSE005](../BosePi/BOSE005-power-loss-test.md)
answered it once, with one clean cut. One trial is not a rate, and a rig that
counts cuts does not exist yet. This document does the next best thing: it
measures the two machines as they stand today, decomposes the risk into the
four layers that can actually fail, and puts a **bounded** number on each —
with the width of the bound stated rather than hidden.

> **Read the bounds, not the midpoints.** The dominant term spans two orders
> of magnitude and the published evidence cannot narrow it. Where this
> document gives a single figure it is a midpoint of a stated band, offered so
> the two nodes can be *compared*, not so either can be quoted.

---

## 1. What was measured, and on what

Both machines were inspected read-only while in service on 2026-09-15. Nothing
was changed and no service restarted.

**`[SD-RISK-010]` The two nodes differ in writable surface by a factor of about
sixty, and in write *volume* by almost nothing.** This is the finding the rest
of the document turns on, and it is not the one the overlay's reputation
suggests.

| | `bose` | `lempi02w` |
| :--- | :--- | :--- |
| Board | Pi 4 Model B rev 1.1 | Pi Zero 2 W rev 1.0 |
| Card | SanDisk `SR128`, 119.1 GB, mfg 05/2020 | Samsung `JE4S5`, 238.8 GB, mfg 06/2023 |
| Root | `overlayroot` tmpfs over `/dev/mmcblk0p2` **ro** | `/dev/mmcblk0p2` ext4 **rw**, 235 GB |
| Boot partition | `/boot/firmware` vfat **ro** | `/boot/firmware` vfat **rw** |
| Library | `/srv/library` 106.6 GB ext4 **ro** | (on the root) |
| Writable state | `/dev/mmcblk0p3`, **4 GB f2fs**, `/var/lempi` | the whole root |
| Swap | **zram0**, 1.8 GB, in RAM | **`dphys-swapfile`**, 512 MB `/var/swap` *on the card* |
| Writable LBA span | **~4 GB** | **~235 GB** |
| Block writes measured | 4.93 GB / 3.82 d = **1.29 GB/day** | 4.04 GB / 2.17 d = **1.86 GB/day** |
| Filesystem lifetime writes | root: 5,270 MB in 90 d (frozen) | root: **89 GB in 90 d ≈ 0.99 GB/day** |
| Undervoltage ever | `throttled=0x0` | `throttled=0x0` |

`bose`'s f2fs carries `barrier`, `discard`, `checkpoint_merge`,
`fsync_mode=posix` and a 60 s checkpoint interval; `fsck.f2fs` (f2fs-tools
1.16.0) is installed and the partition has `passno 2`, so a damaged checkpoint
is repaired at boot rather than mounted around.

`lempi02w`'s ext4 is `rw,noatime`, `data=ordered`, 64 MB journal with
`journal_checksum_v3`, `fsck.repair=yes` on the kernel command line — and
**`Errors behavior: Continue`**, so a metadata inconsistency noticed at runtime
does not remount read-only; it carries on.

**`[SD-RISK-020]` Both listener databases are in WAL with SQLite's default
`synchronous=FULL`, and nothing in the crate changes either.** `player/core/src/db/mod.rs`
sets only `PRAGMA foreign_keys`; the WAL mode came from `[PI-OWE-030]`'s
2026-09-11 change. `page_size` 4096, `wal_autocheckpoint` 1000 pages = 4 MB on
both. Measured on `bose` while playing: the WAL grows **961 B/s**, so it
reaches the 4 MB threshold about **every 69 minutes**. Dirty page cache sat at
**336–424 kB** on both nodes across the sampling window — that is the entire
volume of not-yet-written data a cut would find in RAM.

> `lempi02w`'s audio device read `closed` during this survey, so its figures are
> idle-state figures and its WAL was static. Its playing-state write rate should
> be assumed similar to `bose`'s, not lower.

**`[SD-RISK-030]` No corruption appears in either machine's surviving kernel log
— and the logs are too short for that to mean much.** `bose`'s journal spans 3
days, `lempi02w`'s 4; both have rotated. Zero `ext4`, `f2fs`, I/O or mmc errors.
Boot counting from `wtmp` was attempted and **rejected**: neither Pi has an RTC,
so the 82 `reboot` lines on `lempi02w` carry duplicated stale-clock timestamps
and several read `still running` at once. There is no trustworthy boot count on
either machine.

**`[SD-RISK-040]` `dump.f2fs` reporting `sudden-power-off` on `bose` is not
evidence of a crash.** `ckpt_flags` is `0x44` — `CP_CRC_RECOVERY` plus
`CP_COMPACT_SUM`, `CP_UMOUNT` absent. A *mounted* f2fs always lacks the umount
flag, so `dump.f2fs` says this on every healthy live filesystem. Recorded
because it looks alarming and will be found again by whoever next looks.

## 2. Three events, and what they actually were

**`[SD-RISK-050]` The fleet's deliberate cuts have passed, and its one
real-world failure was never corruption.** These are the only data points that
exist.

- `bose`, 2026-09-10 `[BOS-PWR-010]`: one cut while playing. `quick_check` ok,
  page count identical, every row count moved up. f2fs rolled forward 13
  fsynced dnodes with `err = 0` `[BOS-PWR-020]`.

**`[SD-RISK-160]` The first cut observed since this document existed landed
where the document said the risk is, and passed.** `bose`, 2026-09-15: power
removed while playing, off ten seconds, back on.

`listener.db-wal` stood at **4,120,032 bytes** at the moment of the cut --
within 2 kB of the 4 MB autocheckpoint `[SD-RISK-020]` measures, which is the
only interval in which SQLite documents a WAL as corruptible. Clean on every
axis: `lempi-db-recover` reported *"un-checkpointed WAL on
/var/lempi/listener.db, replaying"*, `integrity_check` returned `ok` over
40,964 rows, the kernel logged **zero** filesystem errors, and playback resumed
on the same passage.

It settles something checksums could not. `[IMPL-BOS-185]` cost five days of
deploys that verified fine and vanished at reboot; after this cut the live
**and** lower copies both still read `1aced9fe...`. The durable write survives a
real power cut, not merely an `md5sum`.

It corroborates nothing statistically. At `[SD-RISK-100]`'s ~1x10^-2 per-cut
estimate a clean cut is the *expected* result, so one distinguishes no
hypothesis. What it establishes is that the recovery path runs end to end on an
unannounced cut rather than in principle.

## 3. The four layers, and which of them a filesystem can help with

**`[SD-RISK-060]` Durability loss is certain at every cut, on both nodes, and is
not a defect.** Up to 69 minutes of un-checkpointed WAL frames and up to 300 s
of drift samples are in flight at any moment. Measured at `[BOS-PWR-030]`: the
history row lagged the resume position by about two minutes, and the listener
lost nothing audible. Expect this every time; it is the design working.

**`[SD-RISK-070]` SQLite in WAL can only be corrupted during a checkpoint, and
the window is measurably small.** SQLite's own position: *"In WAL mode, the only
time that a failed sync operation can cause database corruption is during a
checkpoint operation. A sync failure during a COMMIT might result in loss of
durability but not in a corrupt database file."* With a checkpoint every
~4,160 s and a 4 MB checkpoint taking of order 0.5–2 s, the probability a
random cut lands inside one is **1.2×10⁻⁴ to 4.8×10⁻⁴**. Corruption also
requires the card to have lied about the barrier, which is not certain — so
SQLite-level corruption is **below 10⁻⁴ per cut** on both nodes.

**`[SD-RISK-080]` The filesystem layer is where the two nodes separate, and it
separates on blast radius rather than on probability.** f2fs is log-structured,
never overwrites in place, keeps two checkpoints and rolls forward fsynced
data; ext4 `data=ordered` journals metadata and replays it. Both are sound, and
neither promises anything about data an application never fsynced. The
difference is what a failure costs:

| | `bose` | `lempi02w` |
| :--- | :--- | :--- |
| A minor FS event costs | a log line, one drift sample | a log line — or an OS file |
| A catastrophic FS event costs | 4 GB of state; **the node still boots** | **the appliance** |
| Repaired at boot by | `fsck.f2fs`, `passno 2` | `fsck.repair=yes` |
| Damage containment | root, boot and library are **ro** | `errors=continue` lets it spread |

**`[SD-RISK-090]` The flash translation layer is the layer no filesystem choice
touches, and a read-only root does not make the card read-only.** This is the
least intuitive point here and the most important. The card is *one* device with
*one* FTL. While it services writes to `bose`'s 4 GB f2fs partition, its garbage
collector and wear-leveller may be relocating the physical blocks holding the
read-only root and the 106 GB read-only library. A cut during that relocation
can lose data the filesystem never wrote and cannot protect — published figure:
**up to one allocation group, typically 4 MiB on SDHC and up to 64 MiB on
SDXC**. Both these cards are SDXC, so 64 MiB is the live figure on both.

Neither card has power-loss protection. That is exactly what industrial cards
advertise and retail cards do not — sudden-power-off recovery of the mapping
table, pSLC, background refresh. `SR128` and `JE4S5` are retail parts.

## 4. The numbers, with their bounds

**`[SD-RISK-100]` Per-cut estimates.** "Minor" means a lost write, a truncated
file, or a database needing recovery — repairable in place, nothing a listener
notices beyond seconds of history. "Catastrophic" means an unbootable card,
total loss of the listener database, or a card bricked at the FTL level.

| Per unplanned power cut | `bose` | `lempi02w` |
| :--- | ---: | ---: |
| Durability loss (by design) | ~1 | ~1 |
| Minor — SQLite | <10⁻⁴ | <10⁻⁴ |
| Minor — filesystem | ~10⁻² | ~3×10⁻² – 10⁻¹ |
| Catastrophic — filesystem | ~10⁻⁴ *(survivable)* | ~10⁻⁴ – 10⁻³ *(fatal)* |
| Catastrophic — card/FTL | ~10⁻⁵ – 10⁻³, best ~10⁻⁴ | ~10⁻⁵ – 10⁻³, best ~10⁻⁴ |
| **Minor, combined** | **~1×10⁻²** | **~5×10⁻²** |
| **Catastrophic, combined** | **~1×10⁻⁴** | **~2×10⁻⁴** |

**`[SD-RISK-110]` Per year, and the assumption that dominates it.** `lempi02w` is
fed from the Middleton's own USB port `[PI3-FOUND-090]`, so **every power-down
is a cut** `[PI3-FOUND-120]`. At one switch-off a day that is ~365 cuts/year;
the band used here is 100–700.

For `bose` this document **assumes mains power on a supply not operated by a
user-facing switch**, giving 2–12 cuts/year (household outages plus deliberate
ones). That assumption was not verified `[GDE-DEP-060]`. If `bose` is in fact on
a switched strip or a speaker's own outlet, its column below moves to
`lempi02w`'s cut count and its yearly risk rises by ~60×, while its *per-cut*
figures stay exactly as they are.

| Per year | `bose` (~6 cuts) | `lempi02w` (~365 cuts) |
| :--- | ---: | ---: |
| Cuts | 2–12 | 100–700 |
| Minor events | **~0.06/yr** — one per ~16 years | **~18/yr** — a few per month |
| Catastrophic events | **~6×10⁻⁴/yr** — one per ~1,600 years | **~0.07/yr** — one per ~14 years |
| Catastrophic, honest band | one per 140 to 14,000 years | **one per 2.7 to 270 years** |
| 10-year cumulative catastrophic | **~0.6 %** | **~50 %** *(band: 4 %–97 %)* |

The two-orders-of-magnitude band on the catastrophic row is real and cannot be
narrowed from published work. It is the dominant uncertainty in this document.

**`[SD-RISK-120]` Wear-out is not the binding constraint on either node.** At
1–1.9 GB/day against a 119 GB and a 239 GB card, with `discard` on `bose` and a
weekly `fstrim` on `lempi02w` (199 GB discarded, measured), even a pessimistic
write amplification of 4 and 500 P/E cycles puts endurance at **17–50 years**.
Both nodes will meet a power cut long before they meet a worn-out cell. Power
loss, not wear, is the SD risk here.

## 5. What dominates each node, and the one change per node

**`[SD-RISK-130]` `bose` is dominated by the card, because everything above the
card is already dealt with.** Read-only root, boot and library; barriers on the
only writable 4 GB; zram instead of a swapfile; `fsck.f2fs` installed;
`lempi-db-recover` deployed; one measured clean cut. Nothing cheap is left to
fix in software. The single change that would reduce the residual is **an
industrial/pSLC card with documented sudden-power-off recovery** — and at ~6
cuts/year that is hard to justify.

The honest recommendation for `bose` is therefore not a change to `bose`:
`[PI-FS-050]` still wants a *count*, and [`BosePi/power-test-manifest.sh`](../BosePi/power-test-manifest.sh)
already captures the manifest either side of a cut. Ten more cuts would do more
for this estimate than any hardware swap.

**`[SD-RISK-140]` `lempi02w` is dominated by one writable ext4 root carrying the
whole OS, hit ~365 times a year.** Not by the card, and not by SQLite. Its
catastrophic rate is ~300× `bose`'s almost entirely because it takes ~60× the
cuts against ~60× the writable surface, and because on `lempi02w` a filesystem
failure is an unbootable appliance rather than a lost state partition.

**The single change that would most reduce it is to give `lempi02w` `bose`'s
shape: `overlayroot` over a read-only root, plus a small f2fs state partition.**
That converts ~99 % of its writable surface to read-only and moves its
catastrophic column to `bose`'s. The pattern is already proven in this fleet, on
real hardware, and its one sharp edge is documented — `overlayroot` defaults to
`recurse=1` and will wrap the state partition too unless pinned to
`recurse=0` `[IMPL-BOS-165]`.

Two cheaper partial measures, in order of value:

1. **Replace `dphys-swapfile` with zram**, as `bose` already does. `pswpout`
   shows 125 MB swapped out in 2.17 days — ~58 MB/day of writes to the root
   card, on a 464 MB machine, for relief zram gives with zero card writes.
2. **Mount the root `errors=remount-ro`.** `errors=continue` is what lets a
   detected metadata error keep being written around instead of stopping.

**`[SD-RISK-150]` One change applies to both, costs nothing, and follows from
SQLite's own advice.** SQLite recommends WAL *and checkpointing as infrequently
as possible*, because the checkpoint is the only corruptible window
`[SD-RISK-070]`. `wal_autocheckpoint` is at the 1000-page default on both nodes.
Raising it trades a longer replay at boot — which `lempi-db-recover` already
performs on every start — for proportionally fewer corruptible windows. This is
the only free reduction identified in this survey.

---

## Sources

Filesystem, mount, card, diskstats, WAL-growth and dirty-page figures were
measured on the two machines on 2026-09-15 and are quoted inline above.
Published evidence:

- Zheng et al., [*Understanding the Robustness of SSDs under Power Fault*](https://www.usenix.org/conference/fast13/technical-sessions/presentation/zheng),
  FAST '13, extended as [*Reliability Analysis of SSDs Under Power Fault*](https://dl.acm.org/doi/10.1145/2992782),
  ACM TOCS 2016 — 15 SSDs, ~3,000 fault-injection cycles; 13 misbehaved: 3 bit
  corruption, 3 shorn writes, 8 serializability errors, one lost a third of its
  data, **one bricked**. That ~1-in-3,000 brick rate under *adversarially timed*
  faults is the anchor for `[SD-RISK-100]`'s 10⁻⁴. A domestic cut is less well
  aimed; a consumer microSD controller is weaker than these SSDs'. Those two
  errors point opposite ways, which is why the band spans two orders of
  magnitude rather than one.
- Zheng et al., [*Torturing Databases for Fun and Profit*](https://www.usenix.org/conference/osdi14/technical-sessions/presentation/zheng_mai),
  OSDI '14 — power faults leaving images whose flags cause recovery to be skipped.
- embeddedTS, [*Preventing Filesystem Corruption in Embedded Linux*](https://www.embeddedts.com/assets/preventing-filesystem-corruption-in-embedded-linux)
  — allocation groups typically 4 MiB on SD, up to 64 MiB on SDXC; a cut during
  a wear-levelling update can cost a whole group. Source for `[SD-RISK-090]`.
- SQLite, [*How To Corrupt An SQLite Database File*](https://www.sqlite.org/howtocorrupt.html)
  — WAL vs rollback under power loss, and *"most consumer-grade mass storage
  devices lie about syncing."* Source for `[SD-RISK-070]` and `[SD-RISK-150]`.
- Lee et al., [*F2FS: A New File System for Flash Storage*](https://web.stanford.edu/class/cs240/readings/f2fs.pdf),
  FAST '15 — checkpointing and fsync roll-forward, the mechanism `[BOS-PWR-020]`
  observed working. Jaffer et al., [*Evaluating File System Reliability on SSDs*](https://www.usenix.org/system/files/atc19-jaffer.pdf),
  ATC '19 — ext4 and F2FS under power fault.
- Schroeder et al., [*Flash Reliability in Production*](https://www.usenix.org/conference/fast16/technical-sessions/presentation/schroeder),
  FAST '16 — 20 % of flash drives developed uncorrectable errors in four years,
  and RBER did not predict them. Datacenter SSDs, cited for that error-model
  caution only, **not** as an SD failure rate.
- ATP, [*Industrial SD cards: key factors*](https://www.atpinc.com/blog/industrial-sd-cards-factors-requirements-to-consider)
  and Swissbit, [*powersafe*](https://www.swissbit.com/en/products/powersafe/)
  — sudden-power-off recovery and pSLC as the consumer/industrial divide.
  Hackaday, [*Raspberry Pi and the Story of SD Card Corruption*](https://hackaday.com/2022/03/09/raspberry-pi-and-the-story-of-sd-card-corruption/)
  — the field picture is anecdotal; no usable per-cut rate exists in it.

**No published per-power-cut failure probability for consumer microSD was
found.** Every figure in `[SD-RISK-100]` is a bounded estimate built from the
layer decomposition above, and is presented as such.
