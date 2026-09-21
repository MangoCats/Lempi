> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/SPEC033-album_matching.md` on 2026-08-09. **Not a Lempi specification.**
>
> DAO segmentation cascade and 7-strategy MusicBrainz edition search. The Vipunen P4 algorithm.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# SPEC033: Album Matching Algorithm

**🎵 TIER 2 - DESIGN SPECIFICATION**

Defines the album matching algorithm for identifying MusicBrainz editions from single-file audio recordings. Derived from [Requirements](MCR-REQ001-requirements.md). See Document Hierarchy.

> **Related Documentation:** [Requirements - Album Matching](MCR-REQ001-requirements.md#album-matching) | Audio Ingest Architecture | Library Management | [Audio File Segmentation](MCR-IMPL005-audio_file_segmentation.md) | Migration Guide (am28)

---

## Overview

**[AM-OV-010]** The album matching algorithm automatically identifies MusicBrainz release editions from single-file audio recordings (CD rips, vinyl recordings, cassette transfers) by detecting track boundaries via silence analysis and matching against MusicBrainz metadata.

**Purpose:** Enable automated passage identification for albums stored as single continuous audio files, reducing manual effort during library import.

**Scope:** Track-level boundary detection and edition matching only. Individual passage metadata (lead-in/lead-out, amplitude analysis) handled by separate subsystems.

**Performance:** Designed for robustness (90%+ track count accuracy) and speed (180x optimization via caching, 87.6% early-exit rate).

---

## Design Decisions

### Multi-Stage Fallback Architecture

**[AM-ARCH-010]** Algorithm uses cascading fallback stages to handle diverse audio characteristics:

| Stage | Method | Use Case | Confidence Penalty |
|-------|--------|----------|-------------------|
| **Stage 2** | Parameter Grid Search | Standard albums with clear silence | None (100% confidence) |
| **Stage 3** | DP Assembly | Over-segmented tracks (N detected > K expected) | None (100% confidence) |
| **Stage 4** | RMS Quiet Spot | Poor silence (vinyl, cassette, live recordings) | 30% penalty |
| **Stage 5** | Extra Merging | Bonus tracks, hidden tracks | None (but limited to 3 merges) |

**Rationale:** Different audio sources have different characteristics:
- **CD rips:** Clear 2-second silence gaps → Stage 2 sufficient
- **Vinyl:** Background noise, pops/clicks → Stage 4 RMS detection needed
- **Cassette:** Dolby noise reduction artifacts → Stage 4 RMS detection needed
- **Over-segmented:** Intra-track boundaries detected → Stage 3 DP assembly required

### Comprehensive Edition Discovery

**[AM-ARCH-020]** MusicBrainz search uses 7-strategy approach to maximize edition discovery:

1. **Basic unquoted** - Avoid over-restriction (handles simple queries)
2. **CamelCase splitting** - "HappyNation" → "Happy Nation"
3. **Fuzzy ~1 edit** - Minor typos, punctuation variations
4. **Wildcard fixes** - Known common misspellings
5. **Aggressive fuzzy ~2 edits** - Significant spelling errors
6. **Per-token fuzzy** - Multi-word names with individual token errors
7. **Album-only fallback** - Problematic artist names (apostrophes, special characters)

**Target:** 10-25 editions found per album (vs single edition in baseline implementations).

**Early-Exit:** Stop at first strategy finding ≥10 candidates (reduces API calls).

---

## Multi-Stage Algorithm

### Stage 2: Parameter Grid Search

**[AM-STG2-010]** Tests 180 parameter combinations to find optimal silence detection settings.

**Parameters:**
- **Silence thresholds:** 15 values from -80dB to -30dB (steps of 3-4dB)
- **Minimum durations:** 12 values from 0.1s to 3.0s
- **Total combinations:** 15 × 12 = 180

**[AM-STG2-020]** Empirical parameter ordering based on run27 frequency analysis:
- Most successful parameters tested first
- 87.6% of albums exit before testing all 180 combinations
- Median exit rank: 4.0 (only 4 parameters tested vs 180)

**[AM-STG2-030]** WindowDbProfile optimization:
```
Traditional approach: 180 separate audio scans = O(180N)
Optimized approach:   1 dB profile + 180 filters = O(N + 180W)
                      where W = window count << N
