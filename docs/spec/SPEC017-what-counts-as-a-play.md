# SPEC017: What Counts as a Play

**Design Specification — Tier 1 · one rule, every path**

`listener_play_history` is the only irreplaceable data in the system
`[SPEC-DF-090]`, and rotation, recovery and restraint are all computed from it
`[SPEC009]`. So the question of *when a row is written* is not an implementation
detail of whichever player happened to be running.

This document exists because it briefly was one. The local engine wrote a play
at the **start** of a passage; the MPD Director wrote one at **half its length**.
Both wrote the same table.

---

## 1. The rule

**`[SPEC-PLAY-010]` A passage counts as played once the listener has heard half
of it, or four minutes, whichever comes first.** Measured against the **passage
span**, not the file's length — two passages in one capture have different
lengths and identical file durations `[SPEC-DF-030]`.

Both Last.fm and ListenBrainz use exactly this threshold. Adopting it rather
than inventing one buys three things: it is already tuned by services that have
watched billions of listens, it agrees with whatever scrobbler the listener
already runs `[SPEC-MPD-100]`, and it is a number a person can check.

**`[SPEC-PLAY-020]` The minimum-length exclusion is deliberately not adopted.**
Last.fm additionally ignores tracks under 30 seconds and ListenBrainz under 5.
That floor is an anti-spam rule about fraudulent submissions to a public
service, and it does not apply to a private rotation ledger. Lempi's shortest
radio passage is **12 seconds** `[SPEC-SA-090]`; one that played in full did
play, and excluding it would suppress nothing but the truth.

**`[SPEC-PLAY-012]` It is measured in time *heard*, not in position reached.**
*(Settled 2026-08-22, when seeking was built `[REQ-VIS-225]`.)* While playback
only ever ran forwards the two were the same number, and the engine read the
position straight off the sounding passage. A seek separates them: a click
three-quarters of the way along a bar puts the position past any threshold
instantly, and **nobody listened to the distance**.

So both backends now credit time from the **gap between samples** rather than
from where the head is, and a seek clears the mark they measure from — the jump
across contributes nothing and the next sample simply starts measuring again. A
guest is credited at most one sampling interval per sample for the same reason:
a longer jump in its clock is a seek, not listening `[SPEC-MPD-105]`.

Two things fall out of it that were wrong before and are right now. Seeking
forward earns nothing, which is the point. And a **resumed** passage is credited
only with what is heard after the resume, where reading the position would have
counted a passage resumed at its half-way mark as played the instant it started.

**`[SPEC-PLAY-015]` An unknown length is never a play.** Half of zero is
trivially reachable, so a passage that had not played at all passed the
threshold — observed, in the MPD observer's first live run. Absent is not zero
`[GOV-SRC-040]`.

**`[SPEC-PLAY-017]` A passage is judged when it stops sounding, including when
nothing follows it.** *(Corrected 2026-08-21.)* The engine reads the passage at
the head of the queue to judge it, so an emptied queue once left it nothing to
read — and **skipping the last passage of an evening judged nothing at all**. It
was neither played nor suppressed, and the Director could offer it straight
back. An empty queue is not "nothing to do": it is the strongest evidence a
passage has just departed.

Found by playing audio through the engine rather than by reading it.

**Measured against what was *heard*.** For the local engine that is audible
position, net of output buffering — the ring holds seconds of audio, and
decoder position would count music the listener never got to.

---

## 2. It applies to every path

**`[SPEC-PLAY-030]` One rule, one implementation, called from both.**
*(Settled 2026-08-21.)* `player/src/scrobble.rs` is the only place the threshold
is expressed; the local engine and the MPD Director both call it. Restating it in
two places is how the two paths drifted apart in the first place, so the rule is
imported rather than repeated — including by the prototype binaries.

| path | reads the rule from | writes |
| :--- | :--- | :--- |
| local engine `[REQ-PD-110]` | `scrobble::counts_as_play` | `listener_play_history` |
| MPD Director `[SPEC-MPD-090]` | the same function | the same table |
| future backends `[GDE-BAK-100]` | the same function | the same table |

---

## 3. The divergence this created, recorded

