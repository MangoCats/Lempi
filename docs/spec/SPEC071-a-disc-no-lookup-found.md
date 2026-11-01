# SPEC071: A Disc No Lookup Found

**Design Specification — Tier 2 · written 2026-11-01, at the maintainer's direction · built 2026-10-31/11-01, and *Pure McCartney* identified and linked on the hub · extends [SPEC056](SPEC056-cd-import.md) `[SPEC-CDI-092]`, [SPEC028](SPEC028-cd-ripping-identification.md) `[SPEC-RIP-066]`**

*Pure McCartney* (2016, two discs) went into the library on 2026-10-31 as 24
tracks and no identity at all. Each disc's rip stopped early, at 14 of 19
tracks and 10 of 20, and an aborted rip writes no log `[SPEC-CDI-094]`, so there
was no Disc ID. The barcode fallback asked for each sheet's `CATALOG`, which is
the disc's own code (`0666999387173`, `…166`), not the set's barcode
(`888072386907`), and found nothing. Every track was filed under its CD-TEXT
title and sent to review with no candidate to choose. The tools left to a
person could not help either: `suggest_release.py` matches a folder by its
files' tags, and these MP3s had none.

Everything needed was already on the sheets: the album, the performer, every
track's title and every track's ISRC. A search by album and performer finds
three editions (US, AU, Europe) that share all 39 recordings and both Disc IDs.
All 39 tracks agree with the sheets on place, title and ISRC, and the 24 files
are within ±0.43 s of the listed lengths.

> **Related:** [SPEC056](SPEC056-cd-import.md) (adding a rip) ·
> [SPEC028](SPEC028-cd-ripping-identification.md) (CD-TEXT and MusicBrainz) ·
> [SPEC069](SPEC069-a-disc-ripped-again.md) (a disc ripped again) ·
> `tools/ingest_cd.py`, `tools/identify_cd_rips.py`, `tools/cd_toc.py`

---

## 1. The disc's own CD-TEXT, asked last

**`[SPEC-CDI-093]` Where the Disc ID and the barcode leave no candidate, the
disc is looked up by its CD-TEXT.** *Built 2026-10-31.* `ingest_cd.lookup_cd_text`
searches MusicBrainz by album and performer. It fetches whole each edition that
has a disc with this one's track count, CDs before other formats and at most
eight, cuts each to that disc `[SPEC-CDI-099]`, and keeps only those the sheet
fits, track by track (`sheet_fit`):

- every track is named by its title (`name_match` ≥ 0.85, which ignores
  punctuation and counts containment) or by its ISRC;
- no track the rip has a length for differs by more than 5 s.

The disc's track count and a fuzzy album title are not, by themselves, a
reason to believe anything. An edition that fails is left out, not ranked lower.

It runs for an image rip as well as a file-a-track rip (`identify_disc` serves
both since this change), and only where every earlier lookup left no candidate
at all. A disc found today is found exactly as before, by the same lookup.
"No candidate" now includes a Disc ID that MusicBrainz holds only as a **CD
stub**, with no release: *Smooth Grooves Wedding Songs* matched exactly, and
until this change the exact match skipped the barcode as if it had been an
answer.

Its candidates are offered as any lookup's are. The person picks the edition
`[SPEC-RIP-069]`; a track needs no pick only where the disc's ISRC confirms it
`[SPEC-RIP-067]`. For editions found this way or by barcode, the code must also
be its recording's alone in MusicBrainz, one request per code that would
otherwise confirm. Every lookup now also asks for each recording's ISRCs, which
is what makes that check possible on the Disc ID and barcode paths too. The
sheet's ISRC is read by both parsers into `TocTrack.isrc`.

## 2. A rip added before the rules changed

