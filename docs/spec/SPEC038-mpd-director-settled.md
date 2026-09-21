# SPEC038: The MPD Director — What Is Settled

**Specification — the decisions taken, and the measurements behind them**

Split from [SPEC015](SPEC015-mpd-director.md) on 2026-09-10, which had
reached 320 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [SPEC015](SPEC015-mpd-director.md) for the shape and the
> containment

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §7 in [SPEC038](SPEC038-mpd-director-settled.md), the rest in [SPEC015](SPEC015-mpd-director.md).

## 7. Settled

**`[SPEC-MPD-090]` A play is a play by the rule every path shares: half the
passage, or four minutes, whichever comes first** — defined once in
[SPEC017: What Counts as a Play](SPEC017-what-counts-as-a-play.md) and imported
here rather than restated. *(Decided 2026-08-21; promoted out of this document
the same day, once it was settled that the local engine obeys it too
`[SPEC-PLAY-030]`.)*

**One deviation, deliberate.** Last.fm additionally ignores tracks under 30
seconds and ListenBrainz under 5. That floor is an *anti-spam* rule about
fraudulent submissions to a public service, and it does not apply to a private
rotation ledger. Lempi's shortest radio passage is **12 seconds**
`[SPEC-SA-090]`; one that played in full did play, and excluding it would
suppress nothing but the truth. **The threshold is adopted; the minimum-length
exclusion is not.**

> **Mechanically, elapsed must be sampled rather than read at the end.** `idle
> player` fires *after* the change, when `currentsong` and `elapsed` already
> describe the new song, and `consume 1` removes a skipped song exactly as it
> removes a finished one. So `lempi-mpd` polls `status` at a low rate while
> playing and keeps the last known elapsed, then judges the outgoing passage
> against it. This is the one place the design polls, and it is why.

**Three things MPD does that its documentation does not lead you to expect** —
an unreliable `duration` `[SPEC-MPD-092]`, a `rangeid` that can return `OK`
without honouring the span `[SPEC-MPD-096]`, and a `songid` retained across a
stop `[SPEC-MPD-094]` — are measured in
[SPEC016: What MPD Actually Does](SPEC016-mpd-protocol-findings.md). They are
kept apart because they change when MPD changes, not when Lempi's intent does.

**`[SPEC-MPD-105]` Both tunables are the listener's, edited on the settings page
and remembered.** *(Decided 2026-08-21.)*

| Parameter | Default | Meaning |
| :--- | ---: | :--- |
| **queue depth** | **5** | how many passages the Director keeps ahead. **At or above it, the Director adds nothing** `[SPEC-MPD-095]`. |
| **status sample interval** | **5 s** | how often `status` is read while playing, to judge a play against `[SPEC-MPD-090]`'s threshold |

They follow the pattern the player already has for skip fade, skip lead and
resume-save `[REQ-VIS-155]`: written the moment a control moves rather than on a
timer, persisted in `player_state` beside the three columns already there, and
with their **bounds carried in the snapshot** so the control offers exactly what
the engine accepts rather than keeping a second copy of the limits.

**Queue depth is a promotion, not a new setting.** It exists today as
`lempi --depth N`, defaulting to 5, reachable only by editing a service file.
Moving it to the settings page makes it adjustable on an appliance whose only
interface is a web page.

**Built 2026-09-03: both tunables are live for whichever backend is sounding,
not only the local engine.** `Playback::apply_queue_settings` (a tenth method
on the trait, defaulted to nothing for a backend with no such state) is called
once per pass of `lempi`'s own loop with the settings page's current values,
read back from the same published `Snapshot` the engine already keeps them in.
`Switching` forwards the call to **both** sides, mirroring `tick`'s own
reasoning: a switch must not leave the side just vacated holding a stale
value, or switching back would show it briefly wrong. The local engine's own
`Command`/`EngineHandle` path is unaffected and still works exactly as before
— `Engine` relies on the trait's default no-op here, since its own channel
already reaches it whether or not it is the side sounding.

**The sample interval has a floor worth respecting.** `[SPEC-MPD-110]` is why:
five seconds resolves a four-minute rule easily and a 12-second passage badly,
so the useful range is small at the bottom and the cost of the default being
wrong is a misjudged play rather than a missed one.

**`[SPEC-MPD-095]` The queue belongs to whoever is in front of it. The Director
only ever adds, and only ever below the minimum depth.** *(Decided 2026-08-21.)*
MPD's queue is shared, and a person editing it is not an error to be corrected.

| A person… | The Director… |
| :--- | :--- |
| adds twenty tracks | **adds nothing** — the queue is above depth |
| reorders | leaves the order alone; reads the tail for flow `[SPEC-DIR-160]` |
| removes one of Lempi's picks | tops back up to depth, **with a fresh choice** |
| clears the queue | refills to the minimum, five `[SPEC-MPD-035]` |

