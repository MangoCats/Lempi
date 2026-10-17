//! The node's power, watched from inside the player `[APP-PWR-010]`.
//!
//! **Why here.** lp3-wifi, 2026-10-16..17: its supply fell below the Pi's
//! threshold 1,104 times in one night, the CPU was held at 600 MHz, and the
//! listener heard Bluetooth glitches -- and nothing a listener could see said
//! so. Finding it took an SSH session, `vcgencmd` and the kernel log
//! `[LP3-PWR-010]`. So the player keeps the record itself and the Settings
//! page shows it, beside the audio's own underruns, so the two can be read
//! against each other `[LP3-PWR-030]`.
//!
//! **What it reads, and what that costs.** The Pi's voltage monitor
//! (`rpi_volt`) exposes `in0_lcrit_alarm`, 1 while the supply is low. The
//! kernel driver refreshes it from the firmware every two seconds, so reading
//! it every two seconds loses nothing, and a read is a few microseconds. The
//! throttle bits have no file on these kernels and the firmware mailbox is
//! root's, so they come from `vcgencmd get_throttled`, a few milliseconds of
//! CPU, once a minute and at each dip. Clock and temperature are plain reads.
//! Nothing here can load the node it is watching.
//!
//! **What it keeps:** counts since the player started, the last
//! [`KEEP_EVENTS`] dips, and a minute-by-minute day -- dips, seconds low,
//! samples found throttled, lowest clock, highest temperature, and the
//! player's underrun samples -- about 50 KB. In memory only: the day across
//! restarts is `lempi-health`'s log `[APP-HLT-010]`, and this says which it is.
//!
//! Where no `rpi_volt` exists -- a desktop, a phone, a Pi kernel without the
//! driver -- nothing runs, and `/power` says `available: false`.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

/// How many dips are kept, newest first.
pub const KEEP_EVENTS: usize = 50;
/// A day of minutes.
pub const KEEP_MINUTES: usize = 24 * 60;
/// A dip that ended this recently still makes the state `recent`.
pub const RECENT_SECS: i64 = 600;

/// One minute of the day.
#[derive(Debug, Clone, Default, PartialEq, serde::Serialize)]
pub struct Minute {
    /// The minute's start, Unix seconds.
    pub at: i64,
    pub dips: u32,
    pub low_secs: u32,
    /// Samples that found the CPU throttled (bit 2 of `get_throttled`).
    pub throttled: u32,
    pub min_mhz: Option<u32>,
    pub max_temp_c: Option<f32>,
    /// The player's underrun samples counted in this minute.
    pub underrun_samples: u64,
}

/// One dip: when it began, and how long it lasted once it has ended.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Dip {
    pub at: i64,
    pub secs: Option<i64>,
}

/// The whole record. Pure: every method takes the time it is told, so the
/// tests drive it without a clock or a Pi.
#[derive(Debug, Default)]
pub struct Power {
    pub started: i64,
    pub low: bool,
    low_since: Option<i64>,
    pub dips: u64,
    pub low_secs: i64,
    pub events: VecDeque<Dip>,
    pub minutes: VecDeque<Minute>,
    pub throttled: Option<u32>,
    pub mhz: Option<u32>,
    pub temp_c: Option<f32>,
    last_underruns: Option<u64>,
}

impl Power {
    pub fn new(now: i64) -> Self {
        Power { started: now, ..Default::default() }
    }

    fn minute(&mut self, now: i64) -> &mut Minute {
        let at = now - now.rem_euclid(60);
        if self.minutes.back().map(|m| m.at) != Some(at) {
            self.minutes.push_back(Minute { at, ..Default::default() });
            while self.minutes.len() > KEEP_MINUTES {
                self.minutes.pop_front();
            }
        }
        self.minutes.back_mut().expect("just pushed")
    }

    /// The alarm as read: `true` while the supply is low.
    pub fn alarm(&mut self, low: bool, now: i64) {
        if low && !self.low {
            self.low = true;
            self.low_since = Some(now);
            self.dips += 1;
            self.minute(now).dips += 1;
            self.events.push_front(Dip { at: now, secs: None });
            self.events.truncate(KEEP_EVENTS);
        } else if !low && self.low {
            let secs = (now - self.low_since.unwrap_or(now)).max(0);
            self.low = false;
            self.low_since = None;
            self.low_secs += secs;
            self.minute(now).low_secs += secs as u32;
            if let Some(d) = self.events.front_mut() {
                d.secs = Some(secs);
            }
        }
    }

