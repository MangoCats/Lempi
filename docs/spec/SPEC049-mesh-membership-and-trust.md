# SPEC049: Mesh Membership and Trust

**Design Specification — Tier 2 · written 2026-09-27, from the maintainer's decisions that day · membership and the shared data built 2026-09-27, discovery 2026-09-28 ([SPEC050](SPEC050-node-discovery.md)); §6 has what remains**

Until now a node trusted another by one of two means, and neither was Lempi's own.
Star sync reaches the appliances over ssh, whose keys the operating system
manages. The intake takes music from a phone under one application key that
every copy carries, sent in plain HTTP. That key "keeps out mistakes and not
people" `[REQ-AND-287]`. That was enough while a phone only *offered* music,
which a person reviews before anything is inducted `[REQ-AND-289]`. It is not
enough for what comes next: a phone's preference edits and flags reach every
player in the house. It is not enough in a large office either, where several
households' meshes share one network. This specification gives a mesh an
identity, an authority, members and a channel between them. Discovery
[GUIDE035](../GUIDE035-node-discovery.md) is built on top of that.

> **Related:** [SPEC046](SPEC046-star-sync.md) (what the mesh carries) · [SPEC035](SPEC035-mesh-library-sync.md) (the mesh screen) · [REQ007](REQ007-android.md) `[REQ-AND-286..289]`, `[REQ-AND-330]` · [SPEC048](SPEC048-pending-identification.md) (the intake) · [GUIDE035](../GUIDE035-node-discovery.md) (discovery)

---

## 1. The maintainer's decisions, 2026-09-27

**`[SPEC-MTR-010]` The Vipunen hub is its mesh's authority.** It holds the
mesh's key, decides who is a member, and signs the roster that says so. It is
not a passphrase shared by every node. With a shared passphrase, one node that
leaks it opens the whole mesh, and there is no way to remove one member without
re-keying all the others.

**`[SPEC-MTR-020]` An unenrolled device announces itself by default**, so the
hub's mesh screen can list it as a candidate. A setting in its Settings turns
that off. A device that has opted out is still reachable by a person who types
its name `[GDE-NDS-920]`.

**`[SPEC-MTR-030]` What a member shares, and what it keeps.**

| | shared through the mesh? |
| :--- | :--- |
| plays and rejections | **never**: each node's own `[REQ-PD-113]`, the phone's included |
| preference edits and flags | yes, from every member, the phone included once enrolled |
| programmes | **no**: each node defines and edits its own `[SPEC-MTR-040]` |
| catalogue | authored at the hub `[SPEC-STAR-085]`, or at a leaf Vipunen and sent to the hub `[SPEC-MTR-060]` |

**`[SPEC-MTR-040]` Programmes are a node's own.** A node keeps its own
programme definitions, and a person edits them there. A Lempi node may import
programmes from the hub by hand. The hub may copy its programmes from a node by
hand. Neither happens on its own. Star sync therefore treats
`listener_programs` and `listener_program_seeds` as each node's own, as it
treats plays. It stopped merging them on 2026-09-27; until then it had unioned
them, so every node now starts from the same eight.

**`[SPEC-MTR-050]` One hub; other Vipunen nodes are leaves.** A mesh can have
more than one node able to run Vipunen: the desktop, and `teacherslounge` with
its mirror. One is the hub, named in the roster. The others are leaves. A person
may still run Vipunen on a leaf and edit there `[SPEC-MTR-060]`.

---

## 2. Identity, membership and the roster

