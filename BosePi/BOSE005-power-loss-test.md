# BOSE005: The First Real Power Cut

**Experiment record — one cut, `bose`, 2026-09-10 ~12:55 EDT**

The open question `[PI-FS-050]` has stood since
[PI001](../LempiPi/PI001-image-and-partitions.md) §5a: the f2fs-on-C recommendation was reasoning from how the filesystem is
built, never from a Pi losing power with the database open. One cut has now
been taken, with a manifest captured either side of it.

**This is one data point, not the rig.** PI001 asks for *"cut power at random,
count how often the database survives and how often `fsck` is needed"* — a
frequency, over many cuts. What follows is a single trial that passed. It
retires the "never tried" state and nothing more; `[PI-FS-050]` stays open
until there is a count behind it.

> **Related:** [BOSE004](BOSE004-operating-health.md) for the steady-state
> baseline · [`power-test-manifest.sh`](power-test-manifest.sh) for the
> read-only capture either side · [PI009](../LempiPi/PI009-the-silence-of-2026-09-08.md)
> for the power-cut failure this did *not* reproduce

---

## 1. What happened

| | |
| :--- | :--- |
| Method | Supply cut once while playing, no shutdown, no warning |
| Before | `power-test-manifest-20260910T154127Z.log`, uptime 3 d 18 h |
| After | `power-test-manifest-20260910T165710Z.log`, uptime 2 min |
| Last play recorded pre-cut | `play_id=39012`, 12:52:36, passage 7034 |
| Boot | 12:55:54 · `lempi` up and playing by 12:56 |
| Outcome | **No loss, no corruption, no manual intervention** |

Both capture files sit under `BosePi/logs/`, which `.gitignore` excludes by
design — per-run script output, not repository history. They are therefore on
the machine that ran the test and not in the tree, so **every figure this
document rests on is quoted inline below** rather than left as a reference to
a file a later reader cannot open.

**`[BOS-PWR-010]` The database survived intact, and nothing had to be done to
it.** `pragma quick_check` returns `ok`. The schema hash is unchanged
(`d9c34afa…6af28b6d`, 59 objects), `page_count` is identical at 288,048, and
every row count moved **up**, never down. The five play rows named in the
pre-cut manifest — 38,988 through 38,992 — were each confirmed present
afterwards by primary key, not inferred from a total.

**`[BOS-PWR-020]` f2fs rolled the WAL forward and said so.** The kernel log for
the new boot carries the whole recovery, and it is clean:

```
F2FS-fs (mmcblk0p3): recover_inode: ino = 14a, name = lempi.db-wal, inline = 1
F2FS-fs (mmcblk0p3): recover_data: ino = 14a, ... range (0, 873), recovered = 0, err = 0
F2FS-fs (mmcblk0p3): do_recover_data: dnode: (recoverable: 13, fsynced: 13, total: 13),
                     recovered: (inode: 13, dentry: 0, dnode: 13), err: 0
F2FS-fs (mmcblk0p3): checkpoint: version = 2e4e5ca      (was 2e4d0fa)
```

Thirteen dnodes were fsynced and thirteen recovered — `err = 0` throughout, no
`fsck` and no `errors=continue` damage passing quietly. `lempi.db` had last
been checkpointed at 12:29:53, 23 minutes before the cut, so the roll-forward
had real work to do rather than finding a clean file. `synchronous=FULL` is
what made that safe, and this is the first evidence it is doing its job here.

**`[BOS-PWR-030]` The resume was better than the play record.** `lempi` came
back with `resuming passage 7034 at 246.7s` and carried on. The history row for
that passage had recorded `heard=145s`, because `player_state` is written far
more often than a play row is closed. The recovered position was therefore
*ahead* of the last durable history row — the listener lost nothing audible,
and the couple of minutes between the last closed row and the cut cost only
history detail, not position.

**`[BOS-PWR-040]` `[PI3-FOUND-120]`'s crash loop did not reproduce.**
`NRestarts=0`, `Result=success`, zero failed units, no `attempt to write a
readonly database`. `lempi-db-recover` is still not deployed on `bose`
`[BOS-OPS-050]`-adjacent, and on this trial it was not needed. That is one
trial, not an argument against deploying the guard: the lempi02w failure was a
*rollback* journal reached through a read-only attach, and `bose` is WAL with a
read-write connection open first. The shapes differ, so this result does not
transfer to that one.

## 2. Three findings the cut produced on its own

None was the thing being tested. All three were invisible until a boot
happened, and `bose` had not booted since the day it was built.

**`[BOS-PWR-050]` ALSA renumbered the cards, and `--device hifiberry` is why
that did not matter.** The HiFiBerry moved from card 2 to card 1; `vc4hdmi1`
took its place:

