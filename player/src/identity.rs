//! The identity hash, `audio_md5`, computed in-process `[SPEC-RLK-150]`.
//!
//! **What it is.** The MD5 of the encoded packets Symphonia's demuxer yields
//! for a file's first track with a real codec, in order. Nothing is decoded:
//! the compressed frames are hashed as they sit in the file, so tags, cover art
//! and the container around them are not part of it, and neither is any
//! arithmetic that could differ between machines `[SPEC-RLK-086]`.
//!
//! **Why this and not ffmpeg.** Not merit -- the two readers disagree only
//! about trailing bytes no listener hears `[SPEC-RLK-085]` -- but ownership.
//! ffmpeg's reading is defined by an external package a routine upgrade can
//! change underneath an appliance; this one is compiled in, so the definition
//! of the key other tables are keyed on ships with the binary and can move
//! only through a deliberate build. That is why `symphonia` is pinned exactly
//! in `Cargo.toml`, and why a test below refuses a lock file that disagrees
//! with [`GENERATOR`].
//!
//! **How close the two are, measured 2026-09-26** over the 5,709 library
//! files: 5,648 identical to the incumbent ffmpeg values, 60 different (all
//! MP3, all at the tail), 1 Symphonia cannot open. 214 s for the whole library
//! on one thread from disk, 18 ms a file warm, against ffmpeg's 80 ms.
//!
//! Here rather than in `lempi-core` because it needs Symphonia, which that
//! crate may not reach `[GDE-AND-045]`. `lempi-core` takes the hasher as an
//! argument instead, and every host passes this one.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::path::Path;

use symphonia::core::codecs::CODEC_TYPE_NULL;
use symphonia::core::errors::Error;
use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;

/// What [`hash_audio`] records in `files.md5_generator` `[SPEC-SC-038]`:
/// `name@version`, the exact Symphonia release the definition is.
pub const GENERATOR: &str = "symphonia@0.5.5";

/// [`hash_audio`] and its name, as `lempi-core` takes them.
pub const HASHER: lempi_core::relink::Hasher = lempi_core::relink::Hasher { hash: hash_audio, generator: GENERATOR };

