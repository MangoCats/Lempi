> ⚠️ **INHERITED DOCUMENT — HISTORICAL EVIDENCE**
>
> Copied from `McRhythm/TECHNICAL_DEBT_ANALYSIS.md` on 2026-08-09. **Not a Lempi specification.**
>
> McRhythm's own debt analysis: duplicate extractor hierarchies, 71K-line ingest service. Source of [GDE-MCR-030].
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# WKMP Technical Debt Analysis Report
**Comprehensive Analysis of wkmp-ai and wkmp-common Codebases**

**Analysis Date:** 2025-11-10  
**Scope:** wkmp-ai (80 .rs files, 24,596 LOC), wkmp-common (20 .rs files, 9,299 LOC)  
**Methodology:** Systematic search for anti-patterns, code duplication, missing documentation, and architectural issues

---

## Executive Summary

**Overall Health:** GOOD with noted architectural concerns  
**Critical Issues:** 2 (architectural duplication)  
**High Priority Issues:** 8 (missed API documentation, performance concerns)  
**Medium Priority Issues:** 12 (code organization, utility consolidation)  
**Low Priority Issues:** 7 (minor cleanup opportunities)  

**Key Findings:**
1. **Duplicate Module Hierarchies** - Extractors/fusers implemented in two parallel systems
2. **Large Monolithic Files** - workflow_orchestrator.rs at 2,253 LOC with acknowledged debt
3. **Inconsistent API Documentation** - ~15% of public APIs lack doc comments
4. **Performance: Excessive cloning** - 141 clone() calls in wkmp-ai, many on PathBuf/String
5. **Missing Test Coverage** - 67.5% of files (54/80) have test modules; 26 files without any tests
6. **Stub/Placeholder Code** - Legacy implementation phases coexist with PLAN024 pipeline

---

## 1. DUPLICATE CODE PATTERNS (CRITICAL)

### 1.1 Parallel Extractor Implementations
**Severity:** CRITICAL (architectural debt)  
**Impact:** Maintenance burden, confusion about which to use, potential divergence

**Issue:** Two complete extractor module hierarchies exist in parallel:
- **Location 1:** `/wkmp-ai/src/extractors/` (Tier 1 modern implementation)
- **Location 2:** `/wkmp-ai/src/fusion/extractors/` (Legacy/older implementation)

**Files Involved:**
```
wkmp-ai/src/extractors/
├── acoustid_client.rs (468 lines)
├── audio_derived_extractor.rs (505 lines)
├── chromaprint_analyzer.rs
├── essentia_analyzer.rs (479 lines)
├── id3_extractor.rs
├── id3_genre_mapper.rs (481 lines)
├── musicbrainz_client.rs (504 lines)
└── mod.rs (293 lines)

wkmp-ai/src/fusion/extractors/
├── acoustid_client.rs
├── audio_derived_extractor.rs
├── audio_extractor.rs
├── chromaprint_analyzer.rs
├── essentia_analyzer.rs
├── genre_mapping.rs
├── id3_extractor.rs
├── musicbrainz_client.rs
└── mod.rs (86 lines)
```

**Code Reference:**
- `/wkmp-ai/src/extractors/mod.rs:1-10` - Documents "PLAN024 3-tier hybrid fusion"
- `/wkmp-ai/src/fusion/extractors/mod.rs:1-5` - References "PLAN023" (legacy)
- `/wkmp-ai/src/lib.rs:9-11` - Publicly exports both

**Trait Divergence:** Different trait definitions
- `extractors/mod.rs`: Uses `SourceExtractor` trait from `types` module
- `fusion/extractors/mod.rs`: Uses `Extractor` trait (incompatible interface)

**Recommendation:** 
- **IMMEDIATE:** Add architectural decision document explaining which is authoritative
- **SHORT-TERM:** Deprecate legacy module (fusion/extractors/) with feature gate
- **MEDIUM-TERM:** Remove fusion/extractors/ entirely when PLAN024 fully stabilized

**Estimated Effort:** 4-6 hours consolidation + refactoring

---

### 1.2 Parallel Fuser Module Structures  
**Severity:** HIGH (duplication)  
**Impact:** Code review difficulty, split test coverage

