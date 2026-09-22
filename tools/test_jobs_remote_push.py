#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s `_remote_push` wiring `[SPEC-DF-120]`.

Runs the real `Runner` and its real background worker thread. `export` and
`snapshot` are faked -- the former would otherwise read the real library
(fine, but not the point of this test), the latter would otherwise shell out
to `ssh`; `send`/`apply-remote` are faked the same way `test_jobs_remote_pull
.py` already fakes the pull side's own ssh-touching stage. `compare`
(`apply_changes.py`) runs for real, against a real tiny snapshot db a fake
`remote_snapshot.py` stage produced -- what is under test here is
specifically that `remote_snapshot.py` replaced the old `scp` full-copy
`fetch` stage without changing anything about the stages around it.

    python tools/test_jobs_remote_push.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apply_changes  # noqa: E402
import jobs as jobmod  # noqa: E402

SCHEMA = """
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    length_ms INTEGER, source TEXT NOT NULL);
CREATE TABLE listener_flags (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    flagged_at TEXT NOT NULL, origin TEXT, PRIMARY KEY (subject_kind, subject_id)) WITHOUT ROWID;
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, kind TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT);
CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT);
CREATE TABLE id_checks (passage_id INTEGER);
"""

MD5_A = "a" * 32
REC_TARGET = "aaaaaaaa-0000-0000-0000-000000000002"

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")
    return cond


