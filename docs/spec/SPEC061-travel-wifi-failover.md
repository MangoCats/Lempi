# SPEC061: Travel Wi-Fi Failover and Hotspot Pivot

**Design Specification — Tier 2 · written 2026-10-07, updated 2026-10-07 · built 2026-10-07 (`88e249a`) · autonomous off-infrastructure access point failover, standalone offline operation, and in-browser hotspot pivot**

When a Lempi appliance travels outside its home network, it loses infrastructure Wi-Fi association. This specification defines autonomous failover to the local access point (`lempi-ap`), establishes first-class standalone offline operation (Approach 1), and provides an in-browser pivot mechanism (Approach 3D) to transfer the appliance onto a mobile hotspot or venue network with confirm-or-revert safety.

> **Related:** [SPEC034](SPEC034-wifi-configuration.md) `[SPEC-WIFI-010..050]` (Wi-Fi config, confirm-or-revert, port 80 listener) · [SPEC050](SPEC050-node-discovery.md) `[SPEC-DSC-010..020]` (UDP node discovery) · [SPEC051](SPEC051-trusted-networks.md) `[SPEC-TN-020]` (network trust boundaries) · [SPEC052](SPEC052-web-access-guard.md) `[SPEC-WAG-020]` (host allowance on LAN/AP) · [REQ003](REQ003-audio-playback.md) `[REQ-AUD-150]` (Bluetooth audio coexistence) · [IMPL022](../IMPL022-travel-wifi-failover.md) (implementation plan)

---

## 1. Requirements

**`[REQ-WFO-010]` Autonomous failover to access point.**
When the appliance boots without known Wi-Fi in range, or loses active Wi-Fi connectivity for more than a debounce threshold, it MUST automatically activate its internal access point (`lempi-ap`) without requiring manual console interaction. Failover bringup MUST be standing and MUST NOT schedule an automatic revert timer.

**`[REQ-WFO-020]` Standalone offline operation (Approach 1).**
A listener joining `lempi-ap` MUST be able to operate the player entirely offline. Full playback, catalog browsing, volume adjustments, and queue management MUST succeed with zero internet access, zero external app installation, and zero obligation to pivot.

**`[REQ-WFO-030]` In-browser hotspot pivot (Approach 3D).**
When operating in AP mode, the web interface MUST provide an accessible pivot workflow enabling the user to configure and connect to a mobile hotspot or venue Wi-Fi, supporting both scanned networks and manual SSID entry for single-phone hotspot scenarios.

**`[REQ-WFO-040]` Confirm-or-revert connection safety.**
Every pivot attempt MUST be protected by an automatic OS-level timer `[SPEC-WIFI-010]`. Pending confirmation state MUST persist across browser disconnections and page reloads. If unconfirmed within the timeout, the player MUST revert to `lempi-ap` without poisoning `lempi-ap`'s autoconnect settings.

**`[REQ-WFO-050]` RF coexistence and audio stream preservation.**
Wi-Fi scanning and mode switching MUST NEVER run periodically in the background while Bluetooth A2DP audio is streaming, preventing buffer underruns and acoustic stutter `[PI012]`.

---

## 2. Autonomous Failover Timing & State Machine

**`[SPEC-WFO-010]` Disconnection detection and debounce thresholds.**
The failover keeper (`lempi-wifi-failover`) monitors NetworkManager connection state via periodic evaluation:
1. **Radio State Guard**: If Wi-Fi is soft-blocked via `rfkill` (`radio wlan off`), the keeper exits immediately (`0`) to honor operator intent.
2. **In-Flight Revert Guard**: If an active `lempi-revert-*.timer` is running, a user-directed network switch is underway; the keeper exits immediately (`0`) and defers to the revert timer.
3. **Boot Grace Period (`45s`)**: On system boot, the keeper pauses 45 seconds before evaluating connectivity, allowing DHCP negotiation and initial association with known autoconnect networks to complete.
4. **Link-Loss Debounce (`30s`)**: If an active Wi-Fi link drops during operation, the keeper records the timestamp in `/run/lempi/wifi_disconnected_since` and waits 30 seconds before intervening.
5. **Connection Negotiation Guard**: If `wlan0` is actively negotiating (`connecting` state), the keeper exits cleanly (`0`).
6. **Autonomous Bringup**: If disconnected for $\ge 30$ seconds, the keeper invokes `lempi-btctl ap-failover`, which writes scoped `dnsmasq` resolver configuration and brings up `lempi-ap` without scheduling the 5-minute self-destruct timer used by interactive `ap-start`.

