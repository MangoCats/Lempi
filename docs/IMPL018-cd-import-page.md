# IMPL018: The CD Import Page

**Implementation Plan — Tier 3 · written 2026-09-29 · built, and run at the desk the same day, §8 · the ripper is CUERipper since, §9 · decisions taken 2026-09-29, [SPEC056](spec/SPEC056-cd-import.md) §10**

How [SPEC056](spec/SPEC056-cd-import.md) is built: a Vipunen page
that takes a ripped CD into the library with buttons, and the guide that
opens beside it. Six phases, each shippable alone. The recommended decisions
were taken, D3 with a standing switch rather than a per-disc offer.

> **Related:** [SPEC056](spec/SPEC056-cd-import.md) · [GUIDE037](GUIDE037-ripping-a-cd.md) · `tools/ingest_cd.py`, `tools/jobs.py` (`cd-rip`, `segment-dao`), `tools/console.py`

---

## 1. Phase 0 -- the tool underneath, corrected

**`[IMPL-CDI-100]`** `tools/ingest_cd.py`:

1. **A real dry run** `[SPEC-CDI-040]`: parse the CUE and the log, look up the
   Disc ID, and return the release candidates with their track lists -- no
   encode, nothing on disk.
2. **The chosen edition as an argument** (`--release MBID`), so a person who
   picked one on the page is not asked again in the review queue.
3. **A destination** (`--into FOLDER`): the rip is moved there before it is
   encoded, so the catalogue records the album's permanent path.
4. **The WAV removed** after a verified encode `[SPEC-RIP-040]`; a FLAC copy
   kept first when the Lempi skin's switch is on `[SPEC-CDI-058]`, which is
   built here too: the setting, its route, and the switch on a Vipunen-support
   build's Settings page.

**Tests.** A dry run leaves the folder byte-for-byte as it was; a commit with a
chosen release writes no `id_checks` down-select; the WAV is gone and the MP3
decodes; a failed encode leaves the WAV and writes nothing.

## 2. Phase 1 -- the page and the guide panel

**`[IMPL-CDI-200]`** A new console page, **import**, in the navigation. Its
first version holds only the guide: a **How to** button, and a panel that
renders [GUIDE037](GUIDE037-ripping-a-cd.md) from Markdown -- headings, paragraphs, lists, code and
links, anything else shown as text -- with each heading an anchor, so
`/guide/ripping#one-time-setup` opens at that section `[SPEC-CDI-070]`.

**Tests.** Every heading in GUIDE037 has an anchor the page's **?** links name;
raw HTML in the Markdown is shown as text, not rendered.

## 3. Phase 2 -- the setup check

**`[IMPL-CDI-300]`** Read `HKCU\Software\AWSoftware\EACU` with `winreg`, never
writing it. The on/off settings are checked now. **Calibration**, done once with
the maintainer at the desk: each multi-valued setting (extraction mode, secure
mode, offset) is changed in EAC while a script records the registry before and
after, so the check reads their values from evidence, not guesswork. Until then
they are shown for a person to confirm. EAC missing, no drive, ffmpeg missing:
each a plain line `[SPEC-CDI-028]`.

**Tests.** Against recorded registry snapshots: this desktop's today (five
failures, SPEC056 §2), and one after setup (none).

## 4. Phase 3 -- the inbox and the rip cards

**`[IMPL-CDI-400]`** A read-only scan of the inbox every few seconds while the
page is open, grouping by CUE sheet; in progress or finished by the log
`[SPEC-CDI-030]`. The verdict from `cd_toc.parse_eac_log`, in words, and
`CheckLog.exe` on the log where EAC wrote a checksum `[SPEC-CDI-035]`.

**Tests.** Fixture folders: a rip mid-way (no log), a clean one, one with a
track not accurately ripped, one not in AccurateRip, one with a tampered log.

## 5. Phase 4 -- preview and add

**`[IMPL-CDI-500]`** Opening a card runs the Phase 0 dry run as a job and shows
the album: editions to choose from, track list, the proposed folder (editable),
and whether the catalogue already holds it `[SPEC-CDI-045]`. **Add to library**
runs `cd-rip` with the choices, streaming progress like every job. The finished
card offers the player reload and *Send to the speakers*, which opens the Export
page with the album chosen `[SPEC-CDI-055]`.

**Verify.** A real disc, end to end, at the desk with the maintainer: inserted,
ripped with one key, added with one button, played on the desktop player, sent
to one speaker and played there.

