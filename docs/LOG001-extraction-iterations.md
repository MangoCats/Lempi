# LOG001: Feature Extraction Iteration Log

**Development Record — Tier 0**

Running record of attempts to replace AcousticBrainz locally. Per `[LOG-FEX-100]`, **the iteration history is itself a deliverable**: it records what was tried, what the numbers were, and why each approach plateaued, so the next attempt starts from evidence rather than from scratch.

> ⚠️ **Not what ships.** Everything below is **Route 3 — distillation**: small MLP/gradient-boosted models trained to predict AcousticBrainz's highlevel output from lowlevel features, reaching a median err/β of **0.182** by [Iteration 6](#iteration-6--library-native-constants). It was the working production plan while **Route 2 — reimplementing AcousticBrainz's own Gaia/SVM chain exactly** was still being reverse-engineered. Route 2 succeeded (`[LOG-FEX-099..108]`, max error **0.0072** across all 18 classifiers — over 25× tighter than distillation, and not an approximation at all) and is the **sole** method `tools/extract_library.py` runs; nothing on this page is loaded in production. Kept in full, per `[GDE-LES-030]`'s discipline of reporting every attempt honestly — see [SPEC007 §4](spec/SPEC007-vipunen-architecture.md#4-classification-s5--settled) for the settled production account.

Strategy in [GUIDE003](GUIDE003-feature-extraction-strategy.md). Metric definitions in [SPEC005](spec/SPEC005-flavor-distance.md).

---

## Reading These Numbers

**`[LOG-MET-010]`** The headline metric is **err/β** — total-variation error divided by that characteristic's natural between-recording spread. Raw Pearson r is reported but is misleading on a simplex: it tracks target variance, so a tightly-clustered characteristic scores badly even when its absolute error is tiny `[LOG-I1-030]`.

**`[LOG-MET-020]`** The reference point is **the floor**: AcousticBrainz's own submission-to-submission error, likewise normalized `[GDE-FEX-085]`. Median floor is **0.210**. At or below the floor, further effort is chasing encoding noise rather than improving anything real — the calibration point `[GDE-PHS-005]` calls for.

> ⚠️ **`[LOG-MET-030]` The 0.210 floor used throughout this log is measured on a generic AcousticBrainz sample, not on our library — and it is WRONG IN THE OPPOSITE DIRECTION to what was predicted.**
>
> This log previously warned that 0.210 was *too generous* and that every ratio was "flattering by an unmeasured margin". **That prediction was wrong.** Recomputed on the library's own 7,685 multi-submission recordings `[LOG-I6-010]`, the floor is **0.359**, not 0.210 — AcousticBrainz is markedly *less* self-consistent on our library than on a random sample, because our recordings carry a mean of 77 submissions from many different rips.
>
> Corrected on library-native constants: median err/β **0.182 against a 0.359 floor = 0.51×**, and **16 of 18** characteristics at or below their own floor. The ratios below were **pessimistic**, not flattering. They are retained as recorded, with `[LOG-I6-010]` as the authority.

---

## Iteration 0 — Lempi v1 (inherited, for contrast)

**`[LOG-I0-010]` Approach:** MusicNN embeddings + classification heads, hand-rolled mel spectrogram.

**Result: never measured.** 91% of stored values were silently inherited from MuLibPlay rather than computed `[GDE-V1-010]`. Four independent preprocessing defects `[GDE-V1-020]`.

**Why it plateaued:** it did not plateau — it was never evaluated. This is the failure the whole measurement discipline exists to prevent.

---

## Iteration 1 — Distillation baseline

**`[LOG-I1-010]` Approach.** Route 3 `[LOG-FEX-065]`: predict what Gaia predicted. One shared MLP (512, 256) over 928 non-band lowlevel features → all 71 highlevel dimensions. 99,996 paired samples from the 2022-06-23 sample dumps; split by **recording MBID** (79,957 train / 20,039 test rows; 71,051 / 17,763 recordings) so no recording crosses the split.

