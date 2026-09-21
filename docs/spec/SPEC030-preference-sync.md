# SPEC030: Syncing Listener Preferences and Specials Between Installations

**Design Specification — Tier 2 · Built**

[SPEC029](SPEC029-listener-preference-editing.md) gave a listener a way to
edit an artist's or a recording's own `rotation`/`recovery`/`restraint`
locally, and later its "special" tags — Christmas, Children's, Profanity,
Spiritual — in the same panel. This is what happens once two installations
— a desktop and `lempipi`, say — have each done that independently, and
someone wants the two reconciled.

> **Status.** Built 2026-09-04, per `[REQ-VIS-295]`. `tools/sync_preferences.py`
> (the tool), `jobs.py`'s `sync-preferences` job kind, and a third button on
> the console's existing `/flags` "Sync with a remote" section. Verified
> live against the real desktop and `pi@lempipi` installations this
> session: a real edit, dry-run detected it, `--commit` landed it on the
> remote (service stopped, patched, restarted, confirmed running), and a
> follow-up dry-run reported the two sides converged.

> **Related:** [SPEC029](SPEC029-listener-preference-editing.md) for the
> editing surface this syncs the result of · [SPEC006 §3](SPEC006-data-flow-and-portability.md#3-what-travels-and-what-must-not)
> for the Class-D boundary §1 below crosses, and §9 for the review-decision
> sync this deliberately does *not* copy · [SPEC022](SPEC022-flag-and-edit-sync.md)
> for `listener_flags`' own sync, whose console page and `remote_config`
> this reuses directly

---

## 1. Why this is new territory, not a rebuild

**`[SPEC-PREF-100]`** `listener_preferences` — and, since §7,
`listener_characteristics` beside it — is Class D
([SPEC006 §3](SPEC006-data-flow-and-portability.md#3-what-travels-and-what-must-not)
names it explicitly, alongside likes/dislikes, play history and programs).
Class D's stated rule is that it "never travels with music," moving only
by whole-`lempi.db` migration or the hourly class-D backup/restore
(`[SPEC-DF-090]`, `REQ-LIB-160`). Neither fits here: both are whole-class-D
transfers that would overwrite an installation's own distinct play
history and likes along with whatever rotation/recovery/restraint moved,
and the backup/restore direction is one-way (restore *from* a snapshot),
not the bidirectional, selective reconciliation asked for. This document
is the first time a *subset* of Class D gets its own narrow, two-way sync
— deliberate, not an oversight in `SPEC006`'s own rule, and scoped to
exactly what one panel edits.

## 2. The conflict model: last-write-wins, not a three-way merge

**`[SPEC-PREF-105]`** [SPEC006 §9](SPEC006-data-flow-and-portability.md#9-syncing-an-applied-edit-to-a-remote-installation)'s
baseline/target/current three-way merge (`tools/apply_changes.py`) exists
because a review decision carries real provenance stakes — *which*
correction is actually right — and captures what it replaced specifically
so that can be judged later. `listener_preferences` carries no such
history: a row is only ever "the current tuning," with one `updated_at`
covering all three fields together. There is nothing to compute a
baseline against. **The side with the newer `updated_at` for a given
`(subject_kind, subject_id)` wins**, and its whole row — the "preference
setting group" the tuning was committed as — is copied to the other side.
An exact tie (equal timestamps, differing values) is reported and left
alone rather than guessed at; unlike `apply_changes.py`'s conflicts, there
is no `--resolve` step, because there is no case here where a person's
judgment, not a clock, has to decide.

## 3. The existence rule

**`[SPEC-PREF-110]` A subject syncs only if it is a real artist or
recording on *both* sides.** A subject tuned on only one side is
unambiguously "newest" there (nothing to compare it to) — but it still
does not move if the *other* side's library has never heard of that
artist or recording at all. Checked with a batched `IN (...)` query
against `artists`/`recordings`, scoped to exactly the — typically small —
set of one-sided subjects; never a full-catalogue fetch.

## 4. The protocol, and why one whole-table read already is minimal

**`[SPEC-PREF-115]`** [SPEC022](SPEC022-flag-and-edit-sync.md)'s
`remote_flags.py` already established the precedent this reuses directly:
`listener_flags` is "one small table, a handful of rows even on a
well-used appliance," so fetching it *whole*, in one `ssh ... sqlite3
-json ...` round trip (`tools/remote_peek.py`'s `run_remote_sql`), already
*is* "near minimum necessary" — not a manifest-then-fetch protocol.
`listener_preferences` is the same shape and the same scale: a row exists
only once a subject has actually been tuned, and MuLibPlay's own migrated
data was 36% of tracks (`[GDE-BMK-020]`), not the whole catalogue. Where
"transfer only the changed preferences" actually bites is the **write**
side, and that is where this tool is careful: only the rows that differ
are ever patched, in either direction, never a blind overwrite of the
whole table either way.

Round trips, each only incurred when there is something to do:
1. One remote `SELECT` — the whole `listener_preferences` table.
2. Up to two remote `IN (...)` existence checks (one per subject kind),
   only for one-sided subjects, skipped entirely if there are none.
3. One remote write — only if the decision produced a non-empty push set.

## 5. Applying a decision

**`[SPEC-PREF-120]` Local writes are advisory-only about the player being
stopped**, the same posture `apply_changes.py` already takes for a local
db (`[PI5-LIB-010]`'s precedent, documented not enforced). One improvement
over `apply_changes.py`: a best-effort `POST /library/reload`
([SPEC029](SPEC029-listener-preference-editing.md)'s own `preference.rs`
route) after a successful local write, so an already-running local Lempi
picks the change up without needing a restart — silently skipped if
nothing answers on that port, the same "no Lempi running locally" case
every other local-write tool already tolerates.

**`[SPEC-PREF-125]` A remote write follows the identical `[PI5-LIB-010]`
recipe `jobs.py::_remote_push` and `push_file_tags.py` already use**: `scp`
a small `.sql` patch (`INSERT OR REPLACE INTO listener_preferences`, one
statement per changed row, values rendered with `remote_peek.literal` —
no bind parameters cross an `ssh` boundary), then
`ssh host "sudo systemctl stop lempi && sqlite3 <path> < <patch> && sudo systemctl start lempi"`
— issued only when the push set is non-empty, its own distinct temp
filename so a concurrent decision-sync's patch is never clobbered.

## 6. Reached from the console, not a new page

**`[SPEC-PREF-130]`** The `/flags` console page already carries everything
this needs: a `remote_config` field (`user@host:/path`, sidecar-stored,
`jobs.py::get_remote()`/`set_remote()`), and job-submission buttons that
POST to `/api/remote/...` and poll job events. A third button, "sync
preferences," reuses this wholesale — `POST /api/remote/sync-preferences`
submits the `sync-preferences` job kind, unchanged UI pattern, no new page.
`sync-preferences` does not appear in `jobs.py`'s `SKIPPED` list: unlike
`segment`/`cd-rip`/`amplitude`, it was never something `induct`/`reanalyze`
would otherwise attempt — it is a cross-installation maintenance action,
reached only from its own console button, the same standing `remote-pull`/
`remote-push` already have.

---

## 7. The specials, on the same terms

> **Status.** Built 2026-09-10, per `[REQ-VIS-315]`.

**`[SPEC-PREF-140]` `listener_characteristics` syncs by §2's rule, reusing
§2's code.** Its rows are the same shape of thing `listener_preferences`'s
are — one per decision a person made, its own `updated_at`, no merge
history — so every argument for last-write-wins carries over unchanged, and
the way to keep it carrying over unchanged is for it to be the same
function. `decide()` gained two parameters (`when`, `subject_of`) rather
than a near-copy: this key holds two more fields and keeps its timestamp
somewhere else, and nothing else about the decision differs. §3's existence
rule applies too, against the *recording* inside the longer key, batched
into the same round trip the tunings already pay for. One patch, one
service stop, one restart, however many of the three tables moved — three
pushes would be three gaps in the music to sync three tables nobody edited
separately.

**`[SPEC-PREF-145]` The registry syncs too, and additively — never
last-write-wins.** A value for `user.spiritual` means nothing on an
installation that has never heard of `user.spiritual`, so
`listener_occasions` and `listener_occasion_points` travel with the values;
that is what "usable once defined, on another database" actually requires.
But a curve is not a fact about one recording, it is a setting someone
tuned (`--kids 0.5`), and it carries no `updated_at` to judge by. So: a
definition the other side lacks is **copied**; a definition both sides have
and disagree about is **reported and left alone on both sides**. The
alternative is a sync that silently retunes somebody's seasons, which is a
much worse failure than a conflict a person has to go and look at.

**`[SPEC-PREF-150]` A far side that predates any of this still syncs — and
"absent" is never confused with "could not be read".**
`listener_characteristics` is `CREATE TABLE IF NOT EXISTS`-ed by the patch
before anything is inserted into it, and `listener_occasions.label` is
dropped from the column list when the remote's own table has no such column
— read back as absent rather than assumed. A name is the least important
thing being carried, and losing it must not cost the curve.

`run_remote_sql` deliberately collapses every failure to `{"ok": False}`,
because `[SPEC-DF-118]` only ever needed "did this work". That is exactly
wrong here, and `absent_table()` is the correction: only a *no such table*
error reads as an empty table. Every other failure — a locked database, a
timeout, a dropped connection — leaves the remote's state **unknown**, and
unknown stops the sync rather than being treated as empty. The difference is
not theoretical: reading a locked database as "the remote has no specials"
would push every local value over whatever is actually there, including
newer values, silently. Observed live on 2026-09-10, a count against
`pi@lempipi` failing once seconds after the service restarted and succeeding
four times immediately after.

**`[SPEC-PREF-155]` A split peer is addressed by two paths, and the
positional one is the catalogue.** `[IMPL002 §7.4]` settled this before
either was built: `sync_remote`/`sync_peers.remote` means a peer's
**catalogue**, because `mesh_diff.py` wants that and only that, and the
listener half is a nullable second value read as "the same file" when
absent — true of every peer that has not split, so nothing already
configured needs re-entering. This tool takes it as `--remote-listener`,
and everything it reads and writes goes there; the existence check in
`[SPEC-PREF-110]` is the one thing that genuinely wants the catalogue, and
it is the one thing addressed to the positional argument.

Without it the failure is silent and total: `run_remote_sql` opens exactly
one path, so every existence query against a split peer errors,
`{"ok": False}` collapses to an empty set, every one-sided subject is filed
`skip_missing`, and the tool reports a clean "nothing to do" while having
synced nothing at all. Found live against `pi@lempipi` on 2026-09-10.

Two things had gone stale rather than been wrong when written. This
document's own **Status** block records a live verification against that
installation on 2026-09-04 — true then, quietly falsified when the
appliance was split afterwards `[IMPL011]`; a verification is only as
current as the shape of the thing it verified. And `[IMPL002 §7.4]`'s fix
was *half* built: `sync_peers.remote_listener`, `sync_remote_listener` and
`JobRunner.get_remote_listener()` all existed, with tests, and **nothing
read them** — so `jobs.py`'s `sync-preferences` job, the one the console's
own button runs, addressed a split peer's catalogue with queries only its
listener half can answer. Storage without consumption looks exactly like a
finished feature from the outside.

---

**Traceability:** `[SPEC-PREF-100..155]` · derives `[REQ-VIS-295]`,
`[REQ-VIS-315]` ·
crosses the Class-D boundary `[SPEC006]` §3 states and does not relitigate
it · reuses `run_remote_sql`/`literal` (`tools/remote_peek.py`), the
`[PI5-LIB-010]` stop/patch/restart recipe (`jobs.py::_remote_push`,
`tools/push_file_tags.py`), and the `/flags` console page/`remote_config`
(`[SPEC022]`)