**`[SPEC-MTR-100]` Every node has an identity of its own**: an ECDSA P-256
keypair made at its first start, which never leaves it. It is not Ed25519, as
this was first written: the Keystore on Android 11, the floor, has no Ed25519,
and P-256 is what a Keystore key can present as a TLS client certificate
`[SPEC-MTR-200]`. The fingerprint is SHA-256 of the public key's DER
(SubjectPublicKeyInfo), which is the same bytes on every platform. It is shown
as eight groups of four hex digits from its first half, with the first two
groups as the short form. A node's names (host name, network name, IP address, the name a person
gives it `[GDE-NDS-040]`) are labels on that identity, and any of them may
change. The private key is kept with the node's own state, never with the
catalogue:
- on an appliance, in the durable layer `[IMPL-BOS-185]`;
- on Android, in the Keystore;
- on the desktop, beside `data/`, and in its backups.

**`[SPEC-MTR-105]` A node may also have a short name, and lists show it by
that name.** *Decided by the maintainer 2026-09-27.* The name is optional, and
a person edits it. It may be the host name, a name the network knows it by, or
any UTF-8 text. It is a label, never an identity: the key decides who a node
is, and a name can be changed, repeated, or left empty. Wherever nodes are
listed (the mesh screen, candidates, the phone's Send screen, a follower's
choice) three rules apply:
- **An empty name shows the short fingerprint** in its place.
- **Two nodes with the same name in one list each show their short fingerprint
  after it**, so the two can be told apart without a click.
- **"Show fingerprints" appends every node's short fingerprint.** The person
  switches it on when they want to compare fingerprints, as when confirming an
  enrolment `[SPEC-MTR-130]`.

**`[SPEC-MTR-110]` A mesh is its hub's key.** The hub has a second keypair, the
mesh key; the mesh's identity is its fingerprint. Losing it means enrolling every
member again, so it is backed up with the hub's pair `[SPEC-STAR-086]` and never
travels anywhere else. It lives in `mesh/` beside the hub's catalogue, with the
hub's node key and the signed roster (`tools/mesh.py`). The daily backup stores
all three as an SQLite archive beside the pair's objects, mirrored and kept the
same way; `sqlite3 FILE -Ax` extracts it.

**`[SPEC-MTR-120]` The roster is the membership, signed by the hub.** For each
member it lists:
- the public key;
- its role: `hub`, `leaf-vipunen`, `player` or `phone`;
- its labels;
- when it was enrolled.

It also carries a version number and the discovery key `[SPEC-MTR-310]`. Every
member holds the latest copy, checks the hub's signature on it, and refuses a
version older than the one it already has. A member learns a new version from
the hub, or from any other member, since the signature proves it either way.

**`[SPEC-MTR-130]` Enrolment is a person's act, confirmed on both sides.** The
person picks a candidate on the hub's mesh screen, found or typed. The hub and
the candidate each derive a six-digit code from both public keys and a fresh
nonce from each side, and both show it. This is Bluetooth's numeric comparison,
for the same reason: a device in the middle cannot make the two codes agree.
- The person confirms on the hub.
- The candidate's own Settings asks "Join *mesh name*? Code 123 456" and is
  confirmed there too. An appliance with no screen of its own is confirmed from
  its web page. The framebuffer node is confirmed on its display as well.
- Only then is the candidate added and the new roster sent out.

As built, the candidate starts it, on the hub's members' port:
1. `POST /enrol/start` sends its certificate, with no client certificate
   presented. The hub answers with a commitment to its nonce, a hash, so it
   cannot choose that nonce after seeing the candidate's.
2. `POST /enrol/nonce` sends the candidate's nonce, signed by its key to prove
   it holds the key it named. The hub reveals its nonce, and the candidate
   checks it against the commitment.
3. Each side computes the code from the hub's key as the candidate's own TLS
   connection saw it, the candidate's key, and both nonces.
4. `POST /enrol/confirm` is the candidate's person, signed again; the console's
   Accept is the hub's person.
5. `GET /enrol/status` returns the signed roster once both have confirmed.

The candidate verifies the roster against the mesh key it was told at step 1,
and pins both keys from then on.

*Run 2026-09-27 on the Moto G against a test mesh:*
- both screens showed 501 494;
- the phone joined, verified the roster, and made a members-only request with
  its Keystore key;
- removed at the hub, its next connection was refused in the handshake.

