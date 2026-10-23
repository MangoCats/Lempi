//! Equalizer configuration types shared between the persistence layer
//! (`lempi-core`) and the real-time biquad processor (`lempi-player`)
//! `[SPEC-EQ-050]`.
//!
//! Kept in `lempi-core` so [`crate::db::PlayerStore`] can serialize, validate,
//! and clamp per-output equalizer configurations without introducing any audio
//! dependency into the core crate `[GDE-AND-045]`.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use serde::{Deserialize, Serialize};

pub const EQ_BANDS: usize = 5;
pub const EQ_FREQ_MIN_HZ: f32 = 20.0;
pub const EQ_FREQ_MAX_HZ: f32 = 20_000.0;
pub const EQ_GAIN_MIN_DB: f32 = -12.0;
pub const EQ_GAIN_MAX_DB: f32 = 12.0;
pub const EQ_Q_MIN: f32 = 0.10;
pub const EQ_Q_MAX: f32 = 18.0;
pub const EQ_PREAMP_MIN_DB: f32 = -12.0;
pub const EQ_PREAMP_MAX_DB: f32 = 12.0;

/// The equalizer's named curves `[SPEC-EQ-085]`: gains in dB for the five
/// default bands of `[SPEC-EQ-030]`, in band order. Defined here, once, for
/// every control that offers them -- the Lempi skin and the framebuffer UI --
/// so a preset means the same curve wherever it is chosen.
pub const PRESETS: [(&str, [f32; EQ_BANDS]); 5] = [
    ("flat", [0.0, 0.0, 0.0, 0.0, 0.0]),
    ("bass", [5.0, 2.5, 0.0, 0.0, 0.0]),
    ("vocal", [-1.5, 0.0, 3.5, 2.5, 0.0]),
    ("treble", [0.0, 0.0, 0.0, 2.5, 5.0]),
    ("loudness", [4.5, 1.5, -1.0, 1.5, 4.0]),
];

/// Not a curve: the equalizer switched off `[SPEC-EQ-085]`.
pub const PRESET_OFF: &str = "off";
/// On, and not exactly any preset.
pub const PRESET_CUSTOM: &str = "custom";

/// Filter topology for one parametric equalizer band `[SPEC-EQ-030]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FilterKind {
    LowShelf,
    Peaking,
    HighShelf,
    LowPass,
    HighPass,
}

/// Parameters for one parametric equalizer band `[SPEC-EQ-030]`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct EqBand {
    pub kind: FilterKind,
    pub freq_hz: f32,
    pub gain_db: f32,
    pub q: f32,
    /// A band sent without saying is a band in use.
    #[serde(default = "band_enabled")]
    pub enabled: bool,
}

fn band_enabled() -> bool {
    true
}

impl EqBand {
    /// Clamp every numeric parameter to its valid operating range, replacing
    /// non-finite values with neutral defaults `[SPEC-EQ-030]`.
    pub fn clamped(self) -> Self {
        let freq_hz = if self.freq_hz.is_finite() {
            self.freq_hz.clamp(EQ_FREQ_MIN_HZ, EQ_FREQ_MAX_HZ)
        } else {
            1000.0
        };
        let gain_db = if self.gain_db.is_finite() {
            self.gain_db.clamp(EQ_GAIN_MIN_DB, EQ_GAIN_MAX_DB)
        } else {
            0.0
        };
        let q = if self.q.is_finite() {
            self.q.clamp(EQ_Q_MIN, EQ_Q_MAX)
        } else {
            0.707
        };
        Self {
            kind: self.kind,
            freq_hz,
            gain_db,
            q,
            enabled: self.enabled,
        }
    }

    /// True when this band leaves the signal unchanged `[SPEC-EQ-035]`.
    ///
    /// Low-pass and high-pass filters shape the signal even at `0.0 dB`, so an
    /// enabled pass filter is never a no-op.
    pub fn is_unity(&self) -> bool {
        if !self.enabled {
            return true;
        }
        match self.kind {
            FilterKind::LowShelf | FilterKind::Peaking | FilterKind::HighShelf => {
                self.gain_db.abs() < 1e-6
            }
            FilterKind::LowPass | FilterKind::HighPass => false,
        }
    }
}

