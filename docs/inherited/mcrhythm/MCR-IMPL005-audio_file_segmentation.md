> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/IMPL005-audio_file_segmentation.md` on 2026-08-09. **Not a Lempi specification.**
>
> Segmentation workflow and silence-detection defaults by source medium.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# Audio File Segmentation

**✂️¸ TIER 3 - IMPLEMENTATION SPECIFICATION**

Defines the workflow for segmenting a single audio file into multiple Passages. See Document Hierarchy.

> **Related Documentation:** Library Management | [Requirements](MCR-REQ001-requirements.md) | Database Schema

---

## UI Implementation

**[AFS-UI-010]** The segmentation workflow UI is provided by **wkmp-ai** (not wkmp-ui):
- User accesses via http://localhost:5723 in web browser
- wkmp-ai serves HTML/CSS/JavaScript for all workflow screens
- wkmp-ui does NOT embed or proxy this UI
- Pattern: Standalone web application for specialized import tasks

**[AFS-UI-020]** UI Components:
- Step 1-3: Import wizard pages (source media selection, silence detection, MusicBrainz matching)
- Step 4: Interactive segment editor (waveform display, draggable boundaries)
- Step 5: Progress display and completion summary

**See:** Audio Ingest Architecture - UI Architecture

## 1. Overview

**[AFS-OV-010]** This document specifies the process for segmenting a single, large audio file (e.g., single continuous recording of a full CD, a vinyl album side) into multiple distinct Passages, each corresponding to a single Recording. The workflow is designed to be as automated as possible while providing the user with full control to review and manually adjust the results.

## 2. Segmentation Workflow

The process is a guided, step-by-step workflow within the WKMP UI (Full version only).

### Step 1: Source Media Identification

**[AFS-SRC-010]** The user initiates the workflow by selecting a large audio file for import. The first prompt asks the user to identify the source media type to set appropriate defaults for silence detection. The options are:
- CD
- Vinyl
- Cassette (with Dolby Noise Reduction)
- Cassette (without Dolby Noise Reduction)
- Other

### Step 2: Automatic Silence Detection

**[AFS-SIL-010]** Based on the source media selection, the system uses default parameters to scan the audio file for periods of silence that indicate track boundaries.

**[AFS-SIL-020]** **Default Parameters:**
- **Silence Threshold:**
  - CD: -80dB
  - Vinyl: -60dB
  - Cassette (with Dolby): -70dB
  - Cassette (without Dolby): -50dB
  - Other: -60dB
- **Minimum Silence Duration:** 0.5 seconds

**[AFS-SIL-030]** The user interface shall present these default values and allow the user to edit them before starting the scan. This allows for tuning on a case-by-case basis (e.g., for a particularly noisy vinyl record).

**[AFS-SIL-040]** The system scans the file and creates preliminary Passage boundaries at the midpoint of each detected silent period. The result is a list of initial time-stamped segments.

### Step 3: MusicBrainz Release & Recording Matching

**[AFS-MB-010]** To automatically identify the segments, the system leverages the audio characteristics of the entire file and its derived segments.

1.  **AcoustID Fingerprinting:** The system generates an AcoustID fingerprint for the *entire* audio file using the ChromaPrint algorithm.
2.  **MusicBrainz Picard Integration:** This fingerprint is used to query the MusicBrainz database, similar to the functionality of MusicBrainz Picard, to find matching Releases (albums).
3.  **Candidate List:** The system presents the user with a list of the most likely Release matches, including album title, artist, and track count.

**[AFS-MB-020]** The user selects the most likely Release from the list. If no suitable match is found, the user can opt to proceed with manual segmentation and identification.

**[AFS-MB-030]** Once a Release is selected, the system attempts to align the automatically detected segments with the track list of the selected MusicBrainz Release. It does this by generating fingerprints for each *individual segment* and matching them against the Recordings on the release. This helps correct for errors in the silence detection (e.g., if two tracks have no silence between them).

### Step 4: User Review and Manual Adjustment (wkmp-ai UI)

**[AFS-REV-010]** The user is presented with a review screen that shows:
- The audio waveform for the entire file.
- The proposed Passage boundaries overlaid on the waveform.
- The matched Recording/Song information for each segment from the selected MusicBrainz Release.

**[AFS-REV-020]** From this screen, the user has full manual control to:
- **Adjust Boundaries:** Drag the start and end points of any passage.
- **Add Passages:** Create new passage boundaries for missed tracks.
- **Delete Passages:** Remove incorrectly identified segments.
- **Re-assign Songs:** If a segment was matched to the wrong Recording, the user can choose the correct Recording from the release's tracklist.

### Step 5: Ingestion and Analysis

**[AFS-ING-010]** Once the user indicates they are satisfied with the segmentation and metadata, the system performs the final ingestion:

1.  **Passage Creation:** For each segment, a new Passage is created in the WKMP database, linked to the source audio file and with the correct start/end times.
2.  **Song Association:** The appropriate Song record (including Recording, Artist, and Work) is associated with each new Passage.
3.  **Album Passage:** A single overarching Passage, encompassing the entire audio file, is also created. This allows the user to play the entire album side as a single unit if desired.
4.  **Essentia Analysis:** Each passage's audio is analyzed individually via local Essentia (native binary or Docker container) to compute its Musical Flavor vector. For album files, passage audio is extracted to a temporary WAV file before analysis, ensuring each passage gets a distinct flavor vector.

----
End of document - Audio File Segmentation
