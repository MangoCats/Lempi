#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reaching the co-resident player's own pages and process from inside
Vipunen's console `[SPEC-SUI-140]`, `[SPEC-SUI-135]`.

Split out of `console.py`, where this cluster had grown directly against that
file's own stated safety claim: "the views, and nothing that writes... there
is no POST route in this file." `unflag_everywhere()` below is a write --
two, in fact, one local and one to lempipi -- and `console.py`'s own
`do_POST` has carried a route to it since `[REQ-VIS-265]`. The claim that
still holds, and the one this split makes structural again rather than
aspirational, is narrower: **this module never opens the library for
writing.** Every function here either asks the operating system a question
(is this port listening, is there a binary on `PATH`, open a terminal) or
signals the *already-running* Lempi process over HTTP/SSH to write its own
listener state -- the identical `POST /history/flag/:kind/:id` route the
player's own play-history page already calls (`player/src/web.rs`'s
`set_flag`). Nothing here executes a `sqlite3` write, or even opens one.

**Deliberately no `import console`.** `console.py` is run directly
(`python tools/console.py ...`), which loads it as `__main__` -- a module
that is *not* registered in `sys.modules` under the name `console`. A
top-level `import console` from here would therefore not reuse that running
process's module; it would load a second, independent copy of
`console.py`, with its own fresh `STATE` dict that main()'s argument
parsing never touches. Every value this module would otherwise have read
from `console.STATE` -- the open library's path, Vipunen's own build info,
which subjects to clear and what the remote already thinks -- is instead a
parameter its caller passes in. `console.py` still owns that state and the
database reads that produce it; this module only ever receives the answer.
"""

import http.client
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time

# Where the co-resident player listens. A different number from Vipunen's own
# because they are different services on the same machine, and because
# `[SPEC-SUI-170]` may start the player: colliding would make each look like
# the other's failure.
#
# **This is a mirror, and it is guarded** `[GDE-CLI-100]`. `default_port!` in
# `player/src/cli.rs` is the definition; Python cannot read a Rust macro, so
# the crate's own test checks that this line agrees with it. `LEMPI_PORT`
# outranks it here exactly as it does in the player `[GDE-CLI-090]`.
LEMPI_DEFAULT_PORT = 5720


def _port_from_environment() -> int:
    """The port, resolved the way the player resolves it `[GDE-CLI-090]`.

    **Three mirrors, and two of them used to disagree.** The player honours
    the retired `LEMPI_PORT` as well as `LEMPI_PORT`, and so does
    `build/lib-defaults.sh`; this line read only `LEMPI_PORT`. So with
    `LEMPI_PORT=6000` exported the player bound 6000, the shell scripts
    polled 6000, and every Python tool talked to 5720 -- the
    `[GOV-SRC-040]` shape exactly, three answers to one question with no
    sign that they differed.

    **A bad value does not take the tools down.** This used to be a bare
    `int(...)` at import, so a non-numeric `LEMPI_PORT` raised `ValueError`
    while merely importing this module, killing every tool that imports it
    -- including ones that never talk to the player. The Rust side refuses
    cleanly and names the variable; the least this can do is say so and
    carry on with the default.
    """
    for name in ("LEMPI_PORT", "LEMPI_PORT"):
        raw = os.environ.get(name)
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            print(
                "lempi_control: $%s is %r, which is not a port; using %d"
                % (name, raw, LEMPI_DEFAULT_PORT),
                file=sys.stderr,
            )
            return LEMPI_DEFAULT_PORT
    return LEMPI_DEFAULT_PORT


LEMPI_PORT = _port_from_environment()


def _lempi_reachable(port: int, timeout: float = 0.5) -> bool:
    """A socket question, not a route question `[SPEC-SUI-170]`."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _lempi_has_vipunen_support(port: int, timeout: float = 2.0) -> bool:
    """Whether *this* running Lempi was built with `--features vipunen-support`
    `[SPEC-SUI-213]` -- the one thing `_lempi_reachable`'s socket question
    cannot tell apart: an appliance-equivalent build and a desktop build
    listen identically, and only one of them has anywhere for a handoff to
    land. `/review.js` is a static asset compiled in only by that feature
    `[SPEC-SUI-190]`, so its presence is a build-capability question, not a
    library one -- nothing about *this* library, or any library, is read
    here, which is the boundary `[SPEC-SUI-025]` actually protects. A
    real-world dead handoff (a Lempi running, answering, and 404ing every
    review link) is what this exists to catch before a person clicks it.
    """
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", "/review.js")
            r = conn.getresponse()
            r.read()  # drain -- the body is never inspected, only the status
            return r.status < 400
        finally:
            conn.close()
    except OSError:
        return False


