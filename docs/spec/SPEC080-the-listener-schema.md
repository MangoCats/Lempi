# SPEC080: The Listener Schema

**Design Specification — Tier 2 · split from [SPEC008](SPEC008-database-schema.md) 2026-11-17, whose §6–7 it was, when that document had reached its 300-line limit while still describing the listener by passage number**

The listener half of `lempi.db`: `listener.db`, beside the catalogue `library.db` on every installation `[IMPL-DBSPLIT-025]`. What the listener has heard and decided, the player's own operational state, and the record of why each passage was chosen. [SPEC008](SPEC008-database-schema.md) holds the catalogue and the rules both halves follow.

> **Related:** [SPEC008](SPEC008-database-schema.md) (the catalogue, and the governing rules `[SPEC-SC-010]`..`[SPEC-SC-025]`) · [IMPL023](../IMPL023-permanent-identity.md) §6 (Stage B, which renamed every passage here by its uid) · [SPEC046](SPEC046-star-sync.md) (how these tables travel between nodes) · [`sql/schema.sql`](../../sql/schema.sql) and `player/core/src/db/player_store.rs` (the DDL itself)

---

## 1. Listener State — Class D

**`[SPEC-SC-090]`** Segregated by prefix `[SPEC-SC-020]`. This is the **only irreplaceable data in the system** `[SPEC-DF-090]`; everything else re-derives from audio.

```sql
CREATE TABLE listener_play_history (          -- 37,134 rows in MuLibPlay since 2020
    play_id     INTEGER PRIMARY KEY,
    played_at   INTEGER NOT NULL,             -- unix seconds
    passage_uid TEXT,                         -- the passage by its uid [SPEC-SC-092]; NULL if it had none
    mbid        TEXT,                         -- survives the passage's deletion [SPEC-SC-095]
    heard_ms    INTEGER,                      -- how much was heard [REQ-VIS-250]
    span_ms     INTEGER,                      -- how long the passage was; both NULL on rows predating the columns
    selected_by TEXT                          -- who or what chose it [REQ-VIS-300]; added on open by PlayerStore, not schema.sql
);
CREATE INDEX listener_play_time ON listener_play_history(played_at);
CREATE INDEX listener_play_mbid ON listener_play_history(mbid);

CREATE TABLE listener_preferences (           -- MuLibPlay's rotation/recovery/restraint
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('recording','artist')),
    subject_id   TEXT NOT NULL,
    rotation     REAL,                        -- log scale: 10^v hours [GDE-PD-010]
    recovery     REAL,
    restraint    REAL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id)
) WITHOUT ROWID;
```

`listener_programs` / `listener_program_seeds` hold the time-of-day programs as **seed track lists** `[GDE-PD-040]`; `listener_programme` names the programme those slots make, its fingerprint and whether it is the hub's version or the node's own, and `listener_programme_pending` holds one the hub sent that has not been answered — both made by the player, the second at runtime only `[SPEC-PGM-200]`. `listener_likes` holds weighted Like/Dislike events with their timestamps `[GDE-MCR-070]`.

Six more `listener_`-prefixed tables complete Class D. `listener_rejections` records a skip or dequeue only so the same passage is not re-offered immediately `[SPEC-PLAY-050]`; it feeds no ramp, count or artist damping. `listener_settings` is the single-row home for the artist/recording rotation-scale master multipliers and the appliance's `utc_offset_minutes` for wall-clock programme starts `[SPEC-DIR-118]`. `listener_occasions` and `listener_occasion_points` hold seasonal curves as data, not code — a new occasion is rows here plus `flavor` values, never an engine edit `[SPEC-DIR-130]`; `listener_occasions.label` names it for the listener ("Children's", which `user.childrens` cannot spell), and `listener_characteristics` holds a listener's own hand-set value for one, overriding the `flavor` row the migration left rather than merging with it `[SPEC-PREF-085]`. It is listener-side and not a `flavor` row for a reason that is not stylistic: once split, `library.db` is attached read-only by the player `[PI-DB-020]`, so `flavor` is not writable from there at all. `listener_flags` is a plain "look at this" marker a listener sets from a play-history row, carrying no verdict of its own `[REQ-VIS-265]`; a passage's flag names it by its uid.

**`[SPEC-SC-095]` `mbid` is denormalised into play history on purpose.** MuLibPlay did the same, and it means six years of history survives a library rescan that renumbers passages.

Three more tables hold a listener's decisions about the catalogue, made by `PlayerStore` on open and folded into the library by Vipunen's tools, never by the player: `id_reviews` (a passage's recording kept or reassigned), `boundary_reviews` (its span redrawn) and `artist_reviews` (a recording's credited artist corrected, with the passage it was made from). They travel by star sync, with their `origin` `[SPEC-DF-104]`.

