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

use std::fs::File;
use std::path::Path;

use symphonia::core::audio::{AudioBufferRef, Signal};
use symphonia::core::codecs::{Decoder, DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::{FormatOptions, FormatReader, SeekMode, SeekTo};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;
use symphonia::core::units::Time;

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
        let file = File::open(path).map_err(DecodeError::Io)?;
        let mss = MediaSourceStream::new(Box::new(file), Default::default());
        let mut hint = Hint::new();
        if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            hint.with_extension(ext);
        }
        let probed = symphonia::default::get_probe()
            .format(&hint, mss, &FormatOptions::default(), &MetadataOptions::default())
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
        let mut skip_frames = 0u64;
        if start_ms > 0 {
            let t = Time::from(std::time::Duration::from_millis(start_ms));
            let landed = format
                .seek(SeekMode::Accurate, SeekTo::Time { time: t, track_id: Some(track_id) })
                .map_err(DecodeError::Symphonia)?;
            skip_frames = landed.required_ts.saturating_sub(landed.actual_ts);
        }

        let frame_limit = end_ms.map(|e| {
            let span_ms = e.saturating_sub(start_ms);
            (span_ms as f64 * sample_rate as f64 / 1000.0) as u64
        });

        Ok(Self {
            format,
            decoder,
            track_id,
            sample_rate,
            channels,
            scratch: Vec::new(),
            frames_emitted: 0,
            frame_limit,
            skip_frames,
            underflows: 0,
            path: path.to_path_buf(),
        })
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
}
