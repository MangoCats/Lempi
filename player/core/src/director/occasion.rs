//! Seasonal weighting `[SPEC-DIR-130]` — a time layer, not a flavor dimension.
//!
//! MuLibPlay hardcoded four occasions as `[C]`, `[W]`, `[S]`, `[K]` tags with
//! their curves written into a `switch`. Here a curve is **data**: a named
//! characteristic, an interpolation mode, and a handful of control points. A
//! new occasion is rows in two tables and no edit to the engine.
//!
//! The multiplier is
//!
//! ```text
//!     1 + characteristic_value × (curve(today) − 1)
//! ```
//!
//! so a value of 1.0 applies the curve exactly, 0.0 ignores it, and 0.9 applies
//! nine tenths of it. That keeps the whole seasonal effect **one legible term**
//! in the Why-this-passage panel `[SPEC-DIR-190]`. Folding seasonality into the
//! programme target vector was rejected for exactly that reason.

use std::collections::HashMap;

/// Days before the first of each month, in a non-leap year.
const MONTH_START: [u16; 12] = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
const YEAR_DAYS: f64 = 365.0;

/// Day of the year, 0-based, from a month and day.
///
/// Leap years are deliberately ignored: 29 February lands on the same ordinal
/// as 1 March. A season is not accurate to the day, and pretending otherwise
/// would mean every curve moved by one day for a quarter of all years.
pub fn ordinal(month: u32, day: u32) -> u16 {
    let m = month.clamp(1, 12) as usize - 1;
    MONTH_START[m] + (day.clamp(1, 31) as u16 - 1)
}

