//! Stage A: eligibility and frequency — *how often may this play?*
//!
//! Pure arithmetic over times and tuning values. No database, no clock, no
//! randomness: `now` is a parameter, so every case here is directly testable
//! and the six-years-proven behaviour can be pinned rather than described.
//!
//! Frequency never consults flavor, Taste or seeds `[SPEC-DIR-100]`. Keeping
//! that separation is what lets the Why-this-passage panel tell two legible
//! stories instead of one opaque product `[SPEC-DIR-190]`.

/// Defaults from `[SPEC-DIR-120]`. They matter more than they look: only 36%
/// of MuLibPlay tracks ever received tuned values, so most selection runs on
/// exactly these numbers.
pub const DEF_ROTATION_REC: f64 = 2.0; // 4.2 days
pub const DEF_RECOVERY_REC: f64 = 2.6; // 16.6 days
pub const DEF_ROTATION_ART: f64 = 1.0; // 10 hours
pub const DEF_RECOVERY_ART: f64 = 1.0; // 10 hours
pub const DEF_RESTRAINT: f64 = 0.0; // ×1.0

/// `[SPEC-DIR-195]` proven values.
pub const MIN_WEIGHT: f64 = 0.001;
pub const MIN_LENGTH_S: f64 = 30.0;
pub const MAX_LENGTH_S: f64 = 3600.0;
pub const MAX_DEPTH_S: f64 = 10800.0;
pub const LENGTH_MIDPOINT_S: f64 = 180.0;
pub const LENGTH_CAP: f64 = 4.0;

/// Related recordings share a rotation `[REQ-PD-115]`, and two master time
/// scales -- one for artists, one for recordings -- multiply every block and
/// ramp duration `[REQ-PD-118]`.
///
/// Log-scale time encoding `[SPEC-DIR-110]`: one float spans four orders of
/// magnitude, which is why a single slider can mean "six minutes" or "41 days".
pub fn seconds(v: f64) -> f64 {
    10f64.powf(v) * 3600.0
}

/// The recovery ramp, transcribed from MuLibPlay's `recoveryWeight`.
///
/// Blocked below `rot`, fully recovered at `rot + rec`, linear between. The
/// boundaries are inclusive as shipped -- `age == rot` is still zero.
pub fn recovery_weight(age_s: f64, rot_s: f64, rec_s: f64) -> f64 {
    if age_s <= rot_s {
        return 0.0;
    }
    if age_s >= rot_s + rec_s {
        return 1.0;
    }
    (age_s - rot_s) / rec_s
}

/// A master multiplier over every block and ramp *duration* `[SPEC-DIR-118]`.
///
/// One for artists, one for recordings. At 1.0 it does nothing; at 0.5 every
/// block and ramp is half as long, at 2.0 twice. It exists because the
/// per-subject tuning values are log-scale `[SPEC-DIR-110]` — nudging
/// thousands of them to say "everything a bit sooner" is not a thing a
/// listener can do, whereas one dial is. Scaling *durations* rather than
/// weights keeps frequency and character orthogonal `[SPEC-DIR-100]`: it
/// changes when a passage becomes eligible, never how much it is liked.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TimeScale(f64);

impl TimeScale {
    pub const MIN: f64 = 0.0001;
    pub const MAX: f64 = 100.0;
    pub const PLACES: f64 = 10_000.0; // four decimal places

    /// Clamped to range and rounded to four places, so a stored value and a
    /// typed value always mean the same thing. Non-finite input falls back to
    /// 1.0 rather than poisoning every weight with NaN.
    pub fn new(v: f64) -> Self {
        if !v.is_finite() {
            return Self(1.0);
        }
        let clamped = v.clamp(Self::MIN, Self::MAX);
        Self((clamped * Self::PLACES).round() / Self::PLACES)
    }
    pub fn get(self) -> f64 {
        self.0
    }
}

impl Default for TimeScale {
    fn default() -> Self {
        Self(1.0)
    }
}

/// Per-subject tuning, for an artist or a recording.
#[derive(Debug, Clone, Copy)]
pub struct Tuning {
    pub rotation: f64,
    pub recovery: f64,
    pub restraint: f64,
}

impl Tuning {
    pub fn recording_defaults() -> Self {
        Self { rotation: DEF_ROTATION_REC, recovery: DEF_RECOVERY_REC, restraint: DEF_RESTRAINT }
    }
    pub fn artist_defaults() -> Self {
        Self { rotation: DEF_ROTATION_ART, recovery: DEF_RECOVERY_ART, restraint: DEF_RESTRAINT }
    }
}

/// How far the artist pass reaches into the recording weight.
///
/// Named for what it does, not where it came from, because the choice is now
/// Lempi's rather than an inheritance `[SPEC-DIR-117]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ArtistCoupling {
    /// **Lempi's behaviour.** A partially recovered artist damps its
    /// recordings in proportion to its own ramp, so hearing one recording by
    /// an artist gently suppresses the rest until the artist recovers
    /// `[SPEC-DIR-115]` step 4.
    #[default]
    Damped,
    /// The artist acts only as a gate: it can block, but never damps. This is
    /// MuLibPlay as shipped -- a variable shadowing meant its artist ramp never
    /// reached the recording weight in six years of production. Retained
    /// solely to measure how far Lempi diverges from it `[REQ-PD-110]`, never
    /// as a listening mode.
    GateOnly,
}

