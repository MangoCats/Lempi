# SPEC076: Silent Tracks

**Design Specification — Tier 2 · written and built 2026-11-07, at the maintainer's direction · image rips; not yet used on a real add**

Some discs pad the space before a last track with tracks of nothing. Nine Inch Nails' *Broken* has 91 of them, tracks 7 to 97, one second each, so that *Physical* and *Suck* land at 98 and 99. Imported as tracks, that is 91 radio passages of silence for the Director to choose from, and 91 album passages. This makes each run of them one album passage and no radio passage: a whole-album play keeps the silence at its length, and rotation never plays it.

> **Related:** [SPEC026](SPEC026-cd-ripping-passages.md) `[SPEC-RIP-090]` (hidden audio, measured the same way), `[SPEC-RIP-092]` (a join), `[SPEC-RIP-114]` (one passage for several tracks) · [SPEC066](SPEC066-playing-an-album-as-recorded.md) `[SPEC-ALB-900]` (which this closes) · [SPEC057](SPEC057-holding-a-passage-back.md) (holds) · `tools/ingest_cd.py` (`find_silent`, `commit_rip`), `tools/console_web/import.html`, `player/core/src/db/library.rs` (`Library::album_only`), `player/src/web/browse.js`

---

## 1. What was found

**`[SPEC-SIL-001]` *Broken*, ripped 2026-11-07 with CUERipper, measured before it was added:**

| | |
| :--- | :--- |
| tracks 7-97 | 91 × 1,000 ms, `[silence]` on the sheet and on all five candidate editions |
| their level | every one sampled −91 dB, a 16-bit disc's zeros; 90.97 s of the 91 s digital silence |
| track 97's last 20 ms | −43.9 dB: *Physical*'s first sound, a little ahead of its index |
| MusicBrainz | one recording for all 91 tracks, on every edition |
| AccurateRip | tracks 1-6 and 97-99 accurately ripped; 7-96 *not present in database* |
| track 7's pregap | 373 ms, in no passage, as every gap is |

## 2. What is a silent track

**`[SPEC-SIL-010]` Measured, never taken from a title.** A track is silent when digital silence, below −70 dB, covers all of it but 50 ms at each end. −70 dB takes a pressed disc's zeros and dither, and not a tape's hiss, at about −60 dB, which is a recording of a room. The 50 ms is track 97. A track under half a second is not judged. A run is consecutive silent tracks with no hidden audio `[SPEC-RIP-090]` between them, from its first track's start to its last's end: *Broken*'s is 1,260,333 to 1,351,333 ms, exactly 91 s.

The title is said beside it. MusicBrainz names a silent track `[silence]`; a song may be called *Silence*, so only the bracketed form counts. A track titled `[silence]` that sounds is left a track and named on the page. One that measures silent without the title is still silent, and the page says it was not titled so.

## 3. What it becomes

**`[SPEC-SIL-020]` One album passage a run, and no radio passage.** The Director plays radio passages, so it never picks silence. The album passage carries the run's recordings by their share of it, as `[SPEC-RIP-114]` does; *Broken*'s carries its one recording whole. A whole disc as one passage takes no notice of silence, and a track an earlier rip keeps `[SPEC-AGN-030]` splits a run around it.

**`[SPEC-SIL-030]` Each silent track is identified, without asking anyone.** Its recording is the one every candidate edition puts at its place, else one recording of the run's own, titled `[silence]`, source `cd:silence`. Never AcoustID: a fingerprint of nothing matches nothing, or the wrong thing. Never the review queue: there is no question. The release is linked at the recording's first place, since a release holds one place a recording, so the album lists it as track 7. Hidden audio cannot be joined to a silent track, which has no radio passage to join; that is refused before anything is encoded.

**`[SPEC-SIL-040]` AccurateRip's *not present* is not damage for a silent track.** A track of zeros has nothing in the database to compare with, and the measurement has already shown what it holds. The run's decision lists those tracks as unverified. Any other failure is damage, and holds the run's album passage `[SPEC-HOLD-030]`.

**`[SPEC-SIL-050]` A failure says where it lies.** Each `verification_failed` decision carries its passage's `start_ms`, and `passage_hold.py --damaged` finds the passage by it. Before, it counted: the Nth track was the Nth passage of each kind, which a run makes untrue for every track after it.

**`[SPEC-SIL-060]` Ticked for the person, untickable.** The import page lists each run with its span and length, ticked as one passage, since it is measured. Unticked, or with `--silence-as-tracks`, the tracks are added as any other, a pair each.

## 4. In the album

**`[SPEC-SIL-070]` An opened album lists its album passages that have no radio passage.** One no radio passage covers more than half of, the rule of `[SPEC-ALB-030]` from the other side, in the place of a track on its file. Its row reads *album only — rotation never plays it*, is its own album passage, and offers no seed. Only an album opened by its identity `[SPEC-AID-040]`. One opened by name, and every other listing, stay radio passages. On the hub this adds one row today, *Who's Next*'s 2.9-second *Discard*, which `[SPEC-ALB-900]` had named.

## 5. Open

**`[SPEC-SIL-900]` A rip of one file a track** has one passage a file, and a run cannot be one passage. Its silent tracks are not looked for, and are added as tracks.

**`[SPEC-SIL-910]` Not yet used on a real add, nor heard.** Tested on synthetic discs, and committed against *Broken*'s own sheet, log and editions into a scratch catalogue: 8 radio passages, 9 album passages, 99 tracks identified, nothing held or queued for review. *Broken* itself has not been added.

---

**Traceability:** `[SPEC-SIL-001..070]`, `[SPEC-SIL-900]`, `[SPEC-SIL-910]` · closes `[SPEC-ALB-900]` · builds on `[SPEC-RIP-090]`, `[SPEC-RIP-114]`, `[SPEC-ALB-030]`, `[SPEC-HOLD-030]`
