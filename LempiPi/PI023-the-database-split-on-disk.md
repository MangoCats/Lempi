# PI023: The Database Split, and the Filesystem Under It

**Appliance Record — why two databases, and what carries the writable one**

Split from [PI001](PI001-image-and-partitions.md) on 2026-09-10, which had reached 549 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [PI001](PI001-image-and-partitions.md) for the partition layout · [IMPL002](IMPL002-database-split.md) for the split itself

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-4 and §6-7 in [PI001](PI001-image-and-partitions.md), §5 and §5a in [PI023](PI023-the-database-split-on-disk.md), §5b in [PI024](PI024-appliance-settings.md).

## 5. The database split

**Designed in detail, reviewed, still not built, as of 2026-09-06.** Building
`bose`'s image was the first real attempt to apply this section, and it found
no `ATTACH`, no second file, no schema split — `lempi.db` is still one file,
exactly as `[SPEC-SC-010]` describes it today. `bose`'s image was built
around that reality rather than this one: the whole file lives on C, not
split across B and C — see [BOSE002 `[IMPL-BOS-078]`](../BosePi/BOSE002-image-build.md)
for why that specific substitution is safe for `bose` (its B genuinely
becomes read-only) in a way it happens not to be for `lempipi` (whose data
partition never actually does).

**[IMPL002](IMPL002-database-split.md) works out everything below in the
detail an actual build needs**: the exact `ATTACH` mechanics, every real
call site in the player today that mixes catalog and listener reads
unqualified (more than the "four queries" `[PI-DB-030]` originally named —
enumerated in full there), a genuine ownership gap found in review
(`PlayerStore` currently creates B-side tables as a side effect of being
"the only writable handle," which a split removes), the migration tool's
design, and a review against synchronization (`[SPEC035]`'s mesh tooling
needs no changes — it already addresses each side by an independent path),
RAM, backup, and MPD. Still not built: the player refactor and the migration
tool are both specified, not implemented, and neither has run against real
data.

**`[PI-DB-010]` One file becomes two, along a line the schema already draws.**
`[SPEC-SC-020]` segregates listener state by the `listener_` prefix precisely
so the class-D export is a table-set selection rather than a per-column
judgement. That same line is the partition boundary.

| File | Partition | Tables | Size today |
| :--- | :--- | :--- | ---: |
| `library.db` | B (ro) | `files`, `passages`, `passage_recordings`, `recordings`, `artists`, `releases`, `release_recordings`, `flavor`, `cover_art`, `file_tags`, `id_checks`, the caches | ~1,020 MB |
| `listener.db` | C (rw) | `listener_*`, `player_state`, `id_reviews`, `selection_decisions` | ~2.4 MB |

**The ratio is the point: 0.2%.** The partition being written continuously is
a thousandth the size of the one that is not.

**`[PI-DB-020]` The player opens `listener.db` and ATTACHes `library.db`
read-only.** Not the reverse. The writable connection is the one the player
owns; the library arrives as a read-only attachment, which makes
`[SPEC-SA-015]`'s guard a filesystem fact rather than a convention:

```sql
-- on the player's connection
ATTACH DATABASE 'file:/srv/library/library.db?mode=ro' AS lib;
PRAGMA main.synchronous = FULL;      -- listener state, per [PI-C-050]
PRAGMA main.journal_mode = WAL;
```

**`[PI-DB-030]` Four queries cross the boundary and must be schema-qualified.**
This is the real work of the split, and it is not cosmetic — an unqualified
name silently resolves to `main` and would read an empty table rather than
failing:

- `PLAYS_EXPR` — counts `listener_play_history` against `lib.recordings`;
- `review_queue` — joins `lib.id_checks` to `main.id_reviews`;
- `backup::restore` — re-points history through `lib.passage_recordings`;
- the Director's taste centroids — `listener_likes` against `lib.flavor`.

**`[PI-DB-035]` The boundary is defaults on one side, the listener's answer on
the other.** Several values exist in both files and mean different things:

| | `library.db` (B) | `listener.db` (C) |
| :--- | :--- | :--- |
| Taste centroids | derived defaults | saved, user-editable |
| Artist / track cooldowns | defaults from ingest | `listener_preferences`, edited |
| Like / dislike | — | `listener_likes`, entirely the listener's |

