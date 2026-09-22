//! The one writable connection to `lempi.db` `[REQ-VIS-155]`.
//!
//! Everything that writes -- the resume row, settings, play history,
//! rejections, flags, and (behind `vipunen-support`) the listener's own pending
//! review edits -- goes through [`PlayerStore::open`], because this is the
//! player's only writable handle. Table creation for the rest of the schema
//! happens here too, for the same reason: the read path in
//! [`super::library`] must never meet a table nobody has created yet
//! `[REQ-VIS-180]`.

use rusqlite::Connection;

use super::{BUSY_WAIT, DbError, QualifyingConn};

/// The tag index, defined once `[REQ-VIS-180]`.
///
/// Created by whoever holds a writable handle: `tagscan` when it fills it, and
/// the player at startup when it does not exist at all. **Browsing joins this
/// table, so a missing one is not an empty result but a failed query** -- the
/// first version shipped without this and every browse page came up blank on a
/// library that had never been scanned.
pub(crate) const TAG_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS file_tags (
        file_id INTEGER PRIMARY KEY,
        title TEXT, artist TEXT, album TEXT,
        track_no INTEGER, disc_no INTEGER,
        has_art INTEGER NOT NULL DEFAULT 0,
        scanned_at INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_file_tags_album ON file_tags(album);
    CREATE INDEX IF NOT EXISTS idx_file_tags_artist ON file_tags(artist);";

/// Where plays are written `[SPEC-PLAY-010]`.
///
/// Created by the player for exactly the reason `REJECTION_TABLE` is: the
/// player is what **writes** here, on every play, and those writes are
/// best-effort by design. A library that reached this build without Vipunen
/// having run its schema would have failed every one of them as a log line
/// nobody reads — losing the only irreplaceable data in the system
/// `[SPEC-DF-090]` while appearing to work.
pub(crate) const PLAY_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS listener_play_history (
        play_id     INTEGER PRIMARY KEY,
        played_at   INTEGER NOT NULL,
        passage_id  INTEGER,
        mbid        TEXT,
        -- How much of the passage was heard, and how long it was, in ms
        -- `[REQ-VIS-250]`. Absent on rows written before this column existed --
        -- a NULL history page reads as \"unknown\", never as 0%.
        heard_ms    INTEGER,
        span_ms     INTEGER);
    CREATE INDEX IF NOT EXISTS listener_play_time ON listener_play_history(played_at);
    CREATE INDEX IF NOT EXISTS listener_play_mbid ON listener_play_history(mbid);";

/// What the listener declined, and how `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]`.
///
/// Created by the player rather than left to Vipunen's schema pass, for the same
/// reason as `PLAY_TABLE`: the player is the one **writing** here, on
/// every rejection, and an existing library that predates this feature would
/// otherwise fail every write. Those writes are best-effort by design, so the
/// failure would be a log line and a suppression that silently never happened.
pub(crate) const REJECTION_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS listener_rejections (
        rejection_id INTEGER PRIMARY KEY,
        rejected_at  INTEGER NOT NULL,
        kind         TEXT NOT NULL,
        passage_id   INTEGER,
        mbid         TEXT,
        -- Only ever set for 'skip' -- a 'dequeue' never sounded, so it has no
        -- percentage to report `[REQ-VIS-250]`.
        heard_ms     INTEGER,
        span_ms      INTEGER);
    CREATE INDEX IF NOT EXISTS listener_reject_mbid
        ON listener_rejections(mbid, kind);";

/// "Please look at this" from the listener's own chair `[REQ-VIS-265]`, set
/// and cleared from the play-history page at any time -- a plain flag, not a
/// judgement, so it carries no verdict of its own. Keyed the way `flavor`
/// already is: a recording when the play had one, a passage when it did not,
/// because a passage worth flagging is often exactly the one with no MBID yet.
pub(crate) const FLAGS_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS listener_flags (
        subject_kind TEXT NOT NULL CHECK (subject_kind IN ('recording','passage')),
        subject_id   TEXT NOT NULL,
        flagged_at   TEXT NOT NULL,
        origin       TEXT,
        PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID;";

/// MuLibPlay's own `rotation`/`recovery`/`restraint`, migrated forward
/// unchanged `[SPEC-DIR-115]`, `[REQ-VIS-290]` -- already in `sql/schema.sql`'s
/// base bootstrap, but ensured here too, the same defensive posture
/// `FLAGS_TABLE` above already takes toward every table this handle
/// touches: created because this is the player's only writable handle, so
/// the read path never meets a missing table regardless of how the
/// database was first created.
/// `listener_preferences.rotation`/`recovery`/`restraint`, `None` per field
/// meaning "not tuned, use the default" -- [`PlayerStore::get_preference`]'s
/// own return shape, named rather than a bare tuple so a caller reads
/// `.rotation` instead of `.0`.
#[derive(Debug, Clone, Copy, Default, PartialEq, serde::Serialize)]
pub struct PreferenceRow {
    pub rotation: Option<f64>,
    pub recovery: Option<f64>,
    pub restraint: Option<f64>,
}

/// One "special" a recording can be tagged with `[SPEC-PREF-080]` --
/// MuLibPlay's Christmas/Winter/Summer/Children's occasions and its profanity
/// slider, as [`PlayerStore::list_specials`] reports them.
///
/// Two values, never merged into one: `inherited` is what `flavor` carries
/// (six years of MuLibPlay tagging, or whatever Vipunen derived), `value` is
/// what this listener set by hand and `None` when they never did. Keeping
/// them apart is what lets the panel say "1.0, inherited" rather than
/// implying a person chose it, and what lets Reset mean "go back to the
/// inherited value" rather than "go to zero".
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct SpecialRow {
    pub characteristic: String,
    pub class: String,
    /// What a person is offered this as -- `listener_occasions.label` where
    /// one is set, the characteristic's own last segment otherwise.
    pub label: String,
    pub inherited: Option<f64>,
    pub value: Option<f64>,
}

/// What a subject is actually called `[SPEC-PREF-090]`, for the preference
/// panel's own heading: a recording's title with its credited artist and the
/// release it came from, or an artist's name alone.
///
/// Answered by the server rather than passed in by whichever surface was
/// clicked, because those surfaces do not all know the same things -- the
/// queue carries no album, the Lempi skin's runner-up list carries only a
/// title -- and the heading should not be better or worse depending on where
/// the panel was opened from.
#[derive(Debug, Clone, Default, PartialEq, serde::Serialize)]
pub struct SubjectNaming {
    pub title: Option<String>,
    pub artist: Option<String>,
    pub album: Option<String>,
}

/// One row of the play-frequency table `[REQ-VIS-300]`: a label ("All",
/// "User", or a Program Director program's own name) and a play count for
/// each of the five windows [`PlayerStore::play_frequency`] reports,
/// narrowest-first: past 24h, past 7d, past 30d, past 365d, all time.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct FrequencyRow {
    pub label: String,
    pub counts: [i64; 5],
}

pub(crate) const PREFERENCES_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS listener_preferences (
        subject_kind TEXT NOT NULL CHECK (subject_kind IN ('recording','artist')),
        subject_id   TEXT NOT NULL,
        rotation     REAL,
        recovery     REAL,
        restraint    REAL,
        updated_at   TEXT NOT NULL,
        PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID;";

/// The listener's own hand-set characteristic values `[SPEC-PREF-085]` --
/// the "special" tags MuLibPlay let a person set directly (its `occasions`
/// string, and its profanity slider) and Lempi carried without a way to set.
///
/// Listener-side, not a `flavor` row, and not by preference: once split,
/// `library.db` -- which owns `flavor` -- is ATTACHed **read-only** through
/// this very connection `[PI-DB-020]`, so the player physically cannot write
/// that table on the installation this feature matters most on. It is the
/// right side on the merits too: a person's own judgment about their own
/// music is Class D, like every other `listener_*` table here.
pub(crate) const CHARACTERISTICS_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS listener_characteristics (
        subject_kind   TEXT NOT NULL CHECK (subject_kind IN ('recording')),
        subject_id     TEXT NOT NULL,
        characteristic TEXT NOT NULL,
        class          TEXT NOT NULL,
        value          REAL NOT NULL CHECK (value >= 0.0 AND value <= 1.0),
        updated_at     TEXT NOT NULL,
        PRIMARY KEY (subject_kind, subject_id, characteristic, class)) WITHOUT ROWID;";

/// Give an existing `listener_occasions` the display label `[SPEC-PREF-080]`
/// reads. Same shape and same expected-failure path as `ensure_flags_columns`
/// below: the `CREATE TABLE IF NOT EXISTS` in `schema.sql` is a no-op against
/// a database that already has the table, so the column has to be added
/// separately, and the add fails on every start after the first.
///
/// The player never writes this column -- `tools/load_occasions.py` does --
/// but it has to exist for `list_specials` to select it, on every
/// installation rather than on whichever ones a tool happened to reach.
fn ensure_occasion_columns(conn: &Connection) {
    let _ = conn.execute("ALTER TABLE listener_occasions ADD COLUMN label TEXT", []);
}

/// What to call a special whose `listener_occasions` row carries no label
/// `[SPEC-PREF-080]` -- the characteristic's own last segment, title-cased:
/// `user.christmas` reads as "Christmas".
///
/// A fallback, not the scheme. Every occasion `tools/load_occasions.py`
/// writes names itself properly ("Children's", which `user.childrens`
/// cannot spell), and this only covers an occasion registered before the
/// label column existed, or by hand. Better a plain name than a blank row.
fn derived_label(characteristic: &str) -> String {
    let tail = characteristic.rsplit('.').next().unwrap_or(characteristic);
    let mut chars = tail.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => characteristic.to_string(),
    }
}

/// Bring a `listener_flags` predating `origin` up to date `[SPEC-DF-107]`,
/// the same reason `ensure_history_columns` exists for the two tables beside
/// it: `CREATE TABLE IF NOT EXISTS` above is a no-op on a library that
/// already has the table. Lempi itself never writes this column -- only
/// `tools/import_flags.py`, landing a flag pulled from elsewhere, does -- but
/// the column has to already exist there for that write to have somewhere to
/// go, on every appliance, not just ones a sync tool happens to reach first.
fn ensure_flags_columns(conn: &Connection) {
    let _ = conn.execute("ALTER TABLE listener_flags ADD COLUMN origin TEXT", []);
}

/// Bring an existing `listener_play_history` / `listener_rejections` up to the
/// column set above `[REQ-VIS-250]`.
///
/// A library built before this feature has both tables already, so the
/// `CREATE TABLE IF NOT EXISTS` above is a no-op on it -- the same shape of
/// gap `ensure_review_table` and `ensure_tag_table` close for their own
/// tables. Already-present columns fail here, which is the expected path on
/// every start after the first.
fn ensure_history_columns(conn: &Connection) {
    for (table, column) in [
        ("listener_play_history", "heard_ms INTEGER"),
        ("listener_play_history", "span_ms INTEGER"),
        ("listener_rejections", "heard_ms INTEGER"),
        ("listener_rejections", "span_ms INTEGER"),
        // Who or what selected this play `[REQ-VIS-300]` -- `NULL` for
        // every row recorded before this existed, which is the honest
        // answer and stays that way: no back-annotation. Deliberately a
        // different column name than `listener_flags.origin`, which means
        // something unrelated (which remote wrote the row).
        ("listener_play_history", "selected_by TEXT"),
    ] {
        let _ = conn.execute(&format!("ALTER TABLE {table} ADD COLUMN {column}"), []);
    }
}

/// Cover art fetched from the Cover Art Archive `[REQ-VIS-170]`.
///
/// Keyed by **release**, not by folder. A directory can hold more than one
/// album -- which is exactly the case on the DAO rips that make up most of
/// this library's missing art -- and `folder.jpg` cannot tell them apart.
///
/// Written only by `tools/fetch_cover_art.py`; the player reads it. It is a
/// permanent cache in the sense `[SPEC-SA-020]` gives the lowlevel features:
/// derived from an external service, expensive to obtain, and pointless to
/// re-derive on every rebuild.
///
/// Front and back, because MuLibPlay carried both and its interface showed
/// both side by side. Nothing else is stored: MuLibPlay's back covers are
/// 35.6 MB of its 80.5 MB of art, and a third image nobody displays would be
/// the same bargain again.
pub(crate) const ART_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS cover_art (
        release_mbid TEXT PRIMARY KEY,
        front        BLOB,
        back         BLOB,
        source       TEXT NOT NULL,
        fetched_at   TEXT NOT NULL);";

/// Judgements a person has made about a questionable recording id
/// `[REQ-LIB-165]`.
///
/// Separate from `passage_recordings`, and the player never touches that table.
/// Three reasons, and the last is the one that matters:
///
/// * the read-only guard on the library survives -- a bug here still cannot
///   corrupt what Vipunen built;
/// * the decision is reversible, because the evidence it overrode is still
///   there to compare against;
/// * a reassignment changes what a passage *is*, and play history is keyed by
///   recording. Rewriting the id in place would silently re-attribute every
///   past play of it. That is a migration, and migrations belong to Vipunen.
///
/// So this is a decision log the player owns and honours, and
/// `tools/apply_reviews.py` is what folds accepted decisions into the library
/// proper -- deliberately a separate, deliberate step.
///
/// Gated behind `vipunen-support` along with everything else on this page
/// `[SPEC-SUI-190]`: an appliance that never runs Vipunen has nothing to review.
#[cfg(feature = "vipunen-support")]
pub(crate) const REVIEW_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS id_reviews (
        passage_id  INTEGER PRIMARY KEY,
        -- 'kept': the stored id is right despite the fingerprint.
        -- 'reassigned': chosen_mbid is right.
        -- 'deferred': looked at, not decided; stays out of the queue.
        decision    TEXT NOT NULL,
        chosen_mbid TEXT,
        decided_at  TEXT NOT NULL);";

/// Columns added to `id_reviews` after the first version of it shipped.
///
/// `previous_mbid` is what makes an applied decision reversible: once
/// `apply_reviews` has rewritten `passage_recordings`, the old id exists
/// nowhere else, and an undo with nothing to restore is not an undo.
///
/// `applied_at` is the difference between a judgement that can simply be
/// withdrawn and one that has already changed the library. The page must not
/// offer the same button for both.
/// `origin` is added here and to the other two review tables together
/// `[SPEC-DF-104]`: `NULL` for a decision made on this machine, else the
/// hostname it was synced in from -- so a decision's own history survives
/// however many installations it has already crossed.
#[cfg(feature = "vipunen-support")]
pub(crate) const REVIEW_COLUMNS: [&str; 4] = [
    "chosen_release_mbid TEXT",
    "previous_mbid TEXT",
    "applied_at TEXT",
    "origin TEXT",
];

/// Create `id_reviews` and bring it up to date, in one place.
///
/// The table and the columns added to it later are two halves of one schema,
/// and anything that builds one without the other gets a table the queries
/// cannot read. That is not hypothetical: the test fixture did exactly that
/// and every review test failed on `no such column`.
#[cfg(feature = "vipunen-support")]
pub(crate) fn ensure_review_table(conn: &Connection) -> Result<(), DbError> {
    conn.execute_batch(REVIEW_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
    for column in REVIEW_COLUMNS {
        // Already-present columns fail here, which is the expected path on
        // every start after the first.
        let _ = conn.execute(&format!("ALTER TABLE id_reviews ADD COLUMN {column}"), []);
    }
    Ok(())
}

/// Recorded boundary edits, shaped like `id_reviews` for the same reason
/// `[SPEC021 §2]`: an edit changes what a passage *is*, and the library is
/// Vipunen's to write, not a web click's. `tools/apply_boundary_reviews.py`
/// folds an accepted row into `passages`, on its own schedule.
///
/// No `previous_*` columns for *revert* -- unlike a recording reassignment,
/// the automatic values a manual edit overrides are always recoverable by
/// re-running the pass that produced them, so nothing here is the only copy
/// of a fact. `orig_*` below exists for a different reason `[SPEC-DF-102]`.
#[cfg(feature = "vipunen-support")]
pub(crate) const BOUNDARY_REVIEW_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS boundary_reviews (
        passage_id  INTEGER PRIMARY KEY,
        start_ms    INTEGER NOT NULL,
        end_ms      INTEGER NOT NULL,
        lead_in_ms  INTEGER,
        lead_out_ms INTEGER,
        gain_db     REAL,
        decided_at  TEXT NOT NULL,
        applied_at  TEXT);";

/// The pre-edit span and the passage's file identity `[SPEC-DF-102]`,
/// captured at decision time from the passage's *current* row -- not for
/// revert, which still re-derives, but because this edit changes the
/// passage's only portable identity `[SPEC-DF-035]`. Without the span as it
/// stood before this edit, a receiving installation that has not seen the
/// edit yet has nothing stable to resolve the decision against.
#[cfg(feature = "vipunen-support")]
pub(crate) const BOUNDARY_REVIEW_COLUMNS: [&str; 8] = [
    "audio_md5 TEXT",
    "orig_kind TEXT",
    "orig_start_ms INTEGER",
    "orig_end_ms INTEGER",
    "orig_lead_in_ms INTEGER",
    "orig_lead_out_ms INTEGER",
    "orig_gain_db REAL",
    "origin TEXT",
];

/// Fade, added after the columns above `[SPEC-SUI-226]` -- its own
/// `ALTER TABLE` batch rather than folded into the array above, so an
/// installation already past the first migration only ever gains columns,
/// never re-runs one it already has. Same `orig_*` shape as lead/start/end
/// for the same reason `[SPEC-DF-102]`: a fade edit needs a sync-safe
/// pre-edit baseline exactly like a boundary edit already has one.
#[cfg(feature = "vipunen-support")]
pub(crate) const BOUNDARY_REVIEW_FADE_COLUMNS: [&str; 8] = [
    "fade_in_ms INTEGER",
    "fade_out_ms INTEGER",
    "fade_in_curve TEXT",
    "fade_out_curve TEXT",
    "orig_fade_in_ms INTEGER",
    "orig_fade_out_ms INTEGER",
    "orig_fade_in_curve TEXT",
    "orig_fade_out_curve TEXT",
];

#[cfg(feature = "vipunen-support")]
pub(crate) fn ensure_boundary_review_table(conn: &Connection) -> Result<(), DbError> {
    conn.execute_batch(BOUNDARY_REVIEW_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
    for column in BOUNDARY_REVIEW_COLUMNS {
        let _ = conn.execute(&format!("ALTER TABLE boundary_reviews ADD COLUMN {column}"), []);
    }
    for column in BOUNDARY_REVIEW_FADE_COLUMNS {
        let _ = conn.execute(&format!("ALTER TABLE boundary_reviews ADD COLUMN {column}"), []);
    }
    Ok(())
}

/// A correction to a recording's credited artist `[SPEC-SUI-197]` -- a
/// different table with a different key from `id_reviews`, because it
/// corrects a different table with a different key: `recording_artists` is
/// keyed `(mbid, artist_mbid)`, not `passage_id`.
///
/// **Keyed by `recording_mbid`, not `passage_id`** -- corrected from this
/// table's first version, which used `passage_id` because the correction is
/// *reached* from a passage's card. The credit belongs to the recording, and
/// the same recording can sit under several passages (any file it appears in
/// more than once); keying by `passage_id` let two different cards for the
/// same recording each record their own, silently conflicting correction.
/// It also made a synced correction `[SPEC-DF-103]` impossible to key at all,
/// since a receiver has no originating passage for a decision it never made.
/// `passage_id` is kept as a plain, non-unique column -- which card the
/// correction happened to be made from, useful for provenance, load-bearing
/// for nothing.
///
/// `artist_name` is stored, not looked up at apply time, because a name
/// found through live search (`[SPEC-SUI-196]`) exists nowhere in the
/// database to look back up later -- unlike a fingerprint suggestion, which
/// `identification_cache` already holds a name for.
/// `previous_artist_*` is what makes `apply_boundary_reviews.py`'s sibling
/// able to revert an applied correction -- captured at decision time, the
/// same reason `id_reviews.previous_mbid` is: applying overwrites the only
/// other copy. The single heaviest existing credit, same as `previous_mbid`
/// captures the single heaviest existing recording link; `NULL` when the
/// recording had no credited artist at all, which is itself worth restoring
/// to on revert rather than inventing one.
#[cfg(feature = "vipunen-support")]
pub(crate) const ARTIST_REVIEW_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS artist_reviews (
        recording_mbid         TEXT PRIMARY KEY,
        passage_id             INTEGER,
        artist_mbid            TEXT NOT NULL,
        artist_name            TEXT NOT NULL,
        previous_artist_mbid   TEXT,
        previous_artist_name   TEXT,
        previous_artist_weight REAL,
        decided_at             TEXT NOT NULL,
        applied_at             TEXT);";

/// `origin` `[SPEC-DF-104]`, added the same way the other two review tables
/// gain it: `NULL` for a decision made on this machine, else the hostname it
/// arrived from.
#[cfg(feature = "vipunen-support")]
pub(crate) const ARTIST_REVIEW_COLUMNS: [&str; 1] = ["origin TEXT"];

#[cfg(feature = "vipunen-support")]
pub(crate) fn ensure_artist_review_table(conn: &Connection) -> Result<(), DbError> {
    conn.execute_batch(ARTIST_REVIEW_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
    for column in ARTIST_REVIEW_COLUMNS {
        let _ = conn.execute(&format!("ALTER TABLE artist_reviews ADD COLUMN {column}"), []);
    }
    Ok(())
}

/// Read-write access to the one row of state the player owns.
///
/// Separate from [`crate::db::Library`] on purpose: the library is opened read-only so a
/// player bug cannot corrupt it, and the writable surface stays visibly tiny.
/// Play history will be written here too, but only once `QueueEntry` carries
/// the recording MBID it must be keyed by `[SPEC-SC-095]` — an untested writer
/// with no reader would be a claim nothing exercises.
pub struct PlayerStore {
    conn: QualifyingConn,
}

/// Every setting the listener owns `[REQ-VIS-155]`, in one row.
///
/// A struct rather than a tuple because there are eight of them now and a
/// nine-element tuple is a way to swap two `u64`s without the compiler minding.
/// `[SPEC-DLY-010]`: the trim's range, either way.
pub const ECHO_TRIM_LIMIT_MS: i64 = 2_000;

// No longer `Copy`: `echo_follow_host` is a `String`. Clone is kept, and the
// handful of call sites that relied on an implicit copy now say `.clone()`,
// which is the honest cost of a setting that is text rather than a number.
#[derive(Debug, Clone, PartialEq)]
pub struct Settings {
    pub volume: f32,
    pub skip_fade_ms: u64,
    pub skip_lead_ms: u64,
    pub resume_save_ms: u64,
    /// `[SPEC-PLAY-050]`
    pub skip_suppress_h: u64,
    /// `[SPEC-PLAY-055]`
    pub dequeue_suppress_h: u64,
    /// How many passages to keep ahead. Governs the local engine and the MPD
    /// Director alike `[SPEC-MPD-105]`.
    pub queue_depth: usize,
    /// How often `status` is read while playing `[SPEC-MPD-105]`.
    pub sample_interval_ms: u64,
    /// Whether Lempi may write cue sheets into the music folder so a guest can
    /// name a passage inside a capture `[REQ-VIS-205]`. **Off by default**:
    /// nothing else in Lempi puts a file there.
    pub cue_sheets: bool,
    /// Whether Lempi may write cover art into the music folder so a guest can
    /// show the album `[REQ-VIS-210]`. **Off by default**, for the same reason.
    pub covers: bool,
    /// Whether Lempi may write per-song lyrics into a local client's cache
    /// `[REQ-VIS-215]`. **Off by default**, and useful only where that client
    /// runs on this machine `[SPEC-LYR-075]`.
    pub lyrics_cache: bool,
    /// Whether Lempi may write lyrics beside the audio `[REQ-VIS-220]`.
    /// **Off by default**: it writes into the music folder, and it is
    /// deliberately blind to captures `[SPEC-LYR-080]`.
    pub lyrics_sidecar: bool,
    /// This node's hand-set delay trim, in milliseconds `[SPEC-DLY-010]`.
    ///
    /// The *calibrated* half of the presentation offset `[GDE-ECHO-430]`,
    /// added to whatever the device reports. Signed, because a stack that
    /// over-reports its delay needs the node pulled earlier, not later.
    pub echo_delay_trim_ms: i64,
    /// The node this one follows `[SPEC-ECHO-010]`, as a bare host.
    ///
    /// Empty means independent, which is every node's default and what it
    /// returns to if its master goes away `[GDE-ECHO-500]`. Stored as a host
    /// rather than a URL: every node serves the same socket at the same path,
    /// so the rest is composed and cannot be got wrong.
    pub echo_follow_host: String,
    /// What this node's past joins landed at, so the next one starts from
    /// experience `[GDE-ARC-061]`. Encoded samples; see `echo::JoinBias`.
    ///
    /// Persisted for the reason `echo_prep_ms` was not: that one reset to its
    /// cold guess at every restart and so never converged `[GDE-ARC-058]`.
    pub echo_join_bias: String,
    /// Whether entering follower mode joins at once **and then keeps this
    /// node aligned continuously** `[SPEC-ECHO-030]`, `[GDE-ARC-041]`.
    ///
    /// Named for the join because that is all it used to govern. It now
    /// governs both halves, which is what a listener reads it as: with it
    /// on, a residual the passage boundary will not take is shed by the
    /// frame trim mid-passage rather than waiting minutes for the master's
    /// next track. Off, alignment is corrected only at a boundary.
    ///
    /// **True by default.** Someone who has just typed a node's name wants to
    /// hear whether it worked, and the alternative can be minutes away. The
    /// cost is that the passage playing here is cut, which is what a skip
    /// always costs and is the listener's own choice to make.
    pub echo_join_now: bool,
}

impl Settings {
    /// Every persisted setting, by the name it is stored under
    /// `[SPEC-SC-099]`.
    ///
    /// **The names are the old column names on purpose**, so a database written
    /// before this carries over without a translation table, and so anyone
    /// reading `player_settings` sees what they expect.
    ///
    /// This list is the single source: `value_of` and `set` are both matched
    /// against it by `every_setting_survives_a_round_trip`, so a field added to
    /// one and forgotten in the other fails a test rather than silently losing
    /// itself on the next restart.
    pub const KEYS: [&'static str; 16] = [
        "volume",
        "skip_fade_ms",
        "skip_lead_ms",
        "resume_save_ms",
        "skip_suppress_h",
        "dequeue_suppress_h",
        "queue_depth",
        "sample_interval_ms",
        "cue_sheets",
        "covers",
        "echo_delay_trim_ms",
        "echo_follow_host",
        "echo_join_now",
        "echo_join_bias",
        "lyrics_cache",
        "lyrics_sidecar",
    ];

    /// What to store under `key`, or `None` if this is not a setting.
    ///
    /// Public because this is layer 2 of the option chain `[GDE-CLI-090]`:
    /// `Args::with_settings` is handed exactly this lookup, so the stored
    /// settings reach the command line without either side listing the other's
    /// keys.
    pub fn value_of(&self, key: &str) -> Option<String> {
        Some(match key {
            "volume" => self.volume.to_string(),
            "skip_fade_ms" => self.skip_fade_ms.to_string(),
            "skip_lead_ms" => self.skip_lead_ms.to_string(),
            "resume_save_ms" => self.resume_save_ms.to_string(),
            "skip_suppress_h" => self.skip_suppress_h.to_string(),
            "dequeue_suppress_h" => self.dequeue_suppress_h.to_string(),
            "queue_depth" => self.queue_depth.to_string(),
            "sample_interval_ms" => self.sample_interval_ms.to_string(),
            "cue_sheets" => (self.cue_sheets as i64).to_string(),
            "covers" => (self.covers as i64).to_string(),
            "lyrics_cache" => (self.lyrics_cache as i64).to_string(),
            "lyrics_sidecar" => (self.lyrics_sidecar as i64).to_string(),
            "echo_delay_trim_ms" => self.echo_delay_trim_ms.to_string(),
            "echo_follow_host" => self.echo_follow_host.clone(),
            "echo_join_now" => (self.echo_join_now as i64).to_string(),
            "echo_join_bias" => self.echo_join_bias.clone(),
            _ => return None,
        })
    }

    /// Take one stored value. Anything unreadable leaves the field alone, so a
    /// corrupted row costs that setting and not the rest of them.
    fn set(&mut self, key: &str, value: &str) {
        // Booleans were written as 0/1 by the old columns and still are.
        let flag = || matches!(value, "1" | "true");
        match key {
            "volume" => {
                if let Ok(v) = value.parse() {
                    self.volume = v;
                }
            }
            "skip_fade_ms" => set_u64(&mut self.skip_fade_ms, value),
            "skip_lead_ms" => set_u64(&mut self.skip_lead_ms, value),
            "resume_save_ms" => set_u64(&mut self.resume_save_ms, value),
            "skip_suppress_h" => set_u64(&mut self.skip_suppress_h, value),
            "dequeue_suppress_h" => set_u64(&mut self.dequeue_suppress_h, value),
            "queue_depth" => {
                if let Ok(v) = value.parse() {
                    self.queue_depth = v;
                }
            }
            "sample_interval_ms" => set_u64(&mut self.sample_interval_ms, value),
            "cue_sheets" => self.cue_sheets = flag(),
            "covers" => self.covers = flag(),
            "lyrics_cache" => self.lyrics_cache = flag(),
            "lyrics_sidecar" => self.lyrics_sidecar = flag(),
            "echo_delay_trim_ms" => {
                if let Ok(v) = value.parse::<i64>() {
                    self.echo_delay_trim_ms = v.clamp(-ECHO_TRIM_LIMIT_MS, ECHO_TRIM_LIMIT_MS);
                }
            }
            // Read back as stored. A host that has become unreachable is still
            // the host somebody chose, and silently blanking it would look
            // like nobody ever set one `[GOV-SRC-040]`.
            "echo_follow_host" => self.echo_follow_host = value.to_string(),
            "echo_join_now" => self.echo_join_now = flag(),
            "echo_join_bias" => self.echo_join_bias = value.to_string(),
            _ => {}
        }
    }
}

fn set_u64(field: &mut u64, value: &str) {
    if let Ok(v) = value.parse() {
        *field = v;
    }
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            volume: 1.0,
            echo_join_bias: String::new(),
            skip_fade_ms: crate::SKIP_FADE_MS,
            skip_lead_ms: crate::SKIP_LEAD_MS,
            resume_save_ms: crate::RESUME_SAVE_MS,
            skip_suppress_h: crate::SKIP_SUPPRESS_H,
            dequeue_suppress_h: crate::DEQUEUE_SUPPRESS_H,
            queue_depth: crate::QUEUE_DEPTH,
            sample_interval_ms: crate::SAMPLE_INTERVAL_MS,
            cue_sheets: false,
            covers: false,
            // Absent is not zero `[GOV-SRC-040]`: a trim of 0 here means
            // nothing has been calibrated, which the interface shows as
            // *unset* rather than as a measured zero `[SPEC-DLY-050]`.
            echo_delay_trim_ms: 0,
            echo_follow_host: String::new(),
            echo_join_now: true,
            lyrics_cache: false,
            lyrics_sidecar: false,
        }
    }
}

/// The two ways a listener declines a passage `[SPEC-PLAY-055]`.
///
/// They differ only in the window they earn. A **skip** is a passage stopped
/// after it began sounding; a **dequeue** is one removed from the queue before
/// it ever played, which is the weaker statement and gets the shorter window.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rejection {
    Skip,
    Dequeue,
}

impl Rejection {
    pub fn as_str(self) -> &'static str {
        match self {
            Rejection::Skip => "skip",
            Rejection::Dequeue => "dequeue",
        }
    }
}

