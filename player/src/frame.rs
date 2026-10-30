//! The picture frame on this machine's own screen `[SPEC068]`.
//!
//! The player serves the frame's page (`web/picture_frame.rs`) and starts a
//! browser in kiosk mode to show it, on a profile of its own beside the
//! player's state `[SPEC-PIC-110]`. Switched off, the browser is ended and
//! whatever was underneath is as it was `[SPEC-PIC-120]`. Its pictures are
//! served only to that browser, which alone holds the token made when it was
//! started `[SPEC-PIC-300]`.
//!
//! One thread watches it: a browser that exits within `STARTED_AFTER` of its
//! start did not start, and is tried again after `RETRY`; one that exits later,
//! not ended here, was closed on the screen, and the switch turns off
//! `[SPEC-PIC-130]`. The same thread starts the frame when the switch is on
//! and nothing shows -- at start, or once a display appears.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

pub const CLOCK_PCT_DEFAULT: u32 = 15;
pub const CLOCK_PCT_MIN: u32 = 5;
pub const CLOCK_PCT_MAX: u32 = 40;
/// What is shown `[SPEC-PIC-050]`; anything else in the folder is left out.
const EXTENSIONS: &[&str] = &["jpg", "jpeg", "png", "webp", "gif"];
/// A folder is read again after this, so a picture added appears.
const RESCAN: Duration = Duration::from_secs(3600);
/// The most pictures listed: a folder named by mistake (a whole disk) is cut
/// short, not walked for an hour.
const MOST: usize = 100_000;
/// A browser gone sooner than this after its start never started.
const STARTED_AFTER: Duration = Duration::from_secs(10);
/// How long before a frame that could not show is tried again.
const RETRY: Duration = Duration::from_secs(60);
/// The browser's own profile, beside the listener, and the files that let a
/// later player end a browser an earlier one left behind and put back the
/// screen's blanking an earlier one turned off.
const PROFILE: &str = "frame-browser";
const PID_FILE: &str = "frame-browser.pid";
const XSET_FILE: &str = "frame-xset.txt";
/// Its X window class, so it can be found and raised `[SPEC-PIC-120]`.
const WM_CLASS: &str = "LempiFrame";

/// Where the frame stands.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Shown {
    Off,
    Starting,
    Showing,
    /// Closed on the screen; the switch turned itself off `[SPEC-PIC-130]`.
    Closed,
    /// Could not be shown; tried again after `RETRY`.
    Failed,
}

struct Frame {
    listener: PathBuf,
    library: PathBuf,
    port: u16,
    on: bool,
    folder: Option<String>,
    clock_pct: u32,
    shown: Shown,
    why: Option<String>,
    child: Option<Child>,
    started_at: Option<Instant>,
    retry_at: Option<Instant>,
    token: Option<String>,
    pictures: Vec<PathBuf>,
    scanned: Option<(PathBuf, Instant)>,
    /// What X's blanking was before the frame turned it off, as the `xset`
    /// arguments that put it back `[SPEC-PIC-060]`.
    xset_restore: Option<Vec<Vec<String>>>,
}

static FRAME: Mutex<Option<Frame>> = Mutex::new(None);

fn with<T>(f: impl FnOnce(&mut Frame) -> T) -> Option<T> {
    FRAME.lock().ok()?.as_mut().map(f)
}

/// Start watching, at player start, on a host that serves the network: a
/// phone, serving its own loopback alone, has no frame `[SPEC-PIC-140]`.
pub fn init(listener: &Path, library: &Path, port: u16) {
    let (on, folder, pct) = crate::db::PlayerStore::open_split(listener, library)
        .map(|s| s.load_frame())
        .unwrap_or((false, None, None));
    let state = state_dir(listener);
    // A browser and a blanking an earlier player left behind, put right first.
    end_stale(&state);
    if let Ok(text) = std::fs::read_to_string(state.join(XSET_FILE)) {
        let restore: Vec<Vec<String>> =
            text.lines().map(|l| l.split_whitespace().map(str::to_string).collect()).collect();
        xset_apply(&restore);
        let _ = std::fs::remove_file(state.join(XSET_FILE));
        tracing::info!("frame: put back the screen blanking an earlier player had turned off");
    }
    let frame = Frame {
        listener: listener.to_path_buf(),
        library: library.to_path_buf(),
        port,
        on,
        folder,
        clock_pct: pct.filter(|p| (CLOCK_PCT_MIN..=CLOCK_PCT_MAX).contains(p)).unwrap_or(CLOCK_PCT_DEFAULT),
        shown: Shown::Off,
        why: None,
        child: None,
        started_at: None,
        retry_at: None,
        token: None,
        pictures: Vec::new(),
        scanned: None,
        xset_restore: None,
    };
    match available() {
        Ok(()) => tracing::info!("frame: offered here; {}", if on { "switched on, so showing" } else { "off" }),
        Err(why) => tracing::info!("frame: not offered here: {why} `[SPEC-PIC-140]`"),
    }
    if let Ok(mut g) = FRAME.lock() {
        *g = Some(frame);
    }
    let _ = std::thread::Builder::new().name("lempi-frame".into()).spawn(|| loop {
        std::thread::sleep(Duration::from_secs(1));
        with(tick);
    });
}

