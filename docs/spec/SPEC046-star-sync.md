# SPEC046: Star Sync — One Central Library, Merged From the Fleet

**Design Specification — Tier 2 · written 2026-09-26, the day the primary was found missing**

The Vipunen primary was lost when this repository was seeded (`data/README.md`), and no surviving copy was complete: the desktop lineage stopped in early September, while the latest listener edits lived on `lempi02w`. The maintainer's direction: merge the latest information from every database, **latest edit wins, in a star topology**, into one central copy on the desktop. That copy is verified by hand, then distributed. And a **deterministic program** is to do this from now on, so the same thing never depends on care alone again.

> **Related:** [SPEC006](SPEC006-data-flow-and-portability.md) (data classes, `[SPEC-DF-070]`) · [SPEC030](SPEC030-preference-sync.md) (last-write-wins; its two-node tool is kept for ad hoc use) · [SPEC022](SPEC022-flag-and-edit-sync.md) (flags, patching a node) · [SPEC035](SPEC035-mesh-library-sync.md) (manual vs manual) · [SPEC054](SPEC054-mesh-without-ssh.md) (the signed transport, §8) · [SPEC013](SPEC013-vipunen-console.md) (the console §10 extends) · `data/README.md` · [FLEET001](../../fleet/FLEET001-the-fleet.md)

---

## 1. The shape

**`[SPEC-STAR-010]` One hub, many spokes.** The hub is the desktop's `data/` pair. Every other database in the fleet is a spoke: each appliance's, `smartboardpc`'s, and `teacherslounge`'s, which validates the tools under Linux and is a node like the rest `[SPEC-STAR-103]`. Edits made anywhere reach the hub first, and leave the hub for everywhere else. No spoke is ever merged with another spoke directly. That is what keeps N nodes one comparison each, rather than a mesh `[SPEC-MESH-020]`.

**`[SPEC-STAR-020]` The merge reads snapshots, never live files, and writes a new pair.** Its inputs are consistent copies taken through SQLite's backup API, each hash-verified after transfer (the procedure in §5). Its output goes to a fresh directory beside `data/`; it never writes over an input or over `data/` itself. Promoting the output to be the hub is a separate step that a person takes `[SPEC-STAR-070]`.

**`[SPEC-STAR-030]` Deterministic: the same inputs give the same bytes.** Every choice is made by a rule in §2 and never by input order. When a rule ties, it breaks the tie by node name, alphabetically, and the report says so. Running the merge twice over the same snapshots produces identical output. A test holds it to that.

## 2. The rules, by kind of data

**`[SPEC-STAR-040]` What travels where follows SPEC006's classes, with the fleet as the owner's own machines** `[SPEC-DF-055]`:

| data | tables | merged by | leaves the hub for the spokes? |
| :--- | :--- | :--- | :--- |
| household listener edits | `listener_preferences`, `listener_characteristics`, `listener_settings` (its `utc_offset_minutes` is the node's own, rewritten by each player at start) | **last write wins** on `updated_at` `[SPEC-PREF-105]`; an exact tie with differing values is reported, not guessed | yes |
| flags | `listener_flags` | union, with removals taken from the evidence in §3 | yes |
| likes | `listener_likes` | union by (recording, `recorded_at`): a like or dislike is the household's judgment of a song, not listening data -- distinct from plays and skips, though usually given while listening (the maintainer, 2026-10-01) | yes |
| occasions | `listener_occasions`, `…_occasion_points` | no timestamps: union, with removals from §3 | yes |
| programmes | `listener_programs`, `…_program_seeds` | **never merged** since 2026-09-27: each node's own, copied between a node and the hub only by hand `[SPEC-MTR-040]`; unioned before that | never |
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

**`[SPEC-STAR-075]` Snapshot, then verify the copy, then use it.** A live SQLite file is copied through the backup API on its own node, never with `cp` or `scp` of the file itself, since the WAL holds the recent writes. The SHA-256 is computed on the node, the file is transferred, and the copy is kept only if the hash matches. A transfer that stalls is retried, and a node that cannot be reached is **reported as missing from the merge**, never quietly left out. Found necessary on 2026-09-26: a stalled `scp` left 20.1 of bose's 21.6 MB, which a size-blind copy would have accepted. *Since 2026-10-04 a player makes its own snapshot over the signed transport, sending its shared tables and a summary of its catalogue, each answer signed `[SPEC-STAR-100]`; what is said here of the backup API now holds for the hub's own copy.*

## 6. Distribution

**`[SPEC-STAR-080]` Leaving the hub is by patch, not by file copy** `[SPEC-DF-110]`. A node keeps playing between its snapshot and the switch, and a file copy would lose what it did meanwhile. Built 2026-09-26, in three parts:

1. **A copy per node.** Beside the hub's pair, `tools/star_merge.py` writes `nodes/<name>/`. Its listener holds the household's edits, merged exactly as the hub's are, with the node's own plays and state, in the schema the node's own player made. Its catalogue is the hub's, with the node's own paths matched by `audio_md5`, since a file id is local too `[SPEC-DF-035]`. *(A node once marked a mirror received the hub's whole listener; none is now `[SPEC-STAR-103]`.)*
2. **A patch from the node's snapshot to its copy.** `tools/star_patch.py` carries each changed row's key, a digest of its snapshot value, and only the columns that change. It applies a row only where the node still holds the snapshot value, and one conflict writes nothing. A column the player adds on open is added as the player would add it. Rows the patch does not name, the plays recorded since the snapshot among them, are never touched.
3. **One node at a time.** `tools/star_distribute.py` first rehearses on the node, against a copy of its live data, with the player running. On `--commit` it stops the player, backs up the live pair into a dated folder on the node and leaves it there, applies both patches, and compares the files on disk with the target, table by table `[GDE-DEP-070]`. If the second patch fails, the first is restored from that backup, and the player is started again whatever happened `[SPEC-DF-127]`. It refuses a database on an overlay `[GDE-DEP-060]`. *Since 2026-10-04 this is a manual tool, not a stage: the player applies its own patch `[SPEC-STAR-100]`.*

Found while building it: a fingerprint read `immutable` ignores the WAL, and missed 375 KB of one on `lp3-wifi`. The node-side tool reads a live database with its WAL.

## 7. The routine

The recovery above was run by hand, once. From 2026-09-26 the same thing is one tool, `tools/star_sync.py`, with one command per stage, reading the fleet's plan from `fleet/star-plan.json` (untracked; `fleet-example/star-plan.json` shows its shape).

**`[SPEC-STAR-085]` Routinely, only the hub authors the catalogue, and a node's catalogue is copied only when it has moved.** Catalogue edits are made in Vipunen, on the desktop, so a routine merge takes the hub's catalogue as it stands. Every node receives it with its own paths. A node's catalogue is fingerprinted on the node, with its WAL, and compared with what it was last sent. Only a difference brings the 1.2 GB file across; otherwise the copy last sent is the baseline for its patch. *Over the signed transport the catalogue never comes up: the node's summary is compared with the copy last sent, and a node whose summary differs is sent no catalogue patch `[SPEC-STAR-940]`.* The listener is copied every time, compressed. What each node was last sent is recorded in `data/sync/state.json`, and that copy is also the evidence for anything the node has removed since `[SPEC-STAR-050]`: the node held it then, and does not now. The stages are `snapshot`, `merge`, `patch` (every patch proven offline, and a `SUMMARY.md` for the person), `rehearse` (read-only, against the live files) and `commit` (the hub first, then each node, as `[SPEC-STAR-080]`). An unreachable node is listed as missing and receives nothing until the next run `[SPEC-STAR-075]`. A node keeps the backups of its three most recent syncs, with the catalogue's on the library partition. The recovery's `pre-star-2026-09-26` backups are never pruned.

**`[SPEC-STAR-086]` The hub is backed up daily, mirrored, and says so when it stops.** The primary was lost once because it lived in one untracked folder (`data/README.md`). `star_sync.py … backup` runs daily from Task Scheduler, through `tools/hub_backup.cmd`:
- It copies the pair through the backup API into `data/backups/`, stored by content hash. An unchanged catalogue is neither stored nor sent again.
- It keeps 14 days and the first day of each of 12 months.
- It sends the same days and files to `teacherslounge`.
- It exits non-zero when the newest backup is over 30 hours old or the mirror is behind, and the wrapper then puts a message on screen.
- `status` prints the same answer at any time. The Vipunen launcher runs it first, so a stopped backup is seen by whoever opens the console.

