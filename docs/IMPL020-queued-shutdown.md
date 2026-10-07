# IMPL020: Queued Shutdown Event

**Implementation Plan — Tier 3 · written 2026-10-06**

How a queued appliance shutdown event is integrated into the Lempi player engine and MuLibPlay skin, allowing a listener to schedule a clean power-off while playing through pending audio, preserving future queue selections across the restart, and maintaining seamless cross-skin queue compatibility.

> **Related:** [SPEC059](spec/SPEC059-queued-shutdown.md) · [REQ002](spec/REQ002-functional-requirements.md) `[REQ-HW-120]` · [REQ003](spec/REQ003-audio-playback.md) `[REQ-AUD-140]`, `[REQ-AUD-164]` · [REQ004](spec/REQ004-visibility-provenance.md) `[REQ-VIS-185]`, `[REQ-VIS-186]` · [SPEC009](spec/SPEC009-program-director.md)

---

## 1. Background & Problem Statement

**`[IMPL-QSD-010]` Scheduled appliance shutdown within continuous playback.**
Lempi operates as a continuous radio and passage playback appliance (`[REQ-AUD-142]`). Previously, power-off was only accessible immediately via the Vaino/Lempi skin's Settings panel (`/power/off`), abruptly terminating playback after a 400 ms persistence sleep. Listeners using the MuLibPlay skin (`player/src/web/skins/mulibplay/`) had no shutdown control, and listeners wishing to listen to a specific set of tracks before sleeping or leaving had no means to schedule a shutdown after current music concludes.

This plan integrates a first-class "Enqueue Shutdown" event into the queue model. When triggered, the system appends a Shutdown event to the tail of the current queue. The song preceding the shutdown event plays to its natural conclusion without crossfading into subsequent tracks, the hardware buffer drains fully, future tracks queued behind the shutdown marker are persisted across the power cycle to become the upcoming queue on the next boot, and the machine initiates a clean system poweroff.

---

## 2. MuLibPlay Skin UI & Layout Architecture

**`[IMPL-QSD-020]` Enqueue Shutdown button placement, states, and styling.**
The control is exclusive to the MuLibPlay skin and integrates directly into the browsing control row (`.browserow` in `skin.html`):

* **Placement**: Located to the right of the `Artist`, `Album`, and `Track` links within `.browserow`, centered within the open horizontal space between the Browse buttons and the column boundary via flex centering (`flex: 1; display: flex; justify-content: center;`).
* **Visual Parity**:
  * *Enabled (Zero Shutdown events in queue)*: Styled identically to `.browserow .button` (background `#2010a0`, border `2px solid #402080`, border-radius `20px`, font size `18px`, color `#cccccc`, cursor `pointer`).
  * *Disabled (One Shutdown event in queue)*: Styled dark blue and dimmed matching disabled queue navigation arrows (`background-color: #2010a0`, `opacity: .3`, `cursor: default`, `border: 2px solid #402080`).
* **Dispatch & Cardinality**: Pressing the button dispatches `POST /queue/shutdown`, which appends the Shutdown marker to the tail of the queue. The queue enforces $0 \le n \le 1$ Shutdown events; while present, the button remains disabled.
* **Non-Appliance Hosts**: On development hosts where `capabilities.power_off` is false, the button remains visible and functional, simulating shutdown by cleanly stopping playback upon reaching the event.

---

## 3. Universal Queue Rendering Across Skins

**`[IMPL-QSD-030]` Consistent Shutdown row presentation across all skins.**
While the "Enqueue Shutdown" button is exclusive to MuLibPlay, the enqueued Shutdown event appears across the queues of all skins (`mulibplay`, `lempi`, `winamp`, `fbui`), styled in each skin's native typography and palette:

* **Rendering Hook (`core.js`)**:
  * In `queueRow`: When `item.is_shutdown` is true, the title element displays `"Shutdown"` in standard song title typography (`.qtitle`). Artist, album, duration (`.dur`), and preference links are omitted.
  * In `queueControls`: When `item.is_shutdown` is true, only the remove button (`×`, `data-action="remove"`) is emitted. The sooner (`↑`) and later (`↓`) shift arrow buttons are omitted.
* **Removal**: Clicking `×` dispatches `POST /queue/:qid/remove` (or `DELETE`), removing the Shutdown event from the queue. On removal, the engine publishes an updated snapshot, automatically re-enabling the "Enqueue Shutdown" button in MuLibPlay.
* **Reordering Across Shutdown**: Standard songs adjacent to the Shutdown event retain their normal `↑` and `↓` buttons and may be shifted across the Shutdown event, allowing listeners to move pending songs before or after the shutdown point.

---

## 4. Data Models & API Contracts

**`[IMPL-QSD-040]` Internal queue model and wire snapshot contract.**

