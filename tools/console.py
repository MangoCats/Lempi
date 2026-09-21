#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Vipunen console, read-only half `[SPEC013]`, `[IMPL-SUI-040]`.

Stage 2 of [IMPL003](../docs/IMPL003-vipunen-console-build.md): the views. This
file's own connection to the library stays `mode=ro` throughout -- nothing in
it ever executes a `sqlite3` write -- so the safety claim is structural
rather than promised: a console that cannot open the database for writing
cannot damage a library a player is using, whatever route triggers the
request. That is narrower than "no POST route in this file," which was true
once but no longer is: stage 3's job dispatch and `[REQ-VIS-265]`'s unflag
both hang off `do_POST` below. Neither writes *here* -- a job runs the same
CLI a person would run by hand, against its own connection; unflaging
signals the already-running Lempi to write its own listener state over HTTP,
in `lempi_control.py`, never a `listener_flags` write from this process.

That is what makes it runnable against the live database on day one. The
library is WAL `[SPEC-SUI-082]`, so readers never block the player: browsing
seven thousand files here cannot interrupt a note being played there.

Three views:

  /            library -- what is known, and how it came to be known
  /folder      what is on disk, against what the database claims
  /profile/N   one passage's whole derivation

    python tools/console.py data/library.db --root "C:/Users/Mango Cat/Music"
