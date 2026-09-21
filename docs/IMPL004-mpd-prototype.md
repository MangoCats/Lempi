# IMPL004: Prototyping the MPD Director

**Implementation Guide — the build order for [SPEC015](spec/SPEC015-mpd-director.md), riskiest part first**

> **Related:** [IMPL005](IMPL005-mpd-prototype-results.md) is what it found · [SPEC015](spec/SPEC015-mpd-director.md) is the design · [SPEC016](spec/SPEC016-mpd-protocol-findings.md) is what these stages measured · [GUIDE007](GUIDE007-external-backends-investigation.md) measured the cost · [IMPL003](IMPL003-vipunen-console-build.md) for the same shape of plan

Six stages on `feat/mpd-director`. Each ends in a **measurable claim**, and the order is chosen so that the assumption most likely to be wrong is tested before anything is built on it.

---

## 0. Two things that are true before starting

**`[IMPL-MPD-005]` `lempi-core` is not needed for this.** GUIDE004 wants that extraction for phones, where a separate crate must cross a language boundary `[GDE-AND-045]`. `lempi-mpd` is a binary in *this* crate: it calls `Library::director()` directly, exactly as `lempi` and `station` do. **The port that needs the extraction is not this one**, and conflating them would put a refactor in front of a prototype.

**`[IMPL-MPD-008]` There is no MPD to test against yet, and that is the first decision.** Three candidates, not two, and they are not alternatives so much as tests of different things.

| | tests | audio | risk |
| :--- | :--- | :--- | :--- |
| **MPD on Windows**, native, at the real `Music` tree | the **same-tree prefix** rung, on paths identical to `lempi.db`'s | real | none |
| **MPD in a Linux container** | the **cross-platform** rung — what happens when 276 private-use paths meet a filesystem with real colons `[SPEC-RLK-025]` | awkward | none |
| **MPD on the appliance** | the real deployment | real | **see below** |

Current Windows builds exist and are not an afterthought: **0.24.14, 13 August 2026** — the same day as the Linux release.

**`[IMPL-MPD-009]` The appliance is not an additional test bed. It is a conversion.** This is the consideration that decides the order, and the first draft of this plan missed it.

In the MPD topology Lempi has **no audio path** — MPD owns the device `[SPEC-MPD-030]`.

> **Corrected 2026-08-21, by measurement.** This paragraph used to continue: *the appliance's sink is a single Bluetooth speaker, and two players cannot hold it, so running MPD there means stopping `lempi`.* **That was asserted and never tested, and it is false.** The appliance runs PipeWire, whose business is mixing: two independent clients attach to the one MIDDLETON sink and both report `[active]`. `[PI3-NOT-020]` rules out multiple *sinks*, which is a different question and was misread as this one.
>
> The consequence is not small. Running MPD does **not** require stopping Lempi, the appliance need not be converted from one kind of box to another, and a handoff between the two is available rather than impossible. See [SPEC018](spec/SPEC018-switching-backends.md).

That is a decision about what the appliance *is*, not a test setup. It is also the thing eventually worth proving — a moOde or Volumio owner is in exactly that configuration `[GDE-BAK-080]` — but it should be proved once the design works, not while it is being discovered.

**`[IMPL-MPD-010a]` So: Windows and container for stage 0, Windows through stage 2, the appliance only on purpose.**

Stage 0 needs **no audio at all** — it reads `lempi.db` and an MPD database and writes nothing `[IMPL-MPD-010]` — so the container's weakest point does not apply to it, and running both costs little more than running one. They answer different questions and both answers are wanted.

> **One cost to measure rather than assume.** MPD indexing 7,232 files across a Windows bind mount into a container may be slow enough to matter. If it is, the cross-platform rung can be tested against a **synthetic tree** carrying the real Linux names and no audio — which proves name matching exactly, and gives up only the tag-based rung.

---

## 1. Stage 0 — the mapping, read-only

**`[IMPL-MPD-010]` Nothing else starts first.** Both SPEC015 and GUIDE007 say so, and the reason is that a clean trait over an unreliable mapping is a well-organised way to play the wrong song `[SPEC-MPD-060]`.

Build a tool that reads `lempi.db` and an MPD instance and **writes nothing to either**: for every row in `files`, attempt the resolution ladder — same-tree prefix, then `MUSICBRAINZ_TRACKID`, then unresolved — and report the counts.

