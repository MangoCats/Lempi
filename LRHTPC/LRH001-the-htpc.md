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
| Login | mango, in groups `audio` and `sudo` — **sudo asks for a password** |

The root is an ordinary write, so nothing in CLAUDE.md §4 applies: `findmnt -no FSTYPE /` reads `ext4`, and both scripts below check it again before writing `[GDE-DEP-060]`.

## 2. How it plays

**`[LRH-DAT-010]` Everything lives in mango's home, because nothing here may need root.** sudo asks for a password, and a script over ssh cannot answer one. So the layout is teacherslounge's rather than smartboardpc's:

| what | where |
| :--- | :--- |
| the player and its tools | `~/lempi/bin/` — `lempi`, `import_bundle`, `relink`, `restore_listener` |
| catalogue · listener | `~/lempi-data/library.db` · `~/lempi-data/listener.db` |
| listening backups | `~/lempi-data/listener-backups/`, made by the player `[REQ-LIB-160]` |
| music | `~/Music`, the hub's `%USERPROFILE%\Music` copied whole |
| unit | `~/.config/systemd/user/lempi.service`, from [`lempi.service`](lempi.service) |

**`[LRH-SVC-010]` A `systemd --user` unit, started at boot by linger, and paused.** User, not system, for smartboardpc's reason `[SMT-SVC-010]`: PulseAudio runs in mango's user manager, and a player outside it plays nothing while answering HTTP 200 `[TL-OPS-020]`. Linger is set by mango on itself — polkit allows `set-self-linger` here without a password, read from `pkaction` — so the manager, and with it PulseAudio and the player, comes up at boot with no one logged in. The unit passes `--paused`: a player that took up the music whenever the box booted would play over whatever the television was doing. Play is pressed on its page, `http://lrhtpc:5720/`.

**`[LRH-AUD-010]` Kodi plays through PulseAudio too, so the two share the mixer.** Kodi's `audiooutput.audiodevice` is `PULSE:Default` and passthrough is off (`guisettings.xml`, read 2026-10-28). Had Kodi opened the HDMI device itself, through ALSA, whichever of the two started second would have found it busy. Both now mix into the one sink: nothing locks, and if both play at once both are heard. The setup script checks the setting, because a Kodi change to passthrough would bring the lock back.

## 3. Setting it up

**`[LRH-SET-010]` One script records the node, on the same engine as every other** `[FLT-SHP-030]`: [`setup-lrhtpc.sh`](setup-lrhtpc.sh), `--check` or `--go`. It holds the hostname, the pair and the music, linger, Kodi's audio device, and the unit. It makes no change that needs root, so `--go` can always run over ssh.

**`[LRH-DEP-010]` The player is built on the desktop, by [`deploy-lrhtpc.sh`](deploy-lrhtpc.sh).** The machine has no Rust toolchain, and installing the build dependencies would need root. The script builds a commit — `HEAD` by default — in a worktree made inside the `lempi-linux` image, which is `rust:1.90-bookworm`: the same Debian release as the node, and so the same glibc. Building on smartboardpc would not do, since Ubuntu 24.04's glibc is newer than bookworm's. Then it copies each binary beside its old one and renames it over it, reads back each checksum and `lempi --version`, restarts the unit, and waits for `/build` to name the commit. The unit starts paused, so the script reads `player_state.playing` from the listener first, and presses Play afterwards if it was playing.

**`[LRH-SEED-010]` The library was seeded once, 2026-10-28, and is not seeded by any script.** It is the procedure `BosePi/seed-library.sh` was written for, by hand, on a machine that has neither rsync nor root:

1. The music, as a `tar` stream over ssh from Git Bash: neither end has rsync, and the node cannot install it. The modes arrive as 644 and 755, as Git Bash's `tar` reports them, not the 777 that `rsync -a` carried from NTFS to bose and lempi02w.
2. The catalogue, taken from the hub's `data/library.db` through SQLite's backup API (`integrity_check` ok; 6,433 files, 20,805 passages) and sent as `~/lempi-data/library-new.db`.
3. `relink --library library-new.db --audio-root ~/Music`, reported first and then `--apply`, which binds each row to this machine's path by its audio hash `[SPEC012]`.
4. Renamed to `library.db`; the player then made `listener.db` on its first start.

**`[LRH-SEED-020]` What the seed found: everything, and nothing lost.**

- **The music arrived whole.** 9,008 files in 967 folders, 61,619,876,552 bytes, the same three numbers on both sides (`Thumbs.db` and `desktop.ini` left behind), in 90 minutes. Both ends of the pipe exited 0. The rate was 11.7 MB/s because the desktop's ethernet had negotiated **100 Mb/s** that evening. The node's link is gigabit, so the desktop's port or cable sets the speed of the next copy.
- **relink bound every row.** It hashed the 6,540 audio files in 480 s. All 6,433 catalogue rows were found and moved, with 0 missing and 0 corrupt. Then, read from the database rather than from relink: all 6,433 paths are under `~/Music`, all exist, all are distinct, and `integrity_check` is ok. The 107 files relink reported as unknown are audio the hub's catalogue does not hold either — `Sounds/`, two live sets — and nothing plays them.
- **The first start.** 4 ms to open the session, 10,402 radio passages, paused as asked. Its stream reached PulseAudio's HDMI sink, uncorked: `ALSA plug-in [lempi]`. It cannot see whether that sink is heard, since it has no `wpctl` `[SPEC-APS-030]`, so the music has been *routed* to the television here, not yet heard there.

## 4. What it is not, yet

**`[LRH-OPN-010]` Not enrolled in the mesh, so no sync reaches it.** It is in `fleet/star-plan.json` by host alone, and a run reports it `MISSING ... not enrolled` rather than skipping it in silence. Enrolment is a person's act, confirmed on both sides `[SPEC-MTR-130]`. Until it is enrolled, its plays stay its own and its catalogue stays as seeded.

**`[LRH-OPN-020]` Not a speaker the Export page sends to.** That is a `sync_peers` row in the hub's catalogue `[FLT-DAT-015]`, and a send would put its audio in `~/Music`.

**`[LRH-OPN-030]` Not on the fleet clock.** Joining it means installing chrony, which needs root. Until then it follows no echo leader closely enough to play in step `[GDE-ECHO-300]`.

---

**Traceability:** `[LRH-HW-010]`, `[LRH-DAT-010]`, `[LRH-SVC-010]`, `[LRH-AUD-010]`, `[LRH-SET-010]`, `[LRH-DEP-010]`, `[LRH-SEED-010..020]`, `[LRH-OPN-010..030]` · read from the machine 2026-10-28