**`[LOG-I1-020]` Result.** Overall r **0.880**, TV 0.090, top-1 **91.5%**. Median err/β **0.223** against the 0.210 floor — the median characteristic reproduced to within ~6% of the noise floor of the thing being copied, on the first attempt.

Seven of eighteen characteristics landed **below** the floor: `mood_electronic` 0.66×, `mood_happy` 0.68×, `tonal_atonal` 0.71×, `timbre` 0.87×, `danceability` 0.90×, `mood_party` 0.96×, `voice_instrumental` 0.98×. Expected when distilling a deterministic function from clean labels — the student is more self-consistent than the teacher.

**`[LOG-I1-030]` Analysis — strengths.** All twelve binary mood/timbre characteristics are comfortable. `mood_sad` r 0.983, `mood_happy` 0.974, `mood_electronic` 0.964.

**Analysis — weaknesses.** Error concentrates in the **multi-class genre** classifiers: `genre_tzanetakis` 3.35× floor, `genre_dortmund` 2.46×, `gender` 2.00×, `genre_rosamerica` 1.67×.

Diagnostic finding: mean r tracks target standard deviation almost exactly. `genre_tzanetakis` scores r 0.46 while getting top-1 right 89.2% of the time, because its ten classes cluster tightly (class `met` has SD 0.0175). **This retired raw r as the headline metric** and motivated `[LOG-MET-010]`.

Consequential observation: the weak genre classifiers are exactly the ones SPEC005 found *most reliable* in AcousticBrainz (`genre_dortmund`, `genre_electronic` at 0.880 reliability `[SPEC-FD-050]`). The characteristics carrying the most information are the hardest to distil, so they matter more than their count suggests.

---

## Iteration 2 — Three hypotheses on the two weakest

**`[LOG-I2-010]`** Tested on `genre_tzanetakis` (worst) and `gender`. Floors: 0.181 and 0.196.

| Hypothesis | `genre_tzanetakis` err/β | `gender` err/β |
| :--- | ---: | ---: |
| *iteration 1 baseline (shared MLP)* | *0.604* | *0.392* |
| **H2** dedicated MLP per characteristic | 0.527 | **0.337** |
| **H1** gradient boosting per class | **0.462** | 0.393 |
| **H3** dedicated MLP, band statistics removed | 0.548 | — |

**`[LOG-I2-020]` H2 confirmed — task interference is real.** One trunk serving 18 tasks was crowding out the weak ones. Dedicated models improved both (0.604→0.527, 0.392→0.337). **Adopt.**

**`[LOG-I2-030]` H1 confirmed but characteristic-dependent.** Gradient boosting won decisively on `genre_tzanetakis` (0.462 vs 0.527) and lost on `gender` (0.393 vs 0.337). Plausibly because the teacher is an RBF SVM whose decision structure suits trees on some tasks and smooth function approximation on others. **Adopt per-characteristic model selection**, not a single global choice.

**`[LOG-I2-040]` H3 — designed as a falsification test, and it held.** The models' own `.history.param` specifies `preprocessing: nobands`, so iteration 1 excluded the raw per-band arrays. If the teacher never saw band information, removing the *derived* band statistics should not hurt. It did hurt (0.527 → 0.548), showing the 135 retained band-derived scalars (`barkbands_kurtosis`, `erbbands_flatness_db`, …) do carry teacher-relevant signal.

**Honest limitation:** this tested *removal* of the retained statistics, not *addition* of the excluded raw arrays. Whether adding those raw per-band arrays helps further is **untested** — carried to `[LOG-NEXT-020]`.

---

## Iteration 3 — Consolidation

**`[LOG-I3-010]` Approach.** Dedicated gradient-boosting model per characteristic (max_iter 400, lr 0.08), kept only where it beat iteration 1's shared MLP on held-out err/β. 2,746 s.

**`[LOG-I3-020]` Result — median err/β 0.223 → 0.192, now below the 0.210 floor.** Characteristics at or below their own floor: **9 of 18** (up from 7). Gradient boosting won 11 of 18.

