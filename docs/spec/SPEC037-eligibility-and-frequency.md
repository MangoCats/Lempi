# SPEC037: Stage A — Eligibility and Frequency

**Specification — who is allowed to play, and how often**

Split from [SPEC009](SPEC009-program-director.md) on 2026-09-10, which had
reached 309 lines against `[GOV-DOC-010]`'s 300-line limit. Stage A is the
largest stage of the pipeline and the one with the most rules of its own.

> **Related:** [SPEC009](SPEC009-program-director.md) for the pipeline

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §3 in [SPEC037](SPEC037-eligibility-and-frequency.md), the rest in [SPEC009](SPEC009-program-director.md).

## 3. Stage A — Eligibility & Frequency

**`[SPEC-DIR-110]` Log-scale time encoding.** `seconds(v) = 10^v × 3600`. One float spans four orders of magnitude: `-1.0` → 6 min, `0.0` → 1 h, `2.0` → 4.2 days, `3.0` → 41 days.

**`[SPEC-DIR-115]` Artist pass, then recording pass**, multiplicatively. For each:

1. `w = 10^(-restraint)`
2. **Hard block** if `now - last_played < seconds(rotation)` → excluded entirely.
3. Otherwise **linear recovery ramp**: `w *= clamp((age - rot) / rec, 0, 1)`.
4. Recording weight multiplies its artist's weight. Related recordings block and damp too, scaled by relation strength.
5. Drop below `min_weight` → excluded.

**`[SPEC-DIR-119]` `last_played` in step 2 is the freshest of three identity tiers, not the recording's alone.** Built 2026-09-12, superseding `[SPEC-DIR-116]`'s graded relation for the same-song case — see [GUIDE012](../GUIDE012-work-based-song-blocking.md) `[GDE-WRK-035]` for the measurements and the decision.

A play stamps every key the passage has: its `passage_id`, its recording MBID if it has one, and every work MBID that recording performs. The recording pass then reads the **most recent** stamp across the keys *this* passage has, because a wider key can never be staler than a narrower one:

| tier | key | reaches |
| :--- | :--- | :--- |
| passage | `passage_id` | this passage only — the sole tier an unidentified passage has |
| recording | `passage_recordings.mbid` | other passages of the same recording |
| **work** | `recording_works.work_mbid` | **other recordings of the same song, any artist** |

Three things follow, and each is pinned by test:

1. **The window is the candidate's own.** A passage is weighed from its own side against its own `rotation`/`recovery`, so one play of a shared song holds three recordings of it for three different periods. Nothing is stored per pair, and nothing is directional.
2. **Covers block.** A shared work is a shared work: hearing David Bowie's "Across the Universe" holds The Beatles' and Rufus Wainwright's. Measured at 88 work classes over 200 passages before the rule was adopted, against a mean blast radius of 1.25 passages per play over a pool of 8,330.
3. **Relation attributes are not read.** `live`, `cover`, `partial`, `instrumental` and `medley` are all recorded and none is consulted, so a medley containing a song blocks the song `[GDE-WRK-052]`.

A catalogue with no `recording_works` keeps the pre-2026-09-12 behaviour, and the Director says so on load rather than looking like a library that merely has no works.

**`[SPEC-DIR-116]` Related recordings share a rotation, and each is judged on its own age.** A live take, a remaster and the compilation appearance are the same song to a listener; hearing one should suppress the others.

> **Superseded for same-song blocking by `[SPEC-DIR-119]`, 2026-09-12.** `recording_relations` was never filled — it held 0 rows for the life of the mechanism, which is how the incident in [GUIDE012](../GUIDE012-work-based-song-blocking.md) §1 happened — and identity on a work MBID does the job without a pairwise junction. The code below stays live and the table stays in the schema, because a graded relation is still the only way to express "related, but weaker", which identity cannot. Everything from here to the end of this rule is retained as the record of what it was for.

MuLibPlay intended this and never achieved it, in two independent ways:

```cpp
QMap<qint32,qreal> relTrk = de.relatedTracks( trackId );
foreach ( qint32 tid, relTrk )                       // iterates VALUES, not keys
  if ( tracksMostRecentPlay.contains( tid ) )        // so tid is a truncated strength
```

