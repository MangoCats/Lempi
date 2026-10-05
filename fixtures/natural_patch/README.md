# natural_patch

A hub's catalogue before (`baseline.sql`) and after (`target.sql`), and a node that numbers the same
music differently (`node.sql`), on one schema. `tools/star_patch.py` builds `patch.json` from the first
two, and `tools/test_natural_patch.py` holds it there; the same patch applied to the node must give
`expected.json`, which `tools/test_natural_patch.py` checks with a reference applier and
`lempi-core`'s `mesh_sync.rs` checks with the real one. `python tools/test_natural_patch.py --write`
regenerates the two JSON files.
