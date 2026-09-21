# GUIDE003: Feature Extraction Strategy

**Development Guidance — Tier 0 & Tier 1 complete, promoted to production
2026-08-13; Tier 2 (approximation, for a recipient with neither the dumps
nor local compute) is the only tier still open.**

Strategy for replacing AcousticBrainz with local analysis — the work that becomes **Vipunen**, the separately-licensed x86 library builder `[GDE-ARC-010]`. This is the **first implementation target** `[GDE-PHS-000]` and the highest-risk unknown in the project. Derived from [GUIDE001](GUIDE001-lineage-and-lessons.md); phase context in [GUIDE002](GUIDE002-rearchitecture-plan.md).

---

## 1. Why This Comes First

**`[GDE-FEX-010]` Tracks already depending on it — two distinct populations, not one.** These are frequently confused; they overlap but neither contains the other:

| Population | Count | Definition |
| :--- | ---: | :--- |
| **Untrusted** | **729** | In `lempi.db` with no `mulib.db` counterpart — classification rests on nothing trustworthy today `[GDE-V1-010]`. Some *are* in the dump and can be repaired from it. |
| **Unreachable** | **522** | In `lempi.db` but absent from the AcousticBrainz dump `[LOG-FEX-056]`. No reference exists at any price; only local extraction can serve them. |

The union is the work; the *unreachable* set is the part no amount of harvesting can fix.

**`[GDE-FEX-020]` It gates the project's actual purpose.** A segmenter that finds passages it cannot characterize has not solved new-music induction `[GDE-CHT-020]`. Every subsequent phase either depends on flavor data or is less valuable without it — the reason this was tackled first, and, per `[GDE-FEX-060]` below, why Tier 0/1 being done since 2026-08-13 is what actually unblocked everything downstream of it.

**`[GDE-FEX-025]` Dump coverage does not reduce this priority — high coverage would be a *reprieve*, not a solution.** AcousticBrainz stopped accepting submissions in 2022 and its API is now dead `[GDE-MCR-045]`. Every future acquisition is permanently outside the dump. However well `[GDE-OPN-010]` turns out, local extraction is the only path that does not expire, so it is investigated on its merits and to its limit — not sized to whatever gap the dump happens to leave.

**`[GDE-FEX-027]` Distribution makes it mandatory, not merely preferable** `[GDE-CHT-045]`. A recipient importing their own collection cannot be asked to carry a 37 GB corpus, and cannot fetch what they lack because the API is dead `[GDE-MCR-045]`. Their coverage is unknown and plausibly poor. Local extraction is the only import path that works on a machine that has never seen our development data.

The size comparison settles it — though not where the tension actually lies:

| Shippable artifact | Size |
| :--- | ---: |
| Essentia static extractor (per platform) | **5 MB** |
| Distilled models, shared trunk + 18 heads | **2.5 MB** (1.2 MB fp16) |
| Distilled models, 18 dedicated | **44 MB** |
| *AcousticBrainz highlevel dump* | *37,000 MB* |

Worth stating plainly: **distribution size is not an argument against the dedicated per-characteristic models** that iterations 4–5 favour. At 44 MB they ship comfortably; it is the *dump* that is impossible, at ~840× the dedicated models and ~30,000× the shared one. The accuracy-vs-size question between model families stays open on its own merits `[LOG-NEXT-050]`.

**`[GDE-FEX-028]` And if local extraction merely matches the dump, local extraction wins outright** `[SPEC-FD-130]`. Similarity is a relative judgment within our library, so uniform scoring beats per-track fidelity: a library scored entirely by one model on our own files has zero encoding variance and common-mode model error, while a mixed library pays both an encoding and a model difference on every cross-provenance comparison. The dump is *itself* non-uniform — its ~0.210 err/β self-inconsistency `[GDE-FEX-085]` is exactly that.

**Measured 2026-08-09, and the prediction was half wrong** `[SPEC-FD-140]`. Mixing provenance costs ~8 points of top-1 retrieval — confirmed decisively, and it is the part that governs the design. But all-local did **not** beat all-dump (76.7% vs 77.9%); the student's approximation error slightly outweighs the encoding variance it avoids. The conclusion survives on the mixing penalty alone `[SPEC-FD-145]`: uniform-local is the only regime reachable in the general case, and it costs ~1 point against an all-dump library that cannot be built anyway.

**`[GDE-FEX-030]` It is the one problem no predecessor solved.** McRhythm specified local Essentia analysis and never landed it `[GDE-MCR-040]`. Lempi v1 claimed to have landed it and had not `[GDE-V1-010]`. Two projects have now been wrong about this in opposite directions, which is reason enough to attack it first and honestly.

