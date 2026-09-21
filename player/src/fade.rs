//! Fade curves. **The only place in the player where fade math lives.**
//!
//! Per `[XFD-ORTH-020]`, the mixer never computes a curve -- it sums audio to
//! which fades have already been applied. Keeping every curve here means a
//! crossfade bug has exactly one place to be.
//!
//! Fade-out is not a separate family of curves: it is a fade-in evaluated on
//! remaining progress. `[XFD-CURV-020]` lists exponential fade-in and
//! `[XFD-CURV-030]` logarithmic fade-out, and for a linear-in-decibels fade
//! those are the same function mirrored --
//!
//! ```text
//!   fade-in   g(t) = 10^(-D(1-t)/20)      slow start, fast finish
//!   fade-out  g(t) = g_in(1-t) = 10^(-Dt/20)   fast start, slow finish
//! ```
//!
//! so one function and a mirrored argument covers both, and the two can never
//! drift apart.

/// Fade shape. Named for the fade-**in** sense; see [`Curve::gain_out`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Curve {
    /// Constant rate of change. Precise and predictable `[XFD-LIN-010]`.
    Linear,
    /// Smooth acceleration and deceleration. Gentle and musical `[XFD-COS-010]`.
    Cosine,
    /// Linear in decibels: slow start, fast finish on the way in; fast start,
    /// slow finish on the way out `[XFD-EXP-010]`, `[XFD-EXP-020]`.
    Exponential,
}

/// Depth of the exponential curve, in dB below unity at the silent end.
/// 60 dB is the conventional floor for a musical fade.
const EXP_DEPTH_DB: f32 = 60.0;

impl Curve {
    /// Gain for a fade-**in** at normalised progress `t`, clamped to `[0, 1]`.
    ///
    /// `gain_in(0.0) == 0.0` and `gain_in(1.0) == 1.0` for every curve, so a
    /// fade always starts silent and ends at unity regardless of shape.
    pub fn gain_in(self, t: f32) -> f32 {
        let t = t.clamp(0.0, 1.0);
        match self {
            Curve::Linear => t,
            Curve::Cosine => (1.0 - (std::f32::consts::PI * t).cos()) * 0.5,
            // 10^(-D(1-t)/20), shifted so t == 0 is exactly silent rather than
            // -60 dB. Without the shift a "fade in" would begin audibly.
            Curve::Exponential => {
                let floor = 10f32.powf(-EXP_DEPTH_DB / 20.0);
                let raw = 10f32.powf(-EXP_DEPTH_DB * (1.0 - t) / 20.0);
                ((raw - floor) / (1.0 - floor)).clamp(0.0, 1.0)
            }
        }
    }

    /// Gain for a fade-**out** at normalised progress `t`.
    ///
    /// Defined as the mirror of [`Curve::gain_in`], which is why `Exponential`
    /// serves as the logarithmic fade-out of `[XFD-EXP-020]`.
    pub fn gain_out(self, t: f32) -> f32 {
        self.gain_in(1.0 - t)
    }

    /// The name this curve is stored and sent as -- `passages.fade_in_curve`/
    /// `fade_out_curve`, and the wire shape `/edit/:id/review` accepts
    /// `[SPEC-SUI-226]`. One name per curve, used for both directions: a
    /// curve is a shape, not a direction -- `gain_out` is already `gain_in`
    /// mirrored, not a fourth variant.
    pub fn as_str(self) -> &'static str {
        match self {
            Curve::Linear => "linear",
            Curve::Cosine => "cosine",
            Curve::Exponential => "exponential",
        }
    }

    /// The inverse of [`Curve::as_str`], or `None` for anything else --
    /// checked explicitly at the boundary that reads it, the same posture
    /// `record_review` already takes for a decision verb, rather than
    /// trusting a string from the database or an HTTP body silently.
    pub fn parse(s: &str) -> Option<Curve> {
        match s {
            "linear" => Some(Curve::Linear),
            "cosine" => Some(Curve::Cosine),
            "exponential" => Some(Curve::Exponential),
            _ => None,
        }
    }
}

