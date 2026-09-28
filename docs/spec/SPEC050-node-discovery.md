# SPEC050: Node Discovery — As Built

**Design Specification — Tier 2 · written 2026-09-28 · the wire protocol and behaviour of [SPEC049](SPEC049-mesh-membership-and-trust.md) §5**

SPEC049 §5 says what discovery must do: queries on UDP 13492, a member
answering only its own mesh, a candidate answering the hub, and nothing
connecting because something answered. This is how it is built, and the two
decisions the building made.

> **Related:** [SPEC049](SPEC049-mesh-membership-and-trust.md) `[SPEC-MTR-300..330]` · [GUIDE035](../GUIDE035-node-discovery.md) (the idea) · [SPEC034](SPEC034-wifi-configuration.md) `[SPEC-WIFI-030]` (why not mDNS)

---

## 1. The wire

**`[SPEC-DSC-010]` One JSON datagram each way.** A query is sent once to the
segment's broadcast address, port 13492:

```json
{"lempi": 1, "q": "candidates" | "hubs", "nonce": "<hex>"}
```

A node that answers does so to the asker's address and port, never by
broadcast, and repeats the nonce. An answer without the asker's nonce is
ignored, so an answer recorded earlier or meant for someone else cannot be
passed off as a new one:

```json
{"lempi": 1, "a": "candidate" | "hub", "nonce": "<hex>", "name": "<host name>",
 "fingerprint": "<SHA-256 of its public key's DER>", "web_port": 5720, "version": "<commit>",
 "mesh": "<name>", "mesh_fingerprint": "...", "members_port": 5732}
```

The last three are a hub's only.

**`[SPEC-DSC-015]` The members' query** `[SPEC-MTR-310]`, built with the
players' enrolment:
- **The query** adds `"q": "members"`, the mesh's fingerprint, and a `proof`:
  HMAC-SHA256 over `lempi-members|<mesh>|<nonce>`, keyed with the discovery key
  the roster carries to members.
- **Who answers:** a member answers only a query whose proof checks against
  its own mesh. It then answers `"a": "member"` with a proof of its own over
  `lempi-member|<nonce>|<fingerprint>`.
- **Everyone else, other meshes included, gets silence:** not even that a member
  is there. A member no longer answers as a candidate.

**`[SPEC-DSC-020]` Who answers what.**
- **A player that belongs to no mesh answers `candidates`**, unless "Be found on
  this network" is off in its Settings `[SPEC-MTR-020]`. That is on by default,
  and kept per node in `player_settings`.
- **A hub answers `hubs`**, so a device can find a mesh to join without typing
  an address. It answers only while its members' port is open, since that is
  where joining happens, and never as a candidate.
- **A phone answers nothing.** Its player serves loopback only `[REQ-AND-160]`,
  and a device someone carries is not a candidate for anyone to list. It
  *asks*, for hubs; the answers come to it directly, so it holds no multicast
  lock `[GDE-NDS-130]`.

---

## 2. Two decisions made in building it

**`[SPEC-DSC-030]` One responder per machine, and it is the player.** The hub's
machine runs Vipunen's intake too, and two processes cannot share a UDP port
reliably on Windows. So the player answers for the hub, reading the roster from
`mesh/` beside its own listener database and checking that the members' port
answers. The player's identity is the hub's own `mesh/node.key` there, so the
desktop is one node with one fingerprint. Elsewhere it is `node.key` beside the
listener database, which is `/var/lempi` on every appliance and not the overlay
root on any `[IMPL-BOS-185]`. That identity is ECDSA P-256 in PKCS#8 PEM, the
hub's own format (`lempi_player::discovery`).

**`[SPEC-DSC-040]` A hub tells a stranger that it is a hub.** SPEC049 had a
member answer only its own mesh. Answering `hubs` to anyone gives up the mesh's
name and fingerprint on the segment, which is what lets a phone find it. In an
office that is a choice, and the same Settings switch makes it: a hub with it
off is joined only by typing its address.

---

