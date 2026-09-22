# PI025: What the Local Split Owes lempi02w

**Appliance Record — opened and settled 2026-09-11**

> **Status: paid.** Everything below was applied to `lempi02w` on
> 2026-09-11, in the order §4 required — deploy first, then drop, then WAL —
> and verified on the running appliance. The register is kept as the record
> of what was owed and how each item was proved, not as an outstanding list.

Splitting the *local* database turned up defects that lempi02w already has,
because lempi02w split first and nothing swept the rest of the system
afterwards. The local split completed on 2026-09-11; this register is what
it owes the appliance. Each one is found on the desktop, fixed on the desktop, and
then owed to the appliance — which is running today and is not going to be
disturbed for every one of them individually.

This is that debt, itemised. It exists so the fixes land on `lempi02w` as
one deliberate update rather than as four separate recollections.

> **Related:** [PI023](PI023-the-database-split-on-disk.md) for the split on
> disk · [IMPL011](IMPL011-database-split-built.md) for the migration as it
> ran · [IMPL009 §7.7](IMPL009-database-split-plan.md) for the prerequisite
> the local split had to build first

---

## 1. The pattern, stated once

**`[PI-OWE-010]`** Every item below is the same omission wearing different
clothes: **the split was done thoroughly for the player's main data path
and not swept across everything else that touches a database.** The player
got `QualifyingConn` and `__LIB__`; four folder-writing generators did not.
`split_database.py` copied schema and rows; it did not copy the journal
mode. `tools/` got nothing at all. `[IMPL002 §7.4]`'s second peer path was
stored and never read.

Recorded as a pattern and not four coincidences, because the next thing
that touches a database on a split installation will be the fifth unless
somebody goes looking.

## 2. Owed to lempi02w

**`[PI-OWE-020]` The four folder-writing generators are broken there now,
and `covers`/`cue_sheets` are both switched on.** `run_generation`
(`player/src/bin/lempi.rs`) opened the path the player was *started* with —
the listener half — while `cue`/`covers`/`lyrics_sidecar`/`lyrics_cache`
read catalogue tables and nothing else. On lempi02w that is
`no such table: passages` on the first statement, and has been since the
appliance split. Verified directly against `/var/lempi/listener.db`
2026-09-11, with `covers=1` and `cue_sheets=1` in `player_settings`.

**Fixed locally 2026-09-11** — the generators now open the catalogue half,
which is the same file on an unsplit installation and changes nothing
there.

**Applied 2026-09-11**, build `1a7e100`. Proved by asking for the work
rather than by reading the log: `POST /cue/1` and `POST /covers/1` on the
appliance itself, which before the deploy died on `no such table: passages`
at the first statement. After it:

    cue sheets: 1 cue sheet(s) written, 190 already current, 5518 needed no sheet
    cover art: 43 cover(s) written, 0 already current, 77 shared a folder,
               70 already had a cover, 1 without art

Forty-three covers is work that had not been happening since the appliance
split.

**`[PI-OWE-030]` lempi02w lost WAL in the split, and that is why its
database intermittently reports "locked".** The pre-split
`/srv/library/library.db` is `wal`; both live halves are `delete`. A fresh
SQLite file is `delete` and `split_database.py` never set the mode, so the
migration silently changed it. Measured 2026-09-11: a plain
`SELECT COUNT(*)` against `listener.db` over `ssh` failed once, seconds
after the service restarted, and succeeded four times immediately after —
which is exactly what `delete` mode does when a reader meets the writer,
and what WAL exists to avoid.

Fixed in `split_database.py` for future splits. For lempi02w it is
`PRAGMA journal_mode = WAL` on each half — persistent, no data change, no
rebuild. **Check first whether anything there writes both halves in one
transaction**: SQLite documents a cross-database transaction as atomic only
when the journal mode is *not* WAL. Nothing does today (the player attaches
the catalogue read-only), which is what makes this safe there and would not
make it safe everywhere.

**Applied 2026-09-11**, both halves, `integrity_check` clean after and the
mode persisting across a restart. The symptom went with it: twelve
consecutive `SELECT COUNT(*)` reads against the live listener half while
the player was running, twelve successes, where the same read had failed
intermittently before.

