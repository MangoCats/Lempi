//! Real-time 5-band parametric equalizer using Cytomic linear trapezoidal
//! integrated state-variable filters (SVF) `[SPEC-EQ-010]`, `[SPEC-EQ-035]`.
//!
//! Runs inside [`crate::output`]'s callback after releasing the ring buffer
//! lock so equalizer adjustments take effect on the next audio callback period
//! rather than ~14 seconds of ring buffer later, and never lengthen the time
//! the ring lock is held `[SPEC-EQ-015]`.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::f32::consts::PI;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::Mutex;

pub use lempi_core::eq::{
    EqBand, EqConfig, FilterKind, EQ_BANDS, EQ_FREQ_MAX_HZ, EQ_FREQ_MIN_HZ, EQ_GAIN_MAX_DB,
    EQ_GAIN_MIN_DB, EQ_PREAMP_MAX_DB, EQ_PREAMP_MIN_DB, EQ_Q_MAX, EQ_Q_MIN,
};

/// Number of logarithmically spaced frequency points from 20 Hz to 20 kHz
/// used for auto-preamp headroom calculation and UI curve rendering
/// `[SPEC-EQ-040]`.
pub const EQ_CURVE_POINTS: usize = 64;

/// Maximum interleaved channel count supported by the in-place SVF state bank.
const MAX_CHANNELS: usize = 8;

/// Cytomic linear trapezoidal integrated SVF coefficients for one band
/// `[SPEC-EQ-035]`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SvfCoeffs {
    pub g: f32,
    pub k: f32,
    pub a1: f32,
    pub a2: f32,
    pub a3: f32,
    pub m0: f32,
    pub m1: f32,
    pub m2: f32,
    pub enabled: bool,
}

impl Default for SvfCoeffs {
    fn default() -> Self {
        Self::unity()
    }
}

impl SvfCoeffs {
    pub const fn unity() -> Self {
        Self {
            g: 0.0,
            k: 1.0,
            a1: 0.0,
            a2: 0.0,
            a3: 0.0,
            m0: 1.0,
            m1: 0.0,
            m2: 0.0,
            enabled: false,
        }
    }

    /// Compute Cytomic SVF coefficients for `band` at `sample_rate`
    /// `[SPEC-EQ-035]`.
    pub fn from_band(band: EqBand, sample_rate: u32) -> Self {
        let b = band.clamped();
        if b.is_unity() {
            return Self::unity();
        }
        let fs = sample_rate.max(8_000) as f32;
        // Clamp normalized frequency strictly below Nyquist to keep tan(w)
        // well-conditioned even if a 20 kHz band runs on a 44.1 kHz stream.
        let ratio = (b.freq_hz / fs).clamp(0.0001, 0.495);
        let w = PI * ratio;
        let tan_w = w.tan();
        let q = b.q;

        match b.kind {
            FilterKind::LowPass => {
                let g = tan_w;
                let k = 1.0 / q;
                let a1 = 1.0 / (1.0 + g * (g + k));
                let a2 = g * a1;
                let a3 = g * a2;
                Self { g, k, a1, a2, a3, m0: 0.0, m1: 0.0, m2: 1.0, enabled: true }
            }
            FilterKind::HighPass => {
                let g = tan_w;
                let k = 1.0 / q;
                let a1 = 1.0 / (1.0 + g * (g + k));
                let a2 = g * a1;
                let a3 = g * a2;
                Self { g, k, a1, a2, a3, m0: 1.0, m1: -k, m2: -1.0, enabled: true }
            }
            FilterKind::Peaking => {
                let a = 10.0_f32.powf(b.gain_db / 40.0);
                let g = tan_w;
                let k = 1.0 / (q * a);
                let a1 = 1.0 / (1.0 + g * (g + k));
                let a2 = g * a1;
                let a3 = g * a2;
                let m1 = k * (a * a - 1.0);
                Self { g, k, a1, a2, a3, m0: 1.0, m1, m2: 0.0, enabled: true }
            }
            FilterKind::LowShelf => {
                let a = 10.0_f32.powf(b.gain_db / 40.0);
                let g = tan_w / a.sqrt();
                let k = 1.0 / q;
                let a1 = 1.0 / (1.0 + g * (g + k));
                let a2 = g * a1;
                let a3 = g * a2;
                let m1 = k * (a - 1.0);
                let m2 = a * a - 1.0;
                Self { g, k, a1, a2, a3, m0: 1.0, m1, m2, enabled: true }
            }
            FilterKind::HighShelf => {
                let a = 10.0_f32.powf(b.gain_db / 40.0);
                let g = tan_w * a.sqrt();
                let k = 1.0 / q;
                let a1 = 1.0 / (1.0 + g * (g + k));
                let a2 = g * a1;
                let a3 = g * a2;
                let m0 = a * a;
                let m1 = k * (1.0 - a) * a;
                let m2 = 1.0 - a * a;
                Self { g, k, a1, a2, a3, m0, m1, m2, enabled: true }
            }
        }
    }

