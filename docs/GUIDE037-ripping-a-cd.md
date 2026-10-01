# GUIDE037: Ripping a CD

**Guide — Tier 1 · written 2026-09-29, CUERipper first the same day · opens beside the controls of Vipunen's import page ([SPEC056](spec/SPEC056-cd-import.md))**

How to get a CD into your Lempi library: CUERipper reads the disc, Vipunen does
the rest. You set CUERipper up once; after that, each disc is one button in
CUERipper and one in Vipunen. CUERipper's buttons and options are named here
exactly as CUERipper 2.2.6 shows them.

Exact Audio Copy (EAC) works too, and its setup is at the end of this guide
under **Using EAC instead**. CUERipper is recommended because it needs less
setting up, finds your drive's read offset by itself, and keeps working on
computers where EAC closes itself whenever it opens a file window.

The import page is in Vipunen's console -- **import** in the menu along the
top. Its **How to** button opens this guide beside it, and each **?** opens it
at the step it sits next to.

---

## Before you start

You need CUERipper (free, part of CUETools, from cue.tools), an optical drive,
and a CD. Vipunen's import page tells you if any of these is missing.

## One-time setup

Do this once per computer. The import page reads CUERipper's settings and ticks
each one off, so you can see what is still to do.

### 1. Unpack CUERipper

CUETools comes as a zip with no installer. Unpack it into your Downloads folder,
your Desktop or Program Files -- Vipunen looks for **CUERipper.exe** in each --
and start **CUERipper.exe**. The first time, Windows may ask whether to run it.

### 2. Secure reading, one file for the disc

In CUERipper's main window, below the track list:

- Move the mode slider to **Secure**. (**Burst** reads once and never checks;
  **Paranoid** is slower still and seldom needed.)
- Set the drop-downs to **lossless**, **wav**, and **image**. *Image* makes one
  file for the whole disc with a CUE sheet listing where each track starts,
  which is what Vipunen reads. Vipunen makes the MP3 itself.