/// One look, each second: has the browser gone, is it now started, and
/// should a frame be shown that is not.
fn tick(f: &mut Frame) {
    if let Some(child) = f.child.as_mut() {
        if let Ok(Some(status)) = child.try_wait() {
            let ran = f.started_at.map_or(Duration::ZERO, |t| t.elapsed());
            f.child = None;
            f.token = None;
            xset_put_back(f);
            let _ = std::fs::remove_file(state_dir(&f.listener).join(PID_FILE));
            if ran < STARTED_AFTER {
                f.shown = Shown::Failed;
                f.why = Some(format!("the browser exited after {:.0} s ({status})", ran.as_secs_f64()));
                f.retry_at = Some(Instant::now() + RETRY);
                tracing::warn!("frame: {}; trying again in {} s", f.why.as_deref().unwrap_or(""), RETRY.as_secs());
            } else {
                f.shown = Shown::Closed;
                f.why = None;
                f.on = false;
                save(f, |s| s.save_frame_on(false));
                tracing::info!("frame: closed on the screen after {} s; the switch is off `[SPEC-PIC-130]`",
                               ran.as_secs());
            }
            return;
        }
        if f.shown == Shown::Starting && f.started_at.is_some_and(|t| t.elapsed() >= STARTED_AFTER) {
            f.shown = Shown::Showing;
            tracing::info!("frame: showing");
        }
        return;
    }
    if f.on && f.retry_at.is_none_or(|t| Instant::now() >= t) {
        show(f);
    }
}

fn save(f: &Frame, op: impl FnOnce(&crate::db::PlayerStore) -> Result<(), crate::db::DbError>) -> bool {
    match crate::db::PlayerStore::open_split(&f.listener, &f.library) {
        Ok(s) => op(&s).is_ok(),
        Err(_) => false,
    }
}

fn show(f: &mut Frame) {
    f.retry_at = Some(Instant::now() + RETRY);
    let browser = match available().and_then(|()| find_browser().ok_or_else(no_browser)) {
        Ok(b) => b,
        Err(why) => {
            if f.shown != Shown::Failed || f.why.as_deref() != Some(why.as_str()) {
                tracing::info!("frame: switched on, and cannot show: {why}");
            }
            f.shown = Shown::Failed;
            f.why = Some(why);
            return;
        }
    };
    let state = state_dir(&f.listener);
    end_stale(&state);
    let token = random_hex(16);
    let url = format!("http://127.0.0.1:{}/frame?t={token}", f.port);
    let profile = state.join(PROFILE);
    let mut cmd = Command::new(&browser);
    cmd.args(browser_args(&browser, &url, &profile)).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    let screen = x_display();
    if let Some((d, auth)) = &screen {
        cmd.env("DISPLAY", d);
        if let Some(a) = auth {
            cmd.env("XAUTHORITY", a);
        }
    }
    match cmd.spawn() {
        Ok(child) => {
            let _ = std::fs::write(state.join(PID_FILE), child.id().to_string());
            tracing::info!("frame: started {} (pid {}) on {}", browser.display(), child.id(),
                           screen.as_ref().map_or("this session's desktop", |(d, _)| d.as_str()));
            f.child = Some(child);
            f.started_at = Some(Instant::now());
            f.shown = Shown::Starting;
            f.why = None;
            f.token = Some(token);
            f.retry_at = None;
            if let Some((d, auth)) = screen {
                xset_hold(f, &d, auth.as_deref());
                raise(d, auth);
            }
        }
        Err(e) => {
            f.shown = Shown::Failed;
            f.why = Some(format!("could not start {}: {e}", browser.display()));
            tracing::warn!("frame: {}", f.why.as_deref().unwrap_or(""));
        }
    }
}

