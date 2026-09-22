#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `analyze_amplitude.py` `[SPEC-SA-075]`.

The pure math is tested against synthetic numpy arrays (fast, no ffmpeg, no
subprocess). One real end-to-end case decodes a genuinely ffmpeg-produced
file: a WAV built directly from silence/tone segments (Python's own `wave`
module, no committed binary, nothing ffmpeg's own fade-curve shaping could
make ambiguous -- an `afade`-based linear ramp turned out *not* to exercise
the "slow ramp" path at all: with absolute thresholds this quiet
(`~0.0056`/`0.01`), a linear ramp toward a loud peak crosses them within a
few tens of milliseconds regardless of the fade's own nominal length, so it
reads as a quick ramp every time -- correct per spec, just not what a hard
silence/tone cut demonstrates). A silence/tone/silence hard cut is
unambiguous: verified live to detect `lead_in_ms=2000` exactly and
`lead_out_ms=2100` (one window's own 100 ms quantization) against a real
2.0s/3.0s/2.0s construction.

    python tools/test_analyze_amplitude.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze_amplitude as aa  # noqa: E402

SR = 44100

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def write_wav(path: str, samples: np.ndarray, sample_rate: int = SR) -> None:
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())


def tone(seconds: float, amplitude: float = 0.7, freq: float = 440.0) -> np.ndarray:
    n = int(SR * seconds)
    return amplitude * np.sin(2 * np.pi * freq * np.arange(n) / SR)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds))


# -- pure math, no ffmpeg -----------------------------------------------------

def test_threshold_sign() -> None:
    print("threshold_lead_in/out: corrected sign, both well inside [0, 1]")
    check(0 < aa.THRESHOLD_LEAD_IN < 1, f"got {aa.THRESHOLD_LEAD_IN}")
    check(0 < aa.THRESHOLD_LEAD_OUT < 1, f"got {aa.THRESHOLD_LEAD_OUT}")
    check(abs(aa.THRESHOLD_LEAD_IN - 0.005623) < 1e-5, f"got {aa.THRESHOLD_LEAD_IN}")
    check(abs(aa.THRESHOLD_LEAD_OUT - 0.01) < 1e-9, f"got {aa.THRESHOLD_LEAD_OUT}")
    # The inherited spec's own literal formula (uncorrected sign) is what
    # this must NOT match -- a regression back to it would silently produce
    # thresholds >1, impossible for a bounded-to-[0,1] RMS envelope.
    wrong = 10 ** (45.0 / 20)
    check(aa.THRESHOLD_LEAD_IN != wrong and wrong > 1, f"the uncorrected formula gives {wrong}")


def test_rms_envelope_shape() -> None:
    print("rms_envelope(): one value per 100ms window, trailing partial window dropped")
    samples = np.ones(int(SR * 2.05))  # 2.05s -- 20 full 100ms windows, one partial
    env = aa.rms_envelope(samples)
    check(len(env) == 20, f"expected 20 windows, got {len(env)}")
    check(np.allclose(env, 1.0), f"a constant-1.0 signal must have RMS 1.0 throughout, got {env[:3]}")

    check(len(aa.rms_envelope(np.array([]))) == 0, "an empty signal must not crash")


def test_quick_ramp_shortcut() -> None:
    print("detect_lead_in/out(): a signal already loud at the very start/end is a quick ramp, not a slow one")
    env = np.full(50, 0.5)  # loud (>> both thresholds) from window 0
    lead_in_ms, quick_up = aa.detect_lead_in(env)
    check((lead_in_ms, quick_up) == (0, True), f"got {(lead_in_ms, quick_up)}")
    lead_out_ms, quick_down = aa.detect_lead_out(env)
    check((lead_out_ms, quick_down) == (0, True), f"got {(lead_out_ms, quick_down)}")


def test_constant_amplitude_edge_case() -> None:
    print("[AMP-EDGE-010]: constant amplitude throughout falls out to 0/0 with no special-casing")
    for level in (0.5, aa.THRESHOLD_LEAD_OUT * 0.5):  # loud-constant, quiet-constant
        env = np.full(40, level)
        lead_in_ms, _ = aa.detect_lead_in(env)
        lead_out_ms, _ = aa.detect_lead_out(env)
        check(lead_in_ms == 0 and lead_out_ms == 0, f"level={level}: got {(lead_in_ms, lead_out_ms)}")


