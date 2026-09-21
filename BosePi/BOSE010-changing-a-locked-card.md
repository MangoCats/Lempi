# BOSE010: Changing a Locked Card — Which Tool, and What Evaporates

**Appliance Record — how to make a durable change to `bose` after `--lock-in`**

Split from [BOSE006](BOSE006-what-lands-where.md) on 2026-09-11, which had
reached 299 lines against `[GOV-DOC-010]`'s 300-line limit. BOSE006 answers
*what lives on which partition*; this answers *how to change it once A is
read-only*, which turned out to be a different question with sharper edges.

> **Related:** [BOSE006](BOSE006-what-lands-where.md) `[IMPL-BOS-185]` — the five days of deploys that went to RAM · [BOSE003](BOSE003-build-procedure.md) step 10 — the two locks this is about undoing · [provision-bose.sh](provision-bose.sh) — what installs the hatch, before lock-in

---

## 1. The short answer

**`[IMPL-BOS-190]` Use the tool that writes both layers. Do not use the escape
hatch.** For a **file** — a unit, anything under `/etc`, a helper script —
[`build/install-config.sh`](../build/install-config.sh) is the tool: it detects
the overlay, writes the live and durable copies, and verifies the durable one
`[GDE-DEP-090]`. For a **package**, or anything needing a real root
environment, `overlayroot-chroot` writes through to A with no reboot
`[IMPL-BOS-180]`. The player binary is handled by
[`build/install-player.sh`](../build/install-player.sh), which does the same
two-layer write itself. The escape hatch `[IMPL-BOS-160]` exists for a narrower case
than its own description claims, does not leave A writable `[IMPL-BOS-175]`,
and took this appliance off the network when it was used for a package install.

Anything written to `/` by an ordinary command on a locked card lands in the
tmpfs upper layer and is lost at the next reboot, having looked entirely
successful in the meantime. That is the whole hazard, and it has now bitten
three ways: a binary, a systemd unit, and an apt install.

---

## 2. The escape hatch, and why it is not the tool

**`[IMPL-BOS-160]` The lock-in escape hatch — a flag file, checked at boot,
that undoes `--lock-in` without a card pull.** `[IMPL-BOS-120]` calls
`--lock-in` "the point of no easy return" honestly: after it, a mistake in
`/etc` means another card swap, because `raspi-config`'s own internals are
not something to reverse-engineer under pressure. This narrows that to two
reboots, for the case that actually matters most — the system boots fine,
and someone just wants A writable again.

A marker file in either of two places, checked by a systemd unit
(`lempi-unlock-check.service`) ordered right after both mount:

| Marker | Partition | Reachable when |
| :--- | :--- | :--- |
| `/var/lempi/unlock/request` | C (`f2fs`) | SSH still works — no card needed at all |
| `/boot/firmware/unlock-request` | boot (`vfat`) | SSH is broken, any reader can still write plain FAT |

Finding either, `lempi-unlock-check.sh` edits `/boot/firmware/cmdline.txt`
directly, removing the whole `overlayroot=...` token, and reboots. Two
reboots to actually land in a writable system: this one notices the marker
and flips the config; the next one boots into the result. The marker is
deleted only if the token is actually gone afterward — checked directly,
not assumed from a command's exit status — a failure retries on the next
boot instead of silently giving up.

**Must exist on A before `--lock-in` ever runs, not after — the whole
reason this is worth saying plainly.** Once A is the overlay's read-only
lower layer, adding anything to it needs the overlay disabled first, which
is exactly what this script exists to do. `provision-bose.sh` installs it
alongside `lempi`/`mpd`, so every card built from here on has the escape
hatch before it is ever locked.

**Deliberately narrow, restated so it is not oversold.** Rescues "A boots,
and I want it writable" — nothing running under systemd can rescue a boot
that never gets that far, which still needs a card and a reader, the same
as today. It does not touch B's temporary-reopen case at all; that is
`attended-import.sh`'s job, needs no reboot, and was already solved before
this existed.

**`[IMPL-BOS-175]` The paragraph above is wrong as built: the hatch does NOT
leave A writable, and on this machine it strands it off the network. Do not
use it to install software — use `overlayroot-chroot`. Found 2026-09-11 by
using it.** `bose` was unlocked in order to `apt-get install chrony`, and did
not come back: no SSH, no ping, and `ip neigh` on a LAN peer reporting
`FAILED` — nothing at layer 2 claiming the address. Recovery took a card and
a reader, which is precisely the situation this hatch exists to avoid.

The mechanism is two locks and one key. `[BOSE003]` step 10 sets **both** the
overlay on A *and* `ro` in `fstab` for A. The hatch removes only the
`overlayroot=` token from `cmdline.txt`; `fstab`'s `ro` survives it, and the
one service that would otherwise remount `/` read-write was deliberately made
a no-op by `[IMPL-BOS-170]` because that remount always failed against an
overlay root. So A comes up **read-only with no tmpfs upper layer to absorb
writes** — strictly worse than the locked state, where every write at least
succeeded in RAM until the next reboot.

That is fatal here rather than merely awkward, because `bose` is wireless
`[PI-BOS-020]` and NetworkManager needs a writable `/var/lib/NetworkManager`
to associate. No writable `/var`, no Wi-Fi, no way in.