def build_library(path: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.commit()
    c.close()


def build_snapshot(path: str) -> None:
    """What a real `remote_snapshot.py` run would have produced for one
    resolvable `id_review` change -- a passage the remote already has, ready
    to fast-forward.

    `jobs.py`'s own `work` directory is keyed by `job_id`, which restarts
    from 1 for every fresh `Runner` -- the same path can be reused across
    this file's own two tests (and across repeat runs of this file), so a
    stale file from a previous run has to be cleared first, the same guard
    `remote_snapshot.py`'s own real `build()` already takes.
    """
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
            start_ms INTEGER, end_ms INTEGER);
        CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT);
        CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT, length_ms INTEGER, source TEXT);
    """)
    c.execute(f"INSERT INTO files VALUES (1, '{MD5_A}')")
    c.execute("INSERT INTO passages VALUES (100, 1, 'radio', 0, 200000)")
    apply_changes.ensure_review_tables(c)
    c.commit()
    c.close()


CHANGES_DOC = {"format_version": 1, "changes": [{
    "kind": "id_review",
    "anchor": {"audio_md5": MD5_A, "passage_kind": "radio", "start_ms": 0, "end_ms": 200000},
    "baseline": {"mbid": None},
    "target": {"mbid": REC_TARGET, "title": "A Song", "artists": []},
    "decided_at": "2026-08-31T00:00:00", "origin": "desktop",
}]}


def wait_for(runner, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = runner.job(job_id)
        if j and j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def fake_spawn_success(changes_doc, captured_argv=None):
    def _spawn(self, job_id, stage, argv):
        if stage == "export":
            check("export_changes.py" in argv[1], f"export must run export_changes.py, got {argv}")
            out_path = argv[argv.index("-o") + 1]
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(changes_doc, f)
            return 0, "1 change(s) exported"
        if stage == "snapshot":
            check("remote_snapshot.py" in argv[1], f"snapshot must run remote_snapshot.py, got {argv}")
            out_path = argv[argv.index("-o") + 1]
            build_snapshot(out_path)
            return 0, json.dumps({"ok": True, "changes": 1, "resolved": 1, "out": out_path})
        if stage in ("send", "apply-remote"):
            if captured_argv is not None:
                captured_argv[stage] = argv
            return 0, ""
        return jobmod.Runner._spawn(self, job_id, stage, argv)
    return _spawn


def fake_spawn_snapshot_unreachable(self, job_id, stage, argv):
    if stage == "export":
        out_path = argv[argv.index("-o") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(CHANGES_DOC, f)
        return 0, "1 change(s) exported"
    if stage == "snapshot":
        return 1, "could not reach pi@lempi02w:/srv/library/library.db: no route to host"
    raise AssertionError(f"stage {stage!r} must never run after snapshot fails")


def test_push_lands_a_change(tmp: str) -> None:
    print("export -> snapshot -> compare -> send -> apply-remote, no full-copy fetch anywhere, "
          "and the change actually lands")
    library = os.path.join(tmp, "library.db")
    build_library(library)
    sidecar = os.path.join(tmp, "library.console.db")
    runner = jobmod.Runner(library, sidecar)
    captured = {}
    runner._spawn = fake_spawn_success(CHANGES_DOC, captured).__get__(runner, jobmod.Runner)
    job_id = runner.submit("remote-push", "pi@lempi02w:/srv/library/library.db")
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"expected done, got {j}")
    stages = [e["stage"] for e in j["events"] if e["kind"] == "stage"]
    check(stages == ["export", "snapshot", "compare", "send", "apply-remote"],
          f"expected exactly these five stages in this order, got {stages}")
    check("scp" not in json.dumps(j["events"]),
          "no scp full-copy step should appear anywhere in this job's log")

    # `[SPEC-DF-121]` A real, previously-uncaught bug: a bare `systemctl` as
    # the unprivileged deploy user fails outright with "Interactive
    # authentication required" -- found live against a real lempi02w, not by
    # any test, because this stage was faked wholesale above (and everywhere
    # else this job is tested) without ever inspecting the argv it built.
    apply_argv = captured.get("apply-remote")
    check(apply_argv is not None, "apply-remote must actually run for a change that lands")
    if apply_argv is not None:
        remote_cmd = apply_argv[-1]
        check("sudo systemctl stop lempi" in remote_cmd,
              f"stop must run with sudo -- a bare systemctl needs a password "
              f"non-interactively, got: {remote_cmd!r}")
        check("sudo systemctl start lempi" in remote_cmd,
              f"start must run with sudo too, got: {remote_cmd!r}")
    check(j["result"].get("landed") is True, f"the id_review must have landed, got {j['result']}")
    check(j["result"].get("fastforward") == 1, f"expected 1 fast-forward, got {j['result']}")
    logs = [e["text"] for e in j["events"] if e["kind"] == "log"]
    check(any("1 change(s) to push" in t for t in logs),
          f"a one-sentence summary must say what is about to be pushed, got {logs}")
    check(any("lempi02w now has these changes" in t for t in logs),
          f"a final confirmation must say the push actually landed, got {logs}")


def test_push_nothing_pending(tmp: str) -> None:
    print("no pending edits at all: a plain-English 'nothing to sync' line, not just raw JSON")
    library = os.path.join(tmp, "library3.db")
    build_library(library)
    sidecar = os.path.join(tmp, "library3.console.db")
    runner = jobmod.Runner(library, sidecar)
    empty = {"format_version": 1, "changes": []}
    runner._spawn = fake_spawn_success(empty).__get__(runner, jobmod.Runner)
    job_id = runner.submit("remote-push", "pi@lempi02w:/srv/library/library.db")
    j = wait_for(runner, job_id)
    check(j["state"] == "done", f"expected done, got {j}")
    stages = [e["stage"] for e in j["events"] if e["kind"] == "stage"]
    check(stages == ["export", "snapshot", "compare"],
          f"send/apply-remote must not run when there is nothing to push, got {stages}")
    logs = [e["text"] for e in j["events"] if e["kind"] == "log"]
    check(any("nothing to sync" in t for t in logs),
          f"a plain-English 'nothing to sync' line must appear, got {logs}")
    check(any(t == "the remote was not touched." for t in logs),
          f"must say plainly that lempi02w was never touched, got {logs}")


def test_snapshot_unreachable_fails_before_compare(tmp: str) -> None:
    print("snapshot failing (an unreachable remote) fails the job before compare/send/apply-remote ever run")
    library = os.path.join(tmp, "library2.db")
    build_library(library)
    sidecar = os.path.join(tmp, "library2.console.db")
    runner = jobmod.Runner(library, sidecar)
    runner._spawn = fake_spawn_snapshot_unreachable.__get__(runner, jobmod.Runner)
    job_id = runner.submit("remote-push", "pi@lempi02w:/srv/library/library.db")
    j = wait_for(runner, job_id)
    check(j["state"] == "failed", f"expected failed, got {j}")
    stages = [e["stage"] for e in j["events"] if e["kind"] == "stage"]
    check(stages == ["export", "snapshot"],
          f"compare/send/apply-remote must never run once snapshot failed, got {stages}")



def _apply_cmd_for(tmp: str, name: str, listener: str | None) -> str:
    """Run a landing push and hand back the shell command `apply-remote` built."""
    library = os.path.join(tmp, name + ".db")
    build_library(library)
    runner = jobmod.Runner(library, os.path.join(tmp, name + ".console.db"))
    if listener:
        runner.set_remote_listener(listener)
    captured = {}
    runner._spawn = fake_spawn_success(CHANGES_DOC, captured).__get__(runner, jobmod.Runner)
    job_id = runner.submit("remote-push", "pi@lempi02w:/srv/library/library.db")
    wait_for(runner, job_id)
    argv = captured.get("apply-remote")
    return argv[-1] if argv else ""


def test_a_split_peer_is_patched_through_its_listener_half(tmp: str) -> None:
    print()
    print("a split peer takes the patch through its LISTENER half, catalogue attached")
    # The bug this pins, measured on a real lempi02w 2026-09-11: the whole
    # patch went to the catalogue path, and `CREATE TABLE` does not follow
    # the attach chain -- so `id_reviews`/`boundary_reviews`/`artist_reviews`
    # were created inside `library.db`, shadowing the real ones in the
    # listener half (40 vs 99 rows, and 4 vs 3).
    cmd = _apply_cmd_for(tmp, "split", "pi@lempi02w:/var/lempi/listener.db")
    check("sqlite3 /var/lempi/listener.db" in cmd,
          f"the patch must be applied to the listener half, got: {cmd!r}")
    check("ATTACH DATABASE '/srv/library/library.db' AS lib;" in cmd,
          f"with the catalogue attached so catalogue statements still resolve, got: {cmd!r}")
    check("sqlite3 /srv/library/library.db" not in cmd,
          f"and never straight at the catalogue, which is what planted the shadows: {cmd!r}")


def test_an_unsplit_peer_keeps_the_single_file_command(tmp: str) -> None:
    print()
    print("an unsplit peer is unchanged -- one file, nothing attached")
    cmd = _apply_cmd_for(tmp, "whole", None)
    check("sqlite3 /srv/library/library.db < /tmp/lempi-sync-patch.sql" in cmd,
          f"one file, as before, got: {cmd!r}")
    check("ATTACH" not in cmd,
          f"an installation that never split must carry none of this, got: {cmd!r}")


def test_the_player_restarts_even_when_the_patch_fails(tmp: str) -> None:
    print()
    print("the remote's player is restarted whatever the patch did")
    # `stop && sqlite3 && start` leaves a node stopped for ever the moment
    # the patch fails -- and against `bose`, whose catalogue half is mounted
    # `ro`, it fails every single time. A sync that cannot land its changes
    # is a disappointment; one that silently turns the music off in another
    # room is a fault.
    cmd = _apply_cmd_for(tmp, "restart", "pi@lempi02w:/var/lempi/listener.db")
    # Asserted on the MEANING, not on the presence of "&&": the restart is
    # legitimately guarded by whether the node has the unit at all, and an
    # earlier version of this check forbade the two characters rather than
    # the dependency, so it failed the moment that guard was added.
    apply_at = cmd.index("sqlite3")
    start_at = cmd.index("sudo systemctl start lempi")
    check(";" in cmd[apply_at:start_at],
          f"the restart must be its own statement, not chained to the patch: {cmd!r}")
    check("|| rc=$?" in cmd,
          f"and the patch's own failure must be captured, not fatal: {cmd!r}")
    # A node with no service is not stopped or started at all.
    check("list-unit-files lempi.service" in cmd,
          f"the unit must be checked for before it is managed: {cmd!r}")
    check("sudo systemctl start lempi" in cmd, f"and it must still happen, got: {cmd!r}")
    check("exit $rc" in cmd,
          f"while still reporting the patch's own failure to the job, got: {cmd!r}")
    # Deliberately asserted on the command rather than by running it: a
    # shell here would be this machine's shell, and the thing under test is
    # the command sent to the *remote*. `bash -n` on the generated string
    # confirmed the syntax once, by hand; what must not silently come back
    # is the `&&`, and that is exactly what these three checks pin.



def fake_spawn_per_peer(changes_doc, failing_hosts=(), captured=None):
    """`fake_spawn_success`, but `apply-remote` fails for the named hosts.

    A fan-out's whole reason for existing is what it does when one node is
    broken and the others are not, so the fake has to be able to be broken
    for exactly one of them.
    """
    inner = fake_spawn_success(changes_doc)

    def _spawn(self, job_id, stage, argv):
        if stage == "apply-remote":
            host = argv[1]
            if captured is not None:
                captured.setdefault("apply-remote", []).append(argv)
            return (1, "readonly database") if host in failing_hosts else (0, "")
        if stage == "send":
            if captured is not None:
                captured.setdefault("send", []).append(argv)
            return 0, ""
        return inner(self, job_id, stage, argv)
    return _spawn


def test_push_all_visits_every_ticked_peer(tmp: str) -> None:
    print()
    print("a fan-out pushes to every ticked node, and only the ticked ones")
    library = os.path.join(tmp, "fanout.db")
    build_library(library)
    runner = jobmod.Runner(library, os.path.join(tmp, "fanout.console.db"))
    runner.upsert_peer("lempi02w", "pi@lempi02w:/srv/library/library.db",
                       "pi@lempi02w:/var/lempi/listener.db")
    runner.upsert_peer("bose", "pi@bose:/srv/library/library.db",
                       "pi@bose:/var/lempi/listener.db")
    runner.upsert_peer("teacherslounge", "sw@teacherslounge:/home/sw/lempi-data/library.db",
                       "sw@teacherslounge:/home/sw/lempi-data/listener.db")
    runner.set_peer_enabled("bose", False)      # deliberately left out
    names = [p["name"] for p in runner.peers_for_push()]
    # Reordered by the rename, not by a behaviour change: the appliance's
    # former name sorted after `teacherslounge`, `lempi02w` sorts before it. A
    # mechanical rename preserves a list's positions and cannot know the list
    # was ordered.
    check(names == ["lempi02w", "teacherslounge"],
          f"only the ticked peers are pushed to, alphabetically, got {names}")

    captured = {}
    runner._spawn = fake_spawn_per_peer(CHANGES_DOC, captured=captured).__get__(
        runner, jobmod.Runner)
    j = wait_for(runner, runner.submit("remote-push-all", json.dumps(names)))
    check(j["state"] == "done", f"expected done, got {j['state']}")
    result = j["result"]
    check(result["attempted"] == 2 and result["succeeded"] == 2,
          f"both ticked peers must be attempted and succeed, got {result}")
    # Same reordering as above: the comparison is against a SORTED list, so
    # the expectation has to be sorted under the new names too.
    check(sorted(result["peers"]) == ["lempi02w", "teacherslounge"],
          f"a result per peer, got {sorted(result['peers'])}")
    hosts = sorted(a[1] for a in captured.get("apply-remote", []))
    check(hosts == ["pi@lempi02w", "sw@teacherslounge"],
          f"and bose, unticked, must never have been contacted; got {hosts}")
    # Each peer's work must be kept apart, or two peers in one job overwrite
    # each other's changes.json and patch.
    sent = [a[1] for a in captured.get("send", [])]
    check(len(set(sent)) == len(sent), f"each peer needs its own patch file, got {sent}")


def test_one_failing_peer_does_not_stop_the_others(tmp: str) -> None:
    print()
    print("a node that fails is reported, and the rest are still pushed to")
    # The reason the per-peer result is kept separately at all: an
    # unreachable bose must not silently cancel a push to lempi02w that would
    # have worked, and "1 of 2" is a true answer a single exit code cannot give.
    library = os.path.join(tmp, "partial.db")
    build_library(library)
    runner = jobmod.Runner(library, os.path.join(tmp, "partial.console.db"))
    runner.upsert_peer("lempi02w", "pi@lempi02w:/srv/library/library.db",
                       "pi@lempi02w:/var/lempi/listener.db")
    runner.upsert_peer("bose", "pi@bose:/srv/library/library.db",
                       "pi@bose:/var/lempi/listener.db")
    captured = {}
    runner._spawn = fake_spawn_per_peer(
        CHANGES_DOC, failing_hosts=("pi@bose",), captured=captured).__get__(
            runner, jobmod.Runner)
    j = wait_for(runner, runner.submit("remote-push-all", json.dumps(["bose", "lempi02w"])))
    result = j["result"]
    check(j["state"] == "failed", f"a job with a failed peer must say so, got {j['state']}")
    check(result["failed"] == ["bose"], f"and name which one, got {result['failed']}")
    check(result["succeeded"] == 1 and result["attempted"] == 2,
          f"1 of 2, not all-or-nothing, got {result}")
    hosts = [a[1] for a in captured.get("apply-remote", [])]
    check("pi@lempi02w" in hosts,
          f"the working peer must still have been pushed to after the failure, got {hosts}")
    logs = [e["text"] for e in j["events"] if e["kind"] == "log"]
    check(any("1 of 2 peer(s) updated" in t for t in logs),
          f"the log must say how many of how many, got {logs[-3:]}")


def test_a_deleted_peer_is_skipped_not_fatal(tmp: str) -> None:
    print()
    print("a peer removed while the job was queued is skipped, not crashed on")
    library = os.path.join(tmp, "gone.db")
    build_library(library)
    runner = jobmod.Runner(library, os.path.join(tmp, "gone.console.db"))
    runner._spawn = fake_spawn_per_peer(CHANGES_DOC).__get__(runner, jobmod.Runner)
    j = wait_for(runner, runner.submit("remote-push-all", json.dumps(["ghost"])))
    check(j["state"] == "failed", f"expected failed, got {j['state']}")
    check(j["result"]["failed"] == ["ghost"], f"got {j['result']}")
    logs = [e["text"] for e in j["events"] if e["kind"] == "log"]
    check(any("no such peer" in t for t in logs), f"and say why, got {logs}")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_push_lands_a_change(tmp)
        test_push_nothing_pending(tmp)
        test_snapshot_unreachable_fails_before_compare(tmp)
        test_a_split_peer_is_patched_through_its_listener_half(tmp)
        test_an_unsplit_peer_keeps_the_single_file_command(tmp)
        test_the_player_restarts_even_when_the_patch_fails(tmp)
        test_push_all_visits_every_ticked_peer(tmp)
        test_one_failing_peer_does_not_stop_the_others(tmp)
        test_a_deleted_peer_is_skipped_not_fatal(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs remote_push: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