/// A fade applied over a span of frames.
///
/// Holds no audio and no curve state beyond a counter, so it costs nothing to
/// keep one per passage.
#[derive(Debug, Clone, Copy)]
pub struct Fade {
    pub curve: Curve,
    /// Length of the fade. Zero means pass-through at unity gain
    /// `[XFD-OV-010]`: "Fade Duration = 0 ... no fade curve applied".
    pub frames: u64,
    pub fade_in: bool,
}

impl Fade {
    pub fn none() -> Self {
        Fade { curve: Curve::Linear, frames: 0, fade_in: true }
    }

    /// Gain at `frame` frames into the fade.
    pub fn gain_at(&self, frame: u64) -> f32 {
        if self.frames == 0 {
            return 1.0; // pass-through
        }
        let t = (frame as f32 / self.frames as f32).clamp(0.0, 1.0);
        if self.fade_in { self.curve.gain_in(t) } else { self.curve.gain_out(t) }
    }

    /// Apply the fade in place to interleaved frames starting at `start_frame`.
    ///
    /// This is the single point at which gain touches audio; the mixer
    /// downstream only ever adds `[XFD-ORTH-020]`.
    pub fn apply(&self, samples: &mut [f32], channels: usize, start_frame: u64) {
        if self.frames == 0 || channels == 0 {
            return;
        }
        for (i, frame) in samples.chunks_mut(channels).enumerate() {
            let g = self.gain_at(start_frame + i as u64);
            frame.iter_mut().for_each(|s| *s *= g);
        }
    }
}

/// A passage's complete volume envelope: an independent fade-in and
/// fade-out, `[XFD-ORTH-010]`'s orthogonality made concrete -- either can be
/// zero (pass-through), both can be nonzero and even overlap on a very short
/// passage, and neither has anything to do with `[SPEC-BK-020]`'s `Fade`,
/// which stays exactly what it was for Skip/Handoff `[SPEC-SUI-226]`.
///
/// Distinct from lead-in/lead-out, which decide *when* a neighbour may
/// overlap this passage (`queue.rs`'s `overlap_ms`) and never touch a
/// sample. This is the one place a passage's own gain is shaped, regardless
/// of whether any crossfade ever happens around it -- the two points this
/// exists to close: a hard cut at an arbitrary sample pops, and a passage
/// sliced out of continuous audio (a DAO capture, a live recording) has no
/// silence of its own to lead into.
#[derive(Debug, Clone, Copy)]
pub struct Envelope {
    pub fade_in: Fade,
    pub fade_out: Fade,
    /// The passage's own length, in the same output-rate frames `Fade`
    /// already uses -- needed so fade-out can be measured from the end
    /// without the caller re-deriving that arithmetic at every call site.
    pub total_frames: u64,
}

impl Envelope {
    /// Pass-through: unity gain everywhere, for a decoder span this
    /// envelope has no opinion about (tests, mostly -- real playback always
    /// has a real fade-in/fade-out pair, per the fixed default `[SPEC-SUI-226]`).
    pub fn none() -> Self {
        Envelope { fade_in: Fade::none(), fade_out: Fade::none(), total_frames: 0 }
    }

    /// Gain at `frame` frames into the passage. The two fades are
    /// **multiplied**, not chosen between: on a passage short enough that
    /// the fade-in and fade-out regions overlap, the smaller of the two
    /// wins naturally, the same sequential-multiply approach McRhythm's own
    /// `calculate_passage_volume` uses rather than picking one region over
    /// the other.
    pub fn gain_at(&self, frame: u64) -> f32 {
        let g_in = self.fade_in.gain_at(frame);
        // `saturating_sub` rather than a plain subtraction: a fade-out
        // longer than the passage itself must not underflow this into a
        // huge `u64` and read as "not yet started" for the entire passage.
        let fade_out_start = self.total_frames.saturating_sub(self.fade_out.frames);
        let g_out = if frame >= fade_out_start {
            self.fade_out.gain_at(frame - fade_out_start)
        } else {
            1.0
        };
        g_in * g_out
    }