The genre group, iteration 1's weak spot, improved substantially:

| Characteristic | iter1 | iter3 | vs floor |
| :--- | ---: | ---: | ---: |
| `genre_dortmund` | 0.295 | **0.170** | 2.46× → 1.42× |
| `genre_electronic` | 0.190 | **0.130** | 1.59× → 1.09× |
| `moods_mirex` | 0.278 | **0.179** | 1.32× → **0.85×** (now below floor) |
| `genre_tzanetakis` | 0.604 | **0.460** | 3.35× → 2.55× |
| `genre_rosamerica` | 0.219 | **0.195** | 1.67× → 1.49× |

**`[LOG-I3-030]` Analysis — an oversight in the experiment design, recorded rather than quietly fixed.** Iteration 3 compared, per characteristic, dedicated GBM against the **shared** MLP — and never tried a **dedicated** MLP, despite `[LOG-I2-020]` having already established that dedicated beats shared.

The cost is visible on `gender`: iteration 3 selected GBM at 0.390 over the shared MLP's 0.392, while **iteration 2's dedicated MLP had already reached 0.337**. The reported 1.99× floor for `gender` is therefore wrong — the best already-measured result is 1.72×.

Every characteristic whose iteration-3 winner reads `mlp-shared` (`mood_electronic`, `mood_happy`, `mood_relaxed`, `timbre`, `tonal_atonal`, `voice_instrumental`) is suspect for the same reason. Corrected in iteration 4.

**Lesson worth keeping:** when a comparison table mixes candidate families, every cell must come from the same candidate set. Iteration 3's "best" column silently compared different option sets per row.

---

## Iteration 4 — Closing the iteration-3 gap

**`[LOG-I4-010]` Approach.** Fit a dedicated MLP for the six `mlp-shared` winners plus `gender`, then select best-of-three per characteristic.

**`[LOG-I4-020]` Result — the dedicated MLP won 7 of 7.** Median err/β **0.192 → 0.174**; at-or-below-floor **10 of 18**.

| Characteristic | prev best | dedicated MLP | vs floor |
| :--- | ---: | ---: | ---: |
| `mood_happy` | 0.147 | **0.076** | 0.68× → **0.35×** |
| `mood_electronic` | 0.149 | **0.123** | 0.66× → **0.54×** |
| `tonal_atonal` | 0.155 | **0.140** | 0.71× → **0.64×** |
| `timbre` | 0.189 | **0.152** | 0.87× → **0.70×** |
| `mood_relaxed` | 0.221 | **0.170** | 1.01× → **0.77×** |
| `voice_instrumental` | 0.230 | **0.209** | 0.98× → **0.89×** |
| `gender` | 0.390 | **0.337** | 1.99× → 1.72× |

**`[LOG-I4-030]` Analysis — the fix was itself incomplete, and by the same mechanism.** A clean sweep of 7/7 is not a result about those seven characteristics; it is evidence that **dedicated MLPs were under-tested everywhere**.

The ten characteristics where GBM won in iteration 3 were only ever compared against the *shared* MLP. A dedicated MLP was never fit for them either. Only `gender` and `genre_tzanetakis` have all three candidate families measured — and `gender` flipped the moment its third option was tried.

So iteration 3's error `[LOG-I3-030]` was patched for the subset that was noticed, not for the class of characteristics it affected. Twice now, a comparison table has been read as if complete when it was not.

**Process correction adopted:** define the full candidate set **before** running, and treat any partially-populated comparison as unreported rather than as a result. Iteration 5 completes the factorial.

---

## Iteration 5 — Completing the factorial

**`[LOG-I5-010]` Approach.** Fit dedicated MLPs for the ten remaining characteristics, so every cell of the 18 × {shared MLP, dedicated GBM, dedicated MLP} comparison finally comes from the same candidate set. 6.7 h.

