# SPEC051: Trusted Networks

**Design Specification — Tier 2 · written 2026-09-28 · built for appliances 2026-09-28 (`player/src/trust.rs`, IMPL017 Phase 4); the phone waits on `[SPEC-NSH-195]` · build decisions in [SPEC054](SPEC054-mesh-without-ssh.md) §6**

A node should take part in Lempi's discovery and mesh only on networks its
operator has chosen to trust. This is the answer to the disclosure and
roaming edges of [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-020]`: a
phone that joins café Wi-Fi, or an appliance moved to a strange LAN, should go
quiet rather than announce itself and answer strangers. It complements, and
does not replace, the membership trust of
[SPEC049](SPEC049-mesh-membership-and-trust.md) — a hostile device *on* a
trusted network is still what enrolment's code comparison and fingerprint
pinning defend against.

> **Related:** [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-010..080]` (what this gates) · [SPEC049](SPEC049-mesh-membership-and-trust.md) `[SPEC-MTR-020]` (the announce switch this generalises) · [SPEC034](SPEC034-wifi-configuration.md) `[SPEC-WIFI-030]` (NetworkManager, the appliance's network state) · [GUIDE035](../GUIDE035-node-discovery.md) (the idea)

---

## 1. Why a per-network boundary

**`[SPEC-TN-010]` The threat is a foreign network, not a foreign host.** On the
household LAN, discovery answering strangers is intended: that is how a phone
finds a hub `[SPEC-DSC-040]`. The exposure is a node carried, or moved, onto a
network no one meant to trust:

- a phone on public or café Wi-Fi announces nothing useful there, yet
  [SPEC050](SPEC050-node-discovery.md)'s "ask from every adapter"
  `[SPEC-DSC-050]` would let it ask, and a stranger could answer as a "hub";
- an appliance relocated to a strange LAN would answer `candidates`
  to anyone `[SPEC-DSC-020]`, giving up its name, key fingerprint and build;
- unauthenticated replies invite reflection off a spoofed source address.

A per-network trust flag closes all three at once: on an untrusted network the
node does not announce, does not answer, does not ask, and does not send
anything to a hub it happens to discover.

## 2. What trust gates

**`[SPEC-TN-020]` Participation, not membership.** Trust decides whether the
node *takes part* on this network; it never grants membership, which only the
enrolment of `[SPEC-MTR-130]` does. On a trusted network a node behaves as
[SPEC050](SPEC050-node-discovery.md) already describes. On an untrusted one it
is **dormant**:

- it answers no `candidates`, `hubs` or `members` query `[SPEC-DSC-020]`;
- it sends no discovery queries of its own;
- it does not open, or answer on, the members' TLS port `[SPEC-MTR-200]`;
- it does not upload listener state, preference edits or flags to any hub.

Its own web UI on loopback, and an operator physically at the device, keep
working — dormancy is about the network, not the machine.

**`[SPEC-TN-025]` Membership already held is not lost.** A node that is a member
of a mesh, moved to an untrusted network, stays a member: it simply does
nothing on that network until it returns to a trusted one. Leaving a mesh
remains the deliberate act of `[SPEC-MTR-130]`.

## 3. What names a network

**`[SPEC-TN-030]` Not the SSID.** An SSID is a label anyone can copy; trusting
by SSID would trust an attacker's look-alike AP. A network is identified by
something harder to forge and already at hand:

- **On an appliance**, the **NetworkManager connection profile** — the same
  object [SPEC034](SPEC034-wifi-configuration.md) already manages and
  `wifi-known` already lists, so trust is one flag per known connection and the
  Settings list writes itself. Its UUID is stable across reconnects.
- **On a phone**, a stored network identity — the gateway's MAC address
  together with the SSID — since the phone has no NetworkManager. The gateway
  MAC is what a look-alike SSID cannot cheaply reproduce.

A network the node has no identity for is **unknown**, and treated as untrusted
until the operator says otherwise.

## 4. The default policy

**`[SPEC-TN-040]` Fail closed: unknown is untrusted.** A network the operator
has not marked is not trusted. This is the safe direction — a new or spoofed
network gets silence, not participation.

**`[SPEC-TN-045]` Auto-trust follows a deliberate act, not mere first sight.**
Trust is granted automatically in exactly one case, chosen to fit each node:

- **An appliance** trusts the network it is **first provisioned on** — its
  household LAN `[GDE-DEP-060]`. Appliances are set up there and stay put, so
  first-provisioning is that LAN, and the common case needs no configuration.
- **A phone** trusts the network on which it **enrolled into a mesh** — a
  deliberate step `[SPEC-MTR-130]` — not merely the first network it ever saw.
  A phone roams and may first run anywhere; tying trust to enrolment ties it to
  a security step the operator took on purpose.

Everything else waits for the operator to trust it in Settings.

## 5. The relationship to what exists

**`[SPEC-TN-050]` It subsumes the announce switch.** [SPEC049](SPEC049-mesh-membership-and-trust.md)'s
"Be found on this network" `[SPEC-MTR-020]` is a per-node "answer at all"
toggle. Trusted networks make it per-network. The two compose: a node answers a
query only when it is announcing **and** the current network is trusted. The
existing switch stays as the global off.

**`[SPEC-TN-055]` Trust reduces exposure; the fingerprint is the authentication.**
A trusted network is not an authenticated one. Even there, a node sends data to
a hub only when that hub's key fingerprint matches the one pinned at enrolment
`[SPEC-MTR-130]`. Trusted networks decide *whether to speak on this network*;
the pinned fingerprint decides *whether this really is our hub*. Both are
required, and a review of the phone must confirm it refuses a hub whose
fingerprint differs from the pinned one.

## 6. What the operator sees

**`[SPEC-TN-060]` A trust column on the known-networks list.** The appliance's
Wi-Fi Settings already lists known connections; each gains a trust toggle, and
the currently-connected one is marked. The phone's mesh Settings lists the
networks it has an identity for, with the same toggle. Turning a network off
makes the node dormant there at once; turning it on lets discovery resume. The
list says plainly which network is active and whether it is trusted, so a node
that has gone quiet shows *why*, rather than looking broken `[GDE-DEP-060]`.

**`[SPEC-TN-065]` Granting trust is physical; revoking is not.** Marking a
network trusted needs the pairing window `[SPEC-DSC-090]`, the same local act as
pairing. Otherwise any host on a foreign network could mark *that* network
trusted through the node's open page and wake it there -- undoing the whole of
this document from inside the network it guards against. Revoking trust only
narrows exposure, and stays one click.

## 7. Left for the building

**`[SPEC-TN-900]` Settled by the appliance build, 2026-09-28:**

- *Re-evaluating on a network change:* a reading of the node's connections is
  reused for 30 s and taken again when next needed -- by the next discovery
  query, sync request or page load -- so a change is seen within that without a
  poll of its own.
- *Wired Ethernet:* keyed like any connection, by its NetworkManager UUID. The
  rule counts every connection the node is on, loopback aside, and takes part
  only when all of them are trusted: the discovery socket is bound to every
  interface, so one untrusted connection is one network it would answer on.
- *Mesh-visible or local:* local. Trust is kept in `player_settings`, which no
  sync carries.

Still open: the phone's gateway-MAC identity across a network that legitimately
changes its router, with the phone itself `[SPEC-NSH-195]`.

---

**Traceability:** `[SPEC-TN-010..065]`, `[SPEC-TN-900]` · gates [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-020]` · generalises `[SPEC-MTR-020]` · answers the discovery edges recorded in the follow-up security review (R4)