**`[SPEC-PLAY-040]` This is a measured divergence from MuLibPlay, taken
deliberately.** *(Changed 2026-08-21.)* MuLibPlay's own note says its history
structures update "as each new track finishes playing (or is put in the play
queue)", and Lempi's engine followed it: a play was written the moment a passage
began sounding. The reasoning was explicit — rotation spaces out what the
listener has *encountered*, and a passage skipped after ten seconds has been
encountered.

That reasoning is not wrong, but it answers a different question from the one
`listener_play_history` is now asked. `[REQ-PD-110]` requires MuLibPlay's
*weighting* be reproduced as designed; this changes *what feeds* the weighting,
which is the class of change `[SPEC-DIR-210]` already defers under
`[GDE-PHS-030]` — and it is recorded here rather than absorbed silently.

---

## 4. Declining a passage suppresses it, and does nothing else

Aligning the threshold left a gap: under the old rule a ten-second skip pushed a
recording down the rotation, and under `[SPEC-PLAY-010]` it was never played, so
nothing held it back at all. The answer is not to loosen the threshold — a skip
is genuinely not a listen — but to give the rejection its own narrow effect.

**`[SPEC-PLAY-050]` A rejected passage is held out of selection for a window, and
that is its entire consequence.** *(Settled 2026-08-21.)* It is written to
`listener_rejections`, never to `listener_play_history`, and the eligibility gate
is the only thing that reads it. It contributes **no** play count, **no** recovery
ramp, **no** artist mark and **no** weight of any kind — a passage whose window
has passed weighs exactly as one never rejected, which is asserted rather than
described.

Structurally rather than by convention: the rejection ages are separate fields
from `recording_age_s`, the gate sits with the passage filters *above* the artist and
recording passes, and nothing below it reads them. A rejection cannot leak into a ramp
because there is no path from one to the other.

**`[SPEC-PLAY-055]` Two ways of declining, two windows.** They are not the same
statement and do not earn the same silence.

| kind | what happened | default |
| :--- | :--- | ---: |
| **skip** | the passage began sounding and was stopped before `[SPEC-PLAY-010]`'s threshold | **156 h** |
| **dequeue** | the passage was removed from the queue by hand, never having played | **18 h** |

156 hours is six and a half days: long enough that a rejected passage does not
return within the week, and deliberately offset from a whole week so it does not
come back on the same day at the same time. A dequeue earns less because
declining to hear something *now* says less than stopping it once it had started.

**Not every departure from the queue is a rejection.** A passage the engine could
not open leaves the queue too, and it must leave no mark at all `[REQ-PD-112]` —
that is a failure, not a preference. Only a removal the listener asked for counts.

**`[SPEC-PLAY-057]` Where windows overlap, the longer remaining one wins.** A
second rejection can only extend suppression, never shorten it: a track skipped
155.5 hours ago has half an hour left, and being dequeued today holds it for 18
more; a track skipped an hour ago has 155 hours left, and a dequeue today changes
nothing. The block is the **union** of the windows, and the reason reported is
whichever has longer to run, so the "why" panel names the one actually holding it
out.

This falls out of storing *when* and *how* rather than an expiry. It is also what
lets a listener change a window and have it apply to what they have already
rejected — an expiry computed under yesterday's setting would outlive the setting.

**`[SPEC-PLAY-060]` Both windows are the listener's**, edited on the Lempi skin
settings page and persisted like the other tunables `[REQ-VIS-155]`, taking effect
live rather than at the next rebuild. **Zero is legitimate** for either and turns
that suppression off, which is why each is a number rather than a switch with a
number beside it.

> **Suppression is keyed by recording MBID, not by passage.** Rejecting one
> encoding of a song suppresses the song — every passage under that MBID, as
> rotation itself works. Keying per passage would send the selector to a
> different copy of the take the listener had just refused.

Held-out passages are counted apart from filtered ones in the census: a
suppressed passage is the right shape and is coming back, and folding the two
together would report a library as permanently smaller than it is.

`listener_rejections` is **Class D** and never travels `[SPEC-DF-055]`: it is an
account of one listener's reactions, exactly the material that must not ride
along in a payload.

---

**Traceability:** `[SPEC-PLAY-010..060]` · implemented in `player/src/scrobble.rs`
and `player/src/director/frequency.rs`
· consumed by `[REQ-PD-110]`, `[SPEC-MPD-090]` · rationale
[SPEC015](SPEC015-mpd-director.md), [SPEC016](SPEC016-mpd-protocol-findings.md)
