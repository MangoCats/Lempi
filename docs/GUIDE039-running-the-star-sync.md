# GUIDE039: Running the Star Sync

**Guide — Tier 1 · how to drive the star sync from the hub · moved here from HOWTO §7 on 2026-10-18**

How to run the star sync that brings the appliances' preferences, flags and catalogue together with the hub's. It was section 7 of [HOWTO](../HOWTO.md), which is about building and running Lempi and Vipunen on one machine; this is fleet work, and nothing in it is particular to this fleet's machines.

> **Related:** [FLEET001](../fleet/FLEET001-the-fleet.md) (which nodes exist and what each runs) · [SPEC046](spec/SPEC046-star-sync.md) (the design) · [HOWTO](../HOWTO.md) (running the hub's own Lempi and Vipunen)

---

If you have appliances (a Pi with a speaker) as well as this desktop, the
**star sync** brings their preferences, flags and catalogue together. This
machine is the **hub**: the source of truth, and the machine Vipunen runs on.
Each appliance is a **node**. The design is [SPEC046](spec/SPEC046-star-sync.md);
this is only how to drive it.

**Before a sync**

1. **Rebuild the local Lempi after every pull that touches `player/`** --
   `build/deploy-local.sh`, from the quick start at the top of
   [HOWTO](../HOWTO.md), which builds with `--features vipunen-support`
   (HOWTO §2) and restarts it. The console runs the binary at
   `player/target/release/lempi[.exe]`, and a stale one is the most common
   reason the hub is older than its nodes. `lempi --version` shows the build.
2. **Put a current player on each node** with `build/deploy-appliance.sh
   pi@HOST` (cross-compiles in Docker, installs it, restarts the player and
   checks the result). A node on an older build is warned about loudly,
   `OLD PLAYER`, and its catalogue is sent nothing `[SPEC-STAR-132]`.
3. **Write the plan, `fleet/star-plan.json`** (untracked): copy
   `fleet-example/star-plan.json` and fill in the hub (this machine's
   `data/listener.db` and `data/library.db`) and each node's `member`
   fingerprint. A node must first be **enrolled** in the mesh, by a person
   confirming a code on both sides `[SPEC-MTR-130]`. `LEMPI_STAR_PLAN`
   names another plan file.
4. Start the console on the hub's own databases, from Git Bash -- the
   quick start at the top of [HOWTO](../HOWTO.md) has the line.

**The hub's nightly work, set up once.** On a new or rebuilt hub, once the
plan above exists (its `backups` section names the folder and the mirror), run
`powershell -ExecutionPolicy Bypass -File tools\install_hub_tasks.ps1`. It
creates two Windows scheduled tasks: *Lempi hub backup*, at 03:30 -- the
backup of both databases, the re-read of every kept backup, and the
verification of every file in the library against its recorded hash -- and
*Lempi hub mirror*, every quarter hour, which copies the backups to the mirror
whenever it is on `[SPEC-STAR-086, 088]`. Without them nothing is backed up or
checked, and nothing says so. `python tools/star_sync.py fleet/star-plan.json
status` answers at any time whether all of it is current.

**In the console: `http://127.0.0.1:5730/sync`**

1. **Tick the nodes this run takes part with.** The hub always takes part and
   has no box. A node you leave unticked is not read, not started, not written
   and keeps its last-sent record; nothing is ticked until you do it, and a
   run needs at least one node `[SPEC-STAR-130, 134]`.
2. Press the stages in order: **Snapshot, Merge, Prepare patches, Rehearse,
   Commit.** Rehearse changes nothing anywhere. Commit stays disabled until
   every item has a verdict and every chosen node has rehearsed clean.
   With **Auto Advance** ticked (the default), pressing Snapshot runs on through
   Rehearse by itself, stopping wherever an item needs a verdict; give the
   verdicts, then press the next stage. Commit is always your own press
   `[SPEC-STAR-136]`.
3. **Items** are changes the merge would otherwise discard (two nodes changed
   the same thing differently, or one deleted what another edited). Each shows
   its default, the most recent change; pick another per item, or press
   **Approve all** to take every default. One-sided changes carry over without
   asking, and a node that already matches the hub has nothing to approve.
4. A big catalogue patch goes in parts and takes minutes on a Pi; the page
   shows progress. Close other users of the hub's databases first.

**The same from a terminal**, stage by stage, to read a run between steps:
`python tools/star_sync.py fleet/star-plan.json snapshot --nodes lempi02w`,
then `merge`, `items`, `patch`, `rehearse`, `commit` (`--help` lists them all).
The hub is included in each.

**A node's catalogue says `none sent`.** Read why in the run's `SUMMARY.md`
before anything else: a node sent no catalogue keeps every cover, credit and
boundary as it was, and nothing else says so -- on 2026-10-29 that was why
Queen's *Greatest Hits* showed Heart's sleeve on three nodes and not the
fourth. *No copy was last sent* is a node seeded by hand: adopt it once,
`python tools/star_sync.py fleet/star-plan.json adopt NODE --library
/path/on/node/library.db`, then snapshot again `[SPEC-STAR-946]`. *Changed on
the node* with a current player usually means a node's own rows have not been
asked for: the snapshot's line for it says what was asked `[SPEC-STAR-945]`.

From a terminal the hub is patched by the
command itself, so **close the console and the local Lempi first** (or let the
console's own job do it); the command says so and stops if one is open.
