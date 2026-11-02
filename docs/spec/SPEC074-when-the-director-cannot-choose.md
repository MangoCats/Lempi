# SPEC074: When the Director Cannot Choose

**Design Specification — Tier 2 · written 2026-11-01, at the maintainer's direction · split from [SPEC009](SPEC009-program-director.md), which states the pipeline these paths sit around**

The four stages of [SPEC009](SPEC009-program-director.md) choose a passage
when there is a Director and something is eligible. This document is the rest:
what fills the queue at startup, before the Director exists, and when
everything eligible is held by rotation. All of it is current behaviour, read
from `player/src/session.rs`, `player/core/src/db/library.rs` and
`player/core/src/director/library.rs` on 2026-11-01.

> **Related:** [SPEC009](SPEC009-program-director.md) §2 for the diagram ·
> [SPEC037](SPEC037-eligibility-and-frequency.md) for the Stage A rules both
> fallbacks keep or set aside · [SPEC057](SPEC057-holding-a-passage-back.md)
> for holds · [SPEC017](SPEC017-what-counts-as-a-play.md) for rejection
> windows

---

## 1. Startup, in order

**`[SPEC-DIR-225]` Before the Director exists, the queue is what it left behind.** *(Built 2026-09-11.)* `Director::load` runs on its own thread so startup is not blocked by it `[IMPL-SUI-070]`: 9.86 s on the appliance `[IMPL-SUI-075]`, measured at **14.6 s** on lempi02w's library. `Session::prime` runs in about 100 ms, and does three things, in this order:

1. **The resume point** — the passage that was playing, at its saved position — is queued first, unless the guard of `[SPEC-DIR-240]` drops it.
2. **The remembered queue** (`player_queue`, written whenever the queue changes) is queued after it. Passage ids and their order, nothing else: each entry is rebuilt by `Library::passage`, as `hand_over_seamless` rebuilds one on arrival `[SPEC-BK-030]`. Restored entries carry `selected_by = None`, "no selection event to report". A passage renumbered away by a rescan is skipped and said.
3. **A refill.** With the queue restored there is usually nothing to fill. Whatever is still short is filled by the no-Director fallback, §2.

Why the order matters, measured on lempi02w over 2026-08-14 → 09-11: before the restore, **144 of 675** attributed plays (21%) carried no `selection_decisions` row, against 51 of 1,591 (3.2%) on bose, which is redeployed far less. Twelve service starts over two days × five slots predicted about 60 fallback picks; **62** were observed. Each start emptied the queue and the fallback, then a uniform draw, filled every slot.

---

## 2. No Director yet: `Library::fallback_radio`

**`[SPEC-DIR-230]` The fill when there is no Director.** Used only while `Session` holds no Director: in practice the first start of a fresh image, a remembered queue that came back short, or a Director whose load failed. Since 2026-10-08 (the maintainer: "rotation and recovery are set aside, and nothing else"); until then a uniform draw, `random_radio`.

It weighs each radio passage with the Director's own `weigh`, but as a library can without the Director's state:

| kept | set aside |
| :--- | :--- |
| holds `[SPEC-HOLD-010]` and files that failed this player's check `[GDE-INT-135]`, where the catalogue has them | rotation and recovery, at every tier: no ages are read |
| the length and depth filters `[SPEC-DIR-125]`, and the length bonus | **the listener's restraint**, recording and artist: default tuning, no credits |
| the skip and dequeue windows `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]` | the programme: no seeds, no shaping, no flow |
| the occasion multiplier on the listener's local date `[SPEC-DIR-132]`, and `min_weight` | related recordings |
| a passage without flavor last: drawn only once every eligible one with flavor has been `[SPEC-DIR-147]` | |

What is queued, on air or fading in is skipped `[SPEC-DIR-107]`: with no rotation at all, nothing else would stop the passage playing now from being queued again. In rotation's place, **the longest unplayed is the likeliest**: each weight is multiplied by the seconds since its passage or its recording last played, ten years for one never played and at least 60 s. The batch is then drawn **without replacement** in one go (Efraimidis–Spirakis), so nothing spaces the picks *within* one fill: two passages of one recording, or several by one artist, can land together. Nothing is written to `selection_decisions`; each entry is `selected_by = "auto"`.

