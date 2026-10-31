# SPEC069: A Disc Ripped Again

**Design Specification — Tier 2 · written 2026-10-30, at the maintainer's direction · not yet built · extends [SPEC056](SPEC056-cd-import.md) `[SPEC-CDI-047]`, `[SPEC-CDI-094]`**

A disc already in the library, ripped a second time. On 2026-10-30 a DAO rip
of *Highway 61 Revisited* -- all nine tracks, every one accurately ripped --
was refused because the album's folder held the first rip, a file per track
that stopped after track 6: `already holds [...(Disc 1).cue]: not moved over`,
after the whole MP3 had been encoded. The import could not tell a second
edition from the same disc, unfinished.

It will come up again. On the desk library that day, **29** albums carry
`track_missing` rows `[SPEC-CDI-094]` and **20** DAO files hold **38** damaged
tracks `[SPEC-RIP-054]`; each is a disc someone may rip again.

> **Related:** [SPEC056](SPEC056-cd-import.md) (adding a rip) ·
> [SPEC057](SPEC057-holding-a-passage-back.md) (a damaged track held) ·
> [SPEC066](SPEC066-playing-an-album-as-recorded.md) `[SPEC-ALB-910]` (album
> passages across a segue) · [SPEC067](SPEC067-an-album-by-its-identity.md) (one
> recording twice makes two albums, `[SPEC-AID-025]`) ·
> `tools/ingest_cd.py`, `tools/join_measure.py`, `player/src/decoder.rs`

---

## 1. The disc is the DAO file

**`[SPEC-AGN-010]` A DAO rip is kept whole, and is never cut or re-encoded.**
It is the disc: one contiguous file, so a passage may cross a track boundary
-- hidden audio `[SPEC-RIP-090]`, an intro joined to its track
`[SPEC-RIP-092]`, a whole disc as one piece `[SPEC-RIP-114]`, anything drawn by
hand -- and album passages played in order are the record as it plays. Track
files are kept beside it only to repair it.

**`[SPEC-AGN-015]` A rip of a disc already held is recognised before anything
is encoded.** The same Disc ID, or the same chosen release `[SPEC-CDI-098]`, as
an album already in the library makes the add a merge into that album, and the
card says so -- *an earlier rip of this disc: 6 of 9 tracks, 7–9 missing since
2026-10-24* -- instead of failing at the move. Any clash of names in the album
folder is found in the move's planning, before ffmpeg runs, not in the move
after it; the earlier sheet is renamed, never written over.

## 2. Each track from its better copy

**`[SPEC-AGN-020]` Each track has one radio passage and one album passage, on
the better of its copies.** The DAO's when its track is perfect -- matched in
AccurateRip -- and otherwise whichever copy is less damaged, by the rip logs'
word on each track:

| rank | the log says | |
| ---: | :--- | :--- |
| 1 | accurately ripped (AccurateRip, or CTDB) | perfect |
| 2 | read cleanly, not in AccurateRip's database | |
| 3 | no log | unverified `[SPEC-CDI-094]` |
| 4 | read errors, or cannot be verified | held `[SPEC-HOLD-030]` |
| 5 | not ripped | missing |

A tie goes to the DAO, for the disc. A copy that wins on rank 4 is still held,
as any damaged track is.

**`[SPEC-AGN-025]` The verdicts are the ones already recorded.** For the rip
being added, its log. For what the library holds, the log kept beside the
album `[GDE-INT-175]`, else the `ingest_decisions` rows and holds the earlier
add wrote; a rip added with no log is rank 3. Where both logs carry a track's
AccurateRip CRC, equal CRCs are two reads agreeing, and need no encode to
show it.

**`[SPEC-AGN-030]` A track file is kept only if it wins its track.** The rest
go to a backup folder beside the music folder, not the bin, and their rows --
`files`, `passages`, `passage_recordings`, `file_tags` -- leave the catalogue.
That is all that hangs off them: plays and stars are the recording's, and the
recording stays. For *Highway 61* every DAO track is perfect, so all eighteen
passages are the DAO's and the six track files are retired.

