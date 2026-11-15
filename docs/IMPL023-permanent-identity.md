# IMPL023: Building Permanent Identity

**Implementation Specification — Tier 3 · written 2026-11-14 · Stage 0 done 2026-11-15 on the hub and five nodes, `teacherslounge` to follow · Stage A: the uid and the tools' passage helper built, the player's half not yet · the build plan for [SPEC075](spec/SPEC075-permanent-identity-and-a-movable-hub.md) §3 and §8 steps 1–3, and for every other place a number one database assigned is read by another**

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
6. From the next round, passage references in the patch resolve by uid.

**`[IMPL-UID-054]` Undone by** each node's kept inverse (`mesh_sync::undo`), which drops the added columns, and on the hub by step 2's backup.

## 6. Stage B: the listener names passages by uid

**`[IMPL-UID-060]` Every listener table that names a passage holds its uid instead of its number:**
- `listener_flags`, whose `subject_id` is the uid where `subject_kind = 'passage'`;
- `id_reviews` and `boundary_reviews`, keyed by `passage_uid`;
- `passage_lyrics`, which finds a passage's words by `passage_uid`, so a boundary edit no longer moves them, and keeps every row it holds `[IMPL-UID-063]`;
- the node's own `listener_play_history`, `listener_rejections`, `selection_decisions`, `player_state` and `player_queue`.

The number column goes, because kept beside the uid it would be a second source for one fact ([GOV002](GOV002-sources-of-truth.md)). Its readers, among the 56 files in the player and the tools that name these tables, join `passages` on `passage_uid`. The engine and the Director still hold the integer in memory, found by that join when they load.

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

**`[IMPL-UID-066]` Phones.** A phone app built from this `lempi-core` converts its own listener on open and reports `passage_uids`. Its snapshot stops holding passage rows back, and its member patch carries them. A phone that has not updated sends and is sent no passage rows, which is what every phone does today, so nothing needs translating while it waits `[SPEC-UID-410]`.

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

## 7. Stage C: every wire, URL, tool and log

**`[IMPL-UID-070]` Echo** announces `passage_uid`, and a follower resolves it. The `(audio_md5, kind, start_ms)` name and the fallback to a number go `[SPEC-UID-010]`, since the fleet updates at once.

**`[IMPL-UID-072]` Routes:** the 17 routes and the flag route take a uid, and pages build their links with one. A number in a passage route is refused with 400, so a link carried to another node fails instead of opening another song `[SPEC-UID-035]`.

**`[IMPL-UID-074]` Remote tools:** `lempi_control.py` flags and unflags on a remote by uid, with no peek for the remote's number. The console's `remote_status` and `flag_sync_status` anchor by uid, and `echo_skew.py` compares uids.

**`[IMPL-UID-076]` To people:** star-sync reports, items and the Sync page show a uid's first eight characters beside the title, never a number.

**`[IMPL-UID-077]` The SPEC006 change and flag tools name passages by uid** `[IMPL-UID-900]`: `export_changes.py`, `apply_changes.py`, `remote_snapshot.py`, `accept_remote_basis.py`, `export_flags.py` and `import_flags.py`. A record keeps the span it was made against as its baseline, not as the passage's name.

**`[IMPL-UID-078]` Verified on the fleet:** echo follows the same song on `bose` and `smartboardpc`, the pair of `[SPEC-UID-002]`, and a remote unflag clears the remote's flag.

## 8. Stage D: removed, and made hard to bring back

**`[IMPL-UID-080]` Removed:**
- the span as a passage's name in the patch vocabulary of `[SPEC-NKP-030]` (it stays as the birth key);
- `same_numbering`, `pair_moved` and the `other-ids` state;
- the report's translation section;
- echo's fallback to a number;
- `repair_node_reviews.py` and `repair_review_copies.py`.

That is an estimated 400 lines of translation and the two repair tools (500 lines). The rest of the roughly 1,180 lines that exist because catalogues differ (the `rows` step, the bundle ledger, per-node catalogue copies) exists because bundles write outside the sync, and goes with SPEC075 §4–5.

**`[IMPL-UID-082]` A check refuses the mistake** `[SPEC-UID-070]`. `check_local_ids.py` runs in the pre-commit hook beside `tools/check_joined_lines.py`. It refuses:
- an added `passage_id` or `file_id` field in a serialized struct in a module whose output leaves the machine (`echo.rs`, `mesh_sync.rs`, `star_node.rs`, the web answers);
- a listener column naming a passage by number;
- a passage route taking an integer;
- an `INSERT INTO passages` outside the helpers.

Its allow-list can only shrink, and it is empty at the end of Stage C. A Rust `LocalPassageId` with no `Serialize` follows, for those modules.

**`[IMPL-UID-084]` Every crossing is tested over unlike numbering** `[SPEC-UID-020]`. `tools/test_numbering_fleet.py` and echo's tests gain each of these, run over the `shuffled` catalogue, where a stray number still finds a passage:
- a merge with a removal;
- a passage only a node holds;
- a bundle;
- a boundary edit;
- a remote unflag;
- a member patch.

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
