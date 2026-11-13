//! Per-song lyrics where a client will find them `[SPEC-LYR-070]`.
//!
//! A sidecar belongs to a *file*, and a DAO capture is one file holding a dozen
//! songs — so `<audiofile>.lyrics` can only ever show all twelve at once. The
//! client's own cache is keyed by **artist and title**, which a cue track has
//! `[SPEC-MPD-056]`, so writing there gives every passage its own words.
//!
//! Read out of Cantata's `context/songview.cpp` rather than guessed:
//!
//! ```text
//! 1. lyrics embedded in the tags (TagLib)
//! 2. <audiofile>.lyrics          beside the music   <- wins over the cache
//! 3. <audiofile>.txt
//! 4. cache/lyrics/<artist>/<title>.lyrics           <- this
//! 5. cache/lyrics/<artist>/<title>.txt
//! 6. ~/.lyrics/…   (not on Windows)
//! 7. online
//! ```
//!
//! **The cache is on the machine the client runs on, not the server.** That is
//! the limit worth stating before anyone relies on this: it works when Lempi
//! and the client share a machine, and does nothing at all when the client is a
//! phone in another room `[SPEC-LYR-075]`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use rusqlite::Connection;

use crate::report::Written;

/// Why a song might get no file.
const KEPT: &str = "left as they were";


/// `Covers::encodeName`, which is what Cantata names these with.
///
/// Ported rather than approximated: a name this disagrees about is a file the
/// client will never look for. `/` everywhere, and on Windows the characters a
/// path may not hold.
pub fn encode_name(name: &str) -> String {
    let mut out = String::with_capacity(name.len());
    for c in name.chars() {
        let bad = c == '/'
            || (cfg!(windows) && matches!(c, '?' | ':' | '<' | '>' | '\\' | '*' | '|' | '"'));
        out.push(if bad { '_' } else { c });
    }
    out
}

/// Where the client keeps its cache, on this machine.
///
/// `None` when it cannot be located, which is not a failure — it is a machine
/// the client has never run on, and there is nothing useful to write there.
pub fn cache_dir() -> Option<PathBuf> {
    #[cfg(windows)]
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .map(|p| p.join("cantata").join("Cantata").join("cache"));
    #[cfg(not(windows))]
    let base = std::env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache")))
        .map(|p| p.join("cantata"));
    base.map(|b| b.join("lyrics"))
}

