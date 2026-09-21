//! What counts as a play `[SPEC-PLAY-010]`.
//!
//! One rule, one place, both paths. The local engine and the MPD Director must
//! agree about this or `listener_play_history` means two different things
//! depending on which player was running — which is exactly what it did until
//! `[SPEC-PLAY-030]` was settled.
//!
//! **Half the passage, or four minutes, whichever comes first.** Last.fm and
//! ListenBrainz both use this threshold, so Lempi's rotation ledger agrees with
//! whatever scrobbler the listener already runs without either writing the
//! other's data.
//!
//! **Minus their minimum-length exclusion, deliberately** `[SPEC-PLAY-020]`.
//! Last.fm ignores tracks under 30 s and ListenBrainz under 5; that is an
//! anti-spam rule for a public service and does not apply to a private rotation
//! ledger. The shortest radio passage in this library is 12 s and one that
//! played in full did play.

/// The cap, in milliseconds. A twenty-minute piece needs four minutes, not ten.
pub const FOUR_MINUTES_MS: u64 = 240_000;

/// Has enough of this passage been heard to count as a play?
///
/// `heard_ms` is what the listener actually heard — for the local engine that
/// means *audible* position, net of output buffering, not decoder position.
/// `span_ms` is the **passage** span, not the file's length: two passages in one
/// capture have different lengths and the same file duration `[SPEC-DF-030]`.
///
/// **A span of zero is never a play.** An unknown length made `half of zero`
/// trivially reachable, so a passage that had not played at all passed the
/// threshold. Absent is not zero `[GOV-SRC-040]`.
pub fn counts_as_play(heard_ms: u64, span_ms: u64) -> bool {
    if span_ms == 0 {
        return false;
    }
    heard_ms >= (span_ms / 2).min(FOUR_MINUTES_MS)
}

/// The same question in seconds, for callers reading a protocol rather than a
/// database. Rounds rather than truncates: `5.999999` is six seconds.
pub fn counts_as_play_s(heard_s: f64, span_s: f64) -> bool {
    let ms = |v: f64| if v <= 0.0 { 0 } else { (v * 1000.0).round() as u64 };
    counts_as_play(ms(heard_s), ms(span_s))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn half_a_short_passage_is_enough() {
        assert!(counts_as_play(90_000, 180_000));
        assert!(!counts_as_play(89_000, 180_000));
    }

    #[test]
    fn four_minutes_caps_a_long_one() {
        assert!(counts_as_play(240_000, 1_200_000));
        assert!(!counts_as_play(239_000, 1_200_000));
    }

    #[test]
    fn a_twelve_second_passage_played_whole_is_a_play() {
        // The case an anti-spam floor would discard `[SPEC-PLAY-020]`.
        assert!(counts_as_play(12_000, 12_000));
        assert!(counts_as_play(6_000, 12_000));
        assert!(!counts_as_play(5_999, 12_000));
    }

    #[test]
    fn an_unknown_span_is_never_a_play() {
        assert!(!counts_as_play(0, 0));
        assert!(!counts_as_play(30_000, 0));
        assert!(!counts_as_play_s(30.0, 0.0));
    }

    #[test]
    fn seconds_and_milliseconds_agree_at_the_boundary() {
        assert!(counts_as_play_s(6.0, 12.0));
        assert!(!counts_as_play_s(5.0, 12.0));
        assert!(counts_as_play_s(240.0, 1200.0));
        assert!(!counts_as_play_s(239.0, 1200.0));
    }

    #[test]
    fn an_early_skip_is_not_a_play() {
        assert!(!counts_as_play(10_000, 300_000));
    }
}
