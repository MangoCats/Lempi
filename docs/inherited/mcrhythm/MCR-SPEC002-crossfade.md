> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/SPEC002-crossfade.md` on 2026-08-09. **Not a Lempi specification.**
>
> Crossfade timing model consuming the lead-in/lead-out points. Context for why those points are defined as they are.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# Crossfade Design

**ðŸ—ï¸ TIER 2 - DESIGN SPECIFICATION**

Defines HOW crossfade timing and behavior are designed. Derived from [requirements.md](MCR-REQ001-requirements.md). See Document Hierarchy, and Requirements Enumeration.

> **Related Documentation:** Architecture | Single Stream Playback | Single Stream Design | [Decoder Buffer Design](MCR-SPEC016-decoder_buffer_design.md) | Sample Rate Conversion

---

## Overview

**[XFD-OV-010]** WKMP supports sophisticated crossfading between passages, allowing smooth transitions with configurable fade curves and overlap timing. Each passage has six timing points that control how it begins, ends, and overlaps with adjacent passages.

**[XFD-OV-020] Zero-Duration Timing Behavior:** When timing points are equal (zero-duration intervals), specific operational behaviors apply:
- **Fade Duration = 0** (Fade-In Point = Start OR Fade-Out Point = End): Fader operates in pass-through mode with no fade curve applied; passage plays at constant volume (1.0) during that phase
- **Lead-Out Duration = 0** (Lead-Out Point = End): No overlap with next passage; crossfade duration is 0
- **Lead-In Duration = 0** (Lead-In Point = Start): No overlap with previous passage; previous passage completes fully before this passage starts
- **All Timing Points Equal**: Passage plays from start to end at constant volume with no crossfade overlap with adjacent passages (see [REQ002 ENT-MP-035] for "Audio file as Passage" use case)

## Timing Points

**[XFD-TP-010]** Each passage has six timing points defined relative to the audio file:

```
Start    Fade-In   Lead-In            Lead-Out   Fade-Out    End
  |         |         |                    |         |         |
  |---------|---------|--------------------|---------|---------|
  |         |         |                    |         |         |
  0%        |         |                    |         |        100%
       Fade-In   Lead-In Point        Lead-Out  Fade-Out
        Point                           Point     Point
```
**Figure 1:** Fade inside Lead

Fade-In may be equal to or after Lead-In,
Fade-Out may be equal to or after Lead-Out:

```
Start    Lead-In   Fade-In            Fade-Out   Lead-Out    End
  |         |         |                    |         |         |
  |---------|---------|--------------------|---------|---------|
  |         |         |                    |         |         |
  0%        |         |                    |         |        100%
       Lead-In   Fade-In Point        Fade-Out  Lead-Out
        Point                           Point     Point
```
**Figure 2:** Lead inside Fade

Fade-In / Fade-Out soften the passage start / end volume profiles, for example when taking a passage
from the middle of a continuous piece of music.

Lead-In / Lead-Out define portions of the passage where it is permissible / non-intrusive for a crossfade
operation to take place.

### Point Definitions

  1. **[XFD-PT-010] Start Time**: Beginning of passage audio
  2. **[XFD-PT-020] Fade-In Point**: When volume reaches 100%
  3. **[XFD-PT-030] Lead-In Point**: Latest time the previous passage may still be playing
  4. **[XFD-PT-040] Lead-Out Point**: Earliest time the next passage may start playing
  5. **[XFD-PT-050] Fade-Out Point**: When volume begins decreasing
  6. **[XFD-PT-060] End Time**: End of passage audio

  - **[XFD-PT-070] Lead-In/Lead-Out**: Define when this passage plays simultaneously with adjacent passages
  - **[XFD-PT-080] Fade-In/Fade-Out**: Define volume envelope time end-points (independent of simultaneous playback)

**Database storage:** All timing points stored as INTEGER ticks (not seconds). See SPEC017 Database Storage for field definitions ([SRC-DB-011] through [SRC-DB-016]).

### Durations

- **[XFD-DUR-010] Fade-In Duration** = Fade-In Point - Start Time
- **[XFD-DUR-020] Lead-In Duration** = Lead-In Point - Start Time
- **[XFD-DUR-030] Lead-Out Duration** = End Time - Lead-Out Point
- **[XFD-DUR-040] Fade-Out Duration** = End Time - Fade-Out Point

### Constraints

**[XFD-CONS-010]** Timing points must satisfy two independent constraint chains:

  **[XFD-CONS-011] Fade Point Constraints:**
  - Start ≤ Fade-In ≤ Fade-Out ≤ End

  **[XFD-CONS-012] Lead Point Constraints:**
  - Start ≤ Lead-In ≤ Lead-Out ≤ End

  **[XFD-CONS-020] Cross-Chain Independence:**
  - Fade-In and Lead-In points are independent (either may come first, or may be equal)
  - Fade-Out and Lead-Out points are independent (either may come first, or may be equal)
  - All timing points may be equal to each other (resulting in 0-duration intervals)

  **Valid Examples:**
  1. Fade inside Lead, no simultaneous play with adjacent passages:
  Start(0s) = Lead-In(0s) < Fade-In(2s) < Fade-Out(58s) < Lead-Out(60s) = End(60s)
  2. Fade inside Lead, simultaneously play with adjacent passages possible:
  Start(0s) < Lead-In(5s) < Fade-In(7s) < Fade-Out(53s) < Lead-Out(55s) < End(60s)
  3. Lead inside Fade, no fade of this passage's level in or out:
  Start(0s) = Fade-In(0s) < Lead-In(2s) < Lead-Out(58s) < Fade-Out(60s) = End(60s)
  4. Interleaved ordering:
  Start(0s) < Fade-In(1s) < Lead-In(2s) <  Fade-Out(58s) < Lead-Out(59s) < End(60s)
  5. Zero-duration lead and fade windows, passage plays at constant volume, no simultaneous play with adjacent passages:
  Start(0s) = Fade-In(0s) = Lead-In(0s) = Lead-Out(60s) = Fade-Out(60s) = End(60s)
  6. Partial equality:
  Start(0s) = Fade-In(0s) = Lead-In(0s) < Lead-Out(58s) = Fade-Out(58s) < End(60s)

  **[XFD-CONS-030] Validation Rules:**
  - Both constraint chains must be satisfied independently
  - No additional ordering restrictions exist between Fade and Lead points

  **Semantic Guidance:**

  While any ordering satisfying the constraints is valid, typical usage patterns include:

  - **Fade-In after Lead-In** (Fade-In ≥ Lead-In): Use when passage starts with quiet intro that needs gentle fade-in even after crossfade completes
  - **Lead-In after Fade-In** (Lead-In ≥ Fade-In): Use when passage has abrupt start that benefits from fade-in, but crossfade should end before full volume
  - **Fade-Out before Lead-Out** (Fade-Out ≤ Lead-Out): Use when passage should start fading out before next passage begins crossfading in
  - **Lead-Out before Fade-Out** (Lead-Out ≤ Fade-Out): Use when next passage should start while current passage is still at full volume

---

## Fade Points vs Lead Points: Orthogonal Concepts

**[XFD-ORTH-010]** Fade points and lead points serve independent purposes in the audio processing architecture:

### Lead Points (Overlap Timing)

- **[XFD-ORTH-011] Lead-In Point:** Latest time the previous passage may still be playing
- **[XFD-ORTH-012] Lead-Out Point:** Earliest time the next passage may start playing
- **[XFD-ORTH-013] Purpose:** Define WHEN the mixer plays two passages simultaneously
- **[XFD-ORTH-014] Processing location:** Mixer component ([SPEC016 DBD-MIX-040])
- **[XFD-ORTH-015] Operational impact:** Determines crossfade overlap duration via min(lead_out_duration, lead_in_duration)

### Fade Points (Volume Envelope)

- **[XFD-ORTH-021] Fade-In Point:** When volume reaches 100%
- **[XFD-ORTH-022] Fade-Out Point:** When volume begins decreasing
- **[XFD-ORTH-023] Purpose:** Define volume envelope applied to passage audio
- **[XFD-ORTH-024] Processing location:** Fader component, BEFORE buffering ([SPEC016 DBD-FADE-030/050])
- **[XFD-ORTH-025] Operational impact:** Modifies sample amplitudes before they reach the buffer

### Key Architectural Fact

**[XFD-ORTH-020]** By the time audio reaches the mixer's buffers, fade curves have ALREADY been applied:

1. **Decoder** → decodes audio from file
2. **Resampler** → converts to working sample rate
3. **Fader** → applies fade-in/fade-out curves to samples ([SPEC016 DBD-FADE-030/050])
4. **Buffer** → stores pre-faded audio samples
5. **Mixer** → reads pre-faded samples from buffers and sums during overlap ([SPEC016 DBD-MIX-040])

During crossfade overlap, the mixer simply reads pre-faded samples from both buffers and adds them together. Lead points determine the **timing** of this overlap; fade points determine the **volume envelope** of each passage (already baked into the buffered audio).

### Practical Example

**Scenario:** Passage A transitioning to Passage B

**Passage A:**
- Duration: 60 seconds
- Lead-Out Duration: 5 seconds (last 5 seconds)
- Fade-Out Duration: 3 seconds (last 3 seconds)

**Passage B:**
- Duration: 50 seconds
- Lead-In Duration: 4 seconds (first 4 seconds)
- Fade-In Duration: 2 seconds (first 2 seconds)

**What happens:**

1. **Fade Application (Pre-Buffer):**
   - Passage A: Fader applies fade-out curve to last 3 seconds, writes to buffer
   - Passage B: Fader applies fade-in curve to first 2 seconds, writes to buffer
   - Result: Buffered audio already has volume envelopes applied

2. **Crossfade Timing Calculation:**
   - Crossfade duration = min(5s lead-out, 4s lead-in) = **4 seconds**
   - Passage B starts when Passage A has 4 seconds remaining

3. **Mixer Operation During 4-Second Overlap:**
   - **Seconds 56-58 of A (0-2 of B):**
     - A: Full volume samples (fade-out hasn't started yet)
     - B: Fading-in samples (fade curve already applied to buffered audio)
     - Mixer: Reads both, sums them, outputs mixed audio

   - **Seconds 58-60 of A (2-4 of B):**
     - A: Fading-out samples (fade curve already applied to buffered audio)
     - B: Full volume samples (fade-in complete)
     - Mixer: Reads both, sums them, outputs mixed audio

4. **After Overlap:**
   - Passage A reaches end_time (60s), queue advances
   - Passage B continues playing solo from second 4 onward

**Key Insight:** The mixer doesn't calculate or apply fade curves - it simply sums pre-faded audio. All fade calculations happen in the Fader component before audio enters the buffer.

---

## Fade Curves

### Per-Passage Curve Selection

**[XFD-CURV-010]** Each passage can independently configure its fade-in and fade-out curves:

**[XFD-CURV-020] Fade-In Curve Options:**
- **[XFD-EXP-010] Exponential**: Volume increases exponentially (slow start, fast finish) - natural-sounding
- **[XFD-COS-010] Cosine (S-Curve)**: Smooth acceleration and deceleration - gentle, musical
- **[XFD-LIN-010] Linear**: Constant rate of change - precise, predictable

**[XFD-CURV-030] Fade-Out Curve Options:**
- **[XFD-EXP-020] Logarithmic**: Volume decreases logarithmically (fast start, slow finish) - natural-sounding
- **[XFD-COS-020] Cosine (S-Curve)**: Smooth acceleration and deceleration - gentle, musical
- **[XFD-LIN-020] Linear**: Constant rate of change - precise, predictable

**[XFD-CURV-040] Independence:** Fade-in and fade-out curves are selected independently. A passage may use any combination (e.g., exponential fade-in with linear fade-out).

**[XFD-CURV-050] Application Timing:** Fade curves are applied to audio samples by the Fader component BEFORE buffering. The mixer reads pre-faded samples and performs simple addition during crossfade overlap. See [SPEC016 DBD-MIX-042] for architectural separation details.

## Crossfade Behavior

### Case 1: Following Passage Has Longer Lead-In Duration

**[XFD-BEH-C1-010]** When `Lead-Out Duration of Passage A ≤ Lead-In Duration of Passage B`:

```
Passage A: |---------------------------|******|
                         Lead-Out Point←   End←

