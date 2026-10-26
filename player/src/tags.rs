//! What the audio file says about itself.
//!
//! MusicBrainz is the preferred source for what a passage is called
//! `[REQ-VIS-170]`, but it does not know everything the library needs. Album
//! names live at the Release level and Lempi's `releases` table is empty until
//! Vipunen fills it; cover art is not in the database at all. Both are, however,
//! usually sitting in the file's own tags.
//!
//! So this is the fallback, and only the fallback. It is read from the file
//! rather than fetched, because playback must not depend on a live external
//! service `[REQ-NEG-100]` -- the Cover Art Archive is exactly the kind of
//! dependency that requirement exists to forbid.
//!
//! **This module is the symphonia half.** The types it produces --- `Tags`,
//! `Artwork`, `MIN_ART_BYTES` --- live in [`lempi_core::tags`], because
//! `db::Library` and `queue::QueueEntry` hold them and neither has any business
//! pulling a media framework into its build. They are re-exported below under
//! their original names, so `crate::tags::Tags` reads the same here as it did
//! when this was one file.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::path::Path;

use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::{MetadataOptions, StandardTagKey, StandardVisualKey};
use symphonia::core::probe::Hint;

pub use lempi_core::tags::{number, Artwork, Tags, MIN_ART_BYTES};

fn open(path: &Path) -> Option<symphonia::core::probe::ProbeResult> {
    let file = std::fs::File::open(path).ok()?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    let mut hint = Hint::new();
    if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
        hint.with_extension(ext);
    }
    symphonia::default::get_probe()
        .format(&hint, mss, &FormatOptions::default(), &MetadataOptions::default())
        .ok()
}

/// Read title, artist and album. Never fails: an unreadable or untagged file
/// yields empty tags, because missing metadata must not stop a passage playing.
pub fn read(path: &Path) -> Tags {
    let mut tags = Tags::default();
    let Some(mut probed) = open(path) else { return tags };

    // Two places to look, and both matter. ID3v2 on an MP3 is found by the
    // probe before the format reader exists; Vorbis comments in a FLAC belong
    // to the format itself. Taking only one silently loses half the library.
    let mut take = |rev: &symphonia::core::meta::MetadataRevision| {
        for tag in rev.tags() {
            match tag.std_key {
                Some(StandardTagKey::TrackNumber) => {
                    if tags.track_no.is_none() {
                        tags.track_no = number(&tag.value.to_string());
                    }
                    continue;
                }
                Some(StandardTagKey::DiscNumber) => {
                    if tags.disc_no.is_none() {
                        tags.disc_no = number(&tag.value.to_string());
                    }
                    continue;
                }
                _ => {}
            }
            let slot = match tag.std_key {
                Some(StandardTagKey::TrackTitle) => &mut tags.title,
                Some(StandardTagKey::Artist) => &mut tags.artist,
                Some(StandardTagKey::Album) => &mut tags.album,
                _ => continue,
            };
            if slot.is_none() {
                let v = tag.value.to_string().trim().to_string();
                if !v.is_empty() {
                    *slot = Some(v);
                }
            }
        }
    };

    if let Some(rev) = probed.format.metadata().skip_to_latest() {
        take(rev);
    }
    if let Some(rev) = probed.metadata.get().as_ref().and_then(|m| m.current()) {
        take(rev);
    }
    tags
}

/// How long the file is, in ms, from the container -- read, not decoded
/// `[REQ-AND-270]`. The header's frame count where it states one; otherwise
/// the packets' own durations summed, which walks the file without decoding
/// a sample (an MP3 with no Xing header states nothing). `None` for a file
/// that is not audio this build can open.
///
/// A header that states **0** frames is not believed: on the Moto G,
/// 2026-09-26, Fiona Apple's "The First Taste" said 0 and held 10,893
/// packets. And the track is chosen as the decoder chooses it, the first with
/// a real codec, so what is measured is what plays.
pub fn duration_ms(path: &Path) -> Option<u64> {
    let mut probed = open(path)?;
    let track = probed
        .format
        .tracks()
        .iter()
        .find(|t| t.codec_params.codec != symphonia::core::codecs::CODEC_TYPE_NULL)?
        .clone();
    let rate = u64::from(track.codec_params.sample_rate?);
    let frames = match track.codec_params.n_frames {
        Some(n) if n > 0 => n,
        _ => {
            let mut sum = 0u64;
            while let Ok(p) = probed.format.next_packet() {
                if p.track_id() == track.id {
                    sum += p.dur;
                }
            }
            sum
        }
    };
    (frames > 0).then(|| frames * 1000 / rate)
}

