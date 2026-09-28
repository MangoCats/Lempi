# IMPL017: Closing the Mesh Arc

**Implementation Plan — written 2026-09-28 · accepted 2026-09-28, being executed**

Builds [SPEC054](spec/SPEC054-mesh-without-ssh.md) (ssh leaves the mesh's data
plane; the pairing window hardened; trusted networks) and
[SPEC055](spec/SPEC055-fleet-configuration-kept-private.md) (fleet configuration
kept private), and the fourth security review's findings that can be fixed
without costing the person using the system anything. Six phases, ordered so each
is useful on its own and none leaves the fleet worse if the next is delayed.

> **Related:** [SPEC053](spec/SPEC053-security-hardening.md) (what each phase closes) · [SPEC051](spec/SPEC051-trusted-networks.md) · [SPEC046](spec/SPEC046-star-sync.md)

---

## 0. Decisions needed first

**`[IMPL-NSH-010]` Six, each with the recommendation the plan assumes.**

| # | Decision | Recommended |
|---|---|---|
| D1 | How the hub reaches a player for star sync | Signed requests over the player's web port, as rosters `[SPEC-NSH-020]` -- no HTTPS client in the appliance build |
| D2 | A console peer feature with no carrier after ssh goes | Accept it as "as fresh as the last sync", or drop it; listed in Phase 3 |
| D3 | How the phone recognises a trusted network | Deferred; appliances first `[SPEC-NSH-195]` |
| D4 | The private `lempi-fleet` repository | The maintainer creates it; Phase 5 waits for it |
| D5 | FLEET001 | Stays tracked `[SPEC-FCP-040]` |
| D6 | The boot window | Opens only on a node in no mesh `[SPEC-NSH-170]` |

All six were taken as recommended by the maintainer on 2026-09-28. D4 is the
maintainer's own action; Phase 5 does everything else meanwhile.

## 1. Phase 0 -- the fourth review's fixes that cost nothing

**`[IMPL-NSH-100]` Goal:** close the review's pairing and signalling findings
before building on them. No change a person pairing would notice.

1. **Signal the player alone** `[SPEC-NSH-160]`: `mesh-pair` uses
   `pkill -USR1 -x lempi` only -- no `systemctl kill` (it signals the whole unit,
   and one node's unit is not named `lempi`), no pattern match. The player
   installs its handler as the first act of `main`, on every host.
2. **One pairing per window** `[SPEC-NSH-130]`; **reject gated**, and an
   unconfirmed invitation dropped when the window closes `[SPEC-NSH-140]`; **a new
   length applies from the next press** `[SPEC-NSH-150]`.
3. **The AP swap renames before it deletes**: old `lempi-ap` to `lempi-ap-old`,
   pending to `lempi-ap`, then drop the old -- so no step leaves the node without
   one.
4. **Documents**: correct SPEC050 and `pairing.rs`, which say the window's state
   cannot be read from the LAN (it can, by design, for the page); record in
   SPEC052 that `.lan` and `.intranet` are undelegated rather than reserved, to be
   revisited if a new-gTLD round lists them; record in SPEC053 that entries give
   no reproduction steps for anything still open.

**Tests.** Rust: the window closes after a confirm and after a leave; reject is
refused when closed; a length change leaves the open window's end alone; the
handler is installed before the web server binds. Shell harness: `mesh-pair`
invokes `pkill -x` and nothing else; the AP swap with a forced rename failure
keeps `lempi-ap`.

**Verify.** Deploy; on one member node, `mesh-pair` opens the window and the
player keeps running; a leave refused for a wrong fingerprint leaves the window
open, since only a *successful* pairing or leave consumes it -- that closing is
proven by the Rust test, so no member is detached to show it live.

**Rollback:** each item is independent; revert the commit.

**Built 2026-09-28**, commit `d12b901`, deployed to all three appliances at
`a9fb228`. Verified on lempi02w: `mesh-pair` opened a 300 s window, the player
kept its pid and kept playing, and a leave naming the wrong mesh was refused
(409) with the window still open and the membership untouched.

## 2. Phase 1 -- a physical way to pair on every node

**`[IMPL-NSH-200]` Goal:** `[SPEC-NSH-170]`, so pairing never needs ssh or a
route.

1. Boot window: at start, if the node is in no mesh and the machine booted
   within the window's length (system uptime, not the player's), open the window
   for what remains of it.
