# GUIDE012: Blocking the Same Song Across Different Recordings

**Development Guidance — scoped 2026-09-11 from a live incident on `lempi02w`;
figures replaced and the design settled 2026-09-12, the latter by owner
decision `[GDE-WRK-035]`, which supersedes this document's own recommendation**

The related-recording rotation of `[SPEC-DIR-116]` was fully implemented, fully
tested, and never had a row to act on. This document scoped the fix, measured
it, and records the rule that replaced it — **built 2026-09-12** as
`[SPEC-DIR-119]`. Nothing is asserted here without a measurement behind it
`[GOV-SRC-020]`.

> **Related:** [SPEC037](spec/SPEC037-eligibility-and-frequency.md) `[SPEC-DIR-116]` — the rule this feeds · [SPEC008](spec/SPEC008-database-schema.md) — the `recording_relations` junction · [SPEC007](spec/SPEC007-vipunen-architecture.md) — the S3 identification stage this extends · [GOV002](GOV002-sources-of-truth.md) — ranking by measurement

---

## 1. The gap, as it actually presented

**`[GDE-WRK-010]` A recording rotation keyed on the MBID blocks a *rendering*,
not a song.** On 2026-09-11 the Director queued "Funeral for a Friend / Love
Lies Bleeding" 29 minutes after playing it. Two passages of the same file,
same artist, same title, two different recording MBIDs — `49323205…` at 646.5 s
and `891cb542…` at 709.7 s — and so two independent rotation histories. The
second had never played, and nothing suppressed it.

The recording-side cause is: **`recording_relations` holds 0 rows**, so a
passage can only ever be blocked by its own exact MBID.

**`[GDE-WRK-012]` It had a second cause, on the artist side, fixed 2026-09-12.**
`lempi02w` was restarted 3m46s into that passage. A passage leaves the queue when
it is admitted to the mixer and does not reach `listener_play_history` until it
crosses the counted threshold minutes later, so a Director rebuilt in between
read neither — `Session::adopt` re-notes the queue for exactly this reason and
the *sounding* passage is not in it. Elton John's last play read as 21 days
earlier, and an 8-hour artist block never saw a reason to fire. `adopt` now
takes `head_position()` first. Either fix alone would have prevented the repeat;
both were real.

**`[GDE-WRK-015]` Identification is not the problem.** All 8,330 radio passages
carry a recording id — 8,167 real MusicBrainz MBIDs and 163 synthetic
`local:audio:…` ids for unidentified segments. Those counts are `lempi02w`'s;
the desktop catalogue the crawl actually read reaches 8,008, and the two halves
of the fleet are not identical `[GOV-SRC-030]` — a derivation run against one
does not describe the other. The MBID-equality block works
exactly as intended where it applies: 176 MBIDs span 368 radio passages, and
those pairs already share a rotation.

**`[GDE-WRK-020]` The exposed surface is 245 artist+title groups covering 500
distinct recordings** — songs the library holds more than once under MBIDs
that differ legitimately. A MusicBrainz *Recording* is a specific rendering;
a remaster, a 5.1 mix, an acoustic take and a live version are four Recordings
by design. The entity meaning "the same song" is the **Work**.

---

## 2. What the player already has

**`[GDE-WRK-030]` The player needed new code after all.** An earlier revision
said none was in scope. That held for the junction design, where filling a
table [`library.rs`](../player/core/src/director/library.rs) already read was the
whole job. `[GDE-WRK-035]`'s tiers are new history maps keyed on `passage_id`
and work MBID, so `weigh` in
[`frequency.rs`](../player/core/src/director/frequency.rs), the load, the noting and
the census all moved. Built 2026-09-12.

---

## 3. The rule, decided

**`[GDE-WRK-035]` Three identity tiers, widening, each conditional.** Settled
2026-09-12 by the project owner, and it supersedes the graded-relation design
the rest of this document was scoped around. When a passage plays, the block
covers:

1. **the passage itself**, by `passage_id`;
2. **every passage sharing its recording MBID**, if it has one;
3. **every passage sharing a work MBID with it**, if it has one.