**Issue:** Fusers exist in two locations (though less severe than extractors):
- `/wkmp-ai/src/fusion/` (actual implementations)
- `/wkmp-ai/src/fusion/fusers/` (organization layer)

**Files:**
```
fusion/
├── flavor_synthesizer.rs (561 lines)
├── identity_resolver.rs (454 lines)
├── metadata_fuser.rs (500 lines)
└── mod.rs (31 lines)

fusion/fusers/
├── flavor_synthesizer.rs (stub reference)
├── identity_resolver.rs (stub reference)
├── metadata_fuser.rs (stub reference)
└── mod.rs (3 lines)
```

**Details:**
- `/wkmp-ai/src/fusion/mod.rs` - Implements 3 fusers directly, re-exports them
- `/wkmp-ai/src/fusion/fusers/mod.rs` - Empty subdirectory (no actual implementation)

**Recommendation:**
- Remove `fusion/fusers/` subdirectory entirely (move implementations if needed)
- Keep flat structure in `fusion/` with clear module separation

---

## 2. OVERLY LARGE FUNCTIONS/MODULES (HIGH PRIORITY)

### 2.1 workflow_orchestrator.rs - 2,253 lines (file size itself is an issue)
**Severity:** HIGH (maintainability)  
**Location:** `/wkmp-ai/src/services/workflow_orchestrator.rs:1-2253`  
**Lines:** 2,253 total, single struct with 7 async phase methods

**Acknowledged Debt:** Lines 20-22 explicitly state:
```rust
// Future Refactoring
// This 1,459-line file could be split into separate modules per state for better
// maintainability (see technical debt review for details).
```

**Phase Breakdown:**
| Phase | Method | Lines | Content |
|-------|--------|-------|---------|
| SCANNING | phase_scanning | ~200 | File discovery |
| EXTRACTING | phase_extracting | ~300 | ID3 metadata |
| FINGERPRINTING | phase_fingerprinting | ~400 | Chromaprint + AcoustID |
| SEGMENTING | phase_segmenting | ~200 | Silence detection |
| ANALYZING | phase_analyzing | ~300 | Amplitude analysis |
| FLAVORING | phase_flavoring | ~400 | AcousticBrainz fetch |
| PLAN024 | execute_import_plan024 | ~200 | New pipeline (PLAN024) |

**Issue Details:**
1. State machine logic mixed with service orchestration
2. Event bus emissions scattered throughout
3. Multiple parallel processing patterns (rayon + tokio)
4. Progress tracking replicated across phases
5. Cancellation token checks in every phase

**Recommendation:** Split into phase modules:
```
services/
├── orchestrator.rs (coordinator, 150 lines)
├── orchestrator/
│   ├── phase_scanning.rs
│   ├── phase_extracting.rs
│   ├── phase_fingerprinting.rs
│   ├── phase_segmenting.rs
│   ├── phase_analyzing.rs
│   ├── phase_flavoring.rs
│   └── mod.rs (re-exports)
└── workflow_state.rs (state machine)
```

**Estimated Effort:** 8-12 hours

---

### 2.2 Other Large Files
**Severity:** MEDIUM (code smell)

| File | Lines | Issue |
|------|-------|-------|
| `/wkmp-ai/src/api/ui.rs` | 1,308 | HTML rendering logic should extract template |
| `/wkmp-ai/src/workflow/storage.rs` | 923 | Multiple storage concerns (passage + metadata) |
| `/wkmp-ai/src/workflow/pipeline.rs` | 605 | Could extract boundary detection |
| `/wkmp-ai/src/validators/consistency_validator.rs` | 658 | Complex nested logic |
| `/wkmp-ai/src/validators/quality_scorer.rs` | 652 | Large scoring algorithm |
| `/wkmp-ai/src/workflow/boundary_detector.rs` | 304 | Silence detection algorithm |
| `/wkmp-common/src/events.rs` | 1,711 | Large enum (but reasonable, well-documented) |
| `/wkmp-common/src/params.rs` | 1,450 | Large enum (reasonable, but consider splitting) |
| `/wkmp-common/src/db/migrations.rs` | 1,189 | Migration functions are sequential (acceptable) |

