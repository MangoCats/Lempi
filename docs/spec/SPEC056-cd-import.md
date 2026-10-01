# SPEC056: CD Import, From Vipunen's Own Page

**Design Specification — Tier 2 · written 2026-09-29 · accepted 2026-09-29 (decisions in §10), built 2026-09-29, first albums added at the desk the same day · the ripper is CUERipper since that session, EAC the alternative `[SPEC-CDI-012]` · build plan in [IMPL018](../IMPL018-cd-import-page.md) · the guide it opens: [GUIDE037](../GUIDE037-ripping-a-cd.md)**

Bringing a CD into the library worked, but only for someone who reads
specifications and types command lines: rip in EAC with the right settings,
put the files in the folder the album will live in forever, then run
`ingest_cd.py` by hand. This makes it a page in Vipunen a person can use without
either -- and puts the how-to beside the controls it describes.

> **Related:** [SPEC025](SPEC025-cd-ripping.md) (ripping, as decided) · [SPEC027](SPEC027-cd-ripping-windows-automation.md) `[SPEC-RIP-088]` (person-assisted: Vipunen never drives EAC's window or the drive) · [SPEC028](SPEC028-cd-ripping-identification.md) (Disc ID, CD-TEXT, AcoustID) · [SPEC024](SPEC024-dao-segmentation-cascade.md) (album files with no track list) · [LOG005](../LOG005-cd-ripping-hardware-findings.md) (this desktop's drive and EAC)

---

## 1. The experience

**`[SPEC-CDI-010]` Put the disc in, press one button in the ripper, press one
in Vipunen.** After a one-time setup that Vipunen itself checks, a person inserts a
CD, presses **Go** in CUERipper (or chooses *Copy Image & Create CUE Sheet* in
EAC), and walks away. Vipunen
notices the finished rip on its own, says what it is and whether it ripped
cleanly, and offers **Add to library**. Nothing is typed: no paths, no command
lines, no release ids. Every step on the page has a **?** that opens the guide
at that step, in a panel beside the controls.

**`[SPEC-CDI-012]` CUERipper is the ripper; EAC is the alternative.** Decided at
the desk, 2026-09-29, after EAC 1.8 on the desktop closed itself every time it
opened a file window -- saving a rip, or browsing for a folder -- in burst and
secure mode alike, with Vipunen stopped, after a clean reinstall, and in every
Windows compatibility mode tried. Windows' own log has the same crash on
2026-09-03, the night of the first rip, so it predates this work
([LOG005](../LOG005-cd-ripping-hardware-findings.md) `[LOG-RIP-110]`). CUERipper (CUETools 2.2.6, GPL-2.0-or-later) ripped the same
disc cleanly at the first attempt, and writes what the flow already reads: one
image, a CUE sheet, and a log in EAC's own format -- `cd_toc.parse_eac_log`
read it unchanged. It also shows the drive's read offset from AccurateRip's list
of drives, where EAC has it typed by hand. Vipunen checks whichever is
installed, CUERipper first; nothing after the rip depends on which one ran.

**`[SPEC-CDI-015]` What stays as SPEC027 decided.** Vipunen still never touches
the optical drive or the ripper's window `[SPEC-RIP-088]`: EAC is GUI-only
`[LOG-RIP-060]`, and clicking it for the person proved fragile. What changes is
everything around that one click -- setting up, finding the rip, naming it,
filing it, and checking it.

## 2. One-time setup, checked rather than trusted

**`[SPEC-CDI-020]` An inbox, not a destination per disc.** The ripper is set once
to a single rip inbox (default: `_Rips` inside the music folder) -- CUERipper by
its output path, `%music%\_Rips\%artist% - %album%\%artist% - %album%.cue`, which
gives each rip a folder of its own there; EAC by *Use this directory*. Vipunen
moves each album to its permanent home when it is added (§5). So the person
never chooses a folder per disc, and a rip abandoned half-way never pollutes the
library.

**`[SPEC-CDI-025]` Vipunen reads the ripper's settings and ticks them off.** The
page shows each setting the flow depends on with a tick, or with one line saying
how to fix it and a **?** into the guide. CUERipper keeps its settings in a text
file, `settings.txt`, written when it closes -- in `%APPDATA%\CUERipper` when a
file named `user_profiles_enabled` sits beside the program, as it does in the
portable zip:

| Setting (as CUERipper names it) | Why | Checked by |
| :--- | :--- | :--- |
| the output path, into the inbox | rips land where Vipunen looks, one folder each | `PathFormat` |
| the mode slider on *Secure* | a verified read | `SecureMode` (0 Burst, 1 Secure, 2 Paranoid) |
| *image*, not tracks | one file and a CUE sheet, as §3 reads | `ComboImage` -- asked until calibrated |
| *EAC log style* (Options, Extraction) | Vipunen reads the log's EAC wording | `CreateEACLOG` |

The key names are read from CUERipper 2.2.6's own binaries, and the slider's
values from the order of its own labels. Which `ComboImage` index is *image*
is not in the binary, so that line asks a person to confirm until a settings
file written with *image* chosen has been read.

EAC keeps its options in the registry (`HKCU\Software\AWSoftware\EACU`),
readable by any program:

| Setting (as EAC names it) | Why | Checked by |
| :--- | :--- | :--- |
| *Use this directory* = the inbox | rips land where Vipunen looks | `DirectoryUse`, `DirectorySpecification` |
| *Automatically write status report after extraction* | the log is the rip's verdict | `AutoSaveStatus` |
| *Append checksum to status report* | the log can be proven unedited | `AddChecksumLogFile` |
| *Create log files always in english language* | Vipunen reads the log's English | `CreateEnglishLogFile` |
| *Use CD-Text information in CUE sheet generation* | titles when MusicBrainz has none | `AddCDTextToCUESheet` |
| *Use AccurateRip with this drive* | per-track proof against others' rips | the drive's `UseAccurateRip` |
| *Secure mode*, read offset set | a verified read, aligned for AccurateRip | the drive's `ExtractionMode`, `SampleOffset` |

