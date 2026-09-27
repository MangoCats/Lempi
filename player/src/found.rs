//! Music the phone finds in shared storage `[REQ-AND-260]`.
//!
//! A phone is handed a list of audio files -- on Android, what MediaStore
//! knows -- and each one it has not catalogued is looked at once:
//!
//! * **Its bytes match a catalogued file that is missing from its place**: it
//!   is that file, moved, and is bound where it now is. It keeps everything
//!   the catalogue holds for it.
//! * **Its bytes match a catalogued file that is still in place**: a second
//!   copy, counted and left alone.
//! * **Otherwise it becomes a tags-only passage** `[REQ-AND-260]`: one
//!   whole-file radio passage, named from the file's own tags, with no MBID
//!   and no flavor. The Director already plays such a passage as it plays an
//!   unidentified one `[REQ-AND-310]`, and rotates it by its tag artist
//!   `[REQ-AND-315]`.
//!
//! **What identifies it.** The catalogue keys every file by `audio_md5`, its
//! signature, and since 2026-09-27 the phone computes that itself, with the
//! same Symphonia reading every other host uses ([`crate::identity`],
//! `[REQ-AND-270]`). A found file is keyed by its signature `[SPEC-SC-041]`,
//! so the same audio in bytes of its own -- a re-tagged copy -- is recognised
//! as what it is: a second copy of a file the phone holds, or that file moved.
//! When Vipunen later names the signature, the bundle import makes the file
//! whole where it lies ([`lempi_core::bundle::import_staged`]).
//!
//! Until then found files were keyed `sha256:<their byte hash>`, a namespace
//! a 32-hex signature cannot enter. A scan identifies any still keyed so.
//! Tags-only is marked by the file's one `found:tags` passage, never by its
//! key ([`lempi_core::bundle::TAGS_ONLY_SQL`]).
//!
//! Everything recorded is read from the file: its tags, its length from the
//! container ([`crate::tags::duration_ms`]), its byte hash and its signature.
//! No flavor and no segmentation are derived.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::path::{Path, PathBuf};

use rusqlite::{params, Connection, OptionalExtension};

use crate::bundle::{sha256_file, TAGS_ONLY_SQL};
use crate::identity;

/// The `boundary_src` of a found file's passage, which is how it is known to
/// be tags-only wherever it is shown `[REQ-AND-260]`.
pub const TAGS_ONLY: &str = "found:tags";

/// The key a found file was catalogued under before the phone computed
/// signatures `[SPEC-SC-041]`. Read, to identify such rows; never written now.
pub fn found_key(sha256: &str) -> String {
    format!("sha256:{sha256}")
}

#[derive(Debug, Default, PartialEq)]
pub struct FoundReport {
    /// Files looked at: every path given that the catalogue did not already
    /// hold at that path.
    pub looked_at: usize,
    /// Catalogued files found moved, and bound where they now are.
    pub rebound: usize,
    /// New tags-only passages.
    pub tags_only: usize,
    /// Second copies of files the catalogue holds in place.
    pub duplicates: usize,
    /// Files that could not be read as audio, with why.
    pub unreadable: Vec<String>,
    /// Tags-only entries removed: outside a `Music` folder, or a second copy
    /// of a file Vipunen named.
    pub retired: usize,
    /// Catalogued files that had no byte hash, hashed now.
    pub hashed: usize,
    /// Tags-only files still keyed by their byte hash, re-keyed by their
    /// signature now `[SPEC-SC-041]`.
    pub identified: usize,
}

/// ISO-8601 without a zone, as every other writer of the library does, and
/// from SQLite rather than a date crate `[REQ-HW-140]`.
fn now_iso(db: &Connection) -> Result<String, String> {
    db.query_row("SELECT strftime('%Y-%m-%dT%H:%M:%S','now')", [], |r| r.get(0)).map_err(|e| e.to_string())
}