    /// The once-a-minute sample. `underruns_total` is the player's running
    /// count; only its growth is put in the minute.
    pub fn sample(&mut self, now: i64, throttled: Option<u32>, mhz: Option<u32>,
                  temp_c: Option<f32>, underruns_total: Option<u64>) {
        self.throttled = throttled.or(self.throttled);
        self.mhz = mhz.or(self.mhz);
        self.temp_c = temp_c.or(self.temp_c);
        let grew = match (self.last_underruns, underruns_total) {
            (Some(was), Some(is)) if is >= was => is - was,
            _ => 0,
        };
        if underruns_total.is_some() {
            self.last_underruns = underruns_total;
        }
        let m = self.minute(now);
        if throttled.is_some_and(|t| t & 0x4 != 0) {
            m.throttled += 1;
        }
        if let Some(f) = mhz {
            m.min_mhz = Some(m.min_mhz.map_or(f, |v| v.min(f)));
        }
        if let Some(t) = temp_c {
            m.max_temp_c = Some(m.max_temp_c.map_or(t, |v| v.max(t)));
        }
        m.underrun_samples += grew;
    }

    /// `low` now, `recent` within [`RECENT_SECS`] of a dip, else `ok`.
    pub fn state(&self, now: i64) -> &'static str {
        if self.low {
            return "low";
        }
        match self.events.front() {
            Some(d) if now - (d.at + d.secs.unwrap_or(0)) <= RECENT_SECS => "recent",
            _ => "ok",
        }
    }

    /// Everything the page shows, as JSON.
    pub fn summary(&self, now: i64) -> serde_json::Value {
        let hour: Vec<&Minute> = self.minutes.iter().filter(|m| m.at > now - 3600).collect();
        let sum = |f: &dyn Fn(&Minute) -> u64| hour.iter().map(|m| f(m)).sum::<u64>();
        serde_json::json!({
            "available": true,
            "state": self.state(now),
            "now": now,
            "started": self.started,
            "low_now": self.low,
            "throttled": self.throttled.map(|t| format!("{t:#x}")),
            "throttle": self.throttled.map(decode),
            "mhz": self.mhz,
            "temp_c": self.temp_c,
            "since_start": { "dips": self.dips, "low_secs": self.low_secs },
            "last_hour": {
                "dips": sum(&|m| m.dips as u64),
                "low_secs": sum(&|m| m.low_secs as u64),
                "throttled_samples": sum(&|m| m.throttled as u64),
                "min_mhz": hour.iter().filter_map(|m| m.min_mhz).min(),
                "underrun_samples": sum(&|m| m.underrun_samples),
            },
            "events": self.events,
            "minutes": self.minutes,
        })
    }
}

/// `get_throttled`'s bits, named. The low four are now, the high four since
/// boot.
pub fn decode(t: u32) -> serde_json::Value {
    serde_json::json!({
        "undervoltage_now": t & 0x1 != 0,
        "capped_now": t & 0x2 != 0,
        "throttled_now": t & 0x4 != 0,
        "soft_temp_limit_now": t & 0x8 != 0,
        "undervoltage_since_boot": t & 0x1_0000 != 0,
        "capped_since_boot": t & 0x2_0000 != 0,
        "throttled_since_boot": t & 0x4_0000 != 0,
        "soft_temp_limit_since_boot": t & 0x8_0000 != 0,
    })
}

/// `throttled=0x50005` to `0x50005`, or `None`.
pub fn parse_throttled(line: &str) -> Option<u32> {
    let hex = line.trim().strip_prefix("throttled=")?.trim_start_matches("0x");
    u32::from_str_radix(hex, 16).ok()
}

// ---- the live node ------------------------------------------------------

fn cell() -> &'static OnceLock<Mutex<Power>> {
    static POWER: OnceLock<Mutex<Power>> = OnceLock::new();
    &POWER
}

fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// The alarm file of the Pi's voltage monitor, if this host has one.
pub fn alarm_path() -> Option<PathBuf> {
    let dirs = std::fs::read_dir("/sys/class/hwmon").ok()?;
    for d in dirs.flatten() {
        let p = d.path();
        let named = std::fs::read_to_string(p.join("name")).map(|n| n.trim() == "rpi_volt").unwrap_or(false);
        let alarm = p.join("in0_lcrit_alarm");
        if named && alarm.exists() {
            return Some(alarm);
        }
    }
    None
}

/// Whether this host can be watched at all -- the `power` capability.
pub fn available() -> bool {
    alarm_path().is_some()
}

fn read_trim(p: &Path) -> Option<String> {
    std::fs::read_to_string(p).ok().map(|s| s.trim().to_string())
}

fn take_sample(power: &Mutex<Power>, underruns: &dyn Fn() -> u64) {
    let thr = std::process::Command::new("vcgencmd")
        .arg("get_throttled")
        .output()
        .ok()
        .and_then(|o| parse_throttled(&String::from_utf8_lossy(&o.stdout)));
    let mhz = read_trim(Path::new("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"))
        .and_then(|s| s.parse::<u32>().ok())
        .map(|khz| khz / 1000);
    let temp = read_trim(Path::new("/sys/class/thermal/thermal_zone0/temp"))
        .and_then(|s| s.parse::<f32>().ok())
        .map(|m| m / 1000.0);
    let u = underruns();
    if let Ok(mut p) = power.lock() {
        p.sample(now_unix(), thr, mhz, temp, Some(u));
    }
}

/// Start watching, if this host has a voltage monitor. `underruns` is the
/// player's running underrun count, read once a minute.
pub fn spawn(underruns: impl Fn() -> u64 + Send + 'static) {
    let Some(alarm) = alarm_path() else { return };
    let power = cell().get_or_init(|| Mutex::new(Power::new(now_unix())));
    tracing::info!("power: watching {} [APP-PWR-010]", alarm.display());
    let spawned = std::thread::Builder::new().name("lempi-power".into()).spawn(move || {
        let mut last_sample = 0i64;
        loop {
            let low = read_trim(&alarm).as_deref() == Some("1");
            let mut sample_now = false;
            if let Ok(mut p) = power.lock() {
                let was = p.low;
                p.alarm(low, now_unix());
                // A dip is when the throttle bits are worth reading.
                sample_now = low && !was;
            }
            let now = now_unix();
            if sample_now || now - last_sample >= 60 {
                take_sample(power, &underruns);
                last_sample = now;
            }
            std::thread::sleep(std::time::Duration::from_secs(2));
        }
    });
    if let Err(e) = spawned {
        tracing::warn!("power: could not start the watcher: {e}");
    }
}

/// What `GET /power` answers: the watch's own summary, and the day the page
/// draws -- from the health log where there is one, so it reaches back past
/// the player's own start, else the watch's own minutes; `day.source` says
/// which `[APP-PWR-010]`.
pub fn status() -> serde_json::Value {
    let now = now_unix();
    let (mut v, watched) = match cell().get().and_then(|m| m.lock().ok()) {
        Some(p) => (p.summary(now), p.minutes.iter().map(DayMinute::from).collect::<Vec<_>>()),
        None => return serde_json::json!({ "available": false }),
    };
    let logged = day_from_health_log(Path::new(HEALTH_LOG), now);
    let (source, minutes) = if logged.is_empty() { ("watch", watched) } else { ("health-log", logged) };
    v["day"] = serde_json::json!({ "source": source, "minutes": minutes });
    v
}

/// The state alone, for the live snapshot: `None` where nothing is watched.
/// One lock, no I/O -- cheap enough for twice a second.
pub fn state_now() -> Option<&'static str> {
    let p = cell().get()?.lock().ok()?;
    Some(p.state(now_unix()))
}

// ---- the day, across restarts -------------------------------------------

/// `lempi-health`'s log `[APP-HLT-010]`.
pub const HEALTH_LOG: &str = "/var/lempi/health.log";
/// A day of its lines is about 400 KB; this much of each file's tail is read.
const TAIL_BYTES: u64 = 600_000;

/// One minute of the day as the page draws it.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct DayMinute {
    pub at: i64,
    pub dips: u32,
    /// The sample found the CPU throttled.
    pub throttled: bool,
    pub mhz: Option<u32>,
    pub temp_c: Option<f32>,
    /// Underrun events (the health log) or minutes with any (the watch).
    pub underruns: u32,
    pub playing: Option<bool>,
}