**`[SPEC-MTR-140]` Leaving is a new roster.** The hub removes a member, and
every other member refuses it from the next roster on. A member may also leave
of its own accord, dropping the roster and returning to unenrolled.

---

## 3. The channel

**`[SPEC-MTR-200]` Members talk over mutually authenticated TLS 1.3, pinned to
the roster.** Each side presents a certificate made from its node key. The
other side accepts it only if that key is in the current roster, never because
a certificate authority vouches for it. No certificate authority is involved,
and no public name is needed. On the hub it is `tools/intake.py`'s members'
port, 5732. That server's trust store *is* the roster's certificates, rebuilt at
each new version, so a removed member fails the handshake. It also admits a
client with no certificate, for enrolment only, and every `/member/` route
requires a member. The phone presents its Keystore key and pins the hub by
fingerprint, not by host name (`MeshClient`). The player's side will use
`rustls`. Everything between members goes this way:
- preference edits and flags up from the phone `[REQ-AND-330]`;
- updates down from the hub;
- a leaf's edits to the hub `[SPEC-MTR-060]`.

**`[SPEC-MTR-210]` The application key remains for strangers, and only for
offering music.** An unenrolled node may still offer music to an intake under
the application key `[REQ-AND-287]`, because a person reviews it before anything
is inducted. That is also how a phone joins the mesh in the first place. A hub
may turn this off: an office would. Everything else the intake does needs
membership:
- serving a good copy for a repair `[SPEC-PID-040]`;
- receiving listener state;
- sending updates.

**`[SPEC-MTR-220]` ssh stays only where the channel cannot reach.** Since
2026-10-04 star sync reaches every node by signed requests between mesh
identities `[SPEC-STAR-100]`; ssh remains for starting `teacherslounge`'s
on-demand player and for the hub's backup mirror (IMPL017 §4).

---

## 4. A leaf Vipunen

**`[SPEC-MTR-060]` A leaf's catalogue edits are sent to the hub, and the leaf
says so until they are.** Vipunen on a leaf opens its own copy of the catalogue
and edits it as the hub would. After any job that wrote to the catalogue, the
console shows a banner that stays until the edits reach the hub: "*N* edits
here are not yet at the hub, *hub name*. Send to the hub."
- Sending gives the hub this node's catalogue, as star sync takes a node's
  catalogue that has moved `[SPEC-STAR-085]`.
- The hub merges it three ways against the common ancestor `[SPEC-STAR-047]`
  and returns the merge report to the leaf's console.
- The person who made the edits reads it there and commits it `[SPEC-STAR-070]`.
- Committing updates the hub and the leaf. The other nodes receive it at the
  next routine sync, since distribution stops players and is never a side
  effect.
- A conflict is shown to that person with the rule that decided it, as every
  merge decision is `[SPEC-STAR-060]`.

**`[SPEC-MTR-070]` A leaf never becomes the hub by accident.** Moving the hub
is a deliberate act at the old hub. It hands over the mesh key and signs a
roster naming the new hub. It is later work; until then the hub is where the
roster says.

---

## 5. Discovery, on top

**`[SPEC-MTR-300]` Queries go out on UDP 13492, and answers come back directly
to the sender** `[GDE-NDS-010]`. A query goes to the local broadcast address. It
reaches one network segment, which in an office is usually one floor or one
VLAN. Across routed subnets a typed name remains the way `[GDE-NDS-920]`.
Nothing is relayed.

**`[SPEC-MTR-310]` A member answers only its own mesh.** A member's query
carries a random nonce, and an HMAC over that nonce and the mesh's fingerprint.
The HMAC is keyed with the *discovery key*, a symmetric key the roster carries
to members. It is never the mesh key itself, so a leaked discovery key cannot
sign a roster. A member answers only a query whose HMAC checks against its own
mesh's discovery key. Its answer is an HMAC over the query's nonce, so an
answer recorded earlier cannot be played back. Two meshes on one network each
see only their own members.

