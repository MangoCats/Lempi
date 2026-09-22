# SPEC008: Database Schema

**Design Specification — Tier 2**

The `lempi.db` relational model. Reconciles MuLibPlay's six-years-proven structure `[GDE-BMK-020]` with McRhythm's entity definitions `[GDE-MCR-050]` and the identity/portability rules of [SPEC006](SPEC006-data-flow-and-portability.md).

> **Related:** [SPEC023 Domain Vocabulary](SPEC023-domain-vocabulary.md) for what file/passage/recording/release/album/artist/track each mean and do not mean · [GUIDE002 §2.4](../GUIDE002-rearchitecture-plan.md#2-architectural-decisions) · [SPEC005 Flavor Distance](SPEC005-flavor-distance.md) · [SPEC007 Vipunen](SPEC007-vipunen-architecture.md) · inherited [MCR-REQ002 Entities](../inherited/mcrhythm/MCR-REQ002-entity_definitions.md)

---

## 1. Governing Rules

**`[SPEC-SC-010]` `lempi.db` is a cache, never a source** `[SPEC-DF-010]`. Everything except listener state is re-derivable from audio by Vipunen. Deleting the database must cost time, not information.

**`[SPEC-SC-015]` No field without a consumer** `[GDE-FBD-060]`. MuLibPlay carried `tempo`, `intensity`, `keyMood`, `darkLight`, `genre`, `themes` as NULL for all 8,116 rows across six years `[GDE-BMK-040]`. None are reproduced here.

**`[SPEC-SC-020]` Class D is physically segregated.** Listener state lives in tables prefixed `listener_` so the class-D export `[SPEC-DF-090]` is a table-set selection rather than a column-by-column judgement, and so "never travels with music" `[SPEC-DF-055]` is enforced by structure.

**`[SPEC-SC-025]` Provenance is non-nullable wherever a value was derived** `[GDE-FBD-020]`. The failure this prevents — 91% of v1's descriptors being silently inherited rather than computed `[GDE-V1-010]` — was invisible precisely because nothing recorded origin.

---

## 2. Identity Spine

**`[SPEC-SC-030]`** Three scopes, three keys `[SPEC-DF-030]`. Every table binds at exactly one.

```sql
-- Encoding scope: this exact audio. audio_md5 is Essentia's md5_encoded,
-- stable across tag writes [SPEC-DF-020], free at extraction [SPEC-SA-035].
CREATE TABLE files (
    file_id      INTEGER PRIMARY KEY,
    audio_md5    TEXT    NOT NULL UNIQUE,   -- identity; survives move/rename/retag
    path         TEXT    NOT NULL,          -- machine scope: never transported
    size_bytes   INTEGER NOT NULL,
    mtime        REAL    NOT NULL,          -- cheap change detection only
    format       TEXT    NOT NULL,
    duration_ms  INTEGER NOT NULL,          -- decoded, not header-claimed [REQ-LIB-145]
    first_seen   TEXT    NOT NULL,
    last_seen    TEXT    NOT NULL
);
CREATE INDEX files_path ON files(path);

-- Recording scope: this music, any encoding. Portable across installations.
CREATE TABLE recordings (
    mbid       TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    length_ms  INTEGER,
    source     TEXT NOT NULL                -- [SPEC-SC-025]
);
```

**`[SPEC-SC-035]` `path` is deliberately not unique and never a key.** MuLibPlay's ability to relocate a moved library came from matching content, not paths `[GDE-BMK-050]`; that property is preserved by keying on `audio_md5`.

**`[SPEC-SC-037]` `file_tags` is library, not cache — encoding scope, one row per `files.file_id`.** The player resolves a display name MusicBrainz → tag → filename, so for audio with no MusicBrainz entry `file_tags` is the *only* place an artist name exists `[SPEC-PL-050]`; that is also why tags travel in a payload `[SPEC-DF-092]`.

```sql
CREATE TABLE file_tags (
    file_id     INTEGER PRIMARY KEY REFERENCES files(file_id) ON DELETE CASCADE,
    title       TEXT,
    artist      TEXT,
    album       TEXT,
    track_no    INTEGER,
    disc_no     INTEGER,
    has_art     INTEGER NOT NULL DEFAULT 0,
    scanned_at  INTEGER NOT NULL
);
```

---

## 3. Passages — the Album/Radio Duality

**`[SPEC-SC-040]`** A passage is a span of one file with its own playback metadata `[ENT-MP-030]`, `[SPEC023]`. MuLibPlay's best structural idea `[GDE-BMK-030]`: **each recording-in-file span yields two passages, one per `kind`**, and the Program Director selects only `radio`.

```sql
CREATE TABLE passages (
    passage_id     INTEGER PRIMARY KEY,
    file_id        INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    kind           TEXT    NOT NULL CHECK (kind IN ('album','radio')),
    start_ms       INTEGER NOT NULL,
    end_ms         INTEGER NOT NULL,
    lead_in_ms     INTEGER,                  -- computed [SPEC-SA-075]; NULL = not analysed
    lead_out_ms    INTEGER,
    gain_db        REAL,                     -- loudness match; MuLibPlay observed ~0.70-0.75 linear
    boundary_src   TEXT    NOT NULL,         -- 'computed:<algo>@<ver>' | 'manual' | 'imported:<x>'
    CHECK (end_ms > start_ms)
);
CREATE INDEX passages_file ON passages(file_id);
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
```

**`tools/add_fade_columns.py` grows `passages` past this base shape**, the same layering `tools/fetch_releases.py` uses for `releases` (§3b): it idempotently `ALTER TABLE`s in `fade_in_ms`/`fade_out_ms` (`INTEGER NOT NULL DEFAULT 20`) and `fade_in_curve`/`fade_out_curve` (`TEXT NOT NULL DEFAULT 'exponential'`) — this passage's own volume envelope `[SPEC-SC-046]`, described below.

**`[SPEC-SC-043]` Lead durations are normally milliseconds, deliberately.** Across the migrated library the lead-in median is **5 ms** and the lead-out median **946 ms**. **Lead does not itself apply any gain ramp** `[SPEC-SC-046]` — it only times *when* a crossfade with a neighbour is permitted, never *how loud* either side is during it — so these numbers control overlap duration, not loudness shape. Near-zero overlap `[SPEC-DIR-*]` is therefore the intended default, and these values should not be inflated to "enable crossfading": doing so widens the window a crossfade is *allowed* to use, but produces no audible blend on its own. Audible crossfade is the uncommon case, and reaching it needs two things set together, not one: a long-enough `lead_out_ms`/`lead_in_ms` to admit the overlap, *and* a comparably long `fade_out_ms`/`fade_in_ms` `[SPEC-SC-046]` to actually ramp gain across it — every passage's fixed 20 ms fade default will not produce a slow blend even inside a long lead window, since it finishes in the first 20 ms of whatever `lead_out_ms` admits.

Reviewable and overridable, both, through the waveform boundary editor `[SPEC021]`.

**`[SPEC-SC-046]` Fade is orthogonal to lead, and is now the entire audible envelope** `[SPEC-SUI-226]`, per McRhythm's own inherited distinction (`[XFD-ORTH-010]`): `lead_in_ms`/`lead_out_ms` time *when a crossfade with a neighbour is permitted*; `fade_in_ms`/`fade_out_ms` are this passage's *own* volume envelope, applied whether or not anything neighbours it. This supersedes, not supplements, an earlier asymmetry: before this column existed, `lead_in_ms` was genuinely applied as a real fade-in gain ramp and `lead_out_ms` was not applied as one at all — `engine.rs::open()` now builds its `Envelope` entirely from the four fade columns, and `lead_in_ms`/`lead_out_ms` no longer reach the mixer as a gain ramp in any form, on either side. `analyze_amplitude.py` never touches the four fade columns — they carry a fixed default (20 ms, `exponential`), not a computed one, and exist purely so no passage ever starts or ends at an arbitrary, potentially non-zero sample: a hard click at a file boundary, or an abrupt cut into/out of continuous audio (a DAO capture, a live recording) that has no silence of its own to lead into. Always user-editable per side, independently of `lead_in_ms`/`lead_out_ms` and of each other, through the waveform boundary editor `[SPEC021]`.

Not to be confused with `skip_fade_ms`/`skip_lead_ms` `[REQ-AUD-162]` — a third, unrelated use of both "fade" and "lead": live-adjustable Skip parameters held on `Engine` itself (`engine.rs`), never read from or written to `passages`, and never in effect during ordinary playback. `skip_fade_ms` times the outgoing passage's own fade-out; `skip_lead_ms` times when the *incoming* one starts relative to that fade-out — neither is a duration measured from a passage's own start/end the way `lead_in_ms`/`fade_in_ms` are. Only the *local, same-backend* handoff (an emptied queue's fade-out, `[SPEC-BK-033]`) actually runs through these fields; a cross-backend handoff (`Session::hand_over_seamless`, `session.rs`/`switch.rs`) uses its own fixed constants (`lempi.rs`'s `600` ms fade, `HANDOFF_LEAD_MS=250`), not the listener's skip settings at all.

**`[SPEC-SC-047]` What `radio` and `album` actually change.** Both are the *same* recording-in-file span, played back two different ways — see `[SPEC023]` for the term itself:

- **`radio`**: trimmed toward minimal start/end silence, meant for freeform rotation play; `lead_in_ms`/`lead_out_ms` set the timing window in which this passage's tail may overlap the next one's head, and `fade_in_ms`/`fade_out_ms` are the audible ramp, per `[SPEC-SC-046]` above.
- **`album`**: preserves whatever between-track silence the source actually has — commonly present, never guaranteed — the span a listener hears playing straight through. On a DAO capture specifically, `tools/segment_dao.py` deliberately leaves the detected inter-track silence **out** of both neighbouring spans: one passage's `end_ms` is the *start* of the detected silence and the next's `start_ms` is where the silence *ends*, a real gap of at least `MIN_SILENCE_S` (0.5 s), not an abutment — validated this way against 188 files. Playing them in sequence still reproduces the disc as captured, since the gap is exactly the silence a straight-through listen would also have.

Neither `kind` implies anything about whether the underlying recording or file belongs to a catalogued MusicBrainz release — see `[SPEC023]`'s "Album" entry for that distinct, informal sense of the word.

**`[SPEC-SC-045]` `boundary_src` distinguishing `manual` is what makes override durable** `[SPEC-SA-080]`. Recomputation must never overwrite a `manual` row; conflict resolution ranks provenance before recency `[SPEC-DF-070]`.

**`[SPEC-SC-050]` Passage → recording is many-to-many with weights**, because a passage may contain a medley and a recording may appear in many files. Unidentified passages simply have no rows here — legal, and playable `[ENT-MP-035]`.

```sql
CREATE TABLE passage_recordings (
    passage_id  INTEGER NOT NULL REFERENCES passages(passage_id) ON DELETE CASCADE,
    mbid        TEXT    NOT NULL REFERENCES recordings(mbid),
    weight      REAL    NOT NULL DEFAULT 1.0,
    source      TEXT    NOT NULL,
    PRIMARY KEY (passage_id, mbid)
) WITHOUT ROWID;
```

---

## 3b. Artists, Releases and Credits

**`[SPEC-SC-048]`** `artists` and `releases` are MusicBrainz-keyed entities shaped exactly like `recordings` above (`[SPEC-SC-030]`). `recording_artists` is the weighted junction onto them, shaped like `passage_recordings`. `release_recordings` is not weighted — it has no `weight` column at all — because a track's placement on a release is a fact, not a probabilistic credit; it is instead an *ordered* junction (`position`/`chosen`/`disc`, below). Base DDL for all four lives in [`sql/schema.sql`](../../sql/schema.sql) — this section explains what they're for and how they relate, not a second hand-typed copy of the columns, which is exactly how this section went stale before: it previously said "follow the same shape... omitted for length," which stopped being true the moment `release_recordings` grew `position`/`chosen`/`disc`/`track_length_ms` of its own.

**Migrations grow `releases` past its base shape.** `sql/schema.sql` bootstraps a fresh database; `tools/fetch_releases.py` idempotently `ALTER TABLE`s in `release_group`/`status`/`primary_type`/`secondary_types`/`country`/`track_count` the first time a library needs release identification (`[SPEC010 §3]`) — the same layering `tools/add_fade_columns.py` uses for `passages`' own fade columns (`[SPEC-SC-046]`).

**What each junction is for**, in terms `[SPEC023]` (Domain Vocabulary) names precisely:

- **`recording_artists`** — a recording's credit is a *weighted set*, never a single value (`[SPEC023]` Artist): a collaboration or featured credit is a genuine multi-row entry, and `apply_reviews.py --revert-artist` corrects one recording's whole credit set at a time (`[SPEC010 §3]`).
- **`release_recordings`** — one row is Lempi's realization of Track (`[SPEC023]` Track, inherited `[ENT-MB-010]`): a recording's position (`position`/`disc`) on one specific release. `chosen` is the flag SPEC010's release disambiguation writes when a recording sits on more than one catalogued release, which per `[SPEC023]`'s Release entry is the ordinary case, not an anomaly to resolve away.
- **`recording_relations`** — an asymmetric junction (`mbid`, `related_mbid`, `strength`, `source`) recording which recordings block and damp each other in selection, fed into Stage A's related-recording handling `[SPEC-DIR-115]`/`[SPEC-DIR-116]` and loaded by `player/core/src/director/library.rs`.

---

## 4. Flavor

**`[SPEC-SC-060]`** Long and narrow, not 71 columns. Three reasons, each measured: partial vectors are normal (11 dims from `mulib.db`, 71 from the dump, some locally computed); provenance is **per characteristic**, not per track `[GDE-ARC-030]`; and user-defined characteristics `[GDE-MCR-060]` must be addable without DDL changes.

```sql
CREATE TABLE flavor (
    subject_kind    TEXT NOT NULL CHECK (subject_kind IN ('recording','passage')),
    subject_id      TEXT NOT NULL,          -- recordings.mbid | passages.passage_id
    characteristic  TEXT NOT NULL,          -- 'mood_happy', 'genre_dortmund', 'user.christmas'
    class           TEXT NOT NULL,          -- 'happy', 'rock', 'christmasy'
    value           REAL NOT NULL CHECK (value >= 0.0 AND value <= 1.0),
    source          TEXT NOT NULL,          -- [SPEC-SC-025]
    accuracy        REAL,                   -- measured err/beta for this characteristic
    PRIMARY KEY (subject_kind, subject_id, characteristic, class)
) WITHOUT ROWID;
CREATE INDEX flavor_subject ON flavor(subject_kind, subject_id);
```

**`[SPEC-SC-065]` Two subject kinds, deliberately.** Recording-scope flavor is portable and shareable `[SPEC-DF-040]`; passage-scope covers audio with no MusicBrainz identity, which must still be selectable. A passage prefers its own flavor and falls back to its recording's.

**`[SPEC-SC-070]` `accuracy` is defined but has never been populated.** Every writer of this table (`tools/extract_library.py`, `tools/migrate_mulib.py`) hardcodes it `NULL`; it is `NULL` on all 578,452 rows in the migrated library `[SPEC-PL-100]`. It was designed to carry the model's measured error per subject and characteristic, but nothing has done that work. Independent of whether it were populated, it would not feed the distance metric as things stand today: `[SPEC-FD-120]` originally specified scaling `w_c` by it; that premise was superseded once the library moved to uniform-local provenance `[SPEC-FD-150]`, where every recording shares one extractor and there is no per-recording accuracy signal left to weight by. `w_c` in `player/core/src/director/flavor.rs` is a single corpus-wide reliability per characteristic (`[SPEC-FD-052]`), not a per-value one built from this column.

**`[SPEC-SC-075]` Classes of a characteristic must sum to 1.0 ± 1e-4** `[MFL-DEF-040]`. Not expressible as a SQL constraint; enforced on write and audited in bulk. Verified clean on 21,636 of 21,636 instances in the sample dump.

**`[SPEC-SC-076]` `flavor_constants` stores the distance metric's corpus constants, one row per characteristic** (`beta`, `reliability`, `measured_on`, `measured_at`) `[SPEC-FD-052]`. Stored rather than hardcoded because beta is a property of the corpus being searched, not a fixed value — measuring it on the wrong corpus has already produced two wrong answers `[LOG-I6-030]`. Written by `tools/derive_constants.py` and `tools/migrate_mulib.py`, read by `player/core/src/director/flavor.rs` and `shape.rs`.

---

## 5. Derivation Cache

**`[SPEC-SC-080]` The lowlevel cache is the most valuable table in the database** and the reason S4 and S5 are separate stages `[SPEC-SA-025]`. Extraction costs ~27 s/track and is the only step needing audio decode. Improving a classifier must re-run S5 over cached features and **never re-decode a user's library** `[GDE-CHT-045]`.

```sql
CREATE TABLE lowlevel_cache (
    audio_md5   TEXT    NOT NULL,
    start_ms    INTEGER NOT NULL,           -- 0,-1 = whole file; else the passage slice
    end_ms      INTEGER NOT NULL,
    features    BLOB    NOT NULL,           -- compressed Essentia lowlevel JSON
    extractor   TEXT    NOT NULL,           -- 'essentia-v2.1_beta2-1-ge3940c0'
    extracted_at TEXT   NOT NULL,
    PRIMARY KEY (audio_md5, start_ms, end_ms)
) WITHOUT ROWID;
```

Keyed by slice because extraction is per-passage `[SPEC-SA-090]` — **provisional**: that experiment may add a minimum-duration or eligibility rule, which would be an additional column here, not a restructuring.

**`[SPEC-SC-085]`** Identification lookups (`fpcalc` fingerprints, AcoustID and MusicBrainz responses) are cached in the same spirit: the AcoustID key is rate-limited and a single point of failure `[SPEC-SA-055]`, so a re-run must never re-query.

---

## 6. Listener State — Class D

**`[SPEC-SC-090]`** Segregated by prefix `[SPEC-SC-020]`. This is the **only irreplaceable data in the system** `[SPEC-DF-090]`; everything else re-derives from audio.

```sql
CREATE TABLE listener_play_history (          -- 37,134 rows in MuLibPlay since 2020
    play_id     INTEGER PRIMARY KEY,
    played_at   INTEGER NOT NULL,             -- unix seconds
    passage_id  INTEGER REFERENCES passages(passage_id) ON DELETE SET NULL,
    mbid        TEXT,                         -- survives passage deletion
    heard_ms    INTEGER,                      -- how much was heard [REQ-VIS-250]
    span_ms     INTEGER                       -- how long the passage was; both NULL on rows predating the columns
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

`listener_programs` / `listener_program_seeds` hold the time-of-day programs as **seed track lists** `[GDE-PD-040]`, and `listener_likes` holds weighted Like/Dislike events with their timestamps `[GDE-MCR-070]`.

Five more `listener_`-prefixed tables complete Class D. `listener_rejections` records a skip or dequeue only so the same passage is not re-offered immediately `[SPEC-PLAY-050]`; it feeds no ramp, count or artist damping. `listener_settings` is the single-row home for the artist/recording rotation-scale master multipliers and the appliance's `utc_offset_minutes` for wall-clock programme starts `[SPEC-DIR-118]`. `listener_occasions` and `listener_occasion_points` hold seasonal curves as data, not code — a new occasion is rows here plus `flavor` values, never an engine edit `[SPEC-DIR-130]`; `listener_occasions.label` names it for the listener ("Children's", which `user.childrens` cannot spell), and `listener_characteristics` holds a listener's own hand-set value for one, overriding the `flavor` row the migration left rather than merging with it `[SPEC-PREF-085]`. It is listener-side and not a `flavor` row for a reason that is not stylistic: once split, `library.db` is attached read-only by the player `[PI-DB-020]`, so `flavor` is not writable from there at all. `listener_flags` is a plain "look at this" marker a listener sets from a play-history row, carrying no verdict of its own `[REQ-VIS-265]`.

**`[SPEC-SC-095]` `mbid` is denormalised into play history on purpose.** MuLibPlay did the same, and it means six years of history survives a library rescan that renumbers passages.

---

## 6b. Player State

**`[SPEC-SC-098]`** A single row holding the resume point `[REQ-AUD-140]`: passage, position, playing flag, volume.

Deliberately **not** `listener_`-prefixed. It is operational state, not listener history — losing it costs one track position — so it is excluded from the class-D export `[SPEC-DF-090]`, which exists for data that cannot be reconstructed. Mixing it in would dilute the one guarantee that export makes.

`playing` is a boolean because playback has exactly two states `[REQ-AUD-142]`. Pausing halts the consumer only; producers keep filling buffers, so there is no third "stopped" mode to represent.

**`[SPEC-SC-099]` Settings are rows in `player_settings`, not columns beside the resume point.** *(Changed 2026-08-22.)* They began as columns on `player_state`, one added by `ALTER` as each was invented, and read back **by position** — `?11` in the insert, `r.get(10)` in the select. Adding a setting meant renumbering both lists and the `ALTER` list beside them, and getting it wrong loaded the wrong value **silently** rather than failing.

`player_settings(key, value, updated_at)` removes the positions. One list of keys is the source, and a round-trip test asserts that what is written is what is read, so a setting added to the struct and forgotten in the reader fails a test instead of losing itself at the next restart.

The keys keep the old column names, so a database written before this carries over without a translation table — done once, on open, and skipped forever after.

---

## 7. Visibility

**`[SPEC-SC-100]`** `ingest_decisions` records what each Vipunen stage decided, at what confidence, and what it rejected — a durable record, not a log line `[SPEC-SA-085]`. `selection_decisions` records the Program Director's weight decomposition per choice, which is what the "Why this passage?" panel reads `[GDE-CHT-030]`.

Both are append-only, intended to be bounded by retention (policy still open, `[SPEC-SC-120]` — no pruning exists yet, so both currently grow unbounded), and are **Lempi-local**: they describe process, not music, so they never travel `[SPEC-DF-050]`.

---

## 8. Open

1. **`[SPEC-SC-110]`** Per-passage extraction may add a minimum-duration or flavor-eligibility column `[SPEC-SA-090]`. Additive.
2. **`[SPEC-SC-120]`** Retention policy for the two decision tables.

**`[SPEC-SC-115]` Cover art storage — decided and built.** BLOBs, the MuLibPlay
approach (`[GDE-BMK-030]`; MuLibPlay held them in `albums`, 95 MB db, mostly
art), not the filesystem cache once floated here — but keyed by **release
MBID** in its own table, not folded into `albums`:

```sql
CREATE TABLE cover_art (
    release_mbid TEXT PRIMARY KEY,
    front        BLOB,
    back         BLOB,
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
);
```

Built by `tools/fetch_cover_art.py`; not yet emitted in payloads `[SPEC-PL-105]`.

---

**Traceability:** `[SPEC-SC-010..120]` · derived from `[GDE-ARC-040]`, `[GDE-BMK-030]`, `[SPEC-DF-030]`, `[SPEC-SA-025]`
