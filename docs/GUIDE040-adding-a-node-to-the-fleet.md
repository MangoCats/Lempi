# GUIDE040: Adding a Node to the Fleet

**Guide — Tier 1 · every step from a machine on the network to a node the star sync reaches · written 2026-10-29, after `lrhtpc`**

How to make a machine a node of the fleet: a player with its own pair and its own music, recorded in the repository, enrolled in the mesh and reached by the star sync. Each step points at the document that owns it rather than repeating it. What this guide adds is the order, and the checks between the steps.

It exists because no such list did. On 2026-10-28 and 29 `lrhtpc` was added by reading six documents, and it still met five faults that none of them mentioned. Each is now a step below, marked **lesson**. The worked example throughout is [LRH001](../LRHTPC/LRH001-the-htpc.md).

**The finish line is one command** `[GDE-ADD-140]`: `python tools/check_node_ready.py fleet/star-plan.json NODE` reads the plan, the roster, the sync's state, the speaker registry and the node itself, and names the remedy for anything missing. A node is added when it says `ready` and FLEET001 records it (§8).

> **Related:** [FLEET001](../fleet/FLEET001-the-fleet.md) (the fleet, and the record every step ends in) · [appliance/](../appliance/README.md) (the setup engine) · [GUIDE039](GUIDE039-running-the-star-sync.md) (the sync, once the node is in it) · [HOWTO](../HOWTO.md) §3 (what a library is) · [CLAUDE.md](../CLAUDE.md) §4–§5 (overlay roots, and saying what you assume)

---

## 1. Read the machine before deciding anything

**`[GDE-ADD-010]` Survey first, and write down what you read, with the date.** These answers decide every later step, and each one has been wrong in this repository when it was assumed rather than read:

| read | how | what it decides |
| :--- | :--- | :--- |
| the root filesystem | `findmnt -no FSTYPE /` | `overlay` means every write goes to both layers — CLAUDE.md §4, `[IMPL-BOS-185]` |
| architecture and OS release | `uname -m`, `/etc/os-release` | which build image: the binary needs the node's own glibc or older (§3) |
| password-less sudo | `sudo -n true` | whether setup may touch the system at all, or must live in a home directory; and whether a deploy needs sudo granted for it (§3) |
| the audio stack | `systemctl --user is-active pipewire pulseaudio`, `aplay -l` | a system unit or a user unit `[TL-OPS-020]`, and who else shares the device |
| free space | `df -h` | whether the whole music folder fits |
| the link | `/sys/class/net/*/speed` | how long the music takes, **at both ends** (§4) |

**`[GDE-ADD-020]` Say what you assumed before acting on it** `[GDE-DEP-060]`. The scripts below print the root type before they write. A step done by hand should do the same in its own notes.

## 2. Give it a folder and a script

**`[GDE-ADD-030]` One folder per machine, holding its record, its units and its setup script** `[GDE-DEP-040]`. The record is a `XXX001` document: what was read in §1, and why each choice was made. An appliance's setup script is built on `build/setup-lib.sh` `[APP-SET-010]`, and so is any other machine's `[FLT-SHP-030]`. Each takes `--check` and `--go`, and **anything set up by hand belongs in it**. A node whose items need no root sets `SETUP_NO_ROOT=1`, so `--go` does not end in a `sudo` it cannot answer.

**Add the folder to `PATH_PREFIXES` in `tools/check_docs.py`.** Otherwise a path cited into it inside backticks is never checked.

**`[GDE-ADD-040]` A machine that is someone else's first gets a player that keeps out of the way.** `lrhtpc` is a Kodi box. Its unit starts the player `--paused`, and the setup script checks that Kodi plays through PulseAudio, because a Kodi that opened the device itself would lock the two out of each other `[LRH-AUD-010]`.

## 3. Build for that machine, from a commit

**`[GDE-ADD-050]` The build matches the node, and the node never builds what it cannot.**
- An appliance: `build/deploy-appliance.sh`.
- A source host with a toolchain: `build/update-source-host.sh`.
- Anything else: a build in the image of the node's own OS release, as `LRHTPC/deploy-lrhtpc.sh` does in the bookworm image. Building on a newer machine makes a binary that asks for a newer glibc than the node has.

Commit first, in every case: the build stamp must name a commit that exists (`build/README.md`). Verify the installed copy and what is running — `--version`, then `/build` — never the exit code `[GDE-DEP-070]`.

**`[GDE-ADD-055]` Lesson: install the binaries where a non-interactive ssh finds them — `/usr/local/bin`.** A send from the Export page looks for `import_bundle` with `command -v` over ssh, and that shell reads no profile. `lrhtpc`'s first install, in `~/lempi/bin` to avoid root, could be listed on the Export page and never sent to `[LRH-DEP-020]`. Where sudo asks for a password, grant it for the deploy — a sudoers drop-in removed by a `systemd-run --on-active` timer — rather than installing into a home directory. Check: `ssh NODE 'command -v import_bundle'` names `/usr/local/bin`.

## 4. Seed its library

**`[GDE-ADD-060]` The music, the catalogue, then relink.**
1. **Music**, the whole folder, by `rsync --chmod=D755,F644`, or a `tar` stream over ssh where neither end has rsync. Then compare file count, folder count and total bytes on both sides. Read the link speed at **both** ends first: `lrhtpc`'s copy took 90 minutes because the desktop's port had dropped to 100 Mb/s `[LRH-SEED-020]`.
2. **Catalogue**: the hub's `data/library.db`, copied through SQLite's backup API, so a write in progress is not torn, and sent beside the live name.
3. **relink**: run `relink --library <copy> --audio-root <music>` first without `--apply` (it only reports), then with it. Missing or corrupt above zero means stop. Then read the database itself: every path under the music root, existing and distinct, and `integrity_check` ok `[SPEC-RLK-152]`.
4. Rename it into place. The player makes the listener on its first start.

