# SPEC040: The Peer Registry and the Conflict Review UI

**Specification — the concrete design behind mesh sync**

Split from [SPEC035](SPEC035-mesh-library-sync.md) on 2026-09-10, which had
reached 356 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [SPEC035](SPEC035-mesh-library-sync.md) for the sync design

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §7 and §7a in [SPEC040](SPEC040-peer-registry-and-review-ui.md), the rest in [SPEC035](SPEC035-mesh-library-sync.md).

## 7. A prerequisite this design depends on, resolved here

**`[SPEC-MESH-080]` Closes `[SPEC-SUI-180]` — "re-importing the same bundle is
unspecified." Built 2026-09-06, and turned out narrower than first
written here.** Reading `player/core/src/bundle.rs` before touching it found it
already more idempotent than `[SPEC-SUI-180]` assumed: `files.audio_md5` is
checked before any write, so a resent encoding already reports `Already` and
writes nothing, and `upsert_recording()` already carries `[SPEC-DF-070]`'s
provenance check for flavor. **The actual gap was narrower and sharper: an
already-held encoding's whole block was skipped, including its recordings'
credits — so a *later, different* bundle improving Class A/B data (a better
flavor value, most concretely) for audio already on disk was silently
dropped, not merged.** Fixed by running `upsert_recording()` for an
already-held encoding's credits too, gated on `apply`, touching no
`files`/`passages` rows. Proven by two tests added first and confirmed
failing against the unfixed code:
`a_later_bundle_still_updates_flavor_for_an_already_held_file` (the gap) and
`a_manual_flavor_value_survives_a_later_computed_bundle_even_when_the_file_is_already_held`
(provenance protection still holds on the new call site, not only the old
one). A second, smaller fix landed alongside it: `imported_payloads` was
appending an audit row on every resend even when nothing was written;
now gated on `rows_written > 0`.

**Left as found, out of scope for this fix specifically: `recordings.title`/
`length_ms`/`source` never update once a recording row exists, on *any*
call path, not only the one this fix touches.** `upsert_recording()`'s own
`exists` check skips the whole row, always — a pre-existing behavior, not
introduced or widened here. Worth its own pass (the same provenance check
`flavor` already gets, applied per-recording instead of per-characteristic),
but conflating it with `[SPEC-MESH-080]`'s narrower fix risked a larger,
riskier change than the gap actually found required.

**`[SPEC-MESH-085]` `[SPEC-SUI-175]`'s "nothing re-reads a retained payload"
is real but not a blocker here, and is left open deliberately.** A mesh peer
running an older Vipunen/Lempi than the one that produced a bundle already
retains fields it can't interpret, per `[SPEC-SUI-165]`; failing to later
re-parse them after an upgrade is a missed optimization (a re-transfer would
fix it), not a correctness problem this design introduces or must solve to
proceed. Tracked at `[SPEC013 §6]`, unchanged by this document.

---

## 7a. Concrete design: the peer registry and the conflict review UI

**`[SPEC-MESH-090]` `sync_peers` lives in the console's own sidecar, beside
`jobs`/`remote_config` — never a table Lempi reads `[SPEC-SC-015]`, same
reasoning `[jobs.py]`'s own schema comment already gives for everything
else there.**

```sql
CREATE TABLE IF NOT EXISTS sync_peers (
    name    TEXT PRIMARY KEY,
    remote  TEXT NOT NULL,           -- user@host:/path/to/listener.db
    enabled INTEGER NOT NULL DEFAULT 1
);
```

**`[SPEC-MESH-092]` Additive, not a replacement — `remote_config` keeps
working exactly as it does today.** `[SPEC022]`'s three sync jobs
(`remote-pull`/`remote-push`/`sync-preferences`) already read one address via
`get_remote()`, tested and live-verified; rewriting that to read `sync_peers`
directly would risk exactly the regression this design has no reason to
invite. Instead: **selecting a peer calls `set_remote(peer.remote)`**, so the
three existing jobs keep calling `get_remote()` unchanged and simply act on
whichever peer was selected last. `sync_peers` is where names live;
`remote_config` stays what those three jobs actually read.

**`[SPEC-MESH-094]` The API, mirroring `/api/remote`'s existing shape:**

| Route | Method | Does |
| :--- | :--- | :--- |
| `/api/peers` | GET | list `sync_peers` |
| `/api/peers` | POST | upsert one (`{name, remote}`) |
| `/api/peers/<name>/delete` | POST | remove one — `POST`, not `DELETE`: `console.py`'s `BaseHTTPRequestHandler` only ever implements `do_GET`/`do_POST` |
| `/api/peers/<name>/activate` | POST | `set_remote(peer.remote)` — makes it the target for the three existing sync jobs |
| `/api/mesh/diff` | POST | `{peer}` → submits a `mesh-diff` job against that peer's `remote` |
| `/api/mesh/resolve` | POST | `{peer, table, key, choice}` → submits a `mesh-resolve` job, `choice` one of `"local"`/`"peer"` |

**`[SPEC-MESH-096]` `mesh-diff` is a job like any other** — `_mesh_diff(job_id,
target)` runs `mesh_diff.py <library> <target> --json` as one stage
(`_run_single_stage`, the same shape `sync-preferences` already uses), and
the diff's full report becomes the job's `result`. `mesh_diff.py` gained
`--json`: a final line `{"ok": true, ...report}` — found-a-conflict is data
in the result, never a job failure, since nothing was asked to act yet
`[SPEC-MESH-038]`.

**`[SPEC-MESH-098]` `mesh-resolve` performs `[SPEC-MESH-070]`'s write, to
whichever side(s) disagree with the chosen value.** `tools/resolve_mesh_conflict.py`
(new): given a table, an identity key, and a chosen side, it writes that
side's current value to the *other* side with `source`/`boundary_src`
forced to `manual` — locally through a direct connection, remotely through
the identical `sudo systemctl stop lempi && sqlite3 ... && sudo systemctl
start lempi` recipe `[push_file_tags.py]`/`[sync_preferences.py]` already
use, quoted with `remote_peek.literal()`. A person typing a third value
neither side has yet is `--value`, applied to both sides the same way. The
job kind (`_mesh_resolve`) is a `_run_single_stage` wrapper exactly like
`sync-preferences`'s.

**`[SPEC-MESH-100]` One new console page, `/mesh`, not a section bolted onto
`/flags`.** `/flags`'s "Sync with a remote" section is unchanged. `/mesh`
carries: the peer list (add/remove/activate), a "diff against" selector plus
button, and — once a diff has run — three panels per table: counts for
`local_only`/`peer_only` always, and for `files` specifically (the one
table whose key is directly a `--md5-file`-shaped list) an `export_bundle.py`
command line naming them, printed rather than automated further here —
`recordings`/`passages` keys aren't themselves bundleable selections, so
their counts stand alone. `[SPEC-SUI-110]`
already decided a bundle target is "an ssh host and a directory, never a
Lempi endpoint," and this document does not relitigate that), and a list of
`conflict` rows, each with the two values side by side and "use local"/"use
peer" buttons wired to `/api/mesh/resolve`.

---