Passage B:                             |*********|-------------------|
                                       ←Start    ←Lead-In Point

Timeline:  |---------------------------|------|------------------------|
           A playing alone             Both playing
                                              B playing alone
```

**[XFD-BEH-C1-020] Timing**: Passage B starts at its Start Time when Passage A reaches its Lead-Out Point.

**Example**:
- Passage A: Lead-Out Duration = 3 seconds
- Passage B: Lead-In Duration = 5 seconds
- Result: Passage B starts 3 seconds before Passage A ends (they overlap for 3 seconds)

### Case 2: Following Passage Has Shorter Lead-In Duration

**[XFD-BEH-C2-010]** When `Lead-Out Duration of Passage A > Lead-In Duration of Passage B`:

```
Passage A: |---------------------------|*************|
                         Lead-Out Point←          End←

Passage B:                                   |*******|-----------------|
                                             ←Start  ←Lead-In Point

Timeline:  |---------------------------------|-------|-----------------|
           A playing alone                   Both playing
                                                     B playing alone
```

**[XFD-BEH-C2-020] Timing**: Passage B starts at its Start Time when Passage A has `Lead-In Duration of B` remaining before its End Time.

**Example**:
- Passage A: Lead-Out Duration = 5 seconds, currently at time point where 2 seconds remain
- Passage B: Lead-In Duration = 2 seconds
- Result: Passage B starts now (they overlap for 2 seconds)

### Case 3: No Overlap (Zero Lead Durations)

**[XFD-BEH-C3-010]** When both Lead-In Duration and Lead-Out Duration are 0:

```
Passage A: |---------------------------|
                                       ←End

Passage B:                             |-----------------|
                                       ←Start

Timeline:  |---------------------------|------------------|
           A playing                   B playing
```

**[XFD-BEH-C3-020] Timing**: Passage B starts immediately when Passage A ends (no gap, no overlap).

## Implementation Algorithm

This section provides the complete algorithm for calculating crossfade timing and volume curves. See Single Stream Playback Architecture for details on how this algorithm is executed in the current single-stream implementation.

> **Historical Note:** Prior GStreamer dual-pipeline implementation archived in gstreamer_design.md.

### Crossfade Start Calculation

**[XFD-IMPL-010]** When Passage A is currently playing and Passage B is queued to play next, the system must calculate when to start Passage B and what crossfade duration to use.

**[XFD-IMPL-020]** Pseudocode for crossfade timing calculation:

```pseudocode
// Step 1: Retrieve or compute effective timing points for Passage A
passage_a_start = passage_a.start_time ?? 0.0
passage_a_end = passage_a.end_time ?? file_duration_a
passage_a_lead_out_point = passage_a.lead_out_point

if passage_a_lead_out_point is NULL:
    // Use clamped global Crossfade Time
    effective_crossfade_time = compute_clamped_crossfade_time(passage_a, passage_b)
    passage_a_lead_out_point = passage_a_end - effective_crossfade_time

passage_a_lead_out_duration = passage_a_end - passage_a_lead_out_point

// Step 2: Retrieve or compute effective timing points for Passage B
passage_b_start = passage_b.start_time ?? 0.0
passage_b_lead_in_point = passage_b.lead_in_point

if passage_b_lead_in_point is NULL:
    // Use clamped global Crossfade Time (same as computed above)
    effective_crossfade_time = compute_clamped_crossfade_time(passage_a, passage_b)
    passage_b_lead_in_point = passage_b_start + effective_crossfade_time

passage_b_lead_in_duration = passage_b_lead_in_point - passage_b_start

// Step 3: Determine crossfade duration
crossfade_duration = min(passage_a_lead_out_duration, passage_b_lead_in_duration)

// Step 4: Calculate when to start Passage B
// B starts when A has crossfade_duration remaining
passage_b_start_time = passage_a_end - crossfade_duration

