# SPEC065: Tempo, Pitch, and A-B Loop Controls

**Design Specification — Tier 2 · written 2026-10-18 at the maintainer's direction · built 2026-10-22 · for `[REQ-AUD-172]` · extends [SPEC044](SPEC044-echo-mode-control.md) `[SPEC-ECHO-045]`**

Specifies per-passage ephemeral playback warping controls for the native Lempi audio engine: pitch-preserved **Tempo** (`0.25x–4.0x`), time-preserved **Pitch** (`±12` semitones), and sample-accurate **A-B Span Looping**. Defines their interaction with the 14-second output ring `[REQ-AUD-158]`, variable-rate `Timeline` segment tracking `[REQ-AUD-164]`, crossfade and envelope progression, scrobble suspension, track-change reset rules, and automatic Echo multi-room suspension and resumption.

> **Related:** [REQ003](REQ003-audio-playback.md) `[REQ-AUD-158]`, `[REQ-AUD-164]`, `[REQ-AUD-172]` ·
> [SPEC044](SPEC044-echo-mode-control.md) `[SPEC-ECHO-045]` (Echo suspension on warp) ·
> [SPEC064](SPEC064-parametric-equalizer.md) (output-stage 5-band parametric EQ) ·
> `player/src/decoder.rs`, `player/src/resample.rs`, `player/src/mixer.rs`, `player/src/engine/mod.rs`, `player/src/engine/timeline.rs`, `player/src/engine/persist.rs`, `player/src/echo.rs`, `player/src/echo_client.rs`

---

## 1. Per-Passage Lifecycle & Scrobble Suspension

**`[SPEC-TPL-010]` Ephemeral per-passage controls that reset on track change.**
Tempo (`tempo_milli: u32`, default `1000`), Pitch (`pitch_cents: i32`, default `0`), and A-B Loop (`AbLoop { a_ms: Option<u64>, b_ms: Option<u64> }`) are tools for actively working with the current passage:
1. None of the three is persisted to SQLite (`listener.db`).
2. **All three reset to neutral (`tempo_milli = 1000`, `pitch_cents = 0`, `ab_loop` cleared) whenever the playing passage changes**—whether by manual command (`Command::Skip`, or `Command::PlayNow`, which puts its passage first and skips) or by natural progression to the next passage in the queue. A seek within the passage is not a change of passage and keeps them.
3. Because the next passage always starts at neutral, the pre-decoded slot `self.ready` (`Engine::prepare_next` in `player/src/engine/mod.rs`) is always opened and topped up at neutral (`1000`, `0`), incurring zero time-stretcher overhead on pre-buffered next tracks. At neutral no stretcher exists at all (`Live::stretcher` is `None`).
4. Passages already admitted keep the rates they were opened at: an outgoing passage finishes as it was playing rather than lurching to `1.00x` for its last seconds.

**`[SPEC-TPL-015]` Scrobbling accounting (`heard_ms`) is suspended while warped.**
A passage is considered **warped** whenever `tempo_milli != 1000 || pitch_cents != 0 || ab_loop.is_active()` (`Engine::is_warped(&self) -> bool`). What is *sounding* is warped (`Engine::sounding_warped`) for longer: after a track change resets the controls, the warped passage's tail is still in the ~14 s output ring, so it stays warped until an admission later than `warped_through` is heard.
1. While `self.sounding_warped()` is `true`, playback is treated as playing with the music rather than listening to a passage.
2. In `Engine::record_play` (`player/src/engine/persist.rs`), whenever it is `true`, `self.heard_from` is cleared and the function returns early without adding to `self.heard_ms` and without recording a play in `PlayerStore`.
3. When it is `false` again, normal `heard_ms` accumulation resumes from the next position sampled; the span crossed while warped is never credited.
4. **A passage heard warped at any point leaves as neither a play nor a skip.** It falls short of the play threshold because the warped part is never credited, not because it was turned down, so its departure writes no `Skip` rejection and tells the Director nothing `[SPEC-PLAY-050]`. A passage that had already crossed the threshold before it was warped stays a play. `head_warped` is judged against the admission `record_play` itself is reading (`Engine::warped_at`), since `advance_shown` runs after it in a tick: judged by the previous one, the passage after a warped one was itself marked as worked with. Until 2026-10-23 such a departure was recorded as a skip, which suppressed the song from selection for the skip window.

