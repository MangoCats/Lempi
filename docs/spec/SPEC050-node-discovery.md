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

The last three are a hub's only. The members' query of `[SPEC-MTR-310]`,
proven by the discovery key, is not built: today no enrolled member listens.
It comes with the appliances' enrolment.

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

**Traceability:** `[SPEC-DSC-010..060]` · builds `[SPEC-MTR-300..330]` · revises `[SPEC-MTR-310]` for hubs (`[SPEC-DSC-040]`)
