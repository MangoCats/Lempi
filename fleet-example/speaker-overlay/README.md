# speaker-overlay: an appliance whose root is an overlay

Generic example. `speaker-a` is not a real host and `13491` is not the
default port.

---

## What makes this shape different

**An ordinary write to `/` is lost at the next reboot.** The root is an
overlay: writes land in a tmpfs upper layer, survive a service restart, pass
every check anybody thinks to run — and are gone when the machine comes back
`[IMPL-BOS-185]`. Five days of player deploys went that way before the shape
was understood.

So installing [`lempi.service`](lempi.service) is **not** a file copy:

```sh
# the player binary, which knows about the overlay
build/deploy-appliance.sh <tag> pi@speaker-a

# any other file, including this unit -- writes BOTH layers
build/install-config.sh fleet-example/speaker-overlay/lempi.service \
    pi@speaker-a /etc/systemd/system/lempi.service

# a package, or anything needing a root environment
ssh pi@speaker-a sudo overlayroot-chroot
```

Then `systemctl daemon-reload` and `systemctl restart lempi`.

**Verify the durable copy, never the running one** `[GDE-DEP-070]`. Asking
the live process proves only what is running now, which is exactly the thing
that was already true before the reboot ate it. Read the file back through
the lower layer:

```sh
ssh pi@speaker-a sudo cat /media/root-ro/etc/systemd/system/lempi.service
```

**Say what you assume before acting** `[GDE-DEP-060]`. A line like
*"pi@speaker-a has an overlay root; this will persist through
/media/root-ro"* turns a wrong guess into a visible statement rather than a
silent success.

---

## Which partitions are writable

Not the root. On this shape the library and the listener state live on their
own mounts, which are ordinary and writable:

| Path | Holds | Survives a reboot |
|---|---|---|
| `/` | the system, the binary, the units | **no** — overlay |
| `/srv/library` | the catalogue and the audio | yes, read-only in normal operation |
| `/var/lempi` | the listener database | yes, writable |

That division is why the unit names `--listener /var/lempi/listener.db` and
`--library /srv/library/library.db` separately: the half that is written
continuously has to be on the mount that accepts writes, and the half that
is only read can sit on the one that does not.

---

## Configuration, in order

The unit's command line is layer 1 and wins. Below it, for the options that
have one, the stored setting from the web UI; then the environment; then the
built-in default `[GDE-CLI-090]`. `--listener` and `--library` skip layer 2
entirely, because they are what finds it `[GDE-CLI-095]`.

To move the port on this node without editing the unit, set the stored
setting from the settings page, or drop an `EnvironmentFile=` beside the unit
naming `LEMPI_PORT` — see
[`../source-host/lempi.env`](../source-host/lempi.env). The player prints
which one answered at every start.
