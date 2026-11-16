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
| 1 | Passage ends retimed: 433 passages on 222 tracks `[LOG-LIBW-220]` `[LOG-LIBW-230]` | 2026-11-06 | **Released to the fleet**, lifting the maintainer's hold: the console star sync of 2026-11-08 (jobs 1209–1213) carried all 433 to `bose`, `lempi02w`, `lp3-wifi`, `lrhtpc` and `smartboardpc`, measured on each node 2026-11-11. `teacherslounge`, left out of that run, received them by round `20261115T1339Z` on 2026-11-15, so every node has the new ends. The three backups go 2026-12-06 to 12-07, as settled work's should. |
| 2 | 18 flagged tracks the repair skipped, 2 of them flagged for this purpose (*Dirty Thirty*, *Open All Night*) | 2026-11-06 | For the maintainer to review by hand; the tool never moves a flagged file. |
| 3 | Back-to-back album timing across the library (`repair_passage_ends.py --album-timing`) | 2026-11-07 | On hold at the maintainer's word: far past the 200-track limit. Six examples in `scratchpad/album_examples.out`. Best done after SPEC075's step 2, if at all. |
| 4 | 27 `inherited:mulib` titles with the top bit of each non-ASCII character cleared (*Fadó* → *Fads*) | 2026-11-06 | Not repaired. 27 is a lower bound: found by eye, not by a scan. |
| 5 | 131 radio passages that disagree with their album twin | 2026-11-06 | `tools/apply_reviews.py` rewrites only the passage reviewed, so a reviewed radio cut leaves its twin as it was. The tool is the gap, not the rows. |
| 6 | 790 of 2,033 album passages from image rips leave the pregap unplayed | 2026-11-07 | By design `[SPEC-RIP-035]`: an eac-cue track ends at the next track's `INDEX 00`. Not a cut-off, but an album played from them does not keep the disc's own timing. A decision, if anyone wants it changed. |
| 7 | The two phones missed the 2026-11-05 hold corrections `[LOG-LIBW-210]` | 2026-11-05 | They were left out of that sync. Also, a file the phone had already catalogued keeps its old length until a bundle replaces it (`21586b6`). |
| 8 | *Ghosts I–IV* whole **on the hub only** `[LOG-LIBW-270]` | 2026-11-11 | No node has the 22 tracks or the alignment until a star sync carries them. The next induct re-fingerprints the 26 tracks; AcoustID has named track 27's and 26's recordings for 21 and 22 before, so a "contradicted" there is AcoustID's, not the tracklist's. Backup goes 2026-12-11. |
| 9 | The 27 copied reviews: **gone from the hub and every node, closed 2026-11-15** `[LOG-LIBW-290]` | 2026-11-14 | Round `20261114T2350Z` removed them from bose, lempi02w, lp3-wifi, lrhtpc and smartboardpc. Round `20261115T1339Z` removed them from `teacherslounge` by the missed-run rule `[IMPL-UID-042]`, the flag cleared on 2026-11-08 with them. Read on each node's own disk: 179 reviews, none sharing a `decided_at`, `quick_check` ok. To be removed from this table at the next tidy. |
| 10 | Catalogue patches blocked from 2026-11-08 to 2026-11-15: **every node caught up, closed 2026-11-15** `[LOG-LIBW-300]` | 2026-11-11 | Round `20261115T0104Z` committed the withheld catalogue, *Ghosts I–IV* (item 8) among it, to bose, lempi02w, lrhtpc and smartboardpc: about 22,630 rows each, `kept` 0. Round `20261115T0509Z` committed it to `lp3-wifi` (item 11), and `20261115T1339Z` to `teacherslounge`: 83,525 rows, its retimes of 2026-11-06 among them (item 1). Read on each node's own disk: every passage credited, `quick_check` ok; each speaker holds 24,171 passages and 12,000 `id_checks`, the hub's count. |
| 11 | `lp3-wifi` lacked the credits of 742 files (4,052 passages) and 521 `id_checks`; **repaired 2026-11-15, its cause not found** | 2026-11-15 | Found when its strict rehearsal refused round `20261115T0104Z`; nothing was written there. `bose`, sent the same rounds, lacked none. 680 of the 742 files reached it by bundle sends of 2026-10-14 to 10-29, 60–100% of each; none sent from 2026-11-01 on was affected. The hub's copy last sent held their credits, so no patch had named them. Its library is on its durable ext4 partition, not the overlay root. The runs of that window are pruned, and the node keeps no catalogue backup after September. **Repair, at the maintainer's word:** its catalogue adopted as its baseline (`adopt lp3-wifi --force --tmp /srv/library`, `data/sync/adopted/lp3-wifi-20261115T0423Z/`) `[SPEC-STAR-946]`. Round `20261115T0509Z` then rehearsed CLEAN and committed 27,483 catalogue rows, `kept` 0, the missing credits and checks among them. Its 291 deletions were damage: three credits on *Scarecrow* naming *The One U Wanna C*, *Future Baby Mama* and *Somewhere Here on Earth*, left by `[SPEC-STAR-051]`'s applied copies and repaired on the hub on 2026-10-18 but never here, and four retired placeholder recordings with their 284 flavor values. Read on its disk afterwards: 24,171 passages, every one credited, 12,000 `id_checks`, the same as every other node; `quick_check` ok. |
| 12 | `teacherslounge`'s passage uids: **carried, closed 2026-11-15** `[LOG-LIBW-310]` | 2026-11-15 | It slept before two commits that day; its journal shows a suspend ending at 09:16 local. Round `20261115T2013Z` ran with an *idle* hold placed for 40 min (`systemd-inhibit --what=idle`; a *sleep* hold is refused over ssh there), rehearsed CLEAN and committed 22,765 passages' uids, `kept` 0. Read on its disk: every passage holds the hub's uid; `quick_check` ok. A future round there wants the same hold. |

