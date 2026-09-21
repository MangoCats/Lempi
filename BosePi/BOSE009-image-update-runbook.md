# BOSE009: Executing the bose Update — Order, Rollback, and What Breaks

**Implementation Guide — the runbook, written by trying to break the plan**

[BOSE008](BOSE008-image-update-plan.md) says what to change and why. This
says how, in what order, and what happens when a step fails — because the
plan as first written **would not have produced a working system**, and the
reasons are worth keeping rather than quietly fixing.

Asked directly: *will we end with a working system?* Yes, if executed in the
order below. Not if executed as `BOSE008` originally read.

> **Executed 2026-09-11, and it does.** `bose` runs `5272e3f`, split, both
> halves WAL and integrity-clean, playing. §2's three corrections were all
> real. Executing it found two more the review had missed — `[BOS-RUN-080]`
> and `[BOS-RUN-085]` — which is the argument for doing this with the
> service stopped and one step at a time rather than as a script.

> **Related:** [BOSE008](BOSE008-image-update-plan.md) for the decisions this
> executes · [PI025 `[PI-OWE-040]`](../LempiPi/PI025-what-the-local-split-owes-lempipi.md)
> for the ordering trap this inherits · [BOSE005](BOSE005-power-loss-test.md)
> for the power cut that can interrupt any step

---

## 1. What was verified safe

**`[BOS-RUN-010]` One binary genuinely serves both appliances.** The largest
unexamined risk, since they are now on different Debian generations. The
cross-build image is `rust:1.90-bookworm` (glibc 2.36); the deployed binary
imports nothing above `GLIBC_2.34`; `lempipi` has 2.36 and `bose` has 2.41.
Building against the **older** distribution is what makes that work, and it
is load-bearing rather than incidental: bumping `build/Dockerfile.aarch64`
to trixie would produce a binary that runs on `bose` and refuses to start on
`lempipi`, with no warning at build time.

**`[BOS-RUN-015]` `sqlite3` installs cleanly.** `apt-get install -s sqlite3`
on `bose`: one package, `3.46.1-7+deb13u1`, no dependencies pulled, nothing
upgraded, nothing removed. The player is unaffected either way — it links
SQLite **bundled** `[REQ-HW-145]`, so the system library is not its concern.

**`[BOS-RUN-020]` Writes persist, and the deploy keeps a rollback.** `bose`'s
root overlay is disk-backed, verified by a binary that predates the last
boot and survived it. `deploy-player.sh` checksums before and after upload
and leaves the previous binary at `/usr/local/bin/lempi.prev`.

**`[BOS-RUN-025]` `split_database.py` cannot damage the source.** It refuses
to overwrite an existing output, rehearses by default, verifies row counts
table-for-table plus `integrity_check` plus every index before reporting
success, and never modifies the file it reads.

## 2. What would have broken

**`[BOS-RUN-030]` `BOSE008 §5`'s commands cannot run as written.** They read
as though issued on `bose`. There is **no repository checkout on `bose`**,
and `tools/load_occasions.py` now imports `lempi_db`, so copying that one
file over fails on import. `tools/backfill_profanity.py` additionally reads
`mulib.db`, which is 95 MB and lives only on the desktop.

Corrected mechanism, which is the one `lempipi` was actually given:

```
# ship both files, not one
scp tools/load_occasions.py tools/lempi_db.py pi@bose:/tmp/
ssh pi@bose "python3 /tmp/load_occasions.py /var/lempi/listener.db --write"

# profanity travels as a patch, so mulib.db never does
python tools/backfill_profanity.py data/library.db \
    --mulib ../MuLibPlay/mulib.db --sql-out /tmp/profanity.sql
scp /tmp/profanity.sql pi@bose:/tmp/
ssh pi@bose "sqlite3 /var/lempi/listener.db < /tmp/profanity.sql"
```

The patch is self-guarding — every statement carries its own
`WHERE EXISTS` against `recordings` — so it is checked by `bose`'s catalogue
rather than by the desktop's `[SPEC-PREF-082]`.

**`[BOS-RUN-035]` Splitting before deploying would create shadow tables.**
`bose` runs `358c5b17`, which predates `1a7e100` — the commit that stopped
`PlayerStore::open(&db)` from telling the connection that the listener half
is the whole database. On the old build, a split `bose` would create empty
`file_tags` and `cover_art` in its listener half on **every start**, exactly
as `lempipi` did `[PI-OWE-040]`.

This is the same trap, on the same evidence, one appliance later. **Deploy
first.**

**`[BOS-RUN-040]` Every database write needs the service stopped.** The
player holds `lempi.db` open and writes to it continuously. `BOSE008` did
not say so. Each write step below stops `lempi` and starts it again.

