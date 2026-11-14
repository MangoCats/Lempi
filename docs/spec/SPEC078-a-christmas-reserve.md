# SPEC078: A Christmas Reserve

**Design Specification — Tier 2 · designed and built 2026-11-09, at the maintainer's direction · four faults a review found fixed 2026-11-13 · which build each node runs is [FLEET001 §3](../../fleet/FLEET001-the-fleet.md#3-what-each-runs) `[FLT-RUN-010]`, not this page · live from 1 December 06:00, not yet heard**

From 1 December to Christmas Day, a growing share of the roulette's places is kept for Christmas passages. It starts slow and climbs steeply through the week before Christmas. The occasion multiplier `[SPEC-DIR-130]` is unchanged and still applies; this works on the roulette list after Stage C instead.

> **Related:** [SPEC009](SPEC009-program-director.md) `[SPEC-DIR-160]`, `[SPEC-DIR-165]` (flow and the roulette) · [SPEC037](SPEC037-eligibility-and-frequency.md) `[SPEC-DIR-111]`, `[SPEC-DIR-115]`, `[SPEC-DIR-134]` · [SPEC074](SPEC074-when-the-director-cannot-choose.md) (the relaxed draw) · [SPEC077](SPEC077-a-profanity-tolerance.md) (the same kind of per-player setting) · `player/core/src/director/christmas.rs`, `player/core/src/director/frequency.rs` (`Rotation`), `player/core/src/director/library.rs` (`roulette`, `christmas_fillers`, `load_christmas`), `player/core/src/director/shape.rs` (`too_disliked`, `last_resort`), `player/src/session.rs`, `player/src/web/control.rs`, the MuLibPlay and Lempi skins

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

**`[SPEC-XMR-030]` Places reserved: `christmas_ramp × 100`, rounded.** In general it is `× size`, rounded, where *size* is the roulette list's length. That is the flow order's 100 `[SPEC-DIR-165]`, or the whole shaped pool when there is no flow order. One place is added every 5.76 hours of the rise: the first at 1 December 08:53, 50 by 13 December 03:07, 96 by 24 December 04:05. **With no flow order the pool is shuffled first**, so the places given up are any. A pool with no rank was in the order its rows were read, and until 2026-11-13 the same passages, the newest imports, gave up their places in every such pick. With no decay the shuffle changes nothing else about the draw, and it is done only when places are kept.

## 4. The roulette list

**`[SPEC-XMR-040]` The reserved places are the list's last.** Stage C ranks every passage Stage B gathered, up to about 200 `[SPEC-DIR-160]`. With *R* places reserved:

1. Stage C's first 100 − *R* stand exactly as ranked, at Stage C's weights.
2. Christmas passages Stage C ranked below that cutoff move up into the reserved places, in Stage C's order. Each is weighed as a filler is, with rotation set aside `[SPEC-XMR-050]`.
3. Reserved places still empty take **fillers** `[SPEC-XMR-050]`.
4. Places still empty after that are **backfilled** with the non-Christmas passages from below the cutoff, in Stage C's order `[SPEC-XMR-060]`.

**Every Christmas passage in a reserved place is on one scale.** Until 2026-11-13 one moved up kept Stage C's weight, its recovery running from the end of its rotation, while a filler's ran from its last play. A passage heard five days ago then weighed ×0.05 against ×0.06 for one heard yesterday. The reserve favoured what had played most recently, and a passage lost weight on leaving its rotation. Setting rotation aside never makes a passage lighter, and both weighings rise with the time since the last play (`frequency.rs`, tested on a grid of ages).

The list keeps its rank, and Stage D draws on it as ever: weight × 0.96^rank `[SPEC-DIR-165]`. The places reserved are the roulette's lowest ranks, which carry least of its decay, so the effect is **deliberately non-linear** (the maintainer, 2026-11-09). At equal weights the reserved places hold about 3% of the draw at *R* = 25 (7 December), 11% at 50 (13 December), 35% at 75 (19 December) and 85% at 96 (24 December).

**`[SPEC-XMR-045]` A pool of passages without flavor keeps no places.** When nothing with flavor is left to choose, Stage B's pool is its last resort `[SPEC-DIR-147]`. A Christmas passage with flavor beside those would be drawn where `[SPEC-DIR-147]` says none may be, and there is nothing to place Christmas by. So that pick reserves nothing (the maintainer, 2026-11-13). Stage B says it took the last resort (`last_resort` in its record), and the reserve reads that. The pick's tally carries `last_resort` when places were due, and the skins say *no places kept for Christmas: nothing in the pool has flavor to place them by*.