**`[LOG-I5-020]` Result — median err/β 0.174 → 0.152, against the 0.210 floor.** At or below their own floor: **13 of 18**. Dedicated MLPs won 9 of the 10 tested here; `mood_party` was the sole exception, and only just (GBM 0.222 vs 0.225).

Across the whole factorial, the **dedicated MLP is the best family for 16 of 18 characteristics**. Only `genre_tzanetakis` and `mood_party` retain gradient boosting. Iteration 3's original conclusion — that model class is strongly characteristic-dependent — was largely an artefact of the missing candidate `[LOG-I3-030]`.

Largest movers: `mood_sad` 0.119 → **0.077**, `moods_mirex` 0.179 → **0.135**, `ismir04_rhythm` 0.227 → **0.149**.

**`[LOG-I5-030]` Final model selection.**

| Characteristic | err/β | vs floor | Family |
| :--- | ---: | ---: | :--- |
| `mood_happy` | 0.076 | **0.35×** | dedicated MLP |
| `mood_sad` | 0.077 | **0.64×** | dedicated MLP |
| `moods_mirex` | 0.135 | **0.64×** | dedicated MLP |
| `tonal_atonal` | 0.140 | **0.64×** | dedicated MLP |
| `timbre` | 0.152 | **0.70×** | dedicated MLP |
| `ismir04_rhythm` | 0.149 | **0.72×** | dedicated MLP |
| `mood_relaxed` | 0.170 | **0.77×** | dedicated MLP |
| `danceability` | 0.203 | **0.82×** | dedicated MLP |
| `genre_electronic` | 0.104 | **0.87×** | dedicated MLP |
| `mood_aggressive` | 0.184 | **0.87×** | dedicated MLP |
| `voice_instrumental` | 0.209 | **0.89×** | dedicated MLP |
| `mood_party` | 0.222 | **0.90×** | GBM |
| `mood_electronic` | 0.123 | **0.54×** | dedicated MLP |
| `mood_acoustic` | 0.134 | **1.00×** | dedicated MLP |
| `genre_rosamerica` | 0.158 | 1.21× | dedicated MLP |
| `genre_dortmund` | 0.152 | 1.27× | dedicated MLP |
| `gender` | 0.337 | 1.72× | dedicated MLP |
| `genre_tzanetakis` | 0.460 | 2.55× | GBM |

**`[LOG-I5-040]` Analysis.** Distillation `[LOG-FEX-065]` is validated: the median characteristic now reproduces AcousticBrainz **28% more consistently than AcousticBrainz reproduces itself**, with no Gaia, no Essentia build, and no binary-format reverse-engineering.

Four characteristics remain above their floor. Three are genre classifiers, plus `gender`. Per `[SPEC-FD-050]` the genre classifiers are the *most reliable* in AcousticBrainz, so the residual error is concentrated where it carries the most information — `[LOG-NEXT-010]` (Nyström kernel approximation of the RBF-SVM teacher) targets exactly this group.

**Stopping condition per `[LOG-NEXT-040]`:** the 13 characteristics at or below floor are **frozen**. Further tuning there fits encoding noise, not signal.

**`[LOG-I5-050]` Caveat — resolved by `[LOG-I6-010]`, in the opposite direction to the one predicted.** The concern was that the single-vs-single floor was too generous. Measurement showed the reverse: on our library the floor is 0.359, not 0.210, so these ratios were pessimistic. Superseded below.

---

## Iteration 6 — Library-native constants

**`[LOG-I6-010]` Approach.** Recompute β and the floor on the library's own 7,685 multi-submission recordings rather than a generic AcousticBrainz sample, capped at 8 submissions each (4.1M values). Two floors computed: submission-vs-submission, and submission-vs-consensus — the latter being the stricter reference the caveat called for.

**Result — both predictions wrong, in informative ways.**

| | generic sample | our library |
| :--- | ---: | ---: |
| median floor, single vs single | 0.210 | **0.411** |
| median floor, single vs consensus | — | **0.359** |
| consensus tightening | predicted large | **1.14× only** |

