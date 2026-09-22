# BOSE001: What Is on `bose` Today

**Measurement — Tier 1 · surveyed read-only on `pi@bose`, 2026-08-23**

A reconnaissance of the second appliance candidate before anything is built for
it. Nothing here was changed; every figure was read from the running machine.

`bose` is not a smaller `lempi02w`. It is a **Pi 4 with a real DAC**, twice the
memory, a 44 GB library already on it, and MuLibPlay still playing — and it is
running a 32-bit OS with its root overlay in RAM. Those four facts decide most
of [BOSE002](BOSE002-image-build.md) and [BOSE003](BOSE003-build-procedure.md).

> **Related:** [PI001](../LempiPi/PI001-image-and-partitions.md) for the A/B/C partition
> design this applies · [PI006](../LempiPi/PI006-appliance-characterisation.md) and
> [PI007](../LempiPi/PI007-mpd-on-the-appliance.md) for what the same software costs on
> `lempi02w`

---

## 1. The machine

| | `bose` (`rpi128`) | `lempi02w`, for comparison |
| :--- | :--- | :--- |
| Board | **Pi 4 Model B rev 1.1** (`b03111`) | Pi, 4 cores |
| Memory | **1,884 MiB**, no swap | 464 MB |
| OS | Raspbian 11 bullseye, **32-bit `armv7l`** | Raspberry Pi OS, `aarch64` |
| Kernel | 5.15.56-v7l+ | 6.12.93 |
| Card | SanDisk `SN128`, 119 GB, made 03/2019 | — |
| Network | `wlan0` 192.168.67.21 — **wireless** | wired |
| Thermal | 60.3 °C, `throttled=0x0` | 44 °C |
| Uptime | 4 days 21 h | — |

**`[PI-BOS-010]` It is 32-bit, and that is the one fact that costs work.**
`armv7l` userspace on a board that is perfectly capable of `aarch64`. Every
Lempi binary this project builds targets `aarch64-unknown-linux-gnu` and is now
exercised daily on `lempi02w`; targeting `bose` as it stands would mean adding
and maintaining an `armv7-unknown-linux-gnueabihf` toolchain for one machine.

Since the deliverable is a **new image anyway**, the answer is to install a
64-bit OS and reuse the toolchain that already works. This also finally retires
`[PI-IMG-030]`'s "no ARM64 build has been produced or run" — one has, and it has
been running for a day.

---

## 2. The audio path, which is why this machine is interesting

**`[PI-BOS-020]` A HiFiBerry DAC+ Pro on I²S — not Bluetooth, not HDMI.**

```
card 1: sndrpihifiberry [snd_rpi_hifiberry_dacplus]
        device 0: HiFiBerry DAC+ Pro HiFi pcm512x-hifi-0
```

The driver stack, from the codec upward:

| Module | What it is |
| :--- | :--- |
| `snd_soc_pcm512x` + `snd_soc_pcm512x_i2c` | TI PCM512x codec driver; control over I²C |
| `regmap_i2c`, `i2c_bcm2835` | the control path to the chip |
| `snd_soc_bcm2835_i2s` | the SoC's I²S controller — the audio path |
| `snd_soc_hifiberry_dacplus` | the machine driver tying the two together |
| `snd_soc_core`, `snd_pcm`, `snd_pcm_dmaengine` | ALSA SoC underneath all of it |

Enabled by `dtoverlay=hifiberry-dacplus` in `/boot/config.txt`, with HDMI audio
deliberately off (`dtoverlay=vc4-kms-v3d,audio=off`). `i2cdetect` shows **`UU` at
0x4d** — the codec, claimed by a driver rather than merely present.

Currently running at `S16_LE`, **44,100 Hz**, 2 channels, `MMAP_INTERLEAVED`,
period 661 / buffer 2644 frames — PulseAudio's choice of period, not a limit of
the hardware.

**`[PI-BOS-030]` It has a real hardware mixer, and that changes the volume
answer.**

```
Simple mixer control 'Digital'
  Limits: Playback 0 - 207
  Front Left/Right: 207 [100%] [0.00 dB] [on]
```

`lempi02w` reaches a Bluetooth speaker and had to be given MPD's **software**
mixer, because there was no hardware one and nothing else could set the volume
while MPD played `[SPEC-MPD-140]`. Here the PCM512x offers 208 steps in the
chip. `mixer_type "hardware"` is available on `bose` and is the better answer:
it costs no arithmetic on the samples and is what the DAC is for.