**`[SPEC-DSC-070]` A player joins by invitation, over plain HTTP.** A player
has no screen of its own and no HTTPS client, so the hub invites it over its
web port (`mesh.py invite`, the console's **invite**), and a person compares the
code on the console with the one in the player's Settings. HTTP is safe for
this only because of what the code covers, as Bluetooth's numeric comparison
is safe over an open radio:
- each side derives the code from both keys as *it* received them, and the hub
  commits to its nonce first, so a device in the middle makes the codes differ;
- the invitation carries the mesh key signed by the hub's own key, which the
  code covers, so the mesh key, and every roster it signs after, is
  authenticated by the same comparison.

In order:
1. The player answers with its bare self-signed certificate and a nonce, signed
   by its key.
2. The hub reveals its nonce, and both sides show the code.
3. The player's person confirms in its Settings, signed.
4. The hub's person accepts on the console.
5. Only then does the hub send the roster. The player keeps it only if the
   pinned mesh key signed it and it names the player (`mesh-member.json`
   beside the listener, `lempi_player::membership`).

**`[SPEC-DSC-080]` The hub sends each new roster to its players, and removal
changes the key.** A player keeps the roster version it holds until told.
After an accept or a removal, the hub finds its players by the members'
query and posts them the roster. After a removal it uses the *previous*
discovery key, the one they still hold, since removing a member makes a new
one. A player keeps a roster only if it is newer and signed by its pinned
mesh key. One that no longer names it means it was removed, and it leaves.
The phone is not pushed to, as it listens on nothing: it takes the newest
roster when it checks its channel or syncs.

An invitation runs as its own process, since it waits minutes for two people.
An Accept queued behind it in the console's one-at-a-time jobs was never
reached. Accept, reject and remove are done at once.

**`[SPEC-DSC-090]` A player accepts an invitation, confirms it, or leaves only
while a pairing window is open.** The player's `/mesh/*` routes have no login,
like the rest of its LAN UI, so the code comparison alone did not stop a host on
the network from driving the whole enrolment itself (it can read the code from
`GET /mesh`) `[SecurityReview3 R3]`. So those three changes need a **pairing
window**, opened only by a local, physical act -- a `SIGUSR1` to the player
(`lempi-btctl mesh-pair`, wired to a button, the framebuffer UI, or an operator on
the console). A page on the LAN can neither send that signal nor read that it
happened. The window is five minutes by default, adjustable and clamped to
1--60 minutes, and only adjustable while it is already open. A loopback-only
host (a phone `[REQ-AND-160]`) is inherently local and needs no window. A new
invitation is refused while one is in progress, and `leave` needs the window
too, so detaching a player is a physical act as well; hub-driven removal (a
signed roster that no longer names it) is unaffected.

## 3. What it shows

The console's mesh page, **On this network**, lists the candidates that answer,
by the naming rules of `[SPEC-MTR-105]`, with their address, build and a link to
their page. Enrolling one of them comes with the appliances' step. The phone's
join screen, **Look for hubs on this network**, lists hubs as
"*mesh* — *name* at *address* · hub *fingerprint*"; choosing one fills in the
address, and joining still goes through the code comparison `[SPEC-MTR-130]`.
`tools/discovery.py candidates | hubs` asks the same from a terminal.

*Measured 2026-09-28:*
- the desktop's player answered `hubs` as "GMKTEC", mesh "Home", with the hub's
  key `ddc6 0c85`;
- the Moto G found it as "“Home” — GMKTEC at 192.168.67.95 · hub ddc6 0c85",
  through Windows' firewall;
- the local fleet joined "Home" by invitation, each code the same on the
  console and on the player's own Settings page: smartboardpc 106 355,
  bose 759 347, lp3-wifi 780 573. Roster version 6 then held the hub, the
  phone and the four players; no candidates were left, and all four answered
  the members' query;
- removing smartboardpc (version 7): it left on its own, answered as a
  candidate again, and the new key's members' query no longer found it. The
  other three took version 7. Invited again (578 427), it rejoined, and
  every player held version 8;
- lempi02w joined "Home" by invitation, the console and its own Settings both
  showing 017 830 and naming hub `ddc6 0c85`; roster version 3. Then the proven
  members' query found it at 192.168.67.20, a wrong key got silence, and it
  answered as a candidate no more;
- with ee247d0 deployed, the four players answered as candidates, each with a
  key made at its first start: bose `3398 fb55`, lempi02w ("LempiPiHost")
  `f101 e6e2`, lp3-wifi ("lempiplay3") `7cf0 4ccf`, smartboardpc ("Smart")
  `3509 b81f`.

Two things the fleet taught, both now in the asker:

**`[SPEC-DSC-050]` A query leaves from each of the machine's addresses.**
Windows sends a broadcast to 255.255.255.255 out of one adapter only. The
desktop chose a Hyper-V virtual one (172.19.112.1), so none of the players on
192.168.67.0/24 heard the first queries. Sent from a socket bound to each
local address, the query leaves by every adapter.

**`[SPEC-DSC-060]` A query is repeated, at 0, 0.5 and 1.2 s, under one nonce.**
Wi-Fi delivers broadcasts to a sleeping radio unreliably. lempi02w, a Pi Zero
2 W, answered 5 queries in 10, while the others answered all 10. With the
repeats, and three seconds to answer, all four answered 10 in 10. The phone
repeats its search for hubs the same way. This settles the backoff SPEC049
left open `[SPEC-MTR-910]`: a query is a person's click, so it is repeated
within its three seconds and never re-sent on a schedule.

---

**Traceability:** `[SPEC-DSC-010..090]` · builds `[SPEC-MTR-300..330]` · revises `[SPEC-MTR-310]` for hubs (`[SPEC-DSC-040]`) · the pairing gate answers the follow-up review (R3)