Nothing eligible: nothing is queued, the reason is logged once, and refill rests 30 s before asking again.

---

## 3. Everything eligible is blocked: `Director::decide_relaxed`

**`[SPEC-DIR-232]` The relaxed fallback.** Called when `Director::decide` returns nothing — after Stage A, no passage is both eligible and neither queued nor on air. A repeat is a better answer than silence `[REQ-PD-100]`. Built 2026-10-08, and used by both `Session::refill` and `mpd_direct`.

- **Stage A, with every age removed.** Passage, recording, work, artist and related-recording ages are all treated as never played, so no rotation block or recovery ramp applies. Everything else stays: holds and failed files, the length and depth filters, the rejection windows, **the listener's restraint** (recording and every credited artist), the occasion multiplier, and `min_weight`. A passage turned all the way down stays out.
- **Stage B as usual**: the seeds of the slot in force when the pick will start `[SPEC-DIR-186]`, Like-Taste, the Dislike filter where there are seeds `[SPEC-DIR-150]`, and a passage without flavor only as the last resort `[SPEC-DIR-147]`.
- **No Stage C, no rank decay.** The draw is over the whole shaped pool, each weight multiplied by the seconds since anything it rotates by — passage, recording, any work, any credited artist — last played or was marked as coming (ten years if never, at least 60 s).
- **Recorded** in `selection_decisions` like any pick, its `stages` saying it was the fallback. Its `rank` is the position in scan order, which means nothing here.

Nothing even then: nothing is queued, the panel's pool reads zero, and refill rests 30 s `[REQ-PD-100]`.

---

## 4. Open

1. **`[SPEC-DIR-235]` A restored queue is not re-weighed.** It was chosen against a history and a programme slot that have since moved, and a hold placed since does not remove it either. Over a deploy that is seconds; over an appliance off for a month it is a month-stale queue, and the slot in force at 09:00 is not the one that chose it at 21:00. The shape of the answer is to restore entries as **provisional** and let `adopt` re-weigh the ones that have not started when the Director lands, since it already walks that list to `note_queued` them. Preferred over a hard staleness cutoff, which would throw away a good queue to avoid a rare bad one.
2. **`[SPEC-DIR-240]` A passage that killed the player comes straight back** — *built 2026-10-14*. Restoring the queue restores the cause of a crash with it. The engine already survives a file it cannot open (`take_dropped` → `forget_queued` `[REQ-PD-112]`), so this is about a panic or an OOM. Each start notes the first passage it will play before opening it (`PlayerStore::note_resume_attempt`), and drops it only when the start before tried the same one without getting more than `RESUME_PROGRESS_MS` (30 s) past it: twice, not once, so a crash unrelated to the passage costs no song. With no resume point saved, the remembered queue's head is what is counted. Only a start that stopped playing counts. The dropped passage leaves the resume point and the remembered queue and nothing else; a refill may draw it again later.
3. **`[SPEC-DIR-245]` Provenance is not persisted with the queue.** `Playback::queued_ids` yields ids, so restored entries record `selected_by = None` rather than the slot or person that chose them, and a restart costs `[REQ-VIS-300]` the attribution of whatever was waiting. Worth doing with `[SPEC-DIR-235]`, which wants the entries and not just their ids anyway.
4. **`[SPEC-DIR-238]` The no-Director fallback ignores the listener's restraint and spaces nothing within a fill.** Both follow from it having no Director state (§2), and both are rare since the queue is restored first. Recorded so the difference from `[SPEC-DIR-232]` is not mistaken for an equivalence.

---

**Traceability:** `[SPEC-DIR-225..245]` · split from [SPEC009](SPEC009-program-director.md) · builds on `[REQ-PD-100]`, `[REQ-PD-112]`, `[IMPL-SUI-070]`, `[IMPL-SUI-075]`, `[SPEC-BK-030]`, `[SPEC-HOLD-010]`, `[GDE-INT-135]`, `[SPEC-PLAY-050]`
