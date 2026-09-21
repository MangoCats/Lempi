> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/REQ001-requirements.md` on 2026-08-09. **Not a Lempi specification.**
>
> Functional requirements. 731 lines, 15 revision commits. The most refined requirements in the lineage.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# WKMP Requirements

**📜 TIER 1 - AUTHORITATIVE SOURCE DOCUMENT**

This document is the **top-level specification** defining WHAT WKMP must do. Other documents are designed to satisfy these requirements.

**Update Policy:** ✅ Product decisions only | ❌ NOT derived from design/implementation

> See Document Hierarchy for complete update policies and change control process.

> **Related Documentation:** [wkmp Project Charter](MCR-PCH001_project_charter.md) | API Design | UI Specification | Library Management | Crossfade Design | [Musical Flavor](MCR-SPEC003-musical_flavor.md) | [Program Director](MCR-SPEC005-program_director.md) | Event System | Requirements Enumeration

---

## Overview

**[REQ-OV-010]** WKMP is a music player that selects passages to play based on user preferences for [musical flavor](MCR-SPEC003-musical_flavor.md#quantitative-definition) at various times of day.

**Architectural Note:** WKMP is implemented as a microservices architecture with **6 independent HTTP-based modules**: Audio Player, User Interface, Lyric Editor, Program Director, Audio Ingest, and Database Review. See Architecture for complete design.

### Microservice UI Ownership

**[REQ-UI-010]** UI is distributed across microservices:

| Microservice | UI Responsibility | Access Method | Versions |
|--------------|------------------|---------------|----------|
| **wkmp-ui** | Main playback interface, library browser, preferences, user identity | http://localhost:5720 (primary access point) | All |
| **wkmp-ai** | Import wizard, file segmentation, passage boundary editor (initial import) | http://localhost:5723 (launched from wkmp-ui) | Full |
| **wkmp-le** | Lyric editor (split-window interface for timing sync) | http://localhost:5724 (launched from wkmp-ui) | Full |
| **wkmp-ap** | No UI (backend audio engine) | - | All |
| **wkmp-pd** | No UI (backend selection algorithm) | - | Full, Lite |

**[REQ-UI-020]** "WebUI" terminology clarification:
- "WebUI" in requirements refers to **browser-based UI** (HTML/CSS/JS), NOT exclusively wkmp-ui module
- wkmp-ui is PRIMARY UI (user starts here)
- wkmp-ai provides SPECIALIZED UI for import workflows
- wkmp-le provides SPECIALIZED UI for lyric editing
- All are web-based (accessed via web browser)

**[REQ-UI-030]** UI integration pattern:
- wkmp-ui is the **orchestrator**: provides launch points for specialized tools
- On-demand tools (wkmp-ai, wkmp-le) are **autonomous**: own their complete UX
- No UI embedding: wkmp-ui opens specialized tools in new browser tabs/windows
- Return navigation: specialized tools provide "Return to WKMP" links back to wkmp-ui

**See:** On-Demand Microservices for detailed pattern definition

## Core Features

**[REQ-CF-010]** Plays passages from local files (.mp3, .opus, .aac, .flac and similar)
  - **[REQ-CF-011]** Identifies one or multiple passage start / stop points and crossfade points within each music file

**[REQ-CF-020]** Records when songs within passages are played

**[REQ-CF-030]** Cross references passages to the MusicBrainz database for:
  - **[REQ-CF-031]** identification of the song(s) contained in the passage (see Definitions for definition of song in this context)
  - **[REQ-CF-032]** identification of other relationships that may influence selection of passages for enqueueing

**[REQ-CF-040]** Cross references recordings within passages to the AcousticBrainz database when possible, identifying musical character of each passage.

  **AcousticBrainz Status (as of 2024):**
  - The AcousticBrainz project ceased accepting new submissions in 2022
  - The existing database and API remain online in read-only mode
  - Contains musical analysis data for millions of recordings submitted before discontinuation
  - WKMP uses AcousticBrainz data when available, falls back to local Essentia analysis when not

> **See Library Management - AcousticBrainz Integration for fallback behavior.**

**[REQ-CF-050]** Local copies of all relevant subsets of online database information enabling offline operation

**[REQ-CF-060]** Web-based UI
  - **[REQ-CF-061]** Primary mode of operation is automatic, without user intervention
    - **[REQ-CF-061A]** Auto-start on boot
      - **[REQ-CF-061A1]** systemd service on Linux (Rollout phase 1)
      - **[REQ-CF-061A2]** Task scheduler launched service on Windows (Rollout phase 1)
      - **[REQ-CF-061A3]** launchd on macOS (Rollout phase 2)
    - **[REQ-CF-061B]** Auto-selection of passages to play
      - **[REQ-CF-061B1]** Automatically select passages until at least N passages are either currently playing, paused or waiting in the queue and at least M minutes of total passage playing time is remaining in the queue.
        - **[REQ-CF-061B1A]** N default value is 3 passages, and M default value is 15 minutes. These are user configurable parameters and persisted in the database.
      - **[REQ-CF-061B2]** Stop automatic enqueueing when no passages with associated songs are available to enqueue, because the library lacks passages with songs which are not currently available for automatic selection.
        - **[REQ-CF-061B2A]** When automatic enqueueing is unable to add passages to the queue, the queue may eventually become empty as passages finish playing.
  - **[REQ-CF-062]** Shows details of song or passage currently playing
    - **[REQ-CF-062A]** Shows associated album art or other still image(s) associated with the song when available
    - **[REQ-CF-062B]** Shows song lyrics when available
  - **[REQ-CF-063]** Shows basic identity of passages queued for playing next
  - **[REQ-CF-064]** Manual user controls allow the user to control and configure the system when they want to
    - **[REQ-CF-064A]** Access manual controls from phone and desktop browsers

**[REQ-CF-070]** Audio output to OS standard output channels
  - **[REQ-CF-071]** Analog
  - **[REQ-CF-072]** HDMI
  - **[REQ-CF-073]** Bluetooth

**[REQ-CF-080]** Multiple users may interact with the WebUI
  - **[REQ-CF-081]** Each user has a persistent identity (UUID) established through authentication or anonymous access
  - **[REQ-CF-082]** Real-time UI updates via Server Sent Events keep all users' views in sync
    - **[REQ-CF-082A]** All connected clients receive state change broadcasts within 100ms
  - **[REQ-CF-083]** Single shared passage queue for all users
  - **[REQ-CF-084]** User-specific data (likes, dislikes, taste profiles) tracked per user UUID
  - **[REQ-CF-085]** Concurrent user actions handled via specific strategies: skip throttling, idempotent queue removal, "last write wins" for lyrics
    - **[REQ-CF-085A]** Skip throttling: Only one skip per 5-second window honored across all users
    - **[REQ-CF-085B]** Subsequent skip attempts within throttle window are logged but ignored
    - **[REQ-CF-085C]** Throttle resets 5 seconds after last successful skip

> **See Multi-User Coordination for complete specifications on handling concurrent user actions.**

**[REQ-CF-090]** Passages play continuously, indefinitely (when not paused by user)
    
## Additional Features

**[REQ-AF-010]** Planned for later development (Rollout phase 2):
  - **[REQ-AF-012]** Mobile (Android, iOS) versions
    - **[REQ-AF-012A]** Automatically select passages to play based on database and audio files stored locally on phone or tablet.
## Three Versions

**[REQ-VER-010]** When not otherwise specified, requirements apply to all versions in Rollout phase 1.

### Full Version

**[REQ-VER-020]** Target Platforms:
- **[REQ-VER-021]** Linux desktop (Rollout phase 1)
- **[REQ-VER-022]** Windows (Rollout phase 1)
- **[REQ-VER-023]** macOS (Rollout phase 2)

**[REQ-VER-024]** Features:
- Player and Program Director (passage selector)
- All database building and maintenance
- File scanning and library management
- Preference editing (timeslots, base probabilities)
- Lyrics editing
- ChromaPrint+AcoustID song identification
- MusicBrainz/AcousticBrainz integration
- Essentia local analysis for musical flavor

**[REQ-VER-025]** Resource Profile:
- CPU: Higher (ChromaPrint & Essentia analysis during import)
- Disk I/O: Higher (file scanning)
- Memory: ~512MB typical
- Network: Two distinct network access types:
  - **Internet access**: Required for initial library setup (MusicBrainz, AcousticBrainz, etc.), optional for ongoing playback
  - **Local network access**: Required for remote WebUI access, not required for localhost or automatic playback

### Lite Version

**[REQ-VER-030]** Target Platforms:
- **[REQ-VER-031]** Raspberry Pi Zero2W (Rollout phase 1)
- **[REQ-VER-032]** Linux desktop (Rollout phase 1)
- **[REQ-VER-033]** Windows (Rollout phase 1)
- **[REQ-VER-034]** macOS (Rollout phase 2)

**[REQ-VER-035]** Features:
- Player and Program Director (passage selector)
- Preference editing (timeslots, base probabilities)
- Uses pre-built static database from Full version
- Read-only external data (MusicBrainz/AcousticBrainz cached)
- No file scanning, no Essentia

**[REQ-VER-036]** Resource Profile:
- CPU: Moderate (playback + selection only)
- Disk I/O: Low (no scanning, read-only database)
- Memory: ~256MB typical
- Network:
  - Local network: Required for remote WebUI access, not required for localhost access

### Minimal Version (Rollout phase 1 and 2)

**[REQ-VER-040]** Target Platforms:
- Raspberry Pi Zero2W (Rollout phase 1)
- Embedded systems (Rollout phase 2)
- Resource-constrained devices (Rollout phase 2)

**[REQ-VER-041]** Features:
- Player and User Interface only
- Read-only database and preferences
- No editing capabilities
  - No like/dislike
- Manual passage selection for enqueue via User Interface
- Smallest memory footprint
- No internet access
- Authentication: Always operates as Anonymous user
  - No login or account creation UI elements shown
  - See architecture.md#version-differentiation

**[REQ-VER-042]** Resource Profile:
- CPU: Minimal (playback + basic selection)
- Disk I/O: Minimal (read-only database)
- Memory: <256MB typical
- Network:
  - Internet: None required
  - Local network: Required for remote WebUI access, not required for localhost or automatic playback

### Build Strategy

**[REQ-VER-050]** See Implementation Order - Version Builds for compilation approach.

**[REQ-VER-051]** Database Deployment:
- Full version exports complete database snapshot, records passage play history, likes and dislikes, configure preference parameters, edit lyrics.
- Lite version imports database developed in full version, records passage play history, likes and dislikes.
- Minimal version imports database developed in full version, can operate in read only mode.
- Migration tools for version upgrades

## Technical Requirements

**[REQ-TECH-010]** Target platforms:
  - **[REQ-TECH-011]** Primary target: Raspberry Pi Zero2W (Rollout phase 1) (Lite and Minimal versions)
  - **[REQ-TECH-012]** Generic Linux (Rollout phase 1)
  - **[REQ-TECH-013]** Windows (Rollout phase 1)
  - **[REQ-TECH-014]** macOS (Rollout phase 2)
  - **[REQ-TECH-015]** Later targets (will use different technical stack, e.g. Flutter)
    - Android (Lite and Minimal versions) (Rollout phase 2)
    - iOS (Lite and Minimal versions) (Rollout phase 2)

**[REQ-TECH-020]** Technical Stack:
  - **[REQ-TECH-021]** Rust
  - **[REQ-TECH-022]** Single-stream audio architecture (symphonia for decoding, rubato for resampling, cpal for output)
    - **[REQ-TECH-022A]** Exception: Opus codec support via C library FFI
      - **Rationale:** Pure Rust Opus implementation not available in symphonia 0.5.x
      - **Implementation:** `symphonia-adapter-libopus` crate providing FFI bindings to libopus
      - **Scope:** Limited to Opus codec only; all other codecs use pure Rust implementations
      - **Precedent:** Consistent with existing Chromaprint C library usage for fingerprinting
      - **Deployment:** Requires libopus system library installation on target platforms
      - **See:** REV003-opus_c_library_exception.md for complete rationale
  - **[REQ-TECH-023]** SQLite
  - **[REQ-TECH-024]** Web server
  
## Definitions

**[REQ-DEF-010]** The terms track, recording, work, artist, song and passage have specific definitions found in [Entity Definitions](MCR-REQ002-entity_definitions.md).

### Musical Flavor

**[REQ-FLV-010]** Each passage is characterized to quantify its musical flavor. Details of how musical flavor is determined and used are found in [Musical Flavor](MCR-SPEC003-musical_flavor.md).

#### Musical Flavor Target by time of day

**[REQ-FLV-020]** A 24 hour schedule defines the Musical Flavor Target for each timeslot during the day/night.
  - **[REQ-FLV-021]** Users may adjust timeslots, adding / removing timeslots
    - one or more timeslots must always be defined
    - every time of day must be covered by one and only one defined timeslot
  - **[REQ-FLV-022]** Each timeslot definition includes one or more passages defining the musical flavor of that timeslot

**[REQ-FLV-030]** Users may temporarily override the timeslot defined musical flavor by manually selecting a musical flavor to use for a coming time window (e.g. 1 or 2 hours)

**[REQ-FLV-040]** Musical Flavor Target impacts selection of songs being enqueued, anticipated play start time of the passage to be enqueued based on current queued passages' play time compared to the anticipated musical flavor target at that time, either by schedule or user override.
  - **[REQ-FLV-041]** Passages in the queue are not impacted by changes to schedule based musical flavor targets
    - Passages are not interrupted by any time based transition of musical flavor targets
  - **[REQ-FLV-042]** When a user issues a temporary override for musical flavor target, a new passage is selected based on the new target, then once that passage is enqueued and ready to play all previous passages awaiting play are removed from the queue and any remaining play time on the currently playing song is skipped so play of the newly enqueued passage starts as soon as possible. The queue is then refilled based on the new user selected musical flavor target.

## Non-functional Requirements

**[REQ-NF-010]** Follow defined coding conventions.

**[REQ-NF-020]** Errors logged to developer interface, otherwise gracefully ignored and continue playing as best as able
  - developer interface is stdout/stderr

**[REQ-NF-030]** Configuration file graceful degradation - **APPLIES TO ALL MODULES**
  - **[REQ-NF-031]** When ANY microservice's TOML configuration file is missing, that module SHALL NOT terminate with an error
  - **[REQ-NF-032]** Missing configuration files SHALL result in (ALL modules):
    - A warning logged to stdout/stderr indicating the missing file path
    - Automatic fallback to compiled default values
    - Successful module startup with default configuration
  - **[REQ-NF-033]** Root folder default location (ALL modules, when config file absent and no command-line/environment override):
    - Linux: `~/Music` (user's Music folder)
    - macOS: `~/Music` (user's Music folder)
    - Windows: `%USERPROFILE%\Music` (user's Music folder)
  - **[REQ-NF-034]** Other configuration defaults (ALL modules, when config file absent):
    - Logging level: `info`
    - Log file: stdout only (no file logging)
    - Static assets: platform-specific standard paths (e.g., `/usr/local/share/wkmp/ui/` on Linux)
  - **[REQ-NF-035]** Priority order for root folder resolution (ALL modules, MANDATORY pattern):
    1. Command-line argument (highest priority: `--root-folder` or `--root`)
    2. Environment variable (second priority: `WKMP_ROOT_FOLDER` or `WKMP_ROOT`)
    3. TOML configuration file (third priority: `~/.config/wkmp/<module-name>.toml`)
    4. Compiled default (lowest priority, graceful fallback: platform-specific Music folder)
  - **[REQ-NF-036]** First-run experience (ALL modules):
    - When started with default Music folder path, module SHALL create necessary directories automatically
    - If database does not exist at root folder path, module SHALL create empty database with default schema
    - Module SHALL remain operational and log informational messages about initialization
  - **[REQ-NF-037]** Implementation enforcement:
    - ALL modules (wkmp-ui, wkmp-ap, wkmp-pd, wkmp-ai, wkmp-le) MUST use `wkmp_common::config::RootFolderResolver` for root folder resolution
    - ALL modules MUST use `wkmp_common::config::RootFolderInitializer` for directory and database initialization
    - NO module may hardcode database paths or skip the 4-tier resolution priority
    - This pattern ensures consistent zero-configuration behavior across the entire WKMP system

**[REQ-NF-038]** TOML Configuration Directory Auto-Creation - **APPLIES TO ALL MODULES**
  - When ANY microservice writes TOML configuration file to `~/.config/wkmp/<module>.toml`, the module SHALL automatically create parent directory if missing
  - Directory creation SHALL use secure permissions:
    - Linux/macOS: 0700 (user-only read/write/execute)
    - Windows: Default user-only permissions
  - If directory creation fails, the module SHALL return an error to caller (graceful degradation)
  - Implementation SHALL use `wkmp_common::config::write_toml_config()` which handles directory creation automatically
  - This ensures zero-configuration behavior for TOML file writes across all modules

> **See Architecture - Initialization for detailed startup sequence and default value handling.**

**[REQ-NF-050]** Operational Monitoring
  - **[REQ-NF-051]** Each microservice exposes `/health` HTTP endpoint returning HTTP 200 when operational
  - **[REQ-NF-052]** Health check responses complete within 2 seconds
  - **[REQ-NF-053]** Health endpoint returns JSON with service status: `{"status": "healthy", "module": "<module-name>"}`
  - **[REQ-NF-054]** Future enhancement: Detailed diagnostics (database connectivity, audio device availability, subsystem status)

> **See Architecture - Module Health Checks and API Design - Health Endpoint for implementation details.**

## Passage Identification & Library Management

**[REQ-PI-010]** Full version scans local audio files, extracts metadata, and associates passages with MusicBrainz entities. Lite and minimal versions use pre-built databases.

**[REQ-PI-020]** Support audio formats: MP3, FLAC, OGG, M4A, AAC, OPUS, WAV

**[REQ-PI-030]** Extract metadata from file tags and generate audio fingerprints for MusicBrainz lookup

**[REQ-PI-040]** Each audio file may contain one or more passages

**[REQ-PI-050]** Users must be able to:
- **[REQ-PI-051]** Define multiple passages within a single audio file
- **[REQ-PI-052]** Manually edit passage boundaries and timing points
- **[REQ-PI-053]** Add or delete passage definitions
- **[REQ-PI-054]** Associate each passage with MusicBrainz entities (tracks, recordings, artists, works)

**[REQ-PI-060]** On initial import, the system must assist users by offering automatic passage boundary detection. The detailed workflow for this is specified in [Audio File Segmentation](MCR-IMPL005-audio_file_segmentation.md).

**[REQ-PI-061]** Automatic lead-in detection based on amplitude analysis
- Detect slow amplitude ramps at passage start
- Threshold: 1/4 perceived audible intensity (RMS-based, default: -12dB below peak)
- Maximum lead-in duration: 5 seconds (user-configurable)
- Quick ramp-up detection: If 3/4 intensity reached < 1 second, zero lead-in

**[REQ-PI-062]** Automatic lead-out detection based on amplitude analysis
- Detect slow amplitude ramps at passage end
- Threshold: 1/4 perceived audible intensity (RMS-based, default: -12dB below peak)
- Maximum lead-out duration: 5 seconds (user-configurable)
- Quick ramp-down detection: If 3/4 intensity drops < 1 second, zero lead-out

**[REQ-PI-063]** User-adjustable algorithm parameters
- All amplitude analysis thresholds configurable (RMS window, dB thresholds, durations)
- Global defaults + per-passage overrides supported
- Parameter presets for common musical styles (Classical, Rock/Pop, Electronic)

**[REQ-PI-064]** Extensible metadata framework
- Support arbitrary numeric parameters (0.0-1.0 range)
- Examples: seasonal_holiday (0.0=regular, 1.0=Christmas), profanity_level (0.0=clean, 1.0=explicit)
- Parameters may be automatically determined, manually edited, or both
- Storage in `additional_metadata` JSON column (schema-less for extensibility)

> **See Amplitude Analysis for complete amplitude detection algorithm specification.**

**[REQ-PI-070]** Store MusicBrainz IDs and fetch basic metadata (artist names, release titles, genre tags)

**[REQ-PI-080]** Lyric editing UI provided by wkmp-le microservice (Full version only)
- Accessed via http://localhost:5724 (opened from wkmp-ui library view)
- See UI Specification - Lyric Editor for details
- **[REQ-PI-081]** Concurrent lyric editing uses "last write wins" strategy (no conflict resolution)
- **[REQ-PI-082]** Multiple users may edit lyrics simultaneously; last submitted text persists

> **See Multi-User Coordination - Concurrent Lyric Editing for concurrent editing behavior.**

> **See Library Management for file scanning workflows, metadata extraction details, and MusicBrainz integration process.**
> **See Crossfade Design for default passage timing point values.**

### Album Matching

**[REQ-PI-AM-010]** wkmp-ai MUST identify album editions from single-file recordings with ≥90% track count accuracy
- Match detected track boundaries against MusicBrainz release metadata
- Support albums stored as single continuous audio file (CD rips, vinyl recordings)
- Validate match quality by comparing expected vs detected track counts

**[REQ-PI-AM-020]** wkmp-ai MUST support multi-stage fallback algorithm for robust matching
- Stage 2: Parameter grid search (180 threshold/duration combinations)
- Stage 3: Dynamic programming assembly for over-segmented tracks
- Stage 4: RMS quiet spot detection when silence analysis fails
- Stage 5: Extra track merging for bonus material and hidden tracks

**[REQ-PI-AM-030]** wkmp-ai MUST discover 10-25 candidate editions per album via comprehensive MusicBrainz search
- Multi-strategy search: basic, CamelCase splitting, fuzzy matching, wildcard fixes
- Handle artist/album name variations, typos, and alternative spellings
- Early-exit optimization: stop on first strategy finding ≥10 candidates

**[REQ-PI-AM-040]** wkmp-ai MUST score and rank editions by metadata similarity
- Jaro-Winkler algorithm for artist/album name comparison
- Scoring weights: 60% artist similarity, 40% album similarity
- Filter to top 20 editions before detailed analysis

**[REQ-PI-AM-050]** wkmp-ai MUST achieve ≤10s mean track boundary error for matched albums
- Sample-accurate boundary detection using silence thresholds
- Tolerance: Allow ±10s deviation from expected track durations
- Validation: ≥65% tracks within tolerance for match success

**[REQ-PI-AM-060]** wkmp-ai MUST support caching for performance optimization
- Cache MusicBrainz API responses to reduce network latency
- Single-pass audio analysis: WindowDbProfile for 180x speedup
- Early-exit optimization: 87.6% of albums avoid full parameter sweep

> **See [Album Matching Specification](MCR-SPEC033-album_matching.md) for complete algorithm design, performance optimizations, and stage-specific requirements.**

### Library Edge Cases

**[REQ-PI-090]** Zero-song passage library handling
- When library contains only passages with zero songs:
  - **[REQ-PI-091]** Automatic passage selection cannot operate (no valid candidates)
  - **[REQ-PI-092]** Users may still manually enqueue passages
  - **[REQ-PI-093]** Queue may become empty when all manually enqueued passages finish
  - **[REQ-PI-094]** System remains in user-selected Play/Pause state when queue is empty
  - **[REQ-PI-095]** No automatic state changes occur

**[REQ-PI-100]** Empty library handling
- When library contains no passages at all:
  - **[REQ-PI-101]** Automatic passage selection cannot operate
  - **[REQ-PI-102]** Manual enqueueing is not possible (no passages to select)
  - **[REQ-PI-103]** Queue is empty
  - **[REQ-PI-104]** System remains in user-selected Play/Pause state
  - **[REQ-PI-105]** UI should indicate library is empty and prompt user to import music (Full version) or load database (Lite/Minimal versions)

## Playback behaviors

### Crossfade Handling

**[REQ-XFD-010]** Passages must crossfade smoothly into each other without gaps or abrupt volume changes.

**[REQ-XFD-020]** Users must be able to configure crossfade timing for each passage individually or use global defaults.

**[REQ-XFD-030]** When resuming from Pause, audio must fade in smoothly to prevent audible "pops" or jarring transitions.

> **See Crossfade Design for complete crossfade timing system, fade curves, timing points, and implementation details.**

### Automatic Passage Selection

**[REQ-SEL-010]** Passages are automatically selected based on:
- **[REQ-SEL-011]** Musical flavor distance from current time-of-day target
- **[REQ-SEL-012]** Cooldown periods preventing too-frequent replay of songs, artists, and works
- **[REQ-SEL-013]** User-configured base probabilities for songs, artists, and works
- **[REQ-SEL-014]** A passage must contain one or more songs to be considered for automatic selection
- **[REQ-SEL-015]** Passages with zero songs can only be manually enqueued by users
- **[REQ-SEL-016]** If library contains only zero-song passages, automatic selection cannot operate (manual enqueueing still works)

**[REQ-SEL-020]** Cooldown System
- **[REQ-SEL-021]** Each song, artist, and work has configurable minimum and ramping cooldown periods
- **[REQ-SEL-022]** During minimum cooldown, probability is zero (passage cannot be selected)
- **[REQ-SEL-023]** During ramping cooldown, probability increases linearly from zero to base probability

**[REQ-SEL-030]** Base Probability Editing
- **[REQ-SEL-031]** Users may adjust base probabilities for individual songs, artists, and works
- **[REQ-SEL-032]** Valid range: 0.0 to 1000.0
- **[REQ-SEL-033]** Default values: All songs, artists, and works start at 1.0

> **See [Program Director](MCR-SPEC005-program_director.md) for complete selection algorithm, cooldown system, probability calculations, default cooldown periods, and multi-entity handling.**
> **See UI Specification - Base Probability Editor for user interface design.**

### User Queue additions

**[REQ-UQ-010]** User may select any passage for enqueueing, including those with no songs contained

### Simple Queue Management

**[REQ-QUE-010]** Add passages to queue (append)

**[REQ-QUE-020]** Automatically advance to next passage on completion

### Queue Empty Behavior
<a name="queue-empty-behavior"></a>

**[REQ-QUE-030]** When the queue becomes empty (no passages waiting to play):
- **[REQ-QUE-031]** Audio playback stops naturally (no audio output)
- **[REQ-QUE-032]** Play/Pause mode does NOT change automatically
- **[REQ-QUE-033]** System remains in whatever Play/Pause state the user last set
- **[REQ-QUE-034]** User maintains full control of Play/Pause mode via API regardless of queue state

**[REQ-QUE-040]** When a passage is enqueued while queue is empty:
- **[REQ-QUE-041]** If system is in Play mode: Begin playing the newly enqueued passage immediately
- **[REQ-QUE-042]** If system is in Pause mode: Passage enters queue but does not play until user selects Play mode

**[REQ-QUE-050]** Automatic selection only enqueues passages containing one or more songs
- **[REQ-QUE-051]** Passages with zero songs cannot be automatically selected
- **[REQ-QUE-052]** If library contains only passages with zero songs, automatic selection cannot operate
- **[REQ-QUE-053]** Users may manually enqueue any passage (including zero-song passages) at any time
- **[REQ-QUE-054]** Manual enqueueing works regardless of whether automatic selection can operate

### Play History

**[REQ-HIST-010]** Record in SQLite for each play:
  - **[REQ-HIST-011]** Passage ID
  - **[REQ-HIST-012]** Timestamp (start time)
  - **[REQ-HIST-013]** Duration played (for skip detection)
  - **[REQ-HIST-014]** Completion status (played fully vs skipped)

#### Album Art and Image Management

**[REQ-ART-010]** System displays album artwork and related images for passages during playback.

**[REQ-ART-020]** Support multiple image types:
- **[REQ-ART-021]** Album covers (front, back, liner notes)
- **[REQ-ART-022]** Song-specific images (special performances, live versions, remixes)
- **[REQ-ART-023]** Passage-specific images (compilation tracks, medleys, custom edits)
- **[REQ-ART-024]** Artist images (photos)
- **[REQ-ART-025]** Work images (sheet music covers, opera/ballet production stills)

**[REQ-ART-030]** Images are stored as files and referenced by database (not stored as binary data in database).

> **See Library Management - Image Management for image storage, sizing, and extraction workflow.**
> **See Database Schema - images for image storage schema.**
> **See UI Specification - Album Artwork Display for display and rotation behavior.**

## Player Functionality

### Manual Controls

**[REQ-CTL-010]** Play: Start playback of current passage

**[REQ-CTL-020]** Pause: Pause playback (maintain position)

**[REQ-CTL-030]** Skip: Move to next passage in queue

**[REQ-CTL-040]** Volume: Set volume level (0.0-1.0 floating-point scale)

**[REQ-CTL-050]** Seek: Jump to specific position in current passage (rewind/fast-forward within 0 to passage duration; seeking to end is equivalent to skip)

**[REQ-CTL-060]** Like: Record a like associated with the passage at the time like was indicated by the user (Full and Lite versions only)
  - **[REQ-CTL-061]** Like is recorded against the user's UUID
  - **[REQ-CTL-062]** Used to build user-specific taste profile

**[REQ-CTL-070]** Dislike: Record a dislike associated with the passage at the time dislike was indicated by the user (Full and Lite versions only)
  - **[REQ-CTL-071]** Dislike is recorded against the user's UUID
  - **[REQ-CTL-072]** Used to refine user-specific taste profile

**[REQ-CTL-080]** Remove: Remove a passage from the queue

**[REQ-CTL-090]** Select: Select a passage to enqueue

**[REQ-CTL-100]** Import: Rescan designated music folders for new / changed music files, add them to the local database (Full version only)

**[REQ-CTL-110]** Output: Select audio sink. Default choice is PulseAudio (or the most common sink for the OS/environment), user may override and select other sinks and either let the OS control or manually specify output device.

### Network Status Indicators

**[REQ-NET-010]** Internet connection status visibility (Full version only)
- **[REQ-NET-011]** Display internet connection status in wkmp-ai import UI
- **[REQ-NET-012]** Show connection states: Connected, Retrying, Failed
- **[REQ-NET-013]** Provide "Retry Connection" button when connection fails
- **[REQ-NET-014]** Indicate which features are unavailable without internet

**[REQ-NET-020]** When user attempts internet-dependent action while disconnected, display clear notification explaining requirement and offer retry option.

**Note:** Lite and Minimal versions do not require internet access and do not display internet status indicators.

> **See UI Specification - Network Status Indicators for complete status display design.**
> **See Architecture - Network Error Handling for retry algorithm and connection handling.**

### Import Progress Display

**[REQ-IPD-010]** Import progress UI provides real-time feedback during audio file import (Full version only)
- **[REQ-IPD-011]** Display elapsed time since import started
- **[REQ-IPD-012]** Display estimated remaining time based on processing rate
- **[REQ-IPD-013]** Display current file being processed
- **[REQ-IPD-014]** Display overall progress (files completed / total files)

**[REQ-IPD-020]** Time display update frequency requirements
- **[REQ-IPD-021]** Elapsed time counter MUST update at least once every 15 seconds
- **[REQ-IPD-022]** Estimated remaining time calculation MUST update at least once every 60 seconds
- **[REQ-IPD-023]** Updates MUST occur regardless of file processing activity (independent of per-file progress events)

**Rationale:** Users expect time displays to update regularly even during long-running operations on individual files. Static time displays create perception that the import is frozen.

> **See Audio Ingest Architecture - Section 5: Time Estimates for implementation design.**

### Playback State

> **Technical Specification**: See Event System - PlaybackState Enum for complete technical definition and event handling details.

**[REQ-PB-010]** System has exactly two playback states:
- **[REQ-PB-011]** **Playing**: Audio plays when passages are available in queue
  - If queue has passages: Plays audio
  - If queue is empty: Silent, but plays immediately when passage enqueued
- **[REQ-PB-012]** **Paused**: User has paused playback
  - Audio paused at current position (if passage is playing)
  - Newly enqueued passages wait until user selects Play

**[REQ-PB-020]** No "stopped" state
- Traditional media player "stopped" state does not exist
- System is always either Playing or Paused

**[REQ-PB-030]** Initial state on app launch
- **[REQ-PB-031]** System always starts either in Playing state, or Paused state as configured in persistent storage.
- **[REQ-PB-032]** In Playing state: if queue has passages, playback begins immediately
- **[REQ-PB-033]** In Playing state: If queue is empty, system waits in Playing state (ready to play when passage enqueued)

**[REQ-PB-040]** State persistence
- **[REQ-PB-041]** Current playback state is NOT persisted across app restarts, initial playing state is unaffected by current playback state.
- **[REQ-PB-042]** Always resumes to initial playing state state on launch
- **[REQ-PB-043]** Queue contents ARE persisted (see State Persistence section)

**[REQ-PB-050]** Initial play state configuration
- **[REQ-PB-051]** Setting `initial_play_state` determines startup playback state
- **[REQ-PB-052]** Valid values: "playing" (default) or "paused"
- **[REQ-PB-053]** User may configure via settings UI (Full and Lite versions)
- **[REQ-PB-054]** Minimal version uses hardcoded default ("playing")
- **[REQ-PB-055]** Current playback state never persisted across restarts

### Authentication and User Identity
<a name="user-authentication"></a>

**[REQ-AUTH-010]** System supports multiple concurrent users with persistent identities

**[REQ-AUTH-020]** Three authentication modes:
- **[REQ-AUTH-021]** **Anonymous Mode**: Users access shared "Anonymous" account (no password)
- **[REQ-AUTH-022]** **Account Creation**: Users create personal account with unique username and password
- **[REQ-AUTH-023]** **Account Login**: Users authenticate with existing username and password

**[REQ-AUTH-030]** Client-side session persistence
- **[REQ-AUTH-031]** Browser stores user UUID in localStorage
- **[REQ-AUTH-032]** Session expires after one year of inactivity
- **[REQ-AUTH-033]** Expiration resets to one year on each successful connection
- **[REQ-AUTH-034]** User automatically recognized on subsequent visits without re-authentication

**[REQ-AUTH-040]** Concurrent sessions allowed
- **[REQ-AUTH-041]** Single user UUID may be authenticated from multiple browsers/devices simultaneously
- **[REQ-AUTH-042]** All sessions for all users receive same real-time event stream

> **See User Identity and Authentication for complete authentication flow, password requirements, hashing algorithm, session management, and account management specifications.**

### Web UI

**[REQ-UI-010]** WebUI provided on HTTP port 5720

**[REQ-UI-020]** Authentication system with three modes (Anonymous, Create Account, Login)

**[REQ-UI-030]** Status Display shows:
- **[REQ-UI-031]** Passage title (when different from song/album title)
- **[REQ-UI-032]** Current song information (title, artist, album)
- **[REQ-UI-033]** Play history (global/system-wide: time since last play, play counts by period)
- **[REQ-UI-034]** Lyrics (when available, Full and Lite versions only)
- **[REQ-UI-035]** Playback state (Playing/Paused)
- **[REQ-UI-036]** Progress bar (current position / total duration)
- **[REQ-UI-037]** Album artwork (2 images when available, with priority and rotation)

**Note:** Play history is global (system-wide). All users see the same play history and cooldowns apply collectively, as the system assumes all listeners hear all songs as they are played.

**[REQ-UI-040]** Control Panel provides:
- **[REQ-UI-041]** Play/Pause toggle button
- **[REQ-UI-042]** Skip button
- **[REQ-UI-043]** Volume slider with percentage display

**[REQ-UI-050]** Next Up Queue displays upcoming passages with title and primary artist

**[REQ-UI-060]** API Endpoints provide:
- **[REQ-UI-061]** REST API for all manual controls and status queries
- **[REQ-UI-062]** Server-Sent Events (SSE) for real-time UI updates across all connected clients

> **See UI Specification for complete web UI design, layout, status display logic, artwork rotation, and responsive behavior.**
> **See API Design for complete endpoint specifications, request/response formats, and error handling.**
> **See Database Schema - song_play_counts for play count data storage.**

### State Persistence

**[REQ-PERS-010]** Save on exit/load on startup:
  - **[REQ-PERS-011]** Last played passage and position
  - **[REQ-PERS-012]** Volume level
  - **[REQ-PERS-013]** Queue contents

**[REQ-PERS-020]** Store in SQLite settings table

### Error Handling

**[REQ-ERR-010]** Log errors to stdout/stderr with timestamps (this is one aspect of the developer interface)
  - **[REQ-ERR-011]** Log messages must include filename and line number for developer troubleshooting

**[REQ-ERR-020]** On playback error: Skip to next passage automatically

**[REQ-ERR-030]** On missing file: Remove from queue, continue

**[REQ-ERR-040]** On database error: Attempt retry once, then log and continue

### Network Error Handling

**[REQ-NET-030]** WKMP requires two distinct types of network access with different error handling:

#### Internet Access (External APIs - Full version only)

**[REQ-NET-040]** Used for MusicBrainz metadata lookup, AcousticBrainz flavor data retrieval, cover art fetching, and lyrics research.

**[REQ-NET-050]** Error Handling:
- **[REQ-NET-051]** When internet access fails, retry with fixed 5-second delay (not exponential backoff)
- **[REQ-NET-052]** Maximum 20 consecutive retry attempts
- **[REQ-NET-053]** After 20 failures, stop until user triggers reconnection
- **[REQ-NET-054]** Reconnection triggers: User clicks internet-requiring UI control or "Retry Connection" button

**[REQ-NET-060]** Playback continues unaffected during internet outages (uses local database and audio files only)

#### Local Network Access (WebUI Server)

**[REQ-NET-070]** Used for serving WebUI, Server-Sent Events (SSE), and REST API endpoints

**[REQ-NET-080]** Error Handling:
- **[REQ-NET-081]** HTTP server binds to localhost:5720 on startup
- **[REQ-NET-082]** If port binding fails: Log error and exit (critical failure)
- **[REQ-NET-083]** Once running, server continues indefinitely

**[REQ-NET-090]** Access types:
- **[REQ-NET-091]** Localhost access: Always available (no network required)
- **[REQ-NET-092]** Remote access: Requires local network connectivity (user responsible for configuration)

**[REQ-NET-100]** Automatic playback works without any network access (no WebUI needed for basic operation)

> **See Architecture - Network Error Handling for complete retry algorithm and connection handling.**
> **See UI Specification - Network Status Indicators for status display and user feedback.**

### Offline Operation

**[REQ-OFF-010]** System operates fully without internet access

**[REQ-OFF-020]** Capabilities without internet:
- **[REQ-OFF-021]** Play all passages in local library
- **[REQ-OFF-022]** Automatic passage selection and enqueueing
- **[REQ-OFF-023]** Crossfade between passages
- **[REQ-OFF-024]** WebUI access via localhost
- **[REQ-OFF-025]** Like/Dislike functionality (Full and Lite versions)
- **[REQ-OFF-026]** Queue management
- **[REQ-OFF-027]** All playback controls

**[REQ-OFF-030]** Limitations without internet:
- **[REQ-OFF-031]** Cannot import new music files (Full version)
- **[REQ-OFF-032]** Cannot fetch new MusicBrainz metadata (Full version)
- **[REQ-OFF-033]** Cannot retrieve AcousticBrainz flavor data (Full version)
  - Falls back to local Essentia analysis (Full version only)
- **[REQ-OFF-034]** Cannot download cover art (Full version)

**[REQ-OFF-040]** Internet reconnection:
- **[REQ-OFF-041]** System continues using cached data during outages
- **[REQ-OFF-042]** User can trigger reconnection via UI controls
- **[REQ-OFF-043]** Automatic retry (20 attempts) when user requests internet-dependent features

----
End of document - WKMP Requirements
