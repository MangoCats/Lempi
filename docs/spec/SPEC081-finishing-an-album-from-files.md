# SPEC081: Finishing an Album from Files

**Design Specification — Tier 2 · written and built 2026-11-19 · for `[REQ-LIB-100]`**

An album added as a folder of files is finished in the console the way a CD
add finishes itself. Asked for on 2026-11-19, when *Smiling Phases*, 26 MP3s
added by `ingest_folder.py` on 2026-11-13, was found six days later with every
track a local placeholder, no flavor, no lead-in or out, no album recorded for
any file and no cover. A CD add would have done all of it. Nothing had said the
album was unfinished, and nothing in the browser could finish it.

> **Related:** [SPEC056](SPEC056-cd-import.md) (adding a CD rip, what this
> matches) · [SPEC057](SPEC057-holding-a-passage-back.md) (holds, which a file
> import does not set) · [SPEC082](SPEC082-asking-musicbrainz.md) (the pace every
> MusicBrainz request here keeps) · `tools/finish_folder.py`,
> `tools/local_edition.py`, `tools/console_web/finish.html`

---

## 1. The page

**`[SPEC-FIN-010]` *Finish an album* is a page of its own, `/finish`.** Without
a folder it lists every folder with a track still unnamed or unanalyzed,
newest first. With `?folder=` it shows that album's stages and the one choice
it needs. The Jobs page links to it from two places: from each folder of its
*Not analyzed* list, and from an induct, beside the confirm and once the
induct has run. The induct's own stage list names the page where it skips the
release and the cover.

**`[SPEC-FIN-020]` Each stage is measured from the catalogue, never taken from
a job's account** `[GOV-SRC-010]`. Five stages, each *done*, *partly*, *not
done* or *waiting*, with its counts:

| stage | done when |
| :--- | :--- |
| edition | every file shown under a release, and both passages of every file named |
| flavor | every radio passage's recording has flavor |
| lead-in and out | every radio passage measured, or its boundary set by hand |
| cover | the edition's front is held; a `folder.jpg` or embedded picture is said beside it |
| works | MusicBrainz asked about every named recording; how many perform a known song is said |

Flavor says where its count comes from: measured from these files, or carried
by a recording another copy already gave flavor. On *Smiling Phases* 20 of 26
read done the moment the edition named them, with 10 measured, because 11
songs are also on the hub's Traffic CDs.

While a job runs, the page reads the stages again every 5 s and redraws them
in place; a read costs a catalogue query of a few milliseconds. Until
2026-11-19 they were drawn at load and at the end only, and flavor was
committed every tenth track, so a watched run sat still for a minute.
`extract_library.py` now commits each track as it lands. On a run of 50 or
fewer it also reports each one.

A folder whose files only lack an album record is not listed: 33 such folders
on 2026-11-19 had their albums shown by each recording's chosen release, as
`align_file_releases.py` leaves them.

## 2. The one choice

**`[SPEC-FIN-030]` The edition is the person's explicit choice; nothing else
is asked.** The page searches MusicBrainz by the files' tags, or by a pasted
release URL, through the same discovery job a passage's profile page uses
`[SPEC-SUI-215]`. Each candidate shows what tells one edition from another:
date, country, format, discs, label, barcode, MusicBrainz's own note. It also
shows how many files matched, and which file is which track, with unmatched
files and tracks named. Choosing one asks once, saying how many files will be
named and how many left as they are.

**`[SPEC-FIN-035]` A track is its disc and its place on it.** A two-disc set has
two track 1s. Matched by place alone, disc 2's tracks read as disc 1's, so the
unmatched-track report was wrong, and the track-number tiebreak could favour
the wrong disc. Where the file's tag and the release both say which disc, the
tiebreak needs them to agree.

## 3. What follows from it

**`[SPEC-FIN-040]` The edition is recorded as a CD add records the disc in
hand.** Every matched recording takes it as its chosen release, at its disc and
place, and every matched file is shown under it (`file_releases`). The source
is `folder:chosen`, which `choose_release.py` and `align_file_releases.py` leave
standing beside `cd:import` (`tools/release_sources.py`). Either would
otherwise have replaced a person's pick with a guess, and two editions of one
title are exactly what a title match cannot tell apart. A search that only
looks at a release keeps a choice made on it. It used to replace the
tracklist's rows, resetting `chosen` and dropping the disc.

**`[SPEC-FIN-045]` Both passages of a file are named, and every artist
credited.** The album passage takes its radio twin's recording, as a CD add
gives both `[SPEC-SA-110]`. Until 2026-11-19, accepting a release credited the
radio passage alone, so a whole-album play kept the placeholder. Every
credited artist is linked, with the sort name Browse sorts by
`[REQ-VIS-182]`; only the first was, and nameless of its sort.

