# IMPL022: Travel Wi-Fi Failover and Hotspot Pivot Implementation Plan

**Implementation Specification — Tier 3 · written 2026-10-07, updated 2026-10-07 · step-by-step implementation for autonomous access point failover and in-browser hotspot pivot**

This document details the concrete implementation plan for [SPEC061](spec/SPEC061-travel-wifi-failover.md), providing autonomous failover to `lempi-ap`, first-class offline operation (Approach 1), and a safe in-browser pivot to mobile hotspots or venue networks (Approach 3D).

> **Related:** [SPEC061](spec/SPEC061-travel-wifi-failover.md) (travel Wi-Fi failover specification) · [SPEC034](spec/SPEC034-wifi-configuration.md) (Wi-Fi config and confirm-or-revert) · [SPEC050](spec/SPEC050-node-discovery.md) (UDP discovery) · [SPEC052](spec/SPEC052-web-access-guard.md) (host access guards) · [PI026](../LempiPi/PI026-startup-preflight.md) (startup preflight) · [BOSE006](../BosePi/BOSE006-what-lands-where.md) (partition layout)

---

## 1. Architectural Overview & Component Seams

The design touches five components without altering core playback code:
1. **Appliance Watchdog (`/usr/local/bin/lempi-wifi-failover`)**: Periodic keeper evaluating Wi-Fi connectivity and activating `lempi-ap` after boot grace or link loss, gated against software-blocked radios and active revert timers.
2. **Systemd Automation (`lempi-wifi-failover.service`, `lempi-wifi-failover.timer`)**: Timer-driven oneshot unit located on Partition A (read-only system root), persisted across overlay layers via `install-config.sh` and recorded in `lempi-preflight` `[PI-PRE-020]`.
3. **Privileged Helper & Audio Guard (`appliance/bluetooth/lempi-btctl`)**: Suppresses active `--rescan yes` off-channel radio hops during active Bluetooth playback, and provides direct autonomous AP activation without scheduling user-revert timers.
4. **Revert Script (`LempiPi/lempi-wifi-revert`)**: Restores previous connections without corrupting `lempi-ap`'s autoconnect policy.
5. **Lempi Skin UI (`player/src/web/skins/lempi/`)**: Offline mode status banner, manual and scanned SSID entry forms, and the interactive hotspot pivot workflow.

---

## 2. Appliance Failover Watchdog & Helpers

**`[IMPL-WFO-010]` Watchdog script (`lempi-wifi-failover.sh`).**
Installed to `/usr/local/bin/lempi-wifi-failover`:
- **Radio Block Guard**: Checks `/sys/class/rfkill/` for WLAN state. If Wi-Fi is soft-blocked by operator intent (`radio wlan off`), exits immediately (`0`).
- **In-Flight Revert Guard**: Queries `systemctl list-timers 'lempi-revert-*'`. If an active revert timer exists, exits cleanly (`0`) to prevent stomping on a user-directed network switch.
- **Boot Grace Check**: Queries `/proc/uptime`. If uptime is $< 45$ seconds, exits immediately (`0`) to allow initial association with known home Wi-Fi.
- **Connection Check**: If `wlan0` is connected, or active profile is `lempi-ap`, or NetworkManager is actively in `connecting` state, removes `/run/lempi/wifi_disconnected_since` (if present) and exits (`0`).
- **Debounce Tracker**: Ensures directory `/run/lempi` exists. On first detected disconnection, stamps the current epoch time into `/run/lempi/wifi_disconnected_since`.
- **Autonomous Bringup**: If disconnected for $\ge 30$ seconds, brings up `lempi-ap` directly (`nmcli connection up lempi-ap` or `lempi-btctl ap-failover`) without scheduling a 5-minute confirm-or-revert timer, and removes the debounce file.

**`[IMPL-WFO-015]` Revert safety guard (`LempiPi/lempi-wifi-revert`).**
In `LempiPi/lempi-wifi-revert`:
- When restoring `$PREV`, guards `nmcli connection modify "$PREV" autoconnect yes` so that it is never applied if `$PREV` is `"lempi-ap"`. `lempi-ap` must remain `autoconnect no` to protect boot-time infrastructure association `[SPEC-WFO-080]`.

**`[IMPL-WFO-020]` Systemd service, timer units, and partition durability.**
- Units placed in `/etc/systemd/system/` (Partition A, read-only system root):
  `lempi-wifi-failover.service` (`Type=oneshot`, `User=root`) and `lempi-wifi-failover.timer` (`OnBootSec=45s`, `OnUnitActiveSec=20s`, `AccuracySec=5s`).
- **Overlay Root Durability**: Units and helper scripts must be installed to both the active filesystem and the durable lower layer (`/media/root-ro`) via `build/install-config.sh` and recorded in `LempiPi/setup-appliance.sh` `[IMPL-BOS-185]`. They MUST NOT be re-mapped to Partition C (state partition), which is reserved for variable listener state and logs `[PI-PART-020]`.
- **Startup Preflight (`appliance/lempi-preflight`)**: Reports version and presence of `lempi-wifi-failover` during boot preflight `[PI-PRE-020]`.

