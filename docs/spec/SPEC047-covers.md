# SPEC047: Covers — Beside the Audio, and Shown From the Catalogue

**Design Specification — Tier 2 · written 2026-09-27, from the maintainer's direction that day**

Covers had three sources in the player — the picture embedded in a file, a
picture beside it, and the archive fetched by release — and a phone could be
given only the third: Android refuses pictures in `Music/` `[REQ-AND-205]`. So
the maintainer settled one rule for everywhere. Covers stay where they were
found, undisturbed, for every other player; the catalogue holds its own copy of
each; and Lempi shows covers from the catalogue alone.

> **Related:** [SPEC014](SPEC014-payload-schema.md) `[SPEC-PL-105]` (how covers travel in a bundle) · [SPEC008](SPEC008-database-schema.md) (the catalogue) · [REQ007](REQ007-android.md) `[REQ-AND-205]` (the phone)

---

## 1. The rules

**`[SPEC-COV-010]` Lempi shows covers from the catalogue alone**, on every
host — phone, appliance, desktop, and `fbui`, which asks the player. One
behaviour everywhere: what a listener sees does not depend on what happens to
lie beside a file on one machine. A passage's cover is its recording's chosen
release's, from `cover_art`; failing that, its file's own, from `file_art`
`[SPEC-COV-040]`.

**`[SPEC-COV-020]` Vipunen fills the catalogue from the covers it finds**,
where the catalogue has none: the picture beside the audio (`folder.jpg`,
`cover.jpg` and the like, as the player has always recognised them), else the
picture embedded in the file. A picture beside the audio counts only where the
folder is one album's, since a folder of several albums would give some of its
songs the wrong picture: for a release, every catalogued file there has that
release; for a file's own cover, every file there has one release or shares one
album tag. The catalogue's own cover, fetched or inducted before, is never
replaced by a found one. `tools/induct_covers.py`, reporting by default,
writing with `--write`.

**`[SPEC-COV-030]` A cover beside the audio is left undisturbed** wherever the
host allows it — everywhere but the phone, so far. Vipunen reads found covers
and never rewrites them. A host that may write beside its audio also places a
catalogue cover there where the folder has none (`place_covers`,
`[SPEC-PL-105]`), for other players; a cover already there is never
overwritten.

**`[SPEC-COV-040]` A file no release covers has a cover of its own.** The
archive is keyed by MusicBrainz release, which an unidentified or tags-only
file does not have. `file_art (audio_md5, front, back, source, fetched_at)`
holds a cover per file, by its signature, filled from the same found sources.
It is filled for any file with a passage that no release's cover reaches, not
only a file with no release. That covers a file whose passages sit on several
releases, and an album folder whose songs Vipunen matched to different
releases (Kansas' *Leftoverture*). Kept on the file, such a picture is never
shown for another folder's copy of a release.

**`[SPEC-COV-050]` The catalogue keeps a display copy.** A found picture over
1 MB, or over 1200 px on its long side, is stored as a 1200 px JPEG; the rest
byte for byte, and `source` says which. The original stays on disk.
*Measured 2026-09-27 on the hub:* 469 covers found for files without one, 315
MB as found, of which 45 scans of 2 to 14 MB made 218 MB. Kept as display
copies, 86.5 MB, with 80 shrunk — in line with the archive's existing 100 KB
average. A phone receives these in bundles and decodes them to show them.

---

## 2. The order it is switched in

Switching the player to the catalogue alone first would take away every cover
the catalogue does not yet hold. So: the covers are inducted into the hub;
star sync carries `cover_art` and `file_art` to the nodes, and bundles carry
them to the phone `[SPEC-PL-106]`; and only then is the player's lookup
narrowed.

*Inducted 2026-09-27 on the hub:* 384 release covers and 116 file covers. The
catalogue then holds 1,256 and 116. Measured passage by passage, the three
sources against the catalogue alone:

| | passages |
| :--- | ---: |
| a cover both ways | 16,214 |
| no cover either way | 346 |
| a cover from the catalogue alone only | 0 |
| a cover the three sources gave, and the catalogue does not | 101 |

The 101 are three files in folders holding one picture for several albums:
Elton John's and Radiohead's (three albums each, one `cover.jpg`) and
Pharrell Williams' (three versions from different sources). The picture was
wrong for most of what it was shown for; losing it is `[SPEC-COV-020]` at work.
The catalogue's backup before the induction is in
`data/backups/pre-covers-20260927/`.

---

**Traceability:** `[SPEC-COV-010..050]` · from the maintainer's direction of 2026-09-27 · refines `[REQ-AND-205]`, `[SPEC-PL-105]`, `[REQ-VIS-170]`
