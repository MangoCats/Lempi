# Inherited Documents

**Provenance Register — Tier 0**

Material brought into the Lempi repository from predecessor projects so that **this repository stands alone**. Nothing here is a Lempi specification. Every file carries a banner stating what it is; this index states what bearing each has on current development.

Imported 2026-08-09. Governance for Lempi's *own* documents is [GOV001](../GOV001-document-hygiene.md), which is native to this repository — the 100–250 line rule and the `[REQ]`/`[SPEC]`/`[ENT]`/`[UT]` taxonomy are Lempi's, not inherited.

---

## 1. Why These Are Here

**`[INH-WHY-010]`** Lempi's guidance documents cited predecessor material through relative paths into sibling working directories (`../../McRhythm/…`). Those links resolve only on a machine that happens to have all three projects checked out side by side. On a fresh clone they are dead, and the reasoning behind Lempi's design becomes unverifiable.

**`[INH-WHY-020]`** Two of the three predecessors are also at risk of drift or loss: McRhythm is stalled, and MuLibPlay is a live deployment whose source could change under maintenance. The material Lempi's design *depends on* should not live only in repositories Lempi does not control.

---

## 2. Classification

| Class | Meaning | Treatment |
| :--- | :--- | :--- |
| **ACTIVE DESIGN INPUT** | Normative input to Lempi/Vipunen design. Not yet adapted into Lempi's own specifications, but expected to be. | Cite freely. Adapt into Lempi `SPEC`/`REQ` documents as those are written; the inherited copy then becomes historical. |
| **HISTORICAL EVIDENCE** | Measurements and analysis cited as evidence for a Lempi decision. Never requirements. | Cite as evidence. Do not treat conclusions as binding on Lempi. |
| **EXTERNAL — NOT IMPORTED** | Lives in the predecessor repository; Lempi depends on the *finding*, not the artifact. | Referenced by description in Lempi docs, not by link. |

---

## 3. Register

### `mcrhythm/` — ACTIVE DESIGN INPUT

The user has identified McRhythm's functional requirements as the most refined in the lineage `[GDE-MCR-050]`: they received sustained revision, unlike Lempi v1's specifications. They are inherited as **requirements input**; McRhythm's *architecture* is explicitly rejected `[GDE-CHT-050]`.

| File | Bearing on Lempi/Vipunen |
| :--- | :--- |
| `MCR-REQ001-requirements.md` | Primary functional reference. Covers ground Lempi's own REQ001 does not: queue-empty behaviour, play history, network status, user identity, offline operation, error handling, library edge cases. |
| `MCR-REQ002-entity_definitions.md` | Passage / Song / Recording / Work model, reconciled into `[GDE-ARC-040]`. |
| `MCR-SPEC002-crossfade.md` | **Normative, and live — the source of the `XFD-*` tags `player/core/src/fade.rs` and `REQ002` cite directly** (`[XFD-ORTH-020]`, `[XFD-CURV-*]`, `[XFD-EXP-*]`, `[XFD-LIN-010]`, `[XFD-COS-010]`, `[XFD-OV-010]`). Added to this register 2026-08-30 — it was omitted despite being the only inherited document current code cites by tag. The fade-in/fade-out *point* model (lead vs. fade orthogonality) is ported as designed; the curve *formulas* are not — Lempi's `Exponential` is linear-in-dB (`10^(-D(1-t)/20)`), not this document's `v(t) = t²` — the same "inherit the concept, re-derive the math" pattern as `[SPEC-DIR-117]`'s artist-ramp fix. |
| `MCR-SPEC003-musical_flavor.md` | **Normative for [SPEC005](../spec/SPEC005-flavor-distance.md) and [GUIDE003](../GUIDE003-feature-extraction-strategy.md).** Defines the 18 classifiers / 71 dimensions, the binary-vs-complex distinction, user-defined characteristics, and the distance/taste asymmetry. |
| `MCR-SPEC004-musical_taste.md` | Taste as union centroid. Feeds `[GDE-OPN-030]`, still open. |
| `MCR-SPEC005-program_director.md` | Selection design. Note the **numbering collision**: this is *not* Lempi's SPEC005. |
| `MCR-SPEC006-like_dislike.md` | Like/Dislike semantics `[GDE-MCR-070]`. Note the collision with Lempi's SPEC006. |
| `MCR-SPEC016-decoder_buffer_design.md` | The audio engine design Lempi ports `[GDE-MCR-020]`, `[GDE-ARC-050]`. |
| `MCR-SPEC022-performance_targets.md` | ≤150 MB RSS target on Pi Zero 2W. |
| `MCR-SPEC033-album_matching.md` | The DAO segmentation cascade `[GDE-MCR-010]` — Vipunen's P4 algorithm. |
| `MCR-IMPL005-audio_file_segmentation.md` | Segmentation workflow, silence-detection defaults by source medium. |
| `MCR-PCH001_project_charter.md` | Charter. Useful for intent behind the requirements. |

### `mcrhythm/` — HISTORICAL EVIDENCE