## 3. Entries, newest first

**`[LOG-LIBW-320]` 2026-11-16: the unique index on passage uids, on every
node.** Stage A gave every node its passages' uids, and none the unique index
the hub holds on them `[IMPL-UID-056]`.
- **How:** first a build that reports its catalogue's indexes and applies a
  patch's `add_indexes`, `89c765b`, deployed on every node (FLEET001). Then
  round `20261116T0119Z`, in which each node's catalogue patch was that one
  index and no row. It rehearsed CLEAN and committed on all six, `kept` 0.
  Its 35 items, the phones' stale preferences again, took their defaults by
  `--approve-all`, as the maintainer chose for the same items in round
  `20261114T2350Z`.
- **Read on each node's own disk afterwards:** `passages_uid` is present on
  bose, lempi02w, lp3-wifi, lrhtpc, smartboardpc and teacherslounge. Every
  passage has a uid and none shares one: 24,171 on each, 22,765 on
  `teacherslounge`. `bose`'s catalogue mount is read-only again.
- **No catalogue row changed.** The listener patches carried 21 rows to each
  node (10 preferences, 11 saved lyrics) and 2 to `teacherslounge`.
- **Undone by** each node's kept inverse, which drops the index. The hub was
  not touched; it has had the index since `[LOG-LIBW-310]`.
- **A gap it showed:** the player counts an index it makes as no row, so the
  rehearsal and the commit read `catalogue applied 0` whether or not one was
  made. Only the disk said it landed.

**`[LOG-LIBW-310]` 2026-11-15: every passage given its permanent uid, on the
hub and five nodes.** Stage A's window `[IMPL-UID-052]`, at the maintainer's
word. It ran once the fleet was caught up (§2 item 10) and every player
reported `passage_uids`.
- **Rehearsed first** on a scratch copy of the hub's catalogue. 24,443
  passages got 24,443 distinct uids in 8 s, and `lempi02w`'s patch came to
  10.5 MB, nothing but the uid columns and the empty lineage table.
