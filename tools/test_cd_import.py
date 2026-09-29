#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `cd_import.py` `[SPEC056]`: the guide as the panel shows it, EAC's
setup as the page ticks it off, and the rip inbox as the page reads it.

    python tools/test_cd_import.py
"""
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cd_import as ci  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def test_markdown_is_safe():
    """[SPEC-CDI-070]: the panel renders Markdown and nothing else -- no tag
    from the source reaches the page, and no link runs script."""
    h = ci.render_markdown("# Ripping a disc\n\n<script>alert(1)</script> **bold** and *it*\n\n"
                           "[evil](javascript:alert(1)) [site](https://example.com) [doc](SPEC056.md)\n\n"
                           "- one\n- two\n\n1. first\n2. second\n\n`**not bold**`")
    check("<script" not in h and "&lt;script&gt;" in h, f"raw HTML is text: {h}")
    check("javascript:" not in h.split("evil")[0] + h.split("evil")[-1] and 'href="javascript' not in h,
          f"a script link is text alone: {h}")
    check('<a href="https://example.com" target="_blank" rel="noopener">site</a>' in h, f"outside link: {h}")
    check(">doc<" not in h and "doc" in h, "a repository document is its text alone")
    check('<h1 id="ripping-a-disc">' in h, f"a heading carries its anchor: {h}")
    check("<strong>bold</strong>" in h and "<em>it</em>" in h, "bold and italics")
    check("<ul><li>one</li><li>two</li></ul>" in h and "<ol><li>first</li><li>second</li></ol>" in h, "lists")
    check("<code>**not bold**</code>" in h, "a code span is left alone")


def test_every_anchor_exists():
    """Every step's ? and every setup fix opens the guide at a heading it has."""
    ids = set(re.findall(r'id="([^"]+)"', ci.guide_html()))
    missing = [a for a in ci.ANCHORS.values() if a not in ids]
    check(not missing, f"anchors the page links but the guide lacks: {missing}")
    page = open(os.path.join(HERE, "console_web", "import.html"), encoding="utf-8").read()
    keys = set(re.findall(r'data-guide="([a-z]+)"', page)) | set(re.findall(r"help\('([a-z]+)'\)", page))
    check(keys and keys <= set(ci.ANCHORS), f"every ? on the page names a known section: {keys - set(ci.ANCHORS)}")


# EAC's registry as it read on the desktop 2026-09-29 -- before any setup -- and
# as it should read after the guide's one-time setup.
DRIVE = "HL-DT-STDVDRAM GP65NS60 PF00"
BEFORE = {"Extraction Options": {"DirectoryUse": b"\0\0\0\0", "DirectorySpecification": "C:\\tmp\\eac-test\\",
                                 "AutoSaveStatus": b"\0", "AddChecksumLogFile": b"\0", "AddCDTextToCUESheet": b"\xff"},
          "StartUp Options": {"CreateEnglishLogFile": b"\0"},
          "drives": {DRIVE: {"UseAccurateRip": b"\xff", "SampleOffset": b"\0\0\0\0", "ExtractionMode": b"\0\0"}}}
INBOX = "C:\\Users\\someone\\Music\\_Rips"
AFTER = {"Extraction Options": {"DirectoryUse": b"\0\0\0\0", "DirectorySpecification": INBOX + "\\",
                                "AutoSaveStatus": b"\xff", "AddChecksumLogFile": b"\xff", "AddCDTextToCUESheet": b"\xff"},
         "StartUp Options": {"CreateEnglishLogFile": b"\xff"},
         "drives": {DRIVE: {"UseAccurateRip": b"\xff", "SampleOffset": (6).to_bytes(4, "little", signed=True),
                            "ExtractionMode": b"\x04\0"}}}


def test_setup_checks():
    """[SPEC-CDI-025]: read, ticked, and the uncalibrated left to a person."""
    before = {c["key"]: c for c in ci.checks(BEFORE, INBOX, installed=True, ffmpeg=True)}
    fix = sorted(k for k, c in before.items() if c["ok"] is False)
    check(fix == sorted(["inbox", "log", "checksum", "english", f"offset:{DRIVE}", f"secure:{DRIVE}"]),
          f"the desktop as it was: six to fix, got {fix}")
    sec = before[f"secure:{DRIVE}"]
    check(sec["ok"] is False and "burst" in sec["detail"], f"the desk before setup was in burst mode: {sec}")
    check(before["inbox"]["detail"].startswith("EAC's folder is C:\\tmp\\eac-test"), before["inbox"]["detail"])
    asks = {k: dict(v) for k, v in AFTER.items()}
    asks["Extraction Options"]["DirectoryUse"] = b"\x01\0\0\0"
    inb = next(c for c in ci.checks(asks, INBOX, installed=True, ffmpeg=True) if c["key"] == "inbox")
    check(inb["ok"] is False and "ask every time" in inb["detail"],
          f"the right folder but EAC asking every time is not done: {inb}")
    after = [c for c in ci.checks(AFTER, INBOX, installed=True, ffmpeg=True) if c["ok"] is False]
    check(not after, f"after the guide's setup, nothing to fix: {[c['key'] for c in after]}")
    none = ci.checks(None, INBOX, installed=False, ffmpeg=False)
    check([c["key"] for c in none if c["ok"] is False] == ["installed", "ffmpeg", "settings"],
          f"no EAC at all: said, not failed on, got {[c['key'] for c in none]}")
    check(all(c["guide"] in ci.ANCHORS.values() for c in before.values()), "every fix links the guide")