- **Read offset** shows your drive's read offset. CUERipper fills it in from
  AccurateRip's list of drives; if it shows 0, type the offset for your drive
  from [accuraterip.com/driveoffsets.htm](http://www.accuraterip.com/driveoffsets.htm).

Leave **Options** as they are: **EAC log style**, under **Extraction**, is on
by default, and Vipunen reads that log to know whether the rip is good.

### 3. Where rips go

In the output path box at the bottom, type:

`%music%\_Rips\%artist% - %album%\%artist% - %album%.cue`

`%music%` is your Music folder, and `_Rips` inside it is the rip inbox the
import page shows you. Each rip lands in a folder of its own there; Vipunen
moves each album to its proper place when you add it, so you never choose a
folder in CUERipper again. If you chose a different inbox on the import page,
put that folder in place of `%music%\_Rips`.

### 4. Close it once

CUERipper saves its settings when it closes, and that is when the import page
can read them. Close it once after setting it up; the ticks appear.

## Ripping a disc

1. Put the CD in. CUERipper reads it and looks it up; if it offers several
   releases, pick yours, or just carry on -- Vipunen looks the disc up itself.
2. Press **Go**.
3. Wait until the status bar says **Done ripping**. That is all.

For a scratched or rare disc, tick **Test & Copy** first: it reads everything
twice and compares, so it takes about twice as long.

## Adding it in Vipunen

Open Vipunen's **import** page. The rip appears as a card on its own, and says:

- **Every track accurately ripped** -- matched other people's rips exactly.
- **Ripped; not in AccurateRip's database** -- read cleanly, but there is
  nothing to compare with. Usually fine.
- **Track N had read errors** -- clean the disc and rip it again with *Test &
  Copy*. You can still add it; it is marked as a degraded rip.

Open the card: Vipunen shows the album -- artist, title, year and track list.
If MusicBrainz knows several editions, pick yours. Check the folder name it
proposes, then press **Add to library**. It files the album, makes the MP3,
removes the large WAV, and splits the album into its tracks from the disc's own
track list. Then it analyses the tracks -- how each one sounds, and how it fades
in and out -- so the radio can choose them. That takes about half a minute of
work a track, several at once; the card says when it is done.

A disc ripped a file per track (CUERipper's **tracks** choice) is added the same
way, each track its own MP3. A track the ripper never wrote -- a damaged one it
stopped on -- is named on the card, and the album is added without it.

A Christmas collection or a children's album can be marked as one, whole, as it
is added: tick **Christmas music** or **children's music** under the editions.
A title with "Christmas" or "Kids" in it ticks the box for you; untick it if
that is wrong. Every track on the disc is then marked, as if you had set each
one by hand, and you can still change any single track afterwards.

To keep a lossless FLAC copy of every rip as well, turn on **Keep a lossless FLAC
copy of CD rips** on the Lempi player's Settings page, on this computer. It is
off unless you turn it on, and it applies to every rip after that.

## Afterwards

- **Make it playable here** asks this computer's player to pick the album up.
- **Send to the speakers** opens the Export page with the album already chosen.
  A speaker plays an album only once its audio has been sent to it.
- Anything Vipunen could not identify for certain waits in the **review** queue,
  linked from the card.

## Splitting an album file

An album that came in as one file, with no CUE sheet beside it, plays as one
long track. On the import page, under **Split an album file**:

1. Type part of its artist, album or file name and press **find**, then **split
   this** beside the file.
2. Vipunen looks the album up. The edition closest to the file's length is first
   and already chosen; if none fits, change the search words and search again.
3. Press **Show where it would be cut**. Each cut is listed beside the track it
   should be, with how many seconds it is off.
4. Press **Split it**. Vipunen names each track by its sound, then lists them;
   open any in Lempi's editor to move a cut that is not quite right.

## When something goes wrong

- **No card appears** -- check the output path (step 3) puts the rip in a folder
  of its own inside the inbox the page names.
- **A card says it is still ripping long after CUERipper finished** -- the rip
  stopped part way. Rip the disc again; delete the unfinished folder.
- **"Not accurately ripped" on every track** -- the read offset is probably
  wrong (step 2).
- **CUERipper says "Rip probably contains errors"** -- clean the disc and rip it
  again with **Test & Copy**.
- **The album is already in the library** -- the card says so, and names the
  album it matches, before anything is done.
- **EAC closes itself when it opens a file window** -- it does on some
  computers, reinstalled or not. Use CUERipper.

---

## Using EAC instead

Exact Audio Copy 1.8 (free, from exactaudiocopy.de) makes the same rip. If
CUERipper is not found and EAC is, the import page checks EAC's settings
instead. EAC's menus and options are named here exactly as EAC 1.8 shows them.

### EAC 1. Run the configuration wizard

In EAC, open **EAC → Configuration Wizard…** and follow it with a CD in the
drive. It detects what your drive can do. When it asks about encoding, choose to
extract **uncompressed** -- Vipunen makes the MP3 itself.

### EAC 2. Secure reading and the read offset

Open **EAC → Drive Options…** (F10).

- On **Extraction Method**, choose **Secure mode with following drive features
  (recommended)**, then press **Detect Read Features…** and accept what it
  finds.
- On **Offset / Speed**, set the read offset **before** turning AccurateRip on
  -- ticking **Use AccurateRip with this drive** greys the offset controls out:
  1. Untick **Use AccurateRip with this drive**.
  2. Tick **Use read sample offset correction** and type your drive's offset,
     from [accuraterip.com/driveoffsets.htm](http://www.accuraterip.com/driveoffsets.htm).
     If your exact model is not listed, a sibling model with the same number
     in its name (the letters around it vary) almost always shares it. Press
     **OK**.
  3. Open **Drive Options…** again and tick **Use AccurateRip with this drive**.
     The offset stays, greyed out. Press **OK**.

  **Detect read sample offset correction…** is not a shortcut: it works only
  with a few particular reference CDs EAC lists, so an ordinary popular CD
  says "not in database". A value of 0 almost always means the offset was
  never set.
- On the same tab, tick **CD-Text Read capable drive** if your drive supports
  it.

### EAC 3. The log, in English, with a checksum

Open **EAC → EAC Options…** (F9), **Tools** tab, and tick:

- **Automatically write status report after extraction** -- the log is how
  Vipunen knows the rip is finished and whether it is good.
- **Append checksum to status report** -- proves the log was not edited.
- **Use CD-Text information in CUE sheet generation** -- puts the disc's own
  titles in the CUE sheet, for discs MusicBrainz does not know.

Then, on the **General** tab of the same window, tick **Create log files always
in english language** -- Vipunen reads the log's English wording. (Not on the
Tools tab, where the other log settings are.)

### EAC 4. Where rips go

Still in **EAC Options…**, **Directories** tab: choose **Use this directory**
and set it to the rip inbox the import page shows you. Every rip lands there,
and Vipunen gives each one a folder of its own.

### Ripping with EAC

1. Put the CD in. If EAC asks, pick the matching album from its metadata list,
   or just carry on.
2. Choose **Action → Copy Image & Create CUE Sheet → Uncompressed…**
   (Alt+F7) and confirm the file name EAC proposes.
3. Wait for EAC to finish and write its status report.

For a scratched or rare disc, use **Action → Test & Copy Image & Create CUE
Sheet → Uncompressed…** instead.

---

**Traceability:** the guide for [SPEC056](spec/SPEC056-cd-import.md) · CUERipper's names read from CUERipper 2.2.6's own resources, EAC's checked against EAC 1.8's `Languages/English.txt`, 2026-09-29