/// The embedded cover, if there is one.
///
/// Front cover by preference; failing that, whatever picture is there. A file
/// carrying a back cover and a band photo and nothing else should still show
/// something rather than nothing.
pub fn artwork(path: &Path) -> Option<Artwork> {
    let mut probed = open(path)?;

    let pick = |rev: &symphonia::core::meta::MetadataRevision| -> Option<Artwork> {
        let visuals = rev.visuals();
        let front = visuals
            .iter()
            .find(|v| v.usage == Some(StandardVisualKey::FrontCover));
        let chosen = front.or_else(|| visuals.first())?;
        Some(Artwork {
            media_type: chosen.media_type.clone(),
            data: chosen.data.to_vec(),
        })
    };

    if let Some(rev) = probed.format.metadata().skip_to_latest() {
        if let Some(a) = pick(rev) {
            return Some(a);
        }
    }
    probed.metadata.get().as_ref().and_then(|m| m.current()).and_then(pick)
}

/// How many of the catalogue's files have no tag row: no album name, no place
/// in the browse index `[REQ-VIS-180]`. Read-only, so it can be asked of any
/// catalogue, a read-only one included -- the sync snapshot carries it, and
/// `backfill` asks it before deciding whether to open anything. The same
/// predicate as `Library::files_without_tags`; a catalogue with no tag table
/// yet counts every file.
pub fn untagged(db: &std::path::Path) -> Result<i64, String> {
    use rusqlite::{Connection, OpenFlags};
    let e = |e: rusqlite::Error| e.to_string();
    let c = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY).map_err(e)?;
    c.busy_timeout(crate::scrub::BUSY).map_err(e)?;
    let has_table = c
        .query_row("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'file_tags'", [], |_| Ok(()))
        .is_ok();
    let sql = if has_table {
        "SELECT COUNT(*) FROM files f WHERE NOT EXISTS (SELECT 1 FROM file_tags t WHERE t.file_id = f.file_id)"
    } else {
        "SELECT COUNT(*) FROM files"
    };
    c.query_row(sql, [], |r| r.get(0)).map_err(e)
}

/// Read tags for every file that has none yet, and store them
/// `[REQ-VIS-180]`.
///
/// Shared by `tagscan`, by the player's own startup scan, by `import_bundle`
/// after an import and by the star sync's commit, so there is one definition
/// of what scanning means. Incremental by construction: it asks for the files
/// without a tag row, so a second run costs only what was added -- and a file
/// that could not be stored is asked for again next time.
///
/// **Where the catalogue is kept read-only** -- `bose`'s partition -- it opens
/// the catalogue's window to write, as the sync's commit does, and closes it
/// again `[SPEC-STAR-104]`. Until 2026-10-26 it simply tried: 340 files had
/// arrived there by import with no tags, every one failing "attempt to write
/// a readonly database" at every start, under a warning that said they would
/// not be retried and to run a tool that would have failed the same way.
///
/// Never fatal. A library that cannot be written still plays; it simply cannot
/// browse those files by album, and says so, and what will fill them.
pub fn backfill(db: &std::path::Path, announce: bool) -> Result<(usize, usize), String> {
    // Counted before anything is opened for writing: nothing missing, nothing
    // to open.
    if untagged(db).map(|n| n == 0).unwrap_or(false) {
        return Ok((0, 0));
    }
    let window = crate::catalogue_window::Window::open(db, true);
    let result = match &window {
        Ok(_) if crate::catalogue_window::writable(db) => backfill_open(db, announce),
        Ok(_) => Err("the catalogue is read-only here, and no root helper opens it".to_string()),
        Err(e) => Err(format!("the catalogue is read-only here, and could not be opened for writing: {e}")),
    };
    if let Ok(w) = window {
        if let Some(warning) = w.close(db) {
            tracing::warn!("tag scan: {warning}");
        }
    }
    if let Err(why) = &result {
        let n = untagged(db).map(|n| n.to_string()).unwrap_or_else(|_| "some".into());
        tracing::warn!(
            "{n} file(s) have no tags, so no album name or place in the browse index: {why}; \
             they are filled the next time the catalogue is written here -- an import, a sync, or a start \
             that can open it [REQ-VIS-180]"
        );
    }
    result
}