    /// Magnitude response of this single SVF stage at `freq_hz` in dB
    /// `[SPEC-EQ-040]`.
    pub fn magnitude_db(&self, freq_hz: f32, sample_rate: u32) -> f32 {
        if !self.enabled || self.g <= 0.0 {
            return 0.0;
        }
        let fs = sample_rate.max(8_000) as f32;
        let ratio = (freq_hz / fs).clamp(0.00001, 0.499);
        let r = (PI * ratio).tan() / self.g;
        let r2 = r * r;
        let num_re = self.m0 * (1.0 - r2) + self.m2;
        let num_im = r * (self.m0 * self.k + self.m1);
        let den_re = 1.0 - r2;
        let den_im = self.k * r;
        let mag_sq = (num_re * num_re + num_im * num_im)
            / (den_re * den_re + den_im * den_im).max(1e-30);
        10.0 * mag_sq.max(1e-12).log10()
    }
}

/// Logarithmically spaced frequency for index `idx` in `0..EQ_CURVE_POINTS`
/// (`20 Hz` to `20 kHz`) `[SPEC-EQ-040]`.
pub fn curve_freq_hz(idx: usize) -> f32 {
    let t = (idx.min(EQ_CURVE_POINTS - 1) as f32) / ((EQ_CURVE_POINTS - 1) as f32);
    EQ_FREQ_MIN_HZ * (EQ_FREQ_MAX_HZ / EQ_FREQ_MIN_HZ).powf(t)
}

/// Compute the 64-point logarithmic frequency grid (`20 Hz..=20 kHz`)
/// `[SPEC-EQ-040]`.
pub fn curve_frequencies_hz() -> [f32; EQ_CURVE_POINTS] {
    std::array::from_fn(curve_freq_hz)
}

/// Compute the effective preamp in dB (accounting for `auto_preamp` peak boost
/// across the 64-point log grid) and the 64-point composite frequency response
/// curve in dB `[SPEC-EQ-040]`.
pub fn compute_response(cfg: &EqConfig, sample_rate: u32) -> (f32, [f32; EQ_CURVE_POINTS]) {
    let clamped = cfg.clamped();
    let sections: [SvfCoeffs; EQ_BANDS] =
        std::array::from_fn(|i| SvfCoeffs::from_band(clamped.bands[i], sample_rate));

    let mut band_sums = [0.0_f32; EQ_CURVE_POINTS];
    let mut peak_boost_db = 0.0_f32;
    for (idx, sum_out) in band_sums.iter_mut().enumerate() {
        let f = curve_freq_hz(idx);
        let sum: f32 = sections.iter().map(|s| s.magnitude_db(f, sample_rate)).sum();
        *sum_out = sum;
        if sum > peak_boost_db {
            peak_boost_db = sum;
        }
    }

    let effective_preamp_db = if clamped.auto_preamp {
        clamped.preamp_db - peak_boost_db
    } else {
        clamped.preamp_db
    };

    if !clamped.enabled {
        return (effective_preamp_db, [0.0; EQ_CURVE_POINTS]);
    }

    let mut curve = [0.0_f32; EQ_CURVE_POINTS];
    for i in 0..EQ_CURVE_POINTS {
        curve[i] = effective_preamp_db + band_sums[i];
    }
    (effective_preamp_db, curve)
}

#[derive(Default, Clone, Copy)]
struct SvfState {
    ic1eq: f32,
    ic2eq: f32,
}

#[derive(Clone, Copy)]
struct CompiledBand {
    a1: f32,
    a2: f32,
    a3: f32,
    m0: f32,
    m1: f32,
    m2: f32,
    enabled: bool,
}