**`[SPEC-CDI-095]` A rip already in the library is brought up to the answer an
add would give it now.** *Built 2026-11-01.* `tools/identify_cd_rips.py` finds
each rip by its own sheet, which stays in the album folder. Two discs in one
folder have two sheets, each naming its own files. For each track still on its
placeholder, it runs the add's own rule, `_resolve_track`, not a copy of it, so
the two cannot drift apart. Only a placeholder is touched. A track a person or a
lookup already named is left as it is, and so is a review row that already
offers candidates (*Planet Earth*'s, from the fingerprint pass).

An identified track is changed the way `relink_cd_disc.py` changes one:

- both its passages are re-pointed;
- its review row is deleted, since an add that identifies writes none;
- the listener's plays of it move, with `--listener`;
- its flavor moves to the recording, except where the recording already has
  its own from another copy, which stands;
- each change is an `ingest_decisions` row (stage `identify`) recording what
  it was.

A track that still can't be identified, whose review row offered nothing, is
given the candidates the lookup found. `--release` with `--folder` takes a
person's pick of edition after the add, as **Add** would have
`[SPEC-CDI-040]`. Every track it names is identified, the disc is linked as
their album `[SPEC-CDI-098]`, and a disc decision records that a person chose,
which is what `relink_cd_disc.py` and `backfill_cd_releases.py` read. A folder
holding a set's discs takes the set's edition for each disc, cut to that disc
`[SPEC-CDI-099]`; every disc is looked up before any is written, and one that
does not find the edition refuses it for all. Without `--commit` each rip is
decided inside a transaction that is then rolled back.

*Pure McCartney*, 2026-11-01: 24 of 24 tracks identified by ISRC, all 48
passages re-pointed, no review rows left. The maintainer then chose the US
edition (`faa0a2fc`): both discs linked as its discs 1 and 2, all 24.
That ran before the rule asked whether a code is the recording's alone (§3).
*Band on the Run*'s, `GBCCS1000004`, is filed under two recordings, so it
would now wait for the pick of edition. It was left as identified: all three
editions put the same recording (`db6c7347`) at that place, so any pick gives it.
*Planet Earth* (two tracks) and *Smooth Grooves* (twelve) are unchanged.
Neither sheet carries an ISRC; *Planet Earth*'s rows already offer AcoustID's
answers; and MusicBrainz holds *Smooth Grooves* only as a stub.

## 3. Measured on the hub, 2026-11-01

`identify_cd_rips.py --audit`, read-only: every rip whose sheet is still
beside its audio and whose tracks are identified, 187 rips and 2,393 tracks.
Each was looked up as if CD-TEXT were its only lookup, and its ISRCs checked
by the rule; both answers were compared with the recording the track has,
which for nearly all of them a person's pick of edition gave. *Pure
McCartney*'s 24 are in the count with the recordings this rule gave them, so
they agree with themselves.

| what was asked | agree | differ | other |
| :--- | ---: | ---: | :--- |
| CD-TEXT finds an edition | 176 rips | -- | 11 none; the pick among them in 137 |
| CD-TEXT's candidates, a track (offered, not written) | 2,160 | 43 | 40 split, 150 none |
| ISRC rule, editions by Disc ID | 1,305 | **0** | |
| ISRC rule, editions by CD-TEXT, as first built | 1,357 | 25 | |
| ISRC rule, editions by CD-TEXT, a shared code refused | 1,186 | **10** | |

The 25 were three albums. On *Blonde on Blonde* (14) and *Hot Rocks* (1) the
edition picked and the CD-TEXT editions carry different recordings, each
under the disc's ISRC: MusicBrainz files each of those codes under two to
four recordings. That is why a code found by CD-TEXT must be its recording's
alone (§1); it costs 171 tracks a pick they would otherwise not have needed.
The 10 left are *Woodstock: Music From the Original Soundtrack and More*. The
recordings of the edition picked carry no ISRC in MusicBrainz, and the rule's
are the only recordings filed under these codes. They are the recordings of
the edition picked for the *other* rip of that disc in the same folder, so
here the rule would have made one disc's two rips agree.

The 43 CD-TEXT candidates that differ are offered for a pick, never written
without one: the same song as another MusicBrainz recording, on an edition
other than the one picked (*Quadrophenia*, *Woodstock*), and one title
CD-TEXT and the edition disagree on (*A Very Special Christmas 25 Years*,
track 13).

## 4. Open

**`[SPEC-CDI-905]`** Neither the import page nor the review queue offers a
pick of edition for a rip already added; `--release` is the only way, from a
shell. *Smooth Grooves Wedding Songs* needs a release added to
MusicBrainz, or the fingerprint pass. Whether a disc's `CATALOG` that finds
nothing should be read as a disc's own code rather than a barcode, and the
set's barcode looked for some other way, is not examined.

---

**Traceability:** `[SPEC-CDI-093]`, `[SPEC-CDI-095]`, `[SPEC-CDI-905]` · decides `[SPEC-RIP-067]` with [SPEC028](SPEC028-cd-ripping-identification.md) · extends `[SPEC-CDI-092]`, `[SPEC-CDI-099]`, `[SPEC-RIP-069]`