// Step 5: Store calculated values for crossfade execution
crossfade_config = {
    passage_a_end: passage_a_end,
    passage_b_start_in_a: passage_b_start_time,  // Time in A's timeline
    passage_b_start_in_b: passage_b_start,       // Always start B from its start_time
    crossfade_duration: crossfade_duration
}
```

See [SPEC016 Decoder Buffer Design - Mixer](MCR-SPEC016-decoder_buffer_design.md) for implementation of crossfade mixing with overlapping passages ([DBD-MIX-040]).

**[XFD-IMPL-025] Architectural Note:** This algorithm calculates crossfade TIMING (when passages overlap). Fade curve APPLICATION is handled separately by the Fader component per [DBD-MIX-042]. The mixer implements simple addition of pre-faded samples per [DBD-MIX-041].

**[XFD-IMPL-026] Execution Architecture:** Crossfade timing is calculated by PlaybackEngine (calculation layer), which sets position markers at the crossfade start tick. The Mixer (execution layer) signals when that tick is reached via event-driven position tracking ([DBD-MIX-070] through [DBD-MIX-078]). This separates WHAT/WHEN decisions (engine) from playback reality detection (mixer).

**[XFD-IMPL-030]** Clamped Crossfade Time Calculation:

```pseudocode
function compute_clamped_crossfade_time(passage_a, passage_b) -> f64:
    global_crossfade_time = get_setting("crossfade_time")  // Default: 2.0 seconds

    passage_a_duration = passage_a.end_time - passage_a.start_time
    passage_b_duration = passage_b.end_time - passage_b.start_time

    // Apply 50% clamping rule
    max_allowed_a = passage_a_duration * 0.5
    max_allowed_b = passage_b_duration * 0.5

    // Use the most restrictive constraint
    max_allowed = min(max_allowed_a, max_allowed_b)

    // Clamp global setting if it exceeds 50% of either passage
    effective_crossfade_time = min(global_crossfade_time, max_allowed)

    return effective_crossfade_time
```

**[XFD-IMPL-040]** Timing Data Validation and Error Recovery:

Before calculating crossfade parameters, passage timing data must be validated and corrected if invalid. This handles corrupted database data, user input errors, or programmatic mistakes.

```pseudocode
function validate_and_correct_passage_timing(passage) -> ValidatedPassage:
    // Step 1: Establish start and end boundaries
    start_time = passage.start_time ?? 0.0
    end_time = passage.end_time ?? file_duration
    duration = end_time - start_time

    // Step 2: Validate start < end
    if start_time >= end_time:
        log_error("Passage {id}: Invalid start/end times (start={start_time}, end={end_time}). " +
                  "Start time must be less than end time. Setting start=0.0, end=file_duration.")
        start_time = 0.0
        end_time = file_duration
        duration = end_time - start_time

    // Step 3: Get timing points (may be NULL)
    fade_in_point = passage.fade_in_point
    lead_in_point = passage.lead_in_point
    lead_out_point = passage.lead_out_point
    fade_out_point = passage.fade_out_point

    // Step 4: Correct fade-in point if invalid
    if fade_in_point is not NULL:
        if fade_in_point < start_time:
            log_warning("Passage {id}: fade_in_point ({fade_in_point}) < start_time ({start_time}). " +
                       "Clamping fade_in_point to start_time.")
            fade_in_point = start_time
        if fade_in_point > end_time:
            log_warning("Passage {id}: fade_in_point ({fade_in_point}) > end_time ({end_time}). " +
                       "Clamping fade_in_point to end_time.")
            fade_in_point = end_time

    // Step 5: Correct lead-in point if invalid
    if lead_in_point is not NULL:
        if lead_in_point < start_time:
            log_warning("Passage {id}: lead_in_point ({lead_in_point}) < start_time ({start_time}). " +
                       "Clamping lead_in_point to start_time.")
            lead_in_point = start_time
        if lead_in_point > end_time:
            log_warning("Passage {id}: lead_in_point ({lead_in_point}) > end_time ({end_time}). " +
                       "Clamping lead_in_point to end_time.")
            lead_in_point = end_time

    // Step 6: Correct fade-out point if invalid
    if fade_out_point is not NULL:
        if fade_out_point < start_time:
            log_warning("Passage {id}: fade_out_point ({fade_out_point}) < start_time ({start_time}). " +
                       "Clamping fade_out_point to start_time.")
            fade_out_point = start_time
        if fade_out_point > end_time:
            log_warning("Passage {id}: fade_out_point ({fade_out_point}) > end_time ({end_time}). " +
                       "Clamping fade_out_point to end_time.")
            fade_out_point = end_time

    // Step 7: Correct lead-out point if invalid
    if lead_out_point is not NULL:
        if lead_out_point < start_time:
            log_warning("Passage {id}: lead_out_point ({lead_out_point}) < start_time ({start_time}). " +
                       "Clamping lead_out_point to start_time.")
            lead_out_point = start_time
        if lead_out_point > end_time:
            log_warning("Passage {id}: lead_out_point ({lead_out_point}) > end_time ({end_time}). " +
                       "Clamping lead_out_point to end_time.")
            lead_out_point = end_time

    // Step 8: Correct fade-in > fade-out ordering violation
    if fade_in_point is not NULL and fade_out_point is not NULL:
        if fade_in_point > fade_out_point:
            midpoint = (fade_in_point + fade_out_point) / 2.0
            log_warning("Passage {id}: fade_in_point ({fade_in_point}) > fade_out_point ({fade_out_point}). " +
                       "Setting both to midpoint ({midpoint}).")
            fade_in_point = midpoint
            fade_out_point = midpoint

    // Step 9: Correct lead-in > lead-out ordering violation
    if lead_in_point is not NULL and lead_out_point is not NULL:
        if lead_in_point > lead_out_point:
            midpoint = (lead_in_point + lead_out_point) / 2.0
            log_warning("Passage {id}: lead_in_point ({lead_in_point}) > lead_out_point ({lead_out_point}). " +
                       "Setting both to midpoint ({midpoint}).")
            lead_in_point = midpoint
            lead_out_point = midpoint

    // Step 10: Return validated passage
    return ValidatedPassage {
        start_time: start_time,
        end_time: end_time,
        fade_in_point: fade_in_point,
        lead_in_point: lead_in_point,
        lead_out_point: lead_out_point,
        fade_out_point: fade_out_point,
        fade_in_curve: passage.fade_in_curve,
        fade_out_curve: passage.fade_out_curve,
    }
