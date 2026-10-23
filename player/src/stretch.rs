//! Phase-locked spectral time-stretcher `[SPEC-TPL-050]`.
//!
//! Sits in `Live` between the decoder and [`crate::resample::Resampler`], at
//! the passage's own sample rate. It changes **duration only**: for tempo
//! ratio `t` and pitch ratio `p` it stretches by `p / t`, and the resampler
//! after it then plays that out as if recorded at `f_in * p` -- which raises
//! the pitch by `p` and shortens the duration by `1 / p`, leaving `1 / t`
//! overall ([`pitch_rate_hz`]). Pitch is therefore shifted by the same
//! band-limited FFT resampler every passage already goes through, not by a
//! second, unfiltered interpolator: an octave up through linear or cubic
//! interpolation folds everything above a quarter of the sample rate back
//! down as aliasing.
//!
//! At neutral (`tempo_milli == 1000`, `pitch_cents == 0`) [`Stretcher::process`]
//! is a bit-exact copy that never touches the FFT `[SPEC-TPL-050]`.

#![deny(clippy::print_stdout, clippy::print_stderr)]

use rustfft::num_complex::Complex;
use rustfft::{Fft, FftPlanner};
use std::f64::consts::PI;
use std::sync::Arc;

/// Minimum supported playback tempo (`0.25x`) in milli-x `[SPEC-TPL-040]`.
pub const TEMPO_MIN_MILLI: u32 = 250;
/// Maximum supported playback tempo (`4.00x`) in milli-x `[SPEC-TPL-040]`.
pub const TEMPO_MAX_MILLI: u32 = 4000;
/// Neutral playback tempo (`1.00x`) in milli-x `[SPEC-TPL-040]`.
pub const TEMPO_NEUTRAL_MILLI: u32 = 1000;

/// Minimum supported pitch shift (`-12.0` semitones) in cents `[SPEC-TPL-040]`.
pub const PITCH_MIN_CENTS: i32 = -1200;
/// Maximum supported pitch shift (`+12.0` semitones) in cents `[SPEC-TPL-040]`.
pub const PITCH_MAX_CENTS: i32 = 1200;
/// Neutral pitch shift (`0.0` semitones) in cents `[SPEC-TPL-040]`.
pub const PITCH_NEUTRAL_CENTS: i32 = 0;

const FFT_SIZE: usize = 2048;
const HOP_S: usize = FFT_SIZE / 4; // 512 frames (4x overlap)
const BINS: usize = FFT_SIZE / 2 + 1;

/// Share of a frame's spectral magnitude that must be *new* -- rising since
/// the previous analysis frame -- for it to count as an attack.
///
/// A held tone scores near zero and a drum hit out of quiet near one. Not
/// measured against a corpus; chosen so the steady-tone tests never reset and
/// an onset over silence always does.
const FLUX_ONSET: f64 = 0.35;

/// The rate the resampler must treat stretched audio as having so that it
/// comes out `pitch_cents` higher at the device `[SPEC-TPL-050]`.
///
/// `FftFixedIn` takes integer rates and sizes its FFT from their common
/// divisor, so the exact `f_in * 2^(cents/1200)` -- almost never an integer,
/// and coprime with the device rate when rounded to 1 Hz -- would plan a
/// transform tens of thousands of points long. A multiple of 50 Hz keeps the
/// divisor at 50 or better against every common device rate, and costs at
/// most 25 Hz in ~44 kHz: under one cent, well below what an ear resolves.
pub fn pitch_rate_hz(source_hz: u32, pitch_cents: i32) -> u32 {
    if pitch_cents == PITCH_NEUTRAL_CENTS {
        return source_hz;
    }
    let exact = source_hz as f64 * 2f64.powf(pitch_cents as f64 / 1200.0);
    ((exact / 50.0).round() as u32 * 50).max(50)
}

#[inline]
fn wrap_pi(phase: f64) -> f64 {
    let two_pi = 2.0 * PI;
    phase - two_pi * ((phase + PI) / two_pi).floor()
}

/// Per-channel phase-vocoder state.
struct ChannelVocoder {
    prev_ana_phase: Vec<f64>,
    synth_phase: Vec<f64>,
    next_synth_phase: Vec<f64>,
    ana_mag: Vec<f64>,
    prev_mag: Vec<f64>,
    ana_phase: Vec<f64>,
    inst_freq: Vec<f64>,
    peaks: Vec<usize>,
    ola_buf: Vec<f32>,
}

