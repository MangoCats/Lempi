//! The phone's way into the player `[REQ-AND-410]`: a generated interface for
//! everything the app asks of it, and one hand-written JNI entry.
//!
//! * `init` -- JNI, from `io.github.mangocats.lempi.Lempi`, once per process
//!   and before anything else. Hands the Android context to `ndk_context`,
//!   where cpal's AAudio backend reads it back `[GDE-HST-330]`, and sends the
//!   player's log lines to a file, since Android discards a native library's
//!   stderr. It is the one entry UniFFI cannot make: it takes the context.
//! * `start`, `command`, `set_volume`, `state`, `stop` -- UniFFI exports,
//!   generated for Kotlin by the workspace's `uniffi-bindgen`. A player with
//!   its web server on loopback behind the launch key `[REQ-AND-160]`,
//!   writing nothing beside the audio `[REQ-AND-200]`.
//!
//! **Refused, not crashed.** `start` before `init` is refused with a named
//! error rather than failing inside the audio stack `[REQ-AND-410]`, and a
//! panic is caught at the boundary and returned as an error: unwinding into
//! the JVM is undefined behaviour, not an error the app could show.
#![cfg(target_os = "android")]
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::ffi::{c_void, CStr, CString};
use std::os::fd::AsRawFd;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use jni_sys::{jclass, jobject, jstring, JNIEnv, JavaVM};
use lempi_player::engine::Command;
use lempi_player::host::{Config, Player};

/// Set once `init` has handed over the context. `ndk_context` asserts on a
/// second initialisation, so this is also what makes `init` safe to repeat.
static CONTEXT: OnceLock<()> = OnceLock::new();
static PLAYER: Mutex<Option<Player>> = Mutex::new(None);

/// A Java string as Rust's, or `None` for a null.
unsafe fn text(env: *mut JNIEnv, s: jstring) -> Option<String> {
    if s.is_null() {
        return None;
    }
    let chars = ((**env).v1_2.GetStringUTFChars)(env, s, std::ptr::null_mut());
    if chars.is_null() {
        return None;
    }
    let out = CStr::from_ptr(chars).to_string_lossy().into_owned();
    ((**env).v1_2.ReleaseStringUTFChars)(env, s, chars);
    Some(out)
}

/// `None` as Java's `null` -- success -- and an error as its message.
unsafe fn outcome(env: *mut JNIEnv, r: Result<(), String>) -> jstring {
    match r {
        Ok(()) => std::ptr::null_mut(),
        Err(e) => {
            let c = CString::new(e.replace('\0', " ")).unwrap_or_default();
            ((**env).v1_2.NewStringUTF)(env, c.as_ptr())
        }
    }
}

/// Run `f`, turning a panic into an error message.
fn guarded<T>(f: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or_else(|p| {
        let why = p
            .downcast_ref::<&str>()
            .map(|s| s.to_string())
            .or_else(|| p.downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "a panic with no message".into());
        Err(format!("panicked: {why}"))
    })
}

/// `static native String init(Context context, String logPath)`
#[no_mangle]
pub unsafe extern "system" fn Java_io_github_mangocats_lempi_Lempi_init(
    env: *mut JNIEnv,
    _class: jclass,
    context: jobject,
    log_path: jstring,
) -> jstring {
    let log = text(env, log_path);
    let r = guarded(|| {
        if let Some(path) = log {
            let f = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)
                .map_err(|e| format!("cannot open the log at {path}: {e}"))?;
            // The file is kept open by fd 2 after `f` drops.
            if libc::dup2(f.as_raw_fd(), 2) < 0 {
                return Err(format!("cannot send stderr to {path}"));
            }
        }
        lempi_player::logging::install("lempi-android");
        if CONTEXT.get().is_none() {
            let mut vm: *mut JavaVM = std::ptr::null_mut();
            if ((**env).v1_2.GetJavaVM)(env, &mut vm) != 0 || vm.is_null() {
                return Err("GetJavaVM failed".into());
            }
            // Global, so it outlives this call: cpal reads it on its own
            // threads, whenever it enumerates or opens a device.
            let global = ((**env).v1_2.NewGlobalRef)(env, context);
            if global.is_null() {
                return Err("NewGlobalRef on the context failed".into());
            }
            ndk_context::initialize_android_context(vm as *mut c_void, global as *mut c_void);
            let _ = CONTEXT.set(());
        }
        tracing::info!("lempi-android: context handed over; logging to this file");
        Ok(())
    });
    outcome(env, r)
}

uniffi::setup_scaffolding!();

