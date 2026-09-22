# SPEC018: Switching Backends Without Stopping

**Design Specification — Tier 2 · measured on the appliance, 2026-08-21**

Why a listening session can move between Lempi's own engine and MPD at all, and
how the two are held so that it can. **What happens at the moment of the
exchange is [SPEC020](SPEC020-the-handoff.md)**, split out of this document once
it had outgrown its length.

> **Related:** [SPEC020](SPEC020-the-handoff.md) is the handoff itself · [SPEC015](SPEC015-mpd-director.md) is the MPD design · [SPEC016](SPEC016-mpd-protocol-findings.md) is what MPD does · [IMPL005](../IMPL005-mpd-prototype-results.md) is what the prototype measured

---

## 1. What the measurements settled

Taken on `lempi02w` — Pi Zero 2W, PipeWire and wireplumber, one Bluetooth
speaker — while it was playing, without stopping anything.

| question | answer | why it decides something |
| :--- | :--- | :--- |
| Can two players hold the sink? | **Yes.** Two clients on MIDDLETON, both `[active]` | handoff is possible at all |
| How fast does a client attach? | **85–114 ms** over three trials | the sink is not the bottleneck |
| Cold Lempi to first audio? | **15 s** (≈10 s of it the Director) | starting a player *is* the bottleneck |
| Memory, and headroom? | 464 MB total, **Lempi 100 MB RSS**, 229 MB free, **no swap** | two Directors will not fit comfortably |
| MPD queue Lempi can name? | **5,518 of 5,709** files | 191 captures cannot be adopted |

**`[SPEC-BK-010]` The first row corrects a published claim.** IMPL004 stated
that one Bluetooth sink cannot be held by two players and that MPD therefore
required stopping Lempi. It was never tested and it is wrong: PipeWire mixes.
Everything below follows from that being false.

---

## 2. The shape: one process, two backends

**`[SPEC-BK-020]` The Director loads once, and the *backend* changes underneath
it.** Not two processes handing a session to each other — one process choosing
where its queue is played.

The measurements force this. A second resident player costs another **100 MB**
against **229 MB free with no swap**, and a *non*-resident one costs **15
seconds** to load before it can make a sound. Neither is acceptable for
something a listener does on impulse. Loading the Director once and switching
the output costs neither.

**`[SPEC-BK-022]` The session drives a backend, not the engine.** *(Built
2026-08-21.)* `Session::refill` takes `&mut dyn Backend` — it publishes,
which sits outside `Playback` — while `tend_rebuild` and `adopt` take the
narrower, read-only `&dyn Playback`. `Engine` satisfies the trait with
nothing in `engine.rs` (now `engine/`, split 2026-09-02, file-organization
only) touched, which was the spike's claim and survives contact.

**The trait had to narrow to carry weight, though.** As written it asked for
`queued() -> Vec<QueueEntry>` — a deep clone of every queued passage, per tick,
where the concrete code handed out borrows. The four call sites want three
**passage ids** and one **queued duration**, so it asks for those instead and
costs a `Vec<i64>`. A seam is only free if it is the shape of what crosses it.

**And the settings do not cross it.** `refill` takes the suppression windows as
an argument rather than asking the backend, because they are the listener's and
are the same whoever is playing. What stayed on `Engine` is exactly what should:
`apply_settings`, `attach_store` — process setup, not playback.

```
Capabilities::FULL  { spans: true, gain: true,  ramps: true  }   Lempi's engine
Capabilities::MPD   { spans: true, gain: false, ramps: false }   MPD as a guest
```

**`[SPEC-BK-027]` Both backends exist, and the session drives either.** *(Built
2026-08-21.)* `MpdBackend` implements the same trait: `enqueue` becomes `addid`
plus a verified `rangeid` `[SPEC-MPD-096]`, `tick` becomes a rate-limited poll,
`take_dropped` reports what MPD would not play, and `shortfall` returns **zero
unless MPD is playing** — so `[SPEC-MPD-120]`'s activation rule needs no special
case anywhere else in the session.

Demonstrated by *not* writing a loop. `mpd_session` opens the `Session` that
`lempi` runs, hands it an `MpdBackend`, and calls `refill`. Against a live MPD
it filled the queue to depth with Director selections — spans intact, including
`2312.672-2578.004` inside a capture — and the census moved as it does locally,
artist-blocked **0 → 180**. The Director, its rotation, its flow and its
bookkeeping reached a backend that is not the built-in one without knowing they
had.

**`[SPEC-BK-028]` One process, and the control is on the settings page.**
*(Built 2026-08-22.)* `lempi --mpd HOST:PORT --mpd-root DIR` attaches MPD as a
guest and keeps playing locally; a select on the settings page moves the session
between them, and the page reports which side is sounding and what the last
switch carried.

The browser cannot reach a backend — they are not `Sync` — so this is the
intent-cell pattern `[IMPL-SUI-075]` already uses for a library reload: the
route records a request and replies **accepted**, and the engine thread performs
it where the backends live. **The control is hidden entirely when no guest is
attached**, because a control that can only be refused is worse than no control.

