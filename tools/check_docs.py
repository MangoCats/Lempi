# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Documentation governance checks [GOV-DOC-010..040].

Enforces mechanically what would otherwise depend on discipline. Written after
an audit found that the hazards recorded in docs/inherited/README.md were partly
mis-stated: [INH-HAZ-020] claimed inherited and Lempi identifier tags did not
collide, and measurement found seven exact collisions. Claims about a document
set should be checked by a program, not asserted by a person.

Checks:
  1. Every file under docs/inherited/ carries the MCR- prefix (or is source code).
  2. No Lempi document defines a tag whose prefix is reserved to inherited material.
  3. No exact tag collision between Lempi documents and inherited documents.
  4. Every relative markdown link resolves -- file AND `#fragment` (code spans
     excluded), across every Lempi-authored document, not just docs/.
  5. Lempi documents: 100-250 line target, 300-line hard limit [GOV-DOC-010].
     Over the limit is an error; the 250-300 band is counted, not listed.
  6. Every cited tag is defined somewhere; no tag is defined twice.
  6a. A document number names one document per folder, and a document's `#`
     heading agrees with its filename [GOV-DOC-030].
  6b. Tags cited from shell/unit/Dockerfile sources resolve -- and a scan
     pattern that matches no file is an error, not a quiet zero [GOV-FBN-020].
  7. Every doc-cited player/tools/sql/docs/build/LempiPi/BosePi/sendspin path
     exists in the tree [GOV-DOC-040]; warn only.

Usage:
    python tools/check_docs.py            # report
    python tools/check_docs.py --strict   # non-zero exit on any error
    python tools/check_docs.py --verbose  # also list the 250-300 line band