fn hide(f: &mut Frame) {
    f.token = None;
    if let Some(mut child) = f.child.take() {
        end(&mut child);
        tracing::info!("frame: hidden");
    }
    let _ = std::fs::remove_file(state_dir(&f.listener).join(PID_FILE));
    xset_put_back(f);
    f.shown = Shown::Off;
    f.why = None;
    f.retry_at = None;
}

/// End the browser: asked first, on Linux, so it closes as it would by hand.
fn end(child: &mut Child) {
    #[cfg(unix)]
    {
        let _ = Command::new("kill").args(["-TERM", &child.id().to_string()]).status();
        for _ in 0..30 {
            if child.try_wait().is_ok_and(|s| s.is_some()) {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    #[cfg(windows)]
    {
        // The whole tree: Edge and Chrome run their pages in processes of their own.
        let _ = Command::new("taskkill").args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdout(Stdio::null()).stderr(Stdio::null()).status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

/// A browser an earlier player started on this profile and did not end: Chrome
/// hands a second start on the same profile to the first and exits, so it must
/// go before a new one can start.
fn end_stale(state: &Path) {
    let file = state.join(PID_FILE);
    let Some(pid) = std::fs::read_to_string(&file).ok().and_then(|s| s.trim().parse::<u32>().ok()) else { return };
    let _ = std::fs::remove_file(&file);
    #[cfg(target_os = "linux")]
    {
        let profile = state.join(PROFILE);
        let ours = std::fs::read(format!("/proc/{pid}/cmdline"))
            .is_ok_and(|c| String::from_utf8_lossy(&c).contains(&*profile.to_string_lossy()));
        if ours {
            let _ = Command::new("kill").args(["-TERM", &pid.to_string()]).status();
            std::thread::sleep(Duration::from_secs(2));
            let _ = Command::new("kill").args(["-KILL", &pid.to_string()]).stderr(Stdio::null()).status();
            tracing::info!("frame: ended the browser (pid {pid}) an earlier player left showing");
        }
    }
    #[cfg(windows)]
    {
        let image = Command::new("tasklist").args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
            .output().map(|o| String::from_utf8_lossy(&o.stdout).to_lowercase()).unwrap_or_default();
        if image.contains("msedge.exe") || image.contains("chrome.exe") {
            let _ = Command::new("taskkill").args(["/PID", &pid.to_string(), "/T", "/F"])
                .stdout(Stdio::null()).stderr(Stdio::null()).status();
            tracing::info!("frame: ended the browser (pid {pid}) an earlier player left showing");
        }
    }
}

/// Raise the frame's window over a full-screen program, where `xdotool` is
/// there to do it `[SPEC-PIC-120]`. Looked for for fifteen seconds.
fn raise(display: String, auth: Option<PathBuf>) {
    if which("xdotool").is_none() {
        return;
    }
    let _ = std::thread::Builder::new().name("lempi-frame-raise".into()).spawn(move || {
        let run = |args: &[&str]| {
            let mut c = Command::new("xdotool");
            c.args(args).env("DISPLAY", &display).stderr(Stdio::null());
            if let Some(a) = &auth {
                c.env("XAUTHORITY", a);
            }
            c.output().ok().filter(|o| o.status.success()).map(|o| String::from_utf8_lossy(&o.stdout).to_string())
        };
        for _ in 0..15 {
            std::thread::sleep(Duration::from_secs(1));
            let ids = run(&["search", "--onlyvisible", "--class", WM_CLASS]).unwrap_or_default();
            let ids: Vec<&str> = ids.split_whitespace().collect();
            if !ids.is_empty() {
                for id in &ids {
                    let _ = run(&["windowactivate", id]);
                }
                tracing::info!("frame: raised its window ({})", ids.join(", "));
                return;
            }
        }
        tracing::info!("frame: its window was not found to raise; it shows wherever the desktop put it");
    });
}

// ------------------------------------------------------------ the screen --

/// Whether a frame can be shown here, or why not `[SPEC-PIC-140]`.
pub fn available() -> Result<(), String> {
    #[cfg(target_os = "android")]
    {
        Err("a phone has no frame".into())
    }
    #[cfg(all(unix, not(target_os = "android"), not(target_os = "macos")))]
    {
        if x_display().is_some() || std::env::var_os("WAYLAND_DISPLAY").is_some() {
            Ok(())
        } else {
            Err("no display: none is named in this player's environment, and no X server is running".into())
        }
    }
    #[cfg(windows)]
    {
        if std::env::var_os("SESSIONNAME").is_some() {
            Ok(())
        } else {
            Err("this player is not running in a signed-in person's session".into())
        }
    }
    #[cfg(target_os = "macos")]
    {
        Err("not built for this system yet".into())
    }
}

/// The X display, and its authority file where one is found: the player's
/// own environment first, else the first X server's socket -- a player started
/// at boot, before the session, is not given one.
fn x_display() -> Option<(String, Option<PathBuf>)> {
    if !cfg!(all(unix, not(target_os = "android"), not(target_os = "macos"))) {
        return None;
    }
    let display = std::env::var("DISPLAY").ok().filter(|d| !d.is_empty())
        .or_else(|| Path::new("/tmp/.X11-unix/X0").exists().then(|| ":0".to_string()))?;
    let auth = std::env::var_os("XAUTHORITY").map(PathBuf::from).filter(|p| p.exists()).or_else(|| {
        let home = std::env::var_os("HOME").map(PathBuf::from);
        let uid = std::fs::read_to_string("/proc/self/status").ok().and_then(|s| {
            s.lines().find(|l| l.starts_with("Uid:"))?.split_whitespace().nth(1).map(str::to_string)
        });
        [home.map(|h| h.join(".Xauthority")), uid.map(|u| PathBuf::from(format!("/run/user/{u}/gdm/Xauthority")))]
            .into_iter().flatten().find(|p| p.exists())
    });
    Some((display, auth))
}

/// Looked for in this order `[SPEC-PIC-110]`.
fn find_browser() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        let mut at: Vec<PathBuf> = Vec::new();
        for var in ["ProgramFiles(x86)", "ProgramFiles"] {
            if let Some(p) = std::env::var_os(var) {
                at.push(PathBuf::from(&p).join(r"Microsoft\Edge\Application\msedge.exe"));
            }
        }
        for var in ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"] {
            if let Some(p) = std::env::var_os(var) {
                at.push(PathBuf::from(&p).join(r"Google\Chrome\Application\chrome.exe"));
            }
        }
        at.into_iter().find(|p| p.is_file())
    }
    #[cfg(not(windows))]
    {
        ["google-chrome", "chromium", "chromium-browser"].into_iter().find_map(which)
    }
}

fn no_browser() -> String {
    if cfg!(windows) {
        "no browser found: looked for Edge, then Chrome".into()
    } else {
        "no browser found: looked for google-chrome, chromium and chromium-browser".into()
    }
}

fn which(name: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|p| std::env::split_paths(&p).map(|d| d.join(name)).find(|p| p.is_file()))
}

/// Full screen, its own profile, and nothing that asks anything of a person
/// standing in front of a television.
fn browser_args(browser: &Path, url: &str, profile: &Path) -> Vec<String> {
    let mut a = vec![
        "--kiosk".to_string(),
        format!("--user-data-dir={}", profile.display()),
        "--no-first-run".into(),
        "--no-default-browser-check".into(),
        "--noerrdialogs".into(),
        "--disable-session-crashed-bubble".into(),
        "--hide-crash-restore-bubble".into(),
        "--disable-infobars".into(),
        "--disable-translate".into(),
        "--disable-features=Translate".into(),
        "--overscroll-history-navigation=0".into(),
    ];
    let name = browser.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    if name.starts_with("msedge") {
        a.push("--edge-kiosk-type=fullscreen".into());
    }
    if cfg!(unix) {
        // No keyring prompt for a profile nobody signs in to, and a class to find it by.
        a.push("--password-store=basic".into());
        a.push(format!("--class={WM_CLASS}"));
    }
    a.push(url.to_string());
    a
}

// ----------------------------------------------------- holding it awake --

/// Turn X's screen saver and DPMS off while the frame shows, keeping what puts
/// them back, on disk too, for a player that dies first `[SPEC-PIC-060]`.
fn xset_hold(f: &mut Frame, display: &str, auth: Option<&Path>) {
    let Some(q) = xset_run(display, auth, &["q"]) else {
        tracing::info!("frame: xset is not here to read, so the screen's blanking is left as it is");
        return;
    };
    let restore = xset_restore_args(&q);
    if restore.is_empty() {
        return;
    }
    let _ = std::fs::write(
        state_dir(&f.listener).join(XSET_FILE),
        restore.iter().map(|a| a.join(" ")).collect::<Vec<_>>().join("\n"),
    );
    let _ = xset_run(display, auth, &["s", "off"]);
    let _ = xset_run(display, auth, &["-dpms"]);
    tracing::info!("frame: screen saver and DPMS off while it shows; put back as {:?} after", restore);
    f.xset_restore = Some(restore);
}

fn xset_put_back(f: &mut Frame) {
    if let Some(restore) = f.xset_restore.take() {
        xset_apply(&restore);
        let _ = std::fs::remove_file(state_dir(&f.listener).join(XSET_FILE));
        tracing::info!("frame: screen saver and DPMS put back");
    }
}

fn xset_apply(restore: &[Vec<String>]) {
    let Some((display, auth)) = x_display() else { return };
    for args in restore {
        let a: Vec<&str> = args.iter().map(String::as_str).collect();
        let _ = xset_run(&display, auth.as_deref(), &a);
    }
}

fn xset_run(display: &str, auth: Option<&Path>, args: &[&str]) -> Option<String> {
    let mut c = Command::new("xset");
    c.args(args).env("DISPLAY", display).stderr(Stdio::null());
    if let Some(a) = auth {
        c.env("XAUTHORITY", a);
    }
    c.output().ok().filter(|o| o.status.success()).map(|o| String::from_utf8_lossy(&o.stdout).to_string())
}

/// From `xset q`, the arguments that put the screen saver and DPMS back as
/// they were: `s TIMEOUT CYCLE`, `dpms STANDBY SUSPEND OFF`, and `+dpms` or
/// `-dpms`.
fn xset_restore_args(q: &str) -> Vec<Vec<String>> {
    let after = |line: &str, word: &str| -> Option<String> {
        let mut it = line.split_whitespace();
        it.position(|w| w == word)?;
        it.next().map(str::to_string)
    };
    let mut out = Vec::new();
    if let Some(l) = q.lines().find(|l| l.contains("timeout:") && l.contains("cycle:")) {
        if let (Some(t), Some(c)) = (after(l, "timeout:"), after(l, "cycle:")) {
            out.push(vec!["s".into(), t, c]);
        }
    }
    if let Some(l) = q.lines().find(|l| l.contains("Standby:") && l.contains("Off:")) {
        if let (Some(a), Some(b), Some(c)) = (after(l, "Standby:"), after(l, "Suspend:"), after(l, "Off:")) {
            out.push(vec!["dpms".into(), a, b, c]);
        }
    }
    if q.contains("DPMS is Enabled") {
        out.push(vec!["+dpms".into()]);
    } else if q.contains("DPMS is Disabled") {
        out.push(vec!["-dpms".into()]);
    }
    out
}

// ------------------------------------------------------------ the folder --

/// The folder a person named, or the user's own Pictures `[SPEC-PIC-050]`.
fn folder_of(f: &Frame) -> PathBuf {
    f.folder.as_deref().map(PathBuf::from).unwrap_or_else(default_folder)
}

pub fn default_folder() -> PathBuf {
    let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from);
    home.unwrap_or_default().join("Pictures")
}

