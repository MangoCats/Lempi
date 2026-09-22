# IMPL016: Converting `lempiplay3` — Overlay Root, Split Database, Current Build

**Implementation Plan — written 2026-09-15, executed the same day**

*(Header corrected 2026-09-20. It still read "not yet executed" five days after
`60cb81d` converted the machine, which is the staleness this repository keeps
finding: the fix lands in one place and the claim that contradicts it survives
somewhere else.)*

`lempiplay3` is overdue an update and is the least hardened node in the
fleet. This brings it to `bose`'s shape without losing the one thing it has
that `bose` does not: a working touchscreen.

> **Related:** [GUIDE015](GUIDE015-the-earlier-names.md) — the rename this plan waited on, since completed · [LOG010](LOG010-power-loss-risk-on-the-pi-nodes.md) `[SD-RISK-010]` — why the root shape is the risk · [BOSE009](../BosePi/BOSE009-image-update-runbook.md) — the runbook this follows · [BOSE010](../BosePi/BOSE010-changing-a-locked-card.md) `[IMPL-BOS-180]` — how to change anything afterwards

---

## 1. It is not a reimage

**`[IMPL-VP3-010]` The partition layouts are already identical, so nothing has
to be repartitioned, reformatted or copied.** Measured 2026-09-15:

| | p1 | p2 | p3 | p4 |
| :--- | :--- | :--- | :--- | :--- |
| `bose` | 512M vfat `/boot/firmware` | 8G ext4 `/media/root-ro` | 4G f2fs `/var/lempi` | 106.6G ext4 `/srv/library` |
| `lempiplay3` | 512M vfat `/boot/firmware` | 8G ext4 **`/`** | 4G f2fs `/var/lempi` | 106.6G ext4 `/srv/library` |

Same sizes, same filesystems, same roles. The sole difference is that `bose`
mounts p2 read-only beneath an overlay and `lempiplay3` mounts it read-write.
**The conversion is a package and a kernel parameter**, not an image build —
`BosePi/build-bose-card.sh` and its companions are not needed here.

It also already has two of the three things `[SD-RISK-150]` recommends for
`lempi02w`: state on its own f2fs partition, and **zram** rather than an
SD-backed swapfile. Its root carries 2.9 GB of lifetime writes against
`lempi02w`'s 89 GB.

## 2. What must survive

**`[IMPL-VP3-020]` The touchscreen is the whole reason this node is different,
and every part of it is outside the player binary.** Inventoried on the live
machine, because none of it is in the repository:

| | |
| :--- | :--- |
| `fbui.service` | `ExecStart=/usr/local/bin/fbui --url ws://127.0.0.1:5720/ws`, `User=pi`, `Nice=-5`, `Restart=always`, and an `ExecStartPre` that turns the console cursor off on tty1 |
| the binary | built with the **`fbui` cargo feature**, which `bose` and `lempi02w` never compile `[SPEC-FBUI-015]` |
| display overlays | `dtparam=spi=on`, `dtoverlay=nospi10`, `dtoverlay=piscreen2r,rotate=90,speed=16000000,fps=20`, `dtoverlay=dwc2,dr_mode=host` |
| a deliberate absence | `dtoverlay=vc4-kms-v3d` is commented out — *"HDMI never used on this appliance, disabled for boot speed"* |
| the port | **5720**, not 80. `fbui` connects to it by URL, so the two move together or not at all |
| the library | `/srv/library` on p4, 44 GB in use |

**The overlay makes every one of these read-only.** After the conversion,
changing a `dtoverlay` line or the `fbui` unit needs `overlayroot-chroot`
`[IMPL-BOS-180]` or `build/install-config.sh`. That is the cost of the
hardening and the reason section 4 orders the work as it does.

## 3. What changes

**`[IMPL-VP3-030]` Four changes, of which only one is risky.**

1. **The build.** It currently runs `a76715eb685d+dirty` — a *dirty* build, so
   nobody can say what code is on it. Replace with a named commit built
   `--features fbui` and without `vipunen-support`, per `[GDE-DEP-025]`.
