# PI019: Shell Hygiene, and the Tests That Guard It

**Appliance Record — one copy of each fact, and 77 assertions**

Split from [PI010](PI010-startup-time-and-stutter.md) on 2026-09-10, which had reached 687 lines against `[GOV-DOC-010]`'s 300-line limit.

The player carries 467 test functions. The shell helpers that decide which
speaker plays carried none until 2026-09-10, and five real faults shipped
from them in a single evening. This is the cleanup and the suite that
followed, and the defects each found on its first run.

> **Related:** [PI010](PI010-startup-time-and-stutter.md) for the startup investigation these came out of · [PI011](PI011-two-speakers-and-placement.md) for the stutter

---

## 1. One copy of each fact

**`[PI3-AIM-090]` One copy of each fact, in `lempi-common.sh`.** Reviewed
against the one-sentence policy of `[PI3-AIM-080]` on 2026-09-10. Five things
had been written out four and five times over:

| Duplicated | Copies | Now |
| --- | --- | --- |
| where the listener's database lives | 5 | `lempi_db` |
| reading `speaker_address` out of it | 4 | `lempi_speaker` |
| parsing a sink name from `wpctl status` | 4 | `lempi_sinks`, `lempi_sink_present` |
| a device's alias | 3 | `lempi_alias` |
| decoding an AFH map | 2, in three forms | `lempi_afh_channels`, `_bytes`, `_excluded` |

**Why it mattered rather than merely offended.** The sink parser was wrong
once, in a way that let the player open onto a dummy and report success
`[PI3-FOUND-110]`; the fix then had to be carried by hand into everything that
had copied it. And a helper reading the pre-split database tells the listener
about the speaker they used to have `[PI3-FOUND-280]` -- a failure that has
happened here, and the kind that only happens to duplicated knowledge.

Sourced, not executed:

    . "${LEMPI_COMMON:-/usr/local/lib/lempi-common.sh}" 2>/dev/null ||
        . "$(dirname "$0")/lempi-common.sh"

**`[PI3-FOUND-650]` And one rule was deleted outright, not moved.**
*(Reversed the same evening by `[PI3-FOUND-680]`. Calling it redundant assumed
the agent could refuse a trusted device; BlueZ never asks an agent about one,
so removing the untrusting made the agent unreachable rather than redundant.
It is back, keyed to whoever holds the audio rather than to the chosen
speaker.)*
`lempi-btctl`'s `withdraw_others` untrusted every speaker but the chosen one,
so none could let itself in over it. The agent now refuses any audio profile
from anything that is not holding the audio `[PI3-FOUND-630]`, which reaches
the same outcome by answering rather than by pre-emption -- and untrusting had
a sharp side effect: a paired but untrusted speaker, powered on nearby, was
refused for want of an agent and knocked every nine seconds for as long as it
stayed on `[PI3-FOUND-610]`.

It had also forced a subtlety on the keeper's fallback, which tested `Paired`
rather than `Trusted` precisely because this code untrusted the speaker it
needed to reach `[PI3-FOUND-560]`. **One rule removed, one gotcha removed with
it, and the thing it was protecting against still handled.**

Net across the review: `lempi-speaker.sh` 503 → 340 lines, `lempi-afh-seed`
137 → 108, `lempi-linkstate` 102 → 93, `lempi-wait-sink` 145 → 130, against 137
lines of shared library that replaced roughly twice that in copies.


## 2. The test suite

**`lempi-bt-agent selftest` — the one test this appliance ships.** Checks
every Bluetooth audio and control profile against both an incumbent caller and
an intruder, with and without an incumbent recorded: 36 cases, no D-Bus, no
effect on the running agent.

    lempi-bt-agent selftest

Its UUIDs are transcribed by hand from the assigned-numbers list rather than
read from the code they check, because the version that read from the code
passed five cases while the agent waved through the profile BlueZ actually
asks about `[PI3-FOUND-660]`. **A test that shares the implementation's blind
spot tests nothing.** Verified by reintroducing that exact bug and confirming
the test fails on it.

**`[PI3-AIM-100]` The appliance's shell helpers now have tests.** The player
carries 467 test functions; the scripts that decide which speaker plays
carried none, and on 2026-09-10 five real faults shipped from them in one
evening. `LempiPi/tests/run` covers them: 39 assertions across the shared
library, the keeper's whole policy, and the sink gate.

**How it works.** These scripts reach the world through five commands --
`bluetoothctl`, `wpctl`, `curl`, `sqlite3`, `hcitool`. `tests/stubs` holds
fakes that answer from a fixture directory and record every invocation, and
`PATH` puts them first. The assertions are about *decisions* -- what was
connected, disconnected, trusted, written -- not about hardware, so it runs
anywhere with no adapter and no speaker.

    LempiPi/tests/run            # everything
    LempiPi/tests/run speaker    # one group

