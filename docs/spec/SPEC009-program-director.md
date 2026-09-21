# SPEC009: Program Director

**Design Specification — Tier 2**

How Lempi chooses the next passage. Reproduces MuLibPlay's six-years-proven selection algorithm `[GDE-PD-010..050]`, extended with Like/Dislike Taste and the full 71-dimension flavor vector.

> **Related:** [REQ002 §2](REQ002-functional-requirements.md#2-program-director--pd) · [SPEC005 Flavor Distance](SPEC005-flavor-distance.md) · [SPEC008 Schema](SPEC008-database-schema.md) · [SPEC023 Domain Vocabulary](SPEC023-domain-vocabulary.md) — rotation, weighting and flavor below score **recordings** (occasionally passages, where noted); the panel that shows this decomposition is "Why this passage?" `[REQ-VIS-100]`, not "track" · inherited [MCR-SPEC005](../inherited/mcrhythm/MCR-SPEC005-program_director.md), [MCR-SPEC006 Like/Dislike](../inherited/mcrhythm/MCR-SPEC006-like_dislike.md)

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §3 in [SPEC037](SPEC037-eligibility-and-frequency.md), the rest in [SPEC009](SPEC009-program-director.md).

## 1. The Governing Idea

**`[SPEC-DIR-100]` Two orthogonal mechanisms, kept orthogonal.** This is why MuLibPlay works, and the single most important thing not to break:

| Mechanism | Answers | Inputs |
| :--- | :--- | :--- |
| **Frequency** | *How often may this play?* | rotation, recovery, restraint, play history, occasion |
| **Character** | *Does this fit right now?* | programme seeds, Taste, flavor distance |

They never mix. Frequency produces a weight; character shapes and orders a pool. Conflating them was rejected explicitly `[GDE-OPN-030]`: a taste-match factor folded into the weight product would make "I like this" and "play this often" indistinguishable, and would make the weight undecomposable for `[REQ-VIS-100]`.

**`[SPEC-DIR-105]` Randomness is applied last**, over an already-shaped pool `[GDE-PD-050]`. Character comes from the shaping; surprise comes from the roulette.

---

## 2. Pipeline

```
 all radio passages
   │
   ├─ A. eligibility & frequency ...... weight per passage, or excluded
   ├─ B. pool shaping ................. prune + gather against seeds & Taste
   ├─ C. flow ordering ................ re-sort by similarity to queue tail
   └─ D. weighted roulette ............ rank decay, then weighted random pick
```

---

> **Split on 2026-09-10.** Stage A is now
> [SPEC037](SPEC037-eligibility-and-frequency.md).

**`[SPEC-DIR-225]` Before the Director exists, the queue is what it left behind.** *(Built 2026-09-11.)* `Director::load` runs on its own thread so startup is not blocked by it `[IMPL-SUI-070]` — 9.86 s on the appliance `[IMPL-SUI-075]`, measured at **14.6 s** on lempipi's current library. `Session::prime` runs at 99 ms. For the gap between them the session has no Director, and `refill` falls through to `Library::random_radio`: a uniform draw over every radio passage, with no weight, no occasion multiplier and no `min_weight` test.

That degradation was specified for a **live rebuild**, where the queue is already full and covers the build 18× over, so it cannot fire. At **startup** the queue is empty and it fills every slot — the one case where the fallback is guaranteed rather than unreachable.

Measured on lempipi over 2026-08-14 → 09-11: **144 of 675** attributed plays (21%) carry no `selection_decisions` row, against 51 of 1,591 (3.2%) on bose, which is redeployed far less. All 108 `auto` plays in that window are undecided ones — `selected_by = "auto"` was in practice "the fallback chose this", not "the Director chose this with no programme in force". Twelve service starts over two days × five primed slots predicts ~60 unweighted selections; **62** were observed. At 149 children's passages in 8,330 (1.8%) that is the route by which music `[SPEC-DIR-134]` excludes from every weighted selection reached the speakers anyway.

So the queue is written to `player_queue` whenever it changes and restored by `prime` **before** the opening refill. Passage ids and their order, and nothing else: an entry is rebuilt from its id by `Library::passage`, the same way `hand_over_seamless` rebuilds one on arrival rather than carrying it `[SPEC-BK-030]`. Restored entries keep `selected_by = None`, which already means "no selection event to report at all — a resumed or reconstructed entry". The fallback is not removed; it is no longer *reached*, because there is no shortfall left for it to fill.
## 4. Stage B — Pool Shaping

**`[SPEC-DIR-140]` Seeds define the target.** A programme is a list of 6–8 exemplar passages `[GDE-PD-040]`, down-selected to at most `max_seeds` — one per artist, least-recently-played. Naming songs beats tuning sliders, and it is the mechanism MuLibPlay users actually exercised.

**`[SPEC-DIR-145]` Prune, then gather** — both over flavor distance `[SPEC-FD-040]`:

1. **Prune** — remove passages *most unlike* every seed until the pool reaches `excl_pool`.
2. **Gather** — take the `rand_pool × 2 / seeds` passages *most like* each seed.

**`[SPEC-DIR-150]` Taste enters here, and only here** `[GDE-OPN-030]`. Like-Taste and Dislike-Taste are weighted centroids of the flavor of liked/disliked songs `[MTA-SMPL-020]`, computed per user.

- **Dislike-Taste acts as an exclusion filter**: passages within `dislike_radius` of it are removed from the pool before gathering. This is McRhythm's own suggested use `[LD-LIKE-021]`, and it is the half that needs no tuning to be useful.
- **Like-Taste acts as an additional seed**, weighted `like_seed_weight` relative to programme seeds.

> **Taste is implemented but unexercised.** `listener_likes` is empty in the migrated library, so both halves have unit tests and no field data behind them. `dislike_radius` and `like_seed_weight` remain **new and unvalidated** `[SPEC-DIR-195]` — there is nothing to tune them against until the listener records a Like.

Rationale: Taste is a *character* signal, so it belongs in the stage that shapes character, leaving frequency untouched `[SPEC-DIR-100]`. Treating a Like as "just another way of naming a song that defines a mood" also keeps it in the same idiom as programmes.

**`[SPEC-DIR-157]` Implemented 2026-08-10, and it is visible in what plays.** The same library at three times of day, six passages each:

| Programme | Queue | Mean length |
| :--- | :--- | ---: |
| **Prog** 19:00 | Steely Dan *Aja*, Traffic, Rush *Jacob's Ladder*, Led Zeppelin, Genesis *Squonk* | ~440 s |
| **Groove** 15:00 | Genesis *Los Endos*, Fatboy Slim, U2, Paula Abdul (dance mix), Massive Attack | ~270 s |
| **Light** 10:00 | Genesis *Invisible Touch*, Beatles *Lovely Rita*, Tom Petty, Heart | ~200 s |

Genesis appears in all three, and a *different* Genesis each time — *Squonk* and *Los Endos* for Prog and Groove, *Invisible Touch* for Light. That is the property the whole metric exists for: similarity by sound rather than by artist. Track length was never an input to shaping; it separates because prog is long and pop is short.

Three implementation decisions worth keeping:

- **"Most unlike every seed" is distance to the *nearest* seed.** A programme is a handful of exemplars, not one centre, so a passage close to any one seed belongs even if far from the rest.
- **Gathering is per seed, not a global top-N.** A global list would let one seed in a dense region supply the whole pool and silently drop the rest of the programme.
- **A passage with no flavor is kept, not dropped.** Unmeasured is not unsuitable; excluding them would make a half-scanned library play only the half it had scanned.

**`[SPEC-DIR-155]` Taste never blocks and never boosts frequency.** A disliked passage is removed from the *pool*; its rotation and restraint are unchanged. If the user later removes the Dislike, behaviour returns exactly to baseline with no residue.

**`[SPEC-DIR-158]` Cold start.** With no history, no Likes and no tuned preferences, Stage A degrades to `10^0 = 1.0` for everything and Stage B has no seeds. Selection then reduces to uniform random over eligible passages — correct, if uninspiring. A programme with seeds is the minimum for interesting behaviour, so first-run setup asks for a handful of exemplars rather than presenting an empty station.

---

## 5. Stages C & D — Flow and Roulette

**`[SPEC-DIR-160]` Flow.** Re-sort the pool by flavor distance to the **last passage already queued**, so consecutive passages blend `[GDE-PD-050]`. This is also what makes a hard programme switch acceptable `[SPEC-DIR-180]`: continuity is supplied here, not by blending programmes.

> **Future direction `[SPEC-FD-170]`:** flow currently matches whole-passage flavor to whole-passage flavor, but a handover is heard as the *end* of one passage against the *start* of the next. Characterising the first and last three minutes separately, and matching `exit → entry`, models the transition rather than the pairing. Not the current target — it roughly triples extraction cost and needs a segment discriminator in `flavor`.

**`[SPEC-DIR-165]` Roulette.** Take the top `rand_pool`, apply rank decay `w *= decay^rank`, then pick weighted-random. Selection is by weight, not by rank — a lower-ranked passage can win, which is where the surprise lives.

**`[SPEC-DIR-167]` Both implemented 2026-08-10, and both measurable.**

*Flow works.* Across eight consecutive selections on the real library, mean distance between a passage and the one it followed was **0.432**, against a library median of 1.052 `[SPEC-FD-085]` — consecutive passages are **59% closer than random pairs**. That is the whole claim of `[SPEC-DIR-160]`, and it is the reason a hard programme switch is tolerable.

*The roulette stays a roulette.* Winning ranks across those eight: **5, 10, 17, 24, 31, 50, 61, 99**. Decay favours the low end without making it certain — one passage won from rank 99 at a roulette weight of 0.006. A director that always returned rank 0 would be evidence of a bug, not of good taste.

Two decisions:

- **Rank decay applies only when there is a flow order.** With nothing queued to follow, rank is whatever order the scan happened to visit, and decaying by it would silently favour the first passage examined. The first pick of a session therefore uses undecayed weights, and the record says so.
- **Runners-up are ranked by *decayed* weight.** "Why not something else?" is a question about what nearly won the roulette, which is the decayed figure, not the frequency weight.

---

## 6. Programme Selection

**`[SPEC-DIR-180]` Hard switch at the programme's start time**, as MuLibPlay does. Eight programmes are defined by start time `[GDE-PD-040]`; the active one is whichever most recently started. Blending was rejected: six years of production show no complaint, the flow stage already smooths transitions `[SPEC-DIR-160]`, and blending introduces a tunable nobody asked for while making "which programme am I in?" ambiguous. Start times are the listener's own local clock, via `listener_settings.utc_offset_minutes` — synced from the OS rather than left at its unconfigured default, since that default is UTC and most listeners are not `[REQ-VIS-255]`.

**`[SPEC-DIR-185]`** Manual programme selection overrides time-of-day until the user reverts to automatic. `POST /program/:id` accepts the literal id `"auto"` as a real, parameterless revert: it clears `manual_program` and lets the time-of-day schedule resume, and the settings panel offers it as an ordinary "Automatic (by time of day)" option alongside the named programmes `[REQ-VIS-255]`. **Persisted since 2026-09-08** — a manual choice is written to `player_settings` (`PlayerStore::save_manual_program`) and restored by `Session::open`, so it survives a restart instead of silently reverting to time-of-day every power cycle; `auto` clears the stored row rather than writing an empty value, so "never chosen" and "explicitly reverted" both read back as `None`. Motivated by `lempiplay3` (`[SPEC036]`): a vehicle appliance with no realtime clock and no relationship between a truck's driving hours and a schedule tuned for home listening, so its own UI's programme buttons never send `auto` at all — every other installation's behavior is unchanged, since nothing here affects an installation where no one has ever chosen a programme by hand.

---

## 7. Visibility Contract

**`[SPEC-DIR-190]`** Every automatic selection writes a `selection_decisions` record `[SPEC-SC-100]` sufficient to reconstruct the choice `[REQ-VIS-100]`: artist weight and block state, recording weight and ramp position, occasion multiplier, length bonus, final Stage-A weight, distance to each seed, flow distance, rank, roulette position and target — **and the runners-up that lost, with their weights**. The occasion multiplier is currently recorded as the combined scalar only, not broken out by the characteristic/curve value that produced it; Taste's effect is likewise not yet a distinct field — it is folded into `seed_distances` undifferentiated from programme seeds. Both are recordable extensions of `Weighing`/`Explanation`, not yet built.

The orthogonality of `[SPEC-DIR-100]` is what makes this legible: the panel shows *how often* and *does it fit* as two separate stories rather than one opaque product.

---

## 8. Parameters

**`[SPEC-DIR-195]`** Provenance matters here — some values are proven, others are inherited guesses:

| Parameter | MuLibPlay | Status |
| :--- | ---: | :--- |
| `min_weight` | 0.001 | Proven |
| `max_seeds` | 5 | Proven |
| min / max length, max depth | 30 s / 3600 s / 10800 s | Proven |
| length bonus midpoint, cap | 180 s, 4.0 | Proven |
| `excl_pool` | 1000 | **Verified** `[SPEC-DIR-205]` |
| `rand_pool` | 100 | **Verified** |
| rank `decay` | 0.96 | **Verified** |
| `dislike_radius`, `like_seed_weight` | — | **New, unvalidated** |

**`[SPEC-DIR-205]` Re-derived 2026-08-13 — and the answer is to keep them.** Measured by `tools/pool_params.py` over the fully extracted library, comparing where each pool boundary falls in distance terms under both metrics:

| | rank 10 | rank 200 (*gather*) | rank 1000 (*excl_pool*) | median nearest seed |
| :--- | ---: | ---: | ---: | ---: |
| local 18 — *Cool* | 0.295 | 0.415 | **0.515** | 0.689 |
| inherited 11 — *Cool* | 0.219 | 0.377 | **0.502** | 0.692 |
| local 18 — *Mellow* | 0.313 | 0.451 | **0.554** | 0.730 |
| inherited 11 — *Mellow* | 0.167 | 0.282 | **0.454** | 0.766 |

**The boundaries land in the same place.** `excl_pool = 1000` cuts at a normalised distance of ~0.50–0.55 under both metrics, and the median nearest-seed distance is within a few percent. The concern in `[SPEC-DIR-200]` was that 71 weighted dimensions would shift the distance distribution enough to invalidate values tuned on 11 unweighted ones. Measured, it does not: the *shape* of the neighbourhood these parameters select is preserved.

**The one real difference is at the head, and it favours the new metric.** Passages within half the `excl_pool` boundary distance — the near-duplicate zone — drop from 16–59 under the inherited metric to **4** under the local one. With 18 characteristics it is harder for two recordings to be close on all of them, so extreme similarity is rarer and the gathered pool is less dominated by near-identical recordings. That is an improvement in pool quality that needs no parameter change to collect.

**`decay = 0.96` also stands.** Over `rand_pool = 100` it runs ×1.000 at rank 0, ×0.130 at rank 50, ×0.018 at rank 99 — a strong preference for the head without foreclosing the tail, which is what the observed winning ranks of 5, 10, 17, 24, 31, 50, 61, 99 show in practice `[SPEC-DIR-167]`.

**So the parameters are no longer marked "re-derive".** They were tuned on this library's own distance distribution `[SPEC-FD-053]`, and that distribution survived the change of metric. Revisit only if the library's composition changes substantially, not because the vector grew.

**`[SPEC-DIR-200]` The pool parameters were tuned for 8,116 passages over 11 unweighted dimensions.** Lempi uses 71 dimensions with scale normalization and reliability weighting `[SPEC-FD-040]`, which changes the distance distribution — measured between-recording spread already varies 3× across characteristics `[SPEC-FD-050]`. Carrying 1000/100/0.96 across unchanged is an assumption, not an inheritance. Re-derive against the retrieval harness before treating them as settled.

---

## 9. Open

1. **`[SPEC-DIR-210]` Eligibility is evaluated at selection time**, not projected to estimated play time — MuLibPlay's explicit `TODO`. Deferred deliberately: `[REQ-PD-110]` and the P3 acceptance test require reproducing MuLibPlay's selections, and projection would break that check before it has ever passed. Revisit as a measured divergence once reproduction is demonstrated `[GDE-PHS-030]`.
2. **`[SPEC-DIR-215]`** Whether Like-Taste should age or cap. Likes accumulate without bound; 6–8 curated seeds could be swamped.
3. **`[SPEC-DIR-220]`** Per-user Taste with a shared queue — McRhythm's multi-user model is inherited but undecided for Lempi `[REQ002 §8]`.
4. **`[SPEC-DIR-230]` The fallback itself is still unweighted.** `[SPEC-DIR-225]` stops it being *reached* on a restart; it does not make it safe where there is nothing to restore — a fresh image, a rescan that renumbered every saved id `[SPEC-SC-095]`, or a queue that legitimately drained. Each still draws children's and out-of-season music at its full library share. The direct fix is to teach `random_radio` the occasion floor: exclude passages whose multiplier today puts them under `min_weight`, which needs no Director and is a `WHERE` clause. Deferred deliberately — the restore covers every occurrence observed so far, and this is the backstop for the ones that have not been.
5. **`[SPEC-DIR-235]` A restored queue is not re-weighed.** It was chosen against a history and a programme that have since moved. Over a deploy that is seconds and irrelevant; over an appliance switched off for a month it is a month-stale selection, and the programme in force at 09:00 is not the one that chose it at 21:00. The shape of the answer is to restore entries as **provisional** and let `adopt` re-weigh the ones that have not started when the Director lands ~15 s later — it already walks exactly that list to `note_queued` them. Preferred over a hard staleness cutoff, which would throw away a good queue to avoid a rare bad one.
6. **`[SPEC-DIR-240]` A passage that killed the player comes straight back.** Restoring the queue restores the cause of the crash with it, where the old random fill broke the loop by accident. The engine already survives a file it cannot open (`take_dropped` → `forget_queued` `[REQ-PD-112]`), so this is about a panic or an OOM, not a bad file. A guard on the head — if it is the passage `player_state` names as current and no audible progress was ever recorded against it, drop rather than replay — is cheap and has no other effect.
7. **`[SPEC-DIR-245]` Provenance is not persisted with the queue.** `Playback::queued_ids` yields ids, and carrying `selected_by` through would mean widening the trait across four backends. Restored entries therefore record as `None` rather than as the programme or the person that chose them, so a restart still costs `[REQ-VIS-300]` the attribution for whatever was waiting. Worth doing with `[SPEC-DIR-235]`, which wants the entries and not just their ids anyway.

---

**Traceability:** `[SPEC-DIR-100..245]` · derived from `[GDE-PD-010..050]`, `[GDE-MCR-070]`, `[SPEC-FD-040]`
