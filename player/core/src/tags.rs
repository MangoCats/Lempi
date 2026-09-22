//! What the audio file says about itself -- the part the library stores.
//!
//! **This is half of a module that used to be one file**, and the seam is
//! `symphonia`. `Tags` and `Artwork` are what `db::Library` writes and reads
//! and what `queue::QueueEntry` carries; *reading* them out of a container is
//! decoding, and decoding lives in `lempi_player::tags` with the rest of the
//! audio path. Keeping the types here is what lets a crate whose business is
//! selection stay clear of a media framework `[GDE-AND-020]`.
//!
//! The original module's reasoning still applies to both halves: MusicBrainz is
//! the preferred source for what a passage is called `[REQ-VIS-170]`, but album
//! names live at the Release level and Lempi's `releases` table is empty until
//! Vipunen fills it, and cover art is not in the database at all. Both are
//! usually sitting in the file's own tags, so this is the fallback -- read from
//! the file rather than fetched, because playback must not depend on a live
//! external service `[REQ-NEG-100]`.

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
///
/// `pub(crate)` no longer reaches the reader that calls it, since the reader is
/// in the other crate now -- so this is `pub` and tested here, beside the type
/// whose field it fills.
pub fn number(v: &str) -> Option<u32> {
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

/// Anything smaller than this is not a picture `[REQ-VIS-170]`.
///
/// MuLibPlay applies the same floor to its stored covers, and for the same
/// reason: a truncated download or a placeholder byte or two would otherwise
/// render as a broken image, which looks like a fault in the player rather
/// than a gap in the data.
pub const MIN_ART_BYTES: usize = 256;

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
}