impl ChannelVocoder {
    fn new() -> Self {
        Self {
            prev_ana_phase: vec![0.0; BINS],
            synth_phase: vec![0.0; BINS],
            next_synth_phase: vec![0.0; BINS],
            ana_mag: vec![0.0; BINS],
            prev_mag: vec![0.0; BINS],
            ana_phase: vec![0.0; BINS],
            inst_freq: vec![0.0; BINS],
            peaks: Vec::with_capacity(BINS / 2),
            ola_buf: vec![0.0; FFT_SIZE],
        }
    }

    fn reset(&mut self) {
        self.prev_ana_phase.fill(0.0);
        self.synth_phase.fill(0.0);
        self.next_synth_phase.fill(0.0);
        self.prev_mag.fill(0.0);
        self.ola_buf.fill(0.0);
    }
}

/// Duration-only phase-vocoder stretcher for an interleaved stream.
pub struct Stretcher {
    channels: usize,
    tempo_milli: u32,
    pitch_cents: i32,
    window: Vec<f32>,
    fft_fwd: Arc<dyn Fft<f32>>,
    fft_inv: Arc<dyn Fft<f32>>,
    fft_buf: Vec<Complex<f32>>,
    fft_scratch: Vec<Complex<f32>>,
    vocoders: Vec<ChannelVocoder>,
    /// Interleaved input samples awaiting analysis windows.
    in_buf: Vec<f32>,
    /// Fractional frame cursor inside `in_buf`.
    ana_pos: f64,
    /// Previous integer analysis frame offset (relative to current `in_buf`).
    prev_ana_frame: Option<usize>,
    /// Whether any analysis frame has been taken since the last reset.
    initialized: bool,
}

impl Stretcher {
    pub fn new(channels: usize, tempo_milli: u32, pitch_cents: i32) -> Self {
        let ch = channels.max(1);
        let mut planner = FftPlanner::<f32>::new();
        let fft_fwd = planner.plan_fft_forward(FFT_SIZE);
        let fft_inv = planner.plan_fft_inverse(FFT_SIZE);
        let scratch_len = fft_fwd
            .get_inplace_scratch_len()
            .max(fft_inv.get_inplace_scratch_len());

        let window: Vec<f32> = (0..FFT_SIZE)
            .map(|n| {
                let t = 2.0 * PI * (n as f64) / (FFT_SIZE as f64);
                (0.5 * (1.0 - t.cos())) as f32
            })
            .collect();

        Self {
            channels: ch,
            tempo_milli: tempo_milli.clamp(TEMPO_MIN_MILLI, TEMPO_MAX_MILLI),
            pitch_cents: pitch_cents.clamp(PITCH_MIN_CENTS, PITCH_MAX_CENTS),
            window,
            fft_fwd,
            fft_inv,
            fft_buf: vec![Complex::new(0.0, 0.0); FFT_SIZE],
            fft_scratch: vec![Complex::new(0.0, 0.0); scratch_len],
            vocoders: (0..ch).map(|_| ChannelVocoder::new()).collect(),
            in_buf: Vec::with_capacity(FFT_SIZE * ch * 2),
            ana_pos: 0.0,
            prev_ana_frame: None,
            initialized: false,
        }
    }

    /// True when both tempo and pitch are at their neutral defaults (`1000`
    /// and `0`) `[SPEC-TPL-050]`.
    #[inline]
    pub fn is_passthrough(&self) -> bool {
        self.tempo_milli == TEMPO_NEUTRAL_MILLI && self.pitch_cents == PITCH_NEUTRAL_CENTS
    }

    pub fn tempo_milli(&self) -> u32 {
        self.tempo_milli
    }

    pub fn pitch_cents(&self) -> i32 {
        self.pitch_cents
    }

    /// Duration factor this stretcher applies: `p / t`.
    fn stretch_factor(&self) -> f64 {
        let t = self.tempo_milli as f64 / 1000.0;
        let p = 2f64.powf(self.pitch_cents as f64 / 1200.0);
        p / t
    }

    /// Whether the vocoder is needed at all. Not only at neutral: when tempo
    /// and pitch move together (`t == p`, varispeed) the duration is already
    /// right and the resampler alone does the job.
    fn copies(&self) -> bool {
        self.is_passthrough() || (self.stretch_factor() - 1.0).abs() < 1e-9
    }

