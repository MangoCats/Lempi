# GOV002: Sources of Truth

**Governance Specification — Tier 0**

Lempi repeatedly has two or more answers to the same question: how long is this
file, which recording is this, where does this passage begin. This document
states how to choose between them, and registers where the choice has been made.

It exists because the same mistake keeps arriving in new costumes. Each time it
looked like a new problem, and each time it was this one.

---

## 1. The rule

**`[GOV-SRC-010]` Take the answer from the most reliable source available, and
use a less reliable one only as a declared fallback.** Where two sources answer
the same question, they are ranked once, in writing, and the ranking is
consulted rather than re-litigated at each call site.

**`[GOV-SRC-020]` Reliability is established by measurement against ground
truth, never by reputation or convenience.** "It is the local one", "it is
already in hand", "it is the one the protocol gave us" are not rankings. The
question is always: *when the two disagree, which one was right, and how often
do they disagree?* Until that has been measured, there is no ranking, and saying
so is better than assuming one.

The measurement belongs in the document that records the decision, with its
population and its date. A ranking without a number behind it is an opinion that
has been formatted to look like a finding.

**`[GOV-SRC-030]` A fallback must be visible in the output.** When the preferred
source was unavailable and a weaker one answered instead, whatever consumes the
answer must be able to tell. A verdict resting on an estimate is a weaker claim
than one resting on a measurement, and silently rendering them identically
destroys the only evidence that would have exposed a bad ranking.

**`[GOV-SRC-040]` Absent is not zero, and present is not usable.** A missing
value must propagate as missing. Substituting a neutral-looking default — `0`,
`""`, `unknown` — converts an absence into a false assertion, and the default is
usually the value least likely to be noticed and most likely to be wrong. A
field that exists but cannot be used is the same failure wearing a passing type
check: reject it where it arrives, rather than defaulting it where it is read.

---

## 2. The guard: different is not worse

**`[GOV-SRC-050]` Two sources that answer *different questions* are not ranked
by reliability, and forcing them into a ranking manufactures a false finding.**
Before ranking, establish that both are answering the same question. If they are
not, choose by fitness for purpose and **say that is what was done**.

This guard is not hypothetical; it is the correction that produced it.
`[SPEC-RLK-080]` originally rejected Symphonia as a hasher on a 1% disagreement
with ffmpeg, as though ffmpeg had won on merit. It had not. The stored hashes
are Essentia's, Essentia's audio I/O is FFmpeg-derived, and measuring ffmpeg
against them was close to measuring a tool against its own output. Had Symphonia
generated the references, Symphonia would have scored 100%.

The two hashers disagree about where a stream ends `[SPEC-RLK-085]`. Symphonia
stops at the last decodable frame — the cleaner **identity**. ffmpeg includes
trailing bytes — the stricter **integrity** check. Neither is more correct.
ffmpeg was kept because relink is an integrity check `[SPEC-RLK-140]`, a reason
found *after* the fact and labelled as such.

> A ranking asserted on merit that actually rests on lineage is worse than no
> ranking, because it looks settled. `[GOV-SRC-020]` and `[GOV-SRC-050]` are the
> two halves of the same discipline: measure, and know what you measured.

---

## 3. Register

Where the choice has been made, and on what evidence.

| question | preferred | fallback | evidence |
|---|---|---|---|
| **how long is this audio** | Lempi's decoded `duration_ms` | MPD's `duration` | 36.9% of 5,373 files disagree by >1 s `[SPEC-MPD-092]` |
| **how long is this passage** | the passage span | the file duration | span is authoritative by construction `[SPEC-DF-030]` |
| **which encoding is this** | `audio_md5` | `recording_mbid`, then `file_path` | `[SPEC-DF-030]`, `[SPEC-DF-035]` |
| **which MPD URI is this passage** | same-tree prefix | recording MBID, then unresolved | stage 0: 100% same-platform, 95.2% across `[SPEC-MPD-060]` |
| **is this the file we catalogued** | content hash | path | paths move; `[SPEC-RLK-090]`, `[SPEC-RLK-150]` |
| **did the span we asked for land** | the queue entry read back | the command's `OK` | `OK` hid a dropped range on 6.4% of passages `[SPEC-MPD-096]` |
| **which hasher computes `audio_md5`** | *not a ranking* | — | inheritance and purpose, not merit `[SPEC-RLK-080]`, `[SPEC-RLK-086]` |

**`[GOV-SRC-060]` A new pair of sources is added to this register when it is
ranked, not when it is noticed.** An unranked pair is an open question and
belongs in the owning document's open-questions section, where it reads as
unfinished rather than as decided.

---

## 4. How this keeps arriving

Recorded because the pattern is more useful than any single instance.

**MPD's duration** *(2026-08-21)*. MPD reports `duration` from an estimate — for
a VBR MP3, size over bitrate, so embedded cover art inflates it. A 12.07 s track
carrying a picture was reported as 22.8 s and, having played in full, was
recorded as a skip. Across the library 36.9% disagree by more than a second,
median error 98.8 s, worst `+3421 s`, errors in **both** directions so no
correction factor rescues it. Lempi matched `ffprobe` to the millisecond in
every case checked. `[SPEC-MPD-092]`

**The importer's zero** *(2026-08-21, found by audit)*. `duration_ms` was a
required field, but the check asked only whether the key was present. A string
or a float passed, `num()` returned `None`, and `unwrap_or(0)` wrote a **zero
length** into the column the play/skip judgement had just been taught to trust.
Both the importer and the payload builder now refuse it. No live row was
affected — 0 of 5,709 — so this was prevention.

**The stopped-song verdict** *(2026-08-21)*. A duration of zero made
`half of zero` trivially reachable, so a song that had not played at all was
judged a play. The same `[GOV-SRC-040]` failure at a third site.

> All three are one mistake: **a value that was not really known was allowed to
> stand in for one that was.**

---

## 5. Applying it

When adding code that reads a value obtainable from more than one place:

1. Name both sources and the question they answer. If the questions differ, stop
   — `[GOV-SRC-050]` applies, not `[GOV-SRC-010]`.
2. Measure them against each other on real data, and record the population, the
   disagreement rate and the date.
3. Record the ranking in the owning specification and add a row to §3.
4. Make the fallback visible where the value is consumed `[GOV-SRC-030]`.
5. Ensure absent stays absent `[GOV-SRC-040]` — no neutral default, and reject a
   present-but-unusable value at the boundary it arrives on.

Steps 1 and 2 are the work. Steps 3 to 5 are what stops it being redone.