It **reads and never writes**: each ripper owns its settings and must be closed
to have them changed under it. The on/off values read unambiguously (`ff` on, `00` off,
measured 2026-09-29); the multi-valued ones -- the extraction mode, the offset
-- are shown for a person to confirm until each is calibrated against EAC 1.8
(IMPL018 Phase 2). On this desktop the check fails today on five counts: no
automatic log, no log checksum, no English log, the directory still
`C:\tmp\eac-test\` and not in use, and a read offset of 0 -- measured, not
assumed.

**`[SPEC-CDI-028]` Missing pieces are said plainly, not failed on.** No ripper
found, its settings not yet written, ffmpeg absent: each is a line on the page saying
what is missing and where to get it `[SPEC-RIP-024]`, `[SPEC-RIP-052]`.

## 3. Finding the rip

**`[SPEC-CDI-030]` Vipunen watches the inbox.** While the page is open it looks
every few seconds, and groups what it finds by CUE sheet: the `.cue`, the audio
file it names, and the log. A rip is **in progress** while its audio file is
growing or its log is absent, and **finished** once the log is written -- both
rippers write it last. Each rip becomes a card on the page.

**`[SPEC-CDI-035]` The card says whether the rip is good, in words.** From the
log: *every track accurately ripped* (AccurateRip matched), *ripped, not in
AccurateRip's database* (secure mode said it read cleanly; nothing to compare
against), or *track 7 had read errors* -- with what to do about it (clean the
disc, rip again with *Test & Copy*). EAC's own `CheckLog.exe` confirms the log's
checksum where one was written. A bad rip can still be added
`[SPEC-RIP-054]`; it is marked, never silently accepted.

## 4. Knowing what it is

**`[SPEC-CDI-040]` The preview is real and quick.** Opening a card looks the
disc up -- MusicBrainz Disc ID, then the CUE's CD-TEXT -- and shows the album
as it will be added: artist, title, year, cover, and the track list with titles
and times. Where MusicBrainz knows several editions, the person picks one here,
once, rather than later in the review queue. Nothing is encoded or written to
preview. **Today's dry run does both**: `ingest_cd.py` encodes the whole MP3 and
leaves it on disk before its dry run reports (found 2026-09-29).

**`[SPEC-CDI-045]` Already in the library is said before anything is done.** A
disc whose Disc ID or chosen release the catalogue already holds is flagged on
its card, with the album it matches.

## 5. Adding it

**`[SPEC-CDI-050]` One button, a progress bar, and a finished album.** *Add to
library* runs one job: move the rip from the inbox to `Artist\Album (Year)` in
the music folder (the name shown, and editable, on the card), encode the MP3
`[SPEC-RIP-045]`, write the passages from the CUE with the edition chosen,
record the rip's verdict `[SPEC-RIP-054]`, and remove the WAV
`[SPEC-RIP-040]` -- a FLAC copy kept first when the Settings switch says so `[SPEC-CDI-058]`. **Today the WAV is never removed** -- about 630 MB left beside
each album (found 2026-09-29). Anything still uncertain goes to the existing
review queue, linked from the finished card.

**`[SPEC-CDI-055]` Afterwards, the next steps are buttons too.** *Make it
playable here* asks the local player to reload (as the Jobs page does); *Send
to the speakers* opens the Export page with this album already chosen
`[SPEC-SUI-095]`. The page says that a speaker only plays an album once its
audio has reached it -- a catalogue entry alone is refused by star sync
`[SPEC-STAR-080]`.

## 6. Album files with no track list

**`[SPEC-CDI-060]` The same page splits an existing album file.** A second panel
finds, by search, a file that holds a whole album and no track list -- a DAO
capture `[SPEC024]` -- and splits it with the same preview-then-add
shape: find its release, show where the cascade would cut, open the existing
boundary editor to adjust, then commit. Today that is the `segment-dao` job with
a hand-typed track count or durations, and no page at all.

## 7. The guide beside the controls

**`[SPEC-CDI-070]` One guide, opened in place.** A **How to** button opens a
panel beside the controls; each step's **?** opens it at that step's section,
and each failed setup check links to its fix. The panel renders
[GUIDE037](../GUIDE037-ripping-a-cd.md) -- the same Markdown a reader
sees on GitHub -- so there is one source and it cannot drift into two. The
renderer is small and takes no raw HTML.

## 8. What a person types

**`[SPEC-CDI-080]` Nothing, after choosing the inbox once.** The inbox has a
default and a **Use this** button; the album folder name is proposed and
editable; every action is a button. The command-line tools stay, for scripts
and for the operator, with the same behaviour the page has.

## 9. Measured on this desktop

EAC 1.8 is installed; its menu and option names above are quoted from its own
`Languages/English.txt`, not recalled. The drive is the HL-DT-ST GP65NS60 whose
extraction LOG005 proved through EAC `[LOG-RIP-040]`.

CUERipper 2.2.6 is unpacked, not installed. Its first rip, 2026-09-29: secure
mode, read offset +6 (the same as EAC's log of 2026-09-03), every track
accurately ripped and confirmed by AccurateRip and CTDB. Its CUE sheet is in the
Windows code page, not UTF-8 -- found by a curly apostrophe in a title -- and is
now read as such and stored as UTF-8. Four albums went through the page that
afternoon.

## 10. Decided by the maintainer, 2026-09-29

- **D1** A rip inbox, as §2 describes -- not a folder per disc.
- **D2** The setup check reads EAC's registry and never writes it.
- **D3** The WAV is removed after adding. A lossless copy is **not** offered per
  disc: one switch on the Lempi skin's Settings page, *keep a lossless FLAC copy
  of CD rips*, off by default `[SPEC-CDI-058]`.
- **D4** The guide is the GitHub-readable Markdown, rendered in the panel.
- **D5** The album-file splitter (§6) is in this build, as its last phase.
- **D6** EAC's default action is *Copy Image & Create CUE Sheet*; *Test & Copy*
  is for a scratched or rare disc. The same holds for CUERipper's **Test & Copy**.
- **D7** (at the desk) CUERipper is the ripper; EAC stays, as the alternative
  the page checks when CUERipper is not found `[SPEC-CDI-012]`.

**`[SPEC-CDI-058]` Keeping a lossless copy is a standing choice, not a
question.** The switch is stored in the desktop player's own settings
(`player_settings`, key `keep_lossless_rips`) and read by Vipunen when it adds a
rip: on, a FLAC copy is made beside the MP3 before the WAV is removed. It shows
only on a player built with Vipunen support -- the desktop that rips -- never
on an appliance, which has no drive and no rips to keep.

## 11. Open

**`[SPEC-CDI-900]`** Multi-disc sets and hidden audio stay with
[SPEC026](SPEC026-cd-ripping-passages.md). Whether the page should also send an
album to the speakers without the Export page's own confirmation is left to use.
CUETools also ships a command-line ripper, `CUETools.Ripper.Console.exe`; whether
it reopens SPEC027's automation question `[SPEC-RIP-088]` is not examined.

---

**Traceability:** `[SPEC-CDI-010..080]` (with `[SPEC-CDI-012]`, `[SPEC-CDI-058]`), `[SPEC-CDI-900]` · builds on `[SPEC-RIP-020]`, `[SPEC-RIP-088]`, `[SPEC024]`, `[SPEC028]` · corrects, when built, the dry run and WAV retention of `tools/ingest_cd.py`
