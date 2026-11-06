#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `cd_import.py` `[SPEC056]`: the guide as the panel shows it,
CUERipper's and EAC's setup as the page ticks it off, and the rip inbox as the
page reads it.

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
    keys = set(re.findall(r'data-guide="([a-z-]+)"', page)) | set(re.findall(r"help\('([a-z-]+)'\)", page))
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


MUSIC = "C:\\Users\\someone\\Music"
# CUERipper's own default path, as its binary carries it, and the guide's.
CR_DEFAULT = "%music%\\%artist%\\[%year% - ]%album%\\%artist% - %album%.cue"


def test_cueripper_checks():
    """[SPEC-CDI-012]: CUERipper's settings file, read and ticked off."""
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "settings.txt")
    # Lines as the desk's own settings.txt has them, 2026-10-01 -- switches as
    # 1/0, and values running over several lines, each continued with "=".
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("Version=226\nEmbedLog=1\nCreateEACLOG=1\nAdvanced={\n=  \"encoders\": [\n=  ]\n=}\n"
                f"OutputAudioType=0\nComboImage=0\nPathFormat={ci.TEMPLATE}\nSecureMode=1\nTestAndCopy=0\n"
                "CUERipper=<CUERipperConfig>\n=  <DefaultLosslessFormat>wav</DefaultLosslessFormat>\n"
                "=</CUERipperConfig>\n")
    cfg = ci.read_cueripper(path)
    check(cfg and cfg["PathFormat"] == ci.TEMPLATE and cfg["SecureMode"] == "1", f"read, BOM and all: {cfg}")
    check(ci.read_cueripper(os.path.join(folder, "none.txt")) is None, "not yet written: None, not a failure")

    def by(cfg, inbox=INBOX):
        return {c["key"]: c for c in ci.cueripper_checks(cfg, inbox, MUSIC, ffmpeg=True)}
    good = by(cfg)
    check([k for k, c in good.items() if c["ok"] is not True] == [], f"the desk's file: every check ticked: {good}")
    check(good["log"]["ok"] is True, "CreateEACLOG=1 is on -- the red cross of 2026-10-01")
    check(by(dict(cfg, ComboImage="1"))["image"]["ok"] is False, "tracks, not image, is a fix")
    check(all(c["guide"] in ci.ANCHORS.values() for c in good.values()), "every fix links the guide")

    unset = by(None)
    check(unset["settings"]["ok"] is False and unset["settings"]["guide"] == ci.ANCHORS["cr-close"],
          f"before CUERipper first closes: say so, and how: {unset['settings']}")

    dflt = by(dict(cfg, PathFormat=CR_DEFAULT))
    check(dflt["inbox"]["ok"] is False and CR_DEFAULT in dflt["inbox"]["detail"]
          and ci.TEMPLATE in dflt["inbox"]["fix"], f"CUERipper's own default is not the inbox: {dflt['inbox']}")
    deep = by(dict(cfg, PathFormat="%music%\\_Rips\\%artist%\\%album%\\%album%.cue"))
    check(deep["inbox"]["ok"] is False and "folder of its own" in deep["inbox"]["detail"],
          f"two folders down, the scan would not see it: {deep['inbox']}")
    own = by(dict(cfg, PathFormat="D:\\Rips\\%artist% - %album%\\%artist% - %album%.cue"), inbox="D:\\Rips\\")
    check(own["inbox"]["ok"] is True, f"a chosen inbox, written out: {own['inbox']}")
    check(by(cfg, inbox="D:\\Rips")["inbox"]["fix"].startswith("In CUERipper's output path box, type D:\\Rips\\%artist%"),
          "the fix names a chosen inbox, not %music%")

    burst = by(dict(cfg, SecureMode="0"))["secure"]
    check(burst["ok"] is False and "burst" in burst["detail"], f"burst mode: {burst}")
    check(by(dict(cfg, SecureMode="2"))["secure"]["ok"] is True, "paranoid is secure too")
    check(by({k: v for k, v in cfg.items() if k != "SecureMode"})["secure"]["ok"] is None, "absent: asked, not guessed")
    check(by(dict(cfg, CreateEACLOG="0"))["log"]["ok"] is False, "a log Vipunen cannot read is a fix")
    check(by({k: v for k, v in cfg.items() if k != "CreateEACLOG"})["log"]["ok"] is None, "absent: asked")

    exe = os.path.join(folder, "CUERipper.exe")
    open(exe, "wb").close()
    check(ci.cueripper_settings_path(exe) == path, "without user_profiles_enabled: beside the program")
    open(os.path.join(folder, "user_profiles_enabled"), "wb").close()
    check(ci.cueripper_settings_path(exe).endswith(os.path.join("CUERipper", "settings.txt"))
          and not ci.cueripper_settings_path(exe).startswith(folder), "with it: the user's profile")
    os.environ[ci.CUERIPPER_ENV] = exe
    try:
        check(ci.find_cueripper() == exe, "LEMPI_CUERIPPER names one anywhere")
    finally:
        del os.environ[ci.CUERIPPER_ENV]