Qt's `foreach` over a `QMap` yields values, so `tid` was the relation strength cast to `qint32` — 0 or 1 — and the lookup asked for the play time of track 0. Nothing matched. Even had it matched, the damping call passed `now - tracksMostRecentPlay.value(trackId)`, the *primary* track's age, so a relation would have been judged by the wrong recording's history.

Lempi's rules:

1. **Block** if any related recording played within this recording's rotation window. Strength does not scale the block — sharing a rotation is the point of relating two recordings.
2. **Damp** by each related recording's own ramp, using **its own age**, over a recovery window scaled by relation strength. A weak relation therefore recovers sooner.
3. **Every** relation applies, multiplicatively. Three half-recovered relations yield 0.125, not 0.5.

**`[SPEC-DIR-118]` Master time scales.** One multiplier for artists and one for recordings, over every block and ramp *duration*. Floating point, four decimal places, range 0.0001–100.0000, default 1.0000 — at which they are exactly inert.

They exist because per-subject tuning is log-scale `[SPEC-DIR-110]`. "Everything a bit sooner" is a reasonable thing to want and is otherwise inexpressible without editing thousands of rows. At 0.5 every block and ramp is half as long; at 2.0, twice.

They scale **durations, never weights**. That is what keeps frequency and character orthogonal `[SPEC-DIR-100]`: a scale changes *when* a passage becomes eligible, never *how much* it is wanted, and it remains a single legible term in the panel. The two are independent — a recording scale must not move an artist block — and the recording scale reaches related recordings, which share the recording's windows. Stored in `listener_settings` with the range enforced in the schema as well as in code, since an out-of-range stored value would quietly change selection everywhere.

**`[SPEC-DIR-117]` The artist weight never reaches the recording weight in MuLibPlay as shipped.** Step 4 above describes the intent. The code does something else, and the difference was found only by transcribing it:

```cpp
qreal weight = pow(10.0,-restraint);          // outer
if ( eligibleArtists.contains( artistId ) )
  { weight *= eligibleArtists.value( artistId );   // writes the OUTER weight
    qreal restraint = ...;
    qreal weight = pow(10.0,-restraint);           // SHADOWS it; outer never read again
```

The inner declaration shadows the outer, so the multiplication by the artist weight is dead. `eligibleTracks.insert(trackId, weight)` sits inside that inner block — MuLibPlay's own variable names, quoted rather than translated. **An artist rotation block still excludes the recording — that gate is a map lookup, unaffected — but a partially recovered artist does not damp its recordings at all.** The artist recovery ramp has, in six years of production, done nothing.

**Resolved: Lempi implements the ramp.** MuLibPlay is a proven baseline, not a ceiling — six years of satisfactory listening shows the *design* is sound, which is not the same as showing every behaviour of the binary is worth keeping. Artist recovery exists so that hearing one recording by an artist gently damps the rest until it recovers; that is the intent, and it is better than what ran.

The `ArtistCoupling` enum is named for behaviour rather than provenance, because the choice is now Lempi's:

| | Effect | Purpose |
| :--- | :--- | :--- |
| **`Damped`** (default) | artist ramp multiplies into the recording weight | Lempi's behaviour |
| `GateOnly` | artist can block, never damps | measuring divergence only `[REQ-PD-110]` |

Both are pinned by test so neither can drift into the other. `GateOnly` is retained to *measure* how far Lempi departs from six years of observed behaviour — diagnostic, never a listening mode and never a gate.

**Measured divergence.** Against the migrated library — 8,079 radio passages, 37,134 plays, 428 artists with history of which 329 carry tuned preferences — evaluated at the instant the play history ends:

| | |
| :--- | ---: |
| eligible under `GateOnly` | 2,421 |
| weight changed by the artist ramp | **1,897 (78.4%)** |
| median damping factor | **0.179** (≈5.6× suppression) |
| damping range | 0.0025 – 0.9985 |
| newly excluded below `min_weight` | 69 (2.9%) |