**`[SPEC-STAR-087]` A sync runs when a person starts it.** `[SPEC-STAR-070]` stands: nothing is distributed until someone has read the run's summary and the merge's report. So the sync is not scheduled. `status` shows how long since each node was last synced. The backup, which changes nothing anywhere, is the part that runs by itself.

**`[SPEC-STAR-090]` The audio a node lacks goes by bundle, found by asking it.**
A star sync carries the catalogue, never audio, and refuses an entry for audio
the node does not hold `[SPEC-STAR-080]`. So new music reaches a speaker as a
bundle first. Built 2026-10-01 at the maintainer's request: the Export page's
*Send what's missing* asks each speaker ticked which audio it holds --
`mesh_diff.py` on `files`, `holds` and `credits`, read-only over ssh -- and builds one
bundle of exactly the files this library has and it lacks
(`export_bundle.py --md5-file`), each with every fact about it. It names what
is missing by album. It trusts a node's `files` table to name the audio on its
disk; checked on lempi02w that day, 5,709 listed and 5,709 there, and the 49 it
lacked were exactly the albums added since its last sync. Corrections to what
both already hold are not this: they are the sync's -- with two exceptions,
each sent in the same bundle as payload alone: a passage's hold, so a damaged
track is not left choosable on a speaker until the next sync `[SPEC-HOLD-080]`,
and an artist credit the speaker lacks, so its music is not left out of
Browse by Artist `[SPEC-PL-054]`.

**`[SPEC-STAR-092]` Sending is one press per speaker, over ssh.** *Send to
<speaker>* runs `tools/send_bundle.py` as a console job: first a dry run that
says what it found on the speaker and would do, then -- on a second press --
the real one. `import_bundle` binds each file where it finds it and copies
nothing, so the audio is streamed into the speaker's own music folder
(`/srv/library/audio`, files 0644, folders 0755) *before* the import, never
left in a staging folder the catalogue would then point into. A library on a
read-only mount -- bose's, by design -- is written only inside
`BosePi/attended-import.sh`'s window, with its WAL guard `[BOS-RUN-092]`, and
the player is asked to reload only once the window is shut. The two command
lines remain on the page, for a person to run by hand. `import_bundle` is
installed on every appliance by `build/deploy-appliance.sh` since 2026-10-01;
before, nothing installed it. This is within `[SPEC-SUI-110]`, which rules out
a *Lempi endpoint* -- a speaker needing an API to receive -- not ssh: the target
is still an ssh host and a directory, and the console changes it only on a
person's press, as `remote-push` already does `[SPEC-DF-111]`. *(The first
version of this section read `[SPEC-SUI-110]` as "Vipunen reaches no host to
change it", and printed `rsync` commands for a person to paste; Windows has no
`rsync`, and those commands also imported the audio where it was staged.)*

**`[SPEC-STAR-094]` A speaker's music folder is its own, and is checked before a
send.** The Pis keep their music beside the library, which is the default; a
speaker that does not -- `smartboardpc`, on a USB drive mounted on demand;
`teacherslounge`, in `~/Music` -- has its folder recorded with it
(`sync_peers.audio_root`, set where a node is added). A recorded folder must
exist, or nothing is sent: on a drive not mounted, writing there would fill the
disk under its mount point. And the audio must fit with a GiB to spare, on
every speaker -- smartboardpc's drive had 11 GB free of 1.9 TB, 2026-10-02.

Found building it: every remote read from the desktop decoded the reply in the
Windows code page, so the first curly apostrophe in a speaker's catalogue
crashed `mesh_diff.py` -- and the Mesh page's diff with it. `remote_peek._ssh`
now reads replies as UTF-8, strictly, and says so when one is not. And the CD
import had never recorded a file's byte hash (`files.sha256`, `[REQ-AND-960]`),
so a bundle of a CD album could not check its copies against the catalogue; it
does now, and `add_byte_hashes.py --write` filled the 49 already added.

## 8. How a node is reached

