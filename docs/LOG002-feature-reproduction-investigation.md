# LOG002: Feature Reproduction — Investigation

**Development Record — Tier 0**

Reverse-engineering record for **Route 2**: reimplementing AcousticBrainz's own Gaia/SVM classification chain exactly, rather than approximating it. This is the route that succeeded and shipped — `tools/extract_library.py` runs it today via `tools/gaia_classify.py` `[SPEC-SA-040]`. Unlike [LOG001](LOG001-extraction-iterations.md)'s Route 3 (distillation, explicitly not what ships), everything below fed directly into production. It is kept in full per `[GDE-LES-030]`'s discipline of reporting every attempt honestly — the false starts and wrong guesses are as load-bearing as the successes, because they are what the next reverse-engineering effort on this codebase will want to avoid repeating.

Strategy in [GUIDE003](GUIDE003-feature-extraction-strategy.md#3-strategy-harvest-then-reproduce-then-approximate). Settled production account in [SPEC007 §4](spec/SPEC007-vipunen-architecture.md#4-classification-s5--settled). Verification continues in [LOG003](LOG003-feature-reproduction-verification.md).

---

## Harvest: mirroring and coverage (2026-08-08 – 2026-08-09)

**`[LOG-FEX-050]` Approach.** Mirror the archived AcousticBrainz dumps before they become unrecoverable — the live API had worked for McRhythm on 2026-01-01 and returned HTTP 500 or timed out when retested on 2026-08-08 `[GDE-MCR-045]`. Two families targeted: the full 30-shard `acousticbrainz-highlevel-json-20220623` (ground truth and the source of the `β_c`/`w_c` constants `[SPEC-FD-050]`) and its paired `acousticbrainz-lowlevel-json-20220623` sample (Stage B validation input, `[GDE-FEX-090]`).

**Result: complete.** 31 of 31 files downloaded and checksum-verified against MetaBrainz's published manifest — the full highlevel dump plus the paired lowlevel sample, 41 GB, now held locally and no longer at the mercy of `data.metabrainz.org`.

**Revised role — validation, not necessarily production.** An earlier reading treated dump hits as finished flavor data needing no further work. `[SPEC-FD-150]` overturned that: whether the dump also *serves* as production values depends on `[SPEC-FD-160]`. Harvesting was equally urgent either way — a yardstick that disappears cannot be recovered.

**`[LOG-FEX-055]` Coverage measured 2026-08-09 — `[GDE-OPN-010]` answered.** Full harvest across all 31 files, **29,560,615 documents scanned**:

| | |
| :--- | ---: |
| Library recordings found | **8,001 of 8,542 — 93.7%** |
| …with more than one submission | 7,685 |
| Mean submissions per found recording | **77** (max 1,270) |
| Dimension values stored | 43,760,424 |

**`[LOG-FEX-056]` The gaps skew new, which is the population that matters for distribution.** Among `lempi.db`'s 7,912 MBIDs, 522 are absent. Their era profile differs sharply from the found set:

| Era | of found | of missing |
| :--- | ---: | ---: |
| pre-1980 | 7% | 3% |
| 1980–94 | 39% | 15% |
| 1995–2004 | 26% | 30% |
| 2005–12 | 12% | 19% |
| **2013+** | **8%** | **18%** |

Post-2013 material is **2.3× over-represented** among the misses. Our library skews old; **a recipient's library will skew newer, so their coverage will be worse than 93.7%** — direct support for `[GDE-FEX-027]`.

**`[LOG-FEX-057]` Unexpected asset: 77 submissions per recording on average.** This was not anticipated and it cut two ways.

*Favourably* — the constants could be recomputed on the library's own population rather than a generic sample. **Done 2026-08-09** `[LOG-I6-010]`, and the expected benefit did not materialise: averaging submissions tightens the floor by only **1.14×**, so the "77 submissions cancel the noise" reasoning was much weaker than assumed here.

*Against the earlier argument* — `[SPEC-FD-140]` reasoned that all-local might beat all-dump because the dump carries encoding variance. Averaging was expected to remove most of that. It does not (1.14×), so the dump remains substantially noisier than assumed on *both* readings — and measurably noisier on our library than on a generic sample: floor 0.359 versus 0.210 `[LOG-I6-010]`. The consistency and distribution arguments stand unaffected regardless.

**Note the superseded claim.** An earlier draft said "only the misses need Tier 1" and treated coverage `[GDE-OPN-010]` as sizing the whole document. Three later findings each independently overturn that: future acquisitions are permanently outside the dump `[GDE-FEX-025]`, distribution forbids depending on it `[GDE-FEX-027]`, and uniform local scoring may rank similarity better than mixed provenance does `[SPEC-FD-140]`. Coverage bounds how much reference data validation has to work with, but it no longer sizes the work.

---

## Stage A resolved: the extractor needs no build (2026-08-08)

**`[LOG-FEX-062]` Verified 2026-08-08.** AcousticBrainz published **static binaries of the exact extractor it ran**, and they are still served:

```
https://data.metabrainz.org/pub/musicbrainz/acousticbrainz/extractors/
  essentia-extractor-v2.1_beta2-1-ge3940c0-win-i686.zip     (5 MB, sha1 verified)
  essentia-extractor-v2.1_beta2-linux-x86_64.tar.gz
  essentia-extractor-v2.1_beta2-linux-i686.tar.gz
  essentia-extractor-v2.1_beta2-2-gbb40004-osx.tar.gz
```

The Windows build was downloaded, checksum-verified, and **run successfully on library audio** — no Docker, no WSL, no compiler. It self-reports `extractor "music 1.0"`, `essentia 2.1-beta2`, git `v2.1_beta2-1-ge3940c0`: the exact vintage behind the dumps.

Throughput measured at **~27 s per track** single-threaded (12 tracks, 322 s). For the full 8,216-track library that is ~62 core-hours, trivially parallel; for the ~729 tracks that actually needed it, under 6 core-hours.

The SVM classifier models are likewise published and were downloaded: `essentia-extractor-svm_models-v2.1_beta5.tar.gz` (39 MB) — 18 classifiers, each with a Gaia `.history` transformation chain, a `.history.param` (RBF C-SVC, `C=11`, `gamma=-11`, `balanceClasses`), and a `.history.results.html` accuracy report.

**`[LOG-FEX-065]` Consequence: reproducing feature extraction was already achieved.** No SVM-capable binary was ever published — the released extractors emit lowlevel features only, exactly as AcousticBrainz ran them (classification was a separate server-side step). So the whole remaining problem was one deterministic function: **436+ lowlevel scalars → 71 highlevel dimensions.** Three routes were weighed: building Essentia + Gaia with SVM support (worst — the only one requiring the awkward Gaia build); reimplementing the Gaia inference chain directly (tractable, exactly verifiable, reverse-engineers a binary format); or distilling the classifiers from the dumps (Route 3, logged in full in [LOG001](LOG001-extraction-iterations.md)). Route 3 was pursued first and reached only 11 of 18 classifiers (the six complex multi-class ones stalled — see LOG001); this log picks up the direct reimplementation, Route 2.

---

## Route 2 survey: the chain structure (2026-08-10)

**`[LOG-FEX-067]` Route 2 surveyed 2026-08-10 — the chains are legible, and route 2 is the only route that reaches all 18.** All 18 Gaia `.history` files are on disk. `tools/gaia_history.py` reads them: a `QDataStream` with magic `0x6AEA723D` and length-prefixed UTF-16BE strings. Every chain is the same ten steps, varying only at step five:

```
remove → fixlength → remove → enumerate → {select|normalize|remove}
       → select → cleaner → normalize → svmtrain → select
```

**Two corrections to the assumed pipeline.** There is **no PCA stage** in any of the 18 — the pipeline was originally described as `remove → select → normalize → PCA → SVM` and the PCA does not exist. The descriptor names are stored in full (`.lowlevel.silence_rate_20dB.max`, `.lowlevel.spectral_decrease.dmean`, …), matching the extractor's JSON keys exactly, so the `remove`/`select` steps need no guessing at all.

**`[LOG-FEX-068]` The SVM models need no reverse engineering — they are libsvm text.** Gaia stores each model under a `modelData` parameter as **the contents of a libsvm model file**, in the documented text format:

```
svm_type c_svc      kernel_type rbf     gamma 0.000488281
nr_class 2          total_sv 76         rho 2.52051
label 1 0           probA -3.10782      probB 0.304241
nr_sv 36 40
SV
245.3712498333288 1:0.45952198 2:0.51523113 …
```

All 18 extract cleanly, and **their class counts independently confirm they are the right models** — matching `[SPEC-FD-010]`'s table exactly:

| Classifier | classes | SVs | | Classifier | classes | SVs |
| :--- | ---: | ---: | :-- | :--- | ---: | ---: |
| `genre_dortmund` | 9 | 1,457 | | `moods_mirex` | 5 | 240 |
| `genre_tzanetakis` | 10 | 762 | | `genre_electronic` | 5 | 188 |
| `ismir04_rhythm` | 10 | 608 | | 12 binaries | 2 | 68–1,569 |
| `genre_rosamerica` | 8 | 329 | | | | |

Every model carries `probA`/`probB`, so the 0–1 values AcousticBrainz publishes are libsvm's Platt-scaled probability estimates.

**One trap:** `danceability` uses a **polynomial** kernel while the other seventeen use RBF, and its `.param` file says RBF. Trust the model, not the parameter file. *(Measured against the beta5 model set; under the beta1 models actually used in production, `mood_aggressive` and `mood_happy` are polynomial too — see `[LOG-FEX-100]`, which supersedes the "danceability alone" scope of this claim without changing the trap itself.)*

**`[LOG-FEX-069]` The parameter tree is ordinary Qt `QVariant`.** No bespoke format anywhere: `quint32` type, `quint8` isNull, payload, with the standard QMetaType ids — map 8, list 9, string 10, double 6. `read_variant` handles the six types Gaia actually uses and **raises on anything else rather than guessing**, because a wrong guess here yields plausible numbers, which is the worst possible failure mode for a transform chain.

The `normalize` step decodes fully. `coeffs` is a map of descriptor → `{a, b}`, each a list of doubles — length 1 for scalars, 36 for `.tonal.hpcp.*`, 24 for `.tonal.chords_histogram`. Normalisation is **`y = a·x + b`**: `tuning_frequency` carries `a=0.0402, b=-17.35`, mapping 440 Hz to 0.35 — min-max scaling written as scale-and-offset rather than as a range.

All 18 parse, at 372–875 dimensions each.

**A trap caught before it shipped:** six of the eighteen — `genre_dortmund`, `genre_electronic`, `mood_relaxed`, `moods_mirex`, `timbre`, `voice_instrumental` — normalize **twice**, once at step five and again before the SVM. An API returning "the" coefficients hands back the wrong step for a third of the classifiers, and the output still looks like reasonable numbers. `normalize_coeffs` therefore returns a **list** in chain order. *(This entry originally treated the two steps as alternatives to choose between; `[LOG-FEX-094]` below found they compose instead.)*

**`[LOG-FEX-070a]` The record framing is *not* fully understood — and that is deliberate.** An attempt at a sequential reader failed on all 18. The header is `magic, version, count, reserved`, and each record opens `QString name, QString applier`, then **two** `QVariantMap`s — not one. After those comes an empty map and then a region that could be a `QByteArray` of applier state or a mis-framed string.

Choosing between those by inspection is guesswork, and a sequential reader built on a guess would associate parameters with the **wrong steps** while still producing numbers that look entirely reasonable. That is the failure this whole exercise is most exposed to, so the reader was **removed rather than shipped**. Extraction locates parameters by name and occurrence instead, which works for all 18, and the eventual 658-pair verification (`[LOG-FEX-090a]`) is what confirms the association is right. Resolving the framing was never needed.

---

## First verification attempts

**`[LOG-FEX-090a]` The verification set is complete.** All **658** lowlevel JSONs have published highlevel beside them in `data/flavor-sample.db`, across all 18 characteristics. Recordings carry **multiple submissions** (0, 1, 2 …) with different values, and the lowlevel file is one specific submission — so a comparison must establish *which*, rather than assume submission 0. Comparing against every submission and reporting the best match shows it.

**`[LOG-FEX-091]` First end-to-end run: close, and not correct.** `tools/gaia_predict.py` applies the chain and compares against the reference in one tool, deliberately — a subtly wrong chain still emits plausible probabilities, so "it ran" is evidence of nothing. libsvm prediction was implemented: RBF and polynomial kernels, and the Platt sigmoid matching libsvm's own `sigmoid_predict`.

First run on `tonal_atonal` (2 classes, 76 SVs, one normalize step), 8 recordings:

| descriptor order | median abs error | best | worst |
| :--- | ---: | ---: | ---: |
| as-read | **0.0593** | 0.0087 | 0.3171 |
| sorted | 0.1055 | 0.0039 | 0.2614 |
| reversed | 0.1055 | 0.0039 | 0.2614 |

**Not a match** — a correct reimplementation should agree to within floating-point noise — but also clearly not noise, since random guessing against values in [0,1] would sit near 0.35. The chain was substantially right and specifically wrong.

**The concrete lead: the vector is 372 dimensions and the SVM's highest support-vector index is 380.** Eight dimensions were missing, so every distance was computed in the wrong space. The `enumerate` step converts string descriptors to numbers, and the lowlevel files carry exactly four musical strings — `tonal.key_key`, `tonal.key_scale`, `tonal.chords_key`, `tonal.chords_scale` — which accounts for four, not eight. Errors were also generous by construction: the harness took the best over both label orientations and all submissions, so the true error was at least this large.

**`[LOG-FEX-092]` The eight dimensions are found — and they reveal a version mismatch that blocks route 2.** The SVM's input list is **133 descriptors**: the 125 with normalize coefficients (372 dims) plus **8 enumerated string descriptors** (1 dim each) = **380**, matching the highest support-vector index exactly. The eight are:

```
.tonal.chords_key            .tonal.key_edma.key         .tonal.key_krumhansl.key
.tonal.chords_scale          .tonal.key_edma.scale       .tonal.key_krumhansl.scale
                             .tonal.key_temperley.key    .tonal.key_temperley.scale
```

`key_edma`, `key_krumhansl` and `key_temperley` are the **three-profile key estimation** of later Essentia. Neither input available produces them:

| Artefact | Essentia version | Key descriptors emitted |
| :--- | :--- | :--- |
| AcousticBrainz's published highlevel | 2.1-**beta1** | `key_key`, `key_scale` only |
| The downloadable extractor `[LOG-FEX-062]` | 2.1-**beta2** | `key_key`, `key_scale` only |
| The downloadable SVM models | v2.1_**beta5** | **requires all eight** |

Measured: 0 of 40 archived lowlevel files carry `key_edma`, and neither did our own 12 extractions. Six of the 380 input dimensions could not be supplied by any data we could produce.

Two consequences: exact reproduction of AcousticBrainz's published values with the *beta5* models is impossible, since they are a later vintage than the pipeline that produced the dumps; and the beta5 models cannot be run correctly on our own library either, for the same reason. Route 2 was blocked pending a version question — whether AcousticBrainz published an earlier `svm_models` release matching the beta1/beta2 extractor.

**`[LOG-FEX-093]` Unblocked: the beta1 models exist, in a different directory.** `data.metabrainz.org/pub/musicbrainz/acousticbrainz/**svm_models/**` — a sibling of `extractors/`, not inside it — holds a second, **earlier** set of the same 18 classifiers, stamped `accuracies_v2.1_beta1.html`. Different file sizes throughout (`tonal_atonal` 2 MB against the beta5 copy's 0.7 MB), so these are genuinely different models.

Measured on `tonal_atonal`, against one archived lowlevel file:

| | descriptors needed | present in the lowlevel | vector dims | highest SV index |
| :--- | ---: | ---: | ---: | ---: |
| beta5 (on disk) | 125 | **81 — 44 missing** | 372 | 380 |
| **beta1** (fetched) | 157 | **157 — 0 missing** | 666 | 670 |

The beta1 model's enumerated strings are exactly `.tonal.chords_key`, `.tonal.chords_scale`, `.tonal.key_key`, `.tonal.key_scale` — the four that both the archived lowlevel **and our own beta2 extractor** emit. The vintage matches. Route 2 was viable.

**`[LOG-FEX-094]` The two normalize steps compose; they are not alternatives.** `[LOG-FEX-069]` treated the earlier finding of a double normalize as *which one to use*. That was wrong: they apply **in sequence**.

`.lowlevel.spectral_spread.var` reads `2.197e12` raw. Step 0 (`a=3.10e-15, b=-4.29e-4`) takes it to `0.0064`; step 1 (`a=0.2565, b=+0.5`) takes that to `0.5016` — inside the support vectors' observed range of [0, 11]. Applying only step 1 to the raw value gives `5.6e11`, which makes `‖x‖²` about `3e23`, so every RBF kernel value underflows to zero and the classifier returns `-rho` for **every input**.

That failure is worth naming precisely because of how it presented: a constant output identical across all three candidate descriptor orderings — a plausible-looking probability, 0.8366, that never varied.

**With composition applied**, `tonal_atonal` over 12 recordings gave a median absolute error of **0.0956**, best **0.0003**, worst **0.267**. Predictions now varied, and some recordings matched nearly exactly — but a correct reimplementation matches to floating-point noise throughout, and this did not yet.

---

## Closing in: layout, enumeration, gaussianize

**`[LOG-FEX-095]` The layout is solved; the values are still wrong.** The four enumerated dimensions were found without guessing. Scanning the support vectors for indices whose values are **integers greater than 1** — impossible for a normalised feature — returned exactly two: **547** and **664**, each carrying 0–11, i.e. twelve pitch classes. Sorting all 161 descriptors alphabetically and accumulating widths placed `.tonal.chords_key` at **547** and `.tonal.key_key` at **664**, totalling **670**. Three independent facts agreed, so the ordering was alphabetical over the union of numeric and string descriptors, and the enumerated dimensions were **not normalised** — the support vectors hold raw codes at those positions.

Vector dimensions now matched the model exactly: 670 against 670. **And it was still wrong.** Over 25 recordings with a fixed label orientation:

```
median 0.3660   exact (<0.001) 0/25   range 0.004 … 1.000
```

Errors reaching **1.000** were confident inversions, not numerical drift. A brief earlier reading of "best 0.0000" was an artefact of the harness taking the best over *both* label orientations as well as all submissions — generosity that flattered the result and hid this distribution. The harness needed tightening: the label-to-class-name mapping should be resolved from the chain rather than tried both ways. Two candidate causes were identified, untested: the `cleaner` step between `select` and `normalize` might drop descriptors and shift indices; or the `label 1 0` mapping might not mean what it was assumed to mean.

**`[LOG-FEX-096]` Chain order resolved; the enumeration maps are stored, and unread.** Ordering the steps by file offset settled the sequence:

```
remove(16) → fixlength(29730) → remove(112618) → enumerate(153388)
  → normalize(162678) → select(951377) → cleaner(972078)
  → normalize(982013) → svmtrain(1071587) → select(1889620)
```

`cleaner` uses the `removedesc` applier, so it does drop descriptors — but **both** normalize steps carried 157 in this chain, so nothing was dropped and the `cleaner` cause was eliminated. Composing the two normalizes across the intervening `select`/`cleaner` was correct.

Immediately before `svmtrain` the chain stores, explicitly, **the enumeration maps and the class field**:

```
.tonal.key_scale     minor, major
.tonal.key_key       G#, G, F#, F, E, D#, D, C#, C, B, A#, A
.tonal.chords_scale  minor, major
.tonal.chords_key    G#, G, F#, F, E, D#, D, C#, C, B, A#, A
className            highlevel.tonal_atonal
```

This contradicted the codes then in the tool — `[LOG-FEX-095]` assumed alphabetical (`A=0 … G#=11`); the stored order is descending. Reading the maps needed QVariant types **12** and **32**, which `read_variant` refused rather than guessed — the same discipline that kept the container framing honest `[LOG-FEX-070a]`. `gaia_predict.KEY_CODES` was marked unverified pending that read.

**`[LOG-FEX-097]` The enumeration maps are read — and the codes are arbitrary.** The maps are **not** `QVariant`-framed, which is why the reader balked. They are a bare `quint32` count followed by that many `(QString, quint32)` pairs:

```
00 00 00 02   count = 2
00 00 00 0a "minor"  00 00 00 00      minor = 0
00 00 00 0a "major"  00 00 00 01      major = 1
```

Read properly, the codes are **arbitrary and differ per descriptor**:

| descriptor | codes |
| :--- | :--- |
| `.tonal.key_key` | G#=0, F=1, G=2, C=3, A#=4, A=5, E=6, C#=7, B=8, D=9, F#=10, D#=11 |
| `.tonal.chords_key` | G#=0, F=1, G=2, C=3, E=4, D=5, A=6, F#=7, B=8, C#=9, D#=10, A#=11 |
| both `_scale` | minor=0, major=1 |

The same twelve notes map differently in the two descriptors, so **no ordering rule would have produced them** — not alphabetical, not chromatic, not file order. Both earlier guesses were wrong: `[LOG-FEX-095]`'s alphabetical keys, and a `major=0, minor=1` scale mapping that was exactly inverted. `gaia_history.enum_maps` now reads them from the chain.

**And the chain still did not reproduce.** With correct codes, over 25 recordings at a fixed orientation:

```
median 0.4289   exact (<0.001) 0/25   range 0.020 … 1.000
```

The generous metric improved (worst 0.267 → 0.176) while the strict median moved the wrong way — the residual error was **not** the enumerated dimensions, which were now right, and something larger was still wrong. A global label flip did not explain it either: the distribution was broad rather than bimodal.

**`[LOG-FEX-098]` The divergence, found in the literature: a gaussianize step the tooling was hiding.** MTG's Gaia provides a `gaussianize` transformation, and its ChangeLog discusses serialising gaussianize histories — a transformation not previously accounted for. It is present in the chain, at offset 242175:

```
remove → fixlength → remove → enumerate → normalize → GAUSSIANIZE
  → select → cleaner → normalize → svmtrain → select
```

Applier `distribute`; `descriptorNames = ['lowlevel.*']`; parameter `distribution`, a **per-component** table keyed `.lowlevel.zerocrossingrate.var[0]`. The ~709 KB between it and the following `select` is that table.

Gaussianize maps each component through the training set's empirical distribution, so it is **non-linear and per-component**. It covers `lowlevel.*`, which is most of the vector. Omitting it left the majority of dimensions transformed by the wrong function entirely — precisely the observed signature: broad, decorrelated error, a few recordings near-right by luck, and no amount of fixing indices or codes moving the median.

**This also corrected `[LOG-FEX-094]`.** The "two normalize steps that compose" were the signature of a **gaussianize sandwich**: `normalize → gaussianize → normalize`. Composing the two normalises directly, as that entry concluded, had silently deleted the non-linear middle. The classifiers with two normalizes are a superset of those with gaussianize, which is why the pattern first looked like a normalisation quirk.

**How it stayed hidden for six commits is the more useful lesson.** `chain_summary` filtered step names against a `KNOWN` set, so any transformation not already in the vocabulary was omitted from every chain printed — and the omitted one was the transformation that mattered. Refusing to guess at *values* (`[LOG-FEX-070a]`, `[LOG-FEX-096]`) was right and repeatedly paid off; assuming the *vocabulary* was complete was the same error wearing different clothes, and it was never checked because the output looked plausible. `chain_of` now reports every step and flags unrecognised ones rather than dropping them.

Only 4 of 18 beta5 chains gaussianize — `genre_dortmund`, `genre_electronic`, `moods_mirex`, `voice_instrumental` — and `tonal_atonal` is not among them there. But the **beta1** `tonal_atonal` is. Picking the smallest, simplest-looking classifier as the first test target happened to pick one carrying the extra non-linear step.

> **Sources:** [MTG/gaia ChangeLog](https://github.com/MTG/gaia/blob/master/ChangeLog) · [essentia gaiatransform.cpp](https://github.com/MTG/essentia/blob/master/src/algorithms/highlevel/gaiatransform.cpp) · [gaia normalize.cpp](https://github.com/MTG/gaia/blob/master/src/algorithms/normalize.cpp) · [Essentia music extractor docs](https://essentia.upf.edu/streaming_extractor_music.html)

---

## Route 2 verified — the chain reproduces AcousticBrainz exactly

**`[LOG-FEX-099]` ✅ ROUTE 2 VERIFIED.** Isolating the variable worked: fetching a beta1 classifier **without** gaussianize and checking the rest of the chain against it. Three beta1 classifiers without a gaussianize step, each against 120 archived recordings with published highlevel:

| classifier | exact (<0.001) | median error | p95 | max |
| :--- | ---: | ---: | ---: | ---: |
| `mood_acoustic` | 114/120 — 95% | 0.000065 | 0.0018 | 0.0033 |
| `mood_happy` | 104/120 — 87% | 0.000033 | 0.0024 | 0.0034 |
| `mood_party` | 95/120 — 79% | 0.000002 | 0.0024 | 0.0037 |

**Maximum error 0.0037 across 360 comparisons — no outliers, no inversions.** The residual is consistent with rounding in the published dump values rather than any error in the chain. This is reproduction, not approximation.

The corrected `chain_of` also confirmed the structural rule with no exceptions among the four beta1 classifiers held: **one normalize ⇔ no gaussianize; two normalizes ⇔ a gaussianize sandwich.**

```
mood_acoustic  remove → fixlength → remove → enumerate → select → select
                 → cleaner → normalize → addfield → svmtrain → select
tonal_atonal   remove → fixlength → remove → enumerate → normalize → gaussianize
                 → select → cleaner → normalize → addfield → svmtrain → select
```

**What this validated, all at once:** the beta1 model vintage (`[LOG-FEX-093]`), the alphabetical descriptor layout with enumerated strings interleaved (`[LOG-FEX-095]`), the enum codes read from the chain (`[LOG-FEX-097]`), `y = a·x + b` normalisation (`[LOG-FEX-069]`), the libsvm RBF and Platt-sigmoid implementation (`[LOG-FEX-068]`), and the harness itself. Every one of those had previously been *asserted*; all were now *confirmed* by a single end-to-end result that could not have come out this way if any were wrong. It also confirmed the `[LOG-FEX-098]` diagnosis: the only thing wrong with `tonal_atonal` was the missing gaussianize.

Remaining for full coverage of all 18: gaussianize for the chains that use it (in the beta5 set, `genre_dortmund`, `genre_electronic`, `moods_mirex`, `voice_instrumental` — three of them among the six complex characteristics Lempi most needs `[SPEC-FD-082]`; note the beta1 chains differ, so the beta1 set had to be surveyed rather than assumed); pairwise coupling for the six multi-class classifiers (only binary Platt was implemented so far); and fetching the remaining beta1 classifiers (~100 MB). None of this remained exploratory — the chain was understood and proven. Continued in [LOG003](LOG003-feature-reproduction-verification.md).