2. **The database.** Still the pre-split monolith at `/var/lempi/listener.db`,
   where `bose` and `lempi02w` are both split. Follow
   [IMPL009](../LempiPi/IMPL009-database-split-plan.md) and `[BOS-RUN-080]`.
3. **The root.** Install `overlayroot`, add `overlayroot=tmpfs` to
   `cmdline.txt`, correct `fstab`, reboot. This is the risky one.
4. **The hostname**, changed to `lempiplay3`. Owned by the rename, not by
   this plan.

## 4. The order, and the single constraint that fixes it

**`[IMPL-VP3-040]` Everything that writes to the root must happen before the
root stops being writable.** That one rule determines the whole sequence, and
getting it wrong does not fail — it succeeds, and then vanishes at the next
reboot `[IMPL-BOS-185]`.

| # | Step | Gate before moving on |
| ---: | :--- | :--- |
| 1 | Deploy the current build, `--features fbui`, under the **old** hostname | Plays, **and the screen draws**, and `--version` reports a clean commit |
| 2 | Split the database | Player runs from `library.db` + `listener.db`; old monolith kept, not deleted |
| 3 | Rename to `lempiplay3` | Reachable by the new name; `fbui` still connects on 5720 |
| 4 | Convert the root to overlay | Survives a reboot with the screen working |

Steps 1–3 all write to the root and are ordinary work while it is writable.
Step 4 is last because after it they are not ordinary at all.

Step 1 is also worth doing **immediately and on its own**: it is what
the rename has been waiting for, and it is reversible.

## 4a. Step 3 done early, deliberately and alone, 2026-09-15

**`[IMPL-VP3-045]` The hostname was changed on its own, ahead of steps 1 and
2, and nothing else from the rename went with
it.** Directed rather than discovered: the larger rename waits for the echo
work to reach a stopping point, and this one change was wanted now.

| | |
| :--- | :--- |
| changed | `/etc/hostname`, and the `127.0.1.1` line of `/etc/hosts` |
| backups | `/etc/hostname.pre-rename`, `/etc/hosts.pre-rename`, on the device |
| **not** changed | `/var/lempi`, the database filenames, the folder layouts, the unit names, and every line of prose — all still the rename's |
| router name | `vp3-wifi` → `lp3-wifi` at the next re-lease; until then use **192.168.67.27** |
| verified | `hostname` and `hostnamectl --static` agree; `lempi` and `fbui` both active; PCM `RUNNING` with `hw_ptr` advancing at ~44.1 kHz |
| **not** verified | **the screen** — `[IMPL-VP3-090]` says only a person present can confirm that, and nobody has |

**Nothing in the repository broke, because nothing executable referred to the
old name.** All eleven occurrences are prose, source comments, or
the rename's own audit; no script in `build/` or `tools/` reaches that host by
name. The rename was genuinely isolated, which is why it could be taken out of
order safely.

Taking it out of order costs nothing here: `[IMPL-VP3-040]`'s constraint is
that root writes precede the overlay, and the root is still writable. Steps 1
and 2 remain ahead of step 4 exactly as before.

**For whoever runs the rename: this host is
already renamed.** Its hostname needs no pass, its `/var/lempi` still does, and
the blocker that held it — the machine was unplugged — has cleared.

## 4b. Step 1 done, 2026-09-15

**`[IMPL-VP3-046]` The current build is on, with the touchscreen intact, and
this node needed none of the fixes the other two did.** Built `--features fbui`
without `vipunen-support`, both binaries; `build/install-player.sh` handles only
the player, so `fbui` was installed beside it.

| | |
| :--- | :--- |
| build | `e4eb18fddefd`, clean — replacing `a76715eb685d+dirty` |
| period / buffer | **2048 / 4096, accepted** — no fallback line, so the pin was not refused |
| format | `S16_LE`, unchanged; `pick_config` chose the same one the device already had |
| audio | `pcm=RUNNING`, **0 underruns** across 80 s |
| verdict | `ts=Hardware`, `delay=1804` |
| screen | `480x320 framebuffer opened`, art decoded, **a render completed** |