> **Claims.** A number for each rung, over all 5,709 files. Specifically: **how many of the 276 private-use paths (4.8%) survive a prefix match against a Linux MPD** — the answer is expected to be *none*, and measuring that is the point. Ambiguity is reported separately from failure: two Lempi rows resolving to one URI is a different problem from a row resolving to nothing.

> **DONE 2026-08-21, Windows, MPD 0.24.14.** Native build against the real
> `Music` tree; MPD indexed **5,758 songs** against the library's 5,709 rows.
> [`player/src/mpd.rs`](../player/src/mpd.rs) is the protocol client —
> `std::net` and nothing else, as `[SPEC-MPD-070]` claimed — and
> [`mpd_map`](../player/src/bin/mpd_map.rs) is the measurement.
>
> | rung | rows | |
> | :--- | ---: | ---: |
> | 1 · same-tree prefix | **5,709** | **100.0%** |
> | 2 · recording MBID | 0 | 0.0% |
> | 3 · unresolved | **0** | 0.0% |
>
> **Ambiguous: 0.** No library row resolves to more than one MPD song.
>
> **All 276 private-use paths resolved.** MPD on Windows reports the *same*
> `U+F03A` substitution Lempi stored, so the two agree without either knowing
> why. That is the same-platform case behaving well; it says nothing yet about
> a Linux MPD, which is the case that will not `[IMPL-MPD-008]`.

> **Cross-platform half, 2026-08-21.** Measured against the appliance's own
> file list — 5,745 URIs as a Linux MPD would report them, captured over ssh
> without installing MPD there `[IMPL-MPD-009]`. A bind-mounted container would
> **not** have reproduced this: Linux reading NTFS sees the same `U+F03A` bytes
> Windows stored. Only a tree whose names were *translated* on the way across
> shows the real condition, and the appliance's is one.
>
> | rung | rows | |
> | :--- | ---: | ---: |
> | 1 · same-tree prefix | 5,433 | 95.2% |
> | 3 · unresolved | **276** | **4.8%** |
>
> **All 276 failures are exactly the private-use paths, and none resolved** —
> the prediction was "none" and it is none. `History: America's Greatest Hits`
> is unreachable from a Windows library by path alone.
>
> **And every one of the 276 carries a real recording MBID** — 276 of 276 — so
> rung 2's ceiling here is total. That is what rung 2 is *for*: rung 1 is
> sufficient on one platform and insufficient across two, which is the case the
> ladder was designed against.

**`[IMPL-MPD-011]` Stage 0 is complete, and the gate is passed.** Same-platform
100%, cross-platform 95.2% by path with the remainder recoverable by recording.
Nothing in the ladder needs redesigning `[IMPL-MPD-015]`, and the one rule it
gained came from measurement rather than worry `[IMPL-MPD-013]`.

**`[IMPL-MPD-012]` The 284 shared MBIDs are not an ambiguity. They are the model
working, and Lempi already implements it.** An earlier reading of this
measurement called them a hazard — the right music in the wrong file — and that
was wrong in both directions.

**Selection is recording-scoped; playback is encoding-scoped.** Two rips of one
recording share an artist, a recording id and a flavor vector, so for rotation,
recovery, restraint and artist blocking they are **one thing**. Which of them
sounds is a lower question, and once it is answered *that file's own*
`start_ms`, `end_ms` and gain govern — because those are properties of the
encoding `[SPEC-DF-040]` and of nothing else.

Confirmed in the code rather than assumed: `last_played` and `artist_of` are
keyed by **mbid**, while `length_bonus` is computed from `c.length_s` on each
candidate **row**, and rows are passages. Recording-scope above, encoding-scope
below, already built.

**Which answers how the encoding gets chosen: it is not a separate step.** The
duplicates enter the roulette as their own candidates and compete, and because
the length bonus reads the encoding's own length, a longer or shorter rip is
weighted on its own terms. Rotation then suppresses the *recording*, so
whichever won, the other is suppressed with it and neither returns early.

**`[IMPL-MPD-013]` One real constraint survives, and it is narrower than the
worry it replaces: the times must come from whatever actually plays.** A mapping
may resolve to any encoding of the right recording. What it may **not** do is
hand MPD a URI for file B while sending `rangeid` computed from file A's
passage — that would apply one rip's trim to another's audio, which is the
encoding-scope rule broken rather than applied.