---

## 2. A-B Span Looping

**`[SPEC-TPL-020]` A-B Loop state machine and bounds.**
`AbLoop` on `Engine` holds `a_ms: Option<u64>` and `b_ms: Option<u64>`:
- `Command::SetLoopA(a_ms)` sets the loop start point `a_ms` (clamped to `0..=duration_ms`). If an existing `b_ms` satisfies `b_ms < a_ms + 200`, `b_ms` is cleared.
- `Command::SetLoopB(b_ms)` sets the loop end point `b_ms` (clamped to `0..=duration_ms`), defaulting `a_ms` to `0` if it is unset, and enforcing a minimum loop span of **200 ms** (`b_ms >= a_ms + 200`): a shorter span is refused and changes nothing.
- `Command::ClearLoop` resets both `a_ms` and `b_ms` to `None`.
- `AbLoop::is_active(&self) -> bool` is `true` if and only if both `a_ms` and `b_ms` are `Some`.

**`[SPEC-TPL-025]` Sample-accurate decoder seek within DAO and single-track files.**
`PassageDecoder` (`player/src/decoder.rs`) retains the passage's container offset `start_ms: u64` and exposes `seek_ms(&mut self, passage_offset_ms: u64) -> Result<(), DecodeError>`:
1. `seek_ms` seeks the underlying Symphonia `FormatReader` to container timestamp `self.start_ms + passage_offset_ms` so multi-passage DAO files `[REQ-AUD-120]` seek relative to the passage start, never the file start.
2. It resets the codec state (`self.decoder.reset()`), sets `skip_frames` for sample-accurate sub-packet landing `[REQ-AUD-122]`, records the new offset in `offset_frames`, and resets `frames_emitted` to `0` with `frame_limit` recomputed from the new position to `end_ms`, so repeated loop iterations never meet a premature EOF. `position_frames()` (`offset_frames + frames_emitted`) is the passage-relative decode position the loop end is measured against.

**`[SPEC-TPL-030]` Seamless `B -> A` wrap, ring cut on activation, and crossfade suppression.**
1. **Immediate cut on a loop change**: Because the decoder runs a stream ring plus the ~14 s `OutputRing` ahead of the ear `[REQ-AUD-158]` — some 30 s — audio decoded under the loop as it was is already queued. Activating `B` when the decoder has already passed `b_ms` or the audible position lies outside `[a_ms, b_ms)`, and **any** change to an active loop (moving `A` or `B`, or clearing it), cuts the ring (`Engine::cut_for_warp_change`, a 40 ms fade), returns any admitted incoming passage from `self.live` to the head of the queue, and re-seeks `self.live[0]` — to the current audible millisecond if it is inside the new loop, else to `a_ms`.
2. **Decoder wrap & micro-crossfade**: In `Engine::top_up()`, when `ab_loop` is active and `l.dec.position_frames()` reaches `b_ms`, the decoded packet is truncated at `b_ms`, the next 10 ms past `B` are kept (decoding one more packet if the first ended inside them), `l.dec.seek_ms(a_ms)` is called, and the kept audio is crossfaded **equal-power over 10 ms** into the start of `A`. The seam is made in the decoded audio, before the stretcher and resampler, so both see one continuous signal and neither is reset at it. With nothing past `B` to fade from (the passage ended there), `B` is faded out and `A` faded in instead.
3. **Position across the seam**: the wrap is queued on `Live::wraps` as `(b_ms, a_ms)`. `Live::record_pushed_frames` keeps a linear clock of pushed output frames (`frames × tempo_milli / rate`); when it reaches `b_ms` it continues from `a_ms` and starts a new span, which `take_mixed_spans` hands to `Timeline::record` as a new `Segment` anchored at `a_ms`. The clock reaches `b_ms` exactly when the audio up to `B` has come out of the stretcher and resampler, wherever it was when the decoder jumped.
4. **Suppression of passage `fade_out` and auto-advance**: While `ab_loop.is_active()` is `true`, `Engine::admit_due` admits no next passage, and `Stream::suppress_fade_out` in `player/src/mixer.rs` disables the passage's end-of-track `fade_out` envelope. A loop therefore holds its passage until it is cleared or the listener skips.