impl From<&Minute> for DayMinute {
    fn from(m: &Minute) -> Self {
        DayMinute {
            at: m.at,
            dips: m.dips,
            throttled: m.throttled > 0,
            mhz: m.min_mhz,
            temp_c: m.max_temp_c,
            underruns: u32::from(m.underrun_samples > 0),
            playing: None,
        }
    }
}

/// One health-log line, or `None` for a comment or a line without `t=`.
pub fn parse_health_line(line: &str) -> Option<DayMinute> {
    if line.starts_with('#') {
        return None;
    }
    let mut f = std::collections::HashMap::new();
    for kv in line.split_whitespace().skip(1) {
        if let Some((k, v)) = kv.split_once('=') {
            f.insert(k, v);
        }
    }
    let t: i64 = f.get("t")?.parse().ok()?;
    let num = |k: &str| f.get(k).and_then(|v| v.parse::<u32>().ok());
    let thr = f.get("thr").and_then(|v| u32::from_str_radix(v.trim_start_matches("0x"), 16).ok());
    Some(DayMinute {
        at: t - t.rem_euclid(60),
        dips: num("ev_uv").unwrap_or(0),
        throttled: thr.is_some_and(|t| t & 0x4 != 0),
        mhz: num("arm"),
        temp_c: f.get("temp").and_then(|v| v.parse().ok()),
        underruns: num("ev_underrun").unwrap_or(0),
        playing: match f.get("play") {
            Some(&"1") => Some(true),
            Some(&"0") => Some(false),
            _ => None,
        },
    })
}

fn tail(path: &Path) -> Option<String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut file = std::fs::File::open(path).ok()?;
    let len = file.metadata().ok()?.len();
    let from = len.saturating_sub(TAIL_BYTES);
    file.seek(SeekFrom::Start(from)).ok()?;
    let mut buf = Vec::new();
    file.read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf).into_owned();
    // A tail read from mid-file begins mid-line.
    Some(if from > 0 { text.split_once('\n').map(|x| x.1.to_string()).unwrap_or_default() } else { text })
}

/// The last day of the health log, oldest first, one entry a minute. Reads
/// the rotated `.1` too when the current file does not reach a day back.
pub fn day_from_health_log(path: &Path, now: i64) -> Vec<DayMinute> {
    let since = now - 24 * 3600;
    let parse = |p: &Path| -> Vec<DayMinute> {
        tail(p).map(|t| t.lines().filter_map(parse_health_line).filter(|m| m.at > since && m.at <= now).collect())
            .unwrap_or_default()
    };
    let mut day = parse(path);
    if day.first().is_none_or(|m| m.at > since + 3600) {
        let mut older = parse(&PathBuf::from(format!("{}.1", path.display())));
        older.append(&mut day);
        day = older;
    }
    day.sort_by_key(|m| m.at);
    day.dedup_by_key(|m| m.at);
    day
}

#[cfg(test)]
mod tests {
    use super::*;

    const T: i64 = 1_792_250_000 - 1_792_250_000 % 60; // a minute boundary

    #[test]
    fn a_dip_is_counted_once_and_timed() {
        let mut p = Power::new(T);
        p.alarm(true, T + 10);
        p.alarm(true, T + 12); // still low: not a second dip
        p.alarm(false, T + 16);
        assert_eq!(p.dips, 1);
        assert_eq!(p.low_secs, 6);
        assert_eq!(p.events.front(), Some(&Dip { at: T + 10, secs: Some(6) }));
        assert_eq!(p.minutes.back().map(|m| (m.dips, m.low_secs)), Some((1, 6)));
    }

    #[test]
    fn the_state_follows_the_last_dip() {
        let mut p = Power::new(T);
        assert_eq!(p.state(T), "ok");
        p.alarm(true, T + 5);
        assert_eq!(p.state(T + 6), "low");
        p.alarm(false, T + 11);
        assert_eq!(p.state(T + 12), "recent");
        assert_eq!(p.state(T + 11 + RECENT_SECS + 1), "ok");
    }

