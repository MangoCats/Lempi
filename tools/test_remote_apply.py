#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tools/remote_apply.py `[SecurityReview C4]`.

The generated shell is run for real against fake `systemctl`/`sudo`/`sqlite3`
on PATH, simulating the three node shapes, so what is pinned is behaviour --
which player was stopped, and whether sudo was used -- not the string's spelling.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import remote_apply  # noqa: E402

# A POSIX shell to run the generated command under. `sh` is preferred: on
# Windows `bash` on PATH is often WSL's, which cannot see this Git checkout,
# while `sh` resolves to Git's own. None means no POSIX shell -- skip, loudly.
SH = shutil.which("sh") or shutil.which("bash")

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


FAKE_SYSTEMCTL = """#!/bin/sh
# --user is recognised by the first argument. list-unit-files answers per the
# SYS/USR env the case sets; stop/start just record the call.
if [ "$1" = --user ]; then
    scope=user; shift
else
    scope=system
fi
case "$1" in
  list-unit-files)
    if [ "$scope" = user ]; then [ "${USR:-0}" = 1 ] && echo "lempi.service enabled"; else
       [ "${SYS:-0}" = 1 ] && echo "lempi.service enabled"; fi ;;
  stop|start) echo "systemctl $scope $1 $2" >> "$CALLS" ;;
esac
exit 0
"""
FAKE_SUDO = """#!/bin/sh
echo "sudo $*" >> "$CALLS"
exec "$@"
"""
# sqlite3 records, and fails if FAIL_SQL is set, so rc is seen to survive.
FAKE_SQLITE3 = """#!/bin/sh
echo "sqlite3 $*" >> "$CALLS"
[ "${FAIL_SQL:-0}" = 1 ] && exit 5
exit 0
"""


def run(shape_env):
    tmp = tempfile.mkdtemp()
    bind = os.path.join(tmp, "bin")
    os.makedirs(bind)
    for name, body in (("systemctl", FAKE_SYSTEMCTL), ("sudo", FAKE_SUDO), ("sqlite3", FAKE_SQLITE3)):
        p = os.path.join(bind, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(p, 0o755)
    calls = os.path.join(tmp, "calls")
    open(calls, "w").close()
    # A real patch file, so the `< patch` redirect opens as it does in
    # production (the file is scp'd there first). Forward slashes: Git's sh
    # opens `C:/…`, not `C:\…`.
    patch = os.path.join(tmp, "patch.sql")
    with open(patch, "w", encoding="utf-8") as fh:
        fh.write("SELECT 1;\n")
    env = dict(os.environ)
    env["PATH"] = bind + os.pathsep + env["PATH"]
    env["CALLS"] = calls
    env.update(shape_env)
    cmd = remote_apply.apply_patch_cmd("/var/lempi/listener.db", patch.replace("\\", "/"))
    rc = subprocess.run([SH, "-c", cmd], env=env).returncode
    with open(calls, encoding="utf-8") as fh:
        recorded = fh.read()
    return rc, recorded


def main() -> int:
    if not SH:
        print("remote_apply: SKIPPED -- no POSIX shell (sh/bash) on PATH to run the "
              "generated command. That is a skip, not a pass.", file=sys.stderr)
        return 0
    # A system service (a Pi): sudo systemctl stop and start, around the patch.
    rc, calls = run({"SYS": "1", "USR": "0"})
    check("sudo systemctl stop lempi" in calls, f"system node: sudo stop used: {calls!r}")
    check("sudo systemctl start lempi" in calls, "system node: sudo start used")
    check("sqlite3 /var/lempi/listener.db" in calls, "system node: the patch is applied")
    check(rc == 0, f"system node: success rc, got {rc}")

    # A user service (smartboardpc): systemctl --user, and NEVER sudo.
    rc, calls = run({"SYS": "0", "USR": "1"})
    check("systemctl user stop lempi" in calls, f"user node: --user stop used: {calls!r}")
    check("systemctl user start lempi" in calls, "user node: --user start used")
    check("sudo " not in calls, "user node: sudo is NEVER used")
    check("sqlite3 /var/lempi/listener.db" in calls, "user node: the patch is applied")
    check(rc == 0, f"user node: success rc, got {rc}")

    # No service (teacherslounge): nothing stopped, no sudo, patch still applied.
    rc, calls = run({"SYS": "0", "USR": "0"})
    check("stop lempi" not in calls, f"no-service node: nothing is stopped: {calls!r}")
    check("sudo " not in calls, "no-service node: sudo is NEVER used")
    check("sqlite3 /var/lempi/listener.db" in calls, "no-service node: the patch is still applied")
    check(rc == 0, f"no-service node: success rc, got {rc}")

    # The patch's failure survives to the caller, and the player still restarts.
    rc, calls = run({"SYS": "1", "USR": "0", "FAIL_SQL": "1"})
    check(rc == 5, f"a failing patch's rc survives, got {rc}")
    check("sudo systemctl start lempi" in calls, "and the player is restarted even so")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("remote_apply: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