---

## 3. MISSING API DOCUMENTATION (HIGH PRIORITY)

### 3.1 Public APIs Without Doc Comments
**Severity:** HIGH (API usability)  
**Count:** 15+ public functions/types identified

**Examples of undocumented public APIs:**

| Location | Function/Type | Status |
|----------|---------------|--------|
| `/wkmp-ai/src/config.rs:19` | `resolve_acoustid_api_key()` | ❌ Missing doc comment |
| `/wkmp-ai/src/config.rs:91` | `is_valid_key()` | ❌ No doc comment |
| `/wkmp-ai/src/config.rs:104` | `sync_settings_to_toml()` | ❌ No doc comment |
| `/wkmp-ai/src/config.rs:144` | `migrate_key_to_database()` | ❌ No doc comment |
| `/wkmp-ai/src/services/acoustid_client.rs:18` | Public struct fields | ❌ No field-level docs |
| `/wkmp-ai/src/ffi/chromaprint.rs:*` | Public functions | ❌ Minimal docs |
| `/wkmp-ai/src/validators/quality_scorer.rs` | Public methods | ⚠️ Partial |
| `/wkmp-ai/src/services/file_tracker.rs` | Public tracking functions | ⚠️ Partial |

**Details:**
- 54 files have tests but lack doc comments in initial sections
- Public `struct` fields often lack documentation
- Trait implementations lack "# Implementation Notes"

**CLAUDE.md Standard:** Per project documentation guidelines:
> "All public APIs MUST have doc comments at module level"

**Recommendation:**
1. Add `///` doc comments to all public functions (non-negotiable)
2. Document public struct fields
3. Add examples for complex types
4. Run `cargo doc --no-deps` and review warnings

**Estimated Effort:** 4-6 hours

---

## 4. PERFORMANCE ISSUES (HIGH PRIORITY)

### 4.1 Excessive Cloning (141 instances)
**Severity:** MEDIUM-HIGH (performance, memory)  
**Count:** 141 `.clone()` calls in wkmp-ai; 11 in wkmp-common (asymmetry)

**Problem Areas:**

| Category | Count | Details |
|----------|-------|---------|
| PathBuf clones | ~25 | Expensive filesystem paths cloned repeatedly |
| String clones | ~35 | Configuration strings cloned on each use |
| Struct clones in loops | ~15 | Session/progress objects in loop iterations |
| Arc clones | ~20 | Arc<RwLock<>> unwrapping then cloning |
| HashMap clones | ~8 | Entire collections cloned for merging |

**Hot Spots (actual clones in control loops):**
```rust
// wkmp-ai/src/services/workflow_orchestrator.rs:701-702 (in loop)
let progress_counter = processed_count.clone();    // Arc clone in loop
let progress_success = success_count.clone();      // Arc clone in loop

// wkmp-ai/src/services/file_tracker.rs:269, 505 (in merge logic)
let mut merged = existing.clone();                 // Full struct clone

// wkmp-ai/src/workflow/pipeline.rs (passage context passing)
let ctx = ctx.clone();                             // PassageContext clone
```

**Specific Examples:**

1. **File Path Cloning** - `/wkmp-ai/src/services/workflow_orchestrator.rs:361`
```rust
session.progress.current_file = Some(relative_path.clone());  // Clones PathBuf
```
**Fix:** Use references or `Arc<Path>` if sharing needed

2. **Session Root Folder** - `/wkmp-ai/src/services/workflow_orchestrator.rs:122, 227, 525, 619, 686`
```rust
root_folder: session.root_folder.clone(),  // String clone on event broadcast
```
**Fix:** Use `&str` in event if not modified, or `Arc<String>`

3. **Metadata Clones in Loop** - `/wkmp-ai/src/services/workflow_orchestrator.rs:581-583`
```rust
metadata.title.clone(),    // Multiple String clones
metadata.artist.clone(),   // in tight loop
metadata.album.clone(),
```

**Recommendation:**
1. Profile with `cargo flamegraph` to identify actual hotspots
2. Use `Arc<T>` for frequently cloned large structs
3. Use `Cow<str>` for conditional mutation scenarios
4. Replace loop clones with references where possible
5. Consider `SmallVec` for session-local collections

