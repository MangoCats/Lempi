# SPDX-License-Identifier: AGPL-3.0-or-later
"""A bulk transfer to a node holds its playback while it runs `[PI3-AIM-170]`.

On a Pi whose Wi-Fi shares its radio with the Bluetooth speaker, every byte
sent takes airtime the audio needs. The player pauses for a transfer and plays
again after; it decides whether its radio is shared, so these are safe for any
node, and one with no player answering is simply sent to.

    with held(address, port):        # the player's own web port
        ...send...
    with held_ssh("pi@node"):        # through ssh, to its loopback port
        ...scp...

The shell half is `transfer_hold` / `transfer_release` in
`build/lib-defaults.sh`; both speak to `POST /transfer/hold` and
`/transfer/release/<id>` in `player/src/web/transfer.rs`.
"""

import contextlib
import json
import shlex
import subprocess
import urllib.error
import urllib.request

from lempi_control import LEMPI_DEFAULT_PORT  # the guarded mirror [GDE-CLI-100]

SECONDS = 1800


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _http_post(url):
    """The answer's body as JSON, or None for no answer or not JSON. A 404 has
    a body too, and it is what a forgotten hold says."""
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return _json(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return _json(e.read().decode("utf-8", "replace"))
    except OSError:
        return None


def _ssh_post(host, path):
    cmd = f"curl -s -m 5 -X POST {shlex.quote('http://localhost:%d%s' % (LEMPI_DEFAULT_PORT, path))}"
    try:
        r = subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, cmd],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _json(r.stdout.strip())


@contextlib.contextmanager
def _held(post, who, say):
    got = post(f"/transfer/hold?seconds={SECONDS}") or {}
    hid = got.get("id")
    paused = bool(got.get("paused"))
    if hid is None:
        say(f"  transfer: no player on {who} answered a hold; sending anyway [PI3-AIM-170]")
    elif paused:
        say(f"  transfer: {who} paused its playback for this transfer [PI3-AIM-170]")
    try:
        yield got
    finally:
        if hid is not None:
            done = post(f"/transfer/release/{hid}") or {}
            if done.get("resumed"):
                say(f"  transfer: {who} resumed its playback")
            elif not done.get("released") and paused:
                # Restarted, or the hold ran out: what it owed is a play.
                post("/command/play")
                say(f"  transfer: {who}'s player had forgotten the hold; asked it to play")


def held(address, port=LEMPI_DEFAULT_PORT, say=print):
    """A hold asked of the player at `address`:`port`, over its web port."""
    return _held(lambda path: _http_post(f"http://{address}:{port}{path}"), address, say)


def held_ssh(host, say=print):
    """A hold asked over ssh of `host`'s player, on its loopback port."""
    return _held(lambda path: _ssh_post(host, path), host, say)
