# SPEC059: Queued Shutdown Event

**Design Specification — Tier 2 · written 2026-10-06 · for `[REQ-QSD-010]`**

A mechanism allowing a listener using the MuLibPlay skin to enqueue an appliance shutdown event directly into the playback queue. The system continues playing the audio preceding the shutdown event to its natural completion, suppresses crossfading or decoding of any subsequent tracks, persists all tracks enqueued after the shutdown event for the next power cycle, and initiates a clean system power-off.

> **Related:** [REQ002](REQ002-functional-requirements.md) `[REQ-HW-120]` (power loss survival) ·
> [REQ003](REQ003-audio-playback.md) `[REQ-AUD-140]` (state resumption across restart), `[REQ-AUD-164]` (sounding audio vs mixed buffer) ·
> [REQ004](REQ004-visibility-provenance.md) `[REQ-VIS-185]` (queue edit controls), `[REQ-VIS-186]` (queue entries vs passages) ·
> [SPEC009](SPEC009-program-director.md) `[SPEC-DIR-225]` (queue persistence) ·
> [SPEC018](SPEC018-switching-backends.md) `[SPEC-BK-030]` (passage queue crossing backends)

---

## 1. Requirements

**`[REQ-QSD-010]` An appliance shutdown can be scheduled as a queue entry.**
A listener may schedule an appliance power-off to occur at a future point in the listening session by inserting a distinct Shutdown event into the playback queue. Exactly zero or one Shutdown events may exist in the queue at any time. When enqueued during active playback, shutdown occurs when preceding audio finishes and drains; when enqueued during an idle or stopped state with nothing sounding, shutdown executes immediately.

**`[REQ-QSD-020]` The audio preceding the Shutdown event plays completely without crossfading into subsequent audio.**
When the passage immediately preceding the Shutdown event reaches its conclusion, playback terminates cleanly at the end of that passage. The passage following the Shutdown event must not begin decoding, crossfading, or sounding into the output buffer.

**`[REQ-QSD-030]` Passages following the Shutdown event are persisted across the shutdown.**
The Program Director maintains its configured queue depth (`queue_depth`) ahead of time. Passages positioned after the Shutdown event are preserved in the persistent listener store (`player_queue`) across the power cycle and become the upcoming queue upon subsequent boot.

---

## 2. User Interface Specification

**`[SPEC-QSD-010]` The MuLibPlay skin provides an Enqueue Shutdown control.**
In the MuLibPlay skin (`player/src/web/skins/mulibplay/`), an "Enqueue Shutdown" button (`#b-shutdown`) is placed in `.browserow` to the right of the `Artist`, `Album`, and `Track` navigation buttons, wrapped inside a flex container (`.shutdown-slot`) centered within the remaining open horizontal space (`flex: 1; display: flex; justify-content: center;`).

1. **Enabled State (`shutdown_queued == false`)**:
   - Styled identically to `.browserow .button` (`background: #2010a0`, `border: 2px solid #402080`, `border-radius: 20px`, `font-size: 18px`, `color: #cccccc`, `cursor: pointer`).
2. **Disabled State (`shutdown_queued == true`)**:
   - Styled dark blue / dimmed matching disabled queue navigation controls (`background-color: #2010a0`, `opacity: .3`, `cursor: default`, `border: 2px solid #402080`).
3. **Press Action**:
   - Pressing the enabled button sends `POST /queue/shutdown`.
   - Returns `204 No Content` on success, or `409 Conflict` if a shutdown marker is already present.
   - Appends the Shutdown event to the tail of the current queue (after all currently queued passages).
4. **Scope**:
   - The "Enqueue Shutdown" button appears exclusively in the MuLibPlay skin.

**`[SPEC-QSD-020]` The Shutdown entry renders across all skins with a removal control only.**
Once enqueued, the Shutdown event appears in the queues of all skins (`mulibplay`, `lempi`, `winamp`, `fbui`), styled in each skin's native typography and palette:
- Labeled with the text `"Shutdown"` in standard song title typography (`.qtitle` in web skins, title string in `fbui`).
- Displays no artist, album, duration, or preference link elements.
- Provides **only** the remove button (`×` in web skins, `X` in `fbui`).
- Provides **no** sooner or later shift buttons (`↑`/`↓` in web skins, `-`/`+` in `fbui`).
- Pressing remove dispatches `POST /queue/:qid/remove`, removing the Shutdown event from the queue and re-enabling the "Enqueue Shutdown" button in MuLibPlay.
- Adjacent normal tracks may be shifted across the Shutdown event using their own shift buttons, allowing the listener to reorder passages before or after the shutdown point. The Shutdown event itself cannot be shifted directly (`Queue::shift` returns `false`).

