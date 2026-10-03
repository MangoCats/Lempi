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

## One unit, one command line

[`lempi.service`](lempi.service) carries the node's whole command line, both
database halves named. Until 2026-10-02 this shape had two `ExecStart` lines:
a base unit naming a single database, and a drop-in that blanked it and
supplied the real one. A rebuild that lost the drop-in ran the catalogue as
the listener store. The player now refuses to start without both halves, and
the drop-in is gone.

```sh
scp fleet-example/speaker-plain/lempi.service pi@speaker-b:/tmp/
ssh pi@speaker-b 'sudo install -m644 /tmp/lempi.service /etc/systemd/system/lempi.service'
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
