//! Each player reads its own audio back `[GDE-INT-130]`.
//!
//! Every file was hashed once, when it arrived, and nothing read it again
//! (`reports/integrity-audit-2026-10-14.md`): a speaker's SD card could lose
//! a song and the first sign would be a passage that would not play, or that
//! played garbled. This walks the catalogue's files in the background, hashes
//! each against the SHA-256 its row records, and keeps what it found in the
//! listener's `file_checks` -- the listener's, because a speaker's catalogue
//! may be read-only, and because what one machine's disk holds is its own.
//!
//! **Paced, and low.** It reads at `integrity_check_kib_per_s` (1024 unless
//! the listener's settings say otherwise; 0 turns it off), at the lowest CPU
//! priority Linux gives a thread. Measured 2026-10-15 at full speed: the Pi
//! Zero 2 W hashed its 49.3 GB in 37 minutes; at 1 MiB/s a pass takes about
//! fourteen hours and a twentieth of one core `[GDE-INT-100]`.
//!
//! **What it reads, in turn:** a file playback has just failed on
//! ([`suspect`], `[GDE-INT-140]`); then any never checked; then any whose last
//! check was not `ok`, an hour after it; then any `ok` file last read more than
//! a week ago. With nothing due it sleeps an hour and looks again, so a sync
//! that brings new files has them read within the hour.
//!
//! **What a finding does** `[GDE-INT-135]`: a file `damaged`, `missing` or
//! `unreadable` is withheld from the Director, which reads `file_checks` when
//! it loads; a file moving into or out of those asks for the reload the
//! Settings page's own `/library/reload` asks for.

use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use rusqlite::{params, Connection, OpenFlags, OptionalExtension};

use crate::session::SharedControls;

/// The rate when the listener names none, in KiB per second.
pub const DEFAULT_KIB_PER_S: u64 = 1024;
/// The setting that names another; 0 is off.
pub const RATE_KEY: &str = "integrity_check_kib_per_s";
/// An `ok` file is read again after this long.
const REVISIT: Duration = Duration::from_secs(7 * 24 * 3600);
/// A file not `ok` is read again after this long, so a repair is seen.
const RECHECK: Duration = Duration::from_secs(3600);
/// After start, before the first read: the tag scan and the first passages'
/// decoding come first.
const START_DELAY: Duration = Duration::from_secs(300);
/// With nothing due, how long until it looks again.
const IDLE: Duration = Duration::from_secs(3600);
const CHUNK: usize = 256 * 1024;

fn urgent() -> &'static Mutex<VecDeque<PathBuf>> {
    static Q: OnceLock<Mutex<VecDeque<PathBuf>>> = OnceLock::new();
    Q.get_or_init(|| Mutex::new(VecDeque::new()))
}

/// A file playback just failed on, or found damaged-sounding, read next
/// `[GDE-INT-140]`: a decode error becomes a finding within minutes, not a
/// line in a journal a reboot clears.
pub fn suspect(path: &Path) {
    if let Ok(mut q) = urgent().lock() {
        if !q.iter().any(|p| p == path) {
            q.push_back(path.to_path_buf());
        }
    }
}

/// One file's check, as it is recorded.
#[derive(Debug, Clone, PartialEq)]
pub struct Check {
    pub outcome: &'static str,
    pub size: Option<u64>,
    pub detail: Option<String>,
}