**`[GDE-ADD-070]` Lesson: the listener must be a whole half before the first sync** `[LRH-SEED-030]`. Before 2026-10-29 a listener the player made for itself had only the tables it writes: 13 of the 22. The first star sync to reach `lrhtpc` stopped with `no such table: boundary_reviews`. A current player completes its half on every open, from `sql/schema.sql`. On any node, check it before the first sync: the listener's tables must include every name in `LISTENER_TABLES` (`player/core/src/db/pair.rs`).

## 5. Start it, and check what it does

**`[GDE-ADD-080]` Running is not playing.** After the first start, read three things:
- its log, for the catalogue it loaded and the output it opened;
- `/build`, which must name the commit you built;
- the sink, from `GET /audio/sink`. Where the node cannot observe it (no `wpctl`), read the stream from the sound server itself: `pactl list sink-inputs`. `build/verify-playing.sh` asks for a number that has to move.

Record what you could not check. `lrhtpc`'s first start was *routed* to the television, not heard there.

## 6. Enrol it in the mesh

**`[GDE-ADD-090]` Lesson: open the pairing window, then invite** `[SPEC-NSH-170]`. A player in no mesh accepts an invitation only while its window is open: five minutes, one pairing. Only an act on the machine opens it:
- a boot;
- its pair button, or `lempi-btctl mesh-pair`;
- on a machine with neither, the signal those send: `pkill -USR1 -x lempi`, as the player's own user.

An invite sent before that is refused, and **the console's mesh page does not say so**: the refusal is only in `data/mesh/invites/<address>.log`. Read that log whenever the node shows no invitation.

**`[GDE-ADD-100]` Then compare the code on both sides** `[SPEC-MTR-130]`. The node's own page, under Settings → Mesh, shows "Invited to join … Code NNN NNN" and **The codes match**. The console's **Asking to join** shows the same code to accept. Afterwards read both sides: the node's `GET /mesh` names the mesh and a roster that includes it, and the console lists it under Members.

## 7. Put it in the plan, and sync it carefully once

**`[GDE-ADD-110]` Its fingerprint becomes its `member` in `fleet/star-plan.json`**, from the console's Members list. A node in the plan without one is reported `MISSING ... not enrolled`, never skipped silently. `fleet/targets.env` names only the appliance rollout and the source hosts: a node that is neither is not added there.

**`[GDE-ADD-115]` Lesson: adopt the seeded catalogue before the first sync** `[SPEC-STAR-946]`. A seeded catalogue was never *sent* by a sync, so the hub has no copy to patch it against, and without one it is sent no catalogue, ever: `lrhtpc` read *no copy was last sent* at every run, and kept every cover the hub later corrected. Once it is in the plan:

```
python tools/star_sync.py fleet/star-plan.json adopt NODE --library /path/on/node/library.db
```

It copies the node's own catalogue on the node through the backup API, checks it, fetches it by hash, and records it as the copy last sent. Close the console first if it is older than the command: an older console takes the new `adopted/` folder for the newest run.

**`[GDE-ADD-120]` Lesson: read the first sync's snapshot stage before letting it patch.** Run the stages one at a time ([GUIDE039](GUIDE039-running-the-star-sync.md)), and read what the snapshot says about the new node's catalogue and its listener before the patch stage is run. In the run's `SUMMARY.md`, a `catalogue: none sent` line for the new node means stop: whatever it names, the node will keep its catalogue as it is.

**`[GDE-ADD-150]` A speaker the Export page sends to is a row in the console's registry** `[FLT-DAT-015]`, made on **Flags → speakers**: its catalogue and listener as `user@host:/path`, and its **music folder** as the audio root — left empty, a send puts the audio in `audio/` beside the catalogue, the Pis' layout `[SPEC-STAR-094]`. A node whose catalogue comes by the star sync is unticked for the older remote push. Then **Send what's missing** to it and press **Send** once: that first press is a dry run, and says what it found on the node — the catalogue, read-write or not, the free space — without sending anything.

## 8. Record it

**`[GDE-ADD-130]` The node is added when FLEET001 says so, and not before.** Give it a row in each table there:
- §1, the shape;
- §2, where its pair lives;
- §3, what it runs — written only once the deploy is verified `[FLT-RUN-010]`;
- §3, how it plays.

Commit the folder, FLEET001 and `tools/check_docs.py` together. Stage paths by name, never `git add -A` (CLAUDE.md §9). The fleet clock is optional, and its own decision `[GDE-ECHO-300]`.

**`[GDE-ADD-140]` Then the check, and it must read `ready`:** `python tools/check_node_ready.py fleet/star-plan.json NODE`. It changes nothing. Each line is something a sync or a send needs — enrolment, a copy last sent, `import_bundle` on the ssh PATH, the registry's paths and music folder present on the node, a whole listener half — and each missing one names its remedy. Every check in it was a fault `lrhtpc` met by failing.

---

**Traceability:** `[GDE-ADD-010..150]` · from adding `lrhtpc`, 2026-10-28 and 29 · [LRH001](../LRHTPC/LRH001-the-htpc.md)