/// Five-band parametric equalizer configuration `[SPEC-EQ-030]`, `[SPEC-EQ-040]`.
///
/// A field left out takes its default, so a stored profile from before a
/// field existed still loads rather than resetting to flat.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct EqConfig {
    pub enabled: bool,
    pub preamp_db: f32,
    pub auto_preamp: bool,
    pub bands: [EqBand; EQ_BANDS],
}

impl Default for EqConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            preamp_db: 0.0,
            auto_preamp: true,
            bands: [
                EqBand {
                    kind: FilterKind::LowShelf,
                    freq_hz: 60.0,
                    gain_db: 0.0,
                    q: 0.707,
                    enabled: true,
                },
                EqBand {
                    kind: FilterKind::Peaking,
                    freq_hz: 250.0,
                    gain_db: 0.0,
                    q: 0.707,
                    enabled: true,
                },
                EqBand {
                    kind: FilterKind::Peaking,
                    freq_hz: 1000.0,
                    gain_db: 0.0,
                    q: 0.707,
                    enabled: true,
                },
                EqBand {
                    kind: FilterKind::Peaking,
                    freq_hz: 4000.0,
                    gain_db: 0.0,
                    q: 0.707,
                    enabled: true,
                },
                EqBand {
                    kind: FilterKind::HighShelf,
                    freq_hz: 12_000.0,
                    gain_db: 0.0,
                    q: 0.707,
                    enabled: true,
                },
            ],
        }
    }
}

impl EqConfig {
    /// Clamp preamp and all five bands to their valid ranges `[SPEC-EQ-030]`.
    pub fn clamped(self) -> Self {
        let preamp_db = if self.preamp_db.is_finite() {
            self.preamp_db.clamp(EQ_PREAMP_MIN_DB, EQ_PREAMP_MAX_DB)
        } else {
            0.0
        };
        Self {
            enabled: self.enabled,
            preamp_db,
            auto_preamp: self.auto_preamp,
            bands: self.bands.map(EqBand::clamped),
        }
    }

    /// This configuration with a named curve `[SPEC-EQ-085]`: the default
    /// bands at that preset's gains, preamp zeroed, `auto_preamp` kept. `off`
    /// switches the equalizer off and keeps whatever curve it had; `flat`
    /// resets the curve and leaves it on or off as it was; any other preset
    /// switches it on. `None` names no preset.
    pub fn with_preset(self, name: &str) -> Option<Self> {
        if name == PRESET_OFF {
            return Some(Self { enabled: false, ..self });
        }
        let (_, gains) = PRESETS.iter().find(|(n, _)| *n == name)?;
        let mut bands = Self::default().bands;
        for (band, gain) in bands.iter_mut().zip(gains) {
            band.gain_db = *gain;
        }
        Some(Self {
            enabled: if name == "flat" { self.enabled } else { true },
            preamp_db: 0.0,
            auto_preamp: self.auto_preamp,
            bands,
        })
    }

