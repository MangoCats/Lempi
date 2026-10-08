//! Bluetooth AVRCP transport control handling `[IMPL-BT-010]`.
//!
//! Replaces the legacy characterisation script `lempi-rocker.sh` with a native,
//! in-process Linux input reader. Connects Bluetooth speaker/adapter transport
//! actions (such as the Marshall Middleton rocker or vehicle FM transmitters)
//! directly to the Lempi engine via [`EngineHandle`] `[IMPL-BT-020]`.
//!
//! BlueZ creates the AVRCP `uinput` keyboard node only when an audio profile
//! connects, and tears it down on disconnect `[PI3-ROCKER-030]`. This module
//! monitors `/proc/bus/input/devices` for active `AVRCP` input nodes without
//! caching device paths across reconnects `[IMPL-BT-030]`.
//!
//! State is owned by the engine, not by a local shell variable: Play/Pause
//! toggling dispatches an atomic [`Command::TogglePlayPause`], eliminating
//! snapshot latency race conditions. Previous/back keys are an explicit no-op
//! per `[PI3-ROCKER-010]` and `[REQ-AUD-142]`.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::sync::atomic::AtomicBool;
#[cfg(all(target_os = "linux", feature = "appliance"))]
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

#[cfg(all(target_os = "linux", feature = "appliance"))]
use crate::engine::Command;
use crate::engine::EngineHandle;

pub const EV_SYN: u16 = 0x00;
pub const EV_KEY: u16 = 0x01;

pub const KEY_PAUSE: u16 = 119;
pub const KEY_BACK: u16 = 158;
pub const KEY_FORWARD: u16 = 159;
pub const KEY_NEXTSONG: u16 = 163;
pub const KEY_PLAYPAUSE: u16 = 164;
pub const KEY_PREVIOUSSONG: u16 = 165;
pub const KEY_PLAYCD: u16 = 200;
pub const KEY_PAUSECD: u16 = 201;
pub const KEY_PLAY: u16 = 207;

pub const DEBOUNCE_MS: u64 = 250;
#[cfg(all(target_os = "linux", feature = "appliance"))]
const DISCOVERY_INTERVAL: Duration = Duration::from_millis(1500);
#[cfg(all(target_os = "linux", feature = "appliance"))]
const WARN_INTERVAL: Duration = Duration::from_secs(30);

/// An unpacked Linux input event (`struct input_event`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RawInputEvent {
    pub ev_type: u16,
    pub code: u16,
    pub value: i32,
}

/// Transport action mapped from AVRCP keycodes `[IMPL-BT-050]`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    TogglePlayPause,
    Play,
    Pause,
    Skip,
}

/// Parse standard Linux 24-byte `struct input_event` (`timeval` 16B + type 2B + code 2B + value 4B).
///
/// Hand-rolled parsing field-by-field avoids unsafe struct transmutes and platform-specific
/// header dependencies, matching `fbui.rs`'s ABI-stable parser.
pub fn parse_event(buf: &[u8; 24]) -> RawInputEvent {
    RawInputEvent {
        ev_type: u16::from_ne_bytes([buf[16], buf[17]]),
        code: u16::from_ne_bytes([buf[18], buf[19]]),
        value: i32::from_ne_bytes([buf[20], buf[21], buf[22], buf[23]]),
    }
}

/// Maps an EV_KEY keycode to a player transport action `[IMPL-BT-050]`.
pub fn map_key(code: u16) -> Option<Action> {
    match code {
        KEY_PLAYCD | KEY_PLAYPAUSE | KEY_PAUSECD => Some(Action::TogglePlayPause),
        KEY_PLAY => Some(Action::Play),
        KEY_PAUSE => Some(Action::Pause),
        KEY_NEXTSONG | KEY_FORWARD => Some(Action::Skip),
        // Previous/Back is deliberately unmapped / reserved per [PI3-ROCKER-010] and [REQ-AUD-142].
        KEY_PREVIOUSSONG | KEY_BACK => None,
        _ => None,
    }
}

/// Hardware debounce guard to suppress mechanical switch bounce `[IMPL-BT-040]`.
pub struct Debounce {
    last_action: Option<Instant>,
    window: Duration,
}

impl Debounce {
    pub fn new(window_ms: u64) -> Self {
        Self {
            last_action: None,
            window: Duration::from_millis(window_ms),
        }
    }

    pub fn allow(&mut self, now: Instant) -> bool {
        if let Some(last) = self.last_action {
            if now.duration_since(last) < self.window {
                return false;
            }
        }
        self.last_action = Some(now);
        true
    }
}