**`[PI-OWE-040]` Two empty shadow tables sit in lempi02w's listener half.**
`file_tags` (0 rows) and `cover_art` (0 rows) are in `/var/lempi/listener.db`
while the real ones — 5,709 and 1,079 rows — are in `/srv/library/library.db`.
The player opens the listener half as `main`, so an unqualified reference to
either resolves to the empty copy.

Harmless **today**: `library.rs` qualifies every catalogue table with
`__LIB__.`, so nothing reads them. It is a loaded gun rather than a wound,
and the two generators in `[PI-OWE-020]` are what a pulled trigger looks
like.

**And they are not left over from an older build — the player recreates
them on every start.** Found by splitting the local database and having
`tools/lempi_db.py`'s own shadow check refuse to open the brand-new pair:
the same two tables, on a pair minutes old. Dropped them, started the
player, and both came back; dropped them again, applied the fix below,
started it again, and they stayed gone.

The cause is two lines in `player/src/bin/lempi.rs` that read a setting and
attach a guest store through `PlayerStore::open(&db)`. `open(p)` is
`open_split(p, p)`, which tells the connection the listener half is the
whole database: the alias becomes `main`,
`ensure_library_tables_if_owned` concludes this connection owns the
catalogue, and it creates empty `file_tags` and `cover_art` in the listener
half. Both now use `open_split(&db, &library)`.

So dropping the two tables on lempi02w is not enough on its own — they will
be back on the next restart until the build carrying this fix is deployed.
Do the drop and the deploy together, or just the deploy and then the drop.

**Applied 2026-09-11, in that order.** Dropped after the deploy, and gone
after a full restart, which is the only test that distinguishes this fix
from a tidy-up. The real tables are untouched in the catalogue half —
5,709 `file_tags`, 1,079 `cover_art`.

`schema_meta` is in both halves **on purpose** (`split_database.py`'s own
`BOTH` list) and must not be dropped — the distinction is real and
`tools/lempi_db.py` encodes it as `SHARED_TABLES`.

**`[PI-OWE-050]` The console's sync-preferences job could not reach a split
peer.** `[IMPL002 §7.4]` designed the two-path model and it was half built:
`sync_peers.remote_listener`, `sync_remote_listener` and
`JobRunner.get_remote_listener()` all existed, with tests, and nothing read
them. Fixed locally 2026-09-11; the desktop's own `sync_remote` was also
still naming lempi02w's pre-split `/srv/library/library.db`, a file two days
stale that nothing reads. Nothing is owed *to* lempi02w here — the fix and
the misconfiguration were both on this side — recorded so the appliance is
not suspected of it later.

**`[PI-OWE-055]` Pulling flags from lempi02w reported "nothing flagged" for
a peer with 19.** `remote_flags.py` is the third script `[IMPL002 §7.4]`
named, and the last to be fixed. Against a split peer it failed in both
directions: pointed at the listener half, `no such table: passages`;
pointed at the catalogue half, `no such table: listener_flags` — which its
own guard, written for "no Lempi carrying `[REQ-VIS-265]` has opened this
library yet", turned into a cheerful empty result.

Fixed by attaching the peer's listener half in front of the query, the one
thing `run_remote_sql` can do with a second path since `sqlite3` takes more
than one statement. Measured against `pi@lempi02w` 2026-09-11: 0 flags
before, **19 after**, matching what the appliance actually holds. The guard
now fires only when no listener path was given, so "absent from this file"
can no longer masquerade as "absent from this installation".

Nothing is owed *to* lempi02w — the defect was on this side — but it is the
clearest instance yet of `[PI-OWE-010]`'s pattern, and the reason that
pattern is stated as a pattern.

## 3. Not owed, checked anyway

**`[PI-OWE-060]` The cascade replacement is not needed on lempi02w.**
`[IMPL009 §7.7]`'s `ON DELETE SET NULL` cascade is just as dead there as
here, but nothing on lempi02w deletes passages — segmentation is desktop-only
per `[SPEC035]`. Checked 2026-09-11 rather than assumed: **zero orphans**
across all three of `listener_play_history`, `selection_decisions` and
`player_state`. `tools/passage_orphans.py` is worth keeping as a
diagnostic there, not as scheduled work.

**`[PI-OWE-065]` The two-phase commit in the `apply_*` scripts is desktop-only
for the same reason.** Those three write the catalogue and then stamp a
listener-side review table, which on a split pair is two files and — under
WAL — not one atomic act. They now commit catalogue-first so the only
survivable half-failure is one a re-run repairs. lempi02w runs none of them,
so it inherits nothing here; recorded because the *reasoning* applies to
anything that ever writes both halves there, and nothing does yet.

