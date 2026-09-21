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

use std::path::Path;

use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::{MetadataOptions, StandardTagKey, StandardVisualKey};
use symphonia::core::probe::Hint;

#[derive(Debug, Default, Clone, PartialEq)]
pub struct Tags {
    pub title: Option<String>,
    pub artist: Option<String>,
    pub album: Option<String>,
    /// Position on the record `[REQ-VIS-190]`. MusicBrainz keeps this on the
    /// Release, in `release_recordings.position`; with those tables empty, the
    /// file's own TRACKNUMBER is the only thing that knows an album's order.
    pub track_no: Option<u32>,
    /// Which disc, for sets. Sorted before the track number, so disc two's
    /// opener does not land second.
    pub disc_no: Option<u32>,
}

/// "7", "07", "7/12" and " 7 " all mean seven.
///
/// The `n/total` form is what ID3 writes and what a naive parse chokes on,
/// which would silently sort a whole album alphabetically instead.
fn number(v: &str) -> Option<u32> {
    v.trim()
        .split(['/', '-'])
        .next()?
        .trim()
        .parse::<u32>()
        .ok()
        .filter(|n| *n > 0)
}

impl Tags {
    pub fn is_empty(&self) -> bool {
        self.title.is_none() && self.artist.is_none() && self.album.is_none()
    }
}

/// Embedded cover art: its media type and its bytes.
pub struct Artwork {
    pub media_type: String,
    pub data: Vec<u8>,
}

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

/// Anything smaller than this is not a picture `[REQ-VIS-170]`.
///
/// MuLibPlay applies the same floor to its stored covers, and for the same
/// reason: a truncated download or a placeholder byte or two would otherwise
/// render as a broken image, which looks like a fault in the player rather
/// than a gap in the data.
pub const MIN_ART_BYTES: usize = 256;

/// Cover files sitting beside the audio, in the order worth trying.
///
/// Measured on this library: 1,656 of the 1,986 files with no embedded picture
/// -- 83% -- have one of these in the same folder. The art was already on disk
/// and nothing was looking for it.
const SIBLING_FRONT: [&str; 8] = [
    "folder.jpg", "cover.jpg", "front.jpg", "album.jpg",
    "folder.png", "cover.png", "front.png", "albumart.jpg",
];
const SIBLING_BACK: [&str; 4] = ["back.jpg", "back.png", "backcover.jpg", "folder-back.jpg"];

fn media_type_for(path: &Path) -> String {
    match path.extension().and_then(|e| e.to_str()).map(str::to_ascii_lowercase).as_deref() {
        Some("png") => "image/png".into(),
        Some("gif") => "image/gif".into(),
        Some("webp") => "image/webp".into(),
        _ => "image/jpeg".into(),
    }
}

/// A cover file in the same directory as the audio, if one is there.
///
/// Case-insensitively, because `Folder.jpg` and `folder.jpg` are the same file
/// to Windows and different strings to everyone else.
pub fn sibling_art(path: &Path, back: bool) -> Option<Artwork> {
    let dir = path.parent()?;
    let wanted: &[&str] = if back { &SIBLING_BACK } else { &SIBLING_FRONT };
    let entries: Vec<_> = std::fs::read_dir(dir).ok()?.flatten().collect();
    for name in wanted {
        for e in &entries {
            if e.file_name().to_string_lossy().eq_ignore_ascii_case(name) {
                let p = e.path();
                let data = std::fs::read(&p).ok()?;
                if data.len() >= MIN_ART_BYTES {
                    return Some(Artwork { media_type: media_type_for(&p), data });
                }
            }
        }
    }
    None
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

/// Read tags for every file that has none yet, and store them
/// `[REQ-VIS-180]`.
///
/// Shared by `tagscan` and by the player's own startup scan, so there is one
/// definition of what scanning means. Incremental by construction: it asks for
/// the files without a tag row, so a second run costs only what was added.
///
/// Never fatal. A library that cannot be written -- read-only media, a locked
/// file -- still plays; it simply cannot browse by album.
pub fn backfill(db: &std::path::Path, announce: bool) -> Result<(usize, usize), String> {
    let lib = crate::db::Library::open_writable(db).map_err(|e| e.to_string())?;
    lib.ensure_tag_table().map_err(|e| e.to_string())?;
    let files = lib.files_without_tags().map_err(|e| e.to_string())?;
    if files.is_empty() {
        return Ok((0, 0));
    }
    if announce {
        println!("scanning tags for {} file(s) in the background", files.len());
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
            // to store is not retried by the next run either -- it is a
            // permanent hole. Counted and reported rather than logged once and
            // forgotten `[REQ-VIS-180]`.
            unstored += 1;
            if unstored <= 5 {
                eprintln!("store tags for {file_id}: {e}");
            }
        }
        // A silent minute looks like a hang `[REQ-VIS-140]`.
        if announce && (i % 500 == 499 || i + 1 == files.len()) {
            println!(
                "  tags: {}/{} files ({:.0}s)",
                i + 1,
                files.len(),
                started.elapsed().as_secs_f32()
            );
        }
    }
    if announce {
        println!(
            "tag scan complete: {} files, {art} with cover art, {:.1}s",
            files.len(),
            started.elapsed().as_secs_f32()
        );
    }
    if unstored > 0 {
        eprintln!(
            "WARNING: {unstored} file(s) could not be stored and will not be retried; \
             re-run `tagscan --all` to rebuild the index"
        );
    }
    Ok((files.len(), art))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn track_numbers_survive_the_forms_tags_actually_use() {
        assert_eq!(number("7"), Some(7));
        assert_eq!(number("07"), Some(7));
        assert_eq!(number(" 7 "), Some(7));
        // The form ID3 writes, and the one a naive parse drops -- which would
        // sort a whole album alphabetically instead.
        assert_eq!(number("7/12"), Some(7));
        assert_eq!(number(""), None);
        assert_eq!(number("A"), None);
        assert_eq!(number("0"), None, "zero is absence, not a position");
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
