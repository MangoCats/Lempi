# IMPL021: Per-Output Volume Implementation Plan

**Implementation Plan — Tier 3 · written 2026-10-07 · for `[SPEC-POV-010]`**

Technical implementation plan for associating independent output volume settings with physical DAC and Bluetooth speaker outputs, providing preset pinning ("remember this volume"), automatic last-used tracking, user-directed DAC selection, and configurable Bluetooth connection timeout with automatic DAC fallback.

> **Related:** [SPEC060](spec/SPEC060-per-output-volume.md) `[REQ-POV-010..040]`, `[SPEC-POV-010..060]` ·
> [REQ003](spec/REQ003-audio-playback.md) `[REQ-AUD-152]` ·
> [REQ006](spec/REQ006-visibility-words-and-the-rest.md) `[REQ-VIS-155]` ·
> [SPEC011](spec/SPEC011-audio-path-supervisor.md) `[SPEC-APS-060]` ·
> [PI022](../LempiPi/PI022-the-players-speaker-contract.md) `[PI3-API-010]`

---

## 1. Architecture Overview

Currently, `PlayerStore` persists a single global `volume` scalar in `player_settings` (`[REQ-VIS-155]`). When output alternates between the physical DAC and Bluetooth speakers, the same level is carried over regardless of speaker acoustic differences.

This implementation provides:
1. Canonical output identifiers: `"dac"` and `"bt:<MAC>"`.
2. Storage methods in `PlayerStore` for pinned presets, last-used levels, active output state, and connection timeout setting (`speaker_connect_timeout_s`).
3. Seamless level inheritance for newly encountered outputs (inheriting outgoing active level `[REQ-POV-030.4]`).
4. Blast-safe volume transition during output switching by decoupling the stream before applying target volume (`[REQ-AUD-152]`).
5. Live fader synchronization: updating `volume_last:<active_output>` on transport volume adjustments (`[SPEC-POV-020.4]`).
6. Harmonization with the appliance reconnect keeper (`lempi-speaker.sh`) so selecting DAC suppresses Bluetooth auto-reconnects.
7. Unified Bluetooth connection timeout passing configured budget to the privileged helper with automatic DAC fallback.
8. Boot-time volume initialization in `Session::prime` matching the active output device.
9. Lempi skin settings controls: permanent DAC entry in `#bt-list`, dedicated `#output-volume-row`, and `#bt-timeout-row`.

---

## 2. Storage & Database Schema (`player_store.rs`)

**`[IMPL-POV-010]` Key-value layout in `player_settings`.**
Extends the existing `player_settings` table in `listener.db` without requiring schema migrations:
- `volume_pinned:<output_id>`: Stores explicit pinned dB clamped to `[-72.0, 0.0]` (e.g. `"-12.0"`).
- `volume_last:<output_id>`: Stores last-used dB clamped to `[-72.0, 0.0]` (e.g. `"-9.0"`).
- `active_output`: Stores the currently routed output identifier (e.g. `"dac"` or `"bt:AA:BB:CC:DD:EE:FF"`, default `"dac"`).
- `speaker_connect_timeout_s`: Integer seconds for Bluetooth connection attempts before DAC fallback (clamped `5..=300`, default `45`).

**`[IMPL-POV-020]` Store methods.**
In `player/core/src/db/player_store.rs`:
- `pub fn save_output_volume(&self, output: &str, db: f32, pinned: bool) -> Result<(), DbError>`:
  Clamps `db` to `[-72.0, 0.0]`. Inserts or updates `volume_pinned:<output>` (if `pinned`) or `volume_last:<output>` (if not).
- `pub fn clear_output_pinned_volume(&self, output: &str) -> Result<(), DbError>`:
  Deletes `volume_pinned:<output>` if present.
- `pub fn load_output_volume(&self, output: &str) -> Result<OutputVolume, DbError>`:
  Returns:
  ```rust
  pub struct OutputVolume {
      pub pinned_db: Option<f32>,
      pub last_used_db: Option<f32>,
  }
  ```
