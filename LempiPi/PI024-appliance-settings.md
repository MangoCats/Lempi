# PI024: Appliance Settings

**Appliance Record — what is tuned on the card, and why each one**

Split from [PI001](PI001-image-and-partitions.md) on 2026-09-10, which had reached 549 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [PI001](PI001-image-and-partitions.md) for the partition layout · [IMPL002](IMPL002-database-split.md) for the split itself

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-4 and §6-7 in [PI001](PI001-image-and-partitions.md), §5 and §5a in [PI023](PI023-the-database-split-on-disk.md), §5b in [PI024](PI024-appliance-settings.md).

## 5b. Appliance settings

> **Superseded 2026-09-06, mechanism only — the principles held.** This
> section's `hostapd.conf`/`wpa_supplicant.conf`-on-a-partition design
> predates this exact device. Checked live before anything was built: the
> real appliance runs NetworkManager, not hand-written network config
> files, so `[SPEC034]` builds the confirm-or-revert safety mechanism,
> the published default AP credential, and the "known networks" concept
> below on `nmcli`'s own connection profiles instead — see `[SPEC034]`
> for what was actually shipped. The table and `hostapd`/`wpa_supplicant`
> mechanics below are kept for their reasoning, not as current
> instructions — the same "correction filed, not silently overwritten"
> discipline `IMPL001`'s own PipeWire note already models.

**`[PI-SET-010]` Settings that only exist on the appliance.** The skip times
and the resume interval `[REQ-VIS-155]` are player settings and belong in
`player_state` on partition C. These are different: they configure the *host*,
not the player, and applying one means writing an OS config file and
restarting a service.

| Setting | Default | Applied by |
| :--- | :--- | :--- |
| Wi-Fi AP SSID | `Lempi` | `hostapd.conf`, restart `hostapd` |
| Wi-Fi AP password | `Lempi321` | `hostapd.conf`, restart `hostapd` |
| Wi-Fi band | `2.4` \| `5` \| `both` | `hostapd.conf` `hw_mode`/`channel` |
| Network mode | `ap` \| `client` \| `both` | `hostapd` / `wpa_supplicant` |
| Client SSID + password | none | `wpa_supplicant.conf` |
| Development mode | **off** | `sshd`, diagnostics |
| Bluetooth audio device for auto-connect | none | `bluetoothctl` / BlueZ D-Bus |
| Paired-device list maintenance | — | BlueZ; pair, trust, forget |

**`[PI-SET-012]` Three network modes, and they are not all simultaneous.**

| Mode | For | Default |
| :--- | :--- | :--- |
| **Access point** | direct connection, no infrastructure | on |
| **Client** | join a house network; reachable from anywhere on it | off |
| **Development** | SSH and diagnostics | **off** |

Access point and client are *not* freely combinable. One radio does one job:
concurrent AP+STA exists on some chips through `nl80211`, but both interfaces
must share a channel and the arrangement is fragile — which means the client
network dictates the AP's channel, and losing the client association can take
the access point with it. On a Pi Zero 2 W, treat them as **alternatives**,
and offer both at once only where a second interface is present, exactly as
`both` bands are treated `[PI-SET-032]`.

**`[PI-SET-014]` Every one of these settings can destroy the means of undoing
itself.** Switching to client mode with a mistyped password leaves an
appliance with no access point, on no network, with no screen. This is the
same hazard as an unhonourable band `[PI-SET-036]`, and it deserves a stronger
answer than a fallback, because here the configuration is *valid* — it simply
does not work.

**Confirm-or-revert.** Apply the new configuration, then require the browser
to re-connect and confirm within a timeout — five minutes is generous. If no
confirmation arrives, restore the previous configuration and restart the
services. The listener who mistyped a password sees the appliance come back on
its old settings rather than needing a card reader.

The listener confirms the selection explicitly — a button, not merely a page
that loaded. A browser can re-connect to a cached page without the appliance
being reachable at all, so the confirmation has to be a round trip the device
answers.

This is worth building once, in the helper, rather than per setting: it is the
only mechanism that makes a network change safe to attempt from the device
being reconfigured.

**`[PI-SET-016]` Development mode is off by default and says what it costs.**
It enables `sshd` and whatever diagnostics are useful. Two conditions:

- **Key-only, or a password the listener sets.** `[PI-SET-030]`'s published
  first-boot credential must never reach `sshd` — a known password on an
  appliance that may be on a house network is a different order of exposure
  from a known password on an access point in one room.
- **It survives reboot but is visible, on the main screen.** A mode that
  quietly stays on is worse than one that must be re-enabled, and a state
  shown only on the settings page is a state nobody checks. Two signals, both
  driven by `dev_mode` in the snapshot: a **notation beside the settings gear**
  that names it, and the **ground shifted from dark grey to dark wine red**.
  The badge says which mode; the colour says there is one, from across the
  room and without reading. *Implemented in the Lempi skin 2026-08-16; the
  flag is always false until the appliance helper sets it.*

