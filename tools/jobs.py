#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Jobs for the Vipunen console `[SPEC-SUI-080]`, `[SPEC-SUI-085]`.

The job model is the real work of stage 3; induct is a thin caller
`[IMPL-SUI-050]`.

**The console still never writes the library.** Jobs run the same CLIs a person
runs by hand `[SPEC-SUI-015]`, as subprocesses; those write, as they always
have. Job bookkeeping goes to a sidecar beside the library. So the console's own
connection stays `mode=ro` through stage 3, and "it cannot damage the library"
remains structural rather than becoming a promise the moment writes appear.

The sidecar is also the answer to `[SPEC-SUI-085]`'s requirement that job state
survive the browser: it is a file, so a reload, a closed tab or a restarted
console all find the run still there. Putting it in `lempi.db` instead would add
tables Lempi never reads `[SPEC-SC-015]` and would take the write lock for
bookkeeping -- contending with the player `[SPEC-SUI-082]` to record that
nothing had happened.

Progress is **committed** work, never predicted work. Counts come from querying
the library for what the stage has actually landed, so the bar cannot claim what
the database has not accepted.
"""

import json
import os
import queue
import sqlite3
import subprocess
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import lempi_control  # noqa: E402  -- pause/resume/reload the co-resident player
import threading
import time

DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id     INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,          -- 'propose' | 'induct' | 'reanalyze'
                                        -- | 'export-bundle' | 'remote-pull'
                                        -- | 'remote-push' | 'accept-remote'
                                        -- | 'suggest-release' | 'accept-release'
                                        -- | 'analyze-amplitude' | 'analyze-flavor'
                                        -- | 'segment-dao' | 'cd-rip'
                                        -- | 'sync-preferences'
                                        -- | 'mesh-diff' | 'mesh-resolve'
                                        -- | 'apply-reviews'
    target     TEXT NOT NULL,          -- the folder, or a remote's user@host:/path
    state      TEXT NOT NULL,          -- queued|running|done|failed|stopped
    plan       TEXT,                   -- the proposal, as returned by --json
    result     TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at   TEXT
);
CREATE TABLE IF NOT EXISTS job_events (
    event_id INTEGER PRIMARY KEY,
    job_id   INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    at       TEXT NOT NULL,
    stage    TEXT,
    kind     TEXT NOT NULL,            -- stage|log|counts|error|done
    text     TEXT
);
CREATE INDEX IF NOT EXISTS job_events_job ON job_events(job_id, event_id);
-- One remembered remote per library, console-owned bookkeeping like the rest
-- of this sidecar -- never a table Lempi itself reads [SPEC-SC-015]. A single
-- row, key='sync_remote', value='user@host:/path/to/listener.db' -- the exact
-- form `scp`/`ssh` already want, so nothing here parses or validates it
-- beyond what the remote-pull/remote-push jobs need to split off a host.
CREATE TABLE IF NOT EXISTS remote_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Named peers for mesh sync `[SPEC-MESH-090]`. Additive, not a replacement
-- for `remote_config` above: the three existing sync jobs keep reading
-- `remote_config` unchanged, and activating a peer (`activate_peer()`) is
-- exactly `set_remote(peer.remote)` -- this table is only where names live.
CREATE TABLE IF NOT EXISTS sync_peers (
    name    TEXT PRIMARY KEY,
    remote  TEXT NOT NULL,             -- user@host:/path/to/library.db (or lempi.db, unsplit)
    enabled INTEGER NOT NULL DEFAULT 1
);
"""

# `remote_listener` is additive, not part of `DDL` above, because `sync_peers`
# already exists on every console this ships to and `CREATE TABLE IF NOT
# EXISTS` never adds a column to a table that's already there
# `[IMPL002 §7.4]`. NULL means "same file as `remote`" -- true for every
# peer that hasn't split, `bose` and today's lempi02w included, so nothing
# already configured needs re-entering. Needed because `sync_preferences.py`/
# `remote_flags.py`/`export_flags.py` all join listener-side tables against
# catalog-side ones through a peer's remote path, and a split peer can't
# serve both halves from the one path `remote` already carries.
_MIGRATIONS = [
    "ALTER TABLE sync_peers ADD COLUMN remote_listener TEXT",
]

# Stage 0 ran these by hand in this order and the transcript is the reference
# `[IMPL-SUI-020]`. Segmentation is absent because it is for DAO captures and
# wrong for single-track files `[SPEC-SA-070]`; release and cover-art fetches
# are absent because they need a real MBID, which self-published audio does not
# get `[SPEC-SUI-075]`. The plan says which it skips and why, rather than
# quietly running seven things.
def steps_for(db: str, folder: str, recheck: bool = False) -> list:
    """`recheck=True` is the one thing plain `induct` cannot do `[IMPL-SUI-020]`
    reused for `[SPEC-SUI-214]`'s `reanalyze` job: `fingerprint_ids.py` already
    skips any passage already in `id_checks`, `unmatched` included, so a
    second `induct` over an already-ingested folder can never retry one --
    `--recheck` is the tool's own existing flag for exactly that, simply never
    wired to a caller before now.
    """
    tools = os.path.dirname(os.path.abspath(__file__))
    identify = [sys.executable, os.path.join(tools, "fingerprint_ids.py"), db]
    if recheck:
        identify.append("--recheck")
    # Works `[GDE-WRK-125]` follow identification, because a Work hangs off a
    # recording MBID and `identify`/`merge` are what give one. They were a
    # one-off crawl until 2026-09-25, so a catalogue built later, or a
    # recording inducted since, had none and blocked only by recording
    # `[GDE-WRK-130]`. The fetch is capped (300 at MusicBrainz's 1 request/s,
    # five minutes) so one induct on a fresh cache is not a two-hour crawl --
    # what it leaves the next induct takes -- and it gives up when the network
    # is down rather than failing the induct: works enrich, they do not gate.
    # The cache sits beside the library, where `fetch_works.py` has always
    # expected `data/work_relations.db`.
    cache = os.path.join(os.path.dirname(os.path.abspath(db)), "work_relations.db")
    return [
        ("ingest", [sys.executable, os.path.join(tools, "ingest_folder.py"),
                    db, folder, "--commit", "--json"]),
        ("extract", [sys.executable, os.path.join(tools, "extract_library.py"), db]),
        ("identify", identify),
        ("merge", [sys.executable, os.path.join(tools, "fingerprint_ids.py"), db, "--merge"]),
        ("works", [sys.executable, os.path.join(tools, "fetch_works.py"), db,
                   "--cache", cache, "--limit", "300", "--give-up-after", "3"]),
        ("load-works", [sys.executable, os.path.join(tools, "load_works.py"), db,
                        "--cache", cache]),
    ]