Averaging submissions barely tightens the floor (1.14×), so the "77 submissions cancel the noise" reasoning was much weaker than assumed. And the library floor is nearly **double** the generic one: AcousticBrainz is *less* self-consistent on our music, not more. The likely cause is popularity — 77 submissions from many rippers carry more encoding diversity than a sample recording's two.

**`[LOG-I6-020]` Corrected model performance.** Errors recomputed with library β (the numerator changes too, since β is smaller for 16 of 18 characteristics):

| | old (sample constants) | new (library constants) |
| :--- | ---: | ---: |
| median err/β | 0.152 | 0.182 |
| median floor | 0.210 | 0.359 |
| **ratio** | 0.72× | **0.51×** |
| at or below floor | 13/18 | **16/18** |

Only `genre_tzanetakis` (1.87×) and `gender` (1.04×) now exceed their floor. `genre_tzanetakis` remains the outlier: its library β is 0.1175 against 0.2186 on the sample, so its distributions are half as spread here and the same absolute error normalizes to twice as much.

**`[LOG-I6-030]` Lesson.** Two consecutive predictions about the floor were wrong — first its direction, then the size of the consensus correction. Reference constants are a property of the corpus, and this project has now been surprised by that twice. Constants measured on anything other than the corpus in use should be treated as unvalidated, not as approximations `[SPEC-FD-053]`.

---

## Next Hypotheses

**`[LOG-NEXT-010]` Kernel methods matching the teacher.** The teacher is an RBF C-SVC (`C=11`, `gamma=-11`). A Nyström kernel approximation plus a linear head would mimic that decision structure more directly than either trees or an MLP. Most promising untried idea for the genre group.

**`[LOG-NEXT-020]` Raw per-band arrays.** The untested half of H3 `[LOG-I2-040]`.

**`[LOG-NEXT-030]` More paired data.** Currently capped at ~100k by the *sample* lowlevel dump. The full lowlevel dump is 589 GB — infeasible to mirror whole `[LOG-FEX-050]`, but individual ~20 GB shards would multiply training data severalfold. Worth testing whether iteration 3's residual is data-limited or model-limited **before** paying that download cost.

**`[LOG-NEXT-050]` Shared trunk with dedicated heads.** Iterations 4–5 favour dedicated per-characteristic models, but a shared 928→512→256 trunk with 18 small task-specific heads may capture most of that gain — it keeps the task-specific capacity that `[LOG-I2-020]` showed matters, while letting 18 related tasks share a representation. Possibly *more* accurate, not merely smaller.

Test it on accuracy, **not** on size: at 44 MB the fully dedicated models ship comfortably `[GDE-FEX-027]`, so distribution exerts no real pressure here. Size is a tiebreaker, not a criterion.

**`[LOG-NEXT-040]` Accept per-characteristic floors.** Several characteristics are already below the floor and should be **frozen, not tuned further** — continuing would fit encoding noise. Per `[GDE-PHS-005]`, stopping there is the correct outcome, not a shortfall.

---

## Standing Caveats

**`[LOG-CAV-010]`** All results are Stage B only — lowlevel → highlevel. Stage A needs no validation because the reference extractor binary is run directly `[LOG-FEX-062]`.

**`[LOG-CAV-020]`** Test data comes from the same 2022-06-23 sample dump as training. Held out by recording, but not by era, genre distribution, or encoding population. Real-world accuracy on **new** music — the 729 tracks that motivate this work `[GDE-FEX-010]` — is inferred, not measured, and cannot be measured directly since no ground truth exists for them `[GDE-FEX-160]`.

**`[LOG-CAV-030]`** Every value produced carries its provenance `[GDE-FBD-020]`, and feeds flavor distance weighted by a corpus-wide reliability per characteristic `[SPEC-FD-052]` (the earlier per-value-accuracy premise, `[SPEC-FD-120]`, was superseded once the library moved to uniform-local provenance `[SPEC-FD-150]`). A weak characteristic degrades similarity gracefully rather than poisoning it.