/// Write one file per passage that has words, named as the client will ask.
///
/// **Named by what MPD will report, not by what Lempi believes.** For a cue
/// track that is the cue sheet's own `TITLE`/`PERFORMER`, which Lempi wrote from
/// MusicBrainz; for an ordinary file it is the file's tags. A name taken from
/// the wrong one of those is a file nothing ever opens.
///
/// **The words are the ones the player shows** `[SPEC-LYR-176]`: the passage's
/// own, else the nearest by `[SPEC-LYR-100]`, and none for a passage marked as
/// having none. `conn` is the catalogue, with the listener half attached where
/// the saved words are to be in reach.
pub fn generate(conn: &Connection, dir: &Path, dry_run: bool) -> Result<Written, String> {
    let saved = crate::db::SavedLyrics::load(conn, "main").map_err(|e| e.message().to_string())?;
    // A capture's passages are named by the cue sheet; everything else by its
    // own tags. `COALESCE` puts them in that order per row.
    let mut q = conn
        .prepare(
            "SELECT p.passage_id, \
                (SELECT r.title FROM passage_recordings pr JOIN recordings r ON r.mbid = pr.mbid \
                 WHERE pr.passage_id = p.passage_id LIMIT 1) AS mb_title, \
                (SELECT a.name FROM passage_recordings pr \
                   JOIN recording_artists ra ON ra.mbid = pr.mbid \
                   JOIN artists a ON a.mbid = ra.artist_mbid \
                 WHERE pr.passage_id = p.passage_id ORDER BY ra.weight DESC LIMIT 1) AS mb_artist, \
                t.title, t.artist, \
                (SELECT COUNT(*) FROM passages x WHERE x.file_id = p.file_id AND x.kind = 'radio') \
             FROM passages p \
               LEFT JOIN file_tags t ON t.file_id = p.file_id \
             WHERE p.kind = 'radio' ORDER BY p.passage_id",
        )
        .map_err(|e| e.to_string())?;
    let rows = q
        .query_map([], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, Option<String>>(2)?,
                r.get::<_, Option<String>>(3)?,
                r.get::<_, Option<String>>(4)?,
                r.get::<_, i64>(5)?,
            ))
        })
        .map_err(|e| e.to_string())?;

    // One file per artist+title, not per passage: two passages of one recording
    // would otherwise write the same path twice and count it twice. Where two
    // passages of one name show different words, the file holds those of the
    // nearer tier `[SPEC-LYR-100]`, within a tier the first by
    // `[SPEC-LYR-120]`, and of equals the lower-numbered passage's
    // (`ShownLyrics::before`).
    let mut want: HashMap<(String, String), crate::db::ShownLyrics> = HashMap::new();
    for (passage_id, mb_title, mb_artist, tag_title, tag_artist, in_file) in rows.flatten() {
        let Some(words) = crate::db::shown_words(conn, "main", &saved, passage_id)
            .map_err(|e| e.message().to_string())?
        else {
            continue;
        };
        let capture = in_file > 1;
        let title = if capture {
            mb_title.clone()
        } else {
            tag_title.clone().or(mb_title.clone())
        };
        let artist = if capture {
            mb_artist.clone()
        } else {
            tag_artist.clone().or(mb_artist.clone())
        };
        let (Some(title), Some(artist)) = (title, artist) else {
            continue;
        };
        if title.trim().is_empty() || artist.trim().is_empty() {
            continue;
        }
        let name = (encode_name(&artist), encode_name(&title));
        if want.get(&name).is_none_or(|held| words.before(held)) {
            want.insert(name, words);
        }
    }

    let mut rep = Written::default();
    for ((artist, title), words) in want {
        let text = words.text;
        let folder = dir.join(&artist);
        let path = folder.join(format!("{title}.lyrics"));
        if let Ok(existing) = std::fs::read_to_string(&path) {
            if existing == text {
                rep.already_current();
            } else {
                // Someone else's, or an older import. Either way not this run's
                // to overwrite: a client may have fetched and saved it, and that
                // was its choice about its own cache.
                rep.passed_over(KEPT);
            }
            continue;
        }
        if dry_run {
            rep.wrote();
            continue;
        }
        if let Err(e) = std::fs::create_dir_all(&folder) {
            rep.failed(format!("{}: {e}", folder.display()));
            continue;
        }
        // Past this point the directory exists, so this is never itself a dry
        // run — the dry-run report already happened above.
        crate::report::write_or_report(&path, text.as_bytes(), false, &mut rep);
    }
    Ok(rep)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The port must agree with Cantata's own, or the file is never opened.
    #[test]
    fn names_are_encoded_as_the_client_encodes_them() {
        assert_eq!(encode_name("Tears for Fears"), "Tears for Fears");
        assert_eq!(encode_name("AC/DC"), "AC_DC", "a slash would make a folder");
        #[cfg(windows)]
        {
            assert_eq!(encode_name("Where?"), "Where_");
            assert_eq!(encode_name("A: B"), "A_ B");
            assert_eq!(encode_name(r#"a"b*c|d<e>f\g"#), "a_b_c_d_e_f_g");
        }
    }

    /// A cache directory that cannot be found is a machine the client has never
    /// run on, and nothing useful can be written there.
    #[test]
    fn a_missing_cache_directory_is_not_a_failure() {
        // Whatever this machine answers, it must not panic and must be absolute
        // when present.
        if let Some(d) = cache_dir() {
            assert!(d.is_absolute());
            assert!(d.ends_with("lyrics"));
        }
    }

    /// `[SPEC-LYR-176]`: two passages of one artist and title make one file,
    /// holding the words a person saved before the imported ones whichever
    /// passage comes first; and a passage marked as having none gets no file.
    #[test]
    fn one_name_is_one_file_of_the_words_the_player_shows() {
        let d = std::env::temp_dir().join(format!(
            "lempi_cache_{}_{}",
            std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
        ));
        std::fs::create_dir_all(&d).unwrap();
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT, audio_md5 TEXT);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
                                    start_ms INTEGER, end_ms INTEGER);
             CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL DEFAULT 1.0);
             CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
             CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT, weight REAL);
             CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT);
             CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, artist TEXT, album TEXT);
             CREATE TABLE lyrics (mbid TEXT PRIMARY KEY, text TEXT, source TEXT, fetched_at TEXT);
             CREATE TABLE passage_lyrics (audio_md5 TEXT, kind TEXT, start_ms INTEGER, end_ms INTEGER,
                                          mbid TEXT, text TEXT, no_words INTEGER, updated_at TEXT,
                                          origin TEXT);
             INSERT INTO files VALUES (1, '/m/a.mp3', 'md5-1'), (2, '/m/b.mp3', 'md5-2'), (3, '/m/c.mp3', 'md5-3');
             INSERT INTO passages VALUES (1, 1, 'radio', 0, 100000), (2, 2, 'radio', 0, 100000),
                                         (3, 3, 'radio', 0, 100000);
             INSERT INTO passage_recordings (passage_id, mbid) VALUES (1, 'rec-a'), (2, 'rec-b'), (3, 'rec-c');
             INSERT INTO file_tags VALUES (1, 'The Song', 'The Band', NULL), (2, 'The Song', 'The Band', NULL),
                                          (3, 'Quiet One', 'The Band', NULL);
             INSERT INTO lyrics VALUES ('rec-a', 'imported a', 'mulibplay', '2026-01-01'),
                                       ('rec-b', 'imported b', 'mulibplay', '2026-01-01'),
                                       ('rec-c', 'imported c', 'mulibplay', '2026-01-01');
             INSERT INTO passage_lyrics VALUES
                 ('md5-2', 'radio', 0, 100000, 'rec-b', 'typed for b', 0, '2026-11-12 10:00:00.000', 'here'),
                 ('md5-3', 'radio', 0, 100000, 'rec-c', '', 1, '2026-11-12 10:00:00.000', 'here');",
        )
        .unwrap();
        let rep = generate(&c, &d, false).unwrap();
        assert_eq!(rep.written, 1, "one name, one file; the marked passage none: {}", rep.summary("song"));
        assert_eq!(
            std::fs::read_to_string(d.join("The Band").join("The Song.lyrics")).unwrap(),
            "typed for b",
            "saved words come before imported ones [SPEC-LYR-120]"
        );
        assert!(!d.join("The Band").join("Quiet One.lyrics").exists(), "a marked passage gets no file");
        let _ = std::fs::remove_dir_all(&d);
    }

    /// `[SPEC-LYR-176]`: where two passages share an artist and title, a
    /// passage's own words are the file's, and never words the other passage
    /// only borrows through its work -- however much later those were saved,
    /// and whichever passage is numbered first. Until 2026-11-13 the later
    /// save won, and a file once written is never replaced `[SPEC-LYR-900]`.
    #[test]
    fn a_passages_own_words_are_not_displaced_by_borrowed_ones() {
        let d = std::env::temp_dir().join(format!(
            "lempi_cache_tier_{}_{}",
            std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
        ));
        std::fs::create_dir_all(&d).unwrap();
        let c = Connection::open_in_memory().unwrap();
        // Two songs, each on two files that are different recordings of one
        // work. In each, one passage has words of its own, saved first; the
        // other has none, and shows a cover's -- another artist's recording of
        // the same work, on a file this node lacks -- saved a day later. In
        // `First` the owner is the lower-numbered passage, in `Second` the
        // borrower is.
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT, audio_md5 TEXT);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
                                    start_ms INTEGER, end_ms INTEGER);
             CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL DEFAULT 1.0);
             CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
             CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT, weight REAL);
             CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT);
             CREATE TABLE recording_works (mbid TEXT, work_mbid TEXT);
             CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, artist TEXT, album TEXT);
             CREATE TABLE lyrics (mbid TEXT PRIMARY KEY, text TEXT, source TEXT, fetched_at TEXT);
             CREATE TABLE passage_lyrics (audio_md5 TEXT, kind TEXT, start_ms INTEGER, end_ms INTEGER,
                                          mbid TEXT, text TEXT, no_words INTEGER, updated_at TEXT,
                                          origin TEXT);
             INSERT INTO files VALUES (1, '/m/1.mp3', 'md5-1'), (2, '/m/2.mp3', 'md5-2'),
                                      (3, '/m/3.mp3', 'md5-3'), (4, '/m/4.mp3', 'md5-4');
             INSERT INTO passages VALUES (1, 1, 'radio', 0, 100000), (2, 2, 'radio', 0, 100000),
                                         (3, 3, 'radio', 0, 100000), (4, 4, 'radio', 0, 100000);
             INSERT INTO passage_recordings (passage_id, mbid) VALUES
                 (1, 'first-own'), (2, 'first-bare'), (3, 'second-bare'), (4, 'second-own');
             INSERT INTO file_tags VALUES (1, 'First', 'The Band', NULL), (2, 'First', 'The Band', NULL),
                                          (3, 'Second', 'The Band', NULL), (4, 'Second', 'The Band', NULL);
             INSERT INTO recording_works VALUES
                 ('first-own', 'w1'), ('first-bare', 'w1'), ('first-cover', 'w1'),
                 ('second-own', 'w2'), ('second-bare', 'w2'), ('second-cover', 'w2');
             INSERT INTO passage_lyrics VALUES
                 ('md5-1', 'radio', 0, 100000, 'first-own', 'the band''s own first', 0, '2026-11-12 10:00:00.000', 'here'),
                 ('md5-4', 'radio', 0, 100000, 'second-own', 'the band''s own second', 0, '2026-11-12 10:00:00.000', 'here'),
                 ('far-1', 'radio', 0, 100000, 'first-cover', 'a cover of first', 0, '2026-11-13 10:00:00.000', 'elsewhere'),
                 ('far-2', 'radio', 0, 100000, 'second-cover', 'a cover of second', 0, '2026-11-13 10:00:00.000', 'elsewhere');",
        )
        .unwrap();
        // What sets the case up: each bare passage does show the cover's.
        let saved = crate::db::SavedLyrics::load(&c, "main").unwrap();
        for (bare, cover) in [(2, "a cover of first"), (3, "a cover of second")] {
            let shown = crate::db::shown_words(&c, "main", &saved, bare).unwrap().unwrap();
            assert_eq!((shown.tier, shown.text.as_str()), (4, cover), "passage {bare} borrows through its work");
        }
        let rep = generate(&c, &d, false).unwrap();
        assert_eq!(rep.written, 2, "one file a name: {}", rep.summary("song"));
        let held = |title: &str| std::fs::read_to_string(d.join("The Band").join(format!("{title}.lyrics"))).unwrap();
        assert_eq!(held("First"), "the band's own first", "the owner numbered first");
        assert_eq!(held("Second"), "the band's own second", "the borrower numbered first");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn the_summary_distinguishes_written_from_left_alone() {
        let mut r = Written::default();
        for _ in 0..2 {
            r.wrote();
        }
        for _ in 0..5 {
            r.already_current();
        }
        r.passed_over(KEPT);
        let s = r.summary("song");
        assert!(s.contains("2 song(s) written"));
        assert!(s.contains("5 already current"));
        assert!(s.contains("1 left as they were"));
        assert!(!s.contains("failed"));
    }
}