> **And the seek fault must be re-measured, not assumed.** `[SPEC-MPD-135]`
> works around an output that stops carrying samples after a `seekid` — measured
> against **PipeWire feeding a Bluetooth sink** `[PI-CHR-100]`. `bose` has a
> local I²S card and may reach it through plain ALSA with no PipeWire at all, in
> which case the fault may not exist here. The workaround is harmless if
> unnecessary; believing it necessary without checking would repeat the mistake
> recorded in `[PI-CHR-095]`.

---

## 3. What is running on it now

**`[PI-BOS-040]` MuLibPlay is still the player here**, pid 956, listening on
port 1160, with its 95 MB `mulib.db` in `/home/pi`. This is the predecessor
Lempi succeeds, on the hardware it was built for.

The audio stack is bullseye's split arrangement: **PulseAudio owns the DAC**
(pid 768 holds `/dev/snd/pcmC1D0p`), while `pipewire` and
`pipewire-media-session` run alongside handling video. Not the PipeWire-for-
everything arrangement `lempi02w` uses.

It is also a **full desktop install**, not an appliance: `lightdm`, VNC on
:5900, CUPS with `cups-browsed`, `ModemManager`, `udisks2`, `glamor-test`,
`epmd`. None of that belongs on a machine whose job is to play music, and all of
it writes.

---

## 4. The overlay, and where the RAM went

**`[PI-BOS-050]` The root is already read-only with a RAM overlay — and the RAM
overlay is the problem, not the solution.**

```
overlay / overlay rw,noatime,lowerdir=/lower,upperdir=/upper/data,workdir=/upper/work
boot=overlay ... root=PARTUUID=f37e5e32-02
/dev/mmcblk0p1 /boot vfat ro
```

This is `raspi-config`'s Overlay File System, exactly as `[PI-A-010]`
recommends. What the survey adds is the number `[PI-A-020]` predicted and could
not supply:

| | |
| :--- | ---: |
| Overlay upper (tmpfs, half of RAM) | 943 MB |
| **Consumed after 4 days 21 h** | **438 MB — 47 %** |
| `free` confirms it as shared/tmpfs | 482 MB |
| Left available to the system | 901 MB |

**Nearly a quarter of the machine's memory is holding writes that will be
discarded at the next reboot.** On a 464 MB `lempi02w` this arrangement would
have exhausted the machine; here it merely wastes half a gigabyte.

**`[PI-BOS-060]` And the data being discarded is the data that matters.**
`mulib.db` is 95 MB and was modified three minutes before this survey — it is
being written continuously, into the overlay, in RAM. **Every play MuLibPlay has
recorded since the last boot is lost when the power goes.** That is precisely
the loss `[PI-C-020]` calls out as irreplaceable, happening now, on this
machine, to the predecessor's data.

This single finding is the strongest argument for the whole A/B/C split: the
overlay is a good answer for the OS and a silently terrible one for state.

---

## 5. Storage and the library

| | |
| :--- | ---: |
| Card | 119.1 GB (`mmcblk0`) |
| `p1` `/boot` | 256 MB vfat, mounted **ro** |
| `p2` root (overlay lower) | 127.6 GB ext4 |
| Used on `p2` | **58.5 GB** |
| Free on `p2` | **69.1 GB** |
| **Music library** `/home/pi/Music` | **44 GB**, 7,238 files |
| of which mp3/flac | 5,819 |

So roughly 14 GB of OS and desktop sits beside 44 GB of music. The library is
comparable to `lempi02w`'s (5,705 files) but stored at full size rather than as
the trimmed appliance copy.

**No USB block devices are attached**, and `BOOT_ORDER` was not readable from
the current EEPROM config, so a USB-boot layout is not assumed.

---

## 6. What this survey does not establish

- **Whether the seek fault `[SPEC-MPD-135]` exists on a local I²S card.**
  Measurable only after MPD is installed here.
- **The DAC's full rate and format range.** `--dump-hw-params` could not run:
  PulseAudio holds the device. The PCM512x family supports well beyond 44.1 kHz
  / 16-bit, but that is the datasheet talking, not this machine.
- **SD card wear.** The card exposes no `life_time` attribute — it is a consumer
  card, and its remaining endurance is unknowable from software.
- **What `bose` sounds like.** No listening test was made; this was a read-only
  survey and the machine is somebody's running music player.

---

**Traceability:** `[PI-BOS-010..060]` · supplies the RAM figure `[PI-A-020]`
predicted · retires `[PI-IMG-030]` · plan in
[BOSE002](BOSE002-image-build.md), procedure in [BOSE003](BOSE003-build-procedure.md) ·
design in
[PI001](../LempiPi/PI001-image-and-partitions.md)
