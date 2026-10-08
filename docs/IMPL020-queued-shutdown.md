# IMPL020: Queued Shutdown Event

**Implementation Plan — Tier 3 · written 2026-10-06 · built 2026-10-07 (`3e094ed`)**

How a queued appliance shutdown event is integrated into the Lempi player engine and MuLibPlay skin, allowing a listener to schedule a clean power-off while playing through pending audio, preserving future queue selections across the restart, and maintaining seamless cross-skin queue compatibility.

> **Related:** [SPEC059](spec/SPEC059-queued-shutdown.md) · [REQ002](spec/REQ002-functional-requirements.md) `[REQ-HW-120]` · [REQ003](spec/REQ003-audio-playback.md) `[REQ-AUD-140]`, `[REQ-AUD-164]` · [REQ004](spec/REQ004-visibility-provenance.md) `[REQ-VIS-185]`, `[REQ-VIS-186]` · [SPEC009](spec/SPEC009-program-director.md)

---

## 1. Background & Problem Statement

**`[IMPL-QSD-010]` Scheduled appliance shutdown within continuous playback.**
Lempi operates as a continuous radio and passage playback appliance (`[REQ-AUD-142]`). Previously, power-off was only accessible immediately via the Vaino/Lempi skin's Settings panel (`/power/off`), abruptly terminating playback after a 400 ms persistence sleep. Listeners using the MuLibPlay skin (`player/src/web/skins/mulibplay/`) had no shutdown control, and listeners wishing to listen to a specific set of tracks before sleeping or leaving had no means to schedule a shutdown after current music concludes.

This plan integrates a first-class "Enqueue Shutdown" event into the queue model. When triggered, the system appends a Shutdown event to the tail of the current queue. The song preceding the shutdown event plays to its natural conclusion without crossfading into subsequent tracks, the hardware buffer drains fully, future tracks queued behind the shutdown marker are persisted across the power cycle to become the upcoming queue on the next boot, and the machine initiates a clean system poweroff. If enqueued when playback is stopped or idle, shutdown executes immediately.

---

## 2. MuLibPlay Skin UI & Layout Architecture

**`[IMPL-QSD-020]` Enqueue Shutdown button placement, states, and styling.**
The control is exclusive to the MuLibPlay skin and integrates directly into the browsing control row (`.browserow` in `skin.html`):

* **Placement & Markup**: Located to the right of the `Artist`, `Album`, and `Track` links within `.browserow`, wrapped inside a dedicated flex container:
  ```html
  <div class="shutdown-slot">
    <button class="button" id="b-shutdown" type="button">Enqueue Shutdown</button>
  </div>
  ```
  Styled with `flex: 1; display: flex; justify-content: center;` to center the button in the remaining open space between the browse links and the column boundary.
* **Visual Parity**:
  * *Enabled (`s.shutdown_queued == false`)*: Styled identically to `.browserow .button` (`background: #2010a0`, `border: 2px solid #402080`, `border-radius: 20px`, `font-size: 18px`, `color: #cccccc`, `cursor: pointer`).
  * *Disabled (`s.shutdown_queued == true`)*: Styled dark blue and dimmed matching disabled queue navigation controls (`background-color: #2010a0`, `opacity: .3`, `cursor: default`, `border: 2px solid #402080`).
* **Dispatch & Cardinality**: Pressing the button dispatches `POST /queue/shutdown`, which appends the Shutdown marker to the tail of the queue. The queue enforces $0 \le n \le 1$ Shutdown events; the endpoint returns `204 No Content` on success and `409 Conflict` if already present. While present, the button remains disabled.
* **Non-Appliance Hosts**: On development hosts where `capabilities.power_off` is false, the button remains visible and functional, simulating shutdown by cleanly stopping playback upon reaching the event.

---

## 3. Universal Queue Rendering Across Skins

**`[IMPL-QSD-030]` Consistent Shutdown row presentation across all skins.**
While the "Enqueue Shutdown" button is exclusive to MuLibPlay, the enqueued Shutdown event appears across the queues of all skins (`mulibplay`, `lempi`, `winamp`, `fbui`), styled in each skin's native typography and palette:

* **Web Skins (`core.js`)**:
  * In `queueRow`: When `item.is_shutdown` is true, the title element displays `"Shutdown"` in standard song title typography (`.qtitle`). Artist, album, duration (`.dur`), and preference links are omitted.
  * In `queueControls(qid, editable, ends, is_shutdown)`: When `is_shutdown` is true, only the remove button (`×`, `data-action="remove"`) is emitted. The sooner (`↑`) and later (`↓`) shift buttons are omitted.
  * In `player/src/web/skins/lempi/skin.js`: `renderPick` passes `on.is_shutdown` into `queueControls`.
* **Native Framebuffer Client (`player/src/bin/fbui.rs`)**:
  * `ClientSnapshot::QueueItem` gains `#[serde(default)] pub is_shutdown: bool`.
  * In `render_settings`: When `item.is_shutdown` is true, drawing of `QUEUE_BTN_MINUS` and `QUEUE_BTN_PLUS` is omitted; only `QUEUE_BTN_X` (`X`) is drawn. Touch hit-testing for reordering buttons is bypassed on that row.
* **Removal**: Clicking remove dispatches `POST /queue/:qid/remove`, removing the Shutdown event from the queue. On removal, the engine publishes an updated snapshot (`shutdown_queued: false`), re-enabling the "Enqueue Shutdown" button in MuLibPlay.
* **Reordering Boundaries**: Standard songs adjacent to the Shutdown event retain normal shift buttons and may be moved across Shutdown. The Shutdown marker itself cannot be shifted directly (`Queue::shift` returns `false`).

---

## 4. Data Models & API Contracts

**`[IMPL-QSD-040]` Internal queue model and wire snapshot contract.**

* **Core Queue Entry (`player/core/src/queue.rs`)**:
  * `QueueEntry` gains `pub is_shutdown: bool` (default `false`).
  * `QueueEntry::shutdown()` constructs a marker: `qid: 0`, `passage_id: 0`, `is_shutdown: true`, `naming.tag_title = Some("Shutdown".into())`, duration 0 ms.
  * `Queue::has_shutdown(&self) -> bool` checks presence.
  * `Queue::push_shutdown(&mut self) -> Option<u64>` appends the marker to the tail if absent and returns its stamped `qid`.
  * `Queue::shift(&mut self, qid: u64, delta: isize) -> bool` returns `false` if the target entry has `is_shutdown == true`.
  * `Queue::shortfall(&self)` counts total queue entries against `min_depth`, allowing the Program Director to populate future passages behind the Shutdown event.
* **Queue Persistence Safeguard (`player/src/session.rs`)**:
  * In `remember_queue`: Filters out entries where `is_shutdown` is true (or `passage_id <= 0`), preventing dummy IDs from polluting SQLite table `player_queue`.
* **Web Snapshot & Serialization (`player/src/web/mod.rs`)**:
  * `PlayerState` and `Snapshot` gain top-level `pub shutdown_queued: bool`, mapped from `self.queue.has_shutdown()`.
  * `QueueItem` gains `pub is_shutdown: bool` with `#[serde(default)]`.
  * `fixtures/snapshot/consumers.json`: Updated to record `shutdown_queued` for `skins`, and `queue[].is_shutdown` for `skins` and `fbui`.
* **REST Endpoints (`player/src/web/control.rs`)**:
  * `POST /queue/shutdown`: Sends `Command::EnqueueShutdown` to the engine; returns `204 No Content` on success, or `409 Conflict` if already present.
  * Removal reuses existing `POST /queue/:passages/remove`.

---

## 5. Engine Audio Boundary & Buffer Drain Mechanics

**`[IMPL-QSD-050]` Clean audio termination and suppression of crossfade.**
The engine enforces sample-accurate boundary isolation around the Shutdown marker via an explicit drain state machine:

```
[ Playing Song ] ────► EOF ────► Output Ring Drains (~14 s) ────► Final Sample Sounds
                             │
     [ Shutdown Event ] ◄────┼── Suppress prepare_next() & admit_due()
                             │   (NO crossfade, NO decode of next song)
                             ▼
                    Commit Queue & player_state ────► System Poweroff / Clean Stop
```

