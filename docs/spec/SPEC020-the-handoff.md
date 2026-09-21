# SPEC020: What Crosses a Handoff

**Design Specification — Tier 2 · built, and measured in both directions**

Moving a listening session between backends without stopping it: what travels,
what cannot, how the two sides are ordered so there is no silence between them,
and how a passage that crosses mid-play is judged exactly once.

Split out of [SPEC018](SPEC018-switching-backends.md) on 2026-08-22, which had
grown past the length a document is meant to hold `[GOV-DOC-010]`. That document
keeps *why* there are two backends and how they are held; this one is what
happens at the moment of the exchange.

> **Related:** [SPEC018](SPEC018-switching-backends.md) for the seam itself ·
> [SPEC016](SPEC016-mpd-protocol-findings.md) for what MPD will and will not carry ·
> [SPEC017](SPEC017-what-counts-as-a-play.md) for the rule a crossing passage is judged by

---

## 1. The exchange
**`[SPEC-BK-030]` The outgoing backend stops sounding; the incoming one starts.**
Both may hold a PipeWire stream at once, so the changeover *can* be a crossfade
rather than a gap — the same `[REQ-AUD-158]` shape a skip already uses, and the
reason it is available is the first measurement above.

`switch_to_over` stops the outgoing side **before** switching — after would
silence the side just arrived at — and returns `Faded` or `Cut`.

**`[SPEC-BK-033]` The local fade is `skip` with nothing to skip to.** *(Built
2026-08-21.)* Emptying the queue first means `admit_due` promotes nothing and
the transition has no incoming audio to overlay, so the ring fades out and stays
out — `skip`'s own comment had said as much long before a handoff wanted it.
Reusing that path is the decision, not an economy: there is **one** place in this
engine that takes the ring from sounding to not, with one curve `[REQ-AUD-158]`
and one set of accounting `[XFD-ORTH-020]`, and a handoff has no business owning
a second idea of what a fade is.

| side | fades when |
| :--- | :--- |
| Lempi's engine | there is an output ring and something sounding |
| MPD | its output plugin has a mixer `[SPEC-MPD-099]` — PipeWire yes, `null` no |

Neither fades unconditionally, and **which happened is reported**: saying
`Faded` over a cut is the failure `[PI3-API-030]` names. The engine reports
`Cut` on a silent path rather than flattering itself, and the listener's own
skip shape is borrowed and given back.

**`[SPEC-BK-032]` The queue crosses as passage ids, and is rebuilt on arrival.**
*(Built 2026-08-21.)* `Session::hand_over_seamless` switches the side, reads each carried
id back out of the library, and enqueues it into the incoming backend. **Spans
are re-derived rather than carried**: `start_ms`, `end_ms`, gain and ramps
belong to the passage, not to whichever backend last played it, and handing over
a built entry would have carried the outgoing side's idea of the passage into
the next one.

The transfer lives on the session because the session is the only thing holding
a library; a backend has no business holding one. A passage the library has
renumbered away since the queue was built is skipped and named, and the
Director's queueing mark for it is undone `[REQ-PD-112]` — it never played.

Demonstrated against a live MPD and a **real** `Engine` on a silent output: MPD
holding three Director-selected passages, `hand_over_seamless` to the local side, all
three rebuilt and queued there, and the reported capabilities flipping from
`gain false ramps false` to `gain true ramps true` in the same breath.

**`[SPEC-BK-035]` Position and queue transfer, as far as they can.** Both now
do, in both directions `[SPEC-BK-065]`. **Lempi → MPD is exact**; **MPD → Lempi
is not, and must not pretend to be**: 191 of 5,709 files carry more than one
radio passage, and a whole-file entry could be any of up to forty of them. Those
are reported, never guessed `[SPEC-MPD-060]`.

**`[SPEC-BK-047]` What a handoff reports carried is what the incoming backend
took, not what the library could build.** *(Settled 2026-08-23.)* `enqueue`
returns nothing and cannot refuse in line; a backend that will not take a
passage says so by dropping it, and `carry_queue` must ask. Counting a
successful `build` instead made the report **unfalsifiable** — it read the same
whether the passage arrived or vanished.

Found on the appliance, where a handoff into an MPD whose socket had died
announced *"6 passage(s) carried"* into an empty queue, and went on announcing
it for as long as the connection stayed dead `[PI-CHR-095]`. A report that
cannot come out wrong is not evidence `[PI3-API-030]`.

**`[SPEC-BK-045]` An MPD entry Lempi cannot name is dropped from the switch,
not blocked by it.** *(Settled 2026-08-21.)* 191 of 5,709 files carry more than
one radio passage, so a whole-file entry can be unnameable through no fault of
the listener. Letting those veto a handoff would put a rare and invisible
property of the *library* in charge of an action a person just asked for.

The handoff therefore takes what it can name and leaves the rest behind,
**saying which** — a dropped entry is reported once, because silently shortening
someone's queue is the kind of quiet wrongness `[PI3-API-030]` exists to
refuse. What is dropped is dropped from the *queue*, not from the library: the
passage is as playable as it ever was, and the next thing that names it will
get it.