/// Every picture under `root`, its subfolders included, whose resolved path
/// lies under the resolved folder: a link cannot lead out of it `[SPEC-PIC-300]`.
pub fn scan(root: &Path) -> Vec<PathBuf> {
    let Ok(root) = std::fs::canonicalize(root) else { return Vec::new() };
    let mut out = Vec::new();
    // Each folder once: a link back to a folder above it would walk forever.
    let mut seen = std::collections::HashSet::from([root.clone()]);
    let mut dirs = vec![root.clone()];
    while let Some(d) = dirs.pop() {
        let Ok(entries) = std::fs::read_dir(&d) else { continue };
        for e in entries.flatten() {
            let Ok(real) = std::fs::canonicalize(e.path()) else { continue };
            if !real.starts_with(&root) {
                continue;
            }
            if real.is_dir() {
                if seen.insert(real.clone()) {
                    dirs.push(real);
                }
            } else if real.is_file() && is_picture(&real) {
                out.push(real);
                if out.len() >= MOST {
                    out.sort();
                    return out;
                }
            }
        }
    }
    out.sort();
    out.dedup();
    out
}

fn is_picture(p: &Path) -> bool {
    p.extension().and_then(|e| e.to_str()).is_some_and(|e| EXTENSIONS.contains(&e.to_lowercase().as_str()))
}