    /// Which preset this configuration is, for a control that can only show
    /// a name: `off` when disabled, a preset's name when it is exactly that
    /// curve, else `custom` `[SPEC-EQ-085]`.
    pub fn preset_name(&self) -> &'static str {
        if !self.enabled {
            return PRESET_OFF;
        }
        if self.preamp_db.abs() > 1e-3 {
            return PRESET_CUSTOM;
        }
        let defaults = Self::default().bands;
        let is = |gains: &[f32; EQ_BANDS]| {
            self.bands.iter().zip(&defaults).zip(gains).all(|((b, d), g)| {
                b.enabled
                    && b.kind == d.kind
                    && (b.freq_hz - d.freq_hz).abs() < 0.5
                    && (b.q - d.q).abs() < 1e-3
                    && (b.gain_db - g).abs() < 0.05
            })
        };
        PRESETS.iter().find(|(_, gains)| is(gains)).map_or(PRESET_CUSTOM, |(name, _)| name)
    }

    /// True when the equalizer is disabled or every stage is unity `[SPEC-EQ-035]`.
    pub fn is_flat(&self) -> bool {
        if !self.enabled {
            return true;
        }
        self.preamp_db.abs() < 1e-6 && self.bands.iter().all(EqBand::is_unity)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_is_flat_and_matches_spec_bands() {
        let cfg = EqConfig::default();
        assert!(!cfg.enabled);
        assert_eq!(cfg.preamp_db, 0.0);
        assert!(cfg.auto_preamp);
        assert!(cfg.is_flat());
        assert_eq!(cfg.bands[0].kind, FilterKind::LowShelf);
        assert_eq!(cfg.bands[0].freq_hz, 60.0);
        assert_eq!(cfg.bands[1].freq_hz, 250.0);
        assert_eq!(cfg.bands[2].freq_hz, 1000.0);
        assert_eq!(cfg.bands[3].freq_hz, 4000.0);
        assert_eq!(cfg.bands[4].kind, FilterKind::HighShelf);
        assert_eq!(cfg.bands[4].freq_hz, 12_000.0);
    }

    #[test]
    fn every_preset_names_itself_and_off_keeps_the_curve() {
        for (name, gains) in PRESETS {
            let cfg = EqConfig::default().with_preset(name).unwrap();
            let want = if name == "flat" { PRESET_OFF } else { name };
            assert_eq!(cfg.preset_name(), want, "{name}");
            assert!(cfg.bands.iter().zip(gains).all(|(b, g)| b.gain_db == g));
        }
        let on = EqConfig { enabled: true, ..EqConfig::default() }.with_preset("flat").unwrap();
        assert_eq!(on.preset_name(), "flat", "flat leaves an enabled equalizer on");

        let bass = EqConfig::default().with_preset("bass").unwrap();
        let off = bass.with_preset(PRESET_OFF).unwrap();
        assert_eq!(off.preset_name(), PRESET_OFF);
        assert_eq!(off.bands, bass.bands, "off keeps the curve to come back to");

        let mut nudged = bass;
        nudged.bands[2].gain_db += 1.0;
        assert_eq!(nudged.preset_name(), PRESET_CUSTOM);
        let manual = EqConfig { auto_preamp: false, ..bass };
        assert!(!manual.with_preset("vocal").unwrap().auto_preamp, "auto_preamp is the listener's");
        assert!(EqConfig::default().with_preset("disco").is_none());
    }

    #[test]
    fn enabled_zero_gain_shelving_and_peaking_is_still_flat() {
        let mut cfg = EqConfig { enabled: true, ..EqConfig::default() };
        assert!(cfg.is_flat(), "all-zero gains with zero preamp is a no-op");

        cfg.bands[0].kind = FilterKind::HighPass;
        assert!(!cfg.is_flat(), "an enabled high-pass filter is never flat");
    }

    #[test]
    fn clamping_bounds_all_parameters_and_sanitizes_nan() {
        let mut cfg = EqConfig {
            enabled: true,
            preamp_db: 99.0,
            auto_preamp: false,
            bands: [EqBand {
                kind: FilterKind::Peaking,
                freq_hz: 5.0,
                gain_db: -40.0,
                q: 100.0,
                enabled: true,
            }; EQ_BANDS],
        };
        cfg.bands[1].freq_hz = f32::NAN;
        cfg.bands[1].gain_db = f32::INFINITY;
        cfg.bands[1].q = f32::NEG_INFINITY;

        let c = cfg.clamped();
        assert_eq!(c.preamp_db, EQ_PREAMP_MAX_DB);
        assert_eq!(c.bands[0].freq_hz, EQ_FREQ_MIN_HZ);
        assert_eq!(c.bands[0].gain_db, EQ_GAIN_MIN_DB);
        assert_eq!(c.bands[0].q, EQ_Q_MAX);
        assert_eq!(c.bands[1].freq_hz, 1000.0);
        assert_eq!(c.bands[1].gain_db, 0.0);
        assert_eq!(c.bands[1].q, 0.707);
    }
}
