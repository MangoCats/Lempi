# SPEC072: An Album From Before the CD Import

**Design Specification — Tier 2 · written 2026-11-01, at the maintainer's direction · built 2026-11-01 · extends [SPEC069](SPEC069-a-disc-ripped-again.md) `[SPEC-AGN-015]`, `[SPEC-AGN-020]`**

On 2026-11-01 a DAO rip of *Wish You Were Here* came in, every track
accurately ripped (confidence 400). The library already had the album: five
MP3s inherited from MuLib in 2025, one a track, one of them with audible
glitches. The import did not see them as the same disc. They were never a CD
import, so no Disc ID, barcode or rip decision named them, and the edition
their folder had been matched to (`2319dd32`, by `folder:match`) is not among
the twelve this disc's Disc ID finds. The preview called eleven of the twelve
editions a duplicate, refused **Add** without *Add it again*, and with it
would have made a second album of the same five recordings.

Most of the library came in this way, so the next re-rip of any album from
before the CD import would meet the same refusal.

> **Related:** [SPEC069](SPEC069-a-disc-ripped-again.md) (a disc ripped
> again, the merge this extends) · [SPEC056](SPEC056-cd-import.md) (adding a
> rip) · `tools/ingest_cd.py`, `tools/console_web/import.html`

---

## 1. Found by its recordings

**`[SPEC-AGN-016]` Where no edition, Disc ID or barcode finds an earlier rip,
an album the library had before any CD import is found by its recordings.**
*Built 2026-11-01* (`ingest_cd.inherited_copy`). It is the one folder whose
files hold this disc's recordings, a file a recording, under these rules:

- each file has one radio passage of one recording;
- no file was written by a CD import. Such a rip is found by what it was
  added as, and another edition's rip is not this disc's `[SPEC-AGN-015]`;
- the folder holds at least half the disc's tracks, and at least two;
- they are at least half of what the folder catalogues. A compilation
  holding one or two of the disc's songs is not this album;
- if two folders hold equally many, nothing is found, because the choice
  cannot be made between them.

Each file's track is its recording's place on the edition chosen, and the
merge proceeds as for an earlier rip a file a track `[SPEC-AGN-020]`. With no
log beside it, each file ranks *no log* (rank 3), so a track ripped
accurately replaces it. The card says *a file a track, not added by a CD
import: found by its recordings*, and the earlier copy is called a copy, not
a rip.

Measured on *Wish You Were Here*, read only: found under eleven of the twelve
editions, 5 of 5 tracks, and every track from the new rip. Under the twelfth
(`1c5a1081`, US), tracks 3 and 4 are other recordings, and the card says so.

## 2. A file is the length of its track

**`[SPEC-AGN-052]` An earlier file far from its track's length on the disc
is not that track's audio, whatever its recording says. It is never kept,
and the card says which track it does fit.** *Built 2026-11-01*
(`ingest_cd.MISFIT_MS`, `MISFIT_SHARE`). "Far" means more than 30 s, or
more than 15 % of the track.

- A file ripped here differs from its track only by a gap, which is seconds.
- MuLib's *Wish You Were Here* differs by up to 18 s, because it drew two
  boundaries elsewhere: *Have a Cigar* is 15.7 s long and *Wish You Were
  Here* 18.2 s short, together within 2.6 s of the disc.
- Its two *Shine On* files differed by 71 s and 63 s, because each was filed
  under the other's recording. The fingerprint check had said so in
  October: one *contradicted*, the other *inconclusive*, each naming the
  other part.

A person choosing to keep such a file is refused before anything is
encoded. Applied to the backup taken before the swap was fixed (§3), the
preview says of track 1: *the earlier file is 12:29.6 long, this track
13:40.3: it is not this track's audio -- it fits track 5's length*.

Applies to every merge, not only an inherited copy's: a gap is never 30 s.

## 3. The swap, fixed by hand

*2026-11-01, the maintainer's decision, as a one-off.* The two *Shine On*
files were re-pointed as `apply_reviews.py` re-points a passage. Each
decision is an `id_reviews` row by `GMKtec`, stamped as already applied:

| file | length | was | is |
| :--- | ---: | :--- | :--- |
| `Shine on You Crazy Diamond (Pa 1.mp3` | 12:29.6 | Parts I–V | **Parts VI–IX** |
| `Shine on You Crazy Diamond (Pa.mp3` | 13:32.7 | Parts VI–IX | **Parts I–V** |

`apply_reviews.py` itself was not run: it applies every pending decision,
and nine of the maintainer's were pending. Both passages of each file were
re-pointed, the radio and the album; the review queue re-points the radio
passage alone (§4).

- **Flavor:** each recording's flavor, measured on the other's audio, was
  dropped. `extract_library.py --folder` then classified it again from the
  passages' cached features: 71 values each, with no Essentia run.
- **Plays:** the plays stay with the recording they were filed under, as
  `apply_reviews.py` leaves them, since they happened to what the passage
  was then. That is five plays and two.
- **Backup:** both databases were saved first, to
  `data/backups/before-wywh-swap-2026-11-01`.

## 4. The merge, on the hub

*2026-11-01, as the AU edition (`8269ced3`), whose barcode is the disc's own
catalogue number.* The first attempt (console job 936) failed while writing
the album, `no such table: listener.passages`. Everything was put back: the
five files in their folder and catalogued, the rip in the inbox, nothing
written.

That is `[IMPL-DBSPLIT-055]` again. SQLite checks a foreign key only against
its own schema. The listener's plays name `passages`, which is in the other
file, and the add holds foreign keys on for the catalogue. *Highway 61*'s
merge had no play to clear. The listener's references are now cleared just
after the catalogue's transaction, with foreign keys off for that step only
(`ingest_cd.clear_retired_refs`, tested on a real split pair).

The second attempt (job 937) went through:

- all five tracks from the DAO;
- the five files retired to `_Retired\Pink Floyd\Wish You Were Here\2026-11-01`;
- 26 plays and 3 selection decisions released from the passages that went,
  each keeping its recording;
- the four review decisions of §3 left naming them, as history;
- flavor and amplitude measured on all five new passages, none failed.

## 5. Open

**`[SPEC-AGN-940]`** The review queue re-points a radio passage, not its
file's album passage. Of the 125 reassignments applied by 2026-11-01, 122
left the file's album passage on the old recording, so on those files the
album and radio passages name different recordings. That is not examined
here.

---

**Traceability:** `[SPEC-AGN-016]`, `[SPEC-AGN-052]`, `[SPEC-AGN-940]` · extends `[SPEC-AGN-015]`, `[SPEC-AGN-020]`, `[SPEC-AGN-035]` of [SPEC069](SPEC069-a-disc-ripped-again.md)
