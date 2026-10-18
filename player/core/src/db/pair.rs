//! A fresh installation's two files, made from `sql/schema.sql`
//! `[REQ-AND-201]`.
//!
//! Every installation is split `[PI-DB-020]`, and until 2026-10-17 every pair
//! was made by `tools/split_database.py` from a database Vipunen built, or
//! copied in by hand -- the Moto G's by the spike's `push.sh`, before the app
//! first ran. Nothing could start from nothing: the first truly fresh install,
//! on a Pixel 7a that day, stopped at "unable to open database file", since
//! the player opens the listener read-only first and attaches the library
//! read-only, and neither creates a file. This makes the same two files the
//! split would, from the schema alone, and empty.
//!
//! **Only when neither exists.** One without the other is a damaged
//! installation, not a new one, and is refused by name: making the missing
//! half would put an empty catalogue beside a real listener, or an empty
//! listener beside a real catalogue, without a word `[GOV-SRC-040]`. An
//! appliance never calls this -- its files missing is a fault, and still
//! fails as one.
//!
//! **Which half a table belongs to is `split_database.py`'s list**, the one
//! that has made every pair so far; [`LISTENER_TABLES`] restates its listener
//! side, and a test holds the two to agreement over every table `schema.sql`
//! creates.

use std::path::{Path, PathBuf};

use rusqlite::Connection;

use super::{DbError, QualifyingConn};

const SCHEMA: &str = include_str!("../../../../sql/schema.sql");

/// The listener's half, as `tools/split_database.py`'s `LISTENER_TABLES`
/// names it. Several are created by [`super::PlayerStore`] on open rather than
/// by `schema.sql`, and are listed so the agreement test can see them.
pub const LISTENER_TABLES: &[&str] = &[
    "listener_play_history", "listener_rejections", "listener_flags",
    "listener_preferences", "listener_likes", "listener_programs",
    "listener_program_seeds", "listener_occasions",
    "listener_occasion_points", "listener_characteristics",
    "listener_settings", "player_state",
    "player_settings", "id_reviews", "boundary_reviews", "artist_reviews",
    "selection_decisions", "player_queue", "listener_programme", "file_checks",
    "listener_programme_pending",
];

/// In both halves, as `split_database.py`'s `BOTH`: the schema's own version.
const BOTH: &[&str] = &["schema_meta"];

#[derive(Debug, PartialEq, Eq)]
pub enum Pair {
    /// Both files were there; nothing was written.
    Present,
    /// Neither was; both were made, empty.
    Created,
}

#[derive(Clone, Copy, PartialEq)]
enum Half {
    Library,
    Listener,
}

fn belongs(table: &str, half: Half) -> bool {
    BOTH.contains(&table) || (LISTENER_TABLES.contains(&table) == (half == Half::Listener))
}

/// Make `listener` and `library` from the schema if neither exists.
///
/// Each half is written under a temporary name and renamed into place, so an
/// interrupted first start leaves no half-made file where the player looks,
/// and the next start begins again.
pub fn create_pair_if_absent(listener: &Path, library: &Path) -> Result<Pair, DbError> {
    match (listener.exists(), library.exists()) {
        (true, true) => return Ok(Pair::Present),
        (false, false) => {}
        (has_listener, _) => {
            let (there, missing) = if has_listener { (listener, library) } else { (library, listener) };
            return Err(DbError::Open(format!(
                "{} is there and {} is not: a damaged installation, not a new one, \
                 so no empty half is made beside it",
                there.display(),
                missing.display()
            )));
        }
    }
    let schema = Connection::open_in_memory().map_err(|e| DbError::Open(e.to_string()))?;
    schema.execute_batch(SCHEMA).map_err(|e| DbError::Query(format!("sql/schema.sql: {e}")))?;

    let library_new = beside(library);
    let listener_new = beside(listener);
    build_half(&schema, &library_new, Half::Library)?;
    build_half(&schema, &listener_new, Half::Listener)?;
    std::fs::rename(&library_new, library)
        .map_err(|e| DbError::Open(format!("{}: {e}", library.display())))?;
    if let Err(e) = std::fs::rename(&listener_new, listener) {
        // Not one without the other: take the library back out, so the next
        // start sees a new installation rather than refusing a damaged one.
        let _ = std::fs::remove_file(library);
        return Err(DbError::Open(format!("{}: {e}", listener.display())));
    }
    Ok(Pair::Created)
}

fn beside(path: &Path) -> PathBuf {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".new");
    path.with_file_name(name)
}

