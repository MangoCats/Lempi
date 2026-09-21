# GUIDE031: Where an Option Gets Its Value

**Development Guidance — written 2026-09-20, alongside
[GUIDE030](GUIDE030-command-line-conventions.md), which is the command line
itself. This is the four layers underneath it.**

> **Related:** [`player/src/cli.rs`](../player/src/cli.rs) — the one resolver ·
> [GUIDE030](GUIDE030-command-line-conventions.md) `[GDE-CLI-010]` — one parser,
> seventeen tables · [GOV002](GOV002-sources-of-truth.md) — rank the sources,
> show which answered · [GUIDE023](GUIDE023-lists-that-mirror-something-else.md) `[GDE-ARC-033]`
> — why none of this is written twice · `fleet-example/` — all of it worked
> through

---

## 1. The chain

**`[GDE-CLI-090]` Every option resolves through four layers, highest first:
the command line, the stored setting, the environment, the built-in
default.** One function does it, for every option of every binary, driven by
what the option's own record declares. No binary and no option implements
precedence.

| Layer | Where | Declared by |
|---|---|---|
| 1 | the command line | always |
| 2 | the listener's persisted settings | `.setting("queue_depth")` |
| 3 | the environment | derived from the long name |
| 4 | the built-in default | `.or("5")` |

A value that is present but unreadable is **refused**, at every layer, not
skipped over to the one below `[GDE-CLI-030]`. `--port nonsense` used to
serve the default silently; `LEMPI_PORT=nonsense` would have been quieter
still, since nobody is looking at the environment. A *stored* value that an
option rejects does fall through — a database is not something a process may
refuse to start over — but it says so, naming the key and the layer it fell
to.

**`[GDE-CLI-115]` An option read with `must_*` must be able to answer from
*some* layer, or the binary panics at startup.** `must_real`/`must_int`/
`must_size` have no fallback of their own: nothing on the command line, no
stored setting, no environment, no declared `.or(..)` means a crash, not a
usage message — and on an appliance that is a unit that will not start.

*Written after it shipped.* `common::INTERVAL_S` carried no default while
its sibling `INTERVAL_MS` took `default_sample_interval_ms!()`, so
`mpd_watch` panicked on every run and `mpd_fill`/`mpd_direct` panicked
whenever the listener database held no saved setting. `cargo test` could not
see it: all three are behind the `mpd` feature and the default run does not
compile them. The guard therefore reads the *source* of `src/bin/` rather
than the binaries, the same technique `every_binary_has_a_table` uses, so it
holds whether or not the feature is enabled.

The fix was to derive rather than retype `[GDE-ARC-033]`: the seconds
default is the millisecond macro divided by a thousand, so the two cannot
drift.

---

## 2. Bootstrap: the layer that cannot answer for itself

**`[GDE-CLI-095]` `--listener` and `--library` never consult layer 2, because
layer 2 is found using them.** The stored settings live in the listener
database, and the path to that database is itself an option. An option that
asked the settings where the settings are would be a cycle.

This is declared, not discovered: those two carry `.bootstrap()`, the
resolver skips layer 2 for them, and a test asserts that no bootstrap option
also declares a stored setting. In `lempi.rs` the ordering is visible in the
source — the two paths are read, the database is opened, `with_settings` is
called, and only then is anything else read. Everything above that line
resolves through three layers and everything below it through four.

**Sixteen binaries have no layer 2 at all**, and that is expressed by never
attaching one rather than by sixteen special cases. `Args` simply has no
settings until something gives it some.

---

## 3. The environment name is derived, never written

Strip the leading `--`, uppercase, turn hyphens into underscores, prefix
`LEMPI_`, per `[GDE-CLI-090]`. So `--mpd-root` is `LEMPI_MPD_ROOT` and
`--echo-fleet-min-frames` is `LEMPI_ECHO_FLEET_MIN_FRAMES`.

The hyphen rule is not cosmetic: no POSIX shell can assign to a name
containing one, so a derivation that kept them would mint variables nobody
could set. A test asserts that no option derives a name with a hyphen in it.

Writing the name out beside the option would be the second copy
`[GDE-ARC-033]` warns about — it would survive a rename of the option and go
on answering to the old name. An explicit `.env(..)` exists but is for
genuinely established names only, and every use of it should read as
deliberate.

**`[GDE-CLI-105]` A flag that makes a program write is off layer 3
entirely.** `--apply` in `relink` and `import_bundle`, `--write` in the MPD
tools. An exported `LEMPI_APPLY` left in a shell would turn reporting into
writing, at a distance, with nothing on the command line to show for it.
These carry `.not_from_env()` and a test holds it.

