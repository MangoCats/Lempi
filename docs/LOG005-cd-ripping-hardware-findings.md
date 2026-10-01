# LOG005: CD Ripping — Hardware Findings

**Development Record — Tier 0**

Dated incident record behind [SPEC025](spec/SPEC025-cd-ripping.md) §5a's
failure-handling design. SPEC025 itself describes only current, decided
behaviour; this is where the first real-hardware testing against it, and
what it found, is kept, per `[GOV-DOC-050]`.

---

## 2026-09-03 — A USB-attached drive's DAE returns noise, not audio

**`[LOG-RIP-010]` An investigative DAO capture (`cdrdao`, Weezer "(White
Album)") converted to `.ogg` played back as static.** The TOC/CD-TEXT read
was genuine and correct — real track titles, ISRCs, matching the physical
disc — and the `.bin`'s byte count matched the TOC's own total duration
exactly, so the capture was not truncated. But the audio payload failed
the one test that actually distinguishes real audio from noise: lossless
FLAC compression. Two independently-sampled 10MB chunks (start of track
1, mid-album track 5) both *expanded* under FLAC (+0.6%, +0.8%) instead
of compressing — real audio, even the loudest modern master, always has
enough inter-sample/stereo correlation for FLAC to shrink it; only data
statistically close to random noise turns FLAC's frame overhead into a
net loss. Spectrograms confirmed it visually: flat, uniform broadband
energy, no harmonic structure, at both sampled points.

**`[LOG-RIP-020]` The same signature reproduced on a second, unrelated
disc, isolating the fault to the drive rather than that one disc.** Two
bounded test extractions (`timeout N cdrdao read-cd`, 3s and ~90s
wall-clock) against whatever disc was in the drive at the time — a
different, 14-track CD-DA disc — produced the identical result:
`cdrdao disk-info` read its TOC correctly, but the ~130-second-equivalent
raw-audio sample again expanded under FLAC (+1.4%) with the same flat
spectrogram. Two different discs, same drive, same failure — this rules
out "one bad disc" and points at the extraction path itself.

**`[LOG-RIP-030]` Diagnosis: this drive's DAE fails via `cdrdao`'s
generic-SCSI driver on this Linux machine; TOC-level reads do not.** The
drive is an HL-DT-ST DVDRAM GP65NS60, USB-attached (`lsblk` reports
`TRAN=usb`). `cdrdao disk-info` and the disc's own CD-TEXT/Q-subchannel
resolve correctly — the low-bandwidth control commands work. The bulk
raw-audio transfer (`READ CD`) `cdrdao`'s Generic SCSI-3/MMC driver
issues over this connection does not — a known failure mode for USB
optical bridge chipsets that pass ordinary block reads through cleanly
but don't correctly implement the raw-sector command DAE needs,
returning filler data rather than a clean SCSI error. This is consistent
with, and gives a first real-world data point toward, the reasoning
`[SPEC-RIP-020]` already gives for defaulting to EAC on Windows rather
than `cdrdao` there — though it does not yet confirm it, since the same
physical drive has not yet been tried via SPTI.

**Next.** Continuing on a Windows machine with EAC, same physical USB
drive. The next data point is whether EAC/SPTI succeeds where
`cdrdao`/generic-SCSI failed — supporting `[SPEC-RIP-020]`'s reasoning —
or the drive itself cannot do DAE regardless of host or driver, a
different and harder finding `[SPEC-RIP-020]` does not anticipate.

---

## 2026-09-03 — Confirmed on Windows: the same physical drive's DAE works cleanly via EAC/SPTI

**`[LOG-RIP-040]` The exact same physical drive, moved to a Windows
machine, ripped a 14-track CD cleanly through EAC 1.8 — no noise
signature, on either sample checked.** The disc's own TOC read correctly
(`REM DISCID B90E090E`, 14 tracks, `INDEX 01` positions in the same
`MM:SS:FF` timebase `[SPEC-RIP-030]` already specifies), and the full
extraction produced a 633,927,548-byte WAV (~59.9 min at 44.1kHz/16-bit
stereo) plus a matching `.cue` — the direct Windows analog of `cdrdao`'s
`.bin`+`.toc`, and the same drive model (`HL-DT-ST DVDRAM GP65NS60`)
`[LOG-RIP-030]` diagnosed as failing DAE on Linux.

Applied the identical two-sample diagnostic `[LOG-RIP-010]` established —
60-second samples (start of track 1; the start of track 5, mid-album) run
through the same FLAC-compression and spectrogram checks:

| Sample | WAV bytes | FLAC bytes | Ratio | Compressed by |
| :--- | ---: | ---: | ---: | ---: |
| Track 1 start | 10,584,078 | 7,689,325 | 72.65% | **27.35%** |
| Track 5 start | 10,584,078 | 6,285,390 | 59.39% | **40.61%** |

Both **shrank substantially** — the opposite of the Linux failure's
signature, where both samples *expanded* (+0.6%, +0.8%). Spectrograms of
both samples show clear harmonic banding and rhythmic transient structure
throughout, nothing resembling the flat, uniform broadband energy the
Linux noise samples showed.

