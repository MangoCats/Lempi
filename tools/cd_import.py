#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The CD import page's own logic `[SPEC056]`, kept out of `console.py`.

Three things the page shows, and one it does:

- **the guide** -- GUIDE037, rendered from its Markdown into the panel beside
  the controls `[SPEC-CDI-070]`: headings (each an anchor), paragraphs, lists,
  quotes, rules, bold, italics, inline code and links. Everything is escaped
  first, so the Markdown can carry no HTML of its own;
- **the ripper's setup** -- CUERipper's settings file, or EAC's registry
  when only EAC is found, read and ticked off, never written
  `[SPEC-CDI-012]`, `[SPEC-CDI-025]`;
- **the rip inbox** -- scanned for finished rips, each with its verdict in
  words `[SPEC-CDI-030]`, `[SPEC-CDI-035]`;
- and **staging**: a rip EAC left loose in the inbox is given its own folder
  before it is previewed, since `ingest_cd.py` reads one rip per folder.

Nothing here writes the library. Adding a rip is the `cd-rip` job.
"""
from __future__ import annotations

import glob
import html
import os
import re
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cd_toc  # noqa: E402
import ingest_cd  # noqa: E402  -- the names a rip is held to [SPEC-NAM-035]

GUIDE = os.path.join(os.path.dirname(HERE), "docs", "GUIDE037-ripping-a-cd.md")

# ------------------------------------------------------------------ the guide


def slug(text: str) -> str:
    """A heading's anchor: lower case, words joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


_INLINE = [
    (re.compile(r"`([^`]+)`"), lambda m: f"<code>{m.group(1)}</code>"),
    (re.compile(r"\*\*(.+?)\*\*"), lambda m: f"<strong>{m.group(1)}</strong>"),
    (re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])"), lambda m: f"<em>{m.group(1)}</em>"),
    (re.compile(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)"), lambda m: f"<em>{m.group(1)}</em>"),
]
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _link(m: re.Match) -> str:
    """An outside link opens in a new tab; a link to another document in the
    repository has nowhere to go inside the panel, so it is its text alone."""
    text, url = m.group(1), m.group(2)
    if url.startswith(("https://", "http://")):
        return f'<a href="{url}" target="_blank" rel="noopener">{text}</a>'
    if url.startswith("#"):
        return f'<a href="{url}">{text}</a>'
    return text


def _inline(escaped: str) -> str:
    """Inline marks on text already escaped -- so a mark can wrap text but
    never smuggle a tag: every `<` in the source is `&lt;` by now."""
    code = []

    def hold(m):
        code.append(m.group(1))
        return f"\x00{len(code) - 1}\x00"
    s = re.sub(r"`([^`]+)`", hold, escaped)          # code spans are left alone
    s = _LINK.sub(_link, s)
    for pat, rep in _INLINE[1:]:
        s = pat.sub(rep, s)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{code[int(m.group(1))]}</code>", s)


def render_markdown(text: str) -> str:
    """The small Markdown the guide uses, as HTML safe to put in the page."""
    out, para, items, kind = [], [], [], None

    def flush():
        nonlocal para, items, kind
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para = []
        if items:
            tag = "ol" if kind == "ol" else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + f"</{tag}>")
            items, kind = [], None

    for raw in text.splitlines():
        line = html.escape(raw.rstrip(), quote=True)
        stripped = line.strip()
        h = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        ul = re.match(r"^[-*]\s+(.*)$", stripped)
        ol = re.match(r"^\d+\.\s+(.*)$", stripped)
        if not stripped:
            flush()
        elif h:
            flush()
            n, title = len(h.group(1)), h.group(2)
            plain = re.sub(r"<[^>]+>", "", _inline(title))
            out.append(f'<h{n} id="{slug(html.unescape(plain))}">{_inline(title)}</h{n}>')
        elif stripped == "---":
            flush()
            out.append("<hr>")
        elif stripped.startswith("&gt;"):
            flush()
            out.append(f"<blockquote>{_inline(stripped[4:].strip())}</blockquote>")
        elif ul or ol:
            if para:
                flush()
            want = "ol" if ol else "ul"
            if items and kind != want:
                flush()
            kind = want
            items.append((ol or ul).group(1))
        elif items and raw.startswith("  "):
            items[-1] += " " + stripped                  # a list item's next line
        else:
            if items:
                flush()
            para.append(stripped)
    flush()
    # A blockquote's lines arrive one element each; join neighbours into one.
    return re.sub(r"</blockquote>\n<blockquote>", " ", "\n".join(out))


