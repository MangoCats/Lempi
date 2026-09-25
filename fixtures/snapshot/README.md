# Snapshot contract fixture

`consumers.json` names, for each reader of the player's `Snapshot` (the JSON
pushed on `/ws`), the fields that reader reads `[GDE-HST-060]`. A path is a
field (`title`), a field of an object (`skip.fade_ms`), or a field of every
element of an array (`queue[].qid`).

**Compatibility is presence, not a number** — the rule `[SPEC-PL-065]` set for
the payload. A field sent as `null` is present. There is no snapshot version
to compare; a reader is compatible with a server when every path recorded for
it is there.

Two sides check it, the way `fixtures/fade/` is checked by Rust and `fade.js`:

| side | check | what it proves |
| :--- | :--- | :--- |
| server | `web/mod.rs` `the_snapshot_carries_every_field_its_readers_read` | the server sends every recorded path |
| `echo-follower` | `echo_client.rs` `what_a_follower_reads_is_what_the_fixture_records` | the list is the follower's own serde structs |
| `fbui` | `bin/fbui.rs` `what_fbui_reads_is_what_the_fixture_records` | the list is `ClientSnapshot`'s own fields; Linux only, run by CI's `--all-features` |
| `skins` | `build/verify-skins.js` `runSnapshotFixture` | the list is exactly the fields of the `SPARSE` and `RICH` snapshots every skin is rendered against |

The two Rust readers take their lists from their own `#[derive(Deserialize)]`
structs, through `web::contract::fields_of`, so there is no second hand-typed
list to drift.

**The `skins` entry is narrower than it looks.** It records what the skin
tests render against, not every field a skin's JavaScript touches — those read
more (`echo_node`, `backend`, `can_seek`, …), and nothing here derives that
list from the skins themselves. What it does guarantee is that the snapshots
every skin is proven against describe a server that exists.

`capabilities.*` `[GDE-HST-360]` are recorded under the skins because the
lempi skin hides a host control whose capability is `false`. The test
snapshots carry them all `true`, the appliance case; `verify-skins.js`
checks the `false` case, and a snapshot without the field, separately.

`why` is recorded by presence only: it is a free-form explanation the server
builds elsewhere, and its insides are `[REQ-VIS-100]`'s, not this contract's.

A phone UI would be a fourth entry, checked from its own side the same way.
