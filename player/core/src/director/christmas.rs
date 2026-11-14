//! The Christmas reserve: in the run-up to Christmas, some of the roulette's
//! places are kept for Christmas passages `[SPEC-XMR-010]`.
//!
//! Three pure pieces, kept out of `library` so each can be tested without a
//! database or a clock:
//!
//! - [`christmas_ramp`]: how far into the season a local instant is, 0.0 to
//!   1.0 `[SPEC-XMR-020]`;
//! - [`reserved`]: how many of the roulette's places that keeps for Christmas
//!   `[SPEC-XMR-030]`;
//! - [`reserve`]: the roulette list with those places filled `[SPEC-XMR-040]`;
//!
//! and [`ARTIST_GAP_SHARE`], what of an artist's rotation still holds where
//! the rest of rotation is set aside `[SPEC-XMR-050]`.
//!
//! The ramp is in code for this season, on the maintainer's word
//! (2026-11-09); the curves of `[SPEC-DIR-130]` stay data. What a passage
//! weighs is still Stage A's: this changes **which** passages the roulette
//! draws on and where in its rank they stand, and every passage in a reserved
//! place is weighed by Stage A with rotation set aside, so all of them are on
//! one scale `[SPEC-XMR-040]`.

use serde::Serialize;

use super::occasion::civil_from_unix;

const MIN: i64 = 60;
const HOUR: i64 = 60 * MIN;
const DAY: i64 = 24 * HOUR;

/// The turns of the ramp, in seconds from local midnight at the start of
/// 1 December `[SPEC-XMR-020]`.
const RISE_FROM: i64 = 6 * HOUR; // 1 December, 06:00
const FULL_FROM: i64 = 24 * DAY + 6 * HOUR; // 25 December, 06:00
const FALL_FROM: i64 = 24 * DAY + 23 * HOUR + 45 * MIN; // 25 December, 23:45
const ZERO_FROM: i64 = 25 * DAY + 6 * HOUR; // 26 December, 06:00

/// How much of an artist's rotation still holds it out of a reserved place
/// `[SPEC-XMR-050]`: a twelfth (the maintainer, 2026-11-14; a sixth from
/// 2026-11-13). Everything else of rotation is set aside there, so without
/// this an artist on air could follow itself. A share of each artist's own
/// rotation, after the artist time scale: 50 min on the 10-hour default,
/// 2 h 10 min for an artist held 26 hours.
pub const ARTIST_GAP_SHARE: f64 = 1.0 / 12.0;

/// How far into the Christmas season `local_secs` is `[SPEC-XMR-020]`.
///
/// `local_secs` is the listener's wall clock as unix seconds -- the
/// programme's `local_secs` of a pick's projected start `[SPEC-DIR-186]`.
/// Piecewise linear: 0.0 until 1 December 06:00, rising to 1.0 at
/// 25 December 06:00, held there until 23:45, falling to 0.0 at
/// 26 December 06:00, and 0.0 for the rest of the year.
pub fn christmas_ramp(local_secs: i64) -> f64 {
    let (_, month, day) = civil_from_unix(local_secs);
    if month != 12 {
        return 0.0;
    }
    let t = (i64::from(day) - 1) * DAY + local_secs.rem_euclid(DAY);
    if !(RISE_FROM..ZERO_FROM).contains(&t) {
        0.0
    } else if t < FULL_FROM {
        (t - RISE_FROM) as f64 / (FULL_FROM - RISE_FROM) as f64
    } else if t < FALL_FROM {
        1.0
    } else {
        (ZERO_FROM - t) as f64 / (ZERO_FROM - FALL_FROM) as f64
    }
}

/// How many of a roulette list of `size` places the ramp keeps for Christmas
/// `[SPEC-XMR-030]`: `ramp × size`, rounded. With the flow order's 100 places
/// that is `ramp × 100`, rounded, as asked.
pub fn reserved(ramp: f64, size: usize) -> usize {
    if !ramp.is_finite() {
        return 0;
    }
    ((ramp.clamp(0.0, 1.0) * size as f64).round() as usize).min(size)
}

/// Where a candidate in the roulette list came from `[SPEC-XMR-040]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Place {
    /// Where Stage C put it, above the reserved places.
    #[default]
    Flow,
    /// A Christmas passage Stage C ranked below the cutoff, moved up into a
    /// reserved place, at its own weight.
    MovedUp,
    /// A Christmas passage from outside Stage C's list, nearest the
    /// programme's seeds first, weighed with rotation set aside
    /// `[SPEC-XMR-050]`.
    Filler,
    /// A non-Christmas passage from below the cutoff, in Stage C's order,
    /// taking a reserved place no Christmas passage could fill
    /// `[SPEC-XMR-060]`.
    Backfill,
}

