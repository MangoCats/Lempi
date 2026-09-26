# BOSE004: `bose` in Service — the Health Baseline

**Measurement — Tier 1 · read-only on `pi@bose`, 2026-09-10 10:29–10:40 EDT**

The first health check of `bose` after it was built, taken 3 days 17 hours into
its first real run. Nothing here was changed; every figure was read from the
running machine.

Its purpose is **comparison**, not celebration. [BOSE001](BOSE001-survey.md)
measured the machine before the build; this measures the deployment doing its
job, so a future check has a known-good column to sit beside. Sections 4 and 5
are the ones to read first when something looks wrong — §4 lists what looks
broken and is not, §5 what is genuinely open.

> **Related:** [BOSE001](BOSE001-survey.md) for the machine as found ·
> [BOSE003](BOSE003-build-procedure.md) for how this build was made ·
> [README](README.md) for what has and hasn't been proven ·
> [PI006](../LempiPi/PI006-appliance-characterisation.md) for the same
> measurements on `lempi02w`

---

## 1. The baseline

**`[BOS-OPS-010]` What healthy looked like on 2026-09-10.** Take these again
before concluding anything is wrong; most of them move very little.

| | |
| :--- | :--- |
| Boot / uptime | Sun 2026-09-06 17:35:33 EDT / 3 d 16 h 54 m |
| Kernel | 6.18.34+rpt-rpi-v8 `aarch64` (Debian 1:6.18.34-1+rpt1) |
| Build running | `lempi 0.1.0 (358c5b176833)`, 7,917,456 B, built Sep 6 16:56 |
| `lempi` | pid 1065, **`NRestarts=0`**, 9 tasks, 2 h 06 m CPU total |
| `mpd` | pid 1047, 29 s CPU total, `state: stop` (idle guest) |
| Failed units | **0** |
| Real CPU (10 s) | 0.46 % — user 0.33, sys 0.13 (plus a phantom 25 % iowait, §4) |
| Memory | 313 MiB used, 1.5 GiB cache, of 1,845 MiB |
| Swap | **1.8 GiB zram, 0 B used**, active since Mon 2026-09-07 10:03:34 |
| RSS | `lempi` 109,556 KB · `mpd` 75,248 KB |
| Thermal | 60.3–61.3 °C, `throttled=0x0`, ARM at 1,500 MHz |
| `/media/root-ro` (A) | 4.6 G of 7.9 G, 62 % · `ro,relatime` under `overlayroot` |
| `/var/lempi` (C) | 1.5 G of 4.0 G, 37 % · f2fs, the only writable partition |
| `/srv/library` (B) | 44 G of 105 G, 45 % · `ro` · 7,508 files, 5,695 MP3 |
| SD writes | **0.54 GB/day** (2.01 GB over a 3.71 d boot), 25 KB/s sustained |
| Card | `SR128`, 119 GiB, made **05/2020**, UHS-I DDR50 SDXC |
| Journal | 33.3 MB · `lempi` logged **103 lines in 3.7 days**, 89 of them backups |
| Listeners | `0.0.0.0:5720` lempi · `0.0.0.0:6600` mpd · `:22` sshd |

The card is **not** the one BOSE001 surveyed (`SN128`, made 03/2019). That is
the expected result — the old card is the way back and stays intact — and it is
the cheapest available confirmation that the machine booted the card anyone
thinks it did.

## 2. It is playing, and two independent records say so

**`[BOS-OPS-020]` The ALSA frame counter is the ground truth for *whether it
played*; it is not a clock, and must never be its own reference.**
`[GOV-SRC-010]` applies — rank the sources rather than averaging them.

The HiFiBerry's `pcm0p/sub0/status` (find the card **by name** -- see
`[BOS-PWR-050]`) shows the stream triggered **once**, at
24.85 s after boot, and `RUNNING` ever since. No re-open, no second trigger.

| | |
| :--- | :--- |
| Frames delivered since the single trigger | 14,126,435,269 |
| ~~Monotonic time~~ `tstamp − trigger_time` | 320,327.218 s |
| Audio implied by the frame count @ 44,100 Hz | 320,327.330 s |
| ~~**Drift over 3.71 days**~~ **invalid, see below** | ~~+0.113 s ≈ 0.35 ppm~~ |

**The 0.35 ppm drift figure is withdrawn, 2026-09-13.** The row labelled
"monotonic time" is `tstamp − trigger_time`, and ALSA derives `tstamp` from
`hw_ptr` at the nominal rate — so the last two rows are the same counter
expressed twice, and their difference cannot report a crystal error at all.
Measured properly against the system clock, `bose` runs **≈+14 ppm**; see
`[LOG-FIX-010]` and `[LOG-FIX-030]` in
[LOG007](../docs/LOG007-drift-instrument-correction.md).

