# BOSE008: Updating bose's Image — What to Add, and What to Refuse

**Implementation Guide — the plan, and the budget it is held to**

> **Revised 2026-09-11, twice wrong in its first draft.** That draft held
> this plan to the Pi Zero 2W's 512 MB and called `bose`'s root overlay a
> RAM overlay. `bose` is a **Pi 4 Model B with 1,889 MB** and its overlay is
> **disk-backed**, both of which [BOSE001 §1](BOSE001-survey.md) already
> states — its opening line is literally "`bose` is not a smaller
> `lempi02w`". The draft was written without reading it. §1 and §4 are
> corrected below, and §4's recommendation is **reversed**: the first draft
> advised against splitting `bose`, which was the wrong answer to the
> question actually being asked.

`bose` runs an older build, is unsplit, carries none of the specials data,
and cannot be reached by half the `tools/` directory. This is what to change
and, more usefully, what not to.

The goal it is written against: **make the ecosystem cheap to maintain
without taxing the Pi.** Those pull in opposite directions often enough that
the tension has to be stated rather than resolved by instinct — every
addition below carries what it costs, and the ones that were rejected say
why.

> **Related:** [BOSE009](BOSE009-image-update-runbook.md) for the order to
> execute this in, the rollback at each step, and the three ways the first
> draft of this document would have failed · [BOSE003](BOSE003-build-procedure.md)
> for how the image is built · [BOSE005](BOSE005-power-loss-test.md) for the power cut this plan
> reasons about · [PI025](../LempiPi/PI025-what-the-local-split-owes-lempi02w.md)
> for the same exercise done against `lempi02w` · [IMPL001](../LempiPi/IMPL001-appliance-setup.md)
> for the package list this proposes one addition to

---

## 1. The budget

**`[BOS-IMG-010]` `bose` is a Pi 4 Model B with 1,889 MiB — four times
`lempi02w`'s 475 MiB — and the budget here is its own, not the Zero's.**
`[REQ-HW-140]`'s "every crate is a memory decision" was written for a Pi
Zero 2W, and quoting it at `bose` overstates the constraint by a factor of
four. Read from the running machine 2026-09-11, as
[BOSE001 §1](BOSE001-survey.md) also records.

So the test applied below is not "does it fit" — almost anything fits. It
is **resident cost against usefulness**: a command-line tool that runs for
200 ms during maintenance and is absent from RAM the rest of the time is
nearly free whatever the board, and a daemon is a permanent tenant whatever
the board. That argument survives the correction intact, which is why §2 and
§3 stand; it just is not a memory-scarcity argument, and presenting it as
one on a 2 GB machine would not have survived contact with anyone who
checked.

**`[BOS-IMG-015]` Measured, not assumed — and the first draft assumed.**
`bose`: 922 MB free on `/`, apt cache current to 2026-09-06, network
reachable, root on a **disk-backed** overlay (`upperdir=/media/root-rw/overlay`,
not tmpfs), so package installs and `/usr/local/bin` writes persist across a
reboot. Confirmed rather than argued: `/usr/local/bin/lempi` is dated
2026-09-06 16:56, the machine booted at 17:35, and the binary is still
there.

## 2. Add: `sqlite3` (557 KB, no daemon, no resident cost)

**`[BOS-IMG-020]` Install it.** *(Done 2026-09-11 -- `3.46.1-7+deb13u1`.
**Lost the same day and reinstalled durably; see below.**)* `bose` had no `sqlite3` command, which is
how the whole of this came up: `tools/remote_peek.py` is
`ssh <host> sqlite3 -json <path> "<sql>"`, so **every** tool built on it —
`remote_flags`, `sync_preferences`, `mesh_diff`, `resolve_mesh_conflict`,
`remote_snapshot` — reported that appliance unreachable when it was merely
differently equipped.