def guide_html() -> str:
    with open(GUIDE, encoding="utf-8") as fh:
        return render_markdown(fh.read())


# Where each step and each setup check opens the guide `[SPEC-CDI-070]`. A test
# holds every one of these to a heading the guide actually has.
ANCHORS = {
    "start": "before-you-start",
    "cr-install": "1-unpack-cueripper",
    "cr-setup": "2-secure-reading-one-file-for-the-disc",
    "inbox": "3-where-rips-go",
    "cr-close": "4-close-it-once",
    "eac": "using-eac-instead",
    "wizard": "eac-1-run-the-configuration-wizard",
    "drive": "eac-2-secure-reading-and-the-read-offset",
    "log": "eac-3-the-log-in-english-with-a-checksum",
    "eac-inbox": "eac-4-where-rips-go",
    "rip": "ripping-a-disc",
    "add": "adding-it-in-vipunen",
    "after": "afterwards",
    "split": "splitting-an-album-file",
    "trouble": "when-something-goes-wrong",
}

# ------------------------------------------------------------------ the inbox

INBOX_KEY = "rip_inbox"


def inbox(sidecar: str, music_root: str) -> str:
    """The rip inbox: the one chosen on the page, else `_Rips` in the music
    folder `[SPEC-CDI-020]`. Kept in the console's own settings, beside the
    rest of its configuration -- a Vipunen preference, not a player's
    `[SPEC-RIP-050]`."""
    try:
        db = sqlite3.connect(sidecar)
        try:
            row = db.execute("SELECT value FROM remote_config WHERE key=?1", (INBOX_KEY,)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        row = None
    return row[0] if row else os.path.join(music_root or "", "_Rips")


def set_inbox(sidecar: str, path: str) -> str:
    path = os.path.normpath(path)
    os.makedirs(path, exist_ok=True)
    db = sqlite3.connect(sidecar)
    try:
        db.execute("INSERT INTO remote_config (key, value) VALUES (?1, ?2) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (INBOX_KEY, path))
        db.commit()
    finally:
        db.close()
    return path


# How long an audio file must have sat still, with no log beside it, before it
# is taken as finished rather than still being written.
SETTLED_S = 60


def _cues(root: str) -> list[str]:
    """Every CUE in the inbox and one folder down: EAC writes the image into the
    inbox itself; a staged rip sits in a folder of its own."""
    found = []
    if not os.path.isdir(root):
        return found
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if name.lower().endswith(".cue") and os.path.isfile(p):
            found.append(p)
        elif os.path.isdir(p):
            try:
                found += [os.path.join(p, n) for n in sorted(os.listdir(p)) if n.lower().endswith(".cue")]
            except OSError:
                found.append(p)                 # its card says so; the rest still show
    return found


def verdict(report) -> dict:
    """The log's word on the rip, as the card says it `[SPEC-CDI-035]`:
    `{"level": ok|note|bad, "text": ...}`."""
    if report is None:
        return {"level": "note", "text": "No status report beside it, so the rip cannot be checked. "
                "Turn on the ripper's log (see setup) for the next disc."}
    bad = [t for t in report.tracks if not t.ok]
    unseen = [t for t in report.tracks if t.ok and "not present" in t.detail.lower()]
    if bad:
        which = ", ".join(str(t.number) for t in bad)
        return {"level": "bad", "text": f"Track{'s' if len(bad) > 1 else ''} {which} had read errors or did not "
                "match other people's rips. Clean the disc and rip it again with Test & Copy. You can still add it; "
                "it will be marked."}
    if not report.all_ok:
        return {"level": "bad", "text": "The ripper reported read errors. Clean the disc and rip it again with "
                "Test & Copy. You can still add it; it will be marked."}
    if report.tracks and not unseen:
        return {"level": "ok", "text": "Every track accurately ripped -- it matches other people's rips."}
    if report.tracks and len(unseen) < len(report.tracks):
        return {"level": "ok", "text": f"Ripped cleanly. {len(unseen)} track(s) are not in AccurateRip's "
                "database, so there was nothing to compare them with."}
    return {"level": "ok", "text": "Ripped cleanly. This disc is not in AccurateRip's database, so "
            "there was nothing to compare with -- usually fine."}


def _tracks_card(toc, folder: str, log: str | None, now: float) -> dict:
    """A tracks-mode rip's card `[SPEC-CDI-090]`: a file per track, some
    perhaps never written -- a damaged track the ripper stopped on. Finished
    once its log is written, or once its files have sat still with no log: a
    rip that stopped. A missing track is named, and does not keep the rest
    from being added `[SPEC-CDI-094]`."""
    present = [t for t in toc.tracks if t.file and os.path.isfile(os.path.join(folder, t.file))]
    missing = [t for t in toc.tracks if t not in present]
    out = {"mode": "tracks", "audio": f"{len(present)} of {len(toc.tracks)} track files",
           "missing": [{"number": t.number, "title": t.title} for t in missing]}
    if not present:
        return {**out, "state": "ripping", "detail": "Still being ripped."}
    size = sum(os.path.getsize(os.path.join(folder, t.file)) for t in present)
    newest = max(os.path.getmtime(os.path.join(folder, t.file)) for t in present)
    if log and os.path.isfile(log):
        v = verdict(cd_toc.parse_eac_log(log))
    elif now - newest < SETTLED_S:
        return {**out, "state": "ripping", "detail": "Still being ripped."}
    else:
        v = {"level": "note", "text": "There is no log beside it -- the rip stopped before the end, or "
             "the log was turned off -- so these tracks cannot be checked against other people's rips. "
             "They will be added as unverified."}
    if missing:
        names = ", ".join(f"{t.number}" + (f" ({t.title})" if t.title else "") for t in missing)
        v = {"level": "bad" if v["level"] != "bad" else v["level"],
             "text": f"Track{'s' if len(missing) > 1 else ''} {names} {'were' if len(missing) > 1 else 'was'} "
                     f"not ripped; the album is added without {'them' if len(missing) > 1 else 'it'}. "
                     + v["text"]}
    return {**out, "state": "finished", "verdict": v, "audio_bytes": size}


def _too_long(folder: str) -> str | None:
    """A file in `folder` whose path Windows cannot open. An `isfile` on it
    answers False rather than failing, so its rip would read as still being
    ripped for ever `[SPEC-NAM-035]`."""
    if os.name != "nt":
        return None
    base = os.path.abspath(folder)
    return next((n for n in os.listdir(folder)
                 if len(os.path.join(base, n)) > ingest_cd.WINDOWS_PATH_MAX), None)


def _card(cue: str, root: str, now: float) -> dict:
    """One rip's card. May raise; `scan` makes that a card too."""
    folder, stem = os.path.dirname(cue), os.path.splitext(os.path.basename(cue))[0]
    long = _too_long(folder)
    if long:
        return {"id": os.path.relpath(cue, root), "state": "unreadable", "title": stem,
                "detail": f"{long!r} makes a path of {len(os.path.join(os.path.abspath(folder), long))} characters, "
                f"and Windows opens at most {ingest_cd.WINDOWS_PATH_MAX}: give the file or its folder a shorter "
                "name in the inbox."}
    try:
        toc = cd_toc.parse_eac_cue(cue)
    except Exception as e:                                    # noqa: BLE001
        return {"id": os.path.relpath(cue, root), "state": "unreadable", "title": stem,
                "detail": f"the CUE sheet could not be read: {e}"}
    log = os.path.join(folder, stem + ".log")
    if not os.path.isfile(log):
        names = os.listdir(folder)
        logs = [n for n in names if n.lower().endswith(".log")]
        # By elimination only where this is the folder's one disc: the one log
        # beside two CUEs is the other disc's. *She Who Dwells*, 2026-11-05: a
        # disc 1 stopped at track 13, with disc 2's read errors on its card.
        alone = sum(n.lower().endswith(".cue") for n in names) == 1
        log = os.path.join(folder, logs[0]) if folder != root and len(logs) == 1 and alone else None
    card = {"id": os.path.relpath(cue, root), "folder": folder, "staged": folder != root,
            "title": " - ".join(x for x in (toc.performer, toc.title) if x) or stem,
            "tracks": len(toc.tracks)}
    if toc.tracks_mode:
        card.update(_tracks_card(toc, folder, log, now))
    else:
        audio = os.path.join(folder, toc.data_file or "")
        card["audio"] = os.path.basename(audio)
        if not toc.data_file or not os.path.isfile(audio):
            card.update(state="ripping", detail="Still being ripped.")
        elif log and os.path.isfile(log):
            card.update(state="finished", verdict=verdict(cd_toc.parse_eac_log(log)),
                        audio_bytes=os.path.getsize(audio))
        elif now - os.path.getmtime(audio) < SETTLED_S:
            card.update(state="ripping", detail="Still being ripped.")
        else:
            card.update(state="finished", verdict=verdict(None), audio_bytes=os.path.getsize(audio))
    return card


def scan(root: str, now: float | None = None) -> list[dict]:
    """The rips in the inbox, as the page's cards `[SPEC-CDI-030]`. Read-only.
    A rip that cannot be read is a card that says so: it never takes the other
    rips, or the page, down with it `[SPEC-NAM-035]`."""
    now = time.time() if now is None else now
    cards = []
    for cue in _cues(root):
        try:
            cards.append(_card(cue, root, now))
        except Exception as e:                                # noqa: BLE001
            cards.append({"id": os.path.relpath(cue, root), "state": "unreadable",
                          "title": os.path.splitext(os.path.basename(cue))[0],
                          "detail": f"this rip could not be read: {e}"})
    return cards


def stage(root: str, card_id: str) -> str:
    """The folder a rip can be previewed and added from: its own. A rip EAC
    left loose in the inbox has its files -- the CUE, the audio it names, the
    log of the same name -- moved into a folder named after it first. Returns
    that folder."""
    cue = os.path.normpath(os.path.join(root, card_id))
    if os.path.commonpath([os.path.abspath(cue), os.path.abspath(root)]) != os.path.abspath(root):
        raise ValueError("not a rip in the inbox")
    if not os.path.isfile(cue):
        raise FileNotFoundError(f"no rip {card_id!r} in the inbox")
    folder = os.path.dirname(cue)
    if os.path.abspath(folder) != os.path.abspath(root):
        others = [n for n in os.listdir(folder) if n.lower().endswith(".cue") and n != os.path.basename(cue)]
        return _split_disc(root, folder, cue, others) if others else folder
    stem = os.path.splitext(os.path.basename(cue))[0]
    toc = cd_toc.parse_eac_cue(cue)
    names = {os.path.basename(cue), stem + ".log", *toc.files}
    names = [n for n in names if n and os.path.isfile(os.path.join(root, n))]
    target, renames = _own_folder(root, stem, names)
    _move_in(root, target, names, renames)
    return target


def staged_id(root: str, folder: str, card_id: str) -> str:
    """A rip's card id once `stage` has run: its CUE in its folder, under the
    name staging gave it, which a held-short folder changes `[SPEC-NAM-035]`."""
    name = os.path.basename(card_id)
    if not os.path.isfile(os.path.join(folder, name)):
        cues = [n for n in os.listdir(folder) if n.lower().endswith(".cue")]
        name = cues[0] if len(cues) == 1 else name
    return os.path.relpath(os.path.join(folder, name), root)


# The longest folder Windows creates with `LongPathsEnabled` off: MAX_PATH less
# the 12 it keeps for a file inside, its NUL among them.
WINDOWS_DIR_MAX = 247


def _own_folder(root: str, stem: str, names: list[str]) -> tuple[str, dict[str, str]]:
    """The folder a rip is staged into, and the files it renames: `(folder,
    {old: new})`. Refuses, before anything moves, a rip no name can hold.

    Named after the disc when that holds every file `[SPEC-NAM-035]`. A ripper
    fills the path it can open, and a folder named after the disc spends the
    same characters a second time: *She Who Dwells*, 2026-11-05, 254 characters
    a file in its rip folder and 262 in the one staging made for its second
    disc. So the name is cut at a word, keeping the disc, and the files named
    after the disc take the cut name, which is also what keeps them starting
    with their folder's `[SPEC-NAM-040]`. Track files have names of their own
    and keep them. +1 for each file: the add may write a FLAC beside a WAV."""
    base = os.path.abspath(root)
    named = [n for n in names if stem.startswith(os.path.splitext(n)[0])]
    rest = [n for n in names if n not in named]

    def final(name: str) -> list[str]:
        return rest + (named if name == stem else [name + os.path.splitext(n)[1] for n in named])

    def fits(name: str) -> bool:
        path = os.path.join(base, name)
        return len(path) <= WINDOWS_DIR_MAX and all(
            len(path) + 1 + len(f) + 1 <= ingest_cd.WINDOWS_PATH_MAX for f in final(name))

    whole = stem.rstrip(". ") or stem                   # Windows drops these from a folder's name
    head = ingest_cd._RIPPER_DISC.sub("", whole)
    disc = whole[len(head):]
    floor = min(ingest_cd.ALBUM_NAME_RANGE[0], len(head))
    k = 1
    while True:
        tag = f" ({k})" if k > 1 else ""
        tries = [whole + tag] + [ingest_cd.shorten(head, n) + disc + tag for n in range(len(head) - 1, floor - 1, -1)]
        name = next((t for t in tries if fits(t)), None)
        if name is None:
            longest = max(rest or names, key=len, default=stem)
            raise ValueError(f"{longest!r} will not fit in a folder of its own in the inbox {root!r}: Windows opens "
                             f"at most {ingest_cd.WINDOWS_PATH_MAX} characters. Choose an inbox with a shorter path, "
                             "or shorten that file's name -- nothing was moved")
        if not os.path.exists(os.path.join(root, name)):
            break
        k += 1
    if len(set(final(name))) < len(names):
        raise ValueError(f"{stem!r} has two files that would be given one name: nothing was moved")
    renames = {} if name == stem else {n: name + os.path.splitext(n)[1] for n in named}
    return os.path.join(root, name), renames


def _move_in(source: str, target: str, names: list[str], renames: dict[str, str], covers: list[str] = ()) -> None:
    """`names` moved from `source` into the new folder `target`, under
    `renames`, the CUE's `FILE` lines following; `covers` copied. All or nothing
    `[SPEC-NAM-050]`: a failure puts back what moved and says what failed, naming
    the file, which Python's own copy leaves out of its message. A file that
    cannot be put back keeps the folder it is in, and the message says where:
    that audio may be the only copy."""
    os.makedirs(target)
    moved, sheets, doing = [], {}, ""
    try:
        for n in sorted(names):
            doing = n
            dst = os.path.join(target, renames.get(n, n))
            shutil.move(os.path.join(source, n), dst)
            moved.append((n, dst))
        for n in covers:
            doing = n
            shutil.copy2(os.path.join(source, n), os.path.join(target, n))
        for n, dst in moved:
            if renames and n.lower().endswith(".cue"):
                doing = n
                with open(dst, "rb") as fh:
                    sheets[dst] = fh.read()
                ingest_cd.rename_in_cue(dst, renames)
    except Exception as e:
        for sheet, data in sheets.items():
            try:
                with open(sheet, "wb") as fh:
                    fh.write(data)
            except OSError:
                pass
        stuck = []
        for n, dst in reversed(moved):
            try:
                shutil.move(dst, os.path.join(source, n))
            except OSError:
                stuck.append(dst)
        if stuck:
            raise OSError(f"could not move {doing!r} ({e}), and {len(stuck)} file(s) could not be put back: "
                          f"they are in {target!r}") from e
        shutil.rmtree(target, ignore_errors=True)
        raise OSError(f"could not move {doing!r} ({e}) -- the rip is as the ripper left it") from e


_COVER_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def _split_disc(root: str, folder: str, cue: str, others: list[str]) -> str:
    """One disc of several in one folder, given a folder of its own
    `[SPEC-RIP-118]`: its CUE, the audio it names, and the files named after
    it -- the log and `.accurip`, a name the ripper cut short when no other
    disc's could be meant -- moved; a cover copied, so each disc keeps one.
    CUERipper rips a set's discs into one folder when its template has no
    disc in it: *The Very Best of Jackson Browne*, 2026-10-15, whose add then
    named both discs' files "(Disc 1)" and was refused. The importer works a
    disc at a time, so every downstream step is unchanged. Returns the new
    folder, named by `_own_folder` and moved into by `_move_in`."""
    stem = os.path.splitext(os.path.basename(cue))[0]
    other_stems = [os.path.splitext(o)[0] for o in others]
    toc = cd_toc.parse_eac_cue(cue)
    mine = {os.path.basename(cue), *(f for f in toc.files if f)}
    for n in os.listdir(folder):
        s = os.path.splitext(n)[0]
        if s == stem or (s and stem.startswith(s) and not any(o.startswith(s) for o in other_stems)):
            mine.add(n)
    names = sorted(n for n in mine if os.path.isfile(os.path.join(folder, n)))
    covers = [n for n in os.listdir(folder) if n not in mine and n.lower().endswith(_COVER_EXT)
              and os.path.isfile(os.path.join(folder, n))]
    target, renames = _own_folder(root, stem, names + covers)
    _move_in(folder, target, names, renames, covers)
    return target


# ------------------------------------------------------------- EAC's own setup

EAC_KEY = r"Software\AWSoftware\EACU"
EAC_EXE = [os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Exact Audio Copy", "EAC.exe"),
           os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Exact Audio Copy", "EAC.exe")]


def read_eac() -> dict | None:
    """EAC's settings as raw values, `{section: {name: value}}` with each drive
    under `drives`; `None` where there is no registry or no EAC. Read only."""
    try:
        import winreg
    except ImportError:
        return None

    def values(key) -> dict:
        out, i = {}, 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                return out
            out[name] = value
            i += 1
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, EAC_KEY)
    except OSError:
        return None
    got: dict = {"drives": {}}
    with root:
        for section in ("Extraction Options", "StartUp Options"):
            try:
                with winreg.OpenKey(root, section) as k:
                    got[section] = values(k)
            except OSError:
                got[section] = {}
        try:
            with winreg.OpenKey(root, "Drive Options") as drives:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(drives, i)
                    except OSError:
                        break
                    with winreg.OpenKey(drives, name) as k:
                        got["drives"][name] = values(k)
                    i += 1
        except OSError:
            pass
    return got


def _on(v) -> bool | None:
    """An EAC switch: `ff` on, `00` off, measured 2026-09-29. `None` when absent."""
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return any(v)
    return bool(v)


def _offset(v) -> int | None:
    if isinstance(v, (bytes, bytearray)) and len(v) >= 4:
        return int.from_bytes(v[:4], "little", signed=True)
    return v if isinstance(v, int) else None


def checks(eac: dict | None, inbox_path: str, *, installed: bool, ffmpeg: bool) -> list[dict]:
    """Each setting the flow needs, ticked or not, with its fix and where the
    guide explains it `[SPEC-CDI-025]`. `ok` is True, False, or None for a
    value not yet calibrated -- shown for a person to confirm, never guessed."""
    out = []

    def add(key, label, ok, fix, guide, detail=None):
        out.append({"key": key, "label": label, "ok": ok, "fix": fix, "guide": ANCHORS[guide],
                    "detail": detail})
    add("installed", "Exact Audio Copy is installed", installed,
        "Install EAC 1.8 from exactaudiocopy.de.", "eac")
    add("ffmpeg", "ffmpeg is available", ffmpeg,
        "Install ffmpeg, which makes the MP3s.", "start")
    if eac is None:
        add("settings", "EAC's settings can be read", False,
            "Run EAC once, then run its configuration wizard.", "wizard")
        return out
    ex, st = eac.get("Extraction Options", {}), eac.get("StartUp Options", {})

    def win(p):
        """A Windows path as Windows compares it -- either slash, no trailing
        one, any case -- on whatever machine this runs, since EAC's always is."""
        return str(p or "").replace("\\", "/").rstrip("/").lower()
    same = bool(ex.get("DirectorySpecification")) and win(ex.get("DirectorySpecification")) == win(inbox_path)
    # `DirectoryUse` is 0 when *Use this directory* is chosen -- observed
    # 2026-09-29 with it just chosen, and consistent with the 2026-09-03 test
    # rip, which landed in the configured folder while it read 0. So any other
    # value is a different choice (asking every time), not the one wanted.
    use = ex.get("DirectoryUse")
    chosen = use is not None and not _on(use)
    add("inbox", "Rips go to the inbox", same and chosen,
        f"In EAC Options, Directories: choose Use this directory, and set it to {inbox_path}.", "eac-inbox",
        detail=None if same and chosen else (
            "the folder is right, but EAC is set to ask every time; choose Use this directory"
            if same else f"EAC's folder is {ex.get('DirectorySpecification') or 'not set'}"))
    for key, name, label, fix in (
            ("AutoSaveStatus", "log", "The status report is written after each rip",
             "In EAC Options, Tools: tick Automatically write status report after extraction."),
            ("AddChecksumLogFile", "checksum", "The status report carries a checksum",
             "In EAC Options, Tools: tick Append checksum to status report."),
            ("AddCDTextToCUESheet", "cdtext", "The disc's own titles go into the CUE sheet",
             "In EAC Options, Tools: tick Use CD-Text information in CUE sheet generation.")):
        add(name, label, bool(_on(ex.get(key))), fix, "log")
    add("english", "The status report is in English", bool(_on(st.get("CreateEnglishLogFile"))),
        "In EAC Options, General: tick Create log files always in english language.", "log")
    drives = eac.get("drives") or {}
    if not drives:
        add("drive", "A drive is set up in EAC", False,
            "Put a CD in and run EAC's configuration wizard.", "wizard")
    for name, d in drives.items():
        add(f"accuraterip:{name}", f"AccurateRip is on ({name.strip()})", bool(_on(d.get("UseAccurateRip"))),
            "In Drive Options, Offset / Speed: tick Use AccurateRip with this drive.", "drive")
        off = _offset(d.get("SampleOffset"))
        add(f"offset:{name}", f"The read offset is set ({name.strip()})", None if off is None else off != 0,
            "In Drive Options, Offset / Speed: tick Use read sample offset correction, then press "
            "Detect read sample offset correction with a popular CD in the drive.", "drive",
            detail=None if off is None else f"offset {off:+d}")
        # `ExtractionMode`, calibrated at the desk 2026-09-29 by switching modes
        # and nothing else: secure 04 00, burst 00 00. `SecureMode` did not
        # move -- it records the drive-feature boxes, not the mode.
        mode = _mode(d.get("ExtractionMode"))
        add(f"secure:{name}", f"Secure mode ({name.strip()})", None if mode is None else mode == SECURE,
            "In Drive Options, Extraction Method: choose Secure mode with following drive features.", "drive",
            detail=None if mode in (None, SECURE) else
            "EAC is in burst mode: it reads once and never verifies" if mode == BURST else
            f"EAC is in another mode (code {mode}), not the recommended secure mode")
    return out


# EAC's `ExtractionMode` codes, as observed (IMPL018 Phase 2).
SECURE, BURST = 4, 0


def _mode(v) -> int | None:
    if isinstance(v, (bytes, bytearray)) and len(v) >= 2:
        return int.from_bytes(v[:2], "little")
    return v if isinstance(v, int) else None


# ------------------------------------------------------- CUERipper's own setup
#
# The recommended ripper since 2026-09-29, when EAC 1.8 on the desktop crashed
# in every file dialog it opened and CUERipper ripped the same disc cleanly
# `[SPEC-CDI-012]`. It keeps no registry: `settings.txt`, `Key=Value` lines,
# written when it *closes* -- in `%APPDATA%\CUERipper` when a file named
# `user_profiles_enabled` sits beside the program (the portable zip ships
# one), else beside the program. Key names read from CUERipper 2.2.6's own
# binaries: `PathFormat`, `SecureMode`, `ComboImage` from CUERipper.exe,
# `CreateEACLOG` from CUETools.Processor.dll.

CUERIPPER_ENV = "LEMPI_CUERIPPER"


def find_cueripper() -> str | None:
    """CUERipper.exe where it is usually unpacked; `LEMPI_CUERIPPER` names
    one anywhere else. It has no installer, so there is no one place."""
    env = os.environ.get(CUERIPPER_ENV)
    if env:
        return env if os.path.isfile(env) else None
    home = os.path.expanduser("~")
    roots = [os.path.join(os.path.dirname(HERE), "data", "cuetools"),
             os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.path.join(home, "Downloads"), os.path.join(home, "Desktop")]
    for root in roots:
        for pat in ("CUERipper.exe", os.path.join("CUETools*", "CUERipper.exe"),
                    os.path.join("CUETools*", "CUETools*", "CUERipper.exe")):
            hit = sorted(glob.glob(os.path.join(root, pat)))
            if hit:
                return hit[-1]
    return None


def cueripper_settings_path(exe: str) -> str:
    here = os.path.dirname(exe)
    if os.path.exists(os.path.join(here, "user_profiles_enabled")):
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "CUERipper", "settings.txt")
    return os.path.join(here, "settings.txt")


def read_cueripper(path: str) -> dict | None:
    """`settings.txt` as `{key: value}`; `None` before CUERipper has first
    closed and written it. Read only."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    return dict(line.split("=", 1) for line in lines if "=" in line)


def music_folder() -> str:
    """What CUERipper's `%music%` stands for: Windows' own Music folder,
    wherever it has been moved to."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
            return winreg.QueryValueEx(k, "My Music")[0]
    except (ImportError, OSError):
        return os.path.join(os.path.expanduser("~"), "Music")


# CUERipper's slider, by the order of its own labels: Burst, Secure, Paranoid.
# The desk's file, written with Secure chosen, reads 1.
CR_MODES = {0: "burst", 1: "secure", 2: "paranoid"}
# `ComboImage` is the image/tracks drop-down's index: 0 with *image* chosen,
# read from the desk's first settings file, 2026-10-01.
CR_IMAGE: int | None = 0


def _cr_on(v: str | None) -> bool | None:
    """A CUERipper switch. It writes `1` and `0` -- seen in the desk's file,
    2026-10-01, after a first version of this check expected `True` and put a
    red cross on a log style that was on."""
    if v is None:
        return None
    return {"1": True, "true": True, "0": False, "false": False}.get(v.strip().lower())
TEMPLATE = r"%music%\_Rips\%artist% - %album%\%artist% - %album%.cue"


def _template_dir(fmt: str, music: str) -> tuple[str, int]:
    """Where a path template puts its rips: the fixed folder before the first
    part that varies, and how many varying folders follow it."""
    parts = re.split(r"[\\/]", fmt.replace("%music%", music.rstrip("\\/")))[:-1]
    fixed = []
    for p in parts:
        if "%" in p or "[" in p:
            break
        fixed.append(p)
    return "\\".join(fixed), len(parts) - len(fixed)


def cueripper_checks(cfg: dict | None, inbox_path: str, music: str, *, ffmpeg: bool) -> list[dict]:
    """CUERipper's settings, ticked off as `checks` ticks off EAC's."""
    out = []

    def add(key, label, ok, fix, guide, detail=None):
        out.append({"key": key, "label": label, "ok": ok, "fix": fix, "guide": ANCHORS[guide],
                    "detail": detail})
    add("installed", "CUERipper is found", True, "", "cr-install")
    add("ffmpeg", "ffmpeg is available", ffmpeg, "Install ffmpeg, which makes the MP3s.", "start")
    if cfg is None:
        add("settings", "CUERipper's settings can be read", False,
            "CUERipper saves its settings when it closes. Set it up, then close it once.", "cr-close")
        return out
    def win(p):
        return str(p or "").replace("/", "\\").rstrip("\\").lower()
    fmt = cfg.get("PathFormat") or ""
    where, depth = _template_dir(fmt, music) if fmt else ("", 0)
    good = win(where) == win(inbox_path) and depth == 1
    want = (TEMPLATE if win(inbox_path) == win(os.path.join(music, "_Rips")) else
            inbox_path.rstrip("\\/") + TEMPLATE[len(r"%music%\_Rips"):])
    add("inbox", "Rips go to the inbox", good,
        f"In CUERipper's output path box, type {want}", "inbox", detail=None if good else (
            f"CUERipper's path is {fmt or 'not set'}" if win(where) != win(inbox_path) else
            "the folder is right, but each rip needs a folder of its own directly inside it"))
    mode = cfg.get("SecureMode", "")
    name = CR_MODES.get(int(mode)) if mode.isdigit() else None
    add("secure", "Secure mode", None if name is None else name != "burst",
        "Move CUERipper's mode slider to Secure.", "cr-setup",
        detail="CUERipper is in burst mode: it reads once and never verifies" if name == "burst" else None)
    img = cfg.get("ComboImage")
    add("image", "One file for the whole disc (image)",
        None if CR_IMAGE is None or img is None else img == str(CR_IMAGE),
        "In CUERipper, choose image in the drop-down beside the audio format.", "cr-setup",
        detail="confirm it reads image in CUERipper" if CR_IMAGE is None else None)
    log = _cr_on(cfg.get("CreateEACLOG"))
    add("log", "The log is in EAC's format", log,
        "In CUERipper's Options, under Extraction, set EAC log style to True.", "cr-setup",
        detail="confirm EAC log style is True in CUERipper's Options" if log is None else None)
    return out


def setup(inbox_path: str) -> dict:
    """The page's setup panel: which ripper, each check, and the count still
    to do. CUERipper when it is found; EAC otherwise `[SPEC-CDI-012]`."""
    ff = shutil.which("ffmpeg") is not None
    cr = find_cueripper()
    eac = next((p for p in EAC_EXE if os.path.isfile(p)), None)
    if cr or not eac:
        got = (cueripper_checks(read_cueripper(cueripper_settings_path(cr)), inbox_path, music_folder(),
                                ffmpeg=ff) if cr else
               [{"key": "installed", "label": "A ripper is installed", "ok": False, "guide": ANCHORS["cr-install"],
                 "fix": "Download CUETools from cue.tools and unpack it.", "detail": None}])
        ripper, exe = "CUERipper", cr
    else:
        got, ripper, exe = checks(read_eac(), inbox_path, installed=True, ffmpeg=ff), "EAC", eac
    return {"ripper": ripper, "exe": exe, "checks": got, "todo": sum(1 for c in got if c["ok"] is False),
            "confirm": sum(1 for c in got if c["ok"] is None)}
