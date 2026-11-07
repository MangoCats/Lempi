#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `repair_passage_ends.py` `[SPEC-SA-112]`.

Real MP3s, decoded for real, as `test_repair_durations.py` is: the tool's whole
claim is about what is in the audio, and a mocked envelope would prove nothing.

    python tools/test_repair_passage_ends.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import repair_passage_ends as tool    # noqa: E402

TOOL = os.path.join(HERE, "repair_passage_ends.py")
SCHEMA = os.path.join(os.path.dirname(HERE), "sql", "schema.sql")
FFMPEG = shutil.which("ffmpeg")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def make(path, spec):
    """`spec` is a list of (seconds, amplitude) stretches, joined into one MP3.
    A third item, "fade", ramps that stretch's amplitude linearly down to nothing."""
    parts, labels = [], []
    for i, s in enumerate(spec):
        secs, a = s[0], s[1]
        if a == 0:
            parts.append(f"anullsrc=r=44100:cl=mono,atrim=0:{secs},asetpts=N/SR/TB[p{i}]")
        else:
            fade = f",afade=t=out:st=0:d={secs}" if len(s) > 2 else ""
            # `sine` makes its tone at 1/8 of full scale, so 8x is the amplitude asked for.
            parts.append(f"sine=f=440:r=44100:d={secs},volume={a * 8}{fade}[p{i}]")
        labels.append(f"[p{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(spec)}:v=0:a=1[o]"
    subprocess.run([FFMPEG, "-y", "-nostdin", "-v", "error", "-filter_complex", graph, "-map", "[o]", "-c:a", "libmp3lame", "-b:a", "128k", path], check=True)


class Fixture:
    def __init__(self, tmp):
        os.makedirs(tmp, exist_ok=True)
        self.tmp = tmp
        self.db = os.path.join(tmp, "whole.db")
        self.conn = sqlite3.connect(self.db)
        with open(SCHEMA, encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self.next = 1

    def file(self, name, spec, passages, dur_ms=None):
        """`passages`: (kind, start, end[, boundary_src[, hold]]). Returns their ids."""
        path = os.path.join(self.tmp, name + ".mp3")
        make(path, spec)
        fid = self.next
        self.next += 1
        dur = dur_ms or int(sum(s[0] for s in spec) * 1000)
        self.conn.execute("INSERT INTO files (file_id,audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen) VALUES (?,?,?,0,0,'mp3',?,'x','x')",
                          (fid, f"md5{fid}", path, dur))
        ids = []
        for p in passages:
            src = p[3] if len(p) > 3 else "inherited:mulib"
            hold = p[4] if len(p) > 4 else None
            cur = self.conn.execute("INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src,director_hold,lead_in_ms,lead_out_ms) VALUES (?,?,?,?,?,?,1,1)",
                                    (fid, p[0], p[1], p[2], src, hold))
            ids.append(cur.lastrowid)
        self.conn.commit()
        return ids

    def row(self, pid):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        r = dict(c.execute("SELECT * FROM passages WHERE passage_id=?", (pid,)).fetchone())
        c.close()
        return r


def run(db, *args):
    return subprocess.run([sys.executable, TOOL, db, "--jobs", "4", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def units():
    print("sound_spans and classify, on made envelopes")
    import numpy as np
    loud, silent = 0.1, 0.0
    before = [loud] * 50
    check(tool.classify(np.array(before + [loud] * 100), 5000)["kind"] == "cut", "loud after loud is a cut")
    check(tool.classify(np.array(before + [silent] * 100), 5000)["kind"] == "silence", "nothing after the end is silence")
    check(tool.classify(np.array(before + [loud] * 30 + [silent] * 30 + [loud] * 5), 5000)["kind"] == "gap", "sound, silence, sound is a gap")
    check(tool.classify(np.array(before + [silent] * 30 + [loud] * 50), 5000)["kind"] == "gap", "a silence then sound is a gap too")
    check(tool.classify(np.array(before + [0.0015] * 100), 5000)["kind"] == "silence", "-56 dB is below the sound floor, so it counts as silence")
    quiet_rms = np.array(before + [loud] * 30 + [0.0005] * 63)
    peaks = np.array(before + [loud] * 30 + [0.0005] * 60 + [0.2, 0.0005, 0.0005])
    check(tool.classify(quiet_rms, 5000, peaks)["kind"] == "gap", "a blip too short to lift a window's RMS still marks a gap, by its peak")
    check(tool.classify(np.array(before + [0.008] * 100), 5000)["kind"] == "fade", "audible and quiet is a fade")

    print("a blip in one channel")
    path = os.path.join(tempfile.mkdtemp(prefix="lempi-ends-blip-"), "blip.mp3")
    graph = ("sine=f=440:r=44100:d=20,volume=2[a];anullsrc=r=44100:cl=mono,atrim=0:5,asetpts=N/SR/TB[b];sine=f=440:r=44100:d=0.2,volume=0.04[c];"
             "[a][b][c]concat=n=3:v=0:a=1[l];anullsrc=r=44100:cl=mono,atrim=0:25.2,asetpts=N/SR/TB[r];[l][r]join=inputs=2:channel_layout=stereo[o]")
    subprocess.run([FFMPEG, "-y", "-nostdin", "-v", "error", "-filter_complex", graph, "-map", "[o]", "-c:a", "libmp3lame", "-b:a", "128k", path], check=True)
    env_, peak_, _dec = tool.envelope_of(path)
    check(tool.classify(env_, 10000, peak_)["kind"] == "gap", f"it still marks a gap, though the mix hides it: {tool.classify(env_, 10000, peak_)}")
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    print("split_point")
    env = np.array([loud] * 10 + [0.0005] * 30 + [loud] * 10)
    check(tool.split_point(env, 1000, 4000) == 4000, "the end of the silence, so the next track starts on its sound")
    env = np.array([loud] * 10 + [0.05, 0.02, 0.09, 0.3] + [loud] * 10)
    check(tool.split_point(env, 1000, 1400) == 1100, "no silence: the quietest window")
    env = np.array([loud] * 10 + [0.0005] * 5 + [loud] * 3 + [0.0005] * 20 + [loud] * 5)
    check(tool.split_point(env, 1000, 3800) == 3800, "the longest silence, when there are two")
    check(tool.source_with("inherited:mulib+radio-end:quiet", tool.RADIO_SUFFIX) == "inherited:mulib+radio-end:quiet", "the suffix does not pile up on a second run")


def end_to_end(tmp):
    fx = Fixture(tmp)
    # A loud tone: 40 s, of which the passages hold 20.
    cut_a, cut_r = fx.file("cut", [(40, 0.3)], [("album", 150, 20000), ("radio", 150, 20000)])
    # A quiet tone whose 20 s fade starts at 30 s. The album stops 15 s into it. One
    # radio stops short of the -45 dB point, another well past it.
    fade_spec = [(30, 0.05), (20, 0.05, "fade"), (6, 0)]
    fade_a, fade_r = fx.file("fade_short", fade_spec, [("album", 0, 45000), ("radio", 0, 45000)])
    late_a, late_r = fx.file("fade_late", fade_spec, [("album", 0, 45000), ("radio", 0, 49800)])
    # Only silence after the end.
    sil_a, sil_r = fx.file("silence", [(20, 0.3), (10, 0)], [("album", 0, 20000), ("radio", 0, 20000)])
    # Loud music after the end, then a gap, then a blip: a person's.
    gap_a, gap_r = fx.file("gap", [(25, 0.3), (4, 0), (1, 0.3)], [("album", 0, 20000), ("radio", 0, 20000)])
    # A person set the album end; the radio is still the tool's to move, and vice versa.
    man_a, man_r = fx.file("manual_album", [(40, 0.3)], [("album", 0, 20000, "manual"), ("radio", 0, 20000)])
    man2_a, man2_r = fx.file("manual_radio", [(40, 0.3)], [("album", 0, 20000), ("radio", 0, 20000, "manual")])
    held_a, held_r = fx.file("held", [(40, 0.3)], [("album", 0, 20000, "inherited:mulib", "damaged rip"), ("radio", 0, 20000, "inherited:mulib", "damaged rip")])
    # Two tracks in one file with silence between, and nothing wrong at the end.
    w1, w2 = fx.file("whole", [(10, 0.3), (5, 0), (10, 0.3)], [("album", 500, 10000), ("album", 15000, 25000)])
    # The row says 40 s, the audio is 40 s, but the tail has been claimed already.
    near_a, near_r = fx.file("near", [(40, 0.3)], [("album", 0, 39000), ("radio", 0, 39000)])

    conn = fx.conn
    conn.execute("INSERT INTO lowlevel_cache VALUES ('md51',150,20000,x'00','t','x')")
    conn.execute("INSERT INTO lowlevel_cache VALUES ('md56',0,20000,x'00','t','x')")   # man_a still plays this span
    conn.commit()

    before = {p: fx.row(p) for p in (cut_a, cut_r, sil_a, gap_a, man_a, held_a, w1)}
    print("a rehearsal writes nothing")
    r = run(fx.db, "--plan", os.path.join(tmp, "plan.csv"))
    check(r.returncode == 0, f"rehearsal ran: {r.stderr[-300:]}")
    check(all(fx.row(p) == before[p] for p in before), "no row changed")
    check("for a person" in r.stdout and "gap.mp3" in r.stdout, f"the gap file is named: {r.stdout}")
    check(os.path.getsize(os.path.join(tmp, "plan.csv")) > 100, "the plan was written")

    print("--write")
    r = run(fx.db, "--write")
    check(r.returncode == 0, f"write ran: {r.stdout[-400:]} {r.stderr[-300:]}")
    print("  " + r.stdout.strip().replace("\n", "\n  "))
    a = fx.row(cut_a)
    check(abs(a["end_ms"] - 40000) < 200 and a["start_ms"] == 0, f"a cut album passage is the whole file: {a['start_ms']}..{a['end_ms']}")
    check(a["boundary_src"] == "inherited:mulib+album-edge:decoded", a["boundary_src"])
    x = fx.row(cut_r)
    check(abs(x["end_ms"] - 40000) < 300, f"a cut radio passage ends with the music: {x['end_ms']}")
    check(x["start_ms"] == 150, "radio keeps its start")
    check(x["boundary_src"] == "inherited:mulib+radio-end:quiet", x["boundary_src"])
    check(x["lead_out_ms"] != 1 or x["lead_in_ms"] != 1, "its leads were measured again")

    a, x = fx.row(fade_a), fx.row(fade_r)
    check(abs(a["end_ms"] - 56000) < 300, f"a fade's album passage reaches the file's end: {a['end_ms']}")
    # A 0.05 tone is 0.035 RMS, which fades to -45 dB (0.0056) at gain 0.158: 46.8 s, plus the pad.
    check(46500 < x["end_ms"] < 47800, f"its radio passage ends where the fade gets quiet: {x['end_ms']}")
    check(x["end_ms"] < a["end_ms"] - 5000, "radio well before album, on one file")
    y = fx.row(late_r)
    check(46500 < y["end_ms"] < 47800, f"a radio end past that point moves earlier, to the same place: {y['end_ms']}")

    print("left alone")
    for p, what in ((sil_a, "silence"), (gap_a, "gap"), (gap_r, "gap radio"), (man_a, "manual album"), (man2_r, "manual radio"), (held_a, "held album"), (held_r, "held radio"), (w1, "a whole-album join, without --album-timing")):
        check(fx.row(p)["end_ms"] == before.get(p, fx.row(p))["end_ms"] and fx.row(p)["boundary_src"] in ("inherited:mulib", "manual"), f"{what} untouched: {fx.row(p)['end_ms']} {fx.row(p)['boundary_src']}")
    check(abs(fx.row(man_r)["end_ms"] - 40000) < 300, "beside a manual album end, the radio still moves")
    check(abs(fx.row(man2_a)["end_ms"] - 40000) < 200, "beside a manual radio end, the album still moves")
    check(fx.row(near_a)["end_ms"] == 39000, "a tail under the minimum is not looked at")

    print("the cache and the record")
    c = sqlite3.connect(fx.db)
    check(c.execute("SELECT COUNT(*) FROM lowlevel_cache WHERE audio_md5='md51'").fetchone()[0] == 0, "a span nothing plays loses its cached analysis")
    check(c.execute("SELECT COUNT(*) FROM lowlevel_cache WHERE audio_md5='md56'").fetchone()[0] == 1, "a span the manual passage still plays keeps it")
    d = c.execute("SELECT outcome,detail FROM ingest_decisions WHERE stage='passage_ends' AND audio_md5='md51' ORDER BY decision_id").fetchall()
    check(len(d) == 2, f"each change is recorded: {d}")
    check(json.loads(d[0][1])["old"]["end_ms"] == 20000, "with what it replaced")
    c.close()

    print("a second run finds nothing to do")
    snap = {p: fx.row(p) for p in (cut_a, cut_r, fade_a, fade_r)}
    r = run(fx.db, "--write")
    check(r.returncode == 0 and all(fx.row(p) == snap[p] for p in snap), f"unchanged: {r.stdout[-300:]}")

    print("the limit counts tracks and passages over the whole job")
    c = sqlite3.connect(fx.db)
    done = c.execute("SELECT COUNT(DISTINCT audio_md5), COUNT(*) FROM ingest_decisions WHERE stage='passage_ends'").fetchone()
    check(tool.job_size(c, []) == (done[0], done[1]), f"earlier runs count: {tool.job_size(c, [])} against {done}")
    again = c.execute("SELECT detail FROM ingest_decisions WHERE stage='passage_ends' LIMIT 1").fetchone()[0]
    plan = [{"audio_md5": "md51", "passage_id": json.loads(again)["passage_id"], "kind": "album"}, {"audio_md5": "new", "passage_id": 9999, "kind": "radio"}]
    check(tool.job_size(c, plan) == (done[0] + 1, done[1] + 1), f"something already changed is not counted twice: {tool.job_size(c, plan)}")
    many = [{"audio_md5": "one-file", "passage_id": 10000 + i, "kind": "album"} for i in range(50)]
    check(tool.job_size(c, many)[0] >= 50, "on a whole-album file each album passage is a track")
    c.close()
    snap = fx.row(w1)
    r = run(fx.db, "--album-timing", "--write", "--allow-tracks", "1")
    check(r.returncode == 1 and "REFUSED" in r.stdout, f"past the limit is refused: {r.stdout[-300:]}")
    check(fx.row(w1) == snap, "and writes nothing")
    r = run(fx.db, "--album-timing", "--allow-tracks", "1")
    check(r.returncode == 0, "a rehearsal past the limit still reports")

    print("--only, and a flag")
    only = Fixture(os.path.join(tmp, "only"))
    oc_a, oc_r = only.file("c", [(40, 0.3)], [("album", 0, 20000), ("radio", 0, 20000)])
    of_a, of_r = only.file("f", fade_spec, [("album", 0, 45000), ("radio", 0, 45000)])
    fl_a, fl_r = only.file("flagged_recording", [(40, 0.3)], [("album", 0, 20000), ("radio", 0, 20000)])
    fp_a, fp_r = only.file("flagged_passage", [(40, 0.3)], [("album", 0, 20000), ("radio", 0, 20000)])
    only.conn.execute("INSERT INTO recordings (mbid,title,source) VALUES ('m1','t','x')")
    only.conn.execute("INSERT INTO passage_recordings (passage_id,mbid,source) VALUES (?, 'm1', 'x')", (fl_r,))
    only.conn.execute("INSERT INTO listener_flags VALUES ('recording','m1','now',NULL)")
    only.conn.execute("INSERT INTO listener_flags VALUES ('passage',?,'now',NULL)", (str(fp_a),))
    only.conn.commit()
    only.conn.close()
    r = run(only.db, "--write", "--only", "cut")
    check(r.returncode == 0 and abs(only.row(oc_a)["end_ms"] - 40000) < 200, f"cut applies: {r.stdout[-200:]}")
    check(only.row(of_a)["end_ms"] == 45000 and only.row(of_r)["end_ms"] == 45000, "and a fade waits")
    for p, what in ((fl_a, "the other kind beside a flagged recording"), (fl_r, "a flagged recording's passage"), (fp_a, "a flagged passage"), (fp_r, "the other kind beside a flagged passage")):
        check(only.row(p)["end_ms"] == 20000, f"{what} is left for a person: {only.row(p)['end_ms']}")
    check("a person's" in r.stdout and "flagged_recording" in r.stdout, f"and it is named: {r.stdout[-300:]}")

    print("--album-timing")
    r = run(fx.db, "--album-timing")
    check(fx.row(w1)["start_ms"] == 500, f"its rehearsal writes nothing: {r.stdout[-200:]}")
    r = run(fx.db, "--album-timing", "--write")
    check(r.returncode == 0, f"ran: {r.stdout[-300:]}")
    p1, p2 = fx.row(w1), fx.row(w2)
    check(p1["start_ms"] == 0, "the first track starts at the file's start")
    check(p1["end_ms"] == p2["start_ms"], f"two tracks meet at one point: {p1['end_ms']} {p2['start_ms']}")
    check(10000 <= p1["end_ms"] <= 15100, f"inside what was the gap: {p1['end_ms']}")
    check(abs(p2["end_ms"] - 25000) < 200, f"the last reaches the file's end: {p2['end_ms']}")
    check(fx.row(sil_a)["end_ms"] == 30000, f"a single-track album passage takes the whole file, silence and all: {fx.row(sil_a)['end_ms']}")
    check(fx.row(sil_r)["end_ms"] == 20000, "and its radio passage is not touched")
    check(fx.row(man_a)["end_ms"] == 20000, "manual is still manual")

    print("a plan gone stale is refused whole")
    conn2 = sqlite3.connect(fx.db)
    conn2.isolation_level = None
    good = {"passage_id": sil_r, "kind": "radio", "audio_md5": "md54", "path": "x", "why": "t", "old": {"end_ms": 20000}, "new": {"end_ms": 21000}, "span": (0, 20000)}
    bad = {**good, "passage_id": cut_a, "span": (150, 20000)}
    try:
        tool.apply(conn2, [good, bad])
        check(False, "a stale plan must raise")
    except tool.Stale:
        pass
    check(fx.row(sil_r)["end_ms"] == 20000, "and what came before it in the plan is rolled back")
    conn2.close()
    fx.conn.close()


def main():
    if not FFMPEG:
        print("ffmpeg not found")
        return 2
    units()
    tmp = tempfile.mkdtemp(prefix="lempi-ends-")
    try:
        end_to_end(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILED:
        print(f"\n{len(FAILED)} FAILED")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
