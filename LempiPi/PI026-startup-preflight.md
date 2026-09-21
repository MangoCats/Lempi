# PI026: What the Startup Path Depends On, and Saying So

**Implementation Guide — the tools a boot needs, reported at every boot**

Asked whether startup could verify the tools it needs, log their versions, and
proceed accordingly. It can, it now does on both appliances, and building it
turned a reporting request into a repair: the missing tool no longer stops
recovery, it only changes which command performs it.

> **Related:** [PI025](PI025-what-the-local-split-owes-lempipi.md) `[PI-OWE-100]`
> for `lempi-db-recover`'s WAL reporting · [BOSE009](../BosePi/BOSE009-image-update-runbook.md)
> for why read-only media makes recovery matter more on `bose` `[BOS-RUN-090]`

---

## 1. The failure this closes

**`[PI-PRE-010]` A never-fatal script cannot report its own disarming.**
`lempi-db-recover` is what stands between a power cut and a crash loop
`[PI3-FOUND-120]`, and two of its properties combine badly. It shells out to
`sqlite3`; and it is deliberately never fatal, because a player that starts
and complains beats one that refuses. So if `sqlite3` ever went missing, the
script would print one line about failing to open a database, exit 0, and
recovery would **silently stop happening** — with nothing in the log
separating *"this database is broken"* from *"the tool that repairs it is
gone."* The next power cut would be the crash loop again, months after the
change that caused it.

That is not hypothetical on this ecosystem. `bose` had no `sqlite3` at all
until `[BOS-IMG-020]` installed it on 2026-09-11, and `bose` is the machine
whose catalogue sits on read-only media where recovery is hardest
`[BOS-RUN-090]`.

## 2. Two answers, and the second is the better one

**`[PI-PRE-020]` Report what is there.** `lempi-preflight` runs first in the
`ExecStartPre` chain, names each tool the startup path uses, gives its
version, and states what is lost if it is absent. It repairs nothing and it
**always exits 0** — refusing to boot over a missing diagnostic helper would
be the failure this prevents, not a defence against it.

**`[PI-PRE-030]` Then survive without it.** `lempi-db-recover` no longer names
a command; it resolves a runner, preferring `sqlite3` and falling back to
`python3`, which every Lempi appliance carries because the tooling already
assumes it. Reporting a missing tool is worth something. Not needing it is
worth more.

## 3. The fallback is equivalent, and that was measured

**`[PI-PRE-040]`** The recovery is SQLite's, not the command's — both runners
do one read-write open and read one page, and SQLite repairs on the way in.
That is the reason the command can be swapped, but a reason is not evidence.
Run on `lempipi` 2026-09-11, against two fixtures built by killing a writer
with `SIGKILL`:

| fixture | `sqlite3` | `python3` |
| :--- | :--- | :--- |
| WAL, committed frames never checkpointed | frames replayed, `-wal` removed, both rows readable | **identical** |
| `delete`-mode hot journal, dirty pages really spilled into the `.db` | rolled back, journal deleted, uncommitted rows gone | **identical** |

The second fixture needed care to be worth anything. A first attempt built a
"hot journal" from a small uncommitted transaction that never spilled out of
the page cache, so the database file had never been modified, rollback was a
no-op, and both runners "passed" a test that proved nothing — while leaving
the journal file in place. Forcing the spill with `PRAGMA cache_size=10` and
20,000 rows made rollback a real act with a visible result.

**`[PI-PRE-045]`** That first attempt also found a live false alarm worth
knowing about: after a hot journal with **nothing to roll back**, both runners
leave the journal file behind at full size, so `lempi-db-recover`'s
post-recovery check prints `still journalled after recovery` on stderr for a
benign leftover. It is stderr-only, it does not affect the start, and the
message is literally true. Left alone rather than loosened, because a check
that under-reports a real failure would be worse than one that occasionally
over-reports a harmless file.

## 4. What it reports, and how it decides what to report

**`[PI-PRE-052]` The clock, and the one place `[PI-PRE-050]` does not
apply.** Added 2026-09-18. `[GDE-ECHO-300]` asked for this in its own second
sentence — "extend the startup preflight with a check that it is running and
converged" — and it was never written, so nothing noticed that `lempiplay3`
had been left on `systemd-timesyncd` when the other three nodes moved to
chrony. A listener found it eight days later, hearing the follower wander;
the measured skew was 688 ms and went to a median of 12 ms on chrony alone
`[GDE-ARC-038]`.

It names the disciplining daemon and its reference, and warns with the reason
when that daemon is `systemd-timesyncd` — an SNTP client that corrects the
*time* without disciplining the *frequency*, which is the half echo needs —
or when nothing is disciplining the clock at all.