/// The B-side tables and columns `PlayerStore` has historically created as a
/// side effect of being the only writable handle -- `file_tags`, `cover_art`,
/// `release_recordings`'s Vipunen-selection columns and its performance index
/// -- but only when this connection's own file genuinely *is* the library
/// too `[IMPL002 §4.3, §7.6]`. Once split (`alias == "lib"`), `library.db` is
/// attached read-only here and none of this can run through this connection
/// at all -- `tools/split_database.py` carries these over at split time, and
/// `tagscan`/`fetch_cover_art.py` own them going forward, the same
/// self-sufficient pattern `Library::open_writable` now uses for
/// `file_tags` `[IMPL-DBSPLIT-050]`.
fn ensure_library_tables_if_owned(conn: &QualifyingConn) -> Result<(), DbError> {
    if conn.lib_alias() != "main" {
        return Ok(());
    }
    // Browsing joins this table, and an absent one fails the query rather
    // than returning nothing. Filling it is `tagscan`'s job.
    conn.execute_batch(TAG_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
    // Same reasoning as the tag table above: created by whoever holds a
    // writable handle, so the read path never meets a missing table.
    // Filling it is `tools/fetch_cover_art.py`'s job.
    conn.execute_batch(ART_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
    for column in ["chosen INTEGER DEFAULT 0", "position INTEGER", "disc INTEGER"] {
        let _ =
            conn.execute(&format!("ALTER TABLE release_recordings ADD COLUMN {column}"), []);
    }
    // Album names are looked up BY RECORDING, and `release_recordings` is
    // keyed `(release_mbid, mbid)` -- so the lookup uses the second column
    // of the primary key and no index applies. SQLite falls back to a full
    // scan of the table, once per passage.
    //
    // That was free when the table was empty and became quadratic the
    // moment Vipunen filled it: at 304,334 rows against 8,078 passages,
    // browsing albums went past 400 seconds and the review queue took 229.
    // With this index the review query is 0.50 s. Nothing about the code
    // changed in between, only the amount of data, which is the kind of
    // regression that arrives without a commit to blame.
    //
    // **And the sort after it, which the single-column index did not**
    // `[PI-CHR-065]`. Measured on the appliance against the real library:
    // `browse_albums` picks the chosen release per recording, so it runs
    // `ORDER BY rr.chosen DESC, rel.release_date, rel.title LIMIT 1` once
    // per passage over some thirty-six releases each. Finding the rows was
    // already cheap; ordering them was 18.05 s of a 25.7 s page.
    //
    // Carrying `chosen` and `release_mbid` in the index lets SQLite take the
    // first row instead of sorting them: **0.93 s for the same 694 albums**,
    // built once in 4.4 s. The sort cannot simply be dropped — without it
    // the answer is 1,698 albums, because then any release will do.
    //
    // Created after `chosen` exists, which the ALTER above guarantees. That
    // is why this lives here and not in `schema.sql`: that file describes
    // the table as it is before the column this sorts on is added to it.
    let _ = conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_release_recordings_chosen \
           ON release_recordings(mbid, chosen DESC, release_mbid)",
        [],
    );
    // Redundant once the covering one exists — `mbid` leads both, so
    // anything the old index served the new one serves too. Dropped after
    // it and never before, so there is no moment with neither.
    let _ = conn.execute("DROP INDEX IF EXISTS idx_release_recordings_mbid", []);
    Ok(())
}

impl PlayerStore {
    pub fn open(path: &std::path::Path) -> Result<Self, DbError> {
        Self::open_split(path, path)
    }

    /// Opens `db_path` (listener-side, writable) and attaches `library_path`
    /// read-only `[PI-DB-020]`. `open` above is the common case -- every
    /// installation that hasn't split calls it with one path, which becomes
    /// `open_split(path, path)`, attaching nothing
    /// (`[IMPL-DBSPLIT-025]`). An installation with a genuinely separate
    /// `library.db` calls this directly once it has two paths.
    pub fn open_split(
        db_path: &std::path::Path,
        library_path: &std::path::Path,
    ) -> Result<Self, DbError> {
        let conn = QualifyingConn::open(
            db_path,
            library_path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE | rusqlite::OpenFlags::SQLITE_OPEN_CREATE,
        )?;
        conn.busy_timeout(BUSY_WAIT).map_err(|e| DbError::Open(e.to_string()))?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS player_state (
                 id INTEGER PRIMARY KEY CHECK (id = 1),
                 passage_id INTEGER, position_ms INTEGER NOT NULL DEFAULT 0,
                 playing INTEGER NOT NULL DEFAULT 0, volume REAL NOT NULL DEFAULT 1.0,
                 updated_at TEXT NOT NULL);",
        )
        .map_err(|e| DbError::Open(e.to_string()))?;
        // The queue as it stood, so a restart resumes the listening rather
        // than starting a new one `[SPEC-DIR-225]`. Beside `player_state` and
        // not inside it: that row is the passage *sounding*, this is what is
        // *waiting*, and `hand_over_seamless` already keeps the two apart for
        // the same reason `[SPEC-BK-030]`.
        //
        // Passage ids and their order, and nothing else. A queue entry is
        // rebuilt from its id by `Library::passage`, exactly as the backend
        // handoff rebuilds one on arrival rather than carrying it -- spans,
        // fades and naming all belong to the passage, not to whoever last
        // held it queued.
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS player_queue (
                 position   INTEGER PRIMARY KEY,
                 passage_id INTEGER NOT NULL,
                 updated_at TEXT NOT NULL);",
        )
        .map_err(|e| DbError::Open(e.to_string()))?;
        conn.execute_batch(PLAY_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
        conn.execute_batch(REJECTION_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
        conn.execute_batch(FLAGS_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
        conn.execute_batch(PREFERENCES_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
        conn.execute_batch(CHARACTERISTICS_TABLE).map_err(|e| DbError::Open(e.to_string()))?;
        ensure_history_columns(&conn);
        ensure_flags_columns(&conn);
        ensure_occasion_columns(&conn);
        #[cfg(feature = "vipunen-support")]
        ensure_review_table(&conn)?;
        #[cfg(feature = "vipunen-support")]
        ensure_boundary_review_table(&conn)?;
        #[cfg(feature = "vipunen-support")]
        ensure_artist_review_table(&conn)?;
        // Settings live in `player_settings`, one row each `[SPEC-SC-099]`.
        // They used to be columns on `player_state`, added by ALTER as each was
        // invented, and read back by position -- `?11` here, `r.get(10)` there.
        // Adding one meant renumbering both, and getting it wrong loaded the
        // wrong value silently rather than failing.
        let _ = conn.execute(
            "CREATE TABLE IF NOT EXISTS player_settings (
                 key        TEXT PRIMARY KEY,
                 value      TEXT NOT NULL,
                 updated_at TEXT NOT NULL)",
            [],
        );
        Self::adopt_old_settings_columns(&conn);
        ensure_library_tables_if_owned(&conn)?;
        Ok(Self { conn })
    }

    /// Record a judgement about a questionable id `[REQ-LIB-165]`.
    ///
    /// The decision is validated here rather than trusted from the request:
    /// this is the only writer, so it is the only place the vocabulary can be
    /// enforced. A reassignment must name a recording; a keep must not.
    #[cfg(feature = "vipunen-support")]
    pub fn record_review(
        &self,
        passage_id: i64,
        decision: &str,
        chosen_mbid: Option<&str>,
        chosen_release_mbid: Option<&str>,
    ) -> Result<(), DbError> {
        let mbid = match decision {
            "reassigned" => match chosen_mbid.filter(|m| !m.is_empty()) {
                Some(m) => Some(m),
                None => {
                    return Err(DbError::Query(
                        "a reassignment has to say which recording".into(),
                    ))
                }
            },
            "kept" | "deferred" => None,
            other => return Err(DbError::Query(format!("unknown decision {other:?}"))),
        };
        let release = chosen_release_mbid.filter(|m| !m.is_empty() && decision == "reassigned");

        // Changing a judgement that has already been written into the library
        // is not a matter of overwriting a row -- the old id has to be put back
        // first, and only `apply_reviews` may touch `passage_recordings`.
        let applied: Option<String> = self
            .conn
            .query_row("SELECT applied_at FROM id_reviews WHERE passage_id = ?1",
                       [passage_id], |r| r.get(0))
            .unwrap_or(None);
        if applied.is_some() {
            return Err(DbError::Query(
                "this decision has already been applied to the library; \
                 revert it with tools/apply_reviews.py --revert before changing it"
                    .into(),
            ));
        }

        // What the passage says NOW, captured before anything replaces it. An
        // applied reassignment overwrites the only copy of the old id, so
        // without this an undo would have nothing to restore.
        let previous: Option<String> = self
            .conn
            .query_row(
                "SELECT mbid FROM __LIB__.passage_recordings WHERE passage_id = ?1 \
                  ORDER BY weight DESC, mbid LIMIT 1",
                [passage_id], |r| r.get(0))
            .ok();

        self.conn
            .execute(
                "INSERT INTO id_reviews
                     (passage_id, decision, chosen_mbid, chosen_release_mbid,
                      previous_mbid, decided_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, datetime('now'))
                 ON CONFLICT(passage_id) DO UPDATE SET
                     decision = excluded.decision,
                     chosen_mbid = excluded.chosen_mbid,
                     chosen_release_mbid = excluded.chosen_release_mbid,
                     previous_mbid = excluded.previous_mbid,
                     decided_at = excluded.decided_at",
                rusqlite::params![passage_id, decision, mbid, release, previous],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Withdraw a judgement `[REQ-LIB-165]`.
    ///
    /// Only one that has not been applied. Once `apply_reviews` has rewritten
    /// `passage_recordings`, deleting the review row would strand the library
    /// on a change with no record of why it was made or what it replaced --
    /// an undo that leaves the thing it was undoing in place. Reverting an
    /// applied decision is `apply_reviews --revert`, which puts the old id
    /// back and clears the row in one transaction.
    #[cfg(feature = "vipunen-support")]
    pub fn clear_review(&self, passage_id: i64) -> Result<(), DbError> {
        let applied: Option<String> = self
            .conn
            .query_row("SELECT applied_at FROM id_reviews WHERE passage_id = ?1",
                       [passage_id], |r| r.get(0))
            .map_err(|_| DbError::Query("no decision recorded for that passage".into()))?;
        if applied.is_some() {
            return Err(DbError::Query(
                "already applied to the library; use tools/apply_reviews.py --revert".into(),
            ));
        }
        self.conn
            .execute("DELETE FROM id_reviews WHERE passage_id = ?1", [passage_id])
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Record a boundary edit `[REQ-LIB-175]`, `[SPEC021 §2]`.
    ///
    /// Committing twice on the same passage updates the one row rather than
    /// adding a second -- the same `ON CONFLICT` shape `record_review` uses,
    /// for the same reason: the queue key is the passage, and a second draft
    /// is not a second finding.
    #[cfg(feature = "vipunen-support")]
    #[allow(clippy::too_many_arguments)]
    pub fn record_boundary_review(
        &self,
        passage_id: i64,
        start_ms: u64,
        end_ms: u64,
        lead_in_ms: u64,
        lead_out_ms: u64,
        gain_db: f64,
        fade_in_ms: u64,
        fade_out_ms: u64,
        fade_in_curve: &str,
        fade_out_curve: &str,
    ) -> Result<(), DbError> {
        if start_ms >= end_ms {
            return Err(DbError::Query("start must come before end".into()));
        }
        // Validated here, not trusted from the URL -- the same posture
        // `record_review` already takes for its own verb `[SPEC-SUI-226]`.
        let fade_in_curve = crate::fade::Curve::parse(fade_in_curve)
            .ok_or_else(|| DbError::Query(format!("unknown fade-in curve: {fade_in_curve}")))?
            .as_str();
        let fade_out_curve = crate::fade::Curve::parse(fade_out_curve)
            .ok_or_else(|| DbError::Query(format!("unknown fade-out curve: {fade_out_curve}")))?
            .as_str();

        // Changing a decision already folded into `passages` is not an
        // overwrite of one row -- `apply_boundary_reviews.py` has to put the
        // old span back first, the same guard `record_review` and
        // `record_artist_review` already apply to their own tables. Missing
        // here until this same change added `orig_*`: re-committing after
        // apply would have captured the *already-applied* values as if they
        // were the original, corrupting the one thing `[SPEC-DF-102]` exists
        // to keep honest.
        let applied: Option<String> = self
            .conn
            .query_row(
                "SELECT applied_at FROM boundary_reviews WHERE passage_id = ?1",
                [passage_id],
                |r| r.get(0),
            )
            .unwrap_or(None);
        if applied.is_some() {
            return Err(DbError::Query(
                "this edit has already been applied to the library".into(),
            ));
        }

        // The pre-edit span and file identity, read from `passages` itself
        // rather than from any earlier `boundary_reviews` row `[SPEC-DF-102]`.
        // `passages` does not change until `apply_boundary_reviews.py` runs,
        // so a second commit before that reads the same true original --
        // exactly how `record_review`'s `previous_mbid` stays pinned across
        // repeated commits without needing to remember its own prior value.
        #[allow(clippy::type_complexity)]
        let (audio_md5, orig_kind, orig_start_ms, orig_end_ms, orig_lead_in_ms, orig_lead_out_ms,
             orig_gain_db, orig_fade_in_ms, orig_fade_out_ms, orig_fade_in_curve, orig_fade_out_curve):
            (String, String, i64, i64, Option<i64>, Option<i64>, Option<f64>, i64, i64, String, String) = self
            .conn
            .query_row(
                "SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms, p.lead_in_ms, p.lead_out_ms, p.gain_db, \
                        p.fade_in_ms, p.fade_out_ms, p.fade_in_curve, p.fade_out_curve \
                   FROM __LIB__.passages p JOIN __LIB__.files f ON f.file_id = p.file_id \
                  WHERE p.passage_id = ?1",
                [passage_id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?,
                        r.get(7)?, r.get(8)?, r.get(9)?, r.get(10)?)),
            )
            .map_err(|_| DbError::Query("no such passage".into()))?;

        self.conn
            .execute(
                "INSERT INTO boundary_reviews
                     (passage_id, start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db,
                      fade_in_ms, fade_out_ms, fade_in_curve, fade_out_curve,
                      audio_md5, orig_kind, orig_start_ms, orig_end_ms,
                      orig_lead_in_ms, orig_lead_out_ms, orig_gain_db,
                      orig_fade_in_ms, orig_fade_out_ms, orig_fade_in_curve, orig_fade_out_curve,
                      decided_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17,
                         ?18, ?19, ?20, ?21, datetime('now'))
                 ON CONFLICT(passage_id) DO UPDATE SET
                     start_ms = excluded.start_ms,
                     end_ms = excluded.end_ms,
                     lead_in_ms = excluded.lead_in_ms,
                     lead_out_ms = excluded.lead_out_ms,
                     gain_db = excluded.gain_db,
                     fade_in_ms = excluded.fade_in_ms,
                     fade_out_ms = excluded.fade_out_ms,
                     fade_in_curve = excluded.fade_in_curve,
                     fade_out_curve = excluded.fade_out_curve,
                     audio_md5 = excluded.audio_md5,
                     orig_kind = excluded.orig_kind,
                     orig_start_ms = excluded.orig_start_ms,
                     orig_end_ms = excluded.orig_end_ms,
                     orig_lead_in_ms = excluded.orig_lead_in_ms,
                     orig_lead_out_ms = excluded.orig_lead_out_ms,
                     orig_gain_db = excluded.orig_gain_db,
                     orig_fade_in_ms = excluded.orig_fade_in_ms,
                     orig_fade_out_ms = excluded.orig_fade_out_ms,
                     orig_fade_in_curve = excluded.orig_fade_in_curve,
                     orig_fade_out_curve = excluded.orig_fade_out_curve,
                     decided_at = excluded.decided_at",
                rusqlite::params![
                    passage_id, start_ms as i64, end_ms as i64,
                    lead_in_ms as i64, lead_out_ms as i64, gain_db,
                    fade_in_ms as i64, fade_out_ms as i64, fade_in_curve, fade_out_curve,
                    audio_md5, orig_kind, orig_start_ms, orig_end_ms,
                    orig_lead_in_ms, orig_lead_out_ms, orig_gain_db,
                    orig_fade_in_ms, orig_fade_out_ms, orig_fade_in_curve, orig_fade_out_curve,
                ],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// "Accept as-is" `[SPEC024 §7]`, `[SPEC-SA-125]`: a passage the
    /// segmentation cascade drew boundaries for, confirmed without opening
    /// the waveform editor. Writes a `boundary_reviews` row equal to the
    /// passage's own current values -- a fourth verb alongside a
    /// correction, playing the same role for a cascade result that
    /// `record_review`'s `kept` plays for an identification: nothing to
    /// change, only a decision to record.
    ///
    /// A thin wrapper, not a second writer: every guard `record_boundary_review`
    /// already enforces (an edit already applied is refused, the pre-edit
    /// baseline is captured from `passages` itself, `start_ms < end_ms`)
    /// applies here unchanged, because this only supplies that method's nine
    /// values by reading them back from the same row rather than from a
    /// posted draft. `boundary_src` is not touched here -- exactly like a
    /// waveform correction, that happens only once
    /// `tools/apply_boundary_reviews.py` next folds the recorded row into
    /// `passages` `[SPEC-SC-045]`.
    #[cfg(feature = "vipunen-support")]
    pub fn accept_segment(&self, passage_id: i64) -> Result<(), DbError> {
        // Refused for anything the queue itself would not have offered --
        // otherwise "accept as-is" could silently create a `boundary_reviews`
        // row for a passage nobody asked to confirm.
        let boundary_src: String = self
            .conn
            .query_row(
                "SELECT boundary_src FROM __LIB__.passages WHERE passage_id = ?1",
                [passage_id],
                |r| r.get(0),
            )
            .map_err(|_| DbError::Query("no such passage".into()))?;
        if !boundary_src.starts_with("computed:segment-cascade") {
            return Err(DbError::Query(
                "this passage's boundaries were not set by the segmentation cascade".into(),
            ));
        }
        #[allow(clippy::type_complexity)]
        let (start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db, fade_in_ms, fade_out_ms,
             fade_in_curve, fade_out_curve):
            (i64, i64, Option<i64>, Option<i64>, Option<f64>, i64, i64, String, String) = self
            .conn
            .query_row(
                "SELECT start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db, \
                        fade_in_ms, fade_out_ms, fade_in_curve, fade_out_curve \
                   FROM __LIB__.passages WHERE passage_id = ?1",
                [passage_id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?,
                        r.get(5)?, r.get(6)?, r.get(7)?, r.get(8)?)),
            )
            .map_err(|_| DbError::Query("no such passage".into()))?;
        self.record_boundary_review(
            passage_id,
            start_ms as u64,
            end_ms as u64,
            // NULL lead means "not analysed" -- the same fallback
            // `row_to_entry` already takes for live playback, so accepting
            // "as-is" really does mean the passage's current behaviour, not
            // a fabricated lead.
            lead_in_ms.unwrap_or(0).max(0) as u64,
            lead_out_ms.unwrap_or(0).max(0) as u64,
            gain_db.unwrap_or(0.0),
            fade_in_ms.max(0) as u64,
            fade_out_ms.max(0) as u64,
            &fade_in_curve,
            &fade_out_curve,
        )
    }

    /// Correct a recording's credited artist without touching the recording
    /// itself `[SPEC-SUI-197]` -- the one correction today's review page
    /// cannot make any other way.
    ///
    /// Refuses a passage with no recording linked at all: "the recording is
    /// right, the credit is wrong" presupposes a recording has been chosen,
    /// and there is nothing here to attach a corrected credit to otherwise.
    #[cfg(feature = "vipunen-support")]
    pub fn record_artist_review(
        &self,
        passage_id: i64,
        artist_mbid: &str,
        artist_name: &str,
    ) -> Result<(), DbError> {
        let recording_mbid: String = self
            .conn
            .query_row(
                "SELECT mbid FROM __LIB__.passage_recordings WHERE passage_id = ?1 \
                  ORDER BY weight DESC, mbid LIMIT 1",
                [passage_id],
                |r| r.get(0),
            )
            .map_err(|_| {
                DbError::Query("this passage has no recording linked yet".into())
            })?;

        // Keyed by `recording_mbid`, not `passage_id`: the credit belongs to
        // the recording, and a second passage carrying the same recording
        // must find and refuse (or update) the SAME row, not start a second,
        // silently conflicting one.
        let applied: Option<String> = self
            .conn
            .query_row(
                "SELECT applied_at FROM artist_reviews WHERE recording_mbid = ?1",
                [&recording_mbid],
                |r| r.get(0),
            )
            .unwrap_or(None);
        if applied.is_some() {
            return Err(DbError::Query(
                "this correction has already been applied to the library".into(),
            ));
        }

        // Captured now, because applying overwrites the only other copy --
        // `None` when the recording carries no credit at all today, which is
        // itself the state a revert should restore rather than inventing one.
        let previous: Option<(String, String, f64)> = self
            .conn
            .query_row(
                "SELECT ra.artist_mbid, a.name, ra.weight \
                   FROM __LIB__.recording_artists ra JOIN __LIB__.artists a ON a.mbid = ra.artist_mbid \
                  WHERE ra.mbid = ?1 ORDER BY ra.weight DESC, a.name LIMIT 1",
                [&recording_mbid],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .ok();
        let (prev_mbid, prev_name, prev_weight) = match previous {
            Some((m, n, w)) => (Some(m), Some(n), Some(w)),
            None => (None, None, None),
        };

        self.conn
            .execute(
                "INSERT INTO artist_reviews
                     (recording_mbid, passage_id, artist_mbid, artist_name,
                      previous_artist_mbid, previous_artist_name, previous_artist_weight,
                      decided_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, datetime('now'))
                 ON CONFLICT(recording_mbid) DO UPDATE SET
                     passage_id = excluded.passage_id,
                     artist_mbid = excluded.artist_mbid,
                     artist_name = excluded.artist_name,
                     previous_artist_mbid = excluded.previous_artist_mbid,
                     previous_artist_name = excluded.previous_artist_name,
                     previous_artist_weight = excluded.previous_artist_weight,
                     decided_at = excluded.decided_at",
                rusqlite::params![
                    recording_mbid, passage_id, artist_mbid, artist_name,
                    prev_mbid, prev_name, prev_weight,
                ],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Withdraw an artist correction, the same "recorded, not yet applied"
    /// undo `clear_review` offers for a recording reassignment.
    #[cfg(feature = "vipunen-support")]
    pub fn clear_artist_review(&self, passage_id: i64) -> Result<(), DbError> {
        // Resolved through the passage's CURRENT recording link, the same
        // way `record_artist_review` finds the row to begin with -- the
        // undo button lives on a passage's card, but the row it withdraws is
        // keyed by the recording that card currently names.
        let recording_mbid: String = self
            .conn
            .query_row(
                "SELECT mbid FROM __LIB__.passage_recordings WHERE passage_id = ?1 \
                  ORDER BY weight DESC, mbid LIMIT 1",
                [passage_id],
                |r| r.get(0),
            )
            .map_err(|_| DbError::Query("this passage has no recording linked".into()))?;
        let applied: Option<String> = self
            .conn
            .query_row(
                "SELECT applied_at FROM artist_reviews WHERE recording_mbid = ?1",
                [&recording_mbid],
                |r| r.get(0),
            )
            .map_err(|_| DbError::Query("no artist correction recorded for that recording".into()))?;
        if applied.is_some() {
            return Err(DbError::Query(
                "already applied to the library; use tools/apply_reviews.py --revert-artist".into(),
            ));
        }
        self.conn
            .execute("DELETE FROM artist_reviews WHERE recording_mbid = ?1", [&recording_mbid])
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Save the resume point `[REQ-AUD-140]`.
    /// The listener's settings `[REQ-VIS-155]`.
    ///
    /// Separate from `save`, which runs every second to keep the resume point
    /// current. These change when someone moves a control and not otherwise,
    /// so writing them on that schedule would be a write per second to record
    /// that nothing had happened.
    ///
    /// Volume was already a column here and was never written to it -- the
    /// resume point saved position and playing state and quietly left the
    /// level behind, so it came back at full scale every start.
    pub fn save_settings(&self, s: &Settings) -> Result<(), DbError> {
        for key in Settings::KEYS {
            let Some(value) = s.value_of(key) else { continue };
            self.conn
                .execute(
                    "INSERT INTO player_settings (key, value, updated_at)
                     VALUES (?1, ?2, datetime('now'))
                     ON CONFLICT(key) DO UPDATE SET
                         value = excluded.value, updated_at = excluded.updated_at",
                    rusqlite::params![key, value],
                )
                .map_err(|e| DbError::Query(e.to_string()))?;
        }
        Ok(())
    }

    /// The settings as they were left.
    ///
    /// A key that is absent, or holds something unreadable, falls back to its
    /// **own** default rather than failing the whole read: an absent key is a
    /// setting never touched, which is a first run and not a fault.
    pub fn load_settings(&self) -> Option<Settings> {
        let mut out = Settings::default();
        let mut q = self.conn.prepare("SELECT key, value FROM player_settings").ok()?;
        let rows = q.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).ok()?;
        let mut any = false;
        for (key, value) in rows.flatten() {
            out.set(&key, &value);
            any = true;
        }
        // **`None` means never saved**, which is what a caller distinguishes a
        // first run by. Returning the defaults instead would be the same values
        // wearing a claim that somebody chose them.
        any.then_some(out)
    }

    /// Bring `listener_settings.utc_offset_minutes` into line with what the
    /// OS believes right now, if it differs `[SPEC-DIR-180]`, `[REQ-VIS-255]`.
    ///
    /// **Must run through this connection, not `Library`'s.** `Library::open`
    /// is deliberately read-only ("the player must not be able to corrupt the
    /// library"), so a write attempted there fails silently every time --
    /// which is exactly the trap the first version of this fix fell into: it
    /// looked like it was working, called `sync_os_utc_offset` on schedule,
    /// and never once reached the disk. `PlayerStore` is the one connection
    /// this process holds that can actually write.
    pub fn sync_utc_offset(&self) {
        crate::director::program::sync_os_utc_offset(&self.conn);
    }

    /// Remember which speaker was chosen `[PI3-AIM-020]`, `[REQ-VIS-260]`.
    ///
    /// Found by its absence: the appliance's own reconnect timer had no way
    /// to know which of possibly several trusted, paired devices was the one
    /// actually in use, so it kept paging a stale one left over from early
    /// testing -- and paging a device the shared radio cannot reach stalls
    /// whatever *is* playing for several seconds, audible as a skip and
    /// invisible to every counter Lempi already had, because the stall never
    /// touches the output ring at all.
    ///
    /// Lives in `player_settings`, the same table the settings round-trip
    /// uses, but kept out of `Settings` deliberately: nothing in the panel
    /// reads this back, and folding it into that struct's whole-round-trip
    /// contract would ask `every_setting_survives_a_round_trip` to police a
    /// field the panel does not show.
    pub fn save_speaker_address(&self, address: &str) -> Result<(), DbError> {
        self.conn
            .execute(
                "INSERT INTO player_settings (key, value, updated_at)
                 VALUES ('speaker_address', ?1, datetime('now'))
                 ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = excluded.updated_at",
                rusqlite::params![address],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// The speaker last chosen through `use` or `pair`, if any
    /// `[PI3-AIM-020]`, `[REQ-VIS-260]`. `None` on a library where nothing
    /// has ever been chosen this way -- the caller's job is to fall back to
    /// doing nothing, not to invent an address.
    pub fn load_speaker_address(&self) -> Option<String> {
        self.conn
            .query_row(
                "SELECT value FROM player_settings WHERE key = 'speaker_address'",
                [],
                |r| r.get(0),
            )
            .ok()
    }

    /// The appliance's status LED `[PI3-LED-010]` -- one of four modes
    /// (`on`, `wifi`, `off`, `default`; see `player/src/bluetooth.rs`'s
    /// `set_led` for what each actually does to the hardware). Same table,
    /// same reasoning as `save_speaker_address` for living outside
    /// `Settings`: this has nothing to do with playback, and every reader
    /// of it (a boot-time script, one settings-panel control) reads it
    /// directly rather than through a live snapshot.
    ///
    /// Rejects anything outside the closed set **before** it reaches the
    /// database -- the same discipline `set_preference`'s `subject_kind`
    /// enforces, so a bad value cannot sit in storage waiting to confuse
    /// the boot-time script that reads it back with no one watching.
    pub fn save_led_mode(&self, mode: &str) -> Result<(), DbError> {
        if !matches!(mode, "on" | "wifi" | "off" | "default") {
            return Err(DbError::Query(format!("not a valid led mode: {mode:?}")));
        }
        self.conn
            .execute(
                "INSERT INTO player_settings (key, value, updated_at)
                 VALUES ('led_mode', ?1, datetime('now'))
                 ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = excluded.updated_at",
                rusqlite::params![mode],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Defaults **on** -- the original request, honored directly. A library
    /// that has never touched this setting shows a solid light, not
    /// today's Wi-Fi indicator; switching to `wifi` is one settings-panel
    /// choice away `[PI3-LED-010]`.
    pub fn load_led_mode(&self) -> String {
        self.conn
            .query_row("SELECT value FROM player_settings WHERE key = 'led_mode'", [], |r| {
                r.get::<_, String>(0)
            })
            .unwrap_or_else(|_| "on".into())
    }

    /// The brightness `led_mode = "on"` shows, 1-100 `[PI3-LED-010]`.
    /// Meaningless for the other three modes, but always stored and always
    /// readable regardless of the current mode -- switching back to `on`
    /// later should not have forgotten what it was set to.
    pub fn save_led_brightness(&self, pct: u8) -> Result<(), DbError> {
        let pct = pct.clamp(1, 100);
        self.conn
            .execute(
                "INSERT INTO player_settings (key, value, updated_at)
                 VALUES ('led_brightness_pct', ?1, datetime('now'))
                 ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = excluded.updated_at",
                rusqlite::params![pct.to_string()],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Defaults to 100 -- full brightness, matching `load_led_mode`'s own
    /// default `[PI3-LED-010]`.
    pub fn load_led_brightness(&self) -> u8 {
        self.conn
            .query_row(
                "SELECT value FROM player_settings WHERE key = 'led_brightness_pct'",
                [],
                |r| r.get::<_, String>(0),
            )
            .ok()
            .and_then(|v| v.parse::<u8>().ok())
            .map(|v| v.clamp(1, 100))
            .unwrap_or(100)
    }

    /// The programme chosen by hand, persisted so it survives a restart
    /// rather than reverting to time-of-day selection every power cycle
    /// `[SPEC-DIR-185]`. Written whenever `set_program` is asked for a real
    /// id; cleared (deleted, not written as empty) when asked for `auto`,
    /// so `load_manual_program` returning `None` means the same thing on
    /// read as it does when nothing has ever been chosen.
    pub fn save_manual_program(&self, id: Option<i64>) -> Result<(), DbError> {
        match id {
            Some(id) => self
                .conn
                .execute(
                    "INSERT INTO player_settings (key, value, updated_at)
                     VALUES ('manual_program', ?1, datetime('now'))
                     ON CONFLICT(key) DO UPDATE SET
                         value = excluded.value, updated_at = excluded.updated_at",
                    rusqlite::params![id.to_string()],
                )
                .map(|_| ())
                .map_err(|e| DbError::Query(e.to_string())),
            None => self
                .conn
                .execute("DELETE FROM player_settings WHERE key = 'manual_program'", [])
                .map(|_| ())
                .map_err(|e| DbError::Query(e.to_string())),
        }
    }

    /// `None` both when nothing has ever been chosen and when a stored value
    /// no longer parses -- either way there is nothing to override the clock
    /// with, and `Programs::active`'s own stale-id fallback already handles
    /// an id that no longer exists.
    pub fn load_manual_program(&self) -> Option<i64> {
        self.conn
            .query_row("SELECT value FROM player_settings WHERE key = 'manual_program'", [], |r| {
                r.get::<_, String>(0)
            })
            .ok()
            .and_then(|v| v.parse::<i64>().ok())
    }

    /// Set or clear "flag this for review" on a recording or a passage
    /// `[REQ-VIS-265]`. A plain toggle, not a decision: unlike `id_reviews`
    /// and its siblings, there is nothing here to apply and nothing to
    /// refuse -- checking and unchecking the box are the same operation with
    /// the state reversed, at any time, by design.
    pub fn set_flag(&self, subject_kind: &str, subject_id: &str, flagged: bool) -> Result<(), DbError> {
        if flagged {
            self.conn
                .execute(
                    "INSERT INTO listener_flags (subject_kind, subject_id, flagged_at)
                     VALUES (?1, ?2, datetime('now'))
                     ON CONFLICT(subject_kind, subject_id) DO UPDATE SET
                         flagged_at = excluded.flagged_at",
                    rusqlite::params![subject_kind, subject_id],
                )
                .map(|_| ())
        } else {
            self.conn
                .execute(
                    "DELETE FROM listener_flags WHERE subject_kind = ?1 AND subject_id = ?2",
                    rusqlite::params![subject_kind, subject_id],
                )
                .map(|_| ())
        }
        .map_err(|e| DbError::Query(e.to_string()))
    }

    /// A subject's own hand-tuned `rotation`/`recovery`/`restraint`
    /// `[SPEC-DIR-115]`, `[REQ-VIS-290]` -- `None` per field means "not
    /// tuned, use the Program Director's own default", never a fabricated
    /// zero. Absence of the row itself (never edited) reads the same as a
    /// row present with every column NULL, so a caller need not special-case
    /// "no row yet".
    pub fn get_preference(&self, subject_kind: &str, subject_id: &str) -> Result<PreferenceRow, DbError> {
        self.conn
            .query_row(
                "SELECT rotation, recovery, restraint FROM listener_preferences \
                 WHERE subject_kind = ?1 AND subject_id = ?2",
                rusqlite::params![subject_kind, subject_id],
                |r| Ok(PreferenceRow { rotation: r.get(0)?, recovery: r.get(1)?, restraint: r.get(2)? }),
            )
            .or_else(|e| match e {
                rusqlite::Error::QueryReturnedNoRows => Ok(PreferenceRow::default()),
                other => Err(DbError::Query(other.to_string())),
            })
    }

    /// Write a subject's tuning, upserting the same way `set_flag` does
    /// `[SPEC-DIR-115]`, `[REQ-VIS-290]`. Each field is independent: a
    /// caller passing `None` for one leaves that column exactly as stored
    /// (does **not** clear it) -- `reset_preference` below is the explicit,
    /// separate act of clearing one back to "use the default", so a save
    /// that only touched the rotation slider can never silently blank
    /// restraint it never looked at.
    pub fn set_preference(
        &self,
        subject_kind: &str,
        subject_id: &str,
        rotation: Option<f64>,
        recovery: Option<f64>,
        restraint: Option<f64>,
    ) -> Result<(), DbError> {
        self.conn
            .execute(
                "INSERT INTO listener_preferences \
                     (subject_kind, subject_id, rotation, recovery, restraint, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, datetime('now')) \
                 ON CONFLICT(subject_kind, subject_id) DO UPDATE SET \
                     rotation   = COALESCE(?3, listener_preferences.rotation), \
                     recovery   = COALESCE(?4, listener_preferences.recovery), \
                     restraint  = COALESCE(?5, listener_preferences.restraint), \
                     updated_at = excluded.updated_at",
                rusqlite::params![subject_kind, subject_id, rotation, recovery, restraint],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Clear one field back to "use the default" -- the one operation
    /// `set_preference`'s own `COALESCE` deliberately cannot perform, since
    /// passing `None` there means "unchanged", not "cleared" `[REQ-VIS-290]`.
    /// A no-op, not an error, against a subject with no row yet: there is
    /// nothing to clear, and the default already applies.
    pub fn reset_preference(&self, subject_kind: &str, subject_id: &str, field: &str) -> Result<(), DbError> {
        let column = match field {
            "rotation" => "rotation",
            "recovery" => "recovery",
            "restraint" => "restraint",
            other => return Err(DbError::Query(format!("unknown preference field {other:?}"))),
        };
        self.conn
            .execute(
                &format!(
                    "UPDATE listener_preferences SET {column} = NULL, updated_at = datetime('now') \
                     WHERE subject_kind = ?1 AND subject_id = ?2"
                ),
                rusqlite::params![subject_kind, subject_id],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Every registered special, with both values `[SPEC-PREF-080]`.
    ///
    /// The registry is `listener_occasions` -- the same table the Program
    /// Director already reads its curves from, so "what a listener can tag a
    /// recording with" and "what actually changes selection" cannot drift
    /// apart into two lists. A new special is rows in that table and needs no
    /// edit here, which is `[SPEC-DIR-130]`'s own rule applied one layer up.
    ///
    /// A missing `listener_occasions` is an empty list, not an error -- the
    /// same posture `load_occasions` takes: a database with no occasions
    /// defined simply has no specials to offer, and the panel shows the three
    /// tuning sliders alone.
    pub fn list_specials(&self, subject_id: &str) -> Result<Vec<SpecialRow>, DbError> {
        let Ok(mut stmt) = self.conn.prepare(
            "SELECT o.characteristic, o.class, o.label, \
                    (SELECT f.value FROM __LIB__.flavor f \
                      WHERE f.subject_kind = 'recording' AND f.subject_id = ?1 \
                        AND f.characteristic = o.characteristic AND f.class = o.class), \
                    (SELECT c.value FROM listener_characteristics c \
                      WHERE c.subject_kind = 'recording' AND c.subject_id = ?1 \
                        AND c.characteristic = o.characteristic AND c.class = o.class) \
             FROM listener_occasions o \
             ORDER BY COALESCE(o.label, o.characteristic), o.class",
        ) else {
            return Ok(Vec::new());
        };
        let rows = stmt
            .query_map(rusqlite::params![subject_id], |r| {
                let characteristic: String = r.get(0)?;
                let label: Option<String> = r.get(2)?;
                Ok(SpecialRow {
                    label: label
                        .filter(|l| !l.trim().is_empty())
                        .unwrap_or_else(|| derived_label(&characteristic)),
                    characteristic,
                    class: r.get(1)?,
                    inherited: r.get(3)?,
                    value: r.get(4)?,
                })
            })
            .map_err(|e| DbError::Query(e.to_string()))?;
        rows.collect::<rusqlite::Result<Vec<_>>>().map_err(|e| DbError::Query(e.to_string()))
    }

    /// Set one special on one recording `[SPEC-PREF-085]`.
    ///
    /// Unlike `set_preference` beside it, every call names exactly one
    /// `(characteristic, class)` and carries a real value, so there is no
    /// "leave this one alone" case to encode -- the caller simply does not
    /// call this for the specials it did not touch.
    pub fn set_special(
        &self,
        subject_id: &str,
        characteristic: &str,
        class: &str,
        value: f64,
    ) -> Result<(), DbError> {
        if !(0.0..=1.0).contains(&value) {
            return Err(DbError::Query(format!("{characteristic}/{class} must be between 0 and 1")));
        }
        self.conn
            .execute(
                "INSERT INTO listener_characteristics \
                     (subject_kind, subject_id, characteristic, class, value, updated_at) \
                 VALUES ('recording', ?1, ?2, ?3, ?4, datetime('now')) \
                 ON CONFLICT(subject_kind, subject_id, characteristic, class) DO UPDATE SET \
                     value = excluded.value, updated_at = excluded.updated_at",
                rusqlite::params![subject_id, characteristic, class, value],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Drop the listener's own value, so the inherited `flavor` one applies
    /// again `[SPEC-PREF-085]`.
    ///
    /// A delete rather than a write of zero, and the difference matters: zero
    /// would say "this recording is definitely not christmasy," permanently
    /// overriding what the migration knew. Deleting says "I have no opinion,"
    /// which is what Reset means everywhere else in this panel.
    pub fn reset_special(&self, subject_id: &str, characteristic: &str, class: &str) -> Result<(), DbError> {
        self.conn
            .execute(
                "DELETE FROM listener_characteristics WHERE subject_kind = 'recording' \
                   AND subject_id = ?1 AND characteristic = ?2 AND class = ?3",
                rusqlite::params![subject_id, characteristic, class],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// What to head the preference panel with `[SPEC-PREF-090]`: title, the
    /// credited artist, and the release -- whichever of them the library
    /// actually knows.
    ///
    /// The artist and album expressions are the ones `library.rs` already
    /// uses for a history row (`HIST_ARTIST_EXPR`, `HIST_ALBUM_EXPR`),
    /// including the `chosen DESC, release_date, title` tie-break that picks
    /// one release out of the thirty-odd a well-known recording appears on.
    /// A subject the catalog has never heard of is three `None`s, not an
    /// error: the panel then keeps showing whatever the caller displayed.
    pub fn subject_naming(&self, kind: &str, id: &str) -> Result<SubjectNaming, DbError> {
        let sql = if kind == "artist" {
            "SELECT name, NULL, NULL FROM __LIB__.artists WHERE mbid = ?1"
        } else {
            "SELECT r.title, \
                    (SELECT a.name FROM __LIB__.recording_artists ra \
                       JOIN __LIB__.artists a ON a.mbid = ra.artist_mbid \
                      WHERE ra.mbid = r.mbid ORDER BY ra.weight DESC, a.name LIMIT 1), \
                    (SELECT rel.title FROM __LIB__.release_recordings rr \
                       JOIN __LIB__.releases rel ON rel.mbid = rr.release_mbid \
                      WHERE rr.mbid = r.mbid \
                      ORDER BY rr.chosen DESC, rel.release_date, rel.title LIMIT 1) \
             FROM __LIB__.recordings r WHERE r.mbid = ?1"
        };
        self.conn
            .query_row(sql, rusqlite::params![id], |r| {
                Ok(SubjectNaming { title: r.get(0)?, artist: r.get(1)?, album: r.get(2)? })
            })
            .or_else(|e| match e {
                rusqlite::Error::QueryReturnedNoRows => Ok(SubjectNaming::default()),
                other => Err(DbError::Query(other.to_string())),
            })
    }

    /// How often a subject has played, broken out by who selected it
    /// `[REQ-VIS-300]`.
    ///
    /// One fetch of every matching `(selected_by, played_at)` pair, then
    /// aggregated here rather than in a cross-tab SQL query that would need
    /// to already know the distinct program names -- the same "a handful
    /// of rows even on a well-used appliance" scale `remote_flags.py`
    /// reasons about for `listener_flags`.
    ///
    /// **All** counts every play in the window, `selected_by` or not --
    /// including the `NULL` rows a library predating this feature is full
    /// of, which is what makes All's count the true ceiling: never less
    /// than any other row, often more. **User** is always present, even at
    /// all-zero, since "never manually played" is itself the answer.
    /// Program rows -- including the `"auto"` sentinel for a genuine
    /// Director pick with no program in force -- appear only if this
    /// subject has at least one such play, sorted by their own all-time
    /// count so the most relevant shows first.
    pub fn play_frequency(&self, kind: &str, id: &str) -> Result<Vec<FrequencyRow>, DbError> {
        let sql = if kind == "artist" {
            "SELECT h.selected_by, h.played_at FROM listener_play_history h \
             JOIN __LIB__.recording_artists ra ON ra.mbid = h.mbid WHERE ra.artist_mbid = ?1"
        } else {
            "SELECT selected_by, played_at FROM listener_play_history WHERE mbid = ?1"
        };
        let mut st = self.conn.prepare(sql).map_err(|e| DbError::Query(e.to_string()))?;
        let rows: Vec<(Option<String>, i64)> = st
            .query_map(rusqlite::params![id], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(|e| DbError::Query(e.to_string()))?
            .collect::<rusqlite::Result<_>>()
            .map_err(|e| DbError::Query(e.to_string()))?;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);
        // Rolling windows, not calendar-aligned -- "the past 24 hours",
        // not "today". `i64::MIN` gives all-time no real lower bound.
        const WINDOWS: [i64; 5] = [86_400, 604_800, 2_592_000, 31_536_000, i64::MIN];

        let mut all = [0i64; 5];
        let mut user = [0i64; 5];
        let mut programs: Vec<(String, [i64; 5])> = Vec::new();
        for (selected_by, played_at) in &rows {
            for (i, window) in WINDOWS.iter().enumerate() {
                if *window != i64::MIN && played_at < &(now - window) {
                    continue;
                }
                all[i] += 1;
                match selected_by.as_deref() {
                    Some("user") => user[i] += 1,
                    Some(program) => {
                        let entry = match programs.iter_mut().find(|(label, _)| label.as_str() == program) {
                            Some(entry) => entry,
                            None => {
                                programs.push((program.to_string(), [0; 5]));
                                programs.last_mut().unwrap()
                            }
                        };
                        entry.1[i] += 1;
                    }
                    None => {}
                }
            }
        }
        programs.sort_by(|a, b| b.1[4].cmp(&a.1[4]));

        let mut out = vec![
            FrequencyRow { label: "All".into(), counts: all },
            FrequencyRow { label: "User".into(), counts: user },
        ];
        out.extend(programs.into_iter().map(|(label, counts)| FrequencyRow {
            label: if label == "auto" { "Auto".into() } else { label },
            counts,
        }));
        Ok(out)
    }

    /// Bring settings over from the columns they used to live in.
    ///
    /// Runs once: after the first save every key is present and the copy is
    /// skipped. A library from before the columns existed fails the read and
    /// keeps its defaults, which is the same answer by a shorter road.
    fn adopt_old_settings_columns(conn: &Connection) {
        let already: i64 = conn
            .query_row("SELECT COUNT(*) FROM player_settings", [], |r| r.get(0))
            .unwrap_or(0);
        if already > 0 {
            return;
        }
        let mut moved = 0;
        for key in Settings::KEYS {
            // One column at a time, because a database part-way through the old
            // sequence of ALTERs has some of them and not others.
            // As `Value`, not as `String`: `volume` is REAL and the rest are
            // INTEGER, and asking SQLite for a number as text fails the read
            // rather than converting it.
            let got: Option<rusqlite::types::Value> = conn
                .query_row(
                    &format!("SELECT {key} FROM player_state WHERE id = 1"),
                    [],
                    |r| r.get::<_, rusqlite::types::Value>(0),
                )
                .ok();
            let text = match got {
                Some(rusqlite::types::Value::Integer(i)) => Some(i.to_string()),
                Some(rusqlite::types::Value::Real(f)) => Some(f.to_string()),
                Some(rusqlite::types::Value::Text(t)) => Some(t),
                _ => None,
            };
            if let Some(value) = text {
                let _ = conn.execute(
                    "INSERT OR IGNORE INTO player_settings (key, value, updated_at)
                     VALUES (?1, ?2, datetime('now'))",
                    rusqlite::params![key, value],
                );
                moved += 1;
            }
        }
        if moved > 0 {
            println!("settings: {moved} carried over from the old columns");
        }
    }
    pub fn save(&self, passage_id: Option<i64>, position_ms: u64, playing: bool)
        -> Result<(), DbError>
    {
        self.conn
            .execute(
                "INSERT INTO player_state (id, passage_id, position_ms, playing, updated_at)
                 VALUES (1, ?1, ?2, ?3, datetime('now'))
                 ON CONFLICT(id) DO UPDATE SET
                     passage_id = excluded.passage_id,
                     position_ms = excluded.position_ms,
                     playing = excluded.playing,
                     updated_at = excluded.updated_at",
                rusqlite::params![passage_id, position_ms as i64, playing as i64],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Write the queue down, in play order `[SPEC-DIR-225]`.
    ///
    /// Replaces the whole list rather than diffing it: it is at most a handful
    /// of ids, and a queue that half-updated would name an order that was
    /// never played. One transaction for the same reason `restore` uses one.
    ///
    /// Best-effort at the call site, like every other write on this path --
    /// failing to remember the queue must never interrupt the music.
    pub fn save_queue(&self, ids: &[i64]) -> Result<(), DbError> {
        let q = |e: rusqlite::Error| DbError::Query(e.to_string());
        self.conn.execute_batch("BEGIN IMMEDIATE").map_err(q)?;
        let done = || -> Result<(), DbError> {
            self.conn.execute("DELETE FROM player_queue", []).map_err(q)?;
            for (position, passage_id) in ids.iter().enumerate() {
                self.conn
                    .execute(
                        "INSERT INTO player_queue (position, passage_id, updated_at)
                         VALUES (?1, ?2, datetime('now'))",
                        rusqlite::params![position as i64, passage_id],
                    )
                    .map_err(q)?;
            }
            Ok(())
        }();
        let end = if done.is_ok() { "COMMIT" } else { "ROLLBACK" };
        let _ = self.conn.execute_batch(end);
        done
    }

    /// The queue as it was last written, in play order `[SPEC-DIR-225]`.
    ///
    /// An empty list is the honest answer to every failure here -- a missing
    /// table, an unreadable row, a database written by a player that predates
    /// this. The caller's next step either way is to fill what is short, so a
    /// `Result` would buy it nothing it could act on.
    pub fn load_queue(&self) -> Vec<i64> {
        let Ok(mut q) =
            self.conn.prepare("SELECT passage_id FROM player_queue ORDER BY position")
        else {
            return Vec::new();
        };
        let Ok(rows) = q.query_map([], |r| r.get::<_, i64>(0)) else {
            return Vec::new();
        };
        rows.flatten().collect()
    }

    /// The durable record of one selection `[SPEC-DIR-190]`.
    ///
    /// `detail` is the full decomposition as JSON. A failure here must never
    /// stop the music: the record is for explaining a choice afterwards, not
    /// for making it.
    pub fn record_decision(&self, at: i64, passage_id: i64, detail: &str) -> Result<(), DbError> {
        self.conn
            .execute(
                "INSERT INTO selection_decisions (selected_at, passage_id, detail)
                 VALUES (?1, ?2, ?3)",
                rusqlite::params![at, passage_id, detail],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Record that a passage played `[REQ-PD-110]`.
    ///
    /// `mbid` is stored alongside `passage_id` because six years of history
    /// must survive a rescan that renumbers passages `[SPEC-SC-095]`; the
    /// passage id is the convenience, the MBID is the durable key.
    /// Record a play, keyed by recording MBID `[REQ-PD-112]`.
    ///
    /// Rotation is meaningless without it: an unrecorded play leaves a recording
    /// as eligible as it was before it was heard.
    ///
    /// `heard_ms` is the threshold just crossed, not the final figure -- the
    /// caller corrects it with [`finish_play`](Self::finish_play) once the
    /// passage actually departs `[REQ-VIS-250]`. Returns the row's id so the
    /// caller can do that.
    ///
    /// `selected_by` is the entry's own `QueueEntry::selected_by`
    /// `[REQ-VIS-300]` -- `None` where the caller has nothing to report
    /// (an alternate backend that does not yet track this), `Some("user")`
    /// or a program name where it does. Stored, never inferred here.
    pub fn record_play(
        &self,
        passage_id: i64,
        mbid: Option<&str>,
        heard_ms: u64,
        span_ms: u64,
        selected_by: Option<&str>,
    ) -> Result<i64, DbError> {
        self.conn
            .execute(
                "INSERT INTO listener_play_history \
                     (played_at, passage_id, mbid, heard_ms, span_ms, selected_by) \
                 VALUES (strftime('%s','now'), ?1, ?2, ?3, ?4, ?5)",
                rusqlite::params![passage_id, mbid, heard_ms, span_ms, selected_by],
            )
            .map_err(|e| DbError::Query(e.to_string()))?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Fill in how much of a play was actually heard, now that the passage has
    /// departed and the figure will not grow again `[REQ-VIS-250]`.
    pub fn finish_play(&self, play_id: i64, heard_ms: u64) -> Result<(), DbError> {
        self.conn
            .execute(
                "UPDATE listener_play_history SET heard_ms = ?1 WHERE play_id = ?2",
                rusqlite::params![heard_ms, play_id],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// Record a rejection, for suppression and nothing else `[SPEC-PLAY-050]`.
    ///
    /// A passage the listener declined did not play, so it must not enter
    /// `listener_play_history` — but offering it back an hour later is its own
    /// kind of wrong. This is the narrowest record that fixes that: a timestamp
    /// per recording per kind, read only by the eligibility gate, feeding no
    /// ramp, no artist damping and no count.
    ///
    /// **The window is applied when the gate runs, not stored here**
    /// `[SPEC-PLAY-055]`. Keeping the instant and the kind rather than an expiry
    /// is what lets the listener change a window and have it apply to what they
    /// have already rejected; an expiry computed under yesterday's setting would
    /// outlive the setting itself.
    /// `heard_ms`/`span_ms` are `None` for a dequeue -- the passage never
    /// sounded, so there is nothing to report a percentage of
    /// `[REQ-VIS-250]`.
    pub fn record_rejection(
        &self,
        kind: Rejection,
        passage_id: i64,
        mbid: Option<&str>,
        heard_ms: Option<u64>,
        span_ms: Option<u64>,
    ) -> Result<(), DbError> {
        self.conn
            .execute(
                "INSERT INTO listener_rejections \
                     (rejected_at, kind, passage_id, mbid, heard_ms, span_ms) \
                 VALUES (strftime('%s','now'), ?1, ?2, ?3, ?4, ?5)",
                rusqlite::params![kind.as_str(), passage_id, mbid, heard_ms, span_ms],
            )
            .map(|_| ())
            .map_err(|e| DbError::Query(e.to_string()))
    }

    /// How many plays are on record. For diagnostics, and for anything that
    /// needs to see the ledger move without reading it all.
    pub fn play_count(&self) -> i64 {
        self.conn
            .query_row("SELECT COUNT(*) FROM listener_play_history", [], |r| r.get(0))
            .unwrap_or(0)
    }

    /// When each recording was last rejected in this way. Only the most recent
    /// matters: suppression is a window, not an accumulation.
    pub fn last_rejected(
        &self,
        kind: Rejection,
    ) -> Result<std::collections::HashMap<String, i64>, DbError> {
        let mut q = self
            .conn
            .prepare(
                "SELECT mbid, MAX(rejected_at) FROM listener_rejections                  WHERE mbid IS NOT NULL AND kind = ?1 GROUP BY mbid",
            )
            .map_err(|e| DbError::Query(e.to_string()))?;
        let rows = q
            .query_map([kind.as_str()], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
            })
            .map_err(|e| DbError::Query(e.to_string()))?;
        Ok(rows.flatten().collect())
    }


    /// The saved resume point, or `None` on a first run.
    pub fn load(&self) -> Result<Option<(Option<i64>, u64, bool)>, DbError> {
        self.conn
            .query_row(
                "SELECT passage_id, position_ms, playing FROM player_state WHERE id = 1",
                [],
                |r| {
                    Ok((
                        r.get::<_, Option<i64>>(0)?,
                        r.get::<_, i64>(1)?.max(0) as u64,
                        r.get::<_, i64>(2)? != 0,
                    ))
                },
            )
            .map(Some)
            .or_else(|e| match e {
                rusqlite::Error::QueryReturnedNoRows => Ok(None),
                other => Err(DbError::Query(other.to_string())),
            })
    }

}



#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::test_support::*;
    #[cfg(feature = "vipunen-support")]
    use crate::db::Library; // several tests read back through Library, by design

    /// A decided passage comes back carrying its judgement, so it can be found
    /// again and withdrawn. It is a separate grade on the page and off by
    /// default, so the working queue still shortens as it is answered.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn a_decision_is_remembered_and_can_be_withdrawn() {
        let tmp = std::env::temp_dir().join(format!("lempi-rev-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = reviewable();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        let lib = Library::open(&tmp).unwrap();
        assert!(lib.review_queue(50).unwrap()[0].decision.is_none());

        store.record_review(2, "kept", None, None).unwrap();
        let q = lib.review_queue(50).unwrap();
        assert_eq!(q[0].decision.as_deref(), Some("kept"),
                   "a decided card must still be findable");
        assert!(!q[0].applied, "recorded is not applied");
        assert_eq!(lib.review_progress().decided, 1);

        // Undo: the judgement is withdrawn and the passage is a question again.
        store.clear_review(2).unwrap();
        assert!(lib.review_queue(50).unwrap()[0].decision.is_none());
        assert_eq!(lib.review_progress().decided, 0);
        assert!(store.clear_review(2).is_err(), "nothing to withdraw twice");

        store.record_review(2, "kept", None, None).unwrap();

        // Changing one's mind overwrites rather than duplicating.
        store.record_review(2, "reassigned", Some("aaaaaaaa-0000-0000-0000-000000000002"), None).unwrap();
        assert_eq!(lib.review_progress().decided, 1);
        let (d, m): (String, Option<String>) = store
            .conn
            .query_row("SELECT decision, chosen_mbid FROM id_reviews WHERE passage_id = 2",
                       [], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap();
        assert_eq!(d, "reassigned");
        assert_eq!(m.as_deref(), Some("aaaaaaaa-0000-0000-0000-000000000002"));
        let _ = std::fs::remove_file(&tmp);
    }

    /// A judgement already written into the library cannot be withdrawn by
    /// deleting the record of it: that would leave `passage_recordings`
    /// changed with nothing left saying what it used to be or why. Undo on the
    /// page refuses, and says to use `apply_reviews --revert`, which restores
    /// the old id and clears the row together.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn an_applied_decision_cannot_be_quietly_withdrawn() {
        let tmp = std::env::temp_dir().join(format!("lempi-rev3-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = reviewable();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        let lib = Library::open(&tmp).unwrap();
        let new_id = "aaaaaaaa-0000-0000-0000-000000000002";
        store.record_review(2, "reassigned", Some(new_id), None).unwrap();

        // What the old id was is captured when the decision is made, because
        // applying it overwrites the only other copy.
        let previous: Option<String> = store
            .conn
            .query_row("SELECT previous_mbid FROM id_reviews WHERE passage_id = 2",
                       [], |r| r.get(0))
            .unwrap();
        assert_eq!(previous.as_deref(), Some("aaaaaaaa-0000-0000-0000-000000000001"));

        // `apply_reviews` stamps this once the library has been changed.
        store.conn.execute(
            "UPDATE id_reviews SET applied_at = datetime('now') WHERE passage_id = 2", [])
            .unwrap();

        assert!(store.clear_review(2).is_err(), "an applied decision must not just vanish");
        assert!(store.record_review(2, "kept", None, None).is_err(),
                "nor be silently overwritten by a different answer");
        let q = lib.review_queue(50).unwrap();
        assert!(q[0].applied, "the page has to be able to show it as applied");
    }

    /// An artist correction is independent of the recording decision
    /// `[SPEC-SUI-197]` -- it can be made, found again and withdrawn without
    /// `decision` ever being set, and captures the recording it was made
    /// against (the heavier of passage 2's two links, exactly as
    /// `previous_mbid` above captures the same thing for a reassignment).
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn an_artist_correction_is_recorded_and_can_be_withdrawn() {
        let tmp = std::env::temp_dir().join(format!("lempi-art-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = reviewable();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        let lib = Library::open(&tmp).unwrap();
        assert!(lib.review_queue(50).unwrap()[0].artist_review.is_none());

        store.record_artist_review(2, "artist-mbid-1", "The Real Artist").unwrap();
        let q = lib.review_queue(50).unwrap();
        assert_eq!(q[0].artist_review.as_deref(), Some("The Real Artist"));
        assert!(!q[0].artist_review_applied);
        assert_eq!(q[0].decision, None, "an artist correction must not touch `decision`");

        let recording = "aaaaaaaa-0000-0000-0000-000000000001";
        let prev: Option<String> = store
            .conn
            .query_row(
                "SELECT previous_artist_mbid FROM artist_reviews WHERE recording_mbid = ?1",
                [recording], |r| r.get(0))
            .expect("the row must be keyed by recording_mbid, like previous_mbid does");
        assert_eq!(prev, None, "this recording has no existing credit to capture");

        store.clear_artist_review(2).unwrap();
        assert!(lib.review_queue(50).unwrap()[0].artist_review.is_none());
        assert!(store.clear_artist_review(2).is_err(), "nothing to withdraw twice");

        // Committing twice updates the one row, the same `ON CONFLICT` shape
        // every other decision here uses.
        store.record_artist_review(2, "artist-mbid-1", "First Try").unwrap();
        store.record_artist_review(2, "artist-mbid-2", "Better Name").unwrap();
        let n: i64 = store
            .conn
            .query_row("SELECT COUNT(*) FROM artist_reviews", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n, 1, "a second commit must update, not add a row");
        assert_eq!(lib.review_queue(50).unwrap()[0].artist_review.as_deref(), Some("Better Name"));

        // Applied is a different, non-withdrawable state, the same as a
        // recording reassignment.
        store.conn.execute(
            "UPDATE artist_reviews SET applied_at = datetime('now') WHERE recording_mbid = ?1",
            [recording])
            .unwrap();
        assert!(lib.review_queue(50).unwrap()[0].artist_review_applied);
        assert!(store.clear_artist_review(2).is_err(),
                "an applied correction must not just vanish");
        assert!(store.record_artist_review(2, "artist-mbid-3", "Yet Another").is_err(),
                "nor be silently overwritten by a different answer");
    }

    /// The bug keying by `passage_id` would have been: two passages sharing
    /// one recording each get their own, silently conflicting correction row.
    /// Keyed by `recording_mbid`, a second passage naming the same recording
    /// finds and updates the SAME row -- an unapplied correction is still a
    /// mutable draft regardless of which card it is edited from -- rather
    /// than starting an independent one nothing would ever reconcile.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn two_passages_sharing_a_recording_share_one_artist_correction() {
        let tmp = std::env::temp_dir().join(format!("lempi-art3-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = reviewable();
            // Passage 3 now ALSO names passage 2's recording -- the "same
            // song, two files" case a correction must not see as two
            // separate questions.
            c.execute(
                "INSERT INTO passage_recordings VALUES \
                    (3,'aaaaaaaa-0000-0000-0000-000000000001',1.0,'s')",
                [],
            )
            .unwrap();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();

        store.record_artist_review(2, "artist-mbid-1", "The Real Artist").unwrap();
        let n: i64 = store
            .conn
            .query_row("SELECT COUNT(*) FROM artist_reviews", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n, 1);

        // Reached from the OTHER passage, the SAME recording's correction is
        // what is found and updated -- an unapplied correction is still a
        // mutable draft regardless of which card it is edited from, the same
        // as changing your mind about a reassignment before it is applied.
        // What it must NOT do is create a second, independent row.
        store.record_artist_review(3, "artist-mbid-2", "A Different Answer").unwrap();
        let n2: i64 = store
            .conn
            .query_row("SELECT COUNT(*) FROM artist_reviews", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n2, 1, "a second passage's edit must update the shared row, not add one");
        let name: String = store
            .conn
            .query_row(
                "SELECT artist_name FROM artist_reviews WHERE recording_mbid = 'aaaaaaaa-0000-0000-0000-000000000001'",
                [], |r| r.get(0))
            .unwrap();
        assert_eq!(name, "A Different Answer");
        let _ = std::fs::remove_file(&tmp);
    }

    /// The recording has to exist before its credit can be corrected -- "the
    /// recording is right" presupposes one was chosen.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn an_artist_correction_needs_a_recording_to_correct() {
        let lib_conn = reviewable();
        // Passage 3 in `reviewable()`'s fixture has a link; an entirely
        // unlinked passage id (none in the fixture) is what this checks.
        let tmp = std::env::temp_dir().join(format!("lempi-art2-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        lib_conn.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        let store = PlayerStore::open(&tmp).unwrap();
        assert!(store.record_artist_review(99999, "artist-mbid-1", "Nobody").is_err());
    }

    /// The resume-save interval persists like the others `[REQ-VIS-155]`, and
    /// a value from disk is clamped on the way in -- every one of these writes
    /// lands on the appliance's most volatile partition `[PI-C-010]`, so the
    /// setting that governs how many there are must not be settable to zero by
    /// a corrupted row.
    /// **Every setting survives a save and a load** `[SPEC-SC-099]`.
    ///
    /// This is what makes `Settings::KEYS` the single source rather than one of
    /// three lists that have to agree. A field added to the struct and to
    /// `value_of` but forgotten in `set` loses itself on the next restart, and
    /// silently — which is exactly what the old positional `?11` / `r.get(10)`
    /// pairing did whenever a column was added in the wrong place.
    #[test]
    fn every_setting_survives_a_round_trip() {
        // Deliberately unlike the defaults in every field, so a setting that
        // quietly falls back to its default fails here rather than passing by
        // coincidence.
        let want = Settings {
            echo_join_now: false,
            echo_join_bias: "700,690,710".to_string(),
            echo_delay_trim_ms: -40,
            echo_follow_host: "bose".to_string(),
            volume: 0.375,
            skip_fade_ms: 1_234,
            skip_lead_ms: 321,
            resume_save_ms: 7_000,
            skip_suppress_h: 99,
            dequeue_suppress_h: 7,
            queue_depth: 9,
            sample_interval_ms: 1_500,
            cue_sheets: true,
            covers: true,
            lyrics_cache: true,
            lyrics_sidecar: true,
        };

        let mut got = Settings::default();
        for key in Settings::KEYS {
            let value =
                want.value_of(key).unwrap_or_else(|| panic!("{key} has no value to store"));
            got.set(key, &value);
        }

        assert_eq!(got, want, "a setting was written but not read back");
    }

    /// The other direction: a key listed with no field behind it.
    #[test]
    fn the_key_list_covers_the_whole_struct() {
        let s = Settings::default();
        for key in Settings::KEYS {
            assert!(s.value_of(key).is_some(), "{key} is listed but stores nothing");
        }
        assert!(s.value_of("invented").is_none(), "and an unknown key stores nothing");
    }

    #[test]
    fn the_resume_interval_persists_and_is_clamped() {
        let tmp = std::env::temp_dir().join(format!("lempi-rs2-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        let store = PlayerStore::open(&tmp).unwrap();
        assert!(store.load_settings().is_none(), "nothing saved yet");

        let want = Settings {
            echo_join_now: true,
            echo_join_bias: "-12,34".to_string(),
            echo_delay_trim_ms: 125,
            echo_follow_host: String::new(),
            volume: 0.5,
            skip_fade_ms: 2_000,
            skip_lead_ms: 500,
            resume_save_ms: 30_000,
            skip_suppress_h: 96,
            dequeue_suppress_h: 12,
            queue_depth: 7,
            sample_interval_ms: 3_000,
            cue_sheets: true,
            covers: true,
            lyrics_cache: true,
            lyrics_sidecar: true,
        };
        store.save_settings(&want).unwrap();
        let got = store.load_settings().unwrap();
        assert!((got.volume - want.volume).abs() < 1e-6);
        assert_eq!(
            (got.skip_fade_ms, got.skip_lead_ms, got.resume_save_ms),
            (want.skip_fade_ms, want.skip_lead_ms, want.resume_save_ms)
        );
        assert_eq!(
            (got.skip_suppress_h, got.dequeue_suppress_h, got.queue_depth, got.sample_interval_ms),
            (96, 12, 7, 3_000)
        );
        assert!(got.cue_sheets, "a choice about the music folder must survive a restart");

        // A library written before a column existed reads as THAT field's
        // default, not as zero, and not by failing the whole read.
        let d = Settings::default();
        // A setting that is not stored falls back to **its own** default, not
        // to zero and not by failing the whole read. Under the old columns this
        // was a NULL; now it is simply a key that is not there.
        for (key, expect) in [
            ("resume_save_ms", d.resume_save_ms),
            ("skip_suppress_h", d.skip_suppress_h),
            ("dequeue_suppress_h", d.dequeue_suppress_h),
            ("sample_interval_ms", d.sample_interval_ms),
        ] {
            store
                .conn
                .execute("DELETE FROM player_settings WHERE key = ?1", [key])
                .unwrap();
            let got = store.load_settings().unwrap();
            let actual = match key {
                "resume_save_ms" => got.resume_save_ms,
                "skip_suppress_h" => got.skip_suppress_h,
                "dequeue_suppress_h" => got.dequeue_suppress_h,
                _ => got.sample_interval_ms,
            };
            assert_eq!(actual, expect, "{key} must fall back to its own default");
        }
        store.conn.execute("DELETE FROM player_settings WHERE key = 'queue_depth'", []).unwrap();
        assert_eq!(store.load_settings().unwrap().queue_depth, d.queue_depth);

        // And an unreadable value costs that setting only.
        store
            .conn
            .execute("UPDATE player_settings SET value = 'nonsense' WHERE key = 'skip_fade_ms'", [])
            .unwrap();
        let got = store.load_settings().unwrap();
        assert_eq!(got.skip_fade_ms, d.skip_fade_ms, "unreadable falls back");
        assert_eq!(got.skip_lead_ms, 500, "and the rest still load");

        let _ = std::fs::remove_file(&tmp);
    }

    /// The vocabulary is enforced where it is written, because that is the only
    /// place it can be. A reassignment with nothing to reassign to is the case
    /// a careless request would produce.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn a_reassignment_must_name_a_recording() {
        let tmp = std::env::temp_dir().join(format!("lempi-rev2-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = reviewable();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        assert!(store.record_review(2, "reassigned", None, None).is_err());
        assert!(store.record_review(2, "reassigned", Some(""), None).is_err());
        assert!(store.record_review(2, "nonsense", None, None).is_err());
        assert!(store.record_review(2, "deferred", None, None).is_ok());
        let _ = std::fs::remove_file(&tmp);
    }

    /// A boundary edit is readable back through `Library`, and committing
    /// twice updates the one row rather than adding a second `[SPEC021 §2]`.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn a_boundary_edit_is_recorded_and_updates_in_place() {
        let tmp = std::env::temp_dir().join(format!("lempi-bnd-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = fixture();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        store.record_boundary_review(2, 2000, 290_000, 500, 1500, -1.0, 20, 20, "exponential", "exponential").unwrap();

        let lib = Library::open(&tmp).unwrap();
        let got = lib.boundary_review(2).expect("a committed edit must be readable back");
        assert_eq!((got.start_ms, got.end_ms), (2000, 290_000));
        assert_eq!(got.lead_in_ms, Some(500));
        assert_eq!(got.lead_out_ms, Some(1500));
        assert!((got.gain_db.unwrap() - -1.0).abs() < 1e-9);
        assert_eq!(got.fade_in_ms, Some(20));
        assert_eq!(got.fade_out_ms, Some(20));
        assert_eq!(got.fade_in_curve.as_deref(), Some("exponential"));
        assert_eq!(got.fade_out_curve.as_deref(), Some("exponential"));

        store.record_boundary_review(2, 2100, 291_000, 400, 1400, -0.5, 20, 20, "exponential", "exponential").unwrap();
        let n: i64 = store
            .conn
            .query_row("SELECT COUNT(*) FROM boundary_reviews", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n, 1, "a second commit must update, not add a row");
        let got2 = Library::open(&tmp).unwrap().boundary_review(2).unwrap();
        assert_eq!(got2.start_ms, 2100, "the update must actually take");

        let _ = std::fs::remove_file(&tmp);
    }

    /// The pre-edit span is captured for sync `[SPEC-DF-102]`, and stays
    /// pinned to the true original across a second commit -- the same
    /// "read the live table, not our own prior row" trick `previous_mbid`
    /// already relies on.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn a_boundary_edit_captures_its_pre_edit_span_for_sync() {
        let tmp = std::env::temp_dir().join(format!("lempi-bnd3-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = fixture();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        store.record_boundary_review(2, 2000, 290_000, 500, 1500, -1.0, 20, 20, "exponential", "exponential").unwrap();

        #[allow(clippy::type_complexity)]
        let row = |c: &Connection| -> (String, String, i64, i64, Option<i64>, Option<i64>, Option<f64>, Option<i64>, Option<i64>, Option<String>, Option<String>) {
            c.query_row(
                "SELECT audio_md5, orig_kind, orig_start_ms, orig_end_ms, \
                        orig_lead_in_ms, orig_lead_out_ms, orig_gain_db, \
                        orig_fade_in_ms, orig_fade_out_ms, orig_fade_in_curve, orig_fade_out_curve \
                   FROM boundary_reviews WHERE passage_id = 2",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?,
                        r.get(7)?, r.get(8)?, r.get(9)?, r.get(10)?)),
            )
            .unwrap()
        };
        let (md5, kind, os, oe, oli, olo, og, ofi, ofo, ofic, ofoc) = row(&store.conn);
        assert_eq!((md5.as_str(), kind.as_str()), ("md5", "radio"));
        assert_eq!((os, oe), (1200, 298_000), "must be the passage's ORIGINAL span, not the draft");
        assert_eq!((oli, olo), (Some(3000), Some(4000)));
        assert!((og.unwrap() - -2.5).abs() < 1e-9);
        assert_eq!((ofi, ofo), (Some(20), Some(20)), "the passage's original fade, from the fixture default");
        assert_eq!((ofic.as_deref(), ofoc.as_deref()), (Some("exponential"), Some("exponential")));

        // A second commit, before anything applies it, must not move the
        // baseline to the FIRST draft's values -- `passages` itself has not
        // changed, so re-reading it gives the same true original.
        store.record_boundary_review(2, 2100, 291_000, 400, 1400, -0.5, 20, 20, "exponential", "exponential").unwrap();
        let (_, _, os2, oe2, ..) = row(&store.conn);
        assert_eq!((os2, oe2), (1200, 298_000), "the baseline must not drift on a re-commit");

        let _ = std::fs::remove_file(&tmp);
    }

    /// Changing a decision already folded into `passages` would corrupt the
    /// baseline `[SPEC-DF-102]` sync depends on, the same reason
    /// The queue round-trips in play order, and each save replaces the whole
    /// list `[SPEC-DIR-225]`. A merge would leave a shortened queue with a
    /// tail of passages that are no longer waiting for anything.
    #[test]
    fn the_queue_is_remembered_in_order_and_replaced_whole() {
        let tmp = std::env::temp_dir().join(format!("lempi-queue-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        let store = PlayerStore::open(&tmp).unwrap();

        // Never written is an empty queue, not a failure -- the same posture
        // `load_settings` takes, for a caller whose next step is to fill
        // whatever is short either way.
        assert!(store.load_queue().is_empty());

        store.save_queue(&[7, 3, 11, 3]).unwrap();
        assert_eq!(store.load_queue(), vec![7, 3, 11, 3],
                   "play order is the whole of what is stored, and a repeat is legal");

        store.save_queue(&[5]).unwrap();
        assert_eq!(store.load_queue(), vec![5], "a shorter queue leaves no tail");

        store.save_queue(&[]).unwrap();
        assert!(store.load_queue().is_empty(), "an emptied queue is emptied");
        let _ = std::fs::remove_file(&tmp);
    }

    /// `record_review` refuses the equivalent case for a reassignment.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn a_boundary_edit_cannot_be_recommitted_once_applied() {
        let tmp = std::env::temp_dir().join(format!("lempi-bnd4-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = fixture();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        store.record_boundary_review(2, 2000, 290_000, 500, 1500, -1.0, 20, 20, "exponential", "exponential").unwrap();
        store
            .conn
            .execute(
                "UPDATE boundary_reviews SET applied_at = datetime('now') WHERE passage_id = 2",
                [],
            )
            .unwrap();
        assert!(store.record_boundary_review(2, 2200, 292_000, 300, 1300, -0.2, 20, 20, "exponential", "exponential").is_err());
        let _ = std::fs::remove_file(&tmp);
    }

    /// `start_ms >= end_ms` is nonsense no caller should be able to write,
    /// validated where it is written because this is the only writer.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn a_boundary_edit_cannot_invert_start_and_end() {
        let tmp = std::env::temp_dir().join(format!("lempi-bnd2-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = fixture();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        assert!(store.record_boundary_review(2, 5000, 5000, 0, 0, 0.0, 20, 20, "exponential", "exponential").is_err());
        assert!(store.record_boundary_review(2, 6000, 5000, 0, 0, 0.0, 20, 20, "exponential", "exponential").is_err());
        let _ = std::fs::remove_file(&tmp);
    }

    /// "Accept as-is" writes a `boundary_reviews` row equal to the passage's
    /// own current values `[SPEC024 §7]`, `[SPEC-SA-125]`, readable back
    /// through `Library::segment_queue`'s own `decided` flag -- without
    /// touching `boundary_src`, which stays the cascade's own until
    /// `tools/apply_boundary_reviews.py` next runs `[SPEC-SC-045]`.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn accept_segment_confirms_a_cascade_passage_with_its_own_values() {
        let tmp = std::env::temp_dir().join(format!("lempi-seg-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            // `segmentable()`, not `fixture()`: `segment_queue` below joins
            // through the same naming tables `review_queue` does, and a bare
            // `fixture()` db has none of them.
            let c = segmentable();
            c.execute_batch(
                "INSERT INTO passages VALUES
                     (30,1,'radio',5000,65000,700,900,-3.0,'computed:segment-cascade@v1',15,25,'linear','linear');",
            )
            .unwrap();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        store.accept_segment(30).unwrap();

        let lib = Library::open(&tmp).unwrap();
        let got = lib.boundary_review(30).expect("accept-as-is must leave a readable row");
        assert_eq!((got.start_ms, got.end_ms), (5000, 65000));
        assert_eq!(got.lead_in_ms, Some(700));
        assert_eq!(got.lead_out_ms, Some(900));
        assert!((got.gain_db.unwrap() - -3.0).abs() < 1e-9);
        assert_eq!(got.fade_in_ms, Some(15));
        assert_eq!(got.fade_out_ms, Some(25));
        assert_eq!((got.fade_in_curve.as_deref(), got.fade_out_curve.as_deref()),
                   (Some("linear"), Some("linear")));

        let applied_at: Option<String> = store
            .conn
            .query_row("SELECT applied_at FROM boundary_reviews WHERE passage_id = 30", [], |r| r.get(0))
            .unwrap();
        assert!(applied_at.is_none(),
                "the Rust side records; only tools/apply_boundary_reviews.py stamps applied_at");
        let boundary_src: String = store
            .conn
            .query_row("SELECT boundary_src FROM passages WHERE passage_id = 30", [], |r| r.get(0))
            .unwrap();
        assert_eq!(boundary_src, "computed:segment-cascade@v1",
                    "unchanged until the apply step folds the confirmation in");

        // `[SPEC-SA-124]`'s own criterion is `applied_at`, not merely a row
        // existing -- so the passage still shows here until
        // `tools/apply_boundary_reviews.py` next runs, but now carries
        // `decided: true`, telling the queue's own reader that a look is no
        // longer needed even though the row has not dropped out yet.
        let q = lib.segment_queue(50).unwrap();
        let item = q.iter().find(|i| i.passage_id == 30)
            .expect("recorded but unapplied still shows, by [SPEC-SA-124]'s own criterion");
        assert!(item.decided, "a passage with a recorded decision must say so");

        let _ = std::fs::remove_file(&tmp);
    }

    /// Only a passage the cascade actually produced can be "accepted as-is"
    /// -- otherwise the route could create a `boundary_reviews` row for a
    /// passage `segment_queue` never offered.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn accept_segment_refuses_a_passage_the_cascade_did_not_produce() {
        let tmp = std::env::temp_dir().join(format!("lempi-seg2-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = fixture(); // passage 2 carries boundary_src = 'src'
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        let err = store.accept_segment(2).unwrap_err();
        assert!(err.message().contains("segmentation cascade"), "{}", err.message());
        assert!(store.accept_segment(999_999).is_err(), "no such passage either");
        let _ = std::fs::remove_file(&tmp);
    }

    /// The same "cannot recommit once applied" guard `record_boundary_review`
    /// enforces for a full correction applies here unchanged, since
    /// `accept_segment` is a thin wrapper around it rather than a second
    /// writer with its own rules.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn accept_segment_cannot_be_repeated_once_applied() {
        let tmp = std::env::temp_dir().join(format!("lempi-seg3-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = fixture();
            c.execute_batch(
                "INSERT INTO passages VALUES
                     (31,1,'radio',5000,65000,NULL,NULL,NULL,'computed:segment-cascade@v1',20,20,'exponential','exponential');",
            )
            .unwrap();
            c.execute("VACUUM INTO ?1", [tmp.to_string_lossy()]).unwrap();
        }
        let store = PlayerStore::open(&tmp).unwrap();
        store.accept_segment(31).unwrap();
        store
            .conn
            .execute("UPDATE boundary_reviews SET applied_at = datetime('now') WHERE passage_id = 31", [])
            .unwrap();
        assert!(store.accept_segment(31).is_err());
        let _ = std::fs::remove_file(&tmp);
    }

    /// Play history is what the next selection reads `[REQ-PD-110]`, and it is
    /// keyed by MBID so it survives a rescan that renumbers passages.
    #[test]
    fn a_play_is_recorded_with_its_mbid() {
        let p = std::env::temp_dir().join(format!("lempi_ph_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&p);
        let st = PlayerStore::open(&p).unwrap();
        // The table is PlayerStore::open's now.
        st.record_play(7, Some("aaaaaaaa-0000-0000-0000-000000000001"), 90_000, 180_000, Some("user"))
            .unwrap();
        st.record_play(8, None, 90_000, 180_000, None).unwrap();
        let rows: Vec<(i64, Option<String>, Option<String>)> = st
            .conn
            .prepare("SELECT passage_id, mbid, selected_by FROM listener_play_history ORDER BY play_id")
            .unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .unwrap()
            .map(|r| r.unwrap())
            .collect();
        assert_eq!(
            rows,
            vec![
                (7, Some("aaaaaaaa-0000-0000-0000-000000000001".into()), Some("user".into())),
                (8, None, None),
            ],
            "an unidentified passage still records a play, and selected_by round-trips \
             (or is absent) independently of mbid"
        );
        let _ = std::fs::remove_file(&p);
    }

    /// An existing library, written before any of this, must open and work.
    ///
    /// This is the shipping question rather than a unit one: the appliance's
    /// database has a `player_state` with none of the newer columns and no
    /// `listener_rejections` at all. A query naming a column that does not
    /// exist fails outright rather than returning nothing, so a missed
    /// migration is a player that will not start.
    #[test]
    fn a_library_from_before_all_this_still_opens() {
        let tmp = std::env::temp_dir().join(format!(
            "lempi_legacy_{}_{}.db",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = std::fs::remove_file(&tmp);
        // Exactly the old shape: the original five columns, nothing since.
        {
            let c = Connection::open(&tmp).unwrap();
            c.execute_batch(
                "CREATE TABLE player_state (
                     id INTEGER PRIMARY KEY CHECK (id = 1),
                     passage_id INTEGER, position_ms INTEGER NOT NULL DEFAULT 0,
                     playing INTEGER NOT NULL DEFAULT 0, volume REAL NOT NULL DEFAULT 1.0,
                     updated_at TEXT NOT NULL);
                 INSERT INTO player_state (id, position_ms, playing, volume, updated_at)
                     VALUES (1, 4321, 1, 0.75, datetime('now'));",
            )
            .unwrap();
        }

        let store = PlayerStore::open(&tmp).expect("an old library must still open");

        // The resume point survives the migration -- it is the one thing in
        // that row a listener would notice losing.
        assert_eq!(store.load().unwrap(), Some((None, 4321, true)));

        // New settings read as their defaults, not as zero.
        let s = store.load_settings().expect("settings readable");
        let d = Settings::default();
        assert!((s.volume - 0.75).abs() < 1e-6, "the old value is kept");
        assert_eq!(s.skip_suppress_h, d.skip_suppress_h);
        assert_eq!(s.dequeue_suppress_h, d.dequeue_suppress_h);
        assert_eq!(s.queue_depth, d.queue_depth);
        assert_eq!(s.sample_interval_ms, d.sample_interval_ms);

        // And the table the player writes rejections to now exists.
        store.record_rejection(Rejection::Skip, 1, Some("m"), Some(10_000), Some(180_000)).unwrap();
        assert_eq!(store.last_rejected(Rejection::Skip).unwrap().len(), 1);

        // Writing back does not fail on the migrated row either.
        store.save_settings(&s).unwrap();
        let _ = std::fs::remove_file(&tmp);
    }

    /// A library with `listener_play_history`/`listener_rejections` already in
    /// place -- any build before `[REQ-VIS-250]` -- must gain the two new
    /// columns rather than failing every write with "no such column".
    #[test]
    fn an_existing_history_table_gains_the_played_columns() {
        let tmp = std::env::temp_dir().join(format!(
            "lempi_histmig_{}_{}.db",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = std::fs::remove_file(&tmp);
        {
            let c = Connection::open(&tmp).unwrap();
            c.execute_batch(
                "CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                     played_at INTEGER NOT NULL, passage_id INTEGER, mbid TEXT);
                 CREATE TABLE listener_rejections (rejection_id INTEGER PRIMARY KEY,
                     rejected_at INTEGER NOT NULL, kind TEXT NOT NULL,
                     passage_id INTEGER, mbid TEXT);
                 INSERT INTO listener_play_history VALUES (1, 100, 7, 'm');",
            )
            .unwrap();
        }

        let store = PlayerStore::open(&tmp).expect("a pre-migration history table must still open");

        // The old row survives, unharmed and now reading its new columns as
        // absent rather than missing -- the query naming them no longer fails.
        let old: (i64, Option<i64>, Option<i64>) = store
            .conn
            .query_row(
                "SELECT passage_id, heard_ms, span_ms FROM listener_play_history WHERE play_id = 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!(old, (7, None, None), "an old row keeps its data and gains absent new columns");

        // And a fresh write goes in with the new columns filled.
        let id = store.record_play(8, Some("n"), 90_000, 180_000, Some("auto")).unwrap();
        store.finish_play(id, 150_000).unwrap();
        let heard: i64 = store
            .conn
            .query_row("SELECT heard_ms FROM listener_play_history WHERE play_id = ?1", [id], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(heard, 150_000, "finish_play must update the row record_play wrote");
        let _ = std::fs::remove_file(&tmp);
    }

    /// A rejection is written where suppression can see it and the weighting
    /// cannot, and the two kinds stay apart `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]`.
    #[test]
    fn rejections_are_recorded_apart_from_plays_and_by_kind() {
        // Unique per run, not merely per process: a process id is reused, and a
        // leftover file would meet a bare CREATE TABLE below.
        let tmp = std::env::temp_dir().join(format!(
            "lempi_sk_{}_{}.db",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = std::fs::remove_file(&tmp);
        // Both tables are PlayerStore::open's now.
        let st = PlayerStore::open(&tmp).unwrap();
        let skipped = "aaaaaaaa-0000-0000-0000-000000000009";
        let dropped = "aaaaaaaa-0000-0000-0000-00000000000d";
        st.record_rejection(Rejection::Skip, 11, Some(skipped), Some(10_000), Some(180_000)).unwrap();
        st.record_rejection(Rejection::Dequeue, 12, Some(dropped), None, None).unwrap();

        // Each kind sees only its own: they earn different windows, so mixing
        // them would apply the wrong one.
        let skips = st.last_rejected(Rejection::Skip).unwrap();
        let deqs = st.last_rejected(Rejection::Dequeue).unwrap();
        assert!(skips.contains_key(skipped) && !skips.contains_key(dropped));
        assert!(deqs.contains_key(dropped) && !deqs.contains_key(skipped));

        // Neither becomes a play.
        let plays: i64 = st
            .conn
            .query_row("SELECT COUNT(*) FROM listener_play_history", [], |r| r.get(0))
            .unwrap();
        assert_eq!(plays, 0, "a rejection must never become a play");

        // Only the most recent of a kind matters: a window, not an accumulation.
        st.record_rejection(Rejection::Skip, 11, Some(skipped), Some(15_000), Some(180_000)).unwrap();
        assert_eq!(st.last_rejected(Rejection::Skip).unwrap().len(), 1);
        let _ = std::fs::remove_file(&tmp);
    }

    #[test]
    fn resume_state_round_trips() {
        let dir = std::env::temp_dir().join(format!("lempi_ps_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&dir);
        let st = PlayerStore::open(&dir).unwrap();
        assert!(st.load().unwrap().is_none(), "first run has no resume point");
        st.save(Some(42), 61_500, true).unwrap();
        assert_eq!(st.load().unwrap(), Some((Some(42), 61_500, true)));
        // saving again must UPDATE, not accumulate rows
        st.save(Some(43), 100, false).unwrap();
        assert_eq!(st.load().unwrap(), Some((Some(43), 100, false)));
        let n: i64 = st.conn.query_row("SELECT COUNT(*) FROM player_state", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 1, "resume state is a single row");
        let _ = std::fs::remove_file(&dir);
    }

    /// The regression `[REQ-VIS-255]` exists for: the first version called
    /// `sync_os_utc_offset` through `Library`'s connection, which is opened
    /// read-only, so the write silently failed on every real run while every
    /// unit test -- built on a writable in-memory `Connection` -- passed.
    /// This goes through `PlayerStore::open` the way production actually
    /// does, so a future version that makes the same mistake fails here too.
    #[test]
    fn sync_utc_offset_actually_reaches_disk() {
        let Some(os) = crate::director::program::os_utc_offset_minutes() else {
            return; // nothing to prove on a platform this cannot ask
        };
        let dir = std::env::temp_dir().join(format!("lempi_tz_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&dir);
        let st = PlayerStore::open(&dir).unwrap();
        // `listener_settings` is Vipunen's table, not one `PlayerStore::open`
        // creates -- built here the way a real library already has it.
        let wrong = if os == 0 { 60 } else { 0 };
        st.conn
            .execute_batch(&format!(
                "CREATE TABLE listener_settings (id INTEGER PRIMARY KEY,
                     utc_offset_minutes INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
                 INSERT INTO listener_settings (id, utc_offset_minutes, updated_at)
                     VALUES (1, {wrong}, 't');"
            ))
            .unwrap();

        st.sync_utc_offset();

        let after: i64 = st
            .conn
            .query_row("SELECT utc_offset_minutes FROM listener_settings WHERE id = 1", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(after, os, "the write must reach disk through PlayerStore's own connection");
        let _ = std::fs::remove_file(&dir);
    }

    /// Nothing chosen yet reads as `None`, not as an invented address
    /// `[PI3-AIM-020]`, `[REQ-VIS-260]` -- the reconnect timer's job is to do
    /// nothing in that case, and a stray default here would give it
    /// something to page instead.
    #[test]
    fn no_speaker_address_until_one_is_chosen() {
        let dir = std::env::temp_dir().join(format!("lempi_spk_none_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&dir);
        let st = PlayerStore::open(&dir).unwrap();
        assert_eq!(st.load_speaker_address(), None);
        let _ = std::fs::remove_file(&dir);
    }

    /// The whole point `[PI3-AIM-020]`, `[REQ-VIS-260]`: a chosen speaker
    /// round-trips through the same connection production writes it with,
    /// and choosing a second one replaces the first rather than sitting
    /// beside it -- there is exactly one appliance and one answer to "which
    /// speaker", never a history of them.
    #[test]
    fn a_chosen_speaker_round_trips_and_a_later_choice_replaces_it() {
        let dir = std::env::temp_dir().join(format!("lempi_spk_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&dir);
        let st = PlayerStore::open(&dir).unwrap();

        st.save_speaker_address("AA:BB:CC:DD:EE:FF").unwrap();
        assert_eq!(st.load_speaker_address().as_deref(), Some("AA:BB:CC:DD:EE:FF"));

        st.save_speaker_address("11:22:33:44:55:66").unwrap();
        assert_eq!(st.load_speaker_address().as_deref(), Some("11:22:33:44:55:66"));
        let n: i64 = st
            .conn
            .query_row(
                "SELECT COUNT(*) FROM player_settings WHERE key = 'speaker_address'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(n, 1, "a later choice replaces the row rather than adding to it");
        let _ = std::fs::remove_file(&dir);
    }

    /// Defaults on, at full brightness `[PI3-LED-010]` -- a library nobody
    /// has ever touched this setting on reads exactly the same as one
    /// explicitly set to solid-on-at-100.
    #[test]
    fn the_led_defaults_on_at_full_brightness_until_set() {
        let dir = std::env::temp_dir().join(format!("lempi_led_none_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&dir);
        let st = PlayerStore::open(&dir).unwrap();
        assert_eq!(st.load_led_mode(), "on");
        assert_eq!(st.load_led_brightness(), 100);
        let _ = std::fs::remove_file(&dir);
    }

    /// All four modes round-trip, and a later choice replaces the row
    /// rather than accumulating rows -- same discipline as the speaker
    /// address above, same table.
    #[test]
    fn the_led_mode_round_trips_through_all_four_values() {
        let dir = std::env::temp_dir().join(format!("lempi_led_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&dir);
        let st = PlayerStore::open(&dir).unwrap();

        for mode in ["wifi", "off", "default", "on"] {
            st.save_led_mode(mode).unwrap();
            assert_eq!(st.load_led_mode(), mode);
        }
        let n: i64 = st
            .conn
            .query_row("SELECT COUNT(*) FROM player_settings WHERE key = 'led_mode'", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(n, 1, "a later choice replaces the row rather than adding to it");
        let _ = std::fs::remove_file(&dir);
    }

    /// A value outside the closed set never reaches the database -- the
    /// same enforcement `set_preference` gives `subject_kind`.
    #[test]
    fn save_led_mode_rejects_an_unknown_value() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        assert!(store.save_led_mode("strobe").is_err());
    }

    /// Brightness round-trips and clamps to 1-100 on the way in, so a
    /// caller cannot store a value the boot script would then have to
    /// re-validate `[PI3-LED-010]`.
    #[test]
    fn led_brightness_round_trips_and_clamps() {
        let dir = std::env::temp_dir().join(format!("lempi_ledpct_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&dir);
        let st = PlayerStore::open(&dir).unwrap();

        st.save_led_brightness(42).unwrap();
        assert_eq!(st.load_led_brightness(), 42);

        st.save_led_brightness(0).unwrap();
        assert_eq!(st.load_led_brightness(), 1, "clamped up to the minimum");

        st.save_led_brightness(255).unwrap();
        assert_eq!(st.load_led_brightness(), 100, "clamped down to the maximum");
        let _ = std::fs::remove_file(&dir);
    }

    /// `None` both before anything is ever chosen and after `auto` clears a
    /// choice -- an appliance with no realtime clock must never be able to
    /// tell those two states apart as "fall back to the schedule" versus
    /// "fall back to something else" `[SPEC-DIR-185]`.
    #[test]
    fn manual_program_round_trips_and_auto_clears_it() {
        let dir = std::env::temp_dir().join(format!("lempi_program_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&dir);
        let st = PlayerStore::open(&dir).unwrap();

        assert_eq!(st.load_manual_program(), None, "nothing chosen yet");
        st.save_manual_program(Some(4)).unwrap();
        assert_eq!(st.load_manual_program(), Some(4));
        st.save_manual_program(Some(7)).unwrap();
        assert_eq!(st.load_manual_program(), Some(7), "a later choice replaces the earlier one");
        st.save_manual_program(None).unwrap();
        assert_eq!(st.load_manual_program(), None, "auto clears it rather than storing empty");
        let _ = std::fs::remove_file(&dir);
    }

    /// The vocabulary is enforced by the table itself, the same as `flavor`'s
    /// own `subject_kind` -- there being only one writer does not mean it
    /// cannot pass the wrong string.
    #[test]
    fn set_flag_rejects_an_unknown_subject_kind() {
        let c = historyable();
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(c) };
        assert!(store.set_flag("album", "x", true).is_err());
    }

    /// A subject nobody has ever tuned reads as three `None`s -- not an
    /// error, not a fabricated default. The Program Director's own default
    /// applies without this table needing to know what it is
    /// `[REQ-VIS-290]`.
    #[test]
    fn get_preference_on_an_untouched_subject_is_three_nones() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        assert_eq!(
            store.get_preference("artist", "bbbbbbbb-0000-0000-0000-000000000001").unwrap(),
            PreferenceRow::default()
        );
    }

    /// Setting one field leaves the others exactly as stored -- `None`
    /// means "unchanged", never "clear this too".
    #[test]
    fn set_preference_only_touches_the_fields_given() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        let id = "aaaaaaaa-0000-0000-0000-000000000001";
        store.set_preference("recording", id, Some(1.5), Some(2.0), None).unwrap();
        assert_eq!(
            store.get_preference("recording", id).unwrap(),
            PreferenceRow { rotation: Some(1.5), recovery: Some(2.0), restraint: None }
        );

        // A second save touching only restraint must not blank rotation/recovery.
        store.set_preference("recording", id, None, None, Some(-0.5)).unwrap();
        assert_eq!(
            store.get_preference("recording", id).unwrap(),
            PreferenceRow { rotation: Some(1.5), recovery: Some(2.0), restraint: Some(-0.5) },
            "an untouched field in this save must keep its prior value"
        );
    }

    /// `reset_preference` is the one operation that actually clears a
    /// field back to NULL -- `set_preference(None)` deliberately cannot,
    /// per its own doc comment.
    #[test]
    fn reset_preference_clears_exactly_one_field() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        let id = "aaaaaaaa-0000-0000-0000-000000000001";
        store.set_preference("recording", id, Some(1.5), Some(2.0), Some(-0.5)).unwrap();
        store.reset_preference("recording", id, "recovery").unwrap();
        assert_eq!(
            store.get_preference("recording", id).unwrap(),
            PreferenceRow { rotation: Some(1.5), recovery: None, restraint: Some(-0.5) },
            "only recovery must clear"
        );
    }

    /// Resetting a subject with no row yet is a no-op, not an error -- there
    /// is nothing to clear, and the default already applies.
    #[test]
    fn reset_preference_on_an_untouched_subject_is_a_harmless_no_op() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        assert!(store.reset_preference("artist", "no-such-id", "rotation").is_ok());
    }

    /// The vocabulary is enforced by the table itself, same as `set_flag`'s
    /// own equivalent test.
    #[test]
    fn set_preference_rejects_an_unknown_subject_kind() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        assert!(store.set_preference("album", "x", Some(1.0), None, None).is_err());
    }

    /// `listener_occasions` registers what can be tagged; `flavor` holds what
    /// the migration inherited. Both are needed before a special has anything
    /// to report, and neither is created by `PREFERENCES_TABLE`.
    fn specials_store() -> PlayerStore {
        let c = historyable();
        c.execute_batch(
            "CREATE TABLE listener_occasions (characteristic TEXT, class TEXT, interp TEXT,
                 label TEXT);
             CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT, characteristic TEXT,
                 class TEXT, value REAL, source TEXT, accuracy REAL);
             INSERT INTO listener_occasions VALUES
                 ('user.christmas','christmasy','linear','Christmas'),
                 ('user.profanity','profane','step',NULL);
             INSERT INTO flavor VALUES
                 ('recording','aaaaaaaa-0000-0000-0000-000000000001','user.christmas',
                  'christmasy',1.0,'inherited:mulib',NULL);",
        )
        .unwrap();
        PlayerStore { conn: QualifyingConn::wrap_unsplit(c) }
    }

    #[test]
    fn a_special_reports_the_inherited_value_with_no_listener_value() {
        let store = specials_store();
        let rows = store.list_specials("aaaaaaaa-0000-0000-0000-000000000001").unwrap();
        let christmas = rows.iter().find(|r| r.class == "christmasy").unwrap();
        assert_eq!(christmas.inherited, Some(1.0));
        assert_eq!(christmas.value, None, "nothing was set by hand, so this stays None");
        assert_eq!(christmas.label, "Christmas");
    }

    #[test]
    fn a_special_with_no_label_falls_back_to_its_characteristic() {
        let store = specials_store();
        let rows = store.list_specials("aaaaaaaa-0000-0000-0000-000000000001").unwrap();
        let profanity = rows.iter().find(|r| r.class == "profane").unwrap();
        assert_eq!(profanity.label, "Profanity");
        // Carries no inherited value today: `migrate_mulib.py` drops
        // MuLibPlay's 69 real profanity values by listing the field as
        // dead `[SPEC-PREF-082]`. This fixture pins the shape a special
        // with nothing behind it must still report.
        assert_eq!(profanity.inherited, None);
    }

    #[test]
    fn setting_a_special_leaves_the_inherited_value_visible_beside_it() {
        let store = specials_store();
        let id = "aaaaaaaa-0000-0000-0000-000000000001";
        store.set_special(id, "user.christmas", "christmasy", 0.25).unwrap();
        let rows = store.list_specials(id).unwrap();
        let christmas = rows.iter().find(|r| r.class == "christmasy").unwrap();
        assert_eq!(christmas.value, Some(0.25), "the listener's own value");
        assert_eq!(christmas.inherited, Some(1.0), "and what it overrode, still readable");
    }

    #[test]
    fn resetting_a_special_restores_the_inherited_value_rather_than_zeroing_it() {
        let store = specials_store();
        let id = "aaaaaaaa-0000-0000-0000-000000000001";
        store.set_special(id, "user.christmas", "christmasy", 0.0).unwrap();
        store.reset_special(id, "user.christmas", "christmasy").unwrap();
        let rows = store.list_specials(id).unwrap();
        let christmas = rows.iter().find(|r| r.class == "christmasy").unwrap();
        assert_eq!(christmas.value, None, "Reset means 'no opinion', not 'definitely not'");
        assert_eq!(christmas.inherited, Some(1.0));
    }

    #[test]
    fn a_special_outside_zero_to_one_is_refused() {
        let store = specials_store();
        let id = "aaaaaaaa-0000-0000-0000-000000000001";
        assert!(store.set_special(id, "user.christmas", "christmasy", 1.5).is_err());
        assert!(store.set_special(id, "user.christmas", "christmasy", -0.1).is_err());
    }

    #[test]
    fn a_library_with_no_occasions_registered_simply_offers_no_specials() {
        // `historyable()` alone -- no `listener_occasions` table at all.
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        assert_eq!(store.list_specials("aaaaaaaa-0000-0000-0000-000000000001").unwrap(), vec![]);
    }

    #[test]
    fn a_recording_is_named_by_title_artist_and_album() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        let named = store.subject_naming("recording", "aaaaaaaa-0000-0000-0000-000000000001").unwrap();
        assert_eq!(named.title.as_deref(), Some("A Song"));
        assert_eq!(named.artist.as_deref(), Some("A Band"));
        assert_eq!(named.album.as_deref(), Some("An Album"));
    }

    #[test]
    fn an_artist_is_named_by_name_alone() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        let named = store.subject_naming("artist", "bbbbbbbb-0000-0000-0000-000000000001").unwrap();
        assert_eq!(named.title.as_deref(), Some("A Band"));
        assert_eq!(named.artist, None, "an artist is not its own credited artist");
        assert_eq!(named.album, None);
    }

    #[test]
    fn an_unknown_subject_is_named_by_nothing_rather_than_failing() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        assert_eq!(store.subject_naming("recording", "no-such-mbid").unwrap(), SubjectNaming::default());
    }

    #[test]
    fn a_label_is_derived_from_the_characteristics_last_segment() {
        assert_eq!(derived_label("user.christmas"), "Christmas");
        assert_eq!(derived_label("mood_happy"), "Mood_happy");
        assert_eq!(derived_label(""), "");
    }

    #[test]
    fn reset_preference_rejects_an_unknown_field() {
        let store = PlayerStore { conn: QualifyingConn::wrap_unsplit(historyable()) };
        assert!(store.reset_preference("recording", "x", "loudness").is_err());
    }

    /// `historyable()` builds `listener_play_history` from the base
    /// `PLAY_TABLE` schema, which predates `selected_by` -- the same gap
    /// `ensure_history_columns` exists to close on a real `open()`. Run it
    /// here too, so a play-frequency test can write the column without
    /// going through a real file.
    fn frequency_store() -> PlayerStore {
        let conn = historyable();
        ensure_history_columns(&conn);
        PlayerStore { conn: QualifyingConn::wrap_unsplit(conn) }
    }

    fn now() -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64
    }

    /// One row per window/`selected_by` combination this test cares about,
    /// planted at an exact age so it lands inside or outside each window
    /// on purpose `[REQ-VIS-300]`.
    fn plant(conn: &Connection, mbid: &str, age_secs: i64, selected_by: Option<&str>) {
        conn.execute(
            "INSERT INTO listener_play_history (played_at, passage_id, mbid, selected_by) \
             VALUES (?1, 1, ?2, ?3)",
            rusqlite::params![now() - age_secs, mbid, selected_by],
        )
        .unwrap();
    }

    /// All ≥ every other row in every window, User and program rows are
    /// dynamic/fixed as specified, and windows are rolling (an entry just
    /// past a boundary drops out of that window but stays in wider ones
    /// and in all-time `[REQ-VIS-300]`.
    #[test]
    fn play_frequency_buckets_by_window_and_selector() {
        let store = frequency_store();
        let id = "aaaaaaaa-0000-0000-0000-000000000001";
        // Inside every window: counts everywhere.
        plant(&store.conn, id, 60, Some("user"));
        // Inside 7d/30d/365d/all, outside 24h.
        plant(&store.conn, id, 2 * 86_400, Some("Soft"));
        // Inside 30d/365d/all only.
        plant(&store.conn, id, 10 * 86_400, Some("Soft"));
        // Inside 365d/all only.
        plant(&store.conn, id, 100 * 86_400, Some("auto"));
        // All-time only.
        plant(&store.conn, id, 1_000 * 86_400, Some("auto"));
        // Historical, no provenance recorded -- All only, forever.
        plant(&store.conn, id, 30, None);

        let rows = store.play_frequency("recording", id).unwrap();
        let row = |label: &str| rows.iter().find(|r| r.label == label).unwrap().clone();

        assert_eq!(row("All").counts, [2, 3, 4, 5, 6], "every row, its own window and wider");
        assert_eq!(row("User").counts, [1, 1, 1, 1, 1]);
        assert_eq!(row("Soft").counts, [0, 1, 2, 2, 2]);
        assert_eq!(row("Auto").counts, [0, 0, 0, 1, 2]);
        for i in 0..5 {
            assert!(
                row("All").counts[i]
                    >= row("User").counts[i] + row("Soft").counts[i] + row("Auto").counts[i],
                "All must never be less than the other rows combined, window {i}"
            );
        }
    }

    /// A subject with no play in a given selector's history gets no row for
    /// it at all -- not a row of zeroes. All and User are the two fixed
    /// exceptions, present even when empty.
    #[test]
    fn play_frequency_omits_program_rows_that_never_happened() {
        let store = frequency_store();
        let id = "aaaaaaaa-0000-0000-0000-000000000001";
        plant(&store.conn, id, 60, Some("user"));

        let rows = store.play_frequency("recording", id).unwrap();
        assert_eq!(rows.iter().map(|r| r.label.as_str()).collect::<Vec<_>>(), vec!["All", "User"]);
    }

    /// Artist mode joins through `recording_artists` -- a play recorded
    /// against the recording's own mbid still counts toward the artist it
    /// is credited to `[REQ-VIS-300]`, the same join `HIST_ARTIST_MBID_EXPR`
    /// uses in `library.rs`.
    #[test]
    fn play_frequency_artist_mode_counts_plays_of_its_recordings() {
        let store = frequency_store();
        let recording = "aaaaaaaa-0000-0000-0000-000000000001";
        let artist = "bbbbbbbb-0000-0000-0000-000000000001";
        plant(&store.conn, recording, 60, Some("user"));
        plant(&store.conn, recording, 60, Some("Soft"));

        let rows = store.play_frequency("artist", artist).unwrap();
        let row = |label: &str| rows.iter().find(|r| r.label == label).unwrap().clone();
        assert_eq!(row("All").counts[4], 2);
        assert_eq!(row("User").counts[4], 1);
        assert_eq!(row("Soft").counts[4], 1);
    }

    /// Program rows sort by their own all-time count, most-played first --
    /// the same "most relevant first" instinct behind runner-up ordering.
    #[test]
    fn play_frequency_program_rows_sort_by_all_time_count_descending() {
        let store = frequency_store();
        let id = "aaaaaaaa-0000-0000-0000-000000000001";
        plant(&store.conn, id, 60, Some("Quiet"));
        plant(&store.conn, id, 60, Some("Soft"));
        plant(&store.conn, id, 61, Some("Soft"));
        plant(&store.conn, id, 62, Some("Soft"));

        let rows = store.play_frequency("recording", id).unwrap();
        let labels: Vec<&str> = rows.iter().skip(2).map(|r| r.label.as_str()).collect();
        assert_eq!(labels, vec!["Soft", "Quiet"], "Soft has three plays, Quiet has one");
    }
}
