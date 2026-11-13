# LOG014: Library Work

**Experiment Record — running, begun 2026-11-08**

A dated record of work done **to the library itself**: the hub's catalogue
and the files it names, changed by a tool or by hand other than by ordinary
intake. Retiming passages, holding or releasing them, merging or swapping a
rip, moving files, re-keying, repairing what a sync did. Until 2026-11-08 the
open half of this lived only in one assistant's working notes. None of it was
in any document's Open section, so nobody else could see it.

§2 is what is still open, §3 the entries, newest first. Every count is what
was measured on the day it names. **Measure again before acting on one**: the
catalogue changes under the console every day.

> **Related:** [data/README.md](../data/README.md) for where each copy of the
> pair lives · [SPEC046](spec/SPEC046-star-sync.md) `[SPEC-STAR-084]` for how
> long a tool's backup is kept · [ROADMAP](ROADMAP.md) for what is not built

---

## 1. How an entry is written

**`[LOG-LIBW-010]` One entry per job, with what it would take to undo it.**
An entry gives: the date; the tool and how it was run; how many tracks and
passages it changed; **where** it was applied (the hub only, or which nodes,
verified on each node, not by a sync's exit status); the backup it took;
whose word it ran on; and what it left open, which also goes into §2.

**`[LOG-LIBW-020]` A job changing the timing of more than 200 tracks or 400
passages names the person who approved that number.** It is the maintainer's
rule of 2026-11-06, and `tools/repair_passage_ends.py` enforces it
(`--allow-tracks`, `[SPEC-SA-112]`). The count is for the whole job, every run
of it, and approval covers the number shown.

**`[LOG-LIBW-030]` A backup's expiry is part of the entry.** A copy a tool
takes before an edit is kept 30 days, and the newest three always, then the
nightly backup removes it `[SPEC-STAR-084]`. A job that is not settled by then
needs a file named `KEEP` in its backup folder, saying why. Write the date it
would go into the entry.

**`[LOG-LIBW-040]` Split, don't trim.** When this file nears 250 lines, move
the oldest entries of §3 into a continuation, *LOG0NN: Library Work, to
DATE*, linked from here. §2 stays here.

## 2. Open

**`[LOG-LIBW-100]` Standing items, as of 2026-11-08.**

| # | item | since | state |
| :--- | :--- | :--- | :--- |
| 1 | Passage ends retimed: 433 passages on 222 tracks `[LOG-LIBW-220]` `[LOG-LIBW-230]` | 2026-11-06 | **Released to the fleet**, lifting the maintainer's hold: the console star sync of 2026-11-08 (jobs 1209–1213) carried all 433 to `bose`, `lempi02w`, `lp3-wifi`, `lrhtpc` and `smartboardpc`, measured on each node 2026-11-11. Only `teacherslounge`, left out of that run and with its player stopped, still has the old ends; its next sync closes this. The three backups go 2026-12-06 to 12-07, as settled work's should. |
| 2 | 18 flagged tracks the repair skipped, 2 of them flagged for this purpose (*Dirty Thirty*, *Open All Night*) | 2026-11-06 | For the maintainer to review by hand; the tool never moves a flagged file. |
| 3 | Back-to-back album timing across the library (`repair_passage_ends.py --album-timing`) | 2026-11-07 | On hold at the maintainer's word: far past the 200-track limit. Six examples in `scratchpad/album_examples.out`. Best done after SPEC075's step 2, if at all. |
| 4 | 27 `inherited:mulib` titles with the top bit of each non-ASCII character cleared (*Fadó* → *Fads*) | 2026-11-06 | Not repaired. 27 is a lower bound: found by eye, not by a scan. |
| 5 | 131 radio passages that disagree with their album twin | 2026-11-06 | `tools/apply_reviews.py` rewrites only the passage reviewed, so a reviewed radio cut leaves its twin as it was. The tool is the gap, not the rows. |
| 6 | 790 of 2,033 album passages from image rips leave the pregap unplayed | 2026-11-07 | By design `[SPEC-RIP-035]`: an eac-cue track ends at the next track's `INDEX 00`. Not a cut-off, but an album played from them does not keep the disc's own timing. A decision, if anyone wants it changed. |
| 7 | The two phones missed the 2026-11-05 hold corrections `[LOG-LIBW-210]` | 2026-11-05 | They were left out of that sync. Also, a file the phone had already catalogued keeps its old length until a bundle replaces it (`21586b6`). |
| 8 | *Ghosts I–IV* whole **on the hub only** `[LOG-LIBW-270]` | 2026-11-11 | No node has the 22 tracks or the alignment until a star sync carries them. The next induct re-fingerprints the 26 tracks; AcoustID has named track 27's and 26's recordings for 21 and 22 before, so a "contradicted" there is AcoustID's, not the tracklist's. Backup goes 2026-12-11. |

## 3. Entries, newest first

**`[LOG-LIBW-280]` 2026-11-12: the hub's pair set to WAL mode.** Both halves
were in rollback-journal mode, as the merge of 2026-09-26 wrote them. That
evening a start after a long build read the library for 15 s, a queue save's
commit was refused, and the listener stayed locked for five hours
`[SPEC-STAR-106]`. At the maintainer's word, with the player stopped and the
console idle: `python tools/pair_journal.py data/library.db --wal`. No row
changed: 7,264 files, 24,201 passages and 41,367 plays as before, `quick_check` ok
on both, and the player came back paused on the passage it had left. A read
transaction held on the listener for 8 s no longer stops the player saving.
Hub only: every other node's pair is its own. Backup
`data/backups/pre-wal-20261112/` (1.5 GB, goes 2026-12-12).

**`[LOG-LIBW-270]` 2026-11-11: *Ghosts I–IV* as one album, in one folder.**
After both folders were inducted, 26 of the 36 tracks were on no release
and named `local:audio:` placeholders. At the maintainer's word, after a dry
run: the 18 disc 2 files moved from `Ghosts III-IV` into `Ghosts I–IV (2008)`
as `2-01.`…`2-18.` `[SPEC-RIP-108]`, its sheet with them as `… (Disc 2).cue`,
and the empty folder removed. The 26 files got release `0822f7c5…`
(`file_releases` source `manual`). Their 52 passages, radio and album, now
name the release's recordings, mapped by disc and position and checked
against title and length. The 1,846 flavor values were moved to the real
ids, the 26 placeholders removed, and their stale `id_checks` cleared. One
`ingest_decisions` row per file, stage `release_alignment`. **Track 18** is
the digital edition's recording, `1d35585f` (323 s; the file is 317.7 s),
not the CD's 314 s `74c113dc`: the only one of 36 that differs between the
editions. The maintainer chose to place it at disc 1 track 18 by a
tracklist row of source `manual`, which MusicBrainz does not have. The
desktop player shows one album of 36, 1-1 to 2-18. Hub only. Backup
`data/backups/pre-ghosts-align-20261111/` (goes 2026-12-11).

**`[LOG-LIBW-260]` 2026-11-11: *Ghosts I–IV* completed on disk from the
Internet Archive.** Both CD rips of 2026-11-08 had stopped partway: disc 1
after track 10, disc 2 after track 4, so the hub held 14 of 36. The missing
22 (11–18, 23–36) are the VBR MP3s of archive.org item
`nine-inch-nails-ghosts-IV`, copied byte for byte at the maintainer's word:
each matched the md5 and size IA lists, and decoded with no error. That item
is a fan upload with no licence of its own, but the album was released under
CC BY-NC-SA 3.0, which allows a non-commercial copy; its two extra tracks,
37 and 38, are not on the release and were not taken. Track 22, fetched only
to compare, measured like the CD's: mean −14.4 dB, peak 0.0 dB. Disc 1's
cue sheet named the eight absent files by the ripper's names; its `FILE`
lines now name `1-11.`…`1-18.`, the original in
`data/backups/pre-ghosts-fill-20261111/` (goes 2026-12-11). **Nothing is
catalogued yet**: `ingest_folder.py`'s dry run would add 8 and 14, with 10
and 4 already present. Every other partial or damaged album in the catalogue
is all rights reserved, so none was fetched.

**`[LOG-LIBW-250]` 2026-11-08: *Broken*, and the backups pruned by rule.**
Nine Inch Nails' *Broken* is in the catalogue: one image file, **9 album
passages and 8 radio**, read from the hub that day. Its run of silent tracks
is one album passage and none for rotation `[SPEC-SIL-020]`. The player change
that plays it that way (`4bca76a`) is on the desktop only, so a speaker plays
*Broken* as an album without that rule until it is deployed. The hub held
138 passages back that day, against 112 on 2026-11-05: rips added since.
From that night, tools' backups older than 30 days are removed
`[SPEC-STAR-084]`. The first run removes the three of 2026-09-27
(`pre-covers-`, two `pre-rekey-`), 3.5 GB, and keeps
`prime-time-original-20260927/`, a song's original file.

**`[LOG-LIBW-230]` 2026-11-07: the rest of the short ends, and their cause
closed.** At the maintainer's explicit word, after seeing the number
(218 tracks / 425 passages for the fades, not album timing):
`repair_passage_ends.py --only fade`, 67 passages on 39 tracks, then
`--only cut --min-tail-ms 1000`, 8 on 4, found by a 1 s tail rehearsal.
Hub only. Backups `data/backups/pre-fade-ends-20261107/` and
`pre-small-tails-20261107/`. The cause was closed at intake the same day by
`21586b6` `[REQ-LIB-145]`: the phone's found-file scan (`player/src/tags.rs`)
sums packets for MPEG and AAC, and `segment_dao.py` decodes instead of asking
`ffprobe`. Over all 6,888 files, 1,657 had been more than 5 s from the decode;
none were afterwards. Mainline intake was already clean: 150 of 1,184 files
added since 2026-09-01 matched a fresh decode within 1 ms.

**`[LOG-LIBW-220]` 2026-11-06: an audit, and passages that stopped before the
music did.** Hub, `bose` and `smartboardpc` read. Of 704 files with a tail of
5 s or more, 194 stopped with loud music still to come, by up to 100 s
`[SPEC-SA-112]`. Their ends came from a bitrate estimate: of the first 218
MP3s, 211 ended within 100 ms of `ffprobe`'s figure, a median 13.8 s short
of the decode. 207 came from the MuLibPlay migration and 11 from the first
ingests of 2026-08-15. `repair_passage_ends.py --only cut`: 358 passages on
179 tracks, hub only, backup `data/backups/pre-passage-ends-20261106/`.
*A correction made that day:* an early report overstated the cut files,
because `volumedetect` runs before an output `-ss` trims, so its tail levels
were the whole song's. Measure a tail with `-af atrim=start=X,volumedetect`
and check `n_samples` against the tail's length.

**`[LOG-LIBW-210]` 2026-11-05: an AccurateRip match outranks read errors.**
The maintainer's rule: a track AccurateRip matched is good, whatever its read
errors `[SPEC-RIP-120]`, and a file is judged only by its own disc's log
`[SPEC-RIP-121]`. Applied: 10 tracks recorded and held (*Batman* 2-9,
*Hot Rocks* 1-12, *Voodoo Lounge* 10), 5 released (*The Last DJ* 10 and 11,
*99.9 F°* 4 and 7, *Tommy* Orchestral 2). The hub then held 112 passages;
`bose`, `lempi02w`, `lp3-wifi`, `lrhtpc`, `smartboardpc` and `teacherslounge`
104 each, **read on each node** from `passages.director_hold`. The 8 between
them are *She Who Dwells* disc 2, arriving with its audio on the next Export.
A release is written `""`, not NULL: a bundle carries only decided values, so
NULL would leave a speaker's old hold in place `[SPEC-HOLD-080]`. Backup
`data/backups/pre-damage-rule-20261105/`.

**`[LOG-LIBW-200]` 2026-11-05: albums filed under a literal `~`.** From
2026-11-03 the console, started without `--root`, filed albums under an
unexpanded `~/Music`. Fixed and the files migrated `[SPEC-NAM-070]`. Backup
`data/backups/pre-tilde-move-20261105/`.

**`[LOG-LIBW-110]` Before this log: what the backups say.** Earlier library
work was recorded in its own commit and specification, not here. The
backups it left, oldest first:

| backup | the work | recorded in |
| :--- | :--- | :--- |
| `pre-rekey-20260927T0415Z`, `pre-rekey-20260927T1257Z` | The identity hash re-keyed to Symphonia | [SPEC045](spec/SPEC045-the-identity-hash.md) |
| `prime-time-original-20260927` | *Prime Time*'s WAV wrapper removed; the original file kept, and kept past the 30 days | `f34102c`, `15e66e4` |
| `pre-covers-20260927` | Cover art brought in (`data/induct-covers-2026-09-27.log`) | — |
| `pre-repair-20261018T2158Z` | `repair_review_copies.py` undid the review copies a star sync had made on the hub | `[SPEC-STAR-051]` |
| `before-highway61-merge-2026-10-30` | *Highway 61 Revisited*, ripped again and merged | [SPEC069](spec/SPEC069-a-disc-ripped-again.md) |
| `before-wywh-swap-2026-11-01` | *Wish You Were Here* swapped for a new rip | `[SPEC-RTF-015]` |

The last two were read back by `tools/backfill_retired.py` to record which
files those merges retired. The hub held the 11 `retire` decisions it wrote,
dated 2026-10-30 and 2026-11-01, on 2026-11-08, so neither backup is needed
for that any more.

---

**Traceability:** an Experiment Record per [GOV001](GOV001-document-hygiene.md);
nothing here is a requirement. Each entry traces to the tag of the tool or
rule it applied.