---

## 2. The Target

**`[GDE-FEX-040]` Reproduce the full AcousticBrainz highlevel vector — 18 classifiers, 71 dimensions** `[GDE-MCR-060]`, not MuLibPlay's 11 scalars.

| Kind | Classifiers | Dims |
| :--- | :--- | ---: |
| **Binary** (2 dims, sum 1.0) | `danceability`, `gender`, `mood_acoustic`, `mood_aggressive`, `mood_electronic`, `mood_happy`, `mood_party`, `mood_relaxed`, `mood_sad`, `timbre`, `tonal_atonal`, `voice_instrumental` | 24 |
| **Complex** (3+ dims, sum 1.0) | `genre_dortmund` (9), `genre_tzanetakis` (10), `genre_rosamerica` (8), `genre_electronic` (5), `ismir04_rhythm` (10), `moods_mirex` (5) | 47 |

The 11 dimensions MuLibPlay stores map to one side of eleven binary classifiers (`abBright` ← `timbre.bright`, `abFemale` ← `gender.female`, `abInstrumental` ← `voice_instrumental.instrumental`, and so on). **All 47 complex dimensions are absent from `mulib.db`** and have no local ground truth `[GDE-FEX-060]`.

---

## 3. Strategy: Harvest, then Reproduce, then Approximate

Three tiers in strict priority order. Each is cheaper and more accurate than the one below it, so exhaust each before descending.

### `[GDE-FEX-050]` Tier 0 — Harvest the archived dumps — complete

The full 30-shard AcousticBrainz highlevel dump plus the paired lowlevel sample (41 GB, 31/31 files) is mirrored and checksum-verified locally against MetaBrainz's published manifest, closing off the risk that the now-dead API `[GDE-MCR-045]` could take the last reference data with it. Library coverage is **93.7%** (8,001 of 8,542 recordings), with misses skewing 2.3× toward post-2013 material — direct support for `[GDE-FEX-027]`: a recipient's library, skewing newer than ours, will fare worse.

The dumps' role is **validation ground truth and the source of the `β_c`/`w_c` constants** `[SPEC-FD-050]`, not necessarily production values — see `[SPEC-FD-150]`. Full mirroring and coverage-measurement narrative, including the 77-submissions-per-recording finding and its consequences for the reproducibility floor, is in [LOG002 § Harvest](LOG002-feature-reproduction-investigation.md#harvest-mirroring-and-coverage-2026-08-08--2026-08-09).

### `[GDE-FEX-060]` Tier 1 — Reproduce AcousticBrainz's pipeline, don't approximate it — complete

**This is the central strategic correction over Lempi v1.** v1 tried to *approximate* AcousticBrainz using MusicNN embeddings and classification heads `[GDE-V1-020]` — different models, trained differently, producing outputs that are at best correlated with the target. AcousticBrainz's own pipeline is public:

```
audio ──[Essentia streaming_extractor_music]──> lowlevel features ──[SVM classifier models]──> highlevel vector
```

Running that same pipeline reproduces AcousticBrainz **by construction** rather than by resemblance. Correctness becomes a matter of matching a known reference, not of training something new.

**Both stages are done.** Stage A (audio → lowlevel features) runs AcousticBrainz's own published static extractor binary directly, unmodified — no reimplementation, no build, ~27 s/track `[SPEC-SA-025]`, `[SPEC-SA-030]`. Stage B (lowlevel → highlevel) reimplements Gaia's `.history` transform chain against AcousticBrainz's own published beta1 SVM models: all 18 classifiers reproduce AcousticBrainz's published output **to within measurement noise** — maximum error 0.0072 across 360+ comparisons, three classifiers exact `[SPEC-SA-040]`. This is the sole production classification path: `tools/extract_library.py` runs it uniformly across all 71 dimensions, and the full library (8,073 of 8,079 passages, 99.93%) has been extracted and classified this way, promoted to production 2026-08-13 `[SPEC-SA-048]`.

The reverse-engineering that got here — the chain's structure, a version mismatch between the extractor and SVM-model vintages, a hidden gaussianize stage, a harness bug that briefly looked like a chain bug — is recorded in [LOG002](LOG002-feature-reproduction-investigation.md). Throughput measurement, per-passage extraction, the failure-rate diagnosis that got the production run to 99.93% coverage, and the promotion itself are in [LOG003](LOG003-feature-reproduction-verification.md).

### `[GDE-FEX-070]` Tier 2 — Approximate, only for what Tier 1 cannot reach

