# SPEC046: Star Sync — One Central Library, Merged From the Fleet

**Design Specification — Tier 2 · written 2026-09-26, the day the primary was found missing**

The Vipunen primary was lost when this repository was seeded (`data/README.md`), and no surviving copy was complete: the desktop lineage stopped in early September, while the latest listener edits lived on `lempi02w`. The maintainer's direction: merge the latest information from every database, **latest edit wins, in a star topology**, into one central copy on the desktop. That copy is verified by hand, then distributed. And a **deterministic program** is to do this from now on, so the same thing never depends on care alone again.

> **Related:** [SPEC006](SPEC006-data-flow-and-portability.md) (data classes, `[SPEC-DF-070]`) · [SPEC030](SPEC030-preference-sync.md) (last-write-wins) · [SPEC022](SPEC022-flag-and-edit-sync.md) (flags, patching a node) · [SPEC035](SPEC035-mesh-library-sync.md) (manual vs manual) · `data/README.md` · [FLEET001](../../fleet/FLEET001-the-fleet.md)

---

## 1. The shape

**`[SPEC-STAR-010]` One hub, many spokes.** The hub is the desktop's `data/` pair. Every other database in the fleet is a spoke: each appliance's, `smartboardpc`'s, and `teacherslounge`'s, which is also the hub's mirror for validating the tools under Linux. Edits made anywhere reach the hub first, and leave the hub for everywhere else. No spoke is ever merged with another spoke directly. That is what keeps N nodes one comparison each, rather than a mesh `[SPEC-MESH-020]`.

**`[SPEC-STAR-020]` The merge reads snapshots, never live files, and writes a new pair.** Its inputs are consistent copies taken through SQLite's backup API, each hash-verified after transfer (the procedure in §5). Its output goes to a fresh directory beside `data/`; it never writes over an input or over `data/` itself. Promoting the output to be the hub is a separate step that a person takes `[SPEC-STAR-070]`.

**`[SPEC-STAR-030]` Deterministic: the same inputs give the same bytes.** Every choice is made by a rule in §2 and never by input order. When a rule ties, it breaks the tie by node name, alphabetically, and the report says so. Running the merge twice over the same snapshots produces identical output. A test holds it to that.

## 2. The rules, by kind of data

**`[SPEC-STAR-040]` What travels where follows SPEC006's classes, with the fleet as the owner's own machines** `[SPEC-DF-055]`:

| data | tables | merged by | leaves the hub for the spokes? |
| :--- | :--- | :--- | :--- |
| household listener edits | `listener_preferences`, `listener_characteristics`, `listener_settings` (its `utc_offset_minutes` is the node's own, rewritten by each player at start) | **last write wins** on `updated_at` `[SPEC-PREF-105]`; an exact tie with differing values is reported, not guessed | yes |
| flags | `listener_flags` | union, with removals taken from the evidence in §3 | yes |
| programmes and occasions | `listener_programs`, `…_program_seeds`, `listener_occasions`, `…_occasion_points` | no timestamps: union, with removals from §3 | yes |
| catalogue corrections | `id_reviews`, `boundary_reviews`, `artist_reviews` | union by (subject, `decided_at`); the latest `applied_at` is kept | yes, and applied to each catalogue |
| catalogue (classes A–C) | the library half | three ways against the common ancestor `[SPEC-STAR-047]`; conflicting changes by provenance rank, then recency `[SPEC-DF-070]`; two differing **manual** values: the newer wins by default and is listed for the person `[SPEC-MESH-065]` | yes |
| plays | `listener_play_history`, `listener_rejections` | **never merged**: each node keeps its own, the hub included `[REQ-PD-113]` | never |
| node state | `player_*`, `selection_decisions`, `schema_meta` | not merged; the hub keeps its own | never |

**`[SPEC-STAR-047]` The catalogue merges three ways, against the oldest common ancestor** — SPEC006 §9's baseline/target/current, generalised to N copies. Measured 2026-09-26: every catalogue descends from one desktop library, and each then received a *different* part of the later work. `teacherslounge` holds 40 re-credits the desktop synced on 09-12 (`inherited:mulib` → `synced:GMKtec`), 39 recordings and 51 artist credits. The desktop's 09-10 copy holds 138 flavor rows `teacherslounge` lacks. So no copy is simply newest, and a missing row may be a deletion — the old credit a re-credit replaced — as easily as an addition. For each row, keyed by its primary key:

- the ancestor's value is the baseline, and absent counts as a value;
- a copy that differs from the baseline has **changed** it, and only changes count;
- changes that agree are taken; changes that disagree go by provenance rank (`manual` › `synced:` › computed › `inherited:`/`local:`), then the row's own time column, then node name, and are reported;
- a deletion is a change, but it loses to a conflicting modification, and the row is kept and reported.

Two refinements, both found in the first trial on 2026-09-26:

- **One edit under two labels is not a conflict.** An identification made on the desktop reads `review:acoustid` there, and `synced:GMKtec` on a spoke that received it. Where copies differ only in their provenance label, the hub keeps the original's rather than the `synced:` copy's, and the report counts it as *relabelled*. That was 146 of the trial's 150 "conflicts".
- **A column the ancestor predates is baselined at its default.** Filling in `fade_in_ms = 20` is a migration, not an edit. A copy holding exactly the default has not changed the row; one holding anything else has. That was the other 4.

**`[SPEC-STAR-048]` An appliance's catalogue is a receiver's, not an author's.** Only Vipunen writes the catalogue — the desktop, and the copies of it on `smartboardpc` and `teacherslounge`. An appliance only receives what is pushed to it, so an absence there means *never received*, not *deleted*, and an older value means *not yet updated*, not an edit. Found 2026-09-26: `lempi02w`'s catalogue predates the 08-25 ancestor in places (8,190 `id_checks` against 8,330), and read as an author it would have deleted 140 identification verdicts nobody deleted. So a receiver's absences are never deletions and its values are never taken automatically. Each value it holds that differs from both the ancestor and the merged result is **listed for the person**. A table no author holds at all — `works` and `recording_works` exist only on the appliances — is taken from the receivers, since there is nothing to compare it with.

**Machine-scope columns never merge** `[SPEC-DF-030]`: `files.path`, `size_bytes`, `mtime` and `last_seen` are each machine's own, and the hub keeps the desktop's.

**`[SPEC-STAR-049]` A passage id is local, so a node's ids are translated before its rows join the hub** `[SPEC-DF-035]`. Found 2026-09-26: the four locally ingested Frisina tracks were inducted separately on the desktop and on `lempi02w`, and received their ids in a different order. `lempi02w`'s passage 16407 is the hub's 16409, and its plays of 16407 would otherwise count against a different song. For each listener row naming a passage, the node's own catalogue gives the passage's file (by `audio_md5`). Where the hub's passage of that id lies in a different file, the row is translated to the hub's passage on the node's file with the same kind and the same position among that file's passages. A passage the hub no longer has — a capture re-cut since — keeps its id, which then matches nothing, and is counted in the report. A merge with a catalogue half translates first, then merges.

**`[SPEC-STAR-045]` Plays stay home** `[REQ-PD-113]`. Each node's rotation is built from what *that node* played, so a song played in the kitchen does not rest in the study. The hub is a node like the others here: its listener holds the desktop's own plays, and no one else's. A node's history is kept safe by that node's own backups `[REQ-LIB-160]` and by the dated snapshots a merge takes `[SPEC-STAR-075]`, never by folding it into another node's. *(This spec first had the hub keep the union of every node's plays. The maintainer ruled that out on 2026-09-26, before anything was promoted.)*

## 3. Removals, which a timestamp cannot show

**`[SPEC-STAR-050]` A row that is missing is ambiguous: never added, or removed.** Last-write-wins sees only rows that exist, so an unflagged track would come back on the next merge from any node that still had the flag. The evidence is each node's own hourly listener backups `[REQ-LIB-160]`. A row present in a node's earlier backup and absent from its current snapshot **was removed on that node**, at some time after that backup. The removal wins over a copy elsewhere whose own timestamp is older than that backup, and loses to one newer, which is a re-add. A row that no node's history shows removed is kept. Every removal applied is listed in the report, with the backup that evidences it.

## 4. The report, and the person

**`[SPEC-STAR-060]` Every decision is written down.** The merge writes a report beside its output. It gives counts per table and per node, and lists every row the hub takes from one node over another, every tie, every removal, and every manual-versus-manual conflict, each with the rule that decided it. A decision the report does not name was not made.

**`[SPEC-STAR-070]` Nothing is promoted or distributed until a person has read the report.** The maintainer checks it, can override any listed decision, and then promotes the output to `data/`. Distribution is a separate, later step `[SPEC-STAR-080]`.

## 5. Taking a snapshot

**`[SPEC-STAR-075]` Snapshot, then verify the copy, then use it.** A live SQLite file is copied through the backup API on its own node, never with `cp` or `scp` of the file itself, since the WAL holds the recent writes. The SHA-256 is computed on the node, the file is transferred, and the copy is kept only if the hash matches. A transfer that stalls is retried, and a node that cannot be reached is **reported as missing from the merge**, never quietly left out. Found necessary on 2026-09-26: a stalled `scp` left 20.1 of bose's 21.6 MB, which a size-blind copy would have accepted.

## 6. Distribution

**`[SPEC-STAR-080]` Leaving the hub is by patch, not by file copy** `[SPEC-DF-110]`. A node keeps playing between its snapshot and the switch, and a file copy would lose what it did meanwhile. Built 2026-09-26, in three parts:

1. **A copy per node.** Beside the hub's pair, `tools/star_merge.py` writes `nodes/<name>/`. Its listener holds the household's edits, merged exactly as the hub's are, with the node's own plays and state, in the schema the node's own player made. Its catalogue is the hub's, with the node's own paths matched by `audio_md5`, since a file id is local too `[SPEC-DF-035]`. A mirror of the hub receives the hub's listener.
2. **A patch from the node's snapshot to its copy.** `tools/star_patch.py` carries each changed row's key, a digest of its snapshot value, and only the columns that change. It applies a row only where the node still holds the snapshot value, and one conflict writes nothing. A column the player adds on open is added as the player would add it. Rows the patch does not name, the plays recorded since the snapshot among them, are never touched.
3. **One node at a time.** `tools/star_distribute.py` first rehearses on the node, against a copy of its live data, with the player running. On `--commit` it stops the player, backs up the live pair into a dated folder on the node and leaves it there, applies both patches, and compares the files on disk with the target, table by table `[GDE-DEP-070]`. If the second patch fails, the first is restored from that backup, and the player is started again whatever happened `[SPEC-DF-127]`. It refuses a database on an overlay `[GDE-DEP-060]`.

Found while building it: a fingerprint read `immutable` ignores the WAL, and missed 375 KB of one on `lp3-wifi`. The node-side tool reads a live database with its WAL.

## 7. Open

1. ~~**`[SPEC-STAR-900]` bose was unreachable** when the first snapshots were taken~~ *(resolved: its snapshot was taken after a power cycle the same day, and is in every merge since)*.
2. ~~**`[SPEC-STAR-910]` Whether plays should one day travel**~~ *(resolved by `[REQ-PD-113]`: they do not)*.

---

**Traceability:** `[SPEC-STAR-010..910]` · from the maintainer's direction of 2026-09-26 · applies `[SPEC-DF-055]`, `[SPEC-DF-070]`, `[SPEC-PREF-105]`, `[SPEC-MESH-065]`, `[SPEC-DF-110]`