> **It did not stay installed, and that is the more useful lesson.** The
> 2026-09-11 install went to the overlay's tmpfs upper layer and was gone at
> the next reboot with the binary, the unit and both recovery helpers, as
> recorded in `[IMPL-BOS-185]`. So for several hours this document, and `[BOS-OPS-045]`
> in BOSE004, both asserted a command the appliance did not have — and the
> five tools above would have gone on reporting `bose` unreachable. Repaired
> the same day via `overlayroot-chroot` `[IMPL-BOS-180]`, verified present on
> A rather than only live, and **added to `provision-bose.sh`'s package list**
> so a rebuilt card arrives with it instead of needing this done again.

It costs 557 KB installed, has no reverse dependencies, runs only when
invoked, and is resident for the length of one query. Against that:

- **Diagnosis.** Every investigation run against `lempi02w` this week — shadow
  tables, journal modes, orphan counts, flag counts — was one `sqlite3`
  invocation. On `bose` none of them were possible without writing tooling
  first. The value of the command is highest at exactly the moment nobody
  wants to be writing tooling.
- **`lempi-db-recover` needs it**, and `bose` will need that script the day
  it splits (§4).
- **One exercised path.** `remote_peek` now falls back to `python3` where
  `sqlite3` is missing, and that fallback is worth having — but if `bose` is
  the only host that takes it, it becomes the path nothing else exercises,
  which is precisely the failure `[PI-OWE-010]` names. The fallback should
  be the safety net for an unknown host, not the daily route for a known
  one.

**`[BOS-IMG-025]` And record it in [IMPL001](../LempiPi/IMPL001-appliance-setup.md).**
Neither appliance's documented build installs `sqlite3`; `lempi02w` has it
because someone installed it by hand (`apt-mark showmanual` confirms), and
`bose` does not. That is not two policies, it is an absence of one, and the
next appliance built will be a coin flip. Whichever way this goes it belongs
in the package list beside `zram-tools` and `bluez`.

## 3. Refuse: everything daemon-shaped

**`[BOS-IMG-030]`** The tempting additions are all the ones that would sit
in RAM. Named here so they are refused once rather than re-proposed:

| Suggestion | Why not |
| :--- | :--- |
| A local `sshd`-triggered agent for sync | `ssh` already runs; a second listener buys nothing and costs memory permanently |
| `python3-full` / the whole stdlib | `python3` is present and sufficient; the extras are disk and update surface for nothing |
| A cron'd health reporter | `[BOS-OPS-*]`'s baseline is captured on demand by `power-test-manifest.sh`; a timer that samples a healthy machine forever is load in exchange for data nobody reads |
| Vipunen's tool-chain on the Pi | `[SPEC035]` already settled this: Vipunen work happens on Vipunen-capable nodes. The Pi is a player |
| A second database for metrics | `[SPEC-SC-015]`'s "no field without a consumer", one level up |

**`[BOS-IMG-035]` The rule these share**: an appliance should carry what it
needs *to play music* and what a person needs *to diagnose it when it will
not*. Nothing that samples, reports or synchronises on its own schedule --
the desktop initiates, the appliance answers. That is what keeps the
maintenance cost on the machine that has 32 GB.

## 4. The split, and what it would oblige

**`[BOS-IMG-038]` Split it — reversing this document's first draft.**
That draft argued `bose` has no read-only catalogue partition and therefore
no reason to split, which is true and is the wrong question. It reasons
about `bose` in isolation; the goal this plan is held to is **the cost of
maintaining the ecosystem**, and against that goal the database shape is the
single largest remaining difference between the two appliances.

Unsplit, every `tools/` invocation takes a different argument shape on
`bose` than on `lempi02w`, `lempi-db-recover` is mandatory on one and
meaningless on the other, and the two `ExecStart` lines cannot be read as
the same procedure. That is `[PI-OWE-010]`'s "a path only one machine takes
is a path that rots", stated about a machine instead of a code path — and it
is the same argument that decided the desktop split. Applying it to the
desktop and refusing it for `bose` was inconsistent, and the inconsistency
was mine.

The cost is low on this board: the three obligations in `[BOS-IMG-045]` are
one package, one script and one unit line, and `sqlite3` is recommended
anyway by §2.