Expected speedup:     ~180x
```

**[AM-STG2-040]** Success criteria:
- Track count matches expected: 100% match
- Track count within ±1: Proceed to Stage 3
- Track count ≥65% correct: Accept match (lowered from 80% in Phase 1)

### Stage 3: Dynamic Programming Assembly

**[AM-STG3-010]** Handles over-segmentation: N detected boundaries > K expected tracks.

**Algorithm:** O(N²K) dynamic programming
- **Input:** N detected positions, K expected tracks
- **Output:** Optimal K-1 boundaries minimizing total duration error
- **Method:** Test all (N choose K-1) combinations, score by cumulative error

**[AM-STG3-020]** Scoring function:
```rust
score = Σ |detected_duration[i] - expected_duration[i]|
```

**Use Cases:**
- Intra-track silence (quiet passages within songs)
- Fade-outs followed by quiet intros
- Classical movements with brief pauses

### Stage 4: RMS Quiet Spot Detection

**[AM-STG4-010]** Fallback when silence thresholds fail (vinyl, cassette, live recordings).

**Method:**
- Search ±5 seconds around expected boundary position
- Find RMS minimum (quietest spot) within search window
- Place boundary at detected minimum

**[AM-STG4-020]** Confidence penalty: 30%
- Less reliable than silence-based detection
- Indicates potential quality issues with source material
- Used for ranking when multiple matches found

**Adaptive RMS windows:**
- Min duration ≤0.3s → 25ms window (fine-grained)
- Min duration ≤0.6s → 50ms window (standard)
- Min duration >0.6s → 100ms window (broad detection)

### Stage 5: Extra Track Merging

**[AM-STG5-010]** Handles detected > expected (bonus tracks, hidden tracks).

**Method:**
- Merge adjacent pairs when N detected > K expected
- Keep first (K-1) tracks intact
- Merge remaining (N - K + 1) tracks into final track
- Maximum 3 merges (prevent bad consolidations)

**Use Cases:**
- Bonus tracks (Japanese editions, deluxe versions)
- Hidden tracks (silence gap + secret song)
- Misdetected boundaries at album end

---

## MusicBrainz Integration

### Edition Search

**[AM-MB-010]** 7-strategy comprehensive search (see [AM-ARCH-020]).

**Implementation:** `services/musicbrainz_client.rs:881-955`

**Query Format:**
```
Strategy 1: type:album AND artist:ArtistName AND release:AlbumName
Strategy 2: type:album AND artist:Artist Name AND release:Album Name  (CamelCase split)
Strategy 3: type:album AND artist:ArtistName~ AND release:AlbumName~  (fuzzy ~1)
... (5 more strategies)
```

**Rate Limiting:**
- MusicBrainz API: 1 request/second
- Caching: Persistent SQLite cache (`.cache/album_matcher.db`)
- Early-exit: Stop at first strategy with ≥10 results

### Edition Grouping and Filtering

**[AM-MB-020]** Deduplication via track pattern grouping.

**Track Pattern:**
- Track count + duration signature (rounded to 5s intervals)
- Groups editions with identical track structure
- Reduces 25 editions → ~5-10 unique patterns

**[AM-MB-030]** Filtering to top 20 editions by name similarity.

**Jaro-Winkler Algorithm:**
- Artist similarity weight: 60%
- Album similarity weight: 40%
- Formula: `score = 0.6 × artist_sim + 0.4 × album_sim`

**Rationale:** Artist name more stable than album name (reissues, regional variations).

### Edition Selection

**[AM-MB-040]** Final selection from top 20 editions.

**Criteria (in order):**
1. **Exact track count match** (100% match preferred)
2. **Highest match percentage** (≥65% tracks within tolerance)
3. **Highest name similarity** (Jaro-Winkler score)
4. **Lowest confidence penalty** (Stage 2/3 preferred over Stage 4)

---

## Performance Optimizations

### WindowDbProfile Caching

**[AM-PERF-010]** Single-pass dB profiling for parameter sweep.

**Traditional Approach:**
```rust
for (threshold, min_duration) in parameters {
    scan_audio_file(threshold, min_duration);  // 180 full scans
}
```

**Optimized Approach:**
```rust
let profile = WindowDbProfile::from_samples(&samples, sample_rate);
for (threshold, min_duration) in parameters {
    profile.filter(threshold, min_duration);  // 180 filters, 1 scan
}
```

**Performance:**
- **Before:** O(180N) where N = sample count
- **After:** O(N + 180W) where W = window count
- **Speedup:** ~180x typical (W << N)

**Implementation:** `matching/silence_detection.rs:38-129`

### Empirical Parameter Ordering

**[AM-PERF-020]** Parameters ordered by success frequency (run27 analysis).

**Data:** 193 successfully matched albums from November 2024 baseline
- Rank 1 parameter: 32.1% success rate
- Rank 2-4 parameters: Additional 55.5% success
- Cumulative 87.6% success by rank 4

**Early-Exit Strategy:**
- Break on first 100% match found
- 87.6% of albums avoid testing all 180 parameters
- Median parameters tested: 4 vs 180 (97.8% reduction)

**Time Savings:** ~0.35 seconds per album × 87.6% success rate

**Implementation:** `matching/constants.rs:22-35`

### MusicBrainz Response Caching

**[AM-PERF-030]** Persistent SQLite cache for API responses.

**Cache Location:** `.cache/album_matcher.db` (or configured root folder)

**Cached Data:**
- Release search results (artist + album queries)
- Release details (track lists, durations, metadata)
- TTL: Indefinite (MusicBrainz data stable)

**Benefits:**
- Eliminates network latency for repeated queries
- Reduces API load (respects 1 req/sec limit)
- Enables offline validation testing

**Implementation:** `db/release_cache.rs`

### Skip Boundary Refinement for Perfect Matches

**[AM-PERF-040]** Bypass expensive boundary refinement when match is already perfect.

**Rationale:** Boundary refinement is a post-processing step that searches for better boundaries when split failures are detected (one track absorbed another). For 100% matches, all tracks are already within tolerance, so refinement provides zero benefit.

**Performance Impact:**
- **Target cases:** Albums with obvious matches (30-50% of corpus)
- **Time saved:** 10-60 minutes per album (depends on audio length, track count)
- **Mechanism:** Simple percentage check before refinement call

**Implementation:**
```rust
// Stage 2 only applies refinement when match is imperfect
if !detected_durations.is_empty() && best_percentage < 100.0 {
    detected_durations = apply_refinement_to_durations(/* ... */);
}
```

**Location:** `stages/stage2.rs:199-218` [PERF-OPT-001]

### Strict Early Exit for First Edition Perfect Match

**[AM-PERF-050]** Immediately exit Stage 2 when first-ranked edition achieves 100% match.

**Rationale:** Editions are pre-sorted by name similarity (Jaro-Winkler). If the top-ranked edition achieves 100% match, it's almost certainly correct. Testing additional editions wastes 10-20 minutes per edition for long albums.

**Grace Period Preserved:** Existing grace period (20s default) still applies for perfect matches found in later editions, allowing discovery of better alternatives.

**Performance Impact:**
- **Target cases:** Albums with obvious matches where first edition is correct (30-40% of corpus)
- **Time saved:** 10-20 minutes per album (skips 2+ additional editions at ~10 min each)
- **Risk:** Low - editions sorted by name similarity, first is most likely

**Implementation:**
```rust
if edition_idx == 0 && early_exit.enabled && best_percentage >= 100.0 {
    results.push(result);
    break;  // Skip remaining editions
}
```

**Location:** `stages/stage2.rs:110-147` [PERF-OPT-002]

**Expected Combined Impact:** 2.5-6x speedup on comprehensive test suites (67h → 10-25h projected).

---

## Configuration

### AlbumMatcherConfig

**[AM-CFG-010]** Runtime configuration structure.

```rust
pub struct AlbumMatcherConfig {
    /// Track duration tolerance (seconds)
    pub match_tolerance_secs: f64,  // Default: 10.0