impl Default for CompiledBand {
    fn default() -> Self {
        Self {
            a1: 0.0,
            a2: 0.0,
            a3: 0.0,
            m0: 1.0,
            m1: 0.0,
            m2: 0.0,
            enabled: false,
        }
    }
}

#[derive(Clone, Copy)]
struct CompiledEq {
    preamp_gain: f32,
    bands: [CompiledBand; EQ_BANDS],
    sample_rate: u32,
    channels: usize,
}

impl Default for CompiledEq {
    fn default() -> Self {
        Self {
            preamp_gain: 1.0,
            bands: [CompiledBand::default(); EQ_BANDS],
            sample_rate: 44_100,
            channels: 2,
        }
    }
}

struct AtomicBandWords {
    a1: AtomicU32,
    a2: AtomicU32,
    a3: AtomicU32,
    m0: AtomicU32,
    m1: AtomicU32,
    m2: AtomicU32,
    enabled: AtomicBool,
}

impl Default for AtomicBandWords {
    fn default() -> Self {
        Self {
            a1: AtomicU32::new(0.0_f32.to_bits()),
            a2: AtomicU32::new(0.0_f32.to_bits()),
            a3: AtomicU32::new(0.0_f32.to_bits()),
            m0: AtomicU32::new(1.0_f32.to_bits()),
            m1: AtomicU32::new(0.0_f32.to_bits()),
            m2: AtomicU32::new(0.0_f32.to_bits()),
            enabled: AtomicBool::new(false),
        }
    }
}

impl AtomicBandWords {
    fn store(&self, b: &CompiledBand) {
        self.a1.store(b.a1.to_bits(), Ordering::Relaxed);
        self.a2.store(b.a2.to_bits(), Ordering::Relaxed);
        self.a3.store(b.a3.to_bits(), Ordering::Relaxed);
        self.m0.store(b.m0.to_bits(), Ordering::Relaxed);
        self.m1.store(b.m1.to_bits(), Ordering::Relaxed);
        self.m2.store(b.m2.to_bits(), Ordering::Relaxed);
        self.enabled.store(b.enabled, Ordering::Relaxed);
    }

    fn load(&self) -> CompiledBand {
        CompiledBand {
            a1: f32::from_bits(self.a1.load(Ordering::Relaxed)),
            a2: f32::from_bits(self.a2.load(Ordering::Relaxed)),
            a3: f32::from_bits(self.a3.load(Ordering::Relaxed)),
            m0: f32::from_bits(self.m0.load(Ordering::Relaxed)),
            m1: f32::from_bits(self.m1.load(Ordering::Relaxed)),
            m2: f32::from_bits(self.m2.load(Ordering::Relaxed)),
            enabled: self.enabled.load(Ordering::Relaxed),
        }
    }
}

struct ControlState {
    config: EqConfig,
    sample_rate: u32,
    channels: usize,
}

struct AudioState {
    z: [[SvfState; EQ_BANDS]; MAX_CHANNELS],
    cached: CompiledEq,
    seen_seq: u64,
    /// How much of the filtered signal is heard, 0.0 (dry) to 1.0 (wet).
    /// Ramped, never stepped: switching the equalizer on or off with a
    /// non-zero preamp is otherwise a level step mid-waveform -- a click.
    wet: f32,
    /// The preamp gain actually applied, gliding toward `cached.preamp_gain`
    /// so an `auto_preamp` change during a drag is not a step either.
    preamp_now: f32,
}

impl Default for AudioState {
    fn default() -> Self {
        Self {
            z: [[SvfState::default(); EQ_BANDS]; MAX_CHANNELS],
            cached: CompiledEq::default(),
            seen_seq: 0,
            wet: 0.0,
            preamp_now: 1.0,
        }
    }
}

/// How long switching the equalizer in or out takes, dry to wet or back.
const SWITCH_RAMP_MS: f32 = 20.0;
/// Time constant of the preamp glide.
const PREAMP_GLIDE_MS: f32 = 5.0;

/// Lock-free 5-band parametric equalizer processor shared between the control
/// plane and the audio output callback `[SPEC-EQ-015]`.
pub struct EqProcessor {
    /// Fast-path flag checked by the audio callback before touching any state.
    bypass: AtomicBool,
    /// Sequence counter: odd while a writer is updating coefficients, even
    /// when quiescent.
    seq: AtomicU64,
    preamp_gain: AtomicU32,
    sample_rate: AtomicU32,
    channels: AtomicU32,
    bands: [AtomicBandWords; EQ_BANDS],
    /// Control-side state (never locked by the audio callback).
    control: Mutex<ControlState>,
    /// Audio-callback-only filter delay state (never locked by the control
    /// plane, only accessed via `try_lock` inside `apply`).
    audio: Mutex<AudioState>,
}

