# GUIDE002: Lempi Re-Architecture Plan

**Development Guidance — Tier 0**

The ground-up re-think, re-specification and re-implementation plan for Lempi, derived from the measured evidence in [GUIDE001: Lineage & Lessons](GUIDE001-lineage-and-lessons.md). Every decision below cites the lesson that forced it.

> **Critical path:** local feature extraction is the first target. Its detailed strategy lives in [GUIDE003: Feature Extraction Strategy](GUIDE003-feature-extraction-strategy.md).

---

## 1. Design Charter

**`[GDE-CHT-010]` Match or beat MuLibPlay on every axis.** It runs at 171 MB RSS / 15% CPU on a Pi 4, serving a 44 GB library with a 4-hour DAO file, and it has not needed attention in six years. That is the bar.

**`[GDE-CHT-020]` Fix new-music induction.** This is the reason for the project. Today it is undocumented external manual labor `[GDE-BMK-050]`. McRhythm proved a 93%-accurate automated segmenter is achievable `[GDE-MCR-010]` — but segmentation is worthless without flavor data for what it finds, which is why extraction comes first `[GDE-PHS-000]`.

**`[GDE-CHT-030]` Make every process visible.** The headline requirement. Three things must be inspectable rather than inferred:
- **Why this passage?** — the full weight decomposition behind each selection.
- **How was this identified?** — the ingest decision record for every passage.
- **Is this data trustworthy?** — provenance and a live accuracy scorecard for every descriptor.

**`[GDE-CHT-040]` Ruthless scope discipline.** McRhythm was killed by surface area, not by technology `[GDE-MCR-030]`. Every new module, service, or abstraction must justify itself against a measured constraint.

**`[GDE-CHT-045]` Lempi must eventually be distributable — self-contained, importing someone else's collection.** Not a personal appliance only. This is a first-class constraint, not a future nicety, because it forecloses whole classes of design:

- **No dependence on a bulk external corpus.** Shipping or referencing the 37 GB AcousticBrainz dump is not viable, and its API is dead `[GDE-MCR-045]`, so a recipient cannot fetch what they lack. Another user's library has unknown — plausibly poor — dump coverage.
- **Therefore local extraction is architecturally mandatory**, not merely preferable `[GDE-FEX-027]`. It is the only import path that works on a machine that has never seen our development data.
- **Import is incremental by default, not a one-time batch.** Users add tracks as they collect them, so import must be routine and repeatable rather than a first-run ordeal: resumable, interruptible, progress-reporting, and cheap to re-enter. A 10,000-track library is an acceptable overnight job at the measured 27 s/track, but it is the unrepresentative worst case — most users start at ~1,000 tracks or fewer `[GDE-FEX-135]`.
- **Never re-decode audio to improve a classifier.** Lowlevel features are cached permanently on first import; a better model later is a re-run of classification only, over cached features.

**`[GDE-CHT-050]` Inherit McRhythm's requirements; reject its architecture.** Its functional specifications are the most refined in the lineage `[GDE-MCR-050]` and become Lempi's primary functional reference. Its 6-service structure does not `[GDE-ARC-010]`.

---

## 2. Architectural Decisions

### `[GDE-ARC-010]` Two binaries, split by runtime constraint

Not six services. Not one monolith. The split follows the one boundary that physically exists — *what must run on a 512 MB appliance, and what never will*:

> **Vipunen** — in the *Kalevala*, the mill forged by Ilmarinen the smith that ground out flour, salt and gold: abundance from raw material. A separate artifact, made by a different hand, that Väinämöinen nonetheless depends on and ultimately sails to reclaim. The naming carries the architecture: Vipunen grinds raw audio into descriptive wealth, and it is its own entity — eventually a separate repository, already a separate licence, separate platform — that Lempi consumes but never contains. *Both still live in this one repository today; splitting it is a deliberately deferred step, not yet taken — see `LICENSING.md`'s own "Status" section.*

| | **`lempi`** (player) | **`vipunen`** (library builder) |
| :--- | :--- | :--- |
| **Runs on** | Pi Zero 2W + desktop, portable | Desktop **x86 only** |
| **Licence** | **MIT** | **AGPL-3.0** |
| **Does** | Playback, crossfade, Program Director, web UI, WebSocket | Scanning, fingerprinting, MusicBrainz, DAO segmentation, feature extraction, review UI |

**`[GDE-ARC-015]` Three independent boundaries fall on the same seam**, which is the strongest available evidence that the seam is real:

1. **Runtime** — what must fit in 512 MB versus what never runs on the appliance.
2. **Platform** — the Essentia extractor is x86-only `[GDE-FEX-137]`; the player must reach ARM.
3. **Licence** — Essentia is AGPL; the player must stay MIT and freely portable `[GDE-FEX-139]`.

They interoperate as a system but remain separate entities: separate processes, communicating only through the shared SQLite file. Nothing AGPL is ever linked into `lempi`.

**`[GDE-ARC-018]` Licence direction matters, so keep the shared code MIT.** The schema/DAO layer both binaries use stays MIT. MIT code may be incorporated into an AGPL work; the reverse is not true. `vipunen` therefore takes on AGPL while `lempi` remains unaffected.

