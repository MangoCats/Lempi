# SPEC063: Album Names Held Short

**Design Specification — Tier 2 · written and built 2026-10-13, at the maintainer's direction · extends [SPEC056](SPEC056-cd-import.md) `[SPEC-CDI-050]`**

A CD rip is filed under `Artist\Album (Year)` and keeps the file names the
ripper gave it. On 2026-10-13 a disc with a 128-character title could not be
filed at all, and nothing on the import page said why. This holds an album's
name in folder and file names to a length a person can set, and refuses a
destination the computer cannot hold before any work is done.

> **Related:** [SPEC056](SPEC056-cd-import.md) (adding a CD rip) ·
> [SPEC025](SPEC025-cd-ripping.md) · [GUIDE037](../GUIDE037-ripping-a-cd.md) (the guide the page opens) ·
> `tools/ingest_cd.py`, `player/src/web/settings.rs`

---

## 1. What went wrong, measured

Fiona Apple, *The Idler Wheel Is Wiser Than the Driver of the Screw and
Whipping Cords Will Serve You More Than Ropes Will Ever Do* (2012). CUERipper
cut the album to "...Will Serve You Mor" so that its own paths came to exactly
254 characters, and still had to cut the AccurateRip report to `...Mor(Dis.accurip`.
The page then proposed a folder with MusicBrainz's whole title and the year,
24 characters longer, and kept CUERipper's 117-character file names: 278
characters a file. This desktop has `LongPathsEnabled` = 0, so the first
`shutil.move` failed with `[WinError 3] The system cannot find the path
specified`. The catalogue was untouched -- the move precedes it -- but the
failed add left an empty album folder and the MP3 beside the WAV in the inbox,
and the card still read *ready*.

## 2. The rule

**`[SPEC-NAM-010]` One standing length, set on the Lempi Settings page.**
*Album names, at most N characters*: 60 unless a person chooses another, from
20 to 200. Stored in the desktop player's own settings (`player_settings`, key
`cd_album_name_max`) beside *keep a lossless FLAC copy* `[SPEC-CDI-058]`, and
shown, like that switch, only on a player built with Vipunen support. Vipunen
reads it when it previews and adds a rip; a value outside the range, or none,
reads as 60. `ingest_cd.py --album-name-max N` overrides it from a command line.

**`[SPEC-NAM-020]` A long name is cut at a word, and reads as a title.** At the
last space that fits, unless that would lose more than half the length; a
parenthesis the cut left open is dropped with what followed it; a trailing
small word (*and*, *of*, *the*, ...) and trailing punctuation are dropped.
*The Idler Wheel* at 60 becomes *The Idler Wheel Is Wiser Than the Driver of
the Screw* (53). The year is added after the cut, so the folder reads
`The Idler Wheel Is Wiser Than the Driver of the Screw (2012)`. The proposed
folder is still editable on the card `[SPEC-CDI-080]`.

**`[SPEC-NAM-030]` A destination that cannot be held is refused before
anything is encoded.** The add works out every file's final path first -- the
MP3 and FLAC not yet made included -- and stops, with the rip untouched, when
two files would share a name, or, on Windows, when a path exceeds 259
characters (MAX_PATH less its terminating NUL). The refusal names the longest
path and both remedies: a shorter folder name, or a lower setting. Long paths
are not switched on instead: the player, ffmpeg and every later export would
each be relying on it.

**`[SPEC-NAM-040]` The rip's files follow the album folder.** A ripper names
its files after the disc and its inbox folder the same way -- CUERipper
`Artist - Album` and then `(Disc 1).cue`. A file whose name starts with its rip
folder's name, where the CUE's does too, has that part replaced by
`Artist - Album` as the album folder names it, less the year and held to the
length: the names follow what a person saw and could edit. The artist is the
one the proposed folder was named after (the edition's credit, else CD-TEXT).
A name the ripper cut short to fit its own path is given the CUE's whole new
name. The CUE's `FILE` lines name the new names, so the MP3 and its sheet keep
one stem -- how the player finds a sheet (`player/src/cue.rs`). A tracks-mode
rip's `01. Title.wav` names are not the album's and are left as they are.

**`[SPEC-NAM-050]` A move is all or nothing.** A move that fails part-way puts
back what it moved, restores the CUE, removes the folders it made, and removes
the MP3s it encoded: the rip is in the inbox exactly as the ripper left it, and
**Add** can simply be pressed again.

**`[SPEC-NAM-060]` Only the file system's name is shortened.** The album in
the catalogue is the edition's own title from MusicBrainz, written by
`record_release`, not the folder; Lempi shows it whole everywhere. An export
carries each file's path relative to the music folder (`payload.bundle_path`),
so speakers and phones receive the shortened names -- well inside Linux's 255
bytes a name.

## 3. *The Idler Wheel*, filed

Added again on 2026-10-13 with the rule built, through the import page's own
routes, into the folder the maintainer named, `Fiona Apple\The Idler Wheel (2012)`
-- the preview had proposed `...Driver of the Screw (2012)`. Its files became
`Fiona Apple - The Idler Wheel(Disc 1)` with `.mp3`, `.cue`, `.log` and
`.accurip` (made whole again), beside `folder.jpg`; the WAV removed. Measured
afterwards: the longest full path is 105 characters, where it had been 278; the
MP3 and its CUE share a stem and the CUE names the MP3; all 10 tracks
identified and linked to the US edition, whose catalogue title is the whole
128-character one.

## 4. Open

**`[SPEC-NAM-900]`** The artist folder is not held to the length. Neither is a
tracks-mode rip's track title. Both are caught by `[SPEC-NAM-030]` rather than
shortened, and the folder can be edited on the card. Albums added before this
rule are not renamed: the longest path in the library on 2026-10-13 was 197
characters, inside the limit.

---

**Traceability:** `[SPEC-NAM-010..060]`, `[SPEC-NAM-900]` · extends `[SPEC-CDI-050]`, `[SPEC-CDI-080]` · beside `[SPEC-CDI-058]`