/// Everything about how selection is tuned, as opposed to what is being
/// weighed. Defaults reproduce Lempi's intended behaviour with no scaling.
#[derive(Debug, Clone, Copy, Default)]
pub struct Policy {
    pub coupling: ArtistCoupling,
    pub artist_scale: TimeScale,
    pub recording_scale: TimeScale,
    /// How long a *skipped* recording is held out, in seconds
    /// `[SPEC-PLAY-050]`. Zero disables suppression entirely, which is a
    /// legitimate choice and the reason this is not an `Option`.
    pub skip_suppress_s: f64,
    /// How long a recording *removed from the queue unheard* is held out
    /// `[SPEC-PLAY-055]`. Shorter by default: declining to hear something now
    /// says less than stopping it once it had started.
    pub dequeue_suppress_s: f64,
}

/// Another recording that shares this one's rotation `[SPEC-DIR-116]` — a
/// live take, a remaster, the same song on a compilation.
#[derive(Debug, Clone, Copy)]
pub struct Related {
    /// Seconds since *this related recording* last played; `None` if never.
    pub age_s: Option<f64>,
    /// How strongly the two are related, 0..1. Scales the recovery window: a
    /// weak relation recovers quickly, a near-identical one behaves almost
    /// like the recording itself.
    pub strength: f64,
}

/// Everything that produced the weight, kept as separate terms.
///
/// Returned rather than logged, because `[SPEC-DIR-190]` requires the decision
/// to be reconstructable and a single float cannot be. Exclusions carry their
/// reason for the same purpose: "why did this *not* play" is half the question.
#[derive(Debug, Clone, PartialEq)]
pub struct Weighing {
    pub artist_weight: f64,
    pub artist_blocked: bool,
    pub recording_restraint: f64,
    pub recording_ramp: f64,
    /// Product of the ramps of every related recording still recovering.
    pub related_damping: f64,
    pub length_bonus: f64,
    pub occasion: f64,
    pub weight: f64,
    pub excluded: Option<Exclusion>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Exclusion {
    /// The listener skipped this recording recently `[SPEC-PLAY-050]`. A hard
    /// block for a window and **nothing else** — it damps no weight, marks no
    /// artist and feeds no ramp, because a skip is not a play.
    SkipSuppressed,
    /// The listener took this recording out of the queue before it played
    /// `[SPEC-PLAY-055]`. Same effect, shorter window by default.
    DequeueSuppressed,
    ArtistRotationBlock,
    RecordingRotationBlock,
    RelatedRotationBlock,
    /// Another passage of the same *song* played inside this passage's own
    /// rotation `[GDE-WRK-035]` — a different recording of it, or the same
    /// recording under a different passage. Distinct from
    /// `RecordingRotationBlock` so the panel can say which tier held it out.
    WorkRotationBlock,
    TooShort,
    TooLong,
    TooDeep,
    BelowMinWeight,
}

impl Weighing {
    fn excluded(reason: Exclusion) -> Self {
        Self {
            artist_weight: 0.0,
            artist_blocked: reason == Exclusion::ArtistRotationBlock,
            recording_restraint: 0.0,
            recording_ramp: 0.0,
            related_damping: 1.0,
            length_bonus: 0.0,
            occasion: 1.0,
            weight: 0.0,
            excluded: Some(reason),
        }
    }
    pub fn is_eligible(&self) -> bool {
        self.excluded.is_none()
    }
}

/// One passage's frequency inputs.
#[derive(Debug, Clone, Copy)]
pub struct Candidate<'a> {
    pub length_s: f64,
    /// How far into its file the passage starts — a DAO file holds up to 40,
    /// and the deep ones are rarely what a listener means by "a song".
    pub depth_s: f64,
    pub recording: Tuning,
    pub artist: Tuning,
    /// Seconds since *this passage* last played; `None` if never.
    ///
    /// The narrowest of the three identity tiers `[GDE-WRK-035]`, and the only
    /// one an unidentified passage has. Without it such a passage carries no
    /// history at all and may repeat freely `[GDE-WRK-055]`.
    pub passage_age_s: Option<f64>,
    /// Seconds since this recording last played; `None` if never.
    pub recording_age_s: Option<f64>,
    /// Seconds since anything performing a work of this recording last played;
    /// `None` if never, or if the recording performs no known work.
    ///
    /// The widest tier, and in practice the one that decides: a play stamps
    /// every key the passage has, so this is never staler than the other two
    /// `[GDE-WRK-038]`. A recording performing several works takes the most
    /// recent across all of them.
    pub work_age_s: Option<f64>,
    /// Seconds since anything by this artist last played; `None` if never.
    pub artist_age_s: Option<f64>,
    /// Seconds since this recording was last **skipped**; `None` if never.
    /// Deliberately separate from `recording_age_s`: a skip suppresses and
    /// does not otherwise participate `[SPEC-PLAY-050]`.
    pub skip_age_s: Option<f64>,
    /// Seconds since this recording was last **removed from the queue unheard**;
    /// `None` if never `[SPEC-PLAY-055]`.
    pub dequeue_age_s: Option<f64>,
    /// Other recordings of the same song `[SPEC-DIR-116]`. Borrowed rather
    /// than owned: most candidates have none, and a selection pass weighs
    /// thousands.
    pub related: &'a [Related],
    /// Seasonal multiplier from `[SPEC-DIR-130]`, already resolved to a number
    /// by the occasion curve. Kept as an input so this module stays free of
    /// the clock.
    pub occasion: f64,
}