So a read is layered: take the default from `lib`, override it with the row in
`main` if there is one. That is why the *writable* file is the one the player
opens — the override always has somewhere to go, even with the library
mounted read-only.

It also means a reinitialised partition C is not a broken system but a
**factory-reset** one: every default is still present in `library.db`, and
what is lost is the listener's accumulated opinion. That is a real loss
`[PI-C-020]` and precisely why C is backed up off-device, but it is a
different kind of loss from a system that will not run.

Like/dislike `[REQ-PD-150]` is unbuilt, and belongs on this side of the line
when it is written — it is the listener's judgement, and nothing re-derives it.

**`[PI-DB-040]` Vipunen writes `library.db` on a desktop and never on the Pi.**
Which is already true, and the split makes it enforceable rather than merely
intended.

---

## 5a. Filesystem for partition C: ext4 vs f2fs

**`[PI-FS-010]` `data=journal` is the wrong instinct here, and it is worth
saying why.** It looks like the safest option — journal the data as well as
the metadata, so a torn write cannot leave a half-updated file. But it doubles
every write, and it is largely redundant with what SQLite is already doing.

SQLite in WAL mode with `synchronous=FULL` `[PI-C-050]` already guarantees
that a committed transaction survives power loss: it writes the WAL frame,
fsyncs, and only then reports success. What the filesystem must supply is
(a) metadata that is not corrupted by an interrupted write, and (b) an
`fsync` that is honest. **Metadata journalling gives both**; `data=journal`
adds a second copy of bytes SQLite has already made durable itself.

On an SD card the cost is not theoretical. Flash erases in blocks far larger
than a SQLite page, so the controller already amplifies small writes; doubling
them at the filesystem layer compounds it, and the write pattern here is a
steady drip that never stops.

**`[PI-FS-015]` How steady is now a setting.** The resume point is written on
an interval that defaults to **5 seconds** and is adjustable from the Lempi
skin's settings panel, 1 s to 5 min `[REQ-VIS-155]`. On an appliance this is
the single largest source of unattended writes, so it is the one number that
most directly decides how much of partition C's life is spent with a write in
flight — and it belongs to the installation, not to the source.

Lengthening it costs bounded and small: at most that much playback position
after a power cut. Passage changes, pause and resume bypass the interval and
are written the moment they happen, so what the setting trades is a few
seconds of position, never an event.

**`[PI-FS-020]` The real comparison is ext4 `data=ordered` against f2fs.**

| | ext4 (`data=ordered`, the default) | f2fs |
| :--- | :--- | :--- |
| Design target | spinning disks and SSDs alike | flash with an FTL, specifically |
| Write pattern | in-place update | log-structured, append |
| Amplification on SD | moderate | lower — writes align to erase blocks |
| Small frequent fsync | fine | better; this is what it was built for |
| Append-heavy logs | fine | well suited |
| Recovery | `e2fsck`, extremely mature | `fsck.f2fs`, far less exercised |
| Pi OS support | default, universal | in-kernel, not the default |
| If it goes wrong | a well-trodden path | fewer people have been there |

**`[PI-FS-030]` The recommendation is f2fs, and the reason it is a safe
recommendation is `[PI-C-040]`.** Partition C is the one the design already
declares expendable: the player must start with it absent and recreate it. So
the usual objection to f2fs — that its recovery tooling is less battle-tested
— costs much less here than it would on a root filesystem. If f2fs loses
partition C in a way `fsck.f2fs` cannot mend, the answer is the same answer we
had already committed to: make a new one and carry on.

Set against that, its advantages land exactly on Lempi's write pattern: many
small transactions, continuous appends, and an SD card underneath.

**`[PI-FS-040]` Partition B stays ext4.** It is written rarely and attended,
mounted read-only the rest of the time, and holds a gigabyte that takes hours
to rebuild. Maturity is worth more than wear levelling on a partition that
barely wears.

**`[PI-FS-050]` Unmeasured.** Every claim above is reasoning from how these
filesystems are built, not from Lempi running on a Pi — which has not
happened `[PI-IMG-030]`. The honest test is a power-pull rig: write
continuously, cut power at random, count how often the database survives and
how often `fsck` is needed. Until that is run, this is a recommendation and
not a finding. **One such cut has now been taken** — `bose`, 2026-09-10, passed clean; see [BOSE005](../BosePi/BOSE005-power-loss-test.md). One trial is not the frequency this asks for, so this stays open.

---

