#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `segment_dao.py`'s cascade `[SPEC-SA-115..121]`, `[SPEC024]`.

Every stage that has no audio-decode dependency worth mocking around is
tested here directly, against hand-built synthetic data -- no ffmpeg, no
network, no fixture audio file. `grid_search_profile` and the profile
readers are tested against a synthetic numpy envelope, the same shape
`db_profile` would hand them from a real decode, rather than a real one.

    python tools/test_segment_cascade.py
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import segment_dao  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


# ------------------------------------------------------------------ Stage 3

def test_dp_assembly_exact_partition():
    print("assemble_boundaries_dp: an over-segmented file with a clean split")
    # 5 detected spans, 3 expected tracks: [0,60) should merge with [60,90)
    # since the expected durations are 90, 60, 90 and the detected spans are
    # 60/30/60/30/60 -- the DP should find (0-90),(90-150)? no: try a case
    # engineered to have one obviously-correct partition.
    spans = [(0.0, 30.0), (30.0, 90.0), (90.0, 150.0), (150.0, 180.0), (180.0, 240.0)]
    # True tracks: (0,90) dur 90, (90,180) dur 90, (180,240) dur 60.
    expected = [90.0, 90.0, 60.0]
    got = segment_dao.assemble_boundaries_dp(spans, expected)
    check(got == [(0.0, 90.0), (90.0, 180.0), (180.0, 240.0)], f"got {got}")


def test_dp_assembly_no_op_when_not_over_segmented():
    print("assemble_boundaries_dp: fewer spans than expected tracks is a no-op")
    spans = [(0.0, 100.0)]
    got = segment_dao.assemble_boundaries_dp(spans, [50.0, 50.0])
    check(got == spans, f"got {got}")


def test_dp_assembly_exact_match_is_unchanged():
    print("assemble_boundaries_dp: N == K returns the spans as one-per-group")
    spans = [(0.0, 50.0), (50.0, 100.0)]
    got = segment_dao.assemble_boundaries_dp(spans, [50.0, 50.0])
    check(got == spans, f"got {got}")


# ---------------------------------------------------------------- Stage 4

def test_rms_window_thresholds():
    print("rms_window_ms: the adaptive window per [SPEC-SA-120]")
    check(segment_dao.rms_window_ms(0.2) == 25, "short track -> 25ms")
    check(segment_dao.rms_window_ms(0.3) == 25, "boundary case -> 25ms")
    check(segment_dao.rms_window_ms(0.5) == 50, "mid track -> 50ms")
    check(segment_dao.rms_window_ms(0.6) == 50, "boundary case -> 50ms")
    check(segment_dao.rms_window_ms(2.0) == 100, "long track -> 100ms")


def test_rms_quiet_spot_finds_the_minimum():
    print("rms_quiet_spot: finds the true minimum within the search window")
    window_ms = 100
    # 100 windows of loud (1.0), a dip to 0.0 at index 50 (5.0s), loud again.
    envelope = np.ones(100)
    envelope[50] = 0.0
    got = segment_dao.rms_quiet_spot(envelope, window_ms, expected_pos_s=5.3, search_s=1.0)
    check(abs(got - 5.0) < 1e-9, f"expected 5.0s (index 50), got {got}")


def test_rms_quiet_spot_falls_back_outside_envelope():
    print("rms_quiet_spot: an empty envelope leaves the expected position untouched")
    got = segment_dao.rms_quiet_spot(np.array([]), 100, expected_pos_s=42.0)
    check(got == 42.0, f"got {got}")


# ---------------------------------------------------------------- Stage 5

def test_merge_extra_tracks_folds_the_tail():
    print("merge_extra_tracks: keeps K-1 intact, folds the rest into one")
    spans = [(0.0, 30.0), (30.0, 60.0), (60.0, 90.0), (90.0, 120.0)]
    got = segment_dao.merge_extra_tracks(spans, expected_count=2, max_merges=3)
    check(got == [(0.0, 30.0), (30.0, 120.0)], f"got {got}")


def test_merge_extra_tracks_respects_the_cap():
    print("merge_extra_tracks: past max_merges it refuses rather than over-merge")
    # 6 spans, expect 1 track -> would need 5 merges, over the cap of 3.
    spans = [(float(i), float(i + 1)) for i in range(6)]
    got = segment_dao.merge_extra_tracks(spans, expected_count=1, max_merges=3)
    check(got == spans, f"expected an unchanged refusal, got {got}")


def test_merge_extra_tracks_no_op_when_not_over():
    print("merge_extra_tracks: N <= K is a no-op")
    spans = [(0.0, 30.0), (30.0, 60.0)]
    got = segment_dao.merge_extra_tracks(spans, expected_count=2)
    check(got == spans, f"got {got}")


# ----------------------------------------------------------- profile reader

