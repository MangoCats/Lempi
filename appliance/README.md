# appliance — what every appliance has

Material installed on **every** appliance — `lempi02w`, `bose` and `lp3-wifi` —
and the engine their setup scripts are built on. A machine folder holds only what
is that machine's own `[GDE-DEP-040]`.

Made on 2026-10-02, at the maintainer's direction to do things the same way on
every node wherever a difference bought nothing. Until then each node's script
carried its own copy of these items, and the fixes of one fortnight — `sqlite3`
on lp3-wifi, the timezone, the journal — were each made once per node, or found
missing on the node nobody had touched.

---

## The engine

**`[APP-SET-010]` One way to set up and check an appliance.** Each node has one
script, run on the development host, reaching the node over SSH:

| node | script | its own part |
| :--- | :--- | :--- |
| `lp3-wifi` | [`LempiPlay3/setup-lp3.sh`](../LempiPlay3/setup-lp3.sh) | the panel and fbui, the binds, the overlay, Bluetooth output |
| `bose` | [`BosePi/setup-bose.sh`](../BosePi/setup-bose.sh) | the DAC, MPD, the binds, the overlay and its escape hatch |
| `lempi02w` | [`LempiPi/setup-lempi02w.sh`](../LempiPi/setup-lempi02w.sh) | its unit and WirePlumber 0.4; the rest is `setup-appliance.sh`, run on the node |

Each takes `--check` (the default: compare, change nothing) or `--go` (apply
what differs). Every item prints `ok`, `CHANGED` or `DIFFERS`. On an overlay
root, `--check` reads the **durable** layer, the overlay's lower directory as
the mount itself names it `[GDE-DEP-070]`.

All three at once, one after another, with a summary naming each:

```
bash build/setup-fleet.sh            # --check
bash build/setup-fleet.sh --go       # or name nodes: --go bose lp3-wifi
```

**`[APP-SET-030]` `--go` on an overlay root writes both layers.** A write to
`/` there lands in a tmpfs and is gone at the next reboot `[IMPL-BOS-185]`, so
every change is made twice, the way
[`build/install-config.sh`](../build/install-config.sh) writes one file:

- a file goes live and to the durable layer, and the durable copy is read back
- a retired file is removed from both
- any other change runs live, then again inside `overlayroot-chroot`; its check
  reads the durable layer, so that is what decides whether it took
- what lives on STATE or LIBRARY — pi's home, `/etc/ssh`, `/var/log`, the data
  trees — is changed live only (`state_item`): a live write there is durable
- the durable layer is put back read-only at the end, or a warning says it
  could not be, and units that changed are reloaded

Nothing restarts the player. Written 2026-10-02, after lempi02w's `--go`, at
the maintainer's request that bose and lp3-wifi be brought level the same
way. First run, and proven across a reboot, the same evening (below).

A file is compared byte for byte. A copy that differs only in its comments
still `DIFFERS`: what is on the node is either the repository's file or it is
not.

[`build/setup-lib.sh`](../build/setup-lib.sh) is the engine (`item`, `file_item`,
`retired_item`, and the target's root and OS, said before anything runs
`[GDE-DEP-060]`). It came from `setup-lp3.sh`, where it was first written.

## What every appliance has

**`[APP-SET-020]` One list, in [`common.sh`](common.sh).** Read off all three
nodes before it was written; each item was already true of at least one.

- packages `chrony`, `python3`, `sqlite3`
- the Wi-Fi country and the timezone
- pi's ed25519 key; root's `/etc/ssh` and `/var/log`; every sudoers drop-in root's and 440
- `/var/lempi` and `/srv/library` pi's, nothing in them world-writable
- the fleet clock: its sources, Debian's pool, chrony enabled
- the journal: [`journald-lempi.conf`](journald-lempi.conf), persistent and bounded
- swap: [`rpi-swap-lempi.conf`](rpi-swap-lempi.conf) on trixie; lempi02w's 512 MB file on bookworm
- cloud-init absent or disabled
- [`lempi-preflight`](lempi-preflight) and [`lempi-db-recover`](lempi-db-recover), before every player start

The journal file is named `lempi.conf` for its **sort order**: trixie's image
ships `40-rpi-volatile-storage.conf`, and a `10-` name sorts before it and loses.

## What every Bluetooth appliance has

**`[APP-BT-010]` One set, in [`bluetooth/`](bluetooth/items.sh)**, for the nodes
that play to a Bluetooth speaker: `lempi02w`, and `lp3-wifi` since 2026-10-02.
`bose` plays to its DAC and has none of it.

- the stack: PipeWire with its ALSA and Bluetooth plugins, WirePlumber, BlueZ,
  upower (wanted by `multi-user.target`), and the agent's D-Bus bindings
- the helpers: the shared shell library, the sink gate, `lempi-btctl` and its
  one sudoers rule, the keeper, the watchdog and the agent
- their five units, `/run/lempi`, and the keeper's and watchdog's routine ticks
  kept out of the journal
- linger for pi, and PipeWire's clock: 44.1 kHz and a 4096-frame quantum, in one file
- native AVRCP transport control handling: the player reads the speaker's rocker or adapter buttons directly ([`IMPL019`](../docs/IMPL019-native-bluetooth-controls.md)), replacing the retired standalone `lempi-rocker` script

Until 2026-10-02 lempi02w's copy was heredocs in `setup-appliance.sh` and
lp3-wifi's was files copied from them. Read off both first: the units matched
but for one start timeout, which lempi02w carried in a drop-in; the clock's five
values were the same, in two files on lempi02w and one on lp3-wifi. What stays
per node is WirePlumber — 0.4's Lua on lempi02w's bookworm, 0.5's conf on
lp3-wifi's trixie — and lp3-wifi's radio, on a UART the image saved blocked.

## First run, 2026-10-02

`--check` against all three, before anything was applied, with the Bluetooth
set included:

| node | differs | what |
| :--- | ---: | :--- |
| `lp3-wifi` | 11 | the journal and swap files; the five units and the clock file, comments only; the sudoers rule and the quiet-tick drop-ins, which it never had |
| `bose` | 5 | the journal and swap files; **cloud-init not disabled**; its unlock-check unit has CRLF line endings on the card; `mpd.conf` differs in comments only |
| `lempi02w` | 17 | the journal file and its old name; the unit and the three files retired with the single-file mode `[PI-PRE-080]`; the five units (heredocs, no comments); the clock file and its two retired halves; the timeout drop-in; and two helpers older than the repository — the host's old name in comments, and a test hook in `lempi-btwatch` that changes nothing unset |

**lempi02w, applied 2026-10-02** with `setup-lempi02w.sh --go`, run by the
maintainer: every item `ok` or `CHANGED`, and a `--check` after it reported
all items as recorded, with the player playing throughout. It found one fault
of its own: `setup-appliance.sh` exited 1 after a run that changed something
under `--no-boot-tune`, its last line being a false `[ ] && echo` — fixed.
**bose and lp3-wifi, applied the same evening** by
`build/setup-fleet.sh --go bose lp3-wifi`, run by the maintainer — the
overlay path's first run: 5 and 11 items `CHANGED`, the durable layer back
read-only on both, nothing left staged. Then **both rebooted**, and a `--check`
of all three reported every item as recorded on fresh boots: no failed units,
both players back and playing (lp3-wifi to the Oontz, re-measuring its echo
after the boot's clock step), zram swap at 1.8 GB and 905 MB, the journal
keeping earlier boots, cloud-init gone from bose's boot.

---

**Traceability:** `[APP-SET-010..030]`, `[APP-BT-010]` · `[GDE-DEP-040]`
