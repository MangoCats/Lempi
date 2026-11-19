# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which records of an album's edition are a decision, not a guess `[SPEC-FIN-040]`.

A recording is on many releases, and tools choose one by scoring
(`choose_release.py`) or by matching a folder's title (`align_file_releases.py`).
Two sources settle it instead, and those tools leave them standing:

- `cd:import` -- a CD add knew the disc in hand `[SPEC-CDI-098]`;
- `folder:chosen` -- a person picked the edition a folder of files is from, on
  the console's page for finishing one `[SPEC-FIN-030]`.

Named once here, because a second place spelling the list out is how one of
them would come to be forgotten.
"""
CD_IMPORT = "cd:import"
FOLDER_CHOSEN = "folder:chosen"
SETTLED = (CD_IMPORT, FOLDER_CHOSEN)