fn pictures(f: &mut Frame) -> &[PathBuf] {
    let folder = folder_of(f);
    let stale = f.scanned.as_ref().is_none_or(|(at, when)| *at != folder || when.elapsed() >= RESCAN);
    if stale {
        f.pictures = scan(&folder);
        f.scanned = Some((folder, Instant::now()));
    }
    &f.pictures
}

// ------------------------------------------------------------- the page --

/// The page's token is the one the running browser was given.
pub fn token_ok(t: &str) -> bool {
    with(|f| f.token.as_deref().is_some_and(|ours| !t.is_empty() && ours == t)).unwrap_or(false)
}

/// The next picture, at random and not `last`, with what the page needs.
pub fn next(t: &str, last: Option<usize>) -> Option<Value> {
    if !token_ok(t) {
        return None;
    }
    with(|f| {
        let pct = f.clock_pct;
        let folder = folder_of(f);
        let count = pictures(f).len();
        let n = match count {
            0 => None,
            1 => Some(0),
            _ => {
                use rand_core::RngCore;
                let mut n = (rand_core::OsRng.next_u32() as usize) % count;
                if Some(n) == last {
                    n = (n + 1 + (rand_core::OsRng.next_u32() as usize) % (count - 1)) % count;
                }
                Some(n)
            }
        };
        json!({"n": n, "count": count, "clock_pct": pct,
               "note": (count == 0).then(|| format!("no pictures in {}", folder.display()))})
    })
}

