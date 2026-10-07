# IMPL019: Native Bluetooth Controls

**Implementation Plan — Tier 3 · written 2026-10-06**

How speaker-side transport controls are integrated directly into the Lempi player binary, replacing the legacy standalone `lempi-rocker.sh` characterisation script and providing universal AVRCP Play/Pause and Skip support across all Bluetooth playback appliances.

> **Related:** [PI021](../LempiPi/PI021-interference-and-the-antenna.md) · [APP-BT-010](../appliance/README.md) · [REQ003](spec/REQ003-audio-playback.md) · [SPEC042](spec/SPEC042-framebuffer-implementation-plan.md) · [GUIDE033](GUIDE033-the-player-without-an-appliance.md)

---

## 1. Background & Problem Statement

**`[IMPL-BT-010]` Speaker-side transport controls are an appliance capability, not a single node's tool.**
Early exploration on `lempi02w` ([`PI021`](../LempiPi/PI021-interference-and-the-antenna.md)) measured the five gestures of the Marshall Middleton (`[PI3-ROCKER-010]`) and prototyped transport actions in a shell script (`lempi-rocker.sh`). That prototype was never managed as a systemd service, relied on external tools (`evtest`, `sed`, `awk`, `curl`), retained obsolete radio-switching logic (`wifi_down`) from a withdrawn hypothesis (`[PI3-ROCKER-020]`), and maintained a private `PLAYING=1` shell variable that diverged whenever playback state changed elsewhere.

Per `[APP-BT-010]`, Bluetooth output is a standard role shared across multiple nodes (`lempi02w`, `lp3-wifi`). Transport controls on connected speakers or automotive adapters (such as the Marshall Middleton rocker or the JOYROOM FM transmitter) arrive over standard Bluetooth AVRCP. Integrating event handling natively into `lempi-player` eliminates shell fragility, guarantees atomic Play/Pause toggling against the engine's true state, and normalises transport controls across all Bluetooth playback devices.

---

## 2. Architectural Design & Boundaries

**`[IMPL-BT-020]` The player hosts an in-process Linux input reader.**
A new module, `player/src/avrcp.rs`, is compiled into `lempi-player` on Linux appliances (`#[cfg(all(target_os = "linux", feature = "appliance"))]`):