That last row is worth separating from "the service is active", which is all
`[IMPL-VP3-045]` could claim. A completed render is evidence the display
pipeline works end to end. It is still not evidence the **panel is lit** — a
render to `/dev/fb0` succeeds with nothing attached — so `[IMPL-VP3-090]` is
only partly discharged and a person still has to look.

**`[IMPL-VP3-047]` One render at 66.8 ms against `[SPEC-FBUI-040]`'s measured
20 ms, from a single cold sample that included a 245 ms art decode.** `fbui`
logs only its first render, so there is no steady-state distribution to compare
against and **no conclusion is drawn here**. It is recorded because the two
numbers differ by more than three times and somebody should find out which one
describes the node, not because it is yet a regression.

## 4c. Step 2 rehearsals, 2026-09-15

**`[IMPL-VP3-120]` The rehearsal will not run in `/tmp` on this node, and
failing to notice would have meant never rehearsing at all.** `/tmp` here is a
**452 MB tmpfs** on a Pi 3 with 905 MB of RAM; `lempi.db` is **1.18 GB**.
`split_database.py` puts its work directory wherever `tempfile.mkdtemp()`
points, so the first attempt died with `sqlite3.OperationalError: database or
disk is full` after filling RAM.

The fix is one environment variable — `TMPDIR=/srv/library/.split-tmp`, on the
partition with 56 GB free. The finding is not the fix. It is that **a rehearsal
which cannot run looks exactly like one that passed** if its output is read
through a pipe: the first attempt printed nothing and exited 0, because the
status came from `tail` rather than from Python. Run it to a log with an
explicit `EXIT=` marker.

**`[IMPL-VP3-140]` A source still being written reads as a short copy, and
sends you hunting the wrong fault.** Rehearsing against the live database gave
`listener_play_history has 37974 rows, source has 37975` -- accurate, and it
looks like the copy lost a row. The cause was the player appending a play
mid-run. Same numbers, opposite remedy: stop the writer, do not distrust the
tool. `split_database.py` now counts the source before and after and says so,
which is what the code comments citing this tag refer to.

**`[IMPL-VP3-130]` `[BOS-RUN-060]` never exercises the split tool's own
rehearsal, and should say so.** Its command is

```
attended-import.sh --check -- ssh pi@bose "... split_database.py ... --commit"
```

where `--check` dry-runs `attended-import.sh`'s **mount window**, not the
split — and `--commit` is present in both passes. `--commit` writes straight
to the real destinations and never touches a temp directory, so
`split_database.py`'s rehearsal mode has most likely never been run against a
full-size database on any node in this fleet. `bose`'s own `/tmp` is 923 MB,
which would also have failed.

That is not an argument against the bose runbook, which worked. It is an
argument for rehearsing here **before** `--commit` rather than after, since
this is the first node where the rehearsal has been tried at all.

## 4d. Steps 2 and 4 done, 2026-09-15

**`[IMPL-VP3-150]` The node is `bose`-shaped and playing.** Databases split,
root on an overlay, everything that was preserved still works.

| | |
| :--- | :--- |
| `library.db` | 1,066,520 rows, 1.17 GB, on `/srv/library` |
| `listener.db` | 42,833 rows, 7.1 MB, on `/var/lempi` |
| `lempi.db` | untouched, 1.18 GB — the rollback |
| `/` | `overlay`, `lowerdir=/media/root-ro`, `upperdir` in tmpfs |
| `/media/root-ro` | `ext4 ro` — the real root, now read-only |
| verified playing | `hw_ptr` +266,365 frames in 6 s, ≈44.4 kHz |
| screen | confirmed by eye: drawing, counter advancing, **touch responding** |

**`[IMPL-VP3-160]` Section 1 was wrong that this is "a package and a kernel
parameter", and the omission was the important half.** `bose`'s real `fstab`
carries four bind mounts that make a read-only root *livable*:

    /var/lempi/log            /var/log
    /var/lempi/etc-ssh        /etc/ssh
    /var/lempi/home-pi        /home/pi
    /var/lempi/nm-connections /etc/NetworkManager/system-connections

