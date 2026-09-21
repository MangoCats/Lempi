# PI002: Building the Pi Zero 2 W Test Image

**Implementation Guide — Tier 3**

The first image, for a Pi Zero 2 W out of the box. Deliberately **not** the
appliance described in [PI001](PI001-image-and-partitions.md): no read-only
root, no three-partition layout, no overlay. Those come once something is
known to run.

> **Related:** [PI001 partitions](PI001-image-and-partitions.md) ·
> [IMPL001 appliance setup](IMPL001-appliance-setup.md)

---

## 0. What is already known

**`[PI2-KNOWN-010]` The player cross-compiles and its tests pass on aarch64.**
Verified 2026-08-16 through `build/Dockerfile.aarch64`:

- `cargo build --release --target aarch64-unknown-linux-gnu` — clean;
- **203/203 tests pass** under `qemu-aarch64`, including every SQLite path;
- the artifact is a 6.9 MB PIE needing only `libasound.so.2`, `libgcc_s`,
  `libm`, `libc`.

That last line matters: **nothing links a sound server.** `cpal` talks to
ALSA, and Bluetooth is reached by pointing ALSA at PipeWire rather than by
linking anything. So the audio decision in §3 is configuration, not a rebuild.

**`[PI2-KNOWN-020]` The audio path for these tests is Bluetooth**, with the
costs that implies. The Marshall Middleton is `20:64:DE:CF:F3:AD` and
negotiates **44,100 Hz, 16-bit stereo** — matching a library measured at 100%
44.1 kHz, so no resampling is required if the graph is configured not to
invent any (§4).

---

## 1. Build the binary

```sh
docker build -t lempi-arm64 -f build/Dockerfile.aarch64 build/
docker run --rm -v "/path/to/Lempi:/w" -w /w lempi-arm64 \
  cargo build --release --target aarch64-unknown-linux-gnu \
              --manifest-path player/Cargo.toml
```

On Git Bash prefix the `docker run` with `MSYS_NO_PATHCONV=1`, or the mount
path is mangled into a drive letter.

Artifact: `player/target/aarch64-unknown-linux-gnu/release/lempi`.

---

## 2. The card

**`[PI2-IMG-010]` Raspberry Pi OS Lite (64-bit), Bookworm.** 64-bit because
`library.db` is over a gigabyte and 32-bit address-space limits start to bite
exactly there; Lite because there is no display.

Use Raspberry Pi Imager and set, in its advanced options:

- hostname `lempipi`;
- enable SSH with a public key;
- Wi-Fi **client** credentials — for the test image the Pi joins your network
  rather than serving an access point. The AP `[PI-SET-010]` is a later step
  and would otherwise cut off the way in.

**`[PI2-IMG-020]` Size the card for the library.** Measured: 43.9 GB of audio
plus a 1,022 MB database. **128 GB, endurance-rated.** A 64 GB card leaves no
room to grow and none for the swap of an import.

---

## 3. Bluetooth audio

```sh
sudo apt install -y pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth
systemctl --user enable --now pipewire pipewire-pulse wireplumber
bluetoothctl
  power on
  scan on
  pair 20:64:DE:CF:F3:AD
  trust 20:64:DE:CF:F3:AD          # trust, or it will not auto-reconnect
  connect 20:64:DE:CF:F3:AD
```

**`[PI2-BT-010]` `trust` is the step people miss.** Without it the speaker
pairs, works, and never reconnects after a reboot — which on an appliance
reads as "Bluetooth is broken".

**`[PI2-BT-020]` BlueZ keeps its keys in `/var/lib/bluetooth`.** Harmless now;
`[PI-SET-040]` requires it to move to partition C once the root is read-only,
or every pairing is discarded at reboot.

---

## 4. Do not resample by accident

**`[PI2-RATE-010]` PipeWire's default graph clock is 48 kHz.** The library is
44.1 and the speaker accepts 44.1, so a default install resamples 44.1 → 48
for nothing, and the speaker may resample back. Configure it away:

```
# ~/.config/pipewire/pipewire.conf.d/10-rate.conf
context.properties = {
    default.clock.rate          = 44100
    default.clock.allowed-rates = [ 44100 ]
}
```

Verify with `pw-dump | grep -i clock.rate` and confirm the negotiated codec
with `bluetoothctl info 20:64:DE:CF:F3:AD`. **SBC is cheap to encode and AAC
is not**; with decode measured at 20–28× realtime headroom on this class of
core, the encoder is plausibly the largest cost in the audio path, so it is
worth knowing which one is running.

---

## 5. Install and run

```sh
sudo install -m755 lempi /usr/local/bin/lempi
sudo mkdir -p /srv/library /var/lempi
# copy library.db and the audio across; on a first test the single
# combined database is fine -- the split is PI001 §5 and comes later
lempi --listener /srv/library/library.db --port 5720
```

Then, in order of what it tells you:

1. **Does it play?** Anything else is premature until it does.
2. `systemd-analyze blame` — a boot-time baseline before optimising anything.
3. Watch `underrun_samples` in the UI. Non-zero under a crossfade is the
   headroom question answered on real hardware.
4. `ps -o rss= -C lempi` against the 30 MB of `[REQ-HW-010A]`, and again with
   PipeWire and BlueZ counted, since on 512 MB they are part of the budget.

---

## 6. As a service

```ini
# /etc/systemd/system/lempi.service
[Unit]
Description=Lempi
After=local-fs.target sound.target
# deliberately NOT network-online.target: audio depends on the library and the
# sound device, never on the network [REQ-HW-010B]

[Service]
ExecStart=/usr/local/bin/lempi --listener /srv/library/library.db --port 5720
Restart=always
User=lempi
Nice=-5

[Install]
WantedBy=multi-user.target
```

**`[PI2-SVC-010]` `Nice=-5`, not a real-time priority.** The mixer wants
scheduling preference; giving an untested service `SCHED_FIFO` on a
single-user-facing machine risks locking it up rather than making it smooth.
Real-time scheduling is worth revisiting once underruns are measured and
understood.

---

## 6a. What the first hardware run left as current design

**The unit now delays against a `Dummy Output`, and the player recovers from
an output error instead of playing on silently.**
`lempi-wait-sink` waits up to `LEMPI_SINK_WAIT` (default 45s) for a real sink
to appear, then starts the service anyway if none shows up — a speaker-less
appliance should still come up and say so in its interface, rather than a
service that refuses to run — and the player accepts a `--device` flag with
output-error recovery built into the engine. Both current fact, not
narrative.

What the first run actually found — a stream that played happily into silence
for two days before anyone noticed, and why — is history rather than setup
guidance: see [PI008 §1](PI008-appliance-bringup-history.md#1-the-dummy-sink-that-played-into-nothing).

## 7. What this image is not

No read-only root, no overlay, no three partitions, no privileged settings
helper, no access point. Every one of those is in [PI001](PI001-image-and-partitions.md)
and every one is easier to add to a machine already known to play music than
to debug simultaneously with the first attempt at audio.

The honest purpose of this image is to answer four questions: does it play,
how long does it boot, how much memory does it really take, and does the
Bluetooth path hold up under a crossfade.
