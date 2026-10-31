//! Bounded streaming decoder for a single passage `[REQ-AUD-110]`.
//!
//! Opens a file, seeks to the passage start, and yields decoded frames a packet
//! at a time. It **never** holds more than one decoded packet plus the caller's
//! buffer, so memory is independent of passage length -- decoding minute 240 of
//! a 245-minute file costs exactly what decoding minute 1 costs. Measured by
//! [`memcheck`](bin/memcheck.rs), which fails above the RSS budget rather than
//! asserting the claim.
//!
//! Decoding a span of a larger file costs only that span `[REQ-AUD-120]`: the
//! seek is what makes a 40-track DAO rip tractable.
//!
//! **Nothing here alters the audio** `[REQ-AUD-100]`. Sample rate conversion
//! happens downstream in [`resample`](../resample.rs) and gain in the mixer;
//! what this yields is what the file holds.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::fs::File;
use std::path::Path;

use symphonia::core::audio::{AudioBufferRef, Signal};
use symphonia::core::codecs::{Decoder, DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::{FormatOptions, FormatReader, SeekMode, SeekTo};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;
use symphonia::core::units::{Time, TimeBase};

#[derive(Debug)]
pub enum DecodeError {
    Io(std::io::Error),
    Symphonia(SymError),
    NoAudioTrack,
}

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DecodeError::Io(e) => write!(f, "io: {e}"),
            DecodeError::Symphonia(e) => write!(f, "decode: {e}"),
            DecodeError::NoAudioTrack => write!(f, "no audio track in container"),
        }
    }
}

/// A passage being decoded: a span of one file, not the whole file.
pub struct PassageDecoder {
    format: Box<dyn FormatReader>,
    decoder: Box<dyn Decoder>,
    track_id: u32,
    pub sample_rate: u32,
    pub channels: usize,
    /// Container start offset of this passage in milliseconds `[SPEC-TPL-025]`.
    start_ms: u64,
    /// Container end offset of this passage in milliseconds, if bounded.
    end_ms: Option<u64>,
    /// Passage-relative frame offset of the most recent open or `seek_ms`.
    offset_frames: u64,
    /// Interleaved f32 for the packet just decoded. Reused every call, so the
    /// allocation happens once regardless of passage length.
    scratch: Vec<f32>,
    frames_emitted: u64,
    frame_limit: Option<u64>,
    /// Frames to discard before the passage truly begins. `format.seek` lands
    /// on a packet boundary, not a sample, so it can stop short of the request;
    /// this is the remainder it undershot by.
    skip_frames: u64,
    /// symphonia's bit-reservoir underflows so far, for this opened file
    /// only `[GDE-HST-190]`: a start mid-file explains a few, and the log
    /// layer demotes those; many more suggest damage, and are reported when
    /// this closes. Per opened file, so a seek -- which reopens -- starts afresh.
    underflows: u32,
    /// Named in that report.
    path: std::path::PathBuf,
}

impl Drop for PassageDecoder {
    fn drop(&mut self) {
        crate::logging::reservoir_closed(&self.path, self.underflows);
    }
}

impl PassageDecoder {
    /// Open `path` and seek to `start_ms`, decoding at most `end_ms - start_ms`.
    /// `end_ms == None` means "to end of file".
    pub fn open(path: &Path, start_ms: u64, end_ms: Option<u64>) -> Result<Self, DecodeError> {
        Self::open_at(path, start_ms, end_ms, 0)
    }

    /// Open `path` for the passage spanning `[start_ms, end_ms)` and begin
    /// decoding `passage_offset_ms` into that passage `[SPEC-TPL-025]`.
    pub fn open_at(
        path: &Path,
        start_ms: u64,
        end_ms: Option<u64>,
        passage_offset_ms: u64,
    ) -> Result<Self, DecodeError> {
        let file = File::open(path).map_err(DecodeError::Io)?;
        let mss = MediaSourceStream::new(Box::new(file), Default::default());
        let mut hint = Hint::new();
        if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            hint.with_extension(ext);
        }
        let probed = symphonia::default::get_probe()
            .format(&hint, mss, &format_options(), &MetadataOptions::default())
            .map_err(DecodeError::Symphonia)?;
        let mut format = probed.format;