**`[LOG-RIP-050]` `[SPEC-RIP-020]`'s reasoning is now confirmed, not just
plausible.** Same physical drive, same disc-reading task, two different
hosts/drivers: `cdrdao`'s generic-SCSI path on Linux returned noise where
EAC's SPTI path on Windows returned real audio. This is exactly the
"known failure mode for USB optical bridge chipsets" `[LOG-RIP-030]`
already named — the chipset passes ordinary block reads through cleanly
(both TOC reads worked, on both hosts) but only correctly implements the
raw-sector `READ CD` command DAE needs over one of the two paths tested.
One drive, one clean data point: SPTI succeeded where generic-SCSI did
not, on the exact question `[SPEC-RIP-020]` asked.

**`[LOG-RIP-060]` A real correction to `[SPEC-RIP-020]`'s own automation
claim, found in the course of this same test.** EAC's actual command-line
surface, per its own bundled documentation (`EAC.txt`, `FAQ.txt` —
checked directly, not assumed from a secondary source), is a handful of
driver-compatibility flags (`nocdtext`, `nostopcommand`, `notestunit`,
`nospeedsel`, `noreadsub`, `nomultisession`) and a per-track *post-encode*
external-program hook — not an unattended full-rip mode. `-testandcopy
-imagewav -outputdirectory ... -close`, cited in SPEC025 §2 from a
secondary web source, was tried directly against the real 1.8 build and
did nothing: the process launched and sat idle, no extraction started, no
output for the switches that should have triggered one. (`-outputdirectory`
alone does appear to be honored — the actual rip below, triggered by hand
through the GUI, wrote its output to the directory that flag named in an
earlier launch of the same process — but that is not the same as an
unattended rip.) The GUI extraction itself is trivial and fast once
reached; there is no confirmed way to reach it without a person or a GUI
driver clicking the button. `[SPEC-RIP-020]`'s automation row needs
correcting to match.

---

## 2026-09-29 — EAC crashes in its own file windows; CUERipper rips the same drive

**`[LOG-RIP-110]` EAC 1.8 on the desktop closes itself whenever it opens a
file window.** At the first desk session for the import page
([SPEC056](spec/SPEC056-cd-import.md)), *Copy Image & Create CUE Sheet* opened
its Save window and EAC vanished -- sometimes before the window drew, sometimes
on pressing **Save**. **Browse…** on the Directories tab did the same, so it is
not the rip. Windows' Application log records each one as the same fault: an
access violation (`0xc0000005`) in code with no module name -- code compiled at
run time, not in any DLL on disk -- seventeen times between 13:18 and 13:49.
Changed one at a time, none of these made a difference: burst instead of secure
mode; a folder with no spaces in its path (`C:\tmp\eac-test`); the
log checksum off; the metadata provider changed; the two network drives
disconnected; Windows 8 and Windows 7 compatibility modes; Vipunen stopped;
and EAC uninstalled and 1.8 reinstalled, its settings backed up first. A 32-bit
program opening the same Save and folder windows from PowerShell did not crash,
so Windows' dialogs themselves work. The cause is inside EAC on this machine,
and not found.

**`[LOG-RIP-120]` It predates the import page, and corrects SPEC027's reading
of the 2026-09-03 session.** The earliest EAC crash in the Application log and
in Windows' error reports is **2026-09-03, 23:35** -- the night of
`[LOG-RIP-040]`'s rip, with the same exception code and the same unnamed-module
signature. [SPEC027](spec/SPEC027-cd-ripping-windows-automation.md) says that
session's UI automation "closed the whole application" with one imprecise
click. The log fits this fault at least as well: a click that opened a file
window would have closed EAC exactly so. The automation may have been less
fragile than recorded; the decision it supported `[SPEC-RIP-088]` does not
rest on it alone.

**`[LOG-RIP-130]` CUERipper, same drive, same afternoon: a clean rip at the
first attempt.** CUERipper 2.2.6 (CUETools, GPL-2.0-or-later), unpacked with no
installer, in secure mode, one image file: every track accurately ripped,
confirmed by AccurateRip and by CTDB, read offset **+6** -- the value EAC's log
of 2026-09-03 used.
Its log is in EAC's own format, and the existing parser read it unchanged. Its
CUE sheet is in the Windows code page rather than UTF-8, found by a curly
apostrophe in a title. The desktop rips with CUERipper from here on
`[SPEC-CDI-012]`.

---

**Traceability:** first real-hardware validation of `[SPEC-RIP-052..056]`
(SPEC025 §5a's failure-handling design); **confirms** `[SPEC-RIP-020]`'s
tool-choice reasoning (the DAE question) and **corrects** its automation
claim (`[LOG-RIP-060]`); records why the desktop rips with CUERipper
(`[LOG-RIP-110..130]`) and **questions** SPEC027's account of the 2026-09-03
automation attempt (`[LOG-RIP-120]`).