If Tier 1 proves impossible for some classifiers, fall back to modern embeddings (MusicNN, EffNet-Discogs) with heads fitted **against the harvested ground truth** `[LOG-FEX-050]` — which is exactly what v1 lacked. Same iteration protocol `[GDE-FEX-100]`, same provenance and accuracy labelling `[GDE-ARC-030]`.

Anything reaching production this way is tagged distinctly from Tier 0 and Tier 1 values, so its lower confidence is visible everywhere it is used `[GDE-FBD-030]`.

---

## 4. Validation

### `[GDE-FEX-080]` The ground-truth inventory

| Source | Recordings | Dims | Use |
| :--- | ---: | ---: | :--- |
| `mulib.db` `abXxx` columns | 8,062 | 11 | Available **today**, no download needed. Start here. |
| AcousticBrainz highlevel dump | TBD `[GDE-OPN-010]` | 71 | Primary ground truth once mirrored |
| AcousticBrainz lowlevel dump | same | — | Stage B input `[GDE-FEX-090]` |

Discipline: split into train/tune and **held-out** sets before the first experiment, and never report a tuned score as a held-out one.

### `[GDE-FEX-090]` Two-stage validation — the key methodological idea

Do not measure the pipeline end-to-end and stare at one correlation. The paired lowlevel + highlevel dumps allow isolating the two halves:

**Stage A — feature extraction. Now moot, and measured to be so.** Since we run the reference binary itself `[LOG-FEX-062]`, there is no reimplementation to validate. The defects that sank v1 `[GDE-V1-020]` cannot recur, because no preprocessing is being re-derived.

Attempting Stage A anyway proved the point and exposed a confound worth recording: across 12 library tracks with dump coverage, 363 of 436 shared scalar descriptors agreed within 5% (76 bit-identical), with `rhythm.bpm` matching to ~0.1%. The 73 divergent descriptors were dominated by `.min` statistics and higher-order moments — quantities a single differing frame can move sharply. **But the comparison is not controlled: 0 of 12 tracks shared source audio with AcousticBrainz** (`md5_encoded` differed on all, with track lengths differing by up to 1.8 s and bitrates from 32 to 256 kbps). Those numbers therefore measure *encode variance*, not extractor fidelity — the same confound that sets the reproducibility ceiling `[GDE-FEX-085]`. Since AcousticBrainz's original source files are not available, a controlled Stage A is impossible in principle, and unnecessary in practice.

**Stage B — classification. This is now the entire remaining problem** `[LOG-FEX-065]`. *Given AcousticBrainz's own lowlevel features, does my classifier reproduce AcousticBrainz's highlevel output?*

Requires **only the two dumps — no audio at all.** Runs fast, over far more recordings than the library contains, and is *exactly* verifiable: the teacher is deterministic, so a correct implementation should reproduce the dump near-perfectly (r ≈ 0.999). Unlike Stage A, no encode variance intrudes — both sides consume the identical feature vector.

This is the happy consequence of the confound above: all of the irreducible ~0.82 `[GDE-FEX-085]` lives in audio encoding, which we do not control and need not model; everything we *do* control is exactly checkable.

### `[GDE-FEX-085]` The reproducibility ceiling — measured, and it reframes the target

**AcousticBrainz does not agree with itself.** Roughly 9.5% of recordings in the dump carry more than one submission — the same MusicBrainz recording, a different rip or encoding, analyzed by the same pipeline. Comparing submission 0 against submission 1 across **8,461 such recordings** in the sample dump:

| Classifier | r | MAE | | Classifier | r | MAE |
| :--- | ---: | ---: | :-- | :--- | ---: | ---: |
| `mood_sad` | **0.936** | 0.038 | | `mood_relaxed` | 0.835 | 0.069 |
| `mood_acoustic` | 0.891 | 0.054 | | `tonal_atonal` | 0.801 | 0.094 |
| `gender` | 0.862 | 0.064 | | `mood_aggressive` | 0.795 | 0.060 |
| `mood_happy` | 0.832 | 0.071 | | `timbre` | 0.793 | 0.094 |
| | | | | `voice_instrumental` | 0.767 | 0.103 |
| | | | | `mood_party` | 0.760 | 0.079 |
| | | | | `danceability` | **0.749** | 0.107 |

**Mean r ≈ 0.82.** Independently corroborated: comparing `mulib.db`'s stored values against the dump for 924 shared recordings gives r = 0.72–0.87 — the same range, which also confirms the field mapping in `[GDE-FEX-040]` is correct and that the residual is submission variance, not a mapping error.

Three consequences, all of which change how this work should be judged:

1. **r ≈ 0.95 against per-recording AcousticBrainz labels is not attainable** — not through any extractor deficiency, but because the target label itself is only ~0.82 self-consistent. Pursuing it past the ceiling is chasing encoding noise.
2. **r ≈ 0.82 is approximately ceiling, not a shortfall.** An extractor at that level is statistically indistinguishable from being another legitimate AcousticBrainz submission. This is the strongest form of the point in `[GDE-PHS-005]`: the honest stopping condition is set by the data, not by ambition.
3. **The right test is distributional, not pointwise** `[GDE-FEX-095]`. Ask whether the spread of (local extractor − AcousticBrainz) differences matches the spread of (submission 0 − submission 1) differences. If the two distributions coincide, the extractor is as good as AcousticBrainz — which is the actual goal, and is a well-posed question, unlike "how close to 1.0 can r get".

Note the strong implication for `[GDE-FEX-090]`: **Stage B carries none of this variance.** Given identical lowlevel input the classifiers are deterministic, so a correct Stage B reimplementation should reproduce the dump's highlevel output near-exactly (r ≈ 0.999). All of the irreducible ~0.82 lives in Stage A. This makes the two-stage split more valuable, not less — it separates the part that must be perfect from the part that cannot be.

### `[GDE-FEX-095]` Metrics

- **Binary characteristics** — Pearson r per classifier, plus MAE. Directly comparable to the `mulib.db` baseline.
- **Complex characteristics** — per-dimension r is necessary but insufficient on a simplex. Report alongside it top-1 class agreement and a distributional measure (Jensen–Shannon divergence or cosine over the full simplex).
- **What actually matters downstream** is flavor *distance* — specified in [SPEC005](spec/SPEC005-flavor-distance.md), not raw per-dimension agreement. Report rank correlation between distances computed on reproduced vectors versus ground-truth vectors, over sampled recording pairs. A vector can be mediocre per-dimension and still rank neighbours correctly, and ranking neighbours is what the Program Director consumes `[GDE-PD-050]`.
- **Per-characteristic reliability feeds straight back into the metric** as a corpus-wide `w_c` per characteristic `[SPEC-FD-052]`, not per-value scaling by measured agreement — that per-value premise (`[SPEC-FD-120]`) was superseded once the library moved to uniform-local provenance `[SPEC-FD-150]`. Either way, a weak characteristic degrades similarity gracefully rather than poisoning it.

### `[GDE-FEX-100]` Iteration protocol — best effort, no pass/fail gate

Per `[GDE-PHS-005]`, **there is no ship/no-ship threshold.** What is enforced is measurement and honest reporting `[GDE-LES-030]`; the absence of that is what let v1's failure hide for months. The discipline is the process, not a number.

Each iteration records:

1. **Approach** — what was tried and why it was expected to help.
2. **Scores** — per-characteristic, on held-out data, both stages.
3. **Analysis** — strengths and weaknesses of this approach specifically. Which classifiers did well, which lagged, and what the residuals suggest.
4. **Next hypothesis** — or, an explicit statement that ideas are exhausted.

Explicitly:
- **A good score is not a finish line.** Analyze what worked and what lagged, form a next hypothesis, and try again.
- **But calibrate ambition against the measured ceiling** `[GDE-FEX-085]`: AcousticBrainz self-agreement is r ≈ 0.82. Beyond roughly that level, per-dimension r is measuring encoding variance rather than extractor quality — switch to the distributional test `[GDE-FEX-095]` and to downstream distance-ranking, which are the metrics that still carry signal up there.
- **r ≈ 0.82 after genuinely exhausting the available ideas is a legitimate outcome — and per `[GDE-FEX-085]` it is very close to as good as the target data allows.** Record what was tried and why each approach plateaued.
- The iteration history is itself a deliverable — it is what makes the next person's (or the next year's) attempt cheaper.

### `[GDE-FEX-110]` Publish the scorecard

Current per-characteristic accuracy, per provenance tier, visible in the UI `[GDE-CHT-030]`. A user looking at a track should be able to see whether its flavor came from the dump exactly, was reproduced locally at r = 0.91, or was approximated at r = 0.78.

This is the direct structural answer to `[GDE-V1-010]`: with provenance and accuracy attached to every value, "the descriptors are all fine" can never again be true-looking and false.

---

## 5. Risks