Each blocked passage then serves **its own** block and recovery ramp — the
tuning of the passage being blocked, never of the one that played. If Rufus
Wainwright's "Across the Universe" carries a 12-day rotation, The Beatles' 4
and David Bowie's 8, then a play of any one of them holds each of the others
for 12, 4 and 8 days respectively. There is no single shared window.

**`[GDE-WRK-036]` So it is evaluated from the candidate's side, and the
asymmetry costs nothing.** Stage A already weighs one candidate at a time
against its own `rotation`/`recovery` `[SPEC-DIR-115]`. Asking "when was
anything identified with *me* last heard, and is that inside *my* window"
gives per-passage periods for free — no directed rows, no per-pair storage,
and none of the N×(N−1) bookkeeping the junction model needed.

**`[GDE-WRK-038]` The three tiers are a fallback cascade, not three tests.**
A play stamps every key the passage has, so the wider key is always at least as
recent as the narrower one: a passage's own last play cannot be more recent
than the last play of its recording, nor that than its work's. The effective
age is therefore the **most recent stamp among the keys the passage has** —
in practice the work's, where one exists, with the recording and passage tiers
mattering only when the wider key is absent. A recording in several works
`[GDE-WRK-070]` takes the most recent across all of them.

**`[GDE-WRK-037]` This is identity, not relation, and that removes most of the
build.** Tier 3 is the same mechanism as tier 2 one key further out. It needs
no `recording_relations` rows, no pairwise closure, and no `strength`: a third
`last_played` map keyed by work MBID, filled the way the recording map already
is. The junction's asymmetry and its N×(N−1) row cost stop mattering, because
nothing is written to it.

`strength` was the constraint that made covers an all-or-nothing question
`[GDE-WRK-050]`. Under identity there is no strength to argue about.

**`[GDE-WRK-050]` Covers block. Decided, against the measurement.** A shared
Work links two artists' versions of one song, and that is the intent: play
David Bowie's "Across the Universe" and The Beatles' and Rufus Wainwright's are
held out for the same rotation. §4 measured the reach before the call was made
— 88 classes, 200 passages, 2.4% of the library — and the answer is yes.

**`[GDE-WRK-052]` Relation *attributes* are deliberately not read.** MusicBrainz
marks a performance `live`, `cover`, `partial`, `instrumental`, `medley`.
`[GDE-WRK-035]` uses none of them: a shared Work is a shared Work. Two
consequences were put up for review on 2026-09-12 and both were **affirmed** —
the rule as stated stands:

- `The Star‐Spangled Banner` is one Work over Hendrix's Woodstock
  instrumental, U2's and Boston's. Playing any one holds the other two.
- A John McLaughlin recording marked `medley` shares the `Stairway to Heaven`
  Work with Led Zeppelin's, so a twenty-minute medley containing the song
  blocks the song, and the song blocks the medley.

29 passages are pulled in by a multi-Work membership `[GDE-WRK-070]`, and
`live` is common — 171 relations in the probe's 559. Reading attributes would
mean a graded relation, which `[GDE-WRK-037]` deliberately gave up.

**`[GDE-WRK-055]` The passage tier closed a real hole.** History had been keyed
on the recording MBID alone, and `note_queued` returned `None` for a passage
without one — recording nothing at all, so such a passage could repeat freely.
Every passage carries an id, 163 of them synthetic, so this changed no
behaviour on this library; it is the invariant that keeps it true.

**`[GDE-WRK-057]` Tuning is stored per recording, not per passage.**
`listener_preferences` is keyed `(subject_kind, subject_id)` over `recording`
and `artist` `[SPEC-PREF-010]`. "The ramp associated with that passage"
therefore resolves through its recording. Genuinely per-passage tuning would
be a schema and UI change, and is not assumed here.

---

## 4. Measured — what a Work pass actually buys

**`[GDE-WRK-060]` Method.** All 8,008 recordings reachable from a radio passage,
fetched from `musicbrainz.org/ws/2/recording?inc=work-rels` at one request per
second on the citizenship rules of
[`tools/fetch_releases.py`](../tools/fetch_releases.py); 8,004 returned, 4
errored. A 559-recording probe sized the job first, and where its figures
differed they are noted — a sample drawn from *known-duplicated* songs is
biased toward exactly what it measured.