This is not a marginal correction. On four passages in five the corrected ramp changes the weight, and at the median it suppresses a recently-heard artist more than five-fold — which is precisely the artist spacing the mechanism was designed to provide and has never delivered.

Two cautions on the number. It is a **single-instant snapshot at the end of the history**, the most crowded possible moment, so treat 78.4% as an upper region rather than a steady-state rate. And the same shadowing suppressed artist **restraint** as well as the ramp — separated here, restraint independently affects the same 1,897 passages with a median of 1.0023 but a range of 0.0625–4.4978, so the "much more / never again" knob has been inert at the artist level too. Both are restored by the same fix.

Two consequences to watch:

1. **The artist ramp is now load-bearing, so its defaults are too.** Artist rotation 1.0 and recovery 1.0 `[SPEC-DIR-120]` mean an artist blocks for 10 hours and then damps across the following 10. That second window has never had any effect and has therefore never been tuned by anyone. Treat the artist defaults as unvalidated until observed.
2. **Damping can now push a weight under `min_weight`**, excluding a passage early in the artist's recovery where it would previously have been eligible at full weight. This slightly extends the effective block. It is a consequence of the fix, not a separate decision.

**Resolved by `[SPEC-DIR-116]`:** the same block originally contained a second instance of the pattern — related-recording recovery damping passed the *primary* recording's age rather than each related recording's own. Related recordings are now modelled in Stage A (`player/core/src/director/frequency.rs`'s `Related` handling, `player/core/src/director/library.rs` loading `recording_relations`), and the damping uses each relation's own age, not the primary's — see `[SPEC-DIR-116]` above.

**`[SPEC-DIR-120]` Defaults matter more than they look.** Only 2,918 of 8,116 MuLibPlay tracks (36%) ever received tuned values `[GDE-BMK-020]`, so most selection runs on defaults:

| | default | = | observed tuned median |
| :--- | ---: | ---: | ---: |
| recording rotation | 2.0 | 4.2 days | 2.196 (6.5 days) |
| recording recovery | 2.6 | 16.6 days | 2.722 (22 days) |
| artist rotation | 1.0 | 10 hours | 1.231 (17 hours) |
| artist recovery | 1.0 | 10 hours | 1.595 (39 hours) |
| restraint | 0.0 | ×1.0 | 0.000 |

Tuned medians sit close to the defaults, which is evidence the defaults are well chosen — users nudged rather than fought them. Restraint spans −0.939 to 5.0, i.e. an 8.7× boost to a 10⁻⁵ suppression: it is the "much more / never again" knob.

**`[SPEC-DIR-125]` Passage-level filters and length bonus.** `radio` passages only `[REQ-PD-120]`. Reject shorter than 30 s, longer than 3600 s, or starting more than 10800 s into a file. Then `w *= sqrt(min(4.0, 180 s / length))` — a mild preference for ~3-minute passages, capped at 2× for short ones.

**`[SPEC-DIR-130]` Occasion multiplier — a time layer, not a flavor dimension.** User-defined characteristics `[GDE-MCR-060]` supply the *value*; a seasonal curve turns it into a multiplier:

```
w *= 1 + characteristic_value × (curve(today) − 1)
```

So `user.christmas.christmasy = 0.9` on 21 December with a curve value of 4.2 yields ×3.9; the same passage in June yields ≈×1. This keeps MuLibPlay's proven seasonal behaviour `[GDE-PD-020]` while removing the hardcoded `[C]`/`[W]`/`[S]`/`[K]` tags, and — critically — stays **legible as a single term** in the Why-this-passage panel. Folding seasonality into the programme target vector was rejected for exactly that reason.

Curves are data, not code: a new occasion is a new characteristic plus a curve, with no edit to the engine.

**`[SPEC-DIR-132]` Curve representation.** A curve is control points around a **wrapped** year — January follows December — plus an interpolation mode:

| Mode | Behaviour | Use |
| :--- | :--- | :--- |
| `step` | hold the previous point's value | month-granular curves, as MuLibPlay's `[W]`/`[S]`/`[K]` were |
| `linear` | interpolate in **log** space | smooth curves, as `[C]` effectively was |

Interpolation is logarithmic because these are *ratios*: halfway between ×0.5 and ×2.0 is ×1.0, not ×1.25, and a linear blend of 0.000001 and 10 would sit near 5 for half the gap.

Leap years are deliberately ignored — 29 February shares an ordinal with 1 March. A season is not accurate to the day, and honouring it would shift every curve by a day in three years out of four.

The multiplier is clamped at zero. A characteristic value above 1.0 against a curve below 1.0 would otherwise drive it negative and *invert* the weight; "never right now" is the strongest thing a season may say.

**`[SPEC-DIR-134]` The inherited four are data, and the data already exists.** MuLibPlay's `[C]`, `[W]`, `[S]`, `[K]` migrate to `user.christmas`, `user.winter`, `user.summer`, `user.childrens` — already present in the migrated library as **binary characteristics**, `christmasy` paired with `not_christmasy`. The curve attaches to the positive class; the negative class carries no curve and is ignored.

Verified on the migrated library: with the Christmas curve loaded, 81 christmasy recordings (82 radio passages) drop below `min_weight` out of season, and the rest of the pool is untouched. A partial characteristic value damps rather than excludes, which is the point of scaling by value rather than testing a tag.

Note that `[K]` was never seasonal at all — a flat ×0.000001 on 140 children's recordings. It expresses fine as a single-point curve, which is a fair test of whether "curves are data" actually holds.

**`[SPEC-DIR-136]` Loaded 2026-08-13** by `tools/load_occasions.py`, transcribed from `occasionWeight()` in the inherited `musicdirector.cpp`:

| occasion | class | interp | points | peak | reach |
| :--- | :--- | :--- | ---: | :--- | ---: |
| `user.christmas` | `christmasy` | linear | 11 | **×10 on 25 Dec** | 41 passages |
| `user.winter` | `wintry` | step | 6 | ×2 in December | 2 |
| `user.summer` | `summery` | step | 6 | ×2 in June | 1 |
| `user.childrens` | `for_children` | step | 1 | ×0.000001 all year | **149** |

Measured effect in August: eligible passages fall **8,038 → 7,851**, with 187 more dropping under `min_weight` — the christmas and children's passages, suppressed out of season. The mechanism had been complete and inert since it was written; the library already carried the characteristic values from six years of MuLibPlay tagging, so loading the curves is what made that tagging act again.

> **`[SPEC-DIR-137]` The children's weight interacts with `min_weight`, and 0.000001 means *never*, not *rarely*.** A children's passage weighs `average × multiplier`; at the library's average of 1.214 that is 0.0000012, which falls **below `min_weight` (0.001)** and is therefore *excluded entirely* rather than made unlikely. There is a cliff at multiplier ≈ 0.00083.
>
> | kids multiplier | passage weight | eligible? | plays/year @ 60 passages/day |
> | ---: | ---: | :--- | ---: |
> | 0.000001 *(MuLibPlay's)* | 0.0000012 | **no** | **never** |
> | 0.001 | 0.0012 | yes | 0.42 |
> | **0.0024** | 0.0029 | yes | **1.00** |
> | 0.005 | 0.0061 | yes | 2.08 |
>
> **MuLibPlay used the same `kidSongWeight` and the same `minWeightLimit`, so children's songs never played there either.** The stated intent — "extremely rarely, one per year or less" — is satisfied by the *or less*, and the six years of observed behaviour were the *never* end of it. Retained deliberately at 0.000001; `--kids 0.0024` is the value that would make it genuinely once-a-year instead.
>
> The general lesson: an occasion multiplier small enough to look like suppression can cross `min_weight` and become exclusion, and the two are not the same thing — an excluded passage cannot be surfaced by any amount of listening.

> **The children's weight deserves a decision rather than a default.** MuLibPlay's shipped `kidSongWeight` is 0.000001, which is not a de-emphasis but an effective ban, and here it removes **149 radio passages — 1.8% of the library — permanently, in every season**. It is transcribed faithfully because that is what ran for six years, but it is a parameter: `--kids 0.5` merely damps them, and the value is a row rather than a constant.

---