CUE = ('PERFORMER "Artist"\nTITLE "Album"\nFILE "{stem}.wav" WAVE\n'
       '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n')
LOG_OK = "Copy OK\n\nNo errors occurred\n\nTrack  1  accurately ripped (confidence 5)\nTrack  2  accurately ripped (confidence 5)\n"
LOG_UNSEEN = "Copy OK\n\nNo errors occurred\n\nTrack  1  not present in AccurateRip database\nTrack  2  not present in AccurateRip database\n"
LOG_BAD = "Copy OK\n\nNo errors occurred\n\nTrack  1  accurately ripped (confidence 5)\nTrack  2  not ripped accurately (confidence 2)\n"


def rip(folder, stem, log=None, audio=True, age=600):
    with open(os.path.join(folder, stem + ".cue"), "w", encoding="utf-8") as f:
        f.write(CUE.format(stem=stem))
    if audio:
        p = os.path.join(folder, stem + ".wav")
        with open(p, "wb") as f:
            f.write(b"RIFF" + b"\0" * 64)
        t = time.time() - age
        os.utime(p, (t, t))
    if log is not None:
        with open(os.path.join(folder, stem + ".log"), "w", encoding="utf-8") as f:
            f.write(log)


def test_scan():
    """[SPEC-CDI-030], [SPEC-CDI-035]: each rip's state and verdict, in words."""
    root = tempfile.mkdtemp()
    rip(root, "Clean", LOG_OK)
    rip(root, "Rare", LOG_UNSEEN)
    rip(root, "Bad", LOG_BAD)
    rip(root, "Writing", age=5)
    rip(root, "NoLog", age=600)
    rip(root, "Started", audio=False)
    os.makedirs(os.path.join(root, "Staged"))
    rip(os.path.join(root, "Staged"), "Staged", LOG_OK)
    cards = {c["id"]: c for c in ci.scan(root)}
    level = lambda i: cards[i].get("verdict", {}).get("level")  # noqa: E731
    check(cards["Clean.cue"]["state"] == "finished" and level("Clean.cue") == "ok"
          and "accurately" in cards["Clean.cue"]["verdict"]["text"], f"a clean rip: {cards['Clean.cue']}")
    check(level("Rare.cue") == "ok" and "not in AccurateRip" in cards["Rare.cue"]["verdict"]["text"],
          f"a rare pressing is fine, and says why: {cards['Rare.cue']}")
    check(level("Bad.cue") == "bad" and "Track 2" in cards["Bad.cue"]["verdict"]["text"],
          f"a mismatch is bad, named: {cards['Bad.cue']}")
    check(cards["Writing.cue"]["state"] == "ripping", "an audio file still moving, with no log: still ripping")
    check(cards["NoLog.cue"]["state"] == "finished" and level("NoLog.cue") == "note",
          "a settled rip with no log: finished, and it says to turn the report on")
    check(cards["Started.cue"]["state"] == "ripping", "a CUE whose audio is not there yet: still ripping")
    staged = cards[os.path.join("Staged", "Staged.cue")]
    check(staged["staged"] and level(os.path.join("Staged", "Staged.cue")) == "ok", f"a staged rip: {staged}")
    check(cards["Clean.cue"]["title"] == "Artist - Album" and cards["Clean.cue"]["tracks"] == 2,
          f"titled from its CUE: {cards['Clean.cue']}")


def test_stage():
    """A loose rip gets a folder of its own, taking only its own files; a rip
    already in one stays; nothing outside the inbox is touched."""
    root = tempfile.mkdtemp()
    rip(root, "One", LOG_OK)
    rip(root, "Two", LOG_OK)
    folder = ci.stage(root, "One.cue")
    check(sorted(os.listdir(folder)) == ["One.cue", "One.log", "One.wav"], f"its own files: {os.listdir(folder)}")
    check(sorted(n for n in os.listdir(root) if os.path.isfile(os.path.join(root, n)))
          == ["Two.cue", "Two.log", "Two.wav"], "the other rip is untouched")
    check(ci.stage(root, os.path.join("One", "One.cue")) == folder, "a staged rip stays where it is")
    try:
        ci.stage(root, os.path.join("..", "elsewhere.cue"))
        check(False, "a path out of the inbox is refused")
    except ValueError:
        pass


def test_inbox_setting():
    """The inbox is the console's own setting, with a default in the music folder."""
    side = os.path.join(tempfile.mkdtemp(), "console.db")
    import sqlite3
    db = sqlite3.connect(side)
    db.execute("CREATE TABLE remote_config (key TEXT PRIMARY KEY, value TEXT)")
    db.commit()
    db.close()
    check(ci.inbox(side, "C:\\Music") == os.path.join("C:\\Music", "_Rips"), "the default is _Rips in the music folder")
    chosen = os.path.join(tempfile.mkdtemp(), "rips")
    ci.set_inbox(side, chosen)
    check(ci.inbox(side, "C:\\Music") == os.path.normpath(chosen) and os.path.isdir(chosen), "a chosen inbox is kept, and made")


def main() -> int:
    test_markdown_is_safe()
    test_every_anchor_exists()
    test_setup_checks()
    test_scan()
    test_stage()
    test_inbox_setting()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("cd_import: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
