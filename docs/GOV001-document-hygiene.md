# GOV001: Document Hygiene & Governance Standard

**Governance Specification — Tier 0**

This document establishes the official document hygiene standards, naming conventions, unique identifier taxonomies, and modularity principles for the **Lempi** project repository.

---

## 1. Document Modularity & Context Window Efficiency Rules

To ensure that both human contributors and AI coding assistants can quickly inspect specific specifications without consuming excessive context window capacity or wading through unrelated content, all project documentation MUST follow these core principles:

1. **`[GOV-DOC-010]` Focused Single-Purpose Documents**:
   - Every document MUST focus on a single domain or component.
   - Target file length is **100 to 250 lines** per document, and staying under 250 is **strongly encouraged**: it is the length at which a reader, or an agent loading the file into context, can hold the whole of it at once.
   - **The hard limit is 300 lines.** Above 300 a document MUST be split into sub-documents within appropriate folders (e.g., `docs/spec/`). Between 250 and 300 it is over the target and not in breach — a note, not a defect.
   - *Revised 2026-08-20, from a flat 250.* The 250-line target was doing two jobs and one of them badly: it named a good length **and** implied that exceeding it obliged a split. That put a spec's later, measured additions in competition with its earlier reasoning, and made "trim to fit" a reason to cut argument that had been written deliberately. A band separates the encouragement from the obligation, so the choice to keep something is no longer a choice to cut something else.