**`[SPEC-STAR-100]` One transport: signed requests to a player that is a member of the mesh.** Decided 2026-10-04 by the maintainer's rule, no ssh where that is practical: the player's half is built (`star_node.rs`: `snapshot`, `rehearse`, `commit`) and the hub's is `mesh.sync_step`, `[SPEC-NSH-020..090]`. The player applies its own patch, so it is **not stopped**; its catalogue never travels up (1.27 GB); and no key from the hub's machine reaches any node. `smartboardpc`, a non-appliance host, agreed with the ssh path in the first shadow run, so a general-purpose machine is no obstacle.

| node | how it is reached | player |
| :--- | :--- | :--- |
| `lempi02w`, `bose`, `lp3-wifi`, `smartboardpc` | signed | always running |
| `teacherslounge` | signed | **on demand**, `[SPEC-STAR-101]` |
| phones | their own channel `[REQ-AND-330]` | theirs |
| the desktop | local | the hub |

**ssh carries no sync data.** It remains for what it did before: starting and stopping an on-demand player, the hub's backup mirror `[SPEC-STAR-086]`, and an operator's administration `[SPEC-NSH-010]`. `star_distribute.py`, the old node path, is no longer a stage; it stays as a manual tool for a node whose player is wedged, and goes when `[SPEC-STAR-102]` has been met on every node.

**`[SPEC-STAR-101]` An on-demand player is started for a stage and stopped after it.** A node whose player is not kept running names, in the plan, `"on_demand": {"start": CMD, "stop": CMD}`, each run over ssh. A stage that needs the node starts it, waits up to 90 s for it to answer the members' query (else it is missing from the run `[SPEC-STAR-075]`), and afterwards stops it **only if that stage started it**. On `teacherslounge` the command is a `systemctl --user` unit, not enabled at boot `[TL-OPN-020]`, in the desktop session's audio environment `[TL-OPS-020]`, started with `--paused`: the player neither resumes playing nor plays, whatever it was doing when it last stopped. Membership is the node's own and persists; only the process is temporary.

**`[SPEC-STAR-102]` The signed commit has not yet run live, and the first on each node is attended.** The only shadow run (2026-09-28, since retired as a stage) carried no catalogue rows, and the catalogue measurement `[SPEC-NSH-900]` is open. A live snapshot and merge over the signed transport ran against the four players on 2026-10-05; no commit has. A node's first signed commit is the maintainer's, after a clean rehearsal.

**`[SPEC-STAR-103]` There is no mirror.** A node used to receive the hub's whole listener, plays included. Every node, `teacherslounge` too, now keeps its own plays and state `[REQ-PD-113]` and receives the shared tables and the catalogue, as the rest do. Decided 2026-10-04.

**`[SPEC-STAR-104]` A write to a read-only catalogue checks its journal mode before the partition goes back.** `bose`'s `/srv/library` is remounted read-write for a catalogue commit; if the file is then in WAL mode, read-only is the outage `[BOS-RUN-092]`. The commit leaves the partition read-write, and says so in its result, rather than close it. (`star_patch.py` never changes the mode, so this has held; the check makes it more than luck.)

## 9. Reviewing the conflicts

The merge writes every decision down `[SPEC-STAR-060]`, and a person reads before anything is sent `[SPEC-STAR-070]`. This makes that reading a step the tool enforces, and asks for it only where something is being lost.

**`[SPEC-STAR-110]` The hub is the source of truth; an item is a change the merge would discard.** A node's **change** is a row that differs from the copy it was last sent (`data/sync/state.json`, `[SPEC-STAR-085]`); the hub is held to the same, against its own last-sent copy. Values are compared, not stamps: the same settings made at two times are the same change.

- Every node holds what the hub holds, or nothing has changed since the last sync: **nothing to approve.**
- One node changed a row and the merge takes it: **propagation**, counted per table and not listed.
- **An item** arises when the merge takes a value other than one that some node changed to: two or more nodes, the hub among them, changed one row differently; or a removal `[SPEC-STAR-050]` meets an edit elsewhere; or a catalogue copy deleted a row another modified `[SPEC-STAR-047]`.
- **The default is the most recent change** `[SPEC-PREF-105]`: the newest stamp, equal stamps going to the first node by name `[SPEC-STAR-030]`. Where a table has no stamp, the first node by name.

