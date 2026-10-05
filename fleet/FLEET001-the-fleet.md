# FLEET001: The Fleet

**Machine Record — the fleet as a whole · established 2026-09-26 · updated 2026-10-04 · update at milestones**

Every machine that holds a Lempi or Vipunen database, what it is for, and where its data lives. This file is **mirrored**: it lives in the repository, so the desktop, `teacherslounge` and `smartboardpc` each carry it in their checkout. Updating it means committing here and pulling there. Keep it current at milestones and when the fleet changes — a node added, moved, rebuilt or retired, a role changed — not with every commit. Each fact carries the date it was read, so a stale one is visible as stale.

> **Related:** [SPEC046](../docs/spec/SPEC046-star-sync.md) (how the databases are merged) · [appliance/](../appliance/README.md) (how each appliance is set up and checked) · [`data/README.md`](../data/README.md) (the hub's own state) · `fleet/targets.env`, private, shaped like [`fleet-example/targets.env`](../fleet-example/targets.env) (what the deploy scripts address) · each node's own folder below

---

## 1. The shape

**`[FLT-SHP-010]` A star, with the desktop at the centre** `[SPEC-STAR-010]`. The desktop holds the primary library pair, the hub. Every other database is a spoke. Edits go spoke → hub → spokes, and never spoke to spoke.

| node | ssh | role | hardware · OS | root | own folder |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GMKtec` (the desktop) | — | **hub**: Vipunen, the primary pair in `data/`, the build host for everything | Windows 11 | — | this repository |
| `teacherslounge` | `sw@teacherslounge` | validates the tools under Linux, and keeps its own plays like any node `[SPEC-STAR-103]`; the acoustic instrument `[LOG008]`; a speaker the Export page sends to since 2026-10-02 | Latitude 5590 · Ubuntu 22.04 | ext4 | [TeachersLounge/](../TeachersLounge/TL001-migration-and-the-silent-output.md) |
| `smartboardpc` ("Smart") | `mango@smartboardpc` | Linux build host; playback node; the fleet's chrony reference; a speaker the Export page sends to since 2026-10-02 | Quieter HD3 · Ubuntu 24.04 | ext4 | [SmartPC/](../SmartPC/SMART001-survey.md) |
| `lempi02w` | `pi@lempi02w` | appliance; Bluetooth speaker; where most listener edits are made; kept on bookworm and plain ext4 on purpose `[FLT-ISS-040]` | Pi Zero 2 W · bookworm | ext4 | [LempiPi/](../LempiPi/IMPL001-appliance-setup.md) |
| `bose` | `pi@bose` | appliance; HiFiBerry DAC+; echo leader | Pi 4 · trixie | **overlay** | [BosePi/](../BosePi/BOSE004-operating-health.md) |
| `lp3-wifi` (`lempiplay3`) | `pi@lp3-wifi` | appliance; framebuffer UI; echo follower; the jack, or a Bluetooth speaker since 2026-10-02 `[LP3-BT-010]` | Pi 3 B · trixie | **overlay** | [LempiPlay3/](../LempiPlay3/LP3001-the-node-and-its-units.md) |
| the Moto G | adb, see `android/README.md` | Android test phone — no database in the fleet yet | moto g power (2021) · Android 11 | — | [android/](../android/README.md) |

The overlay nodes need both layers written for anything on the root filesystem, and such a write must go through the scripts CLAUDE.md §4 names. A star sync writes none: its targets are the data partitions, `/var/lempi` (f2fs, read-write) and `/srv/library` (ext4; read-only on `bose`, remounted for the write and put back), read on all three appliances on 2026-10-04. `star_distribute.py` refuses a database that is on an overlay `[SPEC-STAR-080]`.

**`[FLT-SHP-020]` Each appliance is set up, and checked, by one script on one engine** `[APP-SET-010]`: `setup-lp3.sh`, `setup-bose.sh`, `setup-lempi02w.sh`, each with `--check`, which compares the node's durable layer with the repository and changes nothing. What all three have is one list, `appliance/common.sh` `[APP-SET-020]`. Since 2026-10-02; the first run's differences are in `[FLT-ISS-050]`. `build/setup-fleet.sh` runs all three in turn, `--check` or `--go`; on bose and lp3-wifi `--go` writes both layers `[APP-SET-030]`.

**`[FLT-SHP-030]` The source hosts too, since 2026-10-03.** `SmartPC/setup-smart.sh` moved onto the same engine, now `build/setup-lib.sh`, and `TeachersLounge/setup-tl.sh` is new: until then nothing recorded how teacherslounge was set up. What both have is `build/source-host-items.sh` — the packages Vipunen's tools need over ssh, the timezone, the checkout and the `import_bundle` it built on the PATH, and the fleet clock, smartboardpc as its server. `build/setup-fleet.sh` checks all five. Its first run found the clock kept on both under the project's earlier name, `vaino-fleet.*`, written by hand and recorded by no script: the same settings, now rendered from templates and `fleet/targets.env` under `lempi-fleet.*`, the server's subnet derived from its own address so none is written down. Applied by `--go` on 2026-10-03; on 2026-10-04 `build/setup-fleet.sh` reported all five as recorded, and every client was synced to smartboardpc. teacherslounge's record keeps an absence on purpose: no player service `[TL-OPN-020]`.

## 2. Where each database lives — read 2026-10-02

**`[FLT-DAT-010]` Listener and catalogue, per node.** Every node runs the split pair, and nothing else: the single-file mode was removed on 2026-10-02, and the player now refuses to start without both halves `[IMPL-DBSPLIT-025]`. The last single-file database anywhere — the predecessor's, on teacherslounge — was removed on 2026-10-04 after every play in it was matched in the hub's listener `[TL-OPN-030]`; smartboardpc's predecessor data holds only split halves, and its never-pruned recovery copy `[FLT-DAT-030]`. The paths are from each node's running unit; unchanged since 2026-09-26.

| node | listener (its own) | catalogue | backups |
| :--- | :--- | :--- | :--- |
| desktop | `data/listener.db` | `data/library.db` | — |
| `teacherslounge` | `~/lempi-data/listener.db` | `~/lempi-data/library.db` | `~/lempi-data/listener-backups/` |
| `smartboardpc` | `/var/lempi/listener.db` | `/var/lempi/library.db` | `/var/lempi/listener-backups/` |
| `lempi02w` | `/var/lempi/listener.db` | `/srv/library/library.db` | `/var/lempi/listener-backups/` |
| `bose` | `/var/lempi/listener.db` | `/srv/library/library.db` (read-only mount) | `/var/lempi/listener-backups/` |
| `lp3-wifi` | `/var/lempi/listener.db` | `/srv/library/library.db` | `/var/lempi/listener-backups/` |

The desktop's pair went missing when this repository was seeded, and was rebuilt by merging every node's `[SPEC046]`; `data/README.md` records both.

**`[FLT-DAT-015]` The speakers the Export page sends to** — the hub's `sync_peers`, read 2026-10-02. A send puts the audio in the speaker's own music folder, checked to exist and to have room first `[SPEC-STAR-094]`.

| speaker | catalogue | music folder | free, 2026-10-02 |
| :--- | :--- | :--- | ---: |
| `lempi02w`, `bose`, `lp3-wifi` | `/srv/library/library.db` | `/srv/library/audio` (the default) | — |
| `smartboardpc` | `/var/lempi/library.db` | `/media/mango/PortableSSD/Media/Music`, a USB drive | 11 GB of 1.9 TB |
| `teacherslounge` | `~/lempi-data/library.db` | `~/Music` | 78 GB |

The Moto G is not a speaker here: a send is pushed over ssh, and the phone has none. It imports the same bundles by hand, and a phone that asks for what it lacks is the shape it would take as an everyday player.

**`[FLT-DAT-020]` The 2026-09-26 merge reached every node, and each keeps what it had before.** Each node received its own copy by patch `[SPEC-STAR-080]`: the household's merged edits, its own plays and state untouched `[REQ-PD-113]`, and the merged catalogue with its own paths. On every node the backup was taken with the player stopped, both patches applied with no conflict, and every governed table read back from disk identical to the target. The evidence is in `data/recovery/2026-09-26/distribute/`.

| node | listener rows | catalogue rows | its pair from before the merge |
| :--- | ---: | ---: | :--- |
| `bose` | 103 | 258 | `/var/lempi/pre-star-2026-09-26/` |
| `lp3-wifi` | 114 | 396 | `/var/lempi/pre-star-2026-09-26/` |
| `lempi02w` | 73 | 6,447 | `/var/lempi/pre-star-2026-09-26/` |
| `teacherslounge` | 90 (the hub's listener, as it was sent as a mirror until 2026-10-04) | 14,071 | `~/lempi-data/pre-star-2026-09-26/` |
| `smartboardpc` | 3,519 | 13,786 | `pre-star-2026-09-26/` in the previous build's data directory |

Beside each backup, `pre-star-2026-09-26-tools/` holds the tool and the two patches that were applied, so what changed can be read on the node itself. Rolling a node back is `star_patch.py restore`, with the player stopped.

**`[FLT-DAT-030]` From 2026-09-26, the hub is backed up every night, and syncs are one tool** `[SPEC-STAR-085..087]`.
- `data/backups/` on the desktop holds 14 days and 12 month-starts, mirrored to `teacherslounge:~/lempi-hub-backups/`. It is run by the Windows task "Lempi hub backup", which puts a message on screen if it fails.
- A sync is `tools/star_sync.py fleet/star-plan.json`, stage by stage, started by a person.
- Each node keeps the backups of its three most recent syncs as `pre-sync-<run>/`, beside its listener and its catalogue. The recovery's `pre-star-2026-09-26/` is never pruned.
- The first routine sync, `20260926T1952Z`, carried the file hashes and that afternoon's edits to every node.

## 3. What each runs — read 2026-10-02

**`[FLT-RUN-010]`** The appliances' build is from `lempi --version`, re-read on 2026-10-04: `e1e27b9f81ea` on `lempi02w` and `bose`, and `1a72c72f6e53` on `lp3-wifi`, deployed 2026-10-05, `import_bundle` present; `lempi02w` was redeployed the same day and runs `da52627b12be` (live and durable, `import_bundle` the same). The source hosts' is their checkout, as read on 2026-10-02.

| node | player | notes |
| :--- | :--- | :--- |
| `lempi02w`, `bose`, `lp3-wifi` | `e1e27b9`, and `import_bundle` the same | lempi02w is plain ext4; the other two hold durable copies, and lp3-wifi's fbui is the same build |
| `teacherslounge` | `ca8313f`, built there 2026-10-02 by `build/update-source-host.sh`, which now also links `import_bundle` onto the PATH; no player service, **on purpose**: started by hand only `[TL-OPN-020]` | mirrors this file |
| `smartboardpc` | `ca8313f`, built there the same way, its `systemd --user` player restarted onto it `[SMT-SVC-010]` | migrated 2026-09-26 `[FLT-ISS-020]` |

**`[FLT-RUN-020]` How each plays, and what MPD is where.** The appliances' audio paths differ on purpose only where the difference buys something.

| node | output | MPD |
| :--- | :--- | :--- |
| `lempi02w` | PipeWire → a Bluetooth speaker | guest backend, idle until switched to |
| `bose` | ALSA → the HiFiBerry DAC, directly — the one difference kept for what it gives | guest backend |
| `lp3-wifi` | PipeWire → the jack, or the paired Oontz | none, by decision |
| `smartboardpc` | PulseAudio's default device | not installed |
| `teacherslounge` | by hand | not installed |

MPD stays on lempi02w and bose for now, by the maintainer's decision of 2026-10-02; if the difference from lp3-wifi causes friction, the answer is to add it there rather than remove it.

## 4. Standing issues

**`[FLT-ISS-010]` `bose`'s Wi-Fi died silently on 2026-09-26 at 11:33 EDT**, during a 21 MB snapshot copy, and stayed dead for two hours until a power cycle. Its journal is persistent (`/var/lempi/log/journal`), and the previous boot showed **the player playing normally throughout**, with not one Wi-Fi, NetworkManager or kernel message: the link failed without saying so. That is the fault `[BOS-OPS-110]` recorded, when reassociating fixed it. A sustained transfer may be what trips it, as it trips the Moto G's wireless debugging.

**`[FLT-ISS-015]` A Wi-Fi watchdog is a possible future feature, and how often the link dies decides it.** It would reassociate, or at worst reboot, when the gateway stops answering, and so recover a node without a person. It is not built: on 2026-09-26 there is one occurrence and no frequency to decide on. **At each new occurrence, add a row here and decide again** whether it is time to build it. Record the node, when, what it was doing, what brought it back and how long it was down, since those make the frequency and show whether load is the trigger.

| # | when | node | doing | recovered by | down |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | 2026-09-26 11:33 EDT | `bose` | a 21 MB snapshot copy over ssh | power cycle | about 2 h |

Related but distinct, and not counted: on 2026-09-25 `bose` lost only *unmarked* traffic to `smartboardpc`, and one reassociation cleared it `[BOS-OPS-110]`. The link was up; the access point held stuck state.

**`[FLT-ISS-020]` `smartboardpc` migrated to this repository's player, 2026-09-26**, as `teacherslounge` was in TL001 `[TL-MIG-020]`:
- The previous player was stopped. Its listener and catalogue were copied through the backup API to `/var/lempi/`, where SMART001 had placed them `[SMT-DB-030]`, and every table was checked identical to its source.
- Lempi `6e38ab4` was started on that copy, with `XDG_RUNTIME_DIR` set so PulseAudio can be found `[TL-OPS-020]`.
- It plays through PulseAudio's default device, as the previous player did, and follows `bose`'s echo from the setting the data carries. Its stored position advanced 0 → 28,709 ms in 30 s, with no stream errors.
- The previous build and its data directory are untouched and still runnable.

The same evening it gained a unit, set up by `SmartPC/setup-smart.sh` `[SMT-SVC-010]`. A reboot on 2026-09-26 brought it back playing, and following `bose`'s echo, with no one touching it. That machine logs its desktop in automatically, so the reboot did not test the case with no login at all, which the unit is also built for.

**`[FLT-ISS-040]` `lempi02w` stays at risk, as a trial.** It is the node a power cut stops — its speaker supplies it — and the only appliance with a plain writable root and bookworm. The maintainer's decision, 2026-10-02: leave it so. If its card fails, that is a data point, and the repair is a rebuild with the catalogue and music from the other nodes. **At that rebuild, choose its data paths for whoever will run it**, rather than copying today's by default.

~~**`[FLT-ISS-050]` What `--check` found on 2026-10-02, not yet applied.**~~ *Resolved the same evening.* lempi02w by `setup-lempi02w.sh --go`; bose and lp3-wifi by `build/setup-fleet.sh --go`, both layers, then rebooted — and a `--check` of all three on fresh boots reported every item as recorded, both players back and playing `[APP-SET-030]`. Among what it fixed: cloud-init never disabled on bose; lp3-wifi's missing `lempi-btctl` rule and quiet-tick drop-ins; lempi02w's two-line unit and retired files. Still open:
- `bose`'s unit omits `--port`, which defaults to the same 5720 — fixed at its next touch, by the maintainer's choice

~~**`[FLT-ISS-030]` `lp3-wifi` keeps UK time.**~~ *Resolved 2026-09-26.* Its listener recorded a UTC offset of +60 minutes where every other node had −240, so anything that follows the clock — a programme that starts at a set time `[SPEC-DIR-180]` — ran five hours off there. The image had come up in `Europe/London`, and no script said otherwise. `LempiPlay3/setup-lp3.sh` now carries the zone, `America/New_York`. It was written to both layers, and the player recorded −240 at its restart. `overlayroot-chroot` could not remount the durable layer read-only on exit ("mount point is busy"). A second `remount,ro` a moment later succeeded; check `findmnt -no OPTIONS /media/root-ro` after using it.

**`[FLT-ISS-060]` Every player's catalogue has moved from what the hub last sent it, by the same 49 files and 338 passages, 2026-10-05.** They are the hub's own music, put there by the Export page's bundle send under the node's own ids (node file 5710 is the hub's 5719) `[SPEC-STAR-090]`, and the star sync's strict catalogue patch cannot apply over them. By natural key (`audio_md5`, and a passage's span) each equals the hub exactly: the difference is ids alone. The listener half syncs; the catalogue half can be reached by a patch by natural key, sent in parts `[SPEC-NKP-010]`, `[SPEC-NKP-920]`, which is built, and **committed to `lp3-wifi` on 2026-10-05**, the first live star sync: 24 of 25 catalogue tables identical to the hub's by natural key and a second round with nothing to send `[SPEC-NKP-900]`; and **to `lempi02w` the same day** (the same 24 of 25, `file_tags` differing by its own 49 derived rows, then nothing to send in two further rounds), after which the hub took `lempi02w`'s 58 preference and 5 flag edits. `bose`, `smartboardpc` and `teacherslounge` are untouched, as the first test required, and `lp3-wifi` has not yet been sent the preference edits that reached the hub from `lempi02w`. **At each further bundle send, expect the same.**

---

**Traceability:** `[FLT-SHP-010..020]`, `[FLT-DAT-010..030]`, `[FLT-RUN-010..020]`, `[FLT-ISS-010..060]` · read from the machines 2026-09-26 and 2026-10-02