**`[SPEC-XMR-050]` A filler is a Christmas passage from outside Stage C's list, nearest the programme first, with rotation set aside.**
- **Which:** Christmas passages not already in the list or queued, ordered by distance to the nearest seed of the slot on air, as Stage B measures it. With no seeds, they are ordered by flow distance from the queue tail, as Stage C ranks. One without flavor is never a filler: the reserve keeps places only in a pool with flavor `[SPEC-XMR-045]`. Until 2026-11-13 such a passage was sorted last, which with no seeds was no order at all. One too close to Dislike-Taste is left out where Stage B would leave it out, by Stage B's own test (`shape::too_disliked`). That test reads the Dislike only when the slot has seeds `[SPEC-DIR-222]`.
- **Set aside:** every rotation block, whether recording, work or related, and the artist's rotation all but **a sixth of it**. An artist is still held out for a sixth of its own rotation, after the artist time scale: 1 h 40 min on the 10-hour default, 4 h 20 min for an artist held 26 hours (the maintainer, 2026-11-13). Without that, a filler by the artist on air could follow it at once. On Christmas Day the ×10 curve lifts an artist heard four minutes ago to ×0.067, over the floor, and six artists hold 79 of the 182 Christmas passages. The gap is a block only, and the artist's recovery still runs from the last play.
- **Kept:** every recovery, measured from the last play rather than from the end of the rotation: a recording heard a day ago weighs 1 day ÷ 16.6 days of recovery, about ×0.06, where its rotation would have held it out. Artist recovery applies the same way. A passage moved up is weighed the same way `[SPEC-XMR-040]`.
- **Still applied:** a hold, the length filters, the profanity tolerance, the skip and dequeue windows, restraint, the occasion multiplier and the 0.001 floor. A recording just played therefore weighs nothing and stays out.
- Fillers are weighed only when moving up leaves places empty.

**`[SPEC-XMR-060]` A shortage of Christmas leaves the list full.** Moving up and fillers together take at most *R* places. What they cannot fill goes to the passages below the cutoff that are not Christmas, the lower-weighted places, so the roulette still draws on 100 wherever the shaped pool allows.

**`[SPEC-XMR-070]` Said in "why this passage?".** Each pick records its `place`: `flow`, `moved_up`, `filler` or `backfill`. It also records the reserve's `christmas` tally: whether it is enabled, the ramp, places reserved, how many were moved up, filled and backfilled, and `last_resort` `[SPEC-XMR-045]`. The skins add a clause to the why line. A filler and a passage moved up say their rotation was set aside. Both are in the stored decision `[SPEC-DIR-190]`.

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

**`[SPEC-XMR-100]` The real Director, run forward.** `seasonsim` loads a Director from a copy of a node's pair, opened read-only, and picks from now to the end of December. Until 2026-11-13 it read the player's settings through a store that opens read-write and makes its tables on an older listener, so a run could change the copy it measured. Each pick is made at its start, settled as a play, and followed by the next at its end, with the last *queue depth* picks kept out. It runs once with the reserve off and once with it on. Nothing is skipped, and every passage plays whole and back to back, so it makes about 335 picks a day where bose made 263. *Run 2026-11-13 on bose's pair as read that night, 195 Christmas passages of 12,085, seed 1, one season each way. The build before the fixes of 2026-11-13 (`65abce9`) and the build with them ran on the same copy:*

| day | reserve off | on, before the fixes | on, with the fixes |
| :--- | ---: | ---: | ---: |
| 5 December | 2.9% | 6.6% | 5.7% |
| 13 December | 3.0% | 14.5% | 18% |
| 20 December | 3.2% | 34% | 36% |
| 24 December | 4.3% | 70%: 165 passages, the most-played 4 times | 71%: 156, the most-played 5 times |
| 25 December | 3.8% | 97%: 173 passages, the most-played 6 times | 92%: 165, the most-played 6 times |
| 27–29 December | 1.2–2.1% | 0%: every Christmas passage still recovering | 0% |
| December | 2.9% | 20.6% | 21.6% |

**With the reserve off the two builds agree**, in every day's count: 306 Christmas plays of 10,442 picks. Nothing the fixes change reaches a pick with no places kept. With it on, December moves by a point. Christmas Day falls from 97% to 92%: the artist gap holds out fillers by an artist heard within a sixth of its rotation, and their places are backfilled. One season's daily figures vary by a few points, and the other days' differences are within that. Almost every Christmas play with the reserve on was a filler: Stage C's own list rarely holds a Christmas passage that is eligible. A median pick took 12.8 ms with the fixes and 12.9 ms without, the two runs side by side on the desktop.

The first run, 2026-11-09 on that day's pair, found 20.4% of December with the reserve on. The model of the first estimate was a Monte Carlo of Stage A in Python, calibrated per slot on bose's picks of 1–9 November. It came within a few points of the real Director with the reserve on, and overstated the share with it off.

## 7. Open

**`[SPEC-XMR-900]` Not yet heard.** It goes live at 1 December 06:00 on every node that runs it, and which build each node runs is FLEET001's to say `[FLT-RUN-010]`. Until 2026-11-13 this line said the reserve was on the desktop only, a week after every node had it: a second record of a deploy goes stale, which is why there is only one. The fixes of 2026-11-13 need every node running them before 1 December too. More `seasonsim` runs would narrow the daily figures, which vary by a few points from day to day.

**`[SPEC-XMR-910]` Christmas Day repeats.** With rotation set aside, the most-played Christmas passages play six times on 25 December, about every three hours of the day, both before the fixes of 2026-11-13 and after them `[SPEC-XMR-100]`. The first run had seven. The artist gap keeps an artist apart from itself, not a passage. Nothing limits a passage's own repeats, and a minimum gap was offered and not asked for.

---

**Traceability:** `[SPEC-XMR-001..100]`, `[SPEC-XMR-900]`, `[SPEC-XMR-910]` · from the maintainer's design of 2026-11-09, and decisions of 2026-11-13 on a review's findings `[SPEC-XMR-040]`, `[SPEC-XMR-045]`, `[SPEC-XMR-050]` · satisfies `[REQ-PD-129]` · builds on `[SPEC-DIR-147]`, `[SPEC-DIR-160]`, `[SPEC-DIR-165]`, `[SPEC-DIR-186]`, `[SPEC-DIR-222]`, `[SPEC-PTL-030]`
