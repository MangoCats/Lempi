# SPEC058: The Catalogue Patch by Natural Key

**Design Specification — Tier 2 · written 2026-10-05 · built 2026-10-05, parts included, tested on fixtures and the real catalogues, not yet applied to a node**

The star sync's catalogue patch names a row by its numeric key, and a numeric key is local to one database `[SPEC-DF-035]`. Music the Export page sends a node is imported under the *node's* numbering, so from then on the hub and that node hold the same music under different ids, and the strict patch cannot be applied to it `[SPEC-STAR-940]`. This specifies a patch that names rows by what identifies them on every installation, so that a node's numbering stops mattering.

> **Related:** [SPEC046](SPEC046-star-sync.md) `[SPEC-STAR-940]` (the finding) · [SPEC054](SPEC054-mesh-without-ssh.md) `[SPEC-NSH-050..080]` (the transport and the strict patch this changes) · [SPEC006](SPEC006-data-flow-and-portability.md) `[SPEC-DF-035]` (a local sequence number may not cross an installation) · `player/core/src/mesh_sync.rs` · `tools/star_patch.py`

---

## 1. What was found

**`[SPEC-NKP-010]` The nodes and the hub differ in ids alone, measured 2026-10-05.** By `audio_md5`, and a passage's `(audio_md5, kind, start_ms, end_ms)`, each of the four players holds exactly the hub's 5,758 files and 16,999 passages: none a node alone has, none the hub alone has. 49 files differ in `file_id`, and so do their 338 passages, because a bundle imports in the order it arrives and numbers as the node's database counts. The four nodes agree with each other.

**`[SPEC-NKP-015]` Compared row for row with those ids replaced by natural keys** (the hub against `bose`, over the 49 files and everything that refers to them): `passages` and `passage_recordings` are **identical**, 338 rows each. `files` differs in `path`, `last_seen` and **`first_seen`**. The first two are already machine-scope `[SPEC-DF-030]`; `first_seen` is the time *this installation* first saw the file, which is machine-local by meaning and is not yet listed. A strict "the row already holds its target" would fail on it.

**`[SPEC-NKP-020]` Only six catalogue tables hold ids**, read from the hub's catalogue:

| table | rows | its own id | refers to |
| :--- | ---: | :--- | :--- |
| `files` | 5,758 | `file_id` | |
| `passages` | 16,999 | `passage_id` | `file_id` |
| `passage_recordings` | 16,999 | | `passage_id` |
| `id_checks` | 8,273 | | `passage_id` |
| `file_tags` | 5,709 | | `file_id` |
| `flavor` | 590,101 | | `subject_id` where `subject_kind = 'passage'` |

Every `flavor` row today is keyed by a recording, so it holds no id; the schema allows a passage subject and the vocabulary below covers it. Every other catalogue table is keyed by an MBID or a name, and its patch is unchanged.

## 2. The natural keys

**`[SPEC-NKP-030]` Two kinds of id, and what each is on every installation** `[SPEC-DF-035]`:

- a **file** is its `audio_md5`, which is `UNIQUE NOT NULL`;
- a **passage** is `(audio_md5, kind, start_ms, end_ms)`, which `passages_span` makes unique for a file.

The vocabulary is fixed, in Rust and in `star_patch.py`, and a test holds the two equal. A patch names, for each table, which columns are `file` or `passage` references and which column is the table's own surrogate; a node refuses any other word, as it refuses an identifier that is not one `[SPEC-NSH-070]`.

**`[SPEC-NKP-035]` The N-form of a row** is the row with each reference replaced by its natural value and its own surrogate left out. `passages` row 16716 is `{file: md5, kind, start_ms, end_ms, lead_in_ms, …}`; a `passage_recordings` row is `{passage: [md5, kind, start, end], mbid, weight, source}`. Two catalogues numbered differently have the same N-form for the same music.

## 3. The patch