def _lempi_set_flag(port: int, kind: str, subject_id: str, flagged: bool,
                     timeout: float = 2.0) -> bool:
    """Vipunen signals; Lempi writes `[SPEC-SC-020]` -- the identical `POST
    /history/flag/:kind/:id` route the play-history page's own checkbox
    already calls (`player/src/web.rs`'s `set_flag`), never a direct
    `listener_flags` write from this process. `204` is the only success;
    anything else (a malformed kind, a closed connection, an older Lempi
    with no such route) is `False`, for the caller to report.
    """
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("POST", f"/history/flag/{kind}/{subject_id}"
                                  f"?flagged={'true' if flagged else 'false'}")
            r = conn.getresponse()
            r.read()
            return r.status == 204
        finally:
            conn.close()
    except OSError:
        return False


def peer_host(remote: str) -> str:
    """The bare hostname out of `user@host:/path/to/library.db`.

    Split on the *first* colon, which is what `scp`/`ssh` themselves do --
    the path after it may contain colons on Windows, and the user@ part
    never does.
    """
    host = (remote or "").partition(":")[0]
    return host.partition("@")[2] or host


def peer_reachable(remote: str, timeout: float = 2.0) -> bool:
    """Whether this peer answers on ssh, right now `[SPEC-MESH-090]`.

    A TCP connect to port 22, not a real `ssh` handshake: the question the
    page is asking is "is it worth ticking this box", and an honest cheap
    answer given in two seconds is more useful than an authoritative one
    that costs a key exchange per peer per refresh. It will say reachable
    for a host that is up but would refuse the key -- the push itself
    reports that, loudly, and that is the right place for it.

    Never raises: an unresolvable name, a down host and a firewalled port
    are all simply "not reachable" to a person looking at a list.
    """
    return _reachable_on(peer_host(remote), 22, timeout)


def _reachable_on(host: str, port: int, timeout: float = 2.0) -> bool:
    """The socket question itself, with the port a parameter.

    Split out from `peer_reachable` so it can be tested against a listener
    this machine actually owns. A test that needed `bose` to be plugged in
    would fail for reasons having nothing to do with this code.
    """
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _lempi_post(path: str, port: int = LEMPI_PORT, timeout: float = 3.0) -> bool:
    """POST one of the player's own control routes. `True` if it accepted.

    The player answers `204` for `/command/:name` and `202` for the two
    asynchronous ones (`/library/reload` asks; the engine performs it on its
    next pass), so anything below 400 is success here.
    """
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("POST", path)
            r = conn.getresponse()
            r.read()
            return r.status < 400
        finally:
            conn.close()
    except OSError:
        return False


def pause_lempi(port: int = LEMPI_PORT) -> bool:
    """Stop the audio before the library underneath it is rewritten.

    Deliberately `pause`, not a stop or a process kill. `/power/restart` and
    `/power/off` both shell out to `sudo systemctl`, which exists on the
    appliances and not on the desktop this console actually runs on, and the
    player has no shutdown route at all -- so a pause is the only interruption
    available to every installation, and it is also the *right* one: it is
    reversible, it keeps the queue, and it costs no audio-device churn.
    """
    return _lempi_post("/command/pause", port)


def play_lempi(port: int = LEMPI_PORT) -> bool:
    """Put playback back, after `pause_lempi` and the write it guarded."""
    return _lempi_post("/command/play", port)


def reload_lempi_library(port: int = LEMPI_PORT) -> bool:
    """Make a running player adopt a library that changed under it.

    Without this the edit is on disk and inaudible: the Director holds the
    spans it was built with, so a passage whose boundaries just moved keeps
    playing the old ones until something rebuilds it. The rebuild is
    backgrounded and gated on queue depth by the player itself, so this
    returns as soon as the request is *accepted*, not when it completes.
    """
    return _lempi_post("/library/reload", port)


def _remote_set_flag(remote: str, port: int, kind: str, subject_id: str, flagged: bool,
                      timeout: float = 8.0) -> bool:
    """The identical signal `_lempi_set_flag` sends locally, sent instead to
    lempipi's own already-running Lempi over one `ssh ... curl` round trip.
    Never a database write from this process, and never a service
    interruption: unlike `[SPEC-DF-111]`'s patch-apply recipe, nothing here
    writes to lempipi's database directly, so there is nothing for its own
    running player to race against and no reason to stop it.
    """
    host, _, _ = remote.partition(":")
    url = (f"http://localhost:{port}/history/flag/{kind}/{subject_id}"
           f"?flagged={'true' if flagged else 'false'}")
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host,
             f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 -X POST {shlex.quote(url)}"],
            capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "204"


