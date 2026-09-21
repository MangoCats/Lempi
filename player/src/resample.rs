//! Sample-rate conversion between a passage and the output device.
//!
//! A library holds files at 44.1 kHz while a device may open at 48 kHz, so the
//! two rates must be reconciled somewhere. Doing it here — once, on the way into
//! a stream's buffer — means everything downstream sees a single rate, and the
//! mixer never has to ask what rate a stream is `[XFD-ORTH-020]`.
//!
//! Placement matters: resampling *after* mixing would force every stream to the
//! same rate anyway, but would also resample audio that has already been summed,
//! turning one conversion per passage into one per crossfade.

use rubato::{FftFixedIn, Resampler as _};

/// Greatest common divisor, for reducing a sample-rate pair to lowest terms.
fn gcd(a: u32, b: u32) -> u32 {
    if b == 0 { a } else { gcd(b, a % b) }
}

/// Converts interleaved f32 between two rates, or passes through when they match.
pub struct Resampler {
    inner: Option<FftFixedIn<f32>>,
    channels: usize,
    /// Planar scratch. Reused across calls so steady-state conversion does not
    /// allocate.
    planar_in: Vec<Vec<f32>>,
    planar_out: Vec<Vec<f32>>,
    /// Input frames not yet consumed: the FFT resampler needs fixed-size blocks,
    /// and decoder packets do not arrive in those sizes.
    pending: Vec<f32>,
    chunk: usize,
}

impl Resampler {
    /// Equal rates yield a pass-through, which costs nothing and keeps callers
    /// free of `if rates_differ` branches.
    pub fn new(from_hz: u32, to_hz: u32, channels: usize) -> Result<Self, String> {
        if from_hz == to_hz {
            return Ok(Self {
                inner: None,
                channels,
                planar_in: Vec::new(),
                planar_out: Vec::new(),
                pending: Vec::new(),
                chunk: 0,
            });
        }
        // The chunk size is NOT free. FftFixedIn approximates the rate ratio
        // over a chunk, and an arbitrary size misses it: measured 44100->48000
        // at chunk 1024 gives 1.0781 against an ideal 1.08844 -- a ~1% error,
        // roughly 16 cents of pitch, and it drifts as internal state cycles.
        //
        // Choosing a multiple of from_hz / gcd(from_hz, to_hz) makes the ratio
        // exact: 44100:48000 reduces to 147:160, so any multiple of 147 works
        // (verified 0.000% error at 1029, 1470 and 2940).
        let base = (from_hz / gcd(from_hz, to_hz)) as usize;
        let chunk = base * 1024usize.div_ceil(base).max(1); // >= ~1024 frames
        let inner = FftFixedIn::<f32>::new(from_hz as usize, to_hz as usize, chunk, 2, channels)
            .map_err(|e| format!("resampler {from_hz}->{to_hz}: {e}"))?;
        Ok(Self {
            inner: Some(inner),
            channels,
            planar_in: vec![Vec::with_capacity(chunk); channels],
            planar_out: vec![Vec::new(); channels],
            pending: Vec::new(),
            chunk,
        })
    }

    pub fn is_passthrough(&self) -> bool {
        self.inner.is_none()
    }

    /// Convert `input`, appending interleaved output to `out`.
    ///
    /// Whole chunks are consumed and the remainder held for the next call, so a
    /// caller may push arbitrary packet sizes.
    pub fn process(&mut self, input: &[f32], out: &mut Vec<f32>) -> Result<(), String> {
        let Some(rs) = self.inner.as_mut() else {
            out.extend_from_slice(input);
            return Ok(());
        };
        self.pending.extend_from_slice(input);
        let need = self.chunk * self.channels;

        while self.pending.len() >= need {
            for (c, plane) in self.planar_in.iter_mut().enumerate() {
                plane.clear();
                plane.extend(self.pending[..need].iter().skip(c).step_by(self.channels));
            }
            let produced = rs
                .process(&self.planar_in, None)
                .map_err(|e| format!("resample: {e}"))?;
            self.planar_out = produced;

            let frames = self.planar_out.first().map(|p| p.len()).unwrap_or(0);
            out.reserve(frames * self.channels);
            for f in 0..frames {
                for plane in &self.planar_out {
                    out.push(plane[f]);
                }
            }
            self.pending.drain(..need);
        }
        Ok(())
    }

    /// Frames held back awaiting a full chunk.
    pub fn pending_frames(&self) -> usize {
        if self.channels == 0 { 0 } else { self.pending.len() / self.channels }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn equal_rates_pass_through_untouched() {
        let mut r = Resampler::new(44_100, 44_100, 2).unwrap();
        assert!(r.is_passthrough());
        let input: Vec<f32> = (0..64).map(|i| i as f32 * 0.01).collect();
        let mut out = Vec::new();
        r.process(&input, &mut out).unwrap();
        assert_eq!(out, input, "pass-through must not alter samples");
    }

    #[test]
    fn arbitrary_packet_sizes_are_accepted() {
        // Decoder packets are 1152 frames for MP3, which is not the chunk size.
        let mut r = Resampler::new(44_100, 48_000, 2).unwrap();
        let mut out = Vec::new();
        for _ in 0..10 {
            r.process(&vec![0.25f32; 1152 * 2], &mut out).unwrap();
        }
        assert!(!out.is_empty(), "must emit despite ragged input sizes");
        assert!(r.pending_frames() < 2048, "remainder must stay below one chunk");
    }

    /// The bug this module nearly shipped: an arbitrary chunk size makes the
    /// FFT resampler miss the rate ratio by ~1%, which is audible pitch error.
    ///
    /// This replaces separate up- and down-sampling tests. They asserted the
    /// same property with hardcoded frame counts that were only valid for one
    /// direction -- the ratio is the thing under test, so test it once, in both
    /// directions, against each case's own chunk size.
    #[test]
    fn chunk_size_makes_the_rate_ratio_exact() {
        for (from, to) in [(44_100u32, 48_000u32), (48_000, 44_100), (44_100, 96_000)] {
            let mut r = Resampler::new(from, to, 2).unwrap();
            let frames_in = r.chunk * 40;
            let mut out = Vec::new();
            r.process(&vec![0.0f32; frames_in * 2], &mut out).unwrap();
            let ratio = (out.len() / 2) as f64 / frames_in as f64;
            let ideal = to as f64 / from as f64;
            assert!((ratio - ideal).abs() / ideal < 0.001,
                    "{from}->{to}: ratio {ratio:.6} vs ideal {ideal:.6}");
        }
    }

    #[test]
    fn channel_interleaving_survives_conversion() {
        let mut r = Resampler::new(44_100, 48_000, 2).unwrap();
        // Constant but distinct per channel; conversion must not mix them.
        let input: Vec<f32> = (0..1029 * 8).flat_map(|_| [1.0f32, -1.0f32]).collect();
        let mut out = Vec::new();
        r.process(&input, &mut out).unwrap();
        let mid = (out.len() / 2) & !1; // land on a frame boundary
        assert!(out[mid] > 0.5, "left channel should stay positive");
        assert!(out[mid + 1] < -0.5, "right channel should stay negative");
    }
}