## 6. Phase 5 -- album files with no track list

**`[IMPL-CDI-600]`** The second panel `[SPEC-CDI-060]`: the library's single
files holding a whole album, a release search for each, the cascade's cuts
shown, the existing boundary editor opened to adjust, and `segment-dao`
committed. Skipped if D5 defers it.

**Verify.** One of the library's own album files, split, reviewed and played.

## 7. The guide

**`[IMPL-CDI-700]`** GUIDE037 is written with Phase 1, from EAC 1.8's own
language file, and corrected at the Phase 4 desk session wherever the real
screens differ from it. A screenshot is added only where words fail.

## 8. Status, 2026-09-29

**`[IMPL-CDI-800]` Built, everything that needs no disc and no hand on EAC:**

- **Phase 0** (`997e35d`): the true preview, `--release`, `--into`, the WAV
  removed and the CUE repointed; tested end to end with a real WAV, ffmpeg and
  `hash_audio`. The FLAC switch is on the Lempi skin's Settings page
  (`08d5df8`).
- **Phases 1, 3, 4** (`944b921`): the page, the guide panel, the inbox cards and
  their verdicts, preview and add -- driven through HTTP by
  `tools/test_console_import.py` against a real `console.py`, a real rip and a
  local stand-in for MusicBrainz.
- **Phase 2**, reading: against this desktop's registry the check finds five to
  fix (inbox, status report, its checksum, the English log, a read offset of 0)
  -- the plan above said four; `CreateEnglishLogFile` was off as well.
- **Phase 5** (`393b186`): by **search**, not by a list. Measured first: the
  library's long single-passage files are all long songs, so a list of
  "unsplit albums" would have offered nine wrong answers and no right one.

**Found on the way.** `cd_toc.parse_eac_log` matched only one of EAC's three
per-track AccurateRip outcomes, so a real mismatch passed as a good rip
(`606d912`). `CheckLog.exe` is not yet run: its command line is undocumented,
and it is tried at the desk rather than guessed.

**At the desk, 2026-09-29** -- a day early:

1. *Calibration*, with `tools/eac_calibrate.py`: secure mode is `ExtractionMode`
   `04 00`, burst `00 00`; *Use this directory* is `DirectoryUse` 0
   (`1fc45dd`). The guide corrected where EAC's screens differed: the English
   log is on the General tab, and the offset is typed with AccurateRip off,
   since ticking it greys the offset out (`aab766c`).
2. *Setup* -- every check green, and then EAC crashed on every rip, as
   `[LOG-RIP-110]` records.
3. *Real discs*, end to end, with CUERipper instead: four albums added through
   the page. The first CUE sheet was in the Windows code page and misread a
   curly apostrophe; fixed, and sheets are now stored as UTF-8 (`2673e87`,
   `fb77a75`).

## 9. CUERipper, 2026-09-29

**`[IMPL-CDI-900]`** The maintainer's decision at the desk `[SPEC-CDI-012]`:
the setup check reads CUERipper's `settings.txt` when CUERipper is found, and
EAC's registry only when it is not. GUIDE037 leads with CUERipper and keeps
EAC's setup as its last section; its file name no longer names EAC, nor does
SPEC056's. The page's rip step and summary name whichever ripper was found.

**Tests.** `test_cd_import.py`: the guide's settings read back; CUERipper's
default output path and one two folders deep refused, with the fix naming the
inbox; burst refused, paranoid accepted; nothing guessed where a key is absent.

**Calibrated 2026-10-01**, from the first `settings.txt` CUERipper wrote at the
desk: `ComboImage` 0 is *image*, and switches are `1`/`0`. The first version
expected `True` and showed *EAC log style* as off while it was on -- the test
fixture had been written from the same guess, so it passed. The fixture is now
the desk file's own lines, and every check on the desk reads green.

**Left:**

1. *Send to the speakers* and play there -- Phase 4's **Verify**, second half.
   The four albums are catalogued but their audio is not on any speaker yet,
   and star sync refuses a catalogue entry without it `[SPEC-STAR-080]`.
2. Phase 5's **Verify**: one album file split and played.

---

**Traceability:** `[IMPL-CDI-100..900]` · builds [SPEC056](spec/SPEC056-cd-import.md) · uses `[SPEC-RIP-040]`, `[SPEC-RIP-054]`, `[SPEC-RIP-088]`
