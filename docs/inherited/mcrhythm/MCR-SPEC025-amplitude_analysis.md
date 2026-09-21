> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/SPEC025-amplitude_analysis.md` on 2026-08-09. **Not a Lempi specification.**
>
> Lead-in/lead-out detection from an A-weighted RMS intensity envelope. Supplies the automated Radio-side trim points and segue frames [SPEC-SA-075] that MuLibPlay only ever produced by hand.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# WKMP Amplitude-Based Timing Point Detection

**📊 TIER 2 - DESIGN SPECIFICATION**

Defines algorithms for automatic detection of passage lead-in and lead-out points based on amplitude analysis. Derived from user story requirements. See Document Hierarchy.

> **Related Documentation:** [Requirements](MCR-REQ001-requirements.md) | Audio Ingest Architecture | Library Management | [Crossfade Design](MCR-SPEC002-crossfade.md) | [Amplitude Analyzer Implementation](MCR-IMPL009-amplitude_analyzer_implementation.md) | Settings Reference

---

## Overview

**[AMP-OV-010]** This specification defines how wkmp-ai automatically detects optimal lead-in and lead-out timing points for passages based on amplitude analysis of the audio signal.

**Purpose:** Enable smooth crossfading by:
- Detecting passages with slow amplitude ramps (long lead-in/lead-out)
- Detecting passages with quick amplitude ramps (short/zero lead-in/lead-out)
- Providing user-adjustable parameters for fine-tuning

**Scope:** Automatic detection during import (wkmp-ai module)
- Complements silence-based boundary detection (IMPL005)
- Timing points stored in passages table (IMPL001)

---

## Motivation

### Problem Statement

**[AMP-MOT-010]** Crossfade timing points vary widely by musical style:

**Classical Music:**
- Often begins with quiet sustained notes (slow crescendo)
- May end with gradual diminuendo
- **Optimal:** Long lead-in (2-4s), long lead-out (2-4s)

**Rock/Pop Music:**
- Often begins with sudden drum hit or guitar strum
- May end with abrupt cutoff
- **Optimal:** Short lead-in (0-0.5s), short lead-out (0-0.5s)

**Electronic Music:**
- May fade in from silence over several seconds
- May fade out to silence over several seconds
- **Optimal:** Very long lead-in/lead-out (4-5s)

**[AMP-MOT-020]** Manual configuration is impractical:
- Large libraries (1000+ passages) require hours of manual adjustment
- User may not have time/expertise to configure each passage optimally

**Solution:** Automatic amplitude analysis detects passage characteristics and suggests optimal timing points

---

## Core Concept: Perceived Audible Intensity

### Definition

**[AMP-PERC-010]** "Perceived audible intensity" is the subjective loudness a listener perceives.

**Technical Approximation:** Root Mean Square (RMS) with A-weighting
- **RMS:** Approximates power of audio signal (better than peak amplitude)
- **A-weighting:** Frequency filter matching human hearing sensitivity
- **Result:** Metric correlating with human loudness perception

### RMS Calculation

**[AMP-RMS-010]** RMS calculated over sliding window:

```
Given audio samples: s[0], s[1], ..., s[N-1]
Window size: W samples (e.g., 4410 samples for 100ms at 44.1kHz)

For each window starting at index i:
  RMS[i] = sqrt( (1/W) * sum(s[i]^2 + s[i+1]^2 + ... + s[i+W-1]^2) )
```

**Properties:**
- RMS value range: 0.0 to 1.0 (assuming normalized audio)
- Higher RMS = louder perceived sound
- Smooth envelope (filters out individual sample variations)

### A-Weighting Filter

**[AMP-AW-010]** A-weighting filter boosts frequencies where human hearing is most sensitive (2-4 kHz):

**Frequency Response:**
- Attenuates low frequencies (<200 Hz): -30 dB at 50 Hz
- Flat response mid frequencies (1-6 kHz): ~0 dB
- Attenuates high frequencies (>10 kHz): -10 dB at 15 kHz

**Application:** Apply A-weighting filter to audio BEFORE RMS calculation

**Implementation:** Use standard A-weighting filter coefficients (IIR biquad filter)

---

## Threshold Definitions

### 1/4 Intensity Threshold

**[AMP-THR-010]** Lead-in detection threshold defined as:

```
threshold_lead_in = 10^(threshold_dB / 20)

Where:
  threshold_dB = 45.0 dB (absolute dB, default, user-configurable via settings table)

Calculation:
  threshold_lead_in = 10^(45.0 / 20) = 10^(2.25) ≈ 177.8 (linear amplitude)
