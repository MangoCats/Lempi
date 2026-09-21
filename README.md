# Lempi

> **Continuous Radio-Style Music Player Engine**  
> *"Your favourite song, and the one after it, without being asked."*

---

## 📻 Overview

**Lempi** is a continuous, automated music playback system designed to transform a music collection into a personal, 24/7 radio station experience. Rather than requiring users to manually construct playlists or stop between tracks, Lempi dynamically arranges songs and long capture files (such as Disc-At-Once DAO files) into a seamless audio stream with custom crossfades, fade ramps, and trim points. *(Station-ID/jingle-style clip injection was part of an earlier, pre-rearchitecture plan; it is unbuilt and its status is an open question — [`GDE-OPN-060`](docs/GUIDE002-rearchitecture-plan.md#6-open-questions).)*

Lempi is built for both dedicated, low-power embedded appliances (such as a Raspberry Pi Zero 2W connected to high-fidelity audio equipment) and standard desktop computers (Windows, Linux, macOS).

---

## ⚡ Core Philosophy & Inspiration

**Lempi** is poetic-archaic Finnish for *love*, and in compounds `lempi-` is the ordinary Finnish prefix for **favourite** — *lempilaulu*, favourite song; *lempimusiikki*, favourite music. For a player whose whole job is learning which passages this listener loves and putting them in the stream, that is the most exact word the language offers. It is also the root of `lemmikki`, the everyday word for a pet animal, and the Kalevala line runs through it: Lemminkäinen, the reckless singer-hero of Sibelius's four legends, is *son of Lempi*. The library builder it depends on, **Vipunen**, is named from the same tradition.

The name describes the design, and the design has two pillars:

1. **Instant readiness**:  
   Lempi is engineered to play on power-on. On embedded hardware the primary goal is getting the first song playing immediately, with web management services launching asynchronously as network services become available.

2. **An automated stream, not a playlist**:  
   Rather than asking the user to control every track, Lempi reads the context of the moment—listener preferences, play history, time of day, day of week, and day of year—along with high-level audio characteristics, and curates a continuous stream from it.

Lempi is the third attempt at this idea, not the first. **MuLibPlay** — a Qt5 C++ player — has run this listener's library continuously since 2020 and is the benchmark every measured decision here is checked against. **McRhythm** was an ambitious, stalled six-microservice rewrite; its requirements were the most refined in the lineage and are inherited deliberately, while its architecture is explicitly rejected. An earlier **Lempi v1** attempt also failed, on measured grounds (whole-file decode, silently-inherited descriptors). None of this is a clean slate: it is a rearchitecture built on three prior systems' evidence. See [GUIDE001: Project Lineage & Lessons Learned](docs/GUIDE001-lineage-and-lessons.md) for the measurements behind every claim in this paragraph.

---

## 🔑 Key Features & Architecture Pillars

- **Continuous Audio Stream Engine**: Seamlessly transitions across standard single-track audio files and DAO (Disc-at-Once) full album captures, played as trimmed passages with per-passage gain and configurable crossfade ramp profiles.
- **Strict Co-Located Audio Output**: Where the Lempi server runs is strictly where audio streams out. Remote devices act as controllers, not streaming endpoints.
- **Dual Target Platforms**:
  - **Embedded Appliance**: Designed for 24/7 operation on Raspberry Pi Zero 2W (`≤150MB` RSS) with D/A HAT audio or Bluetooth, best-effort fast-boot audio priority, and a power-loss-resilient 3-partition storage strategy.
  - **Desktop Application**: Provides an identical playback and control experience on Windows, Linux, and macOS systems, treated as a first-class target rather than a by-product of the appliance.
- **Skinnable Web Interface**: One shared contract (`core.js`) over a WebSocket snapshot, worn by three interchangeable skins — a quiet reference UI, a MuLibPlay-styled reproduction, and a WinAmp-styled fixed-width appliance skin — each chosen per browser. A fullscreen wall-mounted "kiosk" skin was part of an earlier, pre-rearchitecture plan; it was never built and its status is an open question — [`GDE-OPN-050`](docs/GUIDE002-rearchitecture-plan.md#6-open-questions).
- **Context-Aware Playlist Intelligence**: Uses track metadata (MusicBrainz IDs), a 71-dimension flavor vector reproducing AcousticBrainz's own classifiers from locally-run analysis, play history, and temporal context (time/day/season) to select upcoming songs — see [SPEC009: Program Director](docs/spec/SPEC009-program-director.md).

---

## 📚 Documentation Index

Detailed architectural and design specifications are organized in the [`docs/`](file:///c:/Users/Mango%20Cat/Dev/Lempi/docs) folder. *(Index rebuilt 2026-08-30 — the previous version linked seven files describing a pre-rearchitecture plan that was never built; see `[GDE-DIS-010]` in GUIDE002.)*

**Start here:**
- 🚀 **[HOWTO.md](HOWTO.md)** — build and run Lempi and Vipunen locally, today.
- 📖 **In-app user's guide** — once Lempi is running, click **Help** in any
  skin (or open `/guide`) for a multi-tiered, listener-facing guide: quick
  start, preference tuning, importing/refining music, bringing up an empty
  system, an advanced-features sweep, and an appendix on how the Program
  Director actually decides. Not a file in this repo — it is served by
  Lempi itself, from `player/src/web/guide/`, and is the one document here
  written for a listener rather than a developer.
- 🧭 **[GUIDE001: Project Lineage & Lessons Learned](docs/GUIDE001-lineage-and-lessons.md)** — Measured state of MuLibPlay, McRhythm/wkmp and Lempi v1; the benchmark to beat, the selection algorithm to preserve, and the failures to never repeat.
- 🗺️ **[GUIDE002: Re-Architecture Plan](docs/GUIDE002-rearchitecture-plan.md)** — Design charter, architectural decisions, phased plan, forbidden patterns, and the predecessor disposal register. **This is the current plan** — it supersedes the phased Python-first roadmap this README used to link.

**Governance:**
- 📋 **[GOV001: Document Hygiene & Governance Standard](docs/GOV001-document-hygiene.md)** — identifier taxonomy (`REQ`, `SPEC`, `UT`, `ENT`), modularity rules, and master search index.
- ⚖️ **[GOV002: Sources of Truth](docs/GOV002-sources-of-truth.md)** — how to rank two disagreeing answers to the same question, and the register of rankings already made.
- 📥 **[Inherited Documents Register](docs/inherited/README.md)** — design material copied in from MuLibPlay and McRhythm, classified as active design input vs historical evidence, with collision hazards noted.
- 📜 **[LICENSING.md](LICENSING.md)** — **two licences in this one repository**, not one. See [§ License](#-license) below before assuming the root `LICENSE` file covers everything.

**Design guidance:**
- 🔬 **[GUIDE003: Feature Extraction Strategy](docs/GUIDE003-feature-extraction-strategy.md)** — Replacing AcousticBrainz: harvest the archived dumps, reproduce the pipeline, two-stage validation. **Done** — Tier 0/1 complete and in production since 2026-08-13; only Tier 2 (approximation) remains open.
- 📱 **[GUIDE004: Phone Port Strategy](docs/GUIDE004-phone-port-strategy.md)** · 🌐 **[GUIDE005: Flavor Without Vipunen](docs/GUIDE005-flavor-service.md)** · 🔌 **[GUIDE006: The Director as a Guest](docs/GUIDE006-director-as-a-guest.md)** · 📊 **[GUIDE007: External Backends](docs/GUIDE007-external-backends-investigation.md)**
- 📊 **[LOG001: Feature Extraction Iteration Log](docs/LOG001-extraction-iterations.md)** — dated record of every extraction attempt, its measured result, and why it plateaued.

**Current specifications (`docs/spec/`, ~22 documents):** start from [REQ002: Functional Requirements](docs/spec/REQ002-functional-requirements.md) (what Lempi and Vipunen must do) and [SPEC008: Database Schema](docs/spec/SPEC008-database-schema.md) (the `lempi.db` DDL), then follow their own cross-links — [SPEC009 Program Director](docs/spec/SPEC009-program-director.md), [SPEC007 Vipunen Architecture](docs/spec/SPEC007-vipunen-architecture.md) *(provisional)*, [SPEC006 Data Flow & Portability](docs/spec/SPEC006-data-flow-and-portability.md), [SPEC005 Flavor Distance](docs/spec/SPEC005-flavor-distance.md), and onward through SPEC010–SPEC022 (identification review, the audio path supervisor, library relink, the Vipunen console, the MPD backend, waveform editing). GOV001's master index is the fastest way to jump straight to a tag.

**Architecture and appliance:**
- 🏛️ **[System Architecture & Audio Pipeline](docs/architecture.md)** — describes what is actually built: the two-binary split, the backend seam, the audio path, the data model.
- ⚡ **[Embedded Hardware & Storage Resilience](LempiPi/embedded-hardware.md)** — Raspberry Pi Zero 2W spec, fast-boot sequence, and 3-partition layout.
- 🍓 **[IMPL001: Pi Zero 2W Appliance Setup](LempiPi/IMPL001-appliance-setup.md)** — step-by-step OS build for the appliance.

---

## 🛠️ Technology Stack

Two separate programs sharing one SQLite file, decided in [GUIDE002](docs/GUIDE002-rearchitecture-plan.md) and detailed in [architecture.md](docs/architecture.md) — not a phased Python-then-Rust migration:

- **`player/` — Lempi, the player. Rust from the start** (`symphonia` + `rubato` + `cpal` + `axum`), MIT-licensed, portable to the Pi Zero 2W (`≤150MB` RSS) and to desktop. Nothing AGPL is ever linked into it.
- **`tools/` — Vipunen, the library builder. Python**, AGPL-3.0-or-later (because Essentia is), x86-desktop-only, invoked as a subprocess. Scanning, fingerprinting, MusicBrainz, DAO segmentation, feature extraction, review UI. Never runs on the appliance.

They interoperate only through the shared `lempi.db` file — no linked code, no RPC, no shared process in either direction.

---

## 🚀 Target Platforms & Roadmap

| Platform | Role | Priority |
| :--- | :--- | :--- |
| **Raspberry Pi Zero 2W** | Dedicated 24/7 Radio Station Appliance | **First Priority** |
| **Desktop PC (Win/Linux/macOS)** | Standard Computer Host / Player | **First Priority** |
| **Mobile (Android / iOS)** | Direct Native Mobile Host | *Future / Post-V1 (No early influence)* |

---

## 📜 License

Two licences, one repository: `player/`, `docs/`, `build/`, `sql/` and the root files are **MIT** ([LICENSE](LICENSE)); `tools/` (Vipunen) is **AGPL-3.0-or-later** ([tools/LICENSE](tools/LICENSE)), because it invokes Essentia. See [LICENSING.md](LICENSING.md) for the full arrangement and why the direction only works one way.
