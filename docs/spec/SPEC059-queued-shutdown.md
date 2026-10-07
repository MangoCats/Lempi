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
A listener may schedule an appliance power-off to occur at a future point in the listening session by inserting a distinct Shutdown event into the playback queue. Exactly zero or one Shutdown events may exist in the queue at any time.

**`[REQ-QSD-020]` The audio preceding the Shutdown event plays completely without crossfading into subsequent audio.**
When the passage immediately preceding the Shutdown event reaches its conclusion, playback terminates cleanly at the end of that passage. The passage following the Shutdown event must not begin decoding, crossfading, or sounding.

**`[REQ-QSD-030]` Passages following the Shutdown event are persisted across the shutdown.**
The Program Director maintains its configured queue depth (`queue_depth`) ahead of time. Passages positioned after the Shutdown event are preserved in the persistent listener store (`player_queue`) across the power cycle and become the upcoming queue upon subsequent boot.

---

## 2. User Interface Specification

**`[SPEC-QSD-010]` The MuLibPlay skin provides an Enqueue Shutdown control.**
In the MuLibPlay skin (`player/src/web/skins/mulibplay/`), an "Enqueue Shutdown" button is placed in `.browserow` to the right of the `Artist`, `Album`, and `Track` navigation buttons, centered within the remaining open horizontal space.

1. **Enabled State (No Shutdown in Queue)**:
   - When zero Shutdown events are present in the queue, the button is enabled.
   - Styled identically to the `Artist`, `Album`, and `Track` buttons (`.button`: background `#2010a0`, border `2px solid #402080`, border-radius `20px`, font size `18px`, color `#cccccc`, cursor `pointer`).
2. **Disabled State (Shutdown in Queue)**:
   - When a Shutdown event is present in the queue, the button is disabled.
   - Styled dark blue / dimmed matching disabled queue navigation arrows (`background-color: #2010a0`, `opacity: .3`, `cursor: default`, `border: 2px solid #402080`).
3. **Press Action**:
   - Pressing the enabled button sends `POST /queue/shutdown`.
   - Appends the Shutdown event to the tail of the current queue (after all currently queued passages).
4. **Scope**:
   - The "Enqueue Shutdown" button appears exclusively in the MuLibPlay skin.

**`[SPEC-QSD-020]` The Shutdown entry renders across all skins with a removal control only.**
Once enqueued, the Shutdown event appears in the queues of all skins (`mulibplay`, `lempi`, `winamp`, `fbui`), styled in each skin's native typography and palette:
- Labeled with the text `"Shutdown"` in the same font as a song title (`.qtitle`).
- Displays no artist, album, duration, or preference link elements.
- Provides **only** the remove button (`×`), reusing `.qedit button` styling.
- Provides **no** sooner (`↑`) or later (`↓`) shift arrow buttons.
- Pressing `×` dispatches `DELETE /queue/:qid` (or `POST /queue/:qid/remove`), removing the Shutdown event from the queue and re-enabling the "Enqueue Shutdown" button in MuLibPlay.
- Adjacent normal tracks may be shifted across the Shutdown event using their own `↑` or `↓` buttons, allowing the listener to reorder passages before or after the shutdown point.

---

## 3. Data Model & Wire Contract

**`[SPEC-QSD-030]` The Shutdown event is represented within the queue model.**
The Shutdown event is held in `Queue` as an entry with a unique stamped `qid`:
- `QueueEntry`: `is_shutdown: bool = true`, `passage_id: 0`, `title: "Shutdown"`, `artist: None`, `duration_ms: 0`.
- `QueueItem` (web snapshot wire type): includes `is_shutdown: bool = true`, `passage_id: 0`, `title: "Shutdown"`, `artist: None`, `duration_ms: 0`, `editable: true`.
- Deserialization across clients (`fbui`, `echo-follower`, skins) remains backwards-compatible:
  - `fbui` reads `title: "Shutdown"`, `artist: null`, displaying it without alteration.
  - `echo-follower` encounters `passage_id: 0`, which fails `lib.passage(0)` lookup and is discarded without disturbing follower sync.
  - Snapshot fixtures in `fixtures/snapshot/consumers.json` are maintained.

---

## 4. Playback Engine & Audio Boundary Execution

**`[SPEC-QSD-040]` The Program Director continues filling the queue past the Shutdown event.**
`Queue::shortfall()` measures total entries against `min_depth`. The Program Director selects candidates and enqueues them normally. Entries placed after the Shutdown event represent the intended programme for the subsequent power-on session.

**`[SPEC-QSD-050]` Pre-admission and decoding are suppressed across the Shutdown boundary.**
In `player/src/engine/mod.rs`:
1. `prepare_next()`: If the upcoming queue head is a Shutdown event, decoder pre-opening is bypassed.
2. `admit_due()`: While the passage preceding the Shutdown event is sounding in `self.live`, the engine suppresses crossfade admission of any track sitting after the Shutdown event.
3. The preceding passage plays to its full recorded duration.
4. The output buffer is permitted to drain completely until all samples have sounded to the output sink (`audible_ms == duration_ms` and `out_buffered_frames == 0`).
5. **Skip Interaction**: If the listener presses "Skip" while the passage immediately preceding the Shutdown event is sounding, the engine fades out the current passage over `skip_fade_ms`, suppresses admission of subsequent passages, lets the fade drain, and proceeds directly to shutdown.

**`[SPEC-QSD-060]` Appliance shutdown lifecycle and queue persistence.**
When the preceding passage finishes audible playback (or skip fade drains):
1. Play history for the finishing track is finalized (`record_play`).
2. The Shutdown event is removed from the queue.
3. The remaining queue (containing all passages enqueued after the Shutdown event) is committed to SQLite via `store.save_queue()`.
4. Playback state (`player_state`) is persisted.
5. On appliance installations (`cfg(feature = "appliance")`), the engine initiates system power-off identically to `POST /power/off` (`sudo -n systemctl poweroff`).
6. On non-appliance environments (`capabilities.power_off == false`), the system simulates shutdown by terminating engine playback cleanly.
