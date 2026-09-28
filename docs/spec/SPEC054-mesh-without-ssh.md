# SPEC054: Mesh Without ssh

**Design Specification — Tier 2 · written 2026-09-28 · accepted 2026-09-28, being built · build plan in [IMPL017](../IMPL017-closing-the-mesh-arc.md)**

Star sync still reaches the appliances over ssh `[SPEC-MTR-220]`, and a chain of
console tools reads and writes other nodes the same way. That is the last place
the mesh trusts something other than its own keys, and it carries the security
findings left deferred in [SPEC053](SPEC053-security-hardening.md)
`[SPEC-SEC-040]`, `[SPEC-SEC-120]`. This retires ssh from the mesh's *data*
plane, hardens the pairing window per the fourth review, and settles the build
decisions [SPEC051](SPEC051-trusted-networks.md) left open.

> **Related:** [SPEC049](SPEC049-mesh-membership-and-trust.md) `[SPEC-MTR-030]` `[SPEC-MTR-220]` · [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-080]` `[SPEC-DSC-090]` · [SPEC046](SPEC046-star-sync.md) `[SPEC-STAR-080]` `[SPEC-STAR-087]` · [SPEC051](SPEC051-trusted-networks.md) · [SPEC055](SPEC055-fleet-configuration-kept-private.md) (the fleet's own config, the other half of this work)

---

## 1. What changes and what stays

**`[SPEC-NSH-010]` ssh leaves the data plane, not the operator's hands.** The
mesh's own traffic -- star sync, and the console's reads and writes of other
nodes -- moves onto the mesh's keys. The operator's administration of machines
stays on ssh: deploying (`build/deploy-appliance.sh`, `build/install-config.sh`), provisioning,
and diagnosis. So does the hub's backup mirror, which is a backup of the hub, not
mesh traffic. Nothing here removes an operator's ability to log in to a machine.

## 2. The transport

**`[SPEC-NSH-020]` The hub reaches a player the way it already sends rosters.**
The hub invites players and pushes rosters to them over their ordinary web port,
signed by the mesh key `[SPEC-DSC-080]`; star sync uses the same path. The
appliance build deliberately compiles no HTTPS client (`player/Cargo.toml`,
feature `appliance`), and this keeps it that way. The phone, which is a TLS
client of the hub already `[SPEC-MTR-200]`, keeps its channel unchanged.

**`[SPEC-NSH-030]` Signed both ways, fresh, and in order.**
- Every request from the hub carries the run it belongs to, a nonce and a time,
  signed by the mesh key; the player verifies it against the key it pinned when
  it joined `[SPEC-DSC-070]`.
- Every answer from a player is signed by its node key; the hub verifies it
  against that member's certificate in the roster.
- A player accepts a patch only from a run newer than the last it applied, so a
  captured request cannot be replayed to roll a node back.

**`[SPEC-NSH-040]` Why signatures and not a channel.** What crosses the LAN here
-- the household's shared edits and the catalogue -- is what the player's own
open LAN UI already shows. Integrity and origin are what an attacker could abuse,
and signatures give both. These routes are authenticated by signature, like the
roster, so they are not behind the pairing window; once
[SPEC051](SPEC051-trusted-networks.md) is built they answer only on a trusted
network `[SPEC-NSH-180]`.

## 3. What travels

**`[SPEC-NSH-050]` Up: every table the merge shares, and none it keeps.** A
player sends the listener tables the merge treats as shared -- its last-write-wins
and union tables, `TABLES` in `tools/star_merge.py` -- and never the ones it keeps
per node or takes from the hub: plays, programmes and node state never leave a
node, as today `[SPEC-STAR-045]` `[SPEC-MTR-040]`. The phone sends three of these
`[SPEC-MTR-030]`; an appliance sends all it holds, as the ssh path carried them,
so a change of transport stops nothing from syncing. *Corrected 2026-09-28,
before building:* this first read "what the phone sends", which would have
silently dropped likes, occasions and reviews from every appliance.

**`[SPEC-NSH-060]` A passage row keeps its node's id; the node says what the id
means.** A passage id is local to a node `[SPEC-STAR-049]`. The phone holds such
rows back; an appliance cannot, or passage flags would stop syncing. So beside its
tables a player sends a **catalogue summary**: each passage's
`(passage_id, audio_md5, kind, start_ms, end_ms)` anchor `[SPEC-DF-109]`, and each
file's `audio_md5` with the node's own machine-scope columns `[SPEC-DF-030]`. That
is all the merge reads of a node's catalogue -- it translates passage ids with the
first and keeps the node's paths with the second -- so the catalogue itself never
travels up. It is 1.27 GB on each appliance (measured 2026-09-28), and the ssh
path copied it whole whenever it had moved.

**`[SPEC-NSH-070]` Down: values, not digests.** A patch names each changed row
with its old and new values, as the member patch does (`mesh_sync.rs`), because
the digest `star_patch.py` uses cannot be reproduced exactly from Rust. The shared
tables keep the member rule: a row changed on the node since its upload is kept
and decided next time. The catalogue is authored at the hub `[SPEC-STAR-085]`, so
its patch is strict: one row not as expected writes nothing `[SPEC-STAR-080]`.
Its baseline is what the hub last sent that node, and it covers the hub-authored
columns only; the machine-scope columns are the node's and travel in neither
direction. A node that changed hub-authored data itself is refused, visibly --
which the shadow runs of IMPL017 Phase 2 exist to find before anything depends
on it.

**`[SPEC-NSH-080]` The player applies its own patch.** No stop, no ssh, no
`sqlite3` run as another user. Before applying it backs up what it will change,
keeping its three most recent, and applies the catalogue before the listener; if
the second fails the first is restored, so a node never keeps half a switch. A
library partition mounted read-only is remounted by a narrow root verb that takes
only *on* or *off* and resolves the mount itself, never from a path it is given.

**`[SPEC-NSH-090]` The run is still a person's.** The hub's stages stay as
`[SPEC-STAR-087]` has them: a person starts a run, reads its report, rehearses and
commits. Rehearsal asks each player to check its patch without writing. A player
that does not answer is missing from the run and receives nothing
`[SPEC-STAR-075]`.

## 4. What is retired

**`[SPEC-NSH-100]` The ssh node path in star sync** -- fetching a node's files,
uploading `star_patch.py` to run there, stopping its service -- once the path
above has carried real runs and matched it.

**`[SPEC-NSH-110]` The console's peer chain.** Every console feature that reads
or writes another node over ssh, its routes and stored remotes, and the three
ssh-running reads `[SPEC-SEC-050]`. Each is either carried by star sync (shared
edits), by a leaf Vipunen sending its catalogue to the hub `[SPEC-MTR-060]`, or
dropped; the mapping is a step of the build, and nothing is removed unmapped.

**`[SPEC-NSH-120]` Cross-node keys.** Keys a node was given to reach another node
are removed from every `authorized_keys` once nothing uses them. The operator's
own keys are not touched.

## 5. The pairing window, hardened

The fourth review found the window sound outside its open minutes and narrowable
within them. Everything here costs the person pairing nothing.

**`[SPEC-NSH-130]` One pairing per window.** The window closes after the first
successful confirmation or leave.

**`[SPEC-NSH-140]` Rejecting is gated like the rest,** and an invitation not
confirmed when the window closes is dropped with it.

**`[SPEC-NSH-150]` A new window length applies from the next press.** Changing
it never lengthens a window already open.

**`[SPEC-NSH-160]` The button reaches the player alone.** `lempi-btctl mesh-pair`
signals the process named exactly `lempi` -- not every process in its service,
where a helper mid-change could be killed by the same signal, and not by pattern.
The player installs its handler before anything else at start, on every host, so
a stray signal never ends it.

**`[SPEC-NSH-170]` Every node has a physical way to pair, and none over HTTP.**
- *A node that is in no mesh* opens its window for the configured length after
  the **machine** boots. The web UI can restart the player but not the machine,
  and bringing a powered-off node back needs a hand, so a boot is presence. A
  member's boot opens nothing: a power cut must not open every node at once.
- *The framebuffer node:* a Pair action on its screen.
- *Any node:* the operator at its console (`lempi-btctl mesh-pair`, or on a
  general-purpose machine a signal to its own player).
- No route may ever open the window. [SPEC050](SPEC050-node-discovery.md) records
  that the window's *state* is visible to LAN clients, for the page's sake.

**`[SPEC-NSH-175]` Not taken, because each costs the person pairing.** Hiding
the code or the window's state from LAN clients, or requiring the code to be
typed from a local display. The remaining exposure is a host already on the LAN
acting within an operator's own window; the steps above bound it to one pairing,
and its result is visible on the player's page.

## 6. Trusted networks: the build decisions

**`[SPEC-NSH-180]` Dormant means dormant for the mesh too.** On an untrusted
network a node answers no discovery `[SPEC-TN-020]`, refuses the sync routes of
§2, and refuses pairing. Pairing happens at home, so this costs nothing.

**`[SPEC-NSH-185]` An appliance keys trust to its NetworkManager connection
UUID** `[SPEC-TN-030]`. When the build that brings this first starts, it trusts
the connection active at that moment, so no node already in service goes quiet
on upgrade.

**`[SPEC-NSH-190]` The hub is stationary and trusted.** It moves with its
desktop, not with a person; a hub on a laptop would revisit this.

**`[SPEC-NSH-195]` The phone waits for a decision.** Reading the Wi-Fi name or
access point on Android needs the location permission -- a prompt. The
alternative, trusting a network while the pinned hub answers there, needs no
prompt but must ask to find out, which shows a mesh identifier to a foreign
network. Appliances are built first; the phone follows the choice.

## 7. Open, to settle when built

**`[SPEC-NSH-900]`**
- whether applying a large catalogue patch while playing stalls the audio path,
  measured on the smallest appliance; if it does, apply between passages;
- the largest patch a player accepts, and whether it is streamed;
- whether any console peer feature has no carrier and is simply lost, per
  the mapping of `[SPEC-NSH-110]`;
- whether the phone should also send anchored passage rows `[SPEC-NSH-060]`.

---

**Traceability:** `[SPEC-NSH-010..195]`, `[SPEC-NSH-900]` · builds `[SPEC-MTR-220]` · closes, when built, `[SPEC-SEC-040]` (ssh links), `[SPEC-SEC-050]` (ssh reads), `[SPEC-SEC-110]` (via SPEC051), `[SPEC-SEC-120]` (keys) · the fourth review's pairing findings in §5
