# hand-run: a laptop or desktop, started from a terminal

Generic example. `13491` is not the default port.

---

## What makes this shape different

**No systemd, no unit file.** The whole configuration is the command line and
the shell, which makes this the clearest place to watch the four layers
interact `[GDE-CLI-090]`.

[`run.sh`](run.sh) exports `LEMPI_PORT=13491` and then passes `--port 13492`
anyway. That is deliberate: the command line outranks the environment, and
the player says which won rather than leaving it to be worked out.

```
lempi: --port = 13492, from the command line
lempi: --listener = /home/you/music/listener.db, from the command line
```

Drop the `--port` from the command and the same line reads:

```
lempi: --port = 13491, from $LEMPI_PORT
```

Unset the variable too and the line disappears altogether, because a value
that came from its built-in default is not worth reporting `[GOV-SRC-040]`.
What the report is for is the surprising case, not a wall of everything.

---

## Discovering the options

Every binary prints its own, and that is the definition. No document lists
them, here or anywhere: a list beside the code is the copy that goes stale
`[GDE-ARC-033]`.

```sh
lempi --help
station --help
relink --help
```

Every option is named; there are no bare arguments `[GDE-CLI-020]`. Each has
a short form shown beside the long one, and `-ab` is the option called `ab`
rather than `-a -b` — nothing bundles `[GDE-CLI-070]`.

---

## Two databases

Every installation is split `[IMPL-DBSPLIT-025]`, and the player refuses to
start unless both halves are named:

```sh
lempi --listener ~/music/listener.db --library ~/music/library.db --port 13491
```

The listener half is written continuously and the catalogue is attached
read-only; passing them the wrong way round bootstraps listener tables into
the catalogue, which is a mess to undo.

---

## A quick way to see a layer win

```sh
lempi --listener /tmp/scratch.db --list-devices            # default port
LEMPI_PORT=13491 lempi --listener /tmp/scratch.db --help   # help wins over everything
```

`--list-devices` and `--help` both answer and stop, so neither opens a
database — useful for poking at configuration on a machine whose library you
would rather not touch.
