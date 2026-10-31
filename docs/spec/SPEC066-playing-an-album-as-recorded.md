# SPEC066: Playing an Album as Recorded

**Design Specification — Tier 2 · written and built 2026-10-28, at the maintainer's direction · for `[REQ-VIS-196]`, `[REQ-PD-127]`**

Browse lists radio passages, so a track ticked in an opened album went into the
queue as the cut rotation plays: trimmed, and free to overlap the next. This
makes an opened album queue its **album** passages instead, and gives it one
button that ticks the whole album -- the whole-album play `[REQ-PD-127]` that
[SPEC057](SPEC057-holding-a-passage-back.md) `[SPEC-HOLD-070]` was waiting for.

> **Related:** [SPEC008 §3](SPEC008-database-schema.md#3-passages--the-albumradio-duality) `[SPEC-SC-047]`
> (what `album` and `radio` change) · [SPEC057](SPEC057-holding-a-passage-back.md)
> (holds) · [SPEC026](SPEC026-cd-ripping-passages.md) `[SPEC-RIP-092]` (a hidden intro joined
> to its track) · `player/core/src/db/library.rs` (`Library::as_recorded`),
> `player/src/web/browse.js`, `build/verify-skins.js`

---

## 1. What changes

**`[SPEC-ALB-010]` In an opened album, a ticked track queues its album
passage.** Opened means narrowed to one album: from the Album tab, or from an
artist's albums. Everywhere else -- all tracks, an artist's tracks, a search --
a tick queues the radio passage, as before. Now, Next and Last are unchanged,
and so is the order: the album's own `[REQ-VIS-190]`. Measured on the desk
library 2026-10-28, the album passages carry a `lead_out_ms` of 0 on all but 5
of 10,403, against a radio mean of 1.7 s: a run of them plays straight through,
gaps and all, as the record does `[SPEC-SC-047]`.

The queue route needed no change: it takes any passage id. Plays are counted
by recording, so an album passage's play counts for its recording as a radio
one's does, and the Director fills behind the album as it would behind any
hand-picked passage.

**`[SPEC-ALB-020]` *Select all* ticks the whole album; *Unselect all* unticks
it.** Two buttons beside Now, Next and Last, shown only in an opened album.
*Select all* replaces the selection with every track it will play, in running
order, and is disabled once that is what is ticked, or for an album with no
album passages at all. A track it leaves out (`[SPEC-ALB-040]`,
`[SPEC-ALB-050]`) is counted on the selection line -- *11 tracks selected · 2
left out* -- not silently missing. *Unselect all* unticks every track, and is
disabled, like the verbs, while nothing is ticked. Until 2026-10-28 the one
button did both, reading *Clear* once pressed; an album that opens ticked
needs the way back to nothing to be there from the start.

**`[SPEC-ALB-025]` An album opens ticked, as MuLibPlay's did.** From the Album
tab, every track *Select all* would tick. From an artist, only the tracks
credited to that artist -- by the displayed credit that put the album in their
list -- each ticked the same way, so a held one is its stand-in or unticked.
Either way the whole album is listed, and a person ticks and unticks from
there. Only the opening ticks: a search typed inside the album does not tick
it again. Asked for 2026-10-28.

The artist an album was reached through is the trail and the ticking, never a
filter on the listing. Until 2026-10-28 it was, and the album's tap passed its
displayed credit `[REQ-VIS-183]` as that artist: *Native American Flute
Lullabies*, 18 tracks by five acts and credited to *Various Artists*, opened
empty from the Album tab and as Native Flute Ensemble's 3 tracks from theirs,
because no track carries the credit of the release.

## 2. Finding the album passage

**`[SPEC-ALB-030]` A radio passage's album passage is the one album passage on
its file that it covers more than half of.** Exactly one, or none. Two rules
that look simpler were measured and refused:

| rule | pairs, of 10,402 radio passages | why not |
| :--- | ---: | :--- |
| exact span | 2,335 | the migrated library's radio cuts are trimmed inside their album cut |
| same file, same heaviest recording | 10,402 today | a hidden intro joined to its track names the track, and would queue it twice |
| covers more than half of one | **10,402, each exactly one** | -- |

A join `[SPEC-RIP-092]` covers two album passages -- the intro and the track --
so it has none, and the track is not played twice. A radio cut that runs past
its album cut still pairs: 15 do, by up to 17 s. Measured again through
the code itself, 2026-10-28: all 836 albums opened, 10,385 rows, every one with
its album passage, none claimed twice.

`passage_profile`'s own sibling lookup `[REQ-VIS-270]` keeps its two rules; it
names a sibling for a person to look at, and a join's nearest sibling is a fair
answer there.

**`[SPEC-ALB-040]` A track with no album passage plays its radio passage, and
says so.** Ticked by hand, it queues its radio passage, and its row reads
*radio cut — not in Select all*. *Select all* leaves it out, because the one
case that produces it is a join, whose audio is already in the album passages
around it. None exists on the desk library today.

## 3. Holds

**`[SPEC-ALB-050]` *Select all* passes over a held album passage, or plays an
equal one from another file; a hand tick plays the held one.** This is
`[SPEC-HOLD-070]`, built. A held track's row names its stand-in's album --
*held — Select all plays it from Document* -- or reads *held — not in Select
all*, with the hold's reason as the row's tooltip. Ticked by hand, it queues the
held passage itself, which a person may still choose `[SPEC-HOLD-010]`.

The stand-in is an unheld passage on another file whose **heaviest** recording
is the held one's: the same recording MBID, not the same title and not a medley
that includes it. An album passage is preferred to a radio one, since the rest
of the play is album passages; then the lowest id, so it is the same one each
time. A catalogue without `director_hold` holds nothing `[SPEC-HOLD-055]`.

Measured 2026-10-28: 38 album passages held, and 3 with a stand-in -- *99.9 F°*
from *Retrospective*, *The One I Love* from *Document*, *Have Love Will Travel*
from *An American Treasure*. *At Last* opens with 8 of its 13 tracks held and
none with a stand-in, so *Select all* plays 5.

## 4. Where it is worked out

**`[SPEC-ALB-060]` On the server, for an opened album only.** `browse_tracks`
adds `album_passage`, `album_hold` and `stand_in` to each row when the listing
is narrowed to an album, and omits them from every other listing, so the
2,000-row ones carry nothing new. The page decides only which of a row's two
answers a tick sends: `own` (the album passage, else the radio one) by hand,
`whole` (the album passage, the stand-in, or nothing) for *Select all*.

One query per row, not a join in the listing's own query: an album is a dozen
rows. Measured on the desk across all 836 albums, 0.9 s in total -- about 1 ms
an album, against the listing query's own ~60 ms. The stand-in search scans
`passage_recordings`, which has no index on `mbid`, and runs only for a held
row: 0.7 ms warm over its 20,805 rows. The player opens the library read-only
and does not add one.

## 5. Open

**`[SPEC-ALB-900]`** An album passage with no radio passage is not listed, so
it is not in a whole-album play: Browse is built on radio passages. On the desk
library that is one 2.9-second untitled piece. A rip that makes album passages
alone would need Browse to list them.

**`[SPEC-ALB-910]`** Not yet listened to across a segue. Each passage keeps its
own 20 ms fade at each end `[SPEC-SC-046]`; between two album passages of a
disc image that falls in the real silence `tools/segment_dao.py` leaves between
them, but a record whose tracks run into each other has not been heard played
this way. Measured 2026-10-30, not heard: inside continuous music those two
fades dip to −82 dB for 38 ms -- [SPEC069](SPEC069-a-disc-ripped-again.md)
`[SPEC-AGN-068]`.

---

**Traceability:** `[SPEC-ALB-010..060]`, `[SPEC-ALB-900]`, `[SPEC-ALB-910]` · for `[REQ-VIS-196]`, `[REQ-PD-127]` · builds on `[SPEC-HOLD-070]`, `[SPEC-SC-047]`, `[REQ-VIS-195]`, `[REQ-VIS-190]`