/// The artist pass, then the recording pass `[SPEC-DIR-115]`.
///
/// Never-played is not the same as played-long-ago only in that it skips the
/// ramp entirely: with no history there is nothing to recover from, so the
/// weight is the restraint term alone. That is what makes a fresh library
/// degrade to uniform random rather than to silence `[SPEC-DIR-158]`.
pub fn weigh(c: &Candidate<'_>, policy: &Policy) -> Weighing {
    // Passage filters first: they are free, and they are the reason a 90-minute
    // live set does not enter the roulette [SPEC-DIR-125].
    if c.length_s < MIN_LENGTH_S {
        return Weighing::excluded(Exclusion::TooShort);
    }
    if c.length_s > MAX_LENGTH_S {
        return Weighing::excluded(Exclusion::TooLong);
    }
    if c.depth_s > MAX_DEPTH_S {
        return Weighing::excluded(Exclusion::TooDeep);
    }

    // A recently rejected recording is out, and that is the whole of its
    // effect `[SPEC-PLAY-050]`. Placed with the passage filters rather than in
    // the recording pass to make the point structurally: nothing below reads
    // these ages, so a rejection cannot leak into a ramp or an artist mark.
    //
    // **The longer remaining window wins** `[SPEC-PLAY-057]`. Two rejections of
    // the same recording do not shorten each other, so a recording skipped six
    // days ago is not released early by being dequeued today — the block is
    // the union of the windows, and the reason reported is whichever has
    // longer to run, so the "why" panel names the one actually holding it out.
    let remaining = |age: Option<f64>, window: f64| -> Option<f64> {
        match age {
            Some(a) if window > 0.0 && a < window => Some(window - a),
            _ => None,
        }
    };
    let held = [
        (remaining(c.skip_age_s, policy.skip_suppress_s), Exclusion::SkipSuppressed),
        (remaining(c.dequeue_age_s, policy.dequeue_suppress_s), Exclusion::DequeueSuppressed),
    ];
    if let Some((_, reason)) = held
        .iter()
        .filter(|(left, _)| left.is_some())
        .max_by(|a, b| a.0.unwrap().total_cmp(&b.0.unwrap()))
    {
        return Weighing::excluded(*reason);
    }

    // --- artist pass ---
    let a_scale = policy.artist_scale.get();
    let art_rot = seconds(c.artist.rotation) * a_scale;
    let art_rec = seconds(c.artist.recovery) * a_scale;
    let mut artist_weight = 10f64.powf(-c.artist.restraint);
    if let Some(age) = c.artist_age_s {
        if age < art_rot {
            return Weighing::excluded(Exclusion::ArtistRotationBlock);
        }
        artist_weight *= recovery_weight(age, art_rot, art_rec);
    }

    // --- recording pass ---
    let r_scale = policy.recording_scale.get();
    let rec_rot = seconds(c.recording.rotation) * r_scale;
    let rec_rec = seconds(c.recording.recovery) * r_scale;
    let recording_restraint = 10f64.powf(-c.recording.restraint);
    let mut recording_ramp = 1.0;
    // The three identity tiers are a cascade, not three tests `[GDE-WRK-038]`.
    // A play stamps every key the passage has, so the widest key is never
    // staler than a narrower one: the most recent stamp -- the smallest age --
    // is the one that decides, and the others cannot overturn it. Taking the
    // minimum rather than testing each in turn is what makes that true by
    // construction rather than by the order the tests happen to be written in.
    //
    // The window is always **this** passage's own `rotation`/`recovery`
    // `[GDE-WRK-036]`, never the played passage's. That is the whole of why
    // per-passage periods need no per-pair storage: each candidate is weighed
    // from its own side, so three recordings of one song with 12-, 4- and
    // 8-day rotations each serve their own on a play of any of them.
    let tiers = [
        (c.passage_age_s, Exclusion::RecordingRotationBlock),
        (c.recording_age_s, Exclusion::RecordingRotationBlock),
        (c.work_age_s, Exclusion::WorkRotationBlock),
    ];
    let freshest = tiers
        .iter()
        .filter_map(|(age, why)| age.map(|a| (a, *why)))
        .min_by(|a, b| a.0.total_cmp(&b.0));
    if let Some((age, why)) = freshest {
        if age < rec_rot {
            return Weighing::excluded(why);
        }
        if age < rec_rot + rec_rec {
            recording_ramp = recovery_weight(age, rec_rot, rec_rec);
        }
    }

    // --- related recordings [SPEC-DIR-116] ---
    // Each is judged on ITS OWN age. MuLibPlay passed the primary track's age
    // here, and iterated its relation map by value rather than by key, so
    // related recordings never damped anything at all.
    let mut related_damping = 1.0;
    for r in c.related {
        let Some(age) = r.age_s else { continue };
        if age < rec_rot {
            return Weighing::excluded(Exclusion::RelatedRotationBlock);
        }
        // Strength scales the recovery window, not the block: a weak relation
        // recovers sooner, but sharing a rotation is the point of relating them.
        let window = rec_rec * r.strength.clamp(0.0, 1.0);
        if age < rec_rot + window {
            related_damping *= recovery_weight(age, rec_rot, window);
        }
    }

    let length_bonus = length_bonus(c.length_s);

    let mut weight =
        recording_restraint * recording_ramp * related_damping * length_bonus * c.occasion;
    if policy.coupling == ArtistCoupling::Damped {
        weight *= artist_weight;
    }

    let mut w = Weighing {
        artist_weight,
        artist_blocked: false,
        recording_restraint,
        recording_ramp,
        related_damping,
        length_bonus,
        occasion: c.occasion,
        weight,
        excluded: None,
    };
    // Strictly greater, as shipped: `if (weight > minWeightLimit)`.
    //
    // **Written as a negated `>` on purpose.** `weight <= MIN_WEIGHT` is not
    // the same test: a NaN weight compares false against everything, so that
    // form would let it through as though it had passed. This form excludes
    // it, which is the answer a weight that is not a number deserves.
    #[allow(clippy::neg_cmp_op_on_partial_ord)]
    if !(weight > MIN_WEIGHT) {
        w.weight = 0.0;
        w.excluded = Some(Exclusion::BelowMinWeight);
    }
    w
}