/// Read `path` and compare it with `want`, the recorded SHA-256; `audio_md5`
/// tells a re-tag from damage, through `hash_audio`. `pace` is called after
/// each chunk with the bytes read so far, and returns false to stop -- the
/// thread's own stop -- in which case `None`.
pub fn check_file(
    path: &Path,
    want: &str,
    audio_md5: &str,
    hash_audio: impl Fn(&Path) -> Result<String, String>,
    mut pace: impl FnMut(u64) -> bool,
) -> Option<Check> {
    use sha2::Digest;
    use std::io::Read;
    let mut f = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Some(Check { outcome: "missing", size: None, detail: None })
        }
        Err(e) => return Some(Check { outcome: "unreadable", size: None, detail: Some(e.to_string()) }),
    };
    let mut h = sha2::Sha256::new();
    let mut buf = vec![0u8; CHUNK];
    let mut read = 0u64;
    loop {
        match f.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                h.update(&buf[..n]);
                read += n as u64;
                if !pace(read) {
                    return None;
                }
            }
            Err(e) => return Some(Check { outcome: "unreadable", size: Some(read), detail: Some(e.to_string()) }),
        }
    }
    let got: String = h.finalize().iter().map(|b| format!("{b:02x}")).collect();
    if got == want {
        return Some(Check { outcome: "ok", size: Some(read), detail: None });
    }
    match hash_audio(path) {
        Ok(a) if a == audio_md5 => Some(Check {
            outcome: "retagged",
            size: Some(read),
            detail: Some(format!("bytes {got}, audio intact")),
        }),
        Ok(a) => Some(Check { outcome: "damaged", size: Some(read), detail: Some(format!("bytes {got}, audio {a}")) }),
        Err(e) => Some(Check { outcome: "damaged", size: Some(read), detail: Some(format!("bytes {got}, audio: {e}")) }),
    }
}

/// A file the catalogue names, with what was last found of it.
#[derive(Debug, Clone)]
pub struct Due {
    pub audio_md5: String,
    pub path: PathBuf,
    pub sha256: String,
    pub urgent: bool,
}

fn age(at: &str, now: chrono_like::Now) -> Duration {
    now.since(at)
}

/// The next file to read, by the order in the module's notes; `None` when
/// nothing is due.
pub fn next_due(listener: &Connection, library: &Connection, now_unix: i64) -> rusqlite::Result<Option<Due>> {
    let now = chrono_like::Now(now_unix);
    let mut last: HashMap<String, (String, String)> = HashMap::new();
    {
        let mut st = listener.prepare("SELECT audio_md5, checked_at, outcome FROM file_checks")?;
        for r in st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?)))? {
            let (m, at, o) = r?;
            last.insert(m, (at, o));
        }
    }
    let mut st = library.prepare("SELECT audio_md5, path, sha256 FROM files WHERE sha256 IS NOT NULL AND sha256 <> ''")?;
    let rows: Vec<(String, String, String)> =
        st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<rusqlite::Result<_>>()?;
    let wanted: Vec<PathBuf> = urgent().lock().map(|q| q.iter().cloned().collect()).unwrap_or_default();
    if let Some(p) = wanted.first() {
        if let Ok(mut q) = urgent().lock() {
            q.retain(|x| x != p);
        }
        if let Some((m, path, sha)) = rows.iter().find(|(_, path, _)| Path::new(path) == p) {
            return Ok(Some(Due { audio_md5: m.clone(), path: PathBuf::from(path), sha256: sha.clone(), urgent: true }));
        }
    }
    // Never checked, then not ok and an hour old, then ok and a week old --
    // each tier oldest first.
    let mut best: Option<(u8, String, &(String, String, String))> = None;
    for row in &rows {
        let tier_at = match last.get(&row.0) {
            None => Some((0u8, String::new())),
            Some((at, o)) if o != "ok" && age(at, now) >= RECHECK => Some((1, at.clone())),
            Some((at, o)) if o == "ok" && age(at, now) >= REVISIT => Some((2, at.clone())),
            _ => None,
        };
        if let Some((tier, at)) = tier_at {
            if best.as_ref().is_none_or(|(t, a, _)| (tier, &at) < (*t, a)) {
                best = Some((tier, at, row));
            }
        }
    }
    Ok(best.map(|(_, _, (m, path, sha))| Due {
        audio_md5: m.clone(),
        path: PathBuf::from(path),
        sha256: sha.clone(),
        urgent: false,
    }))
}