CUE = ('PERFORMER "Artist"\nTITLE "Album"\nFILE "{stem}.wav" WAVE\n'
       '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n')
LOG_OK = "Copy OK\n\nNo errors occurred\n\nTrack  1  accurately ripped (confidence 5)\nTrack  2  accurately ripped (confidence 5)\n"
LOG_UNSEEN = "Copy OK\n\nNo errors occurred\n\nTrack  1  not present in AccurateRip database\nTrack  2  not present in AccurateRip database\n"
LOG_BAD = "Copy OK\n\nNo errors occurred\n\nTrack  1  accurately ripped (confidence 5)\nTrack  2  not ripped accurately (confidence 2)\n"
LOG_MATCHED = ("Track  1\n\n     Suspicious position 0:00:01\n     Copy CRC 11111111\n     Accurately ripped (confidence 5)  [A]\n"
               "     Copy OK\n\nTrack  2\n\n     Copy CRC 22222222\n     Accurately ripped (confidence 5)  [B]\n     Copy OK\n")


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
    rip(root, "Matched", LOG_MATCHED)
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
    check(level("Matched.cue") == "ok" and "accurately" in cards["Matched.cue"]["verdict"]["text"],
          f"read errors AccurateRip matched are a good rip [SPEC-RIP-120]: {cards['Matched.cue']}")
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