**`[SPEC-WFO-020]` Access point network topology.**
When `lempi-ap` is activated by failover:
- SSID defaults to `Lempi` (or configured override), WPA-PSK defaults to `Lempi321` `[SPEC-WIFI-040]`.
- Static IP: `10.42.0.1/24` on `wlan0`.
- DHCP & DNS: NetworkManager's scoped `dnsmasq` serves IP leases and maps `lempi` and `lempi.lan` to `10.42.0.1` `[SPEC-WIFI-030]`.
- Web Endpoints: Served concurrently on port `5720` and port `80` (`http://lempi/` or `http://10.42.0.1/`) via `CAP_NET_BIND_SERVICE` `[SPEC-WIFI-050]`.
- Physical Status: ACT LED indicates radio status via `lempi-btctl led wifi` `[PI3-LED-010]`.

---

## 3. Standalone Offline Operation (Approach 1)

**`[SPEC-WFO-030]` Offline web UI state and mobile routing guidance.**
When accessed over `lempi-ap`, the web skin:
1. Detects AP mode (`active_connection == "lempi-ap"` via `GET /wifi/known`).
2. Renders an **Offline Player** status banner:
   - Identifies active output (Hardware DAC or Bluetooth speaker).
   - Informs the listener that playback is operating entirely from local storage.
3. Displays mobile data routing guidance:
   - Explains that modern phones (especially Android) may route browser traffic over cellular data when a Wi-Fi link lacks WAN internet. If the page fails to open or disconnects, the user is advised to temporarily disable mobile data or accept "Stay connected without internet".
   - Highlights that `http://10.42.0.1` or `http://lempi` on port 80 avoids typing port numbers `[SPEC-WIFI-050]`.
4. Leaves the full player controls (library, albums, playback, volume) immediately accessible without modal gates.

**`[SPEC-WFO-040]` Stopping at Approach 1.**
The user is under no obligation to configure further networking. The appliance remains in AP mode indefinitely while power is maintained, providing complete offline playback capabilities.

---

## 4. In-Browser Hotspot Pivot (Approach 3D)

**`[SPEC-WFO-050]` Scan and manual selection.**
When the user chooses to pivot to a mobile hotspot or venue Wi-Fi:
1. **Multi-device / Venue Path**: User taps **"Pivot to Hotspot / Wi-Fi"**; the UI triggers `GET /wifi/scan` on demand and lists nearby SSIDs.
2. **Single-Phone Hotspot Path**: A smartphone cannot broadcast a Wi-Fi hotspot while joined to `lempi-ap`. The UI provides an **"Enter manually"** option allowing the user to pre-fill their hotspot SSID and passphrase *before* enabling the hotspot on their phone.
3. Periodic polling is prohibited to protect audio buffers `[SPEC-WFO-070]`.

**`[SPEC-WFO-060]` Pivot handshake, reload persistence, and reachability.**
1. User submits network credentials via `POST /wifi/connect`.
2. The server writes transition state to `/run/lempi/wifi_pending` and schedules the hard revert timer (`systemd-run --unit=lempi-revert-<id> --on-active=3min /usr/local/bin/lempi-wifi-revert lempi-ap <target>`) `[SPEC-WIFI-010]`.
3. If the switch reverts, `lempi-wifi-revert` restores `lempi-ap` but MUST NOT set `autoconnect yes` on `lempi-ap`.
4. **Reachability on Pivot Networks**:
   - *Direct IP Access*: On a phone hotspot, Lempi is a DHCP client and no longer runs the subnet's DNS server; `.lan` will not resolve. On iOS Hotspot (`172.20.10.x`), Lempi is typically `.2`. On Android, the IP is shown in Hotspot Connected Devices. Lempi's port 80 listener (`http://<ip>/`) allows direct connection without port numbers.
   - *Application Discovery*: Lempi native apps discover the player directly via UDP broadcast on port 13492 `[SPEC-DSC-010]` without DNS.
   - *Screen-Equipped Nodes*: Appliances with a display (e.g. framebuffer [SPEC036](SPEC036-framebuffer-touch-ui.md) or OLED) render the newly acquired IP address and network name directly on screen.
   - *Public/Hotel Client Isolation*: On venue Wi-Fi with client isolation, inter-device traffic is blocked; the user cannot reach Lempi, and the 3-minute revert timer safely restores `lempi-ap` (where Approach 1 applies).
