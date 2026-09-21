> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/SPEC005-program_director.md` on 2026-08-09. **Not a Lempi specification.**
>
> Selection algorithm design.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# WKMP Program Director

**ðŸŽ¯ TIER 2 - DESIGN SPECIFICATION**

Defines the Program Director component responsible for automatic passage selection. Derived from [requirements.md](MCR-REQ001-requirements.md). See Document Hierarchy.

> **Related Documentation:** [Requirements](MCR-REQ001-requirements.md) | Architecture | [Musical Flavor](MCR-SPEC003-musical_flavor.md) | [Musical Taste](MCR-SPEC004-musical_taste.md) | [Cooldown System](MCR-SPEC005-program_director.md#cooldown-system)

---

## Overview

The Program Director is the intelligent passage selection system that automatically chooses which passages to enqueue for playback. It balances multiple factors including musical flavor distance, cooldown periods, base probabilities, and time-of-day preferences to create a continuously evolving, personalized listening experience.

**Key Responsibilities:**
- Calculate passage selection probabilities based on multiple factors
- Implement weighted random selection algorithm
- Maintain time-of-day flavor targets
- Handle timeslot transitions
- Respond to temporary flavor overrides
- Filter out passages in cooldown or with zero probability

## Selection Request Processing

### Request Timing

**[PD-TIME-010]** When the Program Director receives a selection request from the Queue Manager, it must determine the **target playback time** for the passage being selected.

**[PD-TIME-020]** The target playback time is calculated as:
```
target_time = ending_time_of_last_passage_in_queue
```

Where `ending_time_of_last_passage_in_queue` is the anticipated completion time of the final passage currently in the queue.

**[PD-TIME-030]** All time-based calculations in the passage selection process use this `target_time`, including:
- Determining which timeslot applies
- Calculating the musical flavor target for that timeslot
- Evaluating time-of-day weighted taste (if applicable)
- Any other time-dependent selection factors

**[PD-TIME-040]** This design ensures that:
- Passages are selected for the time they will actually play, not the current time
- Timeslot transitions are handled smoothly without disrupting queued passages
- The queue can span timeslot boundaries naturally

### Example Scenario

**Current time:** 11:50 PM
**Queue contents:**
- Passage A: 5 minutes remaining
- Passage B: 8 minutes queued

**Selection request arrives:**
- Last passage ends at: 11:50 PM + 5 min + 8 min = 12:03 AM (next day)
- Target time for selection: 12:03 AM
- Timeslot used: Midnight timeslot (not the 11 PM timeslot currently active)
- Flavor target: Based on midnight timeslot definition

## Musical Flavor Target Determination

### Timeslot-Based Target

**[PD-FLAV-010]** For each timeslot in the 24-hour schedule, users define one or more reference passages that represent the desired musical character for that time period.

**[PD-FLAV-020]** The flavor target for a timeslot is calculated as the **weighted centroid** of the flavor vectors of all reference passages assigned to that timeslot.

**[PD-FLAV-030]** When multiple reference passages are defined for a timeslot:
- Each passage's flavor vector contributes equally (unweighted centroid)
- Missing characteristics handled as per [Musical Flavor - Weighted Taste](MCR-SPEC003-musical_flavor.md#more-than-one-recording-per-passage-calculation)
- Result is a single flavor vector representing the timeslot's target

**[PD-FLAV-040]** The Program Director queries the current timeslot based on `target_time` (not current time) to determine which flavor target to use.

### Temporary Flavor Override

**[PD-OVER-010]** Users may temporarily override the timeslot-based flavor target by manually selecting a different flavor to use for a specified duration (e.g., 1 or 2 hours).

**[PD-OVER-020]** When a temporary override is active:
- Program Director uses the override flavor target instead of timeslot-based target
- Override persists until expiration time is reached
- Expiration time is calculated from the moment the override is set

**[PD-OVER-030]** When temporary override is set:
1. Current passage's remaining time is skipped (crossfade to next passage immediately)
2. All passages in queue are removed
3. New passage selected based on override flavor target
4. Queue replenished using override target

**[PD-OVER-040]** When temporary override expires:
- Program Director reverts to timeslot-based flavor target for next selection
- Already queued passages remain (not removed)
- Natural queue replenishment uses timeslot-based target

> **See:** [Requirements - Musical Flavor Target by time of day](MCR-REQ001-requirements.md#musical-flavor-target-by-time-of-day) for user-facing behavior

## Selection Algorithm

### Overview

**[PD-ALGO-010]** The Program Director uses a multi-stage filtering and ranking algorithm to select passages:

1. **Filter** to passages with non-zero probability
2. **Calculate** flavor distance from target
3. **Rank** by flavor distance
4. **Select** top candidates
5. **Choose** via weighted random selection

### Stage 1: Probability Filtering

**[PD-PROB-010]** For each passage in the library, calculate the **final probability** as:

```
final_probability = base_probability Ã— cooldown_multiplier
```

Where:
- `base_probability` = song_prob Ã— artist_prob Ã— work_prob (see below)
- `cooldown_multiplier` = song_cooldown Ã— artist_cooldown Ã— work_cooldown (see Cooldown System)

**[PD-PROB-020]** **Base Probability Calculation:**

For passages with a single song:
```
base_probability = song.base_probability Ã— artist.base_probability Ã— work.base_probability
```

For passages with multiple songs:
```
base_probability = weighted_average(
  song_probabilities,
  weights = song_durations_in_passage
)
```

Where each `song_probability = song.base_probability Ã— artist_avg.base_probability Ã— work.base_probability`

**[PD-PROB-030]** **Artist Probability for Multi-Artist Songs:**

When a song has multiple artists with assigned weights:
```
artist_probability = sum(artist[i].base_probability Ã— artist[i].weight)
```

Where `sum(artist[i].weight) = 1.0` for all artists of the song.

**[PD-PROB-035]** **Work Probability for Multi-Work Songs:**

When a song has multiple works (mashups, medleys):
```
work_probability = product(work[i].base_probability)
```

Where all associated works' base probabilities are multiplied together.

- **Rationale**: Song represents all works, so all works' probabilities affect selection
- **Example**: Mashup with 3 works (prob 1.0, 0.8, 1.2) → work_probability = 1.0 Ã— 0.8 Ã— 1.2 = 0.96
- **Effect**: Deprioritizing any source work reduces mashup's probability proportionally

**[PD-PROB-040]** **Zero-Song Passages:**

Passages containing zero songs are excluded from automatic selection:
- They have no musical flavor vector
- They cannot be ranked by flavor distance
- Users may manually enqueue them at any time

**[PD-PROB-050]** Filter out all passages where `final_probability = 0`.

### Stage 2: Flavor Distance Calculation

**[PD-DIST-010]** For each passage with non-zero probability, calculate the **flavor distance** from the current target flavor vector.

**[PD-DIST-020]** Flavor distance is calculated as **squared Euclidean distance**:

```
distanceÂ² = Î£(target[i] - passage[i])Â²
```

Summed across all available characteristics (binary and complex elements).

**[PD-DIST-030]** Passages with no flavor data (no characteristics defined):
- Flavor distance = 1.0 (maximum distance)
- Effectively deprioritized but not excluded

> **See:** [Musical Flavor - Distance Calculation](MCR-SPEC003-musical_flavor.md#flavor-distance) for complete algorithm

### Stage 3: Candidate Ranking

**[PD-RANK-010]** Sort all non-zero probability passages by flavor distance (ascending).

**[PD-RANK-020]** Select the **top 100 candidates** with the smallest flavor distances.

**[PD-RANK-030]** If fewer than 100 passages have non-zero probability, use all available passages.

### Stage 4: Weighted Random Selection

**[PD-SEL-010]** From the top 100 candidates, perform **weighted random selection** where each passage's selection weight is its `final_probability`.

**[PD-SEL-020]** Algorithm:
1. Calculate total weight: `W_total = sum(candidate[i].final_probability)`
2. Generate random value: `r ∈ [0, W_total)`
3. Iterate through candidates, accumulating weights until `accumulated_weight ≥ r`
4. Select the passage where threshold is crossed

**[PD-SEL-030]** This ensures:
- Passages closer to target flavor are more likely to be selected (top 100 filtering)
- Higher base probability passages are more likely within the candidates
- Recently played passages are less likely (cooldown multipliers reduce probability)
- Randomness prevents repetitive, predictable selections

### Edge Cases

**[PD-EDGE-010]** **No Candidates Available:**

When no passages have non-zero probability:
- Return error to Queue Manager
- Queue Manager stops automatic enqueueing
- System continues in current playback state (does not change Play/Pause)
- User may manually enqueue passages at any time

**[PD-EDGE-020]** **All Candidates Have Zero Flavor Distance:**

Rare edge case where multiple passages are identical to target:
- All have equal selection probability (based only on `final_probability`)
- Weighted random selection still applies

**[PD-EDGE-030]** **Library Contains Only Zero-Song Passages:**

- No passages can be automatically selected
- Program Director returns "no candidates" error with reason: `NO_SONGS_WITH_FLAVOR`
- Automatic enqueueing stops
- Manual enqueueing still works

**TODO: Zero-Song Passage Implementation Details**
The following items will be addressed when Program Director is fully specified:
- Define whether users can set base probability for zero-song passages (UI consideration)
- Specify whether UI should disable/hide probability controls for zero-song passages
- Clarify exact Program Director error handling when library contains ONLY zero-song passages

## Selection Failure Communication

**[PD-COMM-010]** When Program Director cannot select a passage, it returns an error with a specific reason code to enable appropriate UI feedback:

**Error Reasons:**

1. **`NO_SONGS_WITH_FLAVOR`**
   - Library contains no songs with musical flavor definitions
   - Triggered when: All passages have zero songs OR all songs lack flavor data
   - UI displays: "The library does not contain any songs with musical flavor definitions."

2. **`ALL_IN_COOLDOWN`**
   - All songs with flavor data are currently in cooldown
   - Triggered when: At least one song exists with flavor, but all have cooldown_multiplier = 0.0
   - UI displays: "All songs in the library with musical flavor definitions have been played recently. Waiting until their cooldown periods elapse."

3. **`PROCESSING`**
   - Selection algorithm is running (normal for first request after startup)
   - Triggered when: Initial taste calculation or large library analysis in progress
   - UI displays: "The Program Director is working hard to select your next song, please be patient."

4. **`NOT_RESPONDING`**
   - Program Director module is not responding to requests
   - Triggered when: HTTP request timeout or connection refused
   - UI displays: "The Program Director is not responding to my requests for something to play."

**[PD-COMM-020]** API Response Format:

```json
{
  "success": false,
  "error": {
    "code": "ALL_IN_COOLDOWN",
    "message": "No passages available (all in cooldown)",
    "next_available_at": "2025-10-09T14:30:00Z"  // optional, ISO 8601 timestamp
  }
}
```

> **See:** UI Specification - Automatic Selection Unavailable for user-facing message display
> **See:** [Musical Flavor - Edge Cases](MCR-SPEC003-musical_flavor.md#edge-cases) for additional scenarios

## Cooldown System
<a name="cooldown-system"></a>

> **Entity Definitions:** Song, Artist, and Work entities are defined in [REQ002-entity_definitions.md](MCR-REQ002-entity_definitions.md). See [REQ002:90-189 § 2.0 Entity Relationship Overview](MCR-REQ002-entity_definitions.md#20-entity-relationship-overview) for cooldown tracking relationships.

**[PD-COOL-010]** The cooldown system prevents too-frequent replay of songs, artists, and works.

**[PD-COOL-015]** Cooldowns are **global (system-wide)**: All users see the same cooldown state. The system assumes all listeners hear all songs as they are played, so cooldowns apply to passage selection for everyone collectively.

**[PD-COOL-020]** Each entity (song, artist, work) has:
- **Minimum cooldown period**: Probability is zero (cannot be selected)
- **Ramping cooldown period**: Probability increases linearly from zero to base probability

**[PD-COOL-030]** Default cooldown values:
- **Song**: 7 days minimum, 14 days ramping
- **Artist**: 2 hours minimum, 4 hours ramping
- **Work**: 3 days minimum, 7 days ramping

**[PD-COOL-040]** Cooldown multiplier calculation:

```
elapsed_time = current_time - last_played_time