**`[BOS-RUN-045]` `lempi-wifi-revert` must not be ported.** `BOSE008 §6`
listed it as hardware-neutral, which is true and beside the point: it is
scheduled **only** by `lempi-btctl`'s `wifi-connect`/`ap-start`/`ap-stop`
verbs via `systemd-run`, and `lempi-btctl` is not being ported. Installed
alone it is a script nothing ever calls — harmless, but it would leave
`bose` looking as though it had Wi-Fi revert protection when it has none,
which is worse than plainly not having it. Porting the scheduler instead
means bringing `[SPEC034]`'s whole Wi-Fi/AP feature to `bose`, which is a
separate decision this plan should not smuggle in.

## 3. The order

**`[BOS-RUN-050]`** Each step ends with a working system, so stopping after
any of them is safe.

| # | Step | Rollback |
| :--- | :--- | :--- |
| 0 | Back up `lempi.db` off-device (`VACUUM INTO`, then `scp`) | — |
| 1 | `apt-get install -y sqlite3` | `apt-get remove sqlite3`; nothing depends on it |
| 2 | `LempiPi/deploy.sh <tag> pi@bose` | `sudo cp /usr/local/bin/lempi.prev /usr/local/bin/lempi && systemctl restart lempi` |
| 3 | Stop `lempi`; ship and run `load_occasions.py`; apply `profanity.sql`; start `lempi` | restore the step-0 backup |
| 4 | Install the three neutral helpers (`vitals`, `underruns`, `startup-sample`) | delete them; nothing references them |
| 5 | **Split**, as one transaction — see §4 | point the unit back at `lempi.db`, which is untouched; `lempi.service.pre-split` is kept beside the unit |

**`[BOS-RUN-055]`** Steps 1–4 are independent and individually reversible.
Step 5 is the only one that changes how the appliance is *shaped*, and it is
the only one with a sequencing requirement inside it.

## 4. Step 5, the split, in full

**`[BOS-RUN-060]`** Do these five together or none of them. Between the
second and the fifth, `bose` is a machine whose unit does not match its
databases.

1. `systemctl stop lempi`
2. Split **inside an attended window**, because the halves do not land on the
   same partition — `[BOS-RUN-080]`:

   ```
   bash BosePi/attended-import.sh --check -- ssh pi@bose \
       "python3 /tmp/split_database.py /var/lempi/listener.db \
        --library-out /srv/library/library.db \
        --listener-out /var/lempi/listener.db --commit"
   ```

   then the same with `--go --no-mpd-update`. `split_database.py` is stdlib
   Python and runs wherever python3 is. **Keep `lempi.db`**; it is the
   rollback.
3. `install -m755 lempi-db-recover /usr/local/bin/` — mandatory, not
   optional: without it the first power cut after the split is a crash loop
   `[PI3-FOUND-120]`, and this machine is cut from a speaker's switch.
4. Add `ExecStartPre=/usr/local/bin/lempi-db-recover` and change `ExecStart`
   to the two-path form; `systemctl daemon-reload`.
5. `systemctl start lempi`, then **hear it play** before calling it done,
   which is `[IMPL-BOS-120]`'s own rule and not a new one.

**`[BOS-RUN-070]` Step 2 above never exercises the split tool's own
rehearsal, and did not on `bose`.** `--check` belongs to
`attended-import.sh` and dry-runs the *mount window*; `--commit` is present in
both passes, and `--commit` writes straight to the real destinations without
ever touching a temp directory. So `split_database.py`'s rehearsal mode went
unrun here. Rehearsing it on `lempiplay3` afterwards found two real faults
first time out -- see `[IMPL-VP3-120]` and `[IMPL-VP3-140]` in
[IMPL016](../docs/IMPL016-converting-lempiplay3.md). Run the rehearsal
without `--commit` before the real pass on any future node.

**`[BOS-RUN-065]` A power cut mid-split is survivable at every point**,
which is worth stating on a machine that is switched off by having its power
removed. Before step 4 the unit still names `lempi.db`, which
`split_database.py` never modified, so the appliance boots exactly as it did
before. After step 4 it names the two new files, which exist and verified
clean. The window where neither is true does not exist.

## 5. What executing it found that reviewing it did not

**`[BOS-RUN-080]` The two halves do not go on the same partition, and the
runbook's own §4 had them doing so.** `[IMPL-BOS-078]` settled this before
either document existed: `lempi.db` sits on C *"until the split is built"*,
and the catalogue belongs on B. That is not a preference — C is a 4 GB f2fs
partition with 2.6 GB free, and `library.db` is 1.17 GB, so putting both
halves there alongside the retained original would have left roughly 200 MB
on the partition that takes every write this appliance makes. B has 56 GB.