/// Civil date from a unix timestamp, by Howard Hinnant's `civil_from_days`.
///
/// Ten lines rather than a date-handling dependency, on a target where every
/// crate is a memory decision `[REQ-HW-140]`. UTC: a season does not care
/// which side of midnight a timezone puts it on.
pub fn civil_from_unix(secs: i64) -> (i32, u32, u32) {
    let z = secs.div_euclid(86_400) + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    ((y + i64::from(m <= 2)) as i32, m, d)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Interp {
    /// Hold the previous control point's value until the next one. This is how
    /// MuLibPlay's month-granular curves behaved.
    Step,
    /// Interpolate between control points in **log** space, because these are
    /// ratios: halfway between ×0.5 and ×2.0 is ×1.0, not ×1.25.
    Linear,
}

impl Interp {
    pub fn parse(s: &str) -> Self {
        match s {
            "linear" => Interp::Linear,
            _ => Interp::Step,
        }
    }
}

/// A seasonal curve: control points around a wrapped year.
#[derive(Debug, Clone)]
pub struct Curve {
    pub interp: Interp,
    /// Sorted by ordinal. Never empty once built.
    points: Vec<(u16, f64)>,
}

impl Curve {
    pub fn new(interp: Interp, mut points: Vec<(u16, f64)>) -> Option<Self> {
        if points.is_empty() {
            return None;
        }
        points.sort_by_key(|p| p.0);
        Some(Self { interp, points })
    }

    /// The multiplier on a given day of the year.
    pub fn at(&self, ord: u16) -> f64 {
        let n = self.points.len();
        if n == 1 {
            return self.points[0].1;
        }
        // The last point at or before `ord`, wrapping to the final point of the
        // year when the query falls before the first -- the year is a circle,
        // so a January query sits after a December control point.
        let idx = match self.points.binary_search_by_key(&ord, |p| p.0) {
            Ok(i) => i,
            Err(0) => n - 1,
            Err(i) => i - 1,
        };
        let (prev_ord, prev) = self.points[idx];
        if self.interp == Interp::Step {
            return prev;
        }
        let (next_ord, next) = self.points[(idx + 1) % n];
        let span = wrap_days(prev_ord, next_ord);
        if span <= 0.0 {
            return prev;
        }
        let t = wrap_days(prev_ord, ord) / span;
        // Log space: these are ratios, and a linear blend of 0.000001 and 10
        // would sit at 5 for half the gap.
        (prev.ln() + t * (next.ln() - prev.ln())).exp()
    }
}

/// Forward distance from `a` to `b` around the year.
fn wrap_days(a: u16, b: u16) -> f64 {
    let d = b as f64 - a as f64;
    if d < 0.0 {
        d + YEAR_DAYS
    } else {
        d
    }
}

/// What a characteristic is called, and which class of it this is.
///
/// `("season", "winter")` rather than a bare string, because a characteristic
/// without its class is not a thing an occasion can be looked up by.
pub type Trait = (String, String);

/// Subject mbid → the occasion values recorded against it.
///
/// Named because the same shape is built in `director::library` and passed in
/// here, and a type written out twice is a type that can drift once.
pub type SubjectValues = HashMap<String, Vec<(Trait, f64)>>;

/// Every registered occasion, and the per-subject values they apply to.
#[derive(Debug, Default)]
pub struct Occasions {
    /// (characteristic, class) → curve
    curves: HashMap<Trait, Curve>,
    values: SubjectValues,
}

impl Occasions {
    pub fn new(curves: HashMap<Trait, Curve>, values: SubjectValues) -> Self {
        Self { curves, values }
    }

    pub fn is_empty(&self) -> bool {
        self.curves.is_empty()
    }
    pub fn curve_count(&self) -> usize {
        self.curves.len()
    }

    /// The combined multiplier for one subject on one day.
    ///
    /// Occasions compose multiplicatively, as MuLibPlay's did: a passage that
    /// is both christmasy and wintry gets both. A subject with no occasion values,
    /// or a value of zero, gets exactly 1.0 — the neutral, not a placeholder.
    pub fn multiplier(&self, mbid: Option<&str>, ord: u16) -> f64 {
        let Some(mbid) = mbid else { return 1.0 };
        let Some(vals) = self.values.get(mbid) else { return 1.0 };
        let mut m = 1.0;
        for (key, value) in vals {
            if let Some(curve) = self.curves.get(key) {
                m *= 1.0 + value * (curve.at(ord) - 1.0);
            }
        }
        // A characteristic value above 1.0 with a curve below 1.0 could drive
        // this negative, which would invert the whole weight. Clamp at zero:
        // "never right now" is the strongest a season may say.
        m.max(0.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ord(m: u32, d: u32) -> u16 {
        ordinal(m, d)
    }

    #[test]
    fn civil_dates_round_trip_known_instants() {
        assert_eq!(civil_from_unix(0), (1970, 1, 1));
        assert_eq!(civil_from_unix(1_000_000_000), (2001, 9, 9));
        assert_eq!(civil_from_unix(1_785_261_740), (2026, 7, 28), "the end of the migrated play history");
        assert_eq!(civil_from_unix(-1), (1969, 12, 31), "before the epoch");
    }

    #[test]
    fn ordinals_advance_through_the_year() {
        assert_eq!(ord(1, 1), 0);
        assert_eq!(ord(12, 31), 364);
        assert!(ord(6, 15) > ord(3, 15));
    }

    /// MuLibPlay's Winter curve, as step data. Whole months at a constant
    /// value is exactly what the original did.
    fn winter() -> Curve {
        Curve::new(
            Interp::Step,
            vec![
                (ord(1, 1), 1.5),
                (ord(2, 1), 1.0),
                (ord(3, 1), 0.25),
                (ord(4, 1), 0.000_001),
                (ord(11, 1), 0.5),
                (ord(12, 1), 2.0),
            ],
        )
        .unwrap()
    }

    #[test]
    fn a_step_curve_holds_its_value_across_the_month() {
        let w = winter();
        assert_eq!(w.at(ord(12, 1)), 2.0);
        assert_eq!(w.at(ord(12, 25)), 2.0, "December holds all month");
        assert_eq!(w.at(ord(1, 15)), 1.5);
        assert_eq!(w.at(ord(3, 31)), 0.25);
        assert_eq!(w.at(ord(7, 4)), 0.000_001, "out of season is suppressed");
    }

    /// January must read December's control point, not fall off the front of
    /// the list: the year is a circle.
    #[test]
    fn the_year_wraps() {
        let c = Curve::new(Interp::Step, vec![(ord(6, 1), 3.0), (ord(11, 1), 0.5)]).unwrap();
        assert_eq!(c.at(ord(1, 10)), 0.5, "January follows the November point");
        assert_eq!(c.at(ord(12, 31)), 0.5);
        assert_eq!(c.at(ord(7, 1)), 3.0);
    }

    #[test]
    fn linear_interpolation_is_geometric() {
        let c = Curve::new(Interp::Linear, vec![(ord(1, 1), 0.5), (ord(1, 11), 2.0)]).unwrap();
        // Halfway between x0.5 and x2.0 is x1.0, not x1.25.
        assert!((c.at(ord(1, 6)) - 1.0).abs() < 1e-9, "got {}", c.at(ord(1, 6)));
        assert!((c.at(ord(1, 1)) - 0.5).abs() < 1e-9);
    }

    /// The characteristic value scales the curve's departure from 1.0, so a
    /// half-christmasy passage gets half the seasonal push.
    #[test]
    fn the_characteristic_value_scales_the_curve() {
        let mut curves = HashMap::new();
        curves.insert(
            ("user.winter".into(), "wintry".into()),
            Curve::new(Interp::Step, vec![(ord(1, 1), 3.0)]).unwrap(),
        );
        let mut values = HashMap::new();
        values.insert("rec-a".into(), vec![(("user.winter".to_string(), "wintry".to_string()), 1.0)]);
        values.insert("rec-b".into(), vec![(("user.winter".to_string(), "wintry".to_string()), 0.5)]);
        values.insert("rec-c".into(), vec![(("user.winter".to_string(), "wintry".to_string()), 0.0)]);
        let o = Occasions::new(curves, values);

        assert!((o.multiplier(Some("rec-a"), ord(1, 5)) - 3.0).abs() < 1e-9, "full value applies the curve");
        assert!((o.multiplier(Some("rec-b"), ord(1, 5)) - 2.0).abs() < 1e-9, "half of the departure from 1.0");
        assert!((o.multiplier(Some("rec-c"), ord(1, 5)) - 1.0).abs() < 1e-9, "zero value is inert");
        assert_eq!(o.multiplier(Some("unknown"), ord(1, 5)), 1.0);
        assert_eq!(o.multiplier(None, ord(1, 5)), 1.0, "an unidentified passage has no season");
    }

    #[test]
    fn occasions_compose_multiplicatively() {
        let mut curves = HashMap::new();
        curves.insert(("a".into(), "x".into()), Curve::new(Interp::Step, vec![(0, 2.0)]).unwrap());
        curves.insert(("b".into(), "y".into()), Curve::new(Interp::Step, vec![(0, 3.0)]).unwrap());
        let mut values = HashMap::new();
        values.insert(
            "r".into(),
            vec![(("a".into(), "x".into()), 1.0), (("b".into(), "y".into()), 1.0)],
        );
        let o = Occasions::new(curves, values);
        assert!((o.multiplier(Some("r"), 0) - 6.0).abs() < 1e-9, "both apply");
    }

    /// A season may say "never right now"; it may not invert the weight.
    #[test]
    fn a_multiplier_can_reach_zero_but_never_goes_negative() {
        let mut curves = HashMap::new();
        curves.insert(("a".into(), "x".into()), Curve::new(Interp::Step, vec![(0, 0.0001)]).unwrap());
        let mut values = HashMap::new();
        values.insert("r".into(), vec![(("a".into(), "x".into()), 5.0)]); // out of range on purpose
        let o = Occasions::new(curves, values);
        assert_eq!(o.multiplier(Some("r"), 0), 0.0, "clamped, not inverted");
    }

    /// MuLibPlay's Christmas curve as control points, checked against the
    /// original formula. It is the one occasion that was a formula rather than
    /// a table, so the approximation is measured rather than asserted.
    #[test]
    fn the_christmas_curve_tracks_the_original_formula() {
        fn original(month: u32, day: u32) -> f64 {
            if month < 11 {
                return 0.000_001;
            }
            let days_to = (if month == 12 { 25i32 } else { 55 }) - day as i32;
            if days_to == 0 {
                10.0
            } else if days_to < 0 {
                -1.0 / days_to as f64
            } else if month == 12 {
                5.0 / (days_to as f64).sqrt()
            } else {
                (25.0 / days_to as f64).powi(3)
            }
        }
        let c = Curve::new(
            Interp::Linear,
            vec![
                (ord(1, 1), 0.000_001),
                (ord(10, 31), 0.000_001),
                (ord(11, 1), original(11, 1)),
                (ord(11, 15), original(11, 15)),
                (ord(11, 30), original(11, 30)),
                (ord(12, 10), original(12, 10)),
                (ord(12, 20), original(12, 20)),
                (ord(12, 24), original(12, 24)),
                (ord(12, 25), 10.0),
                (ord(12, 26), original(12, 26)),
                (ord(12, 31), original(12, 31)),
            ],
        )
        .unwrap();

        // The control points themselves must be exact.
        assert!((c.at(ord(12, 25)) - 10.0).abs() < 1e-9, "Christmas Day peaks");
        assert!((c.at(ord(11, 1)) - 0.0992).abs() < 0.001, "November opens low");

        // Between them, the log interpolation must stay within a factor of two
        // of the original -- these are ratios spanning seven orders of
        // magnitude, and a season is not accurate to the day.
        let mut worst: f64 = 1.0;
        for (m, d) in [(11, 8), (11, 22), (12, 5), (12, 15), (12, 22), (12, 28)] {
            let ratio = c.at(ord(m, d)) / original(m, d);
            worst = worst.max(ratio.max(1.0 / ratio));
        }
        assert!(worst < 2.0, "worst divergence from the original was {worst:.2}x");
    }
}