So the mapping's output is a **pair from one file** — `(uri, passage)` — never a
URI from one and times from another. Where rung 1 resolves, that is automatic.
Where rung 2 resolves by recording, the passage must be re-chosen to match the
song MPD will actually play, and if Lempi holds no passage for that encoding it
has nothing legitimate to send and should say so.

**`[IMPL-MPD-014]` MPD knows 49 songs the library does not.** 5,758 against
5,709, and relink counted a comparable never-indexed tail `[SPEC-RLK-070]`.
Those are ingest's business, not the mapping's `[SPEC-RLK-090]` — but a
`lempi-mpd` that silently ignored them would be hiding a list someone might want.

**`[IMPL-MPD-015]` If the ladder resolves poorly, stop and redesign.** A third rung — `(artist, title, duration)` within a tolerance — is the obvious next idea and is exactly the kind of fuzzy key that produces a plausible wrong answer. It should not be added on instinct; it should be added only if stage 0 shows the first two rungs are insufficient, and then measured for false matches rather than coverage.

---

## 2. Stage 1 — observe, write nothing

**`[IMPL-MPD-020]` A client that connects, watches, and judges.** `idle player playlist options`, `status`, `currentsong`, and the elapsed sampling `[SPEC-MPD-090]` requires — at the configured interval, default 5 s `[SPEC-MPD-105]`.

It reports what it *would* record. It does not write `listener_play_history`, does not touch the queue, and does not set stickers.

> **Claims.** Play tracks by hand from any MPD client and the observer agrees with the scrobbling rule: a track played past half its length (or four minutes) is a play, one skipped earlier is not, and a **12-second passage played whole is a play** — which is where the dropped anti-spam floor is proved rather than asserted `[SPEC-MPD-090]`.

