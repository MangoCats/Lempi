# IMPL023: Building Permanent Identity

**Implementation Specification — Tier 3 · written 2026-11-14 · Stage 0 done 2026-11-15 on the hub and five nodes, `teacherslounge` to follow · Stage A's window run 2026-11-15: every passage on the hub and all six nodes has its uid, each node read row by row (LOG014 `[LOG-LIBW-310]`); step 6, references by uid, built 2026-11-15 and not yet run in a round · Stage B's window run 2026-11-16: the hub's listener and all six nodes' by uid, each verified from its own disk, the phones left for later (LOG014 `[LOG-LIBW-330]`) · the build plan for [SPEC075](spec/SPEC075-permanent-identity-and-a-movable-hub.md) §3 and §8 steps 1–3, and for every other place a number one database assigned is read by another**

Every node numbers the household's music in its own order, and the star sync, echo and the tools translate between those numbers wherever they meet. This plan removes the meeting rather than the translation. Outside its own database a passage is named by its `passage_uid` and a file by its `audio_md5`, and a number a database assigned is never read by another machine, nor by the same machine against another catalogue.

> **Related:** [SPEC075](spec/SPEC075-permanent-identity-and-a-movable-hub.md) (the design) · [SPEC058](spec/SPEC058-catalogue-patch-by-natural-key.md) (today's patch, which Stage A changes) · [SPEC046](spec/SPEC046-star-sync.md) `[SPEC-STAR-049]`..`[SPEC-STAR-052]` (today's translation, which Stage B removes) · [SPEC006](spec/SPEC006-data-flow-and-portability.md) `[SPEC-DF-035]` · [GUIDE039](GUIDE039-running-the-star-sync.md) · [LOG014](LOG014-library-work.md) (each window's backup)

---

## 1. Scope

**`[IMPL-UID-010]` The fleet stops for each stage** (the maintainer, 2026-11-14). SPEC075 §8 planned a period with old and new formats side by side, each node reporting a capability. That period is not needed. Each stage below is one window: every node's player is stopped, migrated, verified from its own disk and started again, playing, and no code reads both formats. Phones are the exception, and `[IMPL-UID-066]` says why that is safe.

**`[IMPL-UID-012]` In scope:** SPEC075 §3 (the uid, its birth key, lineage and collisions) and every path in §2 below. **Not in scope:** holdings apart from the catalogue (SPEC075 §4), replication by numbered change (§5) and a movable hub (§6). Each needs uids and is planned when these stages are done; none is needed to stop numbers crossing.

**`[IMPL-UID-014]` Done means:** the check of `[IMPL-UID-082]` passes with an empty allow-list; `tools/test_numbering_fleet.py` covers every path in §2; and a star-sync round has no translation to report, because none exists.

## 2. Where a number crosses today

**`[IMPL-UID-020]` Read from the code at `86e9d3b`, 2026-11-14.** The last column names the stage that removes it.

| path | what crosses | stage |
| :--- | :--- | :---: |
| listener merge of `listener_flags`, `id_reviews`, `boundary_reviews` | node passage numbers, translated by position (`passage_remap`, `passage_unmap`) | B |
| removal evidence (`star_merge.removals`) | raw node numbers, compared with hub-numbered keys | B |
| a node passage the hub cannot match (`gone`) | its raw number, read as the hub's passage of that number | B |
| a node's own plays, rejections, `selection_decisions`, `player_state`, `player_queue` | numbers into a catalogue in another file: wrong after a rebuild, an adoption or a restore elsewhere | B |
| `passage_lyrics` | `(audio_md5, kind, start_ms)`, which a boundary edit changes | B |
| phone member patch | nothing: passage rows withheld both ways | B |
| catalogue patch, `passage` references | the span, paired by position after an edit (`same_numbering`, `pair_moved`) | A |
| catalogue summary, the `rows` step, `catalogue_state`, `catalogue_integrity` offers | a node's own `file_id` and `passage_id`, renamed or compared by the hub | A |
| `flavor` where `subject_kind = 'passage'` | a passage number, as text | A |
| bundle payload | the span alone; the receiver numbers the passage | A |
| echo schedule and queue | a number, with `(audio_md5, kind, start_ms)` beside it | C |
| remote flag and status (`lempi_control.py`, `console.py`) | a span anchor, then the remote's own number | C |
| 17 player routes on `:passage_id`, and `/history/flag/passage/:id` | a number in a URL, which on another node opens another song | C |
| `echo_skew.py`, star-sync reports, items and the Sync page | numbers shown to people | C |
| the SPEC006 change and flag tools (`export_changes.py`, `apply_changes.py`, `remote_snapshot.py`, `export_flags.py`, `import_flags.py`) | spans | C `[IMPL-UID-900]` |

**`[IMPL-UID-022]` What stays a local number, used inside one database only:** `passage_id` and `file_id` as join keys; the engine's and the Director's structures in memory; the hub console's own routes; `play_id`, `rejection_id`, `program_id`, `like_id` and both `decision_id`s. Two of these are constraints for later. `listener_likes` merges by `(mbid, recorded_at)` but writes `like_id` across nodes; no copy holds a row (2026-11-14), and Stage B leaves the column out of merged rows. `ingest_decisions` is patched by the hub's `decision_id`. That is safe while only the hub writes it, and a collision the day a node ingests `[SPEC-UID-230]`, so it leaves the replicated catalogue `[SPEC-UID-115]` or takes a natural key before any node writes it.

## 3. The identity, defined once

**`[IMPL-UID-030]` A `passage_uid` is 32 lowercase hex characters:** the first 128 bits of SHA-256 over the UTF-8 bytes of `lempi-passage-uid/1`, `audio_md5`, `kind`, `start_ms`, `end_ms` and `salt`, joined by `\x1f`, integers in decimal `[SPEC-UID-030]`. It is text rather than a blob because it goes into JSON, URLs and JavaScript unchanged.

**`[IMPL-UID-032]` One function in each language, held equal by a fixture:** `passage_uid` in `lempi-core`'s `db` module and in `tools/passage_uid.py`, both tested against `fixtures/passage_uid/vectors.json` (an ordinary cut, a salted one, an album cut at 0, a span past 2³¹ ms). This is how `fixtures/natural_patch/` already holds the two patch implementations together `[SPEC-NKP-090]`.

**`[IMPL-UID-034]` Columns.** `passages` gains `passage_uid` and its birth key `birth_md5`, `birth_kind`, `birth_start_ms`, `birth_end_ms` and `birth_salt`, with a unique index `passages_uid`. A new table `passage_lineage(old_uid, new_uid, why, stamp)` records re-cuts `[SPEC-UID-065]`. The player adds all of it on open, beside the other `ensure_*` functions in `player/core/src/db/mod.rs`, and `sql/schema.sql` declares it.

**`[IMPL-UID-036]` SQLite enforces it, so no writer can forget.** One trigger refuses a passage inserted without a uid, and another refuses changing a uid that is set. A trigger needs no custom SQL function, so it holds in the `sqlite3` shell and in every tool not yet written. A re-salt `[SPEC-UID-052]` is the one tool that sets the second trigger aside, inside its own transaction, and it writes lineage.

**`[IMPL-UID-038]` One insert per language.** In `tools/`, `passage_uid.insert_passage` makes every passage: `segment_dao.py`, `ingest_cd.py` (five statements), `ingest_folder.py`, `backfill_album_cuts.py` and `migrate_mulib.py` call it. Before uids exist it writes exactly the row each statement wrote. In the player, `bundle.rs` and `found.rs` gain the same with Stage A's player build. The generic inserts in `mesh_sync::write_natural`, `star_sync.baseline_from_rows` and `catalogue_integrity._insert` copy a row that already has its uid, and carry the column as data. `test_passage_uid.py` fails on a passage written by hand anywhere else. Its allow-list names each file still doing so and why, and can only shrink.

**`[IMPL-UID-039]` Collisions** `[SPEC-UID-050]`. Within one database a birth key is unique, as `passages_span` is. A uid that arrives from elsewhere (a bundle, a patch, later a proposal) is checked against its birth key, and a mismatch is refused by name. The second audio digest `[SPEC-UID-061]` is not needed to remove numbers and waits for SPEC075 §4.

## 4. Stage 0: clear the damage first

**`[IMPL-UID-040]` The copied and orphaned reviews go before anything is migrated,** because the migration would give them permanent names. Measured 2026-11-14:
- 27 `id_reviews` rows on the hub and on every node have no `origin`, and share a `decided_at` with an original stamped `GMKtec`. They are what remains of `[SPEC-STAR-051]`'s copies.
- 9 of them are pending: `apply_reviews.py --commit` would rename nine songs.
- 4 further rows are genuine decisions on passages 6455–6458, which no longer exist. They are dropped too `[IMPL-UID-902]`.

A delete on the hub alone is undone by the next merge, because `star_merge.TABLES` merges `id_reviews` with no removals. That is the likely way the copies outlived the 2026-10-18 repair.

**`[IMPL-UID-042]` First, a withdrawn review stays withdrawn** `[IMPL-UID-906]`. `id_reviews` and `boundary_reviews` take removal evidence, as `listener_flags` does `[SPEC-STAR-050]`. Two defects in that evidence are fixed at the same time, because either would turn a withdrawal into the loss of another review:
- a node's history is read in the hub's numbering, as its current rows already are;
- a node that missed the run that removed a row does not bring it back. It still holds the row unchanged from the copy it was last sent, but the copy the hub was last sent no longer holds it.

**`[IMPL-UID-044]` Then the copies and orphans go, by the sync.** They are deleted on the hub after a rehearsal and a backup through SQLite's backup API, never by copying the pair's files, with a LOG014 entry. The next round removes them from every node, as removals the hub's own history evidences, and each node's rehearsal shows them before anything is written. A node missing from that round loses them at its next round, by the second rule of `[IMPL-UID-042]`. Verified from each node's disk `[GDE-DEP-070]`.

## 5. Stage A: the catalogue names passages by uid

**`[IMPL-UID-050]` The code:**
- Everything in §3, `[IMPL-UID-030]`..`[IMPL-UID-039]`.
- The bundle payload carries each passage's uid and birth key (`tools/payload.py`), and `bundle.rs` takes them.
- The snapshot reports `passage_uids: 1`, and the summary and `catalogue_rows` name passages by uid and files by `audio_md5` alone.
- The natural patch's `passage` reference resolves by uid (`mesh_sync::to_local`), and the builder pairs passage rows by uid. A boundary edit is then a plain update, and `same_numbering`, `pair_moved` and the positional pairing of `[SPEC-NKP-046]` have nothing left to do.
- The `rows` step, `catalogue_state` and `catalogue_integrity` compare by uid.
- `flavor` names a passage subject by uid, and `star_merge.hold_out` and the `rows` scope find it by one. Today they miss such rows, and one would withhold a node's whole catalogue patch.

**`[IMPL-UID-052]` The hub alone assigns, and today's patch carries the uids to the nodes** (SPEC075 step 2). No player computes a uid for a passage that already exists. A node then cannot hold a different uid for one passage than the hub does, even one whose boundary edit has not yet reached it, and deploying a build never migrates a catalogue. The window rests on one precondition: every node in step, shown by a round with the old build finding nothing to send ([GUIDE039](GUIDE039-running-the-star-sync.md)). Then:

1. Deploy players that read a uid, and keep resolving by span. They change no data. `build/deploy-appliance.sh` writes both layers on `bose` and `lp3-wifi` `[GDE-DEP-060]`.
2. Stop the sync and back up the hub pair.
3. Assign uids in the hub's catalogue, with the columns, the index, the triggers and `passage_lineage`. This is one tool, which rehearses by default.
4. Run an ordinary round. The strict natural patch adds the columns on every node and sets each passage's uid, rehearsed first, with an inverse kept for each node `[SPEC-NKP-040]`. *Measured offline 2026-11-15 on `bose`'s real baseline and target:*
   - 24,171 passages get 24,171 distinct uids;
   - the uids add 10.5 MB to the patch, through the ordinary `add_columns`;
   - the natural entries come to 13.7 MB, 43% of the 32 MB first part they must fit in `[SPEC-NKP-930]`.
5. Verify from each node's disk, not its answer: every passage the node holds has the hub's uid. A single disagreement stops the window.
6. From the next round, passage references in the patch resolve by uid. *Built 2026-11-15* (`star_patch.NATURAL_BY_UID`), for a node that reports `passage_uids` and a pair whose every passage carries its uid; otherwise by span, and the summary and the run's output say why ([GOV002](GOV002-sources-of-truth.md)). By uid, a passage that changes file is one change, so its old file's removal now waits until after the writes: before, the cascade took the passage first, on the fixture both languages apply, `fixtures/natural_patch/patch.json` since Stage D.

**`[IMPL-UID-054]` Undone by** each node's kept inverse (`mesh_sync::undo`), which drops the added columns, and on the hub by step 2's backup.

**`[IMPL-UID-056]` The unique index reaches every node.** Found 2026-11-15 on `lempi02w`'s disk: every passage holds its uid, but the node has neither the unique index `passages_uid` nor the hub's two triggers. A patch made a table's indexes only with the table, so step 4 could carry none. Without the index nothing on a node refuses two passages one uid, and Stage B's joins by uid would scan every passage.
- A patch entry may now carry `add_indexes`: each one a `CREATE [UNIQUE] INDEX` on the entry's own table, made where no index has that name, and dropped by the undo.
- The node reports its catalogue's indexes (`catalogue_indexes`), read from its own file. The copy last sent holds every index the hub had, so it cannot say which ones never arrived. The report is also the capability: a node that gives none is sent none.
- The triggers stay on the hub. A node's bundle import writes a passage and then stamps its uid, which `passages_uid_required` would refuse.

Offline against `lempi02w`'s copy last sent and its real list, the patch adds that one index and nothing else of schema. *Done 2026-11-16:* `89c765b` deployed on every node, then round `20261116T0119Z`. Each node's own disk holds `passages_uid` (LOG014 `[LOG-LIBW-320]`). A made index counts as no row, so a rehearsal reads `applied 0` either way, and until that changes the disk is the evidence.

## 6. Stage B: the listener names passages by uid

**`[IMPL-UID-060]` Every listener table that names a passage holds its uid instead of its number:**
- `listener_flags`, whose `subject_id` is the uid where `subject_kind = 'passage'`;
- `id_reviews` and `boundary_reviews`, keyed by `passage_uid`;
- `passage_lyrics`, which finds a passage's words by `passage_uid`, so a boundary edit no longer moves them, and keeps every row it holds `[IMPL-UID-063]`;
- the node's own `listener_play_history`, `listener_rejections`, `selection_decisions`, `player_state` and `player_queue`.

The number column goes, because kept beside the uid it would be a second source for one fact ([GOV002](GOV002-sources-of-truth.md)). Its readers, among the 56 files in the player and the tools that name these tables, join `passages` on `passage_uid`. The engine and the Director still hold the integer in memory, found by that join when they load.

**`[IMPL-UID-061]` A node passage without a uid costs its node two things, until a resend gives it one.** A hub cannot hold one, its triggers refuse it `[IMPL-UID-036]`, and `payload.py` sends every uid. A bundle made from a catalogue before Stage A can still bring one: `bundle.rs` lands it as `Birth::Unknown`. Found by review 2026-11-17, and traced:
- **No rotation by passage.** A play of it is written with a NULL uid, and the Director reads a passage's own history only by uid, and its file's artist tag through that history. It is held back by its recording's history alone; an unidentified one, with no recording, has nothing holding it back and can come round again at once.
- **No catalogue patch for the node.** Its catalogue reads `no-uids`, so star sync judges it out of step and sends it none, until that passage has a uid. The round's summary says *catalogue: none sent -- the node's catalogue has a passage without its uid*. A patch could not be made even from the node's own rows (`NaturalUnsupported`), so nothing in the sync fills the uid.
- **Said at import, and by the round.** The bundle report names each such file (`passages_without_uid`, `WITHOUT UID` in `import_bundle`), and the player's log says the same once per import. The round names the node's files without uids, and the repair.
- **The repair: send a current bundle of each file again** (`export_bundle.py --md5` for each, then `send_bundle.py`). The node holds the file, so the import gives each passage there without a uid the hub's uid and birth key, found by its span `[SPEC-PL-110]`. A span the hub has moved since both cuts is left, and the file stays named. The next round finds the node in step. Built 2026-11-17, at the maintainer's choice over a fill inside the sync, which would change its protocol on every node. Until then a resend reached a held file's holds, credits and release, never its passages. `bundle.rs`'s and `test_star_rows.py`'s tests hold each step.

**`[IMPL-UID-062]` Each listener converts against its own catalogue:** a number becomes a uid by an exact join inside one database, which is not a translation between nodes. A row whose number names no passage there keeps a NULL uid and is counted, never guessed. After Stage 0 the hub expects none in its shared tables. `passage_lyrics` holds no numbers and is not converted this way `[IMPL-UID-063]`.

**`[IMPL-UID-063]` No saved words are lost.** `passage_lyrics` is a history, not a table of numbers:
- no row is ever deleted `[SPEC-LYR-155]`;
- every node holds every row, whether or not it holds the file `[SPEC-LYR-200]`;
- a row whose segment is no passage's now still speaks for its recording `[SPEC-LYR-160]`.

Measured 2026-11-14: the hub holds 111 rows, each the segment of one current passage, and each node holds only 108 of those passages. So:
- **Each row keeps its key and every value it holds.** It gains a `passage_uid` column, and nothing else about it changes: no row is merged, replaced or dropped. The key stays `(audio_md5, kind, start_ms)`, which names a save, not a passage number. A new key would collapse the history, and a patch cannot carry one.
- **The hub alone sets the uid,** once. It sets it for each row whose segment is a current passage's, by the same rule the player reads with `[SPEC-LYR-150]`. The merge carries the column to every node's copy, every row included. No node converts its own copy, since none holds every passage.
- **A row the hub cannot place keeps a NULL uid.** It is listed by name, never guessed and never dropped, and goes on speaking for its recording as it does today.
- **Words are found by uid.** A passage's words are the newest row with its uid. Only rows without a uid are matched by segment, as today. A new save records the uid of the passage it is saved for.

**`[IMPL-UID-064]` The merge loses its translation.** `star_merge` keys the shared tables by uid. `passage_remap`, `passage_unmap`, `to_node_numbering` and `Node.remap` go, and with them both defects the 2026-11-14 review found: removal evidence read in the wrong numbering, and an unmatched passage read as the hub's. A node's history, the merge and the node's own rows now hold the same value. Merged `listener_likes` rows leave out `like_id`. A node's own tables go back exactly as it holds them, as today.

**`[IMPL-UID-065]` A uid the hub never set does not replace one a node's save recorded.** `passage_lyrics`' `passage_uid` is the hub's to set (`hub_sets`), but a hub row holding NULL is one it could not place, not one it placed nowhere. A node's newer save under that key wins by its stamp and keeps its own uid; a uid the hub did set is still the merge's. Found by review 2026-11-17: the hub's NULL had replaced the node's, so every node was sent that save without the passage it was saved for. `test_star_merge.py` holds both cases.

**`[IMPL-UID-066]` Phones.** A phone app built from this `lempi-core` converts its own listener on open and reports `passage_uids`. Its snapshot stops holding passage rows back, and its member patch carries them. A phone that has not updated sends and is sent no passage rows, which is what every phone does today, so nothing needs translating while it waits `[SPEC-UID-410]`.

**`[IMPL-UID-094]` A phone's catalogue is given its uids in place, on the hub, before the new app first opens it** (the maintainer, 2026-11-25). Found the same day: a phone built before Stage A holds a catalogue with no `passage_uid` column, since star sync never patched it. An app built after Stage B refuses that pair at open, changing nothing (`listener_uid.rs`). Replacing the catalogue was considered and not chosen, because the listener names the old catalogue's numbers. `android/phone-uids.sh pull` stops the app and copies the pair whole; `tools/phone_uids.py` rehearses on the copy. `commit` gives the uids (backed up first), installs the app and puts the catalogue back, then reads from the phone's disk that its listener converted. Each passage of a hub file takes the hub's uid and birth key: by its span now, or by its birth span, as `[SPEC-PL-110]`. Failing both, once every other passage of its file and kind is paired, the one left on each side is one passage if their spans overlap, so a boundary edit the phone never received does not stop it. A passage of a file the hub lacks is born here. So is one the phone's own scan cut (`found:`) where the hub cut those bytes otherwise. Anything else is listed and refuses the commit. *Measured 2026-11-25 against the hub's own catalogues of 30 Oct, 6 Nov and 15 Nov, whose numbers are the hub's and so are the answer:* 68,429 passages. 68,407 were given the hub's uid for their own number, 866 of them by the third rule, the 6 Nov retiming. The other 22 were born here: files the hub has replaced since 30 Oct. None was wrong, and none was left. *The Moto G's own pair, pulled the same day:* 16,564 passages. 15,964 matched by span now. 530 were the one left, their ends the phone's from before the 6 Nov fix, up to 104 s short. One was the single passage of a 27-track file that the hub had moved by hand. 69 were files the hub lacks, and one was a whole image rip the hub holds as 8 tracks. 96 listener rows name a passage the phone's catalogue no longer holds; they convert without a uid whatever is done here, as on the nodes `[IMPL-UID-069]`. Phones still to do: the Moto G (`16e4a47`) first, as rehearsal, then the Pixel 7a (`e76ff5e`).

**`[IMPL-UID-067]` Two opens convert a listener once.** Whether there is anything to convert is asked again after `BEGIN IMMEDIATE`, with no other open able to be converting. Two opens can meet on a listener still by number: two processes, or a route opening a store while the engine's first open converts. Both found it by number before either held the lock, and the one that waited rebuilt tables already converted and failed its open (`no such column: o.passage_id`). The copy of the whole file is kept from whichever open made it first. Found by review 2026-11-17, after every node had converted, so it waits for a phone's update or a restored listener; `listener_uid.rs`'s race test fails without it.

**`[IMPL-UID-068]` The window:**
1. Stop every player.
2. Back up every listener. On the hub and every node, record what `passage_lyrics` holds: its row count, a digest over its rows, and the words each passage shows.
3. Convert the hub's listener, every node's, and every copy last sent, since the merge reads those as baselines `[SPEC-STAR-110]`. `passage_lyrics` is set on the hub alone and carried by the merge `[IMPL-UID-063]`.
4. Verify that each node's shared rows equal the hub's by uid, and that its own tables' row counts are unchanged.
5. Verify `passage_lyrics` on the hub and on every node, against what step 2 recorded:
   - the same number of rows;
   - the same digest over every column but `passage_uid`;
   - for every passage that showed words before, the same words now, read through the player's own lookup.

   A single difference stops the window, and step 2's backup restores it.
6. Run a merge round: it reports no translation and raises no item that the round before raised none of.
7. Start the players, playing.

**`[IMPL-UID-069]` As built, 2026-11-16.** Measured first, on the hub and all six nodes: no review names a passage its own catalogue lacks, and no flag names a passage. Plays, skips and decisions do: 14 to 47 plays a node, none on the hub or `lrhtpc`, each keeping its recording. They were found by the conversion itself; a census that first said none compared an unqualified column inside its own subquery. Also measured: the table definitions differ from machine to machine; `bose`'s 60,811 plays are the most.
- **Eight tables, not seven.** `artist_reviews` names its passage too, as provenance, and it travels.
- **The player converts on open** (`db/listener_uid.rs`), so a deploy is step 3 for a node; the phones wait, out of this group, until updated separately. It first copies the whole file to `listener.pre-uid.db` beside it, then rebuilds each table in one transaction. Every column is carried by name, so no column is lost, and every index is kept. A catalogue without uids is refused, changing nothing.
- **What has no uid:** a play, a skip or a decision keeps NULL and is counted, since a play is never refused. A review is refused. A queue entry is dropped, counted, since it is nothing but its passage.
- **Writers** keep the integer the engine holds and find the uid in the same statement. **Readers** join back to it once per passage, by `passages_uid` `[IMPL-UID-056]`. A passage's flag is its uid; a number is refused for one.
- **`sql/schema.sql`** declares the catalogue's uid columns and unique index, and the listener's new shape.
- **An interlock:** until the merge is converted, `star_merge` refuses any listener, or copy last sent, already by uid, and merges nothing. Deployed alone, this player would otherwise have its listener read as numbers.
- **Step 3 changes:** the copies last sent and the nodes' hourly backups stay as they are. The merge reads an old one through that node's own map from number to uid, its catalogue summary, which is the same exact join inside one node's numbering.

- **The hub's tools** read and write these tables by uid. Among them: the console's review pages and flag list, `apply_reviews`, `apply_boundary_reviews`, the disc relinkers and the MuLibPlay migration.
- **The SPEC006 tools came forward from Stage C** `[IMPL-UID-077]`, since they share these tables. A record's anchor carries the uid beside its span, and a receiver resolves by the uid. Only a record exported before Stage B, which carries none, resolves by span. A passage's flag travels as its uid, and a remote is never sent a number for one.
- **A reference to a passage that has gone is kept.** `passage_orphans` cleared numbers because a number is reused for other music; a uid never is. So a play naming one still says what was played, and nothing clears it.

- **The merge keys by uid** `[IMPL-UID-064]`. The translation between numberings is gone from it (`passage_remap` stayed until Stage D, for `repair_node_reviews`). A node's copy last sent, its backups and the newest run's copy could still be by number from before Stage B. Until Stage D each was read through its own catalogue's map, the hub's through the hub's: a row naming a review's passage it lacks was left out and counted, and the report said what was read so. A snapshot still by number is refused by its node's name, and nothing is merged. A phone, which sends no table naming a passage, is not refused.
- **Saved words find their passage by uid** `[IMPL-UID-063]`. Under its own kind, a row with a uid is that passage's whatever its segment has become, and no other passage's; a row without one, and tier 2, by segment. A save records the uid. `tools/set_lyrics_uids.py` sets the old rows' uids, once, on the hub. Rehearsed 2026-11-16 on the hub's 143 rows: all 143 placed, each on one current passage; none left; every passage shows the same row. The merge's `hub_sets` carries the hub's value to every node without an item or a tie, and a node's new save keeps its own, as does one the hub holds with none `[IMPL-UID-065]`.

The window ran 2026-11-16 with `tools/listener_window.py` recording each listener before and verifying it after (LOG014 `[LOG-LIBW-330]`).

## 7. Stage C: every wire, URL, tool and log

**`[IMPL-UID-070]` Echo** announces `passage_uid`, and a follower resolves it. The `(audio_md5, kind, start_ms)` name and the fallback to a number go `[SPEC-UID-010]`, since the fleet updates at once.
- **As built, 2026-11-16.** The schedule, the anchor and each queue entry carry the uid. The leader's number is held for its own logic and never serialized, so an old follower reading a new leader finds no number to misuse. The uid is a 16-byte `PassageUid`, so the schedule and anchor stay `Copy`; it reads and writes as the catalogue's text and refuses any other. A follower finds its number through `passages_uid`. A passage it lacks, or one named without a uid (a player older than this one, or a passage the hub has yet to give one), stands in as a number naming nothing. That number carries the uid's first eight characters for the report. `echoprobe`, `echo_skew.py` and `echo_skew.sh` read the uid.

**`[IMPL-UID-072]` Routes:** the 17 routes and the flag route take a uid, and pages build their links with one. A number in a passage route is refused with 400, so a link carried to another node fails instead of opening another song `[SPEC-UID-035]`.
- **As built, 2026-11-16.** One extractor, `web/passage_ref.rs`, reads `:passage_id` as a uid and finds this node's number through `passages_uid`. A number, or any other text, is 400 and a uid this node lacks is 404, before the handler runs. Beside the 17 routes, it covers seek, the queue's add verbs, Browse's `album_of` and the review page's `?passage=` (the flag route has taken a uid since Stage B).
- **What the browser is sent:** the snapshot, its queue, and the browse, profile, review, segment, lyrics, preference and why answers name each passage by `passage_uid`. The number they still hold for the server's own logic is never serialized.
- **The pages:** they read the uid from their address, and show a person its first eight characters. `fbui` fetches covers by it.
- **The hub's console:** its edit and review hand-offs into the player pass the uid. Its own pages keep the hub's number.

**`[IMPL-UID-073]` One open of the pair per request.** The extractor hands on the read-only pair it found the number through, and a handler that reads takes that one and no `Ui` to open another from: the words, their sources, both covers, the profile, review and the editor's three reads. One that writes (a save, a review, a segment) opens its own store, as before, and closes the extractor's first. Found by review 2026-11-17: each of those routes had opened the pair twice.

**`[IMPL-UID-074]` Remote tools:** `lempi_control.py` flags and unflags on a remote by uid, with no peek for the remote's number. The console's `remote_status` and `flag_sync_status` anchor by uid, and `echo_skew.py` compares uids.
- **As built, 2026-11-16.** `remote_peek.py` names a passage by `--passage-uid` alone, refusing anything else before a query is built. `passage_flag` reports `held`, not the remote's number, and reads the peer's listener beside its catalogue (`[SPEC-DF-124]`, open until now). The remote-basis accept goes by uid end to end: route, job and `accept_remote_basis.py`.
- **The unflag:** it sends the passage's uid and its recordings to the remote with no peek first. A read of the remote, when it works, adds the recordings the remote itself links. A remote without the passage clears nothing by it.

**`[IMPL-UID-076]` To people:** star-sync reports, items and the Sync page show a uid's first eight characters beside the title, never a number.
- **As built, 2026-11-16.** `star_merge.shown` gives every uid in a key or row its first eight characters, in `report.md` and in `star_sync`'s `items` and `verdict` lines; `report.json` and `items.json` keep the whole uid. The Sync page names a passage's flag or review by its title and those eight. An integrity finding owned by a passage is named the same way, where it once showed the catalogue's number. The player's pages do the same (`Lempi.shortUid`).

**`[IMPL-UID-077]` The SPEC006 change and flag tools name passages by uid** `[IMPL-UID-900]`: `export_changes.py`, `apply_changes.py`, `remote_snapshot.py`, `accept_remote_basis.py`, `export_flags.py` and `import_flags.py`. A record keeps the span it was made against as its baseline, not as the passage's name.

**`[IMPL-UID-078]` Verified on the fleet:** echo follows the same song on `bose` and `smartboardpc`, the pair of `[SPEC-UID-002]`, and a remote unflag clears the remote's flag.
- **As verified, 2026-11-16 17:20–18:08 UTC**, at the maintainer's word: the desktop, then `lempi02w`, `bose` and `smartboardpc` together, `lp3-wifi`, `lrhtpc` and `teacherslounge`, all on `486ded1` (the player of `b329aa9`), each player put back as it was. Live, 18 or 19 checks per node: no passage number in the snapshot, every route 400 by number and 404 by a missing uid, browse by uid.
- **Echo:** `smartboardpc` was on `bose`'s song, by uid, within a minute of its restart, and on the next. `echo_skew.py` read 45 samples, none on different passages, with a median skew of 26 ms and a p90 of 59 ms.
- **The unflag:** on `lempi02w`, a passage flagged nowhere was flagged through its player and read back by the console's peek. The console's own `unflag_everywhere` then cleared it, and it read back clear. The console's saved remote is `teacherslounge`, which runs no player, so the check named `lempi02w` for that run only.
- **Cost:** `lempi02w`'s passage info went from 9.0 ms by number to 14.7 ms by uid (median of 21). That is one more open of the pair per request, to find the number. Since 2026-11-17 the handler reads through that open `[IMPL-UID-073]`; not yet measured on a node.

## 8. Stage D: removed, and made hard to bring back

**`[IMPL-UID-080]` Removed:**
- the span as a passage's name in the patch vocabulary of `[SPEC-NKP-030]` (it stays as the birth key);
- `same_numbering`, `pair_moved` and the `other-ids` state;
- the report's translation section;
- echo's fallback to a number;
- `repair_node_reviews.py` and `repair_review_copies.py`.

- **The patch, as built 2026-11-16.** One vocabulary names a file by `audio_md5` and a passage by its uid, in `star_patch.py` and `mesh_sync.rs` alike. The word `passage`, a span, is refused by name, as is a catalogue pair with a passage lacking its uid. `same_numbering`, `pair_moved` and `other-ids` are gone, and the catalogue state compares summaries by uid. The strict patch by the hub's own ids, for a player older than natural keys, went too: no node runs one, it was a number crossing, and only `other-ids` had kept it safe. On a copy of `bose`'s real catalogue, edited five ways, the new builder's patch is the old one's to the byte, and `fixtures/natural_patch/patch.json` is the old `patch_uid.json`.
- **The merge, as built 2026-11-16.** `passage_remap`, `passage_unmap` and `to_node_numbering` are gone with the two repair tools. So is the reading of an old copy through its node's map. Every node's copy last sent has been by uid since round `20261116T1340Z`; the phones', from round `20261116T0119Z`, are not. A source still by number now gives none of its rows naming a passage: a review or flag is left out, a play keeps no uid, and the report's *Left out, by number* counts them. A history copy by number therefore shows no removal.

That is an estimated 400 lines of translation and the two repair tools (500 lines). The rest of the roughly 1,180 lines that exist because catalogues differ (the `rows` step, the bundle ledger, per-node catalogue copies) exists because bundles write outside the sync, and goes with SPEC075 §4–5.

**`[IMPL-UID-082]` A check refuses the mistake** `[SPEC-UID-070]`. `check_local_ids.py` runs in the pre-commit hook beside `tools/check_joined_lines.py`. It refuses:
- an added `passage_id` or `file_id` field in a serialized struct in a module whose output leaves the machine (`echo.rs`, `mesh_sync.rs`, `star_node.rs`, the web answers);
- a listener column naming a passage by number;
- a passage route taking an integer;
- an `INSERT INTO passages` outside the helpers.

Its allow-list can only shrink, and it is empty at the end of Stage C. A Rust `LocalPassageId` with no `Serialize` follows, for those modules.
- **As built, 2026-11-16.** `tools/check_local_ids.py` runs in the hook over the lines a commit adds, and in CI over the tree. Its first full scan, of 366 files, found nothing, so there is no allow-list. A field kept with `#[serde(skip)]` passes; `skip_serializing_if` still sends one, and does not. It reads all of `player/src` and `lempi-core`, not only the modules named above. Test code is outside it: it builds the old shapes on purpose. `HELPERS`, the writers that give a passage its uid, is the one list `test_passage_uid.py` reads too. Its test plants each finding, and breaking any rule in turn fails it. `LocalPassageId` is not built.

**`[IMPL-UID-084]` Every crossing is tested over unlike numbering** `[SPEC-UID-020]`. `tools/test_numbering_fleet.py` and echo's tests gain each of these, run over the `shuffled` catalogue, where a stray number still finds a passage:
- a merge with a removal;
- a passage only a node holds;
- a bundle;
- a boundary edit;
- a remote unflag;
- a member patch.
- **As built, 2026-11-16.** `test_numbering_fleet.py` runs five of them over `shuffled`: a merge with a removal, a passage only `partial` holds, a boundary edit, a remote unflag and a member patch. Each picks a passage whose number on `shuffled` is other music on the hub, and checks that music is untouched. The bundle is `bundle.rs`'s `a_bundle_lands_under_the_nodes_own_number_by_its_uid`: a bundle carries no number, so the passage lands under the node's own. Echo's was built in Stage C. A member patch still holds passage rows back both ways; it carries them once the phones update `[IMPL-UID-066]`.

**Stage D on the fleet, 2026-11-16 22:10 UTC.** Round `20261116T2210Z`, rehearsed and not committed, at the maintainer's word. The phones were left out and teacherslounge was held awake. The hub's console ran Stage D's tools: every node's catalogue read *as last sent*, by uid, where the round before under the old code read every one *the hub's music under other ids*. The merge had integrity ok, no decision and no item, and read nothing by number. Every node, both halves, had nothing to send, so no node was asked to apply anything. Every player was left as it was. The players' half of Stage D, the applier refusing the span word, is not deployed: the builder no longer sends one, and the next deploy carries it.

## 9. Order

**`[IMPL-UID-090]` Each stage goes the same way:**
1. Code, with its tests and the shared fixture.
2. Rehearse the migration on copies of every node's newest snapshot in a scratch folder, checked exactly as the window will check, and timed for `lempi02w`, the smallest node.
3. The desktop.
4. The window.

The stages run 0, A, B, C, D. A and B are separate windows, so a fault lies in one half.

**`[IMPL-UID-092]` Scheduling.** The Christmas reserve must reach every node before 1 December 06:00 ([SPEC078](spec/SPEC078-a-christmas-reserve.md)). It deploys whenever that is most convenient before then: on its own, or with any stage's deploy (the maintainer, 2026-11-14).

## 10. Decided

The maintainer, 2026-11-14:

- **`[IMPL-UID-900]` The SPEC006 change and flag tools are converted to uids,** not retired `[IMPL-UID-077]`.
- **`[IMPL-UID-902]` The four orphan reviews are dropped** (passages 6455–6458, decided 2026-11-01) `[IMPL-UID-044]`.
- **`[IMPL-UID-904]` `passage_lyrics` is re-keyed by uid** in Stage B. A passage's words are found by its uid, and no saved row is lost `[IMPL-UID-063]`: each row keeps its own key, so the history stays whole. This changes how [SPEC079](spec/SPEC079-editing-lyrics.md) finds a passage's words, when it is built.
- **`[IMPL-UID-906]` Withdrawn reviews stop coming back now,** in Stage 0, rather than waiting for Stage B `[IMPL-UID-042]`.

---

**Traceability:** `[IMPL-UID-010..906]` · builds `[SPEC-UID-010]`, `[SPEC-UID-020]`, `[SPEC-UID-030]`..`[SPEC-UID-070]` · removes `[SPEC-STAR-049]`..`[SPEC-STAR-052]` and the positional half of `[SPEC-NKP-046]` · leaves SPEC075 §4–6 for a later plan