/// A mild preference for passages near three minutes `[SPEC-DIR-125]`.
/// Capped at 2× so a 20-second fragment cannot dominate the pool.
pub fn length_bonus(length_s: f64) -> f64 {
    (LENGTH_CAP.min(LENGTH_MIDPOINT_S / length_s.max(f64::EPSILON))).sqrt()
}

#[cfg(test)]
mod tests {
    use super::*;

    const HOUR: f64 = 3600.0;
    const DAY: f64 = 24.0 * HOUR;

    /// The encoding the whole stage rests on. Pinned against the worked
    /// examples in the spec so a change to the formula cannot pass silently.
    #[test]
    fn log_scale_matches_the_specified_anchors() {
        assert!((seconds(0.0) - HOUR).abs() < 1e-6, "0.0 is one hour");
        assert!((seconds(-1.0) - 360.0).abs() < 1e-6, "-1.0 is six minutes");
        assert!((seconds(2.0) - 4.1666 * DAY).abs() < 0.01 * DAY, "2.0 is ~4.2 days");
        assert!((seconds(3.0) - 41.666 * DAY).abs() < 0.1 * DAY, "3.0 is ~41 days");
    }

    #[test]
    fn the_recovery_ramp_is_linear_between_its_boundaries() {
        let (rot, rec) = (100.0, 400.0);
        assert_eq!(recovery_weight(50.0, rot, rec), 0.0, "inside rotation is blocked");
        assert_eq!(recovery_weight(100.0, rot, rec), 0.0, "the boundary is inclusive, as shipped");
        assert!((recovery_weight(300.0, rot, rec) - 0.5).abs() < 1e-9, "halfway");
        assert_eq!(recovery_weight(500.0, rot, rec), 1.0, "fully recovered");
        assert_eq!(recovery_weight(9e9, rot, rec), 1.0, "and stays there");
    }

    const SKIP_W: f64 = 156.0 * 3600.0;
    const DEQ_W: f64 = 18.0 * 3600.0;
    fn windows() -> Policy {
        Policy { skip_suppress_s: SKIP_W, dequeue_suppress_s: DEQ_W, ..Default::default() }
    }

    /// A rejection suppresses for a window, and does nothing else at all
    /// `[SPEC-PLAY-050]`.
    #[test]
    fn a_recent_skip_is_excluded_and_an_old_one_is_not() {
        let mut c = candidate();
        c.skip_age_s = Some(SKIP_W - 1.0);
        assert_eq!(weigh(&c, &windows()).excluded, Some(Exclusion::SkipSuppressed));

        c.skip_age_s = Some(SKIP_W + 1.0);
        assert!(weigh(&c, &windows()).is_eligible(), "out of the window, back in the running");
    }