    /// Clear internal overlap-add state (e.g. on seek or loop wrap).
    pub fn reset(&mut self) {
        self.in_buf.clear();
        self.ana_pos = 0.0;
        self.prev_ana_frame = None;
        self.initialized = false;
        for v in &mut self.vocoders {
            v.reset();
        }
    }

    /// Flush any residual buffered frames at passage EOF into `out` and reset state.
    pub fn flush(&mut self, out: &mut Vec<f32>) {
        if !self.copies() && !self.in_buf.is_empty() {
            let pad = vec![0.0_f32; FFT_SIZE * self.channels];
            self.process(&pad, out);
        }
        self.reset();
    }

    /// Process interleaved `input` samples, appending stretched interleaved
    /// samples to `out`.
    pub fn process(&mut self, input: &[f32], out: &mut Vec<f32>) {
        if self.copies() {
            out.extend_from_slice(input);
            return;
        }
        if input.is_empty() {
            return;
        }

        let hop_a = (HOP_S as f64) / self.stretch_factor();
        let ch = self.channels;
        self.in_buf.extend_from_slice(input);
        let avail_frames = self.in_buf.len() / ch;

        while (self.ana_pos as usize) + FFT_SIZE <= avail_frames {
            let cur_frame = self.ana_pos as usize;
            let da = match self.prev_ana_frame {
                Some(prev) => (cur_frame.saturating_sub(prev)) as f64,
                None => HOP_S as f64,
            };

            // Analyse every channel first, so the attack decision below is one
            // decision for the frame. Decided per channel, a hit louder on one
            // side would reset that side's phases alone and pull the image apart.
            let (mut flux, mut total) = (0.0_f64, 0.0_f64);
            for c in 0..ch {
                let (f, t) = self.analyse_channel(c, cur_frame);
                flux += f;
                total += t;
            }
            let onset = self.initialized && total > 1e-6 && flux / total > FLUX_ONSET;
            for c in 0..ch {
                self.synthesise_channel(c, da, !self.initialized || onset || da <= 0.0);
            }
            self.initialized = true;
            self.emit_hop(out);

            self.prev_ana_frame = Some(cur_frame);
            self.ana_pos += hop_a;
        }

        // Drain consumed input frames while keeping `prev_ana_frame` relative.
        let drain_frames = if let Some(prev) = self.prev_ana_frame {
            prev.min(self.ana_pos as usize).min(avail_frames)
        } else {
            (self.ana_pos as usize).min(avail_frames)
        };
        if drain_frames > 0 {
            self.in_buf.drain(..drain_frames * ch);
            self.ana_pos -= drain_frames as f64;
            if let Some(prev) = self.prev_ana_frame.as_mut() {
                *prev -= drain_frames;
            }
        }
    }

    /// Window and transform one channel; returns its spectral flux (magnitude
    /// risen since the last frame) and total magnitude `[SPEC-TPL-050]`.
    fn analyse_channel(&mut self, c: usize, start_frame: usize) -> (f64, f64) {
        let ch = self.channels;
        for i in 0..FFT_SIZE {
            let s = self.in_buf[(start_frame + i) * ch + c];
            self.fft_buf[i] = Complex::new(s * self.window[i], 0.0);
        }
        self.fft_fwd
            .process_with_scratch(&mut self.fft_buf, &mut self.fft_scratch);

        let v = &mut self.vocoders[c];
        let (mut flux, mut total) = (0.0, 0.0);
        for k in 0..BINS {
            let bin = self.fft_buf[k];
            let mag = (bin.re as f64).hypot(bin.im as f64);
            flux += (mag - v.prev_mag[k]).max(0.0);
            total += mag;
            v.prev_mag[k] = mag;
            v.ana_mag[k] = mag;
            v.ana_phase[k] = (bin.im as f64).atan2(bin.re as f64);
        }
        (flux, total)
    }