**`[SPEC-FIN-050]` Then the rest, as one job, each step taking only what is
missing.** `finish-folder`:

1. **edition** -- `suggest_release.py --accept`, atomic. The job stops if it
   fails or matches no file, since every later step rests on it.
2. **flavor**, **lead-in and out** -- the analysis a CD add runs `[REQ-LIB-305]`.
3. **cover** -- the edition's front and back from the Cover Art Archive.
4. **works** -- this album's recordings alone `[SPEC-FIN-060]`, then loaded.
5. **reload** -- the player asked to rebuild, so the Program Director can
   choose the album; said plainly when no player answers.

After the edition, a failing step is said and the rest still run, and the job
fails if any did. Its last word is the folder's stages, measured after.

**`[SPEC-FIN-055]` An edition already in place is not applied again.** When the
chosen edition is the one recorded for every file, and every passage is named,
the edition step is skipped and says so. Applied again, it would rename a
track a person has corrected since. The other steps still run. A different
edition is a new choice, and is applied.

**`[SPEC-FIN-060]` Works are asked about for one album, not the library.**
`fetch_works.py --folder`; without it the job took on the whole library's
backlog, at 1.2 s a recording.

**`[SPEC-FIN-070]` Not done here, named.** A file import has no rip log, so
nothing is held for damage `[SPEC-HOLD-030]`. Occasions are not offered, as
the CD import page offers Christmas. A file the edition does not match is left
as it is, on its placeholder, and listed.

## 4. An edition MusicBrainz does not have

Added 2026-11-20, for music published only on Jamendo, Bandcamp or
archive.org: a Jamendo album of 61 MP3s on archive.org with a `cover.jpg`
beside them, tagged with title, artist, album and track, and with no
MusicBrainz release to pick. Until then such a folder could not be finished:
the job stopped at its first step, every track stayed a placeholder, the
artist was only a tag, and the folder stayed on the index for ever.

**`[SPEC-FIN-080]` The person names the edition, and it is recorded as one
from MusicBrainz is.** Beside the search, *Not on MusicBrainz?* opens a form;
recording it runs `finish-folder` with `local` in place of a release id, and
the edition step is `tools/local_edition.py`. It writes a release, each
artist, each track's title and credits, the release as each recording's
chosen one at its place, and each file shown under it, as `folder:local`.
That source is settled `[SPEC-FIN-040]`: `choose_release.py` and
`align_file_releases.py` leave it standing. A folder named this way reads
*done* at the edition stage and drops off the index once analyzed.

**`[SPEC-FIN-085]` The form is filled from what is recorded, else from the
tags.** It shows the album's title, artist and date, and for each file its
number, disc, title and artists, all editable (`/api/finish/tracks`). A track
with several artists separates them with `;`; the first is the one shown
(weight 1.0), the rest credited (0.5). A blank artist takes the album's. An
unticked file is left out of the edition, and one already on it leaves it. A
file whose recording is a MusicBrainz one is shown and left as it is. Two
tracks at one place, a date not `YYYY[-MM[-DD]]`, or a track naming no file
in the folder is refused, and nothing is written. Named again, the same
release is corrected, never a second made.

**`[SPEC-FIN-090]` Its ids are the library's own, and nothing asks about
them.** The release is `local:release:` and 32 hex digits of the SHA-1 of its
files' sorted `audio_md5`s, so the same files make the same release on any
node. An artist is the library's own when exactly one has that name, any
case, and is otherwise `local:artist:` and the hash of the folded name, so one
artist across albums is one. Each recording keeps its `local:audio:` id, and
its passages keep their `local:ingest` links, which is what keeps a
self-published track AcoustID cannot name out of the review queue. None is
MusicBrainz-shaped: `fetch_cover_art.py` and `fetch_chosen_tracks.py` pass
over a `local:` release, and the works stage reads *not needed*. Star sync
carries these rows by key, as any other.

**`[SPEC-FIN-095]` The cover is the one with the files.** The picture beside
them, else the first one a file carries, taken as `induct_covers.py` takes one
`[SPEC-COV-020]` and kept on the release where it has none. With no picture
anywhere, the cover stage reads *nothing to take*, which counts as finished.

---

**Traceability:** `[SPEC-FIN-010..095]` · for `[REQ-LIB-100]` · matches
`[SPEC-CDI-098]`, `[SPEC-SA-110]`, `[REQ-LIB-305]` · reuses `[SPEC-SUI-215]`
· `[SPEC-FIN-080]` is for files what `[REQ-LIB-300]` asks for a disc