SKIPPED = [
    ("segment", "for DAO captures; these are single-track files [SPEC-SA-070]. "
                "Built [SPEC024], offered as its own action (\"Segment DAO capture\"), "
                "never run silently by induct -- it needs an expected track count/"
                "durations no folder scan can infer on its own"),
    ("releases", "needs a MusicBrainz id, which ingest does not invent"),
    ("cover art", "needs a release id; art beside the file is found by the player"),
    ("amplitude", "built [SPEC-SA-075], not yet measured for Lempi -- offered as its "
                  "own action (\"Analyze amplitude\"), never run silently by induct"),
    ("cd-rip", "a physical rip has already happened by the time this runs -- "
               "[SPEC025..028], person-assisted per [SPEC-RIP-088] -- offered as its "
               "own action (\"Ingest a CD rip\"), never run silently by induct"),
]


def counts(db: str) -> dict:
    """What the library actually holds, right now. Read-only, and cheap.

    Every `flavor` lookup names `subject_kind` -- the prefix column of both the
    key and the index. Omitting it scans 578,452 rows per passage
    `[IMPL-SUI-045]`.
    """
    # Catalogue-only, read-only.
    c = lempi_db.connect(db, lempi_db.ROLE_LIBRARY)
    try:
        q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731
        return {
            "files": q("SELECT count(*) FROM files"),
            "radio": q("SELECT count(*) FROM passages WHERE kind='radio'"),
            "flavor": q("SELECT count(*) FROM passages p JOIN passage_recordings pr "
                        "USING(passage_id) WHERE p.kind='radio' AND EXISTS (SELECT 1 FROM flavor f "
                        "WHERE f.subject_kind='recording' AND f.subject_id=pr.mbid)"),
            "checked": q("SELECT count(*) FROM id_checks"),
        }
    finally:
        c.close()


