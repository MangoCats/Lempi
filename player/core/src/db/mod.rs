//! Read-only access to `lempi.db` for playback.
//!
//! Deliberately narrow: the player reads passages and writes play history, and
//! nothing else. Library building belongs to Vipunen `[SPEC-SA-100]`, so the
//! queries here are the few the audio path actually needs. A general-purpose
//! DAO would be a second source of truth for the schema `[SPEC008]`.
//!
//! Split along the guard [`Library::open`]'s own doc comment states: [`Library`] is
//! the read-only connection everything on the *reading* path uses --
//! selection, naming, browsing -- and [`PlayerStore`] is the one writable
//! handle, the only place a table gets created. One file used to hold both,
//! [`Library`]'s own methods split across two non-contiguous regions with
//! [`PlayerStore`] sandwiched between them; now each is its own file, so the
//! guard that connection-mode already enforces at runtime is legible at the
//! file level too.

mod library;
mod player_store;

pub use library::*;
pub use player_store::*;

/// Bring a `files` table predating `md5_generator` up to date
/// `[SPEC-RLK-150]`.
///
/// One definition, two callers -- [`Library::open_writable`] and
/// [`crate::bundle::import`] -- because both can be the first thing to touch a
/// given library and neither may assume the other ran `[GDE-ARC-033]`. The
/// `CREATE TABLE IF NOT EXISTS` in `sql/schema.sql` is a no-op on a library
/// that already has the table, which is the same gap `ensure_tag_table` and
/// `ensure_history_columns` close for theirs.
///
/// Already-present is the expected path on every run after the first, so the
/// error is discarded rather than reported -- the established shape here. What
/// is *not* discarded is the column's meaning when it is absent: every row it
/// adds is `NULL`, and `NULL` stays `NULL`. See `sql/schema.sql` for why no
/// back-annotation is the honest answer.
pub fn ensure_md5_generator_column(conn: &rusqlite::Connection) {
    let _ = conn.execute("ALTER TABLE files ADD COLUMN md5_generator TEXT", []);
}

#[derive(Debug)]
pub enum DbError {
    Open(String),
    Query(String),
}

impl DbError {
    /// The message without the "query:" / "open database:" prefix.
    ///
    /// Some of these are refusals meant for a person to read -- "already
    /// applied to the library" -- and reach a browser as the explanation of a
    /// 409. Prefixing that with the name of an internal error variant tells
    /// the reader nothing and makes a considered refusal look like a crash.
    pub fn message(&self) -> &str {
        match self {
            DbError::Open(e) | DbError::Query(e) => e,
        }
    }
}

impl std::fmt::Display for DbError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DbError::Open(e) => write!(f, "open database: {e}"),
            DbError::Query(e) => write!(f, "query: {e}"),
        }
    }
}

/// How long a connection waits for a writer to finish before giving up.
///
/// Three writers share this file -- the resume row every second, the tag scan
/// at startup, and nothing else -- and WAL allows one at a time. **Without a
/// busy timeout SQLite does not wait at all**: a contended write returns
/// SQLITE_BUSY immediately, and the tag scan's per-file error path would drop
/// that file's row and carry on, leaving a hole nothing would ever revisit.
/// Five seconds is far longer than any write here takes and far shorter than
/// anyone would wait for a stuck one.
pub(crate) const BUSY_WAIT: std::time::Duration = std::time::Duration::from_secs(5);