A node with no last-sent copy is taken as having been in step with the hub, so only its differences count.

**`[SPEC-STAR-112]` Each item carries its candidates.** `report.json` gives each item an `id` (a digest of its table, key, column where one value of a union row is in question, kind, and each candidate's node and value, **not** the default), the candidates with their node and stamp, the default, and what the merge took.

**`[SPEC-STAR-114]` A verdict is `approved` (take the default) or `chose:<node>` (take that candidate's value, or for a removal against an edit, the removing node or the editing one).** Recorded in `<run>/verdicts.json` with when it was given and `by`: `item`, or `approve-all`. A verdict whose `id` is not in the current merge is void. A verdict that changes an outcome is applied by merging again with the verdicts as input, which is deterministic `[SPEC-STAR-030]`; `patch` does so by itself, keeping the earlier merge beside it as void, and every patch and proof made from it with it.

**`[SPEC-STAR-116]` The commit gate lives in `star_sync.py`, not in the page.** `commit` refuses unless every item of the current merge has a verdict that the merge reflects, every patch is proven, and the rehearsal was clean. The CLI keeps working unattended `[SPEC-SUI-015]`: it reads the same `verdicts.json`, and `--approve-all` records the same verdicts, so no route to a commit skips the record.

**`[SPEC-STAR-118]` Approve all takes every default and records that it did.** It writes `approved`, `by: approve-all`, for each item without a verdict, which is the most recent change, and never replaces a verdict a person has given. The page asks first, naming the counts by kind ("112 conflicts, 4 edits against removals").

## 10. The console page

**`[SPEC-STAR-120]` A Sync page, `/sync`, that drives `star_sync.py` and contains none of it** `[SPEC-SUI-015]`. Each stage is a console job of kind `star-sync` (one at a time, progress over SSE as every job); the stages are those of `[SPEC-STAR-085]`, shown as a strip with the run's state.

```
GET  /sync                          the page
GET  /api/sync/state                each node, the run's stages, what blocks a commit; the hub backup's age
POST /api/sync/stage                run a stage (snapshot|merge|patch|rehearse|commit), as a job
GET  /api/sync/items?kind=&table=&node=&after=    a page of the run's items, names resolved
POST /api/sync/verdicts             {id: verdict, ...}
POST /api/sync/approve-all          every item still without a verdict [SPEC-STAR-118]
GET  /api/sync/summary              SUMMARY.md and the merge's report
```

**`[SPEC-STAR-122]` The list shows every item, one row each, before anything can be committed.** A row gives the kind; what it is in words (an artist, recording or passage by name, resolved from the hub's catalogue, never a bare id); each candidate beside its node and stamp, the default marked; and **Approve** and **Use \<node\>** buttons, as `/mesh` has for a conflict `[SPEC-MESH-065]`. Above it: the counts (pending, approved, changed), the propagated changes by table, filters by kind, table and node, and **Approve all N remaining**. A first run can hold thousands, so the list is paged and the counts cover the whole run.

**`[SPEC-STAR-124]` Commit is a second press, per node, and says what it will do.** The button stays disabled until `[SPEC-STAR-116]`'s gate is met. The first press is the rehearsal; the second commits, naming each node, its backup, and whether its player will be started for it `[SPEC-STAR-101]`. Each node's `RESULT` line is shown as it arrives; a node that is missing or refused is shown as such and the others proceed `[SPEC-STAR-075]`.

**`[SPEC-STAR-126]` The hub step, from inside the console that holds the hub.** `star_sync.py` refuses to patch the hub while a `console.py` or a `lempi.exe` runs, since both open its pair `[SPEC-SUI-012]`, and a commit from the page would refuse itself. So the job does what every console job that writes the live library does: it pauses the co-resident player (`pause_lempi`), puts the console into a sync lock that refuses its own writing routes with 409, and passes its own pid (`--console-pid`), so that this console and the `lempi` it paused are excused and any other `console.py` or `lempi.exe` still refuses. When the hub step ends the lock lifts, the player is asked to reload, and it is resumed only if it was playing.

**`[SPEC-STAR-128]` What the page does not do.** It does not edit `fleet/star-plan.json`, the roster and on-demand commands `[SPEC-STAR-100]`; with no plan it says so and names `fleet-example/star-plan.json`. It does not schedule a sync `[SPEC-STAR-087]`, and the nightly backup stays Task Scheduler's.

**`[SPEC-STAR-130]` A run takes part with the nodes it is given, and every other is left alone.** A node left alone is not read, not started (an on-demand player stays down), not written to, and what it was last sent is kept as it was. `star_sync.py snapshot --nodes A,B` makes the choice, the run records it (`selected.json`), and every later stage stays inside it: a node left alone is not in the run, and naming it is refused. The desktop always takes part. The page asks for the choice and never assumes one: each node has a box to tick, none is ticked until the person ticks it, the choice is remembered in their browser, and a snapshot of no nodes is refused. *Decided 2026-10-05 for the first live run:* the desktop with `lempi02w` and `lp3-wifi`; `bose`, `smartboardpc` and `teacherslounge` left alone, untouched, as the fleet's copies against a failure nobody foresaw.

**`[SPEC-STAR-132]` An old player is said loudly, wherever a sync meets one.** An old player is one whose snapshot reports no `natural_keys` `[SPEC-NKP-075]`: its listener edits still sync, and its catalogue is sent no patch. The snapshot prints a boxed warning and ends its `RESULT` line with `OLD PLAYER(S)`; the run keeps the list (`old_players.json`); `SUMMARY.md` opens with it; a rehearsal or commit says it again before it reaches anyone; and the page shows a red banner, a mark on the node, and a line in the commit confirmation. A player too old to answer the sync routes at all is missing from the run, and its reason says so.

## 11. Open

1. **`[SPEC-STAR-920]` Rolling a signed commit back from the page.** The player keeps each commit's inverse three deep; no command yet reads one for the person.
2. **`[SPEC-STAR-930]` Names for items.** Recording, artist and passage subjects are resolved from the hub's catalogue; a subject it lacks shows its kind and id, marked unresolved.
3. **`[SPEC-STAR-940]` A node whose catalogue is not what the hub last sent it gets no catalogue patch, and says so.** The patch is strict, with the last-sent copy as its baseline, and the node's summary shows whether it still holds that. **Found 2026-10-05, and routine:** all four players hold 49 files and 338 passages the last-sent copies do not, and the hub's own catalogue holds the same music under other ids (node file 5710 is the hub's 5719). They came by the Export page's bundle send `[SPEC-STAR-090]`, whose importer numbers rows as the node's database does, so every send makes the next catalogue patch impossible. The listener half is unaffected and flows. A remedy needs a decision: **(a)** the importer, given the hub's file and passage ids, keeps them where free, so a node ends the send equal to the hub; **(b)** the hub replays the sent bundles on a copy of the node's last-sent catalogue to learn what it holds; **(c)** the node reports digests of its catalogue rows, and the hub patches against what the node actually has. **Compared by natural key, the four nodes equal the hub exactly** (5,758 files and 16,999 passages, none node-only, none missing) and differ only in 49 files' ids and so their 338 passages' (2026-10-05). The snapshot now says which of four states a node is in, from its summary: *as last sent*, *holds the hub's new rows already* (both can take the strict patch, and the player's own rehearsal decides), *the hub's music under other ids*, or *changed on the node*. A patch that names rows by natural key, the player resolving its own ids, would reach the third `[SPEC-DF-035]`; it is specified in [SPEC058](SPEC058-catalogue-patch-by-natural-key.md) and not yet built.
4. ~~**`[SPEC-STAR-900]` bose was unreachable** when the first snapshots were taken~~ *(resolved: its snapshot was taken after a power cycle the same day, and is in every merge since)*.
5. ~~**`[SPEC-STAR-910]` Whether plays should one day travel**~~ *(resolved by `[REQ-PD-113]`: they do not)*.

---

**Traceability:** `[SPEC-STAR-010..940]` · from the maintainer's direction of 2026-09-26, and of 2026-10-04 for §§8–10 · applies `[SPEC-DF-055]`, `[SPEC-DF-070]`, `[SPEC-PREF-105]`, `[SPEC-MESH-065]`, `[SPEC-DF-110]`
