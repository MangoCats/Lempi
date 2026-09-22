#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where credentials come from, in one place.

Environment first, then a gitignored file. The environment wins so a run can
override without editing anything, and the file exists so an unattended run --
which is what these all are -- does not need one exported by hand each time.

Nothing here ever prints a key. A credential that reaches a log has reached
everywhere the log goes, and the two-hour runs these serve are the kind whose
output gets pasted into a bug report.
"""

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets"


def read(name: str, env: str) -> str | None:
    """A credential by name, or `None` if it has not been provided."""
    from_env = os.environ.get(env)
    if from_env and from_env.strip():
        return from_env.strip()
    path = SECRETS / f"{name}.key"
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


def acoustid_key(required: bool = True) -> str | None:
    """The AcoustID **application** key `[SPEC-SA-035]`.

    The application key identifies the client on lookups. It is not the user
    key, which is only needed to submit fingerprints back -- something Vipunen
    has no reason to do, so that one is never asked for.
    """
    key = read("acoustid", "ACOUSTID_KEY")
    if key is None and required:
        print(
            "No AcoustID key. Put the application key in secrets/acoustid.key\n"
            "(gitignored) or set ACOUSTID_KEY. Register one, free, at\n"
            "  https://acoustid.org/new-application",
            file=sys.stderr,
        )
        sys.exit(2)
    return key


if __name__ == "__main__":
    # Reports presence, never the value.
    for name, env in (("acoustid", "ACOUSTID_KEY"),):
        got = read(name, env)
        where = "environment" if os.environ.get(env) else f"secrets/{name}.key"
        print(f"{name:10} {'present (' + str(len(got)) + ' chars) via ' + where if got else 'MISSING'}")
