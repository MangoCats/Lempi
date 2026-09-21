# GUIDE030: One Command Line, Seventeen Binaries

**Development Guidance — written 2026-09-20, from `./lempi --help` starting a
player against a database called `--help`**

> **Related:** [GUIDE031](GUIDE031-where-an-option-gets-its-value.md) `[GDE-CLI-090]`
> — the four layers a value comes from · [`player/src/cli.rs`](../player/src/cli.rs) — the only parser ·
> [`player/src/cli/specs.rs`](../player/src/cli/specs.rs) — the seventeen
> tables · [GUIDE023](GUIDE023-lists-that-mirror-something-else.md) `[GDE-ARC-033]`
> — why nothing here is written twice ·
> [GOV002](GOV002-sources-of-truth.md) `[GOV-SRC-040]` — a fallback must be
> visible

---

## 1. What was wrong

`./lempi --help` did not print help. `lempi` read its database as argument
zero, nothing recognised `--help`, so the string `--help` became the path: the
player opened it, SQLite created it, and a web UI came up serving an empty
library. **A file literally named `--help` sits untracked in a checkout on
another machine**, left by someone who tried it. `--version` was recognised by
one binary of seventeen. The other sixteen each parsed `std::env::args()` their
own way, and two of them read an option their own usage text never mentioned.

---

## 2. The convention

**`[GDE-CLI-010]` One parser, seventeen tables.**
[`player/src/cli.rs`](../player/src/cli.rs) is the only argument-parsing code
in the repository. A binary contributes a `&[Opt]` and no parsing of its own:
`--help`, `--version`, unknown options, malformed values and the usage text
all come from the core, so the seventeen cannot drift apart as the project
changes. No third-party crate — every dependency is a memory decision on a
512 MB Pi `[REQ-HW-140]`, and nothing here needs subcommands or completions.

**`[GDE-CLI-020]` Every option is named. There are no positional arguments.**
A bare word is a mistake, not a value looking for a home. The database path is
`--listener` or `--library` accordingly; see §3.

**A value comes from four places, in order** `[GDE-CLI-090]`: the command
line, the stored setting, the environment, the built-in default. One resolver
does it for every option of every binary, and
[GUIDE031](GUIDE031-where-an-option-gets-its-value.md) is the whole of it --
including why `--listener` is outside layer 2, and why one long name may not
mean two things.

**`[GDE-CLI-030]` An unreadable value is refused, not defaulted.** `--port
abc` used to serve 5720 and say nothing, because every hand-rolled site read
`.parse().ok().unwrap_or(default)`. The kind of each option is declared and
checked at parse time.

**`[GDE-CLI-060]` `-h` is help and `-V` is version, everywhere.** `-v` is
reserved and deliberately unused: it means `--verbose` in every other tool,
and a binary here that one day wants verbosity must not find the letter
already spent. Neither is available to a per-binary option.

**`[GDE-CLI-070]` A short form is one name, never a bundle.** `-ab` is the
option whose short form is `ab`; it is never `-a -b`. Bundling and
multi-letter short forms cannot both be unambiguous, and the multi-letter form
is what lets `--library` and `--listener` coexist in one binary. One letter
wherever it is uncontested; `-lib`/`-lis` only where both are present.

### The single declaration

An option is one record — long name, short form, whether it takes a value and
of what kind, its default, whether it is required, its one-line description —
and the parser, the `--help` text and the fallback value all read *that*. This
is `[GDE-ARC-033]` applied to a command line: the delay-trim limit had three
copies in two languages, and a usage string beside a parser is the same fault
in a new place. `relink --report` is the proof it was already happening — the
binary read the option and the usage string it printed did not mention it.

**Adding an option is one edit, to one table.** If it takes two, something is
wrong.

---

## 3. Which database is which

Two halves `[IMPL-DBSPLIT-025]`, so two names, and each program says which one
it actually opens rather than calling both `--db`:

| Option | What it is | Who takes it |
|---|---|---|
| `--listener PATH` | plays, preferences, programmes — the half nothing can rebuild `[REQ-LIB-160]` | `lempi`, `station`, `dircheck`, `mpd_fill`, `mpd_direct`, `mpd_session` |
| `--library PATH` | files, passages, flavor, cover art | all of the above that take both, plus `tagscan`, `flavorcheck`, `relink`, `import_bundle`, `mpd_map`, `mpd_watch` |

On an installation that has not split they are the same file, and a program
taking both defaults `--library` to `--listener`.

`mpd_fill`, `mpd_direct`, `mpd_session` and `mpd_watch` spelled this `--db`.
The old spelling is still read and warns.

**`--help` is the definition.** This document does not list the options; every
binary prints its own, and a guard checks that everything the repository types
at one of them is something it accepts (§6). A table here would be an
eighteenth copy.

---

## 4. The transition, and when it ends

**`[GDE-CLI-040]` The bare-argument form is still read, and says exactly what
to use instead.** Rejecting it immediately would have silenced three speakers
at their next reboot: the systemd units are on the appliances and are mostly
not tracked here, so a new binary and an old unit meet at a time nobody
chooses. Accepting it silently would have been worse than either — a fallback
that is invisible is the thing `[GOV-SRC-040]` exists to forbid, and nothing
would ever have migrated.

So a bare argument binds to the option it used to be, and the process prints
on **stderr**, where `journalctl` keeps it:

```
lempi: DEPRECATED command line -- this form will stop working [GDE-CLI-040].
lempi: use: lempi --listener /var/lempi/listener.db --library /srv/library/library.db --port 5720
```

