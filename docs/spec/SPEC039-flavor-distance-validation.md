# SPEC039: Flavor Distance — Validation

**Specification — how the metric was checked, and against what**

Split from [SPEC005](SPEC005-flavor-distance.md) on 2026-09-10, which had
reached 351 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [SPEC005](SPEC005-flavor-distance.md) for the metric itself

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §4 in [SPEC039](SPEC039-flavor-distance-validation.md), the rest in [SPEC005](SPEC005-flavor-distance.md).

## 4. Validation

**`[SPEC-FD-060]`** Metric designs compared by retrieval: given submission 0 of a recording as the query, how well does each metric rank submission 1 of the *same* recording against 499 random distractors? 1,500 queries.

> **Not comparable with the table in §5.** This section varies the *metric* with provenance held constant; §5 `[SPEC-FD-140]` varies the *provenance* with the metric held constant. Different baselines and different questions — the two sets of percentages must not be read as a progression.

| Metric | top-1 | top-5 | MRR |
| :--- | ---: | ---: | ---: |
| MuLibPlay — 11 binary dims, squared Euclidean | 76.2% | 80.5% | 0.785 |
| Total variation, all 18 characteristics, unweighted | 79.9% | 83.7% | 0.819 |
| **Total variation, scale-normalized + reliability-weighted** | **81.5%** | 83.6% | **0.827** |

Read honestly: **most of the gain comes from using all 18 characteristics** (+3.7 pp top-1), not from the weighting scheme (a further +1.6 pp). The weighting earns its place — it is nearly free once `β_c` and `w_c` are measured — but the specification's real content is *use the complex characteristics MuLibPlay threw away*.

**`[SPEC-FD-070]` Limitation — this validates robustness, not perceptual similarity.** The test measures whether a metric recognizes the same recording through a different encoding. That is necessary but not sufficient for "finds the most similar-sounding track": a metric could score well here and still rank perceptually unrelated songs as close.

**`[SPEC-FD-080]` Perceptual validation — run 2026-08-10. The metric agrees with the listener.** MuLibPlay's eight programmes each carry 6–8 hand-picked seeds `[GDE-PD-040]` — direct human judgments that these songs belong together. All 49 have flavor.

| | mean distance | pairs |
| :--- | ---: | ---: |
| within programme | 0.9895 | 128 |
| across programmes | 1.1931 | 1,048 |
| **ratio** | **0.829** | |

**Same-programme seeds sit 17.1% closer than cross-programme seeds.** This is the first evidence that the metric tracks *perceptual* similarity rather than merely recognising a re-encode `[SPEC-FD-070]`, and it was obtained on 11 binary characteristics alone.

**`[SPEC-FD-082]` The per-programme spread is the more useful result**, because it points at what is missing:

| Programme | seeds | mean d | vs library mean |
| :--- | ---: | ---: | ---: |
| Loud | 6 | 0.784 | 66% |
| Soft | 6 | 0.841 | 71% |
| Mellow | 6 | 0.877 | 74% |
| Prog | 6 | 0.890 | 75% |
| Fun | 6 | 1.033 | 87% |
| Cool | 5 | 1.093 | 92% |
| Groove | 8 | 1.137 | 95% |
| Light | 6 | 1.168 | 98% |

The programmes that cohere are the ones the 11 binaries can express — *Loud* is largely `mood_aggressive` and `timbre`, *Soft* and *Mellow* largely `mood_relaxed` and `mood_acoustic`. The programmes that barely cohere at all — *Light* at 98% of the library mean, *Groove* at 95% — are the ones defined by **genre and rhythm**, which is precisely what the six absent complex characteristics carry `[SPEC-FD-085]`.

This is a concrete, testable prediction: extracting `genre_*`, `ismir04_rhythm` and `moods_mirex` should tighten *Light* and *Groove* markedly, and *Loud* and *Soft* comparatively little. Re-run this measurement after extraction; it is the cheapest available check on whether extraction bought anything.