* **Core Queue Entry (`player/core/src/queue.rs`)**:
  * `QueueEntry` gains `pub is_shutdown: bool` (default `false`).
  * `QueueEntry::shutdown()` constructs a marker: `qid: 0`, `passage_id: 0`, `is_shutdown: true`, `title: "Shutdown"`.
  * `Queue::has_shutdown(&self) -> bool` checks presence.
  * `Queue::push_shutdown(&mut self) -> Option<u64>` appends the marker to the tail if not already present.
  * `Queue::shortfall(&self)` counts total queue entries against `min_depth`, allowing the Program Director to populate future passages behind the Shutdown event.
* **Web Snapshot & Serialization (`player/src/web/mod.rs`)**:
  * `QueueItem` gains `pub is_shutdown: bool` with `#[serde(default)]`.
  * `Snapshot::from(&PlayerState)` maps `is_shutdown: e.is_shutdown`.
  * Backwards compatibility: `fbui` deserializes `title: "Shutdown"` and `artist: null`; `echo-follower` safely ignores `passage_id: 0` via lookup fallback.
* **REST Endpoints (`player/src/web/control.rs`)**:
  * `POST /queue/shutdown`: Sends `Command::EnqueueShutdown` to the engine; returns `202 Accepted` (or `204 No Content` / `409 Conflict` if already present).
  * Removal reuses existing `POST /queue/:qid/remove`.

---

## 5. Engine Audio Boundary & Buffer Drain Mechanics

**`[IMPL-QSD-050]` Clean audio termination and suppression of crossfade.**
The engine enforces sample-accurate boundary isolation around the Shutdown marker:

```
[ Playing Song ] ────► EOF ────► Output Ring Drains (~14 s) ────► Final Sample Sounds
                             │
     [ Shutdown Event ] ◄────┼── Suppress prepare_next() & admit_due()
                             │   (NO crossfade, NO decode of next song)
                             ▼
                    System Poweroff / Clean Stop
```

1. **Decoder Preparation (`prepare_next`)**: When the upcoming queue head is a Shutdown event, decoder initialization is bypassed.
2. **Admission Suppression (`admit_due`)**: While the passage preceding Shutdown is sounding in `self.live`, crossfade admission of any passage sitting behind Shutdown is blocked.
3. **Hardware Ring Drain (`[REQ-AUD-164]`)**: The engine allows the preceding passage to play to EOF, enter `draining`, and drain the hardware output ring until `out_buffered_frames() == 0`.
4. **Skip Handling**: If the listener presses "Skip" on the track preceding Shutdown, the engine applies the standard `skip_fade_ms` ramp, suppresses subsequent admissions, lets the fade drain, and proceeds immediately to shutdown.

---

## 6. Persistence & Power-Off Execution

**`[IMPL-QSD-060]` Queue persistence and poweroff lifecycle.**
When the preceding song finishes sounding and all output buffers have drained:

1. **History Finalization**: Play history for the finishing song is recorded (`record_play`).
2. **Queue Extraction**: The Shutdown event is popped from the head of `self.queue`.
3. **Durable Persistence**: `store.save_queue()` commits all remaining passages (those that followed the Shutdown event) to `player_queue` in `listener.db`.
4. **Appliance Shutdown**:
   * On appliance installations (`cfg(feature = "appliance")`), spawns `sudo -n systemctl poweroff` matching `/power/off`.
   * On non-appliance environments, cleanly terminates engine playback.
5. **Next Boot Resumption**: Upon subsequent reboot, `Session::init` loads `player_queue` from `listener.db`, reconstructing the saved queue seamlessly.

---

## 7. Verification Plan

* **Automated Unit Tests**:
  * `cargo test -p lempi-core queue`: Verify `push_shutdown`, cardinality enforcement ($0 \le n \le 1$), `remove` by `qid`, and shifting normal songs across Shutdown.
  * `cargo test -p lempi-player shutdown`: Verify `prepare_next` and `admit_due` bypass on Shutdown, output ring drain to 0 frames, queue persistence across shutdown, and `skip` into shutdown.
  * `cargo test -p lempi-player contract`: Verify snapshot deserialization and contract fixture conformance.
  * `python tools/check_docs.py --strict`: Verify zero doc hygiene errors.
* **Manual / Browser Verification**:
  * Open MuLibPlay: Confirm button placement in `.browserow`, enabled styling, and parity with Browse buttons.
  * Click "Enqueue Shutdown": Confirm entry added at queue tail, button becomes disabled (dimmed dark blue), and `×` is the only visible control.
  * Test shift arrows on adjacent songs: Confirm songs move above and below the Shutdown entry.
  * Test remove: Click `×`, confirm removal, and confirm "Enqueue Shutdown" re-enables.
  * Check Lempi and WinAmp skins: Confirm Shutdown row renders cleanly in native skins.
  * Play through track before Shutdown: Confirm zero crossfade into following track, full drain, and clean poweroff/stop trigger.
