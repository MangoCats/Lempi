# IMPL005: What the MPD Prototype Found

**Implementation Record — the measured outcome of [IMPL004](IMPL004-mpd-prototype.md)**

> **Related:** [IMPL004](IMPL004-mpd-prototype.md) is the plan · [SPEC015](spec/SPEC015-mpd-director.md) is the design · [SPEC016](spec/SPEC016-mpd-protocol-findings.md) is what MPD itself turned out to do

Six stages, each ending in a claim that could fail. Split out of IMPL004 under
`[GOV-DOC-010]` once the results outgrew the plan they belonged to — and they
belong apart anyway: the plan changes when the *intent* changes, this record
only when something new is measured.

**Three claims did not survive contact**, and they are the reason this document
is worth more than the plan: MPD's `duration` `[SPEC-MPD-092]`, `rangeid`
returning `OK` without honouring a span `[SPEC-MPD-096]`, and a mapping cache
that saves far less than it was credited with. Each is recorded where it was
found rather than quietly corrected.

---


## Stage 1 -- observe, write nothing

**All four claims hold, and a fifth had to be added**

`mpd_watch` against MPD 0.24.14, five scenarios driven over the protocol, 2 s sampling:

| scenario | verdict |
|---|---|
| seek to 171 s of 334 s (needs 167.0), then `next` | PLAY |
| 8 s of a 231 s track (needs 115.7), then `next` | skip |
| seek past half, **pause 8 s**, resume, then `stop` | PLAY — one continuous watch |
| the real 12.0 s file, played whole | **PLAY** |
| stopped and idle | nothing |

Two defects surfaced only under live play, neither reachable by unit test.

**`[IMPL-MPD-022]` A stop is not an absent song.** MPD **retains `songid` across a stop**, so watching the id alone left a track the listener stopped 57% of the way through unjudged — and unjudged *forever* if no later song started. A stop must close the book; a **pause must not**, since elapsed holds still and the listener is coming back.

**`[IMPL-MPD-024]` MPD's `duration` cannot be judged against.** It is an estimate, not a decode, and for a VBR MP3 it is size over bitrate — so embedded cover art inflates it. A 12.07 s track carrying a picture was reported as **22.8 s** (546659 × 8 ÷ 192000), and a track that played *in full* was recorded as a skip.

This is not a corner case. Comparing all 5,373 files MPD and Lempi both know:

| | files | share |
|---|---:|---:|
| agree within 1 s | 3,388 | 63.1% |
| **disagree by more than 1 s** | **1,985** | **36.9%** |
| — MPD too long | 1,414 | |
| — MPD too short | 571 | |

Median error among the disagreements is **98.8 s**; the worst is `+3421 s` on a 1457 s track. **1,530 of them move the play threshold.** Errors run in *both* directions, so no correction factor rescues it.

> **Consequence for the design.** The observer is not optional-database. Lempi's decoded duration is the reference and MPD's is the fallback, which makes stage 0's mapping ladder load-bearing at stage 1 rather than merely at stage 2 — the URI must resolve before a verdict can be trusted. Stage 2 replaces even the file duration with the passage span, authoritative by construction `[SPEC-DF-030]`.

A verdict resting on MPD's estimate prints a `~` after the length, so a weaker claim looks weaker.

---


## Stage 2 -- enqueue, without the Director

**`rangeid` holds, with one condition worth the whole stage**

`mpd_fill` — uniform-random selector, `consume 1`, `addid` then `rangeid`, top up to depth.

**The span is real.** A passage at `2686.740-2958.564` inside a multi-hour capture ran to 271.3 s of its 271.824 s span and then advanced; the remaining ~7,000 s of that file did not play. Across a 400-passage queue every range matched a real passage span to within **1.0 ms**, 127 of them mid-capture, and MPD's `Time` equalled the span every time.

**The etiquette holds**, all four, unmodified:

| a person… | the filler |
|---|---|
| adds twenty tracks (queue 22) | added **0** over four poll intervals |
| clears the queue | refilled to **5** |
| deletes one of its picks | replaced it with a **different** passage |
| reorders (`move 0 4`) | left the order exactly as chosen |

**`[IMPL-MPD-032]` And `OK` is not evidence.** MPD validates a range end against *its own* duration estimate — the one stage 1 established is wrong on 36.9% of files. Where the end exceeds it, MPD returns `OK`, **drops the end**, reports a shortened `Time`, and plays to EOF anyway.

I watched it happen: a passage due to end at 136.2 s kept playing past its claimed 79.16 s duration and ran to the file's 146.7 s end. **508 of 7,994 passages (6.4%)**, median overrun 11.2 s, worst 532 s. A live run enqueuing 300 passages flagged 18 — 6.0%, as predicted.

Every `rangeid` is now read back and compared against the span requested `[SPEC-MPD-096]`, `[GOV-SRC-030]`. This is the second stage running to find that a single unreliable number — MPD's `duration` — corrupts something new. It is worth expecting a third.

---


## Stage 3 -- the Director drives

**The Director drives, and the diff tells the two departures apart**

`mpd_direct` — `Director::decide` with the queued ids as the exclusion set and the last of them as the flow tail, which is `Session::refill`'s call unchanged. **No selection logic is reimplemented**, and that was the point of the stage.

**The census moves as it does locally.** Over one session, with five passages offered:

| | before | after |
|---|---:|---:|
| eligible | 8,089 | 7,810 |
| artist-blocked | 0 | **280** |

`note_queued` marks the recording and its artist as each passage is offered, so artists block for the rest of the session — the behaviour `[GDE-PD-010]` describes, reached without touching the Director.

**`[IMPL-MPD-047]` The queue diff is the whole of the difficulty, and it turns on one bit.** A song id leaving MPD's queue means two opposite things, and MPD reports the departure identically either way. The distinguishing fact is whether that song was *ever the current one* — which only the sampler can know, because `consume` retires a skipped song exactly as it retires a finished one. Both branches observed:

```
played or skipped [9440] 4                    <- reached the front; the mark stands
played or skipped [9830] CatchAFire
removed by hand   [2274] I Heard It Through…  <- never played; mark undone
```

The queue refilled to depth after each departure. A passage that never reached the front has its `note_queued` undone `[REQ-PD-112]`, so the Director stops believing it was heard — the same place a file that would not open reaches locally, arrived at by a different road.

---


## Stage 4 -- write back, and publish

**It writes, it publishes, and one claim was too generous**

Run against a **copy** of the library, `--write` opt-in so stage 4 cannot happen by accident.

**The ledger fills, correctly divided.** A passage played past its threshold reached `listener_play_history` (37,238 → 37,239, `PLAY [12782] 206s of 396s`); a passage stopped at 5 s of 724 s went to `listener_rejections` as a **skip**; one deleted from the queue unheard went there as a **dequeue**. Three outcomes, three destinations, one rule deciding `[SPEC-PLAY-010]`. The original library was untouched throughout, which is how the copy earns its keep.

**And the loop closes.** The next run's census opened with **2 suppressed** — the skip and the dequeue just written, now holding those recordings out. Nothing was wired to make that happen; the Director reads the tables it always read.

**Stickers publish.** `lempi.passage`, `lempi.chosen_at`, `lempi.flavor` and a 1,318-byte `lempi.why` land on the URI and read back through the ordinary protocol. The flavor summary is composed from class names, so it says `electronic · not aggressive · instrumental` rather than a vector.

**`[IMPL-MPD-057]` A person's own additions are adopted, or refused by name.** `[SPEC-MPD-115]` settled that they feed rotation, which needs the URI resolved backwards. A hand-added single-song file was adopted and its play recorded. A hand-added **DAO capture was refused** — 5,518 of 5,709 URIs name exactly one radio passage, and the other 191 name up to forty. Guessing one would attribute a play to a passage nobody heard, so it is reported instead `[SPEC-MPD-060]`.

**The cache claim was too generous, and is narrowed.** A sticker lookup costs **0.072 ms**; the `listallinfo` it would replace costs **26 ms** once, for 4.3 MB. So the cache saves ~26 ms per *process*, not per lookup — and in the same-tree case it saves nothing at all, because rung 1 is a string prefix that needs no MPD call. **It pays only where rung 1 fails**, which stage 0 measured at 4.8% of this library and 100% of a cross-platform install. The sticker is kept regardless, because it is also *published data* a client can read — but it was specified as a cache and it is barely one.

---

## Stage 5 -- settings and containment

**Both parameters reach the page, and the containment claim needed restating.**

**`[IMPL-MPD-062]` The queue depth is a listener setting now, not a launch flag**, and it governs the local engine as much as the MPD one — a promotion, as `[IMPL-MPD-060]` asked. It joins the guest sampling interval on the settings page, both persisted and bounded, both read by `mpd_direct` with the command-line flags demoted to an override for a test run.

Two of the settings this branch added were **declared, serialised, read by the skin, and never published** — `skip_suppress_h` and `dequeue_suppress_h` were assigned nowhere in `publish`, so the page would have offered a confident **0 hours** for windows the engine actually held at 156 and 18. A control showing a value the engine does not hold is worse than one showing nothing, because it invites a person to trust it. Found by writing the test, not by reading the code.

**`[IMPL-MPD-067]` The gate holds, and the byte-identical claim does not.**

With `--features mpd` off, no MPD binary is produced and the appliance binary contains **no MPD protocol at all** — `listallinfo`, `rangeid`, `sticker set song` and `OK MPD` each occur zero times. That is the property `[SPEC-MPD-070]` actually needs.

| build | `lempi.exe` |
| :--- | ---: |
| feature off | 7,371,776 bytes |
| feature on | 7,379,968 bytes |

Enabling the feature grows the appliance binary by **8,192 bytes** — one page — and *none* of it is MPD code, by the same string search. It is padding from a larger `rlib`, not capability the listener did not ask for.

> **But `[GDE-BAK-050]`'s claim — byte-identical to a build from before this branch — is false, and should not be repaired.** The two differ, because this branch deliberately changed the **local player**: the scrobbling alignment `[SPEC-PLAY-010]`, skip and dequeue suppression `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]`, and the settings promotion above. That is 831 inserted lines across fifteen files that have nothing to do with MPD.
>
> The claim was written when this branch was expected to be purely additive. It stopped being that the moment the scrobbling rule was made global, which was the right change. **The honest restatement is the one measured here:** a listener who does not want MPD compiles without the feature and gets a binary containing none of it.

---

**Traceability:** results for `[IMPL-MPD-010..067]` · plan in [IMPL004](IMPL004-mpd-prototype.md) · protocol findings in [SPEC016](spec/SPEC016-mpd-protocol-findings.md)
