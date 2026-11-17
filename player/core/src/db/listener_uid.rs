//! The listener names a passage by its uid, never its number `[IMPL-UID-060]`.
//!
//! A passage's number is its catalogue's own `[SPEC-DF-035]`: the hub's 16407 is
//! other music on `bose`. Eight listener tables held one until Stage B of IMPL023,
//! and every reading of one beside another catalogue -- the star sync's, a copy
//! restored on another node -- had to translate it, and twice did so wrongly.
//!
//! This converts a listener made before: each of those tables rebuilt in the shape
//! `player_store` now makes, its number turned into the uid its own catalogue gives
//! that passage, by an exact join inside one connection `[IMPL-UID-062]`. Nothing is
//! matched by span or guessed. A number naming no passage there keeps no uid and is
//! counted; a queue entry without one is dropped, counted, since a queue entry is
//! nothing but its passage. Measured 2026-11-16 on the hub and all six nodes: none.
//!
//! One transaction, after a copy of the whole file is taken. Run on open, by
//! `PlayerStore`, so a node converts when its new build first starts and a phone
//! when its app is updated `[IMPL-UID-066]`.

use super::{DbError, QualifyingConn};

/// Each table that named a passage by number, and the statement making its new
/// shape; the first statement of each is its `CREATE TABLE`.
pub(crate) const TABLES: [(&str, &str); 8] = [
    ("listener_play_history", super::player_store::PLAY_TABLE),
    ("listener_rejections", super::player_store::REJECTION_TABLE),
    ("selection_decisions", super::player_store::SELECTION_TABLE),
    ("player_state", super::player_store::PLAYER_STATE_TABLE),
    ("player_queue", super::player_store::PLAYER_QUEUE_TABLE),
    ("id_reviews", super::player_store::REVIEW_TABLE),
    ("boundary_reviews", super::player_store::BOUNDARY_REVIEW_TABLE),
    // Not keyed by its passage, but it names one, and it travels [SPEC-DF-103].
    ("artist_reviews", super::player_store::ARTIST_REVIEW_TABLE),
];

/// What one table's conversion did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Converted {
    pub table: String,
    /// Its rows, before and after: equal, but for `dropped`.
    pub rows: i64,
    /// Rows that named a passage and now hold no uid, because their number names
    /// no passage in this catalogue.
    pub without_uid: i64,
    /// Queue entries left out for that reason.
    pub dropped: i64,
}

fn columns(conn: &rusqlite::Connection, table: &str) -> Result<Vec<(String, String, bool, Option<String>)>, DbError> {
    let mut q = conn.prepare(&format!("PRAGMA main.table_info({table})")).map_err(DbError::query)?;
    let v = q
        .query_map([], |r| Ok((r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, i64>(3)? != 0, r.get::<_, Option<String>>(4)?)))
        .map_err(DbError::query)?
        .collect::<Result<_, _>>()
        .map_err(DbError::query)?;
    Ok(v)
}

/// The tables of this listener that still name a passage by number.
pub fn by_number(conn: &rusqlite::Connection) -> Result<Vec<&'static str>, DbError> {
    let mut out = Vec::new();
    for (t, _) in TABLES {
        if columns(conn, t)?.iter().any(|c| c.0 == "passage_id") {
            out.push(t);
        }
    }
    Ok(out)
}

/// `make`'s statements, its `--` comments taken out first: a comment may hold a
/// `;` -- `REVIEW_TABLE`'s does -- and none of these statements holds a string
/// that could contain one.
fn statements(make: &str) -> Vec<String> {
    let text: String = make.lines().map(|l| l.split("--").next().unwrap_or("")).collect::<Vec<_>>().join("\n");
    text.split(';').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect()
}

/// The `CREATE TABLE` of `make`, for a table named `as_name` instead.
fn create_as(table: &str, make: &str, as_name: &str) -> Result<String, DbError> {
    let all = statements(make);
    let first = all.first().map(String::as_str).unwrap_or("");
    let head = format!("CREATE TABLE IF NOT EXISTS {table} (");
    if !first.starts_with(&head) {
        return Err(DbError::Open(format!("listener_uid: {table}'s statement does not begin `{head}`")));
    }
    Ok(format!("CREATE TABLE {as_name} ({}", &first[head.len()..]))
}

