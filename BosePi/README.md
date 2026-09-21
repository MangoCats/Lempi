# BosePi — the second appliance

Everything specific to `bose`: the survey of the machine as found, the image
design, the build procedure, and the scripts and configuration that carry it
out.

**Separate from [LempiPi/](../LempiPi/) because the hardware differs enough to
matter.** `lempipi` reaches a Bluetooth speaker through PipeWire on 464 MB;
`bose` is a Pi 4 with 2 GB and a HiFiBerry DAC+ Pro on I²S. The player is the
same and the partition design is shared `[PI-PART-020]`, but almost every
setting below it lands differently — the output plugin, the mixer, the
architecture of the binary. Interleaving the two sets of notes would have meant
every reader working out which machine each paragraph was about.

## Read in this order

| | |
| :--- | :--- |
| [BOSE001](BOSE001-survey.md) | What was on `bose` before the build — measured, read-only |
| [BOSE002](BOSE002-image-build.md) | The image design: what goes on which partition |
| [BOSE003](BOSE003-build-procedure.md) | Where each phase runs, and in what order |
| [BOSE004](BOSE004-operating-health.md) | What the deployment looks like **in service** — the health baseline to compare against, the false alarms, and what is still open |
| [BOSE005](BOSE005-power-loss-test.md) | The first real power cut, 2026-09-10 — it passed, plus two findings only a boot could reveal |

## What is here to run

The build is a five-phase pipeline across two machines with a physical card
swap in the middle — no single script can run start to finish unattended, so
[`build-bose-card.sh`](build-bose-card.sh) detects which phase is next and
either runs it or tells you the one physical action needed to reach the next
state.

| | Phase | Runs on | Proven? |
| :--- | :--- | :--- | :--- |
| [`patch-boot-image.ps1`](patch-boot-image.ps1) | 1b: patch a freshly-imaged card's boot partition | dev host (Windows) | Manually, yes; **this exact script, no** |
| [`prepare-card.sh`](prepare-card.sh) | 2: partition and format | **on `bose`**, card in a USB reader | Yes, 2026-09-06 |
| [`provision-bose.sh`](provision-bose.sh) | 3: packages, mounts, `lempi`/`mpd` binaries | dev host, over SSH | Yes, run 3 times total, current |
| [`seed-library.sh`](seed-library.sh) | 4: deploy the local library via `relink` | dev host | **Yes** — 5,709 paths bound, swap-in landed |
| [`attended-import.sh`](attended-import.sh) | reopen B for a later import, close it again | dev host, over SSH | **Yes, fully** — dry run, success, and failure against a hand-simulated `ro`, then re-verified against `bose`'s own real `--lock-in`'d `ro` |
| [`finalize-bose.sh`](finalize-bose.sh) | 5: start, then (only when told) lock down | dev host, over SSH | **Yes, fully** — `--start` cold-booted twice; `--lock-in` run twice: once found a real bug (`[IMPL-BOS-165]`), once clean after the fix |
| [`build-bose-card.sh`](build-bose-card.sh) | orchestrates 2–5 | dev host | Yes — drove phases 3 through 5's `--start` to completion in one real run (found and fixed a `findmnt -q` bug along the way) |
| [`lib.sh`](lib.sh) | shared logging/precondition helpers | sourced, not run | Exercised via every script above |
| [`mpd.conf`](mpd.conf) | the guest's configuration, paths split across two partitions | deployed by phase 3 | Yes |
| [`lempi-bose.service`](lempi-bose.service) | lempi's unit | deployed by phase 5 | Yes — needed `--device hifiberry` (missing entirely at first; without it, `lempi` opened the Pi's own silent onboard jack, played, reported healthy, produced nothing audible). Confirmed audible after that fix, and again after two real cold boots. |
| [`lempi-unlock-check.sh`](lempi-unlock-check.sh) + [`.service`](lempi-unlock-check.service) | the lock-in escape hatch, checked every boot | deployed by phase 3 | **Yes, fully** — first version called `raspi-config nonint do_overlayfs 1`, which silently did nothing once `[IMPL-BOS-165]`'s own fix was in place (`[IMPL-BOS-166]`); rewritten to edit `cmdline.txt` directly, then proven against a real enabled-to-disabled transition, not only the already-unlocked no-op case |
| [`request-unlock.sh`](request-unlock.sh) | writes the C-side marker, asks twice | dev host, over SSH | Same as above — proven both ways now |

**`bose` is playing, audibly, on a clean traceable build (`358c5b176833`), fully `--lock-in`'d, as of 2026-09-06.** A is overlay-protected; B and C are both genuinely mounted directly — `ro` and `rw` respectively, no overlay wrapper on either. Getting there found two real bugs in the lock-in mechanism itself, both in the same evening, both by checking `findmnt`'s actual output rather than trusting an exit code:

- **`[IMPL-BOS-165]`**: `do_overlayfs 0`'s underlying implementation on this trixie-era image is Debian's own `overlayroot` package, not the historic Pi-specific mechanism reading the wrapper function had suggested — and its default (`recurse=1`) overlays **every** mount, not only A. B and C both came back wrapped in their own writable RAM layer on the first `--lock-in` attempt; `/var/lempi/listener.db` would have silently discarded every write on the next reboot — `[REQ-HW-120]` violated by the very mechanism meant to protect it. Fixed with `overlayroot=tmpfs:recurse=0`, now automatic in `finalize-bose.sh`.
- **`[IMPL-BOS-166]`**: that same fix broke `raspi-config`'s own `disable_overlayfs()`, whose `sed` only matches the literal `overlayroot=tmpfs ` with nothing after it. The escape hatch silently did nothing on its first real test against an actually-locked system. Fixed by editing `cmdline.txt` directly instead of going through `raspi-config` for that one step.

`attended-import.sh` was also re-verified against `bose`'s own real `--lock-in`'d `ro`, not only the hand-simulated one from before lock-in existed — clean, no surprises. Between this and the two bugs above, everything this pass touched has now been proven against the real, final, locked-down card, not a stand-in for it.

**Every "Proven? No" above means exactly that, not "probably fine."** Each of
those scripts encodes a sequence that was worked out and, where noted, done
successfully *by hand* — but the script itself, with its own exact commands,
regexes, and assumptions, has not been watched succeed. Different tool
versions, a different Raspberry Pi OS release, or bose-specific quirks not
yet hit could all change what "correct" looks like without the script
knowing. Each one says so in its own header and prints a reminder when run —
read their output, don't just wait for a clean exit.

Every phase script is idempotent except two steps that say so explicitly and
refuse instead of guessing: `prepare-card.sh` won't repair an existing but
wrong partition table, and `seed-library.sh`'s final swap-in won't overwrite
an existing `/var/lempi/listener.db` without `--force-swap`. `finalize-bose.sh
--lock-in` never runs without a human typing `--confirm-heard-it-play` —
[`build-bose-card.sh`](build-bose-card.sh) stops one step short of it, always.

## State of this work

**First real build, started and finished the same day, 2026-09-06 — playing
audibly, fully `--lock-in`'d.** All five phases have run for real, most of
them as the scripts above rather than by hand. See
[BOSE003](BOSE003-build-procedure.md)'s "Corrected" notes and
`[IMPL-BOS-140]` for what didn't match the plan on contact: cloud-init
instead of `userconf.txt`, a `resize` token instead of `firstboot`, `bose`'s
own e2fsprogs unable to grow the new filesystem, a missing `NOPASSWD`
sudoers entry, `blkid` off-PATH, `/srv/library` ownership, MPD's missing
`db_file` directory, `mpd.socket` silently un-masked by `systemctl enable`,
and — the one that actually kept it silent — no `--device` flag at all,
so `lempi` opened the Pi's own onboard jack instead of the DAC. Each was
found by actually running the thing, fixed, and re-verified, not assumed
fixed from reading the fix.

**What's left:** more power-loss trials. One real cut was taken 2026-09-10 and
**passed cleanly** — no corruption, no `fsck`, no hand recovery, nothing lost
— see [BOSE005](BOSE005-power-loss-test.md). `[PI-FS-050]` stays open anyway:
it asks for a *frequency* over many cuts, and one trial is not a rate. That
cut also produced two findings nothing else had surfaced — the ALSA cards
renumber across boots `[BOS-PWR-050]`, and the clock boots four days stale
because its restore file sits on read-only A `[BOS-PWR-060]`. `attended-import.sh` (`[IMPL-BOS-150]`) and
the escape hatch (`[IMPL-BOS-160]`) are both proven and ready for whenever
`bose`'s library needs to grow or its own `/etc` needs a fix, including via
`[SPEC035]`'s mesh sync.

Two decisions from the original plan remain open, unaffected by the above:

- **`[IMPL-BOS-090]`** — whether MPD should share a sink with the player
  (software mixer, crossfade works) or take the DAC directly (hardware mixer,
  no crossfade). The shipped configuration takes the first, because it is the
  one with evidence behind it.
- Whether the seek workaround `[SPEC-MPD-135]` is needed at all here. It was
  measured against PipeWire feeding Bluetooth `[PI-CHR-100]`; a local card may
  not have the fault.

> **`bose` is somebody's working music player**, with MuLibPlay running on it
> until this build. Everything here builds a *new* card. The old one stays
> intact and is the way back — see [IMPL-BOS-085](BOSE003-build-procedure.md)
> for how it's being kept that way even as the new one is seeded from a
> different source.
