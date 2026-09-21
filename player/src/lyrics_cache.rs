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
pub fn generate(conn: &Connection, dir: &Path, dry_run: bool) -> Result<Written, String> {
    // A capture's passages are named by the cue sheet; everything else by its
    // own tags. `COALESCE` puts them in that order per row.
    let mut q = conn
        .prepare(
            "SELECT l.text, \
                (SELECT r.title FROM passage_recordings pr JOIN recordings r ON r.mbid = pr.mbid \
                 WHERE pr.passage_id = p.passage_id LIMIT 1) AS mb_title, \
                (SELECT a.name FROM passage_recordings pr \
                   JOIN recording_artists ra ON ra.mbid = pr.mbid \
                   JOIN artists a ON a.mbid = ra.artist_mbid \
                 WHERE pr.passage_id = p.passage_id ORDER BY ra.weight DESC LIMIT 1) AS mb_artist, \
                t.title, t.artist, \
                (SELECT COUNT(*) FROM passages x WHERE x.file_id = p.file_id AND x.kind = 'radio') \
             FROM passages p \
               JOIN passage_recordings pr2 ON pr2.passage_id = p.passage_id \
               JOIN lyrics l ON l.mbid = pr2.mbid \
               LEFT JOIN file_tags t ON t.file_id = p.file_id \
             WHERE p.kind = 'radio'",
        )
        .map_err(|e| e.to_string())?;
    let rows = q
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, Option<String>>(2)?,
                r.get::<_, Option<String>>(3)?,
                r.get::<_, Option<String>>(4)?,
                r.get::<_, i64>(5)?,
            ))
        })
        .map_err(|e| e.to_string())?;

    // One file per artist+title, not per passage: two passages of one recording
    // would otherwise write the same path twice and count it twice.
    let mut want: HashMap<(String, String), String> = HashMap::new();
    for (text, mb_title, mb_artist, tag_title, tag_artist, in_file) in rows.flatten() {
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
        want.insert((encode_name(&artist), encode_name(&title)), text);
    }

    let mut rep = Written::default();
    for ((artist, title), text) in want {
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
