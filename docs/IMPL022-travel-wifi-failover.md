# IMPL022: Travel Wi-Fi Failover and Hotspot Pivot Implementation Plan

**Implementation Specification — Tier 3 · written 2026-10-07, updated 2026-10-07 · step-by-step implementation for autonomous access point failover and in-browser hotspot pivot**

This document details the concrete implementation plan for [SPEC061](spec/SPEC061-travel-wifi-failover.md), providing autonomous failover to `lempi-ap`, first-class offline operation (Approach 1), and a safe in-browser pivot to mobile hotspots or venue networks (Approach 3D).

> **Related:** [SPEC061](spec/SPEC061-travel-wifi-failover.md) (travel Wi-Fi failover specification) · [SPEC034](spec/SPEC034-wifi-configuration.md) (Wi-Fi config and confirm-or-revert) · [SPEC050](spec/SPEC050-node-discovery.md) (UDP discovery) · [SPEC052](spec/SPEC052-web-access-guard.md) (host access guards) · [PI026](../LempiPi/PI026-startup-preflight.md) (startup preflight) · [BOSE006](../BosePi/BOSE006-what-lands-where.md) (partition layout)

---

## 1. Architectural Overview & Component Seams

The design touches five components without altering core playback code:
1. **Appliance Watchdog (`/usr/local/bin/lempi-wifi-failover`)**: Periodic keeper evaluating Wi-Fi connectivity and activating `lempi-ap` via `lempi-btctl ap-failover` after boot grace or link loss.
2. **Systemd Automation (`lempi-wifi-failover.service`, `lempi-wifi-failover.timer`)**: Timer-driven oneshot unit located on Partition A (read-only system root), persisted across overlay layers via `install-config.sh` and recorded in `lempi-preflight` `[PI-PRE-020]`.
3. **Privileged Helper (`appliance/bluetooth/lempi-btctl`)**:
   - `ap-failover`: Brings up `lempi-ap` standing without scheduling a revert timer.
   - `wifi-pending`: Queries `/run/lempi/wifi_pending` and active revert timers.
   - `wifi-scan`: Suppresses active off-channel rescanning when audio is streaming (checked via `lempi_routed()`).
4. **Revert Script (`LempiPi/lempi-wifi-revert`)**: Restores previous connections without setting `autoconnect yes` on `lempi-ap`, and cleans up pending state.
5. **Backend & Skin UI (`player/`)**: Exposes `GET /wifi/pending`, restores countdowns across page reloads, and renders offline guidance and the single-phone hotspot pivot wizard.

---

## 2. Appliance Failover Watchdog & Helpers

**`[IMPL-WFO-010]` Watchdog script (`lempi-wifi-failover.sh`).**
Installed to `/usr/local/bin/lempi-wifi-failover`:
- **Radio Block Guard**: Checks `/sys/class/rfkill/` for WLAN state. If Wi-Fi is soft-blocked (`radio wlan off`), exits immediately (`0`).
- **In-Flight Revert Guard**: Queries `systemctl list-timers 'lempi-revert-*'`. If an active revert timer exists, exits cleanly (`0`) to prevent stomping on a user switch.
- **Boot Grace Check**: Queries `/proc/uptime`. If uptime is $< 45$ seconds, exits immediately (`0`).
- **Connection Check**: If `wlan0` is connected, active profile is `lempi-ap`, or NetworkManager is in `connecting` state, removes `/run/lempi/wifi_disconnected_since` (if present) and exits (`0`).
- **Debounce Tracker**: On first detected drop, stamps the current epoch into `/run/lempi/wifi_disconnected_since`.
- **Autonomous Bringup**: If disconnected for $\ge 30$ seconds, executes `/usr/local/bin/lempi-btctl ap-failover` and removes the debounce file.

**`[IMPL-WFO-012]` Privileged verbs in `lempi-btctl`.**
- `ap-failover`: Writes `/etc/NetworkManager/dnsmasq-shared.d/lempi.conf`, activates `lempi-ap`, and sets ACT LED to wifi mode. Does NOT schedule a revert timer.
- `wifi-pending`: Inspects `/run/lempi/wifi_pending` (format: `change_id=<id>\nexpires_epoch=<ts>\nssid=<name>`). If file exists and timer is active, computes remaining seconds and returns JSON `{"ok":true,"pending":true,"change_id":"...","remaining_seconds":N,"ssid":"..."}`. Otherwise returns `{"ok":true,"pending":false}`.