```

**Interpretation:** Absolute dB threshold defining the minimum RMS amplitude level considered "audible content beginning". Audio below this threshold is considered lead-in ambience.

**Rationale:** Absolute dB scale provides consistent threshold across all passages regardless of peak amplitude. User-configurable via `lead_in_threshold_dB` setting (IMPL016, SPEC032).

### 3/4 Intensity Threshold

**[AMP-THR-020]** Lead-out detection threshold defined as:

```
threshold_lead_out = 10^(threshold_dB / 20)

Where:
  threshold_dB = 40.0 dB (absolute dB, default, user-configurable via settings table)

Calculation:
  threshold_lead_out = 10^(40.0 / 20) = 10^(2.0) = 100.0 (linear amplitude)
```

**Interpretation:** Absolute dB threshold defining the minimum RMS amplitude level considered "audible content ending". Audio below this threshold is considered lead-out ambience.

**Rationale:** Absolute dB scale provides consistent threshold across all passages regardless of peak amplitude. User-configurable via `lead_out_threshold_dB` setting (IMPL016, SPEC032).

---

## Lead-In Detection Algorithm

### Algorithm Overview

**[AMP-LEADIN-010]** Lead-in detection determines optimal fade-in start point:

```
Input:
  - audio_samples: Decoded PCM audio for passage
  - sample_rate: Sample rate (e.g., 44100 Hz)
  - parameters: Algorithm parameters (thresholds, window size, max duration)

Output:
  - lead_in_duration: Duration in seconds (0.0 to max_lead_in_duration_s)
```

### Step-by-Step Algorithm

**[AMP-LEADIN-020]** Detection process:

**Step 1: Calculate RMS Envelope**
```
1. Apply A-weighting filter to audio samples
2. Calculate RMS for sliding windows (default: 100ms windows)
3. Result: Array of RMS values, one per window

Example (100ms windows at 44.1kHz):
  Window size = 44100 * 0.1 = 4410 samples
  3-minute passage = 180 seconds = 1800 windows
  RMS array: [0.02, 0.15, 0.45, 0.82, 0.95, 0.93, ...]
```

**Step 2: Calculate Absolute Thresholds**
```
threshold_lead_in = 10^(lead_in_threshold_dB / 20)
threshold_lead_out = 10^(lead_out_threshold_dB / 20)

Example (default 45.0 dB / 40.0 dB absolute thresholds):
  threshold_lead_in = 10^(45.0 / 20) ≈ 177.8 (linear amplitude)
  threshold_lead_out = 10^(40.0 / 20) = 100.0 (linear amplitude)
```

**Step 3: Detect Quick Ramp-Up**
```
Find first window where RMS >= threshold_lead_out
time_to_75 = (window_index * window_duration_ms) / 1000

IF time_to_75 < quick_ramp_duration_s:
    RETURN lead_in_duration = 0.0  // Quick ramp-up, no lead-in needed

Example:
  First RMS >= 0.534 at window 3
  time_to_75 = (3 * 100ms) / 1000 = 0.3 seconds

  IF 0.3 < 1.0 (default quick_ramp_duration_s):
      RETURN 0.0 seconds
```

**Step 4: Find Lead-In Point (Slow Ramp-Up)**
```
// Search from start of passage forward
Find first window where RMS >= threshold_lead_in
lead_in_window = window_index
lead_in_duration = (lead_in_window * window_duration_ms) / 1000

// Clamp to maximum
lead_in_duration = min(lead_in_duration, max_lead_in_duration_s)

Example:
  First RMS >= 177.8 at window 23
  lead_in_duration = (23 * 100ms) / 1000 = 2.3 seconds

  IF 2.3 > 10.0 (default max_lead_in_duration_s):
      lead_in_duration = 10.0
  ELSE:
      lead_in_duration = 2.3 seconds
```

**Step 5: Return Result**
```
RETURN lead_in_duration
```

---

## Lead-Out Detection Algorithm

### Algorithm Overview

**[AMP-LEADOUT-010]** Lead-out detection determines optimal fade-out end point:

```
Input:
  - audio_samples: Decoded PCM audio for passage
  - sample_rate: Sample rate
  - parameters: Algorithm parameters

Output:
  - lead_out_duration: Duration in seconds (0.0 to max_lead_out_duration_s)
```

### Step-by-Step Algorithm

**[AMP-LEADOUT-020]** Detection process (mirror of lead-in):

**Step 1-2:** Same as lead-in (RMS envelope, thresholds)

**Step 3: Detect Quick Ramp-Down**
```
// Search from end of passage backward
Find last window where RMS >= threshold_lead_out
num_windows = length(rms_envelope)
time_from_75_to_end = ((num_windows - window_index) * window_duration_ms) / 1000