Reported **unconditionally**, which is the exception to asking the machine:
following is a setting a listener can turn on at any moment
`[SPEC-ECHO-010]`, so every node is a potential follower and there is nothing
to ask. Still never a gate `[PI-PRE-020]` — a bad clock costs alignment, not
playback.

**`[PI-PRE-050]` It asks the machine, not a list.** Which tools matter differs
per appliance, and hard-coding that means two lists to keep in step. The
audio-path tools are reported only when `lempi-wait-sink` is installed, since
that is the only thing in the startup path that uses them. `lempipi` has it
and `bose` does not — and neither machine is named anywhere in the script to
get the right answer.

Live output, 2026-09-11, unedited:

```
# lempipi
lempi-preflight: lempi 0.1.0 (1a7e1008ab62) at /usr/local/bin/lempi
lempi-preflight: sqlite3 3.40.1 at /usr/bin/sqlite3
lempi-preflight: python3 3.11.2 (sqlite module 3.40.1) at /usr/bin/python3
lempi-preflight: database recovery armed via sqlite3
lempi-preflight: wpctl (wireplumber 0.4.13) at /usr/bin/wpctl
lempi-preflight: bluetoothctl 5.66 at /usr/bin/bluetoothctl

# bose -- no audio-path lines, because it has no lempi-wait-sink
lempi-preflight: lempi 0.1.0 (5272e3f62ca3) at /usr/local/bin/lempi
lempi-preflight: sqlite3 3.46.1 at /usr/bin/sqlite3
lempi-preflight: python3 3.13.5 (sqlite module 3.46.1) at /usr/bin/python3
lempi-preflight: database recovery armed via sqlite3
```

**`[PI-PRE-055]` The version is the point.** "`sqlite3` present" ages into
uselessness the moment a version difference matters. These six lines already
answered a question nobody had asked: the two appliances run **different
player builds** (`1a7e1008` against `5272e3f6`) on **different SQLite**
(3.40.1 against 3.46.1). Both facts are consequences of `lempipi` being on
bookworm and `bose` on trixie `[BOS-IMG-068]`, both are fine, and neither was
visible in any boot log before today.

**`[PI-PRE-060]` Version strings are asked for, not guessed.** No two of these
tools agree on how to be asked: `sqlite3 --version` leads with the number,
`bluetoothctl --version` trails it, and `wpctl` has **no version option at
all** — it must be asked through `wireplumber`. The first draft assumed a
uniform form and logged `wpctl Usage:`, which is how a diagnostic becomes
noise.

## 5. Degraded behaviour, as executed

**`[PI-PRE-070]`** All three states were run on `lempipi` against a real dirty
WAL database, with the tools hidden behind a sanitised `PATH`:

| state | preflight says | recovery |
| :--- | :--- | :--- |
| both present | `armed via sqlite3` | `sqlite3` |
| no `sqlite3` | `ABSENT` + the ssh-tooling consequence `[BOS-IMG-020]` | **succeeds** via `python3`; frames replayed, `-wal` removed |
| neither | `UNAVAILABLE`, naming `[PI3-FOUND-120]` | refuses, says exactly why, **exits 0**; frames left intact, not lost |

The third row is the important one. Recovery does not happen, the start is not
blocked, the data is not damaged, and the log says which of those three is
true.

**`[PI-PRE-075]` The unit test that should have caught a missing helper could
not see one.** `cases-units.sh` checks that every unit names a program which
ships beside it -- a good check, written against `^ExecStart=`, which cannot
match `ExecStartPre=`. So the two helpers that run *before* the player were
outside it, and those are the ones where absence is worst: systemd counts a
missing `ExecStartPre` as a failed start, and `Restart=always` turns that into
the boot loop this whole mechanism exists to prevent. Widened to both, and
verified by mutation -- pointing a unit at a helper that does not exist now
fails the suite, where before it passed.

`finalize-bose.sh` had the matching hole: it shipped `bose`'s unit without the
helpers the unit names. It now installs them first `[BOS-RUN-085]`.

## 6. The database nobody opens

**`[PI-PRE-090]` `lempipi` was recovering its pre-split original at every
boot.** `lempi-db-recover` walks three paths, and the first of them defaults
to the whole database that `[IMPL-DBSPLIT-025]` superseded. On `lempipi` that
is `/srv/library/library.db`: 1.16 GB, opened read-write on the slowest machine
in the ecosystem at the one moment it is trying to start, on behalf of nobody.

It is skipped now when **both** halves exist beside it. The rule is one-sided
by design, because the two mistakes are not equal: recovering a database
nobody opens wastes a little boot time, while skipping one the player *does*
open is a crash loop. So the halves' own existence is the test, and an
appliance that has not been split yet is unaffected — its halves are absent,
the whole file is live, and it is recovered exactly as before. Rolling a split
back by removing the halves restores that automatically, with nothing to
remember.