**`[SPEC-NKP-040]` An entry for an id-bearing table says so, and carries N-form values.** Beside `name`, `columns` and `key`, it names `natural: {surrogate, refs}`: for `passages`, `surrogate: "passage_id"` and `refs: [{"col": "file_id", "kind": "file"}]` (a reference may add `when: [column, word]`, for `flavor`'s subject). Its rows carry N-form values, and no value for the surrogate. It may carry `add_columns`, as an id-keyed entry does: the node adds the column in the same transaction, a rehearsal rolls it back, and the undo drops it. *(Found on the real data: the hub's `passages` had gained a column since the last sync.)* A row's `key` is the identity the node looks it up by: the baseline row's natural key for a row that changes or goes, the target's for one that is new. `was`, `now` and `also` are as before `[SPEC-NSH-070]`.

**`[SPEC-NKP-045]` The hub pairs rows by its own id, and a changed key is a change, not a delete and an add.** The baseline (the copy last sent) and the target are both numbered by the hub, so a row in both is one row whatever it is now called. A passage whose boundaries were edited, or a file the hub re-keyed `[SPEC-RLK-155]`, is sent as a change: `key` the old natural key, `now` the new values. The node then updates in place and **keeps its own id for it**, and with it everything of its own that points there. Deleting and re-adding would orphan its listener rows. A child row is sent only when a column other than its reference changed, since its reference still names the same row on the node.

**`[SPEC-NKP-050]` `first_seen` joins the machine-scope columns** (`path`, `size_bytes`, `mtime`, `last_seen`, and `first_seen`) in `lempi-core`'s `MACHINE_SCOPE` and in `star_merge.py`, held equal by the existing test. It travels in neither direction, and a new row takes the hub's value as `also`, as the others do.

## 4. The node applies it

**`[SPEC-NKP-060]` Strict, in two phases, in one transaction.** *Check:* each row's `key` is resolved to a local row in the database as it is before the patch, by `audio_md5` for a file and by `passages_span` for a passage. A row is **already** if the row at its target's natural key holds `now`; otherwise it is **to apply** if the resolved row's N-form, read back by the same vocabulary, equals `was`; otherwise the whole patch is refused and nothing is written, as today. *Write:* in the patch's table order, parents first, updates and deletes by the local key found in the check, and inserts with each reference resolved by natural key once its parent exists, the surrogate left to SQLite.

**`[SPEC-NKP-065]` Comparison excludes the machine-scope columns,** as it does for the strict patch now, so a node's own path, sizes and times never read as a difference.

**`[SPEC-NKP-070]` The inverse stays in the node's own ids.** What the apply wrote is recorded as local keys and local values, so `mesh_sync::undo` and the three-deep keep `[SPEC-NSH-080]` are unchanged.

**`[SPEC-NKP-075]` A player that does not know this is not sent it.** `snapshot` reports `natural_keys: 1`. The hub sends a natural entry only to a node that does; to any other it keeps today's behaviour, and says which state the node is in `[SPEC-STAR-940]`. An old player is never handed a patch it would misread.

## 5. The hub

**`[SPEC-NKP-080]` `star_patch.make_values` gains the natural mode for the six tables,** driven by one registry that is the Rust vocabulary's twin. The summary check `[SPEC-STAR-940]` then treats `other-ids` as patchable, as it does `as-sent` and `holds-hub-rows`, for a node that reports the capability. `changed` is still not: a node holding music the hub lacks `[SPEC-STAR-048]` is a person's question, not a patch's.

**`[SPEC-NKP-078]` Foreign keys are checked when the patch is whole.** A patch applies its tables in the order it names them, alphabetically, and a player's SQLite enforces foreign keys by default, so a child table can come before the rows it points at: `release_recordings` before `releases`, the refusal that ended the second live rehearsal. The apply defers foreign keys to the end of the transaction, in a single request, across parts and in the undo, and refuses a patch only if it leaves more dangling references in the tables it names than it found. A reference the node had already broken is not the patch's to answer for.