What the table still establishes is the part that does not depend on the
timestamp: the frame count itself. It is **not** slack in which audio could
have gone missing — a single underrun leaves a hole here, and a stream restart
resets `hw_ptr` and `trigger_time` outright. Neither happened. Device ownership sits on `pcmC2D0p`
(`sudo fuser -v /dev/snd/*`) — the HiFiBerry, per `[IMPL-BOS-140]`, not the
onboard jack.

The listener history agrees, from its own separate bookkeeping:

| Date | Plays | Audio heard |
| :--- | ---: | ---: |
| 2026-09-07 | 341 | 23.95 h |
| 2026-09-08 | 323 | 24.08 h |
| 2026-09-09 | 330 | 24.00 h |
| 2026-09-10 (to 10:33) | 154 | 10.56 h |

Full days sum to 24 h of audio in a 24 h day; Sep 10 shows 10.56 h at 10.55 h
into the day. Two records, different mechanisms, same answer.

**Selection is working, not looping.** Over the last three days, 981 plays drew
**979 distinct** passages (0.2 % repeat) across all eight programs — Soft 283,
Mellow 231, Fun 97, Prog 95, Light 89, Cool 86, Groove 59, Loud 41. Mean
fraction of each passage heard **0.9995**, with exactly 1 of 981 cut short.
Lifetime reach is 5,931 of 16,661 passages; history holds 38,975 rows.

## 3. Taking these measurements again

Read-only, safe on a locked card, about a minute end to end.

```sh
# service and system
systemctl status lempi.service mpd.service --no-pager
systemctl --failed --no-pager
systemctl show lempi.service -p NRestarts -p CPUUsageNSec -p MemoryCurrent
free -h; cat /proc/swaps; vcgencmd measure_temp; vcgencmd get_throttled
df -h; sudo du -sh /var/lempi/*

# is it actually playing? the only answer that is not self-reported.
# NEVER hard-code the card number -- it moved 2 -> 1 across the first power
# cut, [BOS-PWR-050]. Resolve it by name, the way the unit's --device does.
CARD=$(sed -n 's/^ *\([0-9]\+\) \[sndrpihifiberry.*/\1/p' /proc/asound/cards)
cat /proc/asound/card$CARD/pcm0p/sub0/status   # state: RUNNING; note hw_ptr, trigger_time
sudo fuser -v /dev/snd/*                       # must be lempi, on that card

# what lempi has said for itself, as message shapes rather than lines
sudo journalctl -b -u lempi --no-pager -o cat | sed -E 's/[0-9]+/#/g' | sort | uniq -c | sort -rn

# bytes written to C THIS BOOT -- /proc/diskstats resets at boot, so this is
# a rate source (divide by uptime), not a lifetime total [BOS-PWR-070]
grep mmcblk0p3 /proc/diskstats | awk '{ print $10 * 512 / 1e9, "GB written" }'; uptime -p
```

**`[BOS-OPS-045]`** `sqlite3` is on the card since `[BOS-IMG-020]` — briefly
untrue on 2026-09-11, when the overlay discarded it `[IMPL-BOS-185]`, and true
again since it was reinstalled through `overlayroot-chroot` and added to
`provision-bose.sh`. The `python3` form below works on any appliance and is
what the rest of this document assumes, which is why that outage changed none
of these recipes. Note
the database is **split** since 2026-09-11: `lempi.db` is the retained
pre-split rollback, and the live halves are `/var/lempi/listener.db` and
`/srv/library/library.db` `[BOS-RUN-080]`.

```sh
sudo python3 -c "
import sqlite3
c = sqlite3.connect('file:/var/lempi/listener.db?mode=ro', uri=True)
print('quick_check:', c.execute('pragma quick_check').fetchone()[0])
for n, m in c.execute('select name, sum(pgsize)/1048576.0 m from dbstat group by name order by m desc limit 6'):
    print('  %-24s %8.1f MB' % (n, m))
"
```

## 4. Things that look broken and are not

The most useful section here. Each was chased to a conclusion on 2026-09-10;
none needs action, and each will otherwise be rediscovered as a fresh alarm.