**`[PI-SET-020]` These need a privileged helper, and that is the design
problem.** The player runs unprivileged and partition A is read-only. So the
web UI cannot write `hostapd.conf` directly: it must hand a *validated*
request to a small root helper over a socket, which writes the file and
restarts the unit. The helper's job is to accept a fixed vocabulary and refuse
everything else — a web form that can write arbitrary text into a system
config as root is a remote shell with extra steps.

**`[PI-SET-030]` The default AP password is published in this repository, so
it is not a secret.** `Lempi321` is a *first-boot* credential, and the setup
page must say so plainly and invite a change. An appliance shipping a known
password on an open access point is a fair description of the problem, not a
convenience.

**`[PI-SET-032]` The band setting is limited by the radio, not by hostapd.**
The Pi Zero 2 W's wireless part is 2.4 GHz only — it has no 5 GHz radio at
all. On the primary target the setting therefore has one legal value, and the
interface must say so rather than offer a choice that cannot be honoured.

On a Pi 3B+/4/5 both bands exist, but **`both` is not a single-radio
capability**: one radio serves one band at a time. Simultaneous dual-band
needs a second interface — a USB adapter — with its own `hostapd` instance.

So the setting is offered as three values, and what is *selectable* is decided
at runtime from the adapter's capabilities:

- `2.4` — always available;
- `5` — only where the hardware has a 5 GHz radio;
- `both` — only where a second wireless interface is present.

A stored value the current hardware cannot honour falls back to 2.4 GHz and
says so, rather than leaving `hostapd` failing to start with no access point
and no way in `[PI-SET-036]`.

**`[PI-SET-035]` Not partition B, and the reason is that the risk was never
about the partition.** Putting `hostapd.conf` on B looks attractive — B is
written rarely and attended, which is the same profile as an AP config change.
But a single small config file is made safe by *how* it is written, not by
where it lives: write a temporary file, `fsync`, `rename`. Rename is atomic,
so the reader sees the old file or the new one and never a half-written one.
`backup.rs` already does exactly this for the same reason.

Once the write is atomic, B's advantage disappears and two disadvantages
remain:

- **It couples the network to the library.** B is a gigabyte of ext4 that must
  mount before `hostapd` could read its config. If B is corrupt — the case we
  explicitly plan to survive by rebuilding from Vipunen — there would be no
  access point, and therefore no way to reach the appliance to fix it. The
  recovery path must not depend on the largest thing that can break.
- **It widens the wrong window.** Changing an SSID would mean remounting the
  entire library partition read-write for a reason that has nothing to do with
  music.

**`[PI-SET-036]` So: a default on A, an override on C.** The stock
`hostapd.conf` ships on the read-only system partition and is always present.
The helper writes an override to C, atomically. At boot, use the override if
it exists and parses; otherwise fall back to A's default.

That gives the recovery property the appliance actually needs. Reinitialising
C is already a factory reset `[PI-DB-035]`, and under this arrangement it
restores the *published* credentials `[PI-SET-030]` — so a listener who has
forgotten what they set can always get back in by clearing the partition the
design already treats as expendable.

**`[PI-SET-037]` The override does not appreciably delay the interface.**
Reading it costs a `stat` and a few hundred bytes; what it adds is an ordering
dependency — `hostapd` must wait for C to be mounted. Mounting a small
filesystem on the same card is tens of milliseconds, against `hostapd`'s own
bring-up of the radio, beacon and DHCP server, which is seconds. The override
is noise by two orders of magnitude.

Two things keep it that way:

- **`nofail` on the C mount, with a short timeout.** The one case that could
  actually cost time is `fsck` on an unclean C, and it must not be permitted
  to hold the boot. If C is not there promptly, boot proceeds on A's default
  and the access point comes up with the published credentials — which is the
  same recovery path as a factory reset.
- **Audio does not wait for any of this.** `[REQ-HW-110]`'s best-effort budget
  is the audio path's, and it depends on partition B and the sound device, not
  on the network. The web interface is explicitly allowed to arrive later
  `[REQ-HW-110]`; a listener hears music before a browser could have
  connected either way. *(`REQ-HW-010B` renumbered/renamed — see the
  correction at `[PI-A-020]`.)*

**`[PI-SET-040]` Bluetooth pairing is stateful and belongs on partition C.**
BlueZ keeps its keys under `/var/lib/bluetooth`, which must therefore be
bind-mounted to C like `/var/log` `[PI-A-020]` — otherwise every pairing is
forgotten at reboot, since the overlay is discarded.

**`[PI-SET-050]` Tone control is Lempi's own, not the speaker's.** No
Bluetooth profile carries tone settings: A2DP carries audio and AVRCP carries
transport and metadata. A speaker's bass and treble are reachable only through
whatever proprietary protocol its own app speaks. So if Lempi is to offer tone
control, it belongs in the player's signal path before transmission, where it
works with every speaker rather than one model.

---

