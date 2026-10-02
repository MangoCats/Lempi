# SPEC057: Holding a Passage Back

**Design Specification — Tier 2 · written and built 2026-10-01 · for `[REQ-PD-125]`**

A passage the Program Director never chooses, though a person can still play
it. Asked for on 2026-10-01, when two CD rips turned out to hold damaged
tracks: seven of *At Last*, one of *Memphis Blues*. Those should not come up
on the radio; the albums should still play whole when someone chooses them.

> **Related:** [SPEC056](SPEC056-cd-import.md) (adding a CD rip) ·
> [SPEC025](SPEC025-cd-ripping.md) `[SPEC-RIP-054]` (a rip's failures are recorded, never
> silent) · [SPEC046](SPEC046-star-sync.md) (how the catalogue reaches the speakers)

---

## 1. What a hold is

**`[SPEC-HOLD-010]` A held radio passage is never chosen by the Program
Director; a held album passage is passed over when a whole album is played
`[SPEC-HOLD-070]`; nothing else changes.** Either stays in the library, in
search, and in the queue when a person puts it there, and the album's file is
never touched -- a damaged track is still in it, as ripped. It is a property of the **passage**,
not the recording: a damaged track on one disc says nothing about a clean copy
of the same song on another, and a recording-level preference would have held
both. The Director counts held passages as their own bucket of its pool, apart
from the rotation blocks, which lapse, and from passages of the wrong shape.

**`[SPEC-HOLD-020]` The mark is `passages.director_hold`, and it says why.**

| Value | Meaning | The Director |
| :--- | :--- | :--- |
| NULL | never decided | may pick it |
| text | held, and the reason: `damaged rip: read errors at 0:37:23 …`, or a person's words | never picks it |
| empty | released by a person | may pick it |

Empty and NULL play the same, and differ on purpose: an automatic pass holds
only an undecided passage, so a track a person released is never held again
behind their back `[SPEC-SA-080]`.

## 2. Who sets it

**`[SPEC-HOLD-030]` A damaged rip's tracks are held when the rip is added.**
Adding a CD rip already records each track its log names as failing
verification `[SPEC-RIP-054]`; the same step now holds both of that track's
passages, radio and album, with the log's own words as the reason -- the
maintainer's direction, 2026-10-01. For rips added before this existed,
`tools/passage_hold.py --damaged` holds both passages of every recorded
damaged track not yet decided -- a dry run unless `--commit`. Run on the desk
library 2026-10-01: the eight radio passages first, their eight album
passages once the album rule was decided the same day.

**`[SPEC-HOLD-040]` A person holds or releases any radio passage from its
page.** The Vipunen console's passage page shows whether the Director may pick
it, the reason when it is held, and one button either way -- with an optional
reason when holding. It runs `passage_hold.py` as a job; the console itself
never writes the library. `passage_hold.py --list` prints every passage held.

## 3. How it reaches a speaker

**`[SPEC-HOLD-050]` With the catalogue, by the existing sync.** The column is
part of the desktop's catalogue (`sql/schema.sql`; added to an existing
library by `passage_hold.py` when it first writes). `star_patch.py` already
adds to a node's copy any column the hub's has, so the next star sync carries
the column and every mark `[SPEC-STAR-080]`.

**`[SPEC-HOLD-055]` A player that meets a catalogue without the column holds
nothing, and says so.** A speaker can run a new player before its next sync.
The Director then loads every radio passage as before, and logs that no
passage can be held -- the same tolerance it has for a catalogue without
`recording_works` -- rather than failing to load and leaving the queue empty.

**`[SPEC-HOLD-080]` And with a bundle, which *Send what's missing* builds.**
Found 2026-10-01: the first sends carried the music of eight damaged tracks
without their holds, onto speakers whose libraries had no column, so the
Director there could choose them. Now:

- A passage's decided hold travels in the payload as `hold` (a reason, or
  `""` for a release); undecided is absent, and leaves the receiver's value
  alone.
- `import_bundle` adds the column, writes the hold of a passage it creates,
  and sets it on one already there, matched by exact span. A span with no
  match is counted (`holds unmatched`), never guessed at. The sender's
  decided value wins: holds are the household's, made on Vipunen.
- The Export page's diff compares holds as well as files. A file the speaker
  has whose holds differ goes into the bundle as payload alone, no audio, so
  a hold changed here reaches every speaker at its next send.

A hold made only on a speaker is the speaker's, and is not overwritten by an
undecided one.

A change is seen by the Director when it is next built: on the desktop, when
the player reloads its library (the Jobs page's *ask the player to reload*);
on a speaker, at the sync or send that delivers it -- a send asks the player
to reload.

## 4. Playing a whole album

**`[SPEC-HOLD-070]` A whole-album play skips a held album passage, or plays
an equal one from another file.** *Not built: there is no whole-album play
yet* `[REQ-PD-127]`. When there is, it queues the album's passages in order,
and for one that is held it queues, in its place, an unheld passage of the
same recording from another file if the library has one -- a clean copy from a
compilation, say -- and otherwise leaves the track out. Same recording means
the same recording MBID, not the same title: a live take or a remaster is a
different recording and is not a stand-in. The listener can still choose the
held passage itself. Decided by the maintainer, 2026-10-01; the album passages
are held now so that the play, when built, has the marks to read.

## 5. Limits, named

**`[SPEC-HOLD-060]` Re-segmenting a file drops its holds.** `segment_dao.py`
and the phone's own ingest replace a file's passages; the new ones start
undecided. A damaged rip is not re-segmented in the ordinary course -- its
passages come from the disc's own track list.

**`[SPEC-HOLD-065]` A derived-data bundle does not carry the mark.** A Lempi
with no Vipunen, fed by bundle rather than by star sync
([SPEC014](SPEC014-payload-schema.md)), receives every passage unheld. Carrying
it is a payload change, not made here.

## 6. Open

**`[SPEC-HOLD-900]`** Lempi's own passage page shows no hold and offers no
control; a listener on a speaker cannot hold a passage there. Whether one
should -- and how a speaker's hold returns to the hub, against the hub-authors
rule `[SPEC-STAR-085]` -- is left open.

---

**Traceability:** `[SPEC-HOLD-010..070]`, `[SPEC-HOLD-900]` · for `[REQ-PD-125]`, `[REQ-PD-127]` · builds on `[SPEC-RIP-054]`, `[SPEC-STAR-080]`, `[REQ-PD-120]`