---

## 3. Data Model & Wire Contract

**`[SPEC-QSD-030]` The Shutdown event is represented within the queue model.**
The Shutdown event is held in `Queue` as an entry with a unique monotonic stamped `qid`:
- `QueueEntry`: `is_shutdown: bool = true`, `passage_id: 0`, `naming.tag_title = Some("Shutdown".into())`, `duration_ms: 0`.
- `Queue::push_shutdown(&mut self) -> Option<u64>` appends marker if absent, stamps `qid`, and returns `Some(qid)`.
- `Queue::has_shutdown(&self) -> bool` checks presence.
- `Queue::shift(&mut self, qid: u64, delta: isize) -> bool` rejects moving any entry where `is_shutdown == true`.
- `Session::remember_queue`: filters out entries with `is_shutdown == true` (or `passage_id <= 0`) so dummy IDs never pollute `player_queue`.
- `Snapshot` & `PlayerState`: carry top-level `pub shutdown_queued: bool`, ensuring immediate O(1) state resolution regardless of queue truncation (`QUEUE_SHOWN = 12`).
- `QueueItem` (web snapshot wire type): includes `pub is_shutdown: bool` (`#[serde(default)]`), `passage_id: 0`, `title: "Shutdown"`, `artist: None`, `duration_ms: 0`, `editable: true`.
- `ClientSnapshot::QueueItem` in `fbui.rs`: includes `#[serde(default)] pub is_shutdown: bool`.
- Snapshot fixtures in `fixtures/snapshot/consumers.json` record `shutdown_queued` for `skins`, and `queue[].is_shutdown` for `skins` and `fbui`.

---

## 4. Playback Engine & Audio Boundary Execution

**`[SPEC-QSD-040]` The Program Director continues filling the queue past the Shutdown event.**
`Queue::shortfall()` measures total entries against `min_depth`. The Program Director selects candidates and enqueues them normally. Entries placed after the Shutdown event represent the intended programme for the subsequent power-on session.

**`[SPEC-QSD-050]` Pre-admission and decoding are suppressed across the Shutdown boundary.**
In `player/src/engine/mod.rs`:
1. `prepare_next()`: If the upcoming queue head is a Shutdown event, decoder pre-opening is bypassed.
2. `admit_due()`: While the passage preceding the Shutdown event is sounding or draining, or when the upcoming queue head is Shutdown, admission is suppressed (returns early). The Shutdown marker is not popped and tracks behind Shutdown are not admitted into the mixer.
3. The preceding passage plays to its full recorded duration, enters `draining`, and the output buffer drains completely until `out_buffered_frames() == 0` and `pending_finish` is resolved.
4. **Skip Interaction**: If the listener presses "Skip" while the passage preceding Shutdown is sounding, the engine sets pending shutdown drain state, applies standard `skip_fade_ms` fade-out, bypasses admitting subsequent passages, lets the fade drain to 0 frames, and proceeds to shutdown.

**`[SPEC-QSD-060]` Appliance shutdown lifecycle and queue persistence.**
When `queue.peek()` is Shutdown, `self.live.is_empty()`, `out_buffered_frames() == 0`, and `pending_finish.is_none()` (or immediately if playback was idle):
1. Play history for the finishing track is finalized (`write_finish`).
2. The Shutdown event is popped from the head of `self.queue`.
3. The remaining queue (passages enqueued after Shutdown) is committed to SQLite via `store.save_queue()`.
4. Playback state (`player_state`) is persisted with `passage_id: None`, `position_ms: 0`, and `playing: true` (or prior intent) so next boot starts cleanly at the beginning of the upcoming queue.
5. On appliance installations where `capabilities.power_off` is true, the engine spawns `sudo -n systemctl poweroff`.
6. On non-appliance or development environments (`capabilities.power_off == false`), the system simulates shutdown by stopping engine playback cleanly (`self.set_playing(false)`).