fn build_half(schema: &Connection, out: &Path, half: Half) -> Result<(), DbError> {
    let open = |e: rusqlite::Error| DbError::Open(format!("{}: {e}", out.display()));
    let query = |e: rusqlite::Error| DbError::Query(format!("{}: {e}", out.display()));
    // Left by a first start that was interrupted.
    for leftover in ["", "-wal", "-shm"] {
        let mut p = out.as_os_str().to_os_string();
        p.push(leftover);
        let _ = std::fs::remove_file(PathBuf::from(p));
    }
    {
        let dst = Connection::open(out).map_err(open)?;
        dst.pragma_update(None, "journal_mode", "WAL").map_err(open)?;
        // Tables before their indexes; SQLite's own automatic indexes carry
        // no `sql` and are made again by the tables that imply them.
        let mut st = schema
            .prepare(
                "SELECT tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL \
                 ORDER BY type <> 'table', rowid",
            )
            .map_err(query)?;
        let ddl: Vec<(String, String)> = st
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(query)?
            .collect::<Result<_, _>>()
            .map_err(query)?;
        for (table, sql) in ddl {
            if belongs(&table, half) {
                dst.execute_batch(&sql).map_err(query)?;
            }
        }
        let mut st = schema.prepare("SELECT key, value FROM schema_meta").map_err(query)?;
        let meta: Vec<(String, String)> = st
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(query)?
            .collect::<Result<_, _>>()
            .map_err(query)?;
        for (k, v) in meta {
            dst.execute("INSERT INTO schema_meta (key, value) VALUES (?1, ?2)", [k, v])
                .map_err(query)?;
        }
    }
    if half == Half::Library {
        // What an unsplit store adds to its own catalogue -- the browsing
        // index above all -- and a split one cannot, its library attached
        // read-only: here the library is still a file of its own.
        let conn = QualifyingConn::open(
            out,
            out,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE,
        )?;
        super::player_store::ensure_library_tables_if_owned(&conn)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A directory of its own per test, removed when dropped.
    struct Scratch(PathBuf);
    impl Scratch {
        fn new(name: &str) -> Self {
            let d = std::env::temp_dir().join(format!("lempi-pair-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&d);
            std::fs::create_dir_all(&d).unwrap();
            Scratch(d)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn tables(path: &Path) -> Vec<String> {
        let c = Connection::open(path).unwrap();
        let mut st = c
            .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
            .unwrap();
        st.query_map([], |r| r.get(0)).unwrap().map(Result::unwrap).collect()
    }

    /// The quoted names in one of `split_database.py`'s lists, comments left out.
    fn python_list(name: &str) -> Vec<String> {
        let src = include_str!("../../../../tools/split_database.py");
        let start = src.find(&format!("\n{name} = [")).unwrap_or_else(|| panic!("{name} not found"));
        // Comments first: they cite tags, and a tag's `]` would end the list.
        let text: String =
            src[start..].lines().map(|l| l.split('#').next().unwrap_or("")).collect::<Vec<_>>().join("\n");
        let body = &text[text.find('[').unwrap() + 1..text.find(']').unwrap()];
        body.split('"').skip(1).step_by(2).map(str::to_string).collect()
    }

    /// The split tool has made every pair so far; a fresh one must agree with
    /// it on every table, or a node made here and one made there differ.
    #[test]
    fn halves_agree_with_split_database() {
        let library = python_list("LIBRARY_TABLES");
        let listener = python_list("LISTENER_TABLES");
        let both = python_list("BOTH");
        assert!(library.len() > 10 && listener.len() > 10, "the lists did not parse");
        let mut ours: Vec<&str> = LISTENER_TABLES.to_vec();
        ours.sort_unstable();
        let mut theirs: Vec<&str> = listener.iter().map(String::as_str).collect();
        theirs.sort_unstable();
        assert_eq!(ours, theirs, "LISTENER_TABLES differs from split_database.py's");
        assert_eq!(both, BOTH);

        let schema = Connection::open_in_memory().unwrap();
        schema.execute_batch(SCHEMA).unwrap();
        let mut st = schema.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").unwrap();
        let made: Vec<String> = st.query_map([], |r| r.get(0)).unwrap().map(Result::unwrap).collect();
        for t in made {
            let named = library.contains(&t) as u8 + listener.contains(&t) as u8 + both.contains(&t) as u8;
            assert_eq!(named, 1, "schema.sql's {t} is in {named} of split_database.py's lists, not one");
        }
    }

    #[test]
    fn neither_present_makes_both_and_the_player_opens_them() {
        let dir = Scratch::new("fresh");
        let (listener, library) = (dir.path().join("listener.db"), dir.path().join("library.db"));
        assert_eq!(create_pair_if_absent(&listener, &library).unwrap(), Pair::Created);

        let lib = tables(&library);
        let lis = tables(&listener);
        for t in ["files", "passages", "recordings", "file_releases", "schema_meta"] {
            assert!(lib.contains(&t.to_string()), "library lacks {t}: {lib:?}");
        }
        for t in ["listener_play_history", "player_state", "schema_meta"] {
            assert!(lis.contains(&t.to_string()), "listener lacks {t}: {lis:?}");
        }
        assert!(!lib.iter().any(|t| t.starts_with("listener_")), "{lib:?}");
        assert!(!lis.contains(&"passages".to_string()), "{lis:?}");
        assert!(!dir.path().join("library.db.new").exists());

        // What the player does first: both open as a split pair.
        super::super::Library::open_split(&listener, &library).unwrap();
        super::super::PlayerStore::open_split(&listener, &library).unwrap();
        // And a second start finds them and writes nothing.
        assert_eq!(create_pair_if_absent(&listener, &library).unwrap(), Pair::Present);
    }

    #[test]
    fn one_without_the_other_is_refused() {
        let dir = Scratch::new("half");
        let (listener, library) = (dir.path().join("listener.db"), dir.path().join("library.db"));
        std::fs::write(&listener, b"").unwrap();
        let e = create_pair_if_absent(&listener, &library).unwrap_err().to_string();
        assert!(e.contains("damaged installation"), "{e}");
        assert!(!library.exists());
    }
}
