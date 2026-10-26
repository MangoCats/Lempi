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
//! Settings page's own `/library/reload` asks for. `retagged` -- the bytes
//! changed, the audio intact -- is a finding shown, not withheld.
//!
//! **`[GDE-INT-137]` On a shared radio it waits for the music to pause.**
//! Decided by the maintainer 2026-10-25: where the node's Wi-Fi shares its
//! radio with the speaker playing (`crate::radio`), no file is read while the
//! music plays -- a read under way stops, unrecorded, and is begun again once
//! the music has paused. The music is never paused for it. Both of lempi02w's
//! Wi-Fi freezes came while this read its card at 1 MiB/s under Bluetooth
//! playback `[PI3-FOUND-830]`.
//!
//! **`[GDE-INT-138]` And for a bulk transfer to end.** A transfer hold pauses
//! the music `[PI3-AIM-170]`, which is the pause the check waits for, so an
//! export to lempi02w, 2026-10-26, ran with this reading its card at 1 MB/s
//! beside it -- the two together, as in both freezes. On a shared radio it
//! now waits while any hold is in force, the same way it waits for the music.

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
/// While the music plays on a shared radio, how often it looks again
/// `[GDE-INT-137]`.
const HELD: Duration = Duration::from_secs(30);
/// How long a read of the listener's or the catalogue's file waits for a
/// writer -- an import, a sync's commit -- before it gives up. `rusqlite`'s
/// own default is 5 s, and `bose`'s report answered "database is locked"
/// through an import that held its catalogue longer, 2026-10-25.
pub const BUSY: Duration = Duration::from_secs(10);
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
///
/// The findings are the listener's; only the count of files is the
/// catalogue's, which an import or a sync holds for longer than any wait is
/// worth. So a catalogue that stays busy costs the count -- `files` null,
/// `files_note` saying why -- and never the findings.
pub fn summary(listener: &Path, library: &Path) -> Result<serde_json::Value, String> {
    summary_waiting(listener, library, BUSY)
}