def unflag_everywhere(subjects: list, remote: str | None, status: dict | None,
                       port: int = LEMPI_PORT) -> dict:
    """Clear every plausible flag on this passage, locally and on the
    remote, in one action `[REQ-VIS-265]`. Both writes go through Lempi's
    own `set_flag` -- co-resident over plain HTTP, lempipi's over one `ssh
    ... curl` round trip -- never a `listener_flags` write from this
    process: listener state is Lempi's to write, not Vipunen's.

    Takes what `console.py`'s own read-only queries already resolved,
    rather than a connection and a passage id: `subjects` is
    `passage_flag_subjects(conn, pid)` -- a `("passage", pid)` entry and
    one `("recording", mbid)` entry per linked recording. `status` is
    `flag_sync_status(conn, pid)`, or `None` when no remote is configured at
    all; when given, its `remote_pid`/`remote_mbids` are what let a
    `("passage", pid)` subject -- meaningful only locally, since `pid` is
    not portable `[SPEC-DF-103]` -- be translated to the remote's OWN local
    passage_id before being sent there, and let a `("recording", mbid)`
    subject be unioned with whatever the remote *itself* currently links.

    That union matters: found live the first time this ran for real, an id
    correction accepted locally but not yet pushed left the remote still
    linked to the *old* recording, so resolving subjects only from this
    library's own current link cleared nothing where the flag actually was,
    `_remote_set_flag` reporting success regardless (a DELETE matching zero
    rows is not an error). The same reasoning `[SPEC-DF-112]`'s
    `clear_flags_for()` already applies for an `id_review`'s own
    target+baseline, generalized: ask what the far side currently thinks
    too, clear the union. A remote passage that does not exist there at all
    (`status["remote_pid"] is None`) has its passage-keyed subject skipped,
    not silently dropped -- it is simply not in the union to begin with.
    """
    local_ok = [_lempi_set_flag(port, kind, sid, False) for kind, sid in subjects]
    result = {"local": {"ok": all(local_ok), "cleared": sum(local_ok), "of": len(local_ok)}}

    if not remote:
        result["remote"] = {"configured": False}
        return result
    if status is None or not status["reachable"]:
        result["remote"] = {"configured": True, "reachable": False}
        return result

    remote_subjects = {("recording", mbid) for mbid in status["remote_mbids"]}
    remote_subjects |= {(k, sid) for k, sid in subjects if k == "recording"}
    if status["remote_pid"] is not None:
        remote_subjects.add(("passage", str(status["remote_pid"])))
    remote_ok = [_remote_set_flag(remote, port, kind, sid, False)
                 for kind, sid in sorted(remote_subjects)]
    result["remote"] = {"configured": True, "reachable": True, "ok": all(remote_ok),
                         "cleared": sum(remote_ok), "of": len(remote_ok)}
    return result


def unflag_subject_everywhere(kind: str, subject_id: str, remote: str | None,
                               port: int = LEMPI_PORT) -> dict:
    """Clear exactly this `(kind, subject_id)` flag, locally and on the
    remote, with no passage to resolve through and no union to compute
    `[REQ-VIS-265]` -- the direct sibling of `unflag_everywhere` above, for
    a caller that already has the flag's own primary key in hand (the
    Flags list's own rows, `console.py::flags()`) rather than a passage id
    to resolve it from.

    **Correct outright for a `recording`-kind flag.** An mbid is portable
    by construction -- the identical subject clears the identical row on
    both sides, with nothing to translate. This is the common case: a
    listener flags a *recording*, not a specific passage of it, most of
    the time `[REQ-VIS-265]`.

    **Best-effort only for a `passage`-kind flag.** `subject_id` is a
    *local* passage number, portable to the remote only by coincidence,
    never guaranteed and never checked here -- `unflag_everywhere` exists
    precisely because that translation needs the recording(s) a passage
    currently links to, which this function has no passage to look up at
    all. Call this for a `passage`-kind subject only once
    `unflag_everywhere` is confirmed unusable (nothing local resolves it
    any more), not as a routine substitute for it.
    """
    local_ok = _lempi_set_flag(port, kind, subject_id, False)
    result = {"local": {"ok": local_ok}}
    if not remote:
        result["remote"] = {"configured": False}
        return result
    remote_ok = _remote_set_flag(remote, port, kind, subject_id, False)
    result["remote"] = {"configured": True, "ok": remote_ok}
    return result