**`[BOS-IMG-040]` `bose` is unsplit today, and therefore does not need
`lempi-db-recover` yet.** Checked rather than assumed, and the reason is
structural: the recovery script exists because a *split* player attaches
`library.db` `mode=ro`, and a read-only connection cannot roll back the hot
journal it is required to roll back — which on `lempi02w` turned one power
cut into a 23-restart crash loop `[PI3-FOUND-120]`. `bose` opens one
database read-write, so it rolls back its own journal on the first open.
`[BOS-PWR-*]`'s power cut on 2026-09-10 passed for exactly this reason.

**`[BOS-IMG-045]` So splitting `bose` obliges three things at once**, and
they are a package, not a menu:

1. `sqlite3` installed (§2) — `lempi-db-recover` is `/bin/sh` and shells out
   to it.
2. `lempi-db-recover` installed and wired as `ExecStartPre`, as `lempi02w`
   has it. Without this, the first power cut after the split is a crash
   loop, and the machine is cut from a speaker's USB port.
3. The unit's `ExecStart` changed to the two-path form
   (`listener.db --library library.db`).

Do fewer than three and the split is a trap that springs on the next power
cut rather than at the moment it is made.

**`[BOS-IMG-048]` Corrected: `bose` does not have "one writable
filesystem".** It has B (`/srv/library`, 105 GB, genuinely `ro` after
`[BOSE003]` step 10) and C (`/var/lempi`, 4 GB f2fs, `rw`) — a cleaner
separation than `lempi02w` manages, whose equivalent partition simply stays
`rw` forever. So the catalogue half belongs on B per `[IMPL-BOS-078]`, and
`bose` *does* have the read-only-catalogue situation that makes
`lempi-db-recover` load-bearing. Found while executing; see
`[BOS-RUN-080]`.

**`[BOS-IMG-050]` The reason is homogeneity, and `bose`'s own storage agrees.**
`lempi02w` is split because its catalogue lives on a read-only partition
`[PI023]`; the desktop is split because the split shape had to be the one
exercised daily. `bose` runs no Vipunen tools, which is why the first draft said no — but
its storage posture is B-read-only/C-writable, which is `lempi02w`'s design
rather than an exception to it `[BOS-IMG-048]`.

What it does need is to be *the same machine to maintain*. Three
installations in two shapes means every procedure, every document and every
tool invocation carries a fork, and the forked branch is the one that goes
untested until it fails. One shape everywhere is worth more than `bose`
saving a file.

## 5. The data `bose` is missing

**`[BOS-IMG-060]`** Independent of any of the above, and cheap:

```
python tools/load_occasions.py    /var/lempi/listener.db --write
python tools/backfill_profanity.py /var/lempi/listener.db --mulib <path>/mulib.db --commit
```

Being unsplit, `bose` needs neither `--library` nor the `--sql-out` patch
route — both tools take the one path. That gives it the six specials and the
69 recovered profanity ratings the other two installations already carry
`[SPEC-PREF-080]`, `[SPEC-PREF-082]`.

It also needs a current build: it is running a player that predates the
specials panel entirely, so the data would sit unread until
`LempiPi/deploy.sh <tag> pi@bose` follows.

## 6. The diagnostic helpers `bose` does not have

**`[BOS-IMG-062]` `lempi02w` carries fifteen `/usr/local/bin/lempi-*`
helpers and `bose` carries none.** Most are genuinely `lempi02w`'s and should
stay there — but four are hardware-neutral, and their absence is why
diagnosing `bose` means improvising each time.

| Helper | Port to `bose`? | Why |
| :--- | :--- | :--- |
| `lempi-vitals` | **yes** | Samples vital signs to a file that survives a wedge — needed most on the machine you cannot see |
| `lempi-underruns` | **yes** | Reads the player's own underrun counters `[PI3-FOUND-200]`; underruns are an audio-path fact, not a Bluetooth one |
| `lempi-startup-sample` | **yes** | Records what the player does for the first minutes after boot; boots are boots |
| `lempi-wifi-revert` | **no** — see `[BOS-RUN-045]` | Hardware-neutral and beside the point: it is scheduled only by `lempi-btctl`, which is not being ported, so alone it is a script nothing calls |
| `lempi-db-recover` | **on split** | `[BOS-IMG-045]`'s obligation, not optional once split |
| `lempi-wait-sink` | no | Blocks until **PipeWire** has a sink; `bose` has no PipeWire and goes straight to ALSA via the HiFiBerry |
| `lempi-btctl`, `-bt-agent`, `-hci-capture`, `-linkstate`, `-afh-seed`, `-radio-test`, `-speaker` | no | All Bluetooth. `bose`'s output is an I²S DAC |
| `lempi-led-boot` | no | Status-LED hardware `[PI3-LED-010]` |
| `lempi-rocker` | no | Not in the repository; `lempi02w`-local |