def _synthetic_envelope(loud_silent_pairs, window_ms=100, silent_level=0.0001, loud_level=0.5):
    """Build an envelope alternating LOUD/silent runs of the given durations
    (seconds), for `silences_from_profile`/`spans_from_profile` to read."""
    out = []
    for loud_s, silent_s in loud_silent_pairs:
        out += [loud_level] * round(loud_s * 1000 / window_ms)
        out += [silent_level] * round(silent_s * 1000 / window_ms)
    return np.array(out)


def test_silences_from_profile_finds_the_gaps():
    print("silences_from_profile: reads silence runs off a synthetic envelope")
    window_ms = 100
    # 5s loud, 1s silent, 5s loud -- one silence run, 5.0s-6.0s.
    env = _synthetic_envelope([(5.0, 1.0), (5.0, 0.0)], window_ms)
    sils = segment_dao.silences_from_profile(env, window_ms, threshold_db=-40, min_silence_s=0.5)
    check(len(sils) == 1, f"expected one silence run, got {sils}")
    if sils:
        s, e = sils[0]
        check(abs(s - 5.0) < 0.15 and abs(e - 6.0) < 0.15, f"got {sils[0]}")


def test_silences_from_profile_ignores_short_gaps():
    print("silences_from_profile: a gap shorter than min_silence_s is not silence")
    window_ms = 100
    env = _synthetic_envelope([(5.0, 0.2), (5.0, 0.0)], window_ms)
    sils = segment_dao.silences_from_profile(env, window_ms, threshold_db=-40, min_silence_s=0.5)
    check(sils == [], f"expected no silence run under the floor, got {sils}")


def test_spans_from_profile_round_trips_to_audible_spans():
    print("spans_from_profile: two tracks separated by one real silence gap")
    window_ms = 100
    env = _synthetic_envelope([(30.0, 1.0), (30.0, 0.0)], window_ms)
    total_s = len(env) * window_ms / 1000.0
    spans = segment_dao.spans_from_profile(env, window_ms, threshold_db=-40, min_silence_s=0.5,
                                           total_s=total_s, min_track_s=5.0)
    check(len(spans) == 2, f"expected two audible spans, got {spans}")


# ------------------------------------------------------------- grid search

def test_grid_search_exact_match_short_circuits():
    print("grid_search_profile: an exact track-count match ends the search")
    window_ms = segment_dao.PROFILE_WINDOW_MS
    # Three tracks (40s, 1s gap each) -- comfortably resolvable somewhere in
    # the grid regardless of exactly which threshold/duration wins.
    env = _synthetic_envelope([(40.0, 1.0), (40.0, 1.0), (40.0, 0.0)], window_ms)
    total_s = len(env) * window_ms / 1000.0
    spans, db, min_s, pct, outcome, tested = segment_dao.grid_search_profile(
        env, window_ms, total_s, expected_count=3, min_track_s=5.0)
    check(outcome == "exact", f"expected an exact match, got outcome={outcome} spans={spans}")
    check(pct == 1.0, f"got {pct}")
    check(len(tested) >= 1, "the grid must report what it tried")


def test_grid_search_none_when_nothing_found():
    print("grid_search_profile: nothing long enough to be a track finds nothing")
    # 0.5s of audio, min_track_s=5.0 -- no grid combination can ever produce
    # a usable span, regardless of threshold or minimum silence.
    env = np.full(5, 0.5)  # 5 windows of 100ms = 0.5s, uniformly loud
    spans, db, min_s, pct, outcome, tested = segment_dao.grid_search_profile(
        env, 100, total_s=0.5, expected_count=2, min_track_s=5.0)
    check(spans is None, f"got {spans}")
    check(outcome == "none", f"got {outcome}")


# ---------------------------------------------------------------- CLI glue

def test_parse_expect_bare_count():
    print("_parse_expect: a bare integer stays Stage 2-only")
    got = segment_dao._parse_expect("12")
    check(got == 12 and isinstance(got, int), f"got {got!r}")


def test_parse_expect_duration_list():
    print("_parse_expect: a comma-separated list becomes durations")
    got = segment_dao._parse_expect("245,198.5,312")
    check(got == [245.0, 198.5, 312.0], f"got {got}")


TESTS = [
    test_dp_assembly_exact_partition,
    test_dp_assembly_no_op_when_not_over_segmented,
    test_dp_assembly_exact_match_is_unchanged,
    test_rms_window_thresholds,
    test_rms_quiet_spot_finds_the_minimum,
    test_rms_quiet_spot_falls_back_outside_envelope,
    test_merge_extra_tracks_folds_the_tail,
    test_merge_extra_tracks_respects_the_cap,
    test_merge_extra_tracks_no_op_when_not_over,
    test_silences_from_profile_finds_the_gaps,
    test_silences_from_profile_ignores_short_gaps,
    test_spans_from_profile_round_trips_to_audible_spans,
    test_grid_search_exact_match_short_circuits,
    test_grid_search_none_when_nothing_found,
    test_parse_expect_bare_count,
    test_parse_expect_duration_list,
]


def main() -> int:
    for t in TESTS:
        t()
        print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("segment_cascade: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
