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
| `lempi02w` | [`LempiPi/setup-lempi02w.sh`](../LempiPi/setup-lempi02w.sh) | its unit; the rest is `setup-appliance.sh`, run on the node |

Each takes `--check` (the default: compare, change nothing) or `--go` (apply
what differs). Every item prints `ok`, `CHANGED` or `DIFFERS`. On an overlay
root, `--check` reads the **durable** layer under `/media/root-ro`
`[GDE-DEP-070]`, and `--go` refuses: a write to `/` there is gone at the next
reboot `[IMPL-BOS-185]`. Use [`build/install-config.sh`](../build/install-config.sh)
for one file on a locked node.

A file is compared byte for byte. A copy that differs only in its comments
still `DIFFERS`: what is on the node is either the repository's file or it is
not.

[`setup-lib.sh`](setup-lib.sh) is the engine (`item`, `file_item`,
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

## First run, 2026-10-02

`--check` against all three, before anything was applied:

| node | differs | what |
| :--- | ---: | :--- |
| `lp3-wifi` | 2 | the journal and swap files, combined that day |
| `bose` | 5 | those two; **cloud-init not disabled**; its unlock-check unit has CRLF line endings on the card; `mpd.conf` differs in comments only |
| `lempi02w` | 6 | the journal file and its old name; the unit and the three files retired with the single-file mode `[PI-PRE-080]` |

Nothing was applied by this work. Each `DIFFERS` is a change still to make on
that node.

## Not yet here

The **Bluetooth family** — the speaker keeper, the watchdog, the agent, their
units and helpers — is shared by `lempi02w` and `lp3-wifi`, but still lives in
`LempiPi/`, and lempi02w's units are written by `setup-appliance.sh` while
lp3-wifi carries them as files. The settings match but for one start timeout,
which lempi02w carries in a drop-in. Moving them here is the next step.

---

**Traceability:** `[APP-SET-010]`, `[APP-SET-020]` · `[GDE-DEP-040]`