/// Scans `/proc/bus/input/devices` records for an active AVRCP event device `[IMPL-BT-030]`.
pub fn find_avrcp_device_in(content: &str) -> Option<String> {
    // Records are separated by blank lines
    for record in content.split("\n\n") {
        let mut has_avrcp = false;
        let mut event_name = None;

        for line in record.lines() {
            let line = line.trim();
            if line.starts_with("N: ") && line.to_ascii_uppercase().contains("AVRCP") {
                has_avrcp = true;
            }
            if let Some(handlers) = line.strip_prefix("H: Handlers=") {
                for token in handlers.split_whitespace() {
                    if token.starts_with("event") && token[5..].chars().all(|c| c.is_ascii_digit()) {
                        event_name = Some(token.to_string());
                    }
                }
            }
        }

        if has_avrcp {
            if let Some(ev) = event_name {
                return Some(ev);
            }
        }
    }
    None
}

#[cfg(all(target_os = "linux", feature = "appliance"))]
pub fn spawn(handle: Arc<EngineHandle>, shutdown: Arc<AtomicBool>) -> std::thread::JoinHandle<()> {
    std::thread::Builder::new()
        .name("lempi-avrcp".into())
        .spawn(move || run_worker(handle, shutdown))
        .expect("failed to spawn lempi-avrcp worker thread")
}

#[cfg(not(all(target_os = "linux", feature = "appliance")))]
pub fn spawn(_handle: Arc<EngineHandle>, _shutdown: Arc<AtomicBool>) {
    // Non-Linux or non-appliance builds do not run the AVRCP event worker [GUIDE033].
}

#[cfg(all(target_os = "linux", feature = "appliance"))]
fn run_worker(handle: Arc<EngineHandle>, shutdown: Arc<AtomicBool>) {
    use std::os::unix::fs::OpenOptionsExt;

    tracing::info!("avrcp: native Bluetooth control worker started");
    let mut last_eacces_warn: Option<Instant> = None;

    while !shutdown.load(Ordering::Relaxed) {
        let dev_opt = std::fs::read_to_string("/proc/bus/input/devices")
            .ok()
            .and_then(|c| find_avrcp_device_in(&c));

        if let Some(dev_name) = dev_opt {
            let dev_path = format!("/dev/input/{dev_name}");
            match std::fs::OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NONBLOCK)
                .open(&dev_path)
            {
                Ok(mut file) => {
                    tracing::info!("avrcp: attached to {dev_path}");
                    read_device_loop(&mut file, &handle, &shutdown);
                    tracing::info!("avrcp: detached from {dev_path}");
                }
                Err(err) if err.raw_os_error() == Some(libc::EACCES) => {
                    let now = Instant::now();
                    if last_eacces_warn.is_none_or(|t| now.duration_since(t) >= WARN_INTERVAL) {
                        tracing::warn!(
                            "avrcp: permission denied on {dev_path}; ensure player user belongs to 'input' group"
                        );
                        last_eacces_warn = Some(now);
                    }
                }
                Err(err) => {
                    tracing::debug!("avrcp: open {dev_path} failed: {err}");
                }
            }
        }

        // Interruptible discovery interval
        let step = Duration::from_millis(100);
        let mut waited = Duration::ZERO;
        while waited < DISCOVERY_INTERVAL && !shutdown.load(Ordering::Relaxed) {
            std::thread::sleep(step);
            waited += step;
        }
    }

    tracing::info!("avrcp: worker stopped");
}

