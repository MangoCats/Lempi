# fleet-example: every shape a node can have

**Generic, tracked, and not anybody's fleet.** This is the worked example of
each configuration the project supports. `fleet/` beside it holds the real
household roster — captured `systemctl cat` output, real hostnames, real
paths — and is deliberately untracked. Read this one to learn the shape; read
that one to find out what is actually deployed.

Every host here is invented (`speaker-a`, `speaker-b`, `workshop`, `desk`) and
**every port is `13491`**, which is nobody's default. That is the point: a
reader who copies one of these files gets something that plainly needs
editing rather than something that half-works against a real installation.

---

## The five shapes

| Folder | Shape | What makes it different |
|---|---|---|
| [`speaker-overlay/`](speaker-overlay/) | appliance, **overlay root** | an ordinary write to `/` is lost at reboot; config must go through both layers `[IMPL-BOS-185]` |
| [`speaker-plain/`](speaker-plain/) | appliance, plain writable root | the base unit plus a drop-in — two `ExecStart` lines, and the drop-in wins |
| [`follower/`](follower/) | appliance that follows another | `--follow`, and the presentation-offset figures that go with it |
| [`source-host/`](source-host/) | the machine that builds and deploys | no player unit at all; an environment file instead |
| [`hand-run/`](hand-run/) | a laptop or desktop, run from a terminal | no systemd; everything on the command line or in the shell |

---

## What every one of them shows

**Every option is named** `[GDE-CLI-020]`. There are no bare arguments in any
file here. The player still reads the old positional form and warns, but
nothing in this folder teaches it.

**A value can come from four places** `[GDE-CLI-090]`, highest first:

1. the command line — `--port 13491`
2. the stored setting, in the listener database, set from the web UI
3. the environment — `LEMPI_PORT=13491`
4. the built-in default

The variable name is derived from the option: strip `--`, uppercase, hyphens
to underscores, prefix `LEMPI_`. So `--mpd-root` is `LEMPI_MPD_ROOT` and
`--listener` is `LEMPI_LISTENER`. Nothing lists these names anywhere; they
follow from the option, which is why they cannot fall out of step with it.

**Two options are outside layer 2, on purpose.** `--listener` and `--library`
name the database the stored settings live in, so they cannot be answered by
it `[GDE-CLI-095]`. They resolve from the command line, the environment and
the default only.

**The player says which layer answered.** On startup it prints a line for
every option that did *not* come from its built-in default:

```
lempi: --port = 13491, from $LEMPI_PORT
lempi: --listener = /var/lempi/listener.db, from the command line
```

That is `[GOV-SRC-040]` applied to a precedence chain: four sources for one
value is four chances for two of them to disagree quietly, so the resolved
one names itself rather than being inferred.

---

## Moving the port

Three ways, and none of them is editing a source file:

```sh
# once, for one run
lempi --listener /var/lempi/listener.db --port 13491

# for everything on this machine
export LEMPI_PORT=13491

# permanently for this node, from the web UI's settings page
#   (stored in the listener database; outranks the environment)
```

The built-in default under all three has exactly one definition, the
`default_port!` macro in [`player/src/cli.rs`](../player/src/cli.rs). It was
written out in eight places before 2026-09-20 `[GDE-CLI-100]`.

---

## Applying any of this

`build/deploy-appliance.sh` installs the binary and **does not manage units**.
A unit file is applied by hand, and on an overlay root it must reach both
layers — see [`speaker-overlay/README.md`](speaker-overlay/README.md). After
any change: `systemctl daemon-reload`, then `systemctl restart lempi`, then
**verify the durable copy and not the running one** `[GDE-DEP-070]`.