        let track = format
            .tracks()
            .iter()
            .find(|t| t.codec_params.codec != CODEC_TYPE_NULL)
            .ok_or(DecodeError::NoAudioTrack)?;
        let track_id = track.id;
        let sample_rate = track.codec_params.sample_rate.unwrap_or(44_100);
        let channels = track.codec_params.channels.map(|c| c.count()).unwrap_or(2);
        let time_base = track.codec_params.time_base;

        let decoder = symphonia::default::get_codecs()
            .make(&track.codec_params, &DecoderOptions::default())
            .map_err(DecodeError::Symphonia)?;

        // Seeking is what makes a 40-track DAO file tractable: minute 240 costs
        // the same as minute 1 [REQ-AUD-120].
        // Sample-accurate, not packet-accurate `[REQ-AUD-122]`.
        // Seek lands on a PACKET, and returns where it actually landed. Ignoring
        // that return value leaves every passage start off by up to a packet --
        // silent, but it shifts trim points and, because frame_limit is measured
        // from the requested start, drags the end boundary with it. Discard the
        // shortfall so start_ms means start_ms.
        let target_ms = start_ms.saturating_add(passage_offset_ms);
        let skip_frames = seek_before(&mut *format, track_id, time_base, sample_rate, target_ms)?;

        let frame_limit = end_ms.map(|e| {
            let span_ms = e.saturating_sub(target_ms);
            (span_ms as f64 * sample_rate as f64 / 1000.0) as u64
        });
        let offset_frames = (passage_offset_ms as f64 * sample_rate as f64 / 1000.0) as u64;

        Ok(Self {
            format,
            decoder,
            track_id,
            sample_rate,
            channels,
            start_ms,
            end_ms,
            offset_frames,
            scratch: Vec::new(),
            frames_emitted: 0,
            frame_limit,
            skip_frames,
            underflows: 0,
            path: path.to_path_buf(),
        })
    }

    /// Seek in-place to `passage_offset_ms` relative to the passage's own
    /// `start_ms` without reopening the underlying file `[SPEC-TPL-025]`.
    pub fn seek_ms(&mut self, passage_offset_ms: u64) -> Result<(), DecodeError> {
        let target_ms = self.start_ms.saturating_add(passage_offset_ms);
        let time_base = self.format.tracks().iter().find(|t| t.id == self.track_id)
            .and_then(|t| t.codec_params.time_base);
        self.skip_frames = seek_before(&mut *self.format, self.track_id, time_base, self.sample_rate, target_ms)?;
        self.decoder.reset();
        self.frames_emitted = 0;
        self.frame_limit = self.end_ms.map(|e| {
            let span_ms = e.saturating_sub(target_ms);
            (span_ms as f64 * self.sample_rate as f64 / 1000.0) as u64
        });
        self.offset_frames = (passage_offset_ms as f64 * self.sample_rate as f64 / 1000.0) as u64;
        Ok(())
    }

    /// Current decoded position relative to the passage's `start_ms`, in
    /// milliseconds `[SPEC-TPL-025]`.
    pub fn position_ms(&self) -> u64 {
        self.position_frames().saturating_mul(1000) / (self.sample_rate.max(1) as u64)
    }

    /// Current decoded position relative to the passage's `start_ms`, in
    /// frames at the file's own rate `[SPEC-TPL-025]`.
    pub fn position_frames(&self) -> u64 {
        self.offset_frames + self.frames_emitted
    }

    /// Decode the next packet. Returns interleaved f32 frames, or `None` at end
    /// of passage. The slice borrows internal scratch and is valid until the
    /// next call -- callers copy into their own ring buffer.
    ///
    /// **Not `Iterator::next`, and cannot be.** The item borrows `self`, which
    /// a lending iterator would need and `Iterator` cannot express. Keeping the
    /// name because it is the right verb, and saying here why it is not the
    /// trait method it looks like.
    #[allow(clippy::should_implement_trait)]
    pub fn next(&mut self) -> Result<Option<&[f32]>, DecodeError> {
        if let Some(limit) = self.frame_limit {
            if self.frames_emitted >= limit {
                return Ok(None);
            }
        }
        loop {
            let packet = match self.format.next_packet() {
                Ok(p) => p,
                // Clean end of stream, plus the ResetRequired a chained-header
                // stream can raise at a boundary.
                Err(SymError::IoError(ref e))
                    if e.kind() == std::io::ErrorKind::UnexpectedEof =>
                {
                    return Ok(None)
                }
                Err(SymError::ResetRequired) => return Ok(None),
                Err(e) => return Err(DecodeError::Symphonia(e)),
            };
            if packet.track_id() != self.track_id {
                continue;
            }
            // Decoded inside this file's context, so the log layer can tell a
            // reservoir underflow a mid-file start explains from the tenth one
            // in a damaged file `[GDE-HST-190]`.
            let decoded = crate::logging::decoding(&mut self.underflows, || self.decoder.decode(&packet));
            match decoded {
                Ok(buf) => {
                    // Disjoint field borrows: `buf` borrows self.decoder while
                    // scratch is a different field, which is legal where a
                    // `&mut self` method would not be.
                    fill_scratch(&mut self.scratch, self.channels, &buf);
                    // Drop the post-seek remainder before anything counts it.
                    if self.skip_frames > 0 {
                        let have = (self.scratch.len() / self.channels) as u64;
                        let drop = self.skip_frames.min(have);
                        self.scratch.drain(..(drop as usize * self.channels));
                        self.skip_frames -= drop;
                        if self.scratch.is_empty() {
                            continue;
                        }
                    }
                    let mut n = self.scratch.len() / self.channels;
                    if let Some(limit) = self.frame_limit {
                        let remaining = limit.saturating_sub(self.frames_emitted) as usize;
                        if n > remaining {
                            n = remaining;
                            self.scratch.truncate(n * self.channels);
                        }
                    }
                    self.frames_emitted += n as u64;
                    return Ok(Some(&self.scratch));
                }
                // A corrupt packet mid-file should skip, not abort playback: the
                // rest of the passage is still good.
                Err(SymError::DecodeError(_)) => continue,
                Err(e) => return Err(DecodeError::Symphonia(e)),
            }
        }
    }

    pub fn frames_emitted(&self) -> u64 {
        self.frames_emitted
    }
}

