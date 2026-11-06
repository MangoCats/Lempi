# SPEC037: Stage A — Eligibility and Frequency

**Specification — who is allowed to play, and how often**

Split from [SPEC009](SPEC009-program-director.md) on 2026-09-10, which had
reached 309 lines against `[GOV-DOC-010]`'s 300-line limit. Stage A is the
largest stage of the pipeline and the one with the most rules of its own.

> **Related:** [SPEC009](SPEC009-program-director.md) for the pipeline, whose §3 this is · [SPEC074](SPEC074-when-the-director-cannot-choose.md) for what the two fallbacks keep of it · [SPEC017](SPEC017-what-counts-as-a-play.md) for rejection windows · [SPEC057](SPEC057-holding-a-passage-back.md) for holds

---

## 3. Stage A — Eligibility & Frequency

**`[SPEC-DIR-111]` Everything that keeps a passage out, in the order the code checks it.** *(Read from `Director::weigh_all` and `frequency::weigh`, 2026-11-01.)* A radio passage that clears every row is eligible, at the weight the rest of this section computes. Each exclusion carries its reason into the census and the "why" panel.

| # | out when | for how long | rule |
| ---: | :--- | :--- | :--- |
| 1 | it is not `kind = 'radio'` | always | `[REQ-PD-120]` |
| 2 | it is held: `passages.director_hold` is set, or its file failed this player's own check | until cleared | `[SPEC-HOLD-010]`, `[GDE-INT-135]` |
| 3 | it is shorter than 30 s, longer than 3600 s, or starts more than 10,800 s into its file | always | `[SPEC-DIR-125]` |
| 4 | its recording was skipped within the skip window (156 h by default) or taken out of the queue unheard within the dequeue window (18 h); the longer remaining one is the reason | the window | `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]`, `[SPEC-PLAY-057]` |
| 5 | any credited artist — or, for a passage no credit reaches, its file's artist tag — played inside that artist's own rotation | the rotation | `[SPEC-DIR-114]`, `[REQ-AND-315]` |
| 6 | the same passage, the same recording, or a recording of the same song (work) played inside **this** passage's recording rotation | the rotation | `[SPEC-DIR-119]` |
| 7 | a related recording played inside that rotation (`recording_relations`, empty today) | the rotation | `[SPEC-DIR-116]` |
| 8 | its weight, after every multiplier below, is not above `min_weight` (0.001) | while it stays there | `[SPEC-DIR-115]` step 5, `[SPEC-DIR-137]` |

Beside Stage A, a passage already in the queue is kept out of the next pick by the Director's skip list, which is what keeps an unidentified passage from appearing twice `[REQ-AND-310]`. Two consequences of the keys: rows 4 and 6's recording and work tiers need a recording MBID, so an unidentified or tags-only passage is never suppressed by a rejection and rotates on its passage id (and its tag artist) alone `[GDE-WRK-055]`.

The weight of a passage that clears them all is, with the default `Damped` coupling `[SPEC-DIR-117]`:

```
w = recording restraint × recording ramp × related damping × length bonus × occasion
    × Π over credited artists 10^(−restraint) × the least-recovered artist's ramp
```

**`[SPEC-DIR-110]` Log-scale time encoding.** `seconds(v) = 10^v × 3600`. One float spans four orders of magnitude: `-1.0` → 6 min, `0.0` → 1 h, `2.0` → 4.2 days, `3.0` → 41 days.

**`[SPEC-DIR-115]` Artist pass, then recording pass**, multiplicatively. For each:

1. `w = 10^(-restraint)`
2. **Hard block** if `now - last_played < seconds(rotation)` → excluded entirely.
3. Otherwise **linear recovery ramp**: `w *= clamp((age - rot) / rec, 0, 1)`.
4. Recording weight multiplies its artists' weight — every credited artist, `[SPEC-DIR-114]` below. Related recordings block and damp too, scaled by relation strength.
5. Drop below `min_weight` → excluded.

**`[SPEC-DIR-114]` The artist pass runs once per credited artist, each on its own tuning and its own clock.** Built 2026-10-16, on the maintainer's direction, after lempi02w played two Prince recordings within 21 hours of a third against Prince's 26.5-hour rotation. The Director had kept one artist per recording, whichever credit sorted last by MBID: 23 of Prince's 103 radio recordings rotated as The Revolution, The Time or George Clinton on the 10-hour default, and playing them stamped nothing on Prince. 81 artists lost at least one recording that way.

1. **A play stamps every credited artist.** We Can Funk is a Prince play and a George Clinton play.
2. **Any credited artist inside its own rotation blocks.** Prince's 26.5 hours hold every recording crediting Prince, whoever else it credits; George Clinton's own 10 hours hold only what credits him.
3. **Past the blocks, the least-recovered artist sets the ramp**, as the freshest identity tier does in `[SPEC-DIR-119]`, and **every credit's restraint multiplies in**, so turning a co-artist down quiets the shared recording too.
4. Queueing marks every credit at once `[REQ-PD-112]`, and a programme takes one seed per artist counting every credit of each seed `[SPEC-DIR-140]`.