**Caveat as before:** 49 seeds over 8 programmes is a small sample, and one listener's groupings are not a general perceptual standard. Treat 0.829 as encouraging, not as a validated figure.

**`[SPEC-FD-083]` Tested 2026-08-11 with the complex characteristics present — the prediction was right about *Light* and wrong overall.**

55 programme seeds were extracted locally and classified through all 18 reproduced Gaia chains `[LOG-FEX-102]`; 35 have both inherited and local flavor, so the comparison holds the seed set fixed and varies only the features.

| feature set | within | cross | ratio |
| :--- | ---: | ---: | ---: |
| inherited, 11 characteristics | 0.9957 | 1.2162 | **0.8187** |
| local, 18 characteristics | 0.9764 | 1.1387 | **0.8575** |

**Aggregate separation got worse, not better.** But the per-programme breakdown shows why, and it is not a flat refutation:

| programme | n | inherited 11 | local 18 | local, complex only |
| :--- | ---: | ---: | ---: | ---: |
| **Light** | 6 | 1.168 | 0.973 | **0.788** |
| Fun | 6 | 1.033 | 0.946 | 0.822 |
| Cool | 5 | 1.093 | 1.057 | 0.938 |
| Prog | 4 | 0.728 | 0.679 | 0.631 |
| Loud | 2 | 1.075 | 1.038 | 0.948 |
| **Mellow** | 6 | 0.877 | 1.088 | **1.388** |
| **Soft** | 5 | 0.914 | 0.953 | **1.155** |

*Light* — the worst-cohering programme, and the specific case `[SPEC-FD-082]` named — tightened from 1.168 to 0.788 on the complex characteristics alone. *Fun*, *Cool*, *Prog* and *Loud* improved too. **The aggregate fell because *Mellow* and *Soft* degraded sharply**, and those are precisely the programmes the mood binaries express well and genre/rhythm does not.

The honest reading: **the complex characteristics are not uniformly better, they are differently informative.** A metric weighting all 18 equally trades away what mood captures to gain what genre captures.

Three caveats, the second serious enough to require work before this is treated as settled:

1. **Small n** — 35 seeds, 2–6 per programme. *Loud* rests on a single pair.
2. **The constants are wrong for this data.** `β_c` and `w_c` were measured on *dump* values `[SPEC-FD-052]`, and `[SPEC-FD-090]` states they are per flavor source. They are applied here to *locally extracted* values, so every complex characteristic is scaled by a β measured on a different corpus. That alone could produce the *Mellow*/*Soft* degradation, and it must be re-derived on local values before the comparison means anything.
3. **The baseline is confounded** — inherited-11 versus local-18 differs in provenance as well as in feature count, so it is not a clean "more characteristics" test.

**Consequence for `[SPEC-DIR-200]`:** re-deriving the pool parameters should wait until the constants are re-derived, not merely until the vector grows.

**`[SPEC-FD-084]` Resolved 2026-08-13 on the fully extracted library — and the ratio was the wrong measure.**

The library is now uniformly local: **8,073 of 8,079 passages** extracted per passage and classified through all 18 reproduced chains `[LOG-FEX-102]`, 7,894 recordings, 0.07% loss.

*The constants were re-derived on local values* `[SPEC-FD-090]`, using the 163 recordings that appear in more than one passage as the test–retest set. **Local reliability is higher for every one of the 18:**

| | mean `w_c` | range |
| :--- | ---: | :--- |
| dump-derived `[SPEC-FD-052]` | 0.60 | 0.45 – 0.74 |
| **local** | **0.77** | 0.67 – 0.88 |

`timbre` 0.482 → 0.792, `mood_electronic` 0.454 → 0.746, `genre_tzanetakis` 0.517 → 0.669. This is `[GDE-FEX-028]`'s argument measured directly: the dump's low self-consistency came from ~77 submissions per recording across many rips `[LOG-FEX-057]`; one pipeline over our own files has far less within-recording variance. **Uniform local provenance is not merely equal to the dump — it is measurably more self-consistent.**

*The comparison, on the 48 seeds present in both, isolating each change:*

| | within | cross | ratio | **P@1** | **P@3** | **MRR** |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| inherited 11 + dump constants | 0.9924 | 1.1987 | 0.8279 | 0.188 | 0.236 | 0.406 |
| local 18 + dump constants | 0.9551 | 1.1235 | 0.8502 | **0.271** | 0.229 | **0.458** |
| local 18 + local constants | 0.8876 | 1.0362 | 0.8566 | **0.271** | **0.243** | 0.451 |

**The two measures disagree, and the ratio is the one to discard.** It worsens monotonically while retrieval improves by 44% relative on P@1 (chance is 0.106). The reason is visible in the columns: within-programme distance improved 10.6% and cross-programme improved 13.6%, so the *ratio* fell even though everything cohered better. A metric that compresses the whole space uniformly looks worse by ratio and is not worse.

**Retrieval is the measure that matches the consumer.** Stage B gathers the passages nearest each seed `[SPEC-DIR-145]`; it never computes a within/cross ratio. `[SPEC-FD-080]`'s ratio was a reasonable first proxy and should now be read alongside P@1/MRR rather than alone.

*Per programme, absolute cohesion, inherited → local+local:* seven of eight improved.

| | inh+dump | loc+local | | | inh+dump | loc+local |
| :--- | ---: | ---: | :-- | :--- | ---: | ---: |
| **Light** | 1.168 | **0.849** | | Prog | 0.876 | 0.715 |
| Groove | 1.137 | 1.051 | | Loud | 0.784 | 0.725 |
| Cool | 1.093 | 0.940 | | Soft | 0.841 | 0.811 |
| Fun | 1.033 | 0.855 | | **Mellow** | 0.877 | **0.973** |

*Light* — `[SPEC-FD-082]`'s named prediction — improved most, from worst-cohering to mid-pack. *Mellow* is the sole regression and remains unexplained.

**`[SPEC-FD-086]` Full-library picture, 2026-08-13.** `flavorcheck` over the extracted library:

| | before (inherited 11) | after (local 18) |
| :--- | ---: | ---: |
| subjects | 7,897 | **7,911** |
| **incomparable pairs** | 74 | **0** |
| malformed instances | 0 | 0 |
| median distance | 1.052 | 0.972 |

**Incomparable pairs fall to zero.** Previously 37 recordings carried only user characteristics and could not be compared to anything `[SPEC-FD-085]`; every recording now has the full vector, so no passage is unreachable by similarity. That is a plain correctness gain independent of any accuracy argument.

All 49 seeds now have flavor, against 35 with both provenances:

| programme | mean d | vs library | *(was, inherited 11)* |
| :--- | ---: | ---: | ---: |
| Loud | 0.725 | 70% | *0.784 — 66%* |
| Prog | 0.759 | 73% | *0.890 — 75%* |
| Soft | 0.811 | 78% | *0.841 — 71%* |
| **Light** | **0.849** | **82%** | *1.168 — 98%* |
| Fun | 0.855 | 83% | *1.033 — 87%* |
| Cool | 0.940 | 91% | *1.093 — 92%* |
| Mellow | 0.973 | 94% | *0.877 — 74%* |
| Groove | 1.051 | 102% | *1.137 — 95%* |

*Light* moves from worst-cohering to fourth. *Mellow* and *Groove* are the two that worsen relative to the library mean, and neither is explained.

**Caveat that bounds all of this: n = 48.** A P@1 difference of 0.083 is four seeds. The direction is consistent across three measures and the mechanism is understood, but this is not a statistically strong result, and `[SPEC-FD-080]`'s caveat stands — one listener's groupings are not a general perceptual standard.

---