class Runner:
    """One job at a time, and it says so `[SPEC-SUI-080]`.

    A second job would contend for the library's write lock with the first and
    with the player, and surface as an unexplained stall. Requests made while
    one runs are queued, and shown as queued.
    """

    def __init__(self, library: str, sidecar: str, roots: list | None = None):
        self.library = library
        self.roots = roots or []
        self.sidecar = sidecar
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.current = None          # job_id, while one runs
        self.proc = None             # the live subprocess, so it can be stopped
        db = self._db()
        db.executescript(DDL)
        for migration in _MIGRATIONS:
            try:
                db.execute(migration)
            except sqlite3.OperationalError:
                pass  # already applied -- ALTER TABLE ADD COLUMN has no IF NOT EXISTS
        db.commit()
        # A console killed mid-job leaves a row saying `running` and no process.
        # Say what happened rather than letting it look live for ever.
        db.execute("UPDATE jobs SET state='stopped', ended_at=?1 "
                   "WHERE state IN ('running','queued')", (now(),))
        db.commit()
        db.close()
        self._adopt_configured_remote()
        threading.Thread(target=self._work, daemon=True).start()

    def _adopt_configured_remote(self) -> None:
        """Carry a pre-registry remote into the peer list, once.

        `remote_config.sync_remote` was the whole of the configuration before
        `sync_peers` existed, and on every console that predates the peer
        list it still holds the only address anyone has entered. The page
        that now lists peers would show "no nodes yet" over a perfectly good
        configured remote, and the first thing a person would do is type it
        again -- so it is adopted instead, named for its host.

        Only when the registry is genuinely empty. A console that has peers
        has already answered this question, and re-adding the active one
        under a second name every restart would be its own small disaster.
        """
        db = self._db()
        try:
            if db.execute("SELECT 1 FROM sync_peers LIMIT 1").fetchone():
                return
            row = db.execute(
                "SELECT value FROM remote_config WHERE key='sync_remote'").fetchone()
            if not row or not row["value"]:
                return
            remote = row["value"]
            listener = db.execute(
                "SELECT value FROM remote_config WHERE key='sync_remote_listener'").fetchone()
            host = remote.partition(":")[0]
            name = (host.partition("@")[2] or host) or "remote"
            db.execute(
                "INSERT OR IGNORE INTO sync_peers (name, remote, remote_listener, enabled) "
                "VALUES (?1, ?2, ?3, 1)",
                (name, remote, listener["value"] if listener else None))
            db.commit()
        finally:
            db.close()

    def _db(self):
        db = sqlite3.connect(self.sidecar, timeout=30, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    # -- public ------------------------------------------------------------

    def submit(self, kind: str, target: str) -> int:
        db = self._db()
        cur = db.execute("INSERT INTO jobs (kind,target,state,created_at) VALUES (?1,?2,'queued',?3)",
                         (kind, target, now()))
        job_id = cur.lastrowid
        db.commit()
        db.close()
        self.q.put(job_id)
        return job_id

    def stop(self, job_id: int) -> bool:
        """Interrupt without loss `[REQ-LIB-130]`.

        The stage is killed between items, so the transaction it was inside is
        rolled back by SQLite and the rows it had already committed stand. At
        most the in-flight item is lost `[SPEC-SA-028]`.
        """
        with self.lock:
            if self.current == job_id and self.proc and self.proc.poll() is None:
                self.proc.terminate()
                return True
        db = self._db()
        n = db.execute("UPDATE jobs SET state='stopped', ended_at=?1 "
                       "WHERE job_id=?2 AND state='queued'", (now(), job_id)).rowcount
        db.commit()
        db.close()
        return bool(n)

    def job(self, job_id: int) -> dict:
        db = self._db()
        r = db.execute("SELECT * FROM jobs WHERE job_id=?1", (job_id,)).fetchone()
        ev = db.execute("SELECT * FROM job_events WHERE job_id=?1 ORDER BY event_id",
                        (job_id,)).fetchall()
        db.close()
        if r is None:
            return {}
        d = dict(r)
        d["plan"] = json.loads(d["plan"]) if d["plan"] else None
        d["result"] = json.loads(d["result"]) if d["result"] else None
        d["events"] = [dict(e) for e in ev]
        return d

    def get_remote(self) -> str | None:
        db = self._db()
        r = db.execute("SELECT value FROM remote_config WHERE key='sync_remote'").fetchone()
        db.close()
        return r["value"] if r else None

    def set_remote(self, value: str) -> None:
        db = self._db()
        db.execute("INSERT INTO remote_config (key, value) VALUES ('sync_remote', ?1) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
        db.commit()
        db.close()

    def get_remote_listener(self) -> str | None:
        """The listener-side path for whichever peer is active, or `None`
        when it's the same file as `get_remote()` -- true for every peer
        that hasn't split `[IMPL002 §7.4]`. Callers that need a listener
        path unconditionally should do
        `runner.get_remote_listener() or runner.get_remote()`.
        """
        db = self._db()
        r = db.execute(
            "SELECT value FROM remote_config WHERE key='sync_remote_listener'").fetchone()
        db.close()
        return r["value"] if r else None

    def set_remote_listener(self, value: str | None) -> None:
        db = self._db()
        if value is None:
            db.execute("DELETE FROM remote_config WHERE key='sync_remote_listener'")
        else:
            db.execute(
                "INSERT INTO remote_config (key, value) VALUES ('sync_remote_listener', ?1) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
        db.commit()
        db.close()

    def list_peers(self) -> list:
        db = self._db()
        rows = [dict(r) for r in db.execute(
            "SELECT name, remote, remote_listener, enabled FROM sync_peers ORDER BY name")]
        db.close()
        return rows

    def upsert_peer(self, name: str, remote: str, remote_listener: str | None = None) -> None:
        db = self._db()
        db.execute(
            "INSERT INTO sync_peers (name, remote, remote_listener) VALUES (?1, ?2, ?3) "
            "ON CONFLICT(name) DO UPDATE SET remote=excluded.remote, "
            "remote_listener=excluded.remote_listener",
            (name, remote, remote_listener))
        db.commit()
        db.close()

    def set_peer_enabled(self, name: str, enabled: bool) -> None:
        """Whether a push includes this peer `[SPEC-MESH-090]`.

        The column has existed since the registry did and nothing ever read
        it; this is what the checkbox writes. Independent of which peer is
        *active* -- a node can be the one you pull from and still be left out
        of a push, which is exactly what you want for a peer that is someone
        else's to edit.
        """
        db = self._db()
        db.execute("UPDATE sync_peers SET enabled=?1 WHERE name=?2", (1 if enabled else 0, name))
        db.commit()
        db.close()

    def peers_for_push(self) -> list:
        """The enabled peers, in the order the page shows them."""
        return [p for p in self.list_peers() if p["enabled"]]

    def delete_peer(self, name: str) -> None:
        db = self._db()
        db.execute("DELETE FROM sync_peers WHERE name=?1", (name,))
        db.commit()
        db.close()

    def activate_peer(self, name: str) -> str | None:
        """`[SPEC-MESH-092]`: makes `name` the target `remote-pull`/
        `remote-push`/`sync-preferences` act on, without those jobs
        changing at all -- they still just call `get_remote()`/
        `get_remote_listener()`.
        """
        db = self._db()
        r = db.execute(
            "SELECT remote, remote_listener FROM sync_peers WHERE name=?1", (name,)).fetchone()
        db.close()
        if r is None:
            return None
        self.set_remote(r["remote"])
        self.set_remote_listener(r["remote_listener"])
        return r["remote"]

    def recent(self, limit: int = 25) -> list:
        db = self._db()
        rows = db.execute("SELECT job_id,kind,target,state,created_at,started_at,ended_at "
                          "FROM jobs ORDER BY job_id DESC LIMIT ?1", (limit,)).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def events_since(self, job_id: int, after: int) -> list:
        db = self._db()
        rows = db.execute("SELECT * FROM job_events WHERE job_id=?1 AND event_id>?2 "
                          "ORDER BY event_id", (job_id, after)).fetchall()
        db.close()
        return [dict(r) for r in rows]

    # -- worker ------------------------------------------------------------

    def _emit(self, job_id, kind, text=None, stage=None):
        db = self._db()
        db.execute("INSERT INTO job_events (job_id,at,stage,kind,text) VALUES (?1,?2,?3,?4,?5)",
                   (job_id, now(), stage, kind, text))
        db.commit()
        db.close()

    def _work(self):
        while True:
            job_id = self.q.get()
            db = self._db()
            row = db.execute("SELECT * FROM jobs WHERE job_id=?1", (job_id,)).fetchone()
            db.close()
            if row is None or row["state"] != "queued":
                continue          # stopped before it ever started
            with self.lock:
                self.current = job_id
            try:
                self._run(job_id, row["kind"], row["target"])
            except Exception as e:
                self._emit(job_id, "error", f"{type(e).__name__}: {e}")
                self._finish(job_id, "failed")
            finally:
                with self.lock:
                    self.current = None
                    self.proc = None

    def _run(self, job_id: int, kind: str, target: str):
        db = self._db()
        db.execute("UPDATE jobs SET state='running', started_at=?1 WHERE job_id=?2",
                   (now(), job_id))
        db.commit()
        db.close()
        self._emit(job_id, "counts", json.dumps(counts(self.library)))

        if kind == "propose":
            # Propose is the real dry run, not an estimate: the tools already
            # refuse to write without --commit, so what is confirmed is what
            # was read `[SPEC-SUI-070]`.
            tools = os.path.dirname(os.path.abspath(__file__))
            code, out = self._spawn(job_id, "propose", [
                sys.executable, os.path.join(tools, "ingest_folder.py"),
                self.library, target, "--json"])
            plan = parse_json_tail(out)
            if plan is not None:
                # What will run, from the same list that runs it -- the page
                # used to type the four names itself, and would have gone on
                # showing four after `works` made six.
                plan["stages"] = [s for s, _ in steps_for(self.library, target)]
                plan["skipped"] = [{"stage": s, "why": w} for s, w in SKIPPED]
            db = self._db()
            db.execute("UPDATE jobs SET plan=?1 WHERE job_id=?2",
                       (json.dumps(plan) if plan else None, job_id))
            db.commit()
            db.close()
            return self._finish(job_id, "done" if code == 0 else "failed")

        if kind == "export-bundle":
            # A GUI over `export_bundle.py` `[IMPL007 Stage 4]`, one job, no
            # multi-stage loop -- the tool is already one atomic operation.
            # `target` carries the SQL LIKE pattern the console page built
            # from what was typed; the output directory is deterministic so
            # the page can offer it back without the tool needing to report it.
            tools = os.path.dirname(os.path.abspath(__file__))
            out_dir = os.path.join(os.path.dirname(tools), "out", f"bundle-{job_id}")
            argv = [sys.executable, os.path.join(tools, "export_bundle.py"),
                    self.library, "--like", target, "--gzip", "-o", out_dir]
            for root in self.roots:
                argv += ["--root", root]
            code, _ = self._spawn(job_id, "export", argv)
            db = self._db()
            db.execute("UPDATE jobs SET result=?1 WHERE job_id=?2",
                       (json.dumps({"out_dir": out_dir}), job_id))
            db.commit()
            db.close()
            return self._finish(job_id, "done" if code == 0 else "failed")

        if kind == "remote-pull":
            return self._remote_pull(job_id, target)

        if kind == "apply-reviews":
            return self._apply_reviews(job_id, target)
        if kind == "remote-push":
            return self._remote_push(job_id, target)
        if kind == "remote-push-all":
            return self._remote_push_all(job_id, target)

        if kind == "sync-preferences":
            return self._sync_preferences(job_id, target)

        if kind == "accept-remote":
            return self._accept_remote(job_id, target)

        if kind == "mesh-diff":
            # `target` is plain `user@host:/path` -- the same shape as
            # `remote-pull`/`remote-push`, not the JSON-packed target
            # `accept-release`-style jobs use, since a diff needs nothing
            # beyond which peer to compare against `[SPEC-MESH-096]`.
            return self._run_single_stage(job_id, "diff", [
                sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "mesh_diff.py"), self.library, target, "--json"])

        if kind == "mesh-resolve":
            return self._mesh_resolve(job_id, target)

        if kind == "suggest-release":
            return self._suggest_release(job_id, target)

        if kind == "accept-release":
            return self._accept_release(job_id, target)

        if kind == "segment-dao":
            return self._segment_dao(job_id, target)

        if kind == "analyze-amplitude":
            return self._analyze_amplitude(job_id, target)

        if kind == "analyze-flavor":
            return self._analyze_flavor(job_id, target)

        if kind == "cd-rip":
            return self._cd_rip(job_id, target)

        # 'induct' and 'reanalyze' `[SPEC-SUI-214]` are the same four-stage
        # pipeline, differing only in whether `identify` is told to retry
        # what it already tried -- anything else unrecognized also lands
        # here, matching this method's own long-standing fallthrough.
        return self._run_pipeline(job_id, target, recheck=(kind == "reanalyze"))

    def _run_pipeline(self, job_id: int, target: str, recheck: bool):
        result = {}
        for stage, argv in steps_for(self.library, target, recheck=recheck):
            if self._stopped(job_id):
                return self._finish(job_id, "stopped")
            self._emit(job_id, "stage", stage, stage=stage)
            code, out = self._spawn(job_id, stage, argv)
            if stage == "ingest":
                result["ingest"] = parse_json_tail(out)
            self._emit(job_id, "counts", json.dumps(counts(self.library)), stage=stage)
            if code != 0:
                self._emit(job_id, "error", f"{stage} exited {code}", stage=stage)
                db = self._db()
                db.execute("UPDATE jobs SET result=?1 WHERE job_id=?2",
                           (json.dumps(result), job_id))
                db.commit()
                db.close()
                return self._finish(job_id, "stopped" if code < 0 else "failed")
        db = self._db()
        db.execute("UPDATE jobs SET result=?1 WHERE job_id=?2", (json.dumps(result), job_id))
        db.commit()
        db.close()
        self._finish(job_id, "done")

    def _remote_pull(self, job_id: int, target: str):
        """A GUI over `remote_flags.py`/`import_flags.py` `[SPEC-DF-119]` --
        `target` is `user@host:/path/to/listener.db`. No `scp`, no database
        copy: `listener_flags` is one small table, fetched over one `ssh
        ... sqlite3 -json ...` round trip, the same targeted-read mechanism
        `[SPEC-DF-116]` already gave a single review anchor -- what
        `[SPEC-DF-114]` measured at over an hour was the copy, never the
        actual data. Whatever comes back is landed against the local
        library -- reporting, not silently dropping, whatever does not
        `[SPEC-DF-109]`. That `unmatched` count is exactly "flags on
        recordings or passages that don't exist locally," already computed
        by `import_flags.py`, not re-derived here.
        """
        tools = os.path.dirname(os.path.abspath(__file__))
        work = os.path.join(os.path.dirname(tools), "out", f"remote-pull-{job_id}")
        os.makedirs(work, exist_ok=True)
        flags_json = os.path.join(work, "flags.json")

        self._emit(job_id, "stage", "fetch-flags", stage="fetch-flags")
        fetch = [sys.executable, os.path.join(tools, "remote_flags.py"), target, "-o", flags_json]
        # `[IMPL002 §7.4]`'s second path, for the second of the three tools
        # that needed it. Against a split peer without this, `remote_flags.py`
        # reports "nothing flagged" from the catalogue half -- lempi02w has 19.
        listener = self.get_remote_listener()
        if listener:
            fetch += ["--remote-listener", listener.partition(":")[2] or listener]
        code, _ = self._spawn(job_id, "fetch-flags", fetch)
        if code != 0:
            return self._finish(job_id, "failed")
        self._emit(job_id, "stage", "import", stage="import")
        code, out = self._spawn(job_id, "import", [
            sys.executable, os.path.join(tools, "import_flags.py"),
            self.library, flags_json, "--commit", "--json"])
        result = parse_json_tail(out) or {}
        db = self._db()
        db.execute("UPDATE jobs SET result=?1 WHERE job_id=?2", (json.dumps(result), job_id))
        db.commit()
        db.close()
        return self._finish(job_id, "done" if code == 0 else "failed")

    def _sync_preferences(self, job_id: int, target: str):
        """`[SPEC030]` -- `target` is `user@host:/path/to/listener.db`, the
        same `remote_config` value `remote-pull`/`remote-push` already use.
        A single subprocess invocation, unlike `_remote_pull`'s two stages
        or `_remote_push`'s four: `sync_preferences.py` does its own
        manifest read, existence checks, and (when `--commit` lands
        something) both the local write and the remote patch/restart
        internally, so there is nothing this method needs to sequence.
        """
        tools = os.path.dirname(os.path.abspath(__file__))
        argv = [sys.executable, os.path.join(tools, "sync_preferences.py"),
                self.library, target, "--commit", "--json"]
        # `[IMPL002 §7.4]`'s second path, finally consumed. The column and
        # `get_remote_listener()` have existed since that review; nothing
        # read them, so this job addressed a split peer's catalogue with
        # queries only its listener half can answer and reported a clean
        # "nothing to do" `[SPEC-PREF-155]`. `None` -- every peer that has
        # not split -- passes nothing and behaves exactly as before.
        listener = self.get_remote_listener()
        if listener:
            argv += ["--remote-listener", listener.partition(":")[2] or listener]
        self._run_single_stage(job_id, "sync", argv)

    def _mesh_resolve(self, job_id: int, target: str):
        """`[SPEC-MESH-098]` -- `target` is JSON: `{peer, table, key, choice}`
        or `{peer, table, key, value}`, built by `/api/mesh/resolve` from
        what a person picked in the conflict review UI `[SPEC-MESH-100]`.
        `key` travels as a JSON array (`resolve_mesh_conflict.py`'s own
        `--key` argument), matching `mesh_diff.py`'s own identity tuples --
        never a local row id, which is exactly the non-portable key
        `[SPEC-DF-035]` already rules out for anything crossing a machine.
        """
        payload = json.loads(target)
        tools = os.path.dirname(os.path.abspath(__file__))
        argv = [sys.executable, os.path.join(tools, "resolve_mesh_conflict.py"),
                self.library, payload["peer"], "--table", payload["table"],
                "--key", json.dumps(payload["key"]), "--commit", "--json"]
        if "choice" in payload:
            argv += ["--choice", payload["choice"]]
        else:
            argv += ["--value", json.dumps(payload["value"])]
        self._run_single_stage(job_id, "resolve", argv)

    def _apply_reviews(self, job_id: int, target: str):
        """Fold this library's own reviewed drafts in `[REQ-LIB-175]`,
        `[SPEC021 §5]` -- the local half of what `_remote_push` then ships.

        **Why this is a button at all, having deliberately not been one.**
        The rule it appears to bend is `[IMPL-SUI-055]`, "the console never
        writes the library", and it does not bend it: nothing here touches
        SQLite through the HTTP handler. This runs the very
        `apply_boundary_reviews.py` / `apply_reviews.py` the profile page
        already prints, as subprocesses, through the same job model every
        other write in this console goes through. What changes is only who
        types them.

        That distinction matters because the original reasoning conflated two
        different things. "An edit changes what a passage *is*, and the
        library is Vipunen's to write, not a web click's" is an argument
        against an edit landing *thoughtlessly* -- as a side effect, on a
        timer, or folded into some larger workflow that happens to pass
        through here. It is not an argument for making a deliberate,
        understood action expensive: a command typed into a rarely-used
        terminal, against a path the person has to look up, is not safer
        than a button. It is the same act with worse odds of being done
        right, and with no record of what happened afterwards.
        So: a button, never automatic, never chained to another job, refused
        unless the caller has already been shown exactly what it will write
        (`/api/pending/detail`) and said yes a second time.

        `target` is JSON -- `{"kinds": [...]}` -- naming which of the three
        review kinds to fold in, so a person who reviewed only boundaries is
        never surprised by ninety-nine recording reassignments landing
        alongside them. The console builds it from what it just displayed,
        never from a guess about what is pending now.

        Five stages, and the player is genuinely interrupted for the middle
        three rather than merely warned about `[SPEC-SUI-082]`.
        """
        try:
            kinds = set(json.loads(target or "{}").get("kinds") or [])
        except (ValueError, TypeError):
            self._emit(job_id, "error", f"target must be JSON, got {target!r}")
            return self._finish(job_id, "failed")
        if not kinds:
            self._emit(job_id, "error", "no review kinds named")
            return self._finish(job_id, "failed")

        tools = os.path.dirname(os.path.abspath(__file__))
        result = {"kinds": sorted(kinds), "boundary": None, "id": None}

        # Was it playing? Asked before the pause, so `resume` can put the
        # player back as it found it rather than starting music at somebody
        # who had deliberately stopped it. `player_state` is the player's own
        # persisted answer, written every few seconds, which is close enough
        # for a question whose worst wrong answer is a pause that stays.
        was_playing = False
        try:
            c = lempi_db.connect(self.library, lempi_db.ROLE_LISTENER)
            try:
                row = c.execute("SELECT playing FROM player_state WHERE id = 1").fetchone()
                was_playing = bool(row and row[0])
            finally:
                c.close()
        except Exception:
            pass  # no player_state at all is "not playing", not an error

        self._emit(job_id, "stage", "pause", stage="pause")
        if lempi_control.pause_lempi():
            self._emit(job_id, "log",
                       "player paused" + (" (it was playing)" if was_playing
                                          else " (it was already stopped)"))
        else:
            # Not a failure. Nothing says a player has to be running, and the
            # write is correct without one -- the player attaches the
            # catalogue read-only and never writes the half being rewritten.
            self._emit(job_id, "log", "no local player answering; nothing to interrupt")

        ok = True
        if "boundary" in kinds:
            self._emit(job_id, "stage", "boundary", stage="boundary")
            code, out = self._spawn(job_id, "boundary", [
                sys.executable, os.path.join(tools, "apply_boundary_reviews.py"),
                self.library, "--commit", "--json"])
            result["boundary"] = parse_json_tail(out) or {}
            ok = ok and code == 0
        if ok and ({"id", "artist"} & kinds):
            self._emit(job_id, "stage", "id", stage="id")
            code, out = self._spawn(job_id, "id", [
                sys.executable, os.path.join(tools, "apply_reviews.py"),
                self.library, "--commit", "--json"])
            result["id"] = parse_json_tail(out) or {}
            ok = ok and code == 0

        # Reload before resuming, so what starts playing again is the library
        # as it now is. Asked even when the write failed: a partial apply is
        # exactly the case where a stale Director is most misleading.
        self._emit(job_id, "stage", "reload", stage="reload")
        if lempi_control.reload_lempi_library():
            self._emit(job_id, "log", "player asked to rebuild against the new library")

        self._emit(job_id, "stage", "resume", stage="resume")
        if was_playing:
            self._emit(job_id, "log", "playback resumed" if lempi_control.play_lempi()
                       else "could not resume playback -- press play in Lempi")
        else:
            self._emit(job_id, "log", "left stopped, as it was found")

        self._emit(job_id, "log", _apply_summary(result))
        db = self._db()
        db.execute("UPDATE jobs SET result=?1 WHERE job_id=?2", (json.dumps(result), job_id))
        db.commit()
        db.close()
        return self._finish(job_id, "done" if ok else "failed")

    def _push_to(self, job_id: int, target: str, tag: str = "",
                 listener: str | None = None) -> tuple:
        """A GUI over `export_changes.py`/`apply_changes.py --emit-sql`
        `[SPEC-DF-108..112]` -- the *edits* leg (id/boundary/artist reviews),
        not a raw flag push: Vipunen never sets a flag itself, only clears one
        (`--clear-flags`) when the correction it named actually lands. `target`
        splits into an ssh host and the remote's own db path -- the two
        things `ssh`/`sqlite3` need that a single `scp`-style argument does
        not carry on its own.

        Batched, not automatic: this runs only when asked, applying whatever
        has accumulated in the review tables since the last push -- the
        three-way merge already makes a second push of the same edits a
        no-op, so nothing here needs its own queue of "what changed since
        last time."

        No full-copy `scp` `[SPEC-DF-120]` -- measured at ~1.16 GB / over an
        hour `[SPEC-DF-114]`, and left standing when `[SPEC-DF-119]` gave
        `remote-pull` the identical fix, exactly because "converting it to a
        batch of targeted reads... remains future work" at the time. This is
        that work: `remote_snapshot.py` fetches, over one `ssh ... sqlite3
        -json` round trip per exported change, only what `apply_changes.py`'s
        own comparison ever reads for that change, and reconstructs a
        disposable local db just complete enough for that same, unmodified
        merge logic to run against. `export_changes.py`/`apply_changes.py`
        themselves are untouched by this -- `snapshot_db` is a drop-in
        replacement for what used to be a full `scp` copy, nothing more.
        """
        tools = os.path.dirname(os.path.abspath(__file__))
        work = os.path.join(os.path.dirname(tools), "out", f"remote-push-{job_id}{tag}")
        os.makedirs(work, exist_ok=True)
        snapshot_db = os.path.join(work, "remote-snapshot.db")
        changes_json = os.path.join(work, "changes.json")
        patch_sql = os.path.join(work, "patch.sql")

        host, sep, remote_path = target.partition(":")
        if not sep or not remote_path:
            self._emit(job_id, "error", f"target must be user@host:/path, got {target!r}")
            return False, {"error": f"target must be user@host:/path, got {target!r}"}

        # **A split peer gets the patch through its LISTENER half.**
        #
        # `apply_changes.py --emit-sql` traces statements for both halves:
        # `UPDATE passages` and `ALTER TABLE passages` are catalogue, while
        # `CREATE TABLE IF NOT EXISTS id_reviews` and the review-row inserts
        # are listener. This stage used to run the whole patch against the
        # catalogue path alone, and `CREATE TABLE` does not follow the attach
        # chain -- it always targets `main` -- so every push planted the three
        # review tables inside the catalogue half. Measured 2026-09-11:
        # lempi02w's `library.db` holds `id_reviews` (40) and `boundary_reviews`
        # (4) beside the real ones in its listener half (99 and 3). That is
        # the masking shadow `lempi_db` exists to prevent, arriving from the
        # one direction that had no such guard.
        #
        # Opening the LISTENER half as `main` and attaching the catalogue puts
        # every statement where it belongs, with no change to the patch: a
        # `CREATE` lands in `main`, which is now the right half, and an
        # unqualified `passages` resolves down the chain to the catalogue.
        # Measured both ways before being relied on, the same rule
        # `lempi_db`'s own module docstring documents.
        #
        # Unsplit peers -- no `remote_listener`, or the same file -- keep the
        # single-file command they always had, with nothing attached.
        lis_host, _, lis_path = (listener or "").partition(":")
        split = bool(lis_path) and lis_path != remote_path
        if split and lis_host != host:
            msg = (f"the two halves name different hosts ({host} and {lis_host}); "
                   "a peer's halves must live on one machine")
            self._emit(job_id, "error", msg)
            return False, {"error": msg}
        if split:
            apply_sql = ("{ echo \"ATTACH DATABASE '" + remote_path + "' AS lib;\"; "
                         "cat /tmp/lempi-sync-patch.sql; } | sqlite3 " + lis_path)
        else:
            apply_sql = "sqlite3 " + remote_path + " < /tmp/lempi-sync-patch.sql"

        self._emit(job_id, "stage", "export", stage="export")
        code, _ = self._spawn(job_id, "export", [
            sys.executable, os.path.join(tools, "export_changes.py"), self.library, "-o", changes_json])
        if code != 0:
            return False, {"error": "export failed"}
        self._emit(job_id, "stage", "snapshot", stage="snapshot")
        snap_argv = [
            sys.executable, os.path.join(tools, "remote_snapshot.py"), target, changes_json,
            "-o", snapshot_db, "--json"]
        # The review tables live in the listener half, so a split peer's
        # schema cannot be read from the catalogue path alone. Without this
        # the snapshot looks "bare" and the patch re-ships `CREATE TABLE`/
        # `ALTER TABLE` statements the remote already has -- which sqlite3
        # reports as errors, failing a push that in fact landed everything.
        if split:
            snap_argv += ["--remote-listener", listener]
        code, _ = self._spawn(job_id, "snapshot", snap_argv)
        if code != 0:
            return False, {"error": "could not read the remote"}
        # `--emit-sql`: `snapshot_db` is a disposable comparison, never the
        # target of the write itself `[SPEC-DF-111]` -- the real write to
        # lempi02w happens two stages further down, via its own `sqlite3` CLI.
        self._emit(job_id, "stage", "compare", stage="compare")
        code, out = self._spawn(job_id, "compare", [
            sys.executable, os.path.join(tools, "apply_changes.py"), snapshot_db, changes_json,
            "--commit", "--emit-sql", patch_sql, "--clear-flags", "--json"])
        result = parse_json_tail(out) or {}
        if code != 0:
            return False, result

        # One sentence, not five numbers -- the raw per-change breakdown
        # `apply_changes.py --json` already printed stays in the log exactly
        # as it was, for whoever wants it; a person watching the page gets
        # this instead of having to parse `compare`'s own JSON tail by eye.
        self._emit(job_id, "log", _push_summary(result))

        if not result.get("landed") and not result.get("cleared"):
            # Nothing to land -- lempi02w is never stopped for an empty patch.
            # `patch_statements` alone cannot tell this: it always includes
            # `ensure_review_tables`'s own schema-setup statements, so it is
            # never zero even when nothing actually changed. A second push
            # after the first, with nothing edited since, must cost the
            # household nothing `[SPEC-SUI-082]`'s own posture toward the
            # player applied to its live service instead of just its write
            # lock.
            self._emit(job_id, "log", "the remote was not touched.")
            return True, result

        self._emit(job_id, "stage", "send", stage="send")
        code, _ = self._spawn(job_id, "send", ["scp", patch_sql, f"{host}:/tmp/lempi-sync-patch.sql"])
        if code != 0:
            result["error"] = "could not send the patch"
            return False, result
        # The one command lempi02w already has `[SPEC-DF-111]`: stop so the
        # patch is never applied underneath a live writer, apply it through
        # lempi02w's own `sqlite3`, restart. Briefly interrupts whatever is
        # playing -- why this job runs only on explicit request, never per edit.
        #
        # `sudo` is load-bearing, not decoration `[SPEC-DF-121]`: a bare
        # `systemctl stop lempi` as the unprivileged deploy user fails outright
        # with "Interactive authentication required" -- found live, the first
        # time this stage ever ran against a real lempi02w outside a test's own
        # faked `_spawn`. `pi ALL=(ALL) NOPASSWD: ALL` (`PI005`) already grants
        # this without a password prompt, the same assumption
        # `LempiPi/deploy-player.sh` already makes for its own `systemctl`
        # calls -- this stage had simply never matched it.
        # **The restart is unconditional, and that is the whole point.**
        #
        # This was `stop && sqlite3 && start`, which reads as a sequence and
        # behaves as a guard: any failure of the patch skips the `start` and
        # leaves that node's player stopped, indefinitely, with only a failed
        # stage in a log to say why. A sync that cannot land its changes is a
        # disappointment; a sync that silently turns the music off in another
        # room is a fault.
        #
        # It is not hypothetical. `bose` mounts its catalogue half read-only
        # (`/srv/library`, `ext4 ro`), so `sqlite3` against it fails every
        # time -- measured 2026-09-11 -- and the old chain would have stopped
        # bose's player and left it that way.
        #
        # So: stop, try, restart whatever happened, and exit with the patch's
        # own status so the job still reports the failure honestly.
        self._emit(job_id, "stage", "apply-remote", stage="apply-remote")
        code, _ = self._spawn(job_id, "apply-remote", [
            "ssh", host,
            # Only a node that HAS the unit gets stopped and started.
            # `teacherslounge` deliberately runs no service -- it is launched
            # by hand -- and `systemctl stop lempi` there fails with "Unit
            # lempi.service not found", which is noise on every push and, read
            # quickly, looks like the push itself went wrong. A node with no
            # service has nothing holding the database open, so there is
            # nothing to stop.
            'have=$(systemctl list-unit-files lempi.service 2>/dev/null | '
            'grep -c "^lempi.service" || true); '
            '[ "$have" != 0 ] && sudo systemctl stop lempi; rc=0; '
            f"{apply_sql} || rc=$?; "
            '[ "$have" != 0 ] && sudo systemctl start lempi; exit $rc'])
        if code == 0:
            self._emit(job_id, "log", f"{host} now has these changes.")
        else:
            result["error"] = ("the remote refused the patch (its player was "
                               "restarted regardless)")
        return code == 0, result

    def _remote_push(self, job_id: int, target: str):
        """One named remote, the shape every existing caller and test uses."""
        ok, result = self._push_to(job_id, target, listener=self.get_remote_listener())
        db = self._db()
        db.execute("UPDATE jobs SET result=?1 WHERE job_id=?2", (json.dumps(result), job_id))
        db.commit()
        db.close()
        return self._finish(job_id, "done" if ok else "failed")

    def _remote_push_all(self, job_id: int, target: str):
        """Every peer whose "include in a push" box is ticked `[SPEC-MESH-090]`.

        One job, not one per peer: the household question is "are the other
        nodes up to date", and answering it across four machines in four
        separate logs is how a failure against the third one gets missed.

        A peer that fails does not stop the others. That is deliberate and it
        is the whole reason the per-peer result is kept separately: an
        unreachable bose must not silently cancel a push to lempi02w that
        would have worked, and "3 of 4 succeeded" is a true answer that a
        single aggregate exit code cannot express.
        """
        names = json.loads(target or "[]")
        peers = {p["name"]: p for p in self.list_peers()}
        per_peer, failed = {}, []
        for n, name in enumerate(names, 1):
            peer = peers.get(name)
            if peer is None:
                self._emit(job_id, "log", f"{name}: no such peer any more, skipped")
                per_peer[name] = {"error": "no such peer"}
                failed.append(name)
                continue
            self._emit(job_id, "stage", f"{name}", stage=name)
            ok, result = self._push_to(job_id, peer["remote"], tag=f"-{n}",
                                       listener=peer.get("remote_listener"))
            per_peer[name] = result
            if not ok:
                failed.append(name)
            self._emit(job_id, "log",
                       f"{name}: " + (_push_summary(result) if ok else
                                      result.get("error", "failed")))
        summary = {"peers": per_peer, "attempted": len(names),
                   "succeeded": len(names) - len(failed), "failed": failed}
        self._emit(job_id, "log",
                   f"{summary['succeeded']} of {summary['attempted']} peer(s) updated"
                   + (f"; failed: {', '.join(failed)}" if failed else "."))
        db = self._db()
        db.execute("UPDATE jobs SET result=?1 WHERE job_id=?2", (json.dumps(summary), job_id))
        db.commit()
        db.close()
        return self._finish(job_id, "done" if not failed else "failed")


    def _run_single_stage(self, job_id: int, stage: str, argv: list, *, require_ok: bool = True) -> None:
        """Spawn one stage, save its `--json` tail as the job's result, and
        finish done/failed -- the tail every one-stage job kind below shared
        already, five times over, before this existed: emit the stage, spawn
        it, parse whatever it printed, save that as the result regardless of
        outcome (a failure's own JSON is exactly what a caller wants to see),
        and finish on the same two facts every one of them finished by --
        the exit code, and (unless `require_ok` is False, for a tool whose
        `--json` carries no `ok` field to check) whether the tool itself
        said `"ok": true`.

        A multi-stage job (`_run_pipeline`, `_remote_pull`, `_remote_push`)
        does not fit this shape and is not forced into it: each stage there
        needs its own decision about whether a failure stops the whole job,
        which single-stage callers never have to make.
        """
        self._emit(job_id, "stage", stage, stage=stage)
        code, out = self._spawn(job_id, stage, argv)
        result = parse_json_tail(out) or {}
        db = self._db()
        db.execute("UPDATE jobs SET result=?1 WHERE job_id=?2", (json.dumps(result), job_id))
        db.commit()
        db.close()
        ok = code == 0 and (result.get("ok") if require_ok else True)
        self._finish(job_id, "done" if ok else "failed")

    def _accept_remote(self, job_id: int, target: str):
        """`[SPEC-DF-116..117]`'s one deliberate exception to "the console
        never writes the library" -- kept to that discipline's own shape:
        `accept_remote_basis.py` does the write, spawned the same way every
        other write here is, never this process's own (`mode=ro`)
        connection. `target` carries the small JSON the profile page's own
        POST already resolved server-side -- kind, anchor, and the remote
        value fetched moments before by `/api/profile/:id/remote`.
        """
        payload = json.loads(target)
        tools = os.path.dirname(os.path.abspath(__file__))
        anchor = payload["anchor"]
        argv = [sys.executable, os.path.join(tools, "accept_remote_basis.py"), self.library,
                "--kind", payload["kind"], "--audio-md5", anchor["audio_md5"],
                "--passage-kind", anchor["passage_kind"], "--start-ms", str(anchor["start_ms"]),
                "--end-ms", str(anchor["end_ms"]), "--value", json.dumps(payload["value"]),
                "--commit", "--json"]
        self._run_single_stage(job_id, "accept", argv)

    def _suggest_release(self, job_id: int, target: str):
        """Discovery only `[SPEC-SUI-215]` -- `target` is
        `{"folder", "query"}`, `query` optional (the "browse" half: a
        person's own search overriding the algorithm's guessed one).
        `suggest_release.py` itself never touches `passage_recordings`
        without `--accept`, so this is safe to run as freely as a search.
        """
        payload = json.loads(target)
        tools = os.path.dirname(os.path.abspath(__file__))
        argv = [sys.executable, os.path.join(tools, "suggest_release.py"),
                self.library, payload["folder"], "--json"]
        if payload.get("query"):
            argv += ["--query", payload["query"]]
        self._run_single_stage(job_id, "search", argv)

    def _accept_release(self, job_id: int, target: str):
        """The write half `[SPEC-SUI-215]` -- `target` is
        `{"folder", "release_mbid"}`, the same JSON-in-`target` shape
        `accept-remote` already uses. `suggest_release.py --accept` re-derives
        the same per-file matches from the release now cached by the
        discovery job (or fetches it fresh if this MBID was picked from
        outside the top candidates) and applies them.
        """
        payload = json.loads(target)
        tools = os.path.dirname(os.path.abspath(__file__))
        argv = [sys.executable, os.path.join(tools, "suggest_release.py"),
                self.library, payload["folder"], "--accept", payload["release_mbid"],
                "--commit", "--json"]
        self._run_single_stage(job_id, "apply", argv)

    def _segment_dao(self, job_id: int, target: str):
        """`[SPEC-SA-115..121]`, `[SPEC024]` -- `target` is
        `{"file", "expect"}`, the same JSON-in-`target` shape `suggest-release`
        already uses. `expect` is either an integer track count (Stage 2's
        grid search alone) or a comma-separated string of expected per-track
        durations in seconds (the full cascade) -- `segment_dao.py`'s own
        `--expect` accepts both, unchanged. Deliberately its own job kind
        rather than folded into `steps_for()`: named in `SKIPPED` for the
        same reason `amplitude` already is there -- it needs an expected
        count/durations no folder scan can supply on its own, so never run
        silently by `induct`/`reanalyze`, only by this explicit action.
        """
        payload = json.loads(target)
        tools = os.path.dirname(os.path.abspath(__file__))
        argv = [sys.executable, os.path.join(tools, "segment_dao.py"), self.library,
                "--file", payload["file"], "--expect", str(payload["expect"]),
                "--commit", "--json"]
        self._run_single_stage(job_id, "segment", argv)

    def _cd_rip(self, job_id: int, target: str):
        """`[SPEC025..028]` -- `target` is `{"folder"}`, a completed rip a
        person already ran (EAC's own GUI, or `cdrdao` by hand) and left
        sitting on disk; `ingest_cd.py` never touches an optical drive
        itself `[SPEC-RIP-088]`. Deliberately its own job kind, named in
        `SKIPPED` for the same reason `segment`/`amplitude` already are
        there -- a physical rip has already happened by the time this
        runs, so there is nothing for `induct`/`reanalyze` to trigger even
        if it wanted to.
        """
        payload = json.loads(target)
        tools = os.path.dirname(os.path.abspath(__file__))
        argv = [sys.executable, os.path.join(tools, "ingest_cd.py"), self.library,
                "--folder", payload["folder"], "--commit", "--json"]
        self._run_single_stage(job_id, "ingest", argv)

    def _analyze_amplitude(self, job_id: int, target: str):
        """`[SPEC-SA-075]` -- `target` is a folder path, or empty for the
        whole library. Deliberately its own job kind rather than folded into
        `steps_for()`: named in `SKIPPED` for the same reason `segment`,
        `releases` and `cover art` already are there -- not yet measured for
        Lempi, so never run silently by `induct`/`reanalyze`, only by this
        explicit action.
        """
        tools = os.path.dirname(os.path.abspath(__file__))
        argv = [sys.executable, os.path.join(tools, "analyze_amplitude.py"), self.library, "--json"]
        if target:
            argv += ["--folder", target]
        self._run_single_stage(job_id, "analyze", argv)

    def _analyze_flavor(self, job_id: int, target: str):
        """Refresh one passage's own flavor, `target` its passage id.

        Unlike `analyze-amplitude`, `extract_library.py` is already part of
        `steps_for()`'s normal induct/reanalyze pipeline -- this is not a new
        capability, only a narrower way to reach the same tool for one
        passage, offered from its own profile page rather than requiring a
        library- or folder-wide re-run just to pick up a boundary edit.

        `--json` here carries no `ok` field to check -- unlike the other
        four single-stage kinds, so `require_ok=False` keeps this the exit
        code alone, exactly as it was before `_run_single_stage` existed.
        """
        tools = os.path.dirname(os.path.abspath(__file__))
        argv = [sys.executable, os.path.join(tools, "extract_library.py"), self.library,
                "--passage", target]
        self._run_single_stage(job_id, "extract", argv, require_ok=False)

    def _spawn(self, job_id, stage, argv):
        # UTF-8 on both sides. `ingest_folder.say()` falls back to the console
        # encoding and renders a smart-quoted title as `?Duala?`; that is a
        # rendering artefact of a terminal, and a job is not one
        # `[IMPL-SUI-025]`.
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             env=env, cwd=os.path.dirname(os.path.dirname(
                                 os.path.abspath(__file__))))
        with self.lock:
            self.proc = p
        lines = []
        for line in p.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            if line.strip():
                self._emit(job_id, "log", line, stage=stage)
        p.wait()
        return p.returncode, "\n".join(lines)

    def _stopped(self, job_id) -> bool:
        db = self._db()
        r = db.execute("SELECT state FROM jobs WHERE job_id=?1", (job_id,)).fetchone()
        db.close()
        return r is not None and r["state"] == "stopped"

    def _finish(self, job_id, state):
        db = self._db()
        db.execute("UPDATE jobs SET state=?1, ended_at=?2 WHERE job_id=?3",
                   (state, now(), job_id))
        db.commit()
        db.close()
        self._emit(job_id, "done", state)


def parse_json_tail(out: str):
    """The last line that parses as a JSON object.

    `--json` prints one object and nothing else, but a warning on stderr is
    folded into the same stream, so the object is found rather than assumed to
    be the whole of it.
    """
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _apply_summary(result: dict) -> str:
    """One sentence for `_apply_reviews`, the same job `_push_summary` does
    for the push: each tool's own `--json` line stays in the log above this,
    and its per-passage detail with it -- this is the line a person reads.

    Says "nothing was written" out loud when that is what happened. A run
    that applies zero edits and says nothing at all is indistinguishable
    from one that worked, which is the failure mode this whole button exists
    to avoid.
    """
    parts = []
    b = result.get("boundary") or {}
    i = result.get("id") or {}
    if b.get("applied"):
        part = f"{b['applied']} boundary edit(s) written"
        if b.get("span_moved"):
            part += f" ({b['span_moved']} whose span moved)"
        parts.append(part)
    if i.get("applied"):
        parts.append(f"{i['applied']} recording id(s) reassigned")
    if i.get("artist_applied"):
        parts.append(f"{i['artist_applied']} artist credit(s) corrected")
    for label, d in (("boundary", b), ("id", i)):
        if d.get("error"):
            parts.append(f"{label}: {d['error']}")
        if d.get("skipped"):
            parts.append(f"{d['skipped']} {label} edit(s) skipped")
    if not parts:
        return "nothing was written -- there was nothing pending to apply."
    return ", ".join(parts) + ". These are now eligible to push to a remote."


def _push_summary(result: dict) -> str:
    """One sentence for `_remote_push`'s own `compare` stage, not five raw
    numbers -- `apply_changes.py --json`'s own line stays in the log exactly
    as printed, right above this, for whoever wants the detail; this is what
    a person glancing at the page actually needs to read.
    """
    total = sum(result.get(k, 0) for k in
                ("fastforward", "noop", "conflict", "missing", "resolved", "error"))
    if total == 0:
        return "nothing to sync -- no pending edits in your local library to push."
    parts = []
    landed = (result.get("fastforward") or 0) + (result.get("resolved") or 0)
    if landed:
        parts.append(f"{landed} change(s) to push")
    if result.get("noop"):
        parts.append(f"{result['noop']} already in sync there")
    if result.get("missing"):
        parts.append(f"{result['missing']} not present there yet")
    if result.get("conflict"):
        parts.append(f"{result['conflict']} conflict(s) waiting for --resolve")
    if result.get("error"):
        parts.append(f"{result['error']} refused")
    if result.get("cleared"):
        parts.append(f"{result['cleared']} flag(s) cleared")
    return ", ".join(parts) + "."


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
