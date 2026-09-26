# GUIDE035: Node Discovery

**Development Guidance — a future feature, recorded so it is not lost: how Lempi and Vipunen nodes could find each other on a LAN**

Recorded 2026-09-25 from the maintainer's description, **as an idea for later and explicitly not part of the current path**. Nothing here is scheduled, and nothing here changes a requirement today: every node is still pointed at another by host name or IP address `[REQ-AND-286]` `[SPEC-ECHO-010]`. What follows is the maintainer's design as stated, then what this repository already says that such work would have to meet.

> **Related:** [SPEC044](spec/SPEC044-echo-mode-control.md) (following; `[SPEC-ECHO-100]`) · [SPEC034](spec/SPEC034-wifi-configuration.md) (`[SPEC-WIFI-030]`) · [SPEC035](spec/SPEC035-mesh-library-sync.md) (mesh sync) · [REQ007](spec/REQ007-android.md) (`[REQ-AND-286..289]`)

---

## 1. The idea, as the maintainer described it

**`[GDE-NDS-010]` A discovery protocol of Lempi's own, on port 13492** — using, based on, or similar to Bonjour. A node that has just joined a network sends a query to the local network, and backs off its repeats once it has connected to something.

**`[GDE-NDS-020]` Answering is on by default and can be switched off.** A user can configure any node not to respond to discovery requests at all.

**`[GDE-NDS-030]` Who gets an answer follows the key in use** — the same two keys as `[REQ-AND-287]`:

- Where a **user-unique ID key** has been established, a node by default answers only a request that securely proves possession of that key.
- It may instead be configured to answer requests that securely prove possession of the **default Lempi / Vipunen application ID keys**.
- The request proves possession of the key, rather than presenting the key itself. A broadcast is heard by everything on the segment, so a key sent in the clear would be given to every listener on it.

**`[GDE-NDS-040]` A found node is shown by every name it has**: its network name where known, its host name, its IP address, and a node identifier the user configures. No single one of these is reliable across renames, DHCP leases and networks; together they let a person tell two nodes apart.

**`[GDE-NDS-050]` Found nodes feed two existing choices**: picking the node a follower follows `[SPEC-ECHO-010]`, and picking the Vipunen node a Lempi node synchronises with `[SPEC035]`, `[REQ-AND-285]`.

## 2. What already stands in the way, and what the idea answers

**`[GDE-NDS-100]` `[SPEC-ECHO-100]` said discovery "may not be wanted"**, because mDNS browsing would "introduce a way for the wrong node to be selected silently". The idea answers that concern twice. A node is only offered to a person, who chooses it `[GDE-NDS-050]`; nothing connects to it automatically. And that person sees enough names to tell two nodes apart `[GDE-NDS-040]`. When this is taken up, `[SPEC-ECHO-100]` should be revised in the same change.

**`[GDE-NDS-110]` mDNS is deliberately off on the appliances** `[SPEC-WIFI-030]` (`avahi-daemon` is disabled in IMPL001), and that decision also noted Bonjour's patchy support on Android. A protocol on its own port, rather than mDNS on 5353, leaves both points standing: it needs neither avahi nor Android's Bonjour.

**`[GDE-NDS-120]` Port 13492 is free as a default, but appears in one example.** The player's web UI defaults to 5720. `fleet-example/hand-run/run.sh` uses `--port 13492` purely as an illustrative override. That is a TCP web port, while a broadcast query would be UDP, so the two would not collide. They could still confuse a reader, so the example should change to another port when this is built.

**`[GDE-NDS-130]` A phone must ask to hear broadcasts.** Android drops multicast and broadcast traffic to an app unless the app holds a `WifiManager.MulticastLock`, which also costs battery. A phone that sends queries but does not answer them needs the lock only while it is searching.

## 3. Open, for when it is taken up

1. **`[GDE-NDS-900]` What "securely proving possession" is**: a challenge and response keyed by the ID key, or a signed timestamp. It must prove the key without revealing it to a passive listener, and without letting a recorded proof be replayed later.
2. **`[GDE-NDS-910]` The backoff schedule** `[GDE-NDS-010]`: its intervals, its cap, and what "connects" means — a follow, a sync, or any answer at all.
3. **`[GDE-NDS-920]` Whether a node that has discovery switched off can still be found by a person** who types its name. It should be; `[GDE-NDS-020]` concerns answering broadcasts, not being reachable.

---

**Traceability:** `[GDE-NDS-010..920]` · a future idea, not a requirement · touches `[SPEC-ECHO-100]`, `[SPEC-WIFI-030]`, `[REQ-AND-286]`, `[REQ-AND-287]`