/// The file of picture `n`, for the page holding the token.
pub fn picture(t: &str, n: usize) -> Option<PathBuf> {
    if !token_ok(t) {
        return None;
    }
    with(|f| pictures(f).get(n).cloned()).flatten()
}

pub fn content_type(p: &Path) -> &'static str {
    match p.extension().and_then(|e| e.to_str()).map(str::to_lowercase).as_deref() {
        Some("png") => "image/png",
        Some("webp") => "image/webp",
        Some("gif") => "image/gif",
        _ => "image/jpeg",
    }
}

// --------------------------------------------------------- the settings --

/// Where the frame stands, for the Settings panel `[SPEC-PIC-200]`; `None`
/// on a host that has no frame at all -- a phone.
pub fn status() -> Option<Value> {
    let offered = available().and_then(|()| find_browser().map(|_| ()).ok_or_else(no_browser));
    with(|f| {
        let folder = folder_of(f);
        let count = pictures(f).len();
        let pics = format!("{count} picture{} in {}", if count == 1 { "" } else { "s" }, folder.display());
        let line = match (&offered, f.shown) {
            (Err(why), _) => format!("not available: {why}"),
            (_, Shown::Showing) => format!("showing, {pics}"),
            (_, Shown::Starting) => format!("starting, {pics}"),
            (_, Shown::Closed) => "closed on the screen".to_string(),
            (_, Shown::Failed) => format!("could not show: {}; tried again each minute",
                                          f.why.as_deref().unwrap_or("unknown")),
            (_, Shown::Off) => format!("off, {pics}"),
        };
        json!({"available": offered.is_ok(), "why_not": offered.as_ref().err(), "on": f.on,
               "folder": folder.display().to_string(), "default_folder": default_folder().display().to_string(),
               "clock_pct": f.clock_pct, "clock_min": CLOCK_PCT_MIN, "clock_max": CLOCK_PCT_MAX,
               "shown": f.shown, "pictures": count, "status": line})
    })
}

const NO_FRAME: &str = "this player has no picture frame";

pub fn set_on(on: bool) -> Result<Value, String> {
    with(|f| {
        if !save(f, |s| s.save_frame_on(on)) {
            return Err("could not save".to_string());
        }
        f.on = on;
        if on {
            f.retry_at = None;
            if f.shown == Shown::Closed {
                f.shown = Shown::Off;
            }
            if f.child.is_none() {
                show(f);
            }
        } else {
            hide(f);
        }
        Ok(())
    })
    .ok_or(NO_FRAME)??;
    status().ok_or_else(|| NO_FRAME.to_string())
}

