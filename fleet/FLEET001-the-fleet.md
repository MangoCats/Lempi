# FLEET001: The Fleet

**Machine Record — the fleet as a whole · established 2026-09-26 · update at milestones**

Every machine that holds a Lempi or Vipunen database, what it is for, and where its data lives. This file is **mirrored**: it lives in the repository, so the desktop, `teacherslounge` and `smartboardpc` each carry it in their checkout. Updating it means committing here and pulling there. Keep it current at milestones and when the fleet changes — a node added, moved, rebuilt or retired, a role changed — not with every commit. Each fact carries the date it was read, so a stale one is visible as stale.

> **Related:** [SPEC046](../docs/spec/SPEC046-star-sync.md) (how the databases are merged) · [`data/README.md`](../data/README.md) (the hub's own state) · [`fleet/targets.env`](targets.env) (what the deploy scripts address) · each node's own folder below

---

## 1. The shape

**`[FLT-SHP-010]` A star, with the desktop at the centre** `[SPEC-STAR-010]`. The desktop holds the primary library pair, the hub. Every other database is a spoke. Edits go spoke → hub → spokes, and never spoke to spoke.

| node | ssh | role | hardware · OS | root | own folder |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GMKtec` (the desktop) | — | **hub**: Vipunen, the primary pair in `data/`, the build host for everything | Windows 11 | — | this repository |
| `teacherslounge` | `sw@teacherslounge` | **mirror** of the hub, validating the tools under Linux; the acoustic instrument `[LOG008]` | Latitude 5590 · Ubuntu 22.04 | ext4 | [TeachersLounge/](../TeachersLounge/TL001-migration-and-the-silent-output.md) |
| `smartboardpc` ("Smart") | `mango@smartboardpc` | Linux build host; playback node; the fleet's chrony reference | Quieter HD3 · Ubuntu 24.04 | ext4 | [SmartPC/](../SmartPC/SMART001-survey.md) |
| `lempi02w` | `pi@lempi02w` | appliance; Bluetooth speaker; where most listener edits are made | Pi Zero 2 W · bookworm | ext4 | [LempiPi/](../LempiPi/IMPL001-appliance-setup.md) |
| `bose` | `pi@bose` | appliance; HiFiBerry DAC+; echo leader | Pi 4 · trixie | **overlay** | [BosePi/](../BosePi/BOSE004-operating-health.md) |
| `lp3-wifi` (`lempiplay3`) | `pi@lp3-wifi` | appliance; framebuffer UI; echo follower | Pi 3 B · trixie | **overlay** | [LempiPlay3/](../LempiPlay3/LP3001-the-node-and-its-units.md) |
| the Moto G | adb, see `android/README.md` | Android test phone — no database in the fleet yet | moto g power (2021) · Android 11 | — | [android/](../android/README.md) |

The overlay nodes need both layers written, and any write must go through the scripts CLAUDE.md §4 names.

## 2. Where each database lives — read 2026-09-26

**`[FLT-DAT-010]` Listener and catalogue, per node.** The appliances run `lempi --listener … --library …`. The paths are from each node's running unit.

| node | listener (its own) | catalogue | backups |
| :--- | :--- | :--- | :--- |
| desktop | `data/listener.db` — **missing** | `data/library.db` — **missing** | — |
| `teacherslounge` | `~/lempi-data/listener.db` | `~/lempi-data/library.db` | `~/lempi-data/listener-backups/` |
| `smartboardpc` | the previous build's data directory | same | same, daily |
| `lempi02w` | `/var/lempi/listener.db` | `/srv/library/library.db` | `/var/lempi/listener-backups/` |
| `bose` | `/var/lempi/listener.db` | `/srv/library/library.db` (read-only mount) | `/var/lempi/listener-backups/` |
| `lp3-wifi` | `/var/lempi/listener.db` | `/srv/library/library.db` | `/var/lempi/listener-backups/` |

The desktop's pair went missing when this repository was seeded; `data/README.md` records it and SPEC046 rebuilds it.

## 3. What each runs — read 2026-09-26

**`[FLT-RUN-010]`** The appliances' build is from `lempi --version`. The source hosts' is their checkout.

| node | player | notes |
| :--- | :--- | :--- |
| `lempi02w`, `bose`, `lp3-wifi` | `618fd14`, deployed that day | lempi02w is plain ext4; the other two were written to both layers and their durable copy checked |
| `teacherslounge` | no player service; checkout fast-forwarded to `be778b9` that day | mirrors this file |
| `smartboardpc` | **the previous generation's player**, from that project's own checkout, up 6 days | **never migrated.** `build/deploy-everywhere.sh` named `/home/mango/Dev/Lempi`, which did not exist until it was cloned there that day to mirror this file (`be778b9`): the same silent miss TL001 found on `teacherslounge` `[TL-MIG-010]` |

## 4. Standing issues

**`[FLT-ISS-010]` `bose` dropped off the network on 2026-09-26 at about 15:33 UTC**, during a snapshot copy, and was still unreachable at the link level ("destination host unreachable") an hour later. Its Wi-Fi has a recorded history `[BOS-OPS-110]`. It needs a look, possibly a power cycle, and its database joins the merge when it is back `[SPEC-STAR-900]`.

**`[FLT-ISS-020]` `smartboardpc` needs migrating** to this repository's player `[FLT-RUN-010]`, as `teacherslounge` was in TL001.

**`[FLT-ISS-030]` `lp3-wifi` keeps UK time.** Its listener records a UTC offset of +60 minutes where every other node has −240, and its journal stamps agree: 14:56 on `lp3-wifi` was 09:56 on `bose`. Anything that follows the clock — a programme that starts at a set time `[SPEC-DIR-180]` — runs five hours off there. The fix belongs in its setup script, with the rest of its configuration.

---

**Traceability:** `[FLT-SHP-010]`, `[FLT-DAT-010]`, `[FLT-RUN-010]`, `[FLT-ISS-010..020]` · read from the machines 2026-09-26
