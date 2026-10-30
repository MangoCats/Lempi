# LRH001: The Living-Room HTPC

**Machine Record — `lrhtpc` · surveyed and set up on `mango@lrhtpc`, 2026-10-28 · update when the node changes**

`lrhtpc` is the living room's Kodi box, and since 2026-10-28 a playback node of the fleet: it holds its own pair and the whole of the hub's music, and plays through the television. It is the first node that is somebody else's machine first. Kodi is what it is for, and every choice below is made so that the player stays out of Kodi's way.

> **Related:** [FLEET001](../fleet/FLEET001-the-fleet.md) (the fleet, and this node's row) · [SMART001](../SmartPC/SMART001-survey.md) (smartboardpc, the other x86_64 node, whose user unit this one's follows) · [SPEC046](../docs/spec/SPEC046-star-sync.md) (how its pair would be synced, once it is enrolled) · [HOWTO](../HOWTO.md) (a library is a catalogue the hub sends and a listener the player makes)

---

## 1. The machine — read 2026-10-28

**`[LRH-HW-010]` x86_64, Debian 12, plain ext4, on gigabit ethernet.**

| | |
| :--- | :--- |
| OS | Debian GNU/Linux 12 (bookworm), kernel 6.1.0-37-amd64 |
| CPU · memory | 4 cores · 7.5 GiB, about 4 GiB available with Kodi's session up |
| Root | `/dev/mmcblk0p2`, ext4, 113 G — 86 G free before the music |
| Network | `enp1s0`, 1000 Mb/s; its Wi-Fi is down |
| Audio | PulseAudio, in mango's user manager; default sink the HDMI output |
| Clock | `US/Eastern`, synchronised by the system's own client, not the fleet's chrony |
| Login | mango, in groups `audio` and `sudo` — **sudo asks for a password**, except while granted for a deploy `[LRH-DEP-020]` |

The root is an ordinary write, so nothing in CLAUDE.md §4 applies: `findmnt -no FSTYPE /` reads `ext4`, and both scripts below check it again before writing `[GDE-DEP-060]`.

## 2. How it plays

**`[LRH-DAT-010]` The data lives in mango's home, because nothing that runs here may need root.** sudo asks for a password, and a script over ssh cannot answer one. So the layout is teacherslounge's rather than smartboardpc's. Only the binaries are root's, installed by a deploy under a sudo granted for it `[LRH-DEP-020]`:

| what | where |
| :--- | :--- |
| the player and its tools | `/usr/local/bin/` — `lempi`, `import_bundle`, `relink`, `restore_listener`; in `~/lempi/bin/` until 2026-10-29 |
| catalogue · listener | `~/lempi-data/library.db` · `~/lempi-data/listener.db` |
| listening backups | `~/lempi-data/listener-backups/`, made by the player `[REQ-LIB-160]` |
| music | `~/Music`, the hub's `%USERPROFILE%\Music` copied whole |
| unit | `~/.config/systemd/user/lempi.service`, from [`lempi.service`](lempi.service) |

**`[LRH-SVC-010]` A `systemd --user` unit, started at boot by linger, and paused.** User, not system, for smartboardpc's reason `[SMT-SVC-010]`: PulseAudio runs in mango's user manager, and a player outside it plays nothing while answering HTTP 200 `[TL-OPS-020]`. Linger is set by mango on itself — polkit allows `set-self-linger` here without a password, read from `pkaction` — so the manager, and with it PulseAudio and the player, comes up at boot with no one logged in. The unit passes `--paused`: a player that took up the music whenever the box booted would play over whatever the television was doing. Play is pressed on its page, `http://lrhtpc:5720/`.

**`[LRH-AUD-010]` Kodi plays through PulseAudio too, so the two share the mixer.** Kodi's `audiooutput.audiodevice` is `PULSE:Default` and passthrough is off (`guisettings.xml`, read 2026-10-28). Had Kodi opened the HDMI device itself, through ALSA, whichever of the two started second would have found it busy. Both now mix into the one sink: nothing locks, and if both play at once both are heard. The setup script checks the setting, because a Kodi change to passthrough would bring the lock back.

## 3. Setting it up

**`[LRH-SET-010]` One script records the node, on the same engine as every other** `[FLT-SHP-030]`: [`setup-lrhtpc.sh`](setup-lrhtpc.sh), `--check` or `--go`. It holds the hostname, the pair and the music, linger, Kodi's audio device, and the unit, and it checks the binaries the deploy installs. It makes no change that needs root, so `--go` can always run over ssh.

**`[LRH-DEP-010]` The player is built on the desktop, by [`deploy-lrhtpc.sh`](deploy-lrhtpc.sh).** The machine has no Rust toolchain, and installing the build dependencies would need root. The script builds a commit — `HEAD` by default — in a worktree made inside the `lempi-linux` image, which is `rust:1.90-bookworm`: the same Debian release as the node, and so the same glibc. Building on smartboardpc would not do, since Ubuntu 24.04's glibc is newer than bookworm's. Then it installs each binary beside its old one and renames it over it, reads back each checksum and `lempi --version`, restarts the unit, and waits for `/build` to name the commit. The unit starts paused, so the script reads `player_state.playing` from the listener first, and presses Play afterwards if it was playing.