    /// Advance one channel's synthesis phases with identity phase-locking
    /// around spectral peaks (Laroche & Dolson 1999), or restart them from the
    /// analysis phases at an onset, then overlap-add the frame.
    fn synthesise_channel(&mut self, c: usize, da: f64, restart: bool) {
        let v = &mut self.vocoders[c];
        if restart {
            v.synth_phase.copy_from_slice(&v.ana_phase);
            v.prev_ana_phase.copy_from_slice(&v.ana_phase);
        } else {
            v.peaks.clear();
            for k in 1..(BINS - 1) {
                if v.ana_mag[k] >= v.ana_mag[k - 1]
                    && v.ana_mag[k] > v.ana_mag[k + 1]
                    && v.ana_mag[k] > 1e-7
                {
                    v.peaks.push(k);
                }
            }

            for k in 0..BINS {
                let omega_k = 2.0 * PI * (k as f64) / (FFT_SIZE as f64);
                let expected = omega_k * da;
                let delta = wrap_pi(v.ana_phase[k] - v.prev_ana_phase[k] - expected);
                v.inst_freq[k] = omega_k + delta / da;
            }

            if v.peaks.is_empty() {
                for k in 0..BINS {
                    v.next_synth_phase[k] =
                        wrap_pi(v.synth_phase[k] + v.inst_freq[k] * (HOP_S as f64));
                }
            } else {
                for (idx, &p) in v.peaks.iter().enumerate() {
                    // Parabolic interpolation on log-magnitude refines the peak
                    // frequency when `da` is large (`> 512`, i.e. faster than
                    // the synthesis hop), where phase unwrapping is ambiguous.
                    let inst_f = if da > (HOP_S as f64) && p > 0 && p + 1 < BINS {
                        let m0 = v.ana_mag[p - 1].max(1e-12).ln();
                        let m1 = v.ana_mag[p].max(1e-12).ln();
                        let m2 = v.ana_mag[p + 1].max(1e-12).ln();
                        let denom = m0 - 2.0 * m1 + m2;
                        let frac = if denom.abs() > 1e-9 {
                            (0.5 * (m0 - m2) / denom).clamp(-0.5, 0.5)
                        } else {
                            0.0
                        };
                        let est_omega = 2.0 * PI * ((p as f64) + frac) / (FFT_SIZE as f64);
                        let expected = est_omega * da;
                        let delta = wrap_pi(v.ana_phase[p] - v.prev_ana_phase[p] - expected);
                        est_omega + delta / da
                    } else {
                        v.inst_freq[p]
                    };

                    let peak_synth = wrap_pi(v.synth_phase[p] + inst_f * (HOP_S as f64));
                    let theta_rot = peak_synth - v.ana_phase[p];

                    let region_start = if idx == 0 {
                        0
                    } else {
                        (v.peaks[idx - 1] + p) / 2 + 1
                    };
                    let region_end = if idx + 1 == v.peaks.len() {
                        BINS
                    } else {
                        (p + v.peaks[idx + 1]) / 2 + 1
                    };
                    for k in region_start..region_end {
                        v.next_synth_phase[k] = wrap_pi(v.ana_phase[k] + theta_rot);
                    }
                }
            }

            v.synth_phase.copy_from_slice(&v.next_synth_phase);
            v.prev_ana_phase.copy_from_slice(&v.ana_phase);
        }

        // Reconstruct complex spectrum with conjugate symmetry.
        for k in 0..BINS {
            let mag = v.ana_mag[k] as f32;
            let ph = v.synth_phase[k] as f32;
            let (s, co) = ph.sin_cos();
            let cplx = Complex::new(mag * co, mag * s);
            self.fft_buf[k] = cplx;
            if k > 0 && k < FFT_SIZE - k {
                self.fft_buf[FFT_SIZE - k] = cplx.conj();
            }
        }

        self.fft_inv
            .process_with_scratch(&mut self.fft_buf, &mut self.fft_scratch);

        // COLA scale for squared Hann window at 4x overlap: sum(w^2) = 1.5, plus 1/FFT_SIZE for IFFT.
        let norm = 1.0 / (1.5 * (FFT_SIZE as f32));
        for i in 0..FFT_SIZE {
            v.ola_buf[i] += self.fft_buf[i].re * self.window[i] * norm;
        }
    }