/// Why a call was refused. Flat: Kotlin sees the variant and its message.
#[derive(Debug, uniffi::Error)]
#[uniffi(flat_error)]
pub enum LempiError {
    /// `init` has not handed over the Android context yet.
    NotInitialised,
    /// The player said no, or is not in the state the call needs.
    Refused(String),
}

impl std::fmt::Display for LempiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LempiError::NotInitialised => {
                f.write_str("init was not called: the player needs the Android context before start")
            }
            LempiError::Refused(why) => f.write_str(why),
        }
    }
}

impl From<String> for LempiError {
    fn from(why: String) -> Self {
        LempiError::Refused(why)
    }
}

/// What is playing, for the notification and anything native the app shows.
/// A fixed record rather than the engine's own state: the interface should
/// not change each time the engine learns something new.
#[derive(uniffi::Record)]
pub struct NowPlaying {
    pub playing: bool,
    pub title: Option<String>,
    pub artist: Option<String>,
    pub position_ms: u64,
    /// 0.0 to 1.0.
    pub volume: f32,
    pub queue_len: u32,
}

/// The transport, as the notification and a headset ask for it.
#[derive(uniffi::Enum)]
pub enum Transport {
    Play,
    Pause,
    Skip,
}

fn with_player<T>(f: impl FnOnce(&Player) -> T) -> Result<T, LempiError> {
    let slot = PLAYER.lock().map_err(|_| LempiError::Refused("the player lock is poisoned".into()))?;
    match slot.as_ref() {
        Some(p) => Ok(f(p)),
        None => Err(LempiError::Refused("not started".into())),
    }
}

/// Start the player, its web server on loopback `port` behind `key`.
#[uniffi::export]
pub fn start(listener: String, library: String, port: u16, key: String) -> Result<(), LempiError> {
    if CONTEXT.get().is_none() {
        tracing::error!("start refused: init was not called");
        return Err(LempiError::NotInitialised);
    }
    let r = guarded(|| {
        let mut slot = PLAYER.lock().map_err(|_| "the player lock is poisoned".to_string())?;
        if slot.is_some() {
            return Err("already started".into());
        }
        let cfg = Config {
            listener: PathBuf::from(listener),
            library: PathBuf::from(library),
            depth: lempi_player::QUEUE_DEPTH,
            device: None,
            echo_offset_frames: 0,
            echo_fleet_min_frames: 0,
            echo_rate: 44_100,
            follow: None,
            mpd_addr: None,
            mpd_root: None,
            web_port: Some(port),
            also_port_80: false,
            web_loopback_only: true,
            web_secret: Some(key),
            writes_beside_audio: false,
            backup: true,
            tag_scan: false,
        };
        let player = Player::start(cfg).map_err(|e| e.to_string())?;
        *slot = Some(player);
        Ok(())
    });
    if let Err(e) = &r {
        tracing::error!("start refused: {e}");
    }
    r.map_err(LempiError::from)
}

/// Play, pause or skip.
#[uniffi::export]
pub fn command(what: Transport) -> Result<(), LempiError> {
    with_player(|p| {
        p.command(match what {
            Transport::Play => Command::Play,
            Transport::Pause => Command::Pause,
            Transport::Skip => Command::Skip,
        })
    })
}

/// Master volume, 0.0 to 1.0; the engine clamps it.
#[uniffi::export]
pub fn set_volume(volume: f32) -> Result<(), LempiError> {
    with_player(|p| p.command(Command::SetVolume(volume)))
}

/// What the engine last published; `None` when the player is not running.
#[uniffi::export]
pub fn state() -> Option<NowPlaying> {
    with_player(|p| {
        let s = p.state();
        NowPlaying {
            playing: s.playing,
            // The queue entry's own precedence -- MusicBrainz, then the file's
            // tags, then the filename -- so the notification names a passage
            // as the skin does `[REQ-VIS-170]`. Found 2026-09-26: reading
            // `mb_title` alone showed a raw filename where the skin said
            // "Dear Mr. President".
            title: s.current.as_ref().map(|c| c.title()),
            artist: s.current.as_ref().and_then(|c| c.artist()),
            position_ms: s.position_ms,
            volume: s.volume,
            queue_len: u32::try_from(s.queue_len).unwrap_or(u32::MAX),
        }
    })
    .ok()
}

/// Persist and stop. Stopping a player that is not running is not an error.
#[uniffi::export]
pub fn stop() -> Result<(), LempiError> {
    guarded(|| {
        let taken = PLAYER.lock().map_err(|_| "the player lock is poisoned".to_string())?.take();
        match taken {
            None => Ok(()),
            Some(p) => {
                p.persist();
                if p.shutdown() {
                    Ok(())
                } else {
                    Err("the engine did not stop within five seconds".into())
                }
            }
        }
    })
    .map_err(LempiError::from)
}