/// How far before a passage's start its decoding begins `[SPEC-AGN-064]`.
///
/// symphonia's accurate seek lands on the frame holding the start, and steps
/// back for the bit reservoir that frame's data begins in -- but the frames
/// it steps back to are themselves decoded without theirs, and their output
/// is what the next frame overlaps with. Measured 2026-10-30 on *Highway 61
/// Revisited*'s DAO MP3: started mid-file, a passage came out wrong for up to
/// 26 ms, up to 499 samples of it digital silence, at 6 of 8 places tried.
/// Every passage's 20 ms fade-in had hidden most of it. Decoding from 100 ms
/// before -- nearly four MP3 frames -- and discarding up to the exact sample
/// leaves the start where it was and its audio as encoded.
const PREROLL_MS: u64 = 100;

/// Seek `format` so that decoding from here reaches `target_ms` after
/// discarding the frames returned. Starts `PREROLL_MS` early, measured in
/// the track's own time base; a track with none is sought to its start
/// directly, as every track was before.
fn seek_before(
    format: &mut dyn FormatReader,
    track_id: u32,
    time_base: Option<TimeBase>,
    sample_rate: u32,
    target_ms: u64,
) -> Result<u64, DecodeError> {
    let at = |ms: u64| Time::from(std::time::Duration::from_millis(ms));
    let Some(tb) = time_base else {
        if target_ms == 0 {
            return Ok(0);
        }
        let landed = format
            .seek(SeekMode::Accurate, SeekTo::Time { time: at(target_ms), track_id: Some(track_id) })
            .map_err(DecodeError::Symphonia)?;
        return Ok(landed.required_ts.saturating_sub(landed.actual_ts));
    };
    let landed = format
        .seek(
            SeekMode::Accurate,
            SeekTo::Time { time: at(target_ms.saturating_sub(PREROLL_MS)), track_id: Some(track_id) },
        )
        .map_err(DecodeError::Symphonia)?;
    // The shortfall in the track's units, then in frames: a time base need
    // not be one tick a frame.
    let ticks = tb.calc_timestamp(at(target_ms)).saturating_sub(landed.actual_ts);
    Ok((ticks as f64 * tb.numer as f64 / tb.denom as f64 * sample_rate as f64).round() as u64)
}