    fn emit_hop(&mut self, out: &mut Vec<f32>) {
        out.reserve(HOP_S * self.channels);
        for i in 0..HOP_S {
            for v in &self.vocoders {
                out.push(v.ola_buf[i]);
            }
        }
        for v in &mut self.vocoders {
            v.ola_buf.copy_within(HOP_S..FFT_SIZE, 0);
            v.ola_buf[(FFT_SIZE - HOP_S)..FFT_SIZE].fill(0.0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::resample::Resampler;

    fn dominant_freq_hz(samples: &[f32], sample_rate: u32) -> f64 {
        // Measure frequency via upward zero-crossings in the steady-state interior.
        let trim = samples.len() / 6;
        let interior = &samples[trim..samples.len().saturating_sub(trim)];
        let mut crossings = Vec::new();
        for i in 1..interior.len() {
            if interior[i - 1] <= 0.0 && interior[i] > 0.0 {
                let frac = (-interior[i - 1]) / (interior[i] - interior[i - 1]).max(1e-12);
                crossings.push((i - 1) as f64 + frac as f64);
            }
        }
        if crossings.len() < 2 {
            return 0.0;
        }
        let span = crossings.last().unwrap() - crossings.first().unwrap();
        let periods = (crossings.len() - 1) as f64;
        (sample_rate as f64) * periods / span
    }

    fn sine(freq: f64, sr: u32, frames: usize) -> Vec<f32> {
        (0..frames)
            .map(|i| (2.0 * PI * freq * (i as f64) / (sr as f64)).sin() as f32)
            .collect()
    }

    /// The whole warp path as `Live` runs it: stretcher, then the resampler
    /// told the pitch-shifted rate.
    fn warp(input: &[f32], sr: u32, out_sr: u32, tempo: u32, cents: i32) -> Vec<f32> {
        let mut s = Stretcher::new(1, tempo, cents);
        let mut r = Resampler::new(pitch_rate_hz(sr, cents), out_sr, 1).unwrap();
        let (mut mid, mut out) = (Vec::new(), Vec::new());
        for chunk in input.chunks(1024) {
            mid.clear();
            s.process(chunk, &mut mid);
            r.process(&mid, &mut out).unwrap();
        }
        out
    }

    #[test]
    fn passthrough_is_bit_exact() {
        let mut s = Stretcher::new(2, 1000, 0);
        assert!(s.is_passthrough());
        let input: Vec<f32> = (0..4096).map(|i| (i as f32 * 0.031).sin()).collect();
        let mut out = Vec::new();
        s.process(&input, &mut out);
        assert_eq!(out, input, "neutral 1000/0 must be bit-exact pass-through");
    }

    #[test]
    fn neutral_pitch_rate_is_the_source_rate_and_shifted_rates_are_coarse() {
        assert_eq!(pitch_rate_hz(44_100, 0), 44_100);
        assert_eq!(pitch_rate_hz(44_100, 1200), 88_200);
        for cents in [-1200, -700, -1, 1, 300, 1199] {
            let hz = pitch_rate_hz(44_100, cents);
            assert_eq!(hz % 50, 0, "{cents}: {hz}");
            let exact = 44_100.0 * 2f64.powf(cents as f64 / 1200.0);
            let err_cents = 1200.0 * (hz as f64 / exact).log2();
            assert!(err_cents.abs() < 1.0, "{cents}: {err_cents:.3} cents off");
        }
    }

    #[test]
    fn tempo_stretch_preserves_frequency_and_scales_length() {
        let sr = 44_100u32;
        let frames_in = sr as usize * 2; // 2 seconds of 440 Hz mono sine
        let input = sine(440.0, sr, frames_in);

        for (tempo_milli, expected_len_ratio) in [(500u32, 2.0f64), (2000u32, 0.5f64)] {
            let out = warp(&input, sr, sr, tempo_milli, 0);
            let actual_ratio = (out.len() as f64) / (frames_in as f64);
            assert!(
                (actual_ratio - expected_len_ratio).abs() < 0.05,
                "tempo {tempo_milli}: length ratio {actual_ratio:.3} vs expected {expected_len_ratio:.3}"
            );
            let freq = dominant_freq_hz(&out, sr);
            assert!(
                (freq - 440.0).abs() < 6.0,
                "tempo {tempo_milli}: frequency {freq:.2} Hz drifted from 440 Hz"
            );
        }
    }

    #[test]
    fn pitch_shift_scales_frequency_and_preserves_length() {
        let sr = 44_100u32;
        let frames_in = sr as usize * 2;
        let input = sine(440.0, sr, frames_in);

        // Into a 48 kHz device as well: the pitch step and the device
        // conversion are one resampling, and both must come out right.
        for out_sr in [44_100u32, 48_000] {
            for (cents, expected_freq) in [(-1200i32, 220.0f64), (1200i32, 880.0f64), (300, 523.25)] {
                let out = warp(&input, sr, out_sr, 1000, cents);
                let len_ratio = (out.len() as f64) / (frames_in as f64 * out_sr as f64 / sr as f64);
                assert!(
                    (len_ratio - 1.0).abs() < 0.08,
                    "pitch {cents} cents at {out_sr}: length ratio {len_ratio:.3} should stay ~1.0"
                );
                let freq = dominant_freq_hz(&out, out_sr);
                assert!(
                    (freq - expected_freq).abs() / expected_freq < 0.02,
                    "pitch {cents} cents at {out_sr}: got {freq:.2} Hz, expected {expected_freq:.2} Hz"
                );
            }
        }
    }

    /// The reason pitch goes through the FFT resampler: an octave up must not
    /// fold content above the new Nyquist back into the audible band.
    #[test]
    fn an_octave_up_does_not_alias() {
        let sr = 44_100u32;
        // 15 kHz becomes 30 kHz: above Nyquist, so it must vanish, not
        // reappear at 44.1 - 30 = 14.1 kHz.
        let input = sine(15_000.0, sr, sr as usize * 2);
        let out = warp(&input, sr, sr, 1000, 1200);
        let trim = out.len() / 4;
        let rms = |x: &[f32]| (x.iter().map(|v| v * v).sum::<f32>() / x.len() as f32).sqrt();
        let level = rms(&out[trim..out.len() - trim]);
        assert!(level < 0.02, "an octave up left {level:.3} RMS of aliased 15 kHz");
    }

    #[test]
    fn varispeed_skips_the_vocoder() {
        // Tempo and pitch moved together need no stretching at all.
        let mut s = Stretcher::new(1, 2000, 1200);
        let input: Vec<f32> = (0..4096).map(|i| (i as f32 * 0.01).sin()).collect();
        let mut out = Vec::new();
        s.process(&input, &mut out);
        assert_eq!(out, input);
    }

    #[test]
    fn an_onset_restarts_phases_and_a_held_tone_does_not() {
        let sr = 44_100u32;
        let mut onsets = 0;
        // The decision by hand over a held tone: no frame of a steady sine is
        // mostly new energy.
        let mut probe = Stretcher::new(1, 500, 0);
        probe.in_buf = sine(440.0, sr, sr as usize);
        let mut prev_total = None;
        for start in (0..sr as usize - FFT_SIZE).step_by(HOP_S) {
            let (flux, total) = probe.analyse_channel(0, start);
            if prev_total.is_some() && flux / total > FLUX_ONSET {
                onsets += 1;
            }
            prev_total = Some(total);
        }
        assert_eq!(onsets, 0, "a held tone must never count as an attack");

        let mut hit = Stretcher::new(1, 500, 0);
        let mut quiet_then_loud = vec![0.0f32; FFT_SIZE * 2];
        quiet_then_loud.extend(sine(440.0, sr, FFT_SIZE * 2));
        hit.in_buf = quiet_then_loud;
        let _ = hit.analyse_channel(0, 0);
        let (flux, total) = hit.analyse_channel(0, FFT_SIZE * 2 - FFT_SIZE / 2);
        assert!(flux / total > FLUX_ONSET, "a tone out of silence must count as an attack");
    }

    #[test]
    fn extreme_bounds_250_and_4000_do_not_panic_or_nan() {
        let sr = 44_100u32;
        let frames_in = sr as usize * 2;
        let input: Vec<f32> = (0..(frames_in * 2))
            .map(|i| {
                let f = (i / 2) as f64;
                (2.0 * PI * 330.0 * f / (sr as f64)).sin() as f32
            })
            .collect();

        for (tempo, pitch) in [(250u32, -1200i32), (4000u32, 1200i32), (250, 1200), (4000, -1200)] {
            let mut s = Stretcher::new(2, tempo, pitch);
            let mut r = Resampler::new(pitch_rate_hz(sr, pitch), 48_000, 2).unwrap();
            let (mut mid, mut out) = (Vec::new(), Vec::new());
            for chunk in input.chunks(2048) {
                mid.clear();
                s.process(chunk, &mut mid);
                r.process(&mid, &mut out).unwrap();
            }
            assert!(!out.is_empty(), "extreme ({tempo}, {pitch}) must produce audio");
            assert!(
                out.iter().all(|x| x.is_finite()),
                "extreme ({tempo}, {pitch}) produced non-finite samples"
            );
        }
    }
}
