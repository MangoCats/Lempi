//! The tuning constants selection is decided by, and their bounds.
//!
//! **These were in `lempi_player`'s crate root until 2026-09-22**, and they
//! moved here for the reason the whole crate exists: `db::player_store` reads
//! every one of them as the fallback for a setting nobody has stored, and
//! `director::library` reads two more. A constant whose only readers are in
//! this crate does not belong in the other one.
//!
//! They are re-exported from `lempi_player`'s root under their original names,
//! so `crate::SKIP_SUPPRESS_H` still resolves in all forty-odd places that say
//! it.
//!
//! **Each default travels with its own MIN and MAX.** Leaving the bounds
//! behind in the player would have made a listener setting's legal range and
//! its default two facts in two crates — which is `[GDE-ARC-033]`'s fault
//! exactly, and the reason [`DEFAULT_QUEUE_DEPTH`] below is a string rather
//! than a number.

/// Read one of the defaults below as a number, at compile time.
///
/// The string is the single definition and the `const` is derived from it,
/// rather than the two being written out separately and trusted to agree
/// `[GDE-ARC-033]`. Panics at compile time on a non-decimal default, which is
/// the only way this can be wrong.
///
/// Lived in `lempi_player::cli` when the only defaults it read were the CLI's
/// own; it is here now because the defaults it reads are here, and
/// `lempi_player::cli` re-exports it so `crate::cli::as_number` is unchanged.
pub const fn as_number(s: &str) -> u64 {
    let b = s.as_bytes();
    let mut i = 0;
    let mut n = 0u64;
    while i < b.len() {
        assert!(b[i] >= b'0' && b[i] <= b'9', "a default read as a number is not one");
        n = n * 10 + (b[i] - b'0') as u64;
        i += 1;
    }
    n
}

/// Most tracks one browse request will answer with `[REQ-VIS-180]`.
///
/// A library of 8,000 passages returns in 80 ms, but the number is sent to the
/// browser rather than assumed there, so the page can say "showing the first
/// 2,000" without a second copy of this constant to fall out of step.
pub const BROWSE_LIMIT: usize = 2_000;

/// How long Skip takes to fade the outgoing passage out `[REQ-AUD-158]`.
///
/// The listener is hearing audio mixed up to a ring's depth ago, so a skip can
/// only be prompt if what was already submitted is cut short. This is how much
/// of it survives, and over how long it falls away.
pub const SKIP_FADE_MS: u64 = 2_000;
pub const SKIP_FADE_MAX_MS: u64 = 10_000;

/// How long after a skip the next passage begins its normal fade-in
/// `[REQ-AUD-162]`.
///
/// Shorter than the fade-out, so the two overlap and are summed for the
/// difference -- 1.5 s with both at their defaults. The overlap is what makes a
/// skip sound like a transition rather than a stop followed by a start, and it
/// costs nothing extra because the incoming passage is already decoded
/// `[REQ-AUD-160]`.
pub const SKIP_LEAD_MS: u64 = 500;
pub const SKIP_LEAD_MIN_MS: u64 = 100;
pub const SKIP_LEAD_MAX_MS: u64 = 2_000;

/// How often the resume point is written `[REQ-VIS-155]`.
///
/// Every write lands on the appliance's most volatile partition
/// `[PI-C-010]`, and this is the only one that happens continuously and
/// unattended -- so it is the write rate that decides how much of that
/// partition's life is spent with a write in flight.
///
/// Five seconds rather than one, which is what it was. The cost of the longer
/// interval is bounded and small: at most this much playback position is lost
/// to a power cut, and the *interesting* transitions -- passage change, pause,
/// resume -- bypass the throttle entirely and are written the moment they
/// happen. So the setting trades a few seconds of position, never an event.
pub const RESUME_SAVE_MS: u64 = 5_000;
pub const RESUME_SAVE_MIN_MS: u64 = 1_000;
pub const RESUME_SAVE_MAX_MS: u64 = 300_000;

/// How long a *skipped* passage is held out of selection `[SPEC-PLAY-050]`.
///
/// 156 hours is six and a half days: long enough that a rejected passage does
/// not return within the week, and offset from a whole week so it does not
/// come back on the same day at the same time.
pub const SKIP_SUPPRESS_H: u64 = 156;
/// Zero is a legitimate setting: it turns skip suppression off entirely.
pub const SKIP_SUPPRESS_MIN_H: u64 = 0;
pub const SKIP_SUPPRESS_MAX_H: u64 = 8_760; // a year

/// How long a passage *removed from the queue before it played* is held out
/// `[SPEC-PLAY-055]`. Shorter than a skip: declining to hear something now is a
/// weaker statement than stopping it once it had started.
pub const DEQUEUE_SUPPRESS_H: u64 = 18;
pub const DEQUEUE_SUPPRESS_MIN_H: u64 = 0;
pub const DEQUEUE_SUPPRESS_MAX_H: u64 = 8_760;

/// How many passages the Director keeps queued ahead `[SPEC-MPD-105]`, as the
/// string `--depth`'s default is also written from.
///
/// **A string because the CLI needs one.** `lempi_player`'s
/// `default_queue_depth!()` expands to this constant, so the option's default
/// and [`QUEUE_DEPTH`] remain one definition across the crate boundary rather
/// than two copies of `5` `[GDE-ARC-033]` -- which is what they were before the
/// macro existed, and what a plain `usize` here would have made them again.
pub const DEFAULT_QUEUE_DEPTH: &str = "5";

/// How many passages the Director keeps queued ahead `[SPEC-MPD-105]`.
///
/// A listener setting rather than a launch flag: it governs the local engine
/// and the MPD Director alike, and both read it from the same row.
pub const QUEUE_DEPTH: usize = as_number(DEFAULT_QUEUE_DEPTH) as usize;
/// One is the floor: below it there is no lookahead, and the crossfade has
/// nothing to fade into.
pub const QUEUE_DEPTH_MIN: usize = 1;
pub const QUEUE_DEPTH_MAX: usize = 50;

/// How often a guest's `status` is read while playing, in milliseconds, as the
/// string `--interval`'s default is also written from. See
/// [`DEFAULT_QUEUE_DEPTH`] for why this is a string.
pub const DEFAULT_SAMPLE_INTERVAL_MS: &str = "5000";

/// How often `status` is read while playing, to judge a play against
/// `[SPEC-PLAY-010]`'s threshold and to end a span MPD would not
/// `[SPEC-MPD-096]`. Five seconds `[SPEC-MPD-105]`.
pub const SAMPLE_INTERVAL_MS: u64 = as_number(DEFAULT_SAMPLE_INTERVAL_MS);
pub const SAMPLE_INTERVAL_MIN_MS: u64 = 1_000;
pub const SAMPLE_INTERVAL_MAX_MS: u64 = 60_000;