def test_stage_splits_a_set():
    """[SPEC-RIP-118]: two discs CUERipper ripped into one folder -- The Very
    Best of Jackson Browne, 2026-10-15 -- each staged into a folder of its own,
    with its own files and a copy of the cover; the importer refuses a folder
    still holding two."""
    import ingest_cd
    root = tempfile.mkdtemp()
    folder = os.path.join(root, "JB - Best")
    os.makedirs(folder)
    for d in (1, 2):
        stem = f"JB - Best(Disc {d})"
        with open(os.path.join(folder, stem + ".cue"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(f'PERFORMER "JB"\nTITLE "Best"\nFILE "{stem}.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n')
        for ext in (".wav", ".log", ".accurip"):
            open(os.path.join(folder, stem + ext), "w").close()
    open(os.path.join(folder, "folder.jpg"), "w").close()
    try:
        ingest_cd.find_rip(folder)
        check(False, "the importer refuses a folder of two discs")
    except ValueError as e:
        check("2 CUE sheets" in str(e), f"and says why: {e}")
    two = ci.stage(root, os.path.join("JB - Best", "JB - Best(Disc 2).cue"))
    check(sorted(os.listdir(two)) == sorted(["JB - Best(Disc 2)" + e for e in (".accurip", ".cue", ".log", ".wav")]
                                            + ["folder.jpg"]), f"disc 2's own files, and the cover: {os.listdir(two)}")
    check(sorted(os.listdir(folder)) == sorted(["JB - Best(Disc 1)" + e for e in (".accurip", ".cue", ".log", ".wav")]
                                               + ["folder.jpg"]), f"disc 1 left as it was: {os.listdir(folder)}")
    check(ci.stage(root, os.path.join("JB - Best", "JB - Best(Disc 1).cue")) == folder,
          "the last disc in the folder is staged where it is")
    check(ingest_cd.find_rip(two)[1].endswith("(Disc 2).cue"), "and each folder is one disc to the importer")


def tracks_rip(folder, files, log=None, age=600):
    """A tracks-mode rip of three tracks; `files` names which were written."""
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "Artist - Album.cue"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write('PERFORMER "Artist"\nTITLE "Album"\n' + "".join(
            f'FILE "0{n}. Song {n}.wav" WAVE\n  TRACK 0{n} AUDIO\n    TITLE "Song {n}"\n    INDEX 01 00:00:00\n'
            for n in (1, 2, 3)))
    t = time.time() - age
    for n in files:
        p = os.path.join(folder, f"0{n}. Song {n}.wav")
        with open(p, "wb") as f:
            f.write(b"RIFF" + b"\0" * 64)
        os.utime(p, (t, t))
    if log is not None:
        with open(os.path.join(folder, "Artist - Album.log"), "w", encoding="utf-8") as f:
            f.write(log)


def test_tracks_scan_and_stage():
    """[SPEC-CDI-090], [SPEC-CDI-094]: a tracks-mode rip's card -- the files
    it has, a track it never wrote named, finished once it has stopped -- and
    a loose one staged with every file it names."""
    root = tempfile.mkdtemp()
    tracks_rip(os.path.join(root, "Stopped"), (1, 2))
    tracks_rip(os.path.join(root, "Writing"), (1, 2), age=5)
    tracks_rip(os.path.join(root, "Logged"), (1, 2, 3), log=LOG_OK + "Track  3  accurately ripped\n")
    cards = {c["id"].split(os.sep)[0]: c for c in ci.scan(root)}
    s = cards["Stopped"]
    check(s["state"] == "finished" and s["mode"] == "tracks" and s["audio"] == "2 of 3 track files"
          and s["missing"] == [{"number": 3, "title": "Song 3"}], f"a stopped rip, its missing track named: {s}")
    check(s["verdict"]["level"] == "bad" and "Track 3 (Song 3) was not ripped" in s["verdict"]["text"]
          and "no log" in s["verdict"]["text"], f"and said: {s['verdict']}")
    check(cards["Writing"]["state"] == "ripping", "files still moving, no log: still ripping")
    logged = cards["Logged"]
    check(logged["state"] == "finished" and logged["verdict"]["level"] == "ok" and not logged["missing"],
          f"every track there, the log clean: {logged}")

    loose = tempfile.mkdtemp()
    tracks_rip(loose, (1, 2), log=LOG_OK)
    with open(os.path.join(loose, "unrelated.txt"), "w") as f:
        f.write("x")
    folder = ci.stage(loose, "Artist - Album.cue")
    check(sorted(os.listdir(folder)) == ["01. Song 1.wav", "02. Song 2.wav", "Artist - Album.cue", "Artist - Album.log"],
          f"a loose tracks rip staged with every file it names, and nothing else: {os.listdir(folder)}")


def long_set(root, *, tracks=True):
    """Two discs in one folder, the way CUERipper leaves a title too long for
    its path -- *She Who Dwells*, 2026-11-05: its names cut so that every CUE
    comes to exactly 254 characters, whatever the inbox's own length. Returns
    `(folder name, folder, stem of disc N)`. `tracks`: a file per track, else
    one image the CUE names."""
    words = "Artist - She Who Dwells in the Secret Place of the Most High Shall Abide Under the Shadow " * 3
    size = (254 - len(os.path.abspath(root)) - 2 - len("(Disc 1).cue")) // 2
    name = words[:size]
    name = name[:-1] + "x" if name.endswith(" ") else name
    folder = os.path.join(root, name)
    os.makedirs(folder)
    stem = lambda d: f"{name}(Disc {d})"                                 # noqa: E731
    for d in (1, 2):
        with open(os.path.join(folder, stem(d) + ".cue"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write('PERFORMER "Artist"\nTITLE "Album"\n')
            if tracks:
                for n in (1, 2):
                    f.write(f'FILE "0{n}. D{d} Song.wav" WAVE\n  TRACK 0{n} AUDIO\n    INDEX 01 00:00:00\n')
                    open(os.path.join(folder, f"0{n}. D{d} Song.wav"), "w").close()
            else:
                f.write(f'FILE "{stem(d)}.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                        '  TRACK 02 AUDIO\n    INDEX 01 03:00:00\n')
                open(os.path.join(folder, stem(d) + ".wav"), "w").close()
    with open(os.path.join(folder, stem(2) + ".log"), "w", encoding="utf-8") as f:
        f.write(LOG_OK)
    open(os.path.join(folder, stem(1)[:-4] + ".accurip"), "w").close()
    open(os.path.join(folder, "folder.jpg"), "w").close()
    return name, folder, stem


def longest_path(folder):
    return max(len(os.path.join(os.path.abspath(folder), n)) for n in os.listdir(folder))


def test_stage_holds_a_long_name_short():
    """[SPEC-NAM-035]: a disc whose files fill the path the ripper could open
    is staged where every file opens, and every later step still reads it. The
    split used to name the folder after the disc whole: 262 characters and
    `[WinError 3] The system cannot find the path specified`."""
    import ingest_cd
    root = tempfile.mkdtemp()
    name, folder, stem = long_set(root)
    check(longest_path(folder) == 254, f"the rip as the ripper left it: {longest_path(folder)}")
    cid = os.path.join(name, stem(2) + ".cue")
    two = ci.stage(root, cid)
    new = os.path.basename(two)
    check(longest_path(two) <= ingest_cd.WINDOWS_PATH_MAX - 1, f"every file opens, a FLAC beside it too: {longest_path(two)}")
    check(new.endswith("(Disc 2)"), f"the folder still says which disc: {new}")
    check(not new[:-len("(Disc 2)")].endswith(" "), f"cut at a word, with nothing trailing: {new!r}")
    have = sorted(os.listdir(two))
    check(have == sorted(["01. D2 Song.wav", "02. D2 Song.wav", new + ".cue", new + ".log", "folder.jpg"]),
          f"disc 2's own files, the track files under their own names: {have}")
    check("01. D1 Song.wav" in os.listdir(folder) and stem(1) + ".cue" in os.listdir(folder),
          "disc 1 left as it was")
    sid = ci.staged_id(root, two, cid)
    check(os.path.isfile(os.path.join(root, sid)) and sid.endswith(new + ".cue"), f"the page's new id is the CUE: {sid}")
    check(ingest_cd.find_rip(two)[1].endswith(new + ".cue"), "the importer finds it")
    check(ingest_cd.find_log(two).endswith(new + ".log"), "and its log")
    renames = ingest_cd.renamed_files(two, os.path.join(root, "Artist", "Album (2020)"), "Artist", 60)
    check(renames.get(new + ".cue") == "Artist - Album.cue" and renames.get(new + ".log") == "Artist - Album.log",
          f"the add still renames them after the album [SPEC-NAM-040]: {renames}")
    card = next(c for c in ci.scan(root) if c["id"] == sid)
    check(card["state"] == "finished" and card["audio"] == "2 of 2 track files"
          and card["verdict"]["level"] == "ok", f"its card reads as before: {card}")

    # The same disc's name again: a second folder, whose files follow it too.
    root2 = tempfile.mkdtemp()
    name2, folder2, stem2 = long_set(root2)
    first = ci.stage(root2, os.path.join(name2, stem2(2) + ".cue"))
    with open(os.path.join(folder2, stem2(3) + ".cue"), "w", encoding="utf-8") as f:
        f.write(f'TITLE "Album"\nFILE "{stem2(3)}.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n')
    open(os.path.join(folder2, stem2(3) + ".wav"), "w").close()
    os.rename(first, first.replace("(Disc 2)", "(Disc 3)"))
    third = ci.stage(root2, os.path.join(name2, stem2(3) + ".cue"))
    tag = os.path.basename(third)
    check(tag.endswith("(Disc 3) (2)") and tag + ".cue" in os.listdir(third), f"a name in use: {tag!r}")
    check(longest_path(third) <= ingest_cd.WINDOWS_PATH_MAX - 1, f"and it fits too: {longest_path(third)}")

    # An image rip: the WAV is named after the disc, so the sheet must follow.
    root = tempfile.mkdtemp()
    name, folder, stem = long_set(root, tracks=False)
    two = ci.stage(root, os.path.join(name, stem(2) + ".cue"))
    new = os.path.basename(two)
    sheet = open(os.path.join(two, new + ".cue"), encoding="utf-8").read()
    check(f'FILE "{new}.wav" WAVE' in sheet and os.path.isfile(os.path.join(two, new + ".wav")),
          f"the sheet names the image's new name: {sheet!r}")
    check(longest_path(two) <= ingest_cd.WINDOWS_PATH_MAX - 1, f"an image rip fits: {longest_path(two)}")

    # A loose rip takes the same route.
    root = tempfile.mkdtemp()
    size = 254 - len(os.path.abspath(root)) - 1 - len(".cue")
    loose = ("Artist - Album That Goes On " * 20)[:size - 1] + "x"
    rip(root, loose, LOG_OK)
    held = ci.stage(root, loose + ".cue")
    check(longest_path(held) <= ingest_cd.WINDOWS_PATH_MAX - 1 and len(os.listdir(held)) == 3,
          f"a loose rip fits, with its files: {longest_path(held)}, {len(os.listdir(held))}")

    # A name that fits is left exactly as it was.
    root = tempfile.mkdtemp()
    rip(root, "Short", LOG_OK)
    check(sorted(os.listdir(ci.stage(root, "Short.cue"))) == ["Short.cue", "Short.log", "Short.wav"], "a short rip keeps its names")


def test_stage_refuses_what_cannot_fit():
    """[SPEC-NAM-035]: an inbox too deep for any name is refused in words,
    before anything moves."""
    base = tempfile.mkdtemp()
    deep = os.path.join(base, *["d" * 30] * ((230 - len(base)) // 31))
    os.makedirs(deep)
    folder = os.path.join(deep, "Set")
    os.makedirs(folder)
    title = "t" * max(1, 259 - len(folder) - 1 - len(".wav"))
    for d in (1, 2):
        with open(os.path.join(folder, f"Set(Disc {d}).cue"), "w", encoding="utf-8") as f:
            f.write(f'TITLE "Set"\nFILE "{title[:-1]}{d}.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n')
        open(os.path.join(folder, f"{title[:-1]}{d}.wav"), "w").close()
    before = sorted(os.listdir(folder))
    try:
        ci.stage(deep, os.path.join("Set", "Set(Disc 2).cue"))
        check(False, "a rip no folder name can hold is refused")
    except ValueError as e:
        check("shorter path" in str(e) and "nothing was moved" in str(e) and "WinError" not in str(e), f"in words: {e}")
    check(sorted(os.listdir(folder)) == before and os.listdir(deep) == ["Set"], "and nothing moved")


def test_stage_failure_loses_nothing():
    """[SPEC-NAM-050]: a move that fails puts back what it moved and says which
    file failed. One that cannot be put back keeps the folder it landed in:
    removing it, as the split did, deleted the only copy of that audio."""
    root = tempfile.mkdtemp()
    name, folder, stem = long_set(root, tracks=False)
    cid = os.path.join(name, stem(2) + ".cue")
    with open(os.path.join(folder, stem(2) + ".cue"), "rb") as f:
        sheet = f.read()
    before = sorted(os.listdir(folder))
    real, calls = ci.shutil.move, []

    def fails_on_third(src, dst):
        calls.append(src)
        if len(calls) == 3:
            raise OSError("[WinError 32] The process cannot access the file")
        return real(src, dst)
    ci.shutil.move = fails_on_third
    try:
        ci.stage(root, cid)
        check(False, "the failing move raises")
    except OSError as e:
        check("WinError 32" in str(e) and "as the ripper left it" in str(e) and "could not move '" in str(e),
              f"says what failed and that nothing changed: {e}")
    finally:
        ci.shutil.move = real
    check(sorted(os.listdir(folder)) == before, f"every file back: {sorted(os.listdir(folder))}")
    check(os.listdir(root) == [name], f"and no folder left behind: {os.listdir(root)}")
    with open(os.path.join(folder, stem(2) + ".cue"), "rb") as f:
        check(f.read() == sheet, "the sheet byte for byte")

    # Every move back fails too.
    calls.clear()
    first = []

    def strands(src, dst):
        calls.append(src)
        if len(calls) == 1:
            first.append(dst)
            return real(src, dst)
        raise OSError("[WinError 5] Access is denied")
    ci.shutil.move = strands
    try:
        ci.stage(root, cid)
        check(False, "the failing move raises")
    except OSError as e:
        check("could not be put back" in str(e), f"says so: {e}")
    finally:
        ci.shutil.move = real
    check(first and os.path.isfile(first[0]), "the stranded file is still on disk, not deleted with its folder")


def test_scan_survives_a_bad_rip():
    """[SPEC-NAM-035]: one rip that cannot be read is a card saying so; the
    others still show. A log that raised used to take the whole page down."""
    root = tempfile.mkdtemp()
    rip(root, "Good", LOG_OK)
    rip(root, "Bad", LOG_OK)
    real = ci.cd_toc.parse_eac_log

    def fails_on_bad(path):
        if "Bad" in os.path.basename(path):
            raise OSError("[Errno 13] Permission denied")
        return real(path)
    ci.cd_toc.parse_eac_log = fails_on_bad
    try:
        cards = {c["id"]: c for c in ci.scan(root)}
    finally:
        ci.cd_toc.parse_eac_log = real
    check(cards["Bad.cue"]["state"] == "unreadable" and "Permission denied" in cards["Bad.cue"]["detail"],
          f"the bad one says so: {cards['Bad.cue']}")
    check(cards["Good.cue"]["state"] == "finished", f"the good one is unaffected: {cards['Good.cue']}")

    # A folder holding a path Windows cannot open: `isfile` says False, which
    # read as "still being ripped" for ever.
    if os.name == "nt":
        root = tempfile.mkdtemp()
        folder = os.path.join(root, "Rip")
        tracks_rip(folder, (1, 2, 3))
        over = "x" * (262 - len(os.path.abspath(folder)) - 1 - len(".wav")) + ".wav"
        with open("\\\\?\\" + os.path.join(os.path.abspath(folder), over), "w"):
            pass
        card = ci.scan(root)[0]
        check(card["state"] == "unreadable" and "at most 259" in card["detail"], f"said plainly: {card}")

    # One log beside two CUEs is the other disc's [SPEC-RIP-118].
    root = tempfile.mkdtemp()
    name, folder, stem = long_set(root)
    cards = {c["id"]: c for c in ci.scan(root, now=time.time() + 600)}
    one = cards[os.path.join(name, stem(1) + ".cue")]
    two = cards[os.path.join(name, stem(2) + ".cue")]
    check("no log" in one["verdict"]["text"] and one["verdict"]["level"] == "note",
          f"a disc with no log of its own does not borrow its neighbour's: {one['verdict']}")
    check(two["verdict"]["level"] == "ok", f"the disc it belongs to reads it: {two['verdict']}")


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
    test_cueripper_checks()
    test_scan()
    test_stage()
    test_stage_splits_a_set()
    test_tracks_scan_and_stage()
    test_stage_holds_a_long_name_short()
    test_stage_refuses_what_cannot_fit()
    test_stage_failure_loses_nothing()
    test_scan_survives_a_bad_rip()
    test_inbox_setting()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("cd_import: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