1. **Decoder Preparation (`prepare_next`)**: When the upcoming queue head is a Shutdown event, decoder initialization is bypassed.
2. **Admission Suppression (`admit_due`)**: While the passage preceding Shutdown is sounding in `self.live` or draining in the output ring, or when the upcoming queue head is Shutdown, `admit_due` returns early without advancing the queue, blocking admission of any passage sitting behind Shutdown.
3. **Hardware Ring Drain (`[REQ-AUD-164]`)**: The engine allows the preceding passage to play to EOF, enter `draining`, and drain the hardware output ring until `out_buffered_frames() == 0` and `pending_finish` is resolved.
4. **Skip Handling**: If the listener presses "Skip" on the track preceding Shutdown, `skip()` clears `self.live`, sets `pending_shutdown = true`, cuts the ring to `skip_fade_ms` fade-out without calling `admit_due()`, lets the fade drain to 0 frames, and proceeds to shutdown.
5. **Idle State Handling**: If Shutdown is enqueued when the player is idle or stopped (`self.live.is_empty()` and `out_buffered_frames() == 0`), the engine proceeds immediately to the shutdown lifecycle.

---

## 6. Persistence & Power-Off Execution

**`[IMPL-QSD-060]` Queue persistence and poweroff lifecycle.**
When `queue.peek()` is Shutdown, `self.live.is_empty()`, `out_buffered_frames() == 0`, and `pending_finish.is_none()`:

1. **History Finalization**: Play history for the finishing song is written via `finalize_draining_plays` / `write_finish`.
2. **Queue Extraction**: The Shutdown event is popped from the head of `self.queue`.
3. **Durable Queue Persistence**: `store.save_queue()` commits all remaining passages (those that followed the Shutdown event) to `player_queue` in `listener.db`.
4. **Durable Playback State Persistence**: `store.save(None, 0, true)` persists `player_state` with `passage_id = None`, `position_ms = 0`, and `playing = true` (or prior play intent), ensuring subsequent reboot resumes cleanly at the beginning of the upcoming queue.
5. **Appliance Shutdown**:
   * On appliance installations where `capabilities.power_off` is true, spawns `sudo -n systemctl poweroff`.
   * On non-appliance or development environments (`capabilities.power_off == false`), cleanly stops engine playback (`self.set_playing(false)`), resetting engine state without terminating the server process.
6. **Next Boot Resumption**: Upon subsequent reboot, `Session::prime` loads `player_queue` from `listener.db` and resumes the saved queue seamlessly.

---

## 7. Verification Plan

* **Automated Unit Tests**:
  * `cargo test -p lempi-core queue`: Verify `push_shutdown`, cardinality enforcement ($0 \le n \le 1$), immobility of Shutdown in `shift`, shifting normal songs across Shutdown, and `remove` by `qid`.
  * `cargo test -p lempi-player shutdown`: Verify `prepare_next` and `admit_due` bypass on Shutdown, output ring drain to 0 frames, `player_state` reset to `(None, 0, true)`, queue persistence across shutdown, idle shutdown trigger, and `skip` into shutdown.
  * `cargo test -p lempi-player contract`: Verify snapshot deserialization and contract fixture conformance against updated `fixtures/snapshot/consumers.json`.
  * `python tools/check_docs.py --strict`: Verify zero doc hygiene errors and compliance with 300-line limits.
* **Manual / Browser Verification**:
  * Open MuLibPlay: Confirm button placement in `.browserow`, centered within `.shutdown-slot`, enabled styling, and parity with browse buttons.
  * Click "Enqueue Shutdown": Confirm entry added at queue tail, button becomes disabled (dimmed dark blue), and `×` is the only visible control.
  * Test shift arrows on adjacent songs: Confirm songs move above and below the Shutdown entry, and Shutdown cannot be shifted.
  * Test remove: Click `×`, confirm removal, and confirm "Enqueue Shutdown" re-enables.
  * Check Lempi, WinAmp, and fbui skins: Confirm Shutdown row renders cleanly with remove button only.
  * Play through track before Shutdown: Confirm zero crossfade into following track, full ring buffer drain, and clean poweroff/stop trigger.