B is also genuinely `ro`, which C is not, so writing it needs
`BosePi/attended-import.sh` — the remount-run-restore window
`[IMPL-BOS-150]` already built. Used with `--check` first, as that script
insists.

This also quietly corrects `BOSE008 [BOS-IMG-050]`'s claim that `bose` "has
one writable filesystem". It has two partitions with different postures, and
that is closer to `lempipi`'s intent than `lempipi` itself manages — B here
really is read-only, where `lempipi`'s equivalent just stays `rw` forever.

**`[BOS-RUN-085]` Two of the three helpers are inert without their units.**
`lempi-vitals` and `lempi-startup-sample` each have a
`/etc/systemd/system/*.service` on `lempipi`; installed as bare scripts they
are exactly the half-a-mechanism mistake `[BOS-RUN-045]` had just caught
with `lempi-wifi-revert`, and the review made it again one paragraph later.
Both units are now on `bose` with `lempipi`'s own enablement: `startup-sample`
**enabled** (bounded — it samples the first minutes after a boot),
`lempi-vitals` **disabled** (an infinite sampler, started by hand when
something is being investigated). `lempi-underruns` genuinely is standalone
and needs no unit.

**`[BOS-RUN-090]` A WAL catalogue on read-only media: what actually
happens, measured on `bose`.** `library.db` is WAL, inherited from the
source by `split_database.py`, and now lives on a partition that is `ro` in
normal operation. The first account of this in these documents said a dirty
`-wal` there could not be replayed by any read-only open. That is wrong, and
the truth is more useful:

| left on `ro` media | `mode=ro` — what the player uses | `immutable=1` |
| :--- | :--- | :--- |
| clean (no sidecars) | correct | correct |
| dirty `-wal`, `-shm` present | **correct** — frames are read out of the `-wal` | **1 row of 2, silently stale** |
| dirty `-wal`, `-shm` absent | `unable to open database file` | **silently stale** |

Two conclusions, and they point opposite ways.

**The player is never silently wrong.** `mode=ro` either reads the frames
correctly or fails loudly. That is the good half, and it is not luck —
`[IMPL-DBSPLIT-025]`'s choice to attach the catalogue `mode=ro` is what buys
it.

**But a loud failure here is not self-healing, and on this machine that is
the sharp edge.** If the `-shm` is ever missing while the `-wal` is dirty,
the player cannot open the catalogue, `Restart=always` turns that into the
crash loop `[PI3-FOUND-120]` describes — and `lempi-db-recover` **cannot fix
it on `bose`**, because its one read-write open lands on a partition that is
read-only. Recovery needs an attended window. That is a real difference from
`lempipi`, where the same file sits on a writable filesystem and the script
heals it at the next boot.

`attended-import.sh` `sync`s, which is not a checkpoint. Any future window
that writes `library.db` should end with `PRAGMA wal_checkpoint(TRUNCATE)`,
so B never closes over frames. Today `bose`'s `library.db-wal` is 0 bytes and
nothing writes that file, so the state is sound — but `[BOS-RUN-078]` stops
being optional the first time an import touches the catalogue.

**`[BOS-RUN-095]` Nothing in this project reads a catalogue with
`immutable=1`, and nothing should start.** Checked: every use of that flag
is against a genuinely frozen external file — `mulib.db`, AcousticBrainz
dump shards — which is what it is for. Against a live catalogue it is the
one configuration that answers wrongly without saying so, in every dirty
case above. `remote_peek.py`'s python fallback carries a comment saying so,
because its own wording had used "immutable" loosely to mean "does not
write".

## 6. Open

**`[BOS-RUN-070]` Executed 2026-09-11.** The ordering was the whole of the
answer: `[BOS-RUN-035]` alone would have produced a `bose` quietly
manufacturing shadow tables on every boot, and the post-split check found
none.

**`[BOS-RUN-078]` `attended-import.sh` should checkpoint before closing B**
`[BOS-RUN-090]`. Not built: nothing writes `library.db` today, and the one
window that did leave it clean. It becomes real the first time an import
touches the catalogue rather than only the audio.

**`[BOS-RUN-075]`** `[BOS-RUN-010]`'s glibc floor is undefended: nothing
fails at build time if `build/Dockerfile.aarch64` is bumped past bookworm,
and the failure appears only when `lempipi` refuses to start. A check that
the built binary imports no symbol above the oldest deployed appliance's
glibc would close it.

---

**Traceability:** `[BOS-RUN-010..095]` · executes
[BOSE008](BOSE008-image-update-plan.md) · inherits `[PI-OWE-040]`'s
deploy-before-drop ordering as deploy-before-split · corrects `BOSE008 §5`'s
commands and `§6`'s `lempi-wifi-revert` recommendation