* **Single Source of Truth**: Transport actions are dispatched directly as engine commands. Play/Pause toggle dispatches an atomic `Command::TogglePlayPause` into [`EngineHandle`](../docs/architecture.md#2-the-spine), where the engine flips state and publishes synchronously, eliminating snapshot race conditions and stale state checks on the input thread.
* **Direct Command Path**: Dispatches `Command::TogglePlayPause`, `Command::Play`, `Command::Pause`, and `Command::Skip` directly into the engine channel.
* **Host Portability**: Non-Linux builds (Windows host, Android) and non-Bluetooth appliances (`bose`, which outputs to an I²S DAC) compile without the worker loop, preserving `[GUIDE033]`'s host independence.

```
 Bluetooth Speaker / Adapter (AVRCP Controller)
              │
              ▼ (Bluetooth AVRCP Pass-Through)
 BlueZ / Linux Kernel (uinput driver)
              │
              ▼ (/dev/input/eventX: EV_KEY)
 lempi-player: avrcp.rs (in-process worker)
              │
              └── handle.send(Command::TogglePlayPause | Play | Pause | Skip)
```

---

## 3. Dynamic Hotplug & Connection Lifecycle

**`[IMPL-BT-030]` Resilient discovery across speaker power and ignition cycles.**
BlueZ creates the AVRCP `uinput` keyboard node only when an audio profile connects, and destroys it on disconnect (`[PI3-ROCKER-030]`):

* **Path Discovery**: Scans `/proc/bus/input/devices` for records where `Name` contains `"AVRCP"` (case-insensitive) and extracts the active `Handlers=... eventX` entry.
* **Never Cache Node Paths**: On reconnection (e.g. vehicle ignition cycle or speaker wake-up), BlueZ frequently numbers the new device under a different index (e.g. `event2` → `event3`). Discovery rescans dynamically on each connection cycle.
* **Detachment Triggers**: When a speaker turns off or goes out of range, `libc::poll` flags detachment via `POLLHUP`, `POLLERR`, or `POLLNVAL` in `revents`, or reads on `/dev/input/eventX` yield `ENODEV` (Error 19) or `Ok(0)` (EOF). All are treated as clean detachment signals, closing the open file descriptor and returning to the discovery loop without busy-polling.
* **Permission Resilience**: If `/dev/input/eventX` returns `EACCES` (Permission denied, e.g. when run unprivileged during testing), the worker logs a rate-limited warning (`tracing::warn`) and waits for the next cycle without panicking.

---

## 4. Worker Loop & Event Parsing

**`[IMPL-BT-040]` Interruptible non-blocking worker with contact debouncing.**

* **Interruptible Non-blocking I/O**: The background worker thread (`lempi-avrcp`) opens the event device in non-blocking mode and uses `libc::poll` with a 500 ms timeout against a shutdown signal. In the discovery loop, sleep intervals between rescans use a timed wait on the shutdown signal, ensuring the thread exits cleanly within milliseconds when `Player::shutdown()` is invoked.
* **Binary Event Unpacking**: Safely unpacks standard Linux 24-byte `struct input_event` (`tv_sec: i64, tv_usec: i64, type: u16, code: u16, value: i32`), matching `fbui.rs`'s ABI-stable parser.
* **Autorepeat & Release Filtering**: Evaluates only key-down events (`value == 1`). Key releases (`value == 0`) and autorepeat events (`value == 2`) are ignored.
* **Hardware Debounce**: Enforces a 250 ms debounce guard between dispatched actions to suppress mechanical switch contact bounce.

---

## 5. Keycode Mapping Policy

**`[IMPL-BT-050]` Standard AVRCP keycode assignments.**

| Key Category | Keycodes | Behavior |
| :--- | :--- | :--- |
| **Play/Pause Toggle** | `KEY_PLAYCD` (200), `KEY_PLAYPAUSE` (164), `KEY_PAUSECD` (201) | Dispatches atomic `Command::TogglePlayPause` (engine flips state and publishes) |
| **Discrete Play** | `KEY_PLAY` (207) | Dispatches `Command::Play` (idempotent, no-op if playing) |
| **Discrete Pause** | `KEY_PAUSE` (119) | Dispatches `Command::Pause` (idempotent, no-op if paused) |
| **Skip** | `KEY_NEXTSONG` (163), `KEY_FORWARD` (159) | Dispatches `Command::Skip` |
| **Previous / Back** | `KEY_PREVIOUSSONG` (165), `KEY_BACK` (158) | Explicit no-op (`None`), logged at trace level `[PI3-ROCKER-010]` |

Lempi playback has no "previous track" or "stopped" state (`[REQ-AUD-142]`). Mapping Previous/Back to a no-op honours the architectural invariant while preventing unexpected errors on car adapters with physical `<<` buttons.

---

## 6. Appliance Service & Deployment Changes

* **Group Permissions**: Add `SupplementaryGroups=input` to `LempiPlay3/lempi.service`, `fleet-example/speaker-plain/lempi.service`, and `LempiPi/setup-appliance.sh`'s template, granting `User=pi` access to `/dev/input/event*`.
* **Script Retirement**: Remove the copy block for `lempi-rocker.sh` from `setup-appliance.sh` and retire `/usr/local/bin/lempi-rocker`. Archive or remove `LempiPi/lempi-rocker.sh`.
* **Overlay Nodes**: Changes to `lempi.service` are deployed via `setup-fleet.sh --go` or `setup-lp3.sh --go` to persist across both tmpfs and lower durable layers (`[APP-SET-030]`).

---

## 7. Verification Plan

* **Automated Unit Tests (`player/src/avrcp.rs`)**:
  * Binary unpacking of mock 24-byte `input_event` buffers.
  * Keycode mapping to commands (`TogglePlayPause`, `Play`, `Pause`, `Skip`, `None`).
  * Engine `TogglePlayPause` execution and state transition verification.
  * Debounce suppression on rapid repeated events (< 250 ms).
  * Filter verification: release (`value=0`) and repeat (`value=2`) ignored.
  * Extraction of `eventX` paths from realistic `/proc/bus/input/devices` fixtures.
* **Compilation & Linting**:
  * `cargo test -p lempi-player avrcp` passes.
  * `cargo clippy --all-targets` clean.
  * `python tools/check_docs.py --strict` passes with 0 errors.
* **Manual / Appliance Verification**:
  * Validate Centre press toggles Play/Pause on Marshall Middleton.
  * Validate Right arrow advances passage via Skip crossfade.
  * Power-cycle speaker and confirm event listener rebinds to the newly assigned event index.