"""

import argparse
import glob
import os
import re
import sys
import unicodedata

# The domain may carry a digit after the first letter. It could not until
# 2026-08-20, and the cost was silent: `PI2-*` and `PI3-*` -- 44 tags across the
# two appliance documents -- matched nothing, so governance never saw them. Two
# duplicate definitions were added to PI003 in front of a passing check, which
# is worse than no check at all, because a green run was read as agreement.
#
# **A trailing letter is a sub-tag, and was invisible until 2026-09-21.**
# `[LOG-FEX-070a]`, `[LOG-FEX-090a]`, `[IMPL-BOS-090b]` and `[IMPL-MPD-010a]`
# are real, bolded definitions -- a finding split after its neighbour was
# already numbered -- and the regex ended at the digits, so all eight
# occurrences were unreachable by every check here. One of them,
# `[GDE-FEX-070a]` in `tools/gaia_history.py`, was ALSO mis-prefixed, and was
# therefore wrong in two ways that no check could see.
#
# `x` is excluded because in this tree a trailing `x` is a wildcard digit, not
# a sub-tag: `[IMPL-BOS-07x]` means "the 070-series notes below", and BOSE002
# uses it that way twice. Measured before choosing the class -- `x` appears
# only in those two wildcard references, and `a`/`b` only in the eight real
# sub-tags, so the split is the tree's own usage rather than a guess.
TAG = re.compile(r"\[([A-Z][A-Z0-9]{1,5}-[A-Z0-9]{2,10}-[0-9]{2,4}[a-wyz]?)\]")

# Peel leading markdown one token at a time. '*' counts as a bullet only when
# followed by whitespace, so '**' (bold) survives to mark a definition.
# Emoji/symbol range is deliberately broad (U+2190-U+2BFF plus the emoji planes):
# a narrow list silently broke definition detection when a document used a
# character that had not been enumerated -- e.g. U+2705 white-heavy-check-mark.
PREFIX = re.compile(r"^(?:\s+|>|\#{1,6}|\d+[.)]|[-+]\s|\*\s|\||~~"
                    r"|[←-⯿️\U0001F300-\U0001FAFF])")
LINK = re.compile(r"\[[^\]\[]*\]\((?!https?:|file:|mailto:|#)([^)]+)\)")
CODESPAN = re.compile(r"`[^`\n]*`")
CODESPAN_INNER = re.compile(r"`([^`\n]*)`")

# [GOV-DOC-040]. A doc-cited repository path that no longer exists is the same
# class of failure as a broken markdown link, just inside backticks instead of
# `[]()`. Added 2026-09-02 after a review found six specs across two days
# still citing `player/src/db.rs`, `player/src/web.rs` and `player/src/
# engine.rs` by name -- some with the file's own line count -- after each had
# been split into a subdirectory of topic files. Scoped to the prefixes below,
# deliberately narrow: a prefix like `src/` or `go/` would also match paths
# GUIDE001/GUIDE002 cite *about a predecessor repository on its own disposal
# path*, where "does not exist here" is the point being made, not an error.
PATH_PREFIXES = ("player", "tools", "sql", "LempiPi", "LempiPlay3", "BosePi", "SmartPC",
                  "sendspin", "docs", "build")
CODE_PATH = re.compile(r"\b(?:%s)(?:/[\w.\-]+)+" % "|".join(PATH_PREFIXES))


def cited_paths(text):
    """Repository-looking paths inside backtick spans, trailing '.' stripped
    (a path ending a sentence, e.g. "...in `db.rs`.", is not part of it).

    A path under a `target/` directory is skipped: that is cargo's build
    output, absent from the tree by design, and a how-to that tells someone to
    run `player/target/release/lempi` is citing it correctly `[GDE-ARC-034]`.
    Surfaced when this checker began reading `HOWTO.md` at all -- the prefix
    list above was written narrow to avoid false positives, and could not
    anticipate a case it was never shown.
    """
    out = []
    for span in CODESPAN_INNER.findall(text):
        for m in CODE_PATH.finditer(span):
            path = m.group(0).rstrip(".")
            if "/target/" in path:
                continue
            out.append(path)
    return out

# Prefixes owned by inherited material. Lempi must not mint new tags with these.
# Lempi MAY cite them (e.g. [MFL-DEF-040]) -- citation is not definition, so the
# collision check below distinguishes the two by looking for a bolded definition.
RESERVED_PREFIXES = {"DBD", "MFL", "MTA", "LD", "AM", "AFS", "XFD", "SSP", "PERF", "ARCH"}

INHERITED_DIR = os.path.join("docs", "inherited")

# [GOV-DOC-010]. Under the target is where a document should live; over the
# limit it must be split. Between them is a note and not a defect.
DOC_TARGET = 250
DOC_LIMIT = 300

# **`[GOV-DOC-010]` is a MUST, and enforced as one.** It used to be reported
# as a warning indistinguishable from the advisory ones, `--strict` exited 0
# on warnings, and CI passed: fourteen breaches of a mandatory rule sat
# unnoticed in a list of fifty-two notes. They were split on 2026-09-10 and
# this set emptied, which is what it was for -- it held the backlog while the
# backlog was being cleared, and never grew.
#
# It stays, empty, so a document that goes over again is named as an
# outstanding breach by a deliberate act rather than by accident.
GOV_DOC_010_OUTSTANDING = set()

# Document numbers known to name two documents, awaiting a decision on which
# one keeps the number. Same contract as the register above: debt named by a
# deliberate act, so that a NEW duplicate is an error rather than the third
# entry in a list nobody reads.
#
# Both are the echo pair, added to `docs/spec/` beside older documents that
# already held the number. Renaming touches citations in several files and
# `CLAUDE.md` §9 says to assume someone else is in the echo documents, so the
# choice is the maintainer's. Retires when SPEC020/SPEC021 each name one
# document -- convention would move the newer pair to SPEC043/SPEC044.
DUPLICATE_NUMBERS_OUTSTANDING = {
    ("docs/spec", "SPEC020"),   # node-delay-control vs the-handoff
    ("docs/spec", "SPEC021"),   # echo-mode-control vs waveform-boundary-editor
}

# Known, accepted tag collisions. Each entry is debt with a stated retirement
# condition -- NOT a way to silence the check. A collision absent from this list
# is an error. Adding an entry requires a reason and a condition for removal.
#
# Retired 2026-08-30: the seven REQ-QUE-*/REQ-UI-01* collisions existed because
# Lempi's own REQ001 (a v1 artifact on the disposal path, [GDE-DIS-010]) reused
# tags McRhythm also uses. REQ001 was deleted the same day, its own stated
# retirement condition -- the register is empty until a genuinely new collision
# is found.
KNOWN_COLLISIONS = set()

# Dangling tags cited (in brackets) from documents that survive, but whose
# *definition* lived in a v1 document deleted 2026-08-30 per [GDE-DIS-010]
# (REQ001, SPEC001-004, and the seven pre-rearchitecture root docs). Recorded
# rather than rewritten: these are struck-through "dead entry" rows in GOV001's
# own master index and a superseded-citation note in LempiPi/PI001, kept as
# history rather than silently deleted along with the document they describe.
PREEXISTING_V1_DANGLING = {"REQ-AUD-020", "REQ-AUD-040", "REQ-MB-010",
                           "REQ-PD-010", "REQ-HW-020", "SPEC-AUD-010",
                           "SPEC-DB-010", "SPEC-RUST-010"}

# Paths cited only to record that they were deleted on purpose -- GUIDE002's
# disposal register [GDE-DIS-010] and the open questions naming the docs it
# superseded. "Does not exist" is the point those sentences make, not drift;
# an entry here retires only if the citing text is removed or rewritten to
# stop naming the dead path.
KNOWN_DELETED_PATHS = {"docs/spec/SPEC004-go-migration-guide.md", "docs/roadmap.md",
                        "docs/phase1-plan.md", "docs/user-interface.md",
                        "docs/audio-database.md", "docs/tech-stack-investigation.md",
                        "docs/cost-estimate.md", "docs/timeline-estimate.md",
                        # GUIDE001's disposal register, naming the file the
                        # closed `archive/sonos-direct-play` branch carried.
                        # The branch stayed in the previous repository
                        # `[GDE-NAM-030]`; the row exists to say so, and
                        # retires only if that row is rewritten to stop
                        # naming the file.
                        "player/src/sonos.rs"}

# Paths that are absent from the tree BY DESIGN rather than by deletion --
# gitignored per-run output a document names in order to explain why its
# figures are quoted inline instead of linked. Distinct from the register
# above: nothing was removed, and nothing is coming back.
#
# An entry retires when the citing text stops naming the path, or when the
# path stops being ignored. A tidier version of this would ask
# `git check-ignore` instead of holding a list, which would also cover
# `data/`, `out/` and `fleet/` if they are ever cited; left as a list while
# there is one entry.
KNOWN_ABSENT_PATHS = {
    # BOSE005 §"Both capture files sit under `BosePi/logs/`", excluded by
    # .gitignore:105. The document says so in the same sentence and quotes
    # every figure inline for exactly that reason.
    "BosePi/logs",
}

# Tags used illustratively in GOV001's taxonomy table -- examples of the FORM
# of an identifier, not references to real ones.
EXAMPLE_TAGS = {"REQ-AUD-010", "REQ-DB-020", "SPEC-AUD-020", "SPEC-AUD-040",
                "SPEC-PD-010", "ENT-TRACK-010", "ENT-PASSAGE-010",
                "UT-AUD-001", "UT-DB-001", "GOV-DOC-010"}

# Tags THIS FILE's own comments name as history -- a tag that was merged away,
# one that once meant two different things, one that was mis-prefixed. Each is
# cited to explain why a check exists, and none is a live reference.
#
# Registered rather than de-bracketed, because the brackets are how a reader
# recognises the thing being discussed. Without this the checker fails on its
# own prose the moment its scan reaches `tools/`, which is the next step of
# this work: `[GDE-FEX-070a]` was added to these comments by the very commit
# that taught the regex to see sub-tags.
SELF_CITED_AS_HISTORY = {"REQ-PD-050", "PI3-FOUND-080", "GDE-FEX-070a"}
EXAMPLE_TAGS |= SELF_CITED_AS_HISTORY


def strip_code(text):
    return CODESPAN.sub("", text)


# [GOV-DOC-040]. A `file.md#section` link whose FILE exists but whose SECTION
# does not is a broken link that looks whole: the reader lands at the top of a
# long document with no idea which part was meant. The check discarded the
# fragment (`.split("#")[0]`) and never looked, and seven had accumulated --
# three of them in ROADMAP.md, the one page whose entire job is being an index
# of other documents' sections. They came from splits: PI001 lost its §5 and
# §5b to PI023/PI024, GUIDE009 its §10 to GUIDE016, and the citations stayed
# pointing at the old file.
#
# GitHub's slug: lowercase, drop anything that is not word/space/hyphen, then
# spaces to hyphens, with `-1`, `-2` appended for repeats in document order.
# Validated against this tree before landing -- 206 of 213 anchored links
# resolve under it, and each of the 7 that do not was confirmed by hand to be
# a genuinely missing section rather than a slug this function gets wrong.
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
HTML_ANCHOR = re.compile(r'<a\s+(?:name|id)="([^"]+)"')
_slug_cache = {}


def slug(text):
    t = re.sub(r"`([^`]*)`", r"\1", text.strip())          # code spans keep their text
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)         # links keep their label
    t = re.sub(r"[*_~]", "", t)
    t = unicodedata.normalize("NFC", t.lower())
    t = re.sub(r"[^\w\- ]", "", t, flags=re.UNICODE)
    return t.replace(" ", "-")


def heading_slugs(path):
    """Every fragment `path` offers, fenced code blocks excluded."""
    if path in _slug_cache:
        return _slug_cache[path]
    out, fenced = set(), False
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = HEADING.match(line)
        if m:
            s = base = slug(m.group(2))
            i = 1
            while s in out:
                s, i = f"{base}-{i}", i + 1
            out.add(s)
        a = HTML_ANCHOR.search(line)
        if a:
            out.add(a.group(1).lower())
    _slug_cache[path] = out
    return out


def strip_lead(line):
    prev = None
    while prev != line:
        prev = line
        line = PREFIX.sub("", line, count=1)
    return line


# A definition opens a BLOCK, not merely a line. Added 2026-08-20: a citation
# that happens to wrap onto a new line at the tag looked identical to a
# definition, which produced twelve false "defined 2x" warnings -- including
# SPEC011 citing PI3-FOUND-030 mid-sentence and being reported as redefining it.
# A false duplicate is worse than none: it invites renumbering a tag that was
# fine.
#
# Requiring **bold** instead was measured and rejected: it would lose 62 real
# definitions, GUIDE002 alone defining GDE-ARC-* and GDE-PHS-* in headings.
BLOCK_START = re.compile(r"^\s*(?:\#|\||>|[-+*]\s|\d+[.)])")


def is_definition(line, tag, prev=None):
    """A definition OPENS a block; a citation sits inside a sentence.

    Deliberately conservative. An earlier heuristic accepted any bolded line
    containing the tag and reported 94 duplicate definitions, nearly all false.
    Index rows cannot be excluded by shape -- real definitions also live in
    multi-column tables -- so REFERENCE_SECTIONS names them instead.
    """
    body = strip_lead(line)
    m = re.match(r"(?:\*\*)?`?\[" + re.escape(tag) + r"\]`?(?:\*\*)?", body)
    if not m:
        return False
    # A possessive is a citation about a tag, never a definition of one:
    # "`[GDE-ECHO-330]`'s gate asks...", "`[GOV-DOC-010]`'s line limit".
    # Both open a block, so every other test here reads them as definitions.
    #
    # Measured over the whole tree before landing, because this file's own
    # history is two tightenings that cost more than they returned: this rule
    # removes 4 false duplicates and destroys **0** real definitions. Two
    # wider variants were measured and REJECTED -- treating any following
    # punctuation (`)` `.` `,` `;` em-dash) as a citation destroys 4 real
    # definitions, and treating a following colon as one destroys 10, all in
    # MCR-SPEC002. The residue of look-alike citations that remains is the
    # shape ambiguity this docstring already admits; it is not separable by
    # structure and should be fixed in the prose that causes it.
    if body[m.end():].startswith(("'s", "’s")):
        return False
    # `prev is None` means the caller has not said, so keep the old behaviour.
    if prev is None:
        return True
    # A line carrying its own marker -- bullet, heading, table row, numbered
    # item -- opens a block whatever precedes it. Needed because a list item
    # whose previous SIBLING wrapped has a continuation line above it, which is
    # neither blank nor a marker: that alone hid `[PI-IMG-030]`'s definition.
    if BLOCK_START.match(line):
        return True
    return not prev.strip() or bool(BLOCK_START.match(prev))


# Sections that INDEX tags defined elsewhere. Structure alone cannot distinguish
# these from definitions -- real definitions also live in multi-column tables
# (the lessons and risks tables) -- so they are named explicitly.
REFERENCE_SECTIONS = ("master specification search index", "identifier taxonomy standard")


def in_reference_section(heading):
    return heading and any(k in heading.lower() for k in REFERENCE_SECTIONS)


def lempi_docs():
    """Lempi-authored markdown. docs/inherited/README.md is ours, not inherited."""
    # LempiPi/ is documentation too -- the Raspberry Pi work was moved out of
    # docs/ so the appliance material sits with the image build that uses it,
    # and a checker that cannot see it reports its tags as dangling.
    #
    # **One folder per appliance, and the glob must follow.** BosePi/ was added
    # for the second machine, whose hardware and image differ enough that its
    # material would otherwise be interleaved with the first's. A folder the
    # checker cannot see is worse than no folder: its links go unverified and
    # its tags are reported as dangling from everywhere that cites them.
    # sendspin/ is the same shape again, one folder for one external
    # ecosystem under investigation rather than one appliance.
    #
    # SmartPC/ is the third machine folder. It is NOT an appliance -- x86_64,
    # no image, no overlay -- but the rule that earned this list is about
    # per-machine material having one home, not about what kind of machine it
    # describes.
    # **Found by walking, not by listing** `[GDE-ARC-034]`. The list this
    # replaces named one glob per folder, and the comment above records it
    # being extended three times -- each time after a folder had already been
    # invisible for a while. A list mirroring the filesystem fails the way
    # every such list fails: silently, in the direction of checking less.
    #
    # Measured 2026-09-18, the list was missing eleven files carrying 56 tags
    # and 37 links between them -- `tools/README.md` (27 tags), `README.md`
    # (29 links), the fixture READMEs, and `CLAUDE.md` itself, which is where
    # the rule to run this checker is written down. One of them cited
    # `[SPEC-AUD-040]` as a live specification two and a half weeks after the
    # document defining it was deleted, which is precisely what this checker
    # exists to catch.
    #
    # So the default is now "checked", and anything excluded has to say why.
    # Dot-directories by convention (`.git`, `.venv`, `.pytest_cache`, and
    # whatever the next tool invents) plus the named build outputs. A
    # generated `README.md` inside a cache is not a document, and skipping
    # the class rather than each instance is what keeps this from becoming
    # the same hand-maintained list one layer down.
    skip = {"node_modules", "target", "__pycache__"}
    out = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        for name in files:
            if name.endswith(".md"):
                out.append(os.path.relpath(os.path.join(root, name)).replace("\\", "/"))
    out = [p for p in out if INHERITED_DIR.replace("\\", "/") not in p]
    reg = os.path.join(INHERITED_DIR, "README.md")
    if os.path.exists(reg):
        out.append(reg)
    return out


def inherited_docs():
    return [p for p in glob.glob(os.path.join(INHERITED_DIR, "**", "*.md"), recursive=True)
            if os.path.basename(p) != "README.md"]


def tags_in(path, skip_banner=False):
    """Tags in a document.

    skip_banner drops ONLY the import banner (everything above the first
    horizontal rule), not every blockquote. Source documents legitimately put
    tags in blockquotes -- McRhythm's SPEC003 defines [MFL-DIST-010] in one --
    and dropping those made real tags appear undefined.
    """
    text = open(path, encoding="utf-8").read()
    if skip_banner and text.lstrip().startswith(">"):
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            text = parts[1]
    return set(TAG.findall(text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--verbose", action="store_true",
                    help="also list the documents in the 250-300 line band")
    args = ap.parse_args()
    errors, warnings = [], []

    # 1 -- inherited files must be prefixed [INH-HAZ-010]
    for p in glob.glob(os.path.join(INHERITED_DIR, "**", "*"), recursive=True):
        if not os.path.isfile(p) or p.endswith((".cpp", ".h")):
            continue
        base = os.path.basename(p)
        # Lempi-authored files that live here but are not inherited material
        if base in ("README.md", "PROVENANCE.json"):
            continue
        if not base.startswith("MCR-"):
            errors.append(f"[INH-HAZ-010] inherited file lacks MCR- prefix: {p}")

    # 2 -- reserved prefixes must not be defined by Lempi documents
    for p in lempi_docs():
        for t in tags_in(p):
            if t.split("-")[0] in RESERVED_PREFIXES:
                # a definition is bolded at line start; a citation is inline
                for line in open(p, encoding="utf-8"):
                    if f"**`[{t}]`" in line or f"**[{t}]**" in line:
                        errors.append(f"[INH-HAZ-020] {p} DEFINES reserved-prefix tag {t}")
                        break

    # 3 -- exact tag collisions: a collision is two DEFINITIONS, not a
    # citation. Lempi documents legitimately cite inherited tags such as
    # [MFL-DEF-040] and [ENT-MP-030]; that is correct cross-referencing, not a
    # namespace clash. Comparing raw presence flagged those as errors.
    def definitions(paths, skip_banner=False):
        out = {}
        for path in paths:
            text = open(path, encoding="utf-8").read()
            if skip_banner and text.lstrip().startswith(">"):
                parts = text.split("\n---\n", 1)
                if len(parts) == 2:
                    text = parts[1]
            prev = ""
            for line in text.splitlines():
                for t in set(TAG.findall(line)):
                    if is_definition(line, t, prev):
                        out.setdefault(t, []).append(path)
                prev = line
        return out

    vdef = definitions(lempi_docs())
    idef = definitions(inherited_docs(), skip_banner=True)
    for t in sorted(set(vdef) & set(idef)):
        if t in KNOWN_COLLISIONS:
            warnings.append(f"[INH-HAZ-020] known collision {t} "
                            f"(retires when {COLLISION_RETIRES_WHEN})")
            continue
        errors.append(f"[INH-HAZ-020] tag {t} DEFINED in both "
                      f"{os.path.basename(vdef[t][0])} and {os.path.basename(idef[t][0])}")

    # 4 -- relative links resolve, file AND fragment
    #
    # Scoped to `lempi_docs()` rather than `docs/**` since 2026-09-21. The
    # narrower glob checked 100 of 171 Lempi-authored documents: every link in
    # LempiPi/, BosePi/, LempiPlay3/, SmartPC/ and sendspin/ went unverified,
    # in a repository whose own rule is one folder per machine. Measured
    # before widening -- 0 further broken file links, so this costs nothing
    # today and covers the four fifths of the tree that was never watched.
    for p in lempi_docs():
        p = p.replace(os.sep, "/")
        root = os.path.dirname(p)
        for m in LINK.finditer(strip_code(open(p, encoding="utf-8",
                                               errors="replace").read())):
            href = m.group(1)
            filepart, _, frag = href.partition("#")
            target = os.path.normpath(os.path.join(root, filepart)) if filepart else p
            if not os.path.exists(target):
                errors.append(f"broken link: {p} -> {href}")
            elif frag and os.path.isfile(target):
                if frag.lower() not in heading_slugs(target):
                    errors.append(f"broken anchor: {p} -> {href} "
                                  f"(no such section in "
                                  f"{os.path.basename(target)})")

    # 6 -- tag definition integrity
    inherited_tags = set()
    for p in inherited_docs():
        inherited_tags |= tags_in(p, skip_banner=True)
    defs, uses = {}, {}
    for p in lempi_docs():
        heading, fenced, prev = "", False, ""
        for n, line in enumerate(open(p, encoding="utf-8"), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
            if fenced:
                continue
            if line.lstrip().startswith("#"):
                heading = line.strip("#").strip()
            ref = in_reference_section(heading)
            for t in set(TAG.findall(line)):
                d = is_definition(line, t, prev) and not ref
                (defs if d else uses).setdefault(t, []).append(f"{p}:{n}")
            prev = line
    for t, locs in sorted(uses.items()):
        if t in defs or t in inherited_tags or t in EXAMPLE_TAGS:
            continue
        if t in PREEXISTING_V1_DANGLING:
            warnings.append(f"pre-existing v1 dangling tag {t} at {locs[0]} "
                            f"(retires with the document, [GDE-DIS-010])")
            continue
        errors.append(f"dangling tag {t} cited at {locs[0]} but never defined")
    for t, locs in sorted(defs.items()):
        if len(locs) > 1 and t not in KNOWN_COLLISIONS:
            # Usually REQ001's summary table restating a detail entry, which is
            # deliberate. It stays a warning rather than being taught away,
            # because the same pattern hides real conflicts: [REQ-PD-050] once
            # meant "rotation hard lockout" in the table and "occasion
            # weighting" in the detail, and a citation elsewhere meant the
            # second. Suppressing the shape would have suppressed that.
            same_doc = len({l.rsplit(":", 1)[0] for l in locs}) == 1
            hint = " -- summary table and detail? check they still agree" if same_doc else ""
            warnings.append(f"tag {t} defined {len(locs)}x: {', '.join(locs)}{hint}")

    # 6a -- a document number identifies exactly one document, and the title
    #        agrees with the filename
    #
    # Two failures found by hand on 2026-09-21, neither visible to any check
    # here, both of the kind a reader hits rather than a tool:
    #
    #   * `docs/spec/` held TWO SPEC020s and TWO SPEC021s, each announcing its
    #     own number in its `#` heading. Links resolve because they carry
    #     filenames, but prose does not: "SPEC021 §2" appears in three
    #     documents and cannot be followed. This is exactly the hazard
    #     `[GOV-DOC-030]` introduced the `MCR-` prefix for -- "McRhythm and
    #     Lempi both number SPEC003-SPEC006 with different meanings" --
    #     reappearing inside one folder.
    #   * Five documents opened with a number belonging to a DIFFERENT, real
    #     document: `GUIDE032-sonos-direct-play-closed.md` announced itself as
    #     `# GUIDE016:`, which is the echo playback plan that ten other
    #     documents cite. Renumbering had moved the filenames and left the
    #     titles behind.
    #
    # Scoped per directory, because `docs/IMPL002` and `LempiPi/IMPL002` are
    # deliberately different series in different folders.
    by_number = {}
    for p in lempi_docs():
        key = p.replace(os.sep, "/")
        base = os.path.basename(key)
        m = re.match(r"^([A-Z]+[0-9]+)-", base)
        if not m:
            continue
        num = m.group(1)
        by_number.setdefault((os.path.dirname(key), num), []).append(key)

        heading = ""
        for line in open(key, encoding="utf-8", errors="replace"):
            if line.lstrip().startswith("#"):
                heading = line.strip()
                break
        hm = re.match(r"^#\s*([A-Z]+[0-9]+)\b", heading)
        if hm and hm.group(1) != num:
            errors.append(f"[GOV-DOC-030] {key} opens with '# {hm.group(1)}:' but is "
                          f"filed as {num} -- a reader who follows a link lands on a "
                          f"document announcing itself as something else")

    for (folder, num), paths in sorted(by_number.items()):
        if len(paths) < 2:
            continue
        msg = (f"[GOV-DOC-030] {num} names {len(paths)} documents in "
               f"{folder or '.'}: {', '.join(sorted(paths))} -- a bare "
               f"'{num}' citation with a section number cannot be followed")
        if (folder, num) in DUPLICATE_NUMBERS_OUTSTANDING:
            warnings.append(msg + " [outstanding, awaiting a renumber decision]")
        else:
            errors.append(msg)
    for folder, num in sorted(DUPLICATE_NUMBERS_OUTSTANDING):
        if len(by_number.get((folder, num), [])) < 2:
            print(f"    fixed, remove from DUPLICATE_NUMBERS_OUTSTANDING: "
                  f"{folder}/{num}")

    # 6b -- tags cited from the appliance's own code must exist
    #
    # The shell helpers cite findings as authority: a comment saying why a
    # line is the way it is, anchored to the measurement that decided it. But
    # nothing checked the anchor still existed. [PI3-FOUND-080] was cited
    # twice in LempiPi/lempi-btctl long after the tag itself was merged into
    # [PI3-FOUND-090] by a genre split, and no check could see it, because
    # this checker read only markdown. A citation that resolves to nothing is
    # worse than no citation: it looks like provenance.
    # **A pattern that matches nothing is an ERROR, not a quiet zero.**
    # `BosePi/bose-*` and `BosePi/setup-*.sh` matched no file in this
    # repository -- no BosePi script has ever been named either way -- so this
    # scan covered 0 of the 14 BosePi files that cite tags while appearing to
    # cover them. That is `[GOV-FBN-020]`'s rule ("a surface it cannot scan
    # reports BROKEN, never clean") broken by the checker that enforces it.
    #
    # The patterns below are the real filenames, extended 2026-09-21 to the
    # two directories that were never listed at all. Measured before landing:
    # 173 further citations across 28 files, all of which resolve.
    # `player/` and `tools/` are still deliberately absent -- 18 citations
    # there do NOT resolve, and are being worked separately rather than
    # silenced by omission.
    CODE_PATTERNS = ("LempiPi/lempi-*", "LempiPi/*.conf", "LempiPi/setup-*.sh",
                     "LempiPi/tests/*", "LempiPi/tests/stubs/*",
                     "BosePi/*.sh", "BosePi/*.service", "BosePi/*.conf",
                     "BosePi/*.ps1", "LempiPlay3/*.service",
                     "build/*.sh", "build/*.js", "build/Dockerfile.*")
    code_paths = []
    for pattern in CODE_PATTERNS:
        if not glob.glob(pattern):
            errors.append(f"code-scan pattern {pattern!r} matches no file -- "
                          f"this check is covering nothing it claims to cover "
                          f"[GOV-FBN-020]; fix the pattern or remove it")
    for pattern in CODE_PATTERNS:
        code_paths.extend(glob.glob(pattern))
    for cp in sorted(set(code_paths)):
        if not os.path.isfile(cp) or cp.endswith(".md"):
            continue
        try:
            text = open(cp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for t in sorted(set(TAG.findall(line))):
                if t in defs or t in inherited_tags or t in EXAMPLE_TAGS:
                    continue
                if t in PREEXISTING_V1_DANGLING:
                    continue
                errors.append(f"dangling tag {t} cited in code at {cp}:{n} "
                              f"but never defined")

    # 7 -- doc-cited repository paths must exist, advisory [GOV-DOC-040]
    #
    # Excludes docs/inherited/: those documents describe a predecessor
    # repository this tree never contained, so a cited path never existing
    # here is the expected case, not drift.
    for p in lempi_docs():
        for cited in cited_paths(open(p, encoding="utf-8").read()):
            if (cited in KNOWN_DELETED_PATHS or cited in KNOWN_ABSENT_PATHS
                    or os.path.exists(cited)):
                continue
            warnings.append(f"[GOV-DOC-040] {p} cites `{cited}`, "
                            f"which does not exist in the tree")

    # 5 -- line-count governance, advisory, two tiers [GOV-DOC-010]
    #
    # Revised 2026-08-20 with the rule itself. A single threshold at 250 made
    # every line over it look like a breach, so a document that had earned new
    # measured content could only keep it by cutting older reasoning. The band
    # says which is which: over TARGET is a note, over LIMIT is the split.
    breaches = []
    in_band = []
    for p in lempi_docs():
        n = sum(1 for _ in open(p, encoding="utf-8"))
        if n > DOC_LIMIT:
            key = p.replace(os.sep, "/")
            breaches.append((n, key))
            if key in GOV_DOC_010_OUTSTANDING:
                warnings.append(f"[GOV-DOC-010] {p} is {n} lines, over the "
                                f"{DOC_LIMIT}-line limit; outstanding breach, split it")
            else:
                errors.append(f"[GOV-DOC-010] {p} is {n} lines, over the "
                              f"{DOC_LIMIT}-line HARD LIMIT; split it")
        elif n > DOC_TARGET:
            # Counted, not warned. GOV001 says in as many words that this band
            # is "a note, not a defect", and there are 41 of them: 56% of this
            # checker's entire output was by-design non-defects, which is the
            # condition that hid fourteen real GOV-DOC-010 breaches in a list
            # of fifty-two notes. A reader who scrolls past the output learns
            # to scroll past all of it.
            #
            # `--verbose` still lists them, and a document sitting exactly ON
            # the limit is named unconditionally below: at 300 is a different
            # fact from at 260, and four documents are at 300 right now.
            in_band.append((n, p.replace(os.sep, "/")))

    # Surfaced separately, because a MUST buried among advisory notes is a MUST
    # nobody acts on -- which is how fourteen of them accumulated.
    if breaches:
        print(f"[GOV-DOC-010] {len(breaches)} document(s) over the "
              f"{DOC_LIMIT}-line hard limit:")
        for n, key in sorted(breaches, reverse=True):
            state = ("outstanding" if key in GOV_DOC_010_OUTSTANDING
                     else "NEW BREACH")
            print(f"    {n:5d}  {key}  [{state}]")
        stale = GOV_DOC_010_OUTSTANDING - {k for _, k in breaches}
        for key in sorted(stale):
            print(f"    fixed, remove from GOV_DOC_010_OUTSTANDING: {key}")
        print()

    if in_band:
        at_limit = [(n, k) for n, k in in_band if n == DOC_LIMIT]
        print(f"[GOV-DOC-010] {len(in_band)} document(s) between {DOC_TARGET} and "
              f"{DOC_LIMIT} lines -- a note, not a defect"
              f"{'' if args.verbose else '; --verbose to list'}")
        for n, key in sorted(at_limit, reverse=True):
            print(f"    {n:5d}  {key}  [AT the limit -- one added line is a breach]")
        if args.verbose:
            for n, key in sorted(in_band, reverse=True):
                if n != DOC_LIMIT:
                    print(f"    {n:5d}  {key}")
        print()

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    if errors and args.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