def test_never_gets_loud() -> None:
    print("a passage that never crosses the lead-out threshold at all reports 0/0, not a crash")
    env = np.full(40, aa.THRESHOLD_LEAD_OUT * 0.1)
    check(aa.detect_lead_in(env) == (0, False), "got a non-(0,False) result")
    check(aa.detect_lead_out(env) == (0, False), "got a non-(0,False) result")


def test_slow_ramp_synthetic() -> None:
    print("a genuinely gradual envelope (quiet for over 1s before climbing) is detected, not shortcut")
    # A *linear* ramp toward a loud peak crosses these (tiny, absolute)
    # thresholds almost immediately relative to its own nominal length --
    # true of a real fade too, confirmed against ffmpeg's own `afade` while
    # designing this test (see module docstring). What the quick-ramp
    # shortcut is actually for is a passage that stays quiet for over a
    # second before anything happens, which this constructs directly: 1.2s
    # genuinely below both thresholds, only then climbing.
    quiet = np.full(12, 0.001)
    ramp = np.linspace(0.001, 0.5, 8)
    env = np.concatenate([quiet, ramp, np.full(20, 0.5)])
    lead_in_ms, quick_up = aa.detect_lead_in(env)
    check(quick_up is False, f"1.2s of genuine quiet must not be shortcut as quick, got quick_up={quick_up}")
    check(lead_in_ms > 1000, f"expected a lead-in past the 1.2s quiet stretch, got {lead_in_ms}")


# -- one real end-to-end decode, no committed binary --------------------------

def test_real_decode_hard_cut(tmp: str) -> None:
    print("analyze_passage(): a real ffmpeg decode of a 2.0s/3.0s/2.0s silence-tone-silence WAV")
    path = os.path.join(tmp, "hardcut.wav")
    samples = np.concatenate([silence(2.0), tone(3.0), silence(2.0)])
    write_wav(path, samples)

    result = aa.analyze_passage(path, 0, -1)
    check(result is not None, "a real, decodable file must not return None")
    # One window's own 100ms quantization either side -- not exact-equality,
    # this is a real DSP measurement, not a fixture echo.
    check(abs(result["lead_in_ms"] - 2000) <= 100, f"got {result}")
    check(abs(result["lead_out_ms"] - 2000) <= 200, f"got {result}")
    check(result["quick_ramp_up"] is False and result["quick_ramp_down"] is False, f"got {result}")
    check(result["clipping"] is False, f"a 0.7-amplitude tone must not read as clipping, got {result}")
    check(result["quiet"] is False, f"got {result}")


def test_clipping_detected(tmp: str) -> None:
    print("analyze_passage(): a clipped signal is flagged, analysis still completes [AMP-EDGE-030]")
    path = os.path.join(tmp, "clipped.wav")
    write_wav(path, tone(2.0, amplitude=1.0))  # full-scale -- clips after int16 rounding
    result = aa.analyze_passage(path, 0, -1)
    check(result is not None, "must still complete, not refuse")
    check(result["clipping"] is True, f"got {result}")


def test_decode_pcm_missing_file_returns_none() -> None:
    print("decode_pcm(): a file that does not exist returns None, not an exception")
    check(aa.decode_pcm("Z:/does/not/exist.mp3", 0, -1) is None, "expected None")


# -- selection / gating, against a real SQLite fixture -------------------------

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL, path TEXT NOT NULL);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER,
    kind TEXT, start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER,
    lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT);