    /// Minimum artist name similarity (0.0-1.0)
    pub min_artist_similarity: f64,  // Default: 0.50

    /// Enable early-exit on first 100% match
    pub enable_early_exit: bool,  // Default: true

    /// Early-exit grace period (seconds)
    pub early_exit_grace_secs: u64,  // Default: 20

    /// Enable Stage 3 (DP assembly)
    pub enable_stage3: bool,  // Default: true

    /// Enable Stage 4 (RMS quiet spot)
    pub enable_stage4: bool,  // Default: true

    /// Enable Stage 5 (extra merging)
    pub enable_stage5: bool,  // Default: true

    /// Stage 2 minimum match percentage to proceed
    pub stage2_min_match_pct: f64,  // Default: 0.65

    /// Maximum editions to test per album
    pub max_editions: usize,  // Default: 20
}
```

**[AM-CFG-020]** Parameter tuning rationale:
- **match_tolerance_secs = 10.0:** Handles encoding variations, fade timing differences
- **min_artist_similarity = 0.50:** Allows "The Beatles" vs "Beatles" matching
- **stage2_min_match_pct = 0.65:** Lowered from 0.80 to reduce false negatives
- **max_editions = 20:** Balances thoroughness vs performance (20 tested vs 10-25 found)

---

## Traceability Matrix

| Requirement | Specification Section | Implementation |
|-------------|----------------------|----------------|
| [REQ-PI-AM-010] Track count accuracy ≥90% | [AM-STG2-040], [AM-MB-040] | `orchestrator.rs:115-227` |
| [REQ-PI-AM-020] Multi-stage fallback | [AM-ARCH-010], [AM-STG2-010] through [AM-STG5-010] | `stages/*.rs` |
| [REQ-PI-AM-030] 10-25 editions discovered | [AM-ARCH-020], [AM-MB-010] | `musicbrainz_client.rs:881-955` |
| [REQ-PI-AM-040] Edition scoring/ranking | [AM-MB-030], [AM-MB-040] | `editions/scoring.rs` |
| [REQ-PI-AM-050] ≤10s mean boundary error | [AM-STG2-010], [AM-CFG-020] | `constants.rs` |
| [REQ-PI-AM-060] Performance optimization | [AM-PERF-010] through [AM-PERF-050] | `silence_detection.rs`, `constants.rs`, `stages/stage2.rs` |

---

## Testing and Validation

### Benchmark Tests

**[AM-TEST-010]** Reference test cases (feature-gated: `benchmark_tests`):
- **ZZ Top's First Album:** 10 tracks, 34:12 duration
- **Expected:** 10 passages = 10 tracks exactly (100% match)
- **Tolerance:** Mean error ≤5.0s, all tracks within ±10s

**Location:** `wkmp-ai/tests/fixtures/ZZTopsFirstAlbum.mp3`

### Validation Tests

**[AM-TEST-020]** PLAN027 comprehensive validation (5 albums, 100% passing):
1. ZZ Top's First Album (10 tracks)
2. Ace of Base - Happy Nation (16 tracks)
3. Aerosmith - Pump (10 tracks)
4. Imagine Dragons - Night Visions (18 tracks)
5. Cars - Panorama (10 tracks, **UTF-8 test case**)

**Location:** `wkmp-ai/tests/plan027_validation_test.rs`

### Baseline Comparison

**[AM-TEST-030]** Run29f 200-album comparison test:
- **Baseline:** November 2024 results (200 albums)
- **Purpose:** Regression testing, track improvements/regressions
- **Status:** Ongoing validation (UTF-8 vulnerability fixed)

**Location:** `wkmp-ai/tests/run29f_full_comparison_test.rs`

---

## Known Limitations

**[AM-LIMIT-010]** Algorithm limitations:
- **Gapless albums:** No silence between tracks → Stage 4 RMS required (30% confidence penalty)
- **Live recordings:** Continuous applause → May fail to detect boundaries
- **Classical:** Movement boundaries subtle → May require manual editing
- **Medleys:** Intentionally no gaps → Single passage preferred

**[AM-LIMIT-020]** MusicBrainz data quality:
- Incomplete metadata → May not find correct edition
- Regional variations → Multiple valid matches possible
- Remastered editions → Duration differences >10s may cause mismatch

**Mitigation:** Manual editing via wkmp-ai segment editor (Step 4 in import workflow).

---

## Future Enhancements

**[AM-FUTURE-010]** Potential improvements (not in current scope):
1. **Machine learning:** Train on validated matches to improve threshold selection
2. **Waveform similarity:** Fingerprint-based boundary validation
3. **User feedback:** Learn from manual boundary adjustments
4. **Genre-specific tuning:** Different parameters for Classical, Jazz, Rock, etc.
5. **Spectral analysis:** Frequency-based silence detection (complements RMS)

---

## References

- **Implementation:** `wkmp-ai/src/matching/` module
- **README:** `wkmp-ai/src/matching/README.md` (182 lines, algorithm summary)
- **Migration Guide:** `docs/MIGRATION_am28.md` (100 lines, API changes from am28 prototype)
- **Implementation Plans:** PLAN027 (simplicity-first), PLAN028 (performance), PLAN029 (bottlenecks), PLAN030 (am29f integration), PLAN031 (closed-loop improvement)
- **Baseline Results:** `album_matcher_results_run29f.json` (200 albums, November 2024)

---

**Status:** ✅ Implemented and Validated
**Last Updated:** 2026-01-11
**Maintainer:** See CLAUDE.md for development workflows
