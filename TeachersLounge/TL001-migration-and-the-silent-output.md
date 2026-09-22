# TL001: Migrating `teacherslounge`, and the Output That Failed Silently

**Machine Record — migrated 2026-09-21, parity confirmed 2026-09-22**

`teacherslounge` is an Ubuntu 22.04 laptop, x86_64, with the same 44 G music
library the desktop has `[GDE-ECHO-458]`. It is the fleet's acoustic
instrument — its microphone and ADC carry the calibration behind
[LOG008](../docs/LOG008-acoustic-calibration.md) — and, since this migration,
a Lempi node.

> **Related:** [SPEC035](../docs/spec/SPEC035-mesh-library-sync.md) for the
> mesh design that already named this machine ·
> [GUIDE010](../docs/GUIDE010-echo-node-capabilities.md) `[GDE-ECHO-458]` for
> its audio capabilities · [`build/deploy-everywhere.sh`](../build/deploy-everywhere.sh)
> for where it sits in the fleet · [GUIDE015](../docs/GUIDE015-the-earlier-names.md)
> for why the previous repository is not named here `[GDE-NAM-030]`

---

## 1. It was never migrated, and nothing said so

**`[TL-MIG-010]` The path this fleet's tooling had always named did not
exist.** `build/deploy-everywhere.sh` has listed
`sw@teacherslounge:/home/sw/Dev/Lempi` in `SOURCES` for as long as that file
has existed. There was no checkout there. This leg of a fleet-wide deploy had
therefore never once run against the machine, and had never once complained:
**a source host that is never reached reports nothing at all.**

The machine was running the previous repository instead `[GDE-NAM-030]`, with
its own data directory beside it, and it worked. Nobody was missing a deploy
they had never had. Four statements in the tree described it as a Lempi node;
none of them was checked against the machine until 2026-09-21.

**`[TL-MIG-015]` A claim about a remote machine is checkable in one command,
and was not checked for weeks.** `ssh sw@teacherslounge 'ls ~/Dev/Lempi'`
settles it. The documentation instead carried a description that was
aspirational when written and simply aged into being false, which is
`[GOV-SRC-020]`'s point about a plausible answer nobody measured.

---

## 2. What was migrated

**`[TL-MIG-020]` The data was copied, never moved, and the source was left
runnable throughout.** `sqlite3 .backup` rather than `cp`, because
`listener.db` carries a WAL and the online backup API is what produces a
consistent snapshot of one. Both halves verified afterwards:

| | rows |
| :--- | ---: |
| `files` | 5,709 |
| `passages` | 16,661 |
| `recordings` | 8,224 |
| `flavor` | 578,523 |
| `listener_play_history` | 37,762 |
| `listener_preferences` | 3,263 |

All **39 tables** matched count-for-count between source and copy, and
`PRAGMA integrity_check` returned `ok` on both files. The predecessor's
databases keep their original mtimes; reading them did create SQLite's
`-shm`/`-wal` sidecars in that directory, which are derived files and were
empty, but "untouched" would be a stronger claim than what was verified.

**`[TL-MIG-030]` Parity was measured, not asserted.** The same `dircheck`
load, against the same data, on both builds:

| | previous build | Lempi |
| :--- | ---: | ---: |
| radio passages | 8,330 | 8,330 |
| first Director load | 797 ms / 125.2 MB | 833 ms / 125.3 MB |
| each further hold | ~13.5 MB | ~13.7 MB |

Lempi reports more than the older build does: the eligibility breakdown
(8,103 of 8,330, with the blocked and under-weight counts), and the missing
`recording_works` table announced as a named fallback `[GDE-WRK-035]` where
the older build says nothing. **That table is absent from the database, not
from either program** — both carry identical handling, and this catalogue
predates the feature. It is not a regression.

**`[TL-MIG-040]` Audible, and the number moved.** Confirmed 2026-09-22 on the
laptop's built-in speakers. Corroborated rather than taken on trust:
`player_state.position_ms` advanced 46,934 → 53,527 across the same interval,
which is `[GDE-ECHO-547]`'s standard — ask for a number that has to move.

---

## 3. The lesson: a player that serves, and makes no sound

**`[TL-OPS-010]` Launched over `ssh`, Lempi bound its port, answered HTTP 200,
and emitted 215 ALSA errors in 233 log lines.** Every external sign was
healthy. `GET /` returned the UI, `/build` reported the right commit,
`/browse` served 6.3 KB. The log said, once per period:

```
output: Default Audio Device @ 44100 Hz, 2 ch
output stream error: ALSA function 'snd_pcm_avail_delay' failed with error 'I/O error (5)'
```

**`[TL-OPS-020]` The cause is the session, not the machine.** PulseAudio and
pipewire were running the whole time and the desktop session was live on
tty2. What a non-interactive `ssh` lacks is `XDG_RUNTIME_DIR`, so ALSA's pulse
plugin cannot find `$XDG_RUNTIME_DIR/pulse/native` and the default device
fails on every period. Setting it is what a desktop launch does for free:

```
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
```

215 errors became **1**, at startup, and the log went quiet at 20 lines.

**`[TL-OPS-030]` This is the shape `[GDE-ECHO-547]` already names, arriving
from a new direction.** There, a settle curve read `0 underruns, 0
recoveries` for five minutes on a stream that had never opened. Here, an HTTP
check reads 200 on a player whose output has never produced a sample. In both
cases the proxy is healthy and the thing itself is dead, and in both the only
honest check is to ask for a quantity that has to change.

**Anything that launches this node without a login session inherits this** — a
deploy hook, a `systemd --user` unit written without `PAMName=` or a lingering
session, a cron entry. `build/verify-playing.sh` would report "cannot tell"
here rather than a pass, which is the right answer, but it is not the same as
noticing.

---

## 4. Open

**`[TL-OPN-010]` Vipunen has not been run on this machine.**
[SPEC035](../docs/spec/SPEC035-mesh-library-sync.md) describes it as a
Lempi/Vipunen instance and `tools/console.py` has never been pointed at
`~/lempi-data`. No console sidecar database exists anywhere on the host,
which confirms it has never run there rather than merely not run recently.
Half of what that document assumes is therefore untested.

**`[TL-OPN-020]` There is no unit, so nothing survives a reboot.** The
previous build had none either, so this is parity rather than a regression,
and the machine correctly stays in `SOURCES` rather than `APPLIANCES` —
without a unit there is no `lempi.service` journal and no device of the
player's own to sample. `build/deploy-everywhere.sh` states the exit
condition: give it a unit and it can move. Doing so must carry
`[TL-OPS-020]`'s environment, or it will start and be silent.

**`[TL-OPN-030]` The predecessor is still installed and still runnable**, with
its checkout and a 3.3 G data directory that includes a pre-split monolith and
its backup. Removing it is a separate decision from this migration, and
`[TL-OPN-010]` should be settled first.