**It never removes, never reorders, and never re-adds what was taken out.**
Putting a rejected pick back is the one behaviour that would read as the machine
arguing with the listener.

**And a removed pick must be un-counted.** `note_queued` marks a passage as
recently played so rotation suppresses it while queued; if a person deletes it
before it plays, `forget_queued` has to run or one deletion suppresses that
recording and its artist for a full rotation `[REQ-PD-112]`. Locally that is
driven by `take_dropped`; here the trigger is a queue diff after `idle
playlist`, and the two must reach the same bookkeeping.

**`[SPEC-MPD-100]` `lempi-mpd` does not scrobble.** *(Decided 2026-08-21.)*
MPD's ecosystem already carries scrobblers — `mpdscribble`, `mpdas`, and several
clients — and a second submitter would duplicate every listen. Lempi writes its
**own** `listener_play_history`, which is a different ledger for a different
purpose: rotation input, not a public record `[SPEC-DF-055]`. Sharing the
threshold with the scrobblers `[SPEC-MPD-090]` is what keeps the two agreeing
about what happened without either writing the other's data.

---

**`[SPEC-MPD-110]` One interval serves the judgement; only a known deadline earns
a tighter one.** *(Settled 2026-08-21, by measurement; built 2026-09-03.)* Across
8,330 radio passages the median is 241 s, so five seconds is about 4% of a
typical threshold. The interval exceeds **half** the threshold for
**7 passages (0.1%)** and a quarter of it for 37 (0.4%). Sampling *uniformly*
faster to serve seven passages was rejected as complexity bought at the wrong
price — the fixed interval is still the baseline rate.

The pressure the original question anticipated turned out to come from elsewhere.
For **judgement** a late sample only risks calling a play a skip, bounded and
rare. For the **span end** `[SPEC-MPD-096]` the same interval is an *absolute*
overrun of unwanted audio on 6.4% of passages. So the rule is not a smaller
global interval but a local one: sample at the configured rate, and when a
**known boundary** is close — the span end, or the play threshold of an
unusually short passage — sample to meet it. A deadline that is known is worth
sampling for; a uniformly faster clock is not.

`MpdBackend::effective_interval()` (`player/src/mpd_backend.rs`) implements
exactly that: estimated from the last poll's own position plus wall-clock time
elapsed since — MPD is not asked again just to answer this — it returns the
configured interval unchanged until either deadline is within it, then tightens
to meet the nearer one, floored at 250 ms so a boundary a few milliseconds away
cannot turn "sample to meet it" into a busy loop. The 6.4% span-end overrun
`[SPEC-MPD-096]` measured is what this exists to shrink; it was not re-measured
after this change.

**`[SPEC-MPD-115]` A person's own additions feed rotation — and must clear
`[SPEC-MPD-090]`'s threshold like anything else.** *(Settled 2026-08-21,
corrected the same day.)* The question hides two, and they have different answers.

***Whose* picks count: all of them.** The local engine records what is playing
without ever consulting who queued it, and `listener_play_history` records
listening rather than deciding.

***Whether* a play happened: `[SPEC-PLAY-010]`'s rule.** An earlier draft said
additions count "exactly as the local engine counts them", which was false at the
time: the engine wrote a play the moment a passage began sounding, so a
ten-second skip counted locally and not through MPD. **The engine now obeys the
same threshold** `[SPEC-PLAY-030]`, and the sentence is true because the code
changed, not because the wording did.

> **One table, one rule.** Both paths call the same function `[SPEC-PLAY-030]`,
> so `listener_play_history` means the same thing whichever player wrote it. A
> passage the listener *declined* is held out of selection on its own
> account — see `[SPEC-PLAY-050]` — which is a suppression and not a play.

**`[SPEC-MPD-120]` The Director is active only while MPD is playing.**
*(Settled 2026-08-21.)* `state: play` and below depth is the entire activation
condition. Stopped or paused is a person with their hands on the queue, and
appending then is the fight this rule exists to prevent. No switch is added: the
transport control the listener already uses *is* the control.

Verified against a live MPD, and the model closes on itself — **clearing a queue
while playing stops MPD**, so the gesture that means "leave it alone" and the
gesture that means "stop" are the same one. Pausing goes quiet even as the queue
drops below depth; resuming refills within one interval.

> **Consequence, accepted: the Director keeps music going but cannot start it.**
> `play` on an empty queue returns `OK` and leaves MPD stopped, so from cold there
> is nothing to be active *during*. Someone must supply the first passage. The
> skin therefore owes an explicit **start** action that primes the queue and
> plays — person-initiated, so it does not weaken the rule — and until it exists
> the MPD path begins by hand.

---