    /// A dequeue earns its own, shorter window `[SPEC-PLAY-055]`.
    #[test]
    fn a_dequeue_suppresses_for_its_own_shorter_window() {
        let mut c = candidate();
        c.dequeue_age_s = Some(DEQ_W - 1.0);
        assert_eq!(weigh(&c, &windows()).excluded, Some(Exclusion::DequeueSuppressed));

        // Past 18 h it is free again -- it never earned the 156 h of a skip.
        c.dequeue_age_s = Some(DEQ_W + 1.0);
        assert!(weigh(&c, &windows()).is_eligible());
    }

    /// The longer remaining window wins, and a second rejection cannot shorten
    /// the first `[SPEC-PLAY-057]`.
    #[test]
    fn the_greater_suppression_wins() {
        let mut c = candidate();
        // Skipped 155.5 h ago: 0.5 h of a 156 h window left. Dequeued just now:
        // 18 h left. The dequeue is longer, so it holds and it is the reason.
        c.skip_age_s = Some(155.5 * 3600.0);
        c.dequeue_age_s = Some(0.0);
        assert_eq!(weigh(&c, &windows()).excluded, Some(Exclusion::DequeueSuppressed));

        // Skipped an hour ago: 155 h left, far more than any dequeue. The skip
        // holds, and being dequeued today does NOT release it early.
        c.skip_age_s = Some(3600.0);
        c.dequeue_age_s = Some(0.0);
        assert_eq!(weigh(&c, &windows()).excluded, Some(Exclusion::SkipSuppressed));

        // And the union really is a union: dequeued long ago, skipped recently.
        c.dequeue_age_s = Some(DEQ_W * 10.0);
        assert_eq!(weigh(&c, &windows()).excluded, Some(Exclusion::SkipSuppressed));
    }

    #[test]
    fn a_rejection_changes_no_weight_once_its_window_has_passed() {
        // The whole of `[SPEC-PLAY-050]`: suppression, and no other effect. A
        // passage rejected long ago must weigh exactly as one never rejected.
        let mut rejected = candidate();
        rejected.recording_age_s = Some(seconds(DEF_ROTATION_REC) * 4.0);
        let never = rejected;
        rejected.skip_age_s = Some(400.0 * 3600.0);
        rejected.dequeue_age_s = Some(400.0 * 3600.0);
        assert_eq!(weigh(&rejected, &windows()), weigh(&never, &windows()));
    }

    #[test]
    fn a_zero_window_turns_that_suppression_off() {
        let mut c = candidate();
        c.skip_age_s = Some(1.0);
        c.dequeue_age_s = Some(1.0);
        let off = Policy { skip_suppress_s: 0.0, dequeue_suppress_s: 0.0, ..Default::default() };
        assert!(weigh(&c, &off).is_eligible(), "zero must disable, not block forever");

        // Independently: skip off, dequeue still on.
        let half = Policy { skip_suppress_s: 0.0, dequeue_suppress_s: DEQ_W, ..Default::default() };
        assert_eq!(weigh(&c, &half).excluded, Some(Exclusion::DequeueSuppressed));
    }

    fn candidate<'a>() -> Candidate<'a> {
        Candidate {
            length_s: LENGTH_MIDPOINT_S,
            depth_s: 0.0,
            recording: Tuning::recording_defaults(),
            artist: Tuning::artist_defaults(),
            passage_age_s: None,
            recording_age_s: None,
            work_age_s: None,
            artist_age_s: None,
            skip_age_s: None,
            dequeue_age_s: None,
            related: &[],
            occasion: 1.0,
        }
    }

    /// Cold start: no history, no tuning. Everything must weigh the same, or
    /// a fresh library is not uniform random but silently biased.
    #[test]
    fn a_never_played_library_is_uniform() {
        let w = weigh(&candidate(), &Policy::default());
        assert!(w.is_eligible());
        assert!((w.weight - 1.0).abs() < 1e-9, "weight was {}", w.weight);
        assert_eq!(w.recording_ramp, 1.0, "no history means no ramp to be on");
    }

    #[test]
    fn a_recent_play_blocks_the_recording() {
        let mut c = candidate();
        c.recording_age_s = Some(seconds(DEF_ROTATION_REC) - 1.0);
        assert_eq!(weigh(&c, &Policy::default()).excluded, Some(Exclusion::RecordingRotationBlock));
    }

    /// The incident `[GDE-WRK-010]`: a different recording of the same song,
    /// which the recording tier cannot see.
    #[test]
    fn a_recent_play_of_the_same_work_blocks_a_different_recording() {
        let mut c = candidate();
        c.recording_age_s = None; // this recording itself has never played
        c.work_age_s = Some(seconds(DEF_ROTATION_REC) - 1.0);
        let w = weigh(&c, &Policy::default());
        assert_eq!(w.excluded, Some(Exclusion::WorkRotationBlock));
    }