/// Attaches `library_path` as `lib`, read-only, when it differs from
/// `db_path`, and returns the schema prefix every catalog query should use
/// from then on `[IMPL-DBSPLIT-025]`.
///
/// The two paths are equal for every installation that hasn't split its
/// database -- `bose`, today's lempi02w, every existing test fixture. In
/// that case nothing is attached and this returns `"main"`, so a query
/// written `{lib}.recordings` reads the same table through the same
/// connection it always has, and the one player binary needs no
/// environment-specific build. Only an installation with a genuinely
/// separate `library.db` (lempi02w, once split -- `[PI-DB-020]`) ever
/// attaches anything or ever sees `"lib"` come back.
///
/// Equality is the caller's to guarantee, by construction rather than by
/// canonicalizing paths here: the CLI defaults `--library` to a clone of
/// `--db` when the flag is absent, so the two `Path`s are byte-identical
/// in the common case rather than merely referring to the same file on
/// disk. A caller that passes two different spellings of the same path
/// gets a harmless self-attach, not a silent bug -- see the doc comment on
/// the URI mode below for why that's still safe.
///
/// Requires the connection to have been opened with `OpenFlags::SQLITE_OPEN_URI`
/// -- `mode=ro` in the attach URI is SQLite's own documented way to attach
/// a second file read-only, and it only parses as a URI (rather than a
/// literal filename containing a `?`) when the connection has URI filenames
/// enabled. Every one of `Library::open`, `PlayerStore::open`, and
/// `bin/mpd_direct.rs`'s bare `Connection::open` must include that flag
/// once they call this.
pub fn attach_library(
    conn: &rusqlite::Connection,
    db_path: &std::path::Path,
    library_path: &std::path::Path,
) -> Result<&'static str, DbError> {
    if db_path == library_path {
        return Ok("main");
    }
    let uri = format!("file:{}?mode=ro", library_path.display());
    conn.execute("ATTACH DATABASE ?1 AS lib", [uri])
        .map_err(|e| DbError::Open(format!("attach library {}: {e}", library_path.display())))?;
    Ok("lib")
}

/// The placeholder every catalog-table reference in a query string carries
/// -- `__LIB__.recordings`, never a bare `lib.` or `main.` written by hand
/// -- so [`QualifyingConn::qualify`] is the one and only place that decides
/// which schema a catalog table actually resolves to `[IMPL-DBSPLIT-035]`.
pub(crate) const LIB_PLACEHOLDER: &str = "__LIB__.";

/// Wraps a [`Connection`](rusqlite::Connection) that has already run
/// [`attach_library`], and rewrites `__LIB__.`-prefixed catalog references
/// in every query text passed through it before handing the query to
/// SQLite.
///
/// `Library` and `PlayerStore` both hold one of these instead of a bare
/// `Connection` -- both mix catalog and listener tables in real queries
/// (`[IMPL-DBSPLIT-015]` enumerates why), so both need the same rewrite.
/// Everything the query-building code already does --
/// `self.conn.prepare(&sql)`, `self.conn.query_row(...)`, `self.conn.execute(...)`
/// -- keeps compiling unchanged: those four names are shadowed here with
/// the identical signatures, qualifying first; every other
/// [`Connection`](rusqlite::Connection) method (`last_insert_rowid`,
/// `transaction`, ...) reaches the real connection through `Deref`,
/// needing no change at all. One rewrite point, not eighteen call sites
/// updated by hand and eighteen more to get right the next time a query
/// is added.
pub struct QualifyingConn {
    conn: rusqlite::Connection,
    lib: &'static str,
}

impl QualifyingConn {
    /// Opens `db_path`, runs [`attach_library`] against `library_path`, and
    /// wraps the result. `db_path == library_path` is the common,
    /// unsplit case -- see `attach_library`'s own doc comment.
    pub fn open(
        db_path: &std::path::Path,
        library_path: &std::path::Path,
        flags: rusqlite::OpenFlags,
    ) -> Result<Self, DbError> {
        let conn = rusqlite::Connection::open_with_flags(
            db_path,
            flags | rusqlite::OpenFlags::SQLITE_OPEN_URI,
        )
        .map_err(|e| DbError::Open(e.to_string()))?;
        // Found live, on lempi02w's first real split: rusqlite's *bundled*
        // SQLite defaults `PRAGMA foreign_keys` to ON -- confirmed by
        // asking it directly, not by grepping this crate for an explicit
        // enable, which is exactly the check `[IMPL002 §7.7]` stopped one
        // step short of and got wrong as a result. Harmless on every
        // unsplit installation (`listener_play_history`/`selection_decisions`
        // and the `passages` they reference all sit in the same file, so
        // the lookup always succeeds) -- and fatal to every write on a
        // split one, since SQLite only ever checks a FOREIGN KEY against a
        // table in its *own* schema, never an attached one: the write
        // doesn't fail loudly, `record_decision`/`save` just start
        // returning "no such table: main.passages" on every call, silently
        // dropping resume state and decision explanations forever after.
        // Turned off unconditionally, split or not, because nothing in
        // this crate ever relied on the cascade firing from the player's
        // own writes -- only Vipunen's desktop tools do, deliberately and
        // explicitly, on their own connections, not this one.
        conn.execute("PRAGMA foreign_keys = OFF", [])
            .map_err(|e| DbError::Open(e.to_string()))?;
        let lib = attach_library(&conn, db_path, library_path)?;
        Ok(Self { conn, lib })
    }

