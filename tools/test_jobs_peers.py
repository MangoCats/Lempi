#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `jobs.py`'s peer registry `[SPEC-MESH-090..094]`.

Plain sqlite against the real sidecar schema -- nothing here shells out, so
nothing is faked.

    python tools/test_jobs_peers.py
"""

import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs as jobmod  # noqa: E402

SCHEMA = "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);"

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def _runner(tmp: str) -> "jobmod.Runner":
    db = os.path.join(tmp, "lib.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    return jobmod.Runner(db, os.path.join(tmp, "lib.console.db"))


def test_add_list_and_remove(tmp: str) -> None:
    runner = _runner(tmp)
    check(runner.list_peers() == [], "a fresh sidecar has no peers")

    runner.upsert_peer("bose", "pi@bose:/var/lempi/listener.db")
    runner.upsert_peer("lempi02w", "pi@lempi02w:/srv/library/library.db")
    peers = runner.list_peers()
    check(len(peers) == 2, f"got {peers}")
    check(peers[0]["name"] == "bose", f"expected alphabetical order, got {peers}")
    check(peers[0]["enabled"] == 1, f"got {peers[0]}")

    runner.delete_peer("bose")
    peers = runner.list_peers()
    check([p["name"] for p in peers] == ["lempi02w"], f"got {peers}")


def test_upsert_overwrites_by_name(tmp: str) -> None:
    runner = _runner(tmp)
    runner.upsert_peer("bose", "pi@bose:/var/lempi/listener.db")
    runner.upsert_peer("bose", "pi@bose:/var/lempi/listener-new.db")
    peers = runner.list_peers()
    check(len(peers) == 1, f"a second upsert of the same name must not duplicate: {peers}")
    check(peers[0]["remote"] == "pi@bose:/var/lempi/listener-new.db", f"got {peers}")


def test_activate_peer_sets_remote_config(tmp: str) -> None:
    runner = _runner(tmp)
    check(runner.get_remote() is None, "nothing configured yet")
    runner.upsert_peer("bose", "pi@bose:/var/lempi/listener.db")

    remote = runner.activate_peer("bose")
    check(remote == "pi@bose:/var/lempi/listener.db", f"got {remote}")
    check(runner.get_remote() == "pi@bose:/var/lempi/listener.db",
          "[SPEC-MESH-092]: activating a peer must set remote_config, so the "
          "three existing sync jobs act on it unchanged")

    check(runner.activate_peer("no-such-peer") is None, "an unknown name must not silently succeed")
    check(runner.get_remote() == "pi@bose:/var/lempi/listener.db",
          "a failed activation must not clear the previously active remote")


def test_remote_listener_defaults_to_none(tmp: str) -> None:
    """`[IMPL002 §7.4]`: a peer that has never split has no
    `remote_listener` at all -- `None` means "same file as `remote`",
    not an empty string or a copy of `remote` itself.
    """
    runner = _runner(tmp)
    runner.upsert_peer("bose", "pi@bose:/var/lempi/listener.db")
    peers = runner.list_peers()
    check(peers[0]["remote_listener"] is None,
          f"an unsplit peer must have no remote_listener recorded, got {peers[0]}")

    runner.activate_peer("bose")
    check(runner.get_remote_listener() is None,
          "activating an unsplit peer must not invent a remote_listener")


def test_remote_listener_is_carried_through_upsert_and_activation(tmp: str) -> None:
    """A split peer -- lempi02w, once it has actually split -- carries a
    second path all the way from `upsert_peer` through `activate_peer` to
    `get_remote_listener()`, independent of `remote`.
    """
    runner = _runner(tmp)
    runner.upsert_peer("lempi02w", "pi@lempi02w:/srv/library/library.db",
                        remote_listener="pi@lempi02w:/var/lempi/listener.db")
    peers = runner.list_peers()
    check(peers[0]["remote"] == "pi@lempi02w:/srv/library/library.db", f"got {peers[0]}")
    check(peers[0]["remote_listener"] == "pi@lempi02w:/var/lempi/listener.db", f"got {peers[0]}")

    runner.activate_peer("lempi02w")
    check(runner.get_remote() == "pi@lempi02w:/srv/library/library.db",
          "activation must still set the catalog path in remote_config")
    check(runner.get_remote_listener() == "pi@lempi02w:/var/lempi/listener.db",
          "activation must carry the listener path over too, not just the catalog one")


def test_activating_an_unsplit_peer_after_a_split_one_clears_remote_listener(tmp: str) -> None:
    """Switching the active peer must not leave a stale `remote_listener`
    pointing at the *previous* peer's listener file once the newly active
    one doesn't have one.
    """
    runner = _runner(tmp)
    runner.upsert_peer("lempi02w", "pi@lempi02w:/srv/library/library.db",
                        remote_listener="pi@lempi02w:/var/lempi/listener.db")
    runner.upsert_peer("bose", "pi@bose:/var/lempi/listener.db")
    runner.activate_peer("lempi02w")
    check(runner.get_remote_listener() == "pi@lempi02w:/var/lempi/listener.db", "sanity")

    runner.activate_peer("bose")
    check(runner.get_remote_listener() is None,
          "switching to an unsplit peer must clear the previous remote_listener, "
          "not leave lempi02w's listener.db path active while bose is the target")


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_add_list_and_remove(tmp)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_upsert_overwrites_by_name(tmp)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_activate_peer_sets_remote_config(tmp)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_remote_listener_defaults_to_none(tmp)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_remote_listener_is_carried_through_upsert_and_activation(tmp)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_activating_an_unsplit_peer_after_a_split_one_clears_remote_listener(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("jobs peers: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