fn summary_waiting(listener: &Path, library: &Path, busy: Duration) -> Result<serde_json::Value, String> {
    let e = |e: rusqlite::Error| e.to_string();
    let l = Connection::open_with_flags(listener, OpenFlags::SQLITE_OPEN_READ_ONLY).map_err(e)?;
    l.busy_timeout(busy).map_err(e)?;
    let counted = Connection::open_with_flags(library, OpenFlags::SQLITE_OPEN_READ_ONLY).and_then(|b| {
        b.busy_timeout(busy)?;
        b.query_row("SELECT COUNT(*) FROM files WHERE sha256 IS NOT NULL AND sha256 <> ''", [], |r| r.get::<_, i64>(0))
    });
    let (files, files_note) = match counted {
        Ok(n) => (Some(n), None),
        Err(err) => (None, Some(format!("the catalogue could not be counted: {err}"))),
    };
    let has = l
        .query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_checks'", [], |_| Ok(()))
        .optional()
        .map_err(e)?
        .is_some();
    // Files with no tags -- no album name, no place in the browse index -- as
    // the catalogue says, beside what was read of them `[REQ-VIS-180]`. Null
    // when it cannot be counted, as `files` is.
    let untagged = crate::tags::untagged(library).ok();
    if !has {
        return Ok(serde_json::json!({"files": files, "files_note": files_note, "checked": 0, "counts": {},
                                     "not_ok": [], "oldest": null, "rate_kib_per_s": rate(&l) / 1024,
                                     "untagged": untagged}));
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
    Ok(serde_json::json!({"files": files, "files_note": files_note, "checked": checked, "counts": counts,
                          "not_ok": not_ok, "oldest": oldest, "rate_kib_per_s": rate(&l) / 1024,
                          "untagged": untagged}))
}

/// What the check must give way to `[GDE-INT-137]`: whether the music is
/// playing, and whether a bulk transfer is under way `[GDE-INT-138]`, both
/// asked before every chunk, and whether this node's radio is shared with
/// the speaker, asked before every file.
pub struct GiveWay {
    pub playing: Box<dyn Fn() -> bool + Send>,
    pub transfer: Box<dyn Fn() -> bool + Send>,
    pub shared_radio: Box<dyn Fn() -> bool + Send>,
}

impl GiveWay {
    /// Never gives way: a node with no speaker to share a radio with.
    pub fn never() -> Self {
        GiveWay { playing: Box::new(|| false), transfer: Box::new(|| false), shared_radio: Box::new(|| false) }
    }

    /// On a shared radio, wait: the music is playing, or a transfer is held.
    fn waits(&self, shared: bool) -> bool {
        shared && ((self.playing)() || (self.transfer)())
    }
}

/// Start the check `[GDE-INT-130]`. Ends when `stopped` does.
pub fn spawn(listener: PathBuf, library: PathBuf, controls: SharedControls, give_way: GiveWay, stopped: Receiver<()>) {
    let _ = std::thread::Builder::new().name("lempi-scrub".into()).spawn(move || {
        lower_priority();
        if !sleep(&stopped, START_DELAY) {
            return;
        }
        let mut idle_said = false;
        let mut held_said = false;
        loop {
            match one(&listener, &library, &controls, &give_way, &stopped) {
                Ok(Step::Stopped) => return,
                Ok(Step::Held) => {
                    if !held_said {
                        tracing::info!("integrity: music is playing or a transfer is under way on a shared radio; \
                                        reading again when neither is [GDE-INT-137] [GDE-INT-138]");
                        held_said = true;
                    }
                    if !sleep(&stopped, HELD) {
                        return;
                    }
                }
                Ok(Step::Checked) => {
                    idle_said = false;
                    held_said = false;
                }
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
    Held,
    Idle,
    Off,
    Stopped,
}

fn one(listener: &Path, library: &Path, controls: &SharedControls, give_way: &GiveWay,
       stopped: &Receiver<()>) -> Result<Step, String> {
    let e = |e: rusqlite::Error| e.to_string();
    // Asked once a file: it reads the database, and sometimes asks PipeWire.
    let shared = (give_way.shared_radio)();
    if give_way.waits(shared) {
        return Ok(Step::Held);
    }
    let l = Connection::open(listener).map_err(e)?;
    l.busy_timeout(BUSY).map_err(e)?;
    l.execute_batch(lempi_core::db::FILE_CHECKS_TABLE).map_err(e)?;
    let per_s = rate(&l);
    if per_s == 0 {
        return Ok(Step::Off);
    }
    let b = Connection::open_with_flags(library, OpenFlags::SQLITE_OPEN_READ_ONLY).map_err(e)?;
    b.busy_timeout(BUSY).map_err(e)?;
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
    let mut held = false;
    let checked = check_file(&due.path, &due.sha256, &due.audio_md5, crate::identity::hash_audio, |read| {
        if give_way.waits(shared) {
            held = true;
            return false;
        }
        let should = Duration::from_secs_f64(read as f64 / per_s as f64);
        let ahead = should.saturating_sub(start.elapsed());
        if !ahead.is_zero() && !sleep(stopped, ahead) {
            stop = true;
            return false;
        }
        true
    });
    let Some(c) = checked else {
        if held {
            // Put back at the front if playback had asked for it: the read
            // was stopped, not made.
            if due.urgent {
                suspect(&due.path);
            }
            return Ok(Step::Held);
        }
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

    /// A writer that outlasts the wait -- an import, as on `bose` 2026-10-25
    /// -- costs the report its count of files, and never its findings; one
    /// that finishes in time costs nothing.
    #[test]
    fn a_busy_catalogue_costs_the_count_not_the_findings() {
        let d = std::env::temp_dir().join(format!("lempi-scrub-busy-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let (lp, bp) = (d.join("listener.db"), d.join("library.db"));
        for f in [&lp, &bp] {
            let _ = std::fs::remove_file(f);
        }
        let l = Connection::open(&lp).unwrap();
        l.execute_batch(lempi_core::db::FILE_CHECKS_TABLE).unwrap();
        l.execute_batch("CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);")
            .unwrap();
        l.execute("INSERT INTO file_checks (audio_md5, checked_at, outcome, path) VALUES ('d', '2026-10-25T00:00:00Z', 'damaged', 'x.mp3')", [])
            .unwrap();
        drop(l);
        let b = Connection::open(&bp).unwrap();
        b.execute_batch("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, sha256 TEXT);")
            .unwrap();
        drop(b);
        // A writer holding the catalogue for half a second.
        let (held_tx, held) = std::sync::mpsc::channel();
        let bp2 = bp.clone();
        let writer = std::thread::spawn(move || {
            let w = Connection::open(&bp2).unwrap();
            w.execute_batch("BEGIN EXCLUSIVE; INSERT INTO files VALUES (1, 'a', 'p', 's');").unwrap();
            held_tx.send(()).unwrap();
            std::thread::sleep(Duration::from_millis(500));
            w.execute_batch("COMMIT;").unwrap();
        });
        held.recv().unwrap();
        // Waiting less than the writer holds it: the findings, without the count.
        let busy = summary_waiting(&lp, &bp, Duration::from_millis(50)).expect("a report, not an error");
        writer.join().unwrap();
        assert_eq!(busy["files"], serde_json::Value::Null, "the count is unknown");
        assert!(busy["files_note"].as_str().is_some_and(|n| n.contains("could not be counted")), "and said");
        assert_eq!(busy["not_ok"][0]["outcome"], "damaged", "the finding is still reported");
        // Free again: all of it.
        let free = summary(&lp, &bp).unwrap();
        assert_eq!(free["files"], serde_json::json!(1));
        assert_eq!(free["files_note"], serde_json::Value::Null);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// `[GDE-INT-137]`: on a shared radio, nothing is read while the music
    /// plays -- not begun, and a read under way stopped and not recorded --
    /// and everything is read once it pauses. Off a shared radio, the music
    /// playing changes nothing.
    #[test]
    fn on_a_shared_radio_the_check_waits_for_the_music() {
        use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
        use std::sync::Arc;
        let d = std::env::temp_dir().join(format!("lempi-scrub-give-way-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let (lp, bp, audio) = (d.join("listener.db"), d.join("library.db"), d.join("a.mp3"));
        for f in [&lp, &bp] {
            let _ = std::fs::remove_file(f);
        }
        // Two chunks' worth, so a read can be stopped part-way.
        let bytes = vec![7u8; CHUNK * 2];
        std::fs::write(&audio, &bytes).unwrap();
        Connection::open(&lp).unwrap()
            .execute_batch("CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);
                            INSERT INTO player_settings VALUES ('integrity_check_kib_per_s', '1000000', '');")
            .unwrap();
        let b = Connection::open(&bp).unwrap();
        b.execute_batch("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, sha256 TEXT);")
            .unwrap();
        b.execute("INSERT INTO files VALUES (1, 'aaa', ?1, ?2)", params![audio.to_string_lossy(), sha(&bytes)])
            .unwrap();
        drop(b);
        let checked = || -> i64 {
            Connection::open(&lp).unwrap()
                .query_row("SELECT COUNT(*) FROM file_checks", [], |r| r.get(0)).unwrap_or(0)
        };
        let (_tx, stopped) = std::sync::mpsc::channel::<()>();
        let controls = SharedControls::default();
        let playing = Arc::new(AtomicBool::new(true));
        let transfer = Arc::new(AtomicBool::new(false));
        let give_way = |shared: bool| {
            let (p, t) = (playing.clone(), transfer.clone());
            GiveWay {
                playing: Box::new(move || p.load(Ordering::SeqCst)),
                transfer: Box::new(move || t.load(Ordering::SeqCst)),
                shared_radio: Box::new(move || shared),
            }
        };

        // Playing on a shared radio: not begun.
        assert!(matches!(one(&lp, &bp, &controls, &give_way(true), &stopped), Ok(Step::Held)));
        assert_eq!(checked(), 0, "nothing was read");

        // The music starts during a read: stopped, and nothing recorded.
        playing.store(false, Ordering::SeqCst);
        let asked = Arc::new(AtomicUsize::new(0));
        let (p, n) = (playing.clone(), asked.clone());
        let starts_mid_read = GiveWay {
            playing: Box::new(move || {
                // Paused when the file is chosen; playing from the first chunk.
                if n.fetch_add(1, Ordering::SeqCst) >= 1 { p.store(true, Ordering::SeqCst) }
                p.load(Ordering::SeqCst)
            }),
            transfer: Box::new(|| false),
            shared_radio: Box::new(|| true),
        };
        assert!(matches!(one(&lp, &bp, &controls, &starts_mid_read, &stopped), Ok(Step::Held)));
        assert_eq!(checked(), 0, "a read the music interrupted is not recorded");

        // Paused: read, whole.
        playing.store(false, Ordering::SeqCst);
        assert!(matches!(one(&lp, &bp, &controls, &give_way(true), &stopped), Ok(Step::Checked)));
        assert_eq!(checked(), 1, "read once the music paused");

        // Off a shared radio the music playing changes nothing.
        Connection::open(&lp).unwrap().execute("DELETE FROM file_checks", []).unwrap();
        playing.store(true, Ordering::SeqCst);
        assert!(matches!(one(&lp, &bp, &controls, &give_way(false), &stopped), Ok(Step::Checked)));
        assert_eq!(checked(), 1, "a radio of its own reads while playing");
        let _ = std::fs::remove_dir_all(&d);
    }

    /// `[GDE-INT-138]`: a transfer's hold is waited for as the music is --
    /// the hold pauses the music, and that pause is not the check's to use.
    #[test]
    fn on_a_shared_radio_the_check_waits_for_a_transfer() {
        use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
        use std::sync::Arc;
        let d = std::env::temp_dir().join(format!("lempi-scrub-transfer-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let (lp, bp, audio) = (d.join("listener.db"), d.join("library.db"), d.join("a.mp3"));
        for f in [&lp, &bp] {
            let _ = std::fs::remove_file(f);
        }
        let bytes = vec![7u8; CHUNK * 2];
        std::fs::write(&audio, &bytes).unwrap();
        Connection::open(&lp).unwrap()
            .execute_batch("CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);
                            INSERT INTO player_settings VALUES ('integrity_check_kib_per_s', '1000000', '');")
            .unwrap();
        let b = Connection::open(&bp).unwrap();
        b.execute_batch("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT, sha256 TEXT);")
            .unwrap();
        b.execute("INSERT INTO files VALUES (1, 'aaa', ?1, ?2)", params![audio.to_string_lossy(), sha(&bytes)])
            .unwrap();
        drop(b);
        let checked = || -> i64 {
            Connection::open(&lp).unwrap()
                .query_row("SELECT COUNT(*) FROM file_checks", [], |r| r.get(0)).unwrap_or(0)
        };
        let (_tx, stopped) = std::sync::mpsc::channel::<()>();
        let controls = SharedControls::default();
        let held = Arc::new(AtomicBool::new(true));
        let give_way = |shared: bool| {
            let h = held.clone();
            GiveWay {
                playing: Box::new(|| false),
                transfer: Box::new(move || h.load(Ordering::SeqCst)),
                shared_radio: Box::new(move || shared),
            }
        };

        // Paused by a hold, on a shared radio: not begun.
        assert!(matches!(one(&lp, &bp, &controls, &give_way(true), &stopped), Ok(Step::Held)));
        assert_eq!(checked(), 0, "nothing is read while a transfer is held");

        // A hold arrives during a read: stopped, and nothing recorded.
        held.store(false, Ordering::SeqCst);
        let asked = Arc::new(AtomicUsize::new(0));
        let (h, n) = (held.clone(), asked.clone());
        let held_mid_read = GiveWay {
            playing: Box::new(|| false),
            transfer: Box::new(move || {
                if n.fetch_add(1, Ordering::SeqCst) >= 1 { h.store(true, Ordering::SeqCst) }
                h.load(Ordering::SeqCst)
            }),
            shared_radio: Box::new(|| true),
        };
        assert!(matches!(one(&lp, &bp, &controls, &held_mid_read, &stopped), Ok(Step::Held)));
        assert_eq!(checked(), 0, "a read a transfer interrupted is not recorded");

        // The hold released: read, whole.
        held.store(false, Ordering::SeqCst);
        assert!(matches!(one(&lp, &bp, &controls, &give_way(true), &stopped), Ok(Step::Checked)));
        assert_eq!(checked(), 1, "read once the transfer ended");

        // Off a shared radio a hold changes nothing.
        Connection::open(&lp).unwrap().execute("DELETE FROM file_checks", []).unwrap();
        held.store(true, Ordering::SeqCst);
        assert!(matches!(one(&lp, &bp, &controls, &give_way(false), &stopped), Ok(Step::Checked)));
        assert_eq!(checked(), 1, "a radio of its own reads through a transfer");
        let _ = std::fs::remove_dir_all(&d);
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
