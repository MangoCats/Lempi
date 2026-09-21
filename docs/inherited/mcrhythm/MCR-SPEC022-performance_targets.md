> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/SPEC022-performance_targets.md` on 2026-08-09. **Not a Lempi specification.**
>
> RAM and latency targets for Pi Zero 2W (<=150 MB RSS).
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# SPEC022: Performance Targets

**Document Type:** Tier 2 Specification (Design)
**Status:** Active
**Created:** 2025-10-25
**Last Updated:** 2025-10-25
**Addresses:** Finding 12 from Requirements-Specifications Review Analysis
**Depends On:** [REQ001](MCR-REQ001-requirements.md), [SPEC016](MCR-SPEC016-decoder_buffer_design.md), SPEC014
**Informs:** IMPL001, GUIDE001

---

## Purpose

Define quantified performance targets for WKMP Audio Player (wkmp-ap) to ensure acceptable performance on target deployment platform (Raspberry Pi Zero 2W) and provide measurable success criteria for implementation validation.

---

## Target Platform

**Primary Deployment Target:** Raspberry Pi Zero 2W

**Hardware Specifications:**
- **CPU:** 1 GHz quad-core Cortex-A53 (ARMv8, 64-bit)
- **RAM:** 512 MB
- **Storage:** Typically SD card (10-20 MB/s read speed)
- **Audio:** USB audio interface or onboard 3.5mm jack

**Software Environment:**
- **OS:** Raspberry Pi OS Lite (64-bit)
- **Rust Toolchain:** Stable channel (latest)
- **Runtime:** Tokio async runtime

---

## Performance Target Categories

### 1. Decode Latency

**Definition:** Time to fill passage buffer from decode start to buffer ready state.

**Targets:**

| Metric | Target | Maximum Tolerable | Notes |
|--------|--------|-------------------|-------|
| **Initial Playback Start** | ≤ 0.1 seconds | 5.0 seconds | Time from user action to first audio output |
| **15-Second Buffer Fill** | ≤ 2.0 seconds | 5.0 seconds | Time to decode/resample 15 seconds of audio (per SPEC016) |
| **Crossfade Buffer Preparation** | ≤ 1.0 seconds | 3.0 seconds | Time to prepare next passage during crossfade |

**Context:**
- SPEC016 specifies 15-second incremental buffering
- Target allows 7.5x real-time decode throughput minimum
- Maximum tolerable ensures acceptable user experience
- Initial playback start target optimizes perceived responsiveness

**Measurement Methodology:**
- Measure monotonic elapsed time from decode task spawn to buffer ready state (see SPEC023)
- Test with various audio formats (FLAC, MP3, Opus, AAC)
- Test at various sample rates (44.1 kHz, 48 kHz, 96 kHz)
- Measure on actual Pi Zero 2W hardware

**[PERF-MEASURE-010]** Initial Playback Start Measurement Point:
- **Start:** Timestamp when HTTP POST /playback/enqueue request arrives at wkmp-ap server
- **End:** Timestamp when first PCM sample is sent to audio output device (cpal stream)
- **Includes:** HTTP parsing, database write, decode task spawn, initial decode, buffer fill to minimum threshold, mixer state change, audio callback first invocation
- **Excludes:** Network latency (client → server), user action time (button click → HTTP request)
- **Instrumentation:** Add high-resolution timestamps (`std::time::Instant`) at both measurement points

**Acceptance Criteria:**
- 90% of passages meet target latency
- 100% of passages meet maximum tolerable latency
- No unnecessary delays introduced (avoid sleep/wait without cause)

---

### 2. CPU Usage

**Definition:** Processor utilization during normal playback operation.

**Targets:**

| Metric | Target | Maximum Tolerable | Notes |
|--------|--------|-------------------|-------|
| **Average Aggregate** | ≤ 30% | 50% | Average across all cores during steady playback |
| **Peak Aggregate** | ≤ 60% | 80% | Maximum during decode/crossfade operations |
| **Decode Thread** | ≤ 50% | 75% | Single-core usage during active decode |
| **Playback Thread** | ≤ 10% | 20% | Audio callback thread (lock-free, minimal work) |

**Context:**
- Pi Zero 2W has 4 cores @ 1 GHz each
- Target allows headroom for other processes (UI, PD, system)
- Peak usage acceptable during transient operations (crossfade start)
- Playback thread must be highly efficient (no glitches)

**Measurement Methodology:**
- Use `top` or `/proc/stat` to measure CPU percentage
- Sample at 100ms intervals over 5-minute playback session
- Calculate average, p50, p95, p99, max
- Test with continuous playback (10+ crossfades)

**[PERF-CPU-010]** CPU Percentage Calculation Method:
- **Aggregate CPU:** Sum of CPU usage across all 4 cores (NOT average)
- **Example:** 25% core1 + 20% core2 + 10% core3 + 5% core4 = 60% aggregate
- **Range:** 0% (all cores idle) to 400% (all cores 100% busy)
- **Target interpretation:** "≤30% average aggregate" = sum of all cores ≤30% on average
- **Peak interpretation:** "≤60% peak aggregate" = sum of all cores ≤60% during transient spikes
- **Rationale:** Aggregate sum represents total CPU capacity consumed, matches standard Linux `top` reporting for multi-threaded processes
- **Idle baseline:** Measure system idle baseline (OS + other services) and subtract from wkmp-ap measurements to isolate application CPU usage

**Acceptance Criteria:**
- Average aggregate CPU ≤ target during 90% of playback session
- Peak aggregate CPU ≤ maximum tolerable during all operations
- No audio glitches/underruns due to CPU constraints

---

### 3. Memory Usage

**Definition:** Total application memory footprint (RSS).

**Targets:**

| Metric | Target | Maximum Tolerable | Notes |
|--------|--------|-------------------|-------|
| **Total Application** | ≤ 150 MB | 200 MB | wkmp-ap process RSS |
| **Per-Passage Buffer** | ≤ 10 MB | 15 MB | 15 seconds @ 44.1kHz stereo 32-bit float |
| **Active Buffer Count** | 2-3 passages | 4 passages | Current + next + optional prefetch |

**Context:**
- Pi Zero 2W has 512 MB total RAM
- Target allows ~300 MB for OS + other services
- Buffer calculation: 44100 Hz × 2 channels × 4 bytes × 15 sec = 5.29 MB (theoretical)
- Actual usage includes metadata, resampler state, fade buffers

**Measurement Methodology:**
- Monitor RSS via `/proc/[pid]/status` (VmRSS field)
- Sample every 1 second over 10-minute session
- Calculate average, max
- Test with varying queue lengths (1, 5, 20 passages)

**[PERF-MEM-010]** Memory Baseline Measurement Requirement:
- **CRITICAL:** The 150 MB target is an **estimate** based on theoretical calculations (see Appendix A)
- **Baseline required:** Measure actual memory usage on Pi Zero 2W hardware during Phase 8 (Performance Optimization & Validation) of implementation plan
- **Test conditions:**
  - Clean system (no other services running)
  - Queue with 10 passages
  - Continuous playback for 30 minutes
  - Measure baseline (empty queue) + average (active playback) + peak (crossfade)
- **Target adjustment:** If empirical measurements significantly differ from 150 MB estimate (>20% variance), update this specification with actual baseline + 20% headroom
- **Acceptance threshold:** Empirically measured average ≤ baseline + 20% headroom, peak ≤ baseline + 40% headroom
- **Rationale:** Memory usage depends on implementation details (buffer allocation strategy, library overhead) that cannot be precisely predicted upfront

**Acceptance Criteria:**
- Average memory usage ≤ empirically established target
- Maximum memory usage ≤ maximum tolerable
- No memory leaks (stable usage over extended playback)
- Buffers released promptly when passages complete

---

### 4. Throughput

**Definition:** Rate at which passages can be decoded and buffered.

**Targets:**

| Metric | Target | Minimum Acceptable | Notes |
|--------|--------|-------------------|-------|
| **Decode Throughput** | ≥ 7.5x real-time | 3x real-time | 15-second buffer in ≤2 seconds = 7.5x |
| **Passages Buffered per Minute** | ≥ 20 passages | 10 passages | Assumes 3-second average decode time |
| **Concurrent Decode Operations** | 1 passage | 2 passages | Serial decode per SPEC016 [DBD-DEC-040] |

**Context:**
- SPEC016 specifies serial decode (no parallel decode)
- 15-second buffer fill in ≤2 seconds requires ≥7.5x real-time throughput
- Sufficient to maintain continuous playback with crossfading
- Assumes decode time < crossfade duration (typically 5-10 seconds)

**Measurement Methodology:**
- Measure decode time for 100 diverse passages
- Calculate real-time throughput ratio (buffer_duration / decode_time)
- Test various formats and sample rates
- Verify no queue starvation during continuous playback

**Acceptance Criteria:**
- 90% of passages achieve target throughput
- 100% of passages achieve minimum acceptable throughput
- No playback interruptions due to slow decode

---

### 5. API Response Time

**Definition:** HTTP endpoint response latency (wkmp-ap server).

**Targets:**

| Endpoint Category | Target (p50) | Target (p95) | Maximum Tolerable | Notes |
|-------------------|--------------|--------------|-------------------|-------|
| **State Queries** | ≤ 10 ms | ≤ 50 ms | 500 ms | GET /queue, GET /status |
| **Queue Modifications** | ≤ 50 ms | ≤ 150 ms | 500 ms | POST /queue/add, DELETE /queue/:id |
| **Playback Controls** | ≤ 20 ms | ≤ 100 ms | 500 ms | POST /play, POST /pause, POST /skip |
| **SSE Event Emission** | ≤ 5 ms | ≤ 20 ms | 100 ms | Time from event trigger to SSE send |

**Context:**
- Faster is always better for perceived responsiveness
- Target provides excellent interactive experience
- Maximum tolerable prevents perceived lag
- SSE event emission critical for multi-user coordination

**Measurement Methodology:**
- Use HTTP client to measure round-trip time (request → response)
- Exclude network latency (measure on localhost)
- Sample 1000+ requests per endpoint
- Calculate p50, p95, p99, max percentiles

**[PERF-API-010]** API Response Time Test Conditions:
- **Load:** Single concurrent request (no concurrency stress testing)
- **Network:** Localhost (127.0.0.1) - eliminates network latency
- **Database state:** Pre-populated with 1000 passages, queue with 10 entries
- **System state:** wkmp-ap running in steady-state (not during decode/crossfade)
- **Sample size:** 1000 requests per endpoint category
- **Request distribution:**
  - 40% GET /queue (most common read operation)
  - 20% GET /status
  - 20% POST /playback/enqueue
  - 10% POST /playback/play, POST /playback/pause
  - 10% POST /playback/skip, DELETE /queue/:id
- **Timing measurement:** High-resolution timestamps using `std::time::Instant` at HTTP request arrival and response send
- **Warmup:** Discard first 100 requests (JIT warmup, cache warmup)
- **Rationale:** Single-user scenario focuses on implementation efficiency; concurrency testing out of scope for performance targets

**Acceptance Criteria:**
- p50 response time ≤ target for all endpoint categories
- p95 response time ≤ target for all endpoint categories
- p99 response time ≤ maximum tolerable
- No timeouts under normal load

---

### 6. Skip Latency

**Definition:** Time from skip command to playback of next passage.

**Targets:**

| Metric | Target | Maximum Tolerable | Notes |
|--------|--------|-------------------|-------|
| **Skip to Next (Buffered)** | ≤ 0.1 seconds | 2.0 seconds | Next passage already in buffer |
| **Skip to Next (Unbuffered)** | ≤ 1.0 seconds | 2.0 seconds | Next passage needs decode |
| **Skip to Arbitrary** | ≤ 2.0 seconds | 5.0 seconds | User selects passage from queue |

**Context:**
- Buffered skip should be nearly instantaneous (mixer state change)
- Unbuffered skip requires decode initiation (unavoidable latency)
- Arbitrary skip may require buffer cleanup + new decode
- Target ensures responsive user experience

**Measurement Methodology:**
- Measure monotonic elapsed time from skip command to first audio output (see SPEC023)
- Test buffered case (crossfade in progress, next passage ready)
- Test unbuffered case (next passage not yet decoded)
- Test arbitrary skip (random queue position selection)

**Acceptance Criteria:**
- Buffered skip meets target 100% of time
- Unbuffered skip meets target 90% of time
- All skip operations meet maximum tolerable latency

---

### 7. UI Update Delay

**Definition:** Latency from internal state change to SSE event delivery to UI.

**Targets:**

| Event Type | Target | Maximum Tolerable | Notes |
|------------|--------|-------------------|-------|
| **Playback State Change** | ≤ 50 ms | 500 ms | Play, Pause, Stop events |
| **Queue Modification** | ≤ 100 ms | 1000 ms | Add, Remove, Reorder events |
| **Crossfade Transition** | ≤ 100 ms | 500 ms | Crossfade start/end notifications |
| **Error Conditions** | ≤ 200 ms | 5000 ms | Decode failure, buffer underrun |

**Context:**
- Faster is always better for multi-user coordination
- Target provides near-real-time updates
- Maximum tolerable prevents stale UI state confusion
- Error conditions less critical but should still be timely

**Measurement Methodology:**
- Inject timestamps at state change point and SSE emission point
- Calculate delta between internal event and SSE transmission
- Sample 1000+ events across all event types
- Calculate p50, p95, p99, max

**Acceptance Criteria:**
- p50 event delay ≤ target for all event types
- p95 event delay ≤ target for all event types
- No event delivery failures (except client disconnection)

---

## Performance Testing Strategy

### Testing Phases

**Phase 1: Component-Level Performance (During Implementation)**
- Unit test each component with performance assertions
- Decode: Measure monotonic elapsed time for 100 passages (see SPEC023)
- Resample: Measure CPU time for various sample rate conversions
- Mixer: Measure tick processing time (must be < tick duration)

**Phase 2: Integration Performance (Feature Complete)**
- End-to-end playback session (30 minutes continuous)
- Monitor CPU, memory, decode latency, API response time
- Verify no resource leaks or degradation over time

**Phase 3: Hardware Validation (Pi Zero 2W)**
- Deploy to actual Pi Zero 2W hardware
- Run performance test suite under realistic conditions
- Measure all targets defined above
- Validate acceptance criteria

### Performance Regression Testing

**Automated Performance Tests:**
- Decode 100 passages, assert 90% meet latency target
- Playback 30-minute session, assert CPU average ≤ target
- Memory monitoring, assert no leaks (stable RSS over time)
- API benchmark, assert response times ≤ targets

**Continuous Integration:**
- Run performance tests on every commit (x86_64 baseline)
- Run hardware validation tests weekly (Pi Zero 2W)
- Alert on performance regressions (>10% degradation)

---

## Performance Optimization Priorities

If implementation fails to meet targets, optimize in this order:

**Priority 1: Playback Thread Efficiency**
- Audio callback must be lock-free and minimal work
- Target: ≤10% CPU on single core
- Rationale: Audio glitches unacceptable, highest user impact

**Priority 2: Decode Latency**
- Optimize decode/resample pipeline for throughput
- Target: 15-second buffer in ≤2 seconds
- Rationale: User-perceived responsiveness, skip latency dependent

**Priority 3: Memory Footprint**
- Minimize buffer overhead, release buffers promptly
- Target: ≤150 MB total application RSS
- Rationale: Pi Zero 2W constraint, prevents OOM kills

**Priority 4: CPU Average**
- Optimize decode thread efficiency
- Target: ≤30% average aggregate CPU
- Rationale: Allows headroom for other services

**Priority 5: API Response Time**
- Optimize database queries, minimize locks
- Target: p50 ≤50ms, p95 ≤150ms
- Rationale: User experience, multi-user coordination

---

## Out-of-Scope Performance Considerations

**Explicitly NOT performance targets:**
- **Startup time:** Not specified (acceptable if ≤10 seconds)
- **Database query performance:** Covered by API response time targets
- **Network bandwidth:** SSE event size negligible, not a constraint
- **Disk I/O:** Limited by SD card speed, not optimizable at application level
- **Power consumption:** Not a design goal (Pi Zero 2W plugged into AC power)

---

## Traceability

**Requirements Satisfied:**
- REQ-TECH-011: Raspberry Pi Zero 2W deployment support (implied performance constraints)
- REQ-XFD-010: Sample-accurate crossfading (requires low-latency decode)
- REQ-RESP-010: Responsive user interface (API response time targets)

**Design Decisions Informed By:**
- SPEC016 [DBD-DEC-040]: Serial decode (affects throughput targets)
- SPEC016: 15-second incremental buffering (affects decode latency targets)
- SPEC002: Crossfade timing (affects skip latency targets)

**Implementation Impact:**
- IMPL001: Database schema (query performance affects API response time)
- GUIDE001: Implementation plan (testing phases must include performance validation)

---

## Revision History

| Date | Version | Changes | Author |
|------|---------|---------|--------|
| 2025-10-25 | 1.0 | Initial specification - comprehensive performance targets | Claude |
| 2025-10-26 | 1.1 | Added measurement methodology clarifications: [PERF-MEASURE-010] Initial playback start measurement point, [PERF-CPU-010] CPU percentage calculation method, [PERF-MEM-010] Memory baseline measurement requirement, [PERF-API-010] API response time test conditions | Claude |

---

## Appendix A: Performance Target Rationale

### Why These Specific Numbers?

**Decode Latency (≤2 seconds for 15-second buffer):**
- Provides 7.5x real-time throughput
- Allows decode to complete during typical crossfade (5-10 seconds)
- Prevents queue starvation during continuous playback
- Target of 0.1 seconds for initial playback optimizes perceived responsiveness

**CPU Usage (≤30% average, ≤50% max):**
- Leaves 50% headroom for wkmp-ui, wkmp-pd, OS services
- Pi Zero 2W thermal throttling starts ~80% sustained load
- Prevents audio glitches from CPU starvation
- Allows future feature expansion without performance degradation

**Memory Usage (≤150 MB, ≤200 MB max):**
- Leaves ~300 MB for OS + other services on 512 MB system
- Prevents OOM killer from terminating wkmp-ap
- Allows 2-3 buffered passages (current + next + prefetch)
- Buffer overhead calculation: 15 sec × 44.1 kHz × 2 ch × 4 bytes = 5.29 MB per passage

**API Response Time (≤50ms p50, ≤150ms p95):**
- Human perception: <100ms feels instantaneous, <300ms feels responsive
- Targets "instantaneous" for common operations
- Allows multi-user coordination without perceptible lag
- Maximum tolerable (500ms) prevents user frustration

**Skip Latency (≤0.1s buffered, ≤1.0s unbuffered):**
- Buffered skip: instant gratification for user action
- Unbuffered skip: acceptable for occasional case
- Target balances user experience with implementation complexity

**UI Update Delay (≤50ms playback state, ≤100ms queue changes):**
- Multi-user scenario: User A skips track, User B sees update near-instantly
- Prevents confusion from stale UI state
- Targets "real-time" perception (<100ms)

### Validation of Targets on Pi Zero 2W

**Estimated Decode Performance:**
- `symphonia` FLAC decode: ~20-40x real-time on Pi Zero 2W (estimated from Pi 3 benchmarks)
- `rubato` resample: ~10-20x real-time on ARMv8 (estimated)
- Combined pipeline: ~7-10x real-time (conservative estimate)
- Target of 7.5x real-time is achievable

**Estimated CPU Usage:**
- Audio callback: ~5-10% single core (minimal work, lock-free)
- Decode thread: ~40-60% single core during active decode
- Aggregate: ~15-20% average (decode not continuous), ~60% peak
- Targets achievable with efficient implementation

**Memory Validation:**
- 3 passages × 10 MB per passage = 30 MB buffer memory
- wkmp-ap binary + Rust runtime: ~50-80 MB
- Database connections, HTTP server, event system: ~20-40 MB
- Total estimate: ~100-150 MB (within target)

---

## Appendix B: Performance Test Suite Specification

### Test Suite Structure

**Location:** `wkmp-ap/tests/performance/`

**Test Files:**
- `decode_latency_test.rs` - Decode timing measurements
- `cpu_usage_test.rs` - CPU profiling during playback
- `memory_usage_test.rs` - RSS monitoring over time
- `api_response_time_test.rs` - HTTP endpoint benchmarks
- `skip_latency_test.rs` - Skip operation timing
- `integration_performance_test.rs` - End-to-end 30-minute session

### Test Data Requirements

**Audio Test Files:**
- 100 diverse passages (various formats, sample rates, durations)
- FLAC: 44.1 kHz, 48 kHz, 96 kHz
- MP3: 128 kbps, 256 kbps, 320 kbps
- Opus: 96 kbps, 128 kbps
- AAC: 128 kbps, 256 kbps

**Test Database:**
- Pre-populated with 1000 passages
- Realistic queue lengths (5, 10, 20 passages)
- Representative musical flavor vectors

### Automated Test Execution

**CI/CD Integration:**
```bash
# Run performance test suite
cargo test --release --package wkmp-ap --test performance_tests

# Generate performance report
cargo run --release --package wkmp-ap --bin performance_report

# Compare against baseline (detect regressions)
./scripts/performance_regression_check.sh
```

**Performance Report Output:**
- Markdown table with all metrics (actual vs target vs max tolerable)
- Pass/Fail status for each acceptance criterion
- Percentile distributions (p50, p95, p99, max)
- Historical trend graph (detect regressions over time)

---

**Document Complete**