| Risk | Assessment | Mitigation |
| :--- | :--- | :--- |
| **`[GDE-FEX-120]`** ~~Dumps disappear as the API did~~ — **RETIRED 2026-08-08** | Was the single most time-sensitive risk in the project | **Mirrored and verified: 31/31 files, 41 GB** `[LOG-FEX-050]`. The archive is local. Remaining exposure is ordinary data-loss risk on our own storage, not the loss of an irreplaceable external resource — so it is now a backup question, not a race. |
| **`[GDE-FEX-130]`** ~~Gaia / Essentia will not build~~ — **RESOLVED 2026-08-08** | Was rated moderate-high; now **near zero**. No build is required: AcousticBrainz's own static extractor binary runs natively here `[LOG-FEX-062]`, and the SVM models are published `[LOG-FEX-065]` | Route 2 (reimplement the `.history` chain) shipped; building Gaia was never needed. Full account: [LOG002](LOG002-feature-reproduction-investigation.md). |
| **`[GDE-FEX-140]`** Dump coverage is poor for newer music (the *unreachable* set `[GDE-FEX-010]`) | Moderate. Coverage was 91.1% on an older-skewing sample `[GDE-MCR-045]`; gaps clustered in post-2012 releases, soundtracks and niche genres — plausibly where new music lives | Measure first `[GDE-OPN-010]`. If coverage is poor, Tier 1 matters more, not less. |
| **`[GDE-FEX-135]`** ~~Import time~~ — **ACCEPTED, not a risk** | 27 s/track measured `[LOG-FEX-062]`. 10,000 tracks ≈ 9 h across 8 cores — an acceptable overnight job, and an unrepresentative worst case: most users start at ~1,000 tracks or fewer | **Incremental import is the normal mode**, not batch: users add tracks as they collect them `[GDE-CHT-045]`. Still cache lowlevel permanently — improving a classifier later must never re-decode anyone's audio. |
| **`[GDE-FEX-137]`** ~~No ARM64 extractor~~ — **RESOLVED by scope** | Published builds are win-i686, linux-i686, linux-x86_64, and an x86_64 macOS build from 2015 | **`vipunen` is declared x86-only.** The player stays portable and reaches ARM `[GDE-ARC-015]`. Not solved — scoped out, deliberately. An ARM64 Essentia build is a later option, never a prerequisite. |
| **`[GDE-FEX-139]`** ~~Licensing~~ — **RESOLVED by separation** | Essentia is AGPL-3.0/commercial dual-licensed | **`vipunen` is AGPL-3.0; `lempi` stays MIT** `[GDE-ARC-018]`. Separate processes, communicating only via the shared SQLite file; nothing AGPL is linked into the player. Conservative by design — subprocess invocation is likely aggregation anyway. Classification remains separable regardless: it reimplements Gaia's published transform chain against AcousticBrainz's own published, CC0 SVM parameters `[SPEC-SA-040]`, `[GDE-CLD-025]` — not Essentia code, and not (as an earlier draft of this row said) a model distilled from AcousticBrainz data. |
| **`[GDE-FEX-150]`** No local ground truth for the 47 complex dimensions until the dumps are mirrored | Certain, by construction `[GDE-FEX-060]` | Tier 0 resolves it. Until then, restrict validation to the 11 binary dimensions `mulib.db` provides, and label results as partial. |
| **`[GDE-FEX-160]`** Music post-dating the 2022 dump has no ground truth, ever | Certain | This is precisely the population Tier 1 exists to serve. Its accuracy can only be inferred from held-out performance on older music — state that limitation rather than implying coverage. |

---

## 6. Defects to Not Repeat

From the v1 post-mortem `[GDE-V1-020]`. Each would be caught by Stage A `[GDE-FEX-090]`:

1. **Resample properly.** `samples[::2]` on 44.1 kHz yields 22,050 Hz — not 16,000 — aliased, and time-scaled. Use a real resampler with an anti-alias filter.
2. **Analyze the whole recording.** A single 3-second patch from the midpoint is a near-random sample of a track's character. AcousticBrainz aggregates across the entire recording.
3. **Match the reference preprocessing exactly.** Compression, normalization and filterbank definition are part of the model contract, not implementation detail.
4. **Do not clamp or round outputs.** `[0.02, 0.98]` clamping and 3-decimal rounding discard exactly the discrimination the distance metric depends on.
5. **Tag provenance on write** `[GDE-FBD-020]`. Had this existed, the fact that 91% of v1's values were inherited rather than computed would have been visible on day one instead of a year later.

---

**See also:** [GUIDE001: Lineage & Lessons](GUIDE001-lineage-and-lessons.md) · [GUIDE002: Re-Architecture Plan](GUIDE002-rearchitecture-plan.md) · [LOG002: Feature Reproduction — Investigation](LOG002-feature-reproduction-investigation.md) · [LOG003: Feature Reproduction — Verification & Production](LOG003-feature-reproduction-verification.md) · McRhythm [SPEC003: Musical Flavor](inherited/mcrhythm/MCR-SPEC003-musical_flavor.md)