/// What the reserve did to one pick's roulette list, for "why this passage?"
/// `[SPEC-XMR-070]`.
#[derive(Debug, Clone, Copy, PartialEq, Default, Serialize)]
pub struct Reserve {
    /// This player's setting `[SPEC-XMR-080]`.
    pub enabled: bool,
    /// The ramp at the pick's projected start, whether or not it is enabled.
    pub ramp: f64,
    /// Whether the list had a flow order to rank it `[SPEC-XMR-030]`. Without
    /// one the places kept are any of the shuffled pool's, and a backfilled
    /// place holds one of Stage C's own passages, no lower in any order than
    /// those above the cutoff: what the reserve changed is only the Christmas
    /// passages it moved up or added.
    pub ranked: bool,
    /// Places kept for Christmas: 0 when off, out of season, when the
    /// relaxed fallback chose, or when the pool was the last resort.
    pub reserved: usize,
    pub moved_up: usize,
    pub fillers: usize,
    pub backfilled: usize,
    /// Places were due and none were kept: the pool was Stage B's last
    /// resort, passages without flavor, and the reserve keeps nothing then
    /// `[SPEC-XMR-045]`. Said so that a 0 in season is not left unexplained.
    pub last_resort: bool,
}

/// The roulette list, with `reserved` of its `size` places kept for
/// Christmas `[SPEC-XMR-040]`.
///
/// `ranked` is Stage C's whole order -- every passage Stage B gathered, not
/// only the first `size`. Its first `size − reserved` stand as they are.
/// Below them, in order:
///
/// 1. the Christmas passages Stage C ranked below that cutoff, in its order;
/// 2. `fillers`, already in the order wanted: Christmas passages not in
///    `ranked` at all, nearest the programme's seeds first `[SPEC-XMR-050]`;
/// 3. the non-Christmas passages from below the cutoff, in Stage C's order,
///    so the list is still `size` long when Christmas runs short
///    `[SPEC-XMR-060]`.
///
/// The first two together take at most `reserved` places. With nothing
/// reserved this is Stage C's first `size`, exactly as before.
///
/// Only the first [`fillers_wanted`] of `fillers` can be used; any after
/// them are ignored.
pub fn reserve<T: Copy>(
    ranked: &[T],
    size: usize,
    reserved: usize,
    is_christmas: impl Fn(&T) -> bool,
    fillers: &[T],
) -> (Vec<(T, Place)>, Reserve) {
    let reserved = reserved.min(size);
    let keep = size - reserved;
    let mut out: Vec<(T, Place)> = ranked.iter().take(keep).map(|t| (*t, Place::Flow)).collect();
    let mut tally = Reserve { reserved, ..Reserve::default() };
    if reserved == 0 {
        return (out, tally);
    }
    let below = below_the_cutoff(ranked, size, reserved);
    let budget = |out: &Vec<(T, Place)>, tally: &Reserve| {
        (tally.moved_up + tally.fillers < reserved) && out.len() < size
    };
    for t in below.iter().filter(|t| is_christmas(t)) {
        if !budget(&out, &tally) {
            break;
        }
        out.push((*t, Place::MovedUp));
        tally.moved_up += 1;
    }
    for t in fillers {
        if !budget(&out, &tally) {
            break;
        }
        out.push((*t, Place::Filler));
        tally.fillers += 1;
    }
    for t in below.iter().filter(|t| !is_christmas(t)) {
        if out.len() >= size {
            break;
        }
        out.push((*t, Place::Backfill));
        tally.backfilled += 1;
    }
    (out, tally)
}

/// What of `ranked` stands below [`reserve`]'s cutoff: everything after its
/// first `size − reserved`.
fn below_the_cutoff<T>(ranked: &[T], size: usize, reserved: usize) -> &[T] {
    let keep = size - reserved.min(size);
    ranked.get(keep..).unwrap_or(&[])
}