---

## 3. Standalone Offline Operation (Approach 1)

**`[IMPL-WFO-030]` Offline mode indicator & guidance banner.**
In `player/src/web/skins/lempi/`:
- [`skin.html`](../player/src/web/skins/lempi/skin.html): Adds `#offline-banner` at the top of the settings and player panels:
  - Header: *"Offline Player (Direct Playback via Lempi AP)"*.
  - Advisory text: *"Operating standalone from internal storage. If controls fail to open or pause on lock, temporarily disable mobile data or select 'Stay connected without internet'. Access directly at http://10.42.0.1 or http://lempi on port 80."*
  - Buttons: `[Pivot to Hotspot / Wi-Fi]` and `[Dismiss]`.
- [`skin.js`](../player/src/web/skins/lempi/skin.js): Checks `GET /wifi/known`. If the active connection is `lempi-ap`, unhides `#offline-banner`.
- [`skin.css`](../player/src/web/skins/lempi/skin.css): Styles the banner with clear, non-intrusive accent colors.

**`[IMPL-WFO-040]` Permanent offline capability.**
If the user does not tap the pivot button, the banner remains passive or collapsible. All player features (playback, queue, volume, DAC/Bluetooth routing) function unconditionally without internet connectivity.

---

## 4. In-Browser Hotspot Pivot Flow (Approach 3D)

**`[IMPL-WFO-050]` Scan and manual SSID pivot selection.**
- When the user taps `[Pivot to Hotspot / Wi-Fi]`, `#wifi-scan-list` and `#wifi-connect-form` are revealed.
- Tapping `[Scan for networks]` invokes `GET /wifi/scan` on demand.
- **Single-Phone Hotspot Entry**: Because a single smartphone cannot broadcast a Wi-Fi hotspot while connected to `lempi-ap`, `#wifi-connect-form` provides an editable `<input id="wifi-connect-ssid">` (or "Enter manually" toggle), enabling the user to enter their hotspot SSID and passphrase before enabling personal hotspot on their device.

**`[IMPL-WFO-060]` Pivot handover & safety countdown modal.**
- On submitting credentials (`POST /wifi/connect`), the response yields `{change_id, minutes, ssid}`.
- The UI displays `#wifi-pivot-modal`:
  - Title: *"Connecting Lempi to [SSID]..."*
  - Body: *"Lempi is joining your hotspot. If using a single phone, enable Personal Hotspot now. Then reconnect your phone and open `http://<ip>` (iOS: `http://172.20.10.x`, or check Android Hotspot Connected Devices) or use the Lempi mobile app. If unreachable within [minutes] minutes, Lempi automatically reverts to 'Lempi' Wi-Fi."*
- When the user reconnects on the new network, tapping *"Yes, keep it"* (`POST /wifi/confirm/:change_id`) disarms the revert timer.
- If the venue network enforces client isolation (common in hotels/cafés), inter-device traffic is blocked; the 3-minute revert timer safely restores `lempi-ap`.

---

## 5. RF Coexistence & Audio Stream Protection

**`[IMPL-WFO-070]` Scanning suppression during active playback.**
In `appliance/bluetooth/lempi-btctl` (`wifi-scan`):
- Before invoking `nmcli dev wifi list --rescan yes`, inspect active audio sinks (via `wpctl status`).
- If audio is actively streaming to a Bluetooth output, pass `--rescan no` to return cached results, protecting the 2.4 GHz radio from channel-hopping contention `[PI012]`.

---

## 6. Verification & Test Plan

**`[IMPL-WFO-080]` Automated test suite additions.**
1. **Appliance Shell Tests ([`LempiPi/tests/cases-wifi.sh`](../LempiPi/tests/cases-wifi.sh)):**
   - Test watchdog grace period: verifies no failover when uptime $< 45$s.
   - Test watchdog active revert guard: verifies no failover when `lempi-revert-*.timer` is active.
   - Test watchdog rfkill guard: verifies no failover when `wlan` is soft-blocked.
   - Test watchdog debounce threshold: verifies no failover until disconnection persists $\ge 30$s.
   - Test autonomous AP bringup: verifies `lempi-ap` is brought up without scheduling a revert timer.
   - Test revert script: verifies `lempi-wifi-revert lempi-ap target` does NOT set `autoconnect yes` on `lempi-ap`.
2. **Rust Backend Tests ([`player/src/web/wifi.rs`](../player/src/web/wifi.rs)):**
   - Verify `GET /wifi/known` correctly flags `active: true` for `lempi-ap`.
   - Verify confirm-or-revert cancel call `POST /wifi/confirm/:change_id`.
3. **Hygiene & Documentation:**
   - Execute `python tools/check_docs.py --strict` (0 errors, 0 duplicate definitions in SPEC061, file lengths $< 250$ lines).
   - Execute `python tools/check_fleet_leaks.py --staged --strict`.
