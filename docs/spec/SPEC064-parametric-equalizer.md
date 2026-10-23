# SPEC064: 5-Band Parametric Graphic Equalizer

**Design Specification — Tier 2 · written 2026-10-18 at the maintainer's direction · built 2026-10-22 · for `[REQ-AUD-170]` · extends [SPEC060](SPEC060-per-output-volume.md) `[SPEC-POV-030]`**

Defines a real-time 5-band parametric graphic equalizer evaluated in the audio output callback (`player/src/output.rs`) immediately prior to master volume attenuation `[REQ-AUD-152]`. Supports live zero-latency frequency, $Q$, and gain adjustment with an interactive composite frequency-response curve in the Lempi web skin, auto-headroom preamp protection against digital clipping, and per-output profile persistence across the physical DAC and each known Bluetooth speaker.

> **Related:** [REQ003](REQ003-audio-playback.md) `[REQ-AUD-152]`, `[REQ-AUD-170]` ·
> [SPEC060](SPEC060-per-output-volume.md) `[SPEC-POV-030]` (per-output identification and switching) ·
> [SPEC065](SPEC065-tempo-pitch-and-loop.md) (stream-stage Tempo, Pitch, and A-B Loop controls) ·
> `player/src/output.rs`, `player/core/src/db/player_store.rs`, `player/src/web/bluetooth.rs`

---

## 1. Requirements & Placement in the Audio Path

**`[SPEC-EQ-010]` Real-time callback placement downstream of the 14-second output ring.**
Like master volume `[REQ-AUD-152]`, the parametric equalizer is a listening control rather than a property of an individual passage. Applying equalization before `OutputRing` submission would delay every adjustment by the ring's ~14-second depth and bake outgoing filter settings into crossfading passages. The equalizer therefore runs inside `fill()` in `player/src/output.rs` on samples read from `OutputState::ring`, immediately before the master volume multiply (`volume.get()`).

**`[SPEC-EQ-015]` Lock-free, zero-allocation callback execution outside the ring mutex.**
1. The equalizer state (`eq: Arc<EqProcessor>`) resides on `OutputRing` beside `volume: Volume` and `clock: FrameClock`, outside `Mutex<OutputState>`.
2. In `fill()`, the `MutexGuard<OutputState>` is dropped as soon as samples are copied from `s.ring`; the underrun and declick counters that follow are atomics on `Counts` and need no lock. `eq.apply(&mut out[..got])` and the volume multiply run **outside** the ring mutex, so filter evaluation never extends lock hold time or increases `lock_failures`.
3. The callback path performs zero heap allocations, zero system calls, and zero blocking locks. Filter coefficients are precomputed on the control/engine thread and published through a seqlock: an odd/even generation counter with an explicit `Release` fence after the odd mark and an `Acquire` fence before the re-check. **The fences are required, not belt and braces** — without them the relaxed coefficient stores may be observed out of order on aarch64, which every appliance runs, and a reader could accept a half-written set. The callback's own filter state sits behind a `Mutex` only it ever takes, with `try_lock`.
4. A coefficient change preserves the per-channel state variables (`ic1eq`, `ic2eq`), so a live drag is continuous. Switching the equalizer in or out crossfades dry and filtered over 20 ms, and the applied preamp glides (5 ms time constant) toward its target: both would otherwise be a level step mid-waveform — a click — whenever the preamp is non-zero.
5. Measured on `lp3-wifi` (Pi 3 B, 44.1 kHz), 2026-10-22: with the equalizer on, the player used 7.5–10.1% of one core, against 4.4–4.8% with it off.

**`[SPEC-EQ-020]` Stream sample-rate and channel adaptation.**
When `Output::attach_with` in `player/src/output.rs` has negotiated the device's actual `sample_rate` and `channels` (`pick_config`), it informs `ring.eq.set_stream_format(sample_rate, channels)` before building the stream, so a reopen onto a different device keeps the profile and re-derives its coefficients. Filter coefficients are normalized against the active device sample rate so frequency placement remains accurate across 44.1 kHz, 48 kHz, and 96 kHz outputs.

---

## 2. Filter Topology & Parameter Bounds

**`[SPEC-EQ-030]` 5-band parametric topology and default parameters.**
Each profile consists of five cascaded State Variable Filter stages (`bands: [EqBand; 5]`) plus a master preamp stage. Each band also carries `enabled` (default `true`; a band sent without it is enabled). `FilterKind` additionally defines `LowPass` and `HighPass`, which the API accepts but the skin does not offer:

| Band | `FilterKind` | Default $f_0$ (Hz) | Allowed $f_0$ Range (Hz) | Default $Q$ | Allowed $Q$ Range | Default Gain (dB) | Allowed Gain Range (dB) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `LowShelf` | 60.0 | 20.0 – 20,000.0 | 0.707 | 0.10 – 18.00 | 0.0 | −12.0 … +12.0 |
| 2 | `Peaking` | 250.0 | 20.0 – 20,000.0 | 0.707 | 0.10 – 18.00 | 0.0 | −12.0 … +12.0 |
| 3 | `Peaking` | 1,000.0 | 20.0 – 20,000.0 | 0.707 | 0.10 – 18.00 | 0.0 | −12.0 … +12.0 |
| 4 | `Peaking` | 4,000.0 | 20.0 – 20,000.0 | 0.707 | 0.10 – 18.00 | 0.0 | −12.0 … +12.0 |
| 5 | `HighShelf` | 12,000.0 | 20.0 – 20,000.0 | 0.707 | 0.10 – 18.00 | 0.0 | −12.0 … +12.0 |

**`[SPEC-EQ-035]` Cytomic Linear Trap Integrated State Variable Filter (SVF) formulation.**
Each band is evaluated using Andrew Simper's (Cytomic) trapezoidal-integrated SVF structure, with `A = 10^(gain_db / 40)` and `g = tan(pi * freq_hz / sample_rate)` — divided by $\sqrt{A}$ for a low shelf and multiplied by it for a high shelf — and `k = 1 / q`, or `1 / (q · A)` for a peaking band:
- Unlike Direct Form I biquads, trapezoidal SVF state variables represent physical integrator states and remain unconditionally stable when `freq_hz`, `q`, and `gain_db` are swept continuously in real time.
- Any band whose `freq_hz >= 0.495 * sample_rate` is clamped below Nyquist (`0.495 * sample_rate`) prior to tangent evaluation.
- When `enabled == false` or all five bands and effective preamp are at `0.0 dB`, `EqProcessor::apply` takes a zero-cost bypass fast-path leaving samples bit-identical — once the 20 ms switch-out ramp of `[SPEC-EQ-015]` has finished.

**`[SPEC-EQ-040]` Master preamp and automatic headroom protection (`auto_preamp`).**
`EqConfig` carries `enabled: bool`, `preamp_db: f32` (clamped to `[-12.0, +12.0]`, default `0.0`), and `auto_preamp: bool` (default `true`):
1. The composite magnitude response $H_{\text{dB}}(f) = \sum_{i=1}^{5} 20 \log_{10} |H_i(e^{j 2 \pi f / f_s})|$ is evaluated across a 64-point logarithmically spaced grid from 20 Hz to 20 kHz.
2. When `auto_preamp` is `true`, effective preamp gain is:
   $$\text{effective\_preamp\_db} = \text{preamp\_db} - \max\bigl(0.0,\; \max_{f} H_{\text{dB}}(f)\bigr)$$
   so positive EQ boosts automatically reduce input drive by the peak boost amount, preventing inter-stage and output clipping on full-scale passages.
3. When `auto_preamp` is `false`, `effective_preamp_db = preamp_db`.

---

## 3. Crate Boundary & Per-Output Persistence

**`[SPEC-EQ-050]` `lempi-core` data model vs. `lempi-player` DSP boundary.**
To satisfy the `core_boundary()` check in `build/verify-targets.sh`, pure configuration types (`FilterKind`, `EqBand`, `EqConfig`, parameter clamping, and serde serialization) are defined in `lempi-core` (`player/core/src/eq.rs`) and re-exported by `lempi-player`'s `player/src/eq.rs`, where the real-time SVF state and coefficient evaluation (`EqProcessor`) live. `EqConfig` deserializes with per-field defaults, so a stored profile missing a later field still loads rather than resetting to flat.

**`[SPEC-EQ-055]` Per-output SQLite persistence in `listener.db`.**
Equalizer profiles are associated with each output sink using `[SPEC-POV-030]`'s canonical output identifiers (`"dac"` and `"bt:<MAC>"`):
1. Stored in `player_settings` within `listener.db` (`player/core/src/db/player_store.rs`) under `key = "output_eq:<output_id>"` as serialized JSON of `EqConfig`.
2. An output with no stored `"output_eq:<output_id>"` row resolves to `EqConfig::default()` (`enabled: false`, flat `0.0 dB` bands, `preamp_db: 0.0`, `auto_preamp: true`).
3. `PlayerStore::forget_output` deletes `"output_eq:<output_id>"` alongside `"volume_pinned:<output_id>"` and `"volume_last:<output_id>"`.

**`[SPEC-EQ-060]` Automatic profile restoration on startup and output handoff.**
1. When the engine is given its store (`Engine::attach_store` in `player/src/engine/mod.rs`), it loads `store.load_output_eq(&store.get_active_output())` and applies it to `OutputRing::eq`.
2. Whenever the active output changes in `player/src/web/bluetooth.rs` (`speaker_verb_on` for `Use`, `Pair`, `Forget` fallback, and `use_dac`), the target output's `EqConfig` is loaded from `PlayerStore` and dispatched via `Command::SetEq(target_eq)` alongside `Command::SetVolume(target_amp)`.
3. Only those routes change the active output. An appliance's speaker keeper (`lempi-speaker`) moves the sound by asking the player to reopen its output (`/command/reopen-output`), which changes neither the stored active output nor the profile — exactly as for per-output volume `[SPEC-POV-030]`. See §5.

