# GUIDE037: Ripping a CD With EAC

**Guide — Tier 1 · written 2026-09-29 · for Vipunen's import page, designed in [SPEC056](spec/SPEC056-cd-import-with-eac.md); opens beside its controls once built**

How to get a CD into your Lempi library: Exact Audio Copy (EAC) reads the disc,
Vipunen does the rest. You set EAC up once; after that, each disc is one key in
EAC and one button in Vipunen. EAC's menus and options are named here exactly
as EAC 1.8 shows them.

> **Until the import page exists** ([IMPL018](IMPL018-cd-import-page.md)), the
> one-time setup and ripping below apply as written; the adding is done with
> `python tools/ingest_cd.py <library> --folder <rip folder> --commit`, and the
> rip folder must already be where the album will live.

---

## Before you start

You need EAC 1.8 installed (free, from exactactaudiocopy.de), an optical drive,
and a CD. Vipunen's import page tells you if any of these is missing.

## One-time setup

Do this once per computer. The import page checks each of these settings and
ticks it off, so you can see what is still to do.

### 1. Run the configuration wizard

In EAC, open **EAC → Configuration Wizard…** and follow it with a CD in the
drive. It detects what your drive can do. When it asks about encoding, choose to
extract **uncompressed** -- Vipunen makes the MP3 itself.

### 2. Secure reading and the read offset

Open **EAC → Drive Options…** (F10).

- On **Extraction Method**, choose **Secure mode with following drive features
  (recommended)**, then press **Detect Read Features…** and accept what it
  finds.
- On **Offset / Speed**, tick **Use read sample offset correction** and press
  **Detect read sample offset correction…** with a popular CD in the drive. It
  finds your drive's offset by comparing with other people's rips. A value of
  0 almost always means this step has not been done.
- On the same tab, tick **Use AccurateRip with this drive**, and **CD-Text Read
  capable drive** if your drive supports it.

### 3. The log, in English, with a checksum

Open **EAC → EAC Options…** (F9), **Tools** tab, and tick:

- **Automatically write status report after extraction** -- the log is how
  Vipunen knows the rip is finished and whether it is good.
- **Append checksum to status report** -- proves the log was not edited.
- **Create log files always in english language** -- Vipunen reads the log's
  English wording.
- **Use CD-Text information in CUE sheet generation** -- puts the disc's own
  titles in the CUE sheet, for discs MusicBrainz does not know.

### 4. Where rips go

Still in **EAC Options…**, **Directories** tab: choose **Use this directory**
and set it to the rip inbox the import page shows you (it offers one inside your
music folder). Every rip lands there; Vipunen moves each album to its own folder
when you add it, so you never choose a folder in EAC again.

## Ripping a disc

1. Put the CD in. EAC shows its tracks; if it asks, pick the matching album from
   its metadata list, or just carry on -- Vipunen looks the disc up itself.
2. Choose **Action → Copy Image & Create CUE Sheet → Uncompressed…**
   (Alt+F7) and confirm the file name EAC proposes.
3. Wait for EAC to finish and write its status report. That is all.

For a scratched or rare disc, use **Action → Test & Copy Image & Create CUE
Sheet → Uncompressed…** instead: it reads everything twice and compares, so it
takes about twice as long.

## Adding it in Vipunen

Open Vipunen's **import** page. The rip appears as a card on its own, and says:

- **Every track accurately ripped** -- matched other people's rips exactly.
- **Ripped; not in AccurateRip's database** -- EAC read it cleanly, but there is
  nothing to compare with. Usually fine.
- **Track N had read errors** -- clean the disc and rip it again with *Test &
  Copy*. You can still add it; it is marked as a degraded rip.

Open the card: Vipunen shows the album -- artist, title, year and track list.
If MusicBrainz knows several editions, pick yours. Check the folder name it
proposes, then press **Add to library**. It files the album, makes the MP3,
removes the large WAV, and splits the album into its tracks from the disc's own
track list.

To keep a lossless FLAC copy of every rip as well, turn on **Keep a lossless FLAC
copy of CD rips** on the Lempi player's Settings page, on this computer. It is
off unless you turn it on, and it applies to every rip after that.

## Afterwards

- **Make it playable here** asks this computer's player to pick the album up.
- **Send to the speakers** opens the Export page with the album already chosen.
  A speaker plays an album only once its audio has been sent to it.
- Anything Vipunen could not identify for certain waits in the **review** queue,
  linked from the card.

## When something goes wrong

- **No card appears** -- check the Directories setting (step 4) points at the
  inbox the page names, and that the status report is on (step 3).
- **"Not accurately ripped" on every track** -- the read offset is probably not
  set (step 2).
- **The album is already in the library** -- the card says so, and names the
  album it matches, before anything is done.

---

**Traceability:** the guide for [SPEC056](spec/SPEC056-cd-import-with-eac.md) · names checked against EAC 1.8's `Languages/English.txt`, 2026-09-29