if elapsed_time < minimum_cooldown:
    multiplier = 0.0
elif elapsed_time < (minimum_cooldown + ramping_cooldown):
    multiplier = (elapsed_time - minimum_cooldown) / ramping_cooldown
else:
    multiplier = 1.0
```

**[PD-COOL-050]** For passages with multiple songs:
- Each song has its own cooldown multiplier
- Passage cooldown = weighted average of song cooldowns (weighted by duration)

**[PD-COOL-060]** For songs with multiple artists:
- Each artist has its own cooldown multiplier
- Song's artist cooldown = weighted average of artist cooldowns (weighted by artist weight)

**[PD-COOL-065]** For songs with multiple works:
- Each work has its own cooldown multiplier
- Song's work cooldown = product of all work cooldowns (all works must be out of cooldown)
- Example: Song with 2 works, work A cooldown = 0.5, work B cooldown = 0.8
  - Song's work cooldown = 0.5 Ã— 0.8 = 0.4
- **Rationale**: Mashups and medleys should respect cooldown of all source works

**[PD-COOL-070]** Final cooldown multiplier for a passage:
```
passage_cooldown = song_cooldown Ã— artist_cooldown Ã— work_cooldown
```

Where each factor is the weighted average across multiple songs/artists if applicable, and product across multiple works.

> **See:** [Requirements - Cooldown System](MCR-REQ001-requirements.md#automatic-passage-selection) for user-facing specification

## User-Configurable Parameters

### Base Probabilities

**[PD-BASE-010]** Users may adjust base probabilities for individual songs, artists, and works.

**[PD-BASE-020]** Valid range: 0.0 to 1000.0
- Values < 1.0: Deprioritize (less likely to be selected)
- Value = 1.0: Default (neutral)
- Values > 1.0: Prioritize (more likely to be selected)

**[PD-BASE-030]** Default value for all entities: 1.0

**[PD-BASE-040]** UI presents base probability as:
- Logarithmic scale slider for intuitive adjustment
- Option for direct numeric input for precise control

### Cooldown Periods

**[PD-COOL-080]** Users may customize cooldown periods for songs, artists, and works.

**[PD-COOL-090]** Each entity type has independently configurable:
- Minimum cooldown duration
- Ramping cooldown duration

**[PD-COOL-100]** Cooldown configuration stored per-entity-type (not per individual entity).

### Timeslot Configuration

**[PD-TIME-050]** Users may define timeslots for the 24-hour schedule:
- Add new timeslots
- Remove existing timeslots (minimum: one timeslot must always exist)
- Adjust timeslot boundaries
- Assign reference passages to each timeslot

**[PD-TIME-060]** Constraints:
- Every moment of the day must be covered by exactly one timeslot
- Timeslots may not overlap
- Each timeslot must have at least one reference passage

## Integration with Musical Taste (Future)

**[PD-TASTE-010]** **(Phase 2 - Not Yet Defined):** Integration of Like/Dislike-based musical taste into the selection algorithm.

**[PD-TASTE-020]** Current design records likes and dislikes (Full and Lite versions) but does not yet use them for selection.

**[PD-TASTE-030]** Future integration will likely involve:
- "Like-Taste" vector: Centroid of all liked songs' flavors
- "Dislike-Taste" vector: Centroid of all disliked songs' flavors
- Modified distance calculation incorporating both flavor target and taste vectors
- Exclusion filtering based on dislike similarity

> **See:** [Likes and Dislikes](MCR-SPEC006-like_dislike.md) for data collection specification
> **See:** [Musical Taste](MCR-SPEC004-musical_taste.md) for taste calculation algorithms

**Note:** The final algorithm for how Like-Taste and Dislike-Taste influence passage selection will be specified in a future update to this document.

## Data Sources

**[PD-DATA-010]** The Program Director requires access to:

1. **Passage metadata** (from database):
   - Musical flavor vectors
   - Song associations
   - Duration information

2. **Entity metadata**:
   - Song base probabilities
   - Artist base probabilities and weights
   - Work base probabilities
   - Last-played timestamps for songs, artists, works

3. **Timeslot configuration**:
   - Timeslot definitions (start time, end time)
   - Reference passages per timeslot
   - Currently active temporary override (if any)

4. **User preferences**:
   - Cooldown period settings
   - Base probability adjustments

5. **Current system state**:
   - Queue contents and passage end times
   - Current playback position

## Performance Considerations

**[PD-PERF-010]** Selection request processing should complete within:
- Desktop/Full version: < 100ms
- Raspberry Pi Zero2W/Lite version: < 500ms

**[PD-PERF-020]** Optimization strategies:
- Cache flavor distance calculations when target hasn't changed
- Pre-filter zero-probability passages before distance calculation
- Limit candidate set to top 100 (not all passages)
- Index database queries on last_played_time

**[PD-PERF-030]** Library size targets:
- Efficient with 1,000 passages: < 10ms selection
- Acceptable with 10,000 passages: < 100ms selection
- Usable with 50,000+ passages: < 500ms selection

## Event Emissions

**[PD-EVENT-010]** The Program Director emits events via the EventBus:

- When timeslot changes (scheduled transition)
- When temporary override is set
- When temporary override expires
- When selection fails due to no candidates

> **See:** Event System for complete event specifications

## Testing Requirements

**[PD-TEST-010]** Unit tests must cover:
- Probability calculation with various song/artist/work combinations
- Cooldown multiplier calculation at various elapsed times
- Flavor distance calculation (delegated to Musical Flavor module)
- Weighted random selection distribution
- Edge cases (no candidates, zero songs, etc.)

**[PD-TEST-020]** Integration tests must cover:
- Timeslot transitions during selection
- Temporary override activation and expiration
- Multi-song, multi-artist passage probability calculations
- End-to-end selection request processing

**[PD-TEST-030]** Performance tests must verify:
- Selection latency targets met
- Memory usage reasonable for large libraries
- No memory leaks over extended operation

----
End of document - WKMP Program Director