    /// `[GDE-WRK-036]`: the window is the candidate's own, never the played
    /// passage's. One play of a shared work, three rotations, three answers --
    /// which is what makes per-passage periods need no per-pair storage.
    #[test]
    fn each_passage_of_a_work_serves_its_own_rotation() {
        let age = seconds(2.0); // 4.2 days since something of this work played
        let mut short = candidate(); // rotation 1.7 => ~2.1 days: recovered
        short.recording.rotation = 1.7;
        short.work_age_s = Some(age);
        let mut long = candidate(); // rotation 2.3 => ~8.4 days: still blocked
        long.recording.rotation = 2.3;
        long.work_age_s = Some(age);
        assert_eq!(weigh(&short, &Policy::default()).excluded, None);
        assert_eq!(
            weigh(&long, &Policy::default()).excluded,
            Some(Exclusion::WorkRotationBlock)
        );
    }

    /// `[GDE-WRK-038]`: the tiers are a cascade. The freshest stamp decides,
    /// and a colder wider tier cannot release what a warmer narrower one holds.
    #[test]
    fn the_freshest_tier_decides_and_names_itself() {
        let (rot, rec) = (seconds(DEF_ROTATION_REC), seconds(DEF_RECOVERY_REC));
        // Work cold, recording warm: the recording holds it, and says so.
        let mut c = candidate();
        c.recording_age_s = Some(rot - 1.0);
        c.work_age_s = Some(rot + rec * 2.0);
        assert_eq!(
            weigh(&c, &Policy::default()).excluded,
            Some(Exclusion::RecordingRotationBlock)
        );
        // Both past the block: the ramp uses the freshest, not the oldest.
        let mut d = candidate();
        d.recording_age_s = Some(rot + rec); // fully recovered on its own
        d.work_age_s = Some(rot + rec / 2.0); // half recovered
        let w = weigh(&d, &Policy::default());
        assert!((w.recording_ramp - 0.5).abs() < 1e-9, "ramp {}", w.recording_ramp);
    }

    /// `[GDE-WRK-055]`: a passage with no recording MBID still blocks itself.
    #[test]
    fn an_unidentified_passage_blocks_itself() {
        let mut c = candidate();
        c.recording_age_s = None;
        c.work_age_s = None;
        c.passage_age_s = Some(seconds(DEF_ROTATION_REC) - 1.0);
        assert_eq!(
            weigh(&c, &Policy::default()).excluded,
            Some(Exclusion::RecordingRotationBlock)
        );
    }

    #[test]
    fn a_recent_play_by_the_artist_blocks_the_recording() {
        let mut c = candidate();
        c.artist_age_s = Some(seconds(DEF_ROTATION_ART) - 1.0);
        let w = weigh(&c, &Policy::default());
        assert_eq!(w.excluded, Some(Exclusion::ArtistRotationBlock));
        assert!(w.artist_blocked, "the panel must be able to say which block fired");
    }

    #[test]
    fn a_partly_recovered_recording_is_damped_and_says_so() {
        let mut c = candidate();
        let (rot, rec) = (seconds(DEF_ROTATION_REC), seconds(DEF_RECOVERY_REC));
        c.recording_age_s = Some(rot + rec / 2.0);
        let w = weigh(&c, &Policy::default());
        assert!((w.recording_ramp - 0.5).abs() < 1e-9, "ramp {}", w.recording_ramp);
        assert!((w.weight - 0.5).abs() < 1e-9);
    }

    /// The ramp MuLibPlay never ran [SPEC-DIR-117].
    #[test]
    fn a_recovering_artist_damps_its_recordings() {
        let mut c = candidate();
        let (rot, rec) = (seconds(DEF_ROTATION_ART), seconds(DEF_RECOVERY_ART));
        c.artist_age_s = Some(rot + rec / 2.0);

        let damped = weigh(&c, &Policy::default());
        assert!((damped.artist_weight - 0.5).abs() < 1e-9, "artist is half recovered");
        assert!((damped.weight - 0.5).abs() < 1e-9, "the artist ramp must reach the recording weight");

        let gate = Policy { coupling: ArtistCoupling::GateOnly, ..Default::default() };
        assert!((weigh(&c, &gate).weight - 1.0).abs() < 1e-9,
                "MuLibPlay's shipped behaviour, retained for divergence measurement");
    }

    #[test]
    fn a_barely_recovered_artist_is_damped_not_excluded() {
        let mut c = candidate();
        let (rot, rec) = (seconds(DEF_ROTATION_ART), seconds(DEF_RECOVERY_ART));
        c.artist_age_s = Some(rot + rec * 0.02);
        let w = weigh(&c, &Policy::default());
        assert!(w.is_eligible(), "still eligible: {:?}", w.excluded);
        assert!(w.weight > MIN_WEIGHT && w.weight < 0.05, "weight {}", w.weight);
    }

    #[test]
    fn damping_leaves_a_never_played_artist_alone() {
        let c = candidate();
        assert!((weigh(&c, &Policy::default()).weight - 1.0).abs() < 1e-9);
    }

    // ---------------------------------------------------------------- related