2. Framebuffer node: a Pair action in `fbui` that runs `lempi-btctl mesh-pair`.
3. Record in SPEC050 that no route may ever open the window.

**Tests.** The boot rule with injected uptime and membership: in no mesh and
early -- open; a member -- closed; late -- closed.

**Verify.** On a member node, a player restart opens nothing. The un-enrolled
boot is proven by the test and, when a node is next enrolled from scratch, live.
Any machine reboot is done only with the maintainer's leave, and the node is
returned to playing.

**Built 2026-09-28**, commit `a9fb228`. After the deploy restarted every
player, all three appliances -- each a member -- came up with the window
closed. On lp3-wifi the Pair button's own command, run as fbui's user, opened
the window with the player's pid unchanged. The un-enrolled boot awaits a node
enrolled from scratch, as planned.

## 3. Phase 2 -- signed star sync, alongside ssh

**`[IMPL-NSH-300]` Goal:** the transport of `[SPEC-NSH-020..090]`, run beside the
ssh path until the two agree. Nothing is retired in this phase.

1. **Player routes** under `/mesh/sync/`: *snapshot* (the shared tables with
   passage rows anchored `[SPEC-NSH-060]`), *rehearse* (check a patch, write
   nothing), *commit* (back up, apply, report). Each verifies the mesh key, the
   run's order and freshness `[SPEC-NSH-030]`, and signs its answer with the node
   key.
2. **Apply in Rust**: generalise `mesh_sync.rs` to any table list, with the
   member rule for the shared tables and the strict rule for the catalogue
   `[SPEC-NSH-070]`; backups kept three deep; catalogue before listener, restore
   on a partial failure `[SPEC-NSH-080]`.
3. **Root verb** `lempi-btctl library-rw on|off`: remounts the library's own
   partition, found by `findmnt` on the configured library path, and nothing else.
4. **Hub**: star sync gains a signed-request transport; `patch` emits values
   patches for every node; players are found by the members' query, as roster
   pushes find them.

**Tests.** Rust: a replayed or older run is refused; a request not signed by the
mesh key is refused; the strict catalogue rule writes nothing on one mismatch; a
failed listener apply restores the catalogue. Python: the hub's signatures verify
in Rust; a values patch round-trips. Harness: `library-rw` accepts only
`on`/`off`.

**Verify (shadow runs).** For at least three routine runs, take each player's
snapshot both ways and rehearse both patches; the merge's inputs and each node's
rehearsal must agree. Measure a catalogue commit on the smallest appliance while
playing `[SPEC-NSH-900]`.

**Rollback:** the ssh path is untouched; stop using the new transport.

**Built 2026-09-28**: the engine (`b0da89c`), the player's routes and
`library-rw` (`c50c699`), the hub's transport and `star_sync.py PLAN shadow`
(`9a459cb`), deployed everywhere at `9a459cb`. `library-rw` checked live: a
no-op on lempi02w's writable root; on bose `/srv/library` went read-write and
back to read-only with the player's pid unchanged.

**Shadow run 1 of 3**, `20260928T2322Z`: all four players agreed both ways --
shared tables and catalogue summary identical to the ssh snapshot, values
patches equal in rows to the ssh patches, and each signed rehearsal clean (7 to
apply on three nodes, none kept). No catalogue rows moved in that run, so the
strict catalogue path is so far proven by its tests only, and the catalogue
commit measurement `[SPEC-NSH-900]` waits for a run that carries one. The run
itself was left uncommitted, for the maintainer.