Without them logs vanish every boot and NetworkManager can never save a
change — and that last path is precisely what stranded `bose` in
`[IMPL-BOS-175]`. Only 1.3 MB had to move. The plan missed it because
section 1 compared *partition tables*, which were identical, and stopped
there.

**`[IMPL-VP3-170]` It was done in two reboots, not one, and that is the part
worth copying.** Binds first with the root still writable, so a mistake was
fixable over ssh; the overlay only after that reboot proved clean. Each reboot
tested exactly one change.

Three faults were caught *before* a reboot could punish them, by testing the
thing rather than trusting it:

- `overlayroot`'s install regenerated a **Pi 5** initramfs in its visible
  output. Both flavours were in fact rebuilt, and `lsinitramfs` confirmed
  `scripts/init-bottom/overlayroot` inside the `v8` image this Pi actually
  boots — but "the package is installed" is not "the boot will use it".
- The binds were activated with `mount -a` and **`sshd` restarted from the
  bound `/etc/ssh`** before rebooting. Had the host keys not copied, that is a
  lockout discovered at boot instead of a reconnect test.
- `/etc/overlayroot.conf` was written garbled by a `printf` quoting slip —
  `overlayroot_cfgdisk="disabled"noverlayroot=""n` on one line — and read back
  before, not after, the reboot that would have parsed it.

## 5. The traps, all of them already paid for once

**`[IMPL-VP3-050]` Record `cmdline.txt` verbatim before touching it.**
`[IMPL-BOS-175]` took `bose` off the network for hours: the lock-in escape
hatch clears `overlayroot=` from the command line, but `fstab`'s `ro` survives
it, so the machine booted read-only, NetworkManager could not write
`/var/lib/NetworkManager`, and there was no Wi-Fi to fix it with. Recovery
needed the card in a reader and a copy of the original file. Take that copy
first.

**`[IMPL-VP3-060]` `bose` mounts `/boot/firmware` read-only too**, marked
`# overlayroot:fs-unsupported` in its `fstab`. Match that deliberately or
deliberately do not, but do not arrive at it by accident.

**`[IMPL-VP3-070]` Verify the durable copy, never the running one**
`[GDE-DEP-070]`. After step 4, `build/install-player.sh` writes both layers and
checksums the lower one; anything else needs `build/install-config.sh`. Five
days of `bose` deploys were lost to this.

**`[IMPL-VP3-080]` Answered 2026-09-15: `fbui`'s state is already on the
partition that survives.** Its own startup line says so —
`using existing calibration at /var/lempi/touch-calibration.toml` — which is
the f2fs partition, not the root. The overlay will not take it away, and the
question below is settled rather than outstanding.

The original wording is kept because the reasoning still applies to anything
else added later. **Ask whether `fbui` writes anything.** It draws to the
framebuffer and reads a WebSocket, which needs no writable root — but this has
not been checked, and a cache or state file would silently stop persisting the
moment the overlay goes on. Check before step 4, not after.

## 6. Verifying it, in the terms this project has learned to use

**`[IMPL-VP3-090]` "No errors" is not "working" — on this node less than
anywhere.** `[GDE-ECHO-547]` cost `bose` seven silent minutes while every
counter read healthy, because a stream that never opened cannot underrun. This
node has a second way to be silently wrong: the screen.

Each gate in section 4 wants three separate answers:

- the PCM is **open and its `hw_ptr` advancing**, not merely free of underruns
- the **screen is drawing** — which only a person present can confirm, and is
  the one thing no remote check substitutes for
- after step 4, it all still holds **across a reboot**

## 7. Open

**`[IMPL-VP3-100]` The risk figures for this node have not been computed.**
[LOG010](LOG010-power-loss-risk-on-the-pi-nodes.md) covers `bose` and
`lempi02w`; this node sits between them and closer to `bose` than the root
filesystem alone suggests, because its state and library are already on their
own partitions. Worth computing before step 4 rather than after, so the
conversion is justified by a number.

**`[IMPL-VP3-110]` Whether the state directory was renamed here was the
rename's call**, not this plan's. It
affects `var-lempi.mount`, `fstab` and the player's argument, and doing it in
the same window as step 3 would be cheaper than twice.