/// A folder a person named: it must be there and readable. Empty is the
/// default. `~` is the user's home.
pub fn set_folder(folder: &str) -> Result<Value, String> {
    let folder = folder.trim();
    let resolved = if folder.is_empty() {
        None
    } else {
        let p = match folder.strip_prefix("~/").or_else(|| folder.strip_prefix("~\\")) {
            Some(rest) => default_folder().parent().map(|h| h.join(rest)).unwrap_or_else(|| PathBuf::from(folder)),
            None => PathBuf::from(folder),
        };
        if !p.is_dir() {
            return Err(format!("{} is not a folder here", p.display()));
        }
        std::fs::read_dir(&p).map_err(|e| format!("{} cannot be read: {e}", p.display()))?;
        Some(p.display().to_string())
    };
    with(|f| {
        if !save(f, |s| s.save_frame_folder(resolved.as_deref().unwrap_or(""))) {
            return Err("could not save".to_string());
        }
        f.folder = resolved;
        f.scanned = None;
        Ok(())
    })
    .ok_or(NO_FRAME)??;
    status().ok_or_else(|| NO_FRAME.to_string())
}

pub fn set_clock_pct(pct: u32) -> Result<Value, String> {
    if !(CLOCK_PCT_MIN..=CLOCK_PCT_MAX).contains(&pct) {
        return Err(format!("the clock's size is {CLOCK_PCT_MIN}% to {CLOCK_PCT_MAX}% of the screen's height"));
    }
    with(|f| {
        if !save(f, |s| s.save_frame_clock_pct(pct)) {
            return Err("could not save".to_string());
        }
        f.clock_pct = pct;
        Ok(())
    })
    .ok_or(NO_FRAME)??;
    status().ok_or_else(|| NO_FRAME.to_string())
}

/// The player is stopping: the frame goes with it, and the blanking comes back.
pub fn shutdown() {
    with(hide);
}

fn state_dir(listener: &Path) -> PathBuf {
    listener.parent().map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from("."))
}

