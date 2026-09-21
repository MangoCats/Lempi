# source-host: the machine that builds and deploys

Generic example. `workshop` is not a real host and `13491` is not the default
port.

---

## What makes this shape different

**No player unit.** This machine builds the binary, holds the git checkout
and pushes to the speakers. It may also run a player for development, but
that one is started by hand — see [`../hand-run/`](../hand-run/README.md).

What it does have is [`lempi.env`](lempi.env): layer 3 for the whole machine
`[GDE-CLI-090]`. Every option of every binary has a variable there by
derivation — strip `--`, uppercase, hyphens to underscores, prefix `LEMPI_` —
so `--mpd-root` is `LEMPI_MPD_ROOT`. Nothing lists those names anywhere,
which is exactly why they cannot fall out of step with the options.

```sh
set -a; . fleet-example/source-host/lempi.env; set +a
lempi --listener /tmp/scratch.db --list-devices
```

---

## Building

Unset `CC` first. `libsqlite3-sys` fails to link if a global one is set, and
the error it produces does not point at the cause:

```sh
cd player && env -u CC cargo build --release
```

Cross-compiling for the appliances goes through Docker; see
[`build/README.md`](../../build/README.md).

---

## Deploying

`build/deploy-appliance.sh` installs the **binary** and does not manage
units. A unit file is a separate and deliberate act — the examples in the
sibling folders are what to apply, and on an overlay root they must reach
both layers `[IMPL-BOS-185]`.

Afterwards, **verify the durable copy rather than the running one**
`[GDE-DEP-070]`, and read back what each node actually resolved:

```sh
ssh pi@speaker-a 'journalctl -u lempi -b --no-pager | grep "lempi: --"'
```

---

## Finding nodes still on the old command line

The player reads the retired bare-argument form and says so at every start
`[GDE-CLI-040]`, which makes the migration list a query rather than a memory:

```sh
for h in speaker-a speaker-b speaker-c; do
    printf '%s: ' "$h"
    ssh "pi@$h" 'journalctl -u lempi -b --no-pager | grep -c DEPRECATED'
done
```

A node reporting `0` is migrated. A node reporting more prints the exact
corrected line immediately below its warning, ready to paste into that node's
unit — built from the arguments it was actually started with, so there is
nothing to work out.