**`[IMPL-WFO-015]` Revert safety guard (`LempiPi/lempi-wifi-revert`).**
In `LempiPi/lempi-wifi-revert`:
- When restoring `$PREV`, guards `nmcli connection modify "$PREV" autoconnect yes` so that it is never applied if `$PREV` is `"lempi-ap"` `[SPEC-WFO-080]`.
- Removes `/run/lempi/wifi_pending` upon revert completion.

**`[IMPL-WFO-020]` Systemd service, timer units, and partition durability.**
- Units placed in `/etc/systemd/system/` (Partition A, read-only system root):
  `lempi-wifi-failover.service` (`Type=oneshot`, `User=root`) and `lempi-wifi-failover.timer` (`OnBootSec=45s`, `OnUnitActiveSec=20s`, `AccuracySec=5s`).
- **Overlay Root Durability**: Units and helper scripts are installed to both the active filesystem and `/media/root-ro` via `build/install-config.sh` and recorded in `LempiPi/setup-appliance.sh` `[IMPL-BOS-185]`.
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
If the user does not tap the pivot button, the banner remains passive or collapsible. All playback controls operate unconditionally from local storage.

---

## 4. In-Browser Hotspot Pivot Flow (Approach 3D)

**`[IMPL-WFO-050]` Scan and manual SSID pivot selection.**
- When the user taps `[Pivot to Hotspot / Wi-Fi]`, `#wifi-scan-list` and `#wifi-connect-form` are revealed.
- Tapping `[Scan for networks]` invokes `GET /wifi/scan` on demand.
- **Single-Phone Hotspot Entry**: `#wifi-connect-form` provides an editable `<input id="wifi-connect-ssid">` (or "Enter manually" toggle), enabling the user to enter their hotspot SSID and passphrase before enabling personal hotspot on their device.

**`[IMPL-WFO-060]` Pivot handover, reload persistence, and safety countdown.**
- On submitting credentials (`POST /wifi/connect`), `lempi-btctl` writes `/run/lempi/wifi_pending` and schedules the revert timer.
- The UI displays `#wifi-pivot-modal` with step-by-step guidance:
  - *"Connecting Lempi to [SSID]... If using a single phone, enable Personal Hotspot now. Then reconnect your phone and open `http://<ip>` (iOS: `http://172.20.10.x`, or check Android Hotspot Connected Devices) or use the Lempi mobile app. Reverting in [minutes] minutes if unconfirmed."*
- **Reload Persistence**: On any page load or refresh, `skin.js` invokes `GET /wifi/pending`. If active, it restores the `#wifi-confirm` banner with the live countdown.
- Tapping *"Yes, keep it"* calls `POST /wifi/confirm/:change_id`, which removes `/run/lempi/wifi_pending` and cancels the revert timer.

---

## 5. RF Coexistence & Audio Stream Protection

**`[IMPL-WFO-070]` Scanning suppression during active playback.**
In `appliance/bluetooth/lempi-btctl` (`wifi-scan`):
- Checks active audio sink via `lempi_routed()` (calling `http://localhost:5720/audio/sink`).
- If audio is actively streaming to a Bluetooth sink, passes `--rescan no` to return cached results, protecting the 2.4 GHz radio from channel-hopping contention `[PI012]`.

---

## 6. Verification & Test Plan

**`[IMPL-WFO-080]` Automated test suite additions.**
1. **Appliance Shell Tests ([`LempiPi/tests/cases-wifi.sh`](../LempiPi/tests/cases-wifi.sh)):**
   - Test watchdog grace period, rfkill guard, in-flight revert guard, and debounce threshold.
   - Test `ap-failover`: verifies standing bringup without scheduling a revert timer.
   - Test `wifi-pending`: verifies parsing of `/run/lempi/wifi_pending` with active/expired timers.
   - Test revert script: verifies `lempi-wifi-revert lempi-ap target` does NOT set `autoconnect yes` on `lempi-ap`, and removes `/run/lempi/wifi_pending`.
2. **Rust Backend Tests ([`player/src/web/wifi.rs`](../player/src/web/wifi.rs)):**
   - Test route `GET /wifi/pending`.
   - Verify `GET /wifi/known` correctly flags `active: true` for `lempi-ap`.
3. **Hygiene & Documentation:**
   - Execute `python tools/check_docs.py --strict` (0 errors, file lengths $< 250$ lines).
   - Execute `python tools/check_fleet_leaks.py --staged --strict`.