---

## 4. One long name means one thing

**`[GDE-CLI-110]` No two of the seventeen may use the same long option name
for different things.** Sharing a name for a shared concept is the point of
`cli::specs::common` and is expected — `--library` means the same thing to
nine binaries. What is forbidden is one name meaning two, because the
environment variable is derived from the name: a machine-wide
`LEMPI_INTERVAL` that meant five seconds to three binaries and five
milliseconds to a fourth is a trap with a script's name on it.

Two collisions existed and both were renamed rather than papered over in the
environment layer:

| Binary | Old spelling | Now | Why |
|---|---|---|---|
| `mpd_session` | `‑‑interval`, in ms | `--interval-ms` | three other binaries take `--interval` in seconds |
| `delayprobe` | `‑‑device` | `--alsa-device` | the player matches a cpal name by substring; this is a raw ALSA device string |
| `echoprobe` | `‑‑url` | `--master-url` | in `fbui` that option is the *local* player; here it is another node |

A per-binary **default** or a differently worded help line is local
specialisation, not a conflict — `--seed` has a fixed default in `mpd_fill`
and seeds from the clock in `mpd_direct`, and both mean the same thing.

Two tests hold this. One compares meaning across the tables: value type,
unit, stored-setting key, scale, and derived variable. The other is
structural and stronger — it reads `specs.rs` and fails if any long name is
built twice by separate `Opt::` constructions, so a shared name *must* come
from one shared declaration and two binaries cannot disagree about a name
because there is only one of it.

### The whole namespace

Shared, from one declaration: `--listener`, `--library`, `--port`, `--depth`,
`--device`, `--root`, `--addr`, `--audio-root`, `--url`, `--file`,
`--start-ms`, `--end-ms`, `--apply`, `--write`, `--for`, `--interval`,
`--seed`. Used by one binary each: `--count`, `--list`, `--samples`,
`--all`, `--quick`, `--report`, `--bundle`, `--inventory`, `--uris`,
`--seconds`, `--calibrate`, `--then-handoff`, `--interval-ms`,
`--alsa-device`, `--master-url`, `--list-devices`, `--follow`, `--mpd`,
`--mpd-root`, `--echo-offset-frames`, `--echo-fleet-min-frames`,
`--echo-rate`, `--offset-frames`, `--rate`, `--file2`, `--start2-ms`,
`--end2-ms`, `--lead-s`.

---

## 5. Making the fallback visible

The reason this section exists is `[GOV-SRC-040]`. A precedence chain is four
copies of one quantity, and this project has been bitten repeatedly by
two copies disagreeing quietly. So a resolved value can name its own source,
and the player prints one line per option that did **not** come from its
built-in default:

```
lempi: --port = 13491, from $LEMPI_PORT
lempi: --depth = 9, from the stored setting `queue_depth`
```

Only the surprising ones. A wall listing every option at its default would be
the same as printing nothing. `journalctl -u lempi -b | grep "lempi: --"` is
therefore the answer to *why is this node like that*, per node, without
guessing.

---

## 6. The default port has one definition

**`[GDE-CLI-100]`** It was written out **eight** times: the option default,
`fbui`'s default WebSocket URL, `fbui`'s own doc comment, three shell
scripts, `tools/lempi_control.py`, and a search field in the console.

It is now the `default_port!` macro in [`player/src/cli.rs`](../player/src/cli.rs)
— a macro rather than a constant so it can be `concat!`ed into `fbui`'s URL
at compile time instead of that string carrying its own copy of the number.
`--depth` and `--interval` had the same duplication against
`crate::QUEUE_DEPTH` and `crate::SAMPLE_INTERVAL_MS`; those constants are now
derived from the same strings by a `const fn`.

Two mirrors survive outside the crate, because neither a shell script nor a
Python module can read a Rust macro: `build/lib-defaults.sh` and
`tools/lempi_control.py`. Both are **pinned by the crate's own test**, which
also fails if any other file under `build/` or `tools/` starts defining it
again. `tools/console_web/jobs.html` still carries the number in a form
field; it is a prefilled UI value rather than a default and is left alone.

To move the port, none of that matters: `--port`, the stored setting, or
`LEMPI_PORT`. `build/deploy-local.sh` no longer computes the port it expects
at all — it reads the one the player says it bound `[GDE-DEP-070]`.