**Estimated Effort:** 4-8 hours (after profiling)

---

### 4.2 Potential Memory Leaks / Accumulation
**Severity:** MEDIUM (long-running service)

**Concern Areas:**
1. EventBus capacity (100 event buffer) - could accumulate if SSE clients disconnect ungracefully
2. Cancellation token HashMap - sessions removed but HashMap may not shrink
3. Import session state - no apparent cleanup of completed sessions before restart
   - `/wkmp-ai/src/main.rs:123-135` - cleanup_stale_sessions is called at startup only

**Recommendation:**
- Add periodic cleanup task (every N minutes) for completed sessions
- Review EventBus implementation in wkmp-common for dropped subscriber handling

---

## 5. CODE DUPLICATION / DIVERGENCE (MEDIUM PRIORITY)

### 5.1 AcoustID Client Implementations (TWO VERSIONS)
**Severity:** MEDIUM (maintenance burden)  
**Location:** 
- `/wkmp-ai/src/services/acoustid_client.rs` (441 lines) - old version
- `/wkmp-ai/src/extractors/acoustid_client.rs` (468 lines) - new version
- `/wkmp-ai/src/fusion/extractors/acoustid_client.rs` - duplicate

**Differences:**
- Services version: standalone client with rate limiting, manual API structure parsing
- Extractors version: implements `SourceExtractor` trait, confidence scoring
- Fusion version: minimal wrapper

**Action:** Mark services version as deprecated, migrate to extractors version

---

### 5.2 MusicBrainz Client (THREE VERSIONS)
**Severity:** MEDIUM (confusion)  
**Locations:**
- `/wkmp-ai/src/services/musicbrainz_client.rs` (245 lines)
- `/wkmp-ai/src/extractors/musicbrainz_client.rs` (504 lines)
- `/wkmp-ai/src/fusion/extractors/musicbrainz_client.rs`

**Issue:** Services version is simpler; extractors version is fuller-featured. Which is authoritative?

---

## 6. MISSING TEST COVERAGE (MEDIUM PRIORITY)

### 6.1 Files Without Test Modules
**Severity:** MEDIUM-HIGH (regression risk)  
**Count:** 26 files (32.5% of wkmp-ai)

| File | Reason |
|------|--------|
| `/wkmp-ai/src/api/ui.rs` | HTML rendering (hard to test) |
| `/wkmp-ai/src/api/sse.rs` | SSE streaming |
| `/wkmp-ai/src/api/health.rs` | Simple pass-through |
| `/wkmp-ai/src/db/*.rs` | Database queries (integration tests needed) |
| `/wkmp-ai/src/models/*.rs` | Data models only |
| `/wkmp-ai/src/ffi/chromaprint.rs` | FFI bindings (unit test limitations) |
| `/wkmp-ai/src/services/essentia_client.rs` | External process (stub) |
| `/wkmp-ai/src/validators/*.rs` | Partial coverage |

**Recommendations:**
1. Add integration test suite for API endpoints
2. Add unit tests for database DAOs (with test database)
3. Add property-based tests for validation algorithms
4. Aim for 70%+ coverage target

**Estimated Effort:** 16-24 hours

---

## 7. ARCHITECTURAL INCONSISTENCIES (MEDIUM PRIORITY)

### 7.1 Two Parallel Implementation Philosophies
**Severity:** MEDIUM (future maintenance)

**Issue:** Codebase implements both:
- **PLAN023:** Legacy architecture (services + workflow_orchestrator phases)
- **PLAN024:** New 3-tier hybrid fusion (extractors/fusion/validators + pipeline)

**Evidence:**
```rust
// workflow_orchestrator.rs:202-204
/// Execute import workflow using PLAN024 pipeline
/// **[PLAN024]** Modern 3-tier hybrid fusion pipeline
pub async fn execute_import_plan024(&self, ...)

// BUT ALSO:
// workflow_orchestrator.rs:138-156
// Phase 3: FINGERPRINTING - Audio fingerprinting (stub)
// Phase 4: SEGMENTING - Passage detection (stub)
// Phase 5: ANALYZING - Amplitude analysis (stub)
// Phase 6: FLAVORING - Musical flavor extraction (stub)
```