/// How many fillers [`reserve`] would place, given these arguments: the
/// reserved places that the Christmas passages below its cutoff cannot fill
/// `[SPEC-XMR-050]`. Asked first because fillers cost a pass over every
/// Christmas passage, and are weighed only when this is above 0.
///
/// The cutoff is `reserve`'s own, so the two cannot disagree about it.
pub fn fillers_wanted<T>(ranked: &[T], size: usize, reserved: usize, is_christmas: impl Fn(&T) -> bool) -> usize {
    let reserved = reserved.min(size);
    if reserved == 0 {
        return 0;
    }
    let moving_up = below_the_cutoff(ranked, size, reserved).iter().filter(|t| is_christmas(t)).take(reserved).count();
    reserved - moving_up
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Local wall-clock seconds for a date and time in December 2026.
    fn dec(day: i64, hour: i64, min: i64) -> i64 {
        // 2026-12-01T00:00 as unix seconds, read as local time.
        const DEC_1_2026: i64 = 1_796_083_200;
        assert_eq!(civil_from_unix(DEC_1_2026), (2026, 12, 1));
        DEC_1_2026 + (day - 1) * DAY + hour * HOUR + min * MIN
    }

    fn near(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    /// The four turns, and the year either side `[SPEC-XMR-020]`.
    #[test]
    fn the_ramp_turns_where_it_was_asked_to() {
        assert_eq!(christmas_ramp(dec(1, 5, 59)), 0.0, "before 1 December 06:00");
        assert_eq!(christmas_ramp(dec(1, 6, 0)), 0.0, "starts from zero");
        assert!(near(christmas_ramp(dec(13, 6, 0)), 0.5), "half way, 13 December 06:00");
        assert!(christmas_ramp(dec(24, 23, 59)) < 1.0);
        assert_eq!(christmas_ramp(dec(25, 6, 0)), 1.0, "full at 25 December 06:00");
        assert_eq!(christmas_ramp(dec(25, 23, 44)), 1.0, "held through the day");
        assert_eq!(christmas_ramp(dec(25, 23, 45)), 1.0, "and falls from 23:45");
        let mid = dec(25, 23, 45) + (dec(26, 6, 0) - dec(25, 23, 45)) / 2;
        assert!(near(christmas_ramp(mid), 0.5), "half way down");
        assert_eq!(christmas_ramp(dec(26, 6, 0)), 0.0, "gone at 26 December 06:00");
        assert_eq!(christmas_ramp(dec(31, 23, 59)), 0.0, "and stays gone");
        assert_eq!(christmas_ramp(dec(1, 0, 0) - 1), 0.0, "30 November");
        assert_eq!(christmas_ramp(dec(32, 0, 0)), 0.0, "1 January");
        assert_eq!(christmas_ramp(dec(1, 0, 0) - 200 * DAY), 0.0, "May");
    }

    /// Linear between the turns: each place reserved is 5.76 hours of the
    /// 24 days' rise.
    #[test]
    fn the_rise_is_linear() {
        let a = christmas_ramp(dec(7, 6, 0));
        let b = christmas_ramp(dec(19, 6, 0));
        assert!(near(a, 0.25) && near(b, 0.75), "{a} {b}");
        assert_eq!(reserved(christmas_ramp(dec(1, 8, 52)), 100), 0, "under half a place");
        assert_eq!(reserved(christmas_ramp(dec(1, 8, 53)), 100), 1);
    }

    #[test]
    fn reserved_places_round_and_stay_in_range() {
        assert_eq!(reserved(0.0, 100), 0);
        assert_eq!(reserved(0.374, 100), 37);
        assert_eq!(reserved(0.375, 100), 38);
        assert_eq!(reserved(1.0, 100), 100);
        assert_eq!(reserved(0.5, 37), 19, "a pool without flow order");
        assert_eq!(reserved(1.7, 10), 10);
        assert_eq!(reserved(-1.0, 10), 0);
        assert_eq!(reserved(f64::NAN, 10), 0);
    }

    /// Christmas passages are the odd numbers.
    fn xmas(n: &u32) -> bool {
        n % 2 == 1
    }

    #[test]
    fn nothing_reserved_is_stage_c_unchanged() {
        let ranked: Vec<u32> = (0..150).collect();
        let (out, t) = reserve(&ranked, 100, 0, xmas, &[1001, 1003]);
        assert_eq!(out.iter().map(|x| x.0).collect::<Vec<_>>(), (0..100).collect::<Vec<_>>());
        assert!(out.iter().all(|x| x.1 == Place::Flow));
        assert_eq!((t.moved_up, t.fillers, t.backfilled), (0, 0, 0));
    }

    /// The top stands; Christmas from below the cutoff moves up in Stage C's
    /// order and fills every reserved place before any filler is used.
    #[test]
    fn christmas_below_the_cutoff_moves_up_first() {
        let ranked: Vec<u32> = (0..200).collect();
        let (out, t) = reserve(&ranked, 100, 10, xmas, &[1001]);
        assert_eq!(out.len(), 100);
        assert_eq!(out[..90].iter().map(|x| x.0).collect::<Vec<_>>(), (0..90).collect::<Vec<_>>());
        let moved: Vec<u32> = out[90..].iter().map(|x| x.0).collect();
        assert_eq!(moved, vec![91, 93, 95, 97, 99, 101, 103, 105, 107, 109]);
        assert!(out[90..].iter().all(|x| x.1 == Place::MovedUp));
        assert_eq!((t.moved_up, t.fillers, t.backfilled), (10, 0, 0));
    }

    /// Fillers take what moving up could not, in the order given; what is
    /// still empty is backfilled from below the cutoff, in Stage C's order.
    #[test]
    fn fillers_then_backfill() {
        // Two Christmas passages below a cutoff of 60, two fillers, 40 reserved.
        let mut ranked: Vec<u32> = (0..120).map(|n| n * 2).collect();
        ranked[70] = 141;
        ranked[80] = 161;
        let (out, t) = reserve(&ranked, 100, 40, xmas, &[1001, 1003]);
        assert_eq!(out.len(), 100);
        let tail: Vec<(u32, Place)> = out[60..65].to_vec();
        assert_eq!(
            tail,
            vec![
                (141, Place::MovedUp),
                (161, Place::MovedUp),
                (1001, Place::Filler),
                (1003, Place::Filler),
                (120, Place::Backfill),
            ]
        );
        assert_eq!((t.moved_up, t.fillers, t.backfilled), (2, 2, 36));
        assert!(out[65..].iter().all(|x| x.1 == Place::Backfill && !xmas(&x.0)));
    }

    /// Fillers never take more than the reserved places, even when Stage C's
    /// list is shorter than the roulette's. Their share of the draw is then
    /// larger than in a full list, and that is accepted: the shares of
    /// `[SPEC-XMR-040]` are the full catalogue's `[SPEC-XMR-042]`.
    #[test]
    fn a_short_pool_reserves_no_more_than_asked() {
        let ranked: Vec<u32> = (0..40).map(|n| n * 2).collect();
        let fillers: Vec<u32> = (0..50).map(|n| 1001 + n * 2).collect();
        let (out, t) = reserve(&ranked, 100, 10, xmas, &fillers);
        assert_eq!(out.len(), 50, "all 40 of Stage C's, and 10 Christmas");
        assert_eq!((t.moved_up, t.fillers, t.backfilled), (0, 10, 0));
    }

    /// `fillers_wanted` is the number `reserve` places, given fillers enough,
    /// over lists longer and shorter than the roulette's and every share of
    /// it reserved: the one cutoff, asked two ways.
    #[test]
    fn fillers_wanted_is_what_reserve_places() {
        let plenty: Vec<u32> = (0..200).map(|n| 1001 + n * 2).collect();
        for len in [0usize, 1, 30, 99, 100, 101, 150, 200] {
            for christmas_every in [1u32, 3, 7, 1000] {
                let ranked: Vec<u32> =
                    (0..len as u32).map(|n| if n % christmas_every == 0 { n * 2 + 1 } else { n * 2 }).collect();
                for reserved in [0usize, 1, 10, 50, 75, 99, 100, 140] {
                    let want = fillers_wanted(&ranked, 100, reserved, xmas);
                    let (_, t) = reserve(&ranked, 100, reserved, xmas, &plenty);
                    assert_eq!(want, t.fillers, "len {len}, every {christmas_every}, reserved {reserved}");
                }
            }
        }
    }

    /// Every place reserved: Christmas only, Stage C's best first.
    #[test]
    fn christmas_day_is_all_christmas_when_there_is_enough() {
        let ranked: Vec<u32> = (0..200).collect();
        let fillers: Vec<u32> = (0..60).map(|n| 1001 + n * 2).collect();
        let (out, t) = reserve(&ranked, 100, 100, xmas, &fillers);
        assert_eq!(out.len(), 100);
        assert!(out.iter().all(|x| xmas(&x.0)));
        assert_eq!(out[0], (1, Place::MovedUp));
        assert_eq!((t.moved_up, t.fillers, t.backfilled), (100, 0, 0));
    }
}
