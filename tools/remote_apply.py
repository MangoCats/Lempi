#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shell that applies a SQL patch to a node's database over ssh, stopping
the player around the write only where a service actually holds the file open,
and never requiring `sudo` on a node that has none `[SecurityReview C4]`.

`sync_preferences.py` and `resolve_mesh_conflict.py` used to run
`sudo systemctl stop lempi && sqlite3 … && sudo systemctl start lempi`
unconditionally. That is the *system-service* assumption -- right for the Pi
appliances, wrong for the general-purpose nodes:

- `smartboardpc` runs the player as a **user** unit (`systemctl --user`,
  `SmartPC/setup-smart.sh`), which needs no root; the old command failed there
  and implied a system-sudo requirement Lempi should not place on a
  general-purpose machine;
- `teacherslounge` runs **no** service (launched by hand); the old command's
  first `&&` failed on "Unit not found" and the patch never applied.

So the player shape is matched **on the node**, at run time, the same way
`jobs.py`'s `_remote_push` already tests for a system unit:

- a system `lempi.service`  -> `sudo systemctl` (the Pi appliances);
- a user `lempi.service`    -> `systemctl --user`, no sudo (e.g. `smartboardpc`);
- neither                   -> nothing is stopped (`teacherslounge`).

The patch's own exit status is preserved through `rc`, so a caller still hears
a failure, and the player is restarted whether or not the patch succeeded.
"""
import shlex


def apply_patch_cmd(db_path: str, patch_path: str) -> str:
    """The one-line shell to run as `ssh <host> "<this>"`. Paths are quoted, so
    a path with a space or shell metacharacter is data, not code."""
    q_db = shlex.quote(db_path)
    q_patch = shlex.quote(patch_path)
    return (
        "u=/run/user/$(id -u); "
        "sys=$(systemctl list-unit-files lempi.service 2>/dev/null "
        "| grep -c '^lempi.service' || true); "
        "usr=$(XDG_RUNTIME_DIR=$u systemctl --user list-unit-files lempi.service 2>/dev/null "
        "| grep -c '^lempi.service' || true); "
        'if [ "$sys" != 0 ]; then sudo systemctl stop lempi; '
        'elif [ "$usr" != 0 ]; then XDG_RUNTIME_DIR=$u systemctl --user stop lempi; fi; '
        f"rc=0; sqlite3 {q_db} < {q_patch} || rc=$?; "
        'if [ "$sys" != 0 ]; then sudo systemctl start lempi; '
        'elif [ "$usr" != 0 ]; then XDG_RUNTIME_DIR=$u systemctl --user start lempi; fi; '
        "exit $rc"
    )
