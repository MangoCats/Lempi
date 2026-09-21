# speaker-plain: an appliance with an ordinary writable root

Generic example. `speaker-b` is not a real host and `13491` is not the
default port.

---

## What makes this shape different

**A write to `/` stays written.** No overlay, so installing a unit really is
a file copy. That is the whole difference from
[`../speaker-overlay/`](../speaker-overlay/README.md), and it is worth saying
out loud per node rather than remembering which is which `[GDE-DEP-060]`.

---

## Two ExecStart lines, and only the second runs

`systemctl show lempi -p ExecStart` on a node of this shape reports **two**
commands. That is not a fault:

1. [`lempi.service`](lempi.service) — the base unit, as a setup script
   writes it, naming a single database.
2. [`lempi.service.d/mpd-guest.conf`](lempi.service.d/mpd-guest.conf) — a
   drop-in. Its empty `ExecStart=` discards the base unit's line, and the one
   after it is what actually runs.

**Both are kept correct.** The drop-in is what runs today; the base unit is
what a rebuild from the repository would produce, and a node that came up on
it would be reading its catalogue as its listener store. That has happened.

```sh
scp fleet-example/speaker-plain/lempi.service pi@speaker-b:/tmp/
ssh pi@speaker-b 'sudo install -m644 /tmp/lempi.service /etc/systemd/system/lempi.service'
ssh pi@speaker-b 'sudo mkdir -p /etc/systemd/system/lempi.service.d'
scp fleet-example/speaker-plain/lempi.service.d/mpd-guest.conf pi@speaker-b:/tmp/
ssh pi@speaker-b 'sudo install -m644 /tmp/mpd-guest.conf /etc/systemd/system/lempi.service.d/'
ssh pi@speaker-b 'sudo systemctl daemon-reload && sudo systemctl restart lempi'
```

Then check what it settled on, rather than what you meant:

```sh
ssh pi@speaker-b 'journalctl -u lempi -b --no-pager | grep "lempi: --"'
```

That is the provenance report `[GOV-SRC-040]`: one line per option whose
value did not come from the built-in default, naming the layer that supplied
it. An empty report means everything is at its default, which is itself worth
knowing.

---

## Waiting for a sink

`lempi-wait-sink` runs before the player here and not on the overlay example,
because this shape's speaker arrives over Bluetooth and may not exist at
boot. Without it the player binds whatever ALSA offers — often a dummy sink —
and plays perfectly into nothing while reporting itself healthy
`[IMPL-AUD-010]`. `lempi --list-devices` prints what is actually there.
