# SPEC060: Per-Output Volume Association

**Design Specification — Tier 2 · written 2026-10-07 · for `[REQ-POV-010]` · built 2026-10-07 (`357cb3d`)**

Associates output volume levels with each specific physical and wireless audio output device (the hardware DAC and each known Bluetooth speaker). When switching outputs, the playback engine automatically restores the volume associated with the newly active output. Supports both explicit preset pinning ("remember this volume") and automatic last-used tracking, user-directed switching to DAC, and configurable automatic fallback.

> **Related:** [REQ003](REQ003-audio-playback.md) `[REQ-AUD-152]` (callback volume application) ·
> [REQ006](REQ006-visibility-words-and-the-rest.md) `[REQ-VIS-155]` (settings persistence) ·
> [SPEC011](SPEC011-audio-path-supervisor.md) `[SPEC-APS-060]` (sink observation) ·
> [PI003](../../LempiPi/PI003-choosing-a-speaker.md) `[PI3-AIM-020]` (speaker selection) ·
> [PI022](../../LempiPi/PI022-the-players-speaker-contract.md) `[PI3-API-010]` (output reopening)

---

## 1. Requirements

**`[REQ-POV-010]` Output volume is tracked independently per output device.**
The player maintains volume levels independently for the physical DAC and each known Bluetooth speaker (identified by MAC address). The global master volume fader reflects the volume of the currently active output.

**`[REQ-POV-020]` Output transitions automatically restore the target device's volume.**
When the active output changes (switching between Bluetooth speakers, connecting to a Bluetooth speaker, or returning to the physical DAC), the engine adjusts playback volume to the level associated with the incoming output prior to or concurrently with stream reopening, preventing abrupt level disparities.

> **As built, 2026-10-15: only the player's own switches carry the level.** A
> switch made from Settings -- a speaker chosen, or the DAC entry -- saves the
> outgoing level and applies the incoming one `[SPEC-POV-050]`. A switch the
> appliance makes itself, `lempi-speaker` reconnecting at boot or falling back
> to another known speaker, only reopens the stream: no level is applied, and
> `active_output` still names the speaker the listener chose, so fader moves
> meanwhile are saved as *that* speaker's last-used level. The level at startup
> is resolved for `active_output` too. Five minutes of playing re-points
> `active_output` at the speaker actually in use `[PI3-AIM-130]`.

**`[REQ-POV-030]` Two-tiered volume memory: explicit pinning with last-used fallback.**
1. **Explicit Pinning ("Remember this volume")**: When a listener explicitly associates the current volume with an output, that exact volume is locked as a fixed preset to be applied every time that output connects, regardless of intervening slider adjustments during a session.
2. **Last-Used Fallback**: If an explicit pinned volume has never been set for an output (or has been cleared), connecting to that output restores the volume level that was active when that output was last in use.
3. **Reversion ("Clear remembered volume")**: Clearing the pinned volume removes the fixed preset and reverts the output to the last-used volume tracking behavior.
4. **First-Time Output Continuity**: A newly connected output with neither pinned nor last-used volume on record inherits the currently playing volume from the outgoing output for seamless continuity.

**`[REQ-POV-040]` User-directed DAC selection and automatic connection fallback.**
1. The physical DAC / Onboard Audio is represented as a first-class output alongside known Bluetooth speakers with an explicit selection control.
2. When attempting to connect to a Bluetooth speaker (at boot or on selection), if no Bluetooth speaker successfully connects within a user-configurable timeout (default: 45 seconds), playback automatically falls back to DAC / Onboard Audio.
3. The fallback timeout is user-adjustable from the Lempi skin settings page.

> **As built, 2026-10-15: item 2 holds on selection only.** A speaker chosen in
> Settings that does not connect within the timeout leaves `active_output` set
> to `"dac"` and the DAC sink restored. At boot nothing waits for this timeout
> and nothing falls back as such: `lempi-wait-sink` lets the player start on
> whatever real sink is present after the preferred-speaker wait plus ten
> seconds `[PI3-AIM-120]` -- the DAC, on a node that has one -- without
> changing `active_output`, and `lempi-speaker` moves the stream to the speaker
> if it connects later.
>
> **A node with no DAC, fixed 2026-10-15** `[PI3-FOUND-820]`. Item 2 and the
> DAC entry assume a DAC exists. Where PipeWire offers no local output -- lempi02w
> is such a node -- the selection fallback changes nothing, forgetting the
> speaker in use clears `active_output` rather than storing `"dac"`, the DAC
> entry is left out of the list, and `POST /audio/output/use/dac` is refused.
> A `"dac"` already stored there is ignored by `lempi-speaker` and
> `lempi-wait-sink`. [PI029](../../LempiPi/PI029-the-car-and-the-speaker-at-home.md)

---

## 2. User Interface Specification (Lempi Skin Settings)

**`[SPEC-POV-010]` Output list and DAC selection.**
In the Lempi skin settings panel (`#panel-settings`), the speaker list (`#bt-list`) includes a permanent entry at the top:
- Labeled `"DAC / Onboard Audio"` (or host-specific DAC name, e.g. `"HiFiBerry DAC+ Pro"` on `bose`).
- Displays current connection state (`"playing"` when active, otherwise idle).
- Provides a `"Use"` button when not currently active that disconnects Bluetooth and directs audio to the local DAC.