/// Convert every table [`by_number`] names, in one transaction, after copying
/// the whole file to `backup` (a copy already there is kept, never written over).
/// Nothing to do returns an empty list and takes no copy. A catalogue without
/// uids is refused, loudly: this build writes and reads passages only by uid.
///
/// **What to convert is decided under the write lock** `[IMPL-UID-067]`. Two
/// opens can meet on a listener not yet converted -- two processes, or a web
/// route opening a store while the engine's first open is still converting --
/// and both find it by number before either holds the lock. The one that waited
/// would then rebuild tables the other had already converted, from a
/// `passage_id` they no longer have, and its open would fail. The look before
/// the lock only decides whether to take a copy and the lock at all.
pub(crate) fn convert(conn: &QualifyingConn, backup: Option<&std::path::Path>) -> Result<Vec<Converted>, DbError> {
    let todo = by_number(conn)?;
    if todo.is_empty() {
        return Ok(Vec::new());
    }
    let has_uids: bool = conn
        // `xinfo`, which lists every column, a generated one too.
        .query_row("SELECT COUNT(*) FROM pragma_table_xinfo('passages', ?1) WHERE name = 'passage_uid'", [conn.lib_alias()], |r| {
            r.get::<_, i64>(0)
        })
        .map_err(DbError::query)?
        > 0;
    if !has_uids {
        return Err(DbError::Open(format!(
            "this listener names passages by number in {} and its catalogue gives them no uid: this build needs a \
             catalogue with passage uids (IMPL023 Stage A) and has changed nothing [IMPL-UID-060]",
            todo.join(", ")
        )));
    }
    if let Some(b) = backup {
        copy_before_converting(conn, b)?;
    }
    conn.execute_batch("BEGIN IMMEDIATE").map_err(DbError::open)?;
    let done = (|| {
        // Asked again, now that no other open can be converting it. Empty when
        // one did while this one waited: nothing left to do, and no failure.
        let todo = by_number(conn)?;
        let mut done = Vec::new();
        for t in &todo {
            done.push(rebuild(conn, t)?);
        }
        Ok::<_, DbError>(done)
    })();
    match done {
        Ok(d) => {
            conn.execute_batch("COMMIT").map_err(DbError::open)?;
            Ok(d)
        }
        Err(e) => {
            let _ = conn.execute_batch("ROLLBACK");
            Err(e)
        }
    }
}

/// The whole listener copied to `b` before it is converted, unless a copy is
/// there already. `VACUUM INTO` refuses a file that exists, so a second open
/// racing the first to make it `[IMPL-UID-067]` finds the first one's copy taken
/// and goes on: that copy is the listener before either converted it, since each
/// converts only after its copy is made.
fn copy_before_converting(conn: &QualifyingConn, b: &std::path::Path) -> Result<(), DbError> {
    if b.exists() {
        return Ok(());
    }
    match conn.execute("VACUUM main INTO ?1", [b.to_string_lossy().into_owned()]) {
        Ok(_) => Ok(()),
        Err(e) if b.exists() && e.to_string().contains("already exists") => {
            tracing::info!("listener: another open took the copy {} first, and it is kept", b.display());
            Ok(())
        }
        Err(e) => Err(DbError::open(e).during(format_args!("copy the listener to {} before converting it", b.display()))),
    }
}

