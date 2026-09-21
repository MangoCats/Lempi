# IMPL001: Raspberry Pi Zero 2W Appliance Setup

**Implementation Guide — Tier 3**

Step-by-step build of a Pi Zero 2W as a Lempi appliance. Implements the hardware and storage model in [embedded-hardware.md](embedded-hardware.md) and the requirements in [REQ002 §6](../docs/spec/REQ002-functional-requirements.md#6-appliance--hw).

> **Status, updated 2026-09-02:** this plan *has* since been run against real hardware — see [PI002](PI002-test-image-setup.md) through [PI007](PI007-mpd-on-the-appliance.md) for the actual build, Bluetooth pairing, thermal/CPU characterisation and MPD setup, all measured on a real Pi Zero 2W. One load-bearing decision below did not survive contact: §5's `bluez-alsa` choice was superseded by **PipeWire** (`[PI2-KNOWN-010]`) — see the note at §5. The rest of this document was written from specification ahead of that hardware work; each step still carries its own **Verify** line, and where the PI-series notes correct something here, that correction is noted inline rather than silently overwritten, per this project's own MuLibPlay lesson about notes that decay into scattered tips because nothing was checked back in.

---

## 1. Output Profile — Choose First

**`[IMPL-BOM-010]` The Pi Zero 2W has no analog audio output.** Only mini-HDMI and USB. This is the most consequential hardware fact and it must be settled before flashing.

**`[IMPL-PROF-010]` The output choice also fixes the boot profile.** They are one decision, not two: a Bluetooth sink must associate before audio can flow, so fast boot and Bluetooth are **alternatives rather than a compromise to be split** `[REQ-HW-114]`.

| Profile | Output | Boot | Extra userspace |
| :--- | :--- | :--- | :--- |
| **A — Bluetooth** ⭐ *tested first* | A2DP sink | **slow, accepted** `[REQ-HW-112]` | `bluez`, `bluez-alsa-utils` |
| **B — I2S DAC HAT** | GPIO DAC | fastest | none |
| **C — USB DAC** | USB audio | fast | none |
| **D — HDMI** | mini-HDMI | fast | none |

**`[IMPL-PROF-020]` Profile A is the initially tested channel.** Its startup delay is accepted **for this profile only** and must not become the project's boot standard `[REQ-HW-112]`. MuLibPlay needed a 5-second delay for exactly this reason, so the cost is known and inherent to the channel rather than to Lempi.

**`[IMPL-PROF-030]` Profiles B–D stay supported, not merely possible.** Lempi selects its output by name at runtime (`Output::open_device`), so switching profiles is configuration rather than a rebuild. Anything added for Bluetooth must be conditional: `bluealsa` and the association wait belong to Profile A alone, and enabling them for everyone would impose Bluetooth's boot cost on hardware that does not need it.

Also required: microSD (**A2-rated**, endurance-grade — this runs 24/7), a 5 V ≥2.5 A supply, and the music library.

## 2. Operating System

**`[IMPL-OS-010]` Raspberry Pi OS Lite (64-bit), Bookworm or later.**

- **Lite**, not Desktop: a desktop consumes over half of the 512 MB before Lempi starts.
- **64-bit**: matches the verified `aarch64-unknown-linux-gnu` build `[build/README.md]`. The Zero 2W's Cortex-A53 supports it.

> **If 32-bit is chosen instead**, the target triple becomes `armv7-unknown-linux-gnueabihf` and needs its own cross-build image — the same Dockerfile pattern with `gcc-arm-linux-gnueabihf` and `libasound2-dev:armhf`. The existing Pi (`bose.lan`) runs 32-bit, so do not assume the two machines share binaries.

**`[IMPL-OS-020]` Configure headless access in Raspberry Pi Imager before writing** (gear icon): hostname, SSH with public key, Wi-Fi credentials and country, locale. This avoids ever attaching a monitor — the Zero 2W's mini-HDMI is inconvenient and this is a permanently headless appliance.

> **Verify:** `ssh lempi@<hostname>.local` succeeds; `uname -m` reports `aarch64`.

---

## 3. Baseline Measurement — Before Changing Anything

**`[IMPL-BASE-010]`** Record the untouched baseline. Without it, later tuning cannot be shown to have helped.

```bash
free -m                      # total and used RAM
systemd-analyze              # boot time
systemd-analyze blame | head -20
df -h /
```

> **Verify:** record used RAM at idle and total boot time here. Expect roughly 40–70 MB used on Lite, and a boot in the tens of seconds. The `[REQ-HW-100]` budget is ≤150 MB **for Lempi**, so the OS baseline is what remains available.

---

## 4. Strip to Essentials

**`[IMPL-TRIM-010]`** Each removal must be justified by the appliance role — this is a single-purpose device with no local user.

```bash
# Profiles B-D ONLY. Under Profile A this is what makes audio work at all.
[ "$LEMPI_PROFILE" != A ] && sudo systemctl disable --now bluetooth
sudo systemctl disable --now triggerhappy avahi-daemon
sudo systemctl disable --now ModemManager wpa_supplicant@wlan1 2>/dev/null
sudo apt-get purge -y  # nothing to purge on Lite by default; confirm before adding
sudo systemctl mask systemd-networkd-wait-online.service   # audio must not wait for Wi-Fi
```

**`[IMPL-TRIM-020]` Use zram, not an SD swapfile.** Swapping to SD destroys cards and is slow enough to cause audio dropouts on a 512 MB machine.

```bash
sudo systemctl disable --now dphys-swapfile
sudo apt-get install -y zram-tools
echo -e "ALGO=zstd\nPERCENT=50" | sudo tee /etc/default/zramswap
sudo systemctl restart zramswap
```

> **Verify:** `swapon --show` lists `/dev/zram0` and no file-backed swap; `free -m` shows used RAM lower than baseline.

---

## 5. Audio — Profile A (Bluetooth)

> **Superseded 2026-09-02:** the `bluez-alsa` choice below was the pre-hardware plan. What was actually built and measured on the appliance is **PipeWire** — `pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth`, with ALSA pointed at PipeWire rather than at `bluealsa` (`[PI2-KNOWN-010]`, [PI002](PI002-test-image-setup.md) §0) — carried through pairing, characterisation and MPD setup in [PI002](PI002-test-image-setup.md)–[PI007](PI007-mpd-on-the-appliance.md). The footprint reasoning below did not hold up against a real device; PipeWire's measured cost on the appliance is recorded in [PI006](PI006-appliance-characterisation.md). The `bluez-alsa` steps are kept for their history, not as current instructions.

**`[IMPL-AUD-005]` What makes this a Bluetooth-only output player.** Recorded
2026-09-09 because two documents had reasoned from it without anyone writing
it down, and because it is the reason a whole class of fault is possible here
`[PI3-FOUND-110]`: on this appliance there is **no audible output at all
until a speaker connects**.

Nothing configures that. It falls out of the hardware and one overlay:

| | |
|---|---|
| **Board** | Raspberry Pi Zero 2 W — **no analog jack exists on this model**, so there is no headphone output to disable |
| **`config.txt`** | `dtparam=audio=on` and `dtoverlay=vc4-kms-v3d`, both stock |
| **Kernel command line** | `snd_bcm2835.enable_headphones=0 … enable_hdmi=1 enable_hdmi=0` — **emitted by the firmware**, not written in `cmdline.txt`; KMS takes HDMI audio from the legacy `bcm2835` path, so the later `=0` wins |
| **Result** | ALSA has exactly one card, `vc4hdmi`; PipeWire sees the device but yields **no sink** with nothing plugged into HDMI |

So a headless Zero 2 W with no display attached has precisely one route to a
listener, and it arrives late — after BlueZ connects, seconds to a minute into
the boot. **A player that starts before then has nowhere real to go**, which is
why `lempi-wait-sink` exists and why its failure mode was invisible for so
long: on a machine with a working analog or HDMI sink, a boot gate that
released early would still have made a noise.

Two consequences for anyone building another one. **Verify with `aplay -l`
rather than assuming**: a Pi with a jack, or a Zero 2 W with a display
attached, is *not* this profile — it has a real sink at boot, `lempi-wait-sink`
passes on it immediately, and the player can bind to it and play to nobody,
which makes `[PI3-FOUND-110]`'s fault *more* likely on better-equipped
hardware. And **nothing here belongs in `cmdline.txt`**: these parameters are
already correct, and on a board that does have a jack they would be the wrong
thing to copy.

**`[IMPL-AUD-010]`** Identify what exists first: `aplay -l`, and after setup `lempi --list-devices`. *(That flag was promised here and did not exist until 2026-09-20; it does now `[GDE-CLI-050]`.)`

**`[IMPL-AUD-050]` A2DP needs a bridge into ALSA.** Lempi's `cpal` backend speaks ALSA, and BlueZ alone does not expose an A2DP sink as an ALSA PCM. This corrects the "no PulseAudio, ALSA directly" guidance elsewhere in this document — that holds for Profiles B–D, **not** for Bluetooth. *(As shipped, the bridge is PipeWire, not the `bluez-alsa` steps immediately below — see the superseded note above.)*

```bash
sudo apt-get install -y bluez bluez-alsa-utils
```

`bluez-alsa` (`bluealsa`) was the plan here, preferred over PipeWire or PulseAudio purely on footprint: this is a 512 MB machine with a ≤150 MB budget for Lempi `[REQ-HW-100]`. PipeWire was chosen instead once real hardware was available — see the superseded note above.

**`[IMPL-AUD-060]` Pair and trust once**, so later boots reconnect without interaction:

```bash
bluetoothctl
> power on
> agent on
> scan on            # note the speaker's MAC
> pair    AA:BB:CC:DD:EE:FF
> trust   AA:BB:CC:DD:EE:FF     # trust is what makes reconnection automatic
> connect AA:BB:CC:DD:EE:FF
> quit
```

**`[IMPL-AUD-070]` Lempi waits for the sink rather than systemd doing it.** The unit must not block on Bluetooth `[IMPL-SVC-020]`; instead Lempi retries `open_device(Some("bluealsa"))` until the sink appears, then starts audio. Two reasons: the web UI and database come up during the wait rather than after it, and the delay stays contained in Profile A's configuration instead of the service definition.

> **Verify:** `aplay -D bluealsa /usr/share/sounds/alsa/Front_Center.wav` plays through the speaker. Record **power-on to first audio** — that number is Profile A's accepted cost `[REQ-HW-112]`, and the figure Profiles B–D are measured against.

### Profiles B–D

**`[IMPL-AUD-020]` I2S DAC HAT.** In `/boot/firmware/config.txt`, and no extra userspace:

```
dtparam=audio=off
dtoverlay=hifiberry-dac        # substitute the overlay for the specific HAT
```

**`[IMPL-AUD-030]` Pin the default device** in `/etc/asound.conf` so Lempi never depends on probe order:

```
defaults.pcm.card 0
defaults.ctl.card 0
```

**`[IMPL-AUD-040]`** For Profiles B–D, install neither PulseAudio nor PipeWire: `cpal` uses ALSA directly, and each additional layer adds latency and a failure mode. Disable Bluetooth entirely to claim the faster boot `[REQ-HW-114]`.

## 6. Storage Layout

**`[IMPL-STOR-010]`** Implements the 3-partition isolation model `[embedded-hardware.md]`. The Imager writes two partitions (boot, root); add the other two before first heavy use.

| Mount | Contents | Mode |
| :--- | :--- | :--- |
| `/` | OS, Lempi binaries | **read-only** `[IMPL-STOR-030]` |
| `/srv/music` | audio library | read-only in normal operation |
| `/var/lib/lempi` | `lempi.db`, exports, logs | read-write |

**`[IMPL-STOR-020]`** Keeping `lempi.db` on its own read-write partition means an unclean shutdown risks only that filesystem, and the class-D export `[SPEC-DF-094]` lives there too — so back it up **off the Pi**, since a card failure takes the partition and its backups together.

**`[IMPL-STOR-030]` Read-only root** via `sudo raspi-config` → Performance → Overlay File System. Enable the overlay **and** set the boot partition read-only.

> **Verify:** after reboot, `touch /test` fails; `mount | grep ' / '` shows `overlay`. Remember that from this point OS changes require disabling the overlay, rebooting, changing, and re-enabling — do all package installation *before* this step.

---

## 7. Lempi Service

**`[IMPL-SVC-010]`** Deploy the aarch64 binary built per [build/README.md](../build/README.md) to `/usr/local/bin/lempi`.

**`[IMPL-SVC-020]`** `/etc/systemd/system/lempi.service`. The unit is **identical across all four profiles** — the output difference lives in Lempi's configuration, not here `[IMPL-PROF-030]`. That is deliberate: putting a Bluetooth wait into the unit would impose it on hardware that does not need it.

```ini
[Unit]
Description=Lempi
# Deliberately NOT After=network-online.target, and NOT After=bluetooth:
# audio must start without waiting for Wi-Fi, and under Profile A the wait for
# the A2DP sink belongs inside Lempi [IMPL-AUD-070] so the web UI and database
# come up during it rather than after it.
After=sound.target
Wants=sound.target

[Service]
Type=simple
# Every option is named [GDE-CLI-020]. Output selection is --device NAME (a
# substring match against the audio device), omitted for the system default
# -- there is no --output flag or profile enum.
ExecStart=/usr/local/bin/lempi --listener /var/lib/lempi/listener.db --device ${LEMPI_DEVICE}
EnvironmentFile=/etc/lempi.conf
Restart=always
RestartSec=2
User=lempi
Nice=-5
# Modest priority, not real-time: a runaway RT thread on a single-purpose
# appliance can lock the machine out of SSH.
MemoryMax=200M

[Install]
WantedBy=multi-user.target
```

> **Corrected 2026-09-06, found building `bose`:** the `ExecStart` above no
> longer matches the binary — `--db`, `--music` and `--output` do not exist
> as flags. The actual, currently-deployed form, confirmed against `lempipi`'s
> real unit (`[PI002](PI002-test-image-setup.md)`, `[PI005](PI005-appliance-library.md)`):
> `ExecStart=/usr/local/bin/lempi --listener /srv/library/library.db --port 5720`
> — no `--music`/`--output` at all. **Renamed 2026-09-20**: that path was the
> positional argument until the whole fleet moved to named options `[GDE-CLI-020]`,
> and the old form is still read and warns `[GDE-CLI-040]`. The MPD guest path (used on
> `bose`, not here) adds `--mpd HOST:PORT --mpd-root DIR`
> [BosePi/BOSE003](../BosePi/BOSE003-build-procedure.md). This document's
> `--device` framing above is corrected to match the real flag name, but the
> `--db`/`--music`/`--output` shape was never real for this binary — recorded
> per this project's own discipline of not silently overwriting a decision
> once something built on it.

**`[IMPL-SVC-030]`** `MemoryMax=200M` turns the `[REQ-HW-100]` budget into an enforced limit rather than an aspiration: exceeding it kills the service and `Restart=always` recovers, which is loud and diagnosable instead of the machine silently thrashing into swap.

> **Verify:** `systemctl enable --now lempi`, then `systemd-analyze blame | grep lempi` and `systemctl show lempi -p MemoryPeak`.

---

## 8. Acceptance

**`[IMPL-ACC-010]`** The appliance is ready when all of these hold:

| Check | Requirement | Target |
| :--- | :--- | :--- |
| Peak RSS during playback | `[REQ-HW-100]` | ≤150 MB |
| Power-on to first audio, Profile A | `[REQ-HW-112]` | measure; accepted as-is |
| Power-on to first audio, Profiles B-D | `[REQ-HW-114]` | measure; must beat Profile A |
| 10 hard power cuts, no corruption | `[REQ-HW-120]` | database opens every time |
| Skip latency | `[REQ-AUD-110]` | ≤500 ms |
| 72 h unattended | `[GDE-PHS-020]` | no leak, no drift, no dropout |
| 244.9-minute DAO file plays | `[GDE-FBD-010]` | within the memory budget |

**`[IMPL-ACC-020]`** The last two are the ones that cannot be checked on a desktop and are the reason this hardware matters. `memcheck` measured 15.0 MB for the 244.9-minute file on x86; the Pi figure is what counts.

---

> **Split on 2026-09-10.** The open questions and the rebuild-from-repository
> audit are now [IMPL012](IMPL012-rebuilding-from-the-repository.md).
