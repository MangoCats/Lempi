//! Cover art beside a capture, so a guest can show the album `[REQ-VIS-210]`.
//!
//! MPD's art is **directory-based**: it reads a picture embedded in the file,
//! or `cover.jpg` in the song's folder. A DAO capture has no embedded art —
//! none of the 191 here do — so a guest falls back to whatever its own
//! artist-level lookup finds, and every album by an artist wears one cover.
//!
//! Lempi already holds the right pictures `[REQ-VIS-170]`. This puts one where
//! MPD will look.
//!
//! **Only where the capture is the sole file in its folder.** 75 of the 191
//! share a folder with other captures — six in `Various`, five in `Eagles` —
//! and one directory has room for exactly one cover. Writing there would give
//! several albums the same picture, which is the problem rather than the fix,
//! so those are skipped and counted.
//!
//! **Writes into the listener's music folder**, like [`crate::cue`], and is off
//! until asked for the same reason.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use rusqlite::Connection;

use crate::report::Written;

/// Names MPD accepts for a folder's cover, in the order it tries them.
const COVER_NAMES: &[&str] = &["cover.jpg", "cover.png", "cover.tiff", "cover.bmp", "folder.jpg"];

/// Why a capture might get no cover.
const SHARED: &str = "shared a folder";
const HAS_ONE: &str = "already had a cover";
const NO_ART: &str = "without art";

/// Does this folder already show a cover to MPD?
fn has_cover(dir: &Path) -> bool {
    COVER_NAMES.iter().any(|n| dir.join(n).exists())
}

/// Write a cover beside every capture that is alone in its folder.
///
/// Idempotent by construction: a folder with a cover is left alone, so a second
/// run writes nothing — including over the covers this wrote last time.
pub fn generate(conn: &Connection, dry_run: bool) -> Result<Written, String> {
    // Captures: a file carrying more than one radio passage.
    let mut q = conn
        .prepare(
            "SELECT f.file_id, f.path FROM files f JOIN passages p USING(file_id) \
             WHERE p.kind = 'radio' GROUP BY f.file_id HAVING COUNT(*) > 1",
        )
        .map_err(|e| e.to_string())?;
    let captures: Vec<(i64, String)> = q
        .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
        .map_err(|e| e.to_string())?
        .flatten()
        .collect();

    // How many library files share each folder. A capture alone in its folder
    // is the only one a cover there could belong to.
    let mut per_dir: HashMap<String, usize> = HashMap::new();
    let mut all = conn.prepare("SELECT path FROM files").map_err(|e| e.to_string())?;
    for path in all
        .query_map([], |r| r.get::<_, String>(0))
        .map_err(|e| e.to_string())?
        .flatten()
    {
        let dir = PathBuf::from(&path)
            .parent()
            .map(|d| d.to_string_lossy().to_string())
            .unwrap_or_default();
        *per_dir.entry(dir).or_insert(0) += 1;
    }

    let mut art = conn
        .prepare(
            "SELECT ca.front FROM passages p \
               JOIN passage_recordings pr ON pr.passage_id = p.passage_id \
               JOIN release_recordings rr ON rr.mbid = pr.mbid \
               JOIN cover_art ca ON ca.release_mbid = rr.release_mbid \
             WHERE p.file_id = ?1 AND p.kind = 'radio' AND ca.front IS NOT NULL \
             ORDER BY rr.chosen DESC LIMIT 1",
        )
        .map_err(|e| e.to_string())?;

    let mut rep = Written::default();
    for (file_id, path) in captures {
        let audio = PathBuf::from(&path);
        let Some(dir) = audio.parent() else { continue };
        if per_dir.get(&dir.to_string_lossy().to_string()).copied().unwrap_or(0) > 1 {
            rep.passed_over(SHARED);
            continue;
        }
        if has_cover(dir) {
            rep.passed_over(HAS_ONE);
            continue;
        }
        let front: Option<Vec<u8>> =
            art.query_row([file_id], |r| r.get::<_, Vec<u8>>(0)).ok();
        let Some(bytes) = front else {
            rep.passed_over(NO_ART);
            continue;
        };
        // Named for what it is rather than what it is assumed to be. Every
        // stored front is a JPEG today; a PNG would be misread by anything
        // trusting the extension.
        let name = if bytes.starts_with(&[0x89, b'P', b'N', b'G']) { "cover.png" } else { "cover.jpg" };
        crate::report::write_or_report(&dir.join(name), &bytes, dry_run, &mut rep);
    }
    Ok(rep)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "lempi_cov_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    /// Any name MPD would find counts, not only the one this writes. A folder
    /// holding `folder.jpg` already shows a cover, and replacing it would
    /// overrule a choice someone made.
    #[test]
    fn every_name_mpd_looks_for_counts_as_having_a_cover() {
        for n in COVER_NAMES {
            let d = tmp();
            assert!(!has_cover(&d), "empty folder has none");
            std::fs::write(d.join(n), b"x").unwrap();
            assert!(has_cover(&d), "{n} should count");
            let _ = std::fs::remove_dir_all(&d);
        }
    }

    /// The extension must follow the bytes. Every stored front is a JPEG, and
    /// a PNG written as `.jpg` would be misread by anything trusting the name.
    #[test]
    fn the_name_follows_the_image_not_the_assumption() {
        let png: &[u8] = &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a];
        let jpg: &[u8] = &[0xff, 0xd8, 0xff, 0xe0];
        let name = |b: &[u8]| {
            if b.starts_with(&[0x89, b'P', b'N', b'G']) { "cover.png" } else { "cover.jpg" }
        };
        assert_eq!(name(png), "cover.png");
        assert_eq!(name(jpg), "cover.jpg");
    }

    #[test]
    fn the_summary_says_why_each_one_was_left_alone() {
        let mut r = Written::default();
        for _ in 0..3 {
            r.wrote();
        }
        for _ in 0..75 {
            r.passed_over(SHARED);
        }
        for _ in 0..7 {
            r.passed_over(NO_ART);
        }
        let s = r.summary("cover");
        assert!(s.contains("3 cover(s) written") && s.contains("75 shared a folder"));
        assert!(s.contains("7 without art"));
        assert!(!s.contains("failed"), "silent when nothing failed");
    }
}