IF time_from_75_to_end < quick_ramp_duration_s:
    RETURN lead_out_duration = 0.0  // Quick ramp-down, no lead-out needed

Example:
  Last RMS >= 0.534 at window 1795 (out of 1800 windows)
  time_from_75_to_end = ((1800 - 1795) * 100ms) / 1000 = 0.5 seconds

  IF 0.5 < 1.0 (default quick_ramp_duration_s):
      RETURN 0.0 seconds
```

**Step 4: Find Lead-Out Point (Slow Ramp-Down)**
```
// Search from end of passage backward
Find last window where RMS >= threshold_lead_in
lead_out_window = num_windows - window_index
lead_out_duration = (lead_out_window * window_duration_ms) / 1000

// Clamp to maximum
lead_out_duration = min(lead_out_duration, max_lead_out_duration_s)

Example:
  Last RMS >= 177.8 at window 1768
  lead_out_window = 1800 - 1768 = 32
  lead_out_duration = (32 * 100ms) / 1000 = 3.2 seconds

  IF 3.2 > 10.0:
      lead_out_duration = 10.0
  ELSE:
      lead_out_duration = 3.2 seconds
```

**Step 5: Return Result**
```
RETURN lead_out_duration
```

---

## Algorithm Parameters

### Parameter Definitions

**[AMP-PARAM-010]** Amplitude analysis uses two types of parameters:

**Settings Table Parameters (User-Configurable):**
- `lead_in_threshold_dB` - Absolute dB threshold for lead-in detection (default: 45.0 dB)
- `lead_out_threshold_dB` - Absolute dB threshold for lead-out detection (default: 40.0 dB)

For complete parameter definitions (type, range, modification impact, presets), see IMPL016-settings_reference.md.

**Hardcoded Algorithm Constants (Not User-Configurable):**
- `rms_window_ms` = 100 (RMS sliding window size in milliseconds)
- `quick_ramp_threshold` = 0.75 (intensity level for quick ramp detection, fraction of threshold)
- `quick_ramp_duration_s` = 1.0 (maximum duration for quick ramp in seconds)
- `max_lead_in_duration_s` = 10.0 (maximum allowed lead-in duration in seconds)
- `max_lead_out_duration_s` = 10.0 (maximum allowed lead-out duration in seconds)
- `apply_a_weighting` = false (A-weighting filter disabled by default)

These constants are compiled into `amplitude_analyzer.rs` and cannot be modified via settings table. Future enhancement may move these to settings table for user configurability.

### Parameter Presets

**[AMP-PARAM-020]** Genre-specific preset configurations are defined in IMPL016-settings_reference.md.

**Available Presets:**
- Classical Music (more sensitive to quiet lead-ins/lead-outs)
- Rock/Pop (less sensitive, expects sudden attacks/abrupt endings)
- Electronic/Ambient (very sensitive to long fade-ins/fade-outs)
- Default (balanced for mixed library)

Each preset specifies `lead_in_threshold_dB`, `lead_out_threshold_dB`, and `silence_threshold_dB` values optimized for the respective musical genre. See IMPL016 for complete preset definitions and rationale.

---

## Database Storage

### Conversion to Tick-Based Timing

**[AMP-DB-010]** Convert detected durations to **absolute tick positions** (relative to file start):

**Formula:**
```
lead_in_start_ticks = start_time_ticks + (lead_in_duration * 28_224_000)
lead_out_start_ticks = end_time_ticks - (lead_out_duration * 28_224_000)
```

**Storage Convention:**
- `lead_in_start_ticks` and `lead_out_start_ticks` are stored as **absolute positions** relative to file start
- Values must satisfy: `start_time_ticks <= value <= end_time_ticks` (enforced by database CHECK constraints)
- When relative positions (passage-relative) are needed for display, compute: `lead_in_duration = lead_in_start_ticks - start_time_ticks`

**Example:**
```
Passage:
  start_time_ticks = 0 (start of file)
  end_time_ticks = 5_080_320_000 (180 seconds)

Detected:
  lead_in_duration = 2.3 seconds
  lead_out_duration = 3.2 seconds

Calculated (absolute positions):
  lead_in_start_ticks = 0 + (2.3 * 28_224_000) = 64_915_200
  lead_out_start_ticks = 5_080_320_000 - (3.2 * 28_224_000) = 4_989_952_000