5. **Stateful Reload Confirmation (`GET /wifi/pending`)**:
   - When the user reconnects on the new network and loads the page, `skin.js` queries `GET /wifi/pending`.
   - The UI immediately restores the **"Keep this network?"** confirmation banner with live remaining seconds. Tapping **"Keep Network"** calls `POST /wifi/confirm/:change_id`, which removes `/run/lempi/wifi_pending` and cancels the revert timer.

---

## 5. RF Coexistence & Return to Infrastructure

**`[SPEC-WFO-070]` Audio stream RF protection.**
Wi-Fi scanning aggressively hops all 2.4 GHz channels, creating heavy RF contention with active Bluetooth A2DP audio streams `[PI012]`:
- Before invoking `nmcli dev wifi list --rescan yes`, the helper/service layer checks whether Bluetooth audio is actively streaming via `lempi_routed()` (querying `http://localhost:5720/audio/sink`) or player session state.
- If audio is streaming to a Bluetooth sink, the helper passes `--rescan no` to return cached BSSID results without forcing off-channel radio hops. Unsolicited background scans while playing are strictly prohibited.

**`[SPEC-WFO-080]` Return-to-home infrastructure hysteresis.**
When the appliance returns to its home network:
1. **Reboot Path**: On power cycle, the 45-second boot grace period automatically prioritizes known autoconnect infrastructure profiles before AP failover can trigger.
2. **Runtime Manual Path**: User can tap **"Stop AP, return to Wi-Fi"** (`POST /wifi/ap/stop`), which reconnects to preferred known networks. *Until 2026-10-10 it never could: it looked for type `wifi`, which terse nmcli never prints `[PI3-FOUND-800]`.*
3. **Idle Return (built 2026-10-10)**: If `lempi-ap` has had zero associated client stations for 15 minutes, the failover keeper tries the known autoconnect profile once (`lempi-btctl ap-return`: no revert timer, `--wait 30`, the access point straight back on failure), and the idle clock restarts. Not conditioned on idle audio, as first written: an appliance that plays all day never is, and the return does not scan. Each change in the number of clients is logged — the record of when anyone joined or left [PI028](../../LempiPi/PI028-started-before-its-audio.md).

---

## 6. Traceability Summary

- Reference `[REQ-WFO-010]`: Autonomous AP failover on boot or link loss (`lempi-wifi-failover`)
- Reference `[REQ-WFO-020]`: First-class standalone offline operation (Web UI / `skin.js`)
- Reference `[REQ-WFO-030]`: In-browser hotspot pivot workflow with manual SSID entry (`player/src/web/wifi.rs`)
- Reference `[REQ-WFO-040]`: Confirm-or-revert pivot safety net without autoconnect poisoning (`lempi-wifi-revert`)
- Reference `[REQ-WFO-050]`: Suppress active rescan during BT audio (`lempi-btctl` / WirePlumber)
- Reference `[SPEC-WFO-010]`: 45s boot grace, 30s link-loss debounce, rfkill/revert guards (`lempi-wifi-failover`)
- Reference `[SPEC-WFO-020]`: Fallback AP topology (`10.42.0.1`, port 80/5720, ACT LED) (`lempi-btctl` / `dnsmasq`)
- Reference `[SPEC-WFO-030]`: Offline UI banner & mobile routing advisory (`skin.html` / `skin.js`)
- Reference `[SPEC-WFO-040]`: Permanent stop-at-Approach-1 capability (Web UI)
- Reference `[SPEC-WFO-050]`: On-demand scan & manual SSID pivot selection (`skin.js`)
- Reference `[SPEC-WFO-060]`: Pivot handshake, IP discovery, reload persistence, and 3-min revert (`lempi-wifi-revert`)
- Reference `[SPEC-WFO-070]`: RF scan suppression during active playback via `lempi_routed` (`lempi-btctl`)
- Reference `[SPEC-WFO-080]`: Return-to-infrastructure boot & idle hysteresis (`lempi-wifi-failover`)