## 4. Phase 3 -- cut over, and retire ssh from the data plane

**`[IMPL-NSH-400]` Goal:** `[SPEC-NSH-100..120]`.

1. Star sync uses the signed transport for every player; the ssh node path is
   removed.
2. **Map the console's peer chain** before removing anything:

   | Feature (tool) | Carried after by |
   |---|---|
   | flags from a node (`remote_flags.py`) | star sync, shared tables |
   | preference sync (`sync_preferences.py`, SPEC030) | star sync, shared tables |
   | review edits to a node (`remote-push`, `remote_snapshot.py`) | star sync, catalogue patch |
   | catalogue diff between Vipunens (`mesh_diff.py`, `resolve_mesh_conflict.py`, SPEC035) | a leaf Vipunen sending to the hub `[SPEC-MTR-060]` |
   | a node's flag or review shown live (`remote_peek.py`, three GETs) | the hub's merged copy, as fresh as the last run (D2) |
   | whether a peer answers (`peer_reachable`) | the members' query |
   | file tags to a node (`push_file_tags.py`) | to map before removal |

3. Remove the mapped tools, routes, jobs and the stored remotes; mark SPEC022,
   SPEC030 and SPEC035 superseded where their ssh legs were.
4. **Keys**: list every node's `authorized_keys`; remove keys a node was given to
   reach another node; keep the operator's. Done at a fleet session, each node's
   durable copy checked `[GDE-DEP-070]`.

**Verify.** A full routine run end to end on the new transport; no process on the
hub runs `ssh` or `scp` during it; each node's durable files match the target.
The operator's deploy still works.

**Rollback:** the removal is one commit per group; revert restores it. Keys are
removed last, only after a clean run.

## 5. Phase 4 -- trusted networks, appliances

**`[IMPL-NSH-500]` Goal:** [SPEC051](spec/SPEC051-trusted-networks.md) with
`[SPEC-NSH-180..190]`, the phone after D3.

1. Trust per NetworkManager connection UUID, kept in `player_settings`; on the
   first start of this build the active connection is trusted `[SPEC-NSH-185]`.
2. Dormant on an untrusted network: no discovery answers or queries, the sync
   routes and pairing refused.
3. A trust toggle on the Settings page's known-networks list, the active one
   marked, and a dormant node saying why.

**Tests.** Discovery and the sync routes silent when untrusted; the upgrade rule
trusts exactly the active connection.

**Verify.** On one node, untrust its network: discovery goes silent and the hub
lists it missing; trust it again: it answers. The node keeps playing throughout.

## 6. Phase 5 -- fleet configuration kept private

**`[IMPL-NSH-600]` Goal:** [SPEC055](spec/SPEC055-fleet-configuration-kept-private.md).
Independent of Phases 0--4.

1. After D4: `fleet/` becomes a clone of the private repository on each operator
   machine, carrying `targets.env`, the star-sync plan and `private-tokens.txt`.
2. `targets.env` gains the source hosts and `NTP_SERVER`; `deploy-everywhere.sh`
   reads them through `lib-defaults.sh`.
3. The chrony sources files become templates rendered at deploy `[SPEC-FCP-030]`.
4. `check_fleet_leaks.py --baseline`: CI runs the whole tree strictly against
   the baseline `[SPEC-FCP-050]`.

**Verify.** The rendered chrony file is byte-identical to the durable copy each
node holds now. CI passes with the baseline, and fails on a branch that adds a
private address.

---

**Traceability:** `[IMPL-NSH-010..600]` · builds [SPEC054](spec/SPEC054-mesh-without-ssh.md) and [SPEC055](spec/SPEC055-fleet-configuration-kept-private.md) · on completion, update SPEC053's entries `[SPEC-SEC-040]`, `[SPEC-SEC-050]`, `[SPEC-SEC-110]`, `[SPEC-SEC-120]` and SPEC049 `[SPEC-MTR-220]` to built