```

**Rationale:** Absolute positioning simplifies crossfade calculations (no need to add passage start offset) and is enforced by database CHECK constraints

### Metadata Storage

**[AMP-DB-020]** Store analysis metadata in `passages.import_metadata` (JSON):

```json
{
  "amplitude_analysis": {
    "peak_rms": 0.95,
    "lead_in_detected_s": 2.3,
    "lead_out_detected_s": 3.2,
    "quick_ramp_up": false,
    "quick_ramp_down": false,
    "parameters_used": {
      "rms_window_ms": 100,
      "lead_in_threshold_dB": 45.0,
      "lead_out_threshold_dB": 40.0,
      "quick_ramp_threshold": 0.75,
      "quick_ramp_duration_s": 1.0,
      "max_lead_in_duration_s": 10.0,
      "max_lead_out_duration_s": 10.0,
      "apply_a_weighting": false
    },
    "analyzed_at": "2025-10-27T12:34:56Z"
  }
}
```

**Purpose:** Audit trail for how timing points were determined (parameters snapshot preserves exact analysis configuration)

---

## Edge Cases and Special Handling

### Constant Amplitude

**[AMP-EDGE-010]** If passage has constant RMS throughout (e.g., sustained organ note):
- Peak RMS = constant value
- All RMS values equal peak
- **Result:** Lead-in duration = 0, lead-out duration = 0
- **Rationale:** No ramp detected, use default crossfade timing

### Very Quiet Passages

**[AMP-EDGE-020]** If passage peak RMS < 0.05 (very quiet):
- **Warning:** "Passage may be too quiet for accurate amplitude analysis"
- **Fallback:** Use default lead-in/lead-out (from global settings)
- **Rationale:** Low signal-to-noise ratio makes RMS unreliable

### Clipping Detection

**[AMP-EDGE-030]** If audio samples contain clipping (|sample| >= 0.99):
- **Warning:** "Audio clipping detected, amplitude analysis may be inaccurate"
- **Continue:** Perform analysis but flag result as potentially unreliable
- **Rationale:** Clipped audio has distorted amplitude envelope

---

## Performance Considerations

### Computational Complexity

**[AMP-PERF-010]** Algorithm complexity:

**Time Complexity:** O(N) where N = number of audio samples
- Single pass through audio for RMS calculation
- RMS envelope calculation dominates (multiply-accumulate operations)

**Space Complexity:** O(W) where W = number of RMS windows
- Typically ~1800 windows for 3-minute passage (100ms windows)
- Negligible memory usage (<10 KB per passage)

### Expected Performance

**[AMP-PERF-020]** Performance targets:

| Passage Duration | Analysis Time | Hardware |
|------------------|---------------|----------|
| 3 minutes | 0.5-2 seconds | Desktop (4-core, 3GHz) |
| 3 minutes | 2-5 seconds | Laptop (2-core, 2GHz) |
| 10 minutes | 2-7 seconds | Desktop |

**Bottleneck:** Decoding audio (symphonia) + resampling (rubato) if needed

---

## Validation and Testing

### Test Cases

**[AMP-TEST-010]** Required test scenarios:

1. **Slow Fade-In (Classical):**
   - Input: Passage with gradual crescendo over 3 seconds
   - Expected: lead_in_duration ≈ 2.5-3.5 seconds

2. **Quick Attack (Rock):**
   - Input: Passage starting with sudden drum hit
   - Expected: lead_in_duration ≈ 0.0 seconds

3. **Constant Amplitude:**
   - Input: Passage with steady volume throughout
   - Expected: lead_in_duration = 0.0, lead_out_duration = 0.0

4. **Very Quiet Passage:**
   - Input: Passage with peak RMS < 0.05
   - Expected: Warning logged, fallback to defaults

5. **Clipped Audio:**
   - Input: Passage with samples at ±1.0 (clipping)
   - Expected: Warning logged, analysis continues

### Acceptance Criteria

**[AMP-TEST-020]** Analysis accuracy:
- Detected lead-in/lead-out within ±0.5 seconds of manual expert assessment
- At least 80% of passages classified correctly (quick vs. slow ramp)
- No crashes or errors on corrupted/edge-case audio

---

## Document History

**Version 1.1** (2025-11-15):
- **ALIGNMENT UPDATE:** Aligned with SPEC032 as controlling document
- Updated default thresholds: `lead_in_threshold_dB` = 45.0 dB, `lead_out_threshold_dB` = 40.0 dB
- Clarified interpretation: All dB values are **absolute dB** (RMS amplitude levels, not relative to peak)
- Updated `max_lead_in_duration_s` / `max_lead_out_duration_s` defaults to 10.0 seconds (per implementation)
- Updated `apply_a_weighting` default to `false` (per implementation)
- Documented settings table storage for user-configurable parameters
- Revised presets to use positive absolute dB values

**Version 1.0** (2025-10-27):
- Initial specification

---

**Document Version:** 1.1
**Last Updated:** 2025-11-15
**Status:** Design specification (aligned with SPEC032 and implementation)

---

End of document - Amplitude Analysis