- **On the hub:** `python tools/assign_passage_uids.py data/library.db
  --commit --expect 24443`. That added `passage_uid` and the birth key to
  every passage, the unique index `passages_uid`, `passage_lineage`, and
  the two triggers that refuse a passage written without a uid or a uid
  changed `[IMPL-UID-036]`. Every uid recomputed afterwards from its span
  agrees; `quick_check` ok.
- **To the nodes:** round `20261115T1348Z` carried them by the strict natural
  patch. Each node rehearsed CLEAN and committed its own passages' uids,
  `kept` 0.
- **Read on each node's own disk afterwards:** bose, lempi02w, lp3-wifi,
  lrhtpc and smartboardpc each hold 24,171 passages, every one with the
  hub's uid for it, none another and none without; `quick_check` ok.
- **Where:** the hub and those five. `teacherslounge` rehearsed CLEAN but had
  gone to sleep by the commit, and took its uids that evening in a round of
  its own (§2 item 12).
- **No row changed anything else:** no timing, no credit, no number.
- **Backup:** `data/backups/pre-passage-uids-20261115T134759Z/` (the catalogue
  as it was, without the columns), for the hub. Each node keeps the inverse
  of its commit. It goes 2026-12-15.

**`[LOG-LIBW-300]` 2026-11-15: five dangling `id_checks` rows dropped from the
hub's catalogue.** At the maintainer's word, after a rehearsal on the live
catalogue: `python tools/drop_dangling_checks.py data/library.db --commit
--expect 5`.
- **What went:** fingerprint checks of 2026-10-15 on passages 6454–6462. These
  are the *Wish You Were Here* cuts the swap of 2026-11-01 removed: *Have a
  Cigar*, *Shine On You Crazy Diamond* I–V and VI–IX, *Welcome to the
  Machine* and *Wish You Were Here*.
- **Why:** a check of a passage that no longer exists says nothing, and these
  had withheld every node's catalogue patch since 2026-11-08 (§2 item 10).
- **Measured on the hub's disk afterwards:** 12,000 `id_checks` where there
  were 12,005; passages and files unchanged at 24,443 and 7,305; no catalogue
  row names a missing passage; `quick_check` ok.
- **Where:** the hub only. The next round carries the catalogue to the nodes.
- **Backup:** `data/backups/pre-dangling-checks-20261115T002143Z/` (1.5 GB),
  with the rows in `dropped.json`. `--restore` puts them back. It goes
  2026-12-15.

**`[LOG-LIBW-290]` 2026-11-14: the copied reviews and the orphan reviews
dropped from the hub.** At the maintainer's word (the decisions of
`[IMPL-UID-902]` and `[IMPL-UID-906]`), after a rehearsal on a scratch copy of
the pair and one on the live listener. The command was `python
tools/drop_review_copies.py data/listener.db --commit --expect-copies 27
--expect-orphans 4` `[IMPL-UID-044]`.
- **What went:** the 27 `id_reviews` rows left by `[SPEC-STAR-051]`'s copying.
  9 were pending, which `apply_reviews.py --commit` would have used to rename
  nine songs. The other 18 had been applied or deferred on unrelated passages.
  The 4 orphans went too: genuine decisions of 2026-11-01 on passages 6455–6458,
  which no longer exist.
- **No catalogue row changed.**
- **Measured on the hub's disk afterwards:** 179 reviews where there were 210;
  none shares a time and none names a missing passage; the 7 originals are
  kept; nothing is pending for `apply_reviews`; `quick_check` ok.
- **Where:** the hub only. Every node still holds the 27 copies, under its own
  numbers, until the next star-sync round removes them as removals the hub's
  history evidences `[IMPL-UID-042]` (§2).
- **Backup:** `data/backups/pre-review-copies-20261114T234817Z/`, with every
  dropped row in `dropped.json`. `--restore` puts them back. It goes 2026-12-14.

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

Entries of 2026-11-08 and before, and what the backups say of the work
before this log, are in [LOG015](LOG015-library-work-to-2026-11-08.md).

---

**Traceability:** an Experiment Record per [GOV001](GOV001-document-hygiene.md);
nothing here is a requirement. Each entry traces to the tag of the tool or
rule it applied.