Nothing outside the disc being added is touched. Within it, a retired file's
passage that a person edited -- its `boundary_src` anything but the
`imported:` an add writes -- is named on the card before **Add**, never retired
unsaid.

A track a file wins has **no** passages on the DAO. The DAO still holds that
track's audio, for any passage crossing it, but two album passages of one
recording would make the album two `[SPEC-AID-025]`.

**`[SPEC-AGN-035]` The card shows each track's choice, and a person may change
it.** One row a track: the copy it will play from, and why -- *DAO, accurately
ripped (370)*, *track file: the DAO's read errors* -- chosen in advance by
`[SPEC-AGN-020]`, and changeable before **Add**.

**`[SPEC-AGN-040]` Two DAO rips of one disc follow the same rule.** The better
of the two is the disc. The other is kept whole only if it wins a track, and
that track's passages are a span of it: nothing is cut either way.

**`[SPEC-AGN-045]` What a speaker already holds is withdrawn with it.** A
speaker sent the earlier rip has files the catalogue will no longer name;
retiring them is star sync's to carry `[SPEC-STAR-080]` (open,
`[SPEC-AGN-920]`).

## 3. Back to back is the disc

**`[SPEC-AGN-050]` A track file's album passage covers exactly its span of the
DAO.** CUERipper's tracks mode appends each gap to the track before, so file
*n* runs from track *n*'s INDEX 01 to track *n+1*'s -- the span the DAO's album
passage of that track covers. Measured 2026-10-30: track 1 of the earlier rip
is 373,267 ms, ending where the DAO's track 2 begins, and tracks 1 and 6 cut
from the DAO WAV at those points and encoded as the importer encodes are
**byte-identical** to the earlier files, audio hash and all. A file whose
length does not match its DAO span -- a ripper set to prepend gaps, or to
drop them -- is said on the card, and plays only as the radio passage; the
album passage stays on the DAO.

**`[SPEC-AGN-060]` The join, measured through the engine.** The engine's join
probe (`join_probe`, in `player/src/engine/mod.rs`) plays two passages through
the whole engine into a test output ring at 44.1 kHz, the desktop's rate, and
`tools/join_measure.py` places each side on the disc's own WAV by
cross-correlation. *Highway 61*, 2026-10-30, joined inside "Like a Rolling
Stone" at 3:00.000, where the music is continuous -- this disc's own track
boundaries all sit in digital silence (−91 dBFS), so they measure nothing:

| join | decoding | fades | jump | disturbed | level at worst |
| :--- | :--- | :--- | ---: | ---: | ---: |
| DAO to DAO, one file | as today | 20 ms each side | 0 | 38 ms | −82 dB |
| DAO to DAO, 8 places | either | none | 0 | 0–26 ms | down to silence; 6 of 8 below −3 dB |
| DAO to track file | as today | 20 ms each side | 0 | 44 ms | −140 dB |
| DAO to track file | as today | none | 0 | 26 ms | −85 dB |
| DAO to track file | gapless | none | **0** | **0 ms** | **−0.5 dB** |
| track file to DAO | gapless | none | 0 | 0 ms | −0.5 dB |
| either way | gapless | 100 ms crossfade, linear or cosine | 281–479 samples | 92–110 ms | −11 to −15 dB |

*Disturbed* is the time, within 150 ms of the join, that the render's departure
from the disc stands 12 dB above the codec's own (−38 dB here). The clean
track-file-to-DAO row starts its DAO side at a place the seek happens to get
right (`[SPEC-AGN-064]`). Four findings, one requirement each.

**`[SPEC-AGN-062]` An MP3 this import writes is decoded gapless; every other
file is decoded as it is today.** The decoder opens files with symphonia's
defaults, which leave an MP3's encoder delay in: every position is 1,105
samples (25.06 ms) early against the audio encoded, measured on both files.
Inside one file nobody hears it; at a join of two, the second file's delay
replaces 25 ms of the disc with silence. With the LAME tag's delay and padding
trimmed (`enable_gapless`), the join is clean to the sample.

