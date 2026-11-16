# natural_patch

A hub's catalogue before (`baseline.sql`) and after (`target.sql`), and a node that numbers the same
music differently (`node.sql`), on one schema. Each passage is given its uid as Stage A's window gave
them, a passage kept since keeping the uid it was born with. `tools/star_patch.py` builds `patch.json`
from the first two, naming a file by its `audio_md5` and a passage by its uid alone (IMPL023
`[IMPL-UID-080]`), and `tools/test_natural_patch.py` holds it there. The same patch applied to the
node must give `expected.json`, which `tools/test_natural_patch.py` checks with a reference applier and
`lempi-core`'s `mesh_sync.rs` checks with the real one. `python tools/test_natural_patch.py --write`
regenerates the two JSON files.

Until 2026-11-16 there were two patches here: `patch.json` by span and `patch_uid.json` by uid. Stage D
removed the span as a passage's name, and `patch.json` is now the patch by uid, the same as
`patch_uid.json` was to the byte.