"""


def build_fixture(db: str, tmp: str) -> dict:
    """Three files/passages: never analyzed, already auto-analyzed, and
    manually edited -- the three states `select_passages()` must tell apart.
    """
    paths = {}
    for name in ("new", "already", "manual"):
        p = os.path.join(tmp, f"{name}.wav")
        write_wav(p, tone(1.0))
        paths[name] = p
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-new',?)", (paths["new"],))
    c.execute("INSERT INTO passages VALUES (10,1,'radio',0,1000,NULL,NULL,NULL,'ingest:whole-file')")
    c.execute("INSERT INTO files VALUES (2,'md5-already',?)", (paths["already"],))
    c.execute("INSERT INTO passages VALUES (20,2,'radio',0,1000,500,600,NULL,'ingest:whole-file')")
    c.execute("INSERT INTO files VALUES (3,'md5-manual',?)", (paths["manual"],))
    c.execute("INSERT INTO passages VALUES (30,3,'radio',0,1000,1234,5678,-3.0,'manual')")
    c.commit()
    c.close()
    return paths


def test_select_passages_gating(tmp: str) -> None:
    print("select_passages(): only the un-analyzed, non-manual passage by default")
    db = os.path.join(tmp, "gate1.db")
    build_fixture(db, tmp)
    conn = sqlite3.connect(db)

    todo = aa.select_passages(conn, folder=None, recheck=False)
    ids = {r[0] for r in todo}
    check(ids == {10}, f"expected only the never-analyzed passage, got {ids}")

    print("select_passages(): --recheck adds the already-analyzed one, never the manual one")
    todo = aa.select_passages(conn, folder=None, recheck=True)
    ids = {r[0] for r in todo}
    check(ids == {10, 20}, f"expected 10 and 20, never 30 (manual), got {ids}")
    conn.close()


def test_main_writes_and_respects_gating(tmp: str) -> None:
    print("main(): writes lead_in_ms/lead_out_ms + an ingest_decisions row, "
          "never touches the manual passage, idempotent without --recheck")
    db = os.path.join(tmp, "main1.db")
    build_fixture(db, tmp)

    r = subprocess.run([sys.executable, os.path.join(HERE, "analyze_amplitude.py"), db, "--json"],
                       capture_output=True, text=True)
    check(r.returncode == 0, f"exited {r.returncode}: {r.stderr[:400]}")
    summary = json.loads(r.stdout.strip().splitlines()[-1])
    check(summary["analyzed"] == 1, f"expected exactly the one never-analyzed passage, got {summary}")

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT lead_in_ms, lead_out_ms FROM passages WHERE passage_id=10").fetchone()
    check(row[0] is not None and row[1] is not None, f"passage 10 must now have real values, got {row}")
    manual = conn.execute("SELECT lead_in_ms, lead_out_ms FROM passages WHERE passage_id=30").fetchone()
    check(manual == (1234, 5678), f"the manual passage must be untouched, got {manual}")
    already = conn.execute("SELECT lead_in_ms, lead_out_ms FROM passages WHERE passage_id=20").fetchone()
    check(already == (500, 600), f"the already-analyzed passage must be untouched without --recheck, got {already}")
    decisions = conn.execute(
        "SELECT stage FROM ingest_decisions WHERE audio_md5='md5-new'").fetchall()
    check(len(decisions) == 1 and decisions[0][0] == "amplitude_analysis", f"got {decisions}")
    conn.close()

    print("main(): re-running without --recheck is a pure no-op")
    r = subprocess.run([sys.executable, os.path.join(HERE, "analyze_amplitude.py"), db, "--json"],
                       capture_output=True, text=True)
    summary = json.loads(r.stdout.strip().splitlines()[-1])
    check(summary["analyzed"] == 0, f"expected nothing left to do, got {summary}")


def test_folder_scoping(tmp: str) -> None:
    print("--folder: exact directory match only, mirroring suggest_release.py's own convention")
    db = os.path.join(tmp, "folder1.db")
    sub = os.path.join(tmp, "album")
    os.makedirs(sub, exist_ok=True)
    in_folder = os.path.join(sub, "a.wav")
    write_wav(in_folder, tone(1.0))
    outside = os.path.join(tmp, "b.wav")  # a different directory (tmp itself)
    write_wav(outside, tone(1.0))

    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO files VALUES (1,'md5-in',?)", (in_folder,))
    c.execute("INSERT INTO passages VALUES (10,1,'radio',0,1000,NULL,NULL,NULL,'ingest:whole-file')")
    c.execute("INSERT INTO files VALUES (2,'md5-out',?)", (outside,))
    c.execute("INSERT INTO passages VALUES (20,2,'radio',0,1000,NULL,NULL,NULL,'ingest:whole-file')")
    c.commit()
    c.close()

    conn = sqlite3.connect(db)
    todo = aa.select_passages(conn, folder=sub, recheck=False)
    ids = {r[0] for r in todo}
    check(ids == {10}, f"expected only the passage inside {sub}, got {ids}")
    conn.close()


def main() -> int:
    test_threshold_sign()
    test_rms_envelope_shape()
    test_quick_ramp_shortcut()
    test_constant_amplitude_edge_case()
    test_never_gets_loud()
    test_slow_ramp_synthetic()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_real_decode_hard_cut(tmp)
        test_clipping_detected(tmp)
        test_decode_pcm_missing_file_returns_none()
        test_select_passages_gating(tmp)
        test_main_writes_and_respects_gating(tmp)
        test_folder_scoping(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("analyze_amplitude: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