**`[SPEC-MTR-320]` A candidate answers the hub's call for candidates**, unless
it has opted out `[SPEC-MTR-020]`. It gives:
- that it is a Lempi or Vipunen node, and its role;
- its labels;
- its fingerprint;
- that it belongs to no mesh.

It reveals nothing a listener could use to enter a mesh. The hub's mesh screen
lists candidates apart from members, and enrols one only as `[SPEC-MTR-130]`
describes.

**`[SPEC-MTR-330]` Discovery only offers; a person chooses.** Nothing connects,
follows or syncs because something answered. That is what `[SPEC-ECHO-100]`
asked of any discovery `[GDE-NDS-100]`, and it is revised in the change that
builds this. A phone holds a `MulticastLock` only while it is searching
`[GDE-NDS-130]`. The appliances need neither avahi nor mDNS `[SPEC-WIFI-030]`.

---

## 6. Order of work, and what is open

The order agreed 2026-09-27:
1. this specification;
2. identity, the roster, enrolment and the channel, with the phone as the first
   member enrolled. *Built 2026-09-27*: `tools/mesh.py`, the intake's members'
   port, the console's mesh page, and the phone's "Mesh membership…" screen.
   The hub's real mesh is created, and the phone enrolled in it, by the
   maintainer, who compares the codes;
3. the phone's preference edits and flags up to the mesh, and the hub's updates
   down `[REQ-AND-330]`. *Built 2026-09-27* for the household's edits. The
   phone's "Sync my edits with the mesh" does two things:
   - down: it applies what the last star sync left in its outbox, by the
     three-way rule, keeping any row changed on the phone since;
   - up: it uploads the shared tables (`lempi_core::mesh_sync`).

   Each enrolled phone is a star-sync node reached by upload: merged, reported
   and gated by a person like any other `[SPEC-STAR-070]`. Its history is its
   own earlier uploads, and a table it does not upload is never read as rows
   it removed (`star_merge.removals`). Catalogue updates travel the same
   channel, only what changed `[SPEC-PL-107]`;
4. discovery, and the players' joining by invitation. *Built 2026-09-28*:
   [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-070]`;
5. star sync onto node identities `[SPEC-MTR-220]`: built 2026-10-04, `[SPEC-STAR-100]`.
   Moving the hub `[SPEC-MTR-070]` stays later and optional.

1. **`[SPEC-MTR-900]` Whether occasions follow programmes.** SPEC046 carries
   `listener_occasions` and `listener_occasion_points`, the seasonal calendar,
   in the same class as programmes. The maintainer's decision named programmes,
   so occasions are still shared.
2. **`[SPEC-MTR-910]` The backoff schedule** for repeated queries, as `[GDE-NDS-910]` left it.
3. **`[SPEC-MTR-920]` Clock and ordering for a phone's edits**: a phone's clock is
   not disciplined, and last-write-wins reads `updated_at` `[SPEC-PREF-105]`.
   *Settled with step 3, 2026-09-27:* the phone's edits keep their own times.
   Stamping them with the hub's time at receipt would let an old edit,
   uploaded late, override a newer one. Instead the hub measures the phone's
   clock at each upload and refuses one more than two minutes off, saying by
   how much. The Moto G measured +3.3 s.
4. **`[SPEC-MTR-930]` A phone's edits about a single passage stay on it.** A
   passage id is the node's own `[SPEC-STAR-049]`, and the phone does not
   upload its catalogue, which is how an appliance's ids are translated. None
   exist today on any node. They are counted and reported when the phone
   syncs, and neither sent up nor down.

---

**Traceability:** `[SPEC-MTR-010..920]` · from the maintainer's decisions of 2026-09-27 · takes up [GUIDE035](../GUIDE035-node-discovery.md) `[GDE-NDS-010..920]` · refines `[REQ-AND-287]`, `[SPEC-STAR-040]`