/// The file's `audio_md5`, lower-case hex.
///
/// Probed exactly as the player probes a file it is about to play
/// ([`crate::decoder`]): the extension as a hint, default options, the first
/// track whose codec is known. A file this cannot hash is one the player
/// cannot play either.
pub fn hash_audio(path: &Path) -> Result<String, String> {
    let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    let mut hint = Hint::new();
    if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
        hint.with_extension(ext);
    }
    let probed = symphonia::default::get_probe()
        .format(&hint, mss, &FormatOptions::default(), &MetadataOptions::default())
        .map_err(|e| format!("cannot open: {e}"))?;
    let mut format = probed.format;
    let track = format
        .tracks()
        .iter()
        .find(|t| t.codec_params.codec != CODEC_TYPE_NULL)
        .ok_or("no audio track")?
        .id;
    let mut md5 = md5::Context::new();
    let mut packets = 0u64;
    loop {
        match format.next_packet() {
            Ok(p) if p.track_id() == track => {
                md5.consume(p.buf());
                packets += 1;
            }
            Ok(_) => {}
            // The end of the stream. Symphonia reports it as an unexpected
            // EOF, and that is also where it stops at a truncated last frame:
            // the tail ffmpeg includes and this does not `[SPEC-RLK-085]`.
            Err(Error::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(Error::ResetRequired) => break,
            Err(e) => return Err(format!("reading packet {packets}: {e}")),
        }
    }
    if packets == 0 {
        // An MD5 of nothing is a real hex string, and every empty file would
        // share it. Refused rather than returned as an identity.
        return Err("no audio packets".into());
    }
    Ok(format!("{:x}", md5.compute()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn dir(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lempi-identity-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// Frames of MPEG-1 Layer III, 128 kb/s, 44.1 kHz: a valid header and a
    /// body that is `fill`. Nothing here is decoded, so the body need not be
    /// music -- only the framing is read.
    fn mp3_frames(n: usize, fill: u8) -> Vec<u8> {
        let mut out = Vec::new();
        for _ in 0..n {
            out.extend_from_slice(&[0xFF, 0xFB, 0x90, 0x00]);
            out.extend(std::iter::repeat_n(fill, 417 - 4));
        }
        out
    }

    /// An ID3v2.3 tag holding one title frame of `len` bytes.
    fn id3(len: usize) -> Vec<u8> {
        let mut frame = b"TIT2".to_vec();
        frame.extend_from_slice(&((len + 1) as u32).to_be_bytes());
        frame.extend_from_slice(&[0, 0, 0]);
        frame.extend(std::iter::repeat_n(b'x', len));
        let n = frame.len();
        let mut tag = b"ID3\x03\x00\x00".to_vec();
        tag.extend_from_slice(&[(n >> 21) as u8 & 0x7F, (n >> 14) as u8 & 0x7F, (n >> 7) as u8 & 0x7F, n as u8 & 0x7F]);
        tag.extend(frame);
        tag
    }

    fn write(path: &Path, parts: &[&[u8]]) {
        std::fs::write(path, parts.concat()).unwrap();
    }

    /// The definition, stated as a test: an MP3's identity is the MD5 of its
    /// frames, whatever tag is in front of them and whatever partial frame is
    /// left after them. The second is exactly where ffmpeg's reading differs
    /// `[SPEC-RLK-085]`.
    #[test]
    fn an_mp3_is_its_frames_whatever_tag_or_tail_surrounds_them() {
        let d = dir("mp3");
        let frames = mp3_frames(20, 0x55);
        let bare = d.join("bare.mp3");
        let tagged = d.join("tagged.mp3");
        let tailed = d.join("tailed.mp3");
        write(&bare, &[&frames]);
        write(&tagged, &[&id3(50_000), &frames]);
        write(&tailed, &[&frames, &[0xFF, 0xFB, 0x90, 0x00, 1, 2, 3]]);
        let want = format!("{:x}", md5::compute(&frames));
        assert_eq!(hash_audio(&bare).unwrap(), want, "the frames, and nothing else");
        assert_eq!(hash_audio(&tagged).unwrap(), want, "a tag is not the audio");
        assert_eq!(hash_audio(&tailed).unwrap(), want, "a truncated last frame is not the audio");
        std::fs::remove_dir_all(&d).ok();
    }

    /// Different audio is a different identity. Without this the test above
    /// would pass for a hasher that returned a constant.
    #[test]
    fn different_frames_are_a_different_identity() {
        let d = dir("differ");
        let (a, b) = (d.join("a.mp3"), d.join("b.mp3"));
        write(&a, &[&mp3_frames(20, 0x55)]);
        write(&b, &[&mp3_frames(20, 0x56)]);
        assert_ne!(hash_audio(&a).unwrap(), hash_audio(&b).unwrap());
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn a_file_with_no_audio_has_no_identity() {
        let d = dir("empty");
        let (empty, text) = (d.join("empty.mp3"), d.join("notes.mp3"));
        write(&empty, &[]);
        write(&text, &[b"not audio at all"]);
        assert!(hash_audio(&empty).is_err());
        assert!(hash_audio(&text).is_err());
        assert!(hash_audio(&d.join("absent.mp3")).is_err());
        std::fs::remove_dir_all(&d).ok();
    }

    /// **The definition moves only by a deliberate edit.** `Cargo.toml` pins
    /// `symphonia` exactly; this refuses a lock file that resolved anything
    /// else, so an upgrade has to change [`GENERATOR`] too -- and whoever does
    /// that is reading this, and `[SPEC-RLK-086]`, first.
    #[test]
    fn the_generator_names_the_symphonia_the_lock_file_holds() {
        let lock = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.lock"))
            .unwrap()
            .replace("\r\n", "\n");
        let at = lock.find("name = \"symphonia\"\nversion = \"").expect("symphonia in Cargo.lock");
        let rest = &lock[at + "name = \"symphonia\"\nversion = \"".len()..];
        let version = &rest[..rest.find('"').unwrap()];
        assert_eq!(GENERATOR, format!("symphonia@{version}"));
    }
}
