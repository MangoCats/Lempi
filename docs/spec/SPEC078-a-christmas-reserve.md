# SPEC078: A Christmas Reserve

**Design Specification — Tier 2 · designed and built 2026-11-09, at the maintainer's direction · built on the desktop only; not yet deployed or heard**

From 1 December to Christmas Day, a growing share of the roulette's places is kept for Christmas passages. It starts slow and climbs steeply through the week before Christmas. The occasion multiplier `[SPEC-DIR-130]` is unchanged and still applies; this works on the roulette list after Stage C instead.

> **Related:** [SPEC009](SPEC009-program-director.md) `[SPEC-DIR-160]`, `[SPEC-DIR-165]` (flow and the roulette) · [SPEC037](SPEC037-eligibility-and-frequency.md) `[SPEC-DIR-111]`, `[SPEC-DIR-115]`, `[SPEC-DIR-134]` · [SPEC074](SPEC074-when-the-director-cannot-choose.md) (the relaxed draw) · [SPEC077](SPEC077-a-profanity-tolerance.md) (the same kind of per-player setting) · `player/core/src/director/christmas.rs`, `player/core/src/director/frequency.rs` (`Rotation`), `player/core/src/director/library.rs` (`decide`, `christmas_fillers`, `load_christmas`), `player/src/session.rs`, `player/src/web/control.rs`, the MuLibPlay and Lempi skins

---

## 1. Why

**`[SPEC-XMR-001]` The occasion curve alone gives little Christmas music, because rotation undoes it.** *Measured on bose, 2026-11-09:* 2,210 plays from 1 to 9 November, 41 of them Christmas recordings, at a curve of ×0.10–0.17. 38 of the 41 came in the Soft and Mellow slots. A model of Stage A calibrated on those picks put Christmas Day itself at about 9% of plays, and 24 December at about 8%, despite the curve's ×5 and ×10. Each Christmas recording, once heard, is held for 4.2 days and then takes 16.6 more to recover. Most are still recovering by late December, and six artists hold 79 of the 182 Christmas passages, each on its own rotation.

## 2. Which passages

**`[SPEC-XMR-015]` A Christmas passage is a radio passage whose recording has a Christmas value above 0.0.** The value is `user.christmas` / `christmasy`: the listener's own in `listener_characteristics` where there is one, the inherited `flavor` value where not `[SPEC-PREF-085]`. A passage takes its top recording's value `[SPEC-DIR-112]`. Only the positive class counts. Every inherited recording also carries `not_christmasy`, at 0.0 where `christmasy` is 1.0 `[SPEC-DIR-134]`, and reading the two as one value turned bose's 41 Christmas plays into 1 in a first count. The set is taken when the Director loads, from the occasion values it has just read where Christmas is a registered occasion, as it is on every node. It is read afresh only where it is not: a second pass over `flavor` cost 7.9 s cold on lempi02w. *bose, 2026-11-09:* 182 of 11,504 radio passages, 180 at 1.0, one at 0.9 and one at 0.5.

## 3. The ramp

**`[SPEC-XMR-020]` `christmas_ramp`, from 0.0 to 1.0, on the listener's clock at the pick's projected start `[SPEC-DIR-186]`.**

| from | to | value |
| :--- | :--- | :--- |
| 26 December 06:00 | 1 December 06:00 | 0.0: no effect at all |
| 1 December 06:00 | 25 December 06:00 | rising linearly from 0.0 to 1.0 |
| 25 December 06:00 | 25 December 23:45 | 1.0 |
| 25 December 23:45 | 26 December 06:00 | falling linearly from 1.0 to 0.0 |

The local clock is the programme's own, by its stored UTC offset `[SPEC-DIR-183]`. The ramp is in code for this season, on the maintainer's word; it is not data, and no tool edits it.

**`[SPEC-XMR-030]` Places reserved: `christmas_ramp × 100`, rounded.** In general it is `× size`, rounded, where *size* is the roulette list's length. That is the flow order's 100 `[SPEC-DIR-165]`, or the whole shaped pool when there is no flow order. One place is added every 5.76 hours of the rise: the first at 1 December 08:53, 50 by 13 December 03:07, 96 by 24 December 04:05.

## 4. The roulette list

**`[SPEC-XMR-040]` The reserved places are the list's last.** Stage C ranks every passage Stage B gathered, up to about 200 `[SPEC-DIR-160]`. With *R* places reserved:

1. Stage C's first 100 − *R* stand exactly as ranked.
2. Christmas passages Stage C ranked below that cutoff move up into the reserved places, in Stage C's order, at their own weight.
3. Reserved places still empty take **fillers** `[SPEC-XMR-050]`.
4. Places still empty after that are **backfilled** with the non-Christmas passages from below the cutoff, in Stage C's order `[SPEC-XMR-060]`.

The list keeps its rank, and Stage D draws on it as ever: weight × 0.96^rank `[SPEC-DIR-165]`. The places reserved are the roulette's lowest ranks, which carry least of its decay, so the effect is **deliberately non-linear** (the maintainer, 2026-11-09). At equal weights the reserved places hold about 3% of the draw at *R* = 25 (7 December), 11% at 50 (13 December), 35% at 75 (19 December) and 85% at 96 (24 December).

