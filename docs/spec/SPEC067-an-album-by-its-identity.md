# SPEC067: An Album by Its Identity

**Design Specification — Tier 2 · written and built 2026-10-28, at the maintainer's direction · for `[REQ-VIS-187]`**

Browse grouped albums by their displayed name, so every release of one name was
one album. On the desk library that was eighteen *Greatest Hits*, by eighteen
acts: 237 tracks under one row, opened as one running order. This tells albums
apart by what they are, and keeps together what is one album however
MusicBrainz happens to have split it.

> **Related:** [SPEC008](SPEC008-database-schema.md) `[SPEC-SC-125]` (the release a file
> was taken from) · [SPEC066](SPEC066-playing-an-album-as-recorded.md) (an opened album) ·
> an album's credit `[REQ-VIS-183]` · `player/core/src/db/library.rs`
> (`Library::album_index`), `player/src/web/browse.js`, `build/verify-skins.js`

---

## 1. What an album is

**`[SPEC-AID-010]` An album is identified, not named.** Every radio passage is
given its album's identity, and Browse lists, counts, opens and plays by it.
The name is what is shown. Measured on the desk library 2026-10-28, before:
836 albums, 22 names shared by 68 releases. After: 857 albums, 5 names on
more than one row, and the eighteen *Greatest Hits* each their own.

Its identity is decided by what the passage's file knows, in this order:

| what the file knows | radio passages | its album |
| :--- | ---: | :--- |
| the release it was taken from `[SPEC-SC-125]` | 9,612 | that release, with any it is one album with `[SPEC-AID-025]` |
| only its recording's chosen release | 644 | that release's name and credit `[SPEC-AID-030]` |
| only its own tag | 129 | the tag's name |

**`[SPEC-AID-020]` An album row carries its identity.** `browse_albums` gives
each album an `id` -- `r:` and the MBID of its first release, or `n:` and the
name and credit it was grouped by -- and `browse_tracks` opens one by
`album_id`. Asking by `album`, its name, still opens every album of that name,
for any caller that does.

**`[SPEC-AID-025]` Releases of one title and credit are one album, unless they
are two.** A set whose discs were each matched to a different edition is one
album: Billy Joel's *Greatest Hits Volume I & Volume II*, one disc matched to
an Australian release and one to a US one, both of release group `4a8f66b9`.
It is two where MusicBrainz puts them in different release groups, as Heart's
1986 and 1998 *Greatest Hits*, or where the files hold one recording twice, as
the 1999 and 2009 *Woodstock* owned side by side do -- ten recordings in both.
Taken in folder order, each release joins the first album it fits.

| title, credit | release groups | recordings in both | album(s) |
| :--- | :--- | ---: | ---: |
| *Road Tested*, Billy Joel's *GH I & II*, *Sound + Vision* | one | 0 | 1 |
| *The Live Anthology* | unknown | 0 | 1 |
| Heart's *Greatest Hits* | two | 6 | 2 |
| *Woodstock* | unknown | 10 | 2 |

A set plays part by part, in the order its first files sort, and each part in
its own order `[REQ-VIS-190]`: both discs of a set number from 1, and by number
alone they interleave.

**`[SPEC-AID-030]` A file that names no release is its name and credit.** Its
recording's chosen release is a guess made one recording at a time, and the
guesses disagree: one folder of *Ziggy Stardust* is on four releases that way,
6, 4, 1 and 1 tracks. On name and credit they agree, so it is one album. Where
an album made of file-named releases has that name and credit, it is that
album: six of them had a track or four that named none, *Rubber Soul* among
them. Where no credit is known, the name alone, as before -- a track's own
artist would split a compilation `[REQ-VIS-183]`.

## 2. Opening one

**`[SPEC-AID-040]` A track's album is opened through the track.** A track
listing does not work out identities, which needs the whole library; its
album link `[REQ-VIS-198]` sends `album_of`, the passage, and the server opens
the album that passage is shown under. An identity that names nothing opens
nothing, not everything.

Both work out every passage's album, so the chosen release is looked for only
where the file names none, and opening an album leaves out the plays and
artists only the listing shows. Asked in a join's own condition it was looked
for on every passage, and the first build, `1162858`, was the slower for it.
Measured warm 2026-10-28:

| | by name | `1162858` | `b4d65fd` |
| :--- | ---: | ---: | ---: |
| album listing, desk | 0.31 s | 0.39 s | 0.30 s |
| an album opened, desk | 0.28 s | 0.39 s | 0.27 s |
| album listing, lempi02w | 1.05 s | 1.78 s | 1.15 s |
| an album opened, lempi02w | 0.85 s | 1.60 s | 0.60 s |

**`[SPEC-AID-050]` Albums alike in name and credit are told apart by year.**
Only those, beside the credit -- *Heart · 1986*, *Heart · 1998* -- since on
every other row a year is noise. The year is the first release's.

## 3. Open

**`[SPEC-AID-900]`** Two albums alike in name, credit and year look alike: none
on the desk library today. The track count beside each still differs.

**`[SPEC-AID-910]`** A tag-only album knows no credit, so it does not join a
credited album of its name: *Imagine*, one track from a tag and one from its
recording's chosen release, is two rows of one track each.

---

**Traceability:** `[SPEC-AID-010..050]`, `[SPEC-AID-900]`, `[SPEC-AID-910]` · for `[REQ-VIS-187]` · builds on `[SPEC-SC-125]`, `[REQ-VIS-183]`, `[REQ-VIS-190]`, `[REQ-VIS-198]`