/// Record a check; returns whether the file moved into or out of what the
/// Director withholds, so a reload is due.
pub fn record(listener: &Connection, due: &Due, c: &Check, now_iso: &str) -> rusqlite::Result<bool> {
    let before: Option<String> = listener
        .query_row("SELECT outcome FROM file_checks WHERE audio_md5 = ?1", [&due.audio_md5], |r| r.get(0))
        .optional()?;
    listener.execute(
        "INSERT INTO file_checks (audio_md5, checked_at, outcome, size, path, detail) VALUES (?1,?2,?3,?4,?5,?6) \
         ON CONFLICT(audio_md5) DO UPDATE SET checked_at = excluded.checked_at, outcome = excluded.outcome, \
         size = excluded.size, path = excluded.path, detail = excluded.detail",
        params![due.audio_md5, now_iso, c.outcome, c.size.map(|s| s as i64), due.path.to_string_lossy(), c.detail],
    )?;
    let unplayable = |o: &str| matches!(o, "damaged" | "missing" | "unreadable");
    Ok(before.as_deref().map(unplayable).unwrap_or(false) != unplayable(c.outcome))
}

/// The rate the listener's settings name, in bytes per second; 0 is off.
pub fn rate(listener: &Connection) -> u64 {
    listener
        .query_row("SELECT value FROM player_settings WHERE key = ?1", [RATE_KEY], |r| r.get::<_, String>(0))
        .optional()
        .ok()
        .flatten()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .unwrap_or(DEFAULT_KIB_PER_S)
        * 1024
}

/// This player's checks, as `/integrity` and the sync snapshot report them
/// `[GDE-INT-145]`: how much of the catalogue has been read, the oldest read,
/// and every file not `ok`.
pub fn summary(listener: &Path, library: &Path) -> Result<serde_json::Value, String> {
    let e = |e: rusqlite::Error| e.to_string();
    let l = Connection::open_with_flags(listener, OpenFlags::SQLITE_OPEN_READ_ONLY).map_err(e)?;
    let b = Connection::open_with_flags(library, OpenFlags::SQLITE_OPEN_READ_ONLY).map_err(e)?;
    let files: i64 = b
        .query_row("SELECT COUNT(*) FROM files WHERE sha256 IS NOT NULL AND sha256 <> ''", [], |r| r.get(0))
        .map_err(e)?;
    let has = l
        .query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_checks'", [], |_| Ok(()))
        .optional()
        .map_err(e)?
        .is_some();
    if !has {
        return Ok(serde_json::json!({"files": files, "checked": 0, "counts": {}, "not_ok": [], "oldest": null,
                                     "rate_kib_per_s": rate(&l) / 1024}));
    }
    let mut counts = serde_json::Map::new();
    {
        let mut st = l.prepare("SELECT outcome, COUNT(*) FROM file_checks GROUP BY outcome").map_err(e)?;
        for r in st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))).map_err(e)? {
            let (o, n) = r.map_err(e)?;
            counts.insert(o, n.into());
        }
    }
    let checked: i64 = l.query_row("SELECT COUNT(*) FROM file_checks", [], |r| r.get(0)).map_err(e)?;
    let oldest: Option<String> = l.query_row("SELECT MIN(checked_at) FROM file_checks", [], |r| r.get(0)).map_err(e)?;
    let mut st = l
        .prepare("SELECT audio_md5, path, outcome, checked_at, detail FROM file_checks WHERE outcome <> 'ok' \
                  ORDER BY outcome, path")
        .map_err(e)?;
    let not_ok: Vec<serde_json::Value> = st
        .query_map([], |r| {
            Ok(serde_json::json!({"audio_md5": r.get::<_, String>(0)?, "path": r.get::<_, Option<String>>(1)?,
                                  "outcome": r.get::<_, String>(2)?, "checked_at": r.get::<_, String>(3)?,
                                  "detail": r.get::<_, Option<String>>(4)?}))
        })
        .map_err(e)?
        .collect::<rusqlite::Result<_>>()
        .map_err(e)?;
    Ok(serde_json::json!({"files": files, "checked": checked, "counts": counts, "not_ok": not_ok,
                          "oldest": oldest, "rate_kib_per_s": rate(&l) / 1024}))
}

