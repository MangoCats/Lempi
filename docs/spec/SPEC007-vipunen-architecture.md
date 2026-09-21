# SPEC007: Vipunen Architecture

**Design Specification — Tier 2 · PROVISIONAL**

Structure of **Vipunen**, the library builder that turns raw audio into everything `lempi.db` needs. Named for the *Kalevala*'s Antero Vipunen, the ancient giant who had swallowed the world's songs and had to be entered to give them up — a separate being the bard depends on but never contains `[GDE-ARC-010]`.

> **Status.** §§1–5 rest on measured results and are stable. **§6 remains provisional on segmentation**, now specified and partly built — [SPEC024](SPEC024-dao-segmentation-cascade.md)'s four cascade stages (grid search, DP assembly, RMS fallback, extra-track merging) replace the threshold-sweep-only tool that shipped before, but the automatic MusicBrainz edition search that would drive the cascade without a human supplying the expected track count is still deferred (SPEC024 §8), which is why this section stays PROVISIONAL rather than settling. The per-passage extraction question that previously gated it has been **measured and resolved** `[SPEC-SA-090]`. **Amplitude (`[SPEC-SA-075]`) is no longer unbuilt** — `tools/analyze_amplitude.py`, 2026-08-30 — but stays under the same PROVISIONAL heading: a real implementation, not yet independently re-verified against Lempi's own library the way `[SPEC-SA-070]`'s own figures still need to be.