The skip is announced rather than silent. The file is still sitting there
taking a gigabyte, and the line stops the day somebody deletes it:

```
lempi-db-recover: not recovering /srv/library/library.db, superseded by the split halves
lempi-db-recover: un-checkpointed WAL on /var/lempi/listener.db, replaying
```

**`[PI-PRE-095]` What that file was, measured rather than assumed.** Frozen at
the split — 38,554 plays, last at 2026-09-07 02:34, against 39,037 live — and
byte-for-byte different from its own dated backup despite being logically
identical to it.

A first draft blamed that divergence on the boot-time opens. Wrong, and
checking took one command: the mtime had not moved since 2026-09-08, across
every boot since. The opens were wasted work, not damage, and the `-shm` files
that looked like evidence were thirty seconds old — created by the read-only
queries run to investigate. The change was worth making on cost alone; it did
not need the worse story.

**`[PI-PRE-098]` Deleted, all of them, 2026-09-11.** Two copies was the
finding; the answer given was that one is already excessive. Looking properly
then found six — 5.3 GB of one database's history stacked up on `lempipi`:

| removed | size | what it was |
| :--- | ---: | :--- |
| `lempi.db` | 1.08 GB | the pre-split original |
| `lempi.db.pre-split-20260907` | 1.08 GB | identical content, dated copy |
| `lempi.db.pre-lyrics-import` | 1.08 GB | pre-migration, the import 5 days in service |
| `lempi.db.bak-pre-mulib-art` | 1.02 GB | pre-migration, 19 days old |
| `lempi-new.db` | 1.00 GB | staging copy, never touched after the swap it staged |
| `…pre-fade-migration-20260831.bak-journal` | 1 KB | orphan; its `.bak` was already gone |

`lempi-testlib-20260820.db` (29 MB) was kept — a test library is not a backup
of the live one, and it is a different thing to have.

**`[PI-PRE-097]` `bose` mattered more, for a reason `lempipi` does not have.**
Its pre-split original was 1.10 GB on the **4 GB f2fs partition that absorbs
every write the appliance makes** — the one storage constraint here that is
real rather than theoretical. `lempipi` had 170 GB free and lost only clutter;
`bose` went from 37% of C to 10%. Verified the same way first: 8,185
recordings both sides, and 39,329 plays live against 39,294 in the copy, so
the live half was ahead rather than merely equal.

Deleting it orphaned `lempi.db-shm` and `lempi.db-wal` beside nothing — the
same shape as the `pre-fade-migration` journal above. Both removed, and both
machines then audited for the class rather than the instance. Neither has
another.

**`[PI-PRE-099]` What was checked first, because deletion is the one step with
no rollback.** Not "it looks superseded": `integrity_check` on both live
halves (`ok`, and it takes minutes on a Pi Zero 2W — the cost this script
refuses to pay at boot); catalogue parity table-for-table (8,152 recordings,
16,409 passages, 5,709 files — identical); and every listener table confirmed
a **superset** in the live half, including `listener_characteristics`, which
exists only there. A tree-wide search then found the two documents that
recorded these files as deliberately kept, so the claim and the fact were
corrected together rather than one of them being left to rot.

`lempipi` went from 54 GB used to 48 GB. And the skip line from
`[PI-PRE-090]` has now stopped on its own, exactly as intended — the file it
named is gone.

## 7. Open

**`[PI-PRE-080]`** `lempipi`'s unit template in `setup-lempipi.sh` still names
the pre-split `/srv/library/library.db` in its base `ExecStart`; the live
machine is correct only because `mpd-guest.conf` overrides it.

Deleting those copies `[PI-PRE-098]` improved this by accident, and the
direction is worth noting.
While that file existed, losing the drop-in meant the player would open a
database four days stale and **run**, reporting nothing wrong — plays going
into a file nobody reads. Now the file is gone, so the same mistake is a
refusal to start: loud, immediate, and obvious. Deleting the fallback made
the failure mode better, which is the usual shape of it. Still worth fixing
properly by naming the halves in the template, once the fresh-install path —
which has no split to name — is settled.

**`[PI-PRE-085]` Closed** by `[PI-PRE-090]` on the same day it was raised.
`lempi-db-recover` no longer opens the pre-split original once the split
halves exist. The file itself is still there `[PI-PRE-098]`.

---

**Traceability:** `[PI-PRE-010..085]` · answers a question put after
`[BOS-RUN-090]` · implemented in `lempi-preflight` and `lempi-db-recover`