/// Start the check `[GDE-INT-130]`. Ends when `stopped` does.
pub fn spawn(listener: PathBuf, library: PathBuf, controls: SharedControls, stopped: Receiver<()>) {
    let _ = std::thread::Builder::new().name("lempi-scrub".into()).spawn(move || {
        lower_priority();
        if !sleep(&stopped, START_DELAY) {
            return;
        }
        let mut idle_said = false;
        loop {
            match one(&listener, &library, &controls, &stopped) {
                Ok(Step::Stopped) => return,
                Ok(Step::Checked) => idle_said = false,
                Ok(Step::Idle) | Ok(Step::Off) => {
                    if !idle_said {
                        tracing::info!("integrity: nothing due to read; looking again in an hour [GDE-INT-130]");
                        idle_said = true;
                    }
                    if !sleep(&stopped, IDLE) {
                        return;
                    }
                }
                Err(err) => {
                    tracing::warn!("integrity: a check could not be made ({err}); trying again in an hour");
                    if !sleep(&stopped, IDLE) {
                        return;
                    }
                }
            }
        }
    });
}

enum Step {
    Checked,
    Idle,
    Off,
    Stopped,
}

fn one(listener: &Path, library: &Path, controls: &SharedControls, stopped: &Receiver<()>) -> Result<Step, String> {
    let e = |e: rusqlite::Error| e.to_string();
    let l = Connection::open(listener).map_err(e)?;
    l.busy_timeout(Duration::from_secs(10)).map_err(e)?;
    l.execute_batch(lempi_core::db::FILE_CHECKS_TABLE).map_err(e)?;
    let per_s = rate(&l);
    if per_s == 0 {
        return Ok(Step::Off);
    }
    let b = Connection::open_with_flags(library, OpenFlags::SQLITE_OPEN_READ_ONLY).map_err(e)?;
    let now_unix = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let Some(due) = next_due(&l, &b, now_unix).map_err(e)? else {
        return Ok(Step::Idle);
    };
    drop(b);
    let start = Instant::now();
    let mut stop = false;
    let checked = check_file(&due.path, &due.sha256, &due.audio_md5, crate::identity::hash_audio, |read| {
        let should = Duration::from_secs_f64(read as f64 / per_s as f64);
        let ahead = should.saturating_sub(start.elapsed());
        if !ahead.is_zero() && !sleep(stopped, ahead) {
            stop = true;
            return false;
        }
        true
    });
    let Some(c) = checked else {
        return Ok(if stop { Step::Stopped } else { Step::Checked });
    };
    let now_iso = chrono_like::Now(now_unix).iso();
    let changed = record(&l, &due, &c, &now_iso).map_err(e)?;
    if c.outcome != "ok" {
        tracing::warn!("integrity: {} is {}{} [GDE-INT-130]", due.path.display(), c.outcome,
                       c.detail.as_deref().map(|d| format!(" ({d})")).unwrap_or_default());
    } else if due.urgent {
        tracing::info!("integrity: {} read back whole after playback failed on it", due.path.display());
    }
    if changed {
        // The Director reads the checks when it loads: ask for that load.
        if let Ok(mut c) = controls.lock() {
            c.reload_requested = true;
            c.reload_status = Some("requested by the integrity check".into());
        }
    }
    Ok(Step::Checked)
}

/// Wait `d`, or less if stopped: false when stopped.
fn sleep(stopped: &Receiver<()>, d: Duration) -> bool {
    matches!(stopped.recv_timeout(d), Err(RecvTimeoutError::Timeout))
}

/// The lowest CPU priority this thread can have, on Linux; elsewhere nothing.
fn lower_priority() {
    #[cfg(target_os = "linux")]
    // SAFETY: setpriority on this thread's own id, with a valid niceness.
    unsafe {
        let tid = libc::syscall(libc::SYS_gettid) as libc::id_t;
        libc::setpriority(libc::PRIO_PROCESS, tid, 19);
    }
}