fn mtime(meta: &std::fs::Metadata) -> f64 {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Whether a path is inside a `Music` folder, on any storage volume -- the
/// phone's music is what is in `Music/` `[REQ-AND-220]`. MediaStore also
/// counts audio elsewhere as music: on 2026-09-26 the first scan on the Moto
/// G catalogued a `voicemail.mp3` and other recordings from `Download/`,
/// which radio has no business choosing.
pub fn in_music_folder(path: &str) -> bool {
    path.replace('\\', "/").split('/').any(|c| c == "Music")
}

/// How many files each transaction takes. A phone found 5,761 on its SD card
/// on 2026-09-26: as one transaction, a first scan would have held its write
/// for many minutes, and lost everything if the app was stopped part-way.
const BATCH: usize = 50;

/// Look at each of `paths` the catalogue does not already hold, as above, in
/// batches that each commit: a scan stopped part-way keeps what it did, and
/// the next one takes up the rest, since a catalogued path is skipped.
pub fn scan(db: &mut Connection, paths: &[PathBuf]) -> Result<FoundReport, String> {
    crate::db::ensure_sha256_column(db);
    crate::db::ensure_md5_generator_column(db);
    // In this order: a struct literal evaluates its fields as written.
    let mut rep =
        FoundReport { retired: retire_outside_music(db)?, hashed: hash_unhashed(db)?, ..Default::default() };
    let (identified, merged) = identify_found(db)?;
    rep.identified = identified;
    rep.retired += merged;
    rep.retired += retire_copies(db)?;
    rep.retired += retire_deleted(db)?;
    let paths: Vec<PathBuf> =
        paths.iter().filter(|p| in_music_folder(&p.to_string_lossy())).cloned().collect();
    let paths = &paths[..];
    let total = paths.len();
    for (i, chunk) in paths.chunks(BATCH).enumerate() {
        let tx = db.transaction().map_err(|e| e.to_string())?;
        scan_batch(&tx, chunk, &mut rep)?;
        tx.commit().map_err(|e| e.to_string())?;
        if rep.looked_at > 0 {
            tracing::info!(
                "found: {} of {} listed; {} tags-only, {} rebound, {} duplicates so far",
                ((i + 1) * BATCH).min(total), total, rep.tags_only, rep.rebound, rep.duplicates
            );
        }
    }
    Ok(rep)
}

/// Catalogued files with no byte hash -- rows written before the column
/// existed -- hashed now, where the file is here. Without it a second copy
/// of such a file cannot be recognised: on the Moto G, 47 files on the SD
/// card were catalogued as tags-only beside the same songs in
/// `Music/Lempi/`, whose rows predated the column.
fn hash_unhashed(db: &mut Connection) -> Result<usize, String> {
    let rows: Vec<(i64, String)> = {
        let mut q = db
            .prepare("SELECT file_id, path FROM files WHERE sha256 IS NULL")
            .map_err(|e| e.to_string())?;
        let v = q
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(|e| e.to_string())?
            .filter_map(Result::ok)
            .collect();
        v
    };
    let tx = db.transaction().map_err(|e| e.to_string())?;
    let mut n = 0;
    for (id, path) in rows {
        if let Ok(h) = sha256_file(Path::new(&path)) {
            tx.execute("UPDATE files SET sha256 = ?1 WHERE file_id = ?2", params![h, id])
                .map_err(|e| e.to_string())?;
            n += 1;
        }
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(n)
}

/// Tags-only entries whose bytes a named file also holds: second copies,
/// retired so the same song is not in the Director twice. The named one is
/// kept; it is the one Vipunen knows.
fn retire_copies(db: &mut Connection) -> Result<usize, String> {
    let ids: Vec<(i64, String)> = {
        let mut q = db
            .prepare(&format!(
                "SELECT f.file_id, f.path FROM files f WHERE {TAGS_ONLY_SQL} AND EXISTS \
                 (SELECT 1 FROM files g WHERE g.sha256 = f.sha256 AND g.file_id <> f.file_id AND NOT EXISTS \
                  (SELECT 1 FROM passages q WHERE q.file_id = g.file_id AND q.boundary_src = 'found:tags'))"
            ))
            .map_err(|e| e.to_string())?;
        let v = q
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(|e| e.to_string())?
            .filter_map(Result::ok)
            .collect();
        v
    };
    let tx = db.transaction().map_err(|e| e.to_string())?;
    for (id, path) in &ids {
        let _ = tx.execute("DELETE FROM file_tags WHERE file_id = ?1", params![id]);
        tx.execute("DELETE FROM passages WHERE file_id = ?1", params![id]).map_err(|e| e.to_string())?;
        tx.execute("DELETE FROM files WHERE file_id = ?1", params![id]).map_err(|e| e.to_string())?;
        tracing::info!("found: retired {path}, a second copy of a file Vipunen named");
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(ids.len())
}

/// Tags-only entries whose file has been deleted, while the folder it was in
/// is still there. The folder is the guard: an SD card taken out makes every
/// file on it look deleted, and those entries must survive until it is back.
/// Found 2026-09-26, when seven files were deleted from the Moto G's
/// `Music/Children's/` and their passages were still there to be chosen.
fn retire_deleted(db: &mut Connection) -> Result<usize, String> {
    let rows = tags_only_rows(db)?;
    let gone: Vec<(i64, String)> = rows
        .into_iter()
        .filter(|(_, p)| {
            let p = Path::new(p);
            !p.exists() && p.parent().is_some_and(|d| d.is_dir())
        })
        .collect();
    let tx = db.transaction().map_err(|e| e.to_string())?;
    for (id, path) in &gone {
        let _ = tx.execute("DELETE FROM file_tags WHERE file_id = ?1", params![id]);
        tx.execute("DELETE FROM passages WHERE file_id = ?1", params![id]).map_err(|e| e.to_string())?;
        tx.execute("DELETE FROM files WHERE file_id = ?1", params![id]).map_err(|e| e.to_string())?;
        tracing::info!("found: retired {path}, which has been deleted");
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(gone.len())
}

/// Tags-only entries outside a `Music` folder, with their passages. Only
/// tags-only: they hold nothing the file itself does not, and nothing
/// Vipunen sent. Their play history stays in the listener, as history.
fn retire_outside_music(db: &mut Connection) -> Result<usize, String> {
    let tx = db.transaction().map_err(|e| e.to_string())?;
    let rows = tags_only_rows(&tx)?;
    let mut n = 0;
    for (id, path) in rows.into_iter().filter(|(_, p)| !in_music_folder(p)) {
        let _ = tx.execute("DELETE FROM file_tags WHERE file_id = ?1", params![id]);
        tx.execute("DELETE FROM passages WHERE file_id = ?1", params![id]).map_err(|e| e.to_string())?;
        tx.execute("DELETE FROM files WHERE file_id = ?1", params![id]).map_err(|e| e.to_string())?;
        tracing::info!("found: retired {path}, which is not in a Music folder");
        n += 1;
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(n)
}

/// Every tags-only file: (file_id, path).
fn tags_only_rows(db: &Connection) -> Result<Vec<(i64, String)>, String> {
    let mut q = db
        .prepare(&format!("SELECT f.file_id, f.path FROM files f WHERE {TAGS_ONLY_SQL}"))
        .map_err(|e| e.to_string())?;
    let v = q
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .collect();
    Ok(v)
}

/// Remove a tags-only file's rows.
fn drop_found(tx: &rusqlite::Transaction, id: i64) -> Result<(), String> {
    let _ = tx.execute("DELETE FROM file_tags WHERE file_id = ?1", params![id]);
    tx.execute("DELETE FROM passages WHERE file_id = ?1", params![id]).map_err(|e| e.to_string())?;
    tx.execute("DELETE FROM files WHERE file_id = ?1", params![id]).map_err(|e| e.to_string())?;
    Ok(())
}

/// Tags-only files still keyed `sha256:<bytes>`, from before the phone
/// computed signatures, identified now `[SPEC-SC-041]`. Where no other row has
/// the signature, the file is re-keyed by it. Where one does, this file is that
/// audio in bytes of its own: a second copy, retired, or -- if that row's file
/// is gone -- the file itself, moved and re-tagged, which the row then takes
/// up. A file that cannot be read keeps its key. Returns (re-keyed, retired).
fn identify_found(db: &mut Connection) -> Result<(usize, usize), String> {
    let rows: Vec<(i64, String, i64, f64, Option<String>)> = {
        let mut q = db
            .prepare("SELECT file_id, path, size_bytes, mtime, sha256 FROM files WHERE audio_md5 LIKE 'sha256:%'")
            .map_err(|e| e.to_string())?;
        let v = q
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))
            .map_err(|e| e.to_string())?
            .filter_map(Result::ok)
            .collect();
        v
    };
    let tx = db.transaction().map_err(|e| e.to_string())?;
    let (mut identified, mut merged) = (0, 0);
    for (id, path, size, mtime, sha) in rows {
        let Ok(md5) = identity::hash_audio(Path::new(&path)) else { continue };
        let holder: Option<(i64, String)> = tx
            .query_row("SELECT file_id, path FROM files WHERE audio_md5 = ?1", params![md5], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .optional()
            .map_err(|e| e.to_string())?;
        match holder {
            None => {
                tx.execute(
                    "UPDATE files SET audio_md5 = ?1, md5_generator = ?2 WHERE file_id = ?3",
                    params![md5, identity::GENERATOR, id],
                )
                .map_err(|e| e.to_string())?;
                identified += 1;
            }
            Some((holder_id, held)) => {
                drop_found(&tx, id)?;
                if !Path::new(&held).is_file() {
                    tx.execute(
                        "UPDATE files SET path = ?1, size_bytes = ?2, mtime = ?3, sha256 = ?4 WHERE file_id = ?5",
                        params![path, size, mtime, sha, holder_id],
                    )
                    .map_err(|e| e.to_string())?;
                    tracing::info!("found: {path} is {held}, moved and re-tagged; rebound");
                } else {
                    tracing::info!("found: retired {path}, the same audio as {held}");
                }
                merged += 1;
            }
        }
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok((identified, merged))
}

fn scan_batch(tx: &rusqlite::Transaction, paths: &[PathBuf], rep: &mut FoundReport) -> Result<(), String> {
    let now = now_iso(tx)?;
    for path in paths {
        let p = path.to_string_lossy().to_string();
        let known: bool = tx
            .query_row("SELECT 1 FROM files WHERE path = ?1", params![p], |_| Ok(()))
            .optional()
            .map_err(|e| e.to_string())?
            .is_some();
        if known || !path.is_file() {
            continue;
        }
        rep.looked_at += 1;
        let meta = std::fs::metadata(path).map_err(|e| format!("cannot stat {p}: {e}"))?;
        let sha = match sha256_file(path) {
            Ok(h) => h,
            Err(e) => {
                rep.unreadable.push(format!("{p}: {e}"));
                continue;
            }
        };
        let same: Option<(i64, String)> = tx
            .query_row("SELECT file_id, path FROM files WHERE sha256 = ?1", params![sha], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .optional()
            .map_err(|e| e.to_string())?;
        if let Some((file_id, held)) = same {
            if Path::new(&held).is_file() {
                rep.duplicates += 1;
            } else {
                tx.execute(
                    "UPDATE files SET path = ?1, size_bytes = ?2, mtime = ?3, last_seen = ?4 WHERE file_id = ?5",
                    params![p, meta.len() as i64, mtime(&meta), now, file_id],
                )
                .map_err(|e| e.to_string())?;
                rep.rebound += 1;
            }
            continue;
        }
        // Not these bytes -- but perhaps this audio, re-tagged `[REQ-AND-270]`.
        let md5 = match identity::hash_audio(path) {
            Ok(h) => h,
            Err(e) => {
                rep.unreadable.push(format!("{p}: {e}"));
                continue;
            }
        };
        let same_audio: Option<(i64, String)> = tx
            .query_row("SELECT file_id, path FROM files WHERE audio_md5 = ?1", params![md5], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .optional()
            .map_err(|e| e.to_string())?;
        if let Some((file_id, held)) = same_audio {
            if Path::new(&held).is_file() {
                rep.duplicates += 1;
            } else {
                // Moved, and its bytes are its own now: the row follows the
                // file, byte hash and all.
                tx.execute(
                    "UPDATE files SET path = ?1, size_bytes = ?2, mtime = ?3, last_seen = ?4, sha256 = ?5 WHERE file_id = ?6",
                    params![p, meta.len() as i64, mtime(&meta), now, sha, file_id],
                )
                .map_err(|e| e.to_string())?;
                rep.rebound += 1;
            }
            continue;
        }
        let Some(duration) = crate::tags::duration_ms(path) else {
            rep.unreadable.push(format!("{p}: not audio this player can read"));
            continue;
        };
        let format = path
            .extension()
            .map(|e| e.to_string_lossy().to_ascii_lowercase())
            .unwrap_or_default();
        tx.execute(
            "INSERT INTO files (audio_md5, path, size_bytes, mtime, format, duration_ms, first_seen, last_seen, sha256, \
             md5_generator) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?7, ?8, ?9)",
            params![md5, p, meta.len() as i64, mtime(&meta), format, duration as i64, now, sha, identity::GENERATOR],
        )
        .map_err(|e| format!("cannot catalogue {p}: {e}"))?;
        let file_id = tx.last_insert_rowid();
        let t = crate::tags::read(path);
        // A catalogue built without the table still gets the passage: the
        // tags are what names it, but their absence does not stop it playing.
        let _ = tx.execute(
            "INSERT INTO file_tags (file_id, title, artist, album, track_no, disc_no, has_art, scanned_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 0, strftime('%s','now'))",
            params![file_id, t.title, t.artist, t.album, t.track_no, t.disc_no],
        );
        tx.execute(
            "INSERT INTO passages (file_id, kind, start_ms, end_ms, boundary_src) VALUES (?1, 'radio', 0, ?2, ?3)",
            params![file_id, duration as i64, TAGS_ONLY],
        )
        .map_err(|e| format!("cannot make a passage for {p}: {e}"))?;
        rep.tags_only += 1;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn library() -> Connection {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE,
                 path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
                 format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
                 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
             CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, artist TEXT,
                 album TEXT, track_no INTEGER, disc_no INTEGER,
                 has_art INTEGER NOT NULL DEFAULT 0, scanned_at INTEGER NOT NULL);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                 kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                 lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
                 boundary_src TEXT NOT NULL, fade_in_ms INTEGER NOT NULL DEFAULT 20,
                 fade_out_ms INTEGER NOT NULL DEFAULT 20,
                 fade_in_curve TEXT NOT NULL DEFAULT 'exponential',
                 fade_out_curve TEXT NOT NULL DEFAULT 'exponential');",
        )
        .unwrap();
        c
    }

    /// Half a second of 8 kHz mono PCM, in a WAV the container reader opens.
    fn wav(path: &Path, fill: i16) {
        let (rate, n): (u32, u32) = (8000, 4000);
        let mut b = Vec::new();
        b.extend_from_slice(b"RIFF");
        b.extend_from_slice(&(36 + n * 2).to_le_bytes());
        b.extend_from_slice(b"WAVEfmt ");
        b.extend_from_slice(&16u32.to_le_bytes());
        b.extend_from_slice(&1u16.to_le_bytes());
        b.extend_from_slice(&1u16.to_le_bytes());
        b.extend_from_slice(&rate.to_le_bytes());
        b.extend_from_slice(&(rate * 2).to_le_bytes());
        b.extend_from_slice(&2u16.to_le_bytes());
        b.extend_from_slice(&16u16.to_le_bytes());
        b.extend_from_slice(b"data");
        b.extend_from_slice(&(n * 2).to_le_bytes());
        for i in 0..n {
            b.extend_from_slice(&(fill.wrapping_add(i as i16)).to_le_bytes());
        }
        std::fs::write(path, b).unwrap();
    }

    /// A folder named `Music`, since only a `Music` folder is scanned.
    fn dir(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lempi-found-{name}-{}", std::process::id())).join("Music");
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// `[SPEC-SC-041]`, since 2026-09-27: keyed by its signature, the phone
    /// computing it as every other host does `[REQ-AND-270]`.
    #[test]
    fn a_new_file_becomes_a_tags_only_passage_keyed_by_its_signature() {
        let d = dir("new");
        let f = d.join("found.wav");
        wav(&f, 1);
        let mut c = library();
        let r = scan(&mut c, std::slice::from_ref(&f)).unwrap();
        assert_eq!(r, FoundReport { looked_at: 1, tags_only: 1, ..Default::default() });
        let sha = sha256_file(&f).unwrap();
        let (key, dur, stored): (String, i64, String) = c
            .query_row("SELECT audio_md5, duration_ms, sha256 FROM files", [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .unwrap();
        assert_eq!(key, identity::hash_audio(&f).unwrap(), "keyed by its signature");
        assert_eq!(stored, sha, "and its byte hash kept beside it");
        let generator: String = c.query_row("SELECT md5_generator FROM files", [], |r| r.get(0)).unwrap();
        assert_eq!(generator, identity::GENERATOR, "recording what computed it");
        assert_eq!(dur, 500, "its length read from the container");
        let (src, end): (String, i64) =
            c.query_row("SELECT boundary_src, end_ms FROM passages", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
        assert_eq!((src.as_str(), end), (TAGS_ONLY, 500), "one whole-file passage, marked tags-only");
        let again = scan(&mut c, &[f]).unwrap();
        assert_eq!(again, FoundReport::default(), "a second scan finds nothing new");
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn a_moved_file_is_rebound_and_a_copy_is_left_alone() {
        let d = dir("moved");
        let (moved, held, copy) = (d.join("moved.wav"), d.join("held.wav"), d.join("copy.wav"));
        wav(&moved, 7);
        wav(&held, 9);
        std::fs::copy(&held, &copy).unwrap();
        let mut c = library();
        crate::db::ensure_sha256_column(&c);
        for (md5, path, f) in [("realmd5a", d.join("gone/moved.wav"), &moved), ("realmd5b", held.clone(), &held)] {
            c.execute(
                "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,sha256) \
                 VALUES (?1,?2,1,1.0,'wav',500,'t','t',?3)",
                params![md5, path.to_string_lossy(), sha256_file(f).unwrap()],
            )
            .unwrap();
        }
        let r = scan(&mut c, &[moved.clone(), copy]).unwrap();
        assert_eq!(r, FoundReport { looked_at: 2, rebound: 1, duplicates: 1, ..Default::default() });
        let p: String = c.query_row("SELECT path FROM files WHERE audio_md5='realmd5a'", [], |r| r.get(0)).unwrap();
        assert_eq!(PathBuf::from(p), moved, "the catalogued file is bound where it was found");
        let n: i64 = c.query_row("SELECT count(*) FROM files", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 2, "a second copy is not catalogued");
        std::fs::remove_dir_all(&d).ok();
    }

    /// A named file with no byte hash is hashed, and a tags-only second
    /// copy of it -- catalogued before its hash was known -- is retired.
    #[test]
    fn a_tags_only_copy_of_a_named_file_is_retired_once_it_is_hashed() {
        let d = dir("copies");
        let (named, copy) = (d.join("named.wav"), d.join("copy.wav"));
        wav(&named, 11);
        std::fs::copy(&named, &copy).unwrap();
        let mut c = library();
        crate::db::ensure_sha256_column(&c);
        c.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen) \
             VALUES ('realmd5', ?1, 1, 1.0, 'wav', 500, 't', 't')",
            params![named.to_string_lossy()],
        )
        .unwrap();
        let sha = sha256_file(&copy).unwrap();
        c.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,sha256) \
             VALUES (?1, ?2, 1, 1.0, 'wav', 500, 't', 't', ?3)",
            params![found_key(&sha), copy.to_string_lossy(), sha],
        )
        .unwrap();
        c.execute("INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) VALUES (2,'radio',0,500,'found:tags')", [])
            .unwrap();
        let r = scan(&mut c, &[named.clone(), copy]).unwrap();
        assert_eq!((r.hashed, r.retired), (1, 1), "{r:?}");
        let left: Vec<String> = c
            .prepare("SELECT audio_md5 FROM files")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(left, vec!["realmd5"], "the named file is kept, its copy retired");
        let passages: i64 = c.query_row("SELECT count(*) FROM passages", [], |r| r.get(0)).unwrap();
        assert_eq!(passages, 0, "and the copy's passage with it");
        std::fs::remove_dir_all(&d).ok();
    }

    /// A deleted file's tags-only entry is retired while its folder is there,
    /// and kept while the whole folder is gone, as with an SD card taken out.
    #[test]
    fn a_deleted_file_is_retired_but_not_one_on_a_missing_card() {
        let d = dir("deleted");
        let kept = d.join("kept.wav");
        wav(&kept, 13);
        let mut c = library();
        crate::db::ensure_sha256_column(&c);
        for (i, path) in [d.join("deleted.wav"), d.join("card-out").join("song.wav")].iter().enumerate() {
            c.execute(
                "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,sha256) \
                 VALUES (?1, ?2, 1, 1.0, 'wav', 500, 't', 't', ?1)",
                params![format!("sha256:{i}"), path.to_string_lossy()],
            )
            .unwrap();
            c.execute(
                "INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) VALUES (?1,'radio',0,500,'found:tags')",
                params![c.last_insert_rowid()],
            )
            .unwrap();
        }
        let r = scan(&mut c, &[kept]).unwrap();
        assert_eq!(r.retired, 1, "{r:?}");
        let left: Vec<String> = c
            .prepare("SELECT path FROM files WHERE audio_md5 LIKE 'sha256:_'")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(left.len(), 1, "{left:?}");
        assert!(left[0].contains("card-out"), "the entry on a missing folder is kept: {left:?}");
        std::fs::remove_dir_all(&d).ok();
    }

    /// The same audio in bytes of its own -- a re-tagged copy -- is that
    /// file: a second copy while the original is in place, and the original
    /// moved once it is gone, taking the new bytes' hash with it.
    #[test]
    fn a_re_tagged_copy_is_recognised_by_its_signature() {
        let d = dir("retagged");
        let (orig, copy) = (d.join("orig.wav"), d.join("copy.wav"));
        wav(&orig, 21);
        // The same PCM, a different file: a trailing chunk a WAV reader
        // skips, as a tag editor might leave.
        let mut bytes = std::fs::read(&orig).unwrap();
        bytes.extend_from_slice(b"LIST\x04\x00\x00\x00tag!");
        std::fs::write(&copy, &bytes).unwrap();
        assert_ne!(sha256_file(&orig).unwrap(), sha256_file(&copy).unwrap());
        assert_eq!(identity::hash_audio(&orig).unwrap(), identity::hash_audio(&copy).unwrap());

        let mut c = library();
        scan(&mut c, std::slice::from_ref(&orig)).unwrap();
        // Named by Vipunen since: no longer tags-only. (A tags-only original
        // that is deleted is retired as such, and its copy found anew.)
        c.execute("UPDATE passages SET boundary_src = 'ingest'", []).unwrap();
        let r = scan(&mut c, std::slice::from_ref(&copy)).unwrap();
        assert_eq!((r.duplicates, r.tags_only), (1, 0), "a second copy, not a second song: {r:?}");

        std::fs::remove_file(&orig).unwrap();
        let r = scan(&mut c, std::slice::from_ref(&copy)).unwrap();
        assert_eq!(r.rebound, 1, "{r:?}");
        let (path, sha): (String, String) =
            c.query_row("SELECT path, sha256 FROM files", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
        assert_eq!((path, sha), (copy.to_string_lossy().to_string(), sha256_file(&copy).unwrap()));
        std::fs::remove_dir_all(&d).ok();
    }

    /// A tags-only file keyed the old way is identified by its signature; one
    /// whose audio another row already holds is retired as a second copy.
    #[test]
    fn a_file_keyed_by_its_bytes_is_identified_by_its_signature() {
        let d = dir("legacy");
        let (a, b, held) = (d.join("a.wav"), d.join("b.wav"), d.join("held.wav"));
        wav(&a, 31);
        wav(&b, 32);
        std::fs::copy(&b, &held).unwrap();
        let mut c = library();
        crate::db::ensure_sha256_column(&c);
        crate::db::ensure_md5_generator_column(&c);
        c.execute(
            "INSERT INTO files (file_id,audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen) \
             VALUES (9,?1,?2,1,1.0,'wav',500,'t','t')",
            params![identity::hash_audio(&b).unwrap(), held.to_string_lossy()],
        )
        .unwrap();
        for (id, f) in [(1, &a), (2, &b)] {
            c.execute(
                "INSERT INTO files (file_id,audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,sha256) \
                 VALUES (?1,?2,?3,1,1.0,'wav',500,'t','t',?4)",
                params![id, found_key(&sha256_file(f).unwrap()), f.to_string_lossy(), sha256_file(f).unwrap()],
            )
            .unwrap();
            c.execute(
                "INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) VALUES (?1,'radio',0,500,'found:tags')",
                params![id],
            )
            .unwrap();
        }
        let r = scan(&mut c, &[]).unwrap();
        assert_eq!((r.identified, r.retired), (1, 1), "{r:?}");
        let keys: Vec<(i64, String)> = c
            .prepare("SELECT file_id, audio_md5 FROM files ORDER BY file_id")
            .unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(keys, vec![(1, identity::hash_audio(&a).unwrap()), (9, identity::hash_audio(&b).unwrap())]);
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn a_file_that_is_not_audio_is_reported_not_catalogued() {
        let d = dir("junk");
        let f = d.join("notes.mp3");
        std::fs::write(&f, b"not audio at all").unwrap();
        let mut c = library();
        let r = scan(&mut c, &[f]).unwrap();
        assert_eq!(r.unreadable.len(), 1, "{r:?}");
        let n: i64 = c.query_row("SELECT count(*) FROM files", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 0);
        std::fs::remove_dir_all(&d).ok();
    }

    /// `[REQ-AND-220]`: only what is in a `Music` folder is the phone's music.
    /// Something else MediaStore calls music is not catalogued, and a
    /// tags-only entry left outside by an earlier scan is retired.
    #[test]
    fn only_a_music_folder_is_scanned_and_strays_are_retired() {
        assert!(in_music_folder("/storage/3865-6430/Music/Fluke/Out.mp3"));
        assert!(in_music_folder("/storage/emulated/0/Music/Lempi/a.mp3"));
        assert!(!in_music_folder("/storage/emulated/0/Download/voicemail.mp3"));
        assert!(!in_music_folder("/storage/emulated/0/Musical/a.mp3"), "a whole folder name, not a prefix");

        // Beside `Music`, not in it: the helper's folder is `Music` itself.
        let root = dir("scope").parent().unwrap().to_path_buf();
        let (music, download) = (root.join("Music"), root.join("Download"));
        std::fs::create_dir_all(&music).unwrap();
        std::fs::create_dir_all(&download).unwrap();
        let (song, voicemail) = (music.join("song.wav"), download.join("voicemail.wav"));
        wav(&song, 3);
        wav(&voicemail, 5);
        let mut c = library();
        crate::db::ensure_sha256_column(&c);
        // What an earlier, unscoped scan left behind.
        c.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,sha256) \
             VALUES ('sha256:old', ?1, 1, 1.0, 'wav', 500, 't', 't', 'old')",
            params![voicemail.to_string_lossy()],
        )
        .unwrap();
        c.execute("INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) VALUES (1,'radio',0,500,'found:tags')", [])
            .unwrap();
        let r = scan(&mut c, &[song, voicemail]).unwrap();
        assert_eq!(r.retired, 1, "{r:?}");
        assert_eq!(r.tags_only, 1, "only the file in Music is catalogued: {r:?}");
        let paths: Vec<String> = c
            .prepare("SELECT path FROM files")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert!(paths.iter().all(|p| in_music_folder(p)), "{paths:?}");
        let passages: i64 = c.query_row("SELECT count(*) FROM passages", [], |r| r.get(0)).unwrap();
        assert_eq!(passages, 1, "the stray's passage went with it");
        std::fs::remove_dir_all(&root).ok();
    }
}