    /// Wraps an already-open connection as unsplit (`lib = "main"`),
    /// skipping `attach_library` entirely -- for tests that build an
    /// in-memory fixture directly rather than through a real file path.
    /// Real callers use [`Self::open`]; this exists so the many existing
    /// `Library`/`PlayerStore` test fixtures don't need a real file on disk
    /// just to get a `QualifyingConn`.
    #[cfg(test)]
    pub(crate) fn wrap_unsplit(conn: rusqlite::Connection) -> Self {
        Self { conn, lib: "main" }
    }

    /// `"main"` on every installation that hasn't split; `"lib"` only once
    /// it has. Exposed so callers that build SQL as ad hoc `format!` text
    /// rather than through [`Self::qualify`] -- `bundle.rs`'s import path
    /// does this for a few statements -- can still get it right by asking
    /// rather than assuming.
    pub fn lib_alias(&self) -> &'static str {
        self.lib
    }

    fn qualify<'a>(&self, sql: &'a str) -> std::borrow::Cow<'a, str> {
        if sql.contains(LIB_PLACEHOLDER) {
            std::borrow::Cow::Owned(if self.lib == "main" {
                sql.replace(LIB_PLACEHOLDER, "")
            } else {
                sql.replace(LIB_PLACEHOLDER, &format!("{}.", self.lib))
            })
        } else {
            std::borrow::Cow::Borrowed(sql)
        }
    }

    pub fn prepare(&self, sql: &str) -> rusqlite::Result<rusqlite::Statement<'_>> {
        self.conn.prepare(&self.qualify(sql))
    }

    pub fn query_row<T, P, F>(&self, sql: &str, params: P, f: F) -> rusqlite::Result<T>
    where
        P: rusqlite::Params,
        F: FnOnce(&rusqlite::Row<'_>) -> rusqlite::Result<T>,
    {
        self.conn.query_row(&self.qualify(sql), params, f)
    }

    pub fn execute<P: rusqlite::Params>(&self, sql: &str, params: P) -> rusqlite::Result<usize> {
        self.conn.execute(&self.qualify(sql), params)
    }

    pub fn execute_batch(&self, sql: &str) -> rusqlite::Result<()> {
        self.conn.execute_batch(&self.qualify(sql))
    }
}

impl std::ops::Deref for QualifyingConn {
    type Target = rusqlite::Connection;
    fn deref(&self) -> &rusqlite::Connection {
        &self.conn
    }
}

#[cfg(test)]
mod qualifying_conn_tests {
    use super::QualifyingConn;
    use rusqlite::{Connection, OpenFlags};
    use std::path::PathBuf;