"""

import argparse
import html
import http.client
import json
import os
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
from ingest_folder import AUDIO  # noqa: E402  -- one list of what counts as audio
import jobs as jobmod  # noqa: E402
import lempi_control  # noqa: E402  -- process/network side of the handoff

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "console_web")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fleet_targets():
    """Who this console pushes to, as `{{TOKEN}}` -> text.

    **The pages name no machine.** They used to: `export.html` generated
    `ssh pi@lempipi ...` as literal text, and `profile.html` said "Could not
    check lempipi" fifteen times. That is deployment configuration living in
    tracked application code -- wrong for anyone else's fleet, and wrong for
    this one the day a host is renamed `[GDE-ARC-033]`.

    Read from `fleet/targets.env` if that exists -- gitignored, this
    household's real hosts -- and otherwise from `fleet-example/targets.env`,
    which is tracked and deliberately generic. So a reader with no fleet sees
    a coherent page, and an operator who copies a command without having
    configured one gets `speaker-a`, which fails immediately, rather than a
    real hostname that might be the wrong real host.

    Same precedence as the player's own options `[GDE-CLI-090]`: the specific
    if defined, the generic otherwise. Read per request rather than cached,
    because editing the file and reloading the page should be the whole
    workflow.
    """
    vals = {}
    for d in ("fleet-example", "fleet"):          # generic first, real wins
        path = os.path.join(ROOT, d, "targets.env")
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    vals[k.strip()] = v.strip()
        except OSError:
            continue
    peer = vals.get("LEMPI_PEER_NAME", "speaker-a")
    user = vals.get("LEMPI_PEER_USER", "pi")
    port = vals.get("LEMPI_PEER_PORT", "13491")
    return {
        "{{PEER_NAME}}": peer,
        "{{PEER_SSH}}": f"{user}@{peer}",
        "{{PEER_URL}}": f"http://{peer}:{port}",
        "{{PEER_LIBRARY}}": vals.get("LEMPI_PEER_LIBRARY", "/srv/library/library.db"),
        "{{PEER_INCOMING}}": vals.get("LEMPI_PEER_INCOMING", "/srv/library/incoming"),
        "{{EXAMPLE_PEER}}": vals.get("LEMPI_EXAMPLE_PEER", "speaker-b"),
    }
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Vipunen's own port. Different from lempi_control.LEMPI_PORT (5720) because
# they are different services on the same machine, and because
# `[SPEC-SUI-170]` may start the player: colliding would make each look like
# the other's failure.
DEFAULT_PORT = 5730


def already_serving(port: int, timeout: float = 2.0) -> bool:
    """Whether a console is already answering on `port`.

    Asked as an HTTP question, not a socket one, for the same reason
    `lempi_control._lempi_has_vipunen_support` asks its own in HTTP: a socket
    that accepts proves a process holds the port, not that it serves this
    console. `/console.css` is a static asset -- it reads no database, so
    this stays a liveness question rather than a library one.

    Starting a second console on a port the first one owns is refused rather
    than attempted `[REQ-VIS-320]`. On Windows it would otherwise *succeed*
    and leave two live listeners on one address, which is how a Lempi
    launching this console ends up reporting `did not answer within 20s`
    while several consoles sit there bound.
    """
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", "/console.css")
            r = conn.getresponse()
            r.read()  # drain -- the body is never inspected, only the status
            return r.status < 400
        finally:
            conn.close()
    except OSError:
        return False


# 71 (characteristic, class) pairs across 18 characteristics is a complete
# vector `[SPEC-SA-040]`. Measured on the four reference tracks, and the number
# the completeness tick compares against.
FULL_FLAVOR = 71

# No "db" here on purpose `[IMPL-SUI-045]`: a connection shared by every
# handler thread is what wedged this console, so there is no longer anywhere
# process-wide to put one. `path` is what handlers open their own from.
STATE = {"path": None, "roots": [], "scan": None, "scanned_at": 0, "jobs": None,
         "build": None, "started_at": None, "port": None,
         # The two halves as this process actually has them open, read from
         # `PRAGMA database_list` rather than guessed from a filename or
         # re-sniffed from content `[PI-OWE-010]`. A player Vipunen starts must
         # be given BOTH, or it treats whichever single path it got as its
         # listener database -- and Vipunen's path is the catalogue.
         "library": None, "listener": None}


# ---------------------------------------------------------------- database ---

# How long a query waits for a lock before giving up. The player writes the
# listener half continuously and both halves are WAL, so contention is normal
# and momentary. What must not happen is waiting *forever*: an error reaches
# the page as a 500 it can show, where a hang reaches it as nothing at all.
# Generous enough that an ordinary checkpoint is invisible, short enough that
# a person is told rather than left watching a spinner.
BUSY_TIMEOUT = 15.0


def ro(db: str) -> sqlite3.Connection:
    """Read-only, and it must stay that way `[IMPL-SUI-040]`.

    Split or not `[IMPL-DBSPLIT-025]`: `lempi_db.connect` opens the
    catalogue as `main` -- this console is overwhelmingly a library browser
    -- and attaches the listener half for the history, flag and preference
    views that need it. On an unsplit installation it is the same
    `sqlite3.connect` this always was, with nothing attached.

    `role=ROLE_LIBRARY` is not arbitrary: the catalogue is the half this
    page bootstraps nothing in but reads most of, and putting it in `main`
    means a stray `CREATE` here could only ever shadow a *listener* table,
    which this file never writes. The authorizer refuses that too.

    **One connection per request, never one shared by every thread.** This
    used to return a single connection held in `STATE["db"]`, opened with
    `check_same_thread=False` and used by every handler thread at once.
    `sqlite3` serializes access per connection, so that one handle was a
    convoy: any request blocked inside SQLite held it, and every other
    request -- including ones wanting only the catalogue -- queued behind it
    with no timeout of its own. Observed live 2026-09-11 against a console
    launched from Lempi's browse page, stacks taken with `py-spy`: the accept
    loop healthy in `serve_forever`, two handler threads blocked in
    `conn.execute` on the shared handle, and every later connection
    accumulating in a five-deep listen backlog until new ones were refused
    outright. That refusal is what a co-resident Lempi reads as "no Vipunen
    here", so it starts another -- which is how three consoles ended up bound
    to one port.

    The player made the same call the other way and wrote down why
    (`web/mod.rs`: *"A path rather than a connection: `rusqlite`'s is not
    `Sync`, and a request opens its own"*). Vipunen was the odd one out.
    Measured at 1.5 ms warm, against requests that were already costing
    more than that, so the convoy bought nothing.

    `check_same_thread` goes back to `sqlite3`'s own `True`: a connection
    opened here is used and closed by the one thread that asked for it, and
    the stricter default now catches a future handler that tries to share
    one again.
    """
    conn = lempi_db.connect(db, lempi_db.ROLE_LIBRARY, timeout=BUSY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    return conn


def totals(conn) -> dict:
    q = lambda s: conn.execute(s).fetchone()[0]  # noqa: E731
    # `id_checks` is written by the fingerprint pass, not by schema.sql -- a
    # library nothing has ever fingerprinted has no such table at all, and a
    # query naming a missing table fails outright rather than finding nothing
    # `[REQ-LIB-165]`. "Never checked" must not crash the page that would say so.
    have = lempi_db.tables(conn)  # both halves, not just `main` [IMPL-DBSPLIT-025]
    return {
        "files": q("SELECT count(*) FROM files"),
        "passages": q("SELECT count(*) FROM passages"),
        "radio": q("SELECT count(*) FROM passages WHERE kind='radio'"),
        "recordings": q("SELECT count(*) FROM recordings"),
        # The two facets that are Vipunen's business and never the player's.
        "unidentified": q("SELECT count(*) FROM recordings WHERE mbid NOT LIKE '________-____-____-____-____________'"),
        "unchecked": q("SELECT count(*) FROM passages p WHERE p.kind='radio' AND NOT EXISTS "
                       "(SELECT 1 FROM id_checks c WHERE c.passage_id = p.passage_id)")
                     if "id_checks" in have else q("SELECT count(*) FROM passages WHERE kind='radio'"),
        "no_flavor": q("SELECT count(*) FROM passages p JOIN passage_recordings pr USING(passage_id) "
                       "WHERE p.kind='radio' AND NOT EXISTS "
                       "(SELECT 1 FROM flavor f WHERE f.subject_kind = 'recording' AND f.subject_id = pr.mbid)"),
    }


# Every `flavor` lookup here names `subject_kind` as well as `subject_id`, and
# that is load-bearing rather than tidy. The key is
# (subject_kind, subject_id, characteristic, class) and the index repeats that
# prefix `[SPEC-SC-060]`, so a lookup on `subject_id` alone matches neither and
# SQLite scans all 578,452 rows ONCE PER PASSAGE. Measured: >180 s against
# 0.044 s, and the plan goes from SCAN to SEARCH.
#
# This is the same fault `[REQ-LIB-165]` recorded against
# `release_recordings(mbid)` -- "the lookup uses the second column of the
# primary key, so no index applies". It was fixed there with a new index; here
# the prefix column is already known, so naming it costs nothing.
def library(conn, q: str = "", facet: str = "", limit: int = 400) -> list:
    """Rows for the library view.

    Vipunen browses to *inspect*, so every row carries derivation state the
    player's browse deliberately does not show `[SPEC-SUI-020]`: how much
    flavor, whether the id was ever checked, what named it.
    """
    where, args = ["p.kind = 'radio'"], []
    if q:
        where.append("(t.title LIKE ?1 OR t.artist LIKE ?1 OR t.album LIKE ?1 OR r.title LIKE ?1)")
        args.append(f"%{q}%")
    if facet == "unidentified":
        # Not an MBID: `local:audio:`, `local:track:N`, anything malformed.
        # Shape-checked rather than prefix-checked, so a fourth kind is caught
        # too -- the same test `[REQ-LIB-165]` applies.
        where.append("pr.mbid NOT LIKE '________-____-____-____-____________'")
    elif facet == "unchecked":
        where.append("NOT EXISTS (SELECT 1 FROM id_checks c WHERE c.passage_id = p.passage_id)")
    elif facet == "no-flavor":
        where.append("NOT EXISTS (SELECT 1 FROM flavor f WHERE f.subject_kind = 'recording' AND f.subject_id = pr.mbid)")

    sql = f"""
      SELECT p.passage_id, pr.mbid,
             COALESCE(r.title, t.title) AS title, t.artist, t.album,
             p.end_ms - p.start_ms AS len_ms, p.boundary_src,
             (SELECT count(*) FROM flavor f WHERE f.subject_kind = 'recording' AND f.subject_id = pr.mbid) AS flavor,
             (SELECT verdict FROM id_checks c WHERE c.passage_id = p.passage_id) AS verdict
      FROM passages p
      JOIN passage_recordings pr USING (passage_id)
      JOIN files fi USING (file_id)
      LEFT JOIN recordings r ON r.mbid = pr.mbid
      LEFT JOIN file_tags t ON t.file_id = fi.file_id
      WHERE {' AND '.join(where)}
      ORDER BY t.artist IS NULL, t.artist, t.album, p.passage_id
      LIMIT {int(limit)}"""
    return [dict(r) for r in conn.execute(sql, args)]


def flags(conn) -> list:
    """Recordings and passages flagged "for review" from Lempi's own
    play-history page `[REQ-VIS-265]`, newest flag first.

    Read-only, like everything else in this file -- the checkbox that sets
    and clears a flag lives in Lempi, because it is listener state and
    listener state is Lempi's to write `[SPEC-SC-020]`. This only ever looks.

    `listener_flags` may not exist at all on a library no version of Lempi
    carrying this feature has ever opened; that is "nothing flagged yet",
    not a broken page `[REQ-LIB-165]`.
    """
    have = lempi_db.tables(conn)  # both halves, not just `main` [IMPL-DBSPLIT-025]
    if "listener_flags" not in have:
        return []

    out = []
    for kind, subject_id, flagged_at in conn.execute(
            "SELECT subject_kind, subject_id, flagged_at FROM listener_flags "
            "ORDER BY flagged_at DESC"):
        passages, mbid = [], None
        if kind == "recording":
            mbid = subject_id
            passages = [r[0] for r in conn.execute(
                "SELECT passage_id FROM passage_recordings WHERE mbid=? "
                "ORDER BY weight DESC, passage_id", (mbid,))]
        else:
            pid = int(subject_id)
            if conn.execute("SELECT 1 FROM passages WHERE passage_id=?", (pid,)).fetchone():
                passages = [pid]
                row = conn.execute(
                    "SELECT mbid FROM passage_recordings WHERE passage_id=? "
                    "ORDER BY weight DESC, mbid LIMIT 1", (pid,)).fetchone()
                mbid = row[0] if row else None

        title = artist = None
        if mbid:
            row = conn.execute("SELECT title FROM recordings WHERE mbid=?", (mbid,)).fetchone()
            title = row[0] if row else None
            row = conn.execute(
                "SELECT a.name FROM recording_artists ra JOIN artists a ON a.mbid=ra.artist_mbid "
                "WHERE ra.mbid=? ORDER BY ra.weight DESC LIMIT 1", (mbid,)).fetchone()
            artist = row[0] if row else None
        if title is None and passages:
            # No recording (or the recording carries no title of its own) --
            # the file's own tag is what a listener actually saw play.
            row = conn.execute(
                "SELECT t.title, t.artist FROM passages p JOIN files fi USING(file_id) "
                "LEFT JOIN file_tags t ON t.file_id=fi.file_id WHERE p.passage_id=?",
                (passages[0],)).fetchone()
            if row:
                title, artist = title or row[0], artist or row[1]

        out.append({
            "subject_kind": kind, "subject_id": subject_id, "flagged_at": flagged_at,
            "title": title, "artist": artist, "passages": passages,
            # A passage-keyed flag from before a rescan renumbered things
            # resolves to nothing at all -- said plainly, not left blank
            # `[SPEC-DF-035]`.
            "resolved": bool(passages),
        })
    return out


def pending_counts(conn) -> dict:
    """How many reviewed decisions are sitting as drafts, not yet folded into
    the library `[REQ-VIS-275]` -- the same three tables `tools/apply_reviews
    .py`/`tools/apply_boundary_reviews.py` already read, counted rather than
    listed: a naive user has no reason to know these tools, or that saving an
    edit in Lempi's own editor is only the first of two deliberate steps
    before it can even be pushed anywhere `[SPEC021 §2]`. Zero for any table
    this library predates -- absence is "nothing pending," not an error.
    """
    have = lempi_db.tables(conn)  # both halves, not just `main` [IMPL-DBSPLIT-025]
    counts = {}
    for kind, table in (("id", "id_reviews"), ("boundary", "boundary_reviews"),
                        ("artist", "artist_reviews")):
        counts[kind] = (conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE applied_at IS NULL").fetchone()[0]
            if table in have else 0)
    counts["total"] = sum(counts.values())
    return counts


def pending_detail(conn) -> dict:
    """*What* is pending, not just how many `[REQ-VIS-275]`.

    The first half of the two-step apply: a person is shown the exact
    before/after of every edit that is about to be written, from this
    `mode=ro` connection, and only then offered the button that writes it.
    A confirmation that says "apply 4 edits?" asks for consent to something
    unseen; this asks for consent to a list.

    Read-only and re-derived per request. It deliberately does **not** cache:
    what is pending can change under this page (Lempi's own editor is still
    running), and a stale list is precisely the thing that would make the
    second click mean something other than what the first one showed.
    """
    have = lempi_db.tables(conn)
    out = {"boundary": [], "id": [], "artist": []}

    if "boundary_reviews" in have:
        for r in conn.execute(
                "SELECT br.passage_id, br.orig_start_ms, br.orig_end_ms, br.start_ms, br.end_ms, "
                "       br.orig_lead_out_ms, br.lead_out_ms, br.orig_fade_out_ms, br.fade_out_ms, "
                "       br.decided_at, "
                "       (SELECT r.title FROM recordings r "
                "         JOIN passage_recordings pr ON pr.mbid = r.mbid "
                "        WHERE pr.passage_id = br.passage_id "
                "        ORDER BY pr.weight DESC LIMIT 1) AS title "
                "  FROM boundary_reviews br WHERE br.applied_at IS NULL "
                " ORDER BY br.decided_at"):
            out["boundary"].append({
                "passage_id": r["passage_id"], "title": r["title"],
                "span": [r["orig_start_ms"], r["orig_end_ms"], r["start_ms"], r["end_ms"]],
                "lead_out": [r["orig_lead_out_ms"], r["lead_out_ms"]],
                "fade_out": [r["orig_fade_out_ms"], r["fade_out_ms"]],
                "decided_at": r["decided_at"],
            })

    if "id_reviews" in have:
        # Only a `reassigned` row with a chosen id changes anything:
        # `apply_reviews.py` selects exactly `decision = 'reassigned' AND
        # chosen_mbid IS NOT NULL`. `kept` and `deferred` record a judgement
        # and rewrite nothing, and their `applied_at` therefore stays NULL
        # for ever -- so the raw "pending" count includes rows that no apply
        # will ever clear. Listing those as things about to be written would
        # promise 99 changes and deliver 40, which is worse than not offering
        # the button at all. They are counted separately and explained.
        for r in conn.execute(
                "SELECT v.passage_id, v.decision, v.chosen_mbid, v.decided_at, "
                "       (SELECT r.title FROM recordings r WHERE r.mbid = v.chosen_mbid) AS chosen_title "
                "  FROM id_reviews v "
                " WHERE v.applied_at IS NULL AND v.decision = 'reassigned' "
                "   AND v.chosen_mbid IS NOT NULL "
                " ORDER BY v.decided_at"):
            out["id"].append({
                "passage_id": r["passage_id"], "decision": r["decision"],
                "chosen_mbid": r["chosen_mbid"], "chosen_title": r["chosen_title"],
                "decided_at": r["decided_at"],
            })
        out["id_recorded_only"] = {
            d: n for d, n in conn.execute(
                "SELECT decision, COUNT(*) FROM id_reviews "
                " WHERE applied_at IS NULL AND NOT (decision = 'reassigned' "
                "       AND chosen_mbid IS NOT NULL) GROUP BY decision")}

    if "artist_reviews" in have:
        for r in conn.execute(
                "SELECT recording_mbid, artist_name, decided_at "
                "  FROM artist_reviews WHERE applied_at IS NULL ORDER BY decided_at"):
            out["artist"].append({
                "recording_mbid": r["recording_mbid"], "artist_name": r["artist_name"],
                "decided_at": r["decided_at"],
            })

    out["counts"] = {k: len(v) for k, v in out.items() if isinstance(v, list)}
    out["total"] = sum(out["counts"].values())
    return out


def profile(conn, pid: int) -> dict:
    """One passage's whole derivation `[SPEC-SUI-040]`.

    Per-characteristic provenance is shown because it is stored per
    characteristic `[SPEC-SC-060]`: an aggregate "flavor: yes" would hide a
    mixture that measurably costs retrieval accuracy `[SPEC-FD-145]`.
    """
    p = conn.execute(
        "SELECT p.*, f.audio_md5, f.path, f.format, f.duration_ms, f.size_bytes "
        "FROM passages p JOIN files f USING(file_id) WHERE p.passage_id = ?", (pid,)).fetchone()
    if p is None:
        return {}
    creds = [dict(r) for r in conn.execute(
        "SELECT * FROM passage_recordings WHERE passage_id = ? ORDER BY mbid", (pid,))]
    recs = []
    for c in creds:
        r = conn.execute("SELECT * FROM recordings WHERE mbid = ?", (c["mbid"],)).fetchone()
        flav = [dict(x) for x in conn.execute(
            "SELECT characteristic, class, value, source, accuracy FROM flavor "
            "WHERE subject_kind='recording' AND subject_id = ? ORDER BY characteristic, class",
            (c["mbid"],))]
        recs.append({
            "credit": c,
            "recording": dict(r) if r else None,
            "flavor": flav,
            # Provenance is per characteristic, so a single source string would
            # be a claim the data does not support. Count them instead.
            "flavor_sources": sorted({x["source"] for x in flav}),
            "artists": [dict(a) for a in conn.execute(
                "SELECT ra.artist_mbid, ra.weight, ra.source, ar.name FROM recording_artists ra "
                "LEFT JOIN artists ar ON ar.mbid = ra.artist_mbid WHERE ra.mbid = ?", (c["mbid"],))],
        })
    return {
        "passage": dict(p),
        "tags": dict(conn.execute("SELECT * FROM file_tags WHERE file_id = ?",
                                  (p["file_id"],)).fetchone() or {}),
        "recordings": recs,
        "cached": conn.execute(
            "SELECT count(*) FROM lowlevel_cache WHERE audio_md5 = ? AND start_ms = ? AND end_ms = ?",
            (p["audio_md5"], p["start_ms"], p["end_ms"])).fetchone()[0],
        "check": dict(conn.execute("SELECT * FROM id_checks WHERE passage_id = ?",
                                   (pid,)).fetchone() or {}),
        # What each stage decided, and what it rejected. Written since the
        # migration and read by nothing until now `[SPEC-SC-100]`.
        "decisions": [dict(d) for d in conn.execute(
            "SELECT stage, outcome, confidence, detail, decided_at FROM ingest_decisions "
            "WHERE audio_md5 = ? ORDER BY decided_at", (p["audio_md5"],))],
        # A saved-but-not-yet-applied edit `[REQ-VIS-275]` -- distinct from
        # `boundary_src == 'manual'`, which only ever shows an edit already
        # folded in. This is the state that looked identical to "pushed" from
        # this very page until it wasn't: Lempi's editor commits a draft here
        # and changes nothing else, so a naive glance at this profile has no
        # way to tell "edited" from "edited, but only as far as the draft."
        "pending": _pending_for_passage(conn, pid, [c["mbid"] for c in creds]),
    }


def _pending_for_passage(conn, pid: int, mbids: list) -> dict:
    have = lempi_db.tables(conn)  # both halves, not just `main` [IMPL-DBSPLIT-025]
    out = {}
    if "id_reviews" in have:
        row = conn.execute(
            "SELECT decided_at FROM id_reviews WHERE passage_id=?1 AND applied_at IS NULL", (pid,)
        ).fetchone()
        if row:
            out["id"] = {"decided_at": row[0]}
    if "boundary_reviews" in have:
        row = conn.execute(
            "SELECT decided_at FROM boundary_reviews WHERE passage_id=?1 AND applied_at IS NULL", (pid,)
        ).fetchone()
        if row:
            out["boundary"] = {"decided_at": row[0]}
    if "artist_reviews" in have and mbids:
        placeholders = ",".join("?" * len(mbids))
        row = conn.execute(
            f"SELECT decided_at FROM artist_reviews "
            f"WHERE recording_mbid IN ({placeholders}) AND applied_at IS NULL "
            f"ORDER BY decided_at DESC LIMIT 1", mbids).fetchone()
        if row:
            out["artist"] = {"decided_at": row[0]}
    return out


def _peek(remote: str, kind: str, anchor_args: list, timeout: float = 12.0) -> dict:
    """One `remote_peek.py` subprocess call `[SPEC-DF-116]`. Never this
    process's own connection reaching across the network -- a subprocess,
    the same posture every write/read-adjacent action in this console
    already takes -- and never allowed to hang past `timeout`: a check that
    cannot run must not stop someone from working `[SPEC-DF-118]`.
    """
    tools = os.path.dirname(os.path.abspath(__file__))
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(tools, "remote_peek.py"), remote, "--kind", kind, *anchor_args],
            capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    text = (r.stdout or "").strip()
    if not text:
        return {"ok": False, "error": (r.stderr or f"no output, exited {r.returncode}").strip()[:300]}
    try:
        return json.loads(text.splitlines()[-1])
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"unparseable reply: {e}"}


def remote_status(conn, pid: int) -> dict:
    """A targeted remote read at the moment a profile is opened
    `[SPEC-DF-116..118]` -- never a database copy, never a block. Checks the
    two identities this page can actually hand off to Lempi's own editors
    (id review, boundary editing); an artist-review divergence is not
    offered here because this page never offers one to accept either.
    """
    remote = STATE["jobs"].get_remote()
    if not remote:
        return {"remote": None}   # nothing configured -- nothing to check against

    p = conn.execute(
        "SELECT p.kind, p.start_ms, p.end_ms, p.lead_in_ms, p.lead_out_ms, p.gain_db, f.audio_md5 "
        "FROM passages p JOIN files f USING(file_id) WHERE p.passage_id = ?1", (pid,)).fetchone()
    if p is None:
        return {"remote": remote, "reachable": False, "error": "no such passage"}
    anchor = {"audio_md5": p["audio_md5"], "passage_kind": p["kind"],
              "start_ms": p["start_ms"], "end_ms": p["end_ms"]}
    anchor_args = ["--audio-md5", p["audio_md5"], "--passage-kind", p["kind"],
                   "--start-ms", str(p["start_ms"]), "--end-ms", str(p["end_ms"])]
    local_mbid = conn.execute(
        "SELECT mbid FROM passage_recordings WHERE passage_id=?1 ORDER BY weight DESC, mbid LIMIT 1",
        (pid,)).fetchone()

    reachable = True
    checks = {}
    for kind, local_value in (
        ("id_review", {"mbid": local_mbid[0] if local_mbid else None}),
        ("boundary_review", {"start_ms": p["start_ms"], "end_ms": p["end_ms"],
                              "lead_in_ms": p["lead_in_ms"], "lead_out_ms": p["lead_out_ms"],
                              "gain_db": p["gain_db"]}),
    ):
        result = _peek(remote, kind, anchor_args)
        if not result.get("ok"):
            reachable = False
            continue
        current = result.get("current")
        checks[kind] = {"current": current, "local": local_value,
                         "diverged": current is not None and current != local_value}
    return {"remote": remote, "reachable": reachable, "anchor": anchor, "checks": checks}


def passage_flag_subjects(conn, pid: int) -> list:
    """Every `listener_flags` subject that plausibly names this passage --
    its own passage-keyed row, plus every recording currently linked to it
    `[SPEC-DF-112]`'s own `clear_flags_for()` already establishes this same
    shape for the same reason: a listener may have flagged it before it had
    a recording at all, or under one it has since moved away from. Read-only
    here; shared by the sync-status check and the unflag action below so
    the two can never disagree about what "flagged" means for this passage.
    """
    subjects = [("passage", str(pid))]
    for row in conn.execute(
            "SELECT DISTINCT mbid FROM passage_recordings WHERE passage_id=?1", (pid,)):
        subjects.append(("recording", row[0]))
    return subjects


def passage_flagged_locally(conn, subjects: list) -> bool:
    have = lempi_db.tables(conn)  # both halves, not just `main` [IMPL-DBSPLIT-025]
    if "listener_flags" not in have:
        return False
    return any(
        conn.execute("SELECT 1 FROM listener_flags WHERE subject_kind=?1 AND subject_id=?2",
                     (kind, sid)).fetchone()
        for kind, sid in subjects)


def flag_sync_status(conn, pid: int) -> dict:
    """Is this passage flagged locally, on the remote, and does that match
    what this page already shows -- the display `[REQ-VIS-265]`'s checkbox
    needs before offering to clear it everywhere at once.

    "Shown" is never fetched separately: this process only ever renders a
    passage from its own local database, so whatever a person is looking at
    on this very page *is* `local` at the moment it loaded. The only
    genuinely open question a live check can answer is whether the remote
    still agrees -- `[SPEC-DF-115]`'s own point, that lempipi's flags can
    change with no involvement from Vipunen at all.
    """
    subjects = passage_flag_subjects(conn, pid)
    local = passage_flagged_locally(conn, subjects)
    remote = STATE["jobs"].get_remote()
    if not remote:
        return {"local": local, "remote": None, "reachable": False, "remote_pid": None, "remote_mbids": []}

    p = conn.execute(
        "SELECT p.kind, p.start_ms, p.end_ms, f.audio_md5 FROM passages p "
        "JOIN files f USING(file_id) WHERE p.passage_id=?1", (pid,)).fetchone()
    if p is None:
        return {"local": local, "remote": None, "reachable": False, "remote_pid": None, "remote_mbids": []}
    anchor_args = ["--audio-md5", p["audio_md5"], "--passage-kind", p["kind"],
                   "--start-ms", str(p["start_ms"]), "--end-ms", str(p["end_ms"])]
    result = _peek(remote, "passage_flag", anchor_args)
    if not result.get("ok"):
        return {"local": local, "remote": None, "reachable": False, "remote_pid": None, "remote_mbids": []}
    current = result.get("current") or {}
    remote_mbids = []
    try:
        remote_mbids = json.loads(current.get("remote_mbids") or "[]")
    except (ValueError, TypeError):
        pass  # malformed reply from an old remote_peek.py -- treated as "none known"
    return {"local": local, "remote": bool(current.get("flagged")), "reachable": True,
            "remote_pid": current.get("remote_passage_id"), "remote_mbids": remote_mbids}


# ------------------------------------------------------------------ system ---
# Which running instance is this, and a way to stop it `[SPEC-SUI-210..212]`.
# Grew directly out of a real incident: two stale `console.py` processes were
# both alive against the same library, both bound to :5730 via a Windows
# `SO_REUSEADDR` quirk, and telling them apart took forensic process-listing
# by hand -- exactly the question this page exists to answer at a glance.

def build_info(repo_root: str) -> dict:
    """The commit (and working-tree state) this *process* loaded its source
    from at startup -- not a live `git status`, a snapshot `[SPEC-SUI-211]`.
    This tool has no compiled build to embed a version into; the checkout it
    runs from is the closest honest equivalent, and it cannot change under a
    process already running from it.
    """
    def git(*args):
        try:
            r = subprocess.run(["git", *args], cwd=repo_root, capture_output=True,
                               text=True, timeout=5)
        except OSError:
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    if commit is None:
        # Not a git checkout, or git isn't on PATH -- said plainly, the same
        # posture as every other capability here that can be absent
        # `[SPEC-DF-095]`, not a page that silently omits the section.
        return {"available": False}
    status = git("status", "--porcelain")
    dirty_files = status.count("\n") + 1 if status else 0
    return {
        "available": True,
        "commit": commit,
        "commit_short": git("rev-parse", "--short", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit_date": git("show", "-s", "--format=%cI", "HEAD"),
        "commit_subject": git("show", "-s", "--format=%s", "HEAD"),
        "dirty": None if status is None else dirty_files > 0,
        "dirty_files": dirty_files,
    }


def system_status() -> dict:
    runner = STATE["jobs"]
    active = None
    current = runner.current if runner else None
    if current is not None:
        j = runner.job(current)
        if j:
            active = {"job_id": j["job_id"], "kind": j["kind"], "target": j["target"], "state": j["state"]}
    return {
        "build": STATE["build"],
        "pid": os.getpid(),
        "started_at": STATE["started_at"],
        "port": STATE["port"],
        "db_path": STATE["path"],
        "roots": STATE["roots"],
        "active_job": active,
    }


def _shutdown_soon(httpd) -> None:
    """Off the request-handling thread, on purpose `[SPEC-SUI-212]`:
    `BaseServer.shutdown()` blocks until `serve_forever()`'s own loop (the
    main thread) returns, and calling it from that same loop would deadlock.
    A short delay lets the triggering request's own response actually reach
    the browser before the socket that would carry it stops accepting more.
    """
    time.sleep(0.3)
    httpd.shutdown()


# ----------------------------------------------------------------- handoff ---
# Reaching the player's own pages from inside Vipunen's workflow `[SPEC-SUI-140]`,
# `[SPEC-SUI-135]`. Vipunen never asks Lempi anything about the *library* it is
# running -- only the operating system, whether the port answers at all
# `[SPEC-SUI-025]`, `[SPEC-SUI-170]`, plus one narrow capability probe
# `[SPEC-SUI-213]` a socket alone cannot answer. The round trip closes through
# the shared database on Vipunen's next scan, not through this connection
# `[SPEC-SUI-145]`.
#
# The process/network primitives live in `lempi_control.py`, not here --
# reaching the player's own process, and signaling it to write, is a
# different concern than reading this file's own (`mode=ro`) connection, and
# keeping them apart is what makes this file's safety claim structural again.
# `unflag_everywhere` below stays a thin wrapper: the *read* side -- which
# subjects, whether a remote is configured, what it currently thinks -- is
# this file's own, resolved from `conn`; only the actual signal to each
# Lempi crosses into `lempi_control`.

def unflag_everywhere(conn, pid: int) -> dict:
    """Clear every plausible flag on this passage, locally and on the
    remote, in one action `[REQ-VIS-265]`. See `lempi_control.unflag_everywhere`
    for how the clear itself happens; this resolves what to clear.
    """
    subjects = passage_flag_subjects(conn, pid)
    remote = STATE["jobs"].get_remote()
    status = flag_sync_status(conn, pid) if remote else None
    return lempi_control.unflag_everywhere(subjects, remote, status)


def unflag_subject_everywhere(kind: str, subject_id: str) -> dict:
    """Clear exactly this `(kind, subject_id)` flag, with no passage to
    resolve through at all `[REQ-VIS-265]` -- the Flags list's own row is
    the primary key already, straight from `listener_flags`, including a
    row `flags()` reports as "no longer resolvable" and which
    `unflag_everywhere` above therefore has no passage to anchor through.
    See `lempi_control.unflag_subject_everywhere` for how the clear itself
    happens and what it cannot promise for a `passage`-kind flag.
    """
    return lempi_control.unflag_subject_everywhere(kind, subject_id, STATE["jobs"].get_remote())


# -------------------------------------------------------------------- scan ---

def scan(conn, roots: list) -> dict:
    """The cheap pass `[SPEC-SUI-060]`: stat, do not hash.

    Hashing 7,232 files costs about nine minutes at the measured 74 ms each
    `[SPEC-RLK-070]`, which is not a page load. `size_bytes` and `mtime` exist
    in the schema for exactly this -- "cheap change detection only"
    `[SPEC-SC-030]`.

    **Every verdict here is provisional and says so.** Only a hash separates
    `unknown` from `elsewhere`, or `changed` from `corrupt` `[SPEC-RLK-055]`,
    and a page that reported those without hashing would be asserting what it
    had not observed -- the hazard `[SPEC-RLK-140]` names. Resolving them is a
    job, and jobs are stage 3.
    """
    t0 = time.time()
    # Paths are compared with the platform's own case rules -- `normcase` is a
    # no-op on POSIX and folds on Windows. That is correct here and is NOT the
    # trap `[SPEC-RLK-020]` describes: that hazard is about paths crossing
    # between platforms, and nothing in this view is ever transported.
    def key(p):
        return os.path.normcase(os.path.normpath(p))

    disk = {}
    for root in roots:
        for dp, _, names in os.walk(root):
            for n in names:
                if n.lower().endswith(AUDIO):
                    full = os.path.join(dp, n)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    disk[key(full)] = (full, st.st_size, st.st_mtime)

    rows = {}
    for r in conn.execute("SELECT file_id, audio_md5, path, size_bytes, mtime FROM files"):
        rows[key(r["path"])] = r

    here = changed = 0
    unclaimed, missing = [], []
    for k, (full, size, _mtime) in disk.items():
        r = rows.get(k)
        if r is None:
            # By path alone this is unknown. Its hash may yet match a row whose
            # own path is stale, which would make it `moved` -- not decidable
            # here, and not guessed at.
            unclaimed.append(full)
        elif r["size_bytes"] == size:
            here += 1
        else:
            # The bytes changed. A retag changes size and leaves `audio_md5`
            # untouched `[SPEC-DF-020]`; corruption changes both. Same
            # observation, opposite meanings, and only a hash tells them apart.
            changed += 1
    for k, r in rows.items():
        if k not in disk:
            missing.append(r["path"])

    return {
        "roots": roots,
        "walked_ms": int((time.time() - t0) * 1000),
        "on_disk": len(disk),
        "rows": len(rows),
        # `assumed`, never `verified`: passed on size and mtime, not hashed.
        "assumed_here": here,
        "changed": changed,
        "unclaimed": sorted(unclaimed),
        "missing": sorted(missing),
        "verified": 0,
        "note": "cheap pass: nothing was hashed, so nothing here is verified",
    }


def completeness(conn) -> dict:
    """Library-wide stage coverage -- the view stage 0 proved was needed.

    A backlog of 136 unchecked passages sat invisible until a run stumbled over
    it `[IMPL-SUI-025]`. Nothing reported it because nothing asked.
    """
    t = totals(conn)
    return {
        "radio": t["radio"],
        "with_flavor": t["radio"] - t["no_flavor"],
        "id_checked": t["radio"] - t["unchecked"],
        "identified": t["radio"] - conn.execute(
            "SELECT count(*) FROM passages p JOIN passage_recordings pr USING(passage_id) "
            "WHERE p.kind='radio' AND pr.mbid NOT LIKE "
            "'________-____-____-____-____________'").fetchone()[0],
        "amplitude": conn.execute(
            "SELECT count(*) FROM passages WHERE kind='radio' AND lead_in_ms IS NOT NULL"
        ).fetchone()[0],
    }


# ------------------------------------------------------------------ server ---

class Handler(BaseHTTPRequestHandler):
    server_version = "VipunenConsole/0.1"

    def log_message(self, fmt, *args):  # quieter than the default
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # Class-level default so the attribute always exists, whatever order a
    # future handler does things in -- `_close_db` must never be the thing
    # that raises while unwinding someone else's exception.
    _conn = None

    # This request's own connection, opened on first use `[IMPL-SUI-045]`.
    #
    # Lazy rather than opened up front, for two reasons. Most routes here
    # serve a static file or read `STATE["jobs"]` and touch no library at
    # all, so opening one for them would be pure cost. And `stream()` sits
    # among the API routes but runs for up to fifteen minutes -- opening a
    # connection before the dispatch below would pin one open for that whole
    # time, which is the very thing this change exists to stop.
    def _db(self):
        if self._conn is None:
            self._conn = ro(STATE["path"])
        return self._conn

    def _close_db(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def send_file(self, name, ctype):
        path = os.path.join(WEB, name)
        if not os.path.isfile(path):
            return self.send_error(404)
        with open(path, "rb") as fh:
            body = fh.read()
        for token, value in fleet_targets().items():
            body = body.replace(token.encode(), value.encode())
        # **The player's address is resolved, never typed into the page**
        # `[GDE-CLI-090]`, `[GDE-ARC-033]`. `jobs.html` used to carry
        # `http://127.0.0.1:5720` as literal text, so a fleet that had moved
        # off the default port handed the operator a prefilled field that was
        # quietly wrong. `lempi_control.LEMPI_PORT` is already on the
        # resolution chain -- `LEMPI_PORT` if set, else the guarded default
        # mirrored from `default_port!` -- so substituting here means the
        # field follows the port wherever it goes, including for a reader who
        # never knew there was a default.
        if b"{{PLAYER_URL}}" in body:
            url = "http://127.0.0.1:%d" % lempi_control.LEMPI_PORT
            body = body.replace(b"{{PLAYER_URL}}", url.encode())
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # GET only. There is no `do_POST` in this file and that is the stage 2
    # safety claim in its most direct form `[IMPL-SUI-040]`.
    def do_GET(self):
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        self._conn = None
        try:
            if p == "/":
                return self.send_file("index.html", "text/html; charset=utf-8")
            if p == "/folder":
                return self.send_file("folder.html", "text/html; charset=utf-8")
            if p.startswith("/profile/"):
                return self.send_file("profile.html", "text/html; charset=utf-8")
            if p == "/console.css":
                return self.send_file("console.css", "text/css; charset=utf-8")
            if p == "/console.js":
                return self.send_file("console.js", "application/javascript; charset=utf-8")

            if p == "/api/totals":
                return self.send_json({"totals": totals(self._db()),
                                       "coverage": completeness(self._db())})
            if p == "/api/pending":
                return self.send_json(pending_counts(self._db()))
            if p == "/api/pending/detail":
                # Step one of two. Read-only, and the only thing that makes
                # the second step meaningful `[REQ-VIS-275]`.
                return self.send_json(pending_detail(self._db()))
            if p == "/api/library":
                return self.send_json(library(
                    self._db(), q=(qs.get("q") or [""])[0],
                    facet=(qs.get("facet") or [""])[0]))
            if p.startswith("/api/profile/") and p.endswith("/remote"):
                pid = int(p.split("/")[3])
                return self.send_json(remote_status(self._db(), pid))
            if p.startswith("/api/profile/") and p.endswith("/flag"):
                pid = int(p.split("/")[3])
                return self.send_json(flag_sync_status(self._db(), pid))
            if p.startswith("/api/profile/"):
                pid = int(p.rsplit("/", 1)[-1])
                d = profile(self._db(), pid)
                return self.send_json(d) if d else self.send_error(404)
            if p == "/jobs":
                return self.send_file("jobs.html", "text/html; charset=utf-8")
            if p == "/export":
                return self.send_file("export.html", "text/html; charset=utf-8")
            if p == "/flags":
                return self.send_file("flags.html", "text/html; charset=utf-8")
            if p == "/api/flags":
                return self.send_json(flags(self._db()))
            if p == "/mesh":
                return self.send_file("mesh.html", "text/html; charset=utf-8")
            if p == "/api/peers":
                # `active` is which peer a pull reads, and it is not a column:
                # `remote_config.sync_remote` has always been that, and giving
                # the radio group its own second source of truth is how the two
                # come to disagree. Derived, so they cannot.
                active = STATE["jobs"].get_remote()
                peers = STATE["jobs"].list_peers()
                for peer in peers:
                    peer["active"] = (peer["remote"] == active)
                return self.send_json(peers)
            if p == "/api/peers/reachable":
                # Asked separately from the list, and never as part of it: a
                # peer that is down costs this route two seconds, and the list
                # itself has to stay instant or the page cannot render before
                # the slowest node on the shelf answers. Probed in parallel
                # for the same reason.
                peers = STATE["jobs"].list_peers()
                out = {}
                threads = []

                def probe(peer):
                    out[peer["name"]] = lempi_control.peer_reachable(peer["remote"])

                for peer in peers:
                    t = threading.Thread(target=probe, args=(peer,), daemon=True)
                    t.start()
                    threads.append(t)
                for t in threads:
                    t.join(timeout=4)
                return self.send_json(out)
            if p == "/system":
                return self.send_file("system.html", "text/html; charset=utf-8")
            if p == "/api/system":
                return self.send_json(system_status())
            if p == "/api/remote":
                return self.send_json({"remote": STATE["jobs"].get_remote()})
            if p == "/api/jobs":
                return self.send_json(STATE["jobs"].recent())
            if p.startswith("/api/jobs/") and p.endswith("/stream"):
                return self.stream(int(p.split("/")[3]))
            if p.startswith("/api/jobs/"):
                d = STATE["jobs"].job(int(p.rsplit("/", 1)[-1]))
                return self.send_json(d) if d else self.send_error(404)
            if p == "/api/folder/scan":
                # Read-only and idempotent, so GET rather than the POST the
                # route sketch showed: a refresh must be harmless, and the
                # expensive half (hashing) is a job, not this.
                if STATE["scan"] is None or "refresh" in qs:
                    STATE["scan"] = scan(self._db(), STATE["roots"])
                    STATE["scanned_at"] = time.time()
                return self.send_json(STATE["scan"])
            if p == "/api/handoff/ensure":
                # Idempotent -- GET rather than a POST, the same reasoning as
                # the folder scan above: asking twice costs nothing when a
                # player is already there, which is the common case.
                return self.send_json(lempi_control.ensure_lempi(
                    db_path=STATE["path"], vipunen_build=STATE["build"],
                    listener_path=STATE["listener"], library_path=STATE["library"]))
            self.send_error(404)
        except BrokenPipeError:
            pass
        except Exception as e:  # a failed query must report, never render empty
            self.send_json({"error": f"{type(e).__name__}: {e}"}, code=500)
        finally:
            # Whatever happened, this request's connection goes back. Held
            # open it would be the old shared handle again, one per thread.
            self._close_db()

    # Server-sent events, not a WebSocket. A job emits progress in one
    # direction and takes its commands as POSTs, so a duplex socket would be
    # machinery for a direction nothing uses `[SPEC-SUI-030]`.
    def stream(self, job_id: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        after, idle = 0, 0
        try:
            while idle < 900:            # ~15 min of nothing, then let go
                evs = STATE["jobs"].events_since(job_id, after)
                for e in evs:
                    after = e["event_id"]
                    self.wfile.write(f"data: {json.dumps(e, default=str)}\n\n".encode())
                    self.wfile.flush()
                    if e["kind"] == "done":
                        return
                idle = 0 if evs else idle + 1
                time.sleep(1)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass          # the page went away; the job does not care

    # Stage 3's writes. Note what is NOT here: nothing in this file opens the
    # library for writing. These start jobs, and the jobs run the same CLIs a
    # person runs `[SPEC-SUI-015]`.
    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        self._conn = None
        try:
            if p.startswith("/api/profile/") and p.endswith("/accept-remote"):
                # [SPEC-DF-116..117]'s one deliberate exception to "the
                # console never writes the library" -- the anchor is
                # resolved server-side, fresh, from `pid`, never trusted from
                # the client, so a stale page cannot aim a write at the
                # wrong row.
                pid = int(p.split("/")[3])
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(body or b"{}") or {}
                kind, value = payload.get("kind"), payload.get("value")
                if kind not in ("id_review", "boundary_review") or not isinstance(value, dict):
                    return self.send_json(
                        {"error": "expected {kind: id_review|boundary_review, value: {...}}"}, code=400)
                row = self._db().execute(
                    "SELECT p.kind, p.start_ms, p.end_ms, f.audio_md5 FROM passages p "
                    "JOIN files f USING(file_id) WHERE p.passage_id=?1", (pid,)).fetchone()
                if row is None:
                    return self.send_json({"error": f"no such passage: {pid}"}, code=404)
                anchor = {"audio_md5": row["audio_md5"], "passage_kind": row["kind"],
                          "start_ms": row["start_ms"], "end_ms": row["end_ms"]}
                target = json.dumps({"kind": kind, "anchor": anchor, "value": value})
                return self.send_json({"job_id": STATE["jobs"].submit("accept-remote", target)})
            if p.startswith("/api/profile/") and p.endswith("/unflag"):
                # Not a job `[SPEC-SUI-080]`: unlike every write that model
                # wraps, nothing here spawns a Python tool against a
                # database at all -- both writes happen inside Lempi's own
                # process, over HTTP, synchronously, in the same request/
                # response cycle `_peek()`'s own remote check already uses.
                pid = int(p.split("/")[3])
                if self._db().execute("SELECT 1 FROM passages WHERE passage_id=?1",
                                      (pid,)).fetchone() is None:
                    return self.send_json({"error": f"no such passage: {pid}"}, code=404)
                return self.send_json(unflag_everywhere(self._db(), pid))
            if p == "/api/flags/unflag":
                # The Flags list's own "unflag" button `[REQ-VIS-265]` --
                # given directly, not resolved through a passage, so a row
                # `flags()` already reports as "no longer resolvable" can
                # still be cleared. See `unflag_subject_everywhere`'s own
                # doc for what it can and cannot promise for a
                # `passage`-kind subject specifically.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(body or b"{}") or {}
                kind, subject_id = payload.get("kind"), payload.get("subject_id")
                if kind not in ("recording", "passage") or not subject_id:
                    return self.send_json(
                        {"error": "expected {kind: recording|passage, subject_id: ...}"}, code=400)
                return self.send_json(unflag_subject_everywhere(kind, str(subject_id)))
            if p == "/api/induct/propose":
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                folder = (json.loads(body or b"{}") or {}).get("folder", "")
                if not folder or not os.path.isdir(folder):
                    return self.send_json({"error": f"not a folder: {folder}"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("propose", folder)})
            if p.startswith("/api/induct/") and p.endswith("/commit"):
                job_id = int(p.split("/")[3])
                prev = STATE["jobs"].job(job_id)
                # Confirm the plan that was read, not the folder as it is now
                # `[SPEC-SUI-070]`.
                if not prev or prev["kind"] != "propose" or prev["state"] != "done":
                    return self.send_json({"error": "no completed proposal to confirm"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("induct", prev["target"])})
            if p == "/api/reanalyze":
                # No propose/plan step, unlike fresh induction `[SPEC-SUI-070]`
                # -- this folder is already known, there is no "new files
                # discovered" surprise to preview, only whether to retry what
                # `identify` already gave up on `[SPEC-SUI-214]`.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                folder = (json.loads(body or b"{}") or {}).get("folder", "")
                if not folder or not os.path.isdir(folder):
                    return self.send_json({"error": f"not a folder: {folder}"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("reanalyze", folder)})
            if p == "/api/analyze-amplitude":
                # `[SPEC-SA-075]`, deliberately opt-in -- see `jobs.py`'s own
                # `SKIPPED` entry for why this is never part of `/api/reanalyze`
                # or fresh induction. `folder` is optional here (unlike
                # `/api/reanalyze`'s own required one): an empty/absent value
                # means the whole library, matching `analyze_amplitude.py`'s
                # own CLI default.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                folder = (json.loads(body or b"{}") or {}).get("folder", "") or ""
                if folder and not os.path.isdir(folder):
                    return self.send_json({"error": f"not a folder: {folder}"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("analyze-amplitude", folder)})
            if p == "/api/analyze-flavor":
                # Scoped to one passage, not a folder -- refreshing flavor
                # after a boundary edit is a per-passage question, and
                # re-running extraction over an entire folder just to reach
                # one changed passage would redo work on everything else
                # that is already cached and unaffected.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                passage_id = (json.loads(body or b"{}") or {}).get("passage_id")
                if not isinstance(passage_id, int) or passage_id <= 0:
                    return self.send_json({"error": f"not a passage id: {passage_id!r}"}, code=400)
                return self.send_json(
                    {"job_id": STATE["jobs"].submit("analyze-flavor", str(passage_id))})
            if p == "/api/release/suggest":
                # Discovery only `[SPEC-SUI-215]` -- never touches
                # passage_recordings, so no confirmation step belongs here.
                # `query` is optional -- the "browse" half of the feature:
                # a person overriding the algorithm's own guessed search.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(body or b"{}") or {}
                folder = payload.get("folder", "")
                if not folder or not os.path.isdir(folder):
                    return self.send_json({"error": f"not a folder: {folder}"}, code=400)
                target = json.dumps({"folder": folder, "query": payload.get("query") or None})
                return self.send_json({"job_id": STATE["jobs"].submit("suggest-release", target)})
            if p == "/api/release/accept":
                # The write half `[SPEC-SUI-215]` -- the one place this
                # feature touches the library, and only for whichever
                # release the operator actually picked, never automatically.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(body or b"{}") or {}
                folder, release_mbid = payload.get("folder", ""), payload.get("release_mbid", "")
                if not folder or not os.path.isdir(folder):
                    return self.send_json({"error": f"not a folder: {folder}"}, code=400)
                if not release_mbid:
                    return self.send_json({"error": "no release_mbid given"}, code=400)
                target = json.dumps({"folder": folder, "release_mbid": release_mbid})
                return self.send_json({"job_id": STATE["jobs"].submit("accept-release", target)})
            if p.startswith("/api/jobs/") and p.endswith("/stop"):
                return self.send_json({"stopped": STATE["jobs"].stop(int(p.split("/")[3]))})
            if p == "/api/remote":
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                remote = ((json.loads(body or b"{}") or {}).get("remote") or "").strip()
                if not remote or ":" not in remote:
                    return self.send_json({"error": "expected user@host:/path/to/listener.db"}, code=400)
                STATE["jobs"].set_remote(remote)
                return self.send_json({"remote": remote})
            if p == "/api/remote/pull":
                # Direction one `[SPEC-DF-109]`: lempipi's own flags, resolved
                # against this library. A count of flags on recordings or
                # passages that do not exist here yet is the job's own
                # `result`, not an error.
                remote = STATE["jobs"].get_remote()
                if not remote:
                    return self.send_json({"error": "no remote configured yet"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("remote-pull", remote)})
            if p == "/api/apply-reviews":
                # Step two of two `[REQ-LIB-175]`. The caller names the kinds
                # it just displayed, so this can never apply something the
                # person was not shown -- and `confirmed` must be explicit,
                # so a bare POST (a stray fetch, a replayed request, anything
                # automated) does nothing. Never chained to another job:
                # deliberate is the whole point of the button.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(body or b"{}") or {}
                kinds = [k for k in (payload.get("kinds") or [])
                         if k in ("boundary", "id", "artist")]
                if not payload.get("confirmed"):
                    return self.send_json(
                        {"error": "not confirmed -- review the pending edits first"}, code=400)
                if not kinds:
                    return self.send_json({"error": "no review kinds named"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit(
                    "apply-reviews", json.dumps({"kinds": kinds}))})
            if p.startswith("/api/peers/") and p.endswith("/enabled"):
                # The push checkbox. Separate from `/activate` on purpose:
                # included-in-a-push and read-for-a-pull are different
                # questions about the same peer, and one node is commonly
                # one and not the other.
                name = unquote(p.split("/")[3])
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(body or b"{}") or {}
                STATE["jobs"].set_peer_enabled(name, bool(payload.get("enabled")))
                return self.send_json({"ok": True, "name": name,
                                       "enabled": bool(payload.get("enabled"))})
            if p == "/api/remote/push-all":
                # Every ticked peer, one job `[SPEC-MESH-090]`. The names are
                # resolved here rather than in the job so the log says what
                # was asked for even if a peer is deleted while it runs.
                names = [pe["name"] for pe in STATE["jobs"].peers_for_push()]
                if not names:
                    return self.send_json(
                        {"error": "no peers are ticked for push"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit(
                    "remote-push-all", json.dumps(names)), "peers": names})
            if p == "/api/remote/push":
                # Direction two `[SPEC-DF-108..112]`: whatever review edits
                # have accumulated locally, landed on the remote through its
                # own sqlite3 CLI. Batched -- only ever run on request.
                remote = STATE["jobs"].get_remote()
                if not remote:
                    return self.send_json({"error": "no remote configured yet"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("remote-push", remote)})
            if p == "/api/peers":
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(body or b"{}") or {}
                name = (payload.get("name") or "").strip()
                remote = (payload.get("remote") or "").strip()
                # Optional: only a peer that has actually split
                # (`[IMPL002 §7.4]`) has a second path at all -- absent or
                # blank both mean "same file as remote", not an error.
                remote_listener = (payload.get("remote_listener") or "").strip() or None
                if not name or not remote or ":" not in remote:
                    return self.send_json(
                        {"error": "expected {name, remote: user@host:/path/to/library.db, "
                                  "remote_listener: user@host:/path/to/listener.db (optional)}"}, code=400)
                STATE["jobs"].upsert_peer(name, remote, remote_listener)
                return self.send_json({"name": name, "remote": remote, "remote_listener": remote_listener})
            if p.startswith("/api/peers/") and p.endswith("/delete"):
                name = p.split("/")[3]
                STATE["jobs"].delete_peer(name)
                return self.send_json({"deleted": name})
            if p.startswith("/api/peers/") and p.endswith("/activate"):
                # [SPEC-MESH-092]: this is the only thing that changes what
                # remote-pull/remote-push/sync-preferences act on -- those
                # three jobs are untouched, still reading remote_config.
                name = p.split("/")[3]
                remote = STATE["jobs"].activate_peer(name)
                if remote is None:
                    return self.send_json({"error": f"no such peer: {name}"}, code=404)
                return self.send_json({"remote": remote})
            if p == "/api/mesh/diff":
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(body or b"{}") or {}
                peer = next((pr for pr in STATE["jobs"].list_peers()
                             if pr["name"] == payload.get("peer")), None)
                if peer is None:
                    return self.send_json({"error": f"no such peer: {payload.get('peer')}"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("mesh-diff", peer["remote"])})
            if p == "/api/mesh/resolve":
                # `key`/`choice`/`value` are resolved server-side into one
                # `target` for the job [SPEC-MESH-098] -- the client sends a
                # peer *name*, resolved to its remote here rather than
                # trusted, the same posture accept-remote already takes
                # toward its own anchor.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = json.loads(body or b"{}") or {}
                peer = next((pr for pr in STATE["jobs"].list_peers()
                             if pr["name"] == payload.get("peer")), None)
                if peer is None:
                    return self.send_json({"error": f"no such peer: {payload.get('peer')}"}, code=400)
                if payload.get("table") not in ("recordings", "passages") or not payload.get("key"):
                    return self.send_json({"error": "expected {peer, table, key, choice|value}"}, code=400)
                if "choice" in payload:
                    if payload["choice"] not in ("local", "peer"):
                        return self.send_json({"error": "choice must be 'local' or 'peer'"}, code=400)
                    target = json.dumps({"peer": peer["remote"], "table": payload["table"],
                                          "key": payload["key"], "choice": payload["choice"]})
                elif "value" in payload and isinstance(payload["value"], dict):
                    target = json.dumps({"peer": peer["remote"], "table": payload["table"],
                                          "key": payload["key"], "value": payload["value"]})
                else:
                    return self.send_json({"error": "expected choice: local|peer, or value: {...}"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("mesh-resolve", target)})
            if p == "/api/remote/sync-preferences":
                # `[SPEC030]`: both directions in one job, last-write-wins by
                # `updated_at`, not a pull/push pair -- `listener_preferences`
                # has no baseline to fast-forward against, only a current
                # value and a timestamp.
                remote = STATE["jobs"].get_remote()
                if not remote:
                    return self.send_json({"error": "no remote configured yet"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("sync-preferences", remote)})
            if p == "/api/export/bundle":
                # A GUI over `export_bundle.py` `[IMPL007 Stage 4]`. `q`
                # becomes a `LIKE` pattern the same way `library()`'s own
                # search already works, not a second query language.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                q = ((json.loads(body or b"{}") or {}).get("q") or "").strip()
                if not q:
                    return self.send_json({"error": "type something to select by first"}, code=400)
                return self.send_json({"job_id": STATE["jobs"].submit("export-bundle", f"%{q}%")})
            if p == "/api/system/shutdown":
                # Refused while a job is running, not just discouraged --
                # `remote-push` briefly stops lempipi's own player mid-sync
                # `[SPEC-DF-111]`, and killing this process between that
                # `systemctl stop` and its own `systemctl start` would leave
                # the appliance silent with nothing left running to restart
                # it `[SPEC-SUI-212]`. Every job kind is refused, not only
                # that one -- the console has no way to tell "safe to
                # interrupt" apart from "not" any more cheaply than asking
                # whether one is running at all.
                active = system_status()["active_job"]
                if active:
                    return self.send_json(
                        {"error": f"{active['kind']} (job {active['job_id']}) is still running -- "
                                  f"stop it or wait for it to finish before shutting down"}, code=409)
                print(f"[system] shutdown requested via console UI (pid {os.getpid()})", flush=True)
                self.send_json({"ok": True, "message": "shutting down"})
                threading.Thread(target=_shutdown_soon, args=(self.server,), daemon=True).start()
                return
            if p == "/api/export/open-terminal":
                # An action, not a query -- POST, the same reasoning
                # `/api/jobs/:id/stop` already follows: it is not read-only
                # or idempotent to run twice, since a process starts each time.
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                d = (json.loads(body or b"{}") or {}).get("dir", "")
                return self.send_json(lempi_control.open_terminal(d))
            self.send_error(404)
        except Exception as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, code=500)
        finally:
            self._close_db()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # POSIX needs this to rebind a port still in `TIME_WAIT` from the previous
    # run. Windows means something else entirely by the same flag: there it
    # permits a *second live* socket to bind an address another process is
    # already listening on, silently, and then routes connections between them
    # unpredictably. Measured on 2026-09-11 -- four consoles bound
    # `127.0.0.1:5730` at once, and in the state that started this
    # investigation three were `LISTENING` while every connect was refused
    # outright. `already_serving()` is the deliberate check that replaces it;
    # this flag must not be the thing that decides.
    allow_reuse_address = os.name != "nt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--root", action="append", default=[], help="audio root; repeatable")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        print(f"no such database: {args.db}", file=sys.stderr)
        return 1
    # Before the database, because opening it is the expensive half and this
    # is the likelier failure: a console is usually already running.
    if already_serving(args.port):
        print(f"a console is already serving on 127.0.0.1:{args.port} -- "
              f"open http://127.0.0.1:{args.port}/ , or use --port for a second one",
              file=sys.stderr)
        return 1
    STATE["path"] = os.path.abspath(args.db)
    STATE["roots"] = [os.path.normpath(r) for r in args.root]
    # Beside the library, named after it, exactly as the id-check sidecar is.
    sidecar = os.path.splitext(STATE["path"])[0] + ".console.db"
    STATE["jobs"] = jobmod.Runner(STATE["path"], sidecar, roots=STATE["roots"])
    STATE["port"] = args.port
    STATE["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    STATE["build"] = build_info(REPO_ROOT)

    # Opened, read and closed here: the startup banner is the one library
    # question asked outside a request, and nothing should hold a connection
    # for the life of the process `[IMPL-SUI-045]`.
    boot = ro(STATE["path"])
    try:
        t = totals(boot)
        # `main` is the catalogue (role=ROLE_LIBRARY); the listener half is
        # the attached one, or `main` again when nothing is split.
        attached = {row[1]: row[2] for row in boot.execute("PRAGMA database_list")}
        STATE["library"] = attached.get("main") or STATE["path"]
        STATE["listener"] = attached.get(lempi_db.ALIAS[lempi_db.ROLE_LISTENER],
                                         STATE["library"])
    finally:
        boot.close()
    print(f"library: {t['files']:,} files, {t['radio']:,} radio passages")
    if STATE["roots"]:
        print(f"roots:   {', '.join(STATE['roots'])}")
    else:
        print("roots:   none given; the folder view will have nothing to walk")
    # Loopback only. It holds no write lock today, but it reads a private
    # library and stage 3 gives it one `[SPEC-SUI-010]`.
    print(f"jobs:    {sidecar}")
    b = STATE["build"]
    if b["available"]:
        print(f"build:   {b['commit_short']} ({b['branch']}, {b['commit_date']})"
              + (f" -- {b['dirty_files']} uncommitted file(s)" if b["dirty"] else ""))
    else:
        print("build:   not a git checkout (or git not on PATH) -- /system will say so too")
    print(f"pid:     {os.getpid()}")
    print(f"console: http://127.0.0.1:{args.port}/   (library opened read-only)")
    with Server(("127.0.0.1", args.port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