    /// Apply this envelope in place to interleaved frames starting at
    /// `start_frame` -- the same single point `Fade::apply` is for Skip/
    /// Handoff, for ordinary per-passage playback instead.
    pub fn apply(&self, samples: &mut [f32], channels: usize, start_frame: u64) {
        if channels == 0 {
            return;
        }
        for (i, frame) in samples.chunks_mut(channels).enumerate() {
            let g = self.gain_at(start_frame + i as u64);
            frame.iter_mut().for_each(|s| *s *= g);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CURVES: [Curve; 3] = [Curve::Linear, Curve::Cosine, Curve::Exponential];

    #[test]
    fn every_curve_spans_silence_to_unity() {
        for c in CURVES {
            assert!(c.gain_in(0.0).abs() < 1e-6, "{c:?} must start silent");
            assert!((c.gain_in(1.0) - 1.0).abs() < 1e-6, "{c:?} must end at unity");
        }
    }

    #[test]
    fn every_curve_is_monotonic() {
        for c in CURVES {
            let mut prev = -1.0;
            for i in 0..=100 {
                let g = c.gain_in(i as f32 / 100.0);
                assert!(g >= prev - 1e-6, "{c:?} dipped at t={}", i as f32 / 100.0);
                prev = g;
            }
        }
    }

    /// The property that lets one function serve both directions.
    #[test]
    fn fade_out_mirrors_fade_in() {
        for c in CURVES {
            for i in 0..=100 {
                let t = i as f32 / 100.0;
                assert!((c.gain_out(t) - c.gain_in(1.0 - t)).abs() < 1e-6);
            }
        }
    }

    #[test]
    fn exponential_falls_faster_than_linear_early_in_a_fade_out() {
        // "fast start, slow finish" [XFD-EXP-020]
        let t = 0.15;
        assert!(Curve::Exponential.gain_out(t) < Curve::Linear.gain_out(t));
    }

    /// The one guard against the waveform editor's preview lying about what
    /// production plays `[SPEC021 §4]`: `fade.js` is checked against the same
    /// `fixtures/fade/{linear,cosine,exponential}.json` tables this test
    /// checks Rust against `[SPEC-SUI-226]` -- one fixture per curve, so
    /// `fade.js`'s `Linear`/`Cosine` support is checked exactly as strictly
    /// as `Exponential` always was, not merely by inspection.
    #[test]
    fn matches_the_shared_fade_fixture_js_is_also_checked_against() {
        let fixtures: [(Curve, &str); 3] = [
            (Curve::Linear, include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../fixtures/fade/linear.json"
            ))),
            (Curve::Cosine, include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../fixtures/fade/cosine.json"
            ))),
            (Curve::Exponential, include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../fixtures/fade/exponential.json"
            ))),
        ];
        for (curve, json) in fixtures {
            let rows: Vec<serde_json::Value> = serde_json::from_str(json).unwrap();
            assert!(rows.len() >= 10, "the fixture must not have shrunk to nothing");
            for row in rows {
                let t = row["t"].as_f64().unwrap() as f32;
                let want_in = row["gain_in"].as_f64().unwrap() as f32;
                let want_out = row["gain_out"].as_f64().unwrap() as f32;
                let got_in = curve.gain_in(t);
                let got_out = curve.gain_out(t);
                assert!(
                    (got_in - want_in).abs() < 1e-5,
                    "{curve:?}.gain_in({t}): got {got_in}, want {want_in}"
                );
                assert!(
                    (got_out - want_out).abs() < 1e-5,
                    "{curve:?}.gain_out({t}): got {got_out}, want {want_out}"
                );
            }
        }
    }

    #[test]
    fn zero_length_fade_is_pass_through() {
        let f = Fade::none();
        assert_eq!(f.gain_at(0), 1.0);
        assert_eq!(f.gain_at(99_999), 1.0);
        let mut buf = vec![0.5f32; 8];
        f.apply(&mut buf, 2, 0);
        assert!(buf.iter().all(|s| (*s - 0.5).abs() < 1e-9));
    }

    #[test]
    fn apply_scales_both_channels_identically() {
        let f = Fade { curve: Curve::Linear, frames: 4, fade_in: true };
        let mut buf = vec![1.0f32; 8]; // 4 frames, stereo
        f.apply(&mut buf, 2, 0);
        for frame in buf.chunks(2) {
            assert!((frame[0] - frame[1]).abs() < 1e-9, "channels must match");
        }
        assert!(buf[0] < buf[2] && buf[2] < buf[4], "gain must rise across frames");
    }

    #[test]
    fn curve_names_round_trip() {
        for c in CURVES {
            assert_eq!(Curve::parse(c.as_str()), Some(c), "{c:?} must round-trip");
        }
        assert_eq!(Curve::parse("nonsense"), None, "an unknown name must not silently pick one");
    }

    /// `Envelope` combines a fade-in and a fade-out over one passage --
    /// silent at the very start, full volume in the middle, silent again at
    /// the very end, each curve independent of the other `[SPEC-SUI-226]`.
    #[test]
    fn envelope_fades_in_then_out_around_a_full_volume_middle() {
        let e = Envelope {
            fade_in: Fade { curve: Curve::Exponential, frames: 10, fade_in: true },
            fade_out: Fade { curve: Curve::Exponential, frames: 10, fade_in: false },
            total_frames: 100,
        };
        assert!(e.gain_at(0).abs() < 1e-6, "must start silent");
        assert!((e.gain_at(50) - 1.0).abs() < 1e-6, "must be full volume in the middle");
        assert!((e.gain_at(99) - 0.0).abs() < 1e-2, "must end essentially silent");
        // Monotonic rise into the middle, monotonic fall out of it.
        assert!(e.gain_at(2) < e.gain_at(5) && e.gain_at(5) < e.gain_at(9));
        assert!(e.gain_at(90) > e.gain_at(95) && e.gain_at(95) > e.gain_at(98));
    }

    /// A passage shorter than its own fade-in plus fade-out: the two
    /// regions overlap, and the envelope multiplies rather than choosing
    /// one -- the McRhythm design's own sequential-multiply approach, not a
    /// special case invented here.
    #[test]
    fn envelope_multiplies_where_fade_in_and_fade_out_overlap() {
        let e = Envelope {
            fade_in: Fade { curve: Curve::Linear, frames: 80, fade_in: true },
            fade_out: Fade { curve: Curve::Linear, frames: 80, fade_in: false },
            total_frames: 100,
        };
        // fade_out_start = 100 - 80 = 20, so at frame 50: fade-in is 50/80
        // of the way in, fade-out is (50-20)/80 = 30/80 of the way through
        // -- the product must be less than either curve alone would give,
        // and to a computable exact value.
        let g = e.gain_at(50);
        let want = Curve::Linear.gain_in(50.0 / 80.0) * Curve::Linear.gain_out(30.0 / 80.0);
        assert!((g - want).abs() < 1e-5, "got {g}, want {want}");
        assert!(g < Curve::Linear.gain_in(50.0 / 80.0), "must be pulled down by the overlapping fade-out");
    }

    /// A fade-out longer than the whole passage must not underflow the
    /// `saturating_sub` computing where it starts -- `fade_out_start`
    /// clamps to 0, so the fade-out's own progress is tracked from the
    /// passage's very first frame (still ~full volume, having barely begun
    /// a 1000-frame fade in 10 frames of passage) and keeps falling toward
    /// the end, rather than reading as "never started" or panicking.
    #[test]
    fn envelope_fade_out_longer_than_the_passage_covers_all_of_it() {
        let e = Envelope {
            fade_in: Fade::none(),
            fade_out: Fade { curve: Curve::Linear, frames: 1000, fade_in: false },
            total_frames: 10,
        };
        assert!((e.gain_at(0) - 1.0).abs() < 1e-6, "frame 0 is t=0 of the fade-out: full volume");
        assert!(e.gain_at(9) < e.gain_at(0), "and already falling by the passage's own last frame");
    }

    #[test]
    fn envelope_none_is_pass_through() {
        let e = Envelope::none();
        assert_eq!(e.gain_at(0), 1.0);
        assert_eq!(e.gain_at(1_000_000), 1.0);
    }
}
