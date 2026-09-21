//! Lyrics beside the audio, for a client that can read the music folder
//! `[SPEC-LYR-080]`.
//!
//! The companion to [`crate::lyrics_cache`], and deliberately narrower. Cantata
//! builds this path from **its own configured music folder** joined with the
//! song's path — `song.filePath(MPDConnection::self()->getDetails().dir)` — so
//! a sidecar reaches a client on another machine only where that client can
//! actually reach the music. Where it can, this works for any client that
//! shares the convention, which the cache route never will.
//!
//! **Only files holding a single passage, and the reason is not the obvious
//! one.** A capture is one file holding a dozen songs, so its sidecar could
//! only ever show all twelve at once — but that is not why it is skipped. The
//! sidecar is tried **before** the cache:
//!
//! ```text
//! 1. lyrics embedded in the tags (TagLib)
//! 2. <audiofile>.lyrics   in the client's music folder   <- this
//! 3. <audiofile>.txt
//! 4. cache/lyrics/<artist>/<title>.lyrics                <- lyrics_cache
//! 5. cache/lyrics/<artist>/<title>.txt
//! 6. ~/.lyrics/…   (not on Windows)
//! 7. online
//! ```
//!
//! So a sidecar written beside a capture would **overrule** the per-song words
//! already in the cache, and every song in that file would show all twelve
//! together. Skipping captures is what keeps the two settings complementary
//! rather than one quietly undoing the other.
//!
//! **Writes into the listener's music folder**, like [`crate::cue`] and
//! [`crate::covers`], and is off until asked for the same reason.

use std::path::{Path, PathBuf};

use rusqlite::Connection;

use crate::report::Written;

/// Why a song might get no file.
const KEPT: &str = "left as they were";
const IN_CAPTURE: &str = "inside a capture";


/// `foo.mp3` becomes `foo.lyrics`, which is what the client asks for.
fn sidecar_of(audio: &Path) -> PathBuf {
    audio.with_extension("lyrics")
}