| | before the cut | after |
| :--- | :--- | :--- |
| card 0 | `bcm2835 Headphones` | `bcm2835 Headphones` |
| card 1 | `vc4hdmi1` | **`sndrpihifiberry`** |
| card 2 | **`sndrpihifiberry`** | `vc4hdmi1` |

This is `[IMPL-BOS-140]`'s hazard recurring on a live machine rather than at
build time, and it is the clearest vindication the `--device hifiberry`
substring match has had: the unit asked for a **name**, got the right hardware
at a new number, and played without a hiccup. Had the flag still been absent,
this boot would have opened `vc4hdmi1` and gone silent with every layer
reporting health.

It also broke the first version of [`power-test-manifest.sh`](power-test-manifest.sh),
which read `/proc/asound/card2/...` and duly reported `closed` for a device
that was playing perfectly — the same mistake in miniature, caught because the
manifest also prints device *ownership*, which disagreed. **Both the script and
[BOSE004](BOSE004-operating-health.md) §3 now resolve the card by name.** Any
future instrument that hard-codes a card number on this machine is wrong.

**`[BOS-PWR-060]` `bose` boots believing it is 2026-09-06 17:35:18, and stays
wrong for about 67 seconds.** There is no RTC (`timedatectl` reports
`RTC time: n/a`), so systemd restores the clock from a saved timestamp — and
that file lives on partition **A**, which is read-only under `overlayroot`. It
can therefore never be updated. Every boot restores the *same frozen instant*,
the one A was sealed at.

```
Sep 06 17:35:18  kernel: Booting Linux ...                    <- 4 days in the past
Sep 06 17:35:33  systemd-journald: Realtime clock jumped backwards
                 relative to last journal entry, rotating.
Sep 06 17:35:39  lempi[920]: resuming passage 7034 at 246.7s  <- still wrong
Sep 10 12:56:25  systemd-timesyncd: Initial clock synchronization  <- corrected
```

Three consequences, in rising order of how much they matter:

- **Journal timestamps for the first minute of every boot are ~4 days stale**,
  and `journalctl -b` interleaves them confusingly. The whole of `lempi`'s
  startup lands in that window, so *the startup log of every boot is misdated*.
  This is why the post-cut journal appears to say `Sep 06` throughout.
- **The boot-time listener backup is always discarded.** `lempi` names it from
  the clock — `listener-1788730533.db`, the frozen instant, every time — so
  retention sees the same too-old name at each boot and prunes it first. The
  backup taken at the moment of greatest interest, immediately after a cut and
  before anything else writes, is the one guaranteed not to survive. Confirmed:
  it was written this boot and is already gone from
  `/var/lempi/listener-backups/`.
- **A play closing inside that ~67-second window would be stamped four days in
  the past.** It did not happen here — `play_id=39013` landed at 12:58:22, after
  sync — but only because no passage ended in the gap. `[SPEC-PLAY-*]`'s rule
  about what counts as a play assumes a clock; on this machine that assumption
  is false for the first minute after every power cut.

**`[BOS-PWR-070]` `/proc/diskstats` resets at boot, so this appliance has no
wear total at all.** The manifest read 2.050 GB written to C before the cut and
0.007 GB after — not a card that forgot, a counter that restarts. This matters
because the card exposes no wear-level attribute either `[BOS-OPS-095]`, so the
written-bytes figure is a **rate** source (divide by uptime) and never a
lifetime. [BOSE004](BOSE004-operating-health.md) §1 originally labelled it
"lifetime"; the rate it quoted, 0.54 GB/day, was right, and the label was
wrong. Accumulating real wear would mean sampling and summing across boots,
which nothing does today.

Nothing in this section was caused by the cut. The cut is simply the only thing
that had made `bose` boot since it was built.

## 3. What this does and does not settle

**Settled:** a single ungraceful cut, taken mid-playback with a 23-minute-old
checkpoint and ~4 MB of live WAL, cost nothing — no corruption, no `fsck`, no
hand recovery, no lost rows, and a resume position ahead of the last durable
history row. The zram fix of `[IMPL-BOS-170]` also came through its first real boot,
closing `[BOS-OPS-090]` — swap was present and identical afterwards.

**Not settled:**

- **The frequency `[PI-FS-050]` actually asks for.** One pass is not a rate.
  The interesting cut is the unlucky one — mid-checkpoint, or mid-`fsync` — and
  a single trial is unlikely to have hit it.
- **Whether `lempi-db-recover` is needed here.** Untested, because nothing went
  wrong `[BOS-PWR-040]`.
- **The confound, which passed and so cost nothing.** This boot exercised the
  zram fix of `[IMPL-BOS-170]` *and* the cut together, as flagged beforehand.
  Both worked, so the ambiguity never had to be resolved — had swap been
  missing, this trial could not have said which caused it.
- **Whether the clock behaviour `[BOS-PWR-060]` has already corrupted history.**
  Not looked for beyond this boot; `bose` had not rebooted since it was built,
  so there is at most one prior window.