**`[BOS-OPS-030]` The play history's timestamps scatter by ±100 s, so gaps
computed from consecutive rows are fiction.** Differencing
`played_at + heard_ms` against the next `played_at` produces 28 apparent
silences of 90–142 s over four days. There were none. Across 1,229 intervals
the distribution is centred on zero — median **+0.2 s**, p25 −27.4 s, p75
+26.9 s — with **597 negative** gaps balancing the positive ones. `played_at`
is stamped somewhere during the play rather than at its start. The daily totals
in §2 leave no room for the 47 minutes those "gaps" would sum to, and the frame
counter forbids it. **Judge continuity from `hw_ptr`, never from this table.**
The genuinely large gaps in that data (15,208 s, 2,487 s, 1,927 s) all fall on
2026-09-06 *before* the current boot — bringup, not service.

**`[BOS-OPS-040]` The steady 25 % iowait is an accounting phantom.** `top` and
`vmstat` report `wa 25` with `b 1` continuously. It is not I/O: a scan of every
task in `/proc/*/task/*/stat` finds **zero** in `D` state, and disk traffic over
60 s is 17 KB/s read and 25 KB/s write. It is idle time misattributed, skewed
onto one core — cpu2 carries 19.1 M iowait ticks against 12.6 M idle, while cpu0
carries 37 K. Exactly one core's worth on a 4-core box is the signature of the
quirk rather than of a workload. Recorded here so it is not diagnosed as an I/O
problem during a future incident, which is when it will look most convincing.

**`[BOS-OPS-045]` The startup ALSA noise and the `:80` refusal are both
expected.** `lempi` logs three `pcm_dmix` / `pcm_asym` complaints at start —
that is ALSA's `default` device failing before `lempi` opens the hardware device
directly, which it then reports as
`output: hw:CARD=sndrpihifiberry,DEV=0 @ 44100 Hz, 2 ch`. It also logs
`not also listening on :80 (Permission denied) -- port 5720 still works`,
because it runs as `pi`. Both are one line per boot, and both are benign.

## 5. Standing findings — open, none urgent

**`[BOS-OPS-050]` `MemoryMax=200M` in [`lempi-bose.service`](lempi-bose.service)
is silently doing nothing.** The kernel's memory cgroup controller is not
enabled: `/sys/fs/cgroup/cgroup.controllers` reads `cpuset cpu io pids`, and
`/boot/firmware/cmdline.txt` carries no `cgroup_enable=memory`. Confirmed by
`systemctl show lempi.service -p MemoryCurrent` returning `[not set]`. Lempi
sits at 107 MiB so nothing is harmed today, but **the guard written into the
unit does not exist**, and a leak would meet no ceiling. Enabling it is a
`cmdline.txt` edit plus a reboot, which on a locked card means a full unlock
cycle `[IMPL-BOS-160]` — the cost, not the change, is what makes it a decision.

**`[BOS-OPS-060]` MPD is reachable from the whole LAN with no password.**
[`mpd.conf`](mpd.conf) sets `bind_to_address "0.0.0.0"` on port 6600 and defines
no `password`. The reasoning recorded in
[`lempi-bose.service`](lempi-bose.service) — that the guest is local — is true
of *Lempi's* side (`--mpd 127.0.0.1:6600`) but not of MPD's own listener. Anyone
on the network can drive the guest input, and `0.0.0.0:5720` exposes the web UI
the same way. Probably acceptable on a home network; the point is that the
unit's stated assumption and the actual exposure differ, so the assumption
should not be leaned on a second time.

**`[BOS-OPS-070]` The deployed binary is 126 commits behind `main`, and the
drift is not only the speaker work.** Running `358c5b176833`; `main` was at
`85ad857` on 2026-09-10. `git diff --stat 358c5b176833..HEAD -- player` is
**26 files, +2,891 / −269**, including [`player/core/src/db/mod.rs`](../player/core/src/db/mod.rs)
(+396), [`player/core/src/db/library.rs`](../player/core/src/db/library.rs) (+323),
[`player/core/src/db/player_store.rs`](../player/core/src/db/player_store.rs) (+237) and
[`player/src/session.rs`](../player/src/session.rs) (+139). Before the next
deploy, establish whether any of that carries a schema migration: it would run
against a **1.1 GB live database** on first start, and the listener backups
described in §6 are not a backup of the whole thing.

**`[BOS-OPS-080]` 94 packages are upgradable, several from `stable-security`.**
Among them `bsdutils`, `bsdextrautils`, `eject`, `fdisk`, plus `bluez`, `curl`
and `base-files`. No `/var/run/reboot-required` is present. Applying them needs
an unlock cycle, so this is a scheduled maintenance window rather than something
that can drift in quietly — the design working, not failing.