- `pub fn resolve_output_volume(&self, output: &str, current_db: f32) -> f32`:
  Precedence: pinned dB -> last-used dB -> inherited `current_db` (`[REQ-POV-030.4]`).
- `pub fn get_active_output(&self) -> String` and `pub fn set_active_output(&self, output: &str) -> Result<(), DbError>`:
  Reads and sets current active output (defaults to `"dac"` when unset).
- `pub fn get_speaker_connect_timeout(&self) -> u32` and `pub fn set_speaker_connect_timeout(&self, secs: u32) -> Result<(), DbError>`:
  Reads and sets timeout (clamped to `5..=300`, default 45).
- `pub fn forget_output(&self, output: &str) -> Result<(), DbError>`:
  Cleans up `volume_pinned:<output>` and `volume_last:<output>`. If `active_output == output`, reverts `active_output` to `"dac"`.

---

## 3. Web API Endpoints (`player/src/web/`)

**`[IMPL-POV-030]` Route handlers in `control.rs`, `bluetooth.rs`, and `mod.rs`.**
Mounts REST endpoints:
- `GET /audio/output/volume`:
  Returns active output identifier, output display name, current dB, pinned dB, last-used dB, `has_pinned` boolean, and configured `connect_timeout_s` (`[SPEC-POV-060]`). Display name resolves to `"DAC / Onboard Audio"` for `"dac"`, and the BlueZ alias (or formatted MAC) for `"bt:<MAC>"`.
- `POST /audio/output/volume/remember`:
  Reads active playback volume dB from snapshot, saves as `volume_pinned:<active_output>`.
- `POST /audio/output/volume/clear`:
  Removes `volume_pinned:<active_output>`, reverting active output to last-used volume tracking.
- `POST /audio/output/use/dac`:
  Explicitly selects physical DAC: disconnects active Bluetooth speaker, sets WirePlumber default sink to DAC, persists outgoing device volume, resolves target DAC volume, executes blast-safe output reopen, and marks `"dac"` as active output.
- `POST /audio/output/timeout/:secs`:
  Clamps (`5..=300`) and persists Bluetooth connect timeout parameter in `player_settings`.
- `POST /volume/:db` in `control.rs`:
  In addition to dispatching `Command::SetVolume`, synchronously or asynchronously updates `volume_last:<active_output>` in `player_settings` so continuous fader movements are durably remembered per output (`[SPEC-POV-020.4]`).
- `POST /audio/speakers/forget/:address` in `bluetooth.rs`:
  Calls `store.forget_output(&format!("bt:{address}"))` to clean up associations.

---

## 4. Output Switching, Connection Timeout & Fallback

**`[IMPL-POV-040]` Blast-safe level handoff on device selection.**
In `player/src/web/bluetooth.rs` and audio output supervisor:
1. When switching away from output $A$, commit current playback level to `volume_last:A`.
2. Resolve target volume $V_B$ for incoming output $B$ via `resolve_output_volume(B, current_db)`.
3. Detach or mute Output $A$ stream before modifying the engine volume, preventing Output $A$ from experiencing transitional volume blasts (`[REQ-AUD-152]`).
4. Apply target volume $V_B$ to the engine and output ring during the teardown/reopen interval.
5. Attach and stream to incoming device $B$ at $V_B$.
6. Update `active_output` to $B$.

**`[IMPL-POV-045]` Bluetooth connection timeout and automatic DAC fallback.**
1. When initiating a Bluetooth speaker connection via user selection, pass `speaker_connect_timeout_s` as the timeout budget to `lempi-btctl use` (via parameter or `LEMPI_GRAB_SECONDS`).
2. If the speaker connects successfully within the window, apply target volume $V_B$ and complete the handoff.
3. If the connection attempt fails or times out:
   - Terminate connection attempts cleanly via the helper.
   - Restore default WirePlumber sink to DAC / Onboard Audio.
   - Resolve DAC volume, apply to engine, and reopen output to DAC.
   - Mark `"dac"` as `active_output` and inform the listener in the UI.

