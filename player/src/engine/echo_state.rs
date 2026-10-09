//! What the engine keeps for echo playback `[SPEC-ECHO-010]`: the frame-clock
//! basis it publishes, the schedule it follows, and the corrections it owes.
//!
//! Gathered here, out of `Engine`, so the mixer's own state reads apart from
//! the state of keeping in step with another node. The behaviour lives where it
//! always did, in `engine/mod.rs` and `echo.rs`; this is only where it is kept.
//!
//! Until 2026-10-09 each was a field of `Engine` named `echo_<name>`, which is
//! how the echo guides, GUIDE021 to GUIDE029, refer to them.

use crate::queue::QueueEntry;

pub(crate) struct EchoState {
    /// Whether the frame clock may still be compared against an echo anchor,
    /// and the counters that decide it `[GDE-ECHO-360]`.
    ///
    /// Derived from counters rather than hooked into each event, deliberately:
    /// a device reopen, an underrun and a pause are raised in three different
    /// places and a fourth could be added without anyone remembering this.
    /// Watching the numbers move catches every path, including ones not
    /// anticipated here.
    pub(crate) basis: crate::echo::Basis,
    /// The most recent forward schedule, republished until superseded. Every
    /// message is absolute and idempotent `[GDE-ECHO-320]`, so repeating one
    /// costs nothing and a node that missed the first simply uses this.
    pub(crate) schedule: Option<crate::echo::Schedule>,
    /// Samples of the output ring this node must leave EMPTY `[LOG-ECHO-030]`.
    ///
    /// Zero means "fill to capacity", which is what a node with the fleet's
    /// shortest device delay does. Every other node runs shallower by its own
    /// excess over that minimum, so that `depth + device_delay` comes to the
    /// same total everywhere and one sample sounds at one instant across the
    /// fleet. `bose` at 46.3 ms behind `lempiplay3`'s 42.2 ms leaves 181
    /// frames `[LOG-P4-140]`.
    pub(crate) depth_shortfall: usize,
    /// This node's hand-set delay trim, ms `[SPEC-DLY-010]`.
    pub(crate) delay_trim_ms: i64,
    /// The node this one follows, bare host; empty is independent
    /// `[SPEC-ECHO-010]`.
    pub(crate) follow_host: String,
    /// Join at once, or wait for the followed node's next passage
    /// `[SPEC-ECHO-030]`.
    pub(crate) join_now: bool,
    /// The fitted relative rate error, ppm `[GDE-ECHO-340]`.
    pub(crate) rate_ppm: f64,
    /// How long a commanded start takes to become audible, ms
    /// `[GDE-ECHO-342]`.
    ///
    /// Measured, not assumed. The lead a skip applies is a constant, but the
    /// work before it -- opening the file, seeking, building the resampler,
    /// and topping the decoder up so the overlay is not silence
    /// `[PI-CHR-075]` -- takes as long as the card and the passage make it.
    /// That time lands directly on the air, so a start fired early by a
    /// constant is late by however long the work took: ~900 ms on this fleet,
    /// which was the whole of the lag a listener could hear.
    ///
    /// Smoothed, because the next join's prep is better predicted by the last
    /// few than by any constant, and the first one has to guess something.
    pub(crate) prep_ms: u64,
    /// Position still to shed by trimming, in frames `[GDE-ECHO-349]`.
    ///
    /// Positive when this node is late and must advance. Counted in frames
    /// rather than milliseconds because that is the resolution the actuator
    /// actually has -- 23 us -- and rounding to a millisecond here would
    /// throw away forty times the precision the endgame exists to reach.
    pub(crate) debt_frames: i64,
    /// When a frame was last trimmed. Monotonic, because a wall clock can
    /// step `[GDE-ECHO-365]` and this is an interval.
    pub(crate) last_trim: Option<std::time::Instant>,
    /// Whether the last trim the mixer tried was refused, so a refusal is
    /// reported once rather than twenty times a second `[GDE-ECHO-378]`.
    pub(crate) trim_refused: bool,
    /// The instant a commanded start's sample 0 must **sound**, held from
    /// `fire_echo_start` until the ring is cut `[GDE-ARC-058]`.
    ///
    /// Carried rather than converted to a depth at the point it is learned,
    /// because the depth is only correct if it is computed after the
    /// preparation -- which is the entire difference between this and the
    /// `prep_ms` guess it replaces `[GDE-ECHO-342]`.
    pub(crate) join_at: Option<u64>,
    /// What this node's past joins landed at `[GDE-ARC-061]`.
    pub(crate) join_bias: crate::echo::JoinBias,
    /// Frames of silence still owed before the next passage's audio
    /// `[GDE-ARC-052]`.
    ///
    /// **The actuator a follower running ahead never had.** The ring is
    /// contiguous, so declining to submit does not delay anything — it only
    /// makes the buffer shallower, and the audio airs at the same instant
    /// either way `[a_pause_in_writing_leaves_no_gap_in_the_audio]`. Moving
    /// sound later means putting something in front of it, and silence is
    /// the only thing that can go there without inventing content.
    ///
    /// Spent by the mixer a block at a time. While it is outstanding the
    /// live streams are not mixed at all, so their decoded audio waits in
    /// their own rings and the passage lands whole, only later.
    pub(crate) gap_frames: u64,
    /// An offset correction waiting for the next admission, ms earlier
    /// `[GDE-ECHO-340]`. Zero is no correction, which is also the resting
    /// state of a node that is already level.
    pub(crate) next_shift_ms: i64,
    /// A start instant committed to but not yet reached `[GDE-ECHO-330]`.
    pub(crate) start: Option<(QueueEntry, u64, u64)>,
    pub(crate) seen_recoveries: u64,
    pub(crate) seen_underruns: u64,
    /// Whether `shown` was promoted by `advance_shown`'s own audibility test
    /// rather than set eagerly by a cut `[GDE-ARC-059]`.
    pub(crate) shown_is_sounding: bool,
}

impl EchoState {
    /// A node playing alone, with `prep_guess_ms` as the first guess at how
    /// long a commanded start takes to sound `[GDE-ECHO-342]`.
    pub(crate) fn new(prep_guess_ms: u64) -> Self {
        Self {
            basis: crate::echo::Basis::default(),
            schedule: None,
            depth_shortfall: 0,
            delay_trim_ms: 0,
            follow_host: String::new(),
            join_now: true,
            rate_ppm: 0.0,
            prep_ms: prep_guess_ms,
            debt_frames: 0,
            last_trim: None,
            trim_refused: false,
            join_at: None,
            join_bias: crate::echo::JoinBias::default(),
            gap_frames: 0,
            next_shift_ms: 0,
            start: None,
            seen_recoveries: 0,
            seen_underruns: 0,
            shown_is_sounding: false,
        }
    }
}