    #[test]
    fn a_sample_keeps_the_worst_of_its_minute_and_only_new_underruns() {
        let mut p = Power::new(T);
        p.sample(T + 1, Some(0x50005), Some(600), Some(63.4), Some(1000));
        p.sample(T + 30, Some(0x50000), Some(1200), Some(58.0), Some(1500));
        let m = p.minutes.back().unwrap();
        assert_eq!(m.throttled, 1, "one of the two samples found it throttled");
        assert_eq!(m.min_mhz, Some(600));
        assert_eq!(m.max_temp_c, Some(63.4));
        assert_eq!(m.underrun_samples, 500, "the first reading sets the base; only growth counts");
    }

    #[test]
    fn the_day_and_the_events_are_bounded() {
        let mut p = Power::new(T);
        for i in 0..(KEEP_MINUTES as i64 + 30) {
            p.alarm(true, T + i * 60);
            p.alarm(false, T + i * 60 + 1);
        }
        assert_eq!(p.minutes.len(), KEEP_MINUTES);
        assert_eq!(p.events.len(), KEEP_EVENTS);
        assert_eq!(p.dips, KEEP_MINUTES as u64 + 30, "the count since start is not bounded");
    }

    #[test]
    fn the_last_hour_is_the_last_hour() {
        let mut p = Power::new(T);
        p.alarm(true, T);
        p.alarm(false, T + 2);
        p.alarm(true, T + 7200);
        p.alarm(false, T + 7203);
        let s = p.summary(T + 7210);
        assert_eq!(s["last_hour"]["dips"], 1);
        assert_eq!(s["last_hour"]["low_secs"], 3);
        assert_eq!(s["since_start"]["dips"], 2);
    }

    #[test]
    fn the_throttle_bits_are_read_and_named() {
        assert_eq!(parse_throttled("throttled=0x50005\n"), Some(0x50005));
        assert_eq!(parse_throttled("throttled=0x0"), Some(0));
        assert_eq!(parse_throttled("garbage"), None);
        let d = decode(0x50005);
        assert_eq!(d["undervoltage_now"], true);
        assert_eq!(d["throttled_now"], true);
        assert_eq!(d["capped_now"], false);
        assert_eq!(d["undervoltage_since_boot"], true);
        assert_eq!(d["throttled_since_boot"], true);
    }

    #[test]
    fn a_health_log_line_becomes_a_minute() {
        let line = "2026-10-17T19:34:31 t=1792280071 up=1 thr=0x50005 arm=600 temp=63.4 volt=1.2 \
                    load=0.5 play=1 sink=x acl=1 wifi=connected sig=-60 gw=1 from=cursor ev_uv=3 \
                    ev_bt=0 ev_ring=0 ev_underrun=2 ev_recov=0 ev_sdio=0 ev_xrun=0";
        let m = parse_health_line(line).unwrap();
        assert_eq!(m.at, 1792280071 - 1792280071 % 60);
        assert_eq!((m.dips, m.throttled, m.mhz, m.underruns, m.playing), (3, true, Some(600), 2, Some(true)));
        assert_eq!(m.temp_c, Some(63.4));
        assert!(parse_health_line("# lempi-health: one line a minute").is_none());
        assert!(parse_health_line("2026-10-17T10:16:29 up=1626 ev_uv=4").is_none(),
                "a line from before t= cannot be placed in time");
    }

    #[test]
    fn the_day_reads_both_files_and_only_the_last_day() {
        let dir = std::env::temp_dir().join(format!("lempi-power-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let log = dir.join("health.log");
        let now = T + 30 * 3600;
        let line = |t: i64, uv: u32| format!("x t={t} thr=0x0 arm=1200 temp=50 play=1 ev_uv={uv} ev_underrun=0\n");
        // .1: one line too old, one inside the day; the current file: the last hour.
        std::fs::write(dir.join("health.log.1"), line(now - 25 * 3600, 9) + &line(now - 20 * 3600, 4)).unwrap();
        std::fs::write(&log, line(now - 1800, 1) + &line(now - 60, 2) + &line(now - 60, 2)).unwrap();
        let day = day_from_health_log(&log, now);
        assert_eq!(day.iter().map(|m| m.dips).collect::<Vec<_>>(), vec![4, 1, 2],
                   "the too-old line left out, the rotated file read, a repeated minute kept once");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_host_never_watched_says_so() {
        // `status` before any `spawn`: in the test process nothing spawns.
        if cell().get().is_none() {
            assert_eq!(status()["available"], false);
        }
    }
}