2. **`[GOV-DOC-030]` Inherited documents are segregated and prefixed**:
   - Material copied from predecessor projects lives only under `docs/inherited/`, carries an `MCR-` (or equivalent) filename prefix, and opens with a banner stating its class.
   - This is required because McRhythm and Lempi both number `SPEC003`–`SPEC006` with different meanings. See [inherited/README.md](inherited/README.md) `[INH-HAZ-010]`.
   - **`[GOV-DOC-040]` Enforced by `tools/check_docs.py`**, which also fails on new identifier collisions, dangling tags and unresolvable links. Run it before committing documentation changes. *(Since 2026-09-02, also enforced in CI — [`.github/workflows/check-docs.yml`](../.github/workflows/check-docs.yml) runs it with `--strict` on every push and pull request to `main`, so a doc/code drift that fails it can't land silently.)*
   - **Searches over `REQ`/`ENT` must be scoped**: `grep -rn "REQ-AUD" docs/ --exclude-dir=inherited`. Inherited material carries 827 distinct tags of its own — counted 2026-09-21 with `check_docs.py`'s own `tags_in(skip_banner=True)`, the same set the checker excludes from Lempi's `[INH-HAZ-020]`. The 651 in `[INH-HAZ-020]`'s own sentence is the 2026-08-30 measurement that found the seven collisions and stays as that measurement, not as a current count.

3. **Unique Grep-Searchable Identifiers**:
   - All requirements, design specs, entity definitions, and test cases MUST be assigned a unique, bracketed identifier tag (e.g., `[REQ-AUD-010]`, `[SPEC-AUD-020]`, `[UT-AUD-001]`).
   - Tags MUST be consistent across specifications, source code comments, and automated test names. *(Enforced since 2026-09-21: `check_docs.py` reads Rust, Python, browser JS, HTML, SQL, shell, units and Dockerfiles, and a tag cited from any of them that resolves to nothing is an error. Before that it read the LempiPi shell helpers alone — two of its seven patterns matched no file at all — and 18 citations across `player/` and `tools/` pointed at tags that had never existed.)*
   - A tag may carry a trailing letter — `[LOG-FEX-070a]` — for a finding split out after its neighbour was numbered. A trailing `x` is not a sub-tag but a wildcard: `[IMPL-BOS-07x]` means the 070-series.

4. **Direct Markdown Hyperlinks**:
   - All references to other documents MUST use standard GitHub Markdown file links with explicit relative paths (e.g., `[SPEC009: Program Director](SPEC009-program-director.md)`).

5. **`[GOV-DOC-020]` Synchronous Specification & Test Maintenance Rule**:
   - Whenever an interactive conversation or prompt results in new code creation, architectural refinement, or settled design decisions, the corresponding formal requirements/specifications (`docs/spec/`) and automated test suites (`tests/`) MUST be updated synchronously within the same conversation turn.

6. **`[GOV-DOC-050]` Current, historical and future are segregated, not intermixed** *(added 2026-09-03, after a pass that found six documents where they weren't)*:
   - A mainline document — `architecture.md`, anything in `docs/spec/`, `REQ002`, and the current-state parts of `GUIDE*`/`IMPL*` — describes **only the current system, present tense.** It is not a diary of how that state was reached.
   - A dated finding, an abandoned approach, or a resolved incident is **historical**: it gets at most a one-line pointer inline ("see `LOG00N`"), and the narrative itself lives in a `[LOG-*]`-tagged document, or in [GUIDE001](GUIDE001-lineage-and-lessons.md) for material from the rearchitecture era, which already serves as that history's home. It is never narrated at length inside a current-state document.
   - Design that is not yet built lives in that document's own small, clearly-labeled "Open" section when the work is local to that document's subject — most `docs/spec/*` files already do this well — or in [ROADMAP.md](ROADMAP.md) when it spans documents or has no single home. Either way it must read as open, not as settled fact.
   - **Exempt:** a single-clause `(Built YYYY-MM-DD)` provenance tag is not narrative and stays inline everywhere. The rule targets multi-paragraph digressions, not dating a fact.
   - A historical/future aside under roughly 40 lines gets an in-place bold-lead-in label (`**Historical:**` / `**Future:**`) rather than a new file of its own, to avoid a swarm of tiny stub documents; only a substantial (roughly 100+ line) narrative earns a dedicated new `LOG` doc, and related small asides from the same subsystem are bundled into one such doc rather than one each.

7. **`[GOV-FBN-010]` A retired project name lives in exactly one file**:
   - This project has changed its name, and the names it used before are recorded once, in [GUIDE015](GUIDE015-the-earlier-names.md). Anywhere else they are a mistake --- a copied snippet, a restored file, a citation that should have been rewritten as prose.
   - Enforced by [`tools/check_forbidden_names.py`](../tools/check_forbidden_names.py), which **reads the tokens out of that document at run time rather than holding its own copy**. A checker cannot search for a string without a copy of it, and a copy here would put a retired name in a second place, which is the condition being detected.
   - **`[GOV-FBN-020]` A scan it cannot perform reports BROKEN, not clean.** A missing source document, or one whose token block is empty, exits non-zero rather than reporting a tree with nothing found in it. A guard that silently has no search terms reports success for work it never did.

---

## 2. Identifier Taxonomy Standard

All unique identifiers MUST use one of the following standardized prefixes:

| Category | Tag Format | Description | Example |
| :--- | :--- | :--- | :--- |
| **Requirements** | `[REQ-<DOMAIN>-<NUM>]` | System & functional requirements | `[REQ-AUD-010]`, `[REQ-DB-020]` |
| **Design Specifications** | `[SPEC-<DOMAIN>-<NUM>]` | Component & mathematical specifications | `[SPEC-AUD-040]`, `[SPEC-PD-010]` |
| **Entities & Data Models** | `[ENT-<NAME>-<NUM>]` | Core relational data entities & schemas | `[ENT-TRACK-010]`, `[ENT-PASSAGE-010]` |
| **Unit & Integration Tests**| `[UT-<DOMAIN>-<NUM>]` | Automated test suite assertions | `[UT-AUD-001]`, `[UT-DB-001]` |
| **Governance & Process** | `[GOV-<DOMAIN>-<NUM>]` | Repository rules & policies | `[GOV-DOC-010]` |
| **Development Guidance** | `[GDE-<DOMAIN>-<NUM>]` | Lessons learned, architectural rationale, forbidden patterns | `[GDE-LES-010]`, `[GDE-ARC-020]` |
| **Implementation Guides** | `[IMPL-<DOMAIN>-<NUM>]` | Step-by-step build and deployment procedures | `[IMPL-AUD-020]`, `[IMPL-STOR-030]` |
| **Experiment Records** | `[LOG-<DOMAIN>-<NUM>]` | Dated iteration history: approach, measured result, why it plateaued | `[LOG-I1-020]`, `[LOG-NEXT-010]` |
| **Inherited Material** | `[INH-<DOMAIN>-<NUM>]` | Provenance and classification of documents copied from predecessor projects | `[INH-HAZ-010]` |

### Domain Acronyms
- `AUD` — Audio Engine, Decoders, Slicing, Crossfading
- `DB` — Database, SQLite, Media Scanner
- `MB` — MusicBrainz, AcoustID, Chromaprint Fingerprinting
- `FE` — Feature Extraction, Essentia, LUFS Loudness
- `FD` — Flavor Distance, Song Similarity Metric
- `SC` — Database Schema, Relational Model
- `DIR` — Program Director, Selection Pipeline
- `SA` — Vipunen Architecture, Pipeline Stages
- `VIS` — Visibility, Process Transparency
- `LIB` — Library Building, Ingest (Vipunen)
- `PORT` — Portability, Metadata Transport
- `DF` — Data Flow, Identity Keys, Library Portability
- `PD` — Program Director, Auto-Playlist Selection Math
- `UI` — Web Server, REST API, WebSocket Protocol, Web UI
- `HW` — Embedded Target, RPi Zero 2W, Storage Partitioning
- `APS` — Audio Path Supervisor: output device, sink, speaker lifecycle
- `RLK` — Library relink: binding a transported library to a target's paths, see [SPEC012](spec/SPEC012-library-relink.md); and the identity hash it binds by, see [SPEC045](spec/SPEC045-the-identity-hash.md) — one prefix, two documents, split 2026-09-24 when SPEC012 reached its line limit
- `SUI` — Vipunen Console: the library-builder's own web interface (distinct from `UI`, which is the player's)
- `PL` — Derived-data payload: the one format carried by all three transports
- `MPD` — MPD integration: the Program Director as a guest in someone else's player
- `SRC` — Sources of truth: ranking two answers to one question, see [GOV002](GOV002-sources-of-truth.md)
- `PLAY` — What counts as a play, for every path that writes `listener_play_history`
- `LYR` — Lyrics: where they live, and how far they travel
- `BK` — Switching playback backends without stopping, see [SPEC018](spec/SPEC018-switching-backends.md) for the seam and [SPEC020](spec/SPEC020-the-handoff.md) for the handoff
- `VOC` — Domain vocabulary: what file/passage/recording/release/album/artist/track mean and do not mean, see [SPEC023](spec/SPEC023-domain-vocabulary.md)
- `RIP` — CD ripping via a disc's own table of contents, see [SPEC025](spec/SPEC025-cd-ripping.md) (mechanics), [SPEC026](spec/SPEC026-cd-ripping-passages.md) (hidden-audio and multi-disc passage representation), [SPEC027](spec/SPEC027-cd-ripping-windows-automation.md) (driving a GUI-only Windows tool — open), and [SPEC028](spec/SPEC028-cd-ripping-identification.md) (Disc ID/CD-TEXT/MusicBrainz)
- `PREF` — Editing an artist's or a recording's own rotation/recovery/restraint, see [SPEC029](spec/SPEC029-listener-preference-editing.md) (the panel) and [SPEC030](spec/SPEC030-preference-sync.md) (syncing it between installations)
- `FREQ` — How often a subject has actually played, and who or what chose each play, see [SPEC031](spec/SPEC031-play-frequency.md) — the readout the `PREF` sliders are adjusted against
- `OWE` — What a fix on one installation owes another that shares its shape, see [PI025](../LempiPi/PI025-what-the-local-split-owes-lempi02w.md)
- `PRE` — What an appliance's startup path depends on, verified and reported at every boot rather than assumed, see [PI026](../LempiPi/PI026-startup-preflight.md)
- `OPS` — An appliance measured **in service** rather than at build time: the health baseline, the false alarms, the standing findings. Prefixed per machine (`BOS` for `bose`), because the numbers are only meaningful against one box — see [BOSE004](../BosePi/BOSE004-operating-health.md)

### Development Guidance Domains (`GDE`)
- `BMK` — MuLibPlay benchmark measurements
- `PD` — MuLibPlay selection algorithm (preserved behaviour)
- `MCR` — McRhythm/wkmp findings
- `V1` — Lempi v1 measured failures
- `LES` — Distilled lessons
- `FEX` — Feature extraction strategy (P0 critical path)
- `CHT` / `ARC` / `PHS` / `FBD` / `DIS` / `OPN` — Charter, architecture decisions, phases, forbidden patterns, disposal register, open questions
- `AND` — Phone port strategy: routes onto a phone, and what each costs
- `IOS` — Phone port strategy, iOS-specific: store terms, audio stack, toolchain
- `HST` — The player under a host that is not a Linux appliance: what it assumes, the structural changes that let a phone target be maintained beside the existing ones, and logging with `tracing`, see [GUIDE033](GUIDE033-the-player-without-an-appliance.md)
- `CLD` — Hosted flavor service: what it would be, and whether it pays for itself
- `EXT` — Driving other players with Lempi's selection, and where that stops
- `BAK` — External backends: the measured cost of an MPD/OpenSubsonic adapter
- `MPD` — The Director as an MPD client: leverage, extension via stickers, mapping
- `SPIN` — Sendspin protocol: interfacing Lempi/Vipunen with the Sendspin multi-room ecosystem, see [sendspin/SPIN001-protocol-and-integration-analysis.md](../sendspin/SPIN001-protocol-and-integration-analysis.md)
- `MSA` — Music Assistant: where Lempi/Vipunen might fit in that ecosystem, see [sendspin/SPIN003-music-assistant-ecosystem-fit.md](../sendspin/SPIN003-music-assistant-ecosystem-fit.md)
- `DRIFT` — The Phase 1 clock-rate campaign: per-node readings, their method and what voids them, see [LOG006](LOG006-echo-drift-measurement.md)
- `DEP` — Deploy scripts: what each acts on, what its name should say, and the signposting that makes a wrong assumption visible, see [GUIDE011](GUIDE011-deploy-script-naming.md)
- `SNS` — Sonos direct play: what the abandoned investigation reached, the firmware-side ceiling that stopped it, and the LGPL obligations any revival inherits, see [GUIDE032](GUIDE032-sonos-direct-play-closed.md)
- `ECHO` — Echo playback: two or more instances playing the same passage at the same moment from their own local files, see [GUIDE008](GUIDE008-echo-playback-investigation.md) (the investigation) and [GUIDE009](GUIDE009-echo-playback-plan.md) (the development plan)
- `TL` — `teacherslounge`, the Ubuntu laptop that is the fleet's acoustic instrument and, since 2026-09-21, a Lempi node, see [TeachersLounge/TL001](../TeachersLounge/TL001-migration-and-the-silent-output.md). Per-machine for the same reason `BOS` and `SMT` are: the findings are only meaningful against one box.
- `SMT` — `smartboardpc` ("Smart"), the x86_64 node that is both a build host and an intended playback target, see [SmartPC/SMART001](../SmartPC/SMART001-survey.md). Per-machine like `BOS`, and in its own folder for the same reason — but not an appliance

---

## 3. Master Specification Search Index

Agents and developers can use standard grep commands to instantly locate specifications:

```bash
# Example: Search for all Audio Engine requirements
grep -rn "REQ-AUD" docs/

# Example: Search for Program Director scoring specifications
grep -rn "SPEC-PD" docs/
```

| Tag ID | Component | Location Document |
| :--- | :--- | :--- |
| *(no tag — index only)* | Everything not-yet-built, in one place: links into every document's own "Open" section, plus cross-cutting future material with no single home | [ROADMAP.md](ROADMAP.md) |
| `[GDE-BMK-*]` | MuLibPlay benchmark & selection algorithm | [GUIDE001-lineage-and-lessons.md](GUIDE001-lineage-and-lessons.md) |
| `[GDE-LES-*]` | Distilled lessons from all predecessors | [GUIDE001-lineage-and-lessons.md](GUIDE001-lineage-and-lessons.md#6-the-lessons-distilled) |
| `[GDE-ARC-*]` | Re-architecture decisions | [GUIDE002-rearchitecture-plan.md](GUIDE002-rearchitecture-plan.md#2-architectural-decisions) |
| `[GDE-PHS-*]` | Phased implementation plan | [GUIDE002-rearchitecture-plan.md](GUIDE002-rearchitecture-plan.md#3-phased-plan) |
| `[GDE-FBD-*]` | Forbidden patterns | [GUIDE002-rearchitecture-plan.md](GUIDE002-rearchitecture-plan.md#4-forbidden-patterns) |
| `[GDE-DIS-*]` | Predecessor disposal register | [GUIDE001-lineage-and-lessons.md](GUIDE001-lineage-and-lessons.md#7-disposal-register) |
| `[GDE-FEX-*]` | Feature extraction strategy (P0 critical path) — current strategy only; reverse-engineering and validation history split out per `[GOV-DOC-050]` | [GUIDE003-feature-extraction-strategy.md](GUIDE003-feature-extraction-strategy.md) |
| `[GDE-AND-*]` | Phone ports (Android, iOS): fork vs ground-up, and the licence that decides it | [GUIDE004-phone-port-strategy.md](GUIDE004-phone-port-strategy.md) |
| `[GDE-APP-*]` | The Android app: the decisions its requirements wait on, each with a recommendation | [GUIDE034-android-decisions-before-requirements.md](GUIDE034-android-decisions-before-requirements.md) |
| `[GDE-NDS-*]` | Node discovery on a LAN — a future idea, recorded and not scheduled | [GUIDE035-node-discovery.md](GUIDE035-node-discovery.md) |
| `[GDE-HST-*]` | What the player assumes about its host; groundwork for a phone target; logging with `tracing` | [GUIDE033-the-player-without-an-appliance.md](GUIDE033-the-player-without-an-appliance.md) |
| `[GDE-CLD-*]` | Hosted flavor lookup instead of Vipunen on the device | [GUIDE005-flavor-service.md](GUIDE005-flavor-service.md) |
| `[GDE-EXT-*]` | The Director driving other players; why streaming is closed | [GUIDE006-director-as-a-guest.md](GUIDE006-director-as-a-guest.md) |
| `[GDE-BAK-*]` | Measured cost of an MPD / OpenSubsonic backend, and its containment | [GUIDE007-external-backends-investigation.md](GUIDE007-external-backends-investigation.md) |
| `[GDE-WRK-*]` | Blocking the same song across different recordings: MusicBrainz Works vs artist+title, measured | [GUIDE012-work-based-song-blocking.md](GUIDE012-work-based-song-blocking.md) |
| `[GDE-NAM-*]` | The names this project used before, why both were retired after a trademark screen, and why a cited commit hash resolves in the previous repository rather than this one | [GUIDE015-the-earlier-names.md](GUIDE015-the-earlier-names.md) |
| `[GDE-SNS-*]` | Sonos direct play, closed: what worked, the 30-39 s reconnect ceiling three experiments could not move, and the LAME/LGPL finding | [GUIDE032-sonos-direct-play-closed.md](GUIDE032-sonos-direct-play-closed.md) |
| `[SPEC-MPD-050]` | Extending MPD through its sticker database, without patching it | [SPEC015-mpd-director.md](spec/SPEC015-mpd-director.md#4-extending-mpd-without-patching-mpd) |
| `[SPEC-MPD-060]` | Mapping a Lempi passage to an MPD URI | [SPEC015-mpd-director.md](spec/SPEC015-mpd-director.md#5-the-mapping-which-is-the-hard-part) |
| `[SPEC-MPD-092]` | MPD's measured protocol behaviour, as against its documentation | [SPEC016-mpd-protocol-findings.md](spec/SPEC016-mpd-protocol-findings.md) |
| `[SPEC-PLAY-*]` | When a play is written to history — one rule, every path | [SPEC017-what-counts-as-a-play.md](spec/SPEC017-what-counts-as-a-play.md) |
| `[IMPL-MPD-*]` | Prototyping the MPD Director: the plan, and what it measured | [IMPL004](IMPL004-mpd-prototype.md), [IMPL005](IMPL005-mpd-prototype-results.md) |
| `[SPEC-BK-*]` | Moving a session between Lempi's engine and MPD without stopping | [SPEC018-switching-backends.md](spec/SPEC018-switching-backends.md), [SPEC020-the-handoff.md](spec/SPEC020-the-handoff.md) |
| `[SPEC-LYR-*]` | Lyrics, and what a guest protocol will not carry | [SPEC019-lyrics.md](spec/SPEC019-lyrics.md) |
| `[SPEC-VOC-*]`, `[ENT-FILE-*]`, `[ENT-PASSAGE-*]`, `[ENT-RECORDING-*]`, `[ENT-RELEASE-*]`, `[ENT-ALBUM-*]`, `[ENT-ARTIST-*]`, `[ENT-TRACK-*]` | Domain vocabulary: file/passage/recording/release/album/artist/track, precisely | [SPEC023-domain-vocabulary.md](spec/SPEC023-domain-vocabulary.md) |
| `[IMPL-*]` | Pi Zero 2W appliance setup procedure | [IMPL001-appliance-setup.md](../LempiPi/IMPL001-appliance-setup.md) |
| `[IMPL-DBSPLIT-*]` | Splitting one database into a read-only catalog and a writable listener store: design, plan, review, and what was built | [IMPL002-database-split.md](../LempiPi/IMPL002-database-split.md), [IMPL009-database-split-plan.md](../LempiPi/IMPL009-database-split-plan.md), [IMPL010-database-split-review.md](../LempiPi/IMPL010-database-split-review.md), [IMPL011-database-split-built.md](../LempiPi/IMPL011-database-split-built.md) |
| `[PI3-REPRO-*]` | Rebuilding the appliance from a fresh card and this tree alone, and what that claim rests on | [IMPL012-rebuilding-from-the-repository.md](../LempiPi/IMPL012-rebuilding-from-the-repository.md) |
| *(no tags — procedure)* | Deploying a build of Lempi to the running appliance, by commit or by release | [HOWTO.md](../LempiPi/HOWTO.md) |
| `[REQ-LIB-165]` | Working the questionable-recording review queue | [IMPL002-reviewing-ids.md](IMPL002-reviewing-ids.md) |
| `[SPEC-SUI-*]`, `[REQ-LIB-*]` | Build order for the waveform boundary editor and the Vipunen editing workflows | [IMPL006-vipunen-editing-workflows.md](IMPL006-vipunen-editing-workflows.md) |
| `[SPEC-DF-*]` (sync) | Build order for syncing edits, and a GUI for the bundle | [IMPL007-syncing-edits.md](IMPL007-syncing-edits.md) |
| `[SPEC-DF-*]` (reads) | Build order for targeted remote reads | [IMPL008-targeted-remote-reads.md](IMPL008-targeted-remote-reads.md) |
| `[IMPL-SUI-*]` | Vipunen Console build order and per-stage claims | [IMPL003-vipunen-console-build.md](IMPL003-vipunen-console-build.md) |
| `[IMPL-MPD-*]` | MPD Director prototype: build order, riskiest part first | [IMPL004-mpd-prototype.md](IMPL004-mpd-prototype.md) |
| `[PI3-*]` | Speaker link: what it must survive (PI003) and what the player owes in return (PI022) | [PI003-choosing-a-speaker.md](../LempiPi/PI003-choosing-a-speaker.md), [PI022-the-players-speaker-contract.md](../LempiPi/PI022-the-players-speaker-contract.md) |
| `[PI3-FOUND-*]`, `[PI3-ROCKER-*]`, `[PI3-LED-*]` | What operating the speaker taught. PI004 is the operating record; the stutter investigation is split by genre behind [PI011](../LempiPi/PI011-two-speakers-and-placement.md), which is its front door and says which files to trust | [PI004-speaker-operation.md](../LempiPi/PI004-speaker-operation.md) |
| `[PI5-LIB-*]` | Getting the real library onto the appliance, and its cost | [PI005-appliance-library.md](../LempiPi/PI005-appliance-library.md) |
| `[PI2-RUN-*]`, `[PI3-OPEN-010]`, `[PI5-DEP-*]`, `[PI5-PRIV-*]` | Dated bring-up findings, consolidated out of PI002/PI003/PI005 per `[GOV-DOC-050]` | [PI008-appliance-bringup-history.md](../LempiPi/PI008-appliance-bringup-history.md) |
| `[PI-CHR-*]` | What the player costs on the appliance: CPU, memory, thermals, latency | [PI006-appliance-characterisation.md](../LempiPi/PI006-appliance-characterisation.md) |
| `[PI-OWE-*]` | Defects the local split found that lempi02w already has, itemised so they land as one update | [PI025-what-the-local-split-owes-lempi02w.md](../LempiPi/PI025-what-the-local-split-owes-lempi02w.md) |
| `[PI-PRE-*]` | The tools a boot depends on, reported with versions at every start, and the fallback that means a missing one is not a disarming | [PI026-startup-preflight.md](../LempiPi/PI026-startup-preflight.md) |
| `[LP3-REP-*]`, `[LP3-SET-*]` | `lempiplay3`: the units that existed only on its card, and the script that now records the rest of its setup | [LP3001-the-node-and-its-units.md](../LempiPlay3/LP3001-the-node-and-its-units.md) |
| `[TL-MIG-*]` | Migrating `teacherslounge` off the previous repository: what was copied, and the parity it was measured against | [TL001-migration-and-the-silent-output.md](../TeachersLounge/TL001-migration-and-the-silent-output.md) |
| `[TL-OPS-*]` | Operating `teacherslounge`: the ssh launch that serves HTTP and produces no sound, and why | [TL001-migration-and-the-silent-output.md](../TeachersLounge/TL001-migration-and-the-silent-output.md) |
| `[TL-OPN-*]` | What that migration has not settled | [TL001-migration-and-the-silent-output.md](../TeachersLounge/TL001-migration-and-the-silent-output.md) |
| `[BOS-OPS-*]` | `bose` in service: the health baseline to compare against, the measurements that look alarming and are not, and the standing findings | [BOSE004-operating-health.md](../BosePi/BOSE004-operating-health.md) |
| `[BOS-IMG-*]` | What to add to `bose`'s image and what to refuse, judged on ease of maintaining the ecosystem rather than on `bose` in isolation | [BOSE008-image-update-plan.md](../BosePi/BOSE008-image-update-plan.md) |
| `[BOS-RUN-*]` | Executing the `bose` update: order, rollback per step, and the failures found by trying to break the plan | [BOSE009-image-update-runbook.md](../BosePi/BOSE009-image-update-runbook.md) |
| `[BOS-PWR-*]` | `bose`'s first hard power cut: what survived, and the card-renumbering and stale-clock findings the boot itself produced | [BOSE005-power-loss-test.md](../BosePi/BOSE005-power-loss-test.md) |
| `[LOG-I*-*]` | Extraction iteration history & measured results (Route 3, distillation — not what ships) | [LOG001-extraction-iterations.md](LOG001-extraction-iterations.md) |
| `[LOG-FEX-*]` | Route 2 (Gaia/SVM chain reproduction) reverse-engineering and production validation — what ships | [LOG002-feature-reproduction-investigation.md](LOG002-feature-reproduction-investigation.md), [LOG003-feature-reproduction-verification.md](LOG003-feature-reproduction-verification.md) |
| `[LOG-WFE-*]` | Waveform boundary editor: incident and build history behind SPEC021's usability pass and fade addition | [LOG004-waveform-editor-build-log.md](LOG004-waveform-editor-build-log.md) |
| `[LOG-RIP-*]` | CD ripping: real-hardware findings behind SPEC025 §5a's failure-handling design | [LOG005-cd-ripping-hardware-findings.md](LOG005-cd-ripping-hardware-findings.md) |
| `[LOG-SPK-*]` | The Android device spike: the player on a phone, screen off, measured — what held, what it cost, what it found | [LOG013-the-android-spike.md](LOG013-the-android-spike.md) |
| `[SD-RISK-*]` | What an unplanned power cut costs each Pi node: measured filesystem/card survey, the four failure layers, and bounded per-cut and per-year rates | [LOG010-power-loss-risk-on-the-pi-nodes.md](LOG010-power-loss-risk-on-the-pi-nodes.md) |
| `[INH-*]` | Inherited-document provenance register & hazards | [inherited/README.md](inherited/README.md) |
| `[SPEC-FD-030]` | Total-variation per-characteristic distance | [SPEC005-flavor-distance.md](spec/SPEC005-flavor-distance.md#2-the-metric) |
| `[SPEC-FD-050]` | Measured per-characteristic reliability & scale constants | [SPEC005-flavor-distance.md](spec/SPEC005-flavor-distance.md#3-reliability--measured-not-assumed) |
| `[REQ-*]` (AUD/PD/VIS/LIB/PORT/HW) | Functional requirements -- supersedes REQ001. REQ002 indexes them and holds PD/LIB/PORT/HW; `[REQ-AUD-*]` is REQ003; `[REQ-VIS-*]` is REQ004 (provenance), REQ005 (the listening surface) and REQ006 (words); `[REQ-AND-*]`, Android, is REQ007 | [REQ002-functional-requirements.md](spec/REQ002-functional-requirements.md) |
| `[SPEC-DIR-100]` | Frequency vs character orthogonality | [SPEC009-program-director.md](spec/SPEC009-program-director.md#1-the-governing-idea) |
| `[SPEC-DIR-150]` | Where Like/Dislike Taste enters selection | [SPEC009-program-director.md](spec/SPEC009-program-director.md#4-stage-b--pool-shaping) |
| `[SPEC-SC-030]` | Identity spine: files / recordings / passages DDL | [SPEC008-database-schema.md](spec/SPEC008-database-schema.md#2-identity-spine) |
| `[SPEC-SC-060]` | Flavor storage: long/narrow, per-characteristic provenance | [SPEC008-database-schema.md](spec/SPEC008-database-schema.md#4-flavor) |
| `[SPEC-SA-020]` | Vipunen pipeline stages S1-S7 | [SPEC007-vipunen-architecture.md](spec/SPEC007-vipunen-architecture.md#2-pipeline) |
| `[SPEC-SA-090]` | Per-passage extraction — measured, the mechanism works | [SPEC007-vipunen-architecture.md](spec/SPEC007-vipunen-architecture.md#6-segmentation--amplitude-s2-s6--provisional) |
| `[SPEC-SA-115..128]` | DAO segmentation cascade: grid search, DP assembly, RMS fallback, extra-track merging — built; MusicBrainz edition search and "Stage 6" refinement — open | [SPEC024-dao-segmentation-cascade.md](spec/SPEC024-dao-segmentation-cascade.md) |
| `[SPEC-RIP-010..056]`, `[SPEC-RIP-070..080]` | CD ripping via a disc's own TOC: exact boundaries instead of inferring them — built, single-disc case, person-assisted | [SPEC025-cd-ripping.md](spec/SPEC025-cd-ripping.md) |
| `[SPEC-RIP-060..074]` | CD ripping: Disc ID, CD-TEXT and MusicBrainz identification — built | [SPEC028-cd-ripping-identification.md](spec/SPEC028-cd-ripping-identification.md) |
| `[SPEC-RIP-082..089]` | CD ripping: how Vipunen drives a GUI-only Windows ripper — decided (person-assisted), built | [SPEC027-cd-ripping-windows-automation.md](spec/SPEC027-cd-ripping-windows-automation.md) |
| `[SPEC-RIP-090..106]` | CD ripping: passage representation for hidden/pregap audio and multi-disc sets — designed, not yet built | [SPEC026-cd-ripping-passages.md](spec/SPEC026-cd-ripping-passages.md) |
| `[SPEC-PREF-010..098]` | Editing an artist's or a recording's own rotation/recovery/restraint, and a recording's "special" tags — built | [SPEC029-listener-preference-editing.md](spec/SPEC029-listener-preference-editing.md) |
| `[SPEC-PREF-100..150]` | Syncing listener preferences and specials between installations, last-write-wins — built | [SPEC030-preference-sync.md](spec/SPEC030-preference-sync.md) |
| `[SPEC-FREQ-*]` | How often a subject has played, by window and by who chose each play — built | [SPEC031-play-frequency.md](spec/SPEC031-play-frequency.md) |
| `[SPEC-WIFI-010..050]` | Wi-Fi network switching and access-point mode on the Pi appliance, confirm-or-revert safety, on NetworkManager, plain `http://lempi/` on :80 — built | [SPEC034-wifi-configuration.md](spec/SPEC034-wifi-configuration.md) |
| `[SPEC-DF-030]` | Identity keys: audio_md5 / recording_mbid / file_path | [SPEC006-data-flow-and-portability.md](spec/SPEC006-data-flow-and-portability.md#2-identity--three-keys-three-scopes) |
| `[SPEC-DF-035]` | Local sequence numbers: when a `passage_id` may be used | [SPEC006-data-flow-and-portability.md](spec/SPEC006-data-flow-and-portability.md#2-identity--three-keys-three-scopes) |
| `[SPEC-DF-060]` | Metadata transports: embedded tags, sidecar, db migration | [SPEC006-data-flow-and-portability.md](spec/SPEC006-data-flow-and-portability.md#4-three-transports) |
| ~~`[REQ-AUD-010]`, `[REQ-AUD-020]`, `[REQ-AUD-040]`, `[REQ-DB-020]`, `[REQ-MB-010]`, `[REQ-PD-010]`~~ | v1 requirements, `REQ001` | ⚠️ **Dead entries — `REQ001-system-requirements.md` deleted 2026-08-30**, per `[GDE-DIS-010]`. Superseded, with different tags and numbering, by [REQ002-functional-requirements.md](spec/REQ002-functional-requirements.md): audio playback is `[REQ-AUD-100..164]`, incremental scanning is `[REQ-LIB-*]`, fingerprinting is `[SPEC-SA-060]`, selection scoring is `[SPEC-DIR-*]`. No tag-for-tag mapping exists; read REQ002 fresh rather than hunting for an equivalent number. |
| `[SPEC-APS-060]` | Audio path supervisor: one owner, one snapshot | [SPEC011-audio-path-supervisor.md](spec/SPEC011-audio-path-supervisor.md#3-the-design) |
| `[SPEC-RLK-030]` | Relink: hash the target, match audio_md5, write the path | [SPEC012-library-relink.md](spec/SPEC012-library-relink.md#2-the-mechanism) |
| `[SPEC-RLK-050]` | Relink outcomes: matched / moved / missing / unknown | [SPEC012-library-relink.md](spec/SPEC012-library-relink.md#3-what-it-reports) |
| `[SPEC-RLK-150]` | Deferred: Symphonia takes the hash at the next re-extraction | [SPEC045-the-identity-hash.md](spec/SPEC045-the-identity-hash.md#2-decided-and-deferred) |
| `[SPEC-SUI-020]` | Vipunen's console against the player's browse page | [SPEC013-vipunen-console.md](spec/SPEC013-vipunen-console.md#2-identity-and-boundaries) |
| `[SPEC-SUI-055]` | Folder view: identity and completeness are two axes | [SPEC013-vipunen-console.md](spec/SPEC013-vipunen-console.md#32-folder--what-is-here-and-what-is-known-about-it) |
| `[SPEC-SUI-095]` | Export ships a class A/B/C bundle, not a database | [SPEC013-vipunen-console.md](spec/SPEC013-vipunen-console.md#5-export--new-music-to-a-remote-lempi) |
| `[SPEC-SUI-150]` | Handoff to the player is same-database-only; export is not | [SPEC013-vipunen-console.md](spec/SPEC013-vipunen-console.md#34-handoff--the-players-own-pages-inside-vipunens-workflow) |
| `[SPEC-PL-020]` | Payload shape: two arrays, two binding scopes | [SPEC014-payload-schema.md](spec/SPEC014-payload-schema.md#2-shape--two-arrays-because-there-are-two-scopes) |
| `[SPEC-PL-060]` | Acceptance: required set plus unresolvable conflict | [SPEC014-payload-schema.md](spec/SPEC014-payload-schema.md#4-acceptance) |
| `[SPEC-PL-090]` | Measured payload size, and the SPEC-DF-093 correction | [SPEC014-payload-schema.md](spec/SPEC014-payload-schema.md#5-size-and-a-correction) |
| `[SPEC-APS-100]` | Supervisor migration order | [SPEC011-audio-path-supervisor.md](spec/SPEC011-audio-path-supervisor.md#4-migration-order) |
| ~~`[SPEC-AUD-010]`, `[SPEC-AUD-040]`~~ | Audio Engine Trait Contracts, Ramp Profiles | ⚠️ **Dead entries — `SPEC001-audio-engine.md` deleted 2026-08-30**, per `[GDE-DIS-010]`. The Rust/Python trait-mirroring design is obsolete (Rust from day one, `[GDE-ARC-020]`); the ramp math is superseded by [`player/core/src/fade.rs`](../player/core/src/fade.rs) and the newly-registered [`MCR-SPEC002-crossfade.md`](inherited/mcrhythm/MCR-SPEC002-crossfade.md) (`[INH-*]`) — note the actual curve formulas differ from both old documents; `fade.rs` is the one that runs. |
| ~~`[SPEC-DB-010]`~~ | Relational DDL & Indexes | ⚠️ **Dead entry.** `SPEC002-data-schema-and-ipc.md` deleted 2026-08-30, per `[GDE-DIS-010]`. Superseded by [SPEC008-database-schema.md](spec/SPEC008-database-schema.md). |
| ~~`[SPEC-PD-010]`~~ | Acoustic Transition Flow Scoring | ⚠️ **Dead entry — `SPEC003-program-director-intelligence.md` deleted 2026-08-30**, per `[GDE-DIS-010]`. Superseded, with a different metric entirely (total variation over flavor, not squared Euclidean over four scalars), by [SPEC009-program-director.md](spec/SPEC009-program-director.md) — see `[SPEC-DIR-160]` for flow ordering. |
| ~~`[SPEC-RUST-010]`~~| Python to Rust Module Mapping | ⚠️ **Dead entry.** `SPEC004-rust-migration-guide.md` deleted 2026-08-30, per `[GDE-DIS-010]`. Superseded by `[GDE-ARC-020]`. |