**`[SPEC-XMR-050]` A filler is a Christmas passage from outside Stage C's list, nearest the programme first, with rotation set aside.**
- **Which:** Christmas passages not already in the list or queued, ordered by distance to the nearest seed of the slot on air, as Stage B measures it. One without flavor comes last, as in Stage C `[SPEC-DIR-147]`; one too close to Dislike-Taste is left out, as Stage B would `[SPEC-DIR-150]`.
- **Set aside:** every rotation block, whether artist, recording, work or related.
- **Kept:** every recovery, measured from the last play rather than from the end of the rotation: a recording heard a day ago weighs 1 day ÷ 16.6 days of recovery, about ×0.06, where its rotation would have held it out. Artist recovery applies the same way.
- **Still applied:** a hold, the length filters, the profanity tolerance, the skip and dequeue windows, restraint, the occasion multiplier and the 0.001 floor. A recording just played therefore weighs nothing and stays out.
- Fillers are weighed only when moving up leaves places empty.

**`[SPEC-XMR-060]` A shortage of Christmas leaves the list full.** Moving up and fillers together take at most *R* places. What they cannot fill goes to the passages below the cutoff that are not Christmas, the lower-weighted places, so the roulette still draws on 100 wherever the shaped pool allows.

**`[SPEC-XMR-070]` Said in "why this passage?".** Each pick records its `place`: `flow`, `moved_up`, `filler` or `backfill`. It also records the reserve's `christmas` tally: whether it is enabled, the ramp, places reserved, and how many were moved up, filled and backfilled. The skins add a clause to the why line, and a filler says its rotation was set aside. Both are in the stored decision `[SPEC-DIR-190]`.

## 5. The switch

**`[SPEC-XMR-080]` On by default, a person's to turn off, and kept on the player.** Each player has its own setting, `christmas_reserve` in its `player_settings`, written `on` or `off`. An absent or unreadable value is on. It is never shared, as the profanity tolerance is not `[SPEC-PTL-030]`. `POST /director/christmas/:on` sets it, and it is live from the next pick. The snapshot carries `christmas_reserve` and `christmas_ramp`, the ramp now. Its readout is the ramp as a percentage in season, *on* out of season, and *off*. Where it is shown (the maintainer, 2026-11-09):
- **Lempi skin:** a checkbox on the Settings page, always. A follower plays what its leader chose `[SPEC-ECHO-010]`, so while this node follows, the switch stays usable and a note under it says it has no effect while following.
- **MuLibPlay skin:** a checkbox under the profanity slider, shown only while the ramp is above 0.0, and never while this node follows.
- **WinAmp skin:** none.

**`[SPEC-XMR-090]` Where it does not reach.**
- **People:** a passage queued by hand, an album played whole and a search are untouched `[GDE-OCP-900]`.
- **The relaxed draw** `[SPEC-DIR-232]` keeps no reserve: it is the answer to rotation having emptied the pool, and it already sets rotation aside for every passage alike.
- **The pre-Director fill** `Library::fallback_radio` has no Stage C, so it has no reserve either.

## 6. What it does on bose

**`[SPEC-XMR-100]` The real Director, run forward.** `seasonsim` loads a Director from a copy of a node's pair and picks from now to the end of December. Each pick is made at its start, settled as a play, and followed by the next at its end, with the last *queue depth* picks kept out. It runs once with the reserve off and once with it on. Nothing is skipped, and every passage plays whole and back to back, so it makes about 335 picks a day where bose made 263. *Run 2026-11-09 on bose's pair, one season each way:*

| day | reserve off | reserve on | Python model, on |
| :--- | ---: | ---: | ---: |
| 5 December | 4.2% | 7.1% | 6.9% |
| 13 December | 2.1% | 17% | 11% |
| 20 December | 4.2% | 34% | 33% |
| 24 December | 3.9% | 68%: 150 of 182 passages, the most-played 4 times | 72% |
| 25 December | 4.0% | 99%: 169 passages, the most-played 7 times | 99% |
| 27–29 December | 1.2–2.4% | 0%: every Christmas passage still recovering | — |
| December | 2.9% | 20.4% | about 22% |

Almost every Christmas play with the reserve on was a filler: Stage C's own list rarely holds a Christmas passage that is eligible. A median pick took 26.5 ms on the desktop with the reserve, against 21.9 ms without. The model of the first estimate was a Monte Carlo of Stage A in Python, calibrated per slot on bose's picks of 1–9 November. It came within a few points of the real Director with the reserve on, and overstated the share with it off.

## 7. Open

**`[SPEC-XMR-900]` Built and tested on the desktop; not deployed, and not yet heard.** It needs every node running it before 1 December 06:00. `seasonsim` ran one season each way; more runs would narrow the daily figures, which vary by a few points from day to day.

**`[SPEC-XMR-910]` Christmas Day repeats.** With rotation set aside, the most-played Christmas passages play seven times on 25 December, about every three hours `[SPEC-XMR-100]`. Nothing limits that. A minimum gap was offered and not asked for.

---

**Traceability:** `[SPEC-XMR-001..100]`, `[SPEC-XMR-900]`, `[SPEC-XMR-910]` · from the maintainer's design of 2026-11-09 · satisfies `[REQ-PD-129]` · builds on `[SPEC-DIR-160]`, `[SPEC-DIR-165]`, `[SPEC-DIR-186]`, `[SPEC-PTL-030]`