| File | What it evidences |
| :--- | :--- |
| `MCR-STAGE6_FULL_TEST_RESULTS_20260109.md` | 93.0% album match / 96.0% boundary accuracy over 200 albums → `[GDE-MCR-010]`. **McRhythm's own measurement, not independently reproduced by Lempi.** |
| `MCR-acousticbrainz_coverage_report.md` | 91.1% coverage on 2,664 recordings, dated 2026-01-01 — the proof the AcousticBrainz API was alive then and dead by 2026-08-08 `[GDE-MCR-045]`. |
| `MCR-TECHNICAL_DEBT_ANALYSIS.md` | Duplicate extractor hierarchies, 71K-line ingest service → `[GDE-MCR-030]`, and therefore `[GDE-FBD-040]`, `[GDE-FBD-050]`. |

### `mulibplay/` — ACTIVE DESIGN INPUT

| File | Bearing |
| :--- | :--- |
| `musicdirector.cpp`, `musicdirector.h` | **The selection algorithm that works.** Six years in production. Specified in [GUIDE001 §3](../GUIDE001-lineage-and-lessons.md#3-mulibplays-selection-algorithm--preserve-exactly) as `[GDE-PD-010..050]`; the source is retained because the specification is a reading of it, and a reading can be wrong. Copied unmodified — C++ source, no banner. |

### EXTERNAL — NOT IMPORTED

| Source | Why not |
| :--- | :--- |
| `../MuLibPlay/mulib.db` | 95 MB binary. The *findings* are in GUIDE001 `[GDE-BMK-020]`; the database itself is a data asset for migration `[GDE-PHS-010]`, not documentation. |
| `../MuLibPlay/maintController.cpp` | Cited once for `[GDE-BMK-050]` (no ingest path). The finding is a negative — that nothing is there — and needs no artifact. |
| McRhythm `wkmp-ai/`, `wkmp-ap/` source (~100K lines Rust) | Implementation, not design. Lempi ports the *designs* above. Importing 100K lines of a stalled project would contradict `[GDE-FBD-040]`. |
| McRhythm's remaining ~30 SPEC/IMPL documents | Not currently cited by any Lempi decision. Import on demand rather than wholesale. |

---

## 4. Hazards

**`[INH-HAZ-010]` Document number collisions.** *Mitigation: `tools/check_docs.py` enforces the `MCR-` prefix on every inherited file; `[GOV-DOC-030]` makes it a governance rule.*

 McRhythm and Lempi both number `SPEC003`, `SPEC004`, `SPEC005`, `SPEC006` — with entirely different meanings. Lempi's SPEC005 is Flavor Distance; McRhythm's is Program Director. **The `MCR-` filename prefix is mandatory** for every inherited file, and inherited documents must always be cited with that prefix.

**`[INH-HAZ-020]` Identifier tags collide — measured, not assumed.** An earlier draft of this register asserted that Lempi and McRhythm tags "do not currently collide". That was wrong. Measurement over 651 inherited and 209 Lempi tags found **seven exact collisions**, all between Lempi's own `REQ001` and McRhythm's:

`REQ-QUE-010/020/030/040`, `REQ-UI-010/020/030` — e.g. `[REQ-QUE-010]` is *"Queue Data Structure & Prioritization"* in Lempi and *"Add passages to queue (append)"* in McRhythm.

Three prefixes are shared: `REQ`, `ENT`, `MFL`. (`MFL-DEF-040` appearing in both is *correct* — Lempi specs deliberately cite McRhythm's tag. Citation is not definition.)

**Retired 2026-08-30.** `REQ001` — the v1 artifact these seven collisions belonged to — was deleted per `[GDE-DIS-010]`, its own stated retirement condition. `tools/check_docs.py`'s `KNOWN_COLLISIONS` register is now empty; a `REQ-QUE-*`/`REQ-UI-01*` collision reappearing would be a genuine new error, not a known one.

**Mitigations that remain in force:**
1. `tools/check_docs.py` fails on any *new* collision.
2. `RESERVED_PREFIXES` in the same tool blocks Lempi from *defining* tags under prefixes owned by inherited material (`DBD`, `MFL`, `MTA`, `LD`, `AM`, `AFS`, `XFD`, `SSP`, `PERF`, `ARCH`), while still permitting citation.
3. **Scoped search is now required** for `REQ`/`ENT`: `grep -rn "REQ-AUD" docs/ --exclude-dir=inherited` for Lempi-only. GOV001's search examples are otherwise misleading.

**`[INH-HAZ-030]` These copies are frozen.** They are not synchronised with the source repositories. If McRhythm changes, these do not. That is intentional: they record what Lempi's decisions were actually based on.

**`[INH-HAZ-050]` Cross-references inside inherited documents were adjusted.** Prose is unaltered, but links were rewired: 58 now point at imported siblings (`MCR-`-prefixed), and 111 pointing at McRhythm documents that were *not* imported were reduced to plain text. A reference reading `SPEC008-library_management.md` in an inherited file is a real McRhythm document that simply does not exist here.

**Mitigation:** [`PROVENANCE.json`](PROVENANCE.json) records the sha256 of each **source** file as it stood at import. Because our copies deliberately differ, they cannot be diffed against the originals directly — but the manifest makes drift in the source repositories detectable whenever those repositories are present. Verified 2026-08-09: 16 of 16 sources unchanged since import.

**`[INH-HAZ-040]` Inherited ≠ agreed.** Copying a document here does not adopt its conclusions. McRhythm's architecture is explicitly rejected `[GDE-CHT-050]` while its requirements are inherited — the two travel in the same files.