```

**[XFD-IMPL-041]** Validation rules:
1. **Boundary clamping**: Points outside [start_time, end_time] are clamped to boundaries
2. **Ordering violations**: If fade-in > fade-out or lead-in > lead-out, both points set to midpoint
3. **Logging**: All corrections logged with warning level, including passage ID and corrected values
4. **Graceful degradation**: Never fail playback due to bad timing data; always recover

**[XFD-IMPL-042]** When validation is performed:
- **On passage load**: Before starting playback or calculating crossfade
- **On enqueue**: When passage added to queue via `POST /playback/enqueue`
- **On database read**: When reading passage definitions from database

**[XFD-IMPL-043]** Edge cases handled:
- **Both lead durations = 0**: Results in `crossfade_duration = 0` (Case 3, no overlap)
- **Negative durations from bad data**: Corrected by validation to produce duration ≥ 0
- **NULL timing points**: Handled by computing from global crossfade time (no validation needed)

## Validation Responsibility

The timing validation algorithm ([XFD-IMPL-040] through [XFD-IMPL-043]) is executed at three distinct phases with different ownership and failure handling:

### Phase 1: Enqueue-Time Validation

**Owner:** Audio Player API handler (`POST /playback/enqueue`)

**Scope:** Validate all timing points provided in enqueue request

**Validation Rules:**
- Apply [XFD-IMPL-040]: start_time < end_time
- Apply [XFD-IMPL-041]: fade/lead points within [start_time, end_time]
- Apply [XFD-IMPL-042]: fade points allow minimum fade duration
- Apply [XFD-IMPL-043]: lead points allow minimum crossfade overlap

**Action on Failure:**
- Return `400 Bad Request` with detailed validation error list
- Do not enqueue passage
- Do not modify database or queue

**Rationale:** Fail fast for user-provided invalid data. Prevents bad data from entering the system.

### Phase 2: Database Read Validation

**Owner:** Playback Engine (passage loading from `passages` table)

**Scope:** Validate passage definitions retrieved from database

**Validation Rules:**
- Same as Phase 1, but with correction capability
- Apply boundary clamping per [XFD-IMPL-040]
- Apply midpoint correction per [XFD-IMPL-041], [XFD-IMPL-042]

**Action on Failure:**
- Log warning with passage_id and applied corrections
- Use corrected values for playback
- Continue playback (graceful degradation)
- Do not modify database (corrections are runtime-only)

**Rationale:** Tolerate and correct corrupted database data. Prioritize playback continuity over strict validation.

### Phase 3: Pre-Decode Validation

**Owner:** Playback Engine (crossfade calculation before decoder queue submission)

**Scope:** Final validation before decode resources allocated

**Validation Rules:**
- Same as Phase 1 (strict, no correction)
- Includes audio file duration check (ensures end_time <= file_duration)

**Action on Failure:**
- Emit `PassageCompleted` event with `reason="invalid_timing"`
- Skip passage entirely
- Advance to next passage in queue
- Log error with passage_id and specific validation failure

**Rationale:** Last-resort safety check before expensive decode operation. Prevents resource waste on unplayable passages.

**Traceability:** XFD-VAL-010 (Three-phase validation strategy)

**[XFD-IMPL-050]** Case Detection:

The algorithm automatically handles all three cases:
- **Case 1** (passage_a_lead_out_duration ≤ passage_b_lead_in_duration): `crossfade_duration = passage_a_lead_out_duration`
- **Case 2** (passage_a_lead_out_duration > passage_b_lead_in_duration): `crossfade_duration = passage_b_lead_in_duration`
- **Case 3** (both durations = 0): `crossfade_duration = 0` (no overlap, passages play sequentially)

---

## Queue Advancement Timing

This section specifies WHEN queue advancement occurs during playback and crossfades.

**[XFD-QUEUE-ADV-010]** Queue advancement occurs when the currently playing passage reaches its **end sample**.

**Definition:** The "end sample" is the audio sample corresponding to the passage's `end_time` ([XFD-PT-060]). This is the last sample of audio data that will be played for this passage.

**[XFD-QUEUE-ADV-020]** During normal (non-crossfade) playback:
- Passage plays from `start_time` to `end_time`
- When the sample at `end_time` has been placed in the playout buffer, passage playback is complete
- Queue advancement occurs immediately after completion
- Next passage (if any) begins playing from its `start_time`

**[XFD-QUEUE-ADV-030]** During crossfade overlap:
- Current passage continues playing until `end_time` is reached
- Next passage starts playing based on lead-in/lead-out calculation ([XFD-IMPL-020])
- Both passages play simultaneously during the crossfade overlap period
- When current passage `end_time` sample has been placed in playout buffer, queue advancement occurs
- After queue advancement, what was the "next" passage becomes the new "current" passage
- The new current passage continues playing seamlessly (no interruption)

**[XFD-QUEUE-ADV-040]** Queue advancement timing ensures:
- No interruption of incoming passage playback during crossfade
- Clean transition from `Crossfading` to `SinglePassage` mixer state
- Consistent queue state with mixer state
- Completed passage buffer can be released for cleanup

**[XFD-QUEUE-ADV-050]** Implementation coordination:

Queue advancement requires coordination between multiple components:

1. **Buffer Component** ([SPEC016 DBD-BUF-060]): "When the sample corresponding to the passage end time is removed from the buffer, the buffer informs the queue that passage playout has completed"

2. **Mixer Component** ([SPEC018]): Detects crossfade completion when current passage reaches `end_time`, signals engine via completion flag

3. **Engine Component** ([SPEC018]): Polls for completion signals, advances queue when notified, cleans up completed passage buffer

**[XFD-QUEUE-ADV-060]** Timing precision:

Queue advancement timing uses the tick-based timing system ([SPEC017]):
- `end_time_ticks`: Exact sample position where passage ends
- No floating-point rounding errors
- Sample-accurate queue advancement

See SPEC018 Crossfade Completion Coordination for detailed implementation of crossfade completion signaling between mixer and engine.

---

### Pre-Loading Strategy

**[XFD-IMPL-060]** To enable seamless crossfading, the next passage must be pre-loaded into the idle pipeline before the crossfade begins.

**[XFD-IMPL-070]** Pre-loading trigger calculation:

```pseudocode
// Calculate when to trigger pre-loading of next passage
preload_buffer_time = 5.0  // seconds before crossfade starts
preload_trigger_point = passage_b_start_time - preload_buffer_time

// When current playback position in A reaches preload_trigger_point:
if current_position_a >= preload_trigger_point:
    load_passage_into_idle_pipeline(passage_b)
    transition_pipeline_to_paused_state(idle_pipeline)
```

Note: 5-second pre-load buffer is 1/3 of [SPEC016 DBD-PARAM-070] playout_ringbuffer_size (15.01 seconds @ 44.1kHz). See [SPEC016 Operating Parameters](MCR-SPEC016-decoder_buffer_design.md).

**[XFD-IMPL-080]** Pre-loading ensures buffers are decoded and ready before crossfade begins, preventing audio glitches.

> See Single Stream Playback Architecture for complete buffer pre-loading details.

### Volume Fade Curve Formulas

**[XFD-IMPL-090]** During crossfade, each passage's volume is controlled by applying a fade curve. The fade curve maps normalized time `t` (where 0.0 = fade start, 1.0 = fade end) to volume multiplier `v` (where 0.0 = silence, 1.0 = full volume).

#### Fade-In Curves (0.0 → 1.0 volume)

**[XFD-IMPL-091] Linear Fade-In:**
```
v(t) = t

where:
  t ∈ [0.0, 1.0] (normalized time through fade)
  v ∈ [0.0, 1.0] (output volume multiplier)
```

**[XFD-IMPL-092] Exponential Fade-In:**
```
v(t) = tÂ²

where:
  t ∈ [0.0, 1.0]
  v ∈ [0.0, 1.0]

Characteristic: Slow start, fast finish (perceived as "natural" swell)
```

**[XFD-IMPL-093] Cosine Fade-In (S-Curve):**
```
v(t) = 0.5 Ã— (1 - cos(Ï€ Ã— t))

where:
  t ∈ [0.0, 1.0]
  v ∈ [0.0, 1.0]
  Ï€ ∈ 3.14159265359

Characteristic: Smooth acceleration and deceleration
```

#### Fade-Out Curves (1.0 → 0.0 volume)

**[XFD-IMPL-094] Linear Fade-Out:**
```
v(t) = 1.0 - t

where:
  t ∈ [0.0, 1.0] (normalized time through fade)
  v ∈ [1.0, 0.0] (output volume multiplier)
```

**[XFD-IMPL-095] Logarithmic Fade-Out:**
```
v(t) = (1.0 - t)Â²

where:
  t ∈ [0.0, 1.0]
  v ∈ [1.0, 0.0]

Characteristic: Fast start, slow finish (perceived as "natural" decay)
```

**[XFD-IMPL-096] Cosine Fade-Out (S-Curve):**
```
v(t) = 0.5 Ã— (1 + cos(Ï€ Ã— t))

where:
  t ∈ [0.0, 1.0]
  v ∈ [1.0, 0.0]
  Ï€ ∈ 3.14159265359

Characteristic: Smooth acceleration and deceleration
```

> See Single Stream Playback - Fade Curve Algorithms for implementation of these formulas in the audio pipeline.

#### Normalized Time Calculation

**[XFD-IMPL-100]** For any fade operation, normalized time `t` is calculated as:

```pseudocode
// For fade-in (from fade start to fade-in point)
fade_in_duration = fade_in_point - start_time
current_fade_in_position = current_time - start_time
t_fade_in = current_fade_in_position / fade_in_duration  // Clamp to [0.0, 1.0]