**`[LRH-DEP-020]` The binaries are in `/usr/local/bin`, since 2026-10-29, so a send finds `import_bundle`.** A send looks for it with `command -v` over ssh `[SPEC-STAR-094]`, and a non-interactive ssh here has `PATH=/usr/local/bin:/usr/bin:/bin:/usr/games`: `~/lempi/bin` is on none of it, so the Export page could list this node and never send to it. smartboardpc and teacherslounge link it there with sudo; here sudo asks for a password, so the maintainer grants it for the deploy, as a drop-in `/etc/sudoers.d/zz-mango-temp` that a `systemd-run --on-active=120m` timer removes. The deploy asks `sudo -n true` before it builds, and stops with nothing done if sudo would ask. It copies each binary to `/tmp`, then `sudo install`s it as root's, 755, beside the old one and renames it over it; and it refuses to restart a unit whose `ExecStart` names another copy, which would only show as the wrong build answering.

The move, 2026-10-29: `setup-lrhtpc.sh --go` installed the unit naming `/usr/local/bin/lempi` while the old player ran on; `deploy-lrhtpc.sh` built `65d4799`, installed the four, and the player answered `/build` as `65d4799a39cd`, still paused; a second `--go` removed `~/lempi` and `--check` read all as recorded. A non-interactive `command -v import_bundle` then answers `/usr/local/bin/import_bundle`. The Export page's diff against this node found 65 files it lacks (319.9 MB, seven albums) and four covers, and the send's dry run read the library read-write on ext4 with 29.0 GB free.

**`[LRH-SEED-010]` The library was seeded once, 2026-10-28, and is not seeded by any script.** It is the procedure `BosePi/seed-library.sh` was written for, by hand, on a machine that has neither rsync nor root:

1. The music, as a `tar` stream over ssh from Git Bash: neither end has rsync, and the node cannot install it. The modes arrive as 644 and 755, as Git Bash's `tar` reports them, not the 777 that `rsync -a` carried from NTFS to bose and lempi02w.
2. The catalogue, taken from the hub's `data/library.db` through SQLite's backup API (`integrity_check` ok; 6,433 files, 20,805 passages) and sent as `~/lempi-data/library-new.db`.
3. `relink --library library-new.db --audio-root ~/Music`, reported first and then `--apply`, which binds each row to this machine's path by its audio hash `[SPEC012]`.
4. Renamed to `library.db`; the player then made `listener.db` on its first start.

**`[LRH-SEED-020]` What the seed found: everything, and nothing lost.**

- **The music arrived whole.** 9,008 files in 967 folders, 61,619,876,552 bytes, the same three numbers on both sides (`Thumbs.db` and `desktop.ini` left behind), in 90 minutes. Both ends of the pipe exited 0. The rate was 11.7 MB/s because the desktop's ethernet had negotiated **100 Mb/s** that evening. The node's link is gigabit, so the desktop's port or cable sets the speed of the next copy.
- **relink bound every row.** It hashed the 6,540 audio files in 480 s. All 6,433 catalogue rows were found and moved, with 0 missing and 0 corrupt. Then, read from the database rather than from relink: all 6,433 paths are under `~/Music`, all exist, all are distinct, and `integrity_check` is ok. The 107 files relink reported as unknown are audio the hub's catalogue does not hold either — `Sounds/`, two live sets — and nothing plays them.
- **The first start.** 4 ms to open the session, 10,402 radio passages, paused as asked. Its stream reached PulseAudio's HDMI sink, uncorked: `ALSA plug-in [lempi]`. It cannot see whether that sink is heard, since it has no `wpctl` `[SPEC-APS-030]`, so the music has been *routed* to the television here, not yet heard there.

**`[LRH-SEED-030]` A listener the player makes is not a whole listener half.** The first star sync to reach this node, run `20261029T1614Z`, failed in its patch stage with `no such table: boundary_reviews`. The listener made at the first start had 13 of the listener half's 22 tables. Nine were missing: `artist_reviews`, `boundary_reviews`, `id_reviews`, `listener_likes`, `listener_occasion_points`, `listener_occasions`, `listener_settings`, `selection_decisions` and `schema_meta`.

The cause: on Linux the player opens a missing listener with `SQLITE_OPEN_CREATE` and makes only the tables it uses itself. `create_pair_if_absent` builds the whole half from `sql/schema.sql`, but only when neither file exists, and only the Android app calls it. The three review tables are in no schema at all: the player makes them only in a `vipunen-support` build. Every node before this one ran a listener split from a full database, so none had met this.