    /// A related recording played inside the rotation window blocks this one:
    /// hearing the live take should stop the studio take following it.
    #[test]
    fn a_recently_played_relation_blocks_the_recording() {
        let rel = [Related { age_s: Some(seconds(DEF_ROTATION_REC) - 1.0), strength: 1.0 }];
        let mut c = candidate();
        c.related = &rel;
        assert_eq!(weigh(&c, &Policy::default()).excluded, Some(Exclusion::RelatedRotationBlock));
    }

    /// The repair: each relation is judged on ITS OWN age. MuLibPlay passed
    /// the primary track's age, so a never-played recording with a
    /// just-recovered relation was damped by its own absent history instead.
    #[test]
    fn a_relation_is_judged_on_its_own_age() {
        let (rot, rec) = (seconds(DEF_ROTATION_REC), seconds(DEF_RECOVERY_REC));
        let rel = [Related { age_s: Some(rot + rec / 2.0), strength: 1.0 }];
        let mut c = candidate();
        c.related = &rel;
        c.recording_age_s = None; // this recording has never played
        let w = weigh(&c, &Policy::default());
        assert!((w.related_damping - 0.5).abs() < 1e-9, "damping {}", w.related_damping);
        assert!((w.weight - 0.5).abs() < 1e-9);
        assert_eq!(w.recording_ramp, 1.0, "its own ramp is untouched -- it never played");
    }

    /// All relations apply, not just the first: three half-recovered relations
    /// compound. This is what "applies to all related recordings" has to mean.
    #[test]
    fn every_relation_damps_not_merely_the_first() {
        let (rot, rec) = (seconds(DEF_ROTATION_REC), seconds(DEF_RECOVERY_REC));
        let rel = [
            Related { age_s: Some(rot + rec / 2.0), strength: 1.0 },
            Related { age_s: Some(rot + rec / 2.0), strength: 1.0 },
            Related { age_s: Some(rot + rec / 2.0), strength: 1.0 },
        ];
        let mut c = candidate();
        c.related = &rel;
        let w = weigh(&c, &Policy::default());
        assert!((w.related_damping - 0.125).abs() < 1e-9, "0.5^3, got {}", w.related_damping);
    }

    /// Relation strength scales the recovery window, so a weak relation stops
    /// damping sooner. It does not scale the block: sharing a rotation is the
    /// point of relating two recordings.
    #[test]
    fn a_weak_relation_recovers_sooner_than_a_strong_one() {
        let (rot, rec) = (seconds(DEF_ROTATION_REC), seconds(DEF_RECOVERY_REC));
        let age = rot + rec * 0.3;
        let strong = [Related { age_s: Some(age), strength: 1.0 }];
        let weak = [Related { age_s: Some(age), strength: 0.2 }];
        let mut c = candidate();
        c.related = &strong;
        let s = weigh(&c, &Policy::default()).related_damping;
        c.related = &weak;
        let w = weigh(&c, &Policy::default()).related_damping;
        assert!((s - 0.3).abs() < 1e-9, "strong relation is mid-ramp: {s}");
        assert_eq!(w, 1.0, "weak relation has already fully recovered");
    }

    #[test]
    fn a_never_played_relation_is_inert() {
        let rel = [Related { age_s: None, strength: 1.0 }];
        let mut c = candidate();
        c.related = &rel;
        assert!((weigh(&c, &Policy::default()).weight - 1.0).abs() < 1e-9);
    }

    // ------------------------------------------------------------ time scales

    #[test]
    fn a_time_scale_clamps_rounds_and_survives_nonsense() {
        assert_eq!(TimeScale::default().get(), 1.0);
        assert_eq!(TimeScale::new(0.00001).get(), TimeScale::MIN, "below range clamps up");
        assert_eq!(TimeScale::new(1e9).get(), TimeScale::MAX, "above range clamps down");
        assert_eq!(TimeScale::new(1.234_56).get(), 1.2346, "rounded to four places");
        assert_eq!(TimeScale::new(f64::NAN).get(), 1.0, "nonsense must not poison the weight");
        assert_eq!(TimeScale::new(f64::INFINITY).get(), 1.0);
    }

    /// Halving the recording scale halves the block, so a passage still inside
    /// its rotation window becomes eligible.
    #[test]
    fn halving_the_recording_scale_halves_the_block() {
        let rot = seconds(DEF_ROTATION_REC);
        let mut c = candidate();
        c.recording_age_s = Some(rot * 0.6);
        assert_eq!(weigh(&c, &Policy::default()).excluded, Some(Exclusion::RecordingRotationBlock));

        let faster = Policy { recording_scale: TimeScale::new(0.5), ..Default::default() };
        assert!(weigh(&c, &faster).is_eligible(), "half the block means it has passed");
    }

    /// Doubling doubles it, and the ramp with it: what was fully recovered at
    /// 1.0 is only halfway at 2.0.
    #[test]
    fn doubling_the_recording_scale_stretches_the_ramp() {
        let (rot, rec) = (seconds(DEF_ROTATION_REC), seconds(DEF_RECOVERY_REC));
        let mut c = candidate();
        c.recording_age_s = Some(rot * 2.0 + rec); // fully recovered at scale 1.0
        assert!((weigh(&c, &Policy::default()).recording_ramp - 1.0).abs() < 1e-9);

        let slower = Policy { recording_scale: TimeScale::new(2.0), ..Default::default() };
        let w = weigh(&c, &slower);
        assert!(w.recording_ramp < 1.0, "still recovering under a doubled scale: {}", w.recording_ramp);
    }