/// Write one file beside every single-passage audio file that has words.
///
/// Idempotent: a sidecar whose content already matches is left alone, and one
/// holding anything else is never replaced — a file already there was somebody's,
/// and this is not the place to overrule them.
pub fn generate(conn: &Connection, dry_run: bool) -> Result<Written, String> {
    let mut q = conn
        .prepare(
            "SELECT f.path, l.text, \
                (SELECT COUNT(*) FROM passages x \
                 WHERE x.file_id = p.file_id AND x.kind = 'radio') \
             FROM passages p JOIN files f USING(file_id) \
               JOIN passage_recordings pr ON pr.passage_id = p.passage_id \
               JOIN lyrics l ON l.mbid = pr.mbid \
             WHERE p.kind = 'radio'",
        )
        .map_err(|e| e.to_string())?;
    let rows = q
        .query_map([], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?))
        })
        .map_err(|e| e.to_string())?;

    let mut rep = Written::default();
    for (path, text, in_file) in rows.flatten() {
        // A capture's words belong in the cache, one file per song. A sidecar
        // here would win over them and show all twelve at once.
        if in_file > 1 {
            rep.passed_over(IN_CAPTURE);
            continue;
        }
        let side = sidecar_of(Path::new(&path));
        if let Ok(existing) = std::fs::read_to_string(&side) {
            if existing == text {
                rep.already_current();
            } else {
                rep.passed_over(KEPT);
            }
            continue;
        }
        crate::report::write_or_report(&side, text.as_bytes(), dry_run, &mut rep);
    }
    Ok(rep)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The client changes the extension rather than appending one, so
    /// `foo.mp3.lyrics` would never be opened.
    #[test]
    fn the_sidecar_replaces_the_extension() {
        assert_eq!(sidecar_of(Path::new("/m/a/foo.mp3")), PathBuf::from("/m/a/foo.lyrics"));
        assert_eq!(sidecar_of(Path::new("/m/a/foo.flac")), PathBuf::from("/m/a/foo.lyrics"));
        assert_eq!(
            sidecar_of(Path::new("/m/a/two.parts.mp3")),
            PathBuf::from("/m/a/two.parts.lyrics"),
            "only the last extension goes"
        );
    }

    fn tmp() -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "lempi_side_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    /// A library of two files: one song of its own, one capture holding two.
    fn library(dir: &Path) -> Connection {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT);
             CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
             CREATE TABLE lyrics (mbid TEXT PRIMARY KEY, text TEXT);",
        )
        .unwrap();
        for (id, name) in [(1, "solo.mp3"), (2, "capture.mp3")] {
            c.execute(
                "INSERT INTO files VALUES (?1, ?2)",
                rusqlite::params![id, dir.join(name).to_string_lossy()],
            )
            .unwrap();
        }
        // Passage 1 is alone in its file; 2 and 3 share one, which makes it a
        // capture.
        for (pid, fid) in [(1, 1), (2, 2), (3, 2)] {
            c.execute("INSERT INTO passages VALUES (?1, ?2, 'radio')", [pid, fid]).unwrap();
            c.execute(
                "INSERT INTO passage_recordings VALUES (?1, ?2)",
                rusqlite::params![pid, format!("mb{pid}")],
            )
            .unwrap();
            c.execute(
                "INSERT INTO lyrics VALUES (?1, ?2)",
                rusqlite::params![format!("mb{pid}"), format!("words {pid}")],
            )
            .unwrap();
        }
        c
    }

    /// The whole point of the restriction: a sidecar beside a capture would win
    /// over the per-song cache and show all of its songs at once.
    #[test]
    fn a_capture_gets_no_sidecar_and_a_single_song_file_does() {
        let d = tmp();
        let c = library(&d);
        let rep = generate(&c, false).unwrap();
        assert_eq!(rep.written, 1);
        assert_eq!(rep.count_of(IN_CAPTURE), 2, "both passages of the capture");
        assert!(rep.failed.is_empty());
        assert_eq!(std::fs::read_to_string(d.join("solo.lyrics")).unwrap(), "words 1");
        assert!(!d.join("capture.lyrics").exists(), "never beside a capture");
        let _ = std::fs::remove_dir_all(&d);
    }

    /// Run twice and the second run must touch nothing.
    #[test]
    fn a_second_run_writes_nothing() {
        let d = tmp();
        let c = library(&d);
        assert_eq!(generate(&c, false).unwrap().written, 1);
        let again = generate(&c, false).unwrap();
        assert_eq!((again.written, again.unchanged), (0, 1));
        let _ = std::fs::remove_dir_all(&d);
    }

    /// Somebody else's file is left exactly as it was.
    #[test]
    fn a_file_already_there_is_never_replaced() {
        let d = tmp();
        let c = library(&d);
        std::fs::write(d.join("solo.lyrics"), "mine, not Lempi's").unwrap();
        let rep = generate(&c, false).unwrap();
        assert_eq!((rep.written, rep.count_of(KEPT)), (0, 1));
        assert_eq!(std::fs::read_to_string(d.join("solo.lyrics")).unwrap(), "mine, not Lempi's");
        let _ = std::fs::remove_dir_all(&d);
    }

    /// A dry run reports what it would do and writes nothing.
    #[test]
    fn a_dry_run_leaves_the_folder_alone() {
        let d = tmp();
        let c = library(&d);
        let rep = generate(&c, true).unwrap();
        assert_eq!(rep.written, 1);
        assert!(!d.join("solo.lyrics").exists(), "counted, not written");
        let _ = std::fs::remove_dir_all(&d);
    }

    /// The count that matters is the one a listener could misread as a failure.
    #[test]
    fn the_summary_says_what_was_left_to_the_cache() {
        let mut r = Written::default();
        for _ in 0..1624 {
            r.wrote();
        }
        for _ in 0..702 {
            r.passed_over(IN_CAPTURE);
        }
        let s = r.summary("file");
        assert!(s.contains("1624 file(s) written"));
        assert!(s.contains("702 inside a capture"));
        assert!(!s.contains("failed"), "silent when nothing failed");
    }
}