    fn scratch_path(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("lempi-qconn-{name}-{}.db", std::process::id()))
    }

    const RW: OpenFlags =
        OpenFlags::from_bits_truncate(OpenFlags::SQLITE_OPEN_READ_WRITE.bits() | OpenFlags::SQLITE_OPEN_CREATE.bits());

    /// Unsplit: a placeholder in the query text still resolves, against
    /// the one file, because qualifying against `"main"` just erases it.
    #[test]
    fn unsplit_placeholder_resolves_against_main() {
        let path = scratch_path("unsplit");
        let _ = std::fs::remove_file(&path);
        let q = QualifyingConn::open(&path, &path, RW).unwrap();
        assert_eq!(q.lib_alias(), "main");
        q.execute_batch("CREATE TABLE __LIB__.recordings (mbid TEXT PRIMARY KEY)").unwrap();
        q.execute("INSERT INTO __LIB__.recordings VALUES ('x')", []).unwrap();
        let n: i64 = q.query_row("SELECT COUNT(*) FROM __LIB__.recordings", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 1);
        let _ = std::fs::remove_file(&path);
    }

    /// Split: the same query text, unmodified, now resolves against the
    /// attached `lib` schema instead -- the whole point of the
    /// placeholder is that query-building code never has to know which
    /// case it's in.
    #[test]
    fn split_placeholder_resolves_against_attached_lib() {
        let listener_path = scratch_path("split-listener");
        let library_path = scratch_path("split-library");
        let _ = std::fs::remove_file(&listener_path);
        let _ = std::fs::remove_file(&library_path);

        let lib_conn = rusqlite::Connection::open(&library_path).unwrap();
        lib_conn.execute("CREATE TABLE recordings (mbid TEXT PRIMARY KEY)", []).unwrap();
        lib_conn.execute("INSERT INTO recordings VALUES ('y')", []).unwrap();
        drop(lib_conn);

        let q = QualifyingConn::open(&listener_path, &library_path, RW).unwrap();
        assert_eq!(q.lib_alias(), "lib");
        // Read-only, and correctly so -- inserting through the attached
        // schema is exactly what must fail, per attach_library's own
        // read-only-attach test. Reading is what this type exists for.
        let n: i64 = q.query_row("SELECT COUNT(*) FROM __LIB__.recordings", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 1);

        // A query naming a listener-side table with no placeholder at all
        // must still work untouched -- qualify() is a no-op when there's
        // nothing to rewrite.
        q.execute_batch("CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY)").unwrap();
        q.execute("INSERT INTO listener_play_history DEFAULT VALUES", []).unwrap();

        let _ = std::fs::remove_file(&listener_path);
        let _ = std::fs::remove_file(&library_path);
    }

    /// `Deref` must reach ordinary `Connection` methods this type doesn't
    /// shadow -- `last_insert_rowid` is exactly what `PlayerStore` needs
    /// after an insert.
    #[test]
    fn deref_reaches_unwrapped_connection_methods() {
        let path = scratch_path("deref");
        let _ = std::fs::remove_file(&path);
        let q = QualifyingConn::open(&path, &path, RW).unwrap();
        q.execute_batch("CREATE TABLE t (id INTEGER PRIMARY KEY)").unwrap();
        q.execute("INSERT INTO t DEFAULT VALUES", []).unwrap();
        assert_eq!(q.last_insert_rowid(), 1);
        let _ = std::fs::remove_file(&path);
    }

    /// Found live, on lempi02w's actual first split: rusqlite's *bundled*
    /// SQLite defaults `PRAGMA foreign_keys` to ON, and this project's real
    /// schema has two foreign keys crossing the split boundary
    /// (`listener_play_history`/`selection_decisions` -> `passages`,
    /// `[IMPL002 §7.7]`). SQLite only ever checks a `FOREIGN KEY` against a
    /// table in its *own* schema, never an attached one -- so once
    /// `passages` moved to `lib`, every insert into a table referencing it
    /// started failing "no such table: main.passages," silently dropping
    /// resume state and decision explanations. Reproduces the exact
    /// failure, unfixed, then proves the fix.
    #[test]
    fn a_write_referencing_an_attached_table_does_not_fail_foreign_key_lookup() {
        let listener_path = scratch_path("fk-listener");
        let library_path = scratch_path("fk-library");
        let _ = std::fs::remove_file(&listener_path);
        let _ = std::fs::remove_file(&library_path);

        Connection::open(&library_path)
            .unwrap()
            .execute_batch("CREATE TABLE passages (passage_id INTEGER PRIMARY KEY)")
            .unwrap();

        let q = QualifyingConn::open(&listener_path, &library_path, RW).unwrap();
        // Confirms *why* this would have failed before the fix -- if this
        // assertion ever starts failing because a future rusqlite/SQLite
        // upgrade changes the bundled default, the PRAGMA in `open()` is
        // still correct defense, but this comment's premise needs revisiting.
        let fk: i64 = q.query_row("PRAGMA foreign_keys", [], |r| r.get(0)).unwrap();
        assert_eq!(fk, 0, "open() must turn this off; the bundled default is 1");

        q.execute_batch(
            "CREATE TABLE selection_decisions (decision_id INTEGER PRIMARY KEY, \
             passage_id INTEGER REFERENCES passages(passage_id) ON DELETE SET NULL)",
        )
        .unwrap();
        // The exact shape of the live failure: passage_id names a real row
        // that exists only in the attached schema, not in main.
        q.execute("INSERT INTO selection_decisions (passage_id) VALUES (1)", [])
            .expect("must not fail with 'no such table: main.passages'");

        let _ = std::fs::remove_file(&listener_path);
        let _ = std::fs::remove_file(&library_path);
    }
}