**Verified by breaking things on purpose.** A green suite proves nothing until
it fails on real faults, so three of that evening's bugs were reintroduced into
copies and the suite was re-run:

| Reintroduced fault | Caught |
| --- | --- |
| the `exit 0` that made the chase unreachable | 4 failures |
| adoption guarded only on emptiness `[PI3-FOUND-700]` | 2 failures |
| the doubled backslash in the awk continuation | 6 failures |

**The second one exposed a weak test rather than a strong one.** It passed at
first: the `sqlite3` stub failed reads *and* writes together, while the real
fault is a failed read followed by a perfectly good write that destroys the
listener's choice. The stub now fails them independently. That is the same
error as the agent's first selftest `[PI3-FOUND-660]` -- a fixture that shares
the implementation's assumption -- caught this time by mutation rather than by
a speaker.

**Extended to 77 assertions**, closing the gaps a coverage review found:
never paging while audio plays `[PI3-AIM-060]` and never paging a device that
already has a link `[PI3-FOUND-140]` -- both rules whose violation was
*audible* -- plus `lempi-db-recover`, which runs on every boot of a machine
that is power-cut by design, and `lempi-btctl`, whose address argument is the
only untrusted input this appliance takes. That input is now tested against a
command substitution and a shell metacharacter as well as malformed
addresses; all six are refused before reaching `bluetoothctl` or SQL.

**One product finding came out of writing them.** `lempi-db-recover` assumes
opening a database read-write clears a hot journal. Measured: after a write
killed mid-transaction, the journal survives `PRAGMA user_version`, a real
`SELECT`, `PRAGMA integrity_check` and `BEGIN IMMEDIATE` alike -- 4616 bytes
every time -- while the data reads back correctly, because the transaction
never reached the main database and SQLite does not consider that journal hot.
The script's warning path fires, which is the behaviour now asserted. The
recovery it was built for `[PI3-FOUND-120]` is a different case, and this
fixture does not reproduce it: **rollback of a genuinely hot journal remains
untested.**

**Two more groups, and a real gap each found.**

*`units`* reads the systemd units `setup-appliance.sh` writes -- no stubs, no
hardware -- and requires a finite `TimeoutStartSec` on every `Type=oneshot`.
**It found two more unbounded oneshots the moment it existed**:
`lempi-afh-seed boot`, which applies seven times at ten-second intervals and
talks to `hcitool`, and `lempi-led-boot`. Both now have ceilings. It also
checks that the keeper's timeout clears its own tick budget, and that every
unit's `ExecStart` names a helper that actually ships beside the setup script
-- a unit pointing at a program nobody installed fails only at boot.

*`hci`* covers the aggregation every measurement in PI011 came through. It was
verified once by hand against synthetic input and never again, because buried
inside the capture it could only be exercised by running a real `btmon` for a
real window. It is now a callable mode -- `lempi-hci-capture aggregate <file>`
-- which the appliance's own capture path uses, so the tests exercise the code
that ships. **The assertion that matters is that a silent second is emitted as
a zero row**: a skipped row would read as continuous audio, which is precisely
the symptom this instrument exists to find.

Writing those tests corrected the rate arithmetic. A row stamped T reports
what was seen between T and the *next* marker, so the bytes inside a window
are rows `t_first..t_last-1` over `t_last - t_first`. Dropping the first row
and keeping the last counts the right *number* of rows, so it passes unnoticed
on a long steady capture and is visibly wrong on a short one.

**What it cannot cover, and must not pretend to.** BlueZ's own semantics: a
stub encodes what its author believed, and the belief that an agent is
consulted for a trusted device is exactly what cost an evening
`[PI3-FOUND-680]`. Whether audio is audible, and at what rate, stays with the
listener's ear and `lempi-hci-capture`.


## 3. Two copies of one script

**`[PI3-FOUND-710]` Two rockers, and the appliance ran the broken one.**
`lempi-rocker` at 84 lines and `lempi-rocker.sh` at 124 both lived in
`LempiPi/`, and the install loop took the shorter one by name. The longer is a
superset: it adds `WAIT_FOR` and `MAP_ONLY`, and it fixes `say()` writing to
stdout -- which was captured into the device name `await_dev` returns and
produced a first run reading `/dev/input/22:25:46 waiting...event2`.

So a fix sat in the repository, beside the file it fixed, undeployed. Nothing
was wrong with either file; the defect was having two. The stale copy is
deleted rather than left to be picked again, and the `.sh` source now installs
under the bare name -- the convention `lempi-speaker.sh` already followed
`[PI3-AIM-090]`.