> **DONE 2026-08-21.** All four claims held, and a fifth was found and added: a stop must close the book on a played song, since MPD retains `songid` across it, but a pause must not. See [IMPL005](IMPL005-mpd-prototype-results.md#stage-1----observe-write-nothing) for the measurements — including that MPD's own `duration` cannot be judged against at all (`[SPEC-MPD-092]`, wrong on 36.9% of files), which is why stage 2 replaces it with the passage span rather than trusting it.

---

## 3. Stage 2 — enqueue, without the Director

**`[IMPL-MPD-030]` Prove `rangeid` before proving anything about selection.** This is the single most load-bearing assumption in SPEC015: it is why MPD is the target and OpenSubsonic is not `[GDE-BAK-035]`. Test it with a **trivial random selector**, so a failure here is a protocol failure and not a Director one.

`consume 1`, `addid`, `rangeid <id> <start:end>`, top up to depth `[SPEC-MPD-105]`.

> **Claims.** A passage plays **its span** and stops — verified against a DAO capture, where naming the file would otherwise play forty songs. The queue holds at depth and refills as `consume` drains it. And the etiquette holds under a person's hands `[SPEC-MPD-095]`: adding twenty tracks stops it adding, clearing refills to five, removing a pick produces a **different** one, and reordering is left alone.

> **DONE 2026-08-21.** The span is real — matched to within 1.0 ms across a 400-passage queue — and all four etiquette rules held, unmodified. But `OK` is not evidence: MPD validates a range end against its own (unreliable) duration estimate, drops the end silently when it's exceeded, and plays to EOF anyway — **508 of 7,994 passages (6.4%)**. Every `rangeid` is now read back and checked against the span requested `[SPEC-MPD-096]`. See [IMPL005](IMPL005-mpd-prototype-results.md#stage-2----enqueue-without-the-director) for the measurements.

---

## 4. Stage 3 — the Director drives

**`[IMPL-MPD-040]` Swap the random selector for `Director::decide`.** The exclusion set is the queued passage ids and the flow tail is the last of them `[SPEC-DIR-160]`, which is what `Session::refill` already does — this stage is mostly about *not* reimplementing that.

**`[IMPL-MPD-045]` And the bookkeeping is the part to get right, not the selection.** `note_queued` when a passage is added; `forget_queued` when one leaves without playing `[REQ-PD-112]`. Locally that is driven by `take_dropped`; here it is a queue diff after `idle playlist`, and a deletion by a person must reach the same place as a file that would not open.

> **Claims.** Over a session, the census `[GDE-PD-010]` behaves as it does locally: rotation suppresses what has just played, artists block, and a passage deleted from the queue by hand does **not** stay suppressed *by its queueing mark*.
>
> *(The last clause was written before `[SPEC-PLAY-055]`. Undoing the queueing mark and forgiving the removal are now two different things: the mark comes off, and a dequeue earns its own 18-hour window. Stage 3 writes nothing, so the window is reported rather than applied.)*

> **DONE 2026-08-21.** The census moved as it does locally — 280 artist-blocks over one five-passage session — with **no selection logic reimplemented**. The queue diff turned out to be the whole difficulty, and it comes down to one bit: whether a departing song was ever the current one, which only the sampler can know, since `consume` retires a skip exactly as it retires a finish. See [IMPL005](IMPL005-mpd-prototype-results.md#stage-3----the-director-drives) for the measurements.

---

---

## 5. Stage 4 — write back, and publish

**`[IMPL-MPD-050]` Plays into `listener_play_history`; nothing into anyone's scrobbler** `[SPEC-MPD-100]`.

**`[IMPL-MPD-055]` Stickers under `lempi.`** — `lempi.passage` as the mapping cache, then `lempi.why`, `lempi.flavor`, `lempi.chosen_at` `[SPEC-MPD-050]`.

> **Claims.** The mapping cache measurably shortens a second run. A sticker-aware client displays the explanation with **no change to that client**. And the class-D guarantee holds in the direction that matters here: `listener_play_history` grows, and nothing Lempi writes leaves the machine.

> **DONE 2026-08-21.** Writes land correctly divided across three outcomes (play / skip / dequeue), stickers publish and read back through the ordinary protocol, and the class-D guarantee held. **One claim was too generous:** the sticker cache saves ~26 ms per *process*, not per lookup, and saves nothing at all in the same-tree case since rung 1 needs no MPD call — it pays off only where rung 1 fails, which stage 0 measured at 4.8% of this library. See [IMPL005](IMPL005-mpd-prototype-results.md#stage-4----write-back-and-publish) for the measurements.

---

## 6. Stage 5 — settings and containment

**`[IMPL-MPD-060]` The two parameters reach the settings page** `[SPEC-MPD-105]`, following `[REQ-VIS-155]`'s pattern — persisted on change, bounds in the snapshot. Queue depth is a **promotion** of today's `--depth` flag and applies to the local engine too.

**`[IMPL-MPD-065]` Then the feature gate, last.** `--features mpd`, `std::net` only, and the check that matters: **the appliance binary is byte-identical to a build from before this branch** `[GDE-BAK-050]`. Gating first would mean five stages developed behind a flag nobody had turned on.

> **DONE 2026-08-21.** Both parameters reached the settings page, persisted and bounded, and are read by `mpd_direct` with the CLI flags demoted to a test-run override. **What it found:** see [IMPL005](IMPL005-mpd-prototype-results.md#stage-5----settings-and-containment) — including a real defect this stage's own test-writing caught: `skip_suppress_h`/`dequeue_suppress_h` were declared, serialised and read by the skin, but never actually published, so the settings page would have silently shown "0 hours" for windows genuinely held at 156/18. Fixed the same day. The gate holds — with the feature off, the appliance binary contains no MPD protocol at all; **the byte-identical-binary claim does not**, because this branch deliberately changed the local player as well (the scrobbling alignment and skip/dequeue suppression are now global, not MPD-only).

---

## 7. What could stop this

**`[IMPL-MPD-070]`** Named now so they are recognised rather than discovered:

1. **The mapping resolves badly** `[IMPL-MPD-010]`. The most likely failure and the reason it is stage 0.
2. **Two writers, one queue.** A person and a daemon both editing means races the design discusses `[SPEC-MPD-095]` but has not implemented. MPD's `status` carries a **playlist version**; using it to detect a change between read and write is the obvious guard and is untried.
3. **`rangeid` may behave differently than documented** across MPD versions, or with some decoders. Stage 2 exists to find that out early.
4. **The appliance is a poor test bed while it plays** — see the logistics note above.

---

**Traceability:** `[IMPL-MPD-005..070]` · implements `[SPEC-MPD-010..120]` · under `[GDE-BAK-100]`
