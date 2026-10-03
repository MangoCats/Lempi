# SPEC053: Security Hardening — As Built

**Design Specification — Tier 2 · written 2026-09-28 · the tracked record of the security reviews' findings, the fix for each, and the test that holds it**

Three security reviews were run against the public repository. The review
documents themselves are the maintainer's private working notes and are not
committed (`.gitignore`: `secrets/`). This is their tracked, redacted
counterpart: every finding, what was done, where, the tracked test that pins it,
and its status — so a `[SecurityReview …]` citation in the code resolves to a
document a fresh clone can read. It names no household detail; the network map
finding (N1) is *about* not committing such detail. **An entry for a finding
still open gives no reproduction steps** — it says what is exposed and what the
fix will be, never how to exercise it; the private review keeps those.

> **Related:** [SPEC052](SPEC052-web-access-guard.md) `[SPEC-WAG-010..030]` (the appliance web guard, C1/C2 in full) · [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-090]` (the mesh pairing window, R3) · [SPEC051](SPEC051-trusted-networks.md) (trusted networks, where R4 is designed)

---

## 1. Secrets and disclosure

**`[SPEC-SEC-010]` S1 — no real credentials are committed.** Clean at review;
no change. History carries none either.

**`[SPEC-SEC-020]` S2 — the published intake key is hardened.** `SecurityReview
S2`: the application key is compared with `hmac.compare_digest`, and `intake.py`
refuses to start with the *published* key on any non-loopback bind. Fix:
`tools/intake.py`. Test: `tools/test_intake.py`. Done. The per-device AP-password
idea is not built; `Lempi321` remains the documented default `[SPEC-WIFI-030]`.

**`[SPEC-SEC-030]` N1 / N1a — home-network detail.** `SecurityReview N1`: the
existing detail in the tree is accepted as-is (already public); only *new* leaks
are policed, by the tool at `[SPEC-SEC-130]`. `SecurityReview3 N1a`: the leak
checker's own test used real tokens as fixtures — replaced with fake ones. The
measurement records in `SPEC050`/`discovery.py` that N1a also lists are
grandfathered by the leave-as-is decision. Test: `tools/test_check_fleet_leaks.py`.

## 2. The console (the hub's Vipunen console)

**`[SPEC-SEC-040]` C1 — a web page can no longer drive the console.**
`SecurityReview C1`: `console.py` binds loopback but did not check `Origin` or
`Host`, so a visited page could POST to it. Fix: `Handler._same_origin` refuses a
foreign `Origin` or `Host`. Test: `tools/test_console_csrf.py`. Done. The two
ssh-injection links of C1 (`SecurityReview C4` neighbours) are **deferred to the
ssh removal** `[SPEC-SEC-120]`, not patched; the same-origin check is the barrier
until then.

**`[SPEC-SEC-050]` R2 / R2a — the console's reads are guarded too.**
`SecurityReview2 R2`: `do_GET` now runs the same check, so DNS rebinding cannot
read the library, peers or audio; `/api/handoff/ensure` was made a POST.
`SecurityReview3 R2a`: three GETs still run ssh to the *stored* peers
(`/api/profile/*/remote`, `/flag`, `/api/peers/reachable`) and are named as known
exceptions that go with the ssh removal. Test: `tools/test_console_csrf.py`.

## 3. The appliance web server

**`[SPEC-SEC-060]` C2 — cross-site and DNS-rebinding requests refused.**
`SecurityReview C2` and its `SecurityReview2 C2 follow-up`: `access::origin_guard`
requires `Origin` to equal `Host` and `Host` to be one the node answers to;
bare IPs, dotless LAN names and reserved local-use suffixes are accepted, a
registerable domain is not. Fix: `player/src/web/access.rs`. Test:
`player/src/web/mod.rs` (`the_origin_guard_refuses_cross_site_and_rebinding`).
Full behaviour and the two exterior-access features left for later are in
[SPEC052](SPEC052-web-access-guard.md).

## 4. Wi-Fi credentials

**`[SPEC-SEC-070]` C3 — the Wi-Fi password leaves no URL, argv or `nmcli` line.**
`SecurityReview C3`: it travels in a POST body, then to `lempi-btctl` on stdin,
and into the root-owned NetworkManager keyfile written by shell builtins — never
a process argument. Fix: `player/src/web/wifi.rs`, `player/src/bluetooth.rs`,
`appliance/bluetooth/lempi-btctl`. Test: `LempiPi/tests/cases-wifi.sh`, verified on real
NetworkManager. Done.

**`[SPEC-SEC-080]` R1 / R1a — the key is validated and the AP swap is atomic.**
`SecurityReview2 R1`: `valid_psk` refuses a non-printable, out-of-range or
backslash key before anything is written (closing the keyfile-injection the C3
writer opened), the keyfile is found by UUID, and a failure deletes the
half-made profile. `SecurityReview3 R1a`: `ap-start` builds the new AP under a
temporary name and swaps it in only once its key is set, so a failure never
destroys the `lempi-ap` the revert relies on. Fix: `appliance/bluetooth/lempi-btctl`. Test:
`LempiPi/tests/cases-wifi.sh`. Done.

**`[SPEC-SEC-090]` C5 — only Wi-Fi connections can be forgotten or toggled.**
`SecurityReview C5`: `wifi-forget`/`wifi-autoconnect` now require an
`802-11-wireless` connection, so the root helper cannot delete a wired or VPN
profile. Fix: `appliance/bluetooth/lempi-btctl`. Test: `LempiPi/tests/cases-wifi.sh`. Done.
The Android cleartext and bind-address items are recorded there as accepted.

## 5. Mesh trust

**`[SPEC-SEC-100]` R3 — mesh enrolment and leave need a pairing window.**
`SecurityReview2 R3` added a code/fingerprint check; `SecurityReview3 R3` found
that alone insufficient (a LAN host can read both), so accepting an invitation,
confirming, and leaving now require a pairing window opened only by a local
button (SIGUSR1, `lempi-btctl mesh-pair`), and a new invite is refused while one
is in progress. Fix: `player/src/pairing.rs`, `membership.rs`,
`web/settings.rs`. Test: `player/src/pairing.rs`, `player/src/membership.rs`.
Done. Full behaviour in [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-090]`.

**`[SPEC-SEC-110]` R4 — UDP discovery disclosure and roaming.**
`SecurityReview2 R4` / review 3: **deferred** to the trusted-networks feature,
designed in [SPEC051](SPEC051-trusted-networks.md) and not yet built. Resolved
already: the phone pins the hub's key at enrolment and sends to no discovered
hub.

## 6. ssh and sudo across the fleet

**`[SPEC-SEC-120]` C4 — sudo and cross-node keys.** `SecurityReview C4`: blanket
passwordless sudo on the Raspberry Pi nodes is **accepted** (the platform's
default for a purpose-built device). The passphrase-less cross-node ssh keys, and
the ssh injection of `[SPEC-SEC-040]`, are **deferred to removing ssh from the
mesh data plane** (`[SPEC-MTR-220]`), which deletes the whole chain rather than
patching it. The general-purpose x86 nodes neither need nor are given open sudo:
`tools/remote_apply.py` stops the player around a DB write without sudo where a
service does not require it. Test: `tools/test_remote_apply.py`.

## 7. Tooling and governance

**`[SPEC-SEC-130]` N1 / R5 — the fleet-leak check.** `SecurityReview N1` §4.4 /
`SecurityReview2 R5`: `tools/check_fleet_leaks.py` scans a commit's added lines
against a gitignored denylist plus generic patterns (private IPv4, MACs, personal
paths, `user@host:`), runs in the pre-commit hook with its own escape
(`LEMPI_SKIP_LEAKS`), and in CI generic-only. Fix: `tools/check_fleet_leaks.py`,
`.githooks/pre-commit`, `tools/check_docs.py` (skips `secrets/`). Test:
`tools/test_check_fleet_leaks.py`. Done; a strict tree-wide CI gate awaits the
redaction pass, by stated plan.

---

## Deferred or accepted, carried forward

| Item | Decision |
|---|---|
| C1 ssh links 2–3; C4 cross-node keys | Deferred to the ssh removal `[SPEC-MTR-220]` |
| C4 blanket sudo on Pi nodes | Accepted (vendor default) |
| R4 discovery | Deferred to [SPEC051](SPEC051-trusted-networks.md) |
| N1 existing tree; git history | Left as-is; already-published values treated as public |
| S2 per-device AP password; C5 Android cleartext | Accepted / not built |
| Exterior web access (private-IP restriction; domain allow-list) | Recorded in [SPEC052](SPEC052-web-access-guard.md) `[SPEC-WAG-900..910]` |

**Traceability:** `[SPEC-SEC-010..130]` · records the fixes in `player/src/web/`, `tools/`, `appliance/bluetooth/lempi-btctl` and their tracked tests · fuller design in [SPEC050](SPEC050-node-discovery.md), [SPEC052](SPEC052-web-access-guard.md), [SPEC051](SPEC051-trusted-networks.md)