**`[SPEC-DIR-113]` The artist link opens a preference panel for every credited artist.** Prince + The Time are two subjects with two rotations, so clicking the artist on any surface opens Prince's panel and The Time's, each with its own play-frequency table `[REQ-VIS-300]` and its own Save. The clicked artist's panel opens at once; the others follow from `GET /credits/:mbid`, in display order.

**`[SPEC-DIR-112]` A passage is its top recording, in this generation.** A passage may name several recordings — a medley, a segue — as a weighted set `[ENT-RECORDING-010]`. The Director reads only the highest-weighted one (ties by MBID): its tuning, its rotation and recovery, its works, its relations, its occasions, its flavor, and the key a play is stamped under. The others are not stamped when it plays, and their own plays hold it out only through a work they share with the top one `[SPEC-DIR-119]`. Kept simple deliberately (the maintainer, 2026-11-01); a later generation may weigh the whole set.

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

Both are pinned by test so neither can drift into the other. `GateOnly` was kept to *measure* how far Lempi departs from six years of observed behaviour — diagnostic, never a listening mode and never a gate. **Nothing selects it today**: the measurement below was taken once, and no tool exposes the coupling to repeat it. It is a `Policy` field a future diagnostic could set.

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

**`[SPEC-DIR-125]` Passage-level filters and length bonus.** `radio` passages only `[REQ-PD-120]`. Reject shorter than 30 s, longer than 3600 s, or starting more than 10800 s into a file. Then `w *= sqrt(min(4.0, 180 s / length))`. **This favours short passages, not three-minute ones**: 180 s is where it is neutral, not where it peaks. It rises to its cap of ×2 at 45 s and below, and falls for anything longer — ×0.71 at 6 minutes, ×0.5 at 12, ×0.22 at the 60-minute limit. MuLibPlay's own formula, transcribed; in practice it is a penalty on long passages more than a reward for short ones, since only 30–180 s passages gain at all.

**`[SPEC-DIR-130]` Occasion multiplier — a time layer, not a flavor dimension.** User-defined characteristics `[GDE-MCR-060]` supply the *value*; a seasonal curve turns it into a multiplier:

```
w *= 1 + characteristic_value × (curve(today) − 1)
```

So `user.christmas.christmasy = 0.9` on 21 December, with the curve at ×2.73, yields ×2.56; the same passage in June, with the curve at ×0.000001, yields ×0.10. This keeps MuLibPlay's proven seasonal behaviour `[GDE-PD-020]` while removing the hardcoded `[C]`/`[W]`/`[S]`/`[K]` tags, and — critically — stays **legible as a single term** in the Why-this-passage panel. Folding seasonality into the programme target vector was rejected for exactly that reason.

Curves are data, not code: a new occasion is a new characteristic plus a curve, with no edit to the engine.

**`[SPEC-DIR-132]` Curve representation.** A curve is control points around a **wrapped** year — January follows December — plus an interpolation mode:

| Mode | Behaviour | Use |
| :--- | :--- | :--- |
| `step` | hold the previous point's value | month-granular curves, as MuLibPlay's `[W]`/`[S]`/`[K]` were |
| `linear` | interpolate in **log** space | smooth curves, as `[C]` effectively was |

Interpolation is logarithmic because these are *ratios*: halfway between ×0.5 and ×2.0 is ×1.0, not ×1.25, and a linear blend of 0.000001 and 10 would sit near 5 for half the gap. Between neighbouring points `a` (on day `d₀`) and `b` (on `d₁`), the curve on day `d` is

```
curve(d) = exp( ln a + t·(ln b − ln a) ) = a^(1−t) · b^t,   t = (d − d₀) / (d₁ − d₀), counted around the year
```

so halfway between ×0.000001 and ×10 is ×0.0032, and ×1 is reached a seventh of the way from ×10 down to ×0.000001.

**`[SPEC-DIR-131]` No point is below ×0.000001** — MuLibPlay's own "never". A logarithm of 0 is not a number, and a blend through one would make the weight not a number too. `tools/load_occasions.py` refuses such a point before writing, a schema created since 2026-11-01 refuses it (`CHECK (multiplier >= 0.000001)`), and the Director raises any older row up to the floor, and drops one that is not a number, saying so in its log.

Leap years are deliberately ignored — 29 February shares an ordinal with 1 March. A season is not accurate to the day, and honouring it would shift every curve by a day in three years out of four.

