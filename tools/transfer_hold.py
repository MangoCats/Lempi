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
import threading
import urllib.error
import urllib.request

from lempi_control import LEMPI_DEFAULT_PORT  # the guarded mirror [GDE-CLI-100]

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


# The shortest gap between renewals, in seconds, whatever life the player
# grants a hold: one a few seconds long is not renewed in a busy loop.
RENEW_FLOOR = 30


class _Renewal:
    """`[PI3-AIM-177]` A hold kept as long as its transfer, however long.

    The player caps a hold at an hour, and an export to lempi02w on
    2026-10-26 outlived it: at 19:44:59 the hold expired mid-copy and the
    player played on, its speaker back and its scrub waiting again. So every
    half of the hold's life a fresh hold is taken and the previous one
    released. Overlapping, the fresh one undoes nothing and pauses nothing --
    only a set's first hold pauses, and only its last release resumes -- so
    the music stays as the first hold left it. If this process dies, the last
    hold still ends by itself within one lifetime."""

    def __init__(self, post, seconds, hid, life):
        self.post, self.seconds = post, seconds
        self.hid, self.paused = hid, False
        self.every = max(RENEW_FLOOR, float(life or 0) / 2)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(self.every):
            got = self.post("/transfer/hold" + (f"?seconds={self.seconds}" if self.seconds else "")) or {}
            new = got.get("id")
            if new is None:
                continue  # not answered this time; the hold in force has half its life left
            # A hold taken after the last had ended -- an answer delayed past
            # its life, or a restarted player -- is a first one, and may pause.
            self.paused |= bool(got.get("paused"))
            old, self.hid = self.hid, new
            self.post(f"/transfer/release/{old}")

    def end(self):
        # Past a renewal's two posts at their 20 s each, so the last release
        # names the hold in force, not one a renewal is replacing.
        self.stop.set()
        self.thread.join(timeout=60)


@contextlib.contextmanager
def _held(post, who, say, seconds=None, renew=False):
    # Left out, the player's own default applies (`DEFAULT_SECS`,
    # player/src/web/transfer.rs); a caller that knows its bound passes it.
    got = post("/transfer/hold" + (f"?seconds={seconds}" if seconds else "")) or {}
    hid = got.get("id")
    paused = bool(got.get("paused"))
    quiet = bool(got.get("quiet"))
    if hid is None:
        say(f"  transfer: no player on {who} answered a hold; sending anyway [PI3-AIM-170]")
    elif paused:
        say(f"  transfer: {who} paused its playback for this transfer"
            + (" and released its speaker [PI3-AIM-175]" if quiet else " [PI3-AIM-170]"))
    elif quiet:
        say(f"  transfer: {who} was paused; released its speaker for this transfer [PI3-AIM-175]")
    renewal = None
    if renew and hid is not None:
        renewal = _Renewal(post, seconds, hid, got.get("seconds"))
        renewal.thread.start()
    try:
        yield got
    finally:
        if renewal is not None:
            renewal.end()
            hid = renewal.hid
            paused = paused or renewal.paused
        if hid is not None:
            done = post(f"/transfer/release/{hid}") or {}
            if done.get("resumed"):
                say(f"  transfer: {who} resumed its playback")
            elif not done.get("released") and paused:
                # Restarted, or the hold ran out: what it owed is a play.
                post("/command/play")
                say(f"  transfer: {who}'s player had forgotten the hold; asked it to play")


def held(address, port=LEMPI_DEFAULT_PORT, say=print, seconds=None, renew=False):
    """A hold asked of the player at `address`:`port`, over its web port.
    `renew` keeps it for as long as the block runs `[PI3-AIM-177]`."""
    return _held(lambda path: _http_post(f"http://{address}:{port}{path}"), address, say, seconds, renew)


def held_ssh(host, say=print, seconds=None, renew=False):
    """A hold asked over ssh of `host`'s player, on its loopback port.
    `renew` keeps it for as long as the block runs `[PI3-AIM-177]`."""
    return _held(lambda path: _ssh_post(host, path), host, say, seconds, renew)