#[cfg(test)]
mod attach_library_tests {
    use super::attach_library;
    use rusqlite::{Connection, OpenFlags};
    use std::path::{Path, PathBuf};

    /// Matches the rest of this crate's tests (`player_store.rs`'s own
    /// `lempi-rev-{pid}.db` pattern) rather than introducing a `tempfile`
    /// dependency for one new test module.
    fn scratch_path(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("lempi-attach-{name}-{}.db", std::process::id()))
    }

    fn open_uri(path: &Path) -> Connection {
        let _ = std::fs::remove_file(path);
        Connection::open_with_flags(
            path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_URI,
        )
        .unwrap()
    }

    /// The common case today: every installation that hasn't split passes
    /// the same path twice. Nothing should be attached, and `main` must
    /// still resolve -- the whole point is that this costs nothing when
    /// there is no split.
    #[test]
    fn same_path_attaches_nothing_and_returns_main() {
        let path = scratch_path("same");
        let conn = open_uri(&path);
        conn.execute("CREATE TABLE recordings (mbid TEXT PRIMARY KEY)", [])
            .unwrap();

        let alias = attach_library(&conn, &path, &path).unwrap();
        assert_eq!(alias, "main");

        // The whole reason this matters: a query built with the returned
        // alias must actually run, unqualified through `main`.
        conn.execute(&format!("INSERT INTO {alias}.recordings VALUES ('x')"), [])
            .unwrap();
        let _ = std::fs::remove_file(&path);
    }

    /// The split case: two real files, attached read-only, queryable
    /// through the alias this function hands back -- proven against the
    /// actual linked SQLite rather than assumed from documentation.
    #[test]
    fn different_paths_attach_library_read_only_as_lib() {
        let listener_path = scratch_path("listener");
        let library_path = scratch_path("library");

        let lib_conn = open_uri(&library_path);
        lib_conn
            .execute("CREATE TABLE recordings (mbid TEXT PRIMARY KEY)", [])
            .unwrap();
        lib_conn.execute("INSERT INTO recordings VALUES ('abc')", []).unwrap();
        drop(lib_conn);

        let conn = open_uri(&listener_path);
        let alias = attach_library(&conn, &listener_path, &library_path).unwrap();
        assert_eq!(alias, "lib");

        let mbid: String = conn
            .query_row(&format!("SELECT mbid FROM {alias}.recordings"), [], |r| r.get(0))
            .unwrap();
        assert_eq!(mbid, "abc");

        // The safety guard this whole design leans on: a write attempt
        // against the attached schema must fail, not silently succeed.
        let write_err = conn
            .execute(&format!("INSERT INTO {alias}.recordings VALUES ('def')"), [])
            .unwrap_err();
        let msg = write_err.to_string().to_lowercase();
        assert!(
            msg.contains("read-only") || msg.contains("readonly"),
            "expected a read-only failure, got: {write_err}"
        );
        let _ = std::fs::remove_file(&listener_path);
        let _ = std::fs::remove_file(&library_path);
    }

    /// A second connection, attaching the same two files independently --
    /// the shape `Library` and `PlayerStore` actually need, since they are
    /// two separate connections today and stay that way post-split.
    #[test]
    fn two_independent_connections_can_each_attach_the_same_library() {
        let listener_path = scratch_path("listener2");
        let library_path = scratch_path("library2");
        open_uri(&library_path)
            .execute("CREATE TABLE recordings (mbid TEXT PRIMARY KEY)", [])
            .unwrap();

        let conn_a = open_uri(&listener_path);
        let conn_b = Connection::open_with_flags(
            &listener_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI,
        )
        .unwrap();
        assert_eq!(attach_library(&conn_a, &listener_path, &library_path).unwrap(), "lib");
        assert_eq!(attach_library(&conn_b, &listener_path, &library_path).unwrap(), "lib");

        conn_a.execute("SELECT * FROM lib.recordings", []).unwrap();
        conn_b.execute("SELECT * FROM lib.recordings", []).unwrap();
        let _ = std::fs::remove_file(&listener_path);
        let _ = std::fs::remove_file(&library_path);
    }
}

