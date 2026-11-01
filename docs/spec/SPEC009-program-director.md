# SPEC009: Program Director

**Design Specification — Tier 2 · re-read against the code and restructured 2026-11-01**

How Lempi chooses the next passage. Reproduces MuLibPlay's six-years-proven selection algorithm `[GDE-PD-010..050]`, extended with the full flavor vector — AcousticBrainz's 18 characteristics, 71 dimensions `[ENT-CHAR-010]`, beside the listener's own, which take part too `[SPEC-FD-112]` — and with Like-Taste, which is built but has never had data.

> **Related:** [REQ002 §2](REQ002-functional-requirements.md#2-program-director--pd) · [SPEC037](SPEC037-eligibility-and-frequency.md) for Stage A · [SPEC074](SPEC074-when-the-director-cannot-choose.md) for startup and the two fallbacks · [SPEC005](SPEC005-flavor-distance.md) for flavor distance · [SPEC062](SPEC062-named-programmes.md) for programmes as named things · [SPEC023](SPEC023-domain-vocabulary.md) for the words — rotation, weighting and flavor score **recordings** (occasionally passages, where noted), and the panel showing this is "Why this passage?" `[REQ-VIS-100]` · inherited [MCR-SPEC005](../inherited/mcrhythm/MCR-SPEC005-program_director.md), [MCR-SPEC006](../inherited/mcrhythm/MCR-SPEC006-like_dislike.md)

---

## 1. The Governing Idea

**`[SPEC-DIR-100]` Two orthogonal mechanisms, kept orthogonal.** This is why MuLibPlay works, and the single most important thing not to break:

| Mechanism | Answers | Inputs |
| :--- | :--- | :--- |
| **Frequency** | *How often may this play?* | rotation, recovery, restraint, play history, occasion |
| **Character** | *Does this fit right now?* | the slot's seeds, Taste, flavor distance |

They never mix. Frequency produces a weight; character shapes and orders a pool. Conflating them was rejected explicitly `[GDE-OPN-030]`: a taste-match factor folded into the weight would make "I like this" and "play this often" indistinguishable, and the weight undecomposable for `[REQ-VIS-100]`.

**`[SPEC-DIR-105]` Randomness is applied last**, over an already-shaped pool `[GDE-PD-050]`. Character comes from the shaping; surprise comes from the roulette.

---

## 2. Every Path a Pick Can Take

**`[SPEC-DIR-107]`** The engine asks for a pick whenever its queue is shorter than its depth (five by default), one pick at a time, each marked as coming before the next is asked for `[SPEC-DIR-115]`. **Everything in the queue is kept out of the next pick**, whether the listener put it there or the Director did — and so is what has already left the queue for the output: the passage on air, and one fading in over it (`sounding_ids`). Rotation holds those out of an ordinary pick anyway; the two fallbacks set rotation aside, and without this could queue the passage now playing to play straight after itself. `mpd_direct` asks the same Director the same way, with MPD's queue as the queue `[SPEC015]`: every entry in it, added through any client, is resolved to its passage (by Id, URI or sticker) and kept out.

**`[SPEC-DIR-109]` `mpd_direct` follows what the player changes while it runs.** It is another process, so it cannot share the player's in-process controls; every 5 s it reads the listener file instead. A programme edit (its slots and seeds, compared as one string) is taken into the running Director as the player takes it; the slot held by hand, the stored UTC offset `[SPEC-DIR-183]` and the suppression windows `[SPEC-PLAY-050]` are applied as they stand; with `--write` it asks the OS for the offset hourly, as the player does, never past a hand-set hold. And whenever the player starts a Director rebuild — a preference saved, a star sync, a file found damaged, music imported, a library reload asked for; but not its first build at start, which is the player having no Director yet rather than anything having changed — it records the moment in `player_settings` (`director_rebuilt_at`), and `mpd_direct` rebuilds its own Director and URI map too — on a thread of its own, stepped aside as the player's builder is `[IMPL-SUI-075]`, so it goes on sampling MPD and choosing with the Director in hand for the 10–30 s a rebuild takes on a Pi Zero 2W. When the new one lands it is swapped in, told again what is queued and what was heard, skipped or removed while it built, and given the hold, offset and windows at once. A skip or a removal is noted in the running Director the moment it happens, as the player notes one `[SPEC-PLAY-050]`; before 2026-11-01 `mpd_direct` only wrote it, so without `--write` it suppressed nothing.

```
 player start ─ Session::prime ................................. SPEC074 §1
   1. the resume point            2. the remembered queue
   3. a refill, for whatever is still short
         │
 refill ─┴─ (every tick, while the queue is short)
   │
   ├─ no Director yet ─▶ Library::fallback_radio ................ SPEC074 §2
   │                     every rule but rotation, default tuning,
   │                     no programme, no flavor; a batch at once
   │
   └─ Director::decide(skip = what is queued,
   │                   after = the queue's tail, else the passage on air)
   │     A. eligibility & frequency .. weight per radio passage .. SPEC037
   │     B. pool shaping .............. seeds of the slot on air,
   │                                    Like-Taste, Dislike ...... §4
   │     C. flow ...................... sort by distance to `after`,
   │                                    when `after` has flavor .. §5
   │     D. roulette .................. top 100 × 0.96^rank, drawn;
   │                                    with no flow, all, undecayed
   │     ─▶ picked: mark as coming, record, enqueue ............. §7
   │
   └─ nothing eligible ─▶ Director::decide_relaxed .............. SPEC074 §3
                         A without rotation or recovery, B as above,
                         no C, the longest idle favoured
                         ─▶ still nothing: queue nothing, rest 30 s
```

---

## 3. Stage A — Eligibility and Frequency

Its own document: [SPEC037](SPEC037-eligibility-and-frequency.md). Stage A reads no flavor, seed or Taste; it decides which radio passages may play now and how heavily each is weighted.

---

## 4. Stage B — Pool Shaping

**`[SPEC-DIR-140]` Seeds define the target.** The slot on air names its seeds, recordings that exemplify its sound `[ENT-SEED-010]` — MuLibPlay's were 6–8 per slot `[GDE-PD-040]`, though nothing enforces a count. At most `max_seeds` are used: one per artist, counting every credit of each seed, the least-recently-played first. Naming songs beats tuning sliders, and it is the mechanism MuLibPlay users actually exercised.

**`[SPEC-DIR-142]` A seed without flavor shapes nothing, and is passed over.** A seed works only through its flavor. One with none is skipped **before** the cap, so it never takes a place a seed with flavor could fill; it is named once in the log when the Director is built, and flagged on the player's Programme page and the hub's Programmes page. "Has flavor" is any `flavor` row for the recording. Measured 2026-11-01: none of the hub's 49 seeds lacks flavor.

**`[SPEC-DIR-145]` Prune, then gather** — both over flavor distance `[SPEC-FD-040]`, where a passage's distance to the programme is its distance to the **nearest** seed:

1. **Prune** — when more than `excl_pool` are eligible, keep the `excl_pool` nearest.
2. **Gather** — take the `rand_pool × 2 / seeds` nearest to *each* seed, and union them.

Gathering is per seed, not a global top-N, so one seed in a dense region cannot supply the whole pool and silently drop the rest of the programme slot.

**`[SPEC-DIR-147]` A passage without flavor is an unknown quantity, chosen only as a last resort** (the maintainer, 2026-11-01). The player must still play something when too few passages with flavor are available. It must not represent passages without flavor in proportion: one would be chosen only if and when nothing remains but passages with flavor that are known to be wrong or unwanted. Measured 2026-11-01: **37 of 10,850** radio passages on the hub have no flavor, all of them identified recordings flavor was never extracted for. Every tags-only passage on a phone is in this class too `[REQ-AND-310]`.

What the code does today, read 2026-11-01. Two mechanisms put such a passage last, and both apply only where they run: **pruning** (Stage B, with seeds) sorts it after every passage comparable to a seed, so it is cut whenever 1,000 or more are; **flow** (Stage C, with a queue tail that has flavor) sorts it after every passage with a flow distance, so it reaches the top 100 only if fewer than 100 can be measured. A survivor of the prune joins the gathered pool without the per-seed cap.

| path | with seeds | with no seeds |
| :--- | :--- | :--- |
| `decide`, with a flow order | last resort in a full library: pruned, and sorted last by flow | last resort in a full library: sorted last by flow |
| `decide`, no flow order | last resort in a full library: pruned; in a pool under ~1,000 comparable, a survivor is **drawn at its ordinary weight** | **drawn at its ordinary weight** among every eligible passage |
| `decide_relaxed` (never a flow order) `[SPEC-DIR-232]` | as the row above | **drawn at its ordinary weight** |
| `fallback_radio`, no Director `[SPEC-DIR-230]` | flavor is not read: **ordinary weight** | **ordinary weight** |

"No flow order" is a pick with nothing queued and nothing on air — the very first of a fresh start — or one following a passage without flavor. So the intent holds in a full library with seeds and a flow tail, which is the common case, and the bold cells are where it does not yet hold `[SPEC-DIR-212]`.

**`[SPEC-DIR-150]` Taste enters here, and only here** `[GDE-OPN-030]`. Like-Taste and Dislike-Taste are weighted centroids of the flavor of liked and disliked recordings `[MTA-SMPL-020]`, built once per Director from `listener_likes` — **one per library, not per listener**. Who is listening is not modelled at all; per-listener Taste is one of several possible future directions `[SPEC-DIR-220]`.

- **Like-Taste is an additional seed.** A slot's seeds weigh 1.0, and Like-Taste weighs `like_seed_weight` (1.0 today). In `shape`, a candidate's distance to a seed is divided by that seed's weight, and the divided figure is used in exactly two places:
  1. **Pruning** ranks candidates by their divided distance to the *nearest* seed. Here the weight acts: above 1.0 a passage near Like-Taste survives the cut over one equally near a slot seed, below 1.0 the reverse.
  2. **Gathering** ranks candidates for each seed by their divided distance to *that* seed. Every candidate is divided by the same number, so the order is the order of the raw distances, and each seed — Like-Taste included — takes the same `rand_pool × 2 / seeds`. The weight changes nothing here.

  Nothing else sees it: flow (Stage C) measures from the queue tail, the roulette draws on Stage-A weights, and the record's `seed_distances` are the raw distances. At today's 1.0 it has no effect at all; whatever its value, it never changes Like-Taste's share of the gathered pool, a weight, or the flow order.
- **Dislike-Taste is mostly unimplemented**, and one of several possible future directions. What exists is an exclusion filter: passages within `dislike_radius` of it leave the pool before pruning, the use McRhythm suggested `[LD-LIKE-021]`. It runs only when there are seeds; with none, Stage B is bypassed and a Dislike does nothing.

> **Neither half has ever had data.** Nothing in Lempi writes `listener_likes` — it is empty on the hub (read 2026-11-01) — so both centroids are tested and unexercised, and `dislike_radius` and `like_seed_weight` are **new and unvalidated** `[SPEC-DIR-195]`.

**`[SPEC-DIR-155]` Taste never blocks and never boosts frequency.** A disliked passage leaves the *pool*; its rotation and restraint are unchanged, and removing the Dislike returns behaviour to baseline with no residue.

**`[SPEC-DIR-157]` Shaping is visible in what plays.** *(Built 2026-08-10.)* The same library at three times of day, six passages each:

| Slot | Queue | Mean length |
| :--- | :--- | ---: |
| **Prog** 19:00 | Steely Dan *Aja*, Traffic, Rush *Jacob's Ladder*, Led Zeppelin, Genesis *Squonk* | ~440 s |
| **Groove** 15:00 | Genesis *Los Endos*, Fatboy Slim, U2, Paula Abdul (dance mix), Massive Attack | ~270 s |
| **Light** 10:00 | Genesis *Invisible Touch*, Beatles *Lovely Rita*, Tom Petty, Heart | ~200 s |

Genesis appears in all three, and a *different* Genesis each time. That is the property the metric exists for: similarity by sound rather than by artist. Length was never an input to shaping; it separates because prog is long and pop is short.

**`[SPEC-DIR-158]` Cold start: weighted by length and season, ordered by flow, not uniform.** With no history, no Likes, no tuning and no seeds, what actually happens:

- **Stage A** blocks nothing by rotation and ramps nothing — no passage has played — and every restraint is `10^0 = 1.0`. Each weight is then the **length bonus** (×2 at 45 s and under, ×1 at 180 s, ×0.22 at an hour `[SPEC-DIR-125]`) times the **occasion multiplier**, so the children's passages and out-of-season ones still fall below `min_weight` and are out `[SPEC-DIR-136]`. The filters, holds and failed files apply as always.
- **Stage B** has no seeds and passes every eligible passage through.
- **Stages C and D**: the first pick of a fresh start has nothing to follow, so it is drawn from every eligible passage by that weight. Every later pick follows the one before: the pool is sorted by flavor distance to it, the nearest 100 decayed by rank, and drawn — so the station drifts by sound from wherever it began.
- Each pick then blocks its recording, works and artists by rotation, as at any other time.

A slot with seeds is the minimum for a station with a character of its own.

---

## 5. Stages C & D — Flow and Roulette

**`[SPEC-DIR-160]` Flow.** Re-sort the pool by flavor distance to the passage this pick will follow: the last passage already queued, or, with nothing queued, the passage on air. Consecutive passages then blend `[GDE-PD-050]`, and that continuity is what makes a hard slot switch acceptable `[SPEC-DIR-180]`. With no such passage, or one without flavor, there is no flow order.

> **Future direction `[SPEC-FD-170]`:** flow matches whole-passage flavor to whole-passage flavor, but a handover is heard as the *end* of one passage against the *start* of the next. Matching `exit → entry` would model the transition rather than the pairing. Not the current target — it roughly triples extraction cost.

**`[SPEC-DIR-165]` Roulette.** With a flow order: take the top `rand_pool` (100), apply rank decay `w × decay^rank`, and draw by the decayed weights. **With no flow order there is no meaningful rank**, so the draw is over the whole shaped pool at undecayed weight, and the record says so: with seeds, up to 200 gathered plus any passage without flavor that survived the prune; with none, every eligible passage. Either way the draw is by weight, not by rank: a lower-ranked passage can win, which is where the surprise lives.

**`[SPEC-DIR-167]` Both measurable.** *(Built 2026-08-10.)* Across eight consecutive picks, mean distance between a passage and the one it followed was **0.432**, against a library median of 1.052 `[SPEC-FD-085]` — **59% closer than random pairs**. Winning ranks: **5, 10, 17, 24, 31, 50, 61, 99** — one from rank 99 at a weight of 0.006. A director that always returned rank 0 would be a bug, not good taste.

---

## 6. The Programme and Its Slots

**`[SPEC-DIR-180]` One slot is on air at a time, and the switch is hard.** A node runs one **programme**, the whole day `[ENT-PROGRAMME-010]`, made of **programme slots** `[ENT-SLOT-010]`, each a name, a start time and its seeds. Every slot has a start time; one without is refused wherever a programme is made or received `[ENT-SLOT-010]`. A slot has no stored end: it runs from its start until the next slot's start, wrapping through midnight, so the slots cover the day with no gap and no overlap. The slot on air is whichever started most recently. MuLibPlay's day had eight slots `[GDE-PD-040]`; Lempi takes any number. Blending neighbours was rejected: six years of production show no complaint, flow already smooths the change `[SPEC-DIR-160]`, and blending adds a tunable nobody asked for while making "which slot am I in?" ambiguous.

**`[SPEC-DIR-182]` Everything on the clock is local time.** Slot start times and the occasion calendar `[SPEC-DIR-132]` both read the listener's wall clock, through `listener_settings.utc_offset_minutes`. The player syncs it from the OS at start and hourly — unless a person holds it by hand `[SPEC-DIR-183]` — since the column's default is UTC and most listeners are not `[REQ-VIS-255]`. Seasons read local time since 2026-11-01; before, they turned at UTC midnight.

**`[SPEC-DIR-183]` The offset can be seen, set by hand, and resynced.** *(Built 2026-11-01, at the maintainer's direction.)* The Lempi skin's Settings page shows *Clock offset from UTC* as ±HH:MM, and whether it came from the OS or was set by hand. Typing one (`POST /clock/utc-offset/:minutes`, at most 18 hours either way) stores it and **holds it**: the hourly resync and the one at start then leave it alone and return it as stored, or a hand edit would be undone within the hour. *Resync from OS* (`POST /clock/utc-offset/os`) releases the hold and takes the OS's offset at once. The hold is `utc_offset_by_hand` in `player_settings`, beside the held slot; the engine applies either request on its next refill, to the store and the running Director together, and the snapshot's `utc_offset` says what took.

**`[SPEC-DIR-185]` A slot held by hand overrides the clock** until the listener reverts. `POST /programme/slot/:id` holds slot `id`; the literal `auto` releases it, and the pickers offer that as "Automatic (by time of day)" `[REQ-VIS-255]`. `POST /program/:id` is the same route under its older name `[SPEC-VOC-020]`. The hold is persisted (`PlayerStore::save_manual_slot`) and restored at start; `auto` deletes it, so "never chosen" and "released" read back alike. A held slot that an edit removes is released; one an edit renews with new seeds keeps its hold under its new id `[SPEC-PGM-125]`. A save refreshes the list the route checks at once, so a slot just saved can be held straight away, rather than being refused until the engine's next refill rebuilds the list from its Director. Motivated by `lempiplay3` `[SPEC036]`, a vehicle with no realtime clock, whose buttons never send `auto`.

---

## 7. Visibility Contract

**`[SPEC-DIR-190]`** Every pick the player's Director makes writes a `selection_decisions` record `[SPEC-SC-100]` sufficient to reconstruct it `[REQ-VIS-100]`: the Stage-A terms (artist weight, recording restraint and ramp, related damping, length bonus, occasion), the final weight, the shaping counts, the distance to each seed, the flow distance, the rank, the decayed weight, the roulette target, the slot on air by name and by id — the id also in the row's own `program_id`, since 2026-11-01 — **and the five heaviest runners-up, by decayed weight**. A slot whose seeds change takes a new id `[SPEC-PGM-125]`, so `program_id` names the seeds a pick was shaped by. The no-Director fallback writes none `[SPEC-DIR-230]`; `mpd_direct` writes the same record as the sticker `lempi.why` instead `[SPEC-MPD-050]`.

**`[SPEC-DIR-191]` The stated chance is the chance it had.** `share_pct` is the weight the passage was drawn on over the total drawn on: decayed over decayed with a flow order, undecayed over undecayed without. *Corrected 2026-11-01*: it had divided the undecayed weight by the decayed total, overstating every pick below rank 0 — some 8-fold at rank 50 and 57-fold at rank 99.

**`[SPEC-DIR-192]` What the record does not break out.** The artist weight is one figure for every credited artist together `[SPEC-DIR-114]`, not which credit set the ramp. The occasion multiplier is the combined scalar, not the curve and value behind it. Like-Taste's distance sits among the seed distances, unlabelled. Each is a recordable extension of `Weighing`/`Explanation`, not yet built.

The orthogonality of `[SPEC-DIR-100]` is what makes this legible: the panel tells *how often* and *does it fit* as two stories rather than one opaque product.

---

## 8. Parameters

**`[SPEC-DIR-195]`** Provenance matters — some values are proven, some re-verified here, some new:

| Parameter | Value | Status |
| :--- | ---: | :--- |
| `min_weight` | 0.001 | Proven |
| `max_seeds` | 5 | Proven |
| min / max length, max depth | 30 s / 3600 s / 10800 s | Proven |
| length bonus midpoint, cap | 180 s, 4.0 | Proven — see `[SPEC-DIR-125]` for what it favours |
| `excl_pool` | 1000 | **Re-verified** `[SPEC-DIR-205]` |
| `rand_pool` | 100 | **Re-verified** |
| rank `decay` | 0.96 | **Re-verified** |
| `dislike_radius`, `like_seed_weight` | 0.5, 1.0 | **New, unvalidated** |

**`[SPEC-DIR-200]` Why they were questioned.** The pool parameters were tuned on 8,116 passages over 11 unweighted characteristics. Lempi measures distance over 18 characteristics with scale normalisation and reliability weighting `[SPEC-FD-040]`, and between-recording spread varies 3× across them `[SPEC-FD-050]`. Carrying 1000/100/0.96 across unchanged was an assumption, so they were re-derived — below.

**`[SPEC-DIR-205]` Re-derived 2026-08-13, and kept.** Measured by `tools/pool_params.py`, comparing where each pool boundary falls in distance under both metrics:

| | rank 10 | rank 200 (*gather*) | rank 1000 (*excl_pool*) | median nearest seed |
| :--- | ---: | ---: | ---: | ---: |
| local 18 — *Cool* | 0.295 | 0.415 | **0.515** | 0.689 |
| inherited 11 — *Cool* | 0.219 | 0.377 | **0.502** | 0.692 |
| local 18 — *Mellow* | 0.313 | 0.451 | **0.554** | 0.730 |
| inherited 11 — *Mellow* | 0.167 | 0.282 | **0.454** | 0.766 |

**The boundaries land in the same place**: `excl_pool = 1000` cuts at ~0.50–0.55 under both, and the median nearest-seed distance is within a few percent. The one difference favours the new metric: passages within half the boundary — the near-duplicate zone — drop from 16–59 to **4**, so the gathered pool is less dominated by near-identical recordings. `decay = 0.96` stands too: ×1.000 at rank 0, ×0.130 at rank 50, ×0.018 at rank 99. Settled; revisit only if the library's composition changes substantially, not because the vector grew.

---

## 9. Open

1. **`[SPEC-DIR-210]` Eligibility is evaluated at selection time**, not projected to estimated play time — MuLibPlay's explicit `TODO`. Deferred: `[REQ-PD-110]` requires reproducing MuLibPlay's selections first `[GDE-PHS-030]`.
2. **`[SPEC-DIR-212]` Passages without flavor as a true last resort** `[SPEC-DIR-147]`: in the bold cells of that table — no flow order, the relaxed fallback, the no-Director fallback — they are still drawn at their ordinary weight.
3. **`[SPEC-DIR-215]`** Whether Like-Taste should age or cap. Likes accumulate without bound; a handful of curated seeds could be swamped.
4. **`[SPEC-DIR-220]`** Taste per listener, and a shared queue between listeners — McRhythm's multi-user model is inherited but undecided `[REQ002 §8]`. One of several possible future directions, as is Dislike-Taste beyond its filter `[SPEC-DIR-150]`.
5. Startup and the fallbacks have their own open items: `[SPEC-DIR-235]`, `[SPEC-DIR-238]` and `[SPEC-DIR-245]` in [SPEC074](SPEC074-when-the-director-cannot-choose.md).

---

**Traceability:** `[SPEC-DIR-100..220]` · `[SPEC-DIR-225..245]` in [SPEC074](SPEC074-when-the-director-cannot-choose.md) · Stage A `[SPEC-DIR-110..137]` in [SPEC037](SPEC037-eligibility-and-frequency.md) · derived from `[GDE-PD-010..050]`, `[GDE-MCR-070]`, `[SPEC-FD-040]`