**Status:** No clear deprecation of PLAN023, both coexist

**Recommendation:** 
1. Add CHANGELOG entry explaining migration path
2. Mark PLAN023 methods as `#[deprecated]` 
3. Document sunset date for PLAN023

**Estimated Effort:** 2 hours documentation + 1 hour code marking

---

### 7.2 Inconsistent Error Type Usage
**Severity:** LOW-MEDIUM (API consistency)

**Issue:** Three error type systems coexist:
1. `/wkmp-ai/src/error.rs` - ApiError enum (HTTP response)
2. `/wkmp-ai/src/types.rs` - ExtractionError, FusionError, ValidationError
3. `anyhow::Error` (generic fallback)

**Files Using Different Error Types:**
- API handlers: `ApiError` → HttpResponse
- Services: `anyhow::Error` → propagated
- Extractors/Fusers: Custom domain errors → Result<T>

**Recommendation:** Document when to use each; consider unified error translation layer

---

## 8. INCONSISTENT NAMING & PATTERNS (LOW-MEDIUM PRIORITY)

### 8.1 Naming Inconsistencies
**Severity:** LOW (code clarity)

| Pattern | Examples | Issue |
|---------|----------|-------|
| Module organization | `extractors/` vs `fusion/extractors/` | Which is primary? |
| Client naming | `AcoustIDClient`, `MusicBrainzClient` vs `EssentiaClient` | PascalCase inconsistency |
| Error types | `AcoustIDError`, `MBError` vs `ExtractionError` | Inconsistent suffix |
| Trait methods | `extract()` vs `extract_all()` | Method naming divergence |

**Recommendation:** Add naming convention document to project guidelines

---

### 8.2 Inconsistent Re-export Patterns
**Severity:** LOW (import ergonomics)

**Pattern Variance:**
```rust
// Some modules re-export extensively
pub use acousticbrainz_client::{ABError, ABLowLevel, AcousticBrainzClient, MusicalFlavorVector};

// Others don't
pub mod pipeline;
pub use pipeline::{Pipeline, PipelineConfig};

// Some use wildcard
use crate::extractors::*;

// Others explicit
use crate::extractors::ID3Extractor;
```

**Recommendation:** Standardize to explicit re-exports with clear scope (all public types)

---

## 9. DEAD CODE & DEPRECATION (LOW PRIORITY)

### 9.1 `#[allow(dead_code)]` Annotations
**Severity:** LOW (code cleanliness)  
**Count:** 4 instances

| Location | Issue |
|----------|-------|
| `/wkmp-ai/src/api/import_workflow.rs:346` | Dead function marked |
| `/wkmp-ai/src/services/acoustid_client.rs:16` | Const `ACOUSTID_API_KEY` placeholder |
| `/wkmp-ai/src/services/amplitude_analyzer.rs:52` | Unused field |
| `/wkmp-ai/src/services/silence_detector.rs:169` | Unused helper |

**Recommendation:** Review each; either use or remove

---

### 9.2 Commented-Out Code
**Severity:** LOW (code hygiene)  
**Examples:**
- `/wkmp-ai/src/workflow/mod.rs:11` - `// pub mod song_processor;  // Legacy - replaced by pipeline.rs`
- `/wkmp-ai/src/fusion/extractors/mod.rs:18` - `// pub mod essentia_analyzer; // Deferred to future increment`

**Recommendation:** Remove completely; use git history if needed

---

## 10. CONFIGURATION MANAGEMENT (LOW-MEDIUM PRIORITY)

### 10.1 Multi-Tier Configuration Complexity
**Severity:** LOW-MEDIUM (operation burden)

**Issue:** AcoustID API key resolved via 3-tier fallback:
1. Database (authoritative)
2. Environment variable
3. TOML config file

**Concern:** Multiple sources can conflict; warning logged but no enforcement

**Location:** `/wkmp-ai/src/config.rs:49-55`
```rust
// Warn if multiple sources (potential misconfiguration)
if sources.len() > 1 {
    warn!("AcoustID API key found in multiple sources: {}. Using database (highest priority).",
          sources.join(", "));
}
```