def _lempi_binary() -> str | None:
    """Where the co-resident player's binary is, if one can be found at all.

    Checked against this repository's own build layout first -- the case
    while Vipunen and Lempi are developed side by side -- then `PATH`, for an
    installed player. Never guessed beyond that: a wrong binary started
    against the wrong database is worse than admitting there is none.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in (
        os.path.join(here, "..", "player", "target", "release", "lempi.exe"),
        os.path.join(here, "..", "player", "target", "release", "lempi"),
    ):
        if os.path.isfile(rel):
            return os.path.abspath(rel)
    return shutil.which("lempi")


def _lempi_build(port: int, timeout: float = 2.0) -> dict | None:
    """This co-resident Lempi's own build identity `[SPEC-SUI-227]` -- `GET
    /build`, the machine-readable sibling of the Settings page's "Server
    build" row `player/src/web.rs`'s `build_identity` serves. `None` on
    anything that stops this from being read -- an older Lempi with no such
    route, a non-JSON body, a closed connection -- and the caller treats an
    unknown build the same honest way `console.build_info()` already treats
    a missing git checkout: silently skipping a check it cannot make, never
    guessing `[SPEC-DF-095]`.
    """
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", "/build")
            r = conn.getresponse()
            body = r.read()
            if r.status >= 400:
                return None
            return json.loads(body)
        finally:
            conn.close()
    except (OSError, ValueError):
        return None


def _lempi_staleness(port: int, vipunen_build: dict | None) -> str | None:
    """Whether the co-resident Lempi was built from a different commit than
    *this* Vipunen is running from `[SPEC-SUI-227]` -- found live 2026-08-31: a
    Lempi that was the only one running, and was vipunen-support-capable, was
    still hours behind the checkout, so a boundary edit saved through it
    silently failed to reappear on reopening the editor -- the merge-with-
    draft logic that fixed did not exist in that build. Neither
    `[SPEC-SUI-170]`'s reuse check nor `[SPEC-SUI-213]`'s capability probe
    would have caught that; both ask "is something there," never "is it the
    same something this checkout would build."

    `vipunen_build` is `console.STATE["build"]` -- this Vipunen's own
    `build_info()` result, computed once at startup. `None` -- no mismatch,
    or nothing to compare -- whenever either side's identity is unknown: no
    git checkout, git missing from `PATH`, or a Lempi too old to serve
    `/build` at all. A silent skip, not a guess, matching `build_info()`'s
    own posture toward an absent checkout.
    """
    vipunen = vipunen_build or {}
    vipunen_commit = vipunen.get("commit") if vipunen.get("available") else None
    if not vipunen_commit:
        return None
    lempi = _lempi_build(port)
    lempi_git = (lempi or {}).get("git")
    if not lempi_git or lempi_git == "unknown":
        return None
    lempi_hash = lempi_git.removesuffix("+dirty")
    if vipunen_commit.startswith(lempi_hash):
        return None
    return (f"Vipunen is running from commit {vipunen.get('commit_short') or vipunen_commit[:12]}, "
            f"but the co-resident Lempi on this port was built from a different commit "
            f"({lempi_git}, {lempi.get('commit_date', 'date unknown')}) -- "
            "rebuild whichever one is behind (see HOWTO.md §2) and restart it")


def _lempi_ready(port: int, started: bool, vipunen_build: dict | None = None) -> dict:
    """A reachable Lempi is not necessarily a *useful* one for this handoff
    `[SPEC-SUI-213]` -- found live several times over on 2026-08-30: a plain
    appliance-equivalent build answers every socket check `ensure_lempi()`
    could make and still 404s every review/edit link, which read in a
    browser as a dead page with no explanation. Named here instead, the same
    "say which capability is unavailable, and why" `[SPEC-SUI-170]` already
    commits to for a missing binary or a start that timed out.
    """
    if not _lempi_has_vipunen_support(port):
        binary = _lempi_binary()
        return {"ok": False, "port": port, "started": started,
                "error": ("Vipunen just started a local Lempi, but " if started else
                          "a Lempi is already running on this port, but ")
                         + (f"{binary} " if binary else "the binary ")
                         + "was built without --features vipunen-support, so the review page "
                           "and waveform editor don't exist in it (see HOWTO.md §2). "
                           "Rebuild player/ with that flag, then " +
                           ("restart it" if started else "stop this one and reopen this page")}
    stale = _lempi_staleness(port, vipunen_build)
    if stale:
        return {"ok": False, "port": port, "started": started, "error": stale}
    return {"ok": True, "port": port, "started": started}


def ensure_lempi(port: int = LEMPI_PORT, db_path: str | None = None,
                  vipunen_build: dict | None = None,
                  listener_path: str | None = None,
                  library_path: str | None = None) -> dict:
    """Start the co-resident player if one is not already there `[SPEC-SUI-170]`.

    `db_path` is `console.STATE["path"]` -- the library Vipunen has open --
    and `vipunen_build` is `console.STATE["build"]`, threaded through to
    `_lempi_ready` for the staleness check.

    1. **Already running?** Use it. Do not start a second -- two players on
       one library contend for the audio device and both write the single
       resume row `[SPEC-SC-098]`.
    2. **Not running?** Start it, on **Vipunen's own database path**. This is
       what makes `[SPEC-SUI-150]`'s passage-id handoff sound: the player
       reads the exact file the id came from because Vipunen told it to, not
       because a configuration happened to agree.
    3. **Start failed, or started without the routes this handoff needs?**
       Say which capability is unavailable, and why. Silent degradation is
       its own failure `[SPEC-DF-095]`.
    """
    if _lempi_reachable(port):
        return _lempi_ready(port, started=False, vipunen_build=vipunen_build)

    if not db_path:
        return {"ok": False, "port": port, "error": "no library open"}

    binary = _lempi_binary()
    if not binary:
        return {"ok": False, "port": port,
                "error": "no local Lempi binary found -- build player/ first "
                         "(see build/README.md)"}

    # **Both halves, or none of it works** `[PI-OWE-010]`. The player takes the
    # listener database from `--listener` and the catalogue from
    # `--library`; this passed `db_path` alone, and `db_path` is Vipunen's own
    # path, which is the CATALOGUE half. The player then ran with the catalogue
    # as its listener database and bootstrapped listener tables into it --
    # eleven of them, observed live 2026-09-11, which turned `data/library.db`
    # into something `lempi_db.shape()` reads as WHOLE and every unqualified
    # listener read into a hit on an empty shadow (37,763 plays reading as 0).
    #
    # The two paths come from `console.STATE`, which took them from its own
    # connection's `PRAGMA database_list` -- what Vipunen genuinely has open, not
    # a filename convention and not a second content sniff, which is the one
    # thing the contamination above would itself have corrupted. Equal on an
    # unsplit installation, where `--library` is then correctly omitted.
    listener = listener_path or db_path
    library = library_path or db_path
    # Named, not positional `[GDE-CLI-020]`. The positional form is still
    # read and warns, but Vipunen is in this repository and moves with it.
    argv = [binary, "--listener", listener]
    if library and library != listener:
        argv += ["--library", library]
    argv += ["--port", str(port)]
    try:
        subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        return {"ok": False, "port": port, "error": f"could not start lempi: {e}"}

    # Polled, not a fixed wait: the Program Director's own startup time scales
    # with library size, the same reason `deploy-player.sh` polls rather than
    # sleeping a fixed span before declaring a new build alive.
    deadline = time.time() + 20
    while time.time() < deadline:
        if _lempi_reachable(port):
            return _lempi_ready(port, started=True, vipunen_build=vipunen_build)
        time.sleep(0.25)
    return {"ok": False, "port": port,
            "error": "lempi did not answer within 20s of starting"}


def open_terminal(directory: str) -> dict:
    """Open an ordinary terminal on THIS machine, in `directory` `[IMPL007
    Stage 5]`.

    Not SSH, not rsync, no remote host known to this process at all -- opening
    a local terminal is the same act as the operator opening one themselves,
    just one click closer to the deploy commands the page already prints as
    plain, selectable text. The commands are never typed for them and never
    run by this process; the window is a place to paste them, or type them by
    hand, and see exactly what runs before it does.
    """
    if not os.path.isdir(directory):
        return {"ok": False, "error": f"no such directory: {directory}"}
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/K", f'cd /d "{directory}"'],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Terminal", directory])
        else:
            # Best effort across desktops -- there is no single "the terminal"
            # on Linux the way there is on the other two platforms.
            for candidate in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if shutil.which(candidate):
                    subprocess.Popen([candidate], cwd=directory)
                    break
            else:
                return {"ok": False,
                        "error": "no terminal emulator found on PATH "
                                 "(tried x-terminal-emulator, gnome-terminal, konsole, xterm)"}
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": f"could not open a terminal: {e}"}