// For fade-out (from fade-out point to end)
fade_out_duration = end_time - fade_out_point
current_fade_out_position = current_time - fade_out_point
t_fade_out = current_fade_out_position / fade_out_duration  // Clamp to [0.0, 1.0]
```

#### Volume Application During Crossfade

**[XFD-IMPL-110]** Complete volume calculation for a passage at time `t`:

```pseudocode
function calculate_passage_volume(passage, current_time) -> f64:
    volume = 1.0  // Start at full volume

    // Apply fade-in if in fade-in region
    if current_time < passage.fade_in_point:
        fade_in_duration = passage.fade_in_point - passage.start_time
        if fade_in_duration > 0:
            t = (current_time - passage.start_time) / fade_in_duration
            t = clamp(t, 0.0, 1.0)
            volume *= apply_fade_in_curve(t, passage.fade_in_curve)

    // Apply fade-out if in fade-out region
    if current_time >= passage.fade_out_point:
        fade_out_duration = passage.end_time - passage.fade_out_point
        if fade_out_duration > 0:
            t = (current_time - passage.fade_out_point) / fade_out_duration
            t = clamp(t, 0.0, 1.0)
            volume *= apply_fade_out_curve(t, passage.fade_out_curve)

    return volume

function apply_fade_in_curve(t, curve_type) -> f64:
    switch curve_type:
        case "linear":      return t
        case "exponential": return t * t
        case "cosine":      return 0.5 * (1.0 - cos(Ï€ * t))

function apply_fade_out_curve(t, curve_type) -> f64:
    switch curve_type:
        case "linear":      return 1.0 - t
        case "logarithmic": return (1.0 - t) * (1.0 - t)
        case "cosine":      return 0.5 * (1.0 + cos(Ï€ * t))
```

**[XFD-IMPL-120]** During crossfade overlap, both passages have their volumes calculated independently, then mixed:

```pseudocode
// At any time during crossfade overlap:
volume_a = calculate_passage_volume(passage_a, current_time_in_a)
volume_b = calculate_passage_volume(passage_b, current_time_in_b)

// Mix audio streams (performed by CrossfadeMixer)
mixed_audio = (audio_a * volume_a) + (audio_b * volume_b)

// Apply resume-from-pause fade-in if active (see Pause/Resume section below)
if in_resume_from_pause_fade_in:
    resume_fade_in_level = calculate_resume_fade_in_level(time_since_resume)
    mixed_audio *= resume_fade_in_level

// Apply master volume (0.0-1.0, set by user via POST /audio/volume)
final_output = mixed_audio * master_volume
```

**[XFD-IMPL-130]** Volume Fade Update Frequency:

Volume curves are evaluated and applied at regular intervals controlled by the `volume_fade_update_period` setting.

```pseudocode
function volume_update_loop():
    update_period_ms = get_setting("volume_fade_update_period")  // Default: 10ms

    while playback_active:
        current_time = get_current_time()

        // Calculate passage volumes (with crossfade curves if applicable)
        if passage_a_active:
            volume_a = calculate_passage_volume(passage_a, current_time_in_a)
            set_pipeline_volume(pipeline_a, volume_a)

        if passage_b_active:
            volume_b = calculate_passage_volume(passage_b, current_time_in_b)
            set_pipeline_volume(pipeline_b, volume_b)

        // Apply resume-from-pause fade-in if active
        if in_resume_from_pause_fade_in:
            resume_level = calculate_resume_fade_in_level(time_since_resume)
            set_master_fade_multiplier(resume_level)
        else:
            set_master_fade_multiplier(1.0)

        // Sleep until next update
        sleep(update_period_ms)
```

**Configuration:**
- **Setting key**: `volume_fade_update_period`
- **Type**: INTEGER (milliseconds)
- **Default**: 10ms (100 Hz update rate)
- **Valid range**: 1-100ms
- **Trade-offs**:
  - Lower values (e.g., 5ms): Smoother fades, higher CPU usage
  - Higher values (e.g., 50ms): Lower CPU usage, may have audible steps in fade curves
  - Recommended: 10ms provides smooth, artifact-free transitions with reasonable CPU usage

Note: This is distinct from [SPEC016 DBD-PARAM-111] mixer_check_interval_ms (10ms), which controls mixer thread wake timing. Volume updates occur more frequently for smooth fade curves.

**Implementation Notes:**
- Update timer runs continuously during playback (even when not crossfading)
- When not in crossfade or resume fade-in, volumes remain constant (no calculations needed)
- Timer precision affects fade smoothness; use high-resolution timer if available
- See Single Stream Design for implementation performance details

---

## Pause and Resume Behavior

This section specifies how pause and resume operations affect audio playback, distinct from passage-to-passage crossfades.

### Pause Behavior

**[XFD-PAUS-010]** When user initiates pause (`POST /playback/pause`):

- **Fade-out duration**: None (immediate stop)
- **Fade-out curve**: Does not apply
- **Implementation**: Audio output volume set to 0.0 immediately
- **Playback state**: Position tracking continues to maintain accurate position
- **Playback position**: Preserved at moment of pause
- **Crossfade interaction**: If pause occurs during crossfade, both passages are muted immediately

See [SPEC016 Mixer - Pause Mode](MCR-SPEC016-decoder_buffer_design.md) for mixer implementation of pause behavior:
- [DBD-MIX-050]: Exponential decay output during pause
- [DBD-MIX-051]: pause_decay_factor ([DBD-PARAM-090], default 0.9999)
- [DBD-MIX-052]: pause_decay_floor ([DBD-PARAM-100], default 0.0001)

**Rationale:** Pause is conceptually an immediate stop, not a gradual fade. Users expect instant response when pausing.

### Resume Behavior

**[XFD-PAUS-020]** When user initiates resume (`POST /playback/play` from Paused state):

- **Fade-in duration**: `resume_from_pause_fade_in_duration` seconds (configurable, default: 0.5)
- **Fade-in curve**: `resume_from_pause_fade_in_curve` (configurable, default: "exponential")
- **Available curves**: All fade-in curves used for crossfades:
  - `"linear"`: v(t) = t
  - `"exponential"`: v(t) = tÂ²
  - `"cosine"`: v(t) = 0.5 Ã— (1 - cos(Ï€ Ã— t))

**[XFD-PAUS-030]** Resume fade-in calculation:

```pseudocode
function calculate_resume_fade_in_level(time_since_resume) -> f64:
    fade_in_duration = get_setting("resume_from_pause_fade_in_duration")  // Default: 0.5
    fade_in_curve = get_setting("resume_from_pause_fade_in_curve")        // Default: "exponential"

    if time_since_resume >= fade_in_duration:
        return 1.0  // Fade-in complete

    t = time_since_resume / fade_in_duration
    t = clamp(t, 0.0, 1.0)

    return apply_fade_in_curve(t, fade_in_curve)
```

**[XFD-PAUS-040]** Resume fade-in is applied **multiplicatively** with all other volume levels:

```pseudocode
// During resume fade-in:
passage_volume_a = calculate_passage_volume(passage_a, current_time)  // Includes any crossfade curves
passage_volume_b = calculate_passage_volume(passage_b, current_time)  // (if crossfading)

mixed_audio = (audio_a * passage_volume_a) + (audio_b * passage_volume_b)

// Resume fade-in multiplies the mixed result
resume_level = calculate_resume_fade_in_level(time_since_resume)
mixed_audio *= resume_level

