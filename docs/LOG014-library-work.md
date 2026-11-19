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
| 13 | *Christmas Island*'s 20 releases **on the hub only** `[LOG-LIBW-340]` | 2026-11-19 | A node that took its holds from the rounds since its import keeps them until the next round carries the release. To be read on each node's own disk then. |

## 3. Entries, newest first

**`[LOG-LIBW-340]` 2026-11-19: *Christmas Island*'s 20 passages released,
and its damage records taken back.** Jimmy Buffett's *Christmas Island*
(1996), added 2026-11-18, had all ten tracks held, radio and album, as
`damaged rip: not present in database`. That is AccurateRip having no entry
for the pressing, in words the log reader did not know `[SPEC-HOLD-035]`. Its
EAC log reads secure mode, `Copy OK`, `No errors occurred`. At the
maintainer's word.
- **How:** the reader fixed first, then `python tools/backfill_rip_damage.py
  data/library.db --commit`. Its dry run named those ten tracks and nothing
  else in the library, to record or to take back.
- **What changed:** the ten `verification_failed` rows removed, and the 20
  holds set to `''`, released, so `--damaged` will not hold them again and a
  send carries the release `[SPEC-HOLD-080]`. The hidden track after track 10
  was never held and is untouched. No timing changed.
- **Read on the hub's disk afterwards:** file 7369's ten radio and ten album
  passages `''`, its hidden pair NULL, no damage row for it; 25,499 passages,
  as in the backup; `quick_check` ok. The desktop player reloaded (Director
  rebuild 1.6 s).
- **Where:** the hub only. Rounds `20261118T2018Z` and `20261119T1855Z` ran
  after the import, so the nodes likely hold the 20 holds; not read on any
  node. The next round carries the release (§2 item 13).
- **Backup:** `data/backups/pre-unseen-release-20261119T212705Z/` (1.6 GB, the
  catalogue). It goes 2026-12-19.

**`[LOG-LIBW-330]` 2026-11-16: every listener names its passages by uid,
the phones excepted.** Stage B's window `[IMPL-UID-068]`, at the maintainer's
word to proceed with Stage B and leave the phones for later.
- **How:** each listener recorded just before its own deploy
  (`tools/listener_window.py record`), then the build `17e9b1e`, whose player
  converts its listener when it first opens it, after copying the whole file
  to `listener.pre-uid.db` beside it. In order: the desktop at 13:08 UTC,
  `lempi02w` (Director rebuild 56.6 s, 55 MB at 129 s, no error), `bose`,
  `lp3-wifi`, `smartboardpc`, `teacherslounge` (converted when the round
  started its player), and `lrhtpc` once its sudo was granted again.
- **On the hub, the saved words:** `set_lyrics_uids.py --commit --expect 143`
  placed all 143 rows, each on one current passage. Round `20261116T1340Z`
  carried those uids to all six nodes, rehearsed CLEAN and committed, `kept` 0.
  It also carried one settings row that differed only in its stamp.
- **Read on each machine's own disk afterwards** (`verify --lyrics-uids`):
  every table by uid; no plays, skips or decisions fewer than recorded; reviews,
  resume rows and flags exactly as before; reviews and flags the same as the
  hub's; the lyrics rows, their digest and the row each passage shows
  unchanged, every placed row with its uid. 24 checks on the hub and 28 on
  each node, all the same.
- **Kept without a uid:** plays naming a passage their own catalogue no longer
  holds, each with its recording: `lempi02w` 47, `bose` 42, `lp3-wifi` 33,
  `teacherslounge` 24, `smartboardpc` 14, the hub and `lrhtpc` none, with a
  few skips and decisions like them. No review, and no queue entry, was left
  without one.
- **The players** came back as they were. The paused ones were on the same
  passage and position, and `bose` and `smartboardpc` were playing.
- **Undone by** each machine's `listener.pre-uid.db`, the hub's also by
  `data/backups/pre-lyrics-uids-20261116T130929Z/` and
  `pre-sync-20261116T1340Z/`. They go 2026-12-16.

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

Entries of 2026-11-11 and 2026-11-12 are in
[LOG016](LOG016-library-work-to-2026-11-12.md). Those of 2026-11-08 and
before, and what the backups say of the work before this log, are in
[LOG015](LOG015-library-work-to-2026-11-08.md).

---

**Traceability:** an Experiment Record per [GOV001](GOV001-document-hygiene.md);
nothing here is a requirement. Each entry traces to the tag of the tool or
rule it applied.