fn random_hex(n: usize) -> String {
    use rand_core::RngCore;
    let mut b = vec![0u8; n];
    rand_core::OsRng.fill_bytes(&mut b);
    b.iter().map(|x| format!("{x:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lempi-frame-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// `[SPEC-PIC-050]`: pictures by their kind, in subfolders too, and
    /// nothing else.
    #[test]
    fn a_folder_lists_its_pictures_and_its_subfolders_pictures_only() {
        let d = dir("scan");
        std::fs::create_dir_all(d.join("Wallpapers")).unwrap();
        for f in ["a.jpg", "b.JPEG", "c.png", "notes.txt", "Wallpapers/d.webp", "Wallpapers/e.gif", "Wallpapers/f.tiff"] {
            std::fs::write(d.join(f), b"x").unwrap();
        }
        let names: Vec<String> =
            scan(&d).iter().map(|p| p.file_name().unwrap().to_string_lossy().to_string()).collect();
        let mut sorted = names.clone();
        sorted.sort();
        assert_eq!(sorted, ["a.jpg", "b.JPEG", "c.png", "d.webp", "e.gif"]);
        assert!(scan(&d.join("missing")).is_empty(), "a missing folder lists nothing");
        let _ = std::fs::remove_dir_all(d);
    }

    /// `[SPEC-PIC-300]`: a link that leads out of the folder is not followed.
    #[cfg(unix)]
    #[test]
    fn a_link_out_of_the_folder_is_not_followed() {
        let d = dir("link");
        let (inside, outside) = (d.join("in"), d.join("out"));
        std::fs::create_dir_all(&inside).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(outside.join("secret.jpg"), b"x").unwrap();
        std::fs::write(inside.join("ok.jpg"), b"x").unwrap();
        std::os::unix::fs::symlink(outside.join("secret.jpg"), inside.join("link.jpg")).unwrap();
        std::os::unix::fs::symlink(&outside, inside.join("away")).unwrap();
        std::os::unix::fs::symlink(&inside, inside.join("loop")).unwrap();
        let got = scan(&inside);
        assert_eq!(got.len(), 1, "{got:?}");
        assert!(got[0].ends_with("ok.jpg"));
        let _ = std::fs::remove_dir_all(d);
    }

    /// `[SPEC-PIC-060]`: what `xset q` says becomes the arguments that put it back.
    #[test]
    fn the_blanking_is_read_back_as_the_arguments_that_restore_it() {
        let q = "Keyboard Control:\n  auto repeat:  on\nScreen Saver:\n  prefer blanking:  yes    allow exposures:  yes\n  \
                 timeout:  0    cycle:  600\nDPMS (Energy Star):\n  Standby: 600    Suspend: 600    Off: 600\n  \
                 DPMS is Enabled\n  Monitor is On\n";
        assert_eq!(xset_restore_args(q), vec![
            vec!["s".to_string(), "0".into(), "600".into()],
            vec!["dpms".to_string(), "600".into(), "600".into(), "600".into()],
            vec!["+dpms".to_string()],
        ]);
        assert!(xset_restore_args("").is_empty(), "nothing read, nothing to put back");
    }

    /// `[SPEC-PIC-110]`: kiosk on its own profile, the page last; Edge full screen.
    #[test]
    fn the_browser_is_started_full_screen_on_its_own_profile() {
        let a = browser_args(Path::new("google-chrome"), "http://127.0.0.1:5720/frame?t=x", Path::new("/s/frame-browser"));
        assert_eq!(a[0], "--kiosk");
        assert!(a.iter().any(|x| x == "--user-data-dir=/s/frame-browser"));
        assert_eq!(a.last().unwrap(), "http://127.0.0.1:5720/frame?t=x");
        let e = browser_args(Path::new(r"C:\Edge\msedge.exe"), "u", Path::new("p"));
        assert!(e.iter().any(|x| x == "--edge-kiosk-type=fullscreen"));
    }

    /// `[SPEC-PIC-300]`, `[SPEC-PIC-200]`: the one test that owns the frame's
    /// state, start to end. Nothing without a frame; with one, the page's
    /// answers only for its token, a picture by its number in the list and
    /// never twice running, and the clock's size only within its range.
    #[test]
    fn the_frame_answers_only_its_own_browser() {
        assert!(status().is_none() && !token_ok("") && next("x", None).is_none(), "no frame, nothing");
        let d = dir("state");
        rusqlite::Connection::open(d.join("library.db")).unwrap().execute_batch("CREATE TABLE t (a);").unwrap();
        let pics = d.join("pics");
        std::fs::create_dir_all(&pics).unwrap();
        for f in ["one.jpg", "two.png", "three.jpg"] {
            std::fs::write(pics.join(f), b"x").unwrap();
        }
        *FRAME.lock().unwrap() = Some(Frame {
            listener: d.join("listener.db"), library: d.join("library.db"), port: 0, on: false,
            folder: Some(pics.display().to_string()), clock_pct: CLOCK_PCT_DEFAULT, shown: Shown::Showing,
            why: None, child: None, started_at: None, retry_at: None, token: Some("abc".into()),
            pictures: Vec::new(), scanned: None, xset_restore: None,
        });
        assert!(token_ok("abc") && !token_ok("abd") && !token_ok(""), "its token, and only its token");
        assert!(next("abd", None).is_none() && picture("abd", 0).is_none(), "nothing for another");
        let v = next("abc", Some(1)).unwrap();
        assert_eq!((v["count"].as_u64(), v["clock_pct"].as_u64()), (Some(3), Some(15)));
        for _ in 0..50 {
            let n = next("abc", Some(1)).unwrap()["n"].as_u64().unwrap();
            assert!(n < 3 && n != 1, "never the one just shown: {n}");
        }
        assert!(picture("abc", 2).is_some_and(|p| p.starts_with(std::fs::canonicalize(&pics).unwrap())));
        assert!(picture("abc", 3).is_none(), "no picture past the list");
        assert!(set_clock_pct(4).unwrap_err().contains("5% to 40%"));
        assert_eq!(set_clock_pct(20).unwrap()["clock_pct"], 20);
        assert!(set_folder(&d.join("nowhere").display().to_string()).unwrap_err().contains("not a folder"));
        let s = status().unwrap();
        assert!(s["status"].as_str().unwrap().contains("3 pictures in"), "{s}");
        *FRAME.lock().unwrap() = None;
        let (on, folder, pct) = crate::db::PlayerStore::open_split(&d.join("listener.db"), &d.join("library.db"))
            .unwrap().load_frame();
        assert_eq!((on, folder, pct), (false, None, Some(20)), "the size was kept on this machine");
        let _ = std::fs::remove_dir_all(d);
    }

    #[test]
    fn a_picture_is_served_as_its_kind() {
        assert_eq!(content_type(Path::new("a.PNG")), "image/png");
        assert_eq!(content_type(Path::new("a.jpeg")), "image/jpeg");
        assert_eq!(content_type(Path::new("a.webp")), "image/webp");
    }
}