    /// The two scales are independent: a recording scale must not move an
    /// artist block, or the single dial the listener reached for would move
    /// two.
    #[test]
    fn the_scales_do_not_leak_into_each_other() {
        let art_rot = seconds(DEF_ROTATION_ART);
        let mut c = candidate();
        c.artist_age_s = Some(art_rot * 0.6);
        let recording_only = Policy { recording_scale: TimeScale::new(0.5), ..Default::default() };
        assert_eq!(weigh(&c, &recording_only).excluded, Some(Exclusion::ArtistRotationBlock),
                   "the recording scale must not shorten an artist block");
        let artist_only = Policy { artist_scale: TimeScale::new(0.5), ..Default::default() };
        assert!(weigh(&c, &artist_only).is_eligible());
    }

    /// Scales must reach relations too, which share the recording's windows.
    #[test]
    fn the_recording_scale_reaches_related_recordings() {
        let rot = seconds(DEF_ROTATION_REC);
        let rel = [Related { age_s: Some(rot * 0.6), strength: 1.0 }];
        let mut c = candidate();
        c.related = &rel;
        assert_eq!(weigh(&c, &Policy::default()).excluded, Some(Exclusion::RelatedRotationBlock));
        let faster = Policy { recording_scale: TimeScale::new(0.5), ..Default::default() };
        assert!(weigh(&c, &faster).is_eligible());
    }

    /// A scale of 1.0 must be exactly inert -- the default cannot cost anything.
    #[test]
    fn a_unit_scale_changes_nothing() {
        let (rot, rec) = (seconds(DEF_ROTATION_REC), seconds(DEF_RECOVERY_REC));
        let rel = [Related { age_s: Some(rot + rec * 0.4), strength: 0.7 }];
        let mut c = candidate();
        c.related = &rel;
        c.recording_age_s = Some(rot + rec * 0.75);
        c.artist_age_s = Some(seconds(DEF_ROTATION_ART) * 3.0);
        let explicit = Policy {
            coupling: ArtistCoupling::Damped,
            artist_scale: TimeScale::new(1.0),
            recording_scale: TimeScale::new(1.0),
            skip_suppress_s: 0.0,
            dequeue_suppress_s: 0.0,
        };
        assert_eq!(weigh(&c, &Policy::default()), weigh(&c, &explicit));
    }

    // ----------------------------------------------------------------- misc

    #[test]
    fn restraint_spans_boost_to_suppression() {
        let mut c = candidate();
        c.recording.restraint = -0.939; // the observed maximum boost
        assert!((weigh(&c, &Policy::default()).weight - 8.687).abs() < 0.01);
        c.recording.restraint = 5.0; // "never again"
        assert_eq!(weigh(&c, &Policy::default()).excluded, Some(Exclusion::BelowMinWeight));
    }

    #[test]
    fn the_length_bonus_favours_three_minutes_and_caps_at_two() {
        assert!((length_bonus(180.0) - 1.0).abs() < 1e-9, "the midpoint is neutral");
        assert!((length_bonus(45.0) - 2.0).abs() < 1e-9, "4x capped, so sqrt is 2x");
        assert!((length_bonus(1.0) - 2.0).abs() < 1e-9, "the cap holds however short");
        assert!(length_bonus(720.0) < 1.0, "long passages are mildly disfavoured");
    }

    #[test]
    fn passage_filters_reject_outside_the_playable_range() {
        let mut c = candidate();
        c.length_s = 29.0;
        assert_eq!(weigh(&c, &Policy::default()).excluded, Some(Exclusion::TooShort));
        c.length_s = 3601.0;
        assert_eq!(weigh(&c, &Policy::default()).excluded, Some(Exclusion::TooLong));
        c.length_s = 180.0;
        c.depth_s = 10801.0;
        assert_eq!(weigh(&c, &Policy::default()).excluded, Some(Exclusion::TooDeep));
    }

    #[test]
    fn the_occasion_multiplier_stays_legible() {
        let mut c = candidate();
        c.occasion = 3.9; // christmasy 0.9 against a 4.2 curve, in December
        let w = weigh(&c, &Policy::default());
        assert!((w.weight - 3.9).abs() < 1e-9);
        assert_eq!(w.occasion, 3.9, "the panel needs the term, not just the product");
    }

    #[test]
    fn the_minimum_weight_boundary_is_exclusive() {
        let mut c = candidate();
        c.recording.restraint = -(MIN_WEIGHT.log10()); // weight lands exactly on 0.001
        let w = weigh(&c, &Policy::default());
        assert_eq!(w.excluded, Some(Exclusion::BelowMinWeight), "weight was {}", w.weight);
    }
}