**`[GDE-WRK-065]` Work coverage is 89.0%** — 7,120 of 8,004 recordings carry at
least one `performance` relation. The 884 without are dominated by instrumental
and new-age material, where nobody has created the Work. *(The probe said
92.1%; the sample's bias explains the gap.)*

**`[GDE-WRK-070]` `[GDE-WRK-055]`'s chaining risk is real but small: 68
recordings of 8,004 — 0.85% — link to more than one Work.** The probe found 1
in 556 and called it negligible, which was the sample talking. 68 is still
small enough to guard by exclusion rather than design, but it is not zero and
a derivation step must handle it deliberately. Note that MusicBrainz models the
incident's own medley, "Funeral for a Friend / Love Lies Bleeding", as a
**single** Work (`ed2f3608…`), which is the behaviour this rule wants.

**`[GDE-WRK-075]` Work agrees with artist+title almost everywhere it can
judge.** Of 244 same-artist/same-title groups, Work confirms 214, cannot judge
26 (a member has no Work), and contradicts exactly **1** — two recordings
titled "I Love You" against two distinct work MBIDs *also* both titled "I Love
You", likelier a duplicate upstream than a real distinction. So 0.5%, where an
earlier reading of partial data had said ~14% by counting the undecidable
groups as disagreements. Retained as evidence that the Work data is sound, not
as an argument for the heuristic `[GDE-WRK-100]`.

**`[GDE-WRK-080]` Work's advantage is recall, not precision — and it is large.**
Variants carry different titles, so artist+title cannot see them: over the
library it groups 250 classes across 509 recordings where Work groups **495
across 1,074**. The gap is entirely `Candle in the Wind (acoustic)` ↔ `Candle
in the Wind`, `Tower of Babel` ↔ `Tower of Babel (live)`.

**`[GDE-WRK-085]` Classes stay small**: 422 pairs, 60 of size 3, 9 of size 4,
and a tail to ten the probe never saw. The largest are single-artist version
sets — Moby's "Extreme Ways" ×10 — which is the mechanism working.

**`[GDE-WRK-090]` Cross-artist collision, measured: 88 of 495 classes — 17.8% —
span more than one artist**, and they account for 258 of the 1,494 directed
rows. The probe could not see this at all, being artist-scoped by construction.

At passage level that is **200 passages, 2.4% of the library**, and it is the
reach `[GDE-WRK-050]` accepted:

| Work | passages held together |
| :--- | :--- |
| Little Wing | Jimi Hendrix · Sting · Stevie Ray Vaughan · Derek and the Dominos |
| Across the Universe | The Beatles · David Bowie · Rufus Wainwright |
| Let It Be | The Beatles · Aretha Franklin · Nick Cave |
| The Star‐Spangled Banner | Jimi Hendrix · U2 · Boston |

**`[GDE-WRK-095]` The blast radius is small: a play removes a mean of 1.25
passages from 8,330.** Median 1, maximum 10. 83.3% of passages block nothing
but themselves; 16.7% reach further. The work tier is where the value is — it
widens 1,132 passages where the recording tier reaches only 368, three times
the effect for the same mechanism.

This is the number that says the rule is safe to adopt whole: the pool does not
meaningfully shrink, so there is no case for hedging it.

---

## 5. What building it involves

**`[GDE-WRK-100]` Artist+title is no longer the cheap first step.** It was
proposed as the thing that could land before an eleven-hour crawl
`[GDE-WRK-075]`. The crawl has since run, so its only remaining use is the
11% of recordings with no Work `[GDE-WRK-065]` — and under identity those
cannot be reached by a relation row either, since there is nothing to key on.
A synthetic grouping key would be needed, which is a separate decision and not
taken here.

**`[GDE-WRK-110]` Store the Work; block on it directly.** Add `works` and
`recording_works`, and have the Director build a third `last_played` map from
them. There is nothing to derive into `recording_relations` and no policy step
between the data and the behaviour, which is the main saving of `[GDE-WRK-037]`.

**`[GDE-WRK-115]` The cache cannot be mined; the pass had to be fresh.**
`musicbrainz_cache` holds 8,747 rows, all `recording+releases` or
`release+recordings` — fetched without `inc=work-rels`. The crawl therefore
wrote its own cache, and folding it into `musicbrainz_cache` remains optional
housekeeping rather than a prerequisite.

**`[GDE-WRK-120]` Cost, now measured rather than budgeted: nine hours.** The
crawl ran 2026-09-11 20:29 → 2026-09-12 05:36 for 7,448 outstanding recordings
— about 4.4 s each, well off the 1 req/s the published limit allows, because
MusicBrainz throttles. Budget a night, not an afternoon. Resumable and cached,
same shape as `fetch_releases.py`; committed per row, so an interrupt costs one
request.

**`[GDE-WRK-125]` Stages.**

| stage | state | why here |
| :--- | :--- | :--- |
| 1 · `tools/fetch_works.py`, cache only | **built 2026-09-11** | read-only network, cannot damage anything |
| 2 · the crawl | **done 2026-09-12** `[GDE-WRK-120]` | 8,004 of 8,008, cached in `data/work_relations.db` |
| 3 · measure reach, then decide | **done** `[GDE-WRK-090]`/`[GDE-WRK-095]` | a decision against a number, not a guess |
| 4 · `works`/`recording_works`, filled by `tools/load_works.py` | **done 2026-09-12** | 6,605 works over 7,199 rows |
| 5 · passage-id history tier | **done 2026-09-12** | `[GDE-WRK-055]` |
| 6 · work-MBID tier in the Director | **done 2026-09-12** | `[GDE-WRK-038]`'s widest key |
| 7 · `[SPEC-DIR-119]` in SPEC037 | **done 2026-09-12** | the spec now says what the code does |

**`[GDE-WRK-130]` Transport already works, by accident.** `lempi02w` carries the
whole 1.17 GB `library.db`, caches included, so a `recording_works` table ships
with the file as things stand. [`tools/payload.py`](../tools/payload.py) carries
neither it nor `recording_relations`, so a move to bundle-only sync would need
that gap closed first.

*The accident did not reach the rest of the fleet.* On 2026-09-25 `bose` and
`lp3-wifi` were found without either table: their catalogues predate stage 4,
and the old builds never looked. The rows were copied from `lempi02w`'s
catalogue by MBID, then pruned to what `load_works.py` itself would write —
rows only for recordings the catalogue holds: 6,567 works, 7,154 rows on
each. The Director's warning has not appeared since. On `bose` that write
took the player down for 30 minutes `[BOS-RUN-090]`.

What stops a repeat, all 2026-09-25: Vipunen's `induct` now ends with
`works`/`load-works` — a fetch capped at 300 and giving up offline, since
works enrich and do not gate — so a recording identified later gets its
Works; `fetch_works.py` no longer caches "no answer" as an answer, which
unattended would have been permanent; and `split_database.py` carries both
tables and refuses a source holding any table it assigns to neither half.
That check found a second one it would have dropped, `player_queue`.

---

## 7. Open

- **`[GDE-WRK-200]`** *Resolved 2026-09-12* — covers block `[GDE-WRK-050]`,
  measured at 88 classes over 200 passages before the call was made.
- **`[GDE-WRK-210]`** The 11.0% of recordings with no Work `[GDE-WRK-065]` fall
  back to the recording tier alone `[GDE-WRK-038]`, which is what they get
  today. No worse, no better.
- **`[GDE-WRK-220]`** *Resolved 2026-09-12* — relation attributes stay unread
  and the medley and national-anthem cases stand `[GDE-WRK-052]`. Reopening it
  means reopening `[GDE-WRK-037]`.
- **`[GDE-WRK-230]`** *Resolved 2026-09-12* — the rule is
  [SPEC037](spec/SPEC037-eligibility-and-frequency.md) `[SPEC-DIR-119]`, and
  `[SPEC-DIR-116]` is marked superseded for the same-song case while its code
  and table stay live for graded relations.
- **`[GDE-WRK-240]`** *Resolved 2026-09-12* — `publish_pool`'s hand-written sum
  omitted `suppressed`, reading 8,328 of 8,330 on the real library. Replaced by
  `Census::total`, which destructures the struct so a new field cannot compile
  without being accounted for.