// Master volume applied last
final_output = mixed_audio * master_volume
```

**[XFD-PAUS-050]** Key characteristics of resume fade-in:

- **Applies to all audible audio**: Affects all passages equally (both during crossfade if applicable)
- **Independent of crossfade**: Resume fade-in is orthogonal to passage crossfade curves
- **Update frequency**: Same as crossfade volume updates (`volume_fade_update_period` setting, default 10ms)
- **Completes after duration**: Once `time_since_resume >= fade_in_duration`, resume level remains at 1.0

**Example scenario:** User resumes during crossfade:
1. Passage A is fading out (volume_a = 0.3 from fade-out curve)
2. Passage B is fading in (volume_b = 0.7 from fade-in curve)
3. Resume fade-in active (resume_level = 0.5 at 0.25s into 0.5s fade-in)
4. Mixed audio = (audio_a Ã— 0.3) + (audio_b Ã— 0.7)
5. With resume fade-in: mixed_audio Ã— 0.5
6. With master volume (e.g., 0.8): mixed_audio Ã— 0.5 Ã— 0.8 = final output

### Configuration

**[XFD-PAUS-060]** Resume fade-in settings stored in database `settings` table:

- **Key**: `resume_from_pause_fade_in_duration`
  - **Type**: REAL (seconds)
  - **Default**: 0.5
  - **Valid range**: 0.0 to 5.0
  - **Value 0.0**: Instant resume (no fade-in)

- **Key**: `resume_from_pause_fade_in_curve`
  - **Type**: TEXT
  - **Default**: "exponential"
  - **Valid values**: "linear", "exponential", "cosine"

See database_schema.md - settings table for complete settings documentation.

### Implementation Notes

**[XFD-IMPL-140]** Key implementation considerations:

1. **Timing precision**: **CORRECTION:** Timing precision uses tick-based representation (not floating-point seconds).

   See SPEC017 Problem Statement:
   - [SRC-PROB-020]: Floating-point seconds introduce cumulative rounding errors
   - [SRC-SOL-010]: Use i64 ticks for lossless precision
   - [SRC-TICK-020]: tick_rate = 28,224,000 Hz (LCM of common sample rates)
   - [SRC-TICK-030]: One tick ≈ 35.4 nanoseconds

   Internal timing uses ticks; floating-point may be used for logging/display only.

2. **Thread safety**: Crossfade calculations performed on playback thread; volume updates applied atomically during frame generation
3. **Edge case handling**: Zero-duration fades (instant transitions) handled by setting volume directly without interpolation
4. **Fade curve symmetry**: Exponential fade-in pairs with logarithmic fade-out for perceptually balanced crossfades

> See Single Stream Playback Architecture - Crossfade Execution for complete implementation details.

## Fade Behavior During Crossfade

**[XFD-FADE-010]** Fades operate independently of crossfade overlap

See [SPEC016 Fade In/Out handlers](MCR-SPEC016-decoder_buffer_design.md) for fade curve application implementation:
- [DBD-FADE-030]: Fade-in curve application
- [DBD-FADE-050]: Fade-out curve application

## Volume Calculations

**[XFD-VOL-010]** During crossfade overlap, audio streams are mixed:

```
Final Output = (Passage A Audio Ã— A Volume) + (Passage B Audio Ã— B Volume)
```

See [SPEC016 Mixer](MCR-SPEC016-decoder_buffer_design.md) for implementation details of volume mixing during crossfade ([DBD-MIX-040]).

**[XFD-VOL-020]** Where volume at any time `t` is determined by:
- Current passage fade state (fade-in/fade-out curve)
- Resume-from-pause fade (0.5s exponential ramp, if applicable)
- User-controlled master volume

### Clipping Prevention
**[XFD-VOL-030]** There is no clipping prevention at runtime. If the crossfade configuration results in net volume > 100% clipping will occur.

**[XFD-VOL-040]** During user editing of fade-in, fade-out, lead-in, lead-out points, the user interface displays the passage audio amplitude over time graphically (see [Visual Editor](#visual-editor)
section), and warns the user if the peak audio amplitude exceeds 50% during any portion of the lead-in or lead-out time windows that is not also covered by
corresponding fade-in or fade-out time windows. This warning indicates potential for clipping if this passage overlaps with another passage also at high volume.

**[XFD-VOL-050] Amplitude Measurement:**
  - Peak amplitude is measured from the audio file's waveform data
  - Measured as the maximum absolute sample value in the relevant time window
  - Does not include fade curve or master volume adjustments
  - Checks the raw audio amplitude to detect potential clipping before fades are applied
    - Warning is shown if > 50% amplitude is detected in relevant time window and no fade-in/fade-out curve is applied at that point in the passage playback 

### Resume from Pause

**[XFD-PAUS-010]** When resuming from Pause:
- **[XFD-PAUS-011]** 0.5 second exponential volume ramp from 0% to 100%
- **[XFD-PAUS-012]** Applied AFTER any crossfade calculations
  - all crossfade calculations complete, then multiply the result by the resume from pause ramp value
  - ((A_audio Ã— A_fade) + (B_audio Ã— B_fade)) Ã— resume_ramp Ã— master
- **[XFD-PAUS-013]** Affects all playing passages simultaneously

> Implements requirement: [Crossfade Handling](MCR-REQ001-requirements.md)
  
## Default Configuration

**[XFD-DEF-010]** New passages are created with:
- Start Time: NULL (uses start of file)
- Fade-In Point: NULL (uses global Crossfade Time)
- Lead-In Point: NULL (uses global Crossfade Time)
- Lead-Out Point: NULL (uses global Crossfade Time)
- Fade-Out Point: NULL (uses global Crossfade Time)
- End Time: NULL (uses end of file)
- Fade-In Curve: NULL (uses global Fade Curve selection's fade-in component)
- Fade-Out Curve: NULL (uses global Fade Curve selection's fade-out component)

### Global Crossfade Time
**[XFD-DEF-020]** A single global value "Crossfade Time" is used when Fade-In Point, Fade-Out Point, Lead-In Point, Lead-Out Point are undefined.
- When Fade-In Point is undefined, the effective Fade-In point is: Start Time + Crossfade Time (Clamped when needed)
- When Lead-In Point is undefined, the effective Lead-In point is: Start Time + Crossfade Time (Clamped when needed)
- When Fade-Out Point is undefined, the effective Fade-Out point is: End Time - Crossfade Time (Clamped when needed)
- When Lead-Out Point is undefined, the effective Lead-Out point is: End Time - Crossfade Time (Clamped when needed)

**[XFD-DEF-030] NULL Point Computation Timing:**
  - NULL timing points are NOT pre-computed or stored as effective values in the database
  - Instead, NULL values remain NULL in storage and are resolved dynamically when a passage is currently playing and a following passage is waiting in the queue to play next
  - When both passage identities are first known, the system:
    1. Determines the effective Crossfade Time (applying 50% clamping if needed per Crossfade Time Clamping below)
    2. Computes effective timing points for any NULL values using the clamped Crossfade Time
    3. Applies Crossfade Behavior Cases 1/2/3 using the computed effective durations
  - User-defined (non-NULL) timing points are never modified by clamping
  - This ensures the 50% constraint is satisfied for all applications of undefined lead-in and lead-out times.
  - Fade-In and Fade-Out NULL points are also computed using the clamped Crossfade Time, ensuring consistent behavior across all timing points.

**[XFD-DEF-040]** Crossfade Time is user editable:
  - Minimum Crossfade Time is: 0.0 seconds
  - Maximum Crossfade Time is: 30.0 seconds
- Default (before user editing) Crossfade Time is 2.0 seconds
- User edited Crossfade Time is persisted across power-off sessions

### Crossfade Time Clamping
**[XFD-DEF-050]** User defined fade-in, fade-out, lead-in, lead-out durations are constrained during editing and setting as described above.

**[XFD-DEF-060]** The global Crossfade Time is clamped during playback as follows:
  - When one passage is playing and the queue contains another passage to play next, the global user selected Crossfade Time is compared with
    the End Time - Start Time durations of either or both passages.  If the global user selected Crossfade Time is > 50% of either passage duration,
    effective Crossfade Time is 50% of the shorter passage duration.
  - If a playing passage has a defined (non NULL) lead-out duration, that is unaffected by Crossfade clamping
  - If a passage next for play in the queue has a defined (non NULL) lead-in duration, that is unaffected by Crossfade clamping

**[XFD-DEF-061] Rationale for Asymmetric Clamping:**

This intentional asymmetry between user-defined and NULL timing points serves specific purposes:
  - **NULL points (clamped):** When users haven't customized timing, the 50% rule prevents the global setting from creating excessive overlap on short passages
  - **User-defined points (not clamped):** When users explicitly set timing points, they have made a deliberate choice for that specific passage. The system respects this choice even if it exceeds 50% of passage duration
  - **Design philosophy:** Global defaults should be conservative and safe, while per-passage customization grants full control to users who understand their content

**Use case example:** A 10-second intro passage might have a user-defined 8-second lead-out (80% of duration) to ensure it crossfades smoothly into the next passage. This is intentional and should not be overridden by automatic clamping.

**[XFD-DEF-062] Warning Logic:**
- The UI warns when user sets **lead-in point** > (0.5 Ã— passage duration)
- The UI warns when user sets **lead-out point** < (0.5 Ã— passage duration) [i.e., lead-out duration > 50%]
- Warnings are **informational only**; values are still accepted
- Warning message: "Lead time exceeds 50% of passage duration. This may cause excessive overlap during crossfades."
- **Rationale:** Warns about individual passage configuration that *could* create long overlaps, but doesn't predict actual crossfade duration (which depends on both passages)

Example:
- Typical scenario, lead-out of passage A and lead-in of passage B are both NULL:
  - Crossfade Time set to 5 seconds
  - Passage A is 180 seconds duration, lead-out duration undefined
  - Passage B is 240 seconds duration, lead-in duration undefined
  - When Passage A is playing and Passage B is in the queue to play next, Crossfade Time is compared to the passage times and found to be < 50% of both, so it is used as is.
  
- Scenario A where Crossfade Time Clamping comes into effect, lead-out of passage A and lead-in of passage B are both NULL:
  - Crossfade Time set to 30 seconds
  - Passage A is 40 seconds duration, lead-out duration undefined
  - Passage B is 240 seconds duration, lead-in duration undefined
  - When Passage A is playing and Passage B is in the queue to play next, Crossfade Time is compared to the passage times and found to be > 50% of passage A's duration, so the 
    effective Crossfade Time used to play passage A and B overlapping is 20 seconds, 50% of passage A's duration.  When passage A reaches 20 seconds from its end, simultaneous
    play of passage B begins.
  
- Scenario B where Crossfade Time Clamping comes into effect, lead-out of passage A and lead-in of passage B are both NULL:
  - Crossfade Time set to 30 seconds
  - Passage A is 180 seconds duration, lead-out duration undefined
  - Passage B is 30 seconds duration, lead-in duration undefined
  - When Passage A is playing and Passage B is in the queue to play next, Crossfade Time is compared to the passage times and found to be > 50% of passage B's duration, so the
    effective Crossfade Time used to play passage A and B overlapping is 15 seconds, 50% of passage B's duration.  When passage A reaches 15 seconds from its end, simultaneous
    play of passage B begins.
  
- Scenario C where Crossfade Time Clamping comes into effect, lead-out of passage A and lead-in of passage B are both NULL:
  - Crossfade Time set to 25 seconds
  - Passage A is 30 seconds duration, lead-out duration undefined
  - Passage B is 40 seconds duration, lead-in duration undefined
  - When Passage A is playing and Passage B is in the queue to play next, Crossfade Time is compared to the passage times and found to be > 50% of both passage A and B's duration,
    so the effective Crossfade Time used to play passage A and B overlapping is 15 seconds, 50% of the shorter passage A's duration.  When passage A reaches 15 seconds from its end,
    simultaneous play of passage B begins.

- Scenario D where Crossfade Time Clamping comes into effect, lead-out of passage A is set to 20 seconds lead-in of passage B is NULL:
  - Crossfade Time set to 30 seconds
  - Passage A is 30 seconds duration, lead-out duration set to 20 seconds
  - Passage B is 50 seconds, lead-in duration undefined
  - When Passage A is playing and Passage B is in the queue to play next, Crossfade Time is compared to the passage B's duration and found to be > 50%, so the effective Crossfade Time
    used for passage B's lead-in duration is 15 seconds, 50% of the shorter passage A's duration.  When passage A reaches 15 seconds from its end, simultaneous play of passage B begins.

- Scenario E where Crossfade Time Clamping comes into effect, lead-out of passage A is NULL, lead-in of passage B is set to 20 seconds:
  - Crossfade Time set to 25 seconds
  - Passage A is 32 seconds duration, lead-out duration undefined
  - Passage B is 50 seconds, lead-in duration is set to 20 seconds
  - When Passage A is playing and Passage B is in the queue to play next, Crossfade Time is compared to the passage A's duration and found to be > 50%, so the effective Crossfade Time
    used for passage A's lead-out duration is 16 seconds, 50% of the shorter passage A's duration.  When passage A reaches 16 seconds from its end, simultaneous play of passage B begins.

- Scenario F: Both passages have user-defined lead times that are extreme values (no clamping needed):
  - Crossfade Time set to 30 seconds (not used in this scenario)
  - Passage A is 20 seconds duration
    - User-defined lead-in: 2.0 seconds
    - User-defined lead-out: 18.0 seconds (90% through the passage)
  - Passage B is 15 seconds duration
    - User-defined lead-in: 14.0 seconds (93% through the passage)
    - User-defined lead-out: 14.5 seconds
  - **Analysis:**
    - Passage A remaining time after lead-out: 20.0 - 18.0 = 2.0 seconds
    - Passage B lead-in time: 14.0 seconds
    - Crossfade duration: min(2.0, 14.0) = **2.0 seconds**
  - **Result:**
    - No clamping needed
    - No warning needed
    - Crossfade begins at 18.0s in Passage A
    - Crossfade ends at 14.0s in Passage B (when B reaches its lead-in point)
    - The `min()` function naturally constrains crossfade to valid range
    - Passage A plays from 18.0s → 20.0s (2 seconds) while B plays from 0.0s → 2.0s
    - At 2.0s into crossfade, A completes and B continues solo from 2.0s → 14.0s
    - Even extreme user-defined values cannot cause >100% overlap or invalid states
  - **Key Insight:** The crossfade system is self-constraining. User-defined lead times that seem "extreme" (e.g., lead-out at 90% of passage) are perfectly valid because:
    1. Lead-in must be < lead-out within each passage (enforced at input time)
    2. Crossfade duration = min(remaining_A, lead_in_B)
    3. This guarantees crossfade never exceeds either passage's valid range

## Queue Entry Timing Specification

When passages are enqueued via `/playback/enqueue` API endpoint, timing points may be explicitly specified or omitted to use passage/global defaults.

**[XFD-QUEUE-010]** JSON Format:
```json
{
  "file_path": "relative/path/to/file.mp3",
  "start_time_ms": 0,
  "end_time_ms": 234500,
  "lead_in_point_ms": 0,
  "lead_out_point_ms": 224500,
  "fade_in_point_ms": 0,
  "fade_out_point_ms": 234500,
  "fade_in_curve": "cosine",
  "fade_out_curve": "cosine",
  "passage_guid": "uuid-optional"
}
```

**[XFD-QUEUE-020]** Default Resolution Order:
1. Use explicit value from enqueue request if provided
2. Fall back to passage definition from database if available
3. Fall back to global setting (Crossfade Time, Fade Curve)
4. Use built-in default

**[XFD-QUEUE-030]** Timing Value Format:
- All timing values are unsigned integer milliseconds
- `lead_in_point_ms`, `fade_in_point_ms`, `lead_out_point_ms`, `fade_out_point_ms` are relative to `start_time_ms`

See SPEC017 Sample Rate Conversion - API Representation for conversion between milliseconds (API) and ticks (internal storage, [SRC-API-020]: ms * 28224).
- When converting from database (seconds) to API (milliseconds): multiply by 1000
- When converting from API (milliseconds) to audio samples: multiply by `(sample_rate / 1000)`

> See API Design - POST /playback/enqueue for complete endpoint specification.

### Global Fade Curve Selection

**[XFD-DEF-070]** The Global Fade Curve setting selects a **paired** fade-in/fade-out curve combination used as the default for passages where `fade_in_curve` and/or `fade_out_curve` are undefined (NULL).

**[XFD-DEF-071] Global Fade Curve Options:**

1. **Exponential In / Logarithmic Out** (default, recommended for smooth natural crossfades)
   - Database value: `'exponential_logarithmic'`
   - Fade-In: Exponential (v(t) = tÂ²)
   - Fade-Out: Logarithmic (v(t) = (1-t)Â²)

2. **Linear In / Linear Out**
   - Database value: `'linear_linear'`
   - Fade-In: Linear (v(t) = t)
   - Fade-Out: Linear (v(t) = 1-t)

3. **Cosine In / Cosine Out (S-Curve)**
   - Database value: `'cosine_cosine'`
   - Fade-In: Cosine (v(t) = 0.5 Ã— (1 - cos(Ï€ Ã— t)))
   - Fade-Out: Cosine (v(t) = 0.5 Ã— (1 + cos(Ï€ Ã— t)))

**[XFD-DEF-072] Application:**
- When passage `fade_in_curve` is NULL: Use fade-in component of global setting
- When passage `fade_out_curve` is NULL: Use fade-out component of global setting
- When both are NULL: Use complete paired curve from global setting
- When one is defined and one is NULL: Mix passage-specific curve with global default

**Example:** Global setting is "Exponential In / Logarithmic Out"
- Passage A (both NULL): Uses exponential fade-in, logarithmic fade-out
- Passage B (fade-in = linear, fade-out = NULL): Uses linear fade-in, logarithmic fade-out
- Passage C (fade-in = NULL, fade-out = cosine): Uses exponential fade-in, cosine fade-out
- Passage D (both defined): Ignores global setting entirely

**[XFD-DEF-080] Persistence:** User-selected global Fade Curve setting is persisted across power-off sessions.

### Commentary

The Global Crossfade Time and Fade Curve Selection should be good for most passages, but in cases where it is not the ability to individually tailor Fade-In, Fade-Out, Lead-In, Lead-Out points and Fade-In, Fade-Out curves gives the users the ability to address specific cases that don't work well with a simpler global selection.

## Database Storage

**[XFD-DB-010]** **CORRECTION:** Database stores timing as INTEGER ticks, not seconds.

See SPEC017 Database Storage for tick-based storage format:
- [SRC-DB-011]: start_time_ticks (INTEGER NOT NULL)
- [SRC-DB-012]: fade_in_start_ticks (INTEGER)
- [SRC-DB-013]: lead_in_start_ticks (INTEGER)
- [SRC-DB-014]: lead_out_start_ticks (INTEGER)
- [SRC-DB-015]: fade_out_start_ticks (INTEGER)
- [SRC-DB-016]: end_time_ticks (INTEGER NOT NULL)

Conversion: ticks = seconds * 28,224,000 ([SRC-TICK-020])

**Fade curve storage (unchanged):**
- `fade_in_curve` (nullable enum: 'exponential', 'cosine', 'linear'; NULL = use global default)
- `fade_out_curve` (nullable enum: 'logarithmic', 'cosine', 'linear'; NULL = use global default)

**[XFD-DB-020]** NULL values for timing points use global Crossfade Time as described in Default Configuration. NULL values for curves use global Fade Curve selection.

### Settings Table

**[XFD-DB-030]** Global settings are persisted in a key-value settings table.

See database_schema.md#settings for the definition of the settings table.

## User Interface Considerations

### Visual Editor
**[XFD-UIX-010]** Should display:
- **[XFD-VIS-010]** Waveform with six draggable markers
- **[XFD-VIS-020]** Preview of crossfade overlap with adjacent passages
- **[XFD-VIS-030]** Real-time audio preview of crossfade region

### Validation
**[XFD-UIX-020]** Validation requirements:
- **[XFD-VAL-010]** Enforce temporal constraints when user moves markers (Start ≤ Fade-In ≤ Fade-Out ≤ End, Start ≤ Lead-In ≤ Lead-Out ≤ End)
- **[XFD-VAL-020]** Warn if lead-in or lead-out duration > 50% of passage duration (see [XFD-DEF-062] for warning logic)
- **[XFD-VAL-030]** Suggest sensible defaults based on passage characteristics

## Edge Cases

### First Passage in Queue
**[XFD-EDGE-010]** First passage behavior:
- **[XFD-FIRST-010]** No previous passage to crossfade from
- **[XFD-FIRST-020]** Fade-in applies
- **[XFD-FIRST-030]** If Fade-In Duration > 0, passage starts at zero volume and fades in

### Last Passage in Queue
**[XFD-EDGE-020]** Last passage behavior:
- **[XFD-LAST-010]** No next passage to crossfade to
- **[XFD-LAST-020]** Fade-out applies if defined
- **[XFD-LAST-030]** Passage plays to End Time

### User Skip During Crossfade
**[XFD-EDGE-030]** Skip during crossfade:
- **[XFD-SKIP-010]** Both passages stop immediately
- **[XFD-SKIP-020]** Next passage from the queue, after the one that was starting during crossfade, begins according to its own timing rules

### Pause During Crossfade
**[XFD-EDGE-040]** Pause during crossfade:
- **[XFD-PAUS-030]** Both passages pause at current position
- **[XFD-PAUS-040]** Resume applies 0.5s ramp to both passages
- **[XFD-PAUS-050]** Crossfade relationship preserved

### Queue Modification During Crossfade
**[XFD-EDGE-050]** Queue modification during crossfade:
- **[XFD-QMOD-010]** If next passage removed: current passage plays to completion
- **[XFD-QMOD-020]** If current passage removed: next passage treated as "first passage in queue"

----
End of document - Crossfade Design

**Document Version:** 1.3
**Last Updated:** 2025-01-30

**Change Log:**
- v1.3 (2025-01-30): Added event-driven execution architecture clarification
  - Added [XFD-IMPL-026] Execution Architecture: Documents calculation vs. execution layer separation
  - Clarifies PlaybackEngine calculates crossfade timing and sets position markers
  - Clarifies Mixer signals when ticks reached via event-driven position tracking
  - Cross-references SPEC016 [DBD-MIX-070] through [DBD-MIX-078] for position marker system
  - Addresses architectural refinement from PLAN014 mixer refactoring
- v1.2 (2025-10-25): Added architectural clarifications for fade vs lead orthogonality and queue advancement timing
  - Added "Fade Points vs Lead Points: Orthogonal Concepts" section with requirements [XFD-ORTH-010] through [XFD-ORTH-025]
  - Clarified that Fader applies fade curves BEFORE buffering, mixer simply sums pre-faded audio during overlap
  - Added practical example showing fade application timeline vs crossfade overlap timing
  - Added "Queue Advancement Timing" section with requirements [XFD-QUEUE-ADV-010] through [XFD-QUEUE-ADV-060]
  - Specified that queue advancement occurs when current passage reaches end_time sample
  - Documented coordination between Buffer, Mixer, and Engine components for completion detection
  - Cross-referenced SPEC016 (buffer completion) and SPEC018 (crossfade completion coordination)
  - Added [XFD-CURV-050] Application Timing: Specifies Fader applies curves BEFORE buffering, mixer reads pre-faded samples
  - Added [XFD-IMPL-025] Architectural Note: Clarifies algorithm calculates TIMING (when), Fader handles APPLICATION (how)
  - Cross-references SPEC016 [DBD-MIX-041] (simple addition) and [DBD-MIX-042] (architectural separation)
  - Addresses specification gaps identified during SPEC018 terminology review
- v1.1 (2025-10-17): Added three-phase validation strategy specification
  - Added new "Validation Responsibility" section after timing validation algorithm
  - Defined Phase 1 (Enqueue-time), Phase 2 (Database read), and Phase 3 (Pre-decode) validation
  - Specified ownership, scope, validation rules, and failure actions for each phase
  - Added traceability ID XFD-VAL-010 for three-phase validation strategy
  - Supports architectural decision from wkmp-ap design review (ISSUE-2)
  - Addresses specification ambiguity identified in PLAN014 mixer refactoring (REQ-MIX-003, REQ-MIX-004)