/// Unix seconds as the ISO time `file_checks` keeps, and back, without a
/// date crate: UTC, `YYYY-MM-DDTHH:MM:SSZ`.
mod chrono_like {
    use std::time::Duration;

    #[derive(Clone, Copy)]
    pub struct Now(pub i64);

    impl Now {
        pub fn iso(self) -> String {
            let (days, secs) = (self.0.div_euclid(86_400), self.0.rem_euclid(86_400));
            let (y, m, d) = civil(days);
            format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z", secs / 3600, secs % 3600 / 60, secs % 60)
        }

        /// How long ago `at` was; a time that cannot be read is long ago.
        pub fn since(self, at: &str) -> Duration {
            match parse(at) {
                Some(t) if t <= self.0 => Duration::from_secs((self.0 - t) as u64),
                Some(_) => Duration::ZERO,
                None => Duration::from_secs(u64::MAX / 4),
            }
        }
    }

    fn parse(at: &str) -> Option<i64> {
        let b = at.as_bytes();
        if b.len() < 19 {
            return None;
        }
        let n = |s: &str| s.parse::<i64>().ok();
        let (y, m, d) = (n(&at[0..4])?, n(&at[5..7])?, n(&at[8..10])?);
        let (hh, mm, ss) = (n(&at[11..13])?, n(&at[14..16])?, n(&at[17..19])?);
        Some(days_from_civil(y, m, d) * 86_400 + hh * 3600 + mm * 60 + ss)
    }

    // Howard Hinnant's civil-date algorithms.
    fn civil(z: i64) -> (i64, i64, i64) {
        let z = z + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z - era * 146_097;
        let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = doy - (153 * mp + 2) / 5 + 1;
        let m = if mp < 10 { mp + 3 } else { mp - 9 };
        (if m <= 2 { y + 1 } else { y }, m, d)
    }

    fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
        let y = if m <= 2 { y - 1 } else { y };
        let era = y.div_euclid(400);
        let yoe = y - era * 400;
        let mp = if m > 2 { m - 3 } else { m + 9 };
        let doy = (153 * mp + 2) / 5 + d - 1;
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        era * 146_097 + doe - 719_468
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn iso_round_trips() {
            for t in [0i64, 1_792_091_057, 951_782_400] {
                assert_eq!(parse(&Now(t).iso()), Some(t));
            }
            assert_eq!(Now(1_792_091_057).iso(), "2026-10-15T19:04:17Z", "as Python's datetime says");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dbs() -> (Connection, Connection) {
        let l = Connection::open_in_memory().unwrap();
        l.execute_batch(lempi_core::db::FILE_CHECKS_TABLE).unwrap();
        l.execute_batch("CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);")
            .unwrap();
        let b = Connection::open_in_memory().unwrap();
        b.execute_batch("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, sha256 TEXT);")
            .unwrap();
        (l, b)
    }

    fn sha(b: &[u8]) -> String {
        use sha2::Digest;
        sha2::Sha256::digest(b).iter().map(|x| format!("{x:02x}")).collect()
    }

    /// `[GDE-INT-130]`: each outcome, from real files: whole, re-tagged,
    /// damaged, missing; and a stop part-way read as no check at all.
    #[test]
    fn a_file_is_ok_retagged_damaged_or_missing() {
        let d = std::env::temp_dir().join(format!("lempi-scrub-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join("a.mp3");
        std::fs::write(&p, b"the bytes").unwrap();
        let want = sha(b"the bytes");
        let same = |_: &Path| Ok("md5".to_string());
        let other = |_: &Path| Ok("other".to_string());
        assert_eq!(check_file(&p, &want, "md5", same, |_| true).unwrap().outcome, "ok");
        std::fs::write(&p, b"the bytes, re-tagged").unwrap();
        assert_eq!(check_file(&p, &want, "md5", same, |_| true).unwrap().outcome, "retagged");
        assert_eq!(check_file(&p, &want, "md5", other, |_| true).unwrap().outcome, "damaged");
        assert!(check_file(&p, &want, "md5", same, |_| false).is_none(), "stopped part-way: no check");
        assert_eq!(check_file(&d.join("gone.mp3"), &want, "md5", same, |_| true).unwrap().outcome, "missing");
        std::fs::remove_dir_all(&d).ok();
    }

    /// The order: a suspect first, then never checked, then not ok after an
    /// hour, then ok after a week; nothing when all is recent.
    #[test]
    fn the_next_file_is_the_one_most_owed_a_read() {
        let (l, b) = dbs();
        let now = 1_800_000_000i64;
        let iso = |ago: i64| chrono_like::Now(now - ago).iso();
        b.execute_batch(
            "INSERT INTO files VALUES (1,'fresh','/m/fresh.mp3','s1'), (2,'bad','/m/bad.mp3','s2'),
                                      (3,'stale','/m/stale.mp3','s3'), (4,'new','/m/new.mp3','s4'),
                                      (5,'nohash','/m/nohash.mp3',NULL);",
        )
        .unwrap();
        for (m, ago, o) in [("fresh", 60, "ok"), ("bad", 7200, "damaged"), ("stale", 8 * 86_400, "ok")] {
            l.execute("INSERT INTO file_checks (audio_md5, checked_at, outcome) VALUES (?1,?2,?3)",
                      params![m, iso(ago), o])
                .unwrap();
        }
        assert_eq!(next_due(&l, &b, now).unwrap().unwrap().audio_md5, "new", "never checked first");
        l.execute("INSERT INTO file_checks (audio_md5, checked_at, outcome) VALUES ('new', ?1, 'ok')", [iso(1)])
            .unwrap();
        assert_eq!(next_due(&l, &b, now).unwrap().unwrap().audio_md5, "bad", "then one not ok, an hour on");
        l.execute("UPDATE file_checks SET checked_at = ?1 WHERE audio_md5 = 'bad'", [iso(60)]).unwrap();
        assert_eq!(next_due(&l, &b, now).unwrap().unwrap().audio_md5, "stale", "then an ok one a week old");
        l.execute("UPDATE file_checks SET checked_at = ?1 WHERE audio_md5 = 'stale'", [iso(60)]).unwrap();
        assert!(next_due(&l, &b, now).unwrap().is_none(), "nothing due: the file with no hash is not read");
        suspect(Path::new("/m/fresh.mp3"));
        let d = next_due(&l, &b, now).unwrap().unwrap();
        assert!(d.audio_md5 == "fresh" && d.urgent, "a file playback failed on comes first, however recent");
        assert!(next_due(&l, &b, now).unwrap().is_none(), "and only once");
    }

    /// A reload is asked only when a file moves into or out of what the
    /// Director withholds.
    #[test]
    fn a_reload_is_owed_only_when_playability_changes() {
        let (l, _) = dbs();
        let due = Due { audio_md5: "m".into(), path: PathBuf::from("/m/a.mp3"), sha256: "s".into(), urgent: false };
        let c = |o: &'static str| Check { outcome: o, size: Some(1), detail: None };
        assert!(!record(&l, &due, &c("ok"), "t1").unwrap(), "first read, ok: nothing to reload");
        assert!(record(&l, &due, &c("damaged"), "t2").unwrap(), "ok to damaged: withhold it");
        assert!(!record(&l, &due, &c("missing"), "t3").unwrap(), "still unplayable: nothing new");
        assert!(record(&l, &due, &c("ok"), "t4").unwrap(), "repaired: let it play again");
        assert!(!record(&l, &due, &c("retagged"), "t5").unwrap(), "re-tagged plays: nothing to reload");
    }

    #[test]
    fn the_rate_is_the_listeners_or_the_default() {
        let (l, _) = dbs();
        assert_eq!(rate(&l), DEFAULT_KIB_PER_S * 1024);
        l.execute("INSERT INTO player_settings VALUES (?1, '0', 't')", [RATE_KEY]).unwrap();
        assert_eq!(rate(&l), 0, "0 is off");
    }
}