Measured, both ways, in one process serving the web UI:

```
switch: now on mpd   (faded, 5 passage(s) carried)
switch: now on lempi (faded, 6 passage(s) carried)
```

**`[SPEC-BK-029]` Publishing is a capability of its own, not another playback
method.** *(Built 2026-08-22.)* A guest's clients have no other way to learn why
a passage was chosen, so the Director's reasoning is published to them
`[SPEC-MPD-050]`. Lempi's own UI reads the decision store directly and needs
none of it, so `Publish` sits beside `FadeOut` rather than on `Playback` — a
backend should not have to answer a question only one of them is asked. The
local engine implements it as a no-op and says why.

Published **after** the enqueue, since a sticker is addressed by the URI the
backend has only just chosen, and encoded before the explanation log consumes
the decision.

> **A carried queue arrives without reasoning.** `carry_queue` moves passage
> ids and no more — see `[SPEC-BK-032]` — so the explanations stay behind in the log they were written to.
> So a passage handed to MPD by a *switch* has `lempi.passage` and nothing else
> until the Director next chooses it. Stated because it was found by looking:
> the stickers appeared to work and were in fact fourteen hours stale.

**`[SPEC-BK-025]` Switching backend is not switching player.** The Program
Director, the library, the flavor index, `listener_play_history`, the settings
and the web UI with its three skins are all *above* the seam and do not notice.
Only the thing that turns a queue into sound is exchanged.

---

## 3. What a handoff does

**Moved to [SPEC020: What Crosses a Handoff](SPEC020-the-handoff.md)**, which
is where `[SPEC-BK-030]` through `[SPEC-BK-065]` now live: what travels and what
cannot, the order the two sides are stopped and started in, the measured seek
semantics behind a carried position, and how a passage crossing mid-play avoids
being judged twice.

It left here because this document had outgrown its length `[GOV-DOC-010]`, and
because the seam and the exchange across it are two subjects: everything above
is true whether or not a handoff ever happens.

---
## 4. What this removes from the appliance plan

**`[SPEC-BK-050]` The conversion is cancelled.** IMPL004 treated adding MPD as
turning a Lempi-plays box into an MPD-plays box `[IMPL-MPD-009]`. With one
process and two backends, the appliance stays a Lempi appliance that can *also*
be driven by MPD clients. Three problems that plan carried simply do not arise:

1. **Power-up resume survives.** `[PI5-PWR-030]` requires the appliance to
   resume playing if it was playing, and `[SPEC-MPD-120]` says the Director
   cannot start a session from cold. Under one process the local engine resumes
   as it does today, and MPD is a backend adopted later — never the thing that
   has to start.
2. **The settings page survives**, because the process serving it never stopped.
3. **The audio path supervisor survives.** `[PI3-API-010]`'s reopen-on-default-change
   was built and proved on hardware; keeping Lempi resident keeps it, instead of
   asking MPD to rediscover it.

---

## 5. Settled: resident

**`[SPEC-BK-060]` MPD runs resident.** *(Measured on the appliance 2026-08-23,
MPD 0.23.12.)* The question was whether memory or an indexing pass was the worse
price. Both were measured rather than argued:

| | |
| :--- | ---: |
| MPD resident | **100.8 MB** |
| Lempi beside it | 43.4 MB |
| Available with both running | **264 MB of 464** |
| First index, from nothing | **242 s** for 5,758 songs |
| Start with the database already built | **2.9–4.0 s** |

**Resident, because the memory is affordable and a switch should be immediate.**
264 MB spare is not tight; 101 MB buys an instant handover.

**The 242 seconds is the number that matters, and it is a trap rather than a
cost.** It is only paid when the database is discarded — a warm start is three
or four seconds. So on-demand is a real option if memory is ever wanted back,
*provided the database is kept*; an on-demand MPD that rebuilt its index at each
switch would take four minutes to answer a button.

> **MPD 0.23.12 here against 0.24.0 on the development machine.** Every protocol
> finding in [SPEC020](SPEC020-the-handoff.md) was measured on the newer one.
> `seekid` starting playback from `stop` was re-checked on 0.23.12 and behaves
> the same; the rest has not been re-measured.

It runs as `pi`, not as the packaged `mpd` user, because PipeWire lives in that
session `[PI-CHR-085]` — and it feeds PipeWire rather than ALSA, so both players
reach the same sink instead of fighting for the device.

*(The other two open items are settled: `[SPEC-BK-037]` with `[SPEC-BK-065]`, and
`[SPEC-BK-045]`.)*

---

**Traceability:** `[SPEC-BK-010..029]`, `[SPEC-BK-050]`, `[SPEC-BK-060]` ·
`[SPEC-BK-030..065]` are in [SPEC020](SPEC020-the-handoff.md) · corrects
`[IMPL-MPD-009]` · uses `[GDE-BAK-040]`'s trait · constrained by
`[PI5-PWR-030]`, `[PI3-API-010]`
