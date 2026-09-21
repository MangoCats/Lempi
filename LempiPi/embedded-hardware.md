# Embedded Hardware & Storage Resilience

This document specifies the target hardware, fast-boot strategy, and storage partitioning model for dedicated **24/7 Lempi radio station appliances**.

---

## 🍓 Hardware Specification: Raspberry Pi Zero 2W

The primary initial hardware target for a dedicated, always-on Lempi player is the **Raspberry Pi Zero 2W**.

| Component | Specification | Purpose |
| :--- | :--- | :--- |
| **SoC / CPU** | Broadcom BCM2710A1 (Quad-core 64-bit ARM Cortex-A53 @ 1GHz) | Low-power, fanless 24/7 operation |
| **RAM** | 512MB LPDDR2 | Lightweight footprint for audio engine & web server |
| **Audio Output** | High-Quality Digital-to-Analog (D/A) HAT (e.g., HiFiBerry DAC, I2S) OR Bluetooth Audio | Audiophile-grade line-out to amplified speakers |
| **Networking** | 2.4GHz 802.11 b/g/n Wi-Fi | Web UI control network access |
| **Storage** | MicroSD / eMMC storage divided into 3 isolation partitions | Power-loss fault tolerance |

---

## ⚡ Fast-Boot Sequence ("Eternal Sage" Paradigm)

Because Lempi appliances operate as standalone physical hardware (similar to a traditional radio or stereo component), immediate audio playback upon power connection is the top design priority.

```
 Power On / Reset
       │
       ▼
 [ 1. Minimal Kernel & System Init ]
       │
       ▼
 [ 2. Audio Subsystem & Lempi Core Launch ]  ◄── HIGHEST PRIORITY
       │
       ├───────────────────────────────────────────────────┐
       ▼                                                   ▼
 [ 3. Instant Playback Start ]             [ 4. Async Web Server & Network Init ]
   - Load saved playlist queue               - Bring up Wi-Fi / IP stack
   - Begin decoding track 1 ASAP             - Start HTTP / WebSocket daemon
   - Audio flows out DAC / Bluetooth         - Ready for web UI connections
```

### Priority Breakdown
1. **Priority 1 (Audio First)**: The Lempi core daemon mounts the playlist partition, initializes the audio output interface, and starts playing track 1 from the saved playlist state in the minimum possible time.
2. **Priority 2 (Control Services)**: Network interfaces, Wi-Fi connections, and the HTTP web server initialize asynchronously in the background. The user hears music before the web control panel is online.

---

## 🛡️ Power-Loss Resilient 3-Partition Storage Model

Dedicated appliances are frequently unplugged or experience sudden power loss. To prevent SD card filesystem corruption or loss of the music library, Lempi enforces a **3-partition isolation model**:

```
+-------------------------------------------------------------------------+
|                              PHYSICAL DRIVE                             |
|                                                                         |
|  +-----------------------+ +---------------------+ +-----------------+  |
|  |      PARTITION 1      | |     PARTITION 2     | |   PARTITION 3   |  |
|  |     System OS Core    | |    Music Library    | |  Mutable State  |  |
|  |                       | |                     | |                 |  |
|  |  * Read-Only (RO)     | |  * Read-Only (RO)   | |  * Read-Write   |  |
|  |  * OS / Kernel / Bin  | |  * Audio Files / DAO| |  * Database     |  |
|  |  * Immutable          | |  * Mutable only     | |  * Playlists    |  |
|  |                       | |    during imports   | |  * Play History |  |
|  +-----------------------+ +---------------------+ +-----------------+  |
+-------------------------------------------------------------------------+
```

### Partition Breakdown

1. **Partition 1: System OS (Immutable / Read-Only)**
   - Contains Linux OS kernel, system daemons, audio drivers, and Lempi binaries.
   - Mounted **Read-Only (`ro`)**. Unsafe power-offs cannot corrupt system boot files.

2. **Partition 2: Music Media Storage (Read-Only Operational)**
   - Stores song files, album art, and DAO capture files.
   - Mounted **Read-Only (`ro`)** during standard playback operation.
   - Remounted **Read-Write (`rw`)** strictly when adding new music files or performing media library sync/maintenance, then immediately restored to `ro`.

3. **Partition 3: Mutable State & Database (Read-Write with Power-Loss Protection)**
   - Stores the active playlist queue, SQLite play history database, user preference logs, and runtime state.
   - Mounted **Read-Write (`rw`)** with write-ahead logging (WAL) and crash-resilient journal modes to prevent database corruption during sudden power outages.