#[cfg(all(target_os = "linux", feature = "appliance"))]
fn read_device_loop(
    file: &mut std::fs::File,
    handle: &Arc<EngineHandle>,
    shutdown: &Arc<AtomicBool>,
) {
    use std::io::Read;
    use std::os::unix::io::AsRawFd;

    let fd = file.as_raw_fd();
    let mut pfd = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };
    let mut debounce = Debounce::new(DEBOUNCE_MS);
    let mut buf = [0u8; 24];

    while !shutdown.load(Ordering::Relaxed) {
        pfd.revents = 0;
        let ret = unsafe { libc::poll(&mut pfd, 1, 250) };
        if ret < 0 {
            let err = std::io::Error::last_os_error();
            if err.raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            tracing::warn!("avrcp: poll error: {err}");
            break;
        }
        if ret == 0 {
            // Poll timeout (250 ms), check shutdown flag and continue
            continue;
        }

        // Detachment detected via poll error/hup/nval flags
        if (pfd.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL)) != 0 {
            tracing::info!("avrcp: poll detected detachment (revents={:#x})", pfd.revents);
            break;
        }

        if (pfd.revents & libc::POLLIN) != 0 {
            match file.read_exact(&mut buf) {
                Ok(()) => {
                    let ev = parse_event(&buf);
                    if ev.ev_type == EV_KEY && ev.value == 1 {
                        if let Some(action) = map_key(ev.code) {
                            let now = Instant::now();
                            if debounce.allow(now) {
                                match action {
                                    Action::TogglePlayPause => {
                                        tracing::info!("avrcp: key {} -> toggle play/pause", ev.code);
                                        handle.send(Command::TogglePlayPause);
                                    }
                                    Action::Play => {
                                        tracing::info!("avrcp: key {} -> play", ev.code);
                                        handle.send(Command::Play);
                                    }
                                    Action::Pause => {
                                        tracing::info!("avrcp: key {} -> pause", ev.code);
                                        handle.send(Command::Pause);
                                    }
                                    Action::Skip => {
                                        tracing::info!("avrcp: key {} -> skip", ev.code);
                                        handle.send(Command::Skip);
                                    }
                                }
                            } else {
                                tracing::trace!("avrcp: key {} suppressed by debounce", ev.code);
                            }
                        } else {
                            tracing::trace!("avrcp: key {} unmapped or reserved", ev.code);
                        }
                    }
                }
                Err(err) if err.kind() == std::io::ErrorKind::WouldBlock => {
                    continue;
                }
                Err(err)
                    if err.raw_os_error() == Some(libc::ENODEV)
                        || err.kind() == std::io::ErrorKind::UnexpectedEof =>
                {
                    tracing::info!("avrcp: device detached ({err})");
                    break;
                }
                Err(err) => {
                    tracing::warn!("avrcp: read error ({err})");
                    break;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_24_byte_input_event() {
        let mut buf = [0u8; 24];
        // Timeval tv_sec: i64 (8 bytes), tv_usec: i64 (8 bytes) -> 16 bytes
        // Type: u16 (offset 16..18)
        buf[16..18].copy_from_slice(&EV_KEY.to_ne_bytes());
        // Code: u16 (offset 18..20)
        buf[18..20].copy_from_slice(&KEY_PLAYCD.to_ne_bytes());
        // Value: i32 (offset 20..24)
        buf[20..24].copy_from_slice(&1i32.to_ne_bytes());

        let ev = parse_event(&buf);
        assert_eq!(ev.ev_type, EV_KEY);
        assert_eq!(ev.code, KEY_PLAYCD);
        assert_eq!(ev.value, 1);
    }

    #[test]
    fn maps_avrcp_transport_keys() {
        assert_eq!(map_key(KEY_PLAYCD), Some(Action::TogglePlayPause));
        assert_eq!(map_key(KEY_PLAYPAUSE), Some(Action::TogglePlayPause));
        assert_eq!(map_key(KEY_PAUSECD), Some(Action::TogglePlayPause));
        assert_eq!(map_key(KEY_PLAY), Some(Action::Play));
        assert_eq!(map_key(KEY_PAUSE), Some(Action::Pause));
        assert_eq!(map_key(KEY_NEXTSONG), Some(Action::Skip));
        assert_eq!(map_key(KEY_FORWARD), Some(Action::Skip));

        // Previous and Back are unmapped per PI3-ROCKER-010 / REQ-AUD-142
        assert_eq!(map_key(KEY_PREVIOUSSONG), None);
        assert_eq!(map_key(KEY_BACK), None);
        assert_eq!(map_key(999), None);
    }

    #[test]
    fn debounce_suppresses_rapid_presses() {
        let mut debounce = Debounce::new(250);
        let start = Instant::now();

        assert!(debounce.allow(start));
        assert!(!debounce.allow(start + Duration::from_millis(50)));
        assert!(!debounce.allow(start + Duration::from_millis(200)));
        assert!(debounce.allow(start + Duration::from_millis(260)));
    }

    #[test]
    fn finds_avrcp_device_in_proc_devices_fixture() {
        let fixture = "\
I: Bus=0005 Vendor=0000 Product=0000 Version=0000\n\
N: Name=\"Middleton (AVRCP)\"\n\
P: Phys=\n\
S: Sysfs=/devices/virtual/input/input2\n\
U: Uniq=\n\
H: Handlers=kbd event2\n\
B: PROP=0\n\
B: EV=3\n\
\n\
I: Bus=0003 Vendor=046d Product=c52b Version=0111\n\
N: Name=\"Logitech Wireless Receiver\"\n\
P: Phys=usb-3f980000.usb-1.2/input0\n\
S: Sysfs=/devices/platform/soc/3f980000.usb/usb1/1-1/1-1.2/1-1.2:1.0/0003:046D:C52B.0001/input/input0\n\
U: Uniq=\n\
H: Handlers=sysrq kbd event0\n\
B: PROP=0\n\
\n";

        let found = find_avrcp_device_in(fixture);
        assert_eq!(found.as_deref(), Some("event2"));
    }

    #[test]
    fn ignores_devices_when_avrcp_absent() {
        let fixture = "\
I: Bus=0003 Vendor=046d Product=c52b Version=0111\n\
N: Name=\"Logitech Wireless Receiver\"\n\
H: Handlers=sysrq kbd event0\n\
\n";

        assert_eq!(find_avrcp_device_in(fixture), None);
        assert_eq!(find_avrcp_device_in(""), None);
    }
}