> **Related:** [GUIDE002 §2](../GUIDE002-rearchitecture-plan.md#2-architectural-decisions) · [GUIDE003](../GUIDE003-feature-extraction-strategy.md) · [SPEC006](SPEC006-data-flow-and-portability.md) · [SPEC024](SPEC024-dao-segmentation-cascade.md) for the segmentation cascade's own contract · inherited [MCR-SPEC033 Album Matching](../inherited/mcrhythm/MCR-SPEC033-album_matching.md) · [MCR-SPEC025 Amplitude Analysis](../inherited/mcrhythm/MCR-SPEC025-amplitude_analysis.md)

---

## 1. Identity and Boundaries

**`[SPEC-SA-010]`** Vipunen is a **separate project**: own repository, own licence, own platform envelope.

| | Value | Forced by |
| :--- | :--- | :--- |
| Language | Python | ML/fingerprinting ecosystem; McRhythm's 71K-line Rust ingest collapsed `[GDE-MCR-030]` |
| Licence | **AGPL-3.0** | Essentia is AGPL `[GDE-ARC-018]` |
| Platform | **x86 desktop only** | Essentia extractor has no ARM64 build `[GDE-FEX-137]` |
| Runs | On demand, never on the appliance | `[GDE-ARC-010]` |

**`[SPEC-SA-015]`** Vipunen and Lempi communicate **only through the shared SQLite file**. No RPC, no shared process, no linked code. Nothing AGPL enters Lempi.

**`[SPEC-SA-018]` The x86 restriction is Essentia's alone.** Measured 2026-08-09: `fpcalc` ships ARM64 builds for Linux and macOS `[SPEC-SA-060]`. So if the extractor is ever built for ARM64, nothing else in the toolchain blocks it — the constraint is one binary deep, not architectural.

---

## 2. Pipeline

**`[SPEC-SA-020]`** One direction, resumable at every stage:

```
 audio file
    │
    ├─► [S1] scan .......... discover, hash, detect change
    ├─► [S2] segment ....... passage boundaries          ── PROVISIONAL §6
    ├─► [S3] identify ...... fpcalc → AcoustID → MusicBrainz
    ├─► [S4] extract ....... Essentia → lowlevel JSON     ── CACHED FOREVER
    ├─► [S5] classify ...... reimplemented Gaia/SVM chain → 71-dim flavor
    ├─► [S6] amplitude ..... lead_in_ms/lead_out_ms, gain, radio-kind start_ms/end_ms
    └─► [S7] publish ....... lempi.db + optional tags/sidecars
```

**`[SPEC-SA-025]` S4's output is cached permanently and is the pivot of the whole design.** Lowlevel extraction costs ~27 s/track `[LOG-FEX-062]` and is the only stage requiring audio decode. Everything downstream — classification, re-classification with better models, flavor regeneration — consumes cached features. **Improving a classifier must never re-decode a user's library** `[GDE-CHT-045]`. This is why S4 and S5 are separate stages rather than one.

**`[SPEC-SA-028]` Every stage is independently resumable and idempotent.** Import is incremental by default `[GDE-CHT-045]`: a 10,000-track library is an overnight job, but the common case is adding a handful of tracks. Interruption at any point must lose at most the in-flight item.

---

## 3. Feature Extraction (S4) — Settled

**`[SPEC-SA-030]`** Invoke AcousticBrainz's **own** published static binary as a subprocess — `essentia-extractor v2.1_beta2-1-ge3940c0`, verified running natively and producing valid output `[LOG-FEX-062]`. Not a reimplementation: the reference implementation itself, so there is no feature-extraction fidelity to validate `[GDE-FEX-090]`.

Subprocess invocation also keeps the AGPL boundary clean — aggregation rather than linking, though Vipunen takes AGPL regardless `[GDE-ARC-018]`.

**`[SPEC-SA-035]`** The extractor emits `metadata.audio_properties.md5_encoded` for free. This is Lempi's `audio_md5` identity key `[SPEC-DF-030]`, verified stable across tag writes `[SPEC-DF-020]`.

---

## 4. Classification (S5) — Settled

*(Corrected 2026-08-30: this section previously described distilled MLP/gradient-boosted models as production classification. That was the working plan while Route 2 below was still being reverse-engineered; it is not what shipped. `tools/extract_library.py` imports `gaia_classify` only — no distilled model is loaded anywhere in the production path. See `[LOG-FEX-065..108]` for how this was established.)*

**`[SPEC-SA-040]` Classification reproduces AcousticBrainz's own SVM chain, not an approximation of it.** `tools/gaia_classify.py`, built on `tools/gaia_history.py`'s parser for Gaia's serialized `.history` transform chains — the same 18 files AcousticBrainz published alongside its SVM models — runs each chain (`remove → fixlength → enumerate → normalize →` gaussianize, where the chain has one `→ select → cleaner → normalize → svmtrain → select`) against the library's own lowlevel extraction. Verified against AcousticBrainz's own published highlevel values: **maximum error 0.0072 across all 18 classifiers**, three reproducing exactly, the rest consistent with rounding in the published dump rather than any error in the chain `[LOG-FEX-102]`. This is the sole production path for all 71 dimensions — every characteristic is classified the same way, not eleven one way and six another.

**`[SPEC-SA-045]` Two research routes were tried first and are not what ships.** Building Essentia's own Gaia/SVM toolchain (route 1) was rejected before it started — it buys nothing the reimplementation doesn't already have `[LOG-FEX-065]`. Distilling AcousticBrainz's classifiers into small MLP/gradient-boosted models trained on the harvested dumps (route 3, logged in full in [LOG001](../LOG001-extraction-iterations.md)) reached a respectable median err/β 0.182 against the library-native floor and was the reasonable production candidate *until* the Gaia chain reproduced all 18 classifiers to within measurement noise — at which point distillation stopped being a candidate for production: it approximates a function this project can now compute exactly, at comparable per-track cost. `tools/model_store.py` and the `stageb_iter*.py`/`train_*.py` scripts remain in the tree as the record of that research, per `[GDE-LES-030]`'s standing discipline of measuring and reporting every attempt tried — nothing in `extract_library.py` imports them.

**`[SPEC-SA-048]` Model artifacts are AcousticBrainz's own published libsvm text and Gaia parameter tree, not a model trained by this project.** Each classifier's support vectors, coefficients and Platt-scaling parameters come directly from AcousticBrainz's `svm_models` release at the **beta1** vintage — the one matching this project's extractor and lowlevel features `[LOG-FEX-093]`. Inference is RBF/polynomial kernel evaluation plus Wu–Lin–Weng pairwise coupling for the six multi-class characteristics; nothing here is a numpy forward pass over a trained network. Loading all 18 chains costs ~5 s; classifying one track against all 18 costs **586 ms** `[LOG-FEX-103]`, negligible beside the ~27 s/track lowlevel extraction that precedes it.

---

## 5. Identification (S3) — Toolchain Verified

**`[SPEC-SA-050]` The upstream services are alive.** Given that AcousticBrainz died between January and August 2026 `[GDE-MCR-045]`, this was checked rather than assumed (2026-08-09):

| Service | Status |
| :--- | :--- |
| **AcoustID API** | **Alive.** Returns structured JSON errors with validation codes — not the 500s/timeouts AcousticBrainz gives. |
| **MusicBrainz WS/2** | **Alive.** HTTP 200. |
| **Chromaprint** | **Actively maintained** — v1.6.1 released 2026-07-28. |

**`[SPEC-SA-055]` Requires an AcoustID API key.** A registration dependency Lempi does not otherwise have, and a single point of failure for identification. Rate limits apply; results must be cached in `lempi.db` so a re-run never re-queries.

**`[SPEC-SA-060]` Fingerprinting uses the prebuilt `fpcalc` binary as a subprocess** — same pattern as the extractor, no linking, no build. Official builds exist for `windows-x86_64`, `linux-x86_64`, **`linux-arm64`**, `macos-arm64`, `macos-x86_64`, `macos-universal`.

**`[SPEC-SA-065]` Licence, checked not assumed.** Chromaprint's own source is **MIT**; it bundles LGPL-2.1 FFmpeg code, so the project as a whole is distributed as LGPL-2.1 (upstream states an intent to reach MIT-only). LGPL imposes no additional obligation on Vipunen, which is already AGPL. Subprocess invocation avoids linking questions entirely. **Note for redistribution:** upstream also flags the licence of whichever external FFT library a given binary was compiled against — must be confirmed before shipping `fpcalc` in a Vipunen distribution.

---

## 6. Segmentation & Amplitude (S2, S6) — PROVISIONAL

**`[SPEC-SA-070]` Reproduce McRhythm's segmentation cascade.** It is the fix for MuLibPlay's undocumented manual induction `[GDE-BMK-050]`, and measured 93.0% album match / 96.0% boundary accuracy `[GDE-MCR-010]`. Specified in inherited [MCR-SPEC033](../inherited/mcrhythm/MCR-SPEC033-album_matching.md): grid search → DP assembly → RMS quiet-spot → extra merging, with 7-strategy MusicBrainz edition search and Stage-6 RMS boundary refinement.

**Requirements and specification work done; four of six pieces built.** [REQ-LIB-200..215](REQ002-functional-requirements.md) states Lempi's own contract, independent of McRhythm's implementation; [SPEC024](SPEC024-dao-segmentation-cascade.md) specifies it. Grid search, DP assembly, the RMS fallback and extra-track merging are built there, driven by a hand-supplied expected count/durations rather than an automatic edition match. The 7-strategy MusicBrainz edition search and McRhythm's unspecified "Stage 6" boundary refinement remain open (SPEC024 §8) — the first is deferred, genuinely new work; the second has no recoverable algorithm to reproduce. Those figures must still be **independently re-verified**: at present they are simultaneously the target and the only evidence.

**`[SPEC-SA-075]` `start_ms`/`end_ms` (the `radio`-kind's own trim) and `lead_in_ms`/`lead_out_ms` are computed, not hand-placed. Built 2026-08-30** (`tools/analyze_amplitude.py`, [SPEC013 §3.8](SPEC013-vipunen-console.md)). McRhythm automated this and the work is inherited: [MCR-SPEC025](../inherited/mcrhythm/MCR-SPEC025-amplitude_analysis.md) defines lead-in/lead-out detection from an RMS intensity envelope with absolute-dB thresholds and a "quick ramp" shortcut. This supplies the **Radio** side of the Album/Radio duality `[GDE-BMK-030]`, which MuLibPlay only ever produced by hand. A-weighting is skipped, matching the inherited spec's own actual shipped default (`apply_a_weighting = false`) rather than the "A-weighted" description its own overview paragraph still carries; per-medium presets are not built — every passage uses the same hardcoded constants the spec itself marks "not user-configurable" — and the sign of its own dB-to-linear formula was wrong (`10^(dB/20)` computes >1 for a value the same document bounds to `[0,1]`), corrected to `10^(-dB/20)` here.

**`[SPEC-SA-080]` Automatic placement is always reviewable and overridable.** A waveform view with draggable boundaries, lead-in/lead-out markers and gain, for both segmentation and amplitude results. Manual edits outrank computed values permanently `[SPEC-DF-070]` and are never silently recomputed. Automation sets the default; it does not remove the decision. The same view also carries fade-in/fade-out markers and curves `[SPEC-SC-046]` — not automatic placement (every passage starts from the same fixed default, never computed by `analyze_amplitude.py`), but reviewable and overridable the same way.

**`[SPEC-SA-085]` Every decision is recorded, not just logged** — which stage matched, at what confidence, which candidates were rejected `[GDE-CHT-030]`. This is what converts an undocumented ritual into a reviewable process.

**`[SPEC-SA-090]` Per-passage extraction — MEASURED 2026-08-09. The mechanism works.** Extraction runs **per passage**, not per file: a 40-passage DAO file needs 40 extractor runs over 40 slices, which forces S2 before S4 and makes S4 consume sliced temporary audio.

Ten recordings were truncated to centred excerpts and each excerpt's flavor compared against the same recording's full-length flavor, in SPEC005 distance `[SPEC-FD-040]` normalized by the floor:

| slice | flavor distance | vs floor | extraction failures |
| ---: | ---: | ---: | ---: |
| 180 s | 0.246 | 1.17× | 0 |
| 90 s | 0.386 | 1.83× | 0 |
| 45 s | 0.454 | 2.15× | 0 |
| 20 s | 0.550 | 2.61× | 0 |
| **12 s** | 0.548 | 2.60× | **0** |

**Zero extraction failures at any duration, down to 12 s** — the length of MuLibPlay's shortest radio passage. The pipeline shape in §2 stands; ffmpeg slicing to WAV plus a subprocess extraction is viable, and no minimum-duration fallback is needed for the mechanism to function.

**`[SPEC-SA-092]` What the experiment does and does not show.** It varies *truncation*: how much hearing less of a song changes its computed flavor. A DAO passage is **not** a truncation — the passage boundaries capture the whole recording, so a 240-second DAO passage should behave like a 240-second standalone file. The result therefore does *not* say short passages give unreliable flavor.

**It says something more useful: boundary accuracy affects flavor, not just playback.** A boundary clipping a song to ~70% of its length costs ~1.17× floor; to ~35%, ~2.15×. Segmentation error propagates into selection quality, which raises the stakes on `[SPEC-SA-070]` well beyond "the track starts in the wrong place."

**`[SPEC-SA-094]` Still open:** whether *genuinely short recordings* — 12-second interludes, not truncated long songs — yield reliable flavor. That needs short songs as subjects and is a different experiment.

**`[SPEC-SA-110]` Built 2026-08-31: the album/radio duality `[GDE-BMK-030]` on Lempi's own ingest paths, not only the migrated data.** Found live starting from one flagged track with no album cut at all: every passage `[SPEC-SA-070]`/`tools/ingest_folder.py` had ever written on their own was `radio` only — 116 whole-file, 136 DAO-segmented, `inherited:mulib`'s 8,079/8,078 the sole properly-paired case. `tools/ingest_folder.py --kind both` (the new default) writes both per file; `segment_dao.py --commit` (previously a documented stub — "`--commit` is not implemented yet") now does the same per identified track, replacing a file's placeholder passages rather than adding to them; `tools/backfill_album_cuts.py` closes the gap already on disk, mechanically — `analyze_amplitude.py` only ever writes `lead_in_ms`/`lead_out_ms` on `kind='radio'`, never `start_ms`/`end_ms`, so a radio-only passage from either newer path already carries the exact span its album twin needs, no re-detection required. An album cut's `lead_in_ms`/`lead_out_ms`/`gain_db` are `0`/`0`/`0.0` on all three paths, permanently, not `NULL` the way a fresh radio cut's are: its segue points equal its own hard boundaries by definition, and it is never itself the subject of amplitude analysis.

---

## 7. Deliberate Non-Goals

**`[SPEC-SA-100]`** Vipunen does **not** play audio, serve the player UI, run on ARM, run on the appliance, or hold listener state `[SPEC-DF-055]`. It writes facts about music; Lempi owns everything about the listener.

**`[SPEC-SA-105]`** No microservices. McRhythm split ingest across services and reached 71,321 lines with duplicate parallel hierarchies `[GDE-MCR-030]`. Vipunen is one process with staged, resumable steps until a measured constraint says otherwise `[GDE-FBD-050]`.

---

**Traceability:** `[SPEC-SA-010..105]` · derived from `[GDE-ARC-010]`, `[GDE-ARC-015]`, `[GDE-CHT-045]`, `[LOG-FEX-062]`, `[GDE-MCR-010]`