So the choice is the file's, recorded with it, and set only by the add that
writes the file. Decoding is what a stored boundary is measured against: one
switch for the whole player would leave every number in the catalogue as it
is and still move what each one points at by 25 ms -- a boundary set by ear
in a file this import never touched included. A file already in the library
keeps today's decoding, and every boundary on it plays exactly as before.

**`[SPEC-AGN-064]` A passage starting mid-file is decoded from before it.**
After a seek into the middle of an MP3 the first packets come out wrong: up to
499 samples of digital silence, then distortion, for up to 26 ms -- at 6 of the
8 places measured, gapless or not, by where the seek lands in a frame. Every
passage's 20 ms fade-in has hidden most of it. The decoder seeks some frames
earlier and discards up to the exact sample.

**`[SPEC-AGN-066]` Album passages that meet on the disc meet at a sample.**
A track starts on a sector, 1/75 s, and a passage boundary is a whole
millisecond: track 2 begins at 373,266.67 ms and the earlier rip's passages say
373,267, so its join repeats 14 samples. Where an album passage ends exactly
where its disc neighbour begins, the join is placed in samples, from the
sheet's sectors.

**`[SPEC-AGN-068]` No fade where an album passage meets its neighbour.** Today
each end carries the default 20 ms exponential fade `[SPEC-SC-046]`, so
between two album passages the music dips to −82 dB for 38 ms, inside a single
DAO file -- inaudible where a disc leaves silence between its tracks, a
dropout where it does not `[SPEC-ALB-910]`. An end that meets its disc
neighbour has no fade; the first and last ends of a run keep theirs.

**`[SPEC-AGN-070]` A join is butted, not crossfaded.** A constant-gain
crossfade -- curves that sum to one, as linear and cosine do and exponential
does not -- is inaudible only if the two sides are the same audio at the same
sample. Admission does not place the incoming passage that exactly: it came
281 to 1,067 samples late, each less than one 2,048-frame mix block
(not traced further), and the two copies comb-filtered for the
whole 100 ms. Once `[SPEC-AGN-062]`–`[SPEC-AGN-066]` hold, the butted join
measures clean, so a crossfade has nothing left to hide. Revisited only if a
measured join is found that a butt cannot make clean.

## 4. Order of building

1. The clash found before encoding, and the earlier rip named, `[SPEC-AGN-015]`.
2. The player, `[SPEC-AGN-062]`, `[SPEC-AGN-064]` and `[SPEC-AGN-068]`, measured
   again with the probe; the probe's table is the test.
3. The merge, `[SPEC-AGN-020]`–`[SPEC-AGN-035]`, `[SPEC-AGN-050]` and
   samples at a join `[SPEC-AGN-066]`, for a library album of track files --
   the 29 -- with *Highway 61* first.
4. Two DAOs, `[SPEC-AGN-040]`, and a damaged DAO repaired by later track
   files -- the 20.

## 5. Open

**`[SPEC-AGN-900]`** Where a file's way of decoding is kept, and how it
reaches the speakers with the catalogue, is not designed. Until it is, nothing
decodes gapless `[SPEC-AGN-062]`. The DAO passages already held stay 1,105
samples early against their CUE times, as they have always played; the
analysis in `tools/` decodes with ffmpeg, which is expected to trim the delay
(not measured), so its positions may sit 25 ms from the player's.

**`[SPEC-AGN-910]`** Measured, not yet listened to. The probe's renders are
the evidence; nobody has heard a repaired album across a segue.

**`[SPEC-AGN-920]`** How star sync retires a file the catalogue has dropped,
on a speaker that still holds it, is not designed.

**`[SPEC-AGN-930]`** A DAO file's tracks and a later DAO's are compared by
their logs alone; two spans of two whole-disc encodes do not encode alike, so
the hash comparison of `[SPEC-AGN-050]` does not apply between them.

---

**Traceability:** `[SPEC-AGN-010..070]`, `[SPEC-AGN-900..930]` · extends `[SPEC-CDI-047]`, `[SPEC-CDI-094]` · builds on `[SPEC-HOLD-030]`, `[SPEC-AID-025]`, `[SPEC-SC-046]`, `[SPEC-RIP-054]` · answers in part `[SPEC-ALB-910]`