The multiplier is clamped at zero. A characteristic value above 1.0 against a curve below 1.0 would otherwise drive it negative and *invert* the weight; "never right now" is the strongest thing a season may say.

**`[SPEC-DIR-133]` Which day, which value, how several combine.** "Today" is the **listener's local date at the pick's projected start** `[SPEC-DIR-182]` `[SPEC-DIR-186]` — a season turns at local midnight, as a programme slot starts at local time; until 2026-11-01 it turned at UTC midnight, on the day the pick was chosen. Every age in this section is measured to the same moment. A recording's value for an occasion is its `flavor` row, replaced — not averaged — by the listener's own hand-set value where one exists `[SPEC-PREF-085]`. A recording carrying several occasions takes each one's multiplier, and they multiply. The same values also take part in flavor distance as characteristics, which moves shaping and never a weight `[SPEC-FD-112]`.

**`[SPEC-DIR-134]` The inherited four are data, and the data already exists.** MuLibPlay's `[C]`, `[W]`, `[S]`, `[K]` migrate to `user.christmas`, `user.winter`, `user.summer`, `user.childrens` — already present in the migrated library as **binary characteristics**, `christmasy` paired with `not_christmasy`. The curve attaches to the positive class; the negative class carries no curve and is ignored.

Verified on the migrated library: with the Christmas curve loaded, 81 christmasy recordings (82 radio passages) drop below `min_weight` out of season, and the rest of the pool is untouched. A partial characteristic value damps rather than excludes, which is the point of scaling by value rather than testing a tag.

Note that `[K]` was never seasonal at all — a flat ×0.000001 on 140 children's recordings. It expresses fine as a single-point curve, which is a fair test of whether "curves are data" actually holds.

**`[SPEC-DIR-136]` Loaded 2026-08-13** by `tools/load_occasions.py`, transcribed from `occasionWeight()` in the inherited `musicdirector.cpp`. As stored on the hub, read 2026-11-01 — a `step` curve holds each point's value until the next point, a `linear` one blends between them in log space:

| occasion | class | interp | points | over the year | reach, radio passages |
| :--- | :--- | :--- | ---: | :--- | ---: |
| `user.christmas` | `christmasy` | linear | 11 | ×0.000001 from 1 Jan to 31 Oct; ×0.099 on 1 Nov, ×1 on 30 Nov, ×5 on 24 Dec, **×10 on 25 Dec**, ×1 on 26 Dec, ×0.17 on 31 Dec | 118 |
| `user.winter` | `wintry` | step | 6 | ×1.5 Jan, ×1 Feb, ×0.25 Mar, **×0.000001 Apr–Oct**, ×0.5 Nov, **×2 Dec** | 3 |
| `user.summer` | `summery` | step | 6 | ×0.2 Jan–Apr, ×0.5 May, **×2 Jun**, ×1.5 Jul, ×1 Aug, ×0.2 Sep–Dec | 1 |
| `user.childrens` | `for_children` | step | 1 | **×0.000001 all year** | 151 |
| `user.profanity` | `profane` | step | 1 | ×1 all year: registered, and inert until a value is chosen | 84 |
| `user.spiritual` | `spiritual` | step | 1 | ×1 all year: registered, and inert | 5 |

At ×0.000001 a recording carrying the full value falls under `min_weight` and is **excluded**, not damped `[SPEC-DIR-137]`: christmasy ones outside November and December, wintry ones from April to October, children's all year. Reach counts radio passages whose top recording `[SPEC-DIR-112]` carries a value above 0, the listener's own included `[SPEC-PREF-085]`.

Measured effect in August 2026: eligible passages fell **8,038 → 7,851**, with 187 more dropping under `min_weight` — the christmas, children's and wintry passages, out of season. Reach then was 41, 149 and 2. The mechanism had been complete and inert since it was written; the library already carried the characteristic values from six years of MuLibPlay tagging, so loading the curves is what made that tagging act again.

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

> **The children's weight deserves a decision rather than a default.** MuLibPlay's shipped `kidSongWeight` is 0.000001, which is not a de-emphasis but an effective ban, and here it removes **150 radio passages — 1.4% of the library, read 2026-11-01; 149 and 1.8% in August — permanently, in every season**. The 151st of `[SPEC-DIR-136]`'s reach carries a hand-set 0.2, and is damped ×0.8 rather than removed. It is transcribed faithfully because that is what ran for six years, but it is a parameter: `--kids 0.5` merely damps them, and the value is a row rather than a constant.

---

**Traceability:** `[SPEC-DIR-110..137]` · §3 of [SPEC009](SPEC009-program-director.md) · builds on `[REQ-PD-120]`, `[SPEC-HOLD-010]`, `[GDE-INT-135]`, `[SPEC-PLAY-050]`, `[SPEC-PREF-085]`, `[REQ-AND-315]`, `[GDE-WRK-035]`