**`[IMPL-BOS-180]` `overlayroot-chroot` is the right tool for any persistent
change to a locked card, and it needs no reboot at all.** `[BOSE003]` already
said so — *"writes the same drop-in through to A's real lower filesystem
without needing the full unlock/reboot/relock cycle"* — but said it inside a
paragraph about swap, where nobody looking for "how do I install a package"
would find it. Stated here plainly instead:

```bash
sudo overlayroot-chroot apt-get install -y <pkg>     # persists on A
```

For something that must also take effect *now*, write twice — through the
chroot for A, and to the live overlay — the same treatment `[IMPL-BOS-170]`'s
own two fixes used on 2026-09-07.

Worked example, the chrony install that `[IMPL-BOS-175]` got wrong, done the
right way once `bose` is back — `[GDE-ECHO-300]` wants every node on one LAN
reference, which is `smartboardpc` at 192.168.67.93:

```bash
ssh pi@bose sudo overlayroot-chroot apt-get install -y chrony
# the fleet source file, written to A and to the live overlay both
printf '%s
' 'server 192.168.67.93 iburst prefer minpoll 4 maxpoll 6'   'pool us.pool.ntp.org iburst maxsources 3' 'server time.nist.gov iburst'   | ssh pi@bose 'sudo overlayroot-chroot tee /etc/chrony/sources.d/lempi-fleet.sources'
ssh pi@bose sudo systemctl restart chrony   # live half; no reboot needed
```

Verify with `chronyc sources` showing `^* smartboardpc.lan`, then confirm it
survived by checking again after the next ordinary reboot — `[IMPL-BOS-165]`'s
standing lesson is that the file, not the exit code, is the evidence.

**Until the hatch sets `fstab` back to `rw` as well, treat it as unusable.**
It cannot edit `fstab` from inside the overlay — that is why the scope was
narrowed in the first place — so the honest repair is to drop the
`systemd-remount-fs` no-op and set A's `fstab` entry `rw`, or to retire the
hatch in favour of `overlayroot-chroot` and keep the card reader as the only
rescue.

**`[IMPL-BOS-166]` The first version called `raspi-config nonint
do_overlayfs 1` instead, and it silently did nothing — found on the first
real test of the actual enabled-to-disabled transition, 2026-09-06.**
`disable_overlayfs()`'s own implementation runs `sed -e
"s/\(.*\)overlayroot=tmpfs \(.*\)/\1\2/"` — a literal match against
`overlayroot=tmpfs ` with nothing between `tmpfs` and the next space.
`[IMPL-BOS-165]`'s own fix, `overlayroot=tmpfs:recurse=0`, means that
literal string never appears again, so `raspi-config`'s sed matches
nothing, changes nothing, and still reports success. One correction's own
fix broke the mechanism the next correction depended on — found only
because the actual file was checked afterward rather than the exit code
trusted, the identical discipline `[IMPL-BOS-165]` had just demonstrated
was necessary, applied a second time the same evening. Rewritten to edit
`cmdline.txt` directly (removing `overlayroot=` plus whatever follows it,
regardless of parameters, so it cannot go stale the same way again) rather
than route through `raspi-config` for this one step. Confirmed against a
real enabled-to-disabled transition, not only the already-disabled no-op
case the first version was tested against — twice, since the first fix
attempt had to be applied by hand (A was still locked, and the broken
escape hatch could not yet unlock itself) before the corrected script
could even be deployed durably.

**Scope was also narrowed in the same pass.** The original version also
tried to restore B's `fstab` line to `rw`. Dropped: any edit to a file
living on A — `/etc/fstab` included — made while A is *still* overlaid
lands in the RAM upper layer and is lost on the very reboot the script
itself triggers, the same transient-write trap this whole design exists to
avoid, self-inflicted. Restoring A alone is real and durable because
`cmdline.txt` lives on the boot partition, which is never overlaid;
reopening B was always `attended-import.sh`'s job and is unaffected.

`BosePi/request-unlock.sh` is the human-facing half: writes the C-side
marker over SSH and reboots, asked twice like every other script here that
undoes something deliberate.

**First run, before `bose` was ever `--lock-in`'d**: marker written, one
reboot, log confirmed `do_overlayfs 1 exit 0` and the marker cleared — but
only the already-unlocked no-op case, since there was nothing yet to
disable. The real enabled-to-disabled transition came later, against a
genuinely `--lock-in`'d `bose`, and is what `[IMPL-BOS-166]` above is
about: that first version turned out not to work at all once there was
something real to undo, and the fix proven afterward is what's actually
running now.

**C — state, read-write, the only continuously written partition.**

| Path | What |
| :--- | :--- |
| `/var/lempi/listener.db` | the whole database — library cache and listener state together `[IMPL-BOS-078]` |
| `/var/lempi/log/` | `/var/log` bind-mounted here `[PI-A-020]` |
| `/var/lempi/mpd/` | MPD `state_file`, `sticker.sql`, playlists |
| `/var/lempi/backup/` | `[REQ-LIB-160]` snapshots, pending copy off-device `[PI-C-030]` |
| `/var/lempi/etc-ssh`, `/home-pi`, `/nm-connections` | bind-mount sources for host identity and known networks `[PI-A-025]` |

**MPD's four state files split across two partitions**, which is the same
`[PI-DB-010]` line drawn again: `db_file` is derived from the library and lives
on B; `state_file`, `sticker_file` and `playlist_directory` are the listener's
and live on C. `lempipi` puts all four together under `/srv/library/mpd`
because it has no C to put them on; this image should not copy that.

---