**Recommendation:** Document in deployment guide; consider read-only TOML after migration

---

## 11. WKMP-COMMON SPECIFIC ISSUES

### 11.1 Large Enum Types
**Severity:** LOW (style)

| File | Size | Type | Concern |
|------|------|------|---------|
| `/wkmp-common/src/events.rs` | 1,711 lines | `WkmpEvent` enum | Large but well-documented |
| `/wkmp-common/src/params.rs` | 1,450 lines | Parameter types | Could split into modules |

**Note:** These are acceptable sizes given documentation quality; no action required

---

### 11.2 Test File Organization
**Severity:** LOW (testing)

- `/wkmp-common/src/timing_tests.rs` - Placed in src/ rather than tests/
- Should be in `wkmp-common/tests/timing_tests.rs`

**Note:** Likely an organizational preference; verify intent before moving

---

## 12. MISSING PATTERNS / BEST PRACTICES

### 12.1 Comprehensive Error Context
**Severity:** MEDIUM (debuggability)

**Issue:** Some errors lack context. Example:
```rust
// wkmp-ai/src/services/acoustid_client.rs (hypothetical)
Err(e) => Err(AcoustIDError::NetworkError(format!("{}", e)))
```

Better with `anyhow::Context`:
```rust
client.get(url).await.context("Failed to query AcoustID")?
```

**Recommendation:** Adopt `.context()` pattern where appropriate

---

### 12.2 Missing Logging in Critical Paths
**Severity:** LOW-MEDIUM (operational visibility)

**Coverage:** Good generally, but some areas lack debug/trace logging:
- Individual extractor operations (only at info level)
- Fusion fuser logic (sparse logging)
- Validation criteria (insufficient)

**Recommendation:** Add structured logging (tracing spans) for pipeline phases

---

## Summary Table by Category

| Category | Count | Critical | High | Medium | Low |
|----------|-------|----------|------|--------|-----|
| Duplication | 5 | 2 | 1 | 2 | - |
| Large Modules | 10 | - | 1 | 9 | - |
| Missing Docs | 15+ | - | 1 | - | - |
| Performance | 3 | - | 2 | 1 | - |
| Tests | 26 | - | 1 | 1 | - |
| Architecture | 4 | - | - | 2 | 2 |
| Naming | 8 | - | - | 1 | 7 |
| Dead Code | 6 | - | - | - | 6 |
| Config | 2 | - | - | 1 | 1 |
| **TOTAL** | **79** | **2** | **6** | **17** | **16** |

---

## Recommended Priority Order (for remediation)

### Phase 1: Critical (1-2 weeks)
1. **Extractor/Fuser Duplication** - Document decision on PLAN023 vs PLAN024
2. **API Documentation** - Add doc comments to public APIs
3. **Clone Analysis** - Profile and optimize hot paths

### Phase 2: High Priority (2-4 weeks)
4. **Refactor workflow_orchestrator.rs** - Split into phase modules
5. **Consolidate AcoustID/MusicBrainz** - Remove duplicate implementations
6. **Add Integration Tests** - Build test harness for API/database

### Phase 3: Medium Priority (4-8 weeks)
7. **Architecture Documentation** - Publish PLAN023 sunset date
8. **Code Cleanup** - Remove dead code, fix naming
9. **Performance Optimization** - Use profiling results to optimize clones

### Phase 4: Low Priority (Ongoing)
10. **Minor Naming/Organization** - Standardize re-exports and patterns
11. **Add Debug Logging** - Improve operational observability

---

## Risk Assessment

**No Critical Defects Found** - Codebase is functional and well-structured  
**Main Risks:**
1. **Maintenance:** Parallel architectures (PLAN023/024) will diverge over time
2. **Performance:** Unchecked clone usage could cause scalability issues
3. **Reliability:** Missing test coverage in database/API layers

---

## Files Analyzed

**Total Files:** 100 (80 wkmp-ai + 20 wkmp-common)  
**Total Lines of Code:** 33,895  
**Test Coverage:** 54/80 wkmp-ai files (67.5%)  
**Documented Files:** 65/100 (65%)