**`[SPEC-NKP-085]` What a first patch to the four nodes does** is nothing but record the facts: the 49 files, 338 passages and 338 passage-recordings the hub sent as new are *already* there, each by natural key, and the node's baseline becomes the hub's copy. The catalogue edits made at the hub since 2026-09-28 follow in the same patch.

**`[SPEC-NKP-082]` A column that is the hub's own bookkeeping travels in neither direction.** Unlike a machine-scope column `[SPEC-NKP-050]`, whose value a node's new row takes from the hub, the node needs no value and not even the column: the builder's `omit` drops it from the comparison, from the columns carried, from `add_columns` and from `also`. Today that is `cover_art.caa_asked_at`, when Vipunen last asked the Cover Art Archive, which only `fetch_cover_art.py` reads. **Found live, 2026-10-05:** the first rehearsal against `lp3-wifi` was refused over a `cover_art` row identical to the hub's in everything but that column, which the node did not yet have and so read as NULL against the hub's value; omitting it also took 52 MB out of the patch, which had been carrying every cover row's images because the new column made each one differ. What remains, 92 MB, is real: 258 covers re-fetched and 58 new.

## 6. What it does not do

- It does not make ids equal, and nothing depends on their being so. A node's numbering is its own.
- It does not reach a node that holds music the hub does not `[SPEC-STAR-048]`, nor repair a node whose rows differ in more than ids.
- It does not touch the listener half, whose passage rows are already translated through the node's summary `[SPEC-STAR-049]`.
- It does not move the 590,101 `flavor` rows or any table keyed by an MBID.

## 7. Proof, and the order to build it in

**`[SPEC-NKP-090]` Each claim has a test before any node is touched.**
1. *Rust:* a patch applied to a catalogue numbered differently gives the same N-form as the target; a boundary edit updates in place and keeps the local id; one row not as expected writes nothing, in either phase; `undo` puts it back; an unknown reference kind is refused.
2. *Python:* the builder's patch, applied by a reference implementation to a permuted copy of a catalogue, gives the target's N-form; a changed key is sent as a change.
3. *Across the two:* one fixture of patch and catalogues under `fixtures/`, applied by both, as the snapshot fixtures are `[GDE-HST-060]`.
4. *The real data:* `rehearse`, which writes nothing, against the four players: expect 49, 338 and 338 rows *already*, and no conflict. Then the first signed commit, attended `[SPEC-STAR-102]`.