**`[SPEC-POV-020]` Volume memory controls.**
A dedicated "Output volume" control block (`#output-volume-row`) is placed directly below the "Speaker" section (`#speakers`):
1. **Output Indicator**: Displays the currently active output name (e.g., `"DAC / Onboard Audio"` or `"soundcore Boom 2"`).
2. **"Remember" Button (`#outvol-remember`)**:
   - Associates the currently active master volume level with the active output as its explicit pinned preset.
   - Updates status readout to reflect the pinned decibel level (e.g., `"Pinned (-12.0 dB)"`).
   - Reveals the "Clear" button.
3. **"Clear" Button (`#outvol-clear`)**:
   - Clears the pinned volume preset for the active output.
   - Hidden when no pinned volume exists for the active output.
   - Reverts status display to indicate last-used tracking (e.g., `"Last used (-9.0 dB)"`), or the bare level (`"-9.0 dB"`) when there is none.
4. **Volume Slider Synchronization**: Adjusting the transport volume fader updates the active output's last-used level immediately, without overwriting an existing pinned preset.

**`[SPEC-POV-025]` Bluetooth connect timeout control.**
A settings row (`#bt-timeout-row`) is provided in the Lempi settings panel:
- Label: `"Bluetooth timeout"`
- Numeric input field (`#bt-timeout-input`): in seconds, min `5`, max `300`, step `5`, default `45`.
- Explanatory note: `"When you choose a speaker here, how long to wait for it to connect before falling back to DAC audio, and how long “Can you hear it?” waits for an answer (5–300 seconds)."`
- The same value is the length of the "Can you hear it?" countdown after any switch made from Settings, the DAC entry included `[PI3-UI-030]`.

---

## 3. Data Model & Persistence

**`[SPEC-POV-030]` Output identification and namespace.**
Outputs are uniquely identified by a canonical string key:
- Physical Hardware DAC: `"dac"` (representing on-board analog, I2S HAT, or default ALSA device).
- Bluetooth Speakers: `"bt:<MAC>"` where `<MAC>` is uppercase colon-delimited hex (e.g., `"bt:AA:BB:CC:DD:EE:FF"`).

**`[SPEC-POV-035]` SQLite persistence in `listener.db`.**
Output volume associations and timeouts persist in `player_settings` within `listener.db`:
- Pinned volume: `key = "volume_pinned:<output_id>"`, `value = "<db_float>"`.
- Last-used volume: `key = "volume_last:<output_id>"`, `value = "<db_float>"`.
- Active output tracker: `key = "active_output"`, `value = "<output_id>"`.
- Bluetooth connection timeout: `key = "speaker_connect_timeout_s"`, `value = "<secs_int>"` (default `45`).
- Preferred-speaker wait: `key = "preferred_speaker_wait_s"`, `value = "<secs_int>"` (default `5`, clamped `1..=60`). Not read by the player: `lempi-speaker` and `lempi-wait-sink` read it on the appliance `[PI3-AIM-110]`.

---

## 4. Playback Engine & Audio Path Execution

**`[SPEC-POV-040]` Volume resolution logic.**
When output $O$ becomes active, target volume $V_O$ is resolved:
1. If `volume_pinned:O` exists, use its value.
2. Else if `volume_last:O` exists, use its value.
3. Else inherit currently playing master volume $V_{\text{active}}$.

**`[SPEC-POV-050]` Atomic level transition during output handoff.**
1. When switching away from output $A$, the engine commits current volume $V$ as `volume_last:A`.
2. Target volume $V_B$ for incoming output $B$ is resolved.
3. Stream on output $A$ is detached/muted before target volume $V_B$ is applied to the output ring, ensuring output $A$ is not subjected to $V_B$ during teardown.
4. Output callback begins streaming to incoming device $B$ at $V_B$, eliminating transitional acoustic blasts `[REQ-AUD-152]`.

---

## 5. REST API Wire Contract

**`[SPEC-POV-060]` Output volume and switching endpoints.**
- `GET /audio/output/volume`:
  Returns active output status, pinned status, last-used tracking, and timeout setting:
  ```json
  {
    "output_id": "bt:AA:BB:CC:DD:EE:FF",
    "name": "Speaker A",
    "current_db": -9.0,
    "pinned_db": -12.0,
    "last_used_db": -9.0,
    "has_pinned": true,
    "connect_timeout_s": 45,
    "preferred_wait_s": 5
  }
  ```
- `POST /audio/output/volume/remember`: Pins current volume to active output (`200 OK` with the same body as `GET /audio/output/volume`).
- `POST /audio/output/volume/clear`: Clears pinned volume for active output (`200 OK` with the same body as `GET /audio/output/volume`).
- `POST /audio/output/use/dac`: Disconnects Bluetooth and switches default output to DAC (`200 OK` with sink status). On a node with no DAC, `409 Conflict` with `{"ok": false, "error": "..."}`, and nothing changed `[PI3-FOUND-820]`.
- Every `/audio/speakers` reply carries `has_dac`: `true`, `false`, or `null` when PipeWire could not be asked; the skin omits the DAC entry only on `false`.
- `POST /audio/output/timeout/:secs`: Updates Bluetooth connect timeout parameter, clamped to 5-300 s (`200 OK` with `{"ok": true, "connect_timeout_s": <secs>}`).
- `POST /audio/output/preferred-wait/:secs`: Stores the preferred-speaker wait, clamped to 1-60 s; answers `200 OK` with `{"ok": true, "preferred_wait_s": <secs>}` `[PI3-AIM-110]`.