**`[BOS-IMG-065]`** These are `/bin/sh` and `python3` and cost nothing
resident — they run when invoked and exit. The rule in `[BOS-IMG-035]`
admits them precisely: they are what a person needs to diagnose the machine
when it will not play.

`lempi-wifi-revert` was on this list in the first draft and has been removed
`[BOS-RUN-045]`. "Hardware-neutral" was the wrong test: it is half of a
mechanism whose other half is `lempi-btctl`, and half a safety mechanism is
worse than none, because it looks like protection.

## 7. What stays different, even after all of this

**`[BOS-IMG-068]` The two appliances will not be homogeneous, and should not
all be forced to be.** Asked directly whether everything but the audio
device converges once this plan is executed, the answer is no. What remains,
read from both machines 2026-09-11:

| | `lempi02w` | `bose` |
| :--- | :--- | :--- |
| Board / memory | Pi Zero 2W, 475 MiB | Pi 4 Model B, 1,889 MiB |
| OS / kernel | Debian 12 bookworm, 6.12 | Debian 13 trixie, 6.18 |
| Root filesystem | plain ext4 | disk-backed `overlayroot` |
| Partitions | 3 | 6, `/srv/library` a separate `ro` ext4 |
| Audio path | Bluetooth via PipeWire | HiFiBerry I²S via ALSA |

The first two rows are hardware and distribution generation: converging them
means re-imaging a working appliance to an older Debian on slower silicon,
which trades a real machine for a tidy table. The filesystem and partition
rows follow from the hardware and from `[BOS-*]`'s own build. The audio row
is the one the question already excepted — and it is what drags the seven
Bluetooth helpers and `wait-sink` with it, so that exception is wider than
it first looks.

**`[BOS-IMG-069]` What this plan does converge is everything that a
*procedure* touches**: database shape, the tools that can reach the box, the
build, the specials data, and the diagnostic helpers. After it, a
maintenance instruction can be written once and run on either appliance,
which is the whole of what "homogeneous for maintenance" needs to mean. The
differences left over are ones no runbook has to mention.

---

## 8. Open

**`[BOS-IMG-070]`** None of this is applied. §2, §5 and §6 are independent
and individually reversible; §4 is a package to be taken whole or not at
all. **The order is not free**, though: the build must be deployed before
the split, or the old binary manufactures shadow tables on every boot
`[BOS-RUN-035]`. [BOSE009](BOSE009-image-update-runbook.md) is the sequence.

**`[BOS-IMG-075]`** The `python3` fallback in `remote_peek` will have no
regular exerciser once `bose` has `sqlite3`. It is pinned by shape in
`test_remote_peek.py`, which is not the same as being run. A forcing switch
— an environment variable that makes `run_remote_sql` take the fallback
deliberately — would let the suite exercise both paths against a real host,
and is the honest price of keeping a second path at all.

---

**Traceability:** `[BOS-IMG-010..075]` · revised 2026-09-11 after
[BOSE001](BOSE001-survey.md) was read and two of its own premises were found
wrong · holds `[REQ-HW-140]`'s memory
discipline over package choice · depends on `[PI3-FOUND-120]`'s crash loop
for §4's reasoning and `[BOS-PWR-*]`'s passed power cut for why it does not
apply yet · proposes one addition to [IMPL001](../LempiPi/IMPL001-appliance-setup.md)'s
package list · carries `[PI-OWE-010]`'s "a path only one machine takes is a
path that rots" into a decision about a package