**Repaired 2026-10-29 12:22, by hand.** The player was stopped, already paused, and the listener backed up as `listener-backups/pre-tables-20261029.db`. The nine tables were then created, with their indexes and `schema_meta`'s two rows, from the hub's listener's own DDL, the reference the sync patches against. Its columns turned out to be `schema.sql`'s, the review tables' included; only the statements' text differs. Afterwards: 22 tables, `integrity_check` ok, and the player back up paused, still a member. The failed run's snapshot of this node predates the repair, so the next sync starts from a new snapshot.

**Fixed in the player the same day.** `PlayerStore::open_split` now completes the listener half on every open, from `schema.sql`, and makes the three review tables in every build: `complete_listener` in `player/core/src/db/pair.rs`. A test seeds a pair the way this node was seeded, and it fails without the fix. On a node running an older player, a seeded listener still needs this repair before its first sync. [GUIDE040](../docs/GUIDE040-adding-a-node-to-the-fleet.md) `[GDE-ADD-070]` says how to check.

## 4. What it is not, yet

**`[LRH-OPN-010]` Enrolled 2026-10-29 16:12 UTC, and not yet synced.** It is a player in the mesh "Home", roster version 10, fingerprint `86ac3e28…`, and that fingerprint is its `member` in `fleet/star-plan.json`. The code was 288 682, confirmed on both sides `[SPEC-MTR-130]`.

**The first invite failed, and the console never said so.** The window had not been opened, so the player refused the invitation. The refusal went only to `data/mesh/invites/<address>.log`, and the mesh page looked as if nothing had happened. This node has no pair button and no `lempi-btctl`, so its window is opened by the signal the button sends, given by hand:

```
ssh mango@lrhtpc 'pkill -USR1 -x lempi'
```

The window is open five minutes, for one pairing `[SPEC-NSH-170]`. Send the invite after the signal, not before.

No catalogue has reached it by sync yet; its listener half has, since run `20261029T1653Z`. Its catalogue was seeded by hand, not sent by a sync, so every run read *no copy was last sent* and sent it none -- and with it none of the 23 covers the hub corrected on 2026-10-29, Queen's *Greatest Hits* among them `[SPEC-STAR-945]`. **Adopted 2026-10-29 22:29 UTC** `[SPEC-STAR-946]`, after that evening's Export send to it had applied: its own catalogue, 1,376 MB, copied on the node through the backup API, `integrity_check` ok there, fetched and matched by SHA-256, is now its copy last sent (`data/sync/adopted/`). `tools/check_node_ready.py` then read it `ready`. Read what the next run's snapshot says about its catalogue before letting that run send it anything.

**`[LRH-OPN-020]` A speaker the Export page sends to since 2026-10-29, and sent nothing yet.** Its `sync_peers` row in the hub's catalogue `[FLT-DAT-015]` names the catalogue `~/lempi-data/library.db` by its absolute path, the listener beside it, and the audio root `~/Music`, also absolute; it is left out of the older remote push, since its catalogue comes by star sync. It could be listed before it could be sent to `[LRH-DEP-020]`. The send itself, 65 files at the time of writing, is the maintainer's press.

**`[LRH-OPN-030]` On the fleet clock since 2026-10-30.** Until then it was not, so it could follow no echo leader closely enough to play in step `[GDE-ECHO-300]`. Joining needed root, under a sudo granted for it `[LRH-CLK-010]`.

**`[LRH-CLK-010]` chrony, preferring the fleet's own time server, as the source hosts have it** (`build/source-host-items.sh`). Set up by `setup-lrhtpc.sh` in four items: the package, the fleet's sources file rendered from `fleet/targets.env` `[SPEC-FCP-030]`, chrony restarted after that file last changed, and enabled. Applying needs root, so `--go` asks with `sudo -n` and fails at once without a grant; `--check` reads only what is world-readable, and needs none. The first `--go` failed: lrhtpc's package index named a chrony build the archive had replaced, so the download was a 404. The item now refreshes the index first. Installed 2026-10-30, chrony 4.3, replacing `systemd-timesyncd`; `--check` then read all as recorded. Read from chrony itself a minute later: the fleet's server selected (`^*`, smartboardpc, stratum 5). That server is sound. Its replies carry a root dispersion of 1.2 ms over a 1.8 ms round trip on Wi-Fi, and it tracks the household's pi.hole to 0.14 ms. lrhtpc itself was still settling: its offset from the server went from 40 to 15 ms in the first minutes as chrony slewed, and its own root dispersion read 12 s, the bound of a chrony with too few samples yet. Not yet read once settled.

---

**Traceability:** `[LRH-HW-010]`, `[LRH-DAT-010]`, `[LRH-SVC-010]`, `[LRH-AUD-010]`, `[LRH-SET-010]`, `[LRH-DEP-010]`, `[LRH-DEP-020]`, `[LRH-SEED-010..030]`, `[LRH-OPN-010..030]` · read from the machine 2026-10-28; binaries moved 2026-10-29