---

## 4. Web API, Capabilities & Graphic Equalizer UI

**`[SPEC-EQ-070]` Backend capability gating (`can_eq`).**
`playback::Capabilities` (`player/src/playback.rs`) includes `pub eq: bool` (`true` for the native Lempi `Engine`, `false` for `MpdBackend`). The engine thread in `player/src/host.rs` copies `caps.eq` to `session::Controls.can_eq` where it already copies `can_seek`, and it is serialized on `Snapshot` (`player/src/web/mod.rs`) as `can_eq: bool` alongside `eq: EqConfig`.

**`[SPEC-EQ-080]` HTTP routes.**
Served by `player/src/web/mod.rs` and `player/src/web/settings.rs`:
- `GET /audio/eq`: Returns `200 OK` with JSON `{ "output_id": "<active_output>", "eq": <EqConfig>, "effective_preamp_db": <float>, "curve_db": [<64 floats>], "rate_hz": <int> }`. The curve is computed at the rate the output device opened at (`PlayerState::out_rate`), so it is the filter actually running, and at 48 kHz only before any device has opened.
- `POST /audio/eq`: Accepts JSON `EqConfig`, clamps parameters to `[SPEC-EQ-030]` bounds, sends `Command::SetEq(eq)` to the engine, saves to `PlayerStore` for the currently active `output_id`, and returns `200 OK` with the clamped `EqConfig`, the updated 64-point `curve_db`, and `saved`. **A save that fails is said:** the equalizer is still applied — the listener is listening — but the answer carries `saved: false` and `save_error`, the player logs an error, and the skin writes "Applied, but not saved" beside the curve. Until 2026-10-23 a failed save answered exactly as one that worked, and the profile was forgotten at the next restart.

**`[SPEC-EQ-085]` Named curves, defined once.** `lempi_core::eq::PRESETS` holds them — `flat`, `bass`, `vocal`, `treble`, `loudness` — as gains for the default bands. `POST /audio/eq/preset/:name` applies one to the configuration in force (`EqConfig::with_preset`): the default bands at those gains and preamp zeroed, with `auto_preamp` kept. `off` switches the equalizer off and keeps the curve, `flat` leaves it on or off as it was, and the others switch it on. It answers as `POST /audio/eq` does, or `404` for an unknown name. `Snapshot.eq_preset` names what is in force — `off`, a preset, or `custom` (`EqConfig::preset_name`) — for a control that can show a name but not a curve. Both the skin's preset list and the framebuffer UI's buttons `[SPEC-FBUI-110]` use the route and the field; neither holds a curve of its own, so a preset is the same curve wherever it is chosen.

**`[SPEC-EQ-090]` Interactive graphic equalizer in the Lempi skin.**
In `player/src/web/skins/lempi/skin.html`, `player/src/web/skins/lempi/skin.js`, and `player/src/web/core.js`:
1. Gated by `snapshot.can_eq` (hidden when `can_eq` is `false`).
2. Displays an HTML5 `<canvas>` plotting log-frequency (`20 Hz` to `20 kHz`) on the horizontal axis and gain (`−15 dB` to `+15 dB`) on the vertical axis, rendering the composite response curve and 5 draggable band control nodes.
3. Dragging a node horizontally adjusts `freq_hz`, dragging vertically adjusts `gain_db`, and mouse-wheel / numeric inputs adjust `q`, `freq_hz`, `gain_db`, `preamp_db`, `auto_preamp`, and `enabled`, plus a **Flat / Reset** button that restores the default bands of `[SPEC-EQ-030]`. A press grabs the node under the pointer and nothing else. Changes are sent while they are made, never more than one request in flight.
4. The preset list chooses by name through `[SPEC-EQ-085]`'s route, and shows `eq_preset` — including `Off`.
5. All DOM updates in `renderAudioControls()` are guarded by `changed(...)`, `setText`, and `setHidden` so repeated WebSocket snapshots with unchanged `eq` state cause zero DOM churn in `build/verify-skins.js`.

---

## 5. Open

Found reviewing the build against `lp3-wifi`, 2026-10-22.

- **The profile follows the stored active output, not the sink that is sounding.** On `lp3-wifi` the stored output was `bt:` MIDDLETON while the speaker was away and the keeper had the jack playing, so MIDDLETON's profile — and volume — applied to the jack. Per-output volume has the same gap `[SPEC-POV-030]`; closing it means the player learning which sink it actually opened.
- ~~**A failed save is reported as saved.**~~ *Resolved 2026-10-23* `[SPEC-EQ-080]`: answered `saved: false` with the reason, and shown in the skin.
- ~~**The curve is drawn at 48 kHz.**~~ *Resolved 2026-10-23*: drawn at the device's own rate.
- **`LowPass` and `HighPass` are reachable only through the API.** Tested, but no control offers them; keep them deliberately or remove them.

---

**Traceability:** `[SPEC-EQ-010..090]` · implements `[REQ-AUD-170]` · extends `[SPEC-POV-030]`
