# SPEC052: Web Access Guard — As Built

**Design Specification — Tier 2 · written 2026-09-28 · how the player decides which HTTP requests it will answer, and what is deliberately left for later**

The player's web server is reached two ways, and guards each differently. On a
phone it backs the app's own WebView and asks for a per-launch secret; on an
appliance it is meant to be reached from the household LAN and asks for nothing.
This records the guard as built (the fixes of the first two security reviews),
and the exterior-access controls considered and **not** built.

> **Related:** [REQ007](REQ007-android.md) `[REQ-AND-160]` (the phone's launch key) · [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-090]` (the mesh pairing window, a further gate on `/mesh/*`) · [SPEC034](SPEC034-wifi-configuration.md) (the AP, open by design) · `player/src/web/access.rs`

---

## 1. As built

**`[SPEC-WAG-010]` The phone: a per-launch secret, loopback only.** The phone's
player binds loopback and refuses any request without the launch key
`[REQ-AND-160]`. Nothing below relaxes that.

**`[SPEC-WAG-020]` The appliance: open on the LAN, guarded against cross-site
and rebinding.** The appliance binds every interface and requires no login --
being reachable from the LAN is the design. What it refuses is a request driven
by a page the household merely *visited*, and a DNS-rebinding page:
- **Origin equals Host.** A browser sets `Origin` on every state-changing
  request; a cross-site one carries a foreign authority, refused. A page load
  or same-origin fetch (`Origin` absent, or equal to `Host`) passes.
- **Host is one we answer to.** This defeats DNS rebinding, whose page reaches
  the node under the attacker's own name. Accepted: `localhost`, `127.0.0.1`,
  `::1`, `lempi`/`lempi.lan`, this node's hostname and `<hostname>.local`; **any
  bare IP literal**; a **single-label** (dotless) name; and a name under a
  reserved **local-use suffix** (`.local`, `.lan`, `.home`, `.internal`,
  `.intranet`, `.corp`, `.home.arpa`). A *publicly registerable* domain -- one
  with a real TLD, `evil.com` -- is refused, which is the whole point: only such
  a name can be pointed at the node from off the LAN for rebinding. Of those
  suffixes, `.local` and `.home.arpa` are reserved by RFC and `.internal` by
  ICANN; `.lan`, `.home`, `.intranet` and `.corp` are only *undelegated* --
  conventional, not promised. If a new-gTLD round ever lists one, it leaves
  this list `[SecurityReview4]`.

**`[SPEC-WAG-030]` The guard is not a network boundary.** It stops the
cross-site and rebinding vectors, not a network operator who deliberately
exposes the node. A DMZ or port-forward to the web port is *permitted* by the
guard -- a bare IP `Host` (the WAN address a client types) is a valid IP literal
-- so it puts the login-free UI on the open internet. That is a network-hygiene
decision, not something this guard defends; the controls that do are: do not
forward the port, front it with a reverse proxy that adds auth and rewrites
`Host`, or (a phone-style) launch key. Cross-site POSTs to the exposed port are
still refused, and `/mesh/*` stays behind the pairing window `[SPEC-DSC-090]`.

---

## 2. Considered, not built -- exterior access

Both would let, or stop, the node being reached from outside the LAN in ways the
guard above does not. Neither is needed today; recorded so the reasoning is not
re-derived.

**`[SPEC-WAG-900]` Restrict IP-literal Hosts to private ranges.** Accept a bare
IP `Host` only in the private / loopback / link-local ranges (192.168/8,
10/8, 172.16-31, 127.0.0.1, ::1, fe80::/10), refusing a public one. A browser
navigating to `http://<wan-ip>:<port>/` through a naive port-forward would then
be refused, since a browser sets `Host` to that public IP and scripts cannot
forge it. It is defence-in-depth against an accidental DMZ, not a full control:
a non-browser client (`curl -H "Host: 192.168.x.y"`) can still spoof a private
`Host`, and it does not add the login the exposed UI lacks. Left out because the
real fix for exposure is network hygiene or a fronting proxy.

**`[SPEC-WAG-910]` An operator-defined list of permitted exterior domains.** A
per-node setting naming public names the guard should also accept (a DuckDNS
name, a reverse-proxy hostname), so deliberate remote access works without the
proxy rewriting `Host`. Left out because it re-opens the login-free UI to the
internet, so it would ship only alongside a required launch key or equivalent
auth, and with a plain warning at the point of setting it.

---

**Traceability:** `[SPEC-WAG-010..030]`, `[SPEC-WAG-900..910]` · records `player/src/web/access.rs` (the first two reviews' C1/C2 fixes and their follow-ups) · further `/mesh/*` gate in [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-090]`
