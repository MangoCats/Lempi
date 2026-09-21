# follower: a node heard together with another

Generic example. `speaker-a` is the master, `speaker-c` is this node, and
neither is a real host. `13491` is not the default port.

---

## What makes this shape different

Three options, and all three are measurements rather than preferences.

**`--follow speaker-a`** names the node whose programme this one plays
`[GDE-ECHO-330]`. It **seeds** the stored setting rather than overriding it
`[SPEC-ECHO-010]`: the real control is the settings page, and this flag is
for a node being set up before anybody can reach its interface. A flag that
silently overrode what a listener chose would be worse than no flag, which is
why this one option sits outside the stored-setting layer in both directions.

**`--echo-offset-frames`** is this node's own calibrated presentation offset
and **`--echo-fleet-min-frames`** the smallest in the fleet
`[LOG-ECHO-030]`. Both are measured with `delayprobe`, on the node, with the
player stopped — the device is exclusive:

```sh
ssh pi@speaker-c 'sudo systemctl stop lempi'
ssh pi@speaker-c 'delayprobe --alsa-device hw:CARD=sndrpihifiberry,DEV=0 --seconds 6'
ssh pi@speaker-c 'sudo systemctl start lempi'
```

`--alsa-device`, not `--device`: the player's `--device` is a
case-insensitive substring of a cpal device name, and this is a raw ALSA
device string. Different things do not share a name `[GDE-CLI-110]`, because
the environment variable is derived from the name and one exported
`LEMPI_DEVICE` would otherwise have meant both.

**Absent is not zero** `[GOV-SRC-040]`. With neither figure the node fills its
ring to capacity, which is correct for a node playing alone and for whichever
node holds the fleet's minimum. Only a node told *both* numbers holds
anything back. Leaving one out is not "half configured"; it is a different
and valid configuration, and the startup line says which is in force.

---

## Watching it without touching it

`echoprobe` reads a master's snapshot socket and prints what a follower
*would* do. It starts nothing, trims nothing and touches no audio:

```sh
echoprobe --master-url ws://speaker-a/ws --offset-frames 15676 --rate 44100
```

`--master-url`, not `--url`: `fbui`'s `--url` is the local player's own
socket, and pointing either at the other is quietly wrong `[GDE-CLI-110]`.