---

## 3. Tempo (`0.25x–4.0x`) & Pitch (`±12` Semitones)

**`[SPEC-TPL-040]` Parameter units and ranges.**
- **Tempo (`tempo_milli: u32`)**: Expressed in thousandths of normal speed (`1000` = `1.00x`), clamped to `250..=4000` (`0.25x` to `4.00x`).
- **Pitch (`pitch_cents: i32`)**: Expressed in cents (`100` cents = 1 semitone, `0` = unaltered), clamped to `-1200..=1200` (`−12.0` to `+12.0` semitones).

**`[SPEC-TPL-050]` Pure-Rust MIT phase-locked spectral time-stretcher and resampler.**
To uphold `LICENSING.md` (MIT-only in `player/`), time-stretching and pitch-shifting are implemented in pure Rust using `rustfft` (explicitly declared in `player/Cargo.toml`) and `rubato` (`player/src/resample.rs`). The order is decoder → stretcher (`player/src/stretch.rs`, at the file's rate) → `Resampler` → gain → stream:
1. **Zero-overhead neutral bypass**: When `tempo_milli == 1000 && pitch_cents == 0`, no stretcher exists and decoded samples pass directly to `Resampler`, producing bit-identical output to the unwarped pipeline.
2. **Combined Tempo & Pitch decomposition**: For tempo ratio $t = \text{tempo\_milli} / 1000$ and pitch ratio $p = 2^{\text{pitch\_cents} / 1200}$, the spectral time-stretcher scales duration by $p / t$ without altering pitch, and the downstream `rubato` FFT resampler converts from effective rate $f_{\text{in}} \cdot p$ to `out_rate` — shifting pitch by $p$ and duration by $1/p$, for $1/t$ overall. Pitch is therefore shifted by the band-limited resampler every passage already passes through: an octave up through plain interpolation would fold everything above a quarter of the sample rate back down as aliasing. `FftFixedIn` needs integer rates and sizes its FFT from their common divisor, so $f_{\text{in}} \cdot p$ is rounded to a multiple of 50 Hz (`stretch::pitch_rate_hz`): under one cent of error. When $t = p$ (varispeed) the stretcher copies and the resampler alone does the work.
3. **Phase-locked STFT with transient preservation**: Uses a 2048-sample Hann window with $4\times$ overlap, identity phase-locking around local spectral peaks (preserving vertical phase coherence across bins at slow tempos down to `0.25x`), and spectral-flux onset detection — the share of a frame's magnitude that has risen since the last, over `0.35` — that resets phase accumulation on percussive attacks to prevent drum smearing. The onset decision is made once per frame across all channels, so a hit louder on one side does not pull the stereo image apart.
4. **High-tempo top-up**: `Engine::top_up()` decodes $\lceil t \rceil$ packets per call (one at neutral, as before) so `4.0x` playback never starves the mixer ring, and while warped requires room in the stream ring for a packet's worst-case expansion, since a write that does not fit is lost audio.

**`[SPEC-TPL-060]` Immediate audible cut on mid-passage Tempo or Pitch change.**
When `Command::SetTempo` or `Command::SetPitch` changes `tempo_milli` or `pitch_cents` while a passage is playing, the engine cuts `OutputRing` (`Engine::cut_for_warp_change`) and re-primes `self.live[0]` from the current audible millisecond at the new parameters, rebuilding its stretcher and resampler (`Live::rewarp`), so the change is heard under a 40 ms crossfade rather than 14 seconds later. Behind that fade the cut lays in **at least 1 s** of the re-primed passage, decoded before the cut while the old audio plays on: measured on `lp3-wifi` (Pi 3 B), a cut that left only its fade cost a dropout every time, and three within a second cost 4.2 s of silence. On the skin the sliders send on release only: every change is a cut.

**`[SPEC-TPL-070]` Variable-rate `Timeline`, `played_ms`, and `Stream` envelope math.**
1. **`Segment` math (`player/src/engine/timeline.rs`)**: `Segment` carries `pub tempo_milli: u32` (default `1000`) and computes passage position from output sample index `sample` as:
   ```rust
   fn ms_at(&self, sample: u64) -> u64 {
       let frames = sample.saturating_sub(self.from) / self.channels.max(1) as u64;
       self.at_ms + frames.saturating_mul(self.tempo_milli as u64) / self.rate.max(1) as u64
   }
   ```
2. **Segment non-coalescing across tempo changes**: `Timeline::record` requires `last.tempo_milli == s.tempo_milli` before merging adjacent segments, ensuring a mid-passage tempo adjustment starts a new `Segment` with the updated slope.
3. **`Engine::played_ms` and `audible_ms` (`player/src/engine/mod.rs`)**: `Live` records pushed audio as spans of `(frames, at_ms, tempo_milli)`; mixing consumes them (`take_mixed_spans`), so `played_ms` is the passage position at the end of the last mixed span (`last_mixed_end_ms`), correct across tempo changes and loop wraps. `audible_ms` reads the `Timeline` at the device's read position when it names this admission, and otherwise subtracts the ring's depth scaled by tempo.
4. **Tempo-scaled `Stream` envelope (`player/src/mixer.rs`)**: `Stream::push` advances its envelope position by passage frames consumed (`frames * tempo_milli / 1000`, the remainder kept in `milli_rem`) rather than raw stretched output frames, so `0.25x` tempo does not trigger `fade_out` at 25% of the passage and `4.0x` tempo still reaches `fade_out` at the end of the passage.

**`[SPEC-TPL-080]` Cost, measured.** On `lp3-wifi` (Pi 3 B, 44.1 kHz), 2026-10-22, the player's share of one core, each setting held inside a loop so a track change could not reset it: 4.4–4.8% at neutral; 7.8–9.2% with a loop alone; 21–24% at `0.25x` and `0.5x`; 24–27% at `1.25x` and `2.0x`; 31% at `4.0x`; 21% at −12 st and 36% at +3 st; 28–31% at `1.5x`, −5 st with the equalizer; and 55% — 17% of the whole machine — at `4.0x`, +12 st with the equalizer. Resident memory stayed within 130–143 MB, peak 179 MB, set at start-up; 64.5 °C at most, never throttled; no underruns once `[SPEC-TPL-060]`'s cushion was in.

---

## 4. Echo Suspension & Automatic Resumption

**`[SPEC-TPL-100]` Mutual Echo suspension under `Voided::Warped` and automatic resync.**
Synchronized multi-room Echo playback (`player/src/echo.rs`, `player/src/echo_client.rs`) requires a 1:1 linear mapping between passage time and wall-clock time. Whenever either the followed master or the following node is warped, Echo synchronization suspends cleanly to independent playback:
1. **Master side (`player/src/engine/mod.rs`)**:
   - `Voided` (`player/src/echo.rs`) adds `Voided::Warped`.
   - `Engine::tick` calls `self.update_echo_basis()` **before** `self.publish()`.
   - While `self.sounding_warped()` is `true` (`[SPEC-TPL-015]`), `update_echo_basis` voids the master's basis with `Voided::Warped` and clears `self.echo.schedule = None` (which also suppresses `anchor` in `publish()`).
   - A warp change is a cut, and voids the basis as `Voided::Skip` exactly as a seek does; `advance_shown` re-establishes it once the re-primed audio is heard `[GDE-ARC-066]`. So returning to neutral by command re-anchors when the new audio sounds, not at the command.
   - When the warped tail of a track change has played out (`voided_by() == Some(Voided::Warped)` and no longer `sounding_warped()`), `update_echo_basis` calls `self.echo.basis.establish()` so `publish()` emits a fresh `seq` and `anchor` from `self.air_position()`.
2. **Follower side (`player/src/echo_client.rs`)**:
   - At the top of the WebSocket message loop in `echo_client::run`—**before** `adopt_queue` and `act` (which would otherwise return early when `anchor` is `None`)—and again at the top of `act`, `check_warp_suspension` asks whether `st.voided_by == Some(Voided::Warped)` on the master, or whether this node's own published `tempo_milli`, `pitch_cents` or `ab_loop` is non-neutral. The follower reads its controls, not the master's notion of a warped tail.
   - **While suspended (`fs.suspended_warp == true`)**: The follower keeps the WebSocket open, skips `adopt_queue` and `act` so its warm `Director` `[GDE-ECHO-500]` continues playing and topping up its own queue independently, sends `Command::SetEchoRate(0.0)`, clears `fs.rate`, `fs.filtered`, `fs.landing`, `fs.corrected`, `fs.mid_joined`, `fs.applied_ppm` and `fs.queue`, and sets `follow_status` to `"Echo suspended (tempo, pitch, or loop active); playing independently."`
   - **On return to neutral**: The follower sets the existing `fs.want_rejoin`, resumes `adopt_queue` and `act`, and so forces `join_mid_passage` in `act` even when `head == Some(a.passage_id)` — `want_rejoin` already means "re-place even a node that is near", which is the case here: master and follower positions within the same passage diverged during the warp.

---

## 5. Web API, Capabilities & UI

**`[SPEC-TPL-110]` Backend capabilities (`can_tempo_pitch`, `can_ab_loop`) and Snapshot fields.**
`playback::Capabilities` (`player/src/playback.rs`) adds `pub tempo_pitch: bool` and `pub ab_loop: bool` (`true` on `Engine`, `false` on `MpdBackend`), propagated via `session::Controls` (`player/src/session.rs`) onto `Snapshot` (`player/src/web/mod.rs`) as `can_tempo_pitch: bool`, `can_ab_loop: bool`, `tempo_milli: u32`, `pitch_cents: i32`, and `ab_loop: { a_ms: Option<u64>, b_ms: Option<u64> }`.

**`[SPEC-TPL-120]` HTTP routes and Lempi skin controls.**
Routes in `player/src/web/mod.rs` and `player/src/web/control.rs`:
- `POST /tempo/:milli` — clamps `milli` to `250..=4000`, sends `Command::SetTempo`, returns `202 Accepted`.
- `POST /pitch/:cents` — clamps `cents` to `-1200..=1200` (a leading `+` is accepted), sends `Command::SetPitch`, returns `202 Accepted`; only a value that is not a number is refused, `400`.
- `POST /loop/a/:ms`, `POST /loop/b/:ms`, `POST /loop/clear` — sends `SetLoopA`, `SetLoopB`, or `ClearLoop`, returns `202 Accepted`.
In `player/src/web/skins/lempi/skin.js` and `player/src/web/core.js`, the Tempo slider/presets (`0.25x–4.0x` + `1.0x` reset), Pitch slider/semitone steppers (`−12..+12 st` + `0 st` reset), and `A` / `B` / `Clear` loop controls (with visual `A–B` span markers on the passage seek bar) are gated by `s.can_tempo_pitch` and `s.can_ab_loop` and guarded with `changed(...)` for zero DOM churn.

---

## 6. Open

Found reviewing the build against `lp3-wifi`, 2026-10-22.

- ~~**A warped play is recorded as a skip.**~~ *Resolved 2026-10-23* (`[SPEC-TPL-015]` item 4): shown by test the evening before, a passage played through to its natural end at `1.25x` left a `Skip` rejection. It is now neither a play nor a skip, and the passage after it is still skipped as an ordinary one would be.
- **Nothing on the device's own screen.** `fbui` shows no tempo, pitch or loop, and a loop holds its passage until cleared (`[SPEC-TPL-030]`), so a loop set from a phone on `lp3-wifi` plays on with no sign there and can be cleared only from a browser.
- **The stretcher is unheard at its extremes.** Listened to on `lp3-wifi` through MIDDLETON and judged as expected; `0.25x`, `4.0x` and ±12 st were measured but not listened to. `FLUX_ONSET` (`0.35`) is reasoned, not tuned on music, and each channel's phases advance separately except at an onset.
- **A warp cut works on the engine thread.** The cushion's decode (up to 96 top-ups) runs before the cut, synchronously, and the 1 s overlay is mixed into the output ring under its lock — as a skip's 1.5 s overlay already is. One missed ring lock was logged during the burst replay on `lp3-wifi`.
- **Echo suspension has not been run between two nodes.** Its tests are the engine's and the follower's own, in isolation.

---

**Traceability:** `[SPEC-TPL-010..120]` · implements `[REQ-AUD-172]` · extends `[SPEC-ECHO-045]`