impl Default for EqProcessor {
    fn default() -> Self {
        Self::new()
    }
}

impl EqProcessor {
    pub fn new() -> Self {
        Self {
            bypass: AtomicBool::new(true),
            seq: AtomicU64::new(2),
            preamp_gain: AtomicU32::new(1.0_f32.to_bits()),
            sample_rate: AtomicU32::new(44_100),
            channels: AtomicU32::new(2),
            bands: std::array::from_fn(|_| AtomicBandWords::default()),
            control: Mutex::new(ControlState {
                config: EqConfig::default(),
                sample_rate: 44_100,
                channels: 2,
            }),
            audio: Mutex::new(AudioState::default()),
        }
    }

    /// Update the equalizer configuration and publish recomputed biquad
    /// coefficients atomically `[SPEC-EQ-015]`.
    pub fn set_config(&self, config: EqConfig) {
        let clamped = config.clamped();
        if let Ok(mut ctl) = self.control.lock() {
            ctl.config = clamped;
            self.publish_locked(&ctl);
        }
    }

    /// Update the active stream's sample rate and channel count, recomputing
    /// coefficients for the new rate `[SPEC-EQ-020]`.
    pub fn set_stream_format(&self, sample_rate: u32, channels: usize) {
        let sr = sample_rate.max(8_000);
        let ch = channels.clamp(1, MAX_CHANNELS);
        if let Ok(mut ctl) = self.control.lock() {
            ctl.sample_rate = sr;
            ctl.channels = ch;
            self.publish_locked(&ctl);
        }
    }

    /// Current equalizer configuration `[SPEC-EQ-030]`.
    pub fn config(&self) -> EqConfig {
        self.control.lock().map(|c| c.config).unwrap_or_default()
    }

    /// Current stream sample rate used for filter synthesis.
    pub fn sample_rate(&self) -> u32 {
        self.sample_rate.load(Ordering::Relaxed)
    }

    /// Compute the effective preamp in dB and 64-point composite response
    /// curve at the current stream sample rate `[SPEC-EQ-040]`.
    pub fn response_curve_db(&self) -> (f32, [f32; EQ_CURVE_POINTS]) {
        let (cfg, sr) = self
            .control
            .lock()
            .map(|c| (c.config, c.sample_rate))
            .unwrap_or((EqConfig::default(), 44_100));
        compute_response(&cfg, sr)
    }

    fn publish_locked(&self, ctl: &ControlState) {
        let cfg = &ctl.config;
        let sr = ctl.sample_rate;
        let ch = ctl.channels;

        if cfg.is_flat() {
            self.sample_rate.store(sr, Ordering::Relaxed);
            self.channels.store(ch as u32, Ordering::Relaxed);
            self.bypass.store(true, Ordering::Release);
            return;
        }

        let (effective_preamp_db, _) = compute_response(cfg, sr);
        let preamp_gain = 10.0_f32.powf(effective_preamp_db / 20.0);

        let compiled_bands: [CompiledBand; EQ_BANDS] = std::array::from_fn(|i| {
            let s = SvfCoeffs::from_band(cfg.bands[i], sr);
            CompiledBand {
                a1: s.a1,
                a2: s.a2,
                a3: s.a3,
                m0: s.m0,
                m1: s.m1,
                m2: s.m2,
                enabled: s.enabled,
            }
        });

        // A seqlock. Writers are serialized by `control`, so `seq` is even on
        // entry. **The fences are not optional on aarch64**, which is what every
        // appliance runs: a Release *store* orders only what precedes it, so
        // without the fence below the relaxed data stores may become visible
        // before the odd marker, and the reader could take a half-written set
        // of coefficients as whole. x86 hides this; the Pis do not.
        let s0 = self.seq.load(Ordering::Relaxed);
        self.seq.store(s0.wrapping_add(1), Ordering::Relaxed);
        std::sync::atomic::fence(Ordering::Release);

        self.preamp_gain.store(preamp_gain.to_bits(), Ordering::Relaxed);
        self.sample_rate.store(sr, Ordering::Relaxed);
        self.channels.store(ch as u32, Ordering::Relaxed);
        for (slot, b) in self.bands.iter().zip(compiled_bands.iter()) {
            slot.store(b);
        }

        self.seq.store(s0.wrapping_add(2), Ordering::Release);
        self.bypass.store(false, Ordering::Release);
    }