#[cfg(test)]
pub(crate) mod test_support {
    //! Fixtures shared by both [`super::library`]'s and [`super::player_store`]'s
    //! own test modules. Each returns a plain [`Connection`], so building one
    //! here needs no access to either module's private fields -- only the
    //! table constants both already import back via `pub use`.
    use rusqlite::Connection;
    use super::*;

    /// Build the minimum of SPEC008 the player touches, so these tests pin the
    /// column names the queries depend on.
    pub(crate) fn fixture() -> Connection {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT NOT NULL,
                 size_bytes INTEGER, mtime REAL, format TEXT, duration_ms INTEGER,
                 first_seen TEXT, last_seen TEXT);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                 kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                 lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);
             CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT,
                 weight REAL DEFAULT 1.0, source TEXT);
             INSERT INTO files VALUES (1,'md5','/m/a.mp3',1,1.0,'mp3',300000,'t','t');
             INSERT INTO passages VALUES (1,1,'album',0,300000,NULL,NULL,NULL,'src');
             INSERT INTO passages VALUES (2,1,'radio',1200,298000,3000,4000,-2.5,'src');
             -- passage 2 is a medley: two recordings, the heavier one wins
             INSERT INTO passage_recordings VALUES (2,'aaaaaaaa-0000-0000-0000-00000000000f',0.3,'s'),
                                                   (2,'aaaaaaaa-0000-0000-0000-000000000001',0.9,'s');
             -- Added after the fact, exactly like the real migration
             -- (`tools/add_fade_columns.py`) adds them to a live database
             -- `[SPEC-SUI-226]` -- so every existing bare `INSERT INTO
             -- passages VALUES (...)` above and in every test built on this
             -- fixture keeps working unmodified, backfilled with the same
             -- default a real ALTER TABLE gives every existing row.
             ALTER TABLE passages ADD COLUMN fade_in_ms INTEGER NOT NULL DEFAULT 20;
             ALTER TABLE passages ADD COLUMN fade_out_ms INTEGER NOT NULL DEFAULT 20;
             ALTER TABLE passages ADD COLUMN fade_in_curve TEXT NOT NULL DEFAULT 'exponential';
             ALTER TABLE passages ADD COLUMN fade_out_curve TEXT NOT NULL DEFAULT 'exponential';",
        )
        .unwrap();
        c
    }

    /// The review fixture: the naming tables the queue joins, plus findings.
    pub(crate) fn reviewable() -> Connection {
        let c = fixture();
        c.execute_batch(
            // NOT NULL exactly as SPEC008 declares them. The looser fixture
            // this replaces is what let a writer that omitted `source` pass
            // every test and then fail against the real library: `INSERT OR
            // IGNORE` turned the violation into nothing happening, and the
            // foreign key failed on the statement after.
            "CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
                 length_ms INTEGER, source TEXT NOT NULL);
             CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL,
                 sort_name TEXT, source TEXT NOT NULL);
             CREATE TABLE recording_artists (
                 mbid TEXT NOT NULL REFERENCES recordings(mbid),
                 artist_mbid TEXT NOT NULL REFERENCES artists(mbid),
                 weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
                 PRIMARY KEY (mbid, artist_mbid));
             CREATE TABLE release_recordings (release_mbid TEXT, mbid TEXT, position INTEGER,
                 source TEXT, track_length_ms INTEGER, chosen INTEGER DEFAULT 0, disc INTEGER);
             CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT, release_date TEXT,
                 source TEXT, release_group TEXT, status TEXT, primary_type TEXT,
                 secondary_types TEXT, country TEXT, track_count INTEGER);
             -- As SPEC008 declares it, not as the queries here happen to need
             -- it. A fixture missing `played_at` passed for as long as nothing
             -- indexed the column, and then failed the moment the player began
             -- creating the table itself -- the same looseness the note above
             -- is about.
             CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                 played_at INTEGER NOT NULL DEFAULT 0, passage_id INTEGER, mbid TEXT);
             INSERT INTO recordings VALUES ('aaaaaaaa-0000-0000-0000-000000000001','Wrong Song',NULL,'s');
             CREATE TABLE id_checks (passage_id INTEGER PRIMARY KEY, stored_mbid TEXT NOT NULL,
                 verdict TEXT NOT NULL, score REAL, suggested TEXT, checked_at TEXT NOT NULL);
             INSERT INTO id_checks VALUES
                 (2,'aaaaaaaa-0000-0000-0000-000000000001','contradicted',0.97,
                  '[{\"mbid\":\"aaaaaaaa-0000-0000-0000-000000000002\",\
                     \"title\":\"Right Song\",\"artist\":\"A Band\",\"score\":0.97}]','t');",
        )
        .unwrap();
        c.execute_batch(TAG_TABLE).unwrap();
        #[cfg(feature = "vipunen-support")]
        ensure_review_table(&c).unwrap();
        #[cfg(feature = "vipunen-support")]
        ensure_artist_review_table(&c).unwrap();
        c
    }

    /// The segment-queue fixture `[SPEC024 §7]`: everything `reviewable()`
    /// already sets up (naming tables, `file_tags`) plus `boundary_reviews`
    /// -- created here the same way `PlayerStore::open` creates it for a
    /// running server, since `reviewable()` stops at `ensure_review_table`/
    /// `ensure_artist_review_table` -- and `ingest_decisions` `[SPEC008
    /// §7]`, which neither fixture includes since the ordinary playback and
    /// identification paths never touch it.
    /// Unused at present: the fixture is complete and the segmentation
    /// tests that would take it are not written yet. Kept rather than
    /// deleted -- it documents exactly which tables that path needs
    /// beyond `reviewable()`, which is the hard half of writing them.
    #[allow(dead_code)]
    pub(crate) fn segmentable() -> Connection {
        let c = reviewable();
        #[cfg(feature = "vipunen-support")]
        ensure_boundary_review_table(&c).unwrap();
        c.execute_batch(
            "CREATE TABLE ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
                 stage TEXT NOT NULL, outcome TEXT NOT NULL, confidence REAL, detail BLOB,
                 decided_at TEXT NOT NULL);",
        )
        .unwrap();
        c
    }

    /// The naming tables `play_history` reads, filled with one recording that
    /// has both an artist and a chosen release -- enough to exercise every
    /// column the page shows.
    pub(crate) fn historyable() -> Connection {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(
            "CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
                 length_ms INTEGER, source TEXT NOT NULL);
             CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL,
                 sort_name TEXT, source TEXT NOT NULL);
             CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT,
                 weight REAL DEFAULT 1.0, source TEXT);
             CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT, release_date TEXT,
                 source TEXT);
             CREATE TABLE release_recordings (release_mbid TEXT, mbid TEXT, position INTEGER,
                 source TEXT, chosen INTEGER DEFAULT 0);
             INSERT INTO recordings VALUES ('aaaaaaaa-0000-0000-0000-000000000001','A Song',NULL,'s');
             INSERT INTO artists VALUES ('bbbbbbbb-0000-0000-0000-000000000001','A Band',NULL,'s');
             INSERT INTO recording_artists VALUES
                 ('aaaaaaaa-0000-0000-0000-000000000001','bbbbbbbb-0000-0000-0000-000000000001',1.0,'s');
             INSERT INTO releases VALUES ('cccccccc-0000-0000-0000-000000000001','An Album',NULL,'s');
             INSERT INTO release_recordings VALUES
                 ('cccccccc-0000-0000-0000-000000000001','aaaaaaaa-0000-0000-0000-000000000001',1,'s',1);",
        )
        .unwrap();
        c.execute_batch(PLAY_TABLE).unwrap();
        c.execute_batch(REJECTION_TABLE).unwrap();
        c.execute_batch(FLAGS_TABLE).unwrap();
        c.execute_batch(PREFERENCES_TABLE).unwrap();
        c.execute_batch(CHARACTERISTICS_TABLE).unwrap();
        c
    }

}