**Built 2026-10-05:** steps (1) to (5), in `mesh_sync.rs` (17 tests, among them one that applies the Python builder's patch from the fixture), `star_patch.py` and `test_natural_patch.py`; the hub's state check, summary and page; and a loud warning for any player that reports no `natural_keys` `[SPEC-STAR-132]`. Step (6) waits on a current player on a node, which none yet is. **Parts `[SPEC-NKP-920..940]`, built the same day:** the player's `stage` and `staged` steps and a commit from parts (nine node tests, run on Linux as well as Windows), `apply_parts` and `undo_parts` in `mesh_sync.rs`, and the hub's splitter, which cuts the real 144 MB patch into 21 parts of at most 8 MB, the natural entries first and whole (4.7 MB), every one of the 37,509 rows once.

**Order.** (1) `first_seen` as machine-scope, both lists, with the test. (2) The Rust resolve and apply, with its tests. (3) The capability in `snapshot`. (4) `make_values` in natural mode and the registry, with the Python tests and the shared fixture. (5) The hub's state check, the summary and the page. (6) The rehearsal against the nodes. Each step is useful alone and leaves the sync no worse: until (5) a node is simply sent no catalogue patch, as now.

## 8. Sending a patch in parts

**`[SPEC-NKP-920]` A catalogue patch over 16 MB is sent in parts, staged on the node and committed in one transaction.** Found on the real data: the patch from `bose`'s last-sent catalogue to the hub's now is **144 MB**, 76% of it cover art (590 images, 110 MB), `ingest_decisions` 12 MB, `musicbrainz_cache` and `lowlevel_cache` 8 MB each, the rest about 7 MB. A player's request limit is 64 MB and a Pi has little memory to parse one in. Parts are not separate commits, since a node never keeps half a switch `[SPEC-NSH-080]`, and no transaction can stay open across requests. Each part is **staged**: stored on the node, checked against its hash. The commit then applies every part, in order, in **one SQLite transaction**, so one row not as expected writes nothing from any part.

**`[SPEC-NKP-925]` Two new steps, and two that change.** `stage` carries one part: the run, its number and the count, the SHA-256 of its text *as sent* (the patch is a string, so nothing depends on how a number is spelled), the total of all parts, and the text. The node checks the hash, refuses a part for a run no newer than the last it committed, and stores it atomically under `staged-<run>/`. `staged` says which parts of a run the node holds, by digest, so a rehearsal's upload is not repeated by the commit. `rehearse` and `commit` name `catalogue_parts: {of, sha256: [...]}` in place of `catalogue_patch`, and apply what is staged, each part checked against its digest again. All are signed, fresh and bound to the run, like every step `[SPEC-NSH-030]`.

**`[SPEC-NKP-930]` How the hub cuts.** Every part is at most 8 MB of JSON. The natural entries `[SPEC-NKP-040]` stay whole in the first part, so that the check of §4, which sees the database as it was before the patch, still sees all of them; they come to about 5 MB, and a patch whose natural entries alone pass 32 MB is not sent, and says so. Every other table is cut by rows, in table order; a table's `add_columns` ride in the first part that names it. A patch of 16 MB or less is one request, as before.

**`[SPEC-NKP-935]` The node's memory and disk are bounded, and it says so when they are not.** *(On `lp3-wifi`, 2026-10-05: 2.4 GB free on `/var/lempi` against about 0.3 GB to stage and keep, 905 MB of memory against a part of 8 MB.)* One part is parsed at a time. The inverse of each part is written to disk as it is made, not kept in memory. Before it takes a first part the node checks that its state partition has twice the staged total and 256 MB to spare, and refuses with the figures if it has not. Staged parts are removed when their run commits, and any for a run no newer than the last committed, or a day old, at the next step.

**`[SPEC-NKP-940]` The undo of a multi-part commit is one transaction as well.** The parts' inverses are kept as `inverse-<run>-c<n>.json` beside `inverse-<run>.json`, which lists them, and applied in reverse. The keep of three counts runs, not files. The first patch after a long gap costs the disk its size twice over, staged and inverse; syncing often keeps patches small.

## 9. Open

1. **`[SPEC-NKP-900]` How long the check takes on the smallest node.** *Measured on `lp3-wifi` (a Pi 3 B), 2026-10-05, with the player playing:* a rehearsal of the 14-part, 92 MB patch took two minutes from the first part staged to the answer, 14 parts staged and every part checked in one transaction; it found 21,937 rows to apply, 15,298 already there and none in conflict. The player's memory use stayed at about 173 MB with 530 MB available, its load about 1, and its journal for the window shows no underrun, error or recovery. Still to measure: a **commit**, which writes, and `lempi02w`, a Pi Zero 2 W, which `[SPEC-NSH-900]` asks of the catalogue commit.
2. **`[SPEC-NKP-910]` A node that lacks `passages_span`.** The lookup is still correct without the index, only slower; whether to refuse instead is decided when a node is found without it.

---

**Traceability:** `[SPEC-NKP-010..940]` · answers `[SPEC-STAR-940]` · applies `[SPEC-DF-035]`, `[SPEC-DF-030]` · changes `[SPEC-NSH-070]` only for the six tables of `[SPEC-NKP-020]`