**`[PI-OWE-070]` No catalogue table is missing from lempi02w's catalogue
half, and no listener table has strayed into it.** Checked the same day,
both directions. The only duplicates are the two in `[PI-OWE-040]` and the
deliberate `schema_meta`.

---

## 4. Open

**`[PI-OWE-080]`** *(The state when this register was opened. Settled the
same day — see the status banner at the top, and `[PI-OWE-095]` two entries
below, which records both installations reconciling afterwards. Left as
written rather than edited into agreement, because what was owed is the
record this document exists to keep `[GOV-DOC-050]`.)* None of the above has
been applied to lempi02w. The WAL
change is a `sqlite3` one-liner; `[PI-OWE-020]` and `[PI-OWE-040]` both need
the build, and `[PI-OWE-040]`'s drop must come *after* that deploy or the
next restart simply recreates what was dropped. Doing them together, once,
is the point of this document.

**`[PI-OWE-085]` The local database was split on 2026-09-11**, which is what
found `[PI-OWE-040]`'s real cause and `[PI-OWE-020]` before it. Both halves
are WAL, the shadow check passes, and the player and console both run
against the pair — so everything owed above was exercised on a real split
installation before it was applied to this one.

**`[PI-OWE-095]` Two split installations now reconcile.** With the desktop
and `lempi02w` both split, `sync_preferences.py` ran end to end for the
first time in that shape and pulled the one special the appliance held that
the desktop did not — `user.spiritual` on `93d4c0f2`, set from lempi02w's
own panel. Both sides then read identically and a further run reports
nothing to do. That is `[SPEC-PREF-155]`'s two-path model, `[IMPL002 §7.4]`'s
peer column, and `[SPEC-PREF-140]`'s specials sync all working at once,
against real hardware rather than a fixture.

**`[PI-OWE-090]`** `bose` was unsplit when this register was written, and
`[PI-OWE-010]`'s pattern is what it met on the day it was — **2026-09-11**,
per [BOSE009](../BosePi/BOSE009-image-update-runbook.md), which found two
further traps that reviewing had not. Planned out in
[BOSE008](../BosePi/BOSE008-image-update-plan.md), which carries this
register's findings into a decision about that image and adds the one this
work produced: `bose` had no `sqlite3`, so every tool built on `remote_peek`
reported it unreachable until that gained a `python3` fallback — and the
decision taken was to install the command rather than rely on the
fallback.

That plan now recommends **splitting** `bose` — reversing its own first
draft, which had argued the opposite from `bose`'s storage rather than from
the ecosystem's maintenance cost. The deciding argument is this register's
own `[PI-OWE-010]`: three installations in two shapes means every procedure
carries a fork, and the forked branch is the one that goes untested until it
fails.

**`[PI-OWE-100]` `[PI-OWE-030]`'s WAL change broke `lempi-db-recover`'s
reporting, and I did not notice at the time.** That script detects an
unclean stop by `[ -f "$db-journal" ]`, which under WAL can never be true
again — so the two lines that tell a boot log an unclean shutdown happened
were silently dead from the moment the mode changed. Recovery itself was
never affected: the script opens each database read-write regardless, and
that one open replays a WAL exactly as it rolls back a journal.

Fixed 2026-09-11 and deployed: `-journal` still means an unclean stop
outright, while a `-wal` must be **non-empty** to mean the same thing,
because an ordinary WAL database in use has one and announcing that on
every boot would train the reader to ignore the log. Three cases added to
`tests/cases-dbrecover.sh`, run against real SQLite **on the appliance**
because this desktop has no `sqlite3` either and skips the whole group. The
first restart after installing it said
`un-checkpointed WAL on /var/lempi/listener.db, replaying` — a line the old
script could not have produced.

---

**Traceability:** `[PI-OWE-010..090]` · follows [PI023](PI023-the-database-split-on-disk.md)
and [IMPL011](IMPL011-database-split-built.md) · carries
[IMPL009 §7.7](IMPL009-database-split-plan.md)'s prerequisite and
`[IMPL002 §7.4]`'s peer model forward to the appliance · opened while
splitting the local database, which is what found every item in it