**`[BOS-OPS-090]` Closed 2026-09-10 by the first power cut — the fix works.**
zram came up on that boot exactly as it should; see
[BOSE005](BOSE005-power-loss-test.md) §3. The reasoning that made it worth
checking is kept below, because it is the shape of the next such question.
`[IMPL-BOS-170]`'s fix is genuinely on partition A, and until that boot no
boot had exercised it. `overlayroot-noop.conf` (41 B) exists both in the
overlay's upper layer and at
`/media/root-ro/etc/systemd/system/systemd-remount-fs.service.d/` — the copy on
A is the one that survives a reboot, and it is there. But zram came up at
2026-09-07 10:03:34, three days into an uptime that began 2026-09-06 17:35, so
**swap was started by hand after the repair, not by a boot that used it.** The
journal's `systemd-remount-fs` failure storm (Sep 7 09:47–10:03, ~380 entries)
is the history of that repair and stops dead at the fix. The first cold boot
after this will either confirm it or reopen `[IMPL-BOS-170]`; check
`cat /proc/swaps` first thing.

**`[BOS-OPS-095]` Minor, recorded rather than acted on — the modes since
fixed.** `/var/lempi/listener.db*` were mode `0777`; by 2026-09-25 they were
`644`, and a fleet audit that day found the real case: **all 8,304 library
entries world-writable**, copied from NTFS with `rsync -a` (lempi02w's the
same). Set to 755/644, mtimes kept; every copy path now passes
`--chmod=D755,F644`. The card reports manufacture date 05/2020 and exposes no wear-level
attribute for its type, and `[BOS-OPS-010]`'s written-bytes figure is **per
boot, not lifetime** `[BOS-PWR-070]` — so this appliance has no cumulative wear
measure at all, only a rate that has to be re-derived and accumulated by hand.

**`[BOS-OPS-110]` bose lost smartboardpc, and only for unmarked traffic.**
Found 2026-09-25 when chrony fell back to pi.hole: pings, plain TCP and NTP
from bose to smartboardpc were all lost while `ssh` worked — because OpenSSH
marks its packets (DSCP AF21) and the rest are Best Effort. A capture on
smartboardpc showed marked pings arriving and unmarked ones never arriving; no
firewall on either end, no TDLS link, no address conflict. bose and
smartboardpc were the only two clients on the access point's 5 GHz radio, so
this was the one path forwarded client-to-client inside it. **One Wi-Fi
reassociation of bose cleared it** — smartboardpc back as `^*` within seconds
— so it was stuck state in the access point. If it recurs: test with
`ping -Q 0x48` against plain `ping`; reassociate or restart the access point;
or move bose to 2.4 GHz, where the other two nodes are.

## 6. The database, and what is actually in it

`pragma quick_check` returns **`ok`**. WAL mode, 4 KB pages, 288,048 pages =
1.18 GB, freelist 4,055 pages (16.6 MB) — negligible fragmentation, no vacuum
warranted.

**`[BOS-OPS-100]` Roughly 70 % of the database is ingest-time cache rather than
playback state**, which is why 1.1 GB on a 4 GB partition is not a growth
problem: `musicbrainz_cache` 529 MB, `lowlevel_cache` 204 MB, `cover_art`
104 MB, `flavor` 68 MB, `identification_cache` 53 MB, `flavor_subject` 42 MB.
Those grow when the library grows, not while it plays; playback adds about 330
history rows a day. If space ever does get tight, that list is where to look
first, and `musicbrainz_cache` is rebuildable.

Backups are bounded and thinning correctly: 5 files, 13 MB in
`/var/lempi/listener-backups/`, hourly at first and daily thereafter, written by
`lempi` itself once an hour — the 89 log lines that are 86 % of everything the
service has said in 3.7 days.

## 7. What was not measured

Named so a later reader does not mistake silence for a clean result.

- **A hard power-loss test.** Not part of *this* check — but one cut was taken
  hours later, on 2026-09-10, and is recorded in
  [BOSE005](BOSE005-power-loss-test.md). It passed; `[PI-FS-050]` stays open
  because one trial is not the frequency it asks for.
- **A cold boot of the current configuration.** Done by that cut, and it
  produced two findings this steady-state check could not see: the cards
  renumber `[BOS-PWR-050]`, and the clock boots four days stale
  `[BOS-PWR-060]`.
- **Audio quality.** Continuity was proven; nothing here says the output sounds
  correct, only that samples never stopped arriving at the DAC.
- **Behaviour under a real MPD guest session.** MPD was idle throughout
  (`state: stop`, empty queue), so `[IMPL-BOS-090]`'s shared-sink choice — which
  `mpd.conf` confirms is the one deployed, `mixer_type software` — was not
  exercised.
- **Anything on the old card.** Untouched, and still the way back.