**The rule lives in the guest, where the names are** *(corrected 2026-08-22)*.
An earlier draft described a `switch::adopt_queue` the handoff called on the way
past; it was written and never wired to anything, because the resolution needs
the URI → passage map, which belongs to the backend. `MpdBackend::queued_ids`
now resolves MPD's whole queue — read per poll from `playlistinfo`, already being
called to find departures — through the same `names` map that adopts a person's
own additions `[SPEC-MPD-115]`. `adopt_queue` is deleted.

**Order is part of the rule.** `playlistinfo` returns the queue in the
listener's order, and that order is what `carry_queue` re-enqueues in. It was
being read out of a `HashMap` and arrived shuffled, so moving back to Lempi
rearranged what was coming up — fixed with the same change.

> **This asymmetry matches what is asked of it.** Seamlessness is wanted
> Lempi → MPD and merely desirable MPD → Lempi — the direction that cannot be
> exact. Requirement and constraint agree, which is luck rather than design.

**`[SPEC-BK-037]` The passage that was sounding is judged as it stops, exactly
as a skip is.** *(Settled 2026-08-21.)* No new rule, and that is the finding:
`[SPEC-PLAY-010]` already asks whether enough was heard, and a handoff is just
another way for a passage to stop being heard. It is judged once, by the side
it was sounding on, against what that side observed.

This was written when **the sounding passage did not cross**: what crossed was
the queue `[SPEC-BK-032]`, and a queue does not include what is already playing.
It named the price of doing better — "the fix is not to carry it but to hand over
what was already heard along with it" — and that price has since been paid
`[SPEC-BK-065]`. The passage now crosses, with its position, and the two-sided
judgement it warned about is settled below rather than avoided.

**`[SPEC-BK-040]` What a listener loses by moving to MPD is stated before they
move, not discovered after.** `Capabilities::MPD` drops `gain` and `ramps`: per
passage gain cannot be expressed at all; lead-in/lead-out degrade to one global
crossfade number against a median lead-out of 946 ms `[SPEC-SC-043]`; and
`fade_in_ms`/`fade_out_ms` `[SPEC-SC-046]` — the envelope actually heard on
every passage, crossfade or not — has no MPD equivalent at all, since
`crossfade`/`mixrampdb` only blend between two adjacent files. The control
that offers the switch says so, once, in those terms.

**`[SPEC-BK-055]` MPD's clock on a bounded song runs from the start of the
span, not of the file.** *(Measured 2026-08-22, MPD 0.24.0.)* This decides what
a position means as it crosses, and the two conventions differ by minutes, so it
was asked rather than assumed:

| set up | `duration` | `elapsed` after `play` | after `seekid 5` |
| :--- | ---: | ---: | ---: |
| plain file, no range | 295.750 | 1.140 | — |
| same file, `rangeid 60:120` | **60.000** | **1.163** | **5.775** |
| cue track (`Range 417.8–658.7`) | **240.827** | **1.140** | **20.775** after `seekid 20` |

**So a range and a cue track both run their own clock from zero**, exactly as a
Lempi passage does — a position is a position within the span on both sides, and
crosses unaltered. And **seeking past the span stops the song**: `seekid 70` into
a 60-second range returned `state=stop`, so a carried position must be bounded
rather than merely sent. Starting is quick enough for an overlap to be worth
having: `addid` to first frame past the seek is **14–27 ms, median 17** over
eight trials — a floor, being a null output, but far inside the ring either way.

**`[SPEC-BK-065]` A passage crosses mid-play, and the incoming side is audible
before the outgoing one is silenced.** *(Built 2026-08-22.)* The order is the
whole of it: read the outgoing head **and** queue, build them into the incoming
side while the other still sounds, say where to start, wait until it really
sounds, and only then fade — fading first would be silent for as long as the
other side took to load. Three things this had to get right:

* **A handoff is not a rejection.** The local fade *is* `skip` (`[SPEC-BK-033]`
  above), and a skip below the threshold earns a 156-hour suppression
  `[SPEC-PLAY-050]` — so uncorrected, changing rooms would suppress the song
  still playing in the other one. `FadeOut::hand_off` carries the
  distinction, as a latch consumed at the departure: the head does not leave
  until the fade finishes, several ticks later.
* **Nor is it a second play**, which `[SPEC-BK-037]` predicted. The incoming
  side's accounting is right unaided — its clock starts where the passage
  arrived, so time heard elsewhere is included — but only if the outgoing side
  has not already recorded it. When it has, the passage is adopted as counted
  and earns neither another play nor a rejection.
* **A passage nearly over is not carried**, under two seconds left. It would
  arrive already finished, so the handoff starts at the next one and says so.

**Measured end to end, both directions**, MPD 0.24.0 against the live library:
Lempi → MPD resumed a cue track *inside a capture* at **30.4 s into its own
span**, playing and advancing; MPD → Lempi at **46.8 s after 249 ms**. Six
passages carried each way; no rejection and no play written by either crossing.

> **Continuity is the promise, not sample alignment.** Two independent players
> share no clock. The incoming side starts 250 ms ahead, covering its own start
> and the outgoing fade, so nothing is repeated and nothing is skipped. Being
> wrong by all of it is a fraction of a second, once.

---

---

**Traceability:** `[SPEC-BK-030..065]` · held by `[SPEC-BK-020]`'s seam ·
guest limits from `[SPEC-MPD-052]` · judged under `[SPEC-PLAY-010]`
