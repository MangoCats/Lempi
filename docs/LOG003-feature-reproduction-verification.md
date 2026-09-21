# LOG003: Feature Reproduction — Verification & Production

**Development Record — Tier 0**

Continuation of [LOG002](LOG002-feature-reproduction-investigation.md): closing out Route 2 to all 18 classifiers, then validating the production extraction run — throughput, per-passage correctness, failure diagnosis, and promotion. As with LOG002, this is what shipped; `tools/extract_library.py` runs exactly this path today `[SPEC-SA-040]`.

Strategy in [GUIDE003](GUIDE003-feature-extraction-strategy.md#3-strategy-harvest-then-reproduce-then-approximate). Settled production account in [SPEC007 §4](spec/SPEC007-vipunen-architecture.md#4-classification-s5--settled).

---

## All 18 classifiers reproduce

**`[LOG-FEX-100]` 11 of 18 classifiers reproduce, including three complex ones.** All 18 beta1 models fetched (83 MB). The full survey:

| | needs gaussianize | multi-class |
| :--- | :--- | :--- |
| **7** | `genre_dortmund`, `genre_electronic`, `genre_rosamerica`, `mood_sad`, `timbre`, `tonal_atonal`, `voice_instrumental` | |
| **6** | | `genre_dortmund`, `genre_electronic`, `genre_rosamerica`, `genre_tzanetakis`, `ismir04_rhythm`, `moods_mirex` |

**Eight binary classifiers, 60 recordings each** — both kernels, since `danceability`, `mood_aggressive` and `mood_happy` are polynomial:

```
danceability 55/60   gender 44/60   mood_acoustic 59/60   mood_aggressive 56/60
mood_electronic 55/60   mood_happy 51/60   mood_party 45/60   mood_relaxed 56/60
median error 1.3e-5 … 7.4e-5      max across all eight: 0.0037
```

**Three multi-class classifiers**, via Wu–Lin–Weng pairwise coupling as libsvm implements it:

```
genre_tzanetakis  60/60 exact    ismir04_rhythm  60/60 exact    moods_mirex  60/60 exact
median 0.000000                  max 0.0000
```

The multi-class results were *exact* where the binary ones carried a ~0.003 residual, supporting the residual being rounding in the published values rather than anything in the chain.

**Three of the six complex characteristics — `genre_tzanetakis`, `ismir04_rhythm`, `moods_mirex` — were now reproducible**, and they are precisely what `[SPEC-FD-082]` predicts *Light* and *Groove* need.

One transcription note kept because it is the kind of thing that silently corrupts: `sv_coef` is stored here as `[support_vector][coefficient]`, transposed from libsvm's `[coefficient][support_vector]`. In `decision_values` the pair (i, j) uses `sv_coef[·][j-1]` over class i's block and `sv_coef[·][i]` over class j's — an asymmetry worth transcribing rather than reconstructing from intuition. It raised an `IndexError` rather than quietly producing wrong numbers, which was luck as much as design.

Remaining: gaussianize, for the seven chains that use it — including the other three complex characteristics.

**`[LOG-FEX-101]` Gaussianize transcribed from Gaia's source — 12 of 18.** Two guesses at the semantics were made and both were wrong: quantile-to-uniform made `tonal_atonal` worse (0.43 → 0.64), and quantile-through-inverse-normal-CDF worse again (0.82). Reading MTG's `distribute` applier settled it:

```
rank    = lower_bound(distribution, v)
rank    = clamp(rank, outliers, nPoints - outliers)
normIdx = rank / nPoints
out     = erfinv(2*normIdx - 1)
```

`erfinv(2q−1)` is the inverse normal CDF **scaled by 1/√2** — the factor both guesses missed, and one that matters greatly to an RBF kernel. Python has no `erfinv`; it is written through `NormalDist.inv_cdf`, which is the same function.

The stored tables are per component, keyed `.descriptor[i]`: **little-endian float32 inside a big-endian stream** — a raw memory dump — sorted ascending, with no count prefix. 484 components for most chains.

**`mood_sad` now reproduced**: 30/40 exact, median 0.000128, max 0.0030 — the same signature as the other verified classifiers, confirming the algorithm was right.

**Six did not**: `tonal_atonal`, `voice_instrumental`, `timbre`, `genre_dortmund`, `genre_electronic`, `genre_rosamerica`, all sitting near median 0.8. `mood_sad` and `tonal_atonal` were **structurally identical** on every axis checked: same chain (`normalize → gaussianize → select → cleaner → normalize`), same gaussianize scope (`lowlevel.*`), same 484 tables, same 157 descriptors in both normalize steps, and the two normalize steps covered the *same* descriptor set in each. Whatever differed was not the chain shape — the `select`/`cleaner` steps between gaussianize and the second normalize were still unmodelled and assumed inert.

> **Source:** [gaia `distribute` applier](https://github.com/MTG/gaia/blob/master/src/algorithms/distribute.cpp)

**`[LOG-FEX-102]` ✅ ALL 18 CLASSIFIERS REPRODUCE — route 2 complete.** The six that appeared broken were a **harness** fault, not a chain fault. Class names map to model labels **by value, not by position**: the class sorted at index `i` corresponds to model label `i`. The harness had compared against `label[i]`. Where a model's labels read `[0, 1]` the two coincide and everything verified; where they read `[1, 0]` every prediction was scored against the wrong class:

```
tonal_atonal        0.812 → 0.000633      voice_instrumental  0.810 → 0.000739
timbre              0.776 → 0.000273
```

Full verification, 60 archived recordings each, against AcousticBrainz's published highlevel:

| classifier | exact | median | max | | classifier | exact | median | max |
| :--- | ---: | ---: | ---: | :-- | :--- | ---: | ---: | ---: |
| `genre_tzanetakis` | 60/60 | 0.000000 | 0.0000 | | `mood_electronic` | 55/60 | 0.000074 | 0.0029 |
| `ismir04_rhythm` | 60/60 | 0.000000 | 0.0000 | | `mood_aggressive` | 56/60 | 0.000013 | 0.0025 |
| `moods_mirex` | 60/60 | 0.000000 | 0.0000 | | `mood_relaxed` | 56/60 | 0.000046 | 0.0029 |
| `genre_electronic` | 60/60 | 0.000043 | 0.0003 | | `mood_happy` | 51/60 | 0.000042 | 0.0034 |
| `mood_acoustic` | 59/60 | 0.000064 | 0.0025 | | `genre_rosamerica` | 51/60 | 0.000561 | 0.0024 |
| `genre_dortmund` | 57/60 | 0.000009 | 0.0019 | | `mood_sad` | 48/60 | 0.000149 | 0.0034 |
| `mood_aggressive` | 56/60 | 0.000013 | 0.0025 | | `timbre` | 48/60 | 0.000283 | 0.0024 |
| `danceability` | 55/60 | 0.000044 | 0.0031 | | `mood_party` | 45/60 | 0.000014 | 0.0037 |
| `gender` | 44/60 | 0.000039 | 0.0027 | | `tonal_atonal` | 35/60 | 0.000672 | 0.0072 |
| | | | | | `voice_instrumental` | 36/60 | 0.000659 | 0.0070 |

**Maximum error across all eighteen: 0.0072.** Three reproduce exactly. The one deterministic function from 436+ lowlevel scalars to 71 highlevel dimensions was closed.

**The bookend worth keeping.** `[LOG-FEX-095]` warned that a verification tool which *flatters* is worse than none, because it gets believed. This was the mirror: a harness that was too **harsh** made a correct implementation look broken for six commits, and sent the investigation looking for faults in chain structure that was right all along. Both failures are the same underlying error — trusting the comparison more than the thing compared. The check needs checking too.

---

## Production path and throughput

**`[LOG-FEX-103]` Class names are stored too, and the production path runs.** `classMapping` is a plain `QStringList` beside `className` — index i is model label value i, the mapping whose positional guess caused `[LOG-FEX-102]`. All 18 read cleanly, and all are already in sorted order, which retroactively confirmed the `sorted()` assumption the verification relied on.

`tools/gaia_classify.py` is the production path: load the 18 chains once (~10 s, per the module's own measurement — 83 MB of models), then classify any lowlevel JSON. Run against **our own beta2 extraction** rather than the archive:

```
danceability not_danceable 0.857   mood_aggressive aggressive 0.987
genre_dortmund electronic  0.446   mood_happy      happy      0.998
tonal_atonal   atonal      0.925   voice_instrumental voice   0.980
```

Coherent, and named. **586 ms per track for all 18 classifiers** — so classifying the whole 5,590-file library costs under an hour, negligible beside the ~27 s per track the lowlevel extraction itself takes `[LOG-FEX-062]`. Extraction remained the only expensive step, and it is the one that caches `[SPEC-SC-080]`.

**`[LOG-FEX-104]` Throughput measured on this library.** `tools/extract_library.py` runs both stages: audio → extractor → `lowlevel_cache`, then cache → 18 chains → `flavor`, tagged `source = local:essentia-2.1-beta2+gaia-beta1`.

| | |
| :--- | ---: |
| extraction rate | **6.4 s per audio-minute** (26.6–27.0 s for a 4.2-minute track) |
| library | 5,590 files, **585 audio-hours** |
| full extraction | **62.4 core-hours** |
| classification | 586 ms/track — about 1 hour for the library |

The per-track figure matched `[LOG-FEX-062]`'s ~27 s independently. A first 4-file smoke test suggested 155 hours; that was unrepresentative — too small a sample, containing a long file. **Measure on a median-length track, not on whatever the first query returns.**

One inefficiency worth recording: extraction ran on **whole files**, so a passage inside a 191-minute DAO file cost the whole file. The 49 programme seeds spanned 55 recordings but 1,144 audio-minutes for that reason.

---

## Per-passage extraction and failure-rate fixes

**`[LOG-FEX-105]` Per-passage extraction — a correctness fix, not an efficiency one.** Cutting the 62 core-hours substantially was the expectation. It did not happen:

| | audio | core-hours |
| :--- | ---: | ---: |
| whole-file | 585 h | 62.4 |
| passage-only | 572 h | 61.0 **+3.4 decode = 64.4** |

Passages cover nearly all of their files, so per-passage extraction is slightly **more** expensive. The reason to do it anyway is different and stronger: **5,402 of 5,590 files hold one passage; 188 hold 2,677 between them**, up to 40 in a single file. For those, one whole-file feature vector describes *the average of forty different songs*, and every passage inside inherits it — 34% of the library's audio carrying wrong flavor. Demonstrated on `Synchronicity.mp3`:

| passage | genre | moods_mirex | aggressive |
| :--- | :--- | :--- | ---: |
| Synchronicity I | pop 0.40 | Cluster5 | **0.47** |
| Walking in Your Footsteps | rhy 0.68 | Cluster3 | **0.01** |
| O My God | hip 0.27 | Cluster5 | 0.39 |

**How to slice matters.** The extractor takes `startTime`/`endTime` in a profile, but on a 192-minute MP3 that cost **169–230 s** for a four-minute window and **failed outright at non-zero offsets** (rc=1, rc=4). Decoding the window with ffmpeg first takes **1.5 s**, and the extractor then sees a short file: **32.5 s total**, reliable. Whole-file passages skip the decode entirely — 5,402 of 5,590 files.

`lowlevel_cache` was already keyed `(audio_md5, start_ms, end_ms)` `[SPEC-SC-080]`, so the schema anticipated this before the pipeline did.

**`[LOG-FEX-106]` The failure rate explained: a timeout artefact, plus a data bug.** Early runs failed at 25–33%. Both causes were identified and neither is intrinsic.

**Cause 1 — the timeout.** Whole-file extraction with a 300 s cap fails on anything over ~47 audio-minutes at 6.4 s/minute. The seed run predicted **12** such files and lost **14**. Library-wide that is **1,910 of 8,079 passages (23.6%)**. Per-passage extraction removes it, because the unit of work becomes one song:

| | passages timing out |
| :--- | ---: |
| 300 s cap, whole-file | 1,910 (23.6%) |
| 600 s cap, per-passage | **0** |

Passage lengths: median 4.0 min (26 s), p99 9.9 min, max 43.5 min (278 s) — 2.2× headroom against the 600 s cap. **Measured after the fix: 79 of 80 random passages succeeded (99%).**

**Cause 2 — stored durations are unreliable, a data bug.** Probing 400 files against ffprobe:

| | |
| :--- | ---: |
| duration differs from decoded by >5 s | **117 (29.2%)** |
| duration *over*-states the file | 13 (3.2%), median 0.3 min |
| worst case | **38.4 minutes** |

That worst case was the single probe failure. `WhosNext.mp3` is recorded as 191.73 min and is actually **153.37**; its last "passage" runs 153.38–191.58, entirely past the end of the audio — a **phantom passage**, and segmentation created it in a tail that does not exist.

Rarity, from a 545-passage sample: **0 phantom**, 13 (2.4%) merely *truncated* — end past the real duration but start valid, which extracts fine. Library-wide projection: ~0 phantom, ~193 truncated and harmless.

**The fix.** `probe_duration_ms` asks ffprobe (~50 ms, cached per file, against ~27 s of extraction), then skips passages starting past the real end and clamps ends to it. Verified: the phantom passage is now skipped cleanly and its valid siblings still extract.

**`[LOG-FEX-107]` The extractor's own limit: ~20 minutes per analysis.** The full run reached **8,073 of 8,079 passages (99.93%)**. Six remained, and neither remaining cause is a pipeline fault. One is the phantom passage above. **Five hit a hard limit in the extractor.** Bisected on `Thick as a Brick`:

| window | WAV size | result |
| ---: | ---: | :--- |
| 10 min | 106 MB | rc=0, 59 s |
| 20 min | 212 MB | rc=0, 119 s |
| **25 min** | **265 MB** | **rc=3** |

The published Windows build is **`win-i686` — 32-bit** `[LOG-FEX-062]`, so this is an address-space ceiling rather than a defect; it is not a timeout, since at one job these fail in ~79 s. The five affected passages run 28.3–43.5 minutes and are genuine single works (`Thick as a Brick` really is one 43-minute piece), not segmentation errors. Left unmeasured: `[REQ-PD-160]` and `[SPEC-DIR-145]` already degrade gracefully for an unmeasured passage — 0.06% of the library.

---

## Promoted to production (2026-08-13)

**`[LOG-FEX-108]` And a schema trap worth naming.** `data/lempi_new.db` was promoted to the fully extracted, uniformly local library: **8,073 of 8,079 radio passages (99.93%) carrying local flavor** `[LOG-FEX-107]`, 7,911 recordings, 18 local characteristics and locally-derived constants. The 37,134 rows of play history — the only irreplaceable data in the system `[SPEC-SC-020]` — were unchanged. The prior database was preserved alongside it.

**The trap:** the promoted database predated four tables — `listener_settings`, `listener_occasions`, `listener_occasion_points`, `player_state`. Every reader treats a missing table as *absent data* rather than as an error, by design `[SPEC-DIR-158]`, so the master time scales `[SPEC-DIR-118]` and the occasion curves `[SPEC-DIR-130]` would have been **silently inert** — not defaulted, simply never consulted. Applying `sql/schema.sql` (all `IF NOT EXISTS`) fixed it, and `listener_settings` got its defaults row. Graceful degradation and silent inertness are the same mechanism seen from two sides. A migrated or restored database should have the current schema applied before use.

**What this established:** all 18 AcousticBrainz classifiers run locally, over any audio, from the published extractor and models, with values verified against AcousticBrainz's own output — uniform local provenance `[SPEC-FD-145]` with no accuracy penalty and no approximation, the outcome `[SPEC-FD-150]` argued for and could not previously reach. Note what was *not* required: matching AcousticBrainz. `[SPEC-FD-145]` wants **uniform provenance**, not fidelity to an external reference — the constraint is that every track be scored the same way, not that the way match the dumps.

**Route 2 was thereby promoted from fallback to recommended path — and, as shipped, to the only path.** *(Corrected 2026-08-30: this previously split the recommendation — Route 2 for the six complex characteristics, Route 3's distilled models for the eleven binaries — reasoning that Route 2 had only just reached parity there. It reproduces all 18 to within measurement noise, and `tools/extract_library.py` runs `gaia_classify` uniformly across all 71 dimensions; nothing in the production path loads a distilled model. See `[SPEC-SA-040..048]` for the settled production account, and [LOG001](LOG001-extraction-iterations.md) for why Route 3 was the right thing to try before Route 2 was known to work.)*

For the current-state conclusion, see [GUIDE003 §3](GUIDE003-feature-extraction-strategy.md#3-strategy-harvest-then-reproduce-then-approximate).
