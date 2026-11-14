# passage_uid

The vectors that hold the two implementations of a passage's permanent identity equal `[IMPL-UID-032]`:
`tools/passage_uid.py` and `lempi_core::db::passage_uid`. Each vector is a birth key and the uid it
must give. `tools/test_passage_uid.py` writes `vectors.json` with `--write` and checks it; the test
in `player/core/src/db/uid.rs` checks it too.

Regenerate only for a change to the inputs, never to the encoding (`lempi-passage-uid/1`): a vector
whose uid changes is a different identity for every passage there is.