Note this is deliberately conservative: `vipunen` *invokes* the extractor as a subprocess rather than linking it, which is generally aggregation rather than derivation. Relicensing anyway removes the question instead of arguing it. The classification step is separable in any case — it reimplements Gaia's published transform chain against AcousticBrainz's own published SVM parameters `[SPEC-SA-040]`, CC0 data `[GDE-CLD-025]`, not Essentia code `[GDE-FEX-139]`. *(Corrected 2026-08-30: this previously described the classifiers as "distilled... models trained on AcousticBrainz data" — the production path since `[LOG-FEX-102]` is an exact reimplementation of AcousticBrainz's own chain, not a distilled model at all; the separability argument is unaffected either way.)*

Per `[GDE-LES-050]`, no further decomposition without a measured constraint demanding it.

### `[GDE-ARC-020]` Rust for Lempi, Python for Vipunen

**Player → Rust** (`symphonia` + `rubato` + `cpal` + `axum`). Justification is the 512 MB target: it requires the streaming decoder design `[GDE-LES-010]`, and McRhythm's `wkmp-ap` is ~27K lines of working, specified implementation of exactly that design `[GDE-MCR-020]` available to port. Python's whole-file decode is what broke v1 `[GDE-V1-030]`.

**Vipunen → Python.** It never runs on the appliance, the Essentia / fingerprinting / MusicBrainz ecosystem lives there, and McRhythm's 71K-line Rust ingest service is precisely the component that collapsed `[GDE-MCR-030]`. Library building is bursty batch work where iteration speed matters far more than microseconds.

**Honest risk:** Rust is also what stalled McRhythm. The mitigation is scope, not language — one binary instead of six, with a proven design to port rather than invent.

### `[GDE-ARC-030]` Flavor is the full 71-dimension vector, extensible by the user

Adopt McRhythm's Musical Flavor model `[GDE-MCR-060]` rather than MuLibPlay's 11 scalars: 18 classifiers / 71 dimensions, binary and complex characteristics, plus user-defined characteristics computed identically. The user-defined mechanism replaces MuLibPlay's hardcoded `[C]`/`[W]`/`[S]`/`[K]` occasion multipliers `[GDE-PD-020]` with something general.

Preserve both documented asymmetries: **distance over intersecting characteristics**, **taste over the union centroid**.

Storage must accommodate partial vectors — many recordings will have 11 known dimensions (from `mulib.db`), some 71 (from the dumps), some locally computed. Per-characteristic provenance, not per-recording.

### `[GDE-ARC-040]` Restore the relational entity model

Return to what MuLibPlay proved over six years, reconciled with McRhythm's entity definitions `[GDE-MCR-050]`. Term-for-term precision — what "release" vs. "album" vs. "track" mean here — is [SPEC023](spec/SPEC023-domain-vocabulary.md); this diagram names tables, not the vocabulary built on them:

```
files ──< passages >── recordings ──< artists
  │      (Album / Radio)      │
  │                        releases
  └── content signature (relocatable)

play_history · programs · flavor(+provenance) · taste · ingest_decisions
```

Mandatory properties:
- **`files.signature`** — content hash, so moving the library never breaks the database `[GDE-BMK-050]`.
- **Album/Radio passage duality** `[GDE-BMK-030]` — restored. The Program Director selects Radio passages only.
- **Per-characteristic provenance** — `acousticbrainz-dump-20220623` | `computed:<extractor>@<version>` | `manual` | `inherited:mulib`. Non-nullable. This alone would have caught `[GDE-V1-010]` on day one.
- **Do not create** `tempo`, `intensity`, `keyMood`, `darkLight`, `genre`, `themes` `[GDE-BMK-040]`.

### `[GDE-ARC-050]` Bounded audio buffers — a hard rule

Port `wkmp-ap`'s design directly `[GDE-MCR-020]`: per-passage `decoder → resampler → fader → ring buffer`, ~15 s / ~5.3 MB each, mixer into an output ring buffer.

**Enforcement:** `memcheck` (`player/src/bin/memcheck.rs`), run by `build/verify-targets.sh` against the 244.9-minute `GoodbyeYellowBrickRoad.mp3` and failing if RSS exceeds 150 MB or skip latency exceeds 500 ms. A gate, not a guideline, on any machine that runs it — but it is opt-in via `LEMPI_LONG_FILE`, since no build machine has that file by default, and `verify-targets.sh` reports `SKIPPED` rather than passing quietly when the variable is unset `[GOV-SRC-030]`.

### `[GDE-ARC-060]` Predecessors are study material with a disposal date

`lempi.db`, the Lempi v1 Python implementation, the abandoned Go port, and the v1 specifications are **learning artifacts, not foundations**. They are retained only while they still teach something, then deleted outright — not archived, not partially ported, not left to confuse future readers `[GDE-LES-040]`.

Each carries an explicit open question recording what remains to be learned from it `[GDE-DIS-010]`. When that list empties, the artifact goes.

---

## 3. Phased Plan

Each phase is independently useful, independently testable, and reports a measurable result.

### `[GDE-PHS-000]` P0 — Local Feature Extraction ⭐ **FIRST TARGET**

P0 — done, see [GUIDE001 §8](GUIDE001-lineage-and-lessons.md#8-rearchitecture-phases--retrospective). Full strategy and iteration history in [GUIDE003](GUIDE003-feature-extraction-strategy.md).

### `[GDE-PHS-005]` Extraction quality is best-effort and iterative, not pass/fail

There is **no ship/no-ship threshold.** The discipline being enforced is *measurement and honest reporting* `[GDE-LES-030]` — the absence of which is what let v1's failure hide `[GDE-V1-010]`. The discipline is not a number.

- A good score on the first attempt is a reason to analyze what worked and what didn't and **try again** — not a reason to stop.
- **Calibrate against the measured ceiling.** AcousticBrainz agrees with *itself* at only r ≈ 0.82 across submissions of the same recording `[GDE-FEX-085]`. Targets above that measure encoding noise, not extractor quality. Settling at **r ≈ 0.82 after genuinely exhausting the available ideas is not a failure — it is approximately as good as the target data permits.** Record the approaches tried and why each plateaued.
- Every iteration is logged with its approach, its per-characteristic scores, and its analysis of strengths and weaknesses. The iteration history is itself a deliverable.
- Whatever accuracy is reached, the value ships **with its provenance and its measured accuracy attached** `[GDE-ARC-030]`, so downstream consumers and the user can judge it.

### `[GDE-PHS-010]` P1 — Data Foundation

P1 — done, see [GUIDE001 §8](GUIDE001-lineage-and-lessons.md#8-rearchitecture-phases--retrospective).

### `[GDE-PHS-020]` P2 — The Player

P2 — done, see [GUIDE001 §8](GUIDE001-lineage-and-lessons.md#8-rearchitecture-phases--retrospective).

### `[GDE-PHS-030]` P3 — Program Director + Visibility

P3 — done, see [GUIDE001 §8](GUIDE001-lineage-and-lessons.md#8-rearchitecture-phases--retrospective).

### `[GDE-PHS-040]` P4 — Ingest & DAO Segmentation

P4 — open, see [ROADMAP §3](ROADMAP.md#3-rearchitecture--whats-still-ahead).

### `[GDE-PHS-050]` P5 — Appliance

P5 — done, see [GUIDE001 §8](GUIDE001-lineage-and-lessons.md#8-rearchitecture-phases--retrospective).

---

## 4. Forbidden Patterns

Violations are build failures or review rejections, not style opinions. Each is a scar from a specific measured failure.

| Rule | Because |
| :--- | :--- |
| **`[GDE-FBD-010]`** No whole-file decode into memory. Ever. | `[GDE-V1-030]` — 5.2 GB for one file |
| **`[GDE-FBD-020]`** No flavor value without per-characteristic provenance. | `[GDE-V1-010]` — hid a total extraction failure |
| **`[GDE-FBD-030]`** No ML output without a current measured accuracy figure attached. | `[GDE-V1-020]` — four defects, zero validation |
| **`[GDE-FBD-040]`** No two implementations of one component. Delete the loser. | `[GDE-MCR-030]`, `[GDE-V1-050]` |
| **`[GDE-FBD-050]`** No new service or process without a measured constraint requiring it. | `[GDE-MCR-030]` — six services, no benefit |
| **`[GDE-FBD-060]`** No schema field without a consumer at merge time. | `[GDE-BMK-040]` — six years of NULLs |
| **`[GDE-FBD-070]`** No re-deriving data that already exists and is correct. | `[GDE-LES-060]` |
| **`[GDE-FBD-080]`** No dependency on a live external service without a local mirror. | `[GDE-MCR-045]` — API died in seven months |
| **`[GDE-FBD-090]`** Nothing that can block runs on the engine tick — no subprocess, no sleep, no I/O, no lock held across work. Structure it so the tick *cannot*, per `[SPEC-APS-070]`. | Three separate breaches in two days, each presenting as hardware failure |
| **`[GDE-FBD-100]`** No status field derived from an action having been attempted. Report the observed effect, or `unknown`. | `[SPEC-APS-030]` — five places, including a "success" that destroyed a pairing |
| **`[GDE-FBD-110]`** No diagnostic that can cause, mask, or outlive the fault it measures; no measurement window shorter than the known failure interval. | A sink probe that caused dropouts; a 2-minute test for a 2.5-minute fault |

---

## 5. Disposal Register

See [GUIDE001 §7](GUIDE001-lineage-and-lessons.md#7-disposal-register) for the disposal register.

---

## 6. Open Questions

Resolved items are logged in [GUIDE001 §9](GUIDE001-lineage-and-lessons.md#9-resolved-open-questions). Items still genuinely open are tracked in [ROADMAP §3](ROADMAP.md#3-rearchitecture--whats-still-ahead).

---

**See also:** [GUIDE001: Lineage & Lessons](GUIDE001-lineage-and-lessons.md) · [GUIDE003: Feature Extraction Strategy](GUIDE003-feature-extraction-strategy.md) · [GOV001: Document Hygiene](GOV001-document-hygiene.md)