    fn try_read_compiled(&self) -> Option<(u64, CompiledEq)> {
        let s1 = self.seq.load(Ordering::Acquire);
        if s1 & 1 == 1 {
            return None;
        }
        let preamp_gain = f32::from_bits(self.preamp_gain.load(Ordering::Relaxed));
        let sample_rate = self.sample_rate.load(Ordering::Relaxed);
        let channels = (self.channels.load(Ordering::Relaxed) as usize).clamp(1, MAX_CHANNELS);
        let bands = std::array::from_fn(|i| self.bands[i].load());
        // The reader's half of the same argument: an Acquire *load* would not
        // stop the relaxed loads above from moving after it.
        std::sync::atomic::fence(Ordering::Acquire);
        let s2 = self.seq.load(Ordering::Relaxed);
        if s1 != s2 {
            return None;
        }
        Some((
            s1,
            CompiledEq {
                preamp_gain,
                bands,
                sample_rate,
                channels,
            },
        ))
    }

    /// Process interleaved `f32` samples in-place `[SPEC-EQ-010]`,
    /// `[SPEC-EQ-015]`.
    ///
    /// When the equalizer is disabled or flat, and has finished ramping out,
    /// returns without touching `samples` -- bit-identical `[SPEC-EQ-035]`.
    ///
    /// Switching in or out crossfades dry and filtered over
    /// [`SWITCH_RAMP_MS`]; a coefficient change keeps the filter state, so a
    /// live drag is continuous `[SPEC-EQ-015]`.
    pub fn apply(&self, samples: &mut [f32]) {
        if samples.is_empty() {
            return;
        }
        let bypass = self.bypass.load(Ordering::Acquire);
        // Only this callback ever takes `audio`, so this does not fail in
        // practice; if it ever did, one block passes dry rather than waiting.
        let Ok(mut guard) = self.audio.try_lock() else {
            return;
        };
        let st = &mut *guard;
        if bypass && st.wet == 0.0 {
            return;
        }

        if !bypass {
            if let Some((seq, fresh)) = self.try_read_compiled() {
                if seq != st.seen_seq {
                    let reformatted = st.cached.sample_rate != fresh.sample_rate
                        || st.cached.channels != fresh.channels;
                    // Coming in from dry, the old state belongs to a filter
                    // that stopped sounding; carrying it over would replay it.
                    if st.wet == 0.0 || reformatted {
                        st.z = [[SvfState::default(); EQ_BANDS]; MAX_CHANNELS];
                    }
                    if st.wet == 0.0 {
                        st.preamp_now = fresh.preamp_gain;
                    }
                    st.cached = fresh;
                    st.seen_seq = seq;
                }
            }
        }

        let ch = st.cached.channels.clamp(1, MAX_CHANNELS);
        let sr = st.cached.sample_rate.max(8_000) as f32;
        let bands = st.cached.bands;
        let preamp_to = st.cached.preamp_gain;
        let glide = 1.0 - (-1000.0 / (PREAMP_GLIDE_MS * sr)).exp();
        let step = 1000.0 / (SWITCH_RAMP_MS * sr);
        let target = if bypass { 0.0 } else { 1.0 };

        for frame in samples.chunks_exact_mut(ch) {
            if st.wet != target {
                st.wet = if target > st.wet {
                    (st.wet + step).min(1.0)
                } else {
                    (st.wet - step).max(0.0)
                };
            }
            st.preamp_now += (preamp_to - st.preamp_now) * glide;
            let (wet, preamp) = (st.wet, st.preamp_now);
            for (c, sample) in frame.iter_mut().enumerate() {
                let dry = *sample;
                let mut v0 = dry * preamp;
                let z_ch = &mut st.z[c];
                for (b, z) in bands.iter().zip(z_ch.iter_mut()) {
                    if !b.enabled {
                        continue;
                    }
                    let v3 = v0 - z.ic2eq;
                    let v1 = b.a1 * z.ic1eq + b.a2 * v3;
                    let v2 = z.ic2eq + b.a2 * z.ic1eq + b.a3 * v3;
                    z.ic1eq = 2.0 * v1 - z.ic1eq;
                    z.ic2eq = 2.0 * v2 - z.ic2eq;
                    v0 = b.m0 * v0 + b.m1 * v1 + b.m2 * v2;
                }
                *sample = if wet >= 1.0 { v0 } else { dry + (v0 - dry) * wet };
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn measure_sine_gain_db(proc: &EqProcessor, freq_hz: f32, sample_rate: u32) -> f32 {
        let frames = sample_rate as usize; // 1 second
        let mut buf = Vec::with_capacity(frames * 2);
        for n in 0..frames {
            let s = (2.0 * PI * freq_hz * (n as f32) / (sample_rate as f32)).sin() * 0.25;
            buf.push(s);
            buf.push(s);
        }
        let steady_in_sq: f32 = buf[frames..].iter().map(|&v| v * v).sum();
        proc.apply(&mut buf);
        // Measure RMS ratio over the steady-state second half
        let steady_out_sq: f32 = buf[frames..].iter().map(|&v| v * v).sum();
        10.0 * (steady_out_sq / steady_in_sq.max(1e-20)).max(1e-18).log10()
    }

    #[test]
    fn bypass_when_disabled_or_flat_is_bit_exact() {
        let eq = EqProcessor::new();
        let orig = [0.123_456_f32, -0.654_321, 0.5, -0.5, 0.0, 0.99];
        let mut buf = orig;
        eq.apply(&mut buf);
        assert_eq!(buf, orig, "disabled EQ must not alter a single bit");

        let cfg = EqConfig { enabled: true, ..EqConfig::default() }; // all gains 0 dB, preamp 0 dB
        eq.set_config(cfg);
        eq.apply(&mut buf);
        assert_eq!(buf, orig, "enabled all-zero EQ must also bypass bit-identically");
    }

    #[test]
    fn peaking_boost_and_cut_match_analytical_gain() {
        let eq = EqProcessor::new();
        eq.set_stream_format(48_000, 2);

        let mut cfg = EqConfig { enabled: true, auto_preamp: false, ..EqConfig::default() };
        cfg.bands[2].freq_hz = 1000.0;
        cfg.bands[2].gain_db = 9.0;
        cfg.bands[2].q = 2.0;
        eq.set_config(cfg);

        let gain_1k = measure_sine_gain_db(&eq, 1000.0, 48_000);
        let gain_100 = measure_sine_gain_db(&eq, 100.0, 48_000);
        assert!(
            (gain_1k - 9.0).abs() < 0.15,
            "expected +9.0 dB at 1 kHz, measured {gain_1k:.2} dB"
        );
        assert!(
            gain_100.abs() < 0.15,
            "expected ~0.0 dB far from center at 100 Hz, measured {gain_100:.2} dB"
        );

        // Now test a -9 dB cut at 1 kHz
        cfg.bands[2].gain_db = -9.0;
        eq.set_config(cfg);
        let cut_1k = measure_sine_gain_db(&eq, 1000.0, 48_000);
        assert!(
            (cut_1k - (-9.0)).abs() < 0.15,
            "expected -9.0 dB at 1 kHz, measured {cut_1k:.2} dB"
        );
    }

    #[test]
    fn shelving_and_pass_filters_behave_as_specified() {
        let eq = EqProcessor::new();
        eq.set_stream_format(48_000, 2);

        // Low-shelf +6 dB below 200 Hz
        let mut cfg = EqConfig { enabled: true, auto_preamp: false, ..EqConfig::default() };
        cfg.bands[0] = EqBand {
            kind: FilterKind::LowShelf,
            freq_hz: 200.0,
            gain_db: 6.0,
            q: 0.707,
            enabled: true,
        };
        eq.set_config(cfg);
        let low = measure_sine_gain_db(&eq, 30.0, 48_000);
        let high = measure_sine_gain_db(&eq, 4000.0, 48_000);
        assert!((low - 6.0).abs() < 0.25, "low shelf at 30 Hz gave {low:.2} dB");
        assert!(high.abs() < 0.15, "low shelf at 4 kHz gave {high:.2} dB");

        // High-pass at 1000 Hz attenuates 100 Hz strongly
        let mut hp_cfg = EqConfig { enabled: true, auto_preamp: false, ..EqConfig::default() };
        hp_cfg.bands[0] = EqBand {
            kind: FilterKind::HighPass,
            freq_hz: 1000.0,
            gain_db: 0.0,
            q: 0.707,
            enabled: true,
        };
        eq.set_config(hp_cfg);
        let hp_100 = measure_sine_gain_db(&eq, 100.0, 48_000);
        let hp_8k = measure_sine_gain_db(&eq, 8000.0, 48_000);
        assert!(hp_100 < -30.0, "high-pass at 100 Hz should attenuate >30 dB, got {hp_100:.2} dB");
        assert!(hp_8k.abs() < 0.2, "high-pass passband at 8 kHz got {hp_8k:.2} dB");
    }

    #[test]
    fn auto_preamp_prevents_positive_net_boost_across_curve() {
        let mut cfg = EqConfig { enabled: true, auto_preamp: true, preamp_db: 0.0, ..EqConfig::default() };
        cfg.bands[0].gain_db = 8.0;
        cfg.bands[2].gain_db = 12.0;

        let (eff_preamp, curve) = compute_response(&cfg, 44_100);
        assert!(
            eff_preamp <= -11.5,
            "effective preamp should offset the ~12 dB boost, got {eff_preamp:.2} dB"
        );
        let max_db = curve.iter().copied().fold(f32::NEG_INFINITY, f32::max);
        assert!(
            max_db <= 1e-4,
            "auto_preamp must cap peak composite response at 0.0 dB, got {max_db:.4} dB"
        );
    }

    #[test]
    fn live_coefficient_swap_preserves_filter_state_without_click() {
        let eq = EqProcessor::new();
        eq.set_stream_format(48_000, 2);

        let mut cfg = EqConfig { enabled: true, auto_preamp: false, ..EqConfig::default() };
        cfg.bands[2].gain_db = 3.0;
        eq.set_config(cfg);

        let mut phase = 0.0_f32;
        let dphi = 2.0 * PI * 440.0 / 48_000.0;
        let mut prev = 0.0_f32;

        // Warm up filter state
        for _ in 0..10 {
            let mut block = [0.0_f32; 256];
            for frame in block.chunks_exact_mut(2) {
                let s = phase.sin() * 0.4;
                frame[0] = s;
                frame[1] = s;
                phase += dphi;
            }
            eq.apply(&mut block);
            prev = block[block.len() - 2];
        }

        // Tweak gain mid-stream and check first sample transition has no transient spike
        cfg.bands[2].gain_db = 4.5;
        eq.set_config(cfg);

        let mut next_block = [0.0_f32; 256];
        for frame in next_block.chunks_exact_mut(2) {
            let s = phase.sin() * 0.4;
            frame[0] = s;
            frame[1] = s;
            phase += dphi;
        }
        eq.apply(&mut next_block);
        let step = (next_block[0] - prev).abs();
        assert!(
            step < 0.15,
            "live EQ update caused a discontinuity step of {step:.4}"
        );
    }

    /// Switching in or out is a ramp, not a step, and the dry signal comes
    /// back bit-identical once the ramp is done.
    #[test]
    fn switching_in_and_out_ramps_and_ends_bit_exact() {
        let eq = EqProcessor::new();
        eq.set_stream_format(48_000, 1);
        let dc = |n| vec![0.8_f32; n];

        let mut cfg = EqConfig { enabled: true, auto_preamp: false, preamp_db: -12.0, ..EqConfig::default() };
        eq.set_config(cfg);
        let mut on = dc(4_800);
        eq.apply(&mut on);
        let worst = on.windows(2).map(|w| (w[1] - w[0]).abs()).fold(0.0, f32::max);
        assert!(worst < 0.01, "switching in stepped by {worst}");
        let quiet = 0.8 * 10f32.powf(-12.0 / 20.0);
        assert!((on[on.len() - 1] - quiet).abs() < 1e-3, "got {}", on[on.len() - 1]);

        cfg.enabled = false;
        eq.set_config(cfg);
        let mut off = dc(4_800);
        eq.apply(&mut off);
        let worst = off.windows(2).map(|w| (w[1] - w[0]).abs()).fold(0.0, f32::max);
        assert!(worst < 0.01, "switching out stepped by {worst}");
        let mut after = dc(64);
        eq.apply(&mut after);
        assert_eq!(after, dc(64), "once ramped out, the dry path is untouched");
    }
}