The second line is built from the arguments actually received, not a generic
example, so migrating a unit is a copy rather than a guess. `journalctl -u
lempi | grep DEPRECATED` is therefore the fleet's migration checklist, and it
answers per host instead of from memory `[GDE-DEP-060]`.

**A token beginning with a dash is never bound this way.** That is what keeps
the original fault dead: `--help` is answered before anything else is
considered, and a mistyped `--prt` is refused rather than becoming a filename.

> **`[GDE-CLI-045]` Status: the shim is in, deliberately, and its removal
> condition is this.** Delete the `was_positional` handling from
> `player/src/cli.rs`, and the `.was(n)` calls from the tables, **once all
> four units in §5 use named options** — `bose`, `lempipi` (two: a base unit
> and a drop-in) and `lp3-wifi`. Not before. Two tests hold the door:
> `the_retired_positional_form_still_runs_and_warns_by_name` and
> `the_bare_argument_form_still_works_and_says_exactly_what_to_use_instead`
> both fail when the shim goes, which is the prompt to check this list first.
> Recorded 2026-09-20; unticked as of that date.

---

## 5. What a human must apply, per appliance

**`[GDE-CLI-080]` Nothing here has been deployed.** These units live on the
machines and are not in this repository; `build/deploy-appliance.sh` installs
the binary and does not manage units. Until someone applies these lines, each
node keeps working through §4's shim and says so in its journal every start.

| Host | Unit | Apply |
|---|---|---|
| `bose` | `/etc/systemd/system/lempi.service` — tracked here as [`BosePi/lempi-bose.service`](../BosePi/lempi-bose.service), already updated | ☐ |
| `lempipi` | base unit `/etc/systemd/system/lempi.service`, written by [`LempiPi/setup-lempipi.sh`](../LempiPi/setup-lempipi.sh), already updated | ☐ |
| `lempipi` | drop-in `/etc/systemd/system/lempi.service.d/mpd-guest.conf` — tracked as [`LempiPi/lempi-mpd-guest.conf`](../LempiPi/lempi-mpd-guest.conf), already updated | ☐ |
| `lp3-wifi` | `/etc/systemd/system/lempi.service` — tracked here as [`LempiPlay3/lempi.service`](../LempiPlay3/lempi.service) since 2026-09-21 `[LP3-REP-010]` | ☐ |

**lempipi reports two `ExecStart` lines because it has two.** The drop-in
blanks the base unit's with an empty `ExecStart=` and supplies its own; the
drop-in is what actually runs. Both need changing, because whichever survives
a future edit must be right.

The exact lines:

```ini
# bose
ExecStart=/usr/local/bin/lempi --listener /var/lempi/listener.db --library /srv/library/library.db --device hifiberry --mpd 127.0.0.1:6600 --mpd-root /srv/library/audio

# lempipi, base unit
ExecStart=/usr/local/bin/lempi --listener /srv/library/library.db --port 5720

# lempipi, drop-in mpd-guest.conf (this is the one that runs)
ExecStart=/usr/local/bin/lempi --listener /var/lempi/listener.db --library /srv/library/library.db --port 5720 --mpd 127.0.0.1:6600 --mpd-root /srv/library/audio

# lp3-wifi
ExecStart=/usr/local/bin/lempi --listener /var/lempi/listener.db --library /srv/library/library.db --port 5720
```

Each is `systemctl daemon-reload` then `systemctl restart lempi`, and on
`bose` the file must be written through both overlay layers
`[IMPL-BOS-185]` — use [`build/install-config.sh`](../build/install-config.sh),
and verify the durable copy, not the running one `[GDE-DEP-070]`.

---

## 6. What holds this together

Three guards read the repository rather than restating it, which is
`[GDE-ARC-031]`'s prescription applied here:

- **`every_option_the_repository_types_is_one_the_binary_accepts`** walks every
  `.md`, `.sh`, `.service`, `.conf`, `.html` and `.py` in the tree, finds each
  invocation of one of the seventeen, and checks every `--option` in it
  against that binary's table. Rename an option and the failure names the file
  and line. It asserts a floor on how many invocations it found, so a scan
  that reads nothing cannot pass by finding no fault `[GDE-ECHO-547]`.
- **`every_exec_start_in_the_repository_is_a_current_lempi_command_line`**
  parses every `ExecStart=` naming `lempi` and insists it runs with no
  deprecation. These are the lines a person copies onto an appliance, and a
  typo in one is a speaker that does not come back.
- **`every_binary_has_exactly_one_spec`** compares `src/bin/*.rs` against the
  table list, so a new binary cannot ship with its own hand-rolled parsing.

The tables live in the library, not beside each binary, so that the
feature-gated ones — `mpd_*`, `fbui`, `echoprobe` — are compiled and checked
by every `cargo test` whatever features are selected. `[GDE-ECHO-384]` is this
project's own account of what the alternative costs.

---

## 7. What this turned up

**`[GDE-CLI-050]` `lempi --list-devices` was documented for weeks and never
existed.** `[IMPL-AUD-010]` has told every appliance builder to *"identify
what exists first: `aplay -l`, and after setup `lempi --list-devices`"*. No
binary ever read the flag; the library call it needed,
`Output::list_devices`, was already there. Found by the §6 guard on its first
run, and implemented rather than deleted, because the document was asking for
the right thing — `[IMPL-AUD-010]`'s own hazard is a player bound to a dummy
sink, and this is how you see what the real ones are called.

`relink --report FILE` was the same shape in the other direction: read by the
binary, absent from the usage text it printed. It is in `--help` now by
construction.

Neither was found by reading the code. Both fell out of pinning one list
against the other.