/// How a file is opened. Gapless trimming -- dropping an MP3's encoder delay
/// and padding, as its LAME tag records them -- is off, as it always has
/// been, so a position in an MP3 is about 25 ms early against the audio it
/// was encoded from. One file, one offset, and nobody hears it; a join
/// between two files is where it shows. The join probe turns it on to
/// measure that `[SPEC-AGN-060]`.
#[cfg(not(test))]
fn format_options() -> FormatOptions {
    FormatOptions::default()
}

#[cfg(test)]
fn format_options() -> FormatOptions {
    FormatOptions {
        enable_gapless: std::env::var_os("LEMPI_JOIN_GAPLESS").is_some(),
        ..Default::default()
    }
}

/// Interleave a decoded packet into `scratch`, converting to f32.
///
/// A free function rather than a method: the caller holds a borrow of
/// `self.decoder` through `buf`, so a `&mut self` receiver would conflict.
fn fill_scratch(scratch: &mut Vec<f32>, ch: usize, buf: &AudioBufferRef<'_>) {
    let frames = buf.frames();
    scratch.clear();
    scratch.resize(frames * ch, 0.0);

    // symphonia hands back planar buffers; the audio path wants interleaved,
    // and doing the conversion here keeps it in one place.
    macro_rules! planar {
        ($b:expr, $conv:expr) => {{
            let avail = $b.spec().channels.count();
            for c in 0..ch.min(avail) {
                let src = $b.chan(c);
                for (i, s) in src.iter().enumerate().take(frames) {
                    scratch[i * ch + c] = $conv(*s);
                }
            }
        }};
    }

    match buf {
        AudioBufferRef::F32(b) => planar!(b, |s: f32| s),
        AudioBufferRef::S16(b) => planar!(b, |s: i16| s as f32 / 32_768.0),
        AudioBufferRef::S32(b) => planar!(b, |s: i32| s as f32 / 2_147_483_648.0),
        AudioBufferRef::U8(b) => planar!(b, |s: u8| (s as f32 - 128.0) / 128.0),
        // Remaining formats are rare in this library; emit silence rather than
        // panicking so playback continues.
        _ => scratch.iter_mut().for_each(|v| *v = 0.0),
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// Write a 44.1 kHz stereo WAV whose every sample encodes its own frame
    /// index, so a decoded value says exactly where in the file it came from.
    /// That is what makes a seek testable rather than merely non-crashing.
    pub(crate) fn ramp_wav(path: &std::path::Path, frames: u32) {
        let (rate, ch, bits) = (44_100u32, 2u16, 16u16);
        let data_len = frames * ch as u32 * 2;
        let mut b: Vec<u8> = Vec::with_capacity(44 + data_len as usize);
        b.extend(b"RIFF");
        b.extend(&(36 + data_len).to_le_bytes());
        b.extend(b"WAVEfmt ");
        b.extend(&16u32.to_le_bytes());
        b.extend(&1u16.to_le_bytes()); // PCM
        b.extend(&ch.to_le_bytes());
        b.extend(&rate.to_le_bytes());
        b.extend(&(rate * ch as u32 * 2).to_le_bytes());
        b.extend(&(ch * bits / 8).to_le_bytes());
        b.extend(&bits.to_le_bytes());
        b.extend(b"data");
        b.extend(&data_len.to_le_bytes());
        for f in 0..frames {
            // frame index / 8: unique across the whole fixture, so a decoded
            // sample identifies its own position to within 8 frames. A modulo
            // would alias, and an aliased ramp cannot tell a correct seek from
            // one that is a whole period out.
            let v = (f / 8) as i16;
            b.extend(&v.to_le_bytes());
            b.extend(&v.to_le_bytes());
        }
        std::fs::write(path, b).unwrap();
    }

    pub(crate) fn tmp(name: &str) -> std::path::PathBuf {
        let p = std::env::temp_dir()
            .join(format!("lempi_{}_{}.wav", name, std::process::id()));
        ramp_wav(&p, 44_100 * 5);
        p
    }

    /// The value a frame index should decode to, as a float.
    fn expect(frame: u64) -> f32 {
        (frame / 8) as f32 / 32768.0
    }

    /// Invert `expect`: which frame did this sample come from?
    fn frame_of(v: f32) -> u64 {
        (v * 32768.0).round() as u64 * 8
    }

    #[test]
    fn decodes_from_the_start() {
        let f = tmp("start");
        let mut d = PassageDecoder::open(&f, 0, None).unwrap();
        let first = d.next().unwrap().unwrap()[0];
        assert!((first - expect(0)).abs() < 1e-3, "got {first}");
        let _ = std::fs::remove_file(f);
    }

    /// The seek must land on the requested millisecond, not the start of the
    /// file and not the start of the packet that happens to contain it.
    #[test]
    fn seeks_to_the_requested_offset() {
        let f = tmp("seek");
        let start_ms = 2_000;
        let mut d = PassageDecoder::open(&f, start_ms, None).unwrap();
        let first = d.next().unwrap().unwrap()[0];
        let want = start_ms * 44_100 / 1000;
        let got = frame_of(first);
        assert!(
            got.abs_diff(want) <= 8,
            "seek to {start_ms} ms landed on frame {got}, wanted {want} \
             (off by {} frames = {:.1} ms)",
            got.abs_diff(want),
            got.abs_diff(want) as f64 * 1000.0 / 44_100.0
        );
        let _ = std::fs::remove_file(f);
    }

    /// A bounded passage must stop at end_ms, not run to end of file --
    /// otherwise every passage in a multi-track file would play the whole file.
    #[test]
    fn stops_at_the_end_boundary() {
        let f = tmp("bound");
        let mut d = PassageDecoder::open(&f, 0, Some(1_000)).unwrap();
        let mut frames = 0u64;
        while let Some(c) = d.next().unwrap() {
            frames += (c.len() / 2) as u64;
        }
        let want = 44_100;
        let err = (frames as i64 - want as i64).abs();
        assert!(err < 4096, "decoded {frames} frames for 1000 ms, wanted ~{want}");
        let _ = std::fs::remove_file(f);
    }

    /// `[SPEC-TPL-025]`: `seek_ms` seeks relative to the passage's `start_ms`
    /// inside a multi-track container and updates `position_ms` accordingly.
    #[test]
    fn seek_ms_preserves_passage_start_and_resets_position() {
        let f = tmp("seek_ms_loop");
        // Passage starts at 1_000 ms and ends at 4_000 ms in the 5_000 ms file.
        let mut d = PassageDecoder::open(&f, 1_000, Some(4_000)).unwrap();
        assert_eq!(d.position_ms(), 0);
        let first = frame_of(d.next().unwrap().unwrap()[0]);
        assert!(first.abs_diff(44_100) <= 8, "started at 1_000 ms in file, got {first}");

        // Seek to 1_500 ms into the passage (= 2_500 ms into the file).
        d.seek_ms(1_500).unwrap();
        assert_eq!(d.position_ms(), 1_500);
        let at_b = frame_of(d.next().unwrap().unwrap()[0]);
        let want_b = 2_500 * 44_100 / 1000;
        assert!(at_b.abs_diff(want_b) <= 8, "seek_ms(1500) landed at {at_b}, wanted {want_b}");

        // Loop back to 500 ms into the passage (= 1_500 ms into the file).
        d.seek_ms(500).unwrap();
        assert_eq!(d.position_ms(), 500);
        let at_a = frame_of(d.next().unwrap().unwrap()[0]);
        let want_a = 1_500 * 44_100 / 1000;
        assert!(at_a.abs_diff(want_a) <= 8, "seek_ms(500) landed at {at_a}, wanted {want_a}");
        let _ = std::fs::remove_file(f);
    }
}