fn rebuild(conn: &QualifyingConn, table: &'static str) -> Result<Converted, DbError> {
    let make = TABLES.iter().find(|(t, _)| *t == table).map(|(_, m)| *m).unwrap_or("");
    let fail = |what: String| DbError::Open(format!("listener_uid: {table}: {what}; nothing converted"));
    let old = columns(conn, table)?;
    let indexes: Vec<(String, String)> = {
        let mut q = conn
            .prepare("SELECT name, sql FROM main.sqlite_master WHERE type = 'index' AND tbl_name = ?1 AND sql IS NOT NULL")
            .map_err(DbError::query)?;
        let v = q.query_map([table], |r| Ok((r.get(0)?, r.get(1)?))).map_err(DbError::query)?.collect::<Result<_, _>>().map_err(DbError::query)?;
        v
    };
    if let Some((n, _)) = indexes.iter().find(|(_, s)| s.contains("passage_id")) {
        return Err(fail(format!("index {n} names passage_id, and has no uid form here")));
    }
    let new_name = format!("{table}__uid");
    conn.execute_batch(&create_as(table, make, &new_name)?).map_err(DbError::query)?;
    // A column the old table has and the new shape lacks -- one added since by an
    // ensure_* step -- is carried with its declaration, never lost.
    let mut new_cols: Vec<String> = columns(conn, &new_name)?.into_iter().map(|c| c.0).collect();
    for (name, ty, notnull, dflt) in &old {
        if name == "passage_id" || new_cols.contains(name) {
            continue;
        }
        if *notnull && dflt.is_none() {
            return Err(fail(format!("column {name} is NOT NULL without a default, and cannot be carried")));
        }
        let decl = format!("{name} {ty}{}", dflt.as_ref().map(|d| format!(" DEFAULT {d}")).unwrap_or_default());
        conn.execute_batch(&format!("ALTER TABLE {new_name} ADD COLUMN {decl}")).map_err(DbError::query)?;
        new_cols.push(name.clone());
    }
    let carried: Vec<&str> = old.iter().map(|c| c.0.as_str()).filter(|c| *c != "passage_id").collect();
    let uid = "(SELECT p.passage_uid FROM __LIB__.passages p WHERE p.passage_id = o.passage_id)";
    let queue = table == "player_queue";
    let rows: i64 = conn.query_row(&format!("SELECT COUNT(*) FROM main.{table}"), [], |r| r.get(0)).map_err(DbError::query)?;
    let without_uid: i64 = conn
        .query_row(&format!("SELECT COUNT(*) FROM main.{table} o WHERE o.passage_id IS NOT NULL AND {uid} IS NULL"), [], |r| r.get(0))
        .map_err(DbError::query)?;
    conn.execute(
        &format!(
            "INSERT INTO {new_name} ({cols}, passage_uid) SELECT {sel}, {uid} FROM main.{table} o{filter}",
            cols = carried.join(", "),
            sel = carried.iter().map(|c| format!("o.{c}")).collect::<Vec<_>>().join(", "),
            filter = if queue { format!(" WHERE {uid} IS NOT NULL") } else { String::new() },
        ),
        [],
    )
    .map_err(|e| DbError::query(e).during(format_args!("listener_uid: copy {table}")))?;
    let dropped = if queue { without_uid } else { 0 };
    let now: i64 = conn.query_row(&format!("SELECT COUNT(*) FROM {new_name}"), [], |r| r.get(0)).map_err(DbError::query)?;
    if now != rows - dropped {
        return Err(fail(format!("{rows} row(s) became {now}")));
    }
    conn.execute_batch(&format!("DROP TABLE main.{table}; ALTER TABLE {new_name} RENAME TO {table};")).map_err(DbError::query)?;
    for (_, sql) in &indexes {
        conn.execute_batch(sql).map_err(DbError::query)?;
    }
    // And the new shape's own, where the old table lacked one.
    for s in statements(make).iter().skip(1) {
        conn.execute_batch(s).map_err(DbError::query)?;
    }
    Ok(Converted { table: table.to_string(), rows: now, without_uid: if queue { 0 } else { without_uid }, dropped })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmpdir(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("lempi-listener-uid-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// A listener as a node held one before Stage B, its own catalogue beside it, numbered
    /// its own way: passage 10 is A, 11 is B, 12 is C. Play 3 names passage 99, which this
    /// catalogue does not hold.
    fn old_pair(dir: &std::path::Path) -> (std::path::PathBuf, std::path::PathBuf) {
        let lib = dir.join("library.db");
        let lis = dir.join("listener.db");
        let c = rusqlite::Connection::open(&lib).unwrap();
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER,
                                    end_ms INTEGER, passage_uid TEXT);
             INSERT INTO passages VALUES (10, 1, 'radio', 0, 1000, 'u-a'), (11, 1, 'radio', 0, 500, 'u-b'),
                                         (12, 1, 'radio', 0, 700, 'u-c');",
        )
        .unwrap();
        drop(c);
        let c = rusqlite::Connection::open(&lis).unwrap();
        // As the player opens one: the old shape's reference to `passages` names a
        // table this file has never held [IMPL-DBSPLIT-025].
        c.execute_batch("PRAGMA foreign_keys = OFF").unwrap();
        c.execute_batch(
            "CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, played_at INTEGER NOT NULL,
                 passage_id INTEGER REFERENCES passages(passage_id) ON DELETE SET NULL, mbid TEXT,
                 heard_ms INTEGER, span_ms INTEGER, selected_by TEXT);
             CREATE INDEX listener_play_time ON listener_play_history(played_at);
             INSERT INTO listener_play_history VALUES (1, 100, 10, 'm-a', 1000, 1000, 'director'),
                 (2, 200, 11, 'm-b', NULL, NULL, NULL), (3, 300, 99, 'm-gone', NULL, NULL, NULL),
                 (4, 400, NULL, 'm-x', NULL, NULL, NULL);
             CREATE TABLE listener_rejections (rejection_id INTEGER PRIMARY KEY, rejected_at INTEGER NOT NULL,
                 kind TEXT NOT NULL, passage_id INTEGER, mbid TEXT, heard_ms INTEGER, span_ms INTEGER);
             INSERT INTO listener_rejections VALUES (1, 150, 'skip', 12, 'm-c', 10, 700);
             CREATE TABLE selection_decisions (decision_id INTEGER PRIMARY KEY, selected_at INTEGER NOT NULL,
                 passage_id INTEGER, program_id INTEGER, detail BLOB NOT NULL);
             INSERT INTO selection_decisions VALUES (1, 90, 10, NULL, x'00ff');
             CREATE TABLE player_state (id INTEGER PRIMARY KEY CHECK (id = 1), passage_id INTEGER,
                 position_ms INTEGER NOT NULL DEFAULT 0, playing INTEGER NOT NULL DEFAULT 0,
                 volume REAL NOT NULL DEFAULT 1.0, updated_at TEXT NOT NULL);
             INSERT INTO player_state VALUES (1, 11, 4321, 1, 0.5, 't');
             CREATE TABLE player_queue (position INTEGER PRIMARY KEY, passage_id INTEGER NOT NULL, updated_at TEXT NOT NULL);
             INSERT INTO player_queue VALUES (0, 12, 't'), (1, 99, 't'), (2, 10, 't');
             CREATE TABLE id_reviews (passage_id INTEGER PRIMARY KEY, decision TEXT NOT NULL, chosen_mbid TEXT,
                 decided_at TEXT NOT NULL, chosen_release_mbid TEXT, previous_mbid TEXT, applied_at TEXT, origin TEXT);
             INSERT INTO id_reviews VALUES (11, 'kept', NULL, 'd1', NULL, NULL, NULL, 'bose');
             CREATE TABLE boundary_reviews (passage_id INTEGER PRIMARY KEY, start_ms INTEGER NOT NULL,
                 end_ms INTEGER NOT NULL, lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
                 decided_at TEXT NOT NULL, applied_at TEXT, audio_md5 TEXT);
             INSERT INTO boundary_reviews VALUES (12, 0, 690, NULL, NULL, NULL, 'd2', NULL, 'C');
             CREATE TABLE artist_reviews (recording_mbid TEXT PRIMARY KEY, passage_id INTEGER, artist_mbid TEXT NOT NULL,
                 artist_name TEXT NOT NULL, previous_artist_mbid TEXT, previous_artist_name TEXT,
                 previous_artist_weight REAL, decided_at TEXT NOT NULL, applied_at TEXT);
             INSERT INTO artist_reviews VALUES ('m-a', 10, 'ar-1', 'Artist', NULL, NULL, NULL, 'd3', NULL);",
        )
        .unwrap();
        drop(c);
        (lis, lib)
    }

    fn open(lis: &std::path::Path, lib: &std::path::Path) -> QualifyingConn {
        QualifyingConn::open(lis, lib, rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE).unwrap()
    }

    fn rows(c: &rusqlite::Connection, sql: &str) -> Vec<String> {
        let mut q = c.prepare(sql).unwrap();
        let n = q.column_count();
        let v = q
            .query_map([], |r| Ok((0..n).map(|i| format!("{:?}", r.get::<_, rusqlite::types::Value>(i).unwrap())).collect::<Vec<_>>().join(",")))
            .unwrap()
            .filter_map(Result::ok)
            .collect();
        v
    }

    /// Each new shape is one whole `CREATE TABLE` of its own name, then only indexes.
    #[test]
    fn every_new_shape_splits_into_its_table_and_its_indexes() {
        for (t, make) in TABLES {
            let all = statements(make);
            assert!(all[0].starts_with(&format!("CREATE TABLE IF NOT EXISTS {t} (")) && all[0].ends_with(')'), "{t}: {}", all[0]);
            assert!(all[1..].iter().all(|s| s.starts_with("CREATE INDEX IF NOT EXISTS ")), "{t}: {all:?}");
            assert!(all[0].contains("passage_uid") && !all[0].contains("passage_id"), "{t} names its passage by uid");
            let c = rusqlite::Connection::open_in_memory().unwrap();
            c.execute_batch(&create_as(t, make, "x").unwrap()).unwrap();
        }
    }

    #[test]
    fn a_listener_by_number_becomes_one_by_uid_through_its_own_catalogue() {
        let d = tmpdir("converts");
        let (lis, lib) = old_pair(&d);
        let c = open(&lis, &lib);
        assert_eq!(by_number(&c).unwrap().len(), 8);
        let backup = d.join("listener.pre-uid.db");
        let done = convert(&c, Some(&backup)).unwrap();
        let by: std::collections::HashMap<&str, &Converted> = done.iter().map(|x| (x.table.as_str(), x)).collect();
        assert_eq!((by["listener_play_history"].rows, by["listener_play_history"].without_uid), (4, 1), "{done:?}");
        assert_eq!((by["player_queue"].rows, by["player_queue"].dropped), (2, 1), "a queue entry naming nothing is left out");
        assert!(by_number(&c).unwrap().is_empty(), "nothing names a passage by number any more");
        assert_eq!(
            rows(&c, "SELECT play_id, played_at, passage_uid, mbid, heard_ms, selected_by FROM listener_play_history ORDER BY play_id"),
            [
                "Integer(1),Integer(100),Text(\"u-a\"),Text(\"m-a\"),Integer(1000),Text(\"director\")",
                "Integer(2),Integer(200),Text(\"u-b\"),Text(\"m-b\"),Null,Null",
                "Integer(3),Integer(300),Null,Text(\"m-gone\"),Null,Null",
                "Integer(4),Integer(400),Null,Text(\"m-x\"),Null,Null",
            ],
            "each play keeps its id, time and recording; its passage is its uid, and a number naming nothing here none"
        );
        assert_eq!(rows(&c, "SELECT passage_uid, position_ms, playing FROM player_state"), ["Text(\"u-b\"),Integer(4321),Integer(1)"]);
        assert_eq!(rows(&c, "SELECT position, passage_uid FROM player_queue ORDER BY position"),
                   ["Integer(0),Text(\"u-c\")", "Integer(2),Text(\"u-a\")"]);
        assert_eq!(rows(&c, "SELECT passage_uid, decision, origin FROM id_reviews"), ["Text(\"u-b\"),Text(\"kept\"),Text(\"bose\")"]);
        assert_eq!(rows(&c, "SELECT passage_uid, end_ms, audio_md5 FROM boundary_reviews"), ["Text(\"u-c\"),Integer(690),Text(\"C\")"]);
        assert_eq!(rows(&c, "SELECT passage_uid, detail FROM selection_decisions"), ["Text(\"u-a\"),Blob([0, 255])"]);
        assert_eq!(rows(&c, "SELECT passage_uid, kind FROM listener_rejections"), ["Text(\"u-c\"),Text(\"skip\")"]);
        assert_eq!(rows(&c, "SELECT recording_mbid, passage_uid FROM artist_reviews"), ["Text(\"m-a\"),Text(\"u-a\")"]);
        let idx = rows(&c, "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'listener_play_history' AND sql IS NOT NULL ORDER BY name");
        assert_eq!(idx, ["Text(\"listener_play_mbid\")", "Text(\"listener_play_time\")"], "its index kept, and the shape's own added");
        let pk = rows(&c, "SELECT name FROM pragma_table_info('id_reviews') WHERE pk = 1");
        assert_eq!(pk, ["Text(\"passage_uid\")"], "a review is keyed by its passage's uid");
        // The copy is the listener as it was, whole.
        let b = rusqlite::Connection::open(&backup).unwrap();
        assert_eq!(rows(&b, "SELECT passage_id FROM listener_play_history ORDER BY play_id"),
                   ["Integer(10)", "Integer(11)", "Integer(99)", "Null"]);
        drop(b);
        // A second open has nothing to do, and takes no second copy.
        std::fs::remove_file(&backup).unwrap();
        assert!(convert(&c, Some(&backup)).unwrap().is_empty());
        assert!(!backup.exists());
    }

    /// `[IMPL-UID-067]`: two opens meet on a listener by number. The second
    /// found it so before the first committed, took its copy, and waited for the
    /// lock; holding it, it finds nothing left to convert, and its open
    /// succeeds. Until 2026-11-17 it rebuilt from what it found before the lock
    /// and failed on `o.passage_id`, a column the first had already removed.
    #[test]
    fn a_second_open_that_waited_for_the_first_finds_nothing_left_to_convert() {
        use std::time::{Duration, Instant};
        let d = tmpdir("race");
        let (lis, lib) = old_pair(&d);
        // In WAL mode, as every listener is `[SPEC-STAR-107]`.
        rusqlite::Connection::open(&lis).unwrap().pragma_update(None, "journal_mode", "WAL").unwrap();
        let first = open(&lis, &lib);
        first.busy_timeout(Duration::from_secs(20)).unwrap();
        first.execute_batch("BEGIN IMMEDIATE").unwrap();
        for t in by_number(&first).unwrap() {
            rebuild(&first, t).unwrap();
        }
        // Converted and not committed, so the second open reads it by number.
        let backup = d.join("listener.pre-uid.db");
        let (l2, c2, b2) = (lis.clone(), lib.clone(), backup.clone());
        let second = std::thread::spawn(move || {
            let c = open(&l2, &c2);
            c.busy_timeout(Duration::from_secs(20)).unwrap();
            convert(&c, Some(&b2))
        });
        // Its copy is made after it has looked and before it waits for the lock.
        let t0 = Instant::now();
        while !backup.exists() {
            assert!(t0.elapsed() < Duration::from_secs(20), "the second open never took its copy");
            std::thread::sleep(Duration::from_millis(10));
        }
        std::thread::sleep(Duration::from_millis(300));
        first.execute_batch("COMMIT").unwrap();
        let got = second.join().unwrap();
        assert_eq!(got.map_err(|e| e.to_string()), Ok(Vec::new()), "the second open converts nothing, and does not fail");
        assert!(by_number(&first).unwrap().is_empty());
        assert_eq!(rows(&first, "SELECT passage_uid FROM listener_play_history WHERE play_id = 1"), ["Text(\"u-a\")"]);
        let b = rusqlite::Connection::open(&backup).unwrap();
        assert_eq!(rows(&b, "SELECT passage_id FROM listener_play_history WHERE play_id = 1"), ["Integer(10)"],
                   "and its copy is the listener before either converted it");
    }

    /// The copy a racing open finds already made is kept `[IMPL-UID-067]`, and
    /// the refusal that tells the race apart is the one this SQLite gives.
    #[test]
    fn a_copy_another_open_made_first_is_kept() {
        let d = tmpdir("copy-kept");
        let (lis, lib) = old_pair(&d);
        let c = open(&lis, &lib);
        let b = d.join("copy.db");
        copy_before_converting(&c, &b).unwrap();
        assert!(b.exists());
        let err = c.execute("VACUUM main INTO ?1", [b.to_string_lossy().into_owned()]).unwrap_err();
        assert!(err.to_string().contains("already exists"), "the refusal copy_before_converting reads: {err}");
        copy_before_converting(&c, &b).unwrap();
        let k = rusqlite::Connection::open(&b).unwrap();
        assert_eq!(rows(&k, "SELECT COUNT(*) FROM listener_play_history"), ["Integer(4)"], "the first copy, kept");
    }

    /// A catalogue without uids is refused, and the listener left exactly as it was.
    #[test]
    fn a_catalogue_without_uids_is_refused_and_nothing_changes() {
        let d = tmpdir("no-uids");
        let (lis, lib) = old_pair(&d);
        let l = rusqlite::Connection::open(&lib).unwrap();
        l.execute_batch("ALTER TABLE passages DROP COLUMN passage_uid").unwrap();
        drop(l);
        let c = open(&lis, &lib);
        let err = convert(&c, Some(&d.join("b.db"))).unwrap_err();
        assert!(err.message().contains("gives them no uid"), "{err}");
        assert_eq!(by_number(&c).unwrap().len(), 8, "nothing converted");
        assert!(!d.join("b.db").exists(), "and no copy taken for a conversion that never began");
    }

    /// A failure part-way puts every table back: here an index on the number, which has no
    /// uid form, met after three tables were already rebuilt.
    #[test]
    fn a_table_that_cannot_be_carried_leaves_every_table_as_it_was() {
        let d = tmpdir("rollback");
        let (lis, lib) = old_pair(&d);
        let l = rusqlite::Connection::open(&lis).unwrap();
        l.execute_batch("CREATE INDEX q_by_number ON player_queue(passage_id);").unwrap();
        drop(l);
        let c = open(&lis, &lib);
        let err = convert(&c, None).unwrap_err();
        assert!(err.message().contains("q_by_number"), "{err}");
        assert_eq!(by_number(&c).unwrap().len(), 8, "the tables converted before it are put back too");
        assert_eq!(rows(&c, "SELECT passage_id FROM listener_play_history WHERE play_id = 1"), ["Integer(10)"]);
    }
}