---

## 2. A Passage by Its Uid, Never Its Number

**`[SPEC-SC-092]` Every listener table that names a passage names it by `passage_uid`,** the identity the catalogue gives it and the same on every node `[SPEC-UID-030]`: `listener_play_history`, `listener_rejections`, `selection_decisions`, `player_state`, `player_queue`, `id_reviews`, `boundary_reviews` and `artist_reviews`, and `listener_flags` where `subject_kind = 'passage'` `[IMPL-UID-060]`. None holds `passage_id`. A passage's number is its own catalogue's `[SPEC-DF-035]` — the hub's 16407 is other music on `bose` — and these tables are read beside other catalogues: by the merge, and in a copy restored on another node.

- **No second source.** The number column is gone rather than kept beside the uid ([GOV002](../GOV002-sources-of-truth.md)). A reader joins `passages` on `passage_uid`, through the unique index `passages_uid` `[IMPL-UID-056]`; a writer finds the uid in the same statement that writes the row.
- **No uid is a NULL, never a guess.** A play, a skip or a decision whose passage has no uid keeps NULL and its `mbid`, since a play is never refused; a review is refused; a queue entry is left out, since it is nothing but its passage. Such a passage, brought only by a bundle from before Stage A, has no rotation of its own and holds its node's catalogue out of star sync until it has a uid, which nothing yet gives it `[IMPL-UID-061]`.
- **A listener from before Stage B is converted on open**, against its own catalogue, by an exact join, in one transaction after a copy of the whole file `[IMPL-UID-062]`. What to convert is decided under the write lock, so two opens meeting on one listener convert it once `[IMPL-UID-067]`.
- **`passage_lyrics` is keyed by the save, not the passage:** `(audio_md5, kind, start_ms)`, with a `passage_uid` column saying which passage the words were saved for `[IMPL-UID-063]`.
- **Enforced at commit.** `tools/check_local_ids.py` refuses an added listener column naming a passage by number `[IMPL-UID-082]`.

---

## 3. Player State

**`[SPEC-SC-098]`** A single row, `player_state`, holding the resume point `[REQ-AUD-140]`: the passage, by its uid, its position, the playing flag and the volume.

Deliberately **not** `listener_`-prefixed. It is operational state, not listener history — losing it costs one track position — so it is excluded from the class-D export `[SPEC-DF-090]`, which exists for data that cannot be reconstructed. Mixing it in would dilute the one guarantee that export makes.

`playing` is a boolean because playback has exactly two states `[REQ-AUD-142]`. Pausing halts the consumer only; producers keep filling buffers, so there is no third "stopped" mode to represent.

**`[SPEC-SC-099]` Settings are rows in `player_settings`, not columns beside the resume point.** *(Changed 2026-08-22.)* They began as columns on `player_state`, one added by `ALTER` as each was invented, and read back **by position** — `?11` in the insert, `r.get(10)` in the select. Adding a setting meant renumbering both lists and the `ALTER` list beside them, and getting it wrong loaded the wrong value **silently** rather than failing.

`player_settings(key, value, updated_at)` removes the positions. One list of keys is the source, and a round-trip test asserts that what is written is what is read, so a setting added to the struct and forgotten in the reader fails a test instead of losing itself at the next restart.

The keys keep the old column names, so a database written before this carries over without a translation table — done once, on open, and skipped forever after.

Three more operational tables live in `listener.db` beside `player_state`, created on open by `PlayerStore` rather than by `sql/schema.sql`: `player_settings`; `player_queue`, the saved queue as passage uids in order `[SPEC-DIR-225]`; and `file_checks`, what this machine last found reading each of its own files back `[GDE-INT-130]` — that disk's own, never merged by star sync.

---

## 4. Visibility

**`[SPEC-SC-100]`** `ingest_decisions` records what each Vipunen stage decided, at what confidence, and what it rejected — a durable record, not a log line `[SPEC-SA-085]`. `selection_decisions` records the Program Director's weight decomposition per choice, naming the passage chosen by its uid, which is what the "Why this passage?" panel reads `[GDE-CHT-030]`.

Both are append-only, intended to be bounded by retention (policy still open, [SPEC008](SPEC008-database-schema.md) `[SPEC-SC-120]` — no pruning exists yet, so both currently grow unbounded), and are **Lempi-local**: they describe process, not music, so they never travel `[SPEC-DF-050]`.

---

**Traceability:** `[SPEC-SC-090..100]` · split from [SPEC008](SPEC008-database-schema.md) · built by `[IMPL-UID-060]`, `[IMPL-UID-062]`, `[IMPL-UID-063]`, `[IMPL-UID-067]`