fn backfill_open(db: &std::path::Path, announce: bool) -> Result<(usize, usize), String> {
    let lib = crate::db::Library::open_writable(db).map_err(|e| e.to_string())?;
    lib.ensure_tag_table().map_err(|e| e.to_string())?;
    let files = lib.files_without_tags().map_err(|e| e.to_string())?;
    if files.is_empty() {
        return Ok((0, 0));
    }
    if announce {
        tracing::info!("scanning tags for {} file(s) in the background", files.len());
    }
    let started = std::time::Instant::now();
    let mut art = 0usize;
    let mut unstored = 0usize;
    for (i, (file_id, path)) in files.iter().enumerate() {
        let t = read(path);
        let has_art = artwork(path).is_some();
        if has_art {
            art += 1;
        }
        if let Err(e) = lib.put_tags(*file_id, &t, has_art) {
            // The scan is incremental by "has no row yet", so a row that fails
            // to store is asked for again by the next run. Counted and reported
            // rather than logged once and forgotten `[REQ-VIS-180]`.
            unstored += 1;
            if unstored <= 5 {
                tracing::error!("store tags for {file_id}: {e}");
            }
        }
        // A silent minute looks like a hang `[REQ-VIS-140]`.
        if announce && (i % 500 == 499 || i + 1 == files.len()) {
            tracing::info!(
                "  tags: {}/{} files ({:.0}s)",
                i + 1,
                files.len(),
                started.elapsed().as_secs_f32()
            );
        }
    }
    if announce {
        tracing::info!(
            "tag scan complete: {} files, {art} with cover art, {:.1}s",
            files.len(),
            started.elapsed().as_secs_f32()
        );
    }
    if unstored > 0 {
        tracing::warn!(
            "WARNING: the tags of {unstored} file(s) could not be stored; they are read again at the next scan \
             [REQ-VIS-180]"
        );
    }
    Ok((files.len(), art))
}

#[cfg(test)]
mod tests {
    use super::*;

    // `track_numbers_survive_the_forms_tags_actually_use` moved with `number`
    // itself, to `lempi_core::tags` -- the test belongs beside the code, and
    // this module now only re-exports it.

    /// `[REQ-VIS-180]`, `[SPEC-STAR-104]`: on a catalogue kept read-only, the
    /// scan opens the catalogue's window, fills what has no tags, and closes it
    /// again; with nothing missing it opens nothing. In a test the "partition"
    /// is the file's own permission bit, as for the sync's commit.
    #[test]
    fn a_read_only_catalogue_is_opened_filled_and_closed() {
        use crate::catalogue_window::WINDOW_CALLS;
        let d = std::env::temp_dir().join(format!("lempi-tags-window-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let db = d.join("library.db");
        let c = rusqlite::Connection::open(&db).unwrap();
        c.execute_batch(
            "PRAGMA journal_mode=DELETE;
             CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE, path TEXT);
             INSERT INTO files VALUES (1, 'a', 'no-such-a.mp3'), (2, 'b', 'no-such-b.mp3');",
        )
        .unwrap();
        drop(c);
        let mut perm = std::fs::metadata(&db).unwrap().permissions();
        perm.set_readonly(true);
        std::fs::set_permissions(&db, perm).unwrap();
        WINDOW_CALLS.with(|w| w.borrow_mut().clear());

        assert_eq!(untagged(&db), Ok(2), "a catalogue with no tag table counts every file");
        let got = backfill(&db, false);
        assert_eq!(got, Ok((2, 0)), "both files read");
        assert_eq!(untagged(&db), Ok(0), "and stored");
        assert_eq!(WINDOW_CALLS.with(|w| w.borrow().clone()), vec!["on", "off"], "opened, and closed again");
        assert!(std::fs::metadata(&db).unwrap().permissions().readonly(), "left read-only");

        WINDOW_CALLS.with(|w| w.borrow_mut().clear());
        assert_eq!(backfill(&db, false), Ok((0, 0)));
        assert!(WINDOW_CALLS.with(|w| w.borrow().is_empty()), "nothing missing, nothing opened");

        let mut perm = std::fs::metadata(&db).unwrap().permissions();
        #[allow(clippy::permissions_set_readonly_false)]
        perm.set_readonly(false);
        std::fs::set_permissions(&db, perm).unwrap();
        let _ = std::fs::remove_dir_all(&d);
    }

    /// Missing metadata must never be an error: a passage with no tags plays
    /// exactly as well as one with them.
    #[test]
    fn an_unreadable_file_yields_empty_tags_rather_than_failing() {
        let t = read(Path::new("no-such-file.mp3"));
        assert!(t.is_empty());
        assert!(artwork(Path::new("no-such-file.mp3")).is_none());
    }
}