**`[IMPL-POV-048]` Appliance background reconnect keeper harmonization.**
In `appliance/bluetooth/lempi-common.sh` and `lempi-speaker.sh`:
1. Read `active_output` from `player_settings`.
2. If `active_output` is `"dac"`, exit the script immediately, suppressing the 30-second chase loop. This guarantees that user-directed DAC selection is respected and never overridden by the appliance timer.

**`[IMPL-POV-049]` Startup and boot volume initialization.**
In `player/src/session.rs` (`Session::prime`):
1. Load `active_output` from `player_settings` (defaulting to `"dac"`).
2. Resolve the initial playback volume for `active_output` via `resolve_output_volume`.
3. Apply the resolved volume to `Engine`, ensuring cold boots initialize to the active output's level rather than a stale global scalar.

---

## 5. UI Integration (Lempi Skin)

**`[IMPL-POV-050]` Settings panel markup and styling.**
In `player/src/web/skins/lempi/skin.html` and `skin.css`:
- **DAC Entry in `#bt-list`**: Render permanent `"DAC / Onboard Audio"` at the top of the speaker list. If `"dac"` is active, show `"playing"`; if Bluetooth is active, show `"Use"` button calling `POST /audio/output/use/dac`.
- **Dedicated Output Volume Row (`#output-volume-row`)**: Positioned directly below `#speakers`:
  - `#outvol-name`: active output display label.
  - `#outvol-status`: readable volume status (e.g. `"Remembered preset: -12.0 dB"` or `"Using last used level (-9.0 dB)"`).
  - `#outvol-remember`: `"Remember this volume"` button.
  - `#outvol-clear`: `"Clear remembered volume"` button (hidden or disabled if no pinned preset exists).
  - Kept visible regardless of Bluetooth hardware availability.
- **Bluetooth Timeout Row (`#bt-timeout-row`)**:
  - `#bt-timeout-input`: numeric input (in seconds, min 5, max 300, step 1, default 45).
  - Descriptive caption for DAC fallback behavior; hidden when `capabilities.bluetooth` is false.

**`[IMPL-POV-060]` Event handling and state synchronization.**
In `player/src/web/skins/lempi/skin.js`:
- Fetch `/audio/output/volume` on settings panel open, following speaker transitions, and when the volume fader changes.
- Bind `#outvol-remember` to `POST /audio/output/volume/remember`.
- Bind `#outvol-clear` to `POST /audio/output/volume/clear`.
- Bind `#bt-timeout-input` `change` to `POST /audio/output/timeout/:secs`.
- Update button visibility, disabled state, and status text dynamically.
- Defer the `#bt-confirm` ("Can you hear it?") countdown until the speaker has reported a successful link, preventing countdown expiration while connection attempts are still resolving.

---

## 6. Verification & Test Plan

1. **Unit Tests (`lempi-core`)**:
   - `test_output_volume_pin_and_clear`: verify pinning, clearing, and precedence.
   - `test_output_volume_inheritance`: verify initial volume inheritance when no record exists.
   - `test_output_volume_clamping`: verify decibel clamping to `[-72.0, 0.0]`.
   - `test_speaker_connect_timeout_persistence`: verify timeout load/save, default of 45s, and boundary clamping (`5..=300`).
   - `test_forget_output_cleanup`: verify cleanup of volume rows and active output fallback to `"dac"`.
2. **Integration Tests (`lempi-player`)**:
   - Verify `/audio/output/volume` endpoints (`GET`, `remember`, `clear`, `use/dac`, `timeout/:secs`).
   - Verify fader adjustments (`POST /volume/:db`) update `volume_last:<active_output>`.
   - Verify volume handoff decouples the stream before applying target volume to eliminate acoustic blasts.
3. **Appliance Validation (`lempi02w`)**:
   - Switch between onboard DAC and Bluetooth speaker; verify target volume applied without blast.
   - Verify `lempi-speaker.sh` suppresses reconnection when `active_output` is `"dac"`.
   - Verify fallback to DAC when Bluetooth speaker is powered off.
   - Verify document hygiene: `python tools/check_docs.py --strict`.
